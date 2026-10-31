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


@unittest.skipUnless(HAVE_DEPS, "needs nvalchemi + ASE (GB10 environment)")
class MeltEscalationTest(unittest.TestCase):
    """melt_until_liquid with the MD faked out: hotter each round, stops at 80%, capped at 3x."""

    def _run(self, fractions, t_melt=480.0, t_ref=300.0):
        from types import SimpleNamespace
        from unittest import mock

        import torch

        from coolorbit.sim import melting

        s = melting.Settings(mode="coexistence", label="t", model="m", dt_fs=2.0, temps=[t_ref], t_prep=t_ref,
                             t_melt=t_melt, equil_steps=1, melt_steps=100, quench_steps=1, prod_steps=1,
                             record_every=1, friction_per_fs=0.01, thermostat_fs=100.0, barostat_fs=1000.0,
                             pe_threshold=0.002, jump_threshold=0.03, seed=1)
        batch = SimpleNamespace(positions=torch.zeros(4, 3), cell=torch.eye(3)[None], num_graphs=1)
        used = []
        with mock.patch.object(melting, "nvt", side_effect=lambda m, s_, t, steps, seed: used.append((t, steps))), \
             mock.patch.object(melting, "run_stage", side_effect=lambda dyn, b, *a, **k: b), \
             mock.patch.object(melting, "displaced_fraction", side_effect=list(fractions)):
            _, melted, temps = melting.melt_until_liquid(batch, None, s, [], "prep", torch.ones(4, dtype=torch.bool),
                                                         False, t_ref)
        return melted, temps, used

    def test_escalates_until_melted(self) -> None:
        melted, temps, used = self._run([0.30, 0.60, 0.95])
        self.assertEqual(temps, [480.0, 600.0, 750.0])        # x1.25 per extra round
        self.assertEqual([s for _, s in used], [100, 50, 50])  # extra rounds are half as long
        self.assertAlmostEqual(melted, 0.95)

    def test_stops_as_soon_as_melted(self) -> None:
        _, temps, _ = self._run([0.85, 0.99, 0.99])
        self.assertEqual(temps, [480.0])

    def test_capped_at_three_times_the_reference(self) -> None:
        _, temps, _ = self._run([0.1, 0.1, 0.1], t_melt=800.0, t_ref=300.0)
        self.assertEqual(temps, [800.0, 900.0, 900.0])         # 1000 and 1250 capped at 3 x 300


if __name__ == "__main__":
    unittest.main()
