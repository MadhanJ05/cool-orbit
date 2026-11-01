"""post_classical_checks gate falls back to the sourced calibration set when the board has no measured value yet.

Regression for 2026-11-01: an Al EAM potential that misses the measured Tm by +11.6% would have been posted as a
counted check because the Librarian had not posted Al's reference yet. Board data below is SYNTHETIC except the
measured values, which come from data/calibration/tier1.json (sourced).
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("pcc_under_test", REPO / "scripts" / "post_classical_checks.py")
pcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pcc)  # type: ignore[union-attr]

RULES = {"properties": {"melting_point": {"units": ["K"], "agree": {"rel": 0.10}, "tier1": {"rel": 0.10}}}}


class GateFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.board = Board(self.tmp / "board")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def mace_result(self, element: str, value: float) -> None:
        cand = self.board.post("scout_metals", "candidate", {"name": element, "family": "validation_metal",
                                                              "tier": "trust", "rationale": "SYNTHETIC"})
        self.board.post("simulator_x", "property_result", {
            "candidate": element, "family": "validation_metal", "tier": "trust", "property": "melting_point",
            "value": value, "units": "K", "note": "SYNTHETIC",
            "method": {"engine": "ALCHEMI", "model": "SYNTHETIC", "protocol": "two-phase coexistence"}},
            parents=[cand.artifact_id])

    def analysis(self, element: str, value: float) -> None:
        f = self.tmp / "classical" / "run" / "metals" / element / "analysis_coexistence.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"status": "ok", "value": value, "uncertainty": 10.0, "bracket_K": [value - 10, value + 10],
                                 "method": {"engine": "LAMMPS", "model": "EAM SYNTHETIC", "protocol": "two-phase coexistence"}}))

    def plan(self):
        return {i["result"].payload["candidate"]: i for i in pcc.plan(self.board, "run", RULES, gate=True,
                                                                     root=self.tmp / "classical")}

    def test_unreliable_potential_is_inconclusive_without_a_board_reference(self) -> None:
        self.mace_result("Al", 950.0)
        self.analysis("Al", 1041.4)  # +11.6% vs the measured 933.45 K (tier1.json)
        item = self.plan()["Al"]
        self.assertEqual(item["payload"]["verdict"], "inconclusive")
        self.assertNotIn("value", item["payload"])
        self.assertIn("933.45", item["payload"]["note"])
        self.assertIn("EAM SYNTHETIC", item["payload"]["note"], "the note names the potential, not 'MEAM'")

    def test_reliable_potential_is_counted_and_agrees(self) -> None:
        self.mace_result("Cu", 1358.0)
        self.analysis("Cu", 1327.0)  # -2.3% vs the measured 1357.77 K
        pl = self.plan()["Cu"]["payload"]
        self.assertEqual((pl["verdict"], pl["value"], pl["units"]), ("agree", 1327.0, "K"))


if __name__ == "__main__":
    unittest.main()
