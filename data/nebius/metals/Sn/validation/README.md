# Laptop validation outputs for Sn (NOT results)

Real LAMMPS outputs from the laptop validation (LAMMPS 22 Jul 2025 wheel, CPU, 2026-10-31), kept so the tests of
`analyze.py` read real LAMMPS files, not hand-written ones.

- `pe_T505.1.dat`, `log_val_prep.lammps`: the full coexistence preparation (equilibrate, melt, quench) with only
  400 production steps, so `analyze.py coexist` correctly refuses it (too little production data).
- `h_solid_T505.1.dat`, `h_liquid_T505.1.dat`, `log_val_solid.lammps`, `log_val_liquid.lammps`: full-length latent
  runs at the **experimental** melting point (505.1 K), not at this potential's own melting point. Their latent heat
  is therefore not a result and must not be reported.
