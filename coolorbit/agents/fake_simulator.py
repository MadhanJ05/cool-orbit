"""FakeSimulator: the REAL Simulator agent (coolorbit.sim.simulator_agent) with a synthetic runner.

For laptop development and tests only. Same request parsing, claims, refusals, retries and trust-rule
needs as the GB10 Simulator, but instead of running MD it fabricates a plausible-looking number. Every
result it posts is marked ``"note": "SYNTHETIC test data ..."`` and flagged ``synthetic_test_data``, and
it refuses to touch any board whose directory name does not start with ``dev-`` (real boards are evidence).

Traps: ``traps={"Cu": "single_phase_heating"}`` makes it answer like a naive single agent would:
single-phase heating with a superheated value. ``"silent_heating"`` does the same without raising any
producer flag, so only a referee reading the protocol can catch it. Traps fire on the first attempt only,
so a rerun requested by a referee comes back with the (synthetic) coexistence answer.

Needs ASE (``pip install ase``) because the real Simulator parses structures with it.

    python -m coolorbit.agents.fake_simulator --board boards/dev-<name> [--trap Cu=silent_heating] [--once]
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from coolorbit.agents.base import norm
from coolorbit.board import Board

SYNTHETIC_NOTE = "SYNTHETIC test data from FakeSimulator: not a simulation result, never cite it"
SYNTHETIC_FLAG = "synthetic_test_data"
TRAPS = ("single_phase_heating", "silent_heating")


class SyntheticBoard(Board):
    """A Board that stamps every property_result as synthetic, and only exists for dev boards."""

    def __init__(self, root: str | Path, *, allow_any_board: bool = False) -> None:
        if not allow_any_board and not Path(root).resolve().name.startswith("dev-"):
            raise ValueError(f"FakeSimulator only writes to dev boards (directory name 'dev-*'), not {root!r}")
        super().__init__(root)

    def post(self, agent, artifact_type, payload, **kw):
        if artifact_type == "property_result":
            payload = dict(payload)
            payload["note"] = (SYNTHETIC_NOTE + (" | " + payload["note"] if payload.get("note") else ""))
            payload["flags"] = sorted(set(payload.get("flags", [])) | {SYNTHETIC_FLAG})
        return super().post(agent, artifact_type, payload, **kw)


def _jitter(name: str, scale: float) -> float:
    """Deterministic pseudo-noise in [-scale, scale] so tests are reproducible."""
    h = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return (2 * h - 1) * scale


def synthetic_runner(traps: Optional[dict[str, str]] = None):
    """A runner with the same contract as simulator_agent.subprocess_runner: (result, settings, run_dir)."""
    traps = {norm(k): v for k, v in (traps or {}).items()}

    def run(job, out_dir: Path, timeout_s: float):
        # Traps fire on the first attempt only: a rerun requested by a referee gets the honest answer.
        trap = None if getattr(job, "supersedes", None) else traps.get(norm(job.name))
        mode = job.mode
        flags: list[str] = []
        if job.mode == "coexistence":
            mid = sum(job.temps) / len(job.temps)
            value, unc, prop, units = round(mid + _jitter(job.name, 0.02 * mid), 1), 15.0, "melting_point", "K"
            if trap in TRAPS:  # what a naive agent ships: heat a perfect crystal, read off the jump
                mode = "heating"
                value, unc = round(value * 1.2, 1), 3.0  # superheated, with suspiciously tight error bars
                if trap == "single_phase_heating":
                    flags = ["single_phase_heating", "superheating_risk"]
        else:
            value, unc, prop, units = round(2.0e5 + _jitter(job.name, 2.0e4)), 1.0e4, "latent_heat", "J/kg"
        if job.preset == "quick":
            flags.append("quick_settings_not_production")
        result = {"status": "ok", "property": prop, "value": value, "uncertainty": unc, "units": units, "flags": flags}
        settings = {"settings": {"mode": mode, "model": "MACE-MPA-0", "dt_fs": job.dt_fs, "temps": job.temps,
                                 "prod_steps": job.steps()["prod"]}, "n_atoms": job.n_atoms}
        return result, settings, out_dir / "SYNTHETIC"

    return run


def make_fake_simulator(board_root: str | Path, *, name: str = "simulator_fake",
                        traps: Optional[dict[str, str]] = None, allow_any_board: bool = False):
    from coolorbit.sim.simulator_agent import SimulatorAgent  # needs ASE

    board = SyntheticBoard(board_root, allow_any_board=allow_any_board)
    return SimulatorAgent(board, name=name, runner=synthetic_runner(traps), runs_root=board.root / "runs")


class FakeSimulatorAgent:
    """Adapter so the fake simulator fits the same step()/drain() loop as the other agents."""

    role = "simulator"

    def __init__(self, board_root: str | Path, **kw) -> None:
        self.sim = make_fake_simulator(board_root, **kw)
        self.name = self.sim.name

    def step(self) -> bool:
        return self.sim.step()

    def drain(self, max_steps: int = 1000) -> int:
        n = 0
        while n < max_steps and self.step():
            n += 1
        return n


def main(argv: Optional[list[str]] = None) -> None:
    import argparse
    import time

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board", required=True)
    ap.add_argument("--trap", action="append", default=[], help="candidate=single_phase_heating|silent_heating")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--poll", type=float, default=5.0)
    args = ap.parse_args(argv)
    traps = dict(t.split("=", 1) for t in args.trap)
    bad = {v for v in traps.values() if v not in TRAPS}
    if bad:
        ap.error(f"unknown trap(s) {bad}; use {TRAPS}")
    agent = FakeSimulatorAgent(args.board, traps=traps)
    if args.once:
        agent.drain()
        return
    while True:
        if not agent.step():
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
