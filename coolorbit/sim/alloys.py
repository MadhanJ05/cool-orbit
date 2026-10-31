"""Low-melting metal alloys: element crystals and mixed (liquid-start) alloy boxes.

Why this track: low-melting alloys (Ga, In, Sn, Bi families) conduct heat 50-100x better than paraffin, and
MACE-MP models are trained on inorganic materials, so they are in domain. Many alloys have a measured melting
(eutectic) temperature but a poorly measured latent heat; computing that is a defensible prediction.

What this module provides (no GPU, no MD):

* ``element_crystal(symbol)``: the stable room-temperature crystal of Ga, In, Sn (beta, white tin), Bi, Cu, Al,
  built from its space group and Wyckoff positions. Lattice constants are starting values only (the NPT
  barostat relaxes them); the *topology* (space group + positions) is what matters, and
  ``tests/test_alloys.py`` checks every structure's nearest-neighbour shells against the known ones.
* ``parse_composition("wt:Bi32.5In51Sn16.5")``: atomic fractions from wt% or at%.
* ``mixed_alloy_box(fractions, n_atoms)``: a randomly mixed box at the composition-averaged atomic volume,
  as the starting point for a liquid alloy (MD melts it).

Honest limit, stated where results are reported: we do **not** predict new alloys' melting temperatures
(that needs phase-diagram methods). Alloy latent heats are estimated as H(liquid alloy) - sum x_i H(solid
element i) at the alloy's measured melting temperature, which assumes the solid is a mixture of the pure
elements; intermetallic phases (e.g. BiIn2 in Field's metal) would lower the true value.

Structure source (space groups and Wyckoff positions): J. Donohue, *The Structures of the Elements*
(Wiley, 1974); alpha-Ga refinement: Sharma & Donohue, Z. Kristallogr. 117, 293 (1962).
"""

from __future__ import annotations

import re

import numpy as np
from ase import Atoms
from ase.data import atomic_masses, atomic_numbers
from ase.spacegroup import crystal

# symbol: (phase name, space group number, basis (fractional), cellpar a,b,c,alpha,beta,gamma, setting)
ELEMENT_CRYSTALS: dict[str, tuple] = {
    "Ga": ("alpha-Ga (Cmce, oS8)", 64, [(0.0, 0.1539, 0.0798)], [4.519, 7.659, 4.526, 90, 90, 90], 1),
    "In": ("In (I4/mmm, tI2)", 139, [(0.0, 0.0, 0.0)], [3.252, 3.252, 4.946, 90, 90, 90], 1),
    "Sn": ("beta-Sn, white tin (I41/amd, tI4)", 141, [(0.0, 0.0, 0.0)], [5.832, 5.832, 3.182, 90, 90, 90], 1),
    "Bi": ("Bi (R-3m, hR2; hexagonal axes)", 166, [(0.0, 0.0, 0.2339)], [4.546, 4.546, 11.862, 90, 90, 120], 1),
    "Cu": ("Cu (Fm-3m, cF4)", 225, [(0.0, 0.0, 0.0)], [3.615, 3.615, 3.615, 90, 90, 90], 1),
    "Al": ("Al (Fm-3m, cF4)", 225, [(0.0, 0.0, 0.0)], [4.050, 4.050, 4.050, 90, 90, 90], 1),
}


def element_crystal(symbol: str) -> Atoms:
    """Conventional cell of the element's stable room-temperature crystal (see ELEMENT_CRYSTALS)."""
    if symbol not in ELEMENT_CRYSTALS:
        raise KeyError(f"no reference crystal for {symbol!r}; known: {sorted(ELEMENT_CRYSTALS)}")
    _, sg, basis, cellpar, setting = ELEMENT_CRYSTALS[symbol]
    return crystal(symbol, basis, spacegroup=sg, cellpar=cellpar, setting=setting, primitive_cell=False)


def atomic_volume(symbol: str) -> float:
    """Volume per atom (A^3) of the reference crystal at its starting lattice constants."""
    atoms = element_crystal(symbol)
    return atoms.get_volume() / len(atoms)


_COMP_RE = re.compile(r"([A-Z][a-z]?)(\d+(?:\.\d+)?)")


def parse_composition(spec: str) -> dict[str, float]:
    """``"wt:Bi32.5In51Sn16.5"`` or ``"at:Ga86In14"`` -> atomic fractions summing to 1 (sorted by symbol)."""
    basis, _, body = spec.partition(":")
    if basis not in ("wt", "at") or not body:
        raise ValueError(f"composition must look like wt:Bi32.5In51Sn16.5 or at:Ga86In14, not {spec!r}")
    parts = _COMP_RE.findall(body)
    if not parts or "".join(f"{s}{v}" for s, v in parts) != body:
        raise ValueError(f"could not parse composition {spec!r}")
    amounts: dict[str, float] = {}
    for sym, val in parts:
        if sym not in atomic_numbers:
            raise ValueError(f"unknown element {sym!r} in {spec!r}")
        amounts[sym] = amounts.get(sym, 0.0) + float(val)
    if basis == "wt":
        amounts = {s: v / atomic_masses[atomic_numbers[s]] for s, v in amounts.items()}
    total = sum(amounts.values())
    if total <= 0:
        raise ValueError(f"composition {spec!r} has no material")
    return {s: amounts[s] / total for s in sorted(amounts)}


def composition_label(fractions: dict[str, float]) -> str:
    """Atomic-percent label, e.g. {'Ga': 0.8354, 'In': 0.1646} -> 'Ga83.5In16.5'."""
    return "".join(f"{s}{100 * x:.1f}" for s, x in sorted(fractions.items()))


def mean_atomic_mass(fractions: dict[str, float]) -> float:
    return sum(x * atomic_masses[atomic_numbers[s]] for s, x in fractions.items())


def mixed_alloy_box(fractions: dict[str, float], n_atoms: int = 500, expansion: float = 0.03,
                    seed: int = 0) -> Atoms:
    """Randomly mixed alloy on an FCC scaffold (a starting configuration for melting; not a crystal model).

    The box volume is the composition-weighted atomic volume of the element crystals times (1 + expansion),
    since a liquid is less dense. The species counts match the fractions as closely as integers allow.
    """
    m = max(2, int(np.ceil((n_atoms / 4) ** (1 / 3))))
    n = 4 * m ** 3
    v_atom = sum(x * atomic_volume(s) for s, x in fractions.items()) * (1.0 + expansion)
    a = (4 * v_atom) ** (1 / 3)
    base = np.array([[0, 0, 0], [0, 0.5, 0.5], [0.5, 0, 0.5], [0.5, 0.5, 0]])
    grid = np.array([[i, j, k] for i in range(m) for j in range(m) for k in range(m)])
    positions = (grid[:, None, :] + base[None, :, :]).reshape(-1, 3) * a

    symbols = sorted(fractions)
    counts = np.floor(np.array([fractions[s] for s in symbols]) * n).astype(int)
    remainders = np.array([fractions[s] for s in symbols]) * n - counts
    for idx in np.argsort(-remainders)[: n - counts.sum()]:  # largest-remainder rounding
        counts[idx] += 1
    species = np.repeat(symbols, counts)
    np.random.default_rng(seed).shuffle(species)
    return Atoms(symbols=list(species), positions=positions, cell=np.eye(3) * a * m, pbc=True)
