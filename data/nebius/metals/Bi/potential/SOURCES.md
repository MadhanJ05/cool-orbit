# Bi potential: 2NN MEAM (LAMMPS pair_style meam)

Downloaded unchanged from the NIST Interatomic Potentials Repository, implementation `2021--Zhou-H--Bi--LAMMPS--ipr1`:
https://www.ctcms.nist.gov/potentials/entry/2021--Zhou-H-Dickel-D-E-Baskes-M-I-et-al--Bi/

**Cite:** H. Zhou, D.E. Dickel, M.I. Baskes, S. Mun, and M. Asle Zaeem (2021), "A modified embedded-atom method interatomic potential for bismuth", Modelling Simul. Mater. Sci. Eng. 29(6), 065008. DOI: [10.1088/1361-651x/ac095c](https://doi.org/10.1088/1361-651x/ac095c)

**LAMMPS:** `pair_style meam` + `pair_coeff * * potential/Bi.library.meam Bi potential/Bi.parameter.meam Bi`

**SHA-256:**
- `Bi.library.meam`: `2f4c015669f0b0bed4f434fa322070c47fa797c9aca87de26ec2cfc109cbe9a7`
- `Bi.parameter.meam`: `c6c6e9da06f4efc8b09f9ed4a5ae03029d47c66eedb443980b1df9587e641efa`

**Caveats:**
- The paper's abstract says the potential reproduces the melting point of pure Bi, so the classical check is partly fitted to the experimental value for Bi; say so when comparing.
