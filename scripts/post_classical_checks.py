"""Post the classical-MD (MEAM / LAMMPS) melting points as independent ``classical_md`` checks on MACE results.

The board's trust rule needs an independent method that agrees numerically (status.py, rule b). This posts, as
agent ``referee_classical``, one check per MACE ``melting_point`` result on the board whose candidate has a MEAM
analysis (runs/classical/<run>/metals/<El>/analysis_coexistence.json), fulfilling the result's open classical_md need.

Reliability gate (default on): a classical potential that itself misses the sourced measured value by more than the
trust rules' tier1 tolerance is not a usable second opinion. Its check is posted as ``inconclusive`` WITHOUT a value
(the number and the reason are in the note), so it can neither confirm nor flag the MACE result. Otherwise the check
carries the MEAM value, units and method, and its verdict says whether it agrees within the "agree" tolerance (the
trust rule re-derives agreement from the numbers anyway).

    python scripts/post_classical_checks.py --board boards/science-1 --run meam-1 [--dry-run] [--no-gate]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from coolorbit.agents.base import norm  # noqa: E402
from coolorbit.board import Board, derive_status, load_rules  # noqa: E402

AGENT = "referee_classical"


def within(value: float, ref: float, tol: dict) -> bool:
    return abs(value - ref) <= max(tol.get("abs") or 0.0, (tol.get("rel") or 0.0) * abs(ref))


def measured(snap, candidate: str, prop: str):
    refs = [r for r in snap.of_type("reference_value") if norm(r.payload["candidate"]) == norm(candidate)
            and r.payload["property"] == prop and r.payload["value"] is not None]
    return refs[-1] if refs else None


def gate_reference(snap, candidate: str, prop: str) -> "tuple[Optional[float], Optional[str]]":
    """(measured value, source) for the reliability gate: the board's sourced reference_value, else the same sourced
    calibration set the Librarian posts from (data/calibration/tier1.json). Without the fallback, a potential that
    misses the measurement would be counted whenever the Librarian had not posted yet (found 2026-11-01 for Al)."""
    ref = measured(snap, candidate, prop)
    if ref is not None:
        return ref.payload["value"], ref.payload.get("source")
    from coolorbit.agents.librarian import DEFAULT_DATA, index_records, load_calibration

    rec = index_records(load_calibration(DEFAULT_DATA)).get((norm(candidate), prop)) or {}
    if rec.get("value") is not None and str(rec.get("source") or "").strip():
        return float(rec["value"]), rec["source"]
    return None, None


PROPS = {  # property -> (analysis file, units, how the number was made)
    "melting_point": ("analysis_coexistence.json", "K", "two-phase coexistence: Tm"),
    "latent_heat": ("analysis_latent.json", "J/kg", "solid vs liquid NPT enthalpy at the potential's own Tm: L"),
}


def plan(board: Board, run: str, rules: dict, gate: bool,
         root: Path = REPO_ROOT / "runs" / "classical", prop: str = "melting_point") -> list[dict]:
    snap = board.snapshot()
    fname, units, how = PROPS[prop]
    tol = (rules.get("properties") or {}).get(prop) or {}
    out = []
    for res in snap.of_type("property_result"):
        p = res.payload
        if p["property"] != prop or derive_status(snap, res, rules).superseded_by:
            continue
        if res.producer_agent == AGENT or any(k.producer_agent == AGENT for k in snap.children.get(res.artifact_id, [])):
            continue  # not our own, and not twice
        f = Path(root) / run / "metals" / p["candidate"] / fname
        if not f.exists():
            continue
        a = json.loads(f.read_text())
        if a.get("status") != "ok":
            out.append({"result": res, "skip": f"classical analysis for {p['candidate']} is {a.get('status')}: "
                                               f"{a.get('reason')}"})
            continue
        if a.get("units", units) != p["units"]:
            out.append({"result": res, "skip": f"units differ: classical {a.get('units')} vs result {p['units']}"})
            continue
        method = {k: v for k, v in (a.get("method") or {}).items() if v is not None}
        method.setdefault("engine", "LAMMPS")
        ref_value, ref_source = gate_reference(snap, p["candidate"], prop)
        note = (f"Classical MD ({method.get('model', 'classical potential')}) {how} = {a['value']:g} +/- "
                f"{a['uncertainty']:g} {units}" + (f", bracket {a.get('bracket_K')}" if a.get("bracket_K") else "")
                + f"; from runs/classical/{run}/metals/{p['candidate']}/{fname}.")
        unreliable = gate and ref_value is not None and tol.get("tier1") and not within(a["value"], ref_value,
                                                                                         tol["tier1"])
        if unreliable:
            payload = {"check_type": "classical_md", "verdict": "inconclusive", "flags": ["independent_method_unreliable"],
                       "note": note + f" Not counted: this potential misses the measured {ref_value:g} {units} "
                               f"[{ref_source}] by {a['value'] - ref_value:+.4g} {units}, beyond the tier1 tolerance "
                               f"{tol['tier1']}."}
        else:
            agree = bool(tol.get("agree")) and within(a["value"], p["value"], tol["agree"])
            payload = {"check_type": "classical_md", "verdict": "agree" if agree else "disagree", "value": a["value"],
                       "units": units, "uncertainty": a["uncertainty"], "method": method,
                       "note": note + (f" Measured {ref_value:g} {units} [{ref_source}]; the potential is within the "
                                       f"tier1 tolerance of it." if ref_value is not None
                                       else " No sourced measured value, so this potential is not calibrated.")}
        need = next(((res.artifact_id, i) for i, n in enumerate(res.needs)
                     if n.get("artifact_type") == "check" and n.get("check_type") == "classical_md"), None)
        out.append({"result": res, "payload": payload, "fulfills": need})
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True)
    ap.add_argument("--run", default="meam-1")
    ap.add_argument("--no-gate", action="store_true", help="count classical values even when they miss the measurement")
    ap.add_argument("--property", choices=sorted(PROPS), default="melting_point")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    board, rules = Board(args.board), load_rules()
    for item in plan(board, args.run, rules, gate=not args.no_gate, prop=args.property):
        res = item["result"]
        label = f"{res.payload['candidate']} {res.payload['value']:g} {res.payload['units']} ({res.artifact_id[:8]})"
        if "skip" in item:
            print(f"skip {label}: {item['skip']}")
            continue
        pl = item["payload"]
        print(f"{'would post' if args.dry_run else 'post'} on {label}: {pl['verdict']} "
              f"{pl.get('value', '(no value)')} {pl.get('flags', '')}")
        if not args.dry_run:
            board.post(AGENT, "check", pl, parents=[res.artifact_id], fulfills=item["fulfills"],
                       skill="coolorbit.referee.classical_md")


if __name__ == "__main__":
    main()
