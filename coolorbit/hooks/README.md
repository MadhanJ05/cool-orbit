# SuperheatingDetectorHook: catch melting points read off perfect crystals

A drop-in [NVIDIA ALCHEMI Toolkit](https://github.com/NVIDIA/nvalchemi-toolkit) dynamics hook (tested with
`nvalchemi-toolkit` 0.2.0). Free to reuse under Apache-2.0. From the [Cool Orbit](../../README.md) team.

## The trap it catches

Heat a perfect crystal in MD until it melts, and read the melting point off the jump. It looks clean and
confident, and it is **wrong**: a defect-free crystal has nowhere to start melting, so it stays solid well above
its true melting point (**superheating**), then melts abruptly. The correct method is two-phase (solid-liquid)
coexistence.

The hook watches every system in the batch while it runs. Without needing any protocol metadata, it flags:

| Flag | Meaning |
|---|---|
| `superheating_risk` | The system started as a defect-free crystal. "It didn't melt at T" says nothing about the melting point either |
| `superheating_observed` | It started as a perfect crystal **and then melted**: the temperature it melted at overestimates the melting point |

A two-phase solid|liquid box (the correct method) or a liquid is meant to be recognised as such and not flagged.
**That holds for copper but not for heavy, low-melting metals:** on bismuth, indium and tin two-phase boxes the hook
gave false flags (see [Limits](#limits-stated-plainly) below). Don't read its flags on a two-phase box as superheating
without checking.

## Use it

It is a single file whose only dependencies are `torch` and `nvalchemi`. Copy it into your project:

```bash
curl -O https://raw.githubusercontent.com/MadhanJ05/cool-orbit/main/coolorbit/hooks/superheating.py
```

```python
from superheating import SuperheatingDetectorHook

hook = SuperheatingDetectorHook(action="warn")   # "raise" stops the run; "record" only logs
dynamics.register_hook(hook)                     # any nvalchemi dynamics: NPT, NVT, NVE, ...
dynamics.run(batch)

print(hook.flags())    # e.g. {'superheating_risk', 'superheating_observed'}
print(hook.report())   # per system: start_state, melted, melt_step, melt_temperature_K, flags
```

**How it decides** (details in the docstring). Positions at the first call are the reference. After
`probe_step` steps (default 100), a system where almost no atom has moved more than 0.3 nearest-neighbour
distances counts as a perfect crystal. If at least half of its atoms later move more than 0.5 nearest-neighbour
distances, it melted. Displacements are periodic-wrap and barostat safe, and hydrogen is ignored by default.

## Validation on a GPU (Dell Pro Max GB10, MACE-MPA-0 + cuEquivariance, FCC copper)

| Test | Systems | Result |
|---|---|---|
| Perfect crystals held at 1300, 1500, 1700, 1900, 2100 K (NPT, 5 ps) | 5 | **Fired on all 3 that melted** (melting seen at 1630, 1873 and 2042 K). The 2 that stayed solid got `superheating_risk` only |
| Two-phase solid-liquid boxes at 1300 to 1450 K (NPT, 5 ps) | 4 | **0 false alarms** (all recognised as having an interface) |

On the same setup, the naive "heat until it melts" method reports copper melting at **1600 ± 100 K**. Copper's
melting (freezing) point is **1357.77 K** (an ITS-90 defining fixed point). Raw logs: `runs/hook_heating_cu_quick.log`,
`runs/melting/*_Cu_heating_quick/`, `runs/agent/e2e-cu-quick/`.

Later two-phase coexistence runs (MACE-MPA-0, `action="record"`, production NPT 50 ps; quick Cu 5 ps), from
[docs/results/coexistence-diagnosis.md](../../docs/results/coexistence-diagnosis.md):

| Boxes prepared as two-phase | Systems | Labelled "crystalline" (`superheating_risk`) | `superheating_observed` |
|---|---|---|---|
| Cu, 1275 to 1450 K (two production runs, one quick run) | 12 | **0 of 12** (early mobile fraction 0.27 to 0.35) | 0 |
| Bi 463 to 627 K, In 238 to 494 K (two runs), Sn 429 to 581 K | 16 | **15 of 16** (early mobile fraction 0 to 0.045; the 16th, Sn at 581 K, had 0.057, just over 0.05) | **11 of 12** in the three runs whose boxes became fully mobile (Bi, In first run, Sn): all false alarms |

### Limits, stated plainly

- **False flags on heavy, low-melting metals.** The start-state probe labels a system "crystalline" if 5% or fewer
  of its atoms moved more than 0.3 nearest-neighbour distances in the first `probe_step` steps (100 steps, 0.2 ps
  here). A cool, heavy liquid (Bi, In, Sn at 240 to 630 K) moves less than that in 0.2 ps, so a two-phase box, or
  even a fully liquid one (the Sn box turned out to hold no crystal), is labelled crystalline, and once half its
  atoms have moved (the crystal half melting, or the liquid diffusing) the hook reports `superheating_observed`. On
  these systems, `superheating_risk` and
  `superheating_observed` say nothing about superheating. The reported melt step fell as the temperature rose (Bi:
  6250, 2850, 2100, 1450 steps from 463 to 627 K): the crystal half melted sooner when hotter, which is ordinary
  melting, not superheating.
- `max_mobile_fraction` in `hook.report()` does not depend on that label and stayed informative: 0.94 to 1.0 in
  boxes that became fully liquid, 0.17 to 0.30 in boxes whose liquid half froze early (Cu, and In below 366 K; In at
  366 K froze later and reached 0.47). Our coexistence workflow (protocol 2) uses only that number from a two-phase
  box. A longer `probe_step` might separate slow liquids from crystals; **we have not tested it.**
- Copper is the only material where the "two-phase box is not flagged" behaviour holds in our data (12 systems,
  three runs).
- The thresholds were chosen for atomic and ionic crystals and **have not been validated for molecular crystals**
  (waxes, sugar alcohols), where whole molecules rotate.

Please report what you see.

## Used it? Tell us

Open an issue on [MadhanJ05/cool-orbit](https://github.com/MadhanJ05/cool-orbit/issues), or tell the Cool Orbit
team at the hackathon. We'd like to know which systems it caught (or missed).
