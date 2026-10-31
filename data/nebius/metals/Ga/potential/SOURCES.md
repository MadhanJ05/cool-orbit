# Ga potential: 2NN MEAM (LAMMPS pair_style meam)

Downloaded unchanged from the NIST Interatomic Potentials Repository, implementation `2009--Do-E-C--Ga-In--LAMMPS--ipr1`:
https://www.ctcms.nist.gov/potentials/entry/2009--Do-E-C-Shin-Y-H-Lee-B-J--Ga-In/

**Cite:** E.C. Do, Y.-H. Shin, and B.-J. Lee (2009), "Atomistic modeling of III-V nitrides: modified embedded-atom method interatomic potentials for GaN, InN and Ga1-xInxN", J. Phys.: Condens. Matter 21(32), 325801. DOI: [10.1088/0953-8984/21/32/325801](https://doi.org/10.1088/0953-8984/21/32/325801)

**LAMMPS:** `pair_style meam` + `pair_coeff * * potential/library.meam Ga In potential/GaIn.meam Ga`

**SHA-256:**
- `library.meam`: `0cef73bcd53308fa57e2ab909c75a3bb8995cadd9a353104db3f685caaad336b`
- `GaIn.meam`: `b053845a23d9ce66780738514bd4c6663e1d672d8f4cf70c65979ef231194910`

**Caveats:**
- The Ga parameters come from a Ga-In(-N) alloy fit. NIST warns that elemental potentials taken from alloy descriptions may not work well for the pure species; validate before trusting.
- alpha-Ga is a covalent-dimer (Cmce) metal; 2NN MEAM fitted with an fcc reference may not reproduce its structure. Check the solid stays alpha-Ga during the solid NPT stage.
- No NIST potential for pure Ga ships LAMMPS files (Belashchenko 2012 EAM is liquid-only, no files).
