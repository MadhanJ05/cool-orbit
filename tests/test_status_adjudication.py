"""Trust rule: an adjudicator's decisive ruling settles the method audits posted before it. Synthetic fixtures."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board, derive_status

RULES = {"frozen": True, "independent_check_types": ["second_mlip", "classical_md"], "rejection_min_referees": 2,
         "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 20.0}, "tier1": {"abs": 25.0}}}}
METHOD = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence", "ensemble": "NPT",
          "timestep_fs": 2.0, "n_atoms": 512, "sim_length_ps": 50.0}


class AdjudicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "b")
        cand = self.board.post("scout_x", "candidate", {"name": "SYNTH", "family": "metal", "tier": "discovery"})
        self.res = self.board.post("simulator_x", "property_result",
                                   {"candidate": "SYNTH", "family": "metal", "tier": "discovery",
                                    "property": "melting_point", "value": 1000.0, "units": "K", "method": METHOD},
                                   parents=[cand.artifact_id])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def audit(self, who: str, verdict: str, check_type: str = "method_audit") -> None:
        self.board.post(who, "check", {"check_type": check_type, "verdict": verdict, "note": f"{who} says {verdict}"},
                        parents=[self.res.artifact_id])

    def status(self):
        return derive_status(self.board.snapshot(), self.res, RULES)

    def test_dispute_without_ruling_is_flagged(self) -> None:
        self.audit("referee_rules", "pass")
        self.audit("referee_llm", "fail")
        self.assertEqual(self.status().status, "flagged")

    def test_pass_ruling_clears_a_false_alarm(self) -> None:
        self.audit("referee_rules", "pass")
        self.audit("referee_llm", "fail")
        self.audit("adjudicator", "pass")
        rep = self.status()
        self.assertEqual(rep.status, "under_review")  # not trusted: still needs an independent method
        self.assertTrue(any("settled by adjudicator ruling" in r for r in rep.reasons))

    def test_fail_ruling_keeps_it_flagged(self) -> None:
        self.audit("referee_rules", "pass")
        self.audit("referee_llm", "fail")
        self.audit("adjudicator", "fail")
        self.assertEqual(self.status().status, "flagged")

    def test_inconclusive_ruling_settles_nothing(self) -> None:
        self.audit("referee_rules", "pass")
        self.audit("referee_llm", "fail")
        self.audit("adjudicator", "inconclusive")
        self.assertEqual(self.status().status, "flagged")

    def test_audits_after_the_ruling_and_other_checks_still_count(self) -> None:
        self.audit("referee_rules", "pass")
        self.audit("referee_llm", "fail")
        self.audit("adjudicator", "pass")
        self.audit("referee_other", "fail")  # a new objection after the ruling
        self.assertEqual(self.status().status, "flagged")

    def test_premise_failures_are_never_settled(self) -> None:
        self.audit("referee_premise", "fail", check_type="premise_check")
        self.audit("referee_rules", "pass")
        self.audit("referee_llm", "fail")
        self.audit("adjudicator", "pass")
        self.assertEqual(self.status().status, "flagged")

    def test_adjudicators_come_from_the_rules(self) -> None:
        self.audit("referee_rules", "pass")
        self.audit("referee_llm", "fail")
        self.audit("someone_else", "pass")  # not an adjudicator: settles nothing
        self.assertEqual(self.status().status, "flagged")
        rules = {**RULES, "adjudicators": ["someone_else"]}
        self.assertEqual(derive_status(self.board.snapshot(), self.res, rules).status, "under_review")


if __name__ == "__main__":
    unittest.main()
