# Independent classical check (MEAM / LAMMPS on the GB10 CPU), 2026-10-31

**Finding:** of the four published classical potentials available for our low-melting metals, only **bismuth's**
reproduces the measured melting point and latent heat within our pre-registered tolerances. Tin's melts at least
17% low, indium's 63–77% high, and gallium's cannot hold the real crystal. Because the trust rule only counts an
independent check whose method first recovers the measured value of that material (pre-registered,
`configs/trust_rules.json`), the bad potentials were **excluded automatically**: the system caught bad *checkers*,
not just bad results. Without that gate, the indium potential would have "disagreed" with MACE on false grounds.

**Caveat on Bi:** its potential was fitted to reproduce bismuth's melting point (Zhou et al., MSMSE 29, 065008
(2021), as noted in `data/nebius/README.md`), so Bi passing the melting-point test is partly built in, and as a
check of a MACE melting point it is partly anchored to the experiment it is meant to test. The latent heat was
computed here, not taken from the paper; we have not checked whether it was a fitting target.

## Results

| Metal | Potential (NIST IPR) | MEAM melting point | Measured | Error | MEAM latent heat | Measured | Error | Usable check? |
|---|---|---|---|---|---|---|---|---|
| **Bi** | 2021--Zhou-H--Bi--LAMMPS--ipr1 | **586.7 ± 17 K** (bracket 576–610) | 544.6 K | **+7.7%** | **59.9 ± 0.2 kJ/kg** | 53.1 kJ/kg | **+12.7%** | **yes** (gates ±10% / ±15%) |
| Sn | 2018--Ko-W-S--Sn--LAMMPS--ipr1 | between 300 and 420 K (freezes at 300; stable 330–390; melts at 420) | 505.1 K | **−17% or worse** | not run | | | no |
| In | 2008--Do-E-C--In--LAMMPS--ipr1 | between 700 and 760 K (731.9 ± 30) | 429.7 K | **+63% to +77%** | not run | | | no |
| Ga | 2009--Do-E-C--Ga-In--LAMMPS--ipr1 | not run: the relaxed alpha-Ga cell is off by −8.4%/+6.6% | 302.9 K | | | | | no |

Measured values (all in `data/calibration/tier1.json`): Bi 544.556 K and 53.146 J/g (NIST SRM 2235 certificate,
2015); Sn 505.078 K (ITS-90); In 429.748 K (Assael et al. 2012, citing ITS-90); Ga 302.92 K (NIST WebBook).

## How we know (and what we got wrong on the way)

- Protocol: Sia's bundles (`data/nebius/metals/`), two-phase coexistence (half crystal, half liquid, NPT
  anisotropic, 50 ps production), analysed with `data/nebius/metals/analyze.py`; latent heat from NPT enthalpies of
  each phase at the potential's own melting point. Run locally with the pip LAMMPS build (MEAM package) on the GB10
  CPU (`scripts/run_classical_local.py`), because the Nebius LAMMPS app rejected every submission format we tried.
- Bi: two brackets agree (first pass 581.4 ± 15.5 K, bracket 568–599 K; refined 586.7 ± 17 K, bracket 576–610 K).
  Latent heat phase checks clean (solid moved 0.3%, liquid moved 99.7%).
- In and Sn first missed their brackets (`runs/classical/meam-1`, `meam-2`). In "froze" even at 700 K, with the
  freezing signal growing with temperature, so we suspected a setup artifact (the crystal is prepared at the
  measured melting point). **A diagnostic with each crystal prepared at its own trial temperature
  (`runs/classical/meam-diag`) disproved that:** In still freezes at 700 K and melts at 900 K; Sn is stable at 390 K
  and melts at 420 K. These are failures of the potentials, not of the protocol.
- Combined brackets: In freezes at 700 K in all three runs and melts at 760 K (`meam-1`), so 700–760 K. Sn freezes
  at 300 K, is "stable" (interface did not move decisively in 50 ps) at 330, 360 and 390 K, and melts at 420 K
  (`meam-diag`) and 454.6 K (`meam-1`), so its melting point is somewhere in 300–420 K; we do not narrow it further.
  Either way it is outside the ±10% gate (454.6–555.6 K).

## Files

| What | Path |
|---|---|
| Bi melting point (first pass / refined) | `runs/classical/meam-1/metals/Bi/analysis_coexistence.json`, `runs/classical/meam-bi-refine/metals/Bi/analysis_coexistence.json` |
| Bi latent heat | `runs/classical/meam-bi-refine/metals/Bi/analysis_latent.json` |
| In / Sn brackets | `runs/classical/meam-1/metals/{In,Sn}/analysis_coexistence.json`, `runs/classical/meam-2/metals/{In,Sn}/analysis_coexistence.json` |
| In / Sn diagnostic (Tprep = T) | `runs/classical/meam-diag/metals/{In,Sn}/analysis_diag.json` |
| Ga stability check | `data/nebius/metals/Ga/stability/RESULT.md` |
| Gate and tolerances | `configs/trust_rules.json`, `docs/chemistry-lead-memo-2026-10-31.md`, `scripts/post_classical_checks.py` |
