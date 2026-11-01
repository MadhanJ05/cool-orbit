"""Overnight helper (2026-11-01, approved by Madhan before he slept): post bismuth's classical check when MACE lands.

Watches boards/science-1 every 2 minutes until --until (UTC). When a live (not superseded) MACE melting_point result
for Bi is on the board, and the Librarian has posted Bi's measured melting point (or 20 minutes have passed), it
posts the classical-MD check exactly as scripts/post_classical_checks.py does (--run meam-bi-refine, the
pre-registered reliability gate on), once. The trust rule then derives Bi's status; nothing here sets a status.

It never kills, starts or claims anything else. The science run's own agents keep working (the latent-heat follow-up
at MACE's melting point is requested by the system after a referee passes the melting point). Everything is logged
to runs/overnight.log, including Bi's derived status every time it changes.

    .venv/bin/python scripts/overnight_queue.py [--until 2026-11-01T08:00] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import post_classical_checks as pcc  # noqa: E402
from coolorbit.agents.base import norm  # noqa: E402
from coolorbit.board import Board, derive_status, load_rules  # noqa: E402

BOARD = REPO_ROOT / "boards" / "science-1"
LOG = REPO_ROOT / "runs" / "overnight.log"
RUN = "meam-bi-refine"
CANDIDATE = "Bi"
WAIT_FOR_REFERENCE = timedelta(minutes=20)


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def bi_results(snap, rules):
    """Live MACE melting_point results for Bi (not our classical agent's, not superseded by a rerun)."""
    return [r for r in snap.of_type("property_result")
            if norm(r.payload["candidate"]) == norm(CANDIDATE) and r.payload["property"] == "melting_point"
            and r.producer_agent != pcc.AGENT and not derive_status(snap, r, rules).superseded_by]


def ts(artifact) -> datetime:
    return datetime.fromisoformat(artifact.timestamp)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--until", default="2026-11-01T08:00", help="stop at this UTC time (ISO, no zone)")
    ap.add_argument("--poll", type=float, default=120.0)
    ap.add_argument("--dry-run", action="store_true", help="log what it would post; post nothing")
    ap.add_argument("--once", action="store_true", help="one pass, then exit (for testing)")
    args = ap.parse_args(argv)
    until = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
    board, rules = Board(BOARD), load_rules()
    log(f"start: watching {BOARD.relative_to(REPO_ROOT)} for a MACE {CANDIDATE} melting point; classical run {RUN}; "
        f"until {until.isoformat()}; dry_run={args.dry_run}")
    posted, last_status, seen_reports = False, {}, set()
    while datetime.now(timezone.utc) < until:
        try:
            snap = board.snapshot()
            for rep in snap.of_type("run_report"):
                task = rep.payload.get("task", "")
                about = task.split(" of ", 1)[1].split(" ", 1)[0] if " of " in task else ""
                if rep.artifact_id not in seen_reports and about == CANDIDATE:  # "melting_point of Bi via ..."
                    seen_reports.add(rep.artifact_id)
                    log(f"run report {rep.artifact_id[:8]}: {rep.payload['status']}: {rep.payload['task']}: "
                        f"{rep.payload['reason']}")
            results = bi_results(snap, rules)
            for r in results:
                st = derive_status(snap, r, rules)
                key = (st.status, tuple(st.reasons))
                if last_status.get(r.artifact_id) != key:
                    last_status[r.artifact_id] = key
                    log(f"Bi result {r.artifact_id[:8]} = {r.payload['value']:g} +/- {r.payload.get('uncertainty')} K "
                        f"flags={r.payload.get('flags')}: status {st.status.upper()}; " + " | ".join(st.reasons))
            if results and not posted:
                newest = max(results, key=ts)
                has_ref = pcc.measured(snap, CANDIDATE, "melting_point") is not None
                waited = datetime.now(timezone.utc) - ts(newest)
                if has_ref or waited >= WAIT_FOR_REFERENCE:
                    items = [i for i in pcc.plan(board, RUN, rules, gate=True)
                             if norm(i["result"].payload["candidate"]) == norm(CANDIDATE)]
                    if not items:
                        log("nothing to post (already checked, or no MEAM analysis for Bi in this run)")
                    for item in items:
                        res = item["result"]
                        if "skip" in item:
                            log(f"skip {res.artifact_id[:8]}: {item['skip']}")
                            continue
                        pl = item["payload"]
                        log(f"{'WOULD POST' if args.dry_run else 'POST'} classical_md check on {res.artifact_id[:8]} "
                            f"(MACE {res.payload['value']:g} K): verdict {pl['verdict']}, value {pl.get('value')}, "
                            f"flags {pl.get('flags', [])}; measured value on board: {has_ref}")
                        if not args.dry_run:
                            board.post(pcc.AGENT, "check", pl, parents=[res.artifact_id], fulfills=item["fulfills"],
                                       skill="coolorbit.referee.classical_md")
                    posted = True
                else:
                    log(f"Bi result {newest.artifact_id[:8]} found; waiting for the Librarian's measured value "
                        f"({waited.seconds // 60} of {WAIT_FOR_REFERENCE.seconds // 60} min)")
        except Exception:  # never die silently overnight; log and keep watching
            log("ERROR:\n" + traceback.format_exc())
        if args.once:
            break
        time.sleep(args.poll)
    log(f"stop: {'one pass (--once)' if args.once else 'reached ' + until.isoformat()} (posted={posted})")


if __name__ == "__main__":
    main()
