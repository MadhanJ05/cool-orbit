"""Run several agents round-robin in one process (laptop development and demos).

In production each agent is its own process (``python -m coolorbit.agents.<module> --board ...``);
this helper just steps them in turn until a full round is idle.

    python -m coolorbit.agents --board boards/dev-<name> --fake-sim [--trap Cu=silent_heating]
    python -m coolorbit.agents --board boards/<id> --scouts scout_metals --no-ranker
"""

from __future__ import annotations

import argparse

from coolorbit.agents.base import run_round_robin
from coolorbit.agents.librarian import LibrarianAgent
from coolorbit.agents.premise import PremiseChecker
from coolorbit.agents.ranker import Ranker
from coolorbit.agents.referee_rules import RuleReferee
from coolorbit.agents.scout import Scout, load_seeds
from coolorbit.board import Board


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board", required=True)
    ap.add_argument("--scouts", nargs="*", help="Scouts to run (default: all enabled)")
    ap.add_argument("--preset", choices=["quick", "production"], help="simulation preset the Scouts request")
    ap.add_argument("--fake-sim", action="store_true", help="add the synthetic FakeSimulator (dev-* boards only)")
    ap.add_argument("--trap", action="append", default=[], help="FakeSimulator trap: candidate=silent_heating")
    ap.add_argument("--no-ranker", action="store_true")
    ap.add_argument("--llm", default="inconclusive", choices=["inconclusive", "always", "never"],
                    help="when the premise checker asks Claude (needs ANTHROPIC_API_KEY in .env)")
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--eval-only", action="store_true", help="Scouts answer eval tasks only (comparison runs)")
    args = ap.parse_args()

    board = Board(args.board)
    seeds = load_seeds()
    # The premise checker goes first so requests are checked before anyone runs them.
    agents = [PremiseChecker(board, client=None if args.llm == "never" else "auto", llm_mode=args.llm)]
    agents += [Scout(board, s, seeds=seeds, preset=args.preset, eval_only=args.eval_only) for s in (args.scouts or seeds)]
    if args.fake_sim:
        from coolorbit.agents.fake_simulator import FakeSimulatorAgent

        agents.append(FakeSimulatorAgent(args.board, traps=dict(t.split("=", 1) for t in args.trap)))
    agents += [RuleReferee(board), LibrarianAgent(board)]
    if not args.no_ranker:
        agents.append(Ranker(board, include_synthetic=args.fake_sim))  # synthetic entries are labelled
    rounds = run_round_robin(agents, args.rounds)
    print(f"idle after {rounds} round(s). View: python -m coolorbit.board show --board {args.board} -v")


if __name__ == "__main__":
    main()
