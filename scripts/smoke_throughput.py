"""Cool Orbit smoke test: MACE NVE throughput on the GB10.

Times NVE molecular dynamics of a periodic FCC copper crystal (inside
MACE-MP's training domain) at a few sizes, with plain e3nn kernels and with
cuEquivariance kernels, plus one batched run. Every run also reports NVE
energy drift and an equipartition temperature check, so a fast-but-broken
run is visible instead of silently "passing".

Usage:
    .venv/bin/python scripts/smoke_throughput.py
"""

from __future__ import annotations

import argparse
import json
import platform
import time
import traceback
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import torch
from ase.build import bulk

import nvalchemi
from nvalchemi.data import AtomicData, Batch
from nvalchemi.dynamics import NVE
from nvalchemi.dynamics.base import DynamicsStage
from nvalchemi.dynamics.hooks import EnergyDriftMonitorHook, NaNDetectorHook
from nvalchemi.dynamics.hooks._utils import kinetic_energy_per_graph
from nvalchemi.hooks import NeighborListHook, WrapPeriodicHook
from nvalchemi.models.mace import MACEWrapper

KB_EV = 8.617333262e-5  # eV/K
CU_LATTICE_A = 3.61  # Angstrom, room-temperature FCC Cu (approximate starting value)


def make_cu_crystal(reps: int, temperature: float, seed: int) -> AtomicData:
    """Perfect FCC Cu supercell with Maxwell-Boltzmann velocities, zero net momentum."""
    atoms = bulk("Cu", "fcc", a=CU_LATTICE_A, cubic=True).repeat((reps, reps, reps))
    n = len(atoms)
    masses = torch.tensor(atoms.get_masses(), dtype=torch.float32)
    gen = torch.Generator().manual_seed(seed)
    # Velocities in sqrt(eV/amu), so KE = 0.5 m v^2 is in eV (toolkit convention).
    v = torch.randn(n, 3, generator=gen) * torch.sqrt(KB_EV * temperature / masses).unsqueeze(-1)
    v -= (masses.unsqueeze(-1) * v).sum(0) / masses.sum()  # remove centre-of-mass drift
    data = AtomicData(
        positions=torch.tensor(atoms.positions, dtype=torch.float32),
        atomic_numbers=torch.tensor(atoms.numbers, dtype=torch.long),
        forces=torch.zeros(n, 3),
        energy=torch.zeros(1, 1),
        cell=torch.tensor(atoms.cell.array, dtype=torch.float32).unsqueeze(0),
        pbc=torch.tensor([[True, True, True]]),
    )
    data.add_node_property("velocities", v)
    return data


def energies(batch: Batch) -> tuple[torch.Tensor, torch.Tensor]:
    """Per-graph (total energy, temperature) as CPU float64 tensors."""
    ke = kinetic_energy_per_graph(
        velocities=batch.velocities,
        masses=batch.atomic_masses,
        batch_idx=batch.batch_idx,
        num_graphs=batch.num_graphs,
    ).squeeze(-1)
    pe = batch.energy.squeeze(-1)
    n_atoms = batch.num_nodes_per_graph.to(ke.dtype)
    temp = 2.0 * ke / (3.0 * n_atoms * KB_EV)
    return (ke + pe).double().cpu(), temp.double().cpu()


class TimingProbe:
    """After the warm-up steps, snapshot total energy and start the clock.

    Only synchronizes the GPU once (when the clock starts), so it does not
    slow down the timed steps.
    """

    stage = DynamicsStage.AFTER_STEP
    frequency = 1

    def __init__(self, warmup: int) -> None:
        self.warmup = warmup
        self.t0: float | None = None
        self.e0: torch.Tensor | None = None
        self.n_timed = 0

    def __call__(self, ctx, stage) -> None:
        if self.t0 is not None:
            self.n_timed += 1
        elif ctx.step_count >= self.warmup:
            torch.cuda.synchronize()
            self.e0, _ = energies(ctx.batch)
            self.t0 = time.perf_counter()


def run_one(label: str, model, reps: int, n_copies: int, args) -> dict:
    device = torch.device("cuda")
    data_list = [make_cu_crystal(reps, args.temperature, seed=i) for i in range(n_copies)]
    batch = Batch.from_data_list(data_list, device=device)
    atoms_per_system = data_list[0].num_nodes

    nve = NVE(model=model, dt=args.dt, n_steps=args.warmup + args.steps)
    nve.register_hook(
        NeighborListHook(model.model_config.neighbor_config, stage=DynamicsStage.BEFORE_COMPUTE)
    )
    nve.register_hook(WrapPeriodicHook(stage=DynamicsStage.AFTER_POST_UPDATE))
    nve.register_hook(NaNDetectorHook(frequency=50))
    nve.register_hook(
        EnergyDriftMonitorHook(threshold=1e-4, metric="per_atom_per_step", action="warn", frequency=50)
    )
    probe = TimingProbe(args.warmup)
    nve.register_hook(probe)

    torch.cuda.reset_peak_memory_stats()
    batch = nve.run(batch)
    torch.cuda.synchronize()
    wall = time.perf_counter() - probe.t0

    e1, t_final = energies(batch)
    drift = ((e1 - probe.e0).abs() / (atoms_per_system * probe.n_timed)).max().item()
    steps_per_s = probe.n_timed / wall
    return {
        "label": label,
        "status": "ok",
        "n_systems": n_copies,
        "atoms_per_system": atoms_per_system,
        "total_atoms": atoms_per_system * n_copies,
        "steps_timed": probe.n_timed,
        "wall_s": round(wall, 3),
        "steps_per_s": round(steps_per_s, 2),
        "atom_steps_per_s": round(steps_per_s * atoms_per_system * n_copies),
        "ns_per_day_per_system": round(steps_per_s * args.dt * 86400 / 1e6, 3),
        "max_drift_eV_per_atom_per_step": drift,
        "T_final_K_mean": round(t_final.mean().item(), 1),
        "peak_gpu_mem_GB": round(torch.cuda.max_memory_allocated() / 1e9, 2),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default="medium-mpa-0", help="MACE-MP named checkpoint")
    p.add_argument("--reps", type=int, nargs="+", default=[6, 8], help="cubic supercell reps (4*r^3 atoms)")
    p.add_argument("--batch-copies", type=int, default=8, help="copies in the batched run (smallest size)")
    p.add_argument("--warmup", type=int, default=50)
    p.add_argument("--steps", type=int, default=300)
    p.add_argument("--dt", type=float, default=1.0, help="timestep, fs")
    p.add_argument("--temperature", type=float, default=300.0, help="initial velocity temperature, K")
    p.add_argument("--out", default="runs/smoke")
    p.add_argument("--kernels", choices=["both", "e3nn", "cueq"], default="both")
    p.add_argument("--compile", action="store_true", help="torch.compile the MACE model (inference only)")
    args = p.parse_args()

    if not torch.cuda.is_available():
        raise SystemExit("CUDA not available: this smoke test must run on the GPU.")

    env = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": platform.node(),
        "arch": platform.machine(),
        "gpu": torch.cuda.get_device_name(0),
        "capability": list(torch.cuda.get_device_capability(0)),
        "torch": torch.__version__,
        "nvalchemi": nvalchemi.__version__,
        "mace_torch": version("mace-torch"),
        "cuequivariance": version("cuequivariance"),
        "model": args.model,
        "dtype": "float32",
        "args": vars(args),
    }
    print(json.dumps(env, indent=2))

    results = []
    kernel_sets = [k for k in (("e3nn", False), ("cueq", True)) if args.kernels in ("both", k[0])]
    for kernels, cueq in kernel_sets:
        if args.compile:
            kernels += "+compile"
        try:
            model = MACEWrapper.from_checkpoint(
                args.model, device=torch.device("cuda"), dtype=torch.float32, enable_cueq=cueq,
                compile_model=args.compile,
            )
        except Exception as exc:  # report the failure, keep going
            results.append({"label": f"{kernels} model load", "status": "failed", "error": repr(exc)})
            traceback.print_exc()
            continue
        jobs = [(r, 1) for r in args.reps]
        if cueq and args.kernels == "both":
            jobs.append((min(args.reps), args.batch_copies))
        for reps, copies in jobs:
            label = f"{kernels} {copies}x{4 * reps**3} atoms"
            print(f"\n--- {label} ---", flush=True)
            try:
                res = run_one(label, model, reps, copies, args)
            except Exception as exc:  # report the failure, keep going
                res = {"label": label, "status": "failed", "error": repr(exc)}
                traceback.print_exc()
            res["kernels"] = kernels
            results.append(res)
            print(json.dumps(res), flush=True)
            torch.cuda.empty_cache()
        del model
        torch.cuda.empty_cache()

    print("\n=== Summary ===")
    hdr = f"{'run':<26}{'steps/s':>9}{'atom-steps/s':>14}{'ns/day':>9}{'drift eV/at/step':>18}{'T_end K':>9}{'mem GB':>8}"
    print(hdr)
    for r in results:
        if r["status"] != "ok":
            print(f"{r['label']:<26}  FAILED: {r['error'][:80]}")
            continue
        print(
            f"{r['label']:<26}{r['steps_per_s']:>9.1f}{r['atom_steps_per_s']:>14,}"
            f"{r['ns_per_day_per_system']:>9.2f}{r['max_drift_eV_per_atom_per_step']:>18.2e}"
            f"{r['T_final_K_mean']:>9.1f}{r['peak_gpu_mem_GB']:>8.2f}"
        )
    print(
        f"\nSanity: a crystal started on perfect lattice sites at {args.temperature:.0f} K should settle"
        f" near {args.temperature / 2:.0f} K (equipartition). ns/day assumes dt={args.dt} fs;"
        " H-rich organics will need ~0.5 fs."
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"throughput_{env['timestamp_utc'].replace(':', '')}.json"
    out_file.write_text(json.dumps({"env": env, "results": results}, indent=2))
    print(f"Saved {out_file}")


if __name__ == "__main__":
    main()
