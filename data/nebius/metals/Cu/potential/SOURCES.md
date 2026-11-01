# Cu potential: EAM (LAMMPS pair_style eam/alloy)

Downloaded unchanged from the NIST Interatomic Potentials Repository, implementation `2001--Mishin-Y--Cu-1--LAMMPS--ipr1`:
https://www.ctcms.nist.gov/potentials/entry/2001--Mishin-Y-Mehl-M-J-Papaconstantopoulos-D-A-et-al--Cu-1/

**Cite:** Y. Mishin, M.J. Mehl, D.A. Papaconstantopoulos, A.F. Voter, and J.D. Kress (2001), "Structural stability and lattice defects in copper: Ab initio, tight-binding, and embedded-atom calculations", Physical Review B 63(22), 224106. DOI: [10.1103/physrevb.63.224106](https://doi.org/10.1103/physrevb.63.224106)

**LAMMPS:** `pair_style eam/alloy` + `pair_coeff * * potential/Cu01.eam.alloy Cu`

**Implementation page:** https://www.ctcms.nist.gov/potentials/entry/2001--Mishin-Y-Mehl-M-J-Papaconstantopoulos-D-A-et-al--Cu-1/2001--Mishin-Y--Cu-1--LAMMPS--ipr1.html

**Downloaded from:**
- https://www.ctcms.nist.gov/potentials/Download/2001--Mishin-Y-Mehl-M-J-Papaconstantopoulos-D-A-et-al--Cu-1/2/Cu01.eam.alloy

**Release notes (not committed):** https://www.ctcms.nist.gov/potentials/Download/2001--Mishin-Y-Mehl-M-J-Papaconstantopoulos-D-A-et-al--Cu-1/2/Cu01_releaseNotes_1.pdf (SHA-256 `cb000976d3760baf6bb3718ca91b9eec1ed1db27019994a4702f31bffffe54cb`). They give the LAMMPS usage above and the 0 K fcc energy at a = 3.615 A, -3.539999968 eV/atom; the committed file reproduces it (ASE EAM calculator, 2026-11-01).

**NIST's own melting estimate for this implementation:** 1323.26 +/- 1.46 K (A1--Cu--fcc; NIST iprPy melting_temperature (two-phase, NPH + Berendsen, 200,000 steps)). Context only, from the implementation page.

**SHA-256:**
- `Cu01.eam.alloy`: `5e374260448eea5c26de59432d75e702c1778cc6999d2527a789e5387ce9dc9e`

**Caveats:**
- This is the paper's EAM1 potential (NIST notes: 'This listing is for the reference's EAM1 potential'); its EAM2 is listed without LAMMPS files.
- NIST's own iprPy two-phase estimate for this implementation is 1323.26 +/- 1.46 K (fcc), on its implementation page. Context only; it does not set the bracket or the reference.
- The abstract (as listed on NIST) does not name the melting point among the fitted or reproduced properties; the paper's fitting database itself was not checked.
