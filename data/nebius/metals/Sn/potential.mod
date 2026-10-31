# Sn: 2NN MEAM, NIST IPR 2018--Ko-W-S--Sn--LAMMPS--ipr1 (doi:10.3390/met8110900). Needs the MEAM package.
pair_style meam
pair_coeff * * potential/library.meam Sn potential/Sn.meam Sn
