# In: 2NN MEAM, NIST IPR 2008--Do-E-C--In--LAMMPS--ipr1 (doi:10.1016/j.calphad.2007.08.004). Needs the MEAM package.
pair_style meam
pair_coeff * * potential/library.meam In potential/In.meam In
