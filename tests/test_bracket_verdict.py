"""Coexistence bracket verdicts: "above/below the bracket" only when the end of the bracket is decisive.

Fixtures are the per-temperature verdicts of tonight's real MACE production runs (Cu attempts 1-2, Ga), plus
synthetic cases. Needs the simulation package (imports coolorbit.sim.melting).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from coolorbit.sim.melting import bracket_verdict
    from coolorbit.sim.simulator_agent import retry_need
    HAVE_SIM = True
except ImportError:
    HAVE_SIM = False


def pt(t, verdict, delta=0.0):
    return {"T_target_K": t, "verdict": verdict, "delta_pe_eV_per_atom": delta}


@unittest.skipUnless(HAVE_SIM, "needs the simulation environment")
class BracketVerdictTest(unittest.TestCase):
    def test_cu_attempt2_all_stable_is_undecided_not_above(self) -> None:  # real run, 2026-10-31
        v = bracket_verdict([pt(1350, "stable", -0.0039), pt(1375, "stable", 0.0026), pt(1400, "stable", 0.0072),
                             pt(1425, "stable", 0.0034)])
        self.assertEqual(v["failure"], "undecided")
        self.assertNotIn("lower_bound_K", v)

    def test_ga_froze_low_then_stable_is_undecided_with_a_lower_bound(self) -> None:  # real run, 2026-10-31
        v = bracket_verdict([pt(250, "freezing"), pt(290, "freezing"), pt(330, "stable"), pt(370, "stable")])
        self.assertEqual((v["failure"], v["lower_bound_K"]), ("undecided", 290))

    def test_cu_attempt1_hottest_froze_is_above(self) -> None:  # real run: 1350 K froze decisively
        v = bracket_verdict([pt(1275, "freezing"), pt(1300, "freezing"), pt(1325, "stable"), pt(1350, "freezing")])
        self.assertEqual((v["failure"], v["lower_bound_K"]), ("above_bracket", 1350))

    def test_coldest_melted_is_below(self) -> None:
        v = bracket_verdict([pt(400, "melting"), pt(450, "melting")])
        self.assertEqual(v["failure"], "below_bracket")

    def test_clean_bracket_gives_a_melting_point(self) -> None:
        v = bracket_verdict([pt(1000, "freezing", -0.02), pt(1050, "stable", 0.0), pt(1100, "melting", 0.02)])
        self.assertEqual(v["status"], "ok")
        self.assertTrue(1000 <= v["value"] <= 1100)
        self.assertEqual(v["bracket_K"], [1000, 1100])

    def test_undecided_retry_runs_longer_at_the_same_temperatures(self) -> None:
        job = SimpleNamespace(attempt=1, mode="coexistence", temps=[1350.0, 1375.0, 1400.0, 1425.0],
                              need={"property": "melting_point", "query": "Cu", "rationale": "x"},
                              steps=lambda: {"prod": 25000}, property="melting_point", name="Cu")
        need = retry_need(job, {"failure": "undecided", "reason": "undecided"})
        self.assertEqual(need["temps_K"], job.temps)
        self.assertEqual(need["prod_steps"], 50000)
        self.assertEqual(need["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
