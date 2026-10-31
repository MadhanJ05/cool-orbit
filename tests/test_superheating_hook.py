"""Unit tests for SuperheatingDetectorHook with synthetic positions (CPU, no MD, no model).

Needs nvalchemi (GB10 env); skipped elsewhere. Run: .venv/bin/python -m unittest tests.test_superheating_hook -v
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import torch
    from ase.build import bulk

    from coolorbit.hooks import SuperheatingDetected, SuperheatingDetectorHook
    HAVE_DEPS = True
except ImportError:  # laptop without nvalchemi
    HAVE_DEPS = False


def _lattice(reps: int = 4):
    atoms = bulk("Cu", "fcc", a=3.61, cubic=True).repeat((reps, reps, reps))
    return (torch.tensor(atoms.positions, dtype=torch.float32),
            torch.tensor(atoms.numbers), torch.tensor(atoms.cell.array, dtype=torch.float32))


@unittest.skipUnless(HAVE_DEPS, "needs nvalchemi + torch (GB10 environment)")
class SuperheatingHookTest(unittest.TestCase):
    """Three systems in one batch: crystal that melts, two-phase box, crystal that stays solid."""

    def setUp(self) -> None:
        torch.manual_seed(0)
        self.pos, self.z, self.cell = _lattice()
        self.n = self.pos.shape[0]
        self.top = (self.pos[:, 2] > self.cell[2, 2] / 2)

    def _random(self, k: int) -> torch.Tensor:
        return torch.rand(k, 3) @ self.cell

    def _batch(self, systems: list[torch.Tensor]) -> SimpleNamespace:
        m = len(systems)
        return SimpleNamespace(
            positions=torch.cat(systems), num_graphs=m,
            batch_idx=torch.arange(m).repeat_interleave(self.n),
            cell=self.cell.expand(m, 3, 3).clone(),
            atomic_numbers=self.z.repeat(m),
            velocities=torch.zeros(m * self.n, 3), atomic_masses=torch.full((m * self.n,), 63.5))

    def _vibrate(self, pos: torch.Tensor) -> torch.Tensor:
        return pos + 0.08 * torch.randn_like(pos)

    def _two_phase(self) -> torch.Tensor:
        p = self._vibrate(self.pos)
        p[self.top] = self._random(int(self.top.sum()))
        return p

    def _run(self, hook, frames) -> None:
        for step, systems in frames:
            hook(SimpleNamespace(batch=self._batch(systems), step_count=step), hook.stage)

    def test_flags_only_the_perfect_crystal_that_melts(self) -> None:
        hook = SuperheatingDetectorHook(action="record")
        melted = self._random(self.n)
        self._run(hook, [
            (0, [self.pos, self._two_phase(), self.pos]),
            (100, [self._vibrate(self.pos), self._two_phase(), self._vibrate(self.pos)]),
            (1000, [melted, self._random(self.n), self._vibrate(self.pos)]),
        ])
        crystal_melts, two_phase, crystal_stays = hook.reports
        self.assertEqual(crystal_melts.start_state, "crystalline")
        self.assertTrue(crystal_melts.melted)
        self.assertEqual(crystal_melts.melt_step, 1000)
        self.assertIn("superheating_observed", crystal_melts.flags)

        self.assertEqual(two_phase.start_state, "disordered_or_interface")
        self.assertFalse(two_phase.melted)  # a two-phase box is the correct method: never flagged
        self.assertEqual(two_phase.flags, [])

        self.assertEqual(crystal_stays.start_state, "crystalline")
        self.assertFalse(crystal_stays.melted)
        self.assertEqual(crystal_stays.flags, ["superheating_risk"])
        self.assertAlmostEqual(crystal_melts.nn_distance_A, 3.61 / 2 ** 0.5, places=2)

    def test_handles_periodic_wrapping(self) -> None:
        """A crystal shifted by a whole lattice vector (wrapped) must not look mobile."""
        hook = SuperheatingDetectorHook(action="record")
        wrapped = self.pos + self.cell[0]
        self._run(hook, [(0, [self.pos]), (100, [wrapped]), (1000, [wrapped])])
        self.assertEqual(hook.reports[0].start_state, "crystalline")
        self.assertFalse(hook.reports[0].melted)

    def test_raise_action(self) -> None:
        hook = SuperheatingDetectorHook(action="raise")
        with self.assertRaises(SuperheatingDetected):
            self._run(hook, [(0, [self.pos]), (100, [self._vibrate(self.pos)]), (1000, [self._random(self.n)])])


if __name__ == "__main__":
    unittest.main()
