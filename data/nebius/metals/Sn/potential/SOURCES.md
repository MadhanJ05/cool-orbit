# Sn potential: 2NN MEAM (LAMMPS pair_style meam)

Downloaded unchanged from the NIST Interatomic Potentials Repository, implementation `2018--Ko-W-S--Sn--LAMMPS--ipr1`:
https://www.ctcms.nist.gov/potentials/entry/2018--Ko-W-S-Kim-D-H-Kwon-Y-J-Lee-M--Sn/

**Cite:** W.-S. Ko, D.-H. Kim, Y.-J. Kwon, and M. Lee (2018), "Atomistic Simulations of Pure Tin Based on a New Modified Embedded-Atom Method Interatomic Potential", Metals 8(11), 900. DOI: [10.3390/met8110900](https://doi.org/10.3390/met8110900)

**LAMMPS:** `pair_style meam` + `pair_coeff * * potential/library.meam Sn potential/Sn.meam Sn`

**SHA-256:**
- `library.meam`: `96ec767178674f624b08135129c72e98927855ac277dd35d7f04b3394e3b94b7`
- `Sn.meam`: `b25add2fed441cf67f5a1ae5c2b165f03b5083f34ada8f783140ea7853f0dac3`

**Caveats:**
- Starts from beta-Sn (white tin), the phase that melts; alpha-Sn is irrelevant above 286 K.
