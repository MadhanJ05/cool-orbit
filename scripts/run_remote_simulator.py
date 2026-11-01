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
from coolorbit.sim.simulator_agent import JobError, SimulatorAgent  # noqa: E402
from coolorbit.sim.ssh_runner import SshRunner  # noqa: E402


class RemoteSimulatorAgent(SimulatorAgent):
    """SimulatorAgent whose claim and timeout allow for a shared remote GPU being ``slowdown`` x slower than the
    idle-GB10 estimate. Seen 2026-11-01: a Cu latent run estimated at 12 min took 56 min on the shared H100, so the
    default allowance (3 x 12 + 15 = 51 min) killed the SSH session 5 min before the run finished."""

    def __init__(self, *args, slowdown: float = 4.0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.slowdown = slowdown

    def step(self) -> bool:
        for on, job in self.pending():
            target, idx = on.artifact.artifact_id, on.index
            ttl = 15.0 if isinstance(job, JobError) else self.slowdown * 3 * job.estimate_minutes() + 15
            if not self.board.claim(self.name, target, idx, ttl_minutes=ttl):
                continue  # another agent got it first
            if isinstance(job, JobError):
                self._refuse(on, job)
            else:
                self._run(job, ttl)
            return True
        return False


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
    ap.add_argument("--slowdown", type=float, default=4.0,
                    help="how much slower than the idle-GB10 estimate the shared remote GPU may be (claim + timeout)")
    ap.add_argument("--min-minutes", type=float, default=0.0)
    ap.add_argument("--max-minutes", type=float, default=float("inf"))
    args = ap.parse_args(argv)
    until = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
    board = Board(REPO_ROOT / args.board if not Path(args.board).is_absolute() else args.board)
    agent = RemoteSimulatorAgent(board, args.agent, slowdown=args.slowdown,
                           runner=SshRunner(args.host, port=args.port, remote_dir=args.remote_dir),
                           runs_root=REPO_ROOT / "runs" / "remote" / board.root.name,
                           min_minutes=args.min_minutes, max_minutes=args.max_minutes)
    print(f"[{args.agent}] watching {board.root} via {args.host}:{args.port} until {until.isoformat()}", flush=True)
    while datetime.now(timezone.utc) < until:
        if not agent.step():
            time.sleep(args.poll)
    print(f"[{args.agent}] stop: reached {until.isoformat()}", flush=True)


if __name__ == "__main__":
    main()
