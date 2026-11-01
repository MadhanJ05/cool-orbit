"""Science run: the collective screens thermal-battery candidates on the GB10 (CLAUDE.md sections 5, 8 and 9).

No comparison arms and no Claude Code worker. The team, stepped round-robin in one process until nothing is left:
  referee_premise   checks every simulation request before it runs (rules; Claude only when rules can't decide)
  scout_metals      the pure metals chosen with --metals (melting point by two-phase coexistence, then latent heat
                    at the simulated melting point once a referee has passed it)
  scout_alloys      calibration alloys chosen with --alloys (latent heat at the measured melting point)
  other Scouts      waxes, fatty acids, sugar alcohols, salt hydrates: posted with classical-MD requests that no
                    engine here serves, so the gap is visible (--no-organics leaves them out)
  simulator_gb10    the real ALCHEMI/MACE Simulator on the GB10 GPU
  referee_rules, librarian, ranker
The board is the state: rerunning the same command resumes (a claim on an interrupted job expires on its own).

    python scripts/run_science.py --board boards/science-1 --dry-run        # the plan and a GPU estimate only
    nohup .venv/bin/python scripts/run_science.py --board boards/science-1 > runs/science-1.log 2>&1 &
"""

from __future__ import annotations

import argparse
import copy
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from coolorbit.agents.alloy_scout import AlloyScout  # noqa: E402
from coolorbit.agents.base import norm  # noqa: E402
from coolorbit.agents.librarian import DEFAULT_DATA, LibrarianAgent, index_records, load_calibration  # noqa: E402
from coolorbit.agents.premise import PremiseChecker  # noqa: E402
from coolorbit.agents.ranker import Ranker  # noqa: E402
from coolorbit.agents.referee_rules import RuleReferee  # noqa: E402
from coolorbit.agents.scout import Scout, load_seeds  # noqa: E402
from coolorbit.board import Board  # noqa: E402
from coolorbit.sim.simulator_agent import JobError, SimulatorAgent, build_job, subprocess_runner  # noqa: E402

DEFAULT_METALS = ["In", "Sn", "Bi", "Ga"]


def science_seeds(metals: list[str], organics: bool) -> dict:
    """Scout seeds for this run: scout_metals limited to ``metals`` (in that order); other families optional."""
    seeds = copy.deepcopy(load_seeds())
    by_name = {norm(c["name"]): c for c in seeds["scout_metals"]["candidates"]}
    missing = [m for m in metals if norm(m) not in by_name]
    if missing:
        raise SystemExit(f"unknown metals {missing}; scout_metals has {[c['name'] for c in by_name.values()]}")
    seeds["scout_metals"]["candidates"] = [by_name[norm(m)] for m in metals]
    seeds["scout_metals"]["budget"] = len(metals)
    keep = {"scout_metals"} | ({s for s, c in seeds.items() if not c.get("general")} if organics else set())
    return {s: c for s, c in seeds.items() if s in keep}


def build_agents(board: Board, seeds: dict, alloys: list[str], *, preset: Optional[str], llm: str,
                 runner: Callable, runs_root: Path, simulator: bool = True) -> list:
    """``simulator=False`` leaves the GPU to Simulators in their own processes (python -m
    coolorbit.sim.simulator_agent, scripts/run_remote_simulator.py): a GPU job inside this round-robin loop blocks
    every other agent for its whole run (seen 2026-11-01: the Scout could not post the next metal for 73 min)."""
    agents = [PremiseChecker(board, client=None if llm == "never" else "auto", llm_mode=llm)]
    if alloys:
        agents.append(AlloyScout(board, only=alloys, preset=preset))
    agents += [Scout(board, s, seeds=seeds, preset=preset) for s in seeds]
    if simulator:
        agents.append(SimulatorAgent(board, "simulator_gb10", runner=runner, runs_root=runs_root))
    agents += [RuleReferee(board), LibrarianAgent(board), Ranker(board)]
    return agents


def run_until_idle(agents: list, max_rounds: int, until: Optional[float] = None, poll: float = 30.0,
                   clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Step every agent in turn until a full round does nothing. One agent's crash is recorded, not fatal.

    With ``until`` (epoch seconds) an idle round waits ``poll`` seconds and looks again until then, so results posted
    by agents in other processes (e.g. a second Simulator on a remote GPU) still get refereed and followed up.
    """
    rounds, errors, t0 = 0, [], clock()
    while rounds < max_rounds:
        rounds += 1
        did = False
        for agent in agents:
            try:
                did = bool(agent.step()) or did
            except Exception as exc:  # noqa: BLE001
                errors.append(f"round {rounds} {getattr(agent, 'name', type(agent).__name__)}: {exc!r}"[:300])
                traceback.print_exc()
        if not did:
            if until is not None and clock() < until:
                sleep(poll)
                continue
            return {"stopped": "idle" if until is None else "until", "rounds": rounds, "errors": errors,
                    "wall_min": (clock() - t0) / 60}
    return {"stopped": "max_rounds", "rounds": rounds, "errors": errors, "wall_min": (time.time() - t0) / 60}


def plan(seeds: dict, alloys: list[str], preset: Optional[str]) -> list[dict]:
    """The GPU jobs this run will start with, each with the Simulator's own time estimate (nothing runs).

    Uses a throwaway board: the Scouts post, then every request is parsed exactly as the Simulator would. Pure
    metals also get the latent-heat follow-up, estimated at the measured melting point (the real one is requested
    at the simulated melting point, after a referee passes it).
    """
    refs = index_records(load_calibration(DEFAULT_DATA))
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        board = Board(Path(tmp) / "plan")
        scouts = ([AlloyScout(board, only=alloys, preset=preset, verbose=False)] if alloys else []) + \
                 [Scout(board, s, seeds=seeds, preset=preset, verbose=False) for s in seeds]
        while any([s.step() for s in scouts]):
            pass
        for cand in board.snapshot().of_type("candidate"):
            tm = (refs.get((norm(cand.payload["name"]), "melting_point")) or {}).get("value")
            first = cand.needs[0] if cand.needs else {}
            if first.get("property") == "melting_point" and first.get("engine") in (None, "", "ALCHEMI") and tm:
                board.post("planner", "request", {"note": "latent heat follow-up (estimate only)"},
                           parents=[cand.artifact_id],
                           needs=[{"artifact_type": "property_result", "property": "latent_heat",
                                   "query": f"latent_heat of {cand.payload['name']} (estimate)",
                                   "rationale": "follow-up after a passed melting point", "temperature_K": tm,
                                   **({"preset": preset} if preset else {})}])
        snap = board.snapshot()
        for on in board.open_needs():
            if on.need.get("artifact_type") != "property_result":
                continue
            cand = snap.artifacts[on.artifact.artifact_id] if on.artifact.artifact_type == "candidate" else \
                snap.artifacts[on.artifact.parent_artifact_ids[0]]
            row = {"candidate": cand.payload["name"], "tier": cand.payload["tier"], "property": on.need["property"]}
            try:
                job = build_job(snap, on)
            except JobError as err:
                row.update(gpu="refused", note=err.reason[:90])
            else:
                if job is None:
                    row.update(gpu="none", note=f"needs engine {on.need.get('engine')!r}: not served on this board")
                else:
                    row.update(gpu="GB10", mode=job.mode, atoms=job.n_atoms, temps=len(job.temps),
                               minutes=round(job.estimate_minutes(), 1), note=f"reps {job.reps}, preset {job.preset}")
            rows.append(row)
    return rows


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True, help="e.g. boards/science-1 (created if missing; resumed if present)")
    ap.add_argument("--metals", nargs="*", default=DEFAULT_METALS, help=f"pure metals (default: {DEFAULT_METALS})")
    ap.add_argument("--alloys", nargs="*", default=None,
                    help="calibration alloys, e.g. BiInSn InBi (default: every alloy with a sourced melting point)")
    ap.add_argument("--no-organics", action="store_true", help="leave out the classical-MD families")
    ap.add_argument("--preset", choices=["quick", "production"], default=None, help="default: production")
    ap.add_argument("--llm", choices=["inconclusive", "always", "never"], default="inconclusive")
    ap.add_argument("--max-rounds", type=int, default=10_000)
    ap.add_argument("--no-simulator", action="store_true",
                    help="run the team without an in-process Simulator (run the Simulators as separate processes)")
    ap.add_argument("--until", default=None,
                    help="keep watching the board until this UTC time (ISO, no zone) instead of stopping when idle, "
                         "e.g. while a second Simulator in another process (scripts/run_remote_simulator.py) works")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and GPU estimate; run nothing")
    args = ap.parse_args(argv)

    seeds = science_seeds(args.metals, organics=not args.no_organics)
    if args.alloys is None:
        from coolorbit.agents.alloy_scout import alloy_seeds

        args.alloys = [s["name"] for s in alloy_seeds(load_calibration(DEFAULT_DATA)) if s["melting_point_K"]]
    if args.dry_run:
        rows = plan(seeds, args.alloys, args.preset)
        for r in rows:
            print(f"{r['candidate']:28s} {r['tier']:9s} {r['property']:13s} {r['gpu']:7s} "
                  f"{(str(r.get('atoms', '')) + ' at') if r.get('atoms') else '':8s} "
                  f"{r.get('minutes', ''):>6} min  {r['note']}")
        gpu = sum(r.get("minutes", 0) for r in rows)
        print(f"\n{len(rows)} request(s); GB10 estimate {gpu:.0f} min = {gpu / 60:.1f} h, run one at a time "
              "(reruns asked by the referee come on top)")
        return
    board = Board(REPO_ROOT / args.board if not Path(args.board).is_absolute() else args.board)
    runs_root = REPO_ROOT / "runs" / "science" / board.root.name
    print(f"science run on {board.root}: metals {args.metals}, alloys {args.alloys}, "
          f"organics {'no' if args.no_organics else 'yes'}, preset {args.preset or 'production'}", flush=True)
    until = None
    if args.until:
        from datetime import datetime, timezone

        until = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc).timestamp()
    out = run_until_idle(build_agents(board, seeds, args.alloys, preset=args.preset, llm=args.llm,
                                      runner=subprocess_runner, runs_root=runs_root,
                                      simulator=not args.no_simulator), args.max_rounds, until=until)
    print(f"{out['stopped']} after {out['rounds']} round(s), {out['wall_min']:.0f} min; agent errors: "
          f"{len(out['errors'])}", flush=True)
    for e in out["errors"]:
        print("  " + e)
    print(f"view: .venv/bin/python -m coolorbit.board show --board {args.board} -v")


if __name__ == "__main__":
    main()
