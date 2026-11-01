# Cu: EAM, NIST IPR 2001--Mishin-Y--Cu-1--LAMMPS--ipr1 (doi:10.1103/physrevb.63.224106). Needs the MANYBODY package.
pair_style eam/alloy
pair_coeff * * potential/Cu01.eam.alloy Cu
