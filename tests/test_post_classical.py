"""post_classical_checks: MEAM melting points become classical_md checks, gated on the method's own accuracy.

Synthetic fixtures; no LAMMPS, no network.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board, derive_status

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("post_classical_checks", REPO / "scripts" / "post_classical_checks.py")
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

RULES = {"frozen": True, "independent_check_types": ["second_mlip", "classical_md"], "rejection_min_referees": 2,
         "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 40.0}, "tier1": {"abs": 40.0}}}}
METHOD = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence", "ensemble": "NPT",
          "timestep_fs": 2.0, "n_atoms": 576, "sim_length_ps": 50.0}
MEAM = {"engine": "LAMMPS", "model": "2NN MEAM (SYNTHETIC)", "protocol": "two-phase coexistence", "timestep_fs": 2.0}


class PostClassicalTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.board = Board(self.tmp / "board")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def setup_metal(self, name: str, mace: float, meam: float, measured: float):
        cand = self.board.post("scout_metals", "candidate", {"name": name, "family": "metal", "tier": "trust"})
        res = self.board.post("simulator_gb10", "property_result",
                              {"candidate": name, "family": "metal", "tier": "trust", "property": "melting_point",
                               "value": mace, "units": "K", "method": METHOD},
                              parents=[cand.artifact_id],
                              needs=[{"artifact_type": "check", "check_type": "classical_md",
                                      "query": f"independent melting_point for {name}",
                                      "rationale": "trust rule (b): independent method"}])
        self.board.post("librarian", "reference_value", {"candidate": name, "property": "melting_point",
                                                         "value": measured, "units": "K", "source": "SYNTHETIC"})
        d = self.tmp / "classical" / "run" / "metals" / name
        d.mkdir(parents=True)
        (d / "analysis_coexistence.json").write_text(json.dumps(
            {"status": "ok", "value": meam, "uncertainty": 15.0, "bracket_K": [meam - 15, meam + 15], "method": MEAM}))
        return res

    def checks(self, res):
        return [k for k in self.board.snapshot().children.get(res.artifact_id, []) if k.producer_agent == pc.AGENT]

    def post_all(self, gate=True):
        for item in pc.plan(self.board, "run", RULES, gate, root=self.tmp / "classical"):
            if "payload" in item:
                self.board.post(pc.AGENT, "check", item["payload"], parents=[item["result"].artifact_id],
                                fulfills=item["fulfills"])

    def test_reliable_agreeing_method_counts_and_fulfils_the_need(self) -> None:
        res = self.setup_metal("SynthA", mace=560.0, meam=575.0, measured=545.0)
        self.post_all()
        [chk] = self.checks(res)
        self.assertEqual((chk.payload["verdict"], chk.payload["value"]), ("agree", 575.0))
        self.assertEqual(self.board.open_needs(), [])  # the classical_md need is fulfilled
        rep = derive_status(self.board.snapshot(), res, RULES)
        self.assertTrue(any("agrees" in r for r in rep.reasons))

    def test_unreliable_method_is_posted_without_a_value_and_cannot_flag(self) -> None:
        res = self.setup_metal("SynthB", mace=440.0, meam=730.0, measured=430.0)  # MEAM misses measured by 300 K
        self.post_all()
        [chk] = self.checks(res)
        self.assertEqual(chk.payload["verdict"], "inconclusive")
        self.assertIsNone(chk.payload.get("value"))
        self.assertIn("independent_method_unreliable", chk.payload["flags"])
        self.assertNotEqual(derive_status(self.board.snapshot(), res, RULES).status, "flagged")

    def test_without_the_gate_a_bad_potential_would_flag_a_good_result(self) -> None:
        res = self.setup_metal("SynthC", mace=440.0, meam=730.0, measured=430.0)
        self.post_all(gate=False)
        self.assertEqual(derive_status(self.board.snapshot(), res, RULES).status, "flagged")

    def test_posts_once(self) -> None:
        res = self.setup_metal("SynthD", mace=560.0, meam=575.0, measured=545.0)
        self.post_all()
        self.post_all()
        self.assertEqual(len(self.checks(res)), 1)


if __name__ == "__main__":
    unittest.main()
