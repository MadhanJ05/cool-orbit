# Ga dropped: the potential distorts alpha-Ga (reported failure)

**Potential:** 2NN MEAM, NIST IPR `2009--Do-E-C--Ga-In--LAMMPS--ipr1` (Ga part of the Do, Shin, Lee 2009 Ga-In(-N)
fit). NIST warns that elemental potentials taken from alloy fits may not work for the pure element.

**Check** (`in.ga_stability.lmp` + `check_ga.py`, run on a laptop CPU with LAMMPS 22 Jul 2025, 2026-10-31; all
outputs in this folder, including the relaxed structure `alpha_relaxed.lmp`):

| Check | Result | Pass? |
|---|---|---|
| alpha-Ga below fcc at 0 K | −3.0039 vs −2.9000 eV/atom (−104 meV/atom) | yes |
| relaxed 0 K cell within 3% of the experimental alpha-Ga cell | a ×0.999, **b ×0.916, c ×1.066** | **no** |
| atoms stay put at 303 K (20 ps NPT) | 0% moved > 1.5 Å | yes |
| box shape kept at 303 K (vs the relaxed cell) | within 2.3% | yes |
| close pair survives at 303 K (RDF running count reaches 1 neighbour by 2.55 Å) | 2.51 Å | yes |
| next shell stays beyond 2.6 Å at 303 K (≤ 1.2 neighbours within 2.6 Å) | **1.9** | **no** |

Neighbour shells at 0 K (`shells_0K.py`, mean distances per atom):

| Shell | Experimental alpha-Ga | This potential, relaxed |
|---|---|---|
| close pair | 2.466 Å | 2.425 Å (1.0 neighbour within 2.6 Å) |
| next shell (2 + 2 neighbours) | 2.700, 2.735 Å | **2.619, 2.679 Å** |
| shell after | 2.792 Å | 2.874 Å |

**Verdict: failed.** The close pair survives (2.43 Å at 0 K; at 303 K the running count reaches one neighbour by
2.51 Å, where it shows as a shoulder on the next peak, first local maximum 2.67 Å). But the relaxed cell is
off by −8.4% (b) and +6.6% (c), and the next shell collapses toward the pair (2.70/2.74 → 2.62/2.68 Å), so at 303 K
about 1.9 neighbours sit within 2.6 Å instead of 1. The potential's alpha-Ga is a distorted structure; its melting
point would not be gallium's, so Ga has **no classical-MD jobs** (its `manifest.json` lists none). The potential
files and this evidence stay, so the failure is visible.

(An earlier version of this file said the 2.44 Å dimer was lost, from the *tallest* RDF peak at 2.71 Å. That was
wrong: `check_ga.first_peak` now finds the first local maximum, and the pair test uses the running count.)

Options if we still want an independent Ga check: a potential fitted to pure Ga (none LAMMPS-ready on NIST IPR;
Belashchenko 2012 is liquid-only), or a second MLIP instead of classical MD.

Reproduce (from `metals/Ga/`): `lmp -in stability/in.ga_stability.lmp -var T 302.92 -var seed 2026 -log
stability/log.ga_stability`, then `python stability/check_ga.py stability/log.ga_stability stability/rdf_T302.92.dat`
and `python stability/shells_0K.py` (needs ASE).
