"""Arm C's Simulator fits its preset to the task's remaining wall clock (post-hoc change after cmp-1 repeat 1).

All jobs below are SYNTHETIC (no board artifacts, no GPU). Run from the repo root:
    .venv/bin/python -m unittest tests.test_arm_c_budget -v
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board
from coolorbit.sim.simulator_agent import Job

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_arm_c_under_test", REPO / "scripts" / "run_arm_c.py")
arm_c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arm_c)  # type: ignore[union-attr]

NOW = 1_000_000.0


def job(preset: str = "production", prod_steps=None, n_atoms: int = 512) -> Job:
    # The size of cmp-1's aluminium job: 512 atoms x 4 temperatures, two-phase coexistence.
    return Job(target=None, need_index=0, need={}, candidate=None, property="melting_point", mode="coexistence",
               temps=[800.0, 900.0, 1000.0, 1100.0], reps=[4, 4, 8], dt_fs=2.0, preset=preset,
               prod_steps=prod_steps, attempt=1, structure_args=[], n_atoms=n_atoms)


class FitToBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "b")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def sim(self, minutes_left):
        deadline = None if minutes_left is None else NOW + minutes_left * 60
        return arm_c.ArmCSimulator(self.board, "simulator_gb10", deadline=deadline, clock=lambda: NOW)

    def test_production_that_cannot_fit_drops_to_quick(self) -> None:
        j = job()
        self.assertGreater(j.estimate_minutes(), 30)  # ~48 idle-GPU minutes: more than half of 60
        fitted = self.sim(60).fit_to_budget(j)
        self.assertEqual(fitted.preset, "quick")
        self.assertLessEqual(fitted.estimate_minutes(), 30)

    def test_production_that_fits_is_kept(self) -> None:
        self.assertEqual(self.sim(200).fit_to_budget(job()).preset, "production")

    def test_without_a_deadline_nothing_changes(self) -> None:
        j = job()
        self.assertIs(self.sim(None).fit_to_budget(j), j)

    def test_a_longer_retry_drops_its_extra_length_only_if_it_must(self) -> None:
        long_retry = job(prod_steps=50_000)
        fitted = self.sim(60).fit_to_budget(long_retry)
        self.assertEqual((fitted.preset, fitted.prod_steps), ("quick", None))
        modest = job(prod_steps=16_000)  # production ~33 min (> 30), quick ~28 min (fits): keep the length
        self.assertGreater(modest.estimate_minutes(), 30)
        fitted = self.sim(60).fit_to_budget(modest)
        self.assertEqual((fitted.preset, fitted.prod_steps), ("quick", 16_000))
        self.assertLessEqual(fitted.estimate_minutes(), 30)

    def test_record_note_says_post_hoc(self) -> None:
        self.assertIn("post-hoc", arm_c.FIT_TO_BUDGET_NOTE)


if __name__ == "__main__":
    unittest.main()
