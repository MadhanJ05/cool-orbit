# Cool Orbit

**AI agents on NVIDIA ALCHEMI find and cross-check thermal-battery materials to cool orbital data centers.**

**Live dashboard:** https://coolorbit-dashboard-762928923551.us-east1.run.app
(Overview · Materials · Trust · Agents; the previous dashboard is kept at
[`/classic/`](https://coolorbit-dashboard-762928923551.us-east1.run.app/classic/)). The Cloud Run copy is deleted
after the event; this repository is the permanent record.

Built in one weekend at the ScienceClaw hackathon (MIT Media Lab, Oct 30 to Nov 1, 2026). The full project brief
is [`CLAUDE.md`](CLAUDE.md); every claim in the pitch is in the claims register in
[`docs/pitch/outline.md`](docs/pitch/outline.md).

---

## The problem

- Google's Project Suncatcher prototype runs **one TPU at a time, in 15-minute bursts**, "to avoid straining the
  satellite's power and thermal management systems" (TechCrunch, Oct 1, 2026).
- In a vacuum, heat leaves only as infrared from radiators, and radiators are among the heaviest parts of the mission.
- A **thermal battery** (a phase-change material, PCM) melts to soak up a burst and refreezes while the radiator
  catches up, so the radiator can be sized for the average load instead of the peak. NASA flew a paraffin-wax PCM
  experiment on the ISS.
- A better material doesn't make chips produce less heat and doesn't remove the radiator. It **buffers the bursts**.

**Question:** which material makes the best thermal battery for bursty AI chips in orbit, and can we trust the
simulations that say so?

## What we found after one day of testing

**Gallium is the best alternative to paraffin wax so far.** It wins 5 of our 9 comparisons, for one 15-minute burst
at 1 kW (measured properties fed into [`scripts/burst_calc.py`](scripts/burst_calc.py); every input is sourced in
`data/burst/inputs.json`):

| What matters | Paraffin (n-octadecane) | Gallium | Better |
|---|---|---|---|
| Heat conduction, liquid (W/m·K) | 0.149 | 28.1 | **gallium, 189×** |
| Heat-exchange area needed (m²) | 1.81 | 0.081 | **gallium, 22×** |
| Aluminium fins needed (kg) | 1.22 | 0.055 | **gallium, 22×** |
| Container volume (L) | 4.91 | 1.90 | **gallium, 2.6×** |
| Volume change on melting | +4.8% | −2.9% | **gallium** |
| Heat stored per kg (kJ/kg) | 236.2 | 80.2 | paraffin, 2.9× |
| **Device mass for one burst (kg)** | **5.04** | **11.3** | **paraffin, 2.2× lighter** |
| Safe with aluminium hardware | yes | no (liquid Ga attacks Al) | paraffin |
| Melts inside the 315–345 K window | no (301 K) | no (302.9 K) | neither |

Read it honestly:
- **The trade-off is weight.** Gallium moves heat far better, but the device is 2.2× heavier.
- **Neither melts inside our 315–345 K operating window** (chemistry-lead memo,
  [`docs/chemistry-lead-memo-2026-10-31.md`](docs/chemistry-lead-memo-2026-10-31.md)). No candidate inside the window
  is trusted yet.
- **Gallium's numbers are measured values, not our simulations.** Our gallium simulations failed with both methods:
  MACE's latent heat came out about 5× low, and the classical potential didn't hold alpha-gallium's crystal. We
  report that as a failure.

## Can the simulations be trusted?

A result counts as **trusted** only when all three of these hold
([`configs/trust_rules.json`](configs/trust_rules.json), pre-registered by the chemistry lead):
1. Another agent's method audit passes.
2. An independent method agrees within tolerance (a second force field, or classical MD from a different agent).
3. For known materials, it matches a sourced measurement within tolerance (±10% melting point, ±15% latent heat).

No agent can set a status: it is derived from the evidence on the board ([`coolorbit/board/status.py`](coolorbit/board/status.py)).

| Material | Status | Why |
|---|---|---|
| Copper (validation metal) | **trusted** (melting point and latent heat) | MACE two-phase 1358 ± 68 K vs 1357.77 K measured; classical EAM check 1327 K; latent heat 190.2 vs 206.7 kJ/kg |
| Aluminium (validation metal) | under review | MACE matches the measurement, but the only classical potential fails its own test (+11.6%), so no independent check counts |
| Indium, bismuth | flagged | MACE-MPA-0 comes out 20–25% low |
| Tin | stopped, reported | the crystal didn't survive preparation at 505 K |
| Field's metal (Bi-In-Sn) | flagged | latent heat 25.3 kJ/kg vs 25.4–27.1 measured; the adjudicator upheld a flag on the pure-element-solid approximation |
| Gallium | failed (both methods) | see above; ranked on measured data only |

Details: [`docs/results/classical-check-eam.md`](docs/results/classical-check-eam.md),
[`docs/results/classical-check.md`](docs/results/classical-check.md),
[`docs/results/coexistence-diagnosis.md`](docs/results/coexistence-diagnosis.md).

## Does a team of agents beat a single agent?

Equal budgets ($1.50 of LLM per task per arm, 60 minutes, same tools including web access and the measured-value
file), the same 8 pre-registered tasks, 3 repeats ([`docs/results/comparison-cmp1.md`](docs/results/comparison-cmp1.md)):

| | A: single agent | B: worker + checker | C: our collective |
|---|---|---|---|
| Simulation traps caught | 60% | 67% | **80%** |
| False alarms on correct tasks | 11% | 22% | **11%** |
| Confident wrong answers | 0 | 0 | 0 |
| LLM cost per repeat | ~$4.57 | ~$3.50 | **~$2.40** |

Read it honestly:
- **The collective's advantage appeared after a disclosed post-hoc change** to arm C's Simulator (fit each GPU job to
  the time left). Repeat 1: 60%; repeats 2 and 3: 100% and 80%.
- No arm shipped a confident wrong answer. With NVIDIA's recommended prompt and web access, a single agent already
  refuses the ill-posed tasks.
- Arm C's cost excludes 6 refused task runs that had no cost log.
- 8 tasks × 3 repeats: no statistical claims.

## How it works

Agents are separate programs with one job each. **There is no boss:** they coordinate only through a shared
results board (ScienceClaw artifacts with lineage plus a needs index), claiming work and posting results.

| Step | Agents | What they do |
|---|---|---|
| Steer | the team, the chemistry lead | pre-register the trust tolerances, the operating window and the comparison tasks |
| Propose | Scouts, one per material family | propose candidates and route each to an engine that can handle it |
| Gate | premise checker | asks whether a property is measurable at all with this method, model, size and timescale |
| Simulate | Simulator on the GB10 GPU (overflow to Brev H100) | MACE-MPA-0 two-phase coexistence on NVIDIA ALCHEMI, every trial temperature batched in one GPU job |
| Try to prove it wrong | rule referee, LLM referee, classical-MD referee, Librarian, adjudicator | method audit, an independent LLM audit, LAMMPS with published EAM/MEAM potentials, sourced measurements, rulings on disputes |
| Keep | results board, trust rule, ranker | an append-only record; status derived from evidence; a shortlist from trusted results only |

Everything runs on a **Dell Pro Max GB10** (Grace Blackwell, ARM64, 128 GB unified memory): the agents, the board,
the ALCHEMI simulations on the GPU, and the classical MD on the Grace CPU cores. Agent details are in
[`coolorbit/agents/README.md`](coolorbit/agents/README.md).

## What others can reuse

- **[`SuperheatingDetectorHook`](coolorbit/hooks/README.md):** a drop-in ALCHEMI dynamics hook that flags melting
  points read off a perfect crystal (which superheats). GPU control on copper: 3 of 3 superheated crystals caught,
  0 false alarms on copper two-phase boxes. **Limit:** it raises false flags on heavy, low-melting two-phase boxes
  (Bi, In, Sn).
- **Evaluation harness** ([`coolorbit/eval/`](coolorbit/eval/)): the task set, board-based scoring (pre-registered
  and post-hoc, both reported) and sandboxed Docker runners for the single-agent and pipeline baselines.
- **Results board with derived trust** ([`coolorbit/board/`](coolorbit/board/)) and the **dashboard**
  ([`dashboard/`](dashboard/)).
- **Classical-MD input bundles** with cited NIST potentials ([`data/nebius/`](data/nebius/)).

## Honest limits

- We simulate and rank; we don't make materials or claim a new one. Unmeasured (Tier 2) values are predictions for
  lab testing.
- AI force fields have errors; we measure them against data and report them. MACE-MPA-0 is 15–28% low on In, Bi and
  Sn melting points.
- MACE-MP models are trained on inorganic crystals, so waxes are out of their domain. Our Simulator refuses them and
  asks for classical MD.
- Our own first coexistence analysis was wrong. The saved runs exposed it, and protocol v2 replaced it.
- Classical potentials fail too: of four NIST MEAM potentials only bismuth's passed our pre-registered gate.

## Repository map

| Path | What it is |
|---|---|
| `coolorbit/agents/` | Scouts, referees, premise checker, Librarian, adjudicator, ranker |
| `coolorbit/sim/` | the ALCHEMI Simulator agent, two-phase melting and latent-heat protocols, remote runner |
| `coolorbit/board/` | the results board, schema and derived trust status |
| `coolorbit/hooks/` | `SuperheatingDetectorHook` |
| `coolorbit/eval/` | comparison tasks, runners for arms A and B, sandbox, metrics |
| `scripts/` | science run, arm C runner, burst calculation, classical checks, smoke test |
| `boards/` | the results boards themselves (science runs and every comparison repeat) |
| `data/` | measured calibration values with sources, burst inputs, classical-MD bundles |
| `docs/results/`, `docs/pitch/` | write-ups, and the pitch outline with its claims register |
| `dashboard/` | the live dashboard (exporter, server, pages) |

## Run it

```bash
# science run on the GB10 (the board is the state: rerunning resumes)
python scripts/run_science.py --board boards/science-1 --dry-run      # the plan and a GPU estimate only
python scripts/run_science.py --board boards/science-1

# comparison: arms A/B, arm C, then score a repeat
python -m coolorbit.eval.runner --arm A --run <run_id> --repeat 1 --budget-usd 1.5 --timeout-min 60
python scripts/run_arm_c.py --help
python -m coolorbit.eval.metrics --repeat boards/eval/<run>/C-r1

# whole pipeline on a laptop, synthetic results, with a trap the referees must catch
python -m coolorbit.agents --board boards/dev-<name> --fake-sim --trap Al=silent_heating

# dashboard
python -m dashboard.export --board boards/science-2 --board boards/eval/cmp-1
python dashboard/server.py        # http://localhost:8080 (see dashboard/README.md and dashboard/DEPLOY.md)
```

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

### Throughput: MACE-MPA-0 NVE on the GB10

[`scripts/smoke_throughput.py`](scripts/smoke_throughput.py): periodic FCC Cu, NVE, dt = 1 fs, float32,
50 warm-up + 299 timed steps.

| Run | steps/s | atom-steps/s | ns/day | drift (eV/atom/step) | T end (K) | GPU mem (GB) |
|---|---|---|---|---|---|---|
| e3nn, 864 atoms | 2.6 | 2,257 | 0.23 | 2.4e-8 | 151 | 5.6 |
| e3nn, 2,048 atoms | 1.1 | 2,231 | 0.09 | 3.2e-8 | 152 | 13.0 |
| cuEquivariance, 864 atoms | 21.8 | 18,874 | 1.89 | 2.6e-8 | 151 | 1.5 |
| cuEquivariance, 2,048 atoms | 9.8 | 20,059 | 0.85 | 3.8e-8 | 152 | 3.4 |
| cuEquivariance, 8 x 864 atoms (batched) | 3.1 | 21,233 | 0.27 per system | 4.3e-8 | 155 | 11.2 |

NVE energy drift is about 3e-8 eV/atom/step, and the temperature relaxes to about 150 K from a 300 K start on
perfect lattice sites, as equipartition predicts. Always enable cuEquivariance (8.5× faster than e3nn kernels, 4×
less memory); the GB10 saturates at about 20k atom-steps/s with MACE-medium. Raw results: [`runs/smoke/`](runs/smoke/).

## Credits and licence

Apache-2.0 ([`LICENSE`](LICENSE)). `.claude/skills/` is copied unmodified from
[NVIDIA/nvalchemi-toolkit](https://github.com/NVIDIA/nvalchemi-toolkit) v0.2.0 (Apache-2.0). The results board builds
on [ScienceClaw](https://github.com/lamm-mit/scienceclaw) (MIT LAMM).
