# Ga dropped: the potential does not hold alpha-Ga (reported failure)

**Potential:** 2NN MEAM, NIST IPR `2009--Do-E-C--Ga-In--LAMMPS--ipr1` (Ga part of the Do, Shin, Lee 2009 Ga-In(-N)
fit). NIST warns that elemental potentials taken from alloy fits may not work for the pure element. This check
says it doesn't, for the property we need.

**Check** (`in.ga_stability.lmp` + `check_ga.py`, run on a laptop CPU with LAMMPS 22 Jul 2025, 2026-10-31; all
outputs in this folder):

| Check | Result | Pass? |
|---|---|---|
| alpha-Ga below fcc at 0 K | −3.0039 vs −2.9000 eV/atom (−104 meV/atom) | yes |
| relaxed 0 K cell within 3% of the experimental alpha-Ga cell | a ×0.999, **b ×0.916, c ×1.066** | **no** |
| atoms stay put at 303 K (20 ps NPT) | 0% moved > 1.5 Å | yes |
| box shape kept at 303 K | within 2.3% of the relaxed cell | yes |
| one dimer neighbour within 2.6 Å | **1.9** per atom | **no** |
| first RDF peak near the 2.44 Å alpha-Ga dimer | **2.71 Å** | **no** |

**Verdict: failed.** The potential relaxes alpha-Ga into a different, distorted structure: the covalent dimer that
defines alpha-Ga (2.44 Å) is gone. A "melting point" of that structure would not be gallium's, so Ga has **no
classical-MD jobs** (its `manifest.json` lists none). The potential files and this evidence stay, so the failure
is visible.

Options if we still want an independent Ga check: a potential fitted to pure Ga (none LAMMPS-ready on NIST IPR;
Belashchenko 2012 is liquid-only), or a second MLIP instead of classical MD.

Reproduce (from `metals/Ga/`): `lmp -in stability/in.ga_stability.lmp -var T 302.92 -var seed 2026 -log
stability/log.ga_stability`, then `python stability/check_ga.py stability/log.ga_stability stability/rdf_T302.92.dat`.
