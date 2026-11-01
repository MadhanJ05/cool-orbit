"""scripts/run_science.py run_until_idle with --until: keep watching while another process may post (SYNTHETIC)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_science_under_test", REPO / "scripts" / "run_science.py")
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)  # type: ignore[union-attr]


class Agent:
    def __init__(self, work_at: set[int]) -> None:
        self.calls, self.work_at = 0, work_at

    def step(self) -> bool:
        self.calls += 1
        return self.calls in self.work_at


class RunUntilIdleTest(unittest.TestCase):
    def test_without_until_it_stops_at_the_first_idle_round(self) -> None:
        a = Agent({1})
        out = rs.run_until_idle([a], 100)
        self.assertEqual((out["stopped"], out["rounds"]), ("idle", 2))

    def test_with_until_it_keeps_polling_and_picks_up_late_work(self) -> None:
        now = [0.0]
        slept = []

        def sleep(s: float) -> None:
            slept.append(s)
            now[0] += s

        a = Agent({1, 4})  # work in round 1, then nothing, then new work appears in round 4
        out = rs.run_until_idle([a], 100, until=100.0, poll=30.0, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(a.calls, out["rounds"])
        self.assertGreaterEqual(a.calls, 5, "it kept looking after the first idle round")
        self.assertEqual(out["stopped"], "until")
        self.assertTrue(all(s == 30.0 for s in slept))


if __name__ == "__main__":
    unittest.main()
