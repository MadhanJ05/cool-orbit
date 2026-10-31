"""Geometry test for the two-phase box preparation (CPU, no MD). Needs nvalchemi (GB10 env); skipped elsewhere."""

from __future__ import annotations

import unittest

try:
    import numpy as np
    from ase.build import bulk

    from coolorbit.sim.melting import expand_top_half
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False


@unittest.skipUnless(HAVE_DEPS, "needs nvalchemi + ASE (GB10 environment)")
class ExpandTopHalfTest(unittest.TestCase):
    def test_gives_the_melting_half_room_without_touching_the_crystal(self) -> None:
        atoms = bulk("Cu", "fcc", a=3.61, cubic=True).repeat((4, 4, 8))
        scaled = atoms.get_scaled_positions(wrap=True)
        frozen = scaled[:, 2] < 0.5
        out = expand_top_half(atoms, frozen, 0.05)

        np.testing.assert_allclose(out.positions[frozen], (scaled @ np.asarray(atoms.cell))[frozen], atol=1e-9)
        self.assertAlmostEqual(out.cell[2, 2], atoms.cell[2, 2] * 1.025, places=9)
        np.testing.assert_allclose(out.cell[:2], atoms.cell[:2])
        s = out.get_scaled_positions(wrap=False)
        self.assertTrue(((s > -1e-9) & (s < 1 + 1e-9)).all())
        before = atoms.get_all_distances(mic=True)
        after = out.get_all_distances(mic=True)
        np.fill_diagonal(before, np.inf)
        np.fill_diagonal(after, np.inf)
        self.assertGreaterEqual(after.min(), before.min() - 1e-9)  # stretching never squeezes atoms together


if __name__ == "__main__":
    unittest.main()
