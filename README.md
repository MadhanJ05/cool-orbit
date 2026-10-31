# Cool Orbit

AI agents on NVIDIA ALCHEMI find and cross-check thermal battery materials to cool orbital data centers.

Built at the ScienceClaw hackathon (MIT Media Lab, Oct 30 to Nov 1, 2026). The full project brief is in
[`CLAUDE.md`](CLAUDE.md).

## Status

- [x] GB10 environment: `nvalchemi-toolkit` 0.2.0 (MACE, AIMNet2), torch 2.14.1+cu130
- [x] Smoke test and throughput baseline (below)
- [ ] Results board, agent collective, checking hooks, comparison harness

## Setup

Verified on a Dell Pro Max GB10 (ARM64, Blackwell sm_121, driver 580, CUDA 13.0), Ubuntu 24.04.

```bash
sudo apt install -y python3.12-dev          # Triton needs Python.h to build its CUDA helper
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/NVIDIA/nvalchemi-toolkit.git
git -C nvalchemi-toolkit checkout v0.2.0    # match the installed release
uv venv --seed --python 3.12
uv pip install --torch-backend cu130 --index-strategy unsafe-best-match \
  'nvalchemi-toolkit[cu13,mace,aimnet,ase,pymatgen]' 'plotext<6' matplotlib pandas scipy
```

Gotchas we hit:

- Without `python3.12-dev`, anything that goes through `torch.compile`/Triton fails with a gcc error
  (`Python.h: No such file or directory`).
- `--torch-backend cu130 --index-strategy unsafe-best-match` (from the toolkit's install guide) is needed for
  uv to resolve the CUDA wheels correctly.
- `plotext<6`: v0.2.0 does not pin it, but plotext 6 removed `clf()`, which the toolkit's reporting code calls
  (the pin exists on the toolkit's `main` branch).
- MACE inside dynamics needs a `NeighborListHook` registered at `DynamicsStage.BEFORE_COMPUTE`.
- The official `examples/advanced/04_mace_nvt.py` silently falls back to Lennard-Jones if MACE fails to load.
  Check for `Using MACE model` in its output.

## Smoke test: MACE-MPA-0 NVE throughput on the GB10

[`scripts/smoke_throughput.py`](scripts/smoke_throughput.py): periodic FCC Cu, NVE, dt = 1 fs, float32,
50 warm-up + 299 timed steps.

| Run | steps/s | atom-steps/s | ns/day | drift (eV/atom/step) | T end (K) | GPU mem (GB) |
|---|---|---|---|---|---|---|
| e3nn, 864 atoms | 2.6 | 2,257 | 0.23 | 2.4e-8 | 151 | 5.6 |
| e3nn, 2,048 atoms | 1.1 | 2,231 | 0.09 | 3.2e-8 | 152 | 13.0 |
| cuEquivariance, 864 atoms | 21.8 | 18,874 | 1.89 | 2.6e-8 | 151 | 1.5 |
| cuEquivariance, 2,048 atoms | 9.8 | 20,059 | 0.85 | 3.8e-8 | 152 | 3.4 |
| cuEquivariance, 8 x 864 atoms (batched) | 3.1 | 21,233 | 0.27 per system | 4.3e-8 | 155 | 11.2 |

Physics checks: NVE energy drift is about 3e-8 eV/atom/step, and the temperature relaxes to about 150 K from a
300 K start on perfect lattice sites, as equipartition predicts. Raw results are in [`runs/smoke/`](runs/smoke/).

Takeaways: always enable cuEquivariance (8.5x faster than e3nn kernels, 4x less memory). The GB10 saturates
at about 20k atom-steps/s with MACE-medium, so batching several ~1k-atom boxes adds little throughput.

## Credits

`.claude/skills/` is copied unmodified from [NVIDIA/nvalchemi-toolkit](https://github.com/NVIDIA/nvalchemi-toolkit)
v0.2.0 (Apache-2.0).
