# Al: EAM, NIST IPR 1999--Mishin-Y--Al--LAMMPS--ipr1 (doi:10.1103/physrevb.59.3393). Needs the MANYBODY package.
pair_style eam/alloy
pair_coeff * * potential/Al99.eam.alloy Al
