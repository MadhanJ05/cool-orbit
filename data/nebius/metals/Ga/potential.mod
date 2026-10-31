# Ga: 2NN MEAM, NIST IPR 2009--Do-E-C--Ga-In--LAMMPS--ipr1 (doi:10.1088/0953-8984/21/32/325801). Needs the MEAM package.
pair_style meam
pair_coeff * * potential/library.meam Ga In potential/GaIn.meam Ga
