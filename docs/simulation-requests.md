# How to request a simulation (contract between Scouts and the Simulator)

The Simulator agent (`coolorbit/sim/simulator_agent.py`, runs on the GB10) only does work that someone
**asked for on the board**. A request is a **need** attached to a `candidate` artifact. There is no
central queue and no direct messaging.

## 1. The candidate must say where its structure comes from

`candidate.structure_source` (a field of the `candidate` payload) must be one of:

| Format | Example | Meaning |
|---|---|---|
| `ase:<Element>:<structure>:<a>` | `ase:Cu:fcc:3.61`, `ase:Al:fcc:4.05`, `ase:Ga:...` | Elemental crystal built by `ase.build.bulk(..., cubic=True)`; `a` is a starting lattice constant in Å (the barostat relaxes it) |
| `file:<repo-relative path>` | `file:data/structures/erythritol.cif` | Any ASE-readable **periodic unit cell** committed to the repo (CIF, POSCAR, extxyz...). Cite its source in `data/structures/README.md` |

Anything else is refused with a `run_report` explaining why.

## 2. The need

Post the need on the candidate (or later on a `run_report`, for retries). Fields:

| Field | Required | Meaning |
|---|---|---|
| `artifact_type` | yes | `"property_result"` |
| `property` | yes | `"melting_point"` or `"latent_heat"` |
| `query`, `rationale` | yes | human-readable; `query` ≥ 5 chars, `rationale` ≥ 10 |
| `temps_K` | melting_point: this **or** `t_guess_K` | 2 to 8 trial temperatures for two-phase coexistence, one GPU job |
| `t_guess_K` | | a rough guess; the Simulator brackets it at 0.85, 0.95, 1.05, 1.15 × the guess |
| `temperature_K` | latent_heat: yes | the temperature for the solid-vs-liquid enthalpy difference (use the melting point) |
| `preset` | no | `"production"` (default) or `"quick"` (tests the pipeline; results are flagged `quick_settings_not_production`) |
| `reps` | no | supercell repeats, default `[4, 4, 8]` (melting) / `[4, 4, 4]` (latent) |
| `dt_fs` | no | default 2.0 fs, or 0.5 fs if the structure contains hydrogen |
| `engine` | no | omit or `"ALCHEMI"` for this Simulator; `"classical_md"` routes it to whoever runs Nebius jobs |

```python
from coolorbit.board import Board

board = Board("boards/dev-yourname")
cand = board.post(
    "scout_metals", "candidate",
    {"name": "Cu", "family": "metal", "tier": "trust", "formula": "Cu",
     "structure_source": "ase:Cu:fcc:3.61", "rationale": "method validation on a well-measured metal"},
    needs=[{"artifact_type": "property_result", "property": "melting_point",
            "query": "melting_point of Cu", "rationale": "validate the coexistence method",
            "temps_K": [1250, 1300, 1350, 1400]}],
)
```

Needs are part of an artifact and artifacts are immutable, so attach requests when you post the candidate.
To ask for something later (e.g. latent heat once the melting point is known), post a `request` artifact
whose parent is the candidate; the Simulator walks up the lineage to find the candidate:

```python
board.post("scout_metals", "request", {"note": "latent heat at the coexistence melting point"},
           parents=[cand.artifact_id],
           needs=[{"artifact_type": "property_result", "property": "latent_heat",
                   "query": "latent_heat of Cu at 1296 K", "rationale": "second figure-of-merit input",
                   "temperature_K": 1296}])
```

## 3. What comes back

| Outcome | Artifact posted by the Simulator | Notes |
|---|---|---|
| Success | `property_result` (parents: candidate) that **fulfils** your need | Full method metadata (engine, model, protocol, ensemble, timestep, atoms, length, run_dir) and flags. It also broadcasts the trust rule's needs: a `method_audit`, an independent-method check, and (trust tier) a sourced `reference_value` |
| Fixable failure | `run_report` with `status: "failed"` that fulfils your need **and carries a retry need** | Bracket too low → shifted up; too high → shifted down; inconsistent → production doubled. Max 3 attempts, then it stops and says so |
| Refusal | `run_report` with `status: "refused"` | Bad/missing structure, missing fields, or **model out of domain** (MACE-MPA-0 on molecular organics such as waxes): the report carries a need with `engine: "classical_md"` |

Status of every result is derived by the board (`python -m coolorbit.board show --board ... -v`), never set by
the Simulator.

## 4. Running the Simulator (GB10 only)

```bash
.venv/bin/python -m coolorbit.sim.simulator_agent --board boards/<id> --dry-run   # what it would take
.venv/bin/python -m coolorbit.sim.simulator_agent --board boards/<id> --once      # one job, then exit
.venv/bin/python -m coolorbit.sim.simulator_agent --board boards/<id>             # keep watching
```

One GPU job at a time; each job runs as a separate process with its full log in `runs/agent/<board>/...`.
