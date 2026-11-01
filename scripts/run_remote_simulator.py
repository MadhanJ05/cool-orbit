"""A second Simulator agent whose GPU is a remote machine reached over SSH (the Brev H100), on the same board.

It is the ordinary SimulatorAgent (claims needs on the board, posts results and run reports with full method metadata)
with coolorbit.sim.ssh_runner.SshRunner as its runner, so the GB10's simulator and this one split the queue through
claims, with no coordinator. Stops at --until (UTC).

    COOLORBIT_COEX_PROTOCOL=2 .venv/bin/python scripts/run_remote_simulator.py --board boards/science-2 \
        --agent simulator_h100 --host coolorbit-h100-host [--until 2026-11-01T13:00]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from coolorbit.board import Board  # noqa: E402
from coolorbit.sim.simulator_agent import SimulatorAgent  # noqa: E402
from coolorbit.sim.ssh_runner import SshRunner  # noqa: E402


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True)
    ap.add_argument("--agent", default="simulator_h100")
    ap.add_argument("--host", default="coolorbit-h100-host", help="Host entry in ~/.brev/ssh_config")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--remote-dir", default="~/cool-orbit",
                    help="repo checkout on the remote the runner may switch commits in (keep it separate from any "
                         "checkout other jobs are running from)")
    ap.add_argument("--until", default="2026-11-01T13:00", help="stop at this UTC time (ISO, no zone)")
    ap.add_argument("--poll", type=float, default=30.0)
    ap.add_argument("--min-minutes", type=float, default=0.0)
    ap.add_argument("--max-minutes", type=float, default=float("inf"))
    args = ap.parse_args(argv)
    until = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
    board = Board(REPO_ROOT / args.board if not Path(args.board).is_absolute() else args.board)
    agent = SimulatorAgent(board, args.agent, runner=SshRunner(args.host, port=args.port, remote_dir=args.remote_dir),
                           runs_root=REPO_ROOT / "runs" / "remote" / board.root.name,
                           min_minutes=args.min_minutes, max_minutes=args.max_minutes)
    print(f"[{args.agent}] watching {board.root} via {args.host}:{args.port} until {until.isoformat()}", flush=True)
    while datetime.now(timezone.utc) < until:
        if not agent.step():
            time.sleep(args.poll)
    print(f"[{args.agent}] stop: reached {until.isoformat()}", flush=True)


if __name__ == "__main__":
    main()
