"""Post a result whose remote run finished after the runner gave up (e.g. an SSH timeout), with full provenance.

On 2026-11-01 the SSH runner's timeout (3 x the idle-GPU estimate + 15 min) expired at 51 min while a Cu latent-heat
run on the shared H100 finished at 56 min. The Simulator posted a failed run_report; the run itself completed and its
outputs are on disk. This posts that run's result as the same Simulator's ``property_result``, built exactly as the
Simulator builds it (coolorbit.sim.results.build_property_payload), parented to the candidate and to the failed report,
with a note saying it was recovered, and with the trust rule's needs so the referees check it like any other result.

    .venv/bin/python scripts/recover_remote_result.py --board boards/science-2 --report 3b1c... --run-dir <local copy>
        [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from coolorbit.board import Board  # noqa: E402
from coolorbit.sim.results import PROTOCOLS, build_property_payload  # noqa: E402
from coolorbit.sim.simulator_agent import find_candidate  # noqa: E402


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True)
    ap.add_argument("--report", required=True, help="id (or prefix) of the failed run_report")
    ap.add_argument("--run-dir", required=True, help="local copy of the run folder holding result.json/settings.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    board = Board(REPO_ROOT / args.board)
    snap = board.snapshot()
    hits = [a for a in snap.of_type("run_report") if a.artifact_id.startswith(args.report)]
    if len(hits) != 1:
        raise SystemExit(f"need exactly one run_report matching {args.report!r}, found {len(hits)}")
    report = hits[0]
    if report.payload["status"] != "failed":
        raise SystemExit("that run_report is not a failure")
    cand = find_candidate(snap, report)
    if cand is None:
        raise SystemExit("could not find the candidate behind that report")
    run_dir = Path(args.run_dir)
    result = json.loads((run_dir / "result.json").read_text())
    settings = json.loads((run_dir / "settings.json").read_text())
    if result.get("status") != "ok":
        raise SystemExit(f"the run did not succeed either: {result.get('status')} {result.get('reason')}")
    payload = build_property_payload(cand.payload, result, settings["settings"], settings["n_atoms"], str(run_dir))
    payload["note"] = (f"Recovered result: the remote run completed after the runner gave up "
                       f"(failed report {report.artifact_id[:8]}: {report.payload['reason'][:120]}). Same run folder; "
                       f"posted with scripts/recover_remote_result.py.")
    prop, name = payload["property"], cand.payload["name"]
    mode = settings["settings"].get("mode")
    protocol = PROTOCOLS.get(mode, mode)
    needs = [
        {"artifact_type": "check", "check_type": "method_audit", "query": f"audit the {protocol} {prop} run for {name}",
         "rationale": "trust rule (a): another agent must audit the simulation method"},
        {"artifact_type": "check", "check_type": "classical_md",
         "query": f"independent {prop} for {name} with a different method (classical MD or a second MLIP)",
         "rationale": "trust rule (b): an independent method must agree within the pre-registered tolerance"},
    ]
    if cand.payload["tier"] == "trust":
        needs.append({"artifact_type": "reference_value", "query": f"measured {prop} of {name} with a citable source",
                      "rationale": "trust rule (c): trust-tier results must match a sourced measurement"})
    print(f"{'would post' if args.dry_run else 'post'} as {report.producer_agent}: {name} {prop} = {payload['value']} "
          f"+/- {payload['uncertainty']} {payload['units']} (parents: candidate {cand.artifact_id[:8]}, "
          f"report {report.artifact_id[:8]})")
    if not args.dry_run:
        art = board.post(report.producer_agent, "property_result", payload,
                         parents=[cand.artifact_id, report.artifact_id], needs=needs, skill="coolorbit.sim.melting")
        print(f"posted {art.artifact_id}")


if __name__ == "__main__":
    main()
