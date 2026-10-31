"""Rule-based method-audit referee: tries to prove every simulation result wrong from its method metadata.

It audits each ``property_result`` on the board once (whether or not anyone asked), and fulfils the
result's ``method_audit`` need when it can claim it. Checks (thresholds in ``audit_rules.json``):

* melting point from single-phase heating (superheating)            -> ``single_phase_heating``
* transport property under a thermostat (Langevin etc.) production   -> ``langevin_transport``
* inorganic-only MLIP (MACE-MP family...) on a molecular organic      -> ``model_out_of_domain``
* hydrogen present with a timestep above the limit                   -> ``timestep_too_large_for_hydrogen``
* too few atoms / too short a run for the protocol                   -> ``system_too_small`` / ``run_too_short``
* method metadata missing                                             -> ``missing_method_metadata``
* static properties (lattice constant, EOS, adsorption energy...) run no MD, so the MD-field and atom-count
  rules are skipped; they need engine/model/protocol, and fields the producer records are checked:
  unconverged relaxation, too few EOS points, a thin slab or small vacuum, DFT without k-points
* blocking flags the producer itself raised (superheating hook, quick settings, ...)

Verdict ``fail`` if any rule fires, else ``pass``. It never checks its own results.

A failing check also carries a **rerun need** when the flaw is fixable (docs/simulation-requests.md,
"Reruns"): two-phase coexistence instead of single-phase heating, the production preset instead of quick
or undersized settings, ``engine: classical_md`` for an out-of-domain model. The Simulator links the rerun's
result to the flawed one, which marks the old result superseded. At most ``max_reruns`` per lineage.

    python -m coolorbit.agents.referee_rules --board boards/<id> [--once]
"""

from __future__ import annotations

import json
import math
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

    if prop in rules.get("static_properties", ()):
        _audit_static(prop, m, rules, fire)
    else:
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


def _audit_static(prop: str, m: dict, rules: dict, fire) -> None:
    """Static (relaxation / energy) results: no MD fields, no MD atom-count minimum."""
    missing = [f for f in rules["static_required_method_fields"] if m.get(f) in (None, "")]
    if missing:
        fire("missing_method_metadata", f"method metadata missing: {', '.join(missing)}; cannot audit a static result")
    c = rules["static_checks"]
    fmax = m.get("fmax_eV_per_A")
    if fmax is not None and fmax > c["max_fmax_eV_per_A"]:
        fire("relaxation_not_converged", f"residual force {fmax} eV/A > {c['max_fmax_eV_per_A']} eV/A")
    points = m.get("n_eos_points")
    if prop in ("equation_of_state", "bulk_modulus", "energy_volume_curve") and points is not None \
            and points < c["min_eos_points"]:
        fire("too_few_eos_points", f"{points} volumes in the E(V) fit < {c['min_eos_points']}")
    if prop in c["slab_properties"]:
        layers, vacuum = m.get("slab_layers"), m.get("vacuum_A")
        if layers is not None and layers < c["min_slab_layers"]:
            fire("slab_too_thin", f"{layers} slab layers < {c['min_slab_layers']}")
        if vacuum is not None and vacuum < c["min_vacuum_A"]:
            fire("vacuum_too_small", f"{vacuum} A vacuum < {c['min_vacuum_A']} A (periodic images interact)")
    engine = str(m.get("engine", "")).lower()
    if any(p in engine for p in c["dft_engine_patterns"]) and m.get("kpoints") in (None, "", []):
        fire("missing_kpoints", "DFT result without a k-point mesh (kpoints)")


SINGLE_PHASE_FLAGS = {"single_phase_heating", "superheating_risk", "superheating_observed"}
PREP_FLAGS = {"liquid_half_incompletely_melted", "crystal_half_disturbed"}
TYPICAL_SUPERHEATING = 1.2  # single-phase heating of a perfect crystal overshoots Tm by roughly 20%
DEFAULT_REPS = {"melting": [4, 4, 8], "latent": [4, 4, 4]}  # the Simulator's defaults (docs/simulation-requests.md)


def rerun_depth(snap: BoardSnapshot, res: Artifact) -> int:
    """How many earlier same-property results this one supersedes (its rerun chain length)."""
    depth, cur = 0, res
    while True:
        prev = next((snap.artifacts[p] for p in cur.parent_artifact_ids if p in snap.artifacts
                     and snap.artifacts[p].artifact_type == "property_result"
                     and snap.artifacts[p].payload["property"] == res.payload["property"]), None)
        if prev is None:
            return depth
        depth, cur = depth + 1, prev


def origin_need(snap: BoardSnapshot, res: Artifact) -> dict:
    """The request this result fulfilled (to recover settings the result doesn't record, e.g. reps)."""
    fn = res.payload.get("_fulfilled_need")
    parent = snap.artifacts.get(fn["parent_artifact_id"]) if fn else None
    idx = res.payload.get("_need_index")
    if parent is None or idx is None or idx >= len(parent.needs):
        return {}
    return parent.needs[idx]


def _scaled_reps(reps: list[int], n_atoms: int, min_atoms: int) -> list[int]:
    """Grow the supercell, smallest axis first, until it has at least ``min_atoms`` atoms."""
    per_cell = n_atoms / math.prod(reps)
    out = list(reps)
    while per_cell * math.prod(out) < min_atoms:
        i = out.index(min(out))
        out[i] += 1
    return out


def rerun_need(result: dict, flags: list[str], rules: dict, origin: Optional[dict] = None) -> Optional[dict]:
    """The request that would fix this result's flaws, or None if no request field can fix them.

    Each fix changes something the Simulator reads (docs/simulation-requests.md); a rerun that would repeat the
    same run is never requested.
    """
    origin = origin or {}
    prop, name = result["property"], result["candidate"]
    m = result.get("method") or {}
    fl = set(flags)
    protocol = str(m.get("protocol", "")).lower()
    single_phase = any(p in protocol for p in rules["single_phase_protocols"])
    temps = list(m.get("temperatures_K") or [])
    production = "quick_settings_not_production" not in fl
    need = {"artifact_type": "property_result", "property": prop}
    fixes = []

    # Engine: out-of-domain models go to classical MD; a non-ALCHEMI result stays with the classical-MD route.
    if "model_out_of_domain" in fl or str(m.get("engine", "ALCHEMI")) != "ALCHEMI":
        need["engine"] = "classical_md"
        if "model_out_of_domain" in fl:
            fixes.append("classical MD (the MLIP is outside its training domain)")
    if "timestep_too_large_for_hydrogen" in fl:
        need["dt_fs"] = rules["max_timestep_fs_with_hydrogen"]
        fixes.append(f"a {need['dt_fs']} fs timestep (hydrogen present)")

    if prop in ("melting_point", "transition_temperature"):
        mode = "melting"
        if single_phase and SINGLE_PHASE_FLAGS & fl:
            need["t_guess_K"] = round(result["value"] / TYPICAL_SUPERHEATING)
            fixes.append(f"two-phase coexistence (bracket around {need['t_guess_K']} K, the heating value "
                         f"corrected for ~{round((TYPICAL_SUPERHEATING - 1) * 100)}% superheating)")
        elif len(temps) >= 2:
            need["temps_K"] = temps  # a two-phase run: keep its bracket
        elif origin.get("temps_K") or origin.get("t_guess_K"):
            need.update({k: origin[k] for k in ("temps_K", "t_guess_K") if origin.get(k)})
        else:
            need["t_guess_K"] = round(result["value"])
    elif prop in ("latent_heat", "transition_latent_heat"):
        mode = "latent"
        t = temps[0] if temps else origin.get("temperature_K")
        if t is None:
            return None
        need["temperature_K"] = t
    else:
        return None  # the Simulator only runs melting points and latent heats

    if not production:
        need["preset"] = "production"
        fixes.append("production settings (the result used the quick preset)")
    protocol_key = "two-phase coexistence" if mode == "melting" and not single_phase else protocol
    if "system_too_small" in fl and m.get("n_atoms"):
        min_atoms = rules["min_atoms"].get(protocol_key, rules["min_atoms"]["default"])
        reps = list(origin.get("reps") or DEFAULT_REPS[mode])
        need["reps"] = _scaled_reps(reps, m["n_atoms"], min_atoms)
        fixes.append(f"a larger supercell (reps {need['reps']}, at least {min_atoms} atoms)")
    if "run_too_short" in fl and m.get("sim_length_ps") and m.get("timestep_fs"):
        min_len = rules["min_sim_length_ps"].get(protocol_key, rules["min_sim_length_ps"]["default"])
        dt = need.get("dt_fs", m["timestep_fs"])
        need["prod_steps"] = math.ceil(min_len * 1000 / dt)
        fixes.append(f"a longer production run ({need['prod_steps']} steps = {min_len} ps)")
    # PREP_FLAGS (box preparation) have no request field of their own: on a quick run the production preset
    # fixes them; on a production run nothing a request can set changes them, so no fix is added and the
    # check carries no rerun (it would repeat the same run).
    if not fixes:
        return None
    return _finish(need, prop, name, flags, fixes)


def _finish(need: dict, prop: str, name: str, flags: list[str], fixes: list[str]) -> dict:
    need["query"] = f"rerun {prop} of {name} with " + " and ".join(fixes)
    need["rationale"] = "method audit failed on " + ", ".join(flags) + "; a corrected rerun supersedes this result"
    return need


class RuleReferee(Agent):
    role = "referee"

    def __init__(self, board: Board, name: str = AGENT_NAME, *, rules: Optional[dict] = None,
                 max_reruns: int = 2, **kw) -> None:
        super().__init__(board, name, **kw)
        self.rules = rules if rules is not None else load_audit_rules()
        self.max_reruns = max_reruns

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
            needs = []
            if flags:
                rerun = rerun_need(res.payload, flags, self.rules, origin_need(snap, res))
                if rerun is not None and rerun_depth(snap, res) >= self.max_reruns:
                    note += f" [no rerun requested: {self.max_reruns} reruns already in this lineage]"
                    rerun = None
                needs = [rerun] if rerun else []
            self.post("check", {"check_type": "method_audit", "verdict": verdict, "flags": flags, "note": note[:2000]},
                      parents=[res.artifact_id], fulfills=fulfills, needs=needs, skill="coolorbit.referee.rules")
            self.log("decision", result=res.artifact_id[:8], candidate=res.payload["candidate"],
                     property=res.payload["property"], verdict=verdict, flags=flags,
                     rerun=needs[0]["query"] if needs else None)
            return True
        return False


def main(argv: Optional[list[str]] = None) -> None:
    agent_cli(__doc__.split("\n\n")[0], lambda a, b: RuleReferee(b), argv=argv)


if __name__ == "__main__":
    main()
