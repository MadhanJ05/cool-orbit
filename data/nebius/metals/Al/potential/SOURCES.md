# Al potential: EAM (LAMMPS pair_style eam/alloy)

Downloaded unchanged from the NIST Interatomic Potentials Repository, implementation `1999--Mishin-Y--Al--LAMMPS--ipr1`:
https://www.ctcms.nist.gov/potentials/entry/1999--Mishin-Y-Farkas-D-Mehl-M-J-Papaconstantopoulos-D-A--Al/

**Cite:** Y. Mishin, D. Farkas, M.J. Mehl, and D.A. Papaconstantopoulos (1999), "Interatomic potentials for monoatomic metals from experimental data and ab initio calculations", Physical Review B 59(5), 3393-3407. DOI: [10.1103/physrevb.59.3393](https://doi.org/10.1103/physrevb.59.3393)

**LAMMPS:** `pair_style eam/alloy` + `pair_coeff * * potential/Al99.eam.alloy Al`

**Implementation page:** https://www.ctcms.nist.gov/potentials/entry/1999--Mishin-Y-Farkas-D-Mehl-M-J-Papaconstantopoulos-D-A--Al/1999--Mishin-Y--Al--LAMMPS--ipr1.html

**Downloaded from:**
- https://www.ctcms.nist.gov/potentials/Download/1999--Mishin-Y-Farkas-D-Mehl-M-J-Papaconstantopoulos-D-A--Al/2/Al99.eam.alloy

**Release notes (not committed):** https://www.ctcms.nist.gov/potentials/Download/1999--Mishin-Y-Farkas-D-Mehl-M-J-Papaconstantopoulos-D-A--Al/2/Al99_releaseNotes_1.pdf (SHA-256 `a740ce36126559fbee65273b8d4380d29d8cc0ecac7a97680e166cea093dbe30`). They give the LAMMPS usage above and the 0 K fcc energy at a = 4.05 A, -3.359999988125 eV/atom; the committed file reproduces it (ASE EAM calculator, 2026-11-01).

**NIST's own melting estimate for this implementation:** 1040.41 +/- 1.04 K (A1--Cu--fcc; NIST iprPy melting_temperature (two-phase, NPH + Berendsen, 200,000 steps)). Context only, from the implementation page.

**SHA-256:**
- `Al99.eam.alloy`: `60c8a085be79d273324ab421f5b1447578fef55c1acfc6492c0999f15ee8a284`

**Caveats:**
- NIST's own iprPy two-phase estimate for this implementation is 1040.41 +/- 1.04 K (fcc), +11.5% vs the measured 933.45 K, i.e. just outside the +/-10% tier1 gate. If our run agrees, this potential is not a usable check for Al. Context only; it does not set the bracket.
- The abstract (as listed on NIST) does not name the melting point among the fitted or reproduced properties; the paper's fitting database itself was not checked.
