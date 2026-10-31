"""Neighbour shells of experimental alpha-Ga vs this potential's relaxed 0 K structure (needs ASE).

    python stability/shells_0K.py      # from metals/Ga/, with the repo's .venv (ASE + coolorbit on the path)
"""

import sys
from pathlib import Path

import numpy as np
from ase.io import read
from ase.neighborlist import neighbor_list

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from coolorbit.sim.alloys import element_crystal  # noqa: E402


def shells(atoms) -> tuple[list[float], float]:
    i, d = neighbor_list("id", atoms, 3.2)
    per = [np.sort(d[i == k])[:7] for k in range(len(atoms))]
    return [round(float(x), 3) for x in np.mean(per, axis=0)], float(np.mean([(p < 2.6).sum() for p in per]))


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    relaxed = read(here / "alpha_relaxed.lmp", format="lammps-data", atom_style="atomic")
    relaxed.set_atomic_numbers([31] * len(relaxed))
    for label, atoms in (("experimental alpha-Ga", element_crystal("Ga")), ("MEAM relaxed, 0 K", relaxed)):
        d, n = shells(atoms)
        print(f"{label:22s} 1st..7th neighbour distances {d} A; neighbours within 2.6 A: {n:.2f}")
