# Bi: 2NN MEAM, NIST IPR 2021--Zhou-H--Bi--LAMMPS--ipr1 (doi:10.1088/1361-651x/ac095c). Needs the MEAM package.
pair_style meam
pair_coeff * * potential/Bi.library.meam Bi potential/Bi.parameter.meam Bi
