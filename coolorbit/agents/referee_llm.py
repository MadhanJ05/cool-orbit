"""LLM method-audit referee and the disagreement adjudicator.

* ``LLMReferee`` (agent ``referee_llm``, role ``referee_llm_audit`` in configs/llm_roles.json): reads each
  ``property_result``'s method metadata and tries to find a concrete physical flaw the rule referee's fixed
  thresholds can miss (wrong protocol for the property, an ensemble or barostat that biases it, a model used
  outside its domain, a self-validating test, a unit or convention error). It never sees the other referees'
  checks, so its verdict is independent. It posts a ``method_audit`` check: ``pass`` (sound), ``fail`` (a
  concrete flaw) or ``inconclusive`` (not enough information, or the LLM was unavailable). It never requests
  reruns itself: the rule referee does, and the adjudicator does when it upholds a failure the rules missed.

* ``Adjudicator`` (agent ``adjudicator``, role ``adjudicator``: the most expensive model, few calls): when two
  independent referees' method audits of the same result disagree (one passes, another fails), it reads the
  result and both arguments and rules on it, posting a ``method_audit`` check whose note names the checks it
  settles. If it upholds a failure and no rerun was requested yet, it attaches the rerun need the rule referee
  would have built (for flags the Simulator can act on). At most ``max_calls`` rulings per process.

  Status note: the board's trust rule (coolorbit/board/status.py) flags a result on ANY negative check, so a
  ruling of ``pass`` is recorded as evidence but does not by itself clear the flag.

Both are skipped on results that are already superseded by a rerun and, unless ``include_synthetic``, on
synthetic test data (dev boards), so no money is spent on them. Keys come from coolorbit/llm_keys.py via
coolorbit/llm_roles.py; with no usable key the agents do nothing (``client=None``).

    python -m coolorbit.agents.referee_llm --board boards/<id> [--once]                 # the LLM referee
    python -m coolorbit.agents.referee_llm --board boards/<id> --adjudicator [--once]   # the adjudicator
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from coolorbit.agents.base import Agent, agent_cli, candidate_of
from coolorbit.agents.premise import _api_failure, _structured_request, make_client
from coolorbit.agents.ranker import SYNTHETIC_FLAG
from coolorbit.agents.referee_rules import load_audit_rules, origin_need, rerun_depth, rerun_need
from coolorbit.board import Board, BoardSnapshot
from coolorbit.board._scienceclaw import Artifact
from coolorbit.board.schema import NEGATIVE_VERDICTS, POSITIVE_VERDICTS

AGENT_NAME = "referee_llm"
ADJUDICATOR_NAME = "adjudicator"
AUDIT_ROLE = "referee_llm_audit"   # configs/llm_roles.json decides model and effort, never this file
ADJUDICATOR_ROLE = "adjudicator"

AUDIT_SYSTEM = """You are an independent method-audit referee in a team of AI agents that run atomistic \
simulations (MLIP molecular dynamics and relaxations with MACE/AIMNet2/UMA via NVIDIA ALCHEMI, and classical \
MD with GAFF2/OpenMM) to find phase-change materials. Another agent produced the result below. Try to prove \
its METHOD wrong from the metadata it records.

Fail it only for a concrete, specific flaw that would bias or invalidate the number, for example:
- melting point from single-phase heating of a perfect crystal (superheats; needs two-phase coexistence or \
hysteresis);
- a transport property (diffusion, viscosity, ionic conductivity) from thermostatted (Langevin, Berendsen) \
production instead of NVE;
- a force field outside its training domain (MACE-MP-family models are trained on inorganic crystals, not \
molecular organics);
- a timestep too large for the lightest atoms, an NVE run with energy drift, a box too small or a run too \
short for the quantity, a latent heat taken at the wrong temperature or with mismatched phases;
- a self-validating test, or a unit or reference-convention error (e.g. J/mol reported as J/kg, an \
impossible order of magnitude).
Static properties (lattice constant, equation of state, bulk modulus, adsorption or formation energy) involve \
no MD: small cells are correct for them and MLIPs use no k-points. Judge the method, not whether the value \
matches literature: a separate referee compares it to measured data. False alarms cost the team as much as \
missed errors, so answer "sound" unless you can name the flaw, and "uncertain" only when the metadata lacks \
something you would need."""

ADJUDICATOR_SYSTEM = """You are the adjudicator for a team of AI referees that audit atomistic simulation \
results. Two independent referees disagree about the method behind the result below: at least one passed it \
and at least one failed it. Read the result's metadata and each referee's argument, then rule: "flawed" if a \
failing referee named a real flaw that biases or invalidates the number, "sound" if the objection does not \
hold for this method and property, "uncertain" only if the metadata cannot settle it. Static properties \
(lattice constant, EOS, bulk modulus, adsorption energy) involve no MD and correctly use small cells. False \
alarms cost the team as much as missed errors."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["sound", "flawed", "uncertain"]},
        "flags": {"type": "array", "items": {"type": "string"},
                  "description": "short snake_case trap names, e.g. single_phase_heating, langevin_transport"},
        "reasoning": {"type": "string", "description": "two to five sentences naming the evidence"},
        "suggested_fix": {"type": "string"},
    },
    "required": ["verdict", "flags", "reasoning", "suggested_fix"],
    "additionalProperties": False,
}
_TO_CHECK = {"sound": "pass", "flawed": "fail", "uncertain": "inconclusive"}


def ask(client: Callable[..., tuple[Any, float]], role: str, system: str, user: str) -> dict:
    """One structured call. Returns {verdict (board check verdict), flags, reasoning, suggested_fix, usage}.
    API problems, refusals, truncation and unparseable output become ``inconclusive`` with the reason."""
    out = {"verdict": "inconclusive", "flags": [], "reasoning": "", "suggested_fix": "", "usage": {}}
    try:
        response, cost = client(role, **_structured_request(role, system, user, SCHEMA))
    except Exception as exc:  # noqa: BLE001 - _api_failure re-raises anything that is not an SDK/key error
        out["reasoning"] = _api_failure(exc).reasons[0]
        return out
    out["usage"] = {"role": role, "model": getattr(response, "model", None), "cost_usd": round(cost, 6),
                    "input_tokens": getattr(response.usage, "input_tokens", None),
                    "output_tokens": getattr(response.usage, "output_tokens", None)}
    if response.stop_reason in ("refusal", "max_tokens"):
        out["reasoning"] = f"{role}: " + ("declined the request" if response.stop_reason == "refusal"
                                          else "answer truncated (max_tokens)")
        return out
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
        out["verdict"] = _TO_CHECK[data["verdict"]]
    except (json.JSONDecodeError, KeyError, TypeError):
        out["reasoning"] = f"{role} returned unparseable output"
        return out
    out["flags"] = [str(f) for f in data.get("flags", [])][:10] if out["verdict"] == "fail" else []
    out["reasoning"] = str(data.get("reasoning", "")).strip()
    out["suggested_fix"] = str(data.get("suggested_fix", "")).strip()
    return out


def _note(tag: str, a: dict) -> str:
    text = f"[{tag}] " + (a["reasoning"] or "no reasoning given")
    if a["suggested_fix"] and a["verdict"] == "fail":
        text += f". Fix: {a['suggested_fix']}"
    if a["usage"]:
        text += f" [{a['usage'].get('model')}, ${a['usage'].get('cost_usd', 0):.4f}]"
    return text[:2000]


def result_view(snap: BoardSnapshot, res: Artifact) -> dict:
    """What a referee is shown: the result and its candidate, never other referees' checks or references."""
    cand = candidate_of(snap, res)
    view = {k: v for k, v in res.payload.items() if not k.startswith("_")}
    if cand is not None:
        view["candidate_record"] = {k: cand.payload.get(k) for k in ("name", "family", "formula", "structure_source")
                                    if cand.payload.get(k)}
    return view


def _skip(snap: BoardSnapshot, res: Artifact, include_synthetic: bool) -> bool:
    if SYNTHETIC_FLAG in res.payload.get("flags", []) and not include_synthetic:
        return True
    return any(k.artifact_type == "property_result" and k.payload.get("property") == res.payload["property"]
               for k in snap.children.get(res.artifact_id, []))  # superseded by a rerun


def _audits(snap: BoardSnapshot, res: Artifact) -> list[Artifact]:
    return [k for k in snap.children.get(res.artifact_id, [])
            if k.artifact_type == "check" and k.payload["check_type"] == "method_audit"]


class LLMReferee(Agent):
    role = "referee"

    def __init__(self, board: Board, name: str = AGENT_NAME, *, client: Any = "auto",
                 include_synthetic: bool = False, **kw) -> None:
        super().__init__(board, name, **kw)
        self.client = make_client() if client == "auto" else client
        self.include_synthetic = include_synthetic

    def step(self) -> bool:
        if self.client is None:
            return False
        snap = self.board.snapshot()
        for res in snap.of_type("property_result"):
            if res.producer_agent == self.name or _skip(snap, res, self.include_synthetic):
                continue
            if any(k.producer_agent == self.name for k in _audits(snap, res)):
                continue
            user = "Result to audit (JSON):\n" + json.dumps(result_view(snap, res), indent=2, default=str)
            a = ask(self.client, AUDIT_ROLE, AUDIT_SYSTEM, user)
            self.post("check", {"check_type": "method_audit", "verdict": a["verdict"], "flags": a["flags"],
                                "note": _note("llm audit", a)},
                      parents=[res.artifact_id], skill="coolorbit.referee.llm_audit")
            self.log("decision", result=res.artifact_id[:8], candidate=res.payload["candidate"],
                     property=res.payload["property"], verdict=a["verdict"], flags=a["flags"], usage=a["usage"])
            return True
        return False


def disagreement(snap: BoardSnapshot, res: Artifact, adjudicator: str = ADJUDICATOR_NAME) -> Optional[list[Artifact]]:
    """The conflicting method audits on a result (distinct independent referees, at least one positive and one
    negative), or None if they agree."""
    audits = [c for c in _audits(snap, res) if c.producer_agent not in (res.producer_agent, adjudicator)]
    pos = {c.producer_agent for c in audits if c.payload["verdict"] in POSITIVE_VERDICTS}
    neg = {c.producer_agent for c in audits if c.payload["verdict"] in NEGATIVE_VERDICTS}
    if not pos or not neg or pos == neg:
        return None
    return [c for c in audits if c.payload["verdict"] in POSITIVE_VERDICTS | NEGATIVE_VERDICTS]


class Adjudicator(Agent):
    role = "referee"

    def __init__(self, board: Board, name: str = ADJUDICATOR_NAME, *, client: Any = "auto", max_calls: int = 10,
                 max_reruns: int = 2, include_synthetic: bool = False, rules: Optional[dict] = None, **kw) -> None:
        super().__init__(board, name, **kw)
        self.client = make_client() if client == "auto" else client
        self.max_calls, self.calls = max_calls, 0
        self.max_reruns = max_reruns
        self.include_synthetic = include_synthetic
        self.rules = rules if rules is not None else load_audit_rules()

    def _rerun(self, snap: BoardSnapshot, res: Artifact, conflict: list[Artifact], flags: list[str]) -> list[dict]:
        """A rerun need when the ruling upholds a failure and no referee already asked for one."""
        if any(c.needs for c in conflict) or rerun_depth(snap, res) >= self.max_reruns:
            return []
        known = [f for f in dict.fromkeys(flags + [f for c in conflict for f in c.payload.get("flags", [])])
                 if f in self.rules["blocking_producer_flags"] or f in ("system_too_small", "run_too_short")]
        need = rerun_need(res.payload, known, self.rules, origin_need(snap, res)) if known else None
        return [need] if need else []

    def step(self) -> bool:
        if self.client is None or self.calls >= self.max_calls:
            return False
        snap = self.board.snapshot()
        for res in snap.of_type("property_result"):
            if res.producer_agent == self.name or _skip(snap, res, self.include_synthetic):
                continue
            if any(k.producer_agent == self.name for k in _audits(snap, res)):
                continue
            conflict = disagreement(snap, res, self.name)
            if not conflict:
                continue
            args = [{"check_id": c.artifact_id[:8], "referee": c.producer_agent, "verdict": c.payload["verdict"],
                     "flags": c.payload.get("flags", []), "argument": c.payload.get("note", "")} for c in conflict]
            user = ("Result (JSON):\n" + json.dumps(result_view(snap, res), indent=2, default=str)
                    + "\n\nReferee method audits that disagree (JSON):\n" + json.dumps(args, indent=2))
            self.calls += 1
            a = ask(self.client, ADJUDICATOR_ROLE, ADJUDICATOR_SYSTEM, user)
            settles = ", ".join(f"{x['referee']} {x['verdict']} ({x['check_id']})" for x in args)
            needs = self._rerun(snap, res, conflict, a["flags"]) if a["verdict"] == "fail" else []
            self.post("check", {"check_type": "method_audit", "verdict": a["verdict"], "flags": a["flags"],
                                "note": _note(f"adjudicator, settles: {settles}", a)},
                      parents=[res.artifact_id], needs=needs, skill="coolorbit.referee.adjudicator")
            self.log("decision", result=res.artifact_id[:8], candidate=res.payload["candidate"],
                     property=res.payload["property"], verdict=a["verdict"], settles=settles,
                     rerun=needs[0]["query"] if needs else None, usage=a["usage"])
            return True
        return False


def main(argv: Optional[list[str]] = None) -> None:
    def add_args(ap) -> None:
        ap.add_argument("--adjudicator", action="store_true", help="run the adjudicator instead of the LLM referee")
        ap.add_argument("--max-calls", type=int, default=10, help="adjudicator: most rulings per process")

    agent_cli(__doc__.split("\n\n")[0],
              lambda a, b: Adjudicator(b, max_calls=a.max_calls) if a.adjudicator else LLMReferee(b),
              add_args=add_args, argv=argv)


if __name__ == "__main__":
    main()
