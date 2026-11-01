# Independent classical check, EAM (Cu, Al) on the GB10 CPU, 2026-11-01

**Finding:** the Mishin EAM potential for **copper** recovers copper's measured melting point (−2.3%) and latent
heat (−8.7%). It passes the pre-registered gate and can serve as the independent `classical_md` check of the MACE
Cu result. The Mishin EAM potential for **aluminium** melts **11.6% too high**: 1041 K against a measured 933 K.
That misses the ±10% melting-point gate, so it is **not usable** as a melting-point check for Al, even though its
latent heat lands at −8.8%. Our coexistence values match NIST's own published estimates for both implementations
(within 4 K for Cu and 1 K for Al, from a different protocol). That match puts the Al miss on the potential, not on
our protocol.

Nothing was posted to any board. Run on the GB10 CPU with the same bundles and launcher as the MEAM runs
(`docs/results/classical-check.md`).

## Results

| Metal | Potential (NIST IPR) | EAM melting point | Measured | Error | Tm gate ±10% | EAM latent heat (at its own Tm) | Measured | Error | L gate ±15% | Usable check? |
|---|---|---|---|---|---|---|---|---|---|---|
| **Cu** | 2001--Mishin-Y--Cu-1--LAMMPS--ipr1 | **1327.0 ± 10.0 K** (bracket 1310–1330) | 1357.77 K | **−2.3%** (bracket −3.5% to −2.0%) | **pass** (window 1222.0–1493.5 K) | **188.7 ± 1.0 kJ/kg** at 1327.0 K | 206.7 kJ/kg | **−8.7%** | **pass** | **yes** |
| **Al** | 1999--Mishin-Y--Al--LAMMPS--ipr1 | **1041.4 ± 11.6 K** (bracket 1026.8–1050) | 933.45 K | **+11.6%** (bracket +10.0% to +12.5%) | **fail** (window 840.1–1026.8 K) | 362.0 ± 2.4 kJ/kg at 1041.4 K | 397.0 kJ/kg | −8.8% | pass | **no** (Tm gate) |

Measured values, all from `data/calibration/tier1.json`:
- **Cu:** Tm 1357.77 K, the ITS-90 freezing point of copper (NIST, https://its90.nist.gov/TerminologyFPoint).
  L = 206,748 J/kg from NIST-JANAF (https://janaf.nist.gov/tables/Cu-001.txt): 13.138 kJ/mol at 1358 K.
- **Al:** Tm 933.45 K and L = 396,975 J/kg, both from NIST-JANAF (https://janaf.nist.gov/tables/Al-001.txt):
  10.711 kJ/mol at 933.45 K.

Uncertainties:
- **Melting point:** ± half the bracket. The point estimate is the zero crossing of a straight line through the runs
  inside the bracket.
- **Latent heat:** statistical only (block SEM of the two enthalpies). It does not include the effect of the
  melting-point uncertainty.
- One seed per temperature, 50 ps production.

**Al gate, in numbers:** Al froze decisively at 1026.8 K (ΔPE −0.046 eV/atom against a 0.015 threshold). Its melting
point is therefore above 1026.8 K, which is the gate's upper edge (1.10 × 933.45 = 1026.795 K). It was "stable" at
1035 K and melted at 1050 K and above.

## Cross-check against NIST's own melting estimates

The NIST implementation pages list iprPy two-phase melting estimates. Their protocol differs from ours: NPH with a
Berendsen-scaled setup, 200,000 steps, and a solid fraction read by polyhedral template matching. We read these
numbers after building the bundles, and they were not used to place our brackets (0.8–1.2 × measured, as for MEAM).

| | NIST iprPy (fcc) | This run | Difference |
|---|---|---|---|
| Cu01 | 1323.26 ± 1.46 K | 1327.0 ± 10.0 K | +3.7 K |
| Al99 | 1040.41 ± 1.04 K | 1041.4 ± 11.6 K | +1.0 K |

Sources:
- Cu01: https://www.ctcms.nist.gov/potentials/entry/2001--Mishin-Y-Mehl-M-J-Papaconstantopoulos-D-A-et-al--Cu-1/2001--Mishin-Y--Cu-1--LAMMPS--ipr1.html
- Al99: https://www.ctcms.nist.gov/potentials/entry/1999--Mishin-Y-Farkas-D-Mehl-M-J-Papaconstantopoulos-D-A--Al/1999--Mishin-Y--Al--LAMMPS--ipr1.html

## Against the MACE result (not posted)

On `boards/science-2` the MACE-MPA-0 two-phase result for Cu is 1358.0 ± 68.0 K (producer `simulator_h100b`). The
EAM value, 1327.0 K, is 2.3% below it, inside the ±10% "agree" tolerance. Posting it with
`scripts/post_classical_checks.py --run eam-1` should give an `agree` check carrying the EAM value. When this was
written, the board had **no MACE result for Al**.

**Watch the Al gate before posting.** The poster's reliability gate only fires when the board holds a measured
`reference_value` for the candidate (`unreliable = gate and ref is not None and ...`), and `coolorbit/board/status.py`
has no gate of its own. `boards/science-2` currently holds a measured melting point for Cu (1357.77 K) but **none
for Al**. If `--run eam-1` is posted after the Al MACE result lands and before an Al reference is on the board, the
Al99 value (1041.4 K) would go out as a counted `agree`/`disagree` check instead of `inconclusive`. You can avoid
this in either of two ways:
- post the Al reference first, then run the poster;
- check the `--dry-run` output for Al before posting.

Cosmetic: the poster's note text says "Classical MD (MEAM)". For these checks the structured `method.model` reads
`EAM, NIST IPR ...`, but the free-text note would still say MEAM.

## Potentials and sources

Both potentials are single-element EAM fits (not taken from alloy fits). They come from NIST as `pair_style eam/alloy`
setfl files, converted by C.A. Becker from Y. Mishin's tables. The files were downloaded unchanged on 2026-11-01.
Each SHA-256 is recorded in `data/nebius/metals/<El>/potential/SOURCES.md` and `manifest.json`.

| | Cu | Al |
|---|---|---|
| Citation | Y. Mishin, M.J. Mehl, D.A. Papaconstantopoulos, A.F. Voter, J.D. Kress, "Structural stability and lattice defects in copper: Ab initio, tight-binding, and embedded-atom calculations", Phys. Rev. B 63, 224106 (2001) | Y. Mishin, D. Farkas, M.J. Mehl, D.A. Papaconstantopoulos, "Interatomic potentials for monoatomic metals from experimental data and ab initio calculations", Phys. Rev. B 59, 3393 (1999) |
| DOI | 10.1103/physrevb.63.224106 | 10.1103/physrevb.59.3393 |
| NIST implementation | `2001--Mishin-Y--Cu-1--LAMMPS--ipr1` (the paper's EAM1) | `1999--Mishin-Y--Al--LAMMPS--ipr1` |
| File | `Cu01.eam.alloy` | `Al99.eam.alloy` |
| SHA-256 | `5e374260448eea5c26de59432d75e702c1778cc6999d2527a789e5387ce9dc9e` | `60c8a085be79d273324ab421f5b1447578fef55c1acfc6492c0999f15ee8a284` |
| Download | https://www.ctcms.nist.gov/potentials/Download/2001--Mishin-Y-Mehl-M-J-Papaconstantopoulos-D-A-et-al--Cu-1/2/Cu01.eam.alloy | https://www.ctcms.nist.gov/potentials/Download/1999--Mishin-Y-Farkas-D-Mehl-M-J-Papaconstantopoulos-D-A--Al/2/Al99.eam.alloy |
| Integrity check | 0 K fcc energy at a = 3.615 Å: −3.539999968 eV/atom, matching NIST's release notes (ASE EAM calculator) | at a = 4.05 Å: −3.359999988 eV/atom, matching the release notes |

**Caveat:** we did not check whether the melting point was a fitting target in either paper. The abstracts, as
listed on NIST, do not name it among the fitted or reproduced properties. This matters for Cu in the same way it
did for Bi: if Tm was fitted, the check is partly anchored to the experiment.

## How it was run

- **Bundles:** `data/nebius/metals/{Cu,Al}/`, generated by `data/nebius/build_metal_bundles.py Cu Al`. The builder
  now takes a per-element `pair_style` (default `meam`). Rebuilding all six elements into a scratch copy left the
  In, Sn, Bi and Ga bundles byte-identical.
- **Structures:** fcc from `coolorbit/sim/alloys.py`.
- **Boxes:**

  | | Cu | Al |
  |---|---|---|
  | Coexistence box | 2744 atoms (7×7×14 cells, 25.3 × 25.3 × 50.6 Å) | 1728 atoms (6×6×12 cells, 24.3 × 24.3 × 48.6 Å) |
  | Latent-heat box | 864 atoms | 500 atoms |

- **Protocol:** unchanged from MEAM (`in.coexist.lmp`, `in.latent.lmp`, `analyze.py`).
  - 2 fs timestep, Nosé-Hoover NPT, 50 ps production, seed 2026.
  - Tprep = measured Tm; Tmelt = 3 × Tprep, which is 4073 K for Cu and 2800.5 K for Al.
  - LAMMPS 22 Jul 2025 (pip build in `~/lammps-venv`, MANYBODY package), one core per job.
- **Melt checks (identical across temperatures, since the preparation is the same):**

  | | Cu | Al |
  |---|---|---|
  | Liquid half moved | 99.7% | 99.8% |
  | Crystal half moved | 0.7% | 0.7% |
  | Liquid temperature during the melt | 4024 K (set 4073) | 2797 K (set 2800.5) |

- **Cavitation check (Cu):** the 4073 K melt is very hot, so we looked for cavitation. The liquid half expanded
  (Lz 51.7 → 61.5 Å), then plateaued with no runaway, and shrank back after the quench.
- **Latent-heat phase checks:**

  | | Cu | Al |
  |---|---|---|
  | Solid moved | 0% | 0% |
  | Liquid moved | 99.4% | 99.4% |
  | Solid box-shape change | ≤ 0.3% | ≤ 0.3% |

  No significant enthalpy drift in either element.

### Per-temperature verdicts (ΔPE in eV/atom, threshold in brackets)

| Cu T (K) | 1086.2 | 1222 | 1250 | 1270 | 1290 | 1310 | **1330** | 1350 | 1357.8 | 1493.5 | 1629.3 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| verdict | freeze | freeze | freeze | freeze | freeze | **freeze** −0.054 (0.019) | **melt** +0.010 (0.007) | melt | melt | melt | melt |

| Al T (K) | 746.8 | 840.1 | 933.5 | **1026.8** | 1035 | **1050** | 1065 | 1080 | 1095 | 1110 | 1120.1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| verdict | freeze | freeze | freeze | **freeze** −0.046 (0.015) | stable +0.003 (0.009) | **melt** +0.014 (0.006) | melt | melt | melt | melt | melt |

### Passes

1. **Pass 1:** 0.8–1.2 × measured Tm, five temperatures per element.
   - Cu: bracket 1222–1357.8 K.
   - Al: bracket 1026.8–1120.1 K, already outside the gate.
2. **Refinement:** `--temps` with six temperatures per element, in the same run folder.
   - Cu: 1250–1350 K, 20 K apart.
   - Al: 1035–1110 K, 15 K apart.
   - `analyze.py` reads every `pe_T*.dat` in the folder, so the final brackets combine both passes.
3. **Latent:** `--kind latent --tm Cu=1327.0 Al=1041.4`.

CPU cost: 1476 CPU-seconds (user) in total, about 25 CPU-minutes. That is pass 1 654 s + pass 2 767 s + latent 55 s,
from `/usr/bin/time`. Wall time was about 3.3 min on up to 10 cores.

## Files

| What | Path |
|---|---|
| Final melting points (both passes) | `runs/classical/eam-1/metals/{Cu,Al}/analysis_coexistence.json` |
| Pass-1-only analyses | `runs/classical/eam-1/metals/{Cu,Al}/analysis_coexistence_pass1.json`, `runs/classical/eam-1/summary_coexistence_pass1.json` |
| Latent heats | `runs/classical/eam-1/metals/{Cu,Al}/analysis_latent.json` |
| Launcher output (with CPU time) | `runs/classical/eam-1/launcher_coexistence_pass1.log`, `launcher_coexistence_pass2.log`, `launcher_latent.log` |
| Bundles, sources, hashes | `data/nebius/metals/{Cu,Al}/` (`potential/SOURCES.md`, `manifest.json`) |
| Gate and tolerances | `configs/trust_rules.json`, `scripts/post_classical_checks.py` |

Reproduce:
```
.venv/bin/python scripts/run_classical_local.py --run eam-1 --elements Cu Al --kind coexistence --parallel 10
.venv/bin/python scripts/run_classical_local.py --run eam-1 --elements Cu Al --kind coexistence --parallel 10 \
    --temps Cu=1250,1270,1290,1310,1330,1350 Al=1035,1050,1065,1080,1095,1110
.venv/bin/python scripts/run_classical_local.py --run eam-1 --elements Cu Al --kind latent --tm Cu=1327.0 Al=1041.4 --parallel 4
```
