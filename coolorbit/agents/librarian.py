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
    python -m coolorbit.agents.librarian --board boards/<id> --serve [--once] # answer reference_value needs

Names: the board matches references by candidate name, so a record can list ``aliases`` (e.g. copper ->
"Cu"). Bulk posting posts the canonical name and every alias; serving a need posts the reference under
the exact name the result uses.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from coolorbit.agents.base import Agent, candidate_of, norm as _norm
from coolorbit.board import Board, OpenNeed
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
OPTIONAL_KEYS = ("formula", "uncertainty", "note", "phase", "temperature_K", "quote", "alternates", "aliases")


class CalibrationError(ValueError):
    pass


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
    aliases = rec.get("aliases", [])
    if not (isinstance(aliases, list) and all(isinstance(a, str) and a.strip() for a in aliases)):
        raise CalibrationError(f"{where}: aliases must be a list of non-empty names")
    if prop == "density" and value is not None and rec.get("phase") not in ("solid", "liquid"):
        raise CalibrationError(f"{where}: density needs phase 'solid' or 'liquid'")
    try:
        to_reference_payload(rec)
    except Exception as exc:  # pydantic ValidationError -> one error type for callers
        raise CalibrationError(f"{where}: board schema rejects it: {exc}") from exc


def validate_dataset(records: list[dict]) -> None:
    for i, rec in enumerate(records):
        validate_record(rec, f"record {i} ({rec.get('candidate')}/{rec.get('property')})")
    counts = Counter((_norm(n), r["property"]) for r in records for n in names_of(r))
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


def names_of(rec: dict) -> list[str]:
    """Canonical name plus aliases (deduplicated after normalisation)."""
    out, seen = [], set()
    for n in [rec["candidate"], *rec.get("aliases", [])]:
        if _norm(n) not in seen:
            seen.add(_norm(n))
            out.append(n)
    return out


def index_records(records: Iterable[dict]) -> dict[tuple[str, str], dict]:
    """(normalised name or alias, property) -> record."""
    return {(_norm(n), r["property"]): r for r in records for n in names_of(r)}


def to_reference_payload(rec: dict, name: Optional[str] = None) -> dict:
    """Map a dataset record onto the board's ReferenceValue schema (validated).

    ``name`` overrides the candidate name (used to post under an alias or the result's exact name).
    """
    note_parts = [rec.get("note", "")]
    if rec.get("phase"):
        note_parts.append(f"phase: {rec['phase']}")
    if rec.get("temperature_K") is not None:
        note_parts.append(f"at {rec['temperature_K']} K")
    if rec.get("formula"):
        note_parts.append(f"formula: {rec['formula']}")
    payload = {
        "candidate": name or rec["candidate"],
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
    """Post one reference_value per record and per alias. Idempotent: identical references already on
    the board are skipped. If a candidate artifact with the same name exists, it becomes the parent.

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
        names = names_of(rec)
        if wanted is not None and not wanted & {_norm(n) for n in names}:
            continue
        for display in names:
            name = _norm(display)
            payload = to_reference_payload(rec, display)
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


class LibrarianAgent(Agent):
    """Answers ``reference_value`` needs (the Simulator broadcasts one for every trust-tier result).

    It looks the (candidate, property) up in the curated dataset, by name or alias, and posts the
    reference under the exact name the result uses, fulfilling the need. If the dataset has nothing,
    it says so with a null reference instead of leaving the need hanging or guessing.
    """

    role = "librarian"

    def __init__(self, board: Board, name: str = AGENT_NAME, *, records: Optional[list[dict]] = None,
                 data_path: str | Path = DEFAULT_DATA, **kw) -> None:
        super().__init__(board, name, **kw)
        self.records = records if records is not None else load_calibration(data_path)
        self.index = index_records(self.records)

    @staticmethod
    def _wants(on: OpenNeed) -> bool:
        return on.need.get("artifact_type") == "reference_value"

    def _subject(self, on: OpenNeed) -> tuple[Optional[str], Optional[str]]:
        """(candidate name, property) the need is about."""
        p = on.artifact.payload
        if on.artifact.artifact_type == "property_result":
            return p.get("candidate"), on.need.get("property") or p.get("property")
        cand = candidate_of(self.board.snapshot(), on.artifact)
        return (cand.payload["name"] if cand else None), on.need.get("property")

    def step(self) -> bool:
        for on in self.open_needs(self._wants):
            name, prop = self._subject(on)
            if not name or not prop:
                self.log("skip", target=on.artifact.artifact_id[:8], reason="cannot tell which candidate/property")
                continue
            if not self.claim(on, ttl_minutes=10):
                continue
            rec = self.index.get((_norm(name), prop))
            if rec is not None:
                payload = to_reference_payload(rec, name)
                why = "found in curated dataset" if rec["value"] is not None else "dataset records it as not found"
            else:
                payload = to_reference_payload(
                    {"candidate": name, "property": prop, "value": None, "units": PROPERTY_UNITS.get(prop, "unknown"),
                     "source": None,
                     "note": f"NOT FOUND: no measured {prop} for {name!r} in data/calibration/tier1.json; "
                             "the Librarian only serves curated, sourced values"}) if prop in PROPERTY_UNITS else None
                why = "not in curated dataset"
            if payload is None:
                self.log("decline", target=on.artifact.artifact_id[:8], reason=f"unknown property {prop!r}")
                continue
            self.post("reference_value", payload, fulfills=(on.artifact.artifact_id, on.index),
                      skill="coolorbit.librarian.tier1")
            self.log("decision", candidate=name, property=prop, value=payload["value"], reason=why)
            return True
        return False


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="calibration JSON (default: data/calibration/tier1.json)")
    ap.add_argument("--board", help="board directory to post to")
    ap.add_argument("--only", nargs="*", help="post only these candidate names")
    ap.add_argument("--agent", default=AGENT_NAME)
    ap.add_argument("--check", action="store_true", help="validate the dataset and exit")
    ap.add_argument("--serve", action="store_true", help="run as an agent that answers reference_value needs")
    ap.add_argument("--once", action="store_true", help="with --serve: answer what is open now, then exit")
    ap.add_argument("--poll", type=float, default=30.0)
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
    if args.serve:
        LibrarianAgent(Board(args.board), args.agent, records=records).run(once=args.once, poll_s=args.poll)
        return 0
    out = post_references(Board(args.board), records, agent=args.agent, only=args.only)
    print(f"posted {out['posted']} reference_value artifacts ({out['null_values']} null), "
          f"skipped {out['skipped_existing']} already on the board")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
