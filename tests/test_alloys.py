"""Structure and composition tests for the alloy builder (CPU, no MD). Needs ASE; skipped without it.

The nearest-neighbour checks are what make the textbook structures trustworthy: a wrong space group or
Wyckoff position would give the wrong coordination shells.
"""

from __future__ import annotations

import unittest

try:
    import numpy as np

    from coolorbit.sim.alloys import (composition_label, element_crystal, mean_atomic_mass, mixed_alloy_box,
                                      parse_composition)
    HAVE_ASE = True
except ImportError:
    HAVE_ASE = False


def shells(symbol: str, reps=(3, 3, 3), tol: float = 0.05) -> list[tuple[float, int]]:
    """(distance, count) of the first neighbour shells around atom 0, grouping distances within tol."""
    atoms = element_crystal(symbol).repeat(reps)
    d = np.sort(atoms.get_distances(0, range(1, len(atoms)), mic=True))
    out: list[tuple[float, int]] = []
    for x in d[:16]:
        if out and abs(x - out[-1][0]) < tol:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((x, 1))
    return out


@unittest.skipUnless(HAVE_ASE, "needs ASE")
class ElementCrystalTest(unittest.TestCase):
    def test_atoms_per_conventional_cell(self) -> None:
        expected = {"Ga": 8, "In": 2, "Sn": 4, "Bi": 6, "Cu": 4, "Al": 4}
        for sym, n in expected.items():
            self.assertEqual(len(element_crystal(sym)), n, sym)

    def test_alpha_gallium_has_its_covalent_dimer(self) -> None:
        (d1, n1), (d2, _), *_ = shells("Ga")
        self.assertEqual(n1, 1)                 # each Ga has exactly one close partner (Ga2 dimer)
        self.assertAlmostEqual(d1, 2.44, delta=0.05)
        self.assertGreater(d2, 2.6)             # then the 2.7-2.8 A shells

    def test_white_tin_has_4_plus_2_neighbours(self) -> None:
        (d1, n1), (d2, n2), *_ = shells("Sn")
        self.assertEqual((n1, n2), (4, 2))
        self.assertAlmostEqual(d1, 3.02, delta=0.05)
        self.assertAlmostEqual(d2, 3.18, delta=0.05)

    def test_bismuth_has_3_plus_3_neighbours(self) -> None:
        (d1, n1), (d2, n2), *_ = shells("Bi")
        self.assertEqual((n1, n2), (3, 3))
        self.assertAlmostEqual(d1, 3.07, delta=0.06)
        self.assertAlmostEqual(d2, 3.53, delta=0.06)

    def test_indium_has_4_plus_8_neighbours(self) -> None:
        (d1, n1), (d2, n2), *_ = shells("In")
        self.assertEqual((n1, n2), (4, 8))
        self.assertAlmostEqual(d1, 3.25, delta=0.05)
        self.assertAlmostEqual(d2, 3.38, delta=0.05)

    def test_fcc_metals_have_12_neighbours(self) -> None:
        for sym in ("Cu", "Al"):
            (d1, n1), *_ = shells(sym)
            self.assertEqual(n1, 12, sym)


@unittest.skipUnless(HAVE_ASE, "needs ASE")
class CompositionTest(unittest.TestCase):
    def test_weight_to_atomic(self) -> None:
        # EGaIn, 75.5 wt% Ga / 24.5 wt% In
        f = parse_composition("wt:Ga75.5In24.5")
        self.assertAlmostEqual(f["Ga"], 0.8354, places=3)
        self.assertAlmostEqual(sum(f.values()), 1.0, places=12)
        self.assertEqual(composition_label(f), "Ga83.5In16.5")

    def test_atomic_and_errors(self) -> None:
        self.assertEqual(parse_composition("at:Ga50In50"), {"Ga": 0.5, "In": 0.5})
        for bad in ("Ga50In50", "wt:", "wt:Xx10", "at:Ga50In50!"):
            with self.assertRaises(ValueError, msg=bad):
                parse_composition(bad)

    def test_mixed_box_counts_and_density(self) -> None:
        f = parse_composition("wt:Bi32.5In51Sn16.5")  # Field's metal
        box = mixed_alloy_box(f, n_atoms=500, seed=1)
        n = len(box)
        self.assertGreaterEqual(n, 500)
        counts = {s: box.get_chemical_symbols().count(s) for s in f}
        for s, x in f.items():
            self.assertLessEqual(abs(counts[s] - x * n), 1, s)
        density = mean_atomic_mass(f) * n / box.get_volume() * 1.66053906660  # g/cm3
        self.assertTrue(7.0 < density < 9.0, density)  # starting box near the measured ~7.9 g/cm3


if __name__ == "__main__":
    unittest.main()
