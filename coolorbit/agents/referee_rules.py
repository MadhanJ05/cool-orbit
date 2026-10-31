"""Rule-based method-audit referee: tries to prove every simulation result wrong from its method metadata.

It audits each ``property_result`` on the board once (whether or not anyone asked), and fulfils the
result's ``method_audit`` need when it can claim it. Checks (thresholds in ``audit_rules.json``):

* melting point from single-phase heating (superheating)            -> ``single_phase_heating``
* transport property under a thermostat (Langevin etc.) production   -> ``langevin_transport``
* inorganic-only MLIP (MACE-MP family...) on a molecular organic      -> ``model_out_of_domain``
* hydrogen present with a timestep above the limit                   -> ``timestep_too_large_for_hydrogen``
* too few atoms / too short a run for the protocol                   -> ``system_too_small`` / ``run_too_short``
* method metadata missing                                             -> ``missing_method_metadata``
* blocking flags the producer itself raised (superheating hook, quick settings, ...)

Verdict ``fail`` if any rule fires, else ``pass``. It never checks its own results.

    python -m coolorbit.agents.referee_rules --board boards/<id> [--once]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from coolorbit.agents.base import ORGANIC_ELEMENTS, Agent, agent_cli, candidate_of, formula_elements
from coolorbit.board import Board, BoardSnapshot
from coolorbit.board._scienceclaw import Artifact

RULES_PATH = Path(__file__).with_name("audit_rules.json")
AGENT_NAME = "referee_rules"


def load_audit_rules(path: str | Path = RULES_PATH) -> dict:
    return json.loads(Path(path).read_text())


def _lookup(table: dict, protocol: str):
    return table.get(protocol, table["default"])


def audit(result: dict, rules: dict, candidate: Optional[dict] = None) -> tuple[list[str], list[str]]:
    """Pure function: (flags, reasons) for one property_result payload. Empty flags = pass."""
    flags: list[str] = []
    reasons: list[str] = []

    def fire(flag: str, why: str) -> None:
        if flag not in flags:
            flags.append(flag)
            reasons.append(why)

    m = result.get("method") or {}
    prop = result.get("property", "")
    protocol = str(m.get("protocol", "")).lower()
    ensemble = str(m.get("ensemble") or "").lower()
    model = str(m.get("model", "")).lower()

    if prop in rules["melting_properties"] and any(p in protocol for p in rules["single_phase_protocols"]):
        fire("single_phase_heating", f"{prop} from '{m.get('protocol')}': a perfect crystal superheats; "
                                     "use two-phase coexistence (or hysteresis)")
    if prop in rules["transport_properties"] and any(e in ensemble for e in rules["thermostat_ensembles_banned_for_transport"]):
        fire("langevin_transport", f"{prop} measured under '{m.get('ensemble')}': thermostat friction damps "
                                   "transport (NVIDIA saw 3-5x); production must be NVE")

    family = (candidate or {}).get("family") or result.get("family", "")
    elements = formula_elements((candidate or {}).get("formula"))
    organic = family in rules["organic_families"] or ({"C", "H"} <= elements and elements <= ORGANIC_ELEMENTS)
    if organic and any(pat in model for pat in rules["inorganic_only_model_patterns"]):
        fire("model_out_of_domain", f"model '{m.get('model')}' is trained on inorganic materials; "
                                    f"'{family or 'this candidate'}' is a molecular organic")

    dt = m.get("timestep_fs")
    has_h = "H" in elements or organic
    if has_h and dt is not None and dt > rules["max_timestep_fs_with_hydrogen"]:
        fire("timestep_too_large_for_hydrogen",
             f"timestep {dt} fs with hydrogen present (limit {rules['max_timestep_fs_with_hydrogen']} fs)")

    missing = [f for f in rules["required_method_fields"] if m.get(f) in (None, "")]
    if missing:
        fire("missing_method_metadata", f"method metadata missing: {', '.join(missing)}; cannot audit size/length/ensemble")
    n_atoms, length = m.get("n_atoms"), m.get("sim_length_ps")
    min_atoms = _lookup(rules["min_atoms"], protocol)
    min_len = _lookup(rules["min_sim_length_ps"], protocol)
    if n_atoms is not None and n_atoms < min_atoms:
        fire("system_too_small", f"{n_atoms} atoms < {min_atoms} required for '{m.get('protocol')}'")
    if length is not None and length < min_len:
        fire("run_too_short", f"{length} ps production < {min_len} ps required for '{m.get('protocol')}'")

    for flag in result.get("flags", []):
        why = rules["blocking_producer_flags"].get(flag)
        if why:
            fire(flag, f"producer flag '{flag}': {why}")
    return flags, reasons


class RuleReferee(Agent):
    role = "referee"

    def __init__(self, board: Board, name: str = AGENT_NAME, *, rules: Optional[dict] = None, **kw) -> None:
        super().__init__(board, name, **kw)
        self.rules = rules if rules is not None else load_audit_rules()

    def _audited(self, snap: BoardSnapshot, res: Artifact) -> bool:
        return any(k.artifact_type == "check" and k.producer_agent == self.name
                   and k.payload["check_type"] == "method_audit" for k in snap.children.get(res.artifact_id, []))

    def step(self) -> bool:
        snap = self.board.snapshot()
        audit_needs = {(on.artifact.artifact_id): on for on in self.board.open_needs(include_claimed=False)
                       if on.need.get("artifact_type") == "check" and on.need.get("check_type") == "method_audit"}
        for res in snap.of_type("property_result"):
            if res.producer_agent == self.name or self._audited(snap, res):
                continue
            cand = candidate_of(snap, res)
            flags, reasons = audit(res.payload, self.rules, cand.payload if cand else None)
            verdict = "fail" if flags else "pass"
            note = ("; ".join(reasons) if reasons else
                    "rule-based audit passed: protocol, ensemble, model domain, timestep, size, length, producer flags")
            if not self.rules.get("frozen"):
                note += " [audit thresholds are placeholders, not frozen]"
            fulfills = None
            on = audit_needs.get(res.artifact_id)
            if on is not None and self.claim(on, ttl_minutes=5):
                fulfills = (on.artifact.artifact_id, on.index)
            self.post("check", {"check_type": "method_audit", "verdict": verdict, "flags": flags, "note": note[:2000]},
                      parents=[res.artifact_id], fulfills=fulfills, skill="coolorbit.referee.rules")
            self.log("decision", result=res.artifact_id[:8], candidate=res.payload["candidate"],
                     property=res.payload["property"], verdict=verdict, flags=flags)
            return True
        return False


def main(argv: Optional[list[str]] = None) -> None:
    agent_cli(__doc__.split("\n\n")[0], lambda a, b: RuleReferee(b), argv=argv)


if __name__ == "__main__":
    main()
