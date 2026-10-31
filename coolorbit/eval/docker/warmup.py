"""Warm the container's caches before the agent starts (runs inside the sandbox image, not on the host).

Downloads the MACE checkpoint and compiles its GPU kernels by running a few NVE steps on a small Cu crystal,
so the agent's own first MACE call finds weights and kernels cached in this container (arm C's Simulator runs
warm on the GB10; this keeps arms A and B comparable). It runs outside the agent's time limit and budget,
and its duration is recorded per session.

Uses only the calls the GB10 already runs (coolorbit/sim/melting.py load_model, scripts/smoke_throughput.py).
Always prints exactly one line ``COOLORBIT_WARMUP {json}`` and exits 0, even on failure: a failed warm-up is
recorded, and the agent still gets its session.
"""

from __future__ import annotations

import json
import os
import time

MODEL = os.environ.get("COOLORBIT_WARMUP_MODEL", "medium-mpa-0")
STEPS = int(os.environ.get("COOLORBIT_WARMUP_STEPS", "20"))


def main() -> dict:
    t0 = time.perf_counter()
    out = {"ok": False, "model": MODEL, "steps": STEPS}
    try:
        import torch
        from ase.build import bulk
        from nvalchemi.data import AtomicData, Batch
        from nvalchemi.dynamics import NVE
        from nvalchemi.dynamics.base import DynamicsStage
        from nvalchemi.hooks import NeighborListHook, WrapPeriodicHook
        from nvalchemi.models.mace import MACEWrapper

        t_load = time.perf_counter()
        model = MACEWrapper.from_checkpoint(MODEL, device=torch.device("cuda"), dtype=torch.float32,
                                            enable_cueq=True)
        out["load_s"] = round(time.perf_counter() - t_load, 2)

        atoms = bulk("Cu", "fcc", a=3.61, cubic=True).repeat((3, 3, 3))
        n = len(atoms)
        data = AtomicData(
            positions=torch.tensor(atoms.positions, dtype=torch.float32),
            atomic_numbers=torch.tensor(atoms.numbers, dtype=torch.long),
            forces=torch.zeros(n, 3),
            energy=torch.zeros(1, 1),
            cell=torch.tensor(atoms.cell.array, dtype=torch.float32).unsqueeze(0),
            pbc=torch.tensor([[True, True, True]]),
        )
        data.add_node_property("velocities", torch.zeros(n, 3))
        batch = Batch.from_data_list([data], device=torch.device("cuda"))
        nve = NVE(model=model, dt=1.0, n_steps=STEPS)
        nve.register_hook(NeighborListHook(model.model_config.neighbor_config, stage=DynamicsStage.BEFORE_COMPUTE))
        nve.register_hook(WrapPeriodicHook(stage=DynamicsStage.AFTER_POST_UPDATE))
        t_md = time.perf_counter()
        nve.run(batch)
        torch.cuda.synchronize()
        out["md_s"] = round(time.perf_counter() - t_md, 2)
        out["ok"] = True
    except Exception as exc:  # recorded, never fatal
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


if __name__ == "__main__":
    print("COOLORBIT_WARMUP " + json.dumps(main()), flush=True)
