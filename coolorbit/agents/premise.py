"""Premise checker: before anything runs, ask "is this property measurable with this method, model,
size and timescale?" (NVIDIA found zero agents questioned an ill-posed task; this one must.)

Two layers:

1. **Rules** (``premise_rules``): settle the cases we can decide cheaply and reproducibly, e.g. ion
   diffusion in a pristine crystal at MD timescales (NVIDIA's LiF probe), melting points by single-phase
   heating, transport under a thermostat, an inorganic-only MLIP on a molecular organic. Rules also
   recognise well-posed controls (coexistence melting, NPT latent heat, static EOS, adsorption energies,
   liquid diffusion in NVE).
2. **LLM** (``llm_premise``, Claude Opus 5.5, structured JSON): consulted when the rules are inconclusive
   (or always, with ``llm_mode="always"``). It cannot overturn a rule-based ``fail``. With no API
   credentials it is skipped and the verdict stays ``inconclusive`` (reported, never silently passed).

On the board (``PremiseChecker``, agent ``referee_premise``) it posts a ``premise_check`` on every
simulation request (a ``property_result`` need) as soon as it appears, and, if a result is produced from a
request it failed, posts the same ``fail`` on that result so the trust rule flags it.

    python -m coolorbit.agents.premise --board boards/<id> [--once] [--llm inconclusive|always|never]
    python -m coolorbit.agents.premise --task '{"material": "LiF", "property": "self_diffusion", ...}'
    python -m coolorbit.agents.premise --tasks tasks.json            # list of task dicts -> JSON verdicts
"""

from __future__ import annotations

import json
import math
import re
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from coolorbit.agents.base import ORGANIC_ELEMENTS, Agent, agent_cli, candidate_of, formula_elements, norm
from coolorbit.agents.referee_rules import load_audit_rules
from coolorbit.board import Board, BoardSnapshot, OpenNeed
from coolorbit.board._scienceclaw import Artifact

AGENT_NAME = "referee_premise"
LLM_MODEL = "claude-opus-5-5"
LLM_EFFORT = "high"  # Opus 5.5 defaults to medium; premise checks are judgement-heavy
FALLBACK_BETA = "server-side-fallback-2026-07-01"

STATIC_PROPERTIES = {"equation_of_state", "bulk_modulus", "lattice_constant", "energy_volume_curve",
                     "adsorption_energy", "binding_energy", "formation_energy", "surface_energy"}
WELL_POSED_MELTING = ("coexistence", "hysteresis", "two-phase", "interface")
PRISTINE_WORDS = ("pristine", "perfect crystal", "defect-free", "defect free", "ideal crystal", "no defects")
# "perfect fcc copper crystal", "ideal single crystal", ...: an adjective, then a few words, then crystal/lattice
PRISTINE_RE = re.compile(r"\b(?:perfect|ideal|defect[- ]free)\b(?:\s+[\w()/-]+){0,4}?\s+(?:crystal|lattice)\b")
# In this project "conductivity" means thermal conductivity (data/calibration units W/m/K): phonon/electron
# transport, measurable in a perfect crystal. Ion/atom transport properties are the timescale-limited ones.
THERMAL_PROPERTIES = {"conductivity", "thermal_conductivity", "thermal_diffusivity"}
CRYSTAL_WORDS = ("crystal", "crystalline", "solid", "lattice")
LIQUID_WORDS = ("liquid", "molten", "melt ", "amorphous", "glass")
ATTEMPT_FREQ_HZ = 1.0e13  # typical phonon attempt frequency for an Arrhenius hop estimate
K_B_EV = 8.617333262e-5


@dataclass
class PremiseVerdict:
    verdict: str  # pass | fail | inconclusive (board Check verdicts)
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    suggested_fix: str = ""
    source: str = "rules"  # rules | llm | rules+llm
    usage: dict = field(default_factory=dict)  # LLM token counts, for cost metrics

    def note(self) -> str:
        text = "; ".join(self.reasons) or "no issues found"
        if self.suggested_fix:
            text += f". Fix: {self.suggested_fix}"
        return f"[{self.source}] {text}"


# --------------------------------------------------------------------------- rules


def _text(task: dict) -> str:
    return " ".join(str(task.get(k) or "") for k in ("description", "state", "defects", "material")).lower() + " "


def _state(task: dict) -> Optional[str]:
    """crystal | liquid | None, from explicit fields, the description, or T vs a known melting point."""
    state = str(task.get("state") or "").lower()
    if state in ("liquid", "melt", "molten", "amorphous", "glass"):
        return "liquid"
    if state in ("crystal", "crystalline", "solid"):
        return "crystal"
    t, tm = task.get("temperature_K"), task.get("melting_point_K")
    if t is not None and tm is not None:
        return "liquid" if t >= tm else "crystal"
    text = _text(task)
    if any(w in text for w in LIQUID_WORDS):
        return "liquid"
    if any(w in text for w in CRYSTAL_WORDS) or any(w in text for w in PRISTINE_WORDS):
        return "crystal"
    return None


def _pristine(task: dict) -> bool:
    defects = str(task.get("defects") or "").lower()
    if defects in ("none", "pristine", "perfect", "defect-free"):
        return True
    if defects and defects not in ("unknown", "unspecified"):
        return False  # vacancies, interstitials, dopants, grain boundaries ... were put in deliberately
    text = _text(task)
    return any(w in text for w in PRISTINE_WORDS) or bool(PRISTINE_RE.search(text))


def _organic(task: dict, rules: dict) -> bool:
    els = formula_elements(task.get("formula"))
    return task.get("family") in rules["organic_families"] or ({"C", "H"} <= els and els <= ORGANIC_ELEMENTS)


def premise_rules(task: dict, rules: Optional[dict] = None) -> PremiseVerdict:
    """Pure, deterministic rules. ``inconclusive`` means "ask the LLM"."""
    rules = rules or load_audit_rules()
    m = task.get("method") or {}
    prop = str(task.get("property", "")).lower()
    protocol = str(m.get("protocol") or "").lower()
    ensemble = str(m.get("ensemble") or "").lower()
    model = str(m.get("model") or "").lower()
    v = PremiseVerdict("inconclusive")

    def fail(flag: str, why: str, fix: str) -> None:
        v.verdict = "fail"
        v.flags.append(flag)
        v.reasons.append(why)
        v.suggested_fix = "; ".join(x for x in (v.suggested_fix, fix) if x)

    transport = prop in rules["transport_properties"] or "diffus" in prop or "conductiv" in prop
    state = _state(task)

    # 1. Transport in a pristine crystal at MD timescales (NVIDIA's LiF probe).
    thermal = prop in THERMAL_PROPERTIES or "thermal" in prop
    if transport and not thermal and state == "crystal":
        length_ps = m.get("sim_length_ps")
        ea = task.get("activation_energy_eV")
        t = task.get("temperature_K")
        if ea is not None and t and length_ps:
            hops = ATTEMPT_FREQ_HZ * length_ps * 1e-12 * math.exp(-ea / (K_B_EV * t))
            if hops < 10:
                fail("ill_posed_timescale",
                     f"{prop} in a crystal: with Ea = {ea} eV at {t} K the expected number of hops in "
                     f"{length_ps} ps is ~{hops:.2g}, so MD would measure noise, not a diffusion coefficient",
                     "use NEB barriers + kinetic Monte Carlo, much higher T with Arrhenius extrapolation, or add defects")
        elif _pristine(task):
            fail("ill_posed_timescale",
                 f"{prop} in a pristine (defect-free) crystal: ions only move via vacancy/interstitial formation "
                 "plus migration (barriers of order 1 eV or more), so in ns-scale MD essentially no hops occur and "
                 "the 'diffusion coefficient' is a fit to vibrations, not a measurable property at this timescale",
                 "model defect-mediated transport explicitly (NEB + kMC), or study the liquid / a defective cell")

    # 2. Method premises that are wrong whatever the material.
    if prop in rules["melting_properties"] and any(p in protocol for p in rules["single_phase_protocols"]):
        fail("single_phase_heating", f"{prop} by '{m.get('protocol')}': perfect crystals superheat, the jump "
                                     "overshoots the melting point", "use two-phase coexistence (or hysteresis)")
    if transport and any(e in ensemble for e in rules["thermostat_ensembles_banned_for_transport"]):
        fail("langevin_transport", f"{prop} under '{m.get('ensemble')}': thermostat friction damps transport "
                                   "(NVIDIA measured 3-5x)", "equilibrate with a thermostat, measure in NVE")
    if _organic(task, rules) and any(p in model for p in rules["inorganic_only_model_patterns"]):
        fail("model_out_of_domain", f"model '{m.get('model')}' is trained on inorganic materials; "
                                    f"{task.get('material')} is a molecular organic",
             "use classical MD (GAFF2/OpenMM) or a molecular MLIP with verified periodic support")
    if v.verdict == "fail":
        return v

    # 3. Recognised well-posed tasks.
    if prop in rules["melting_properties"] and prop == "melting_point":
        if any(w in protocol for w in WELL_POSED_MELTING):
            v.verdict, v.reasons = "pass", [f"melting point by '{m.get('protocol')}' is well-posed"]
        elif not protocol:
            v.verdict = "pass"
            v.reasons = ["melting point is well-defined; condition: two-phase coexistence (or hysteresis), "
                         "never single-phase heating"]
    elif prop in ("latent_heat", "enthalpy_of_fusion", "density"):
        v.verdict = "pass"
        v.reasons = [f"{prop} from NPT runs of each phase at the transition temperature is well-posed"
                     + ("" if task.get("temperature_K") else "; condition: give the transition temperature")]
    elif prop in STATIC_PROPERTIES:
        v.verdict, v.reasons = "pass", [f"{prop} is a static (relaxation / energy) property: no timescale issue"]
    elif transport and not thermal and state == "liquid":
        v.verdict = "pass"
        v.reasons = [f"{prop} in a liquid is measurable in ns-scale MD"
                     + ("" if "nve" in ensemble else "; condition: NVE production (no thermostat)")]
    if v.verdict == "inconclusive":
        v.reasons = [f"no rule decides '{prop}' for state={state or 'unknown'}, protocol={m.get('protocol') or 'unknown'}"]
    return v


# --------------------------------------------------------------------------- LLM

SYSTEM_PROMPT = """You are the premise referee in a team of AI agents that run atomistic simulations \
(MLIP molecular dynamics with MACE/AIMNet2/UMA via NVIDIA ALCHEMI, and classical MD with GAFF2/OpenMM). \
Before anyone spends GPU time, you decide whether the requested property is well-defined and measurable \
with the stated method, model, system size and simulated timescale.

Judge the physics, not the cost. Flag a task as ill_posed only for a concrete physical reason, for example:
- the process is too rare for the simulated timescale (estimate it: attempt frequency ~1e13 Hz times \
exp(-Ea/kT) times the simulated time; fewer than ~10 events means the number is noise);
- the protocol is known to be biased (single-phase heating for melting points superheats; thermostats \
such as Langevin damp transport properties, so production must be NVE);
- the force field is outside its training domain (MACE-MP-family models are trained on inorganic crystals);
- the property is not defined for the stated system (e.g. a single melting point for an incongruently \
melting compound), or the box is too small for the quantity.
Well-posed tasks must pass: false alarms cost the team as much as missed errors. If the method is fine but \
needs a condition (e.g. NVE production, convergence in box size), answer well_posed and state the condition. \
Use "uncertain" only when the task description lacks information you would need."""

LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["well_posed", "ill_posed", "uncertain"]},
        "flags": {"type": "array", "items": {"type": "string"},
                  "description": "short snake_case trap names, e.g. ill_posed_timescale"},
        "reasoning": {"type": "string", "description": "two to five sentences, with any estimate you made"},
        "suggested_fix": {"type": "string"},
    },
    "required": ["verdict", "flags", "reasoning", "suggested_fix"],
    "additionalProperties": False,
}
_LLM_TO_CHECK = {"well_posed": "pass", "ill_posed": "fail", "uncertain": "inconclusive"}


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def make_client():
    """An Anthropic client, or None when no credentials are configured (rules-only mode)."""
    _load_env()
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("ANTHROPIC_PROFILE")):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic()


def _api_failure(exc: Exception) -> PremiseVerdict:
    """Map an anthropic SDK error to an inconclusive verdict, most specific first; re-raise anything else."""
    names = [c.__name__ for c in type(exc).__mro__]
    if "APIError" not in names:
        raise exc
    if "AuthenticationError" in names:
        why = "authentication failed"
    elif "RateLimitError" in names:
        why = "rate limited"
    elif "APIStatusError" in names:
        why = f"API error {getattr(exc, 'status_code', '?')}"
    elif "APIConnectionError" in names:  # includes timeouts
        why = "connection error"
    else:
        why = type(exc).__name__
    return PremiseVerdict("inconclusive", reasons=[f"LLM unavailable: {why}"], source="llm")


def llm_premise(task: dict, client: Any, rule_view: Optional[PremiseVerdict] = None,
                model: str = LLM_MODEL, effort: str = LLM_EFFORT) -> PremiseVerdict:
    """Ask Claude for a structured premise verdict. API problems become ``inconclusive``.

    Does not import ``anthropic`` itself (only ``make_client`` does), so tests can pass a fake client on a
    machine without the SDK. SDK errors are recognised by their class hierarchy (``anthropic.APIError``);
    anything else is a bug and is re-raised.
    """
    user = ("Task to premise-check (JSON):\n" + json.dumps(task, indent=2, default=str)
            + ("\n\nThe rule-based layer said: " + rule_view.note() if rule_view else ""))
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": LLM_SCHEMA}},
            betas=[FALLBACK_BETA],
            fallbacks="default",  # on a safety decline, the API retries on a fallback model in the same call
        )
    except Exception as exc:  # noqa: BLE001 - narrowed below to the SDK's own error classes
        return _api_failure(exc)

    usage = {"model": getattr(response, "model", model),
             "input_tokens": getattr(response.usage, "input_tokens", None),
             "output_tokens": getattr(response.usage, "output_tokens", None)}
    if response.stop_reason == "refusal":
        return PremiseVerdict("inconclusive", reasons=["LLM declined the request"], source="llm", usage=usage)
    if response.stop_reason == "max_tokens":
        return PremiseVerdict("inconclusive", reasons=["LLM answer truncated (max_tokens)"], source="llm", usage=usage)
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
        verdict = _LLM_TO_CHECK[data["verdict"]]
    except (json.JSONDecodeError, KeyError, TypeError):
        return PremiseVerdict("inconclusive", reasons=["LLM returned unparseable output"], source="llm", usage=usage)
    return PremiseVerdict(verdict, flags=[str(f) for f in data.get("flags", [])][:10],
                          reasons=[str(data.get("reasoning", "")).strip()],
                          suggested_fix=str(data.get("suggested_fix", "")).strip(), source="llm", usage=usage)


def check_premise(task: dict, *, client: Any = None, llm_mode: str = "inconclusive",
                  rules: Optional[dict] = None) -> PremiseVerdict:
    """Rules first; the LLM when they are inconclusive (or always). A rule ``fail`` is never overturned."""
    if llm_mode not in ("inconclusive", "always", "never"):
        raise ValueError(f"llm_mode must be inconclusive|always|never, not {llm_mode!r}")
    rv = premise_rules(task, rules)
    ask = client is not None and (llm_mode == "always" or (llm_mode == "inconclusive" and rv.verdict == "inconclusive"))
    if not ask:
        if rv.verdict == "inconclusive" and client is None and llm_mode != "never":
            rv.reasons.append("no LLM credentials configured, so left inconclusive")
        return rv
    lv = llm_premise(task, client, rv)
    if rv.verdict == "fail":
        verdict = "fail"
    elif rv.verdict == "pass" and lv.verdict == "inconclusive":
        verdict = "pass"
    else:
        verdict = lv.verdict
    return PremiseVerdict(verdict, flags=list(dict.fromkeys(rv.flags + lv.flags)),
                          reasons=[r for r in rv.reasons + lv.reasons if r],
                          suggested_fix="; ".join(x for x in (rv.suggested_fix, lv.suggested_fix) if x),
                          source="rules+llm", usage=lv.usage)


# --------------------------------------------------------------------------- board agent


def task_from_need(snap: BoardSnapshot, art: Artifact, need: dict, calibration_index: Optional[dict] = None) -> dict:
    """Describe a simulation request (docs/simulation-requests.md) as a premise-check task."""
    cand = candidate_of(snap, art)
    cp = cand.payload if cand else {}
    prop = need.get("property", "")
    engine = need.get("engine") or "ALCHEMI"
    if engine == "ALCHEMI":
        protocol = {"melting_point": "two-phase coexistence",
                    "latent_heat": "NPT enthalpy difference (solid vs liquid)"}.get(prop, need.get("protocol"))
        method = {"engine": "ALCHEMI", "model": need.get("model", "MACE-MPA-0"), "protocol": protocol,
                  "ensemble": need.get("ensemble")}
    else:
        method = {"engine": engine, "model": need.get("model"), "protocol": need.get("protocol"),
                  "ensemble": need.get("ensemble")}
    task = {"material": cp.get("name"), "family": cp.get("family"), "formula": cp.get("formula"),
            "structure_source": cp.get("structure_source"), "property": prop, "method": method,
            "temperature_K": need.get("temperature_K"), "temps_K": need.get("temps_K") or need.get("t_guess_K"),
            "state": need.get("state"), "defects": need.get("defects"),
            "description": need.get("query", "") + ". " + need.get("rationale", "")}
    if calibration_index is not None and cp.get("name"):
        rec = calibration_index.get((norm(cp["name"]), "melting_point"))
        if rec and rec["value"] is not None:
            task["melting_point_K"] = rec["value"]
    return {k: v for k, v in task.items() if v not in (None, "", [])}


def _tag(need_index: int) -> str:
    return f"[need {need_index}]"


class PremiseChecker(Agent):
    role = "referee"

    def __init__(self, board: Board, name: str = AGENT_NAME, *, client: Any = "auto", llm_mode: str = "inconclusive",
                 rules: Optional[dict] = None, calibration: Optional[list] = None, **kw) -> None:
        super().__init__(board, name, **kw)
        self.client = make_client() if client == "auto" else client
        self.llm_mode = llm_mode
        self.rules = rules or load_audit_rules()
        if calibration is None:
            from coolorbit.agents.librarian import DEFAULT_DATA, load_calibration
            calibration = load_calibration(DEFAULT_DATA) if DEFAULT_DATA.exists() else []
        from coolorbit.agents.librarian import index_records
        self.cal_index = index_records(calibration)
        if self.client is None:
            self.log("mode", llm="off (no credentials): rules only, undecided tasks stay inconclusive")

    def _my_checks(self, snap: BoardSnapshot, art_id: str) -> list[Artifact]:
        return [k for k in snap.children.get(art_id, []) if k.artifact_type == "check"
                and k.producer_agent == self.name and k.payload["check_type"] == "premise_check"]

    def _checked(self, snap: BoardSnapshot, art_id: str, need_index: int) -> Optional[Artifact]:
        return next((c for c in self._my_checks(snap, art_id) if c.payload["note"].startswith(_tag(need_index))), None)

    def step(self) -> bool:
        snap = self.board.snapshot()
        # 1. Every simulation request, before it runs (claimed or not: we don't claim, we only advise).
        for on in self.board.open_needs(include_claimed=True):
            if on.need.get("artifact_type") != "property_result":
                continue
            if self._checked(snap, on.artifact.artifact_id, on.index):
                continue
            task = task_from_need(snap, on.artifact, on.need, self.cal_index)
            v = check_premise(task, client=self.client, llm_mode=self.llm_mode, rules=self.rules)
            self._post(v, parent=on.artifact.artifact_id, need_index=on.index, task=task)
            return True
        # 2. Results produced from a request: carry the request's verdict onto the result.
        for res in snap.of_type("property_result"):
            fn = res.payload.get("_fulfilled_need")
            if not fn or self._my_checks(snap, res.artifact_id):
                continue
            idx = res.payload.get("_need_index")
            prior = self._checked(snap, fn["parent_artifact_id"], idx)
            if prior is None:
                parent = snap.artifacts.get(fn["parent_artifact_id"])
                if parent is None or idx is None or idx >= len(parent.needs):
                    continue
                task = task_from_need(snap, parent, parent.needs[idx], self.cal_index)
                v = check_premise(task, client=self.client, llm_mode=self.llm_mode, rules=self.rules)
            else:
                v = PremiseVerdict(prior.payload["verdict"], flags=list(prior.payload["flags"]),
                                   reasons=[f"same request as premise check {prior.artifact_id[:8]}: "
                                            + prior.payload["note"].split("] ", 2)[-1]], source="carried")
                task = None
            self._post(v, parent=res.artifact_id, need_index=idx, task=task)
            return True
        return False

    def _post(self, v: PremiseVerdict, *, parent: str, need_index: int, task: Optional[dict]) -> None:
        note = f"{_tag(need_index)} {v.note()}"
        self.post("check", {"check_type": "premise_check", "verdict": v.verdict, "flags": v.flags, "note": note[:2000]},
                  parents=[parent], skill="coolorbit.referee.premise")
        self.log("decision", target=parent[:8], need_index=need_index, verdict=v.verdict, flags=v.flags,
                 source=v.source, usage=v.usage or None, task=task)


def main(argv: Optional[list[str]] = None) -> None:
    import argparse
    import sys

    args_in = sys.argv[1:] if argv is None else argv
    if "--task" in args_in or "--tasks" in args_in:
        ap = argparse.ArgumentParser(description="premise-check standalone tasks")
        ap.add_argument("--task", help="one task as JSON")
        ap.add_argument("--tasks", help="JSON file with a list of tasks")
        ap.add_argument("--llm", default="inconclusive", choices=["inconclusive", "always", "never"])
        a = ap.parse_args(args_in)
        tasks = [json.loads(a.task)] if a.task else json.loads(Path(a.tasks).read_text())
        client = None if a.llm == "never" else make_client()
        out = [{"task": t, **asdict(check_premise(t, client=client, llm_mode=a.llm))} for t in tasks]
        print(json.dumps(out if a.tasks else out[0], indent=2, default=str))
        return

    def add(ap):
        ap.add_argument("--llm", default="inconclusive", choices=["inconclusive", "always", "never"])

    agent_cli(__doc__.split("\n\n")[0],
              lambda a, b: PremiseChecker(b, client=None if a.llm == "never" else "auto", llm_mode=a.llm), add, args_in)


if __name__ == "__main__":
    main()
