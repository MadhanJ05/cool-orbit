"""Librarian: loads the sourced Tier-1 calibration dataset and posts ``reference_value`` artifacts.

The trust tier can only become "trusted" against a measured value with a source, so this agent is
on the critical path. It never invents numbers: every value comes from ``data/calibration/tier1.json``,
where each record carries a checkable source, and records with no verified source have ``value: null``.

Board contract (see ``coolorbit/board/status.py``): a trust-tier result is compared with EVERY sourced
``reference_value`` that has the same candidate name and property, and any mismatch flags it. So the
dataset must hold exactly one value per (candidate, property); other measurements go in ``alternates``
(kept in the JSON for the record, never posted).

Usage:
    python -m coolorbit.agents.librarian --board boards/dev-<name>            # post all records
    python -m coolorbit.agents.librarian --board boards/dev-<name> --only n-eicosane copper
    python -m coolorbit.agents.librarian --check                              # validate the dataset only
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from coolorbit.board import Board
from coolorbit.board.schema import ReferenceValue

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = REPO_ROOT / "data" / "calibration" / "tier1.json"
AGENT_NAME = "librarian"

# Property names and units the board's trust rules understand (configs/trust_rules.json).
# transition_latent_heat is for solid-solid PCMs; it has no trust rule yet (chemistry lead's call).
PROPERTY_UNITS = {
    "melting_point": "K",
    "transition_temperature": "K",
    "latent_heat": "J/kg",
    "transition_latent_heat": "J/kg",
    "density": "g/cm3",
    "conductivity": "W/m/K",
}
REQUIRED_KEYS = ("candidate", "family", "property", "value", "units", "source")
OPTIONAL_KEYS = ("formula", "uncertainty", "note", "phase", "temperature_K", "quote", "alternates")


class CalibrationError(ValueError):
    pass


def _norm(text: object) -> str:
    # Same normalisation as coolorbit.board.status, so uniqueness here means uniqueness there.
    return " ".join(str(text or "").lower().split())


def validate_record(rec: dict, where: str = "record") -> None:
    """Raise CalibrationError if one record breaks the dataset rules."""
    missing = [k for k in REQUIRED_KEYS if k not in rec]
    if missing:
        raise CalibrationError(f"{where}: missing keys {missing}")
    unknown = set(rec) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS)
    if unknown:
        raise CalibrationError(f"{where}: unknown keys {sorted(unknown)}")
    prop = rec["property"]
    if prop not in PROPERTY_UNITS:
        raise CalibrationError(f"{where}: unknown property {prop!r}; expected one of {sorted(PROPERTY_UNITS)}")
    if rec["units"] != PROPERTY_UNITS[prop]:
        raise CalibrationError(f"{where}: {prop} must be in {PROPERTY_UNITS[prop]}, got {rec['units']!r}")
    value, source = rec["value"], rec["source"]
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CalibrationError(f"{where}: value must be a number or null")
        if value <= 0:
            raise CalibrationError(f"{where}: value must be positive (absolute units), got {value}")
        if not (isinstance(source, str) and source.strip()):
            raise CalibrationError(f"{where}: a value needs a source; record value=null if none was found")
    if prop == "density" and value is not None and rec.get("phase") not in ("solid", "liquid"):
        raise CalibrationError(f"{where}: density needs phase 'solid' or 'liquid'")
    try:
        to_reference_payload(rec)
    except Exception as exc:  # pydantic ValidationError -> one error type for callers
        raise CalibrationError(f"{where}: board schema rejects it: {exc}") from exc


def validate_dataset(records: list[dict]) -> None:
    for i, rec in enumerate(records):
        validate_record(rec, f"record {i} ({rec.get('candidate')}/{rec.get('property')})")
    counts = Counter((_norm(r["candidate"]), r["property"]) for r in records)
    dupes = sorted(k for k, n in counts.items() if n > 1)
    if dupes:
        raise CalibrationError(
            f"more than one record per (candidate, property): {dupes}. The board compares a result with "
            "every reference for that pair, so keep one and move the others to 'alternates'.")


def load_calibration(path: str | Path = DEFAULT_DATA) -> list[dict]:
    """Load and validate the calibration dataset. Returns the list of records."""
    data = json.loads(Path(path).read_text())
    records = data["records"] if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise CalibrationError("expected a list of records (or {'records': [...]})")
    validate_dataset(records)
    return records


def to_reference_payload(rec: dict) -> dict:
    """Map a dataset record onto the board's ReferenceValue schema (validated)."""
    note_parts = [rec.get("note", "")]
    if rec.get("phase"):
        note_parts.append(f"phase: {rec['phase']}")
    if rec.get("temperature_K") is not None:
        note_parts.append(f"at {rec['temperature_K']} K")
    if rec.get("formula"):
        note_parts.append(f"formula: {rec['formula']}")
    payload = {
        "candidate": rec["candidate"],
        "property": rec["property"],
        "value": rec["value"],
        "units": rec["units"],
        "uncertainty": rec.get("uncertainty"),
        "source": rec["source"] or None,
        "note": "; ".join(p for p in note_parts if p),
    }
    return ReferenceValue.model_validate(payload).model_dump(mode="json")


def post_references(board: Board, records: Iterable[dict], *, agent: str = AGENT_NAME,
                    only: Optional[Iterable[str]] = None) -> dict:
    """Post one reference_value per record. Idempotent: identical references already on the board
    are skipped. If a candidate artifact with the same name exists, it becomes the parent (lineage).

    Returns counts {"posted", "skipped_existing", "null_values"} plus the posted artifact ids.
    """
    wanted = {_norm(n) for n in only} if only else None
    snap = board.snapshot()
    existing = {
        (_norm(a.payload["candidate"]), a.payload["property"], a.payload["value"], a.payload.get("source"))
        for a in snap.of_type("reference_value")
    }
    candidates = {_norm(a.payload["name"]): a.artifact_id for a in snap.of_type("candidate")}
    out = {"posted": 0, "skipped_existing": 0, "null_values": 0, "artifact_ids": []}
    for rec in records:
        name = _norm(rec["candidate"])
        if wanted is not None and name not in wanted:
            continue
        payload = to_reference_payload(rec)
        key = (name, payload["property"], payload["value"], payload["source"])
        if key in existing:
            out["skipped_existing"] += 1
            continue
        parents = [candidates[name]] if name in candidates else []
        art = board.post(agent, "reference_value", payload, parents=parents,
                         skill="coolorbit.librarian.tier1")
        existing.add(key)
        out["posted"] += 1
        out["null_values"] += payload["value"] is None
        out["artifact_ids"].append(art.artifact_id)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="calibration JSON (default: data/calibration/tier1.json)")
    ap.add_argument("--board", help="board directory to post to")
    ap.add_argument("--only", nargs="*", help="post only these candidate names")
    ap.add_argument("--agent", default=AGENT_NAME)
    ap.add_argument("--check", action="store_true", help="validate the dataset and exit")
    args = ap.parse_args(argv)

    try:
        records = load_calibration(args.data)
    except (CalibrationError, KeyError, json.JSONDecodeError) as exc:
        print(f"calibration dataset invalid: {exc}", file=sys.stderr)
        return 1
    n_null = sum(r["value"] is None for r in records)
    print(f"{len(records)} records ({len(records) - n_null} sourced values, {n_null} null/not found) "
          f"for {len({_norm(r['candidate']) for r in records})} materials")
    if args.check or not args.board:
        return 0
    out = post_references(Board(args.board), records, agent=args.agent, only=args.only)
    print(f"posted {out['posted']} reference_value artifacts ({out['null_values']} null), "
          f"skipped {out['skipped_existing']} already on the board")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
