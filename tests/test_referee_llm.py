"""Tests for the LLM method-audit referee and the disagreement adjudicator, with a fake LLM (no API calls).

Run: .venv/bin/python -m unittest tests.test_referee_llm -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from coolorbit.agents.referee_llm import (ADJUDICATOR_ROLE, AUDIT_ROLE, Adjudicator, LLMReferee, ask,
                                          disagreement, result_view)
from coolorbit.agents.referee_rules import RuleReferee
from coolorbit.board import Board, derive_status

TRUST = {"frozen": True, "independent_check_types": ["second_mlip", "classical_md"], "rejection_min_referees": 2,
         "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 20.0}, "tier1": {"abs": 25.0}}}}
GOOD = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence", "ensemble": "NPT",
        "timestep_fs": 2.0, "n_atoms": 512, "sim_length_ps": 50.0}
SOUND = {"verdict": "sound", "flags": [], "reasoning": "coexistence, NPT, adequate size", "suggested_fix": ""}
FLAWED = {"verdict": "flawed", "flags": ["single_phase_heating"], "reasoning": "heating ramp superheats",
          "suggested_fix": "two-phase coexistence"}


class FakeClient:
    """Mimics premise.roles_llm(role, **request) -> (response, cost_usd); replies per role; records calls."""

    def __init__(self, replies=None, stop_reason="end_turn", exc=None):
        self.calls, self.replies, self.stop_reason, self.exc = [], replies or {}, stop_reason, exc

    def __call__(self, role, **request):
        self.calls.append({"role": role, **request})
        if self.exc:
            raise self.exc
        reply = self.replies[role]
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return SimpleNamespace(stop_reason=self.stop_reason, model=f"model-for-{role}",
                               content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=800, output_tokens=100)), 0.02


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "dev-test")
        self.cand = self.board.post("scout_test", "candidate", {"name": "Cu", "family": "metal", "tier": "trust",
                                                                "formula": "Cu"})

    def tearDown(self):
        self._tmp.cleanup()

    def result(self, method=None, flags=(), value=1360.0):
        return self.board.post("simulator_x", "property_result",
                               {"candidate": "Cu", "family": "metal", "tier": "trust", "property": "melting_point",
                                "value": value, "units": "K", "method": dict(method or GOOD), "flags": list(flags)},
                               parents=[self.cand.artifact_id])

    def checks(self, res, by):
        return [k for k in self.board.snapshot().children.get(res.artifact_id, [])
                if k.artifact_type == "check" and k.producer_agent == by]


class AskTest(unittest.TestCase):
    def test_maps_verdicts_and_keeps_effort(self):
        c = FakeClient({AUDIT_ROLE: FLAWED})
        a = ask(c, AUDIT_ROLE, "sys", "user")
        self.assertEqual((a["verdict"], a["flags"]), ("fail", ["single_phase_heating"]))
        oc = c.calls[0]["output_config"]
        self.assertIn("effort", oc)  # the role's effort survives llm_roles' shallow merge
        self.assertEqual(oc["format"]["type"], "json_schema")
        self.assertNotIn("model", c.calls[0])  # never hard-codes a model
        self.assertEqual(ask(FakeClient({AUDIT_ROLE: SOUND}), AUDIT_ROLE, "s", "u")["verdict"], "pass")

    def test_problems_are_inconclusive(self):
        self.assertEqual(ask(FakeClient({AUDIT_ROLE: SOUND}, stop_reason="refusal"), AUDIT_ROLE, "s", "u")["verdict"],
                         "inconclusive")
        self.assertEqual(ask(FakeClient({AUDIT_ROLE: "not json"}), AUDIT_ROLE, "s", "u")["verdict"], "inconclusive")
        uncertain = {**SOUND, "verdict": "uncertain", "flags": ["x"]}
        a = ask(FakeClient({AUDIT_ROLE: uncertain}), AUDIT_ROLE, "s", "u")
        self.assertEqual((a["verdict"], a["flags"]), ("inconclusive", []))  # only a failure carries flags

        class NoUsableKey(Exception):
            pass
        a = ask(FakeClient(exc=NoUsableKey("none")), AUDIT_ROLE, "s", "u")
        self.assertEqual(a["verdict"], "inconclusive")
        self.assertIn("no usable Anthropic key", a["reasoning"])
        with self.assertRaises(ZeroDivisionError):  # a bug is not swallowed as "LLM unavailable"
            ask(FakeClient(exc=ZeroDivisionError()), AUDIT_ROLE, "s", "u")


class LLMRefereeTest(Case):
    def test_audits_each_result_once(self):
        res = self.result()
        c = FakeClient({AUDIT_ROLE: SOUND})
        ref = LLMReferee(self.board, client=c, verbose=False)
        self.assertTrue(ref.step())
        self.assertFalse(ref.step())
        [chk] = self.checks(res, "referee_llm")
        self.assertEqual((chk.payload["check_type"], chk.payload["verdict"]), ("method_audit", "pass"))
        self.assertIn("[llm audit]", chk.payload["note"])
        self.assertEqual(chk.needs, [])  # the LLM referee never requests reruns itself
        self.assertEqual(len(c.calls), 1)

    def test_independent_view(self):
        res = self.result()
        self.board.post("referee_rules", "check", {"check_type": "method_audit", "verdict": "fail", "note": "RULES"},
                        parents=[res.artifact_id])
        self.board.post("librarian", "reference_value", {"candidate": "Cu", "property": "melting_point",
                                                         "value": 1357.77, "units": "K", "source": "SYNTHETIC src"})
        c = FakeClient({AUDIT_ROLE: SOUND})
        LLMReferee(self.board, client=c, verbose=False).step()
        prompt = c.calls[0]["messages"][0]["content"]
        self.assertNotIn("RULES", prompt)       # does not see other referees' checks
        self.assertNotIn("1357.77", prompt)     # nor the measured value
        self.assertIn("two-phase coexistence", prompt)
        self.assertEqual(result_view(self.board.snapshot(), res)["candidate_record"]["formula"], "Cu")

    def test_skips_synthetic_superseded_own_and_no_client(self):
        self.result(flags=["synthetic_test_data"])
        old = self.result()
        self.board.post("simulator_x", "property_result",
                        {**old.payload, "value": 1350.0}, parents=[old.artifact_id])  # rerun supersedes old
        c = FakeClient({AUDIT_ROLE: SOUND})
        ref = LLMReferee(self.board, client=c, verbose=False)
        while ref.step():
            pass
        self.assertEqual(len(c.calls), 1)  # only the rerun
        self.assertFalse(LLMReferee(self.board, client=None, verbose=False).step())


class AdjudicatorTest(Case):
    def disagree(self, rules_verdict_method=None):
        res = self.result(method=rules_verdict_method)
        RuleReferee(self.board, verbose=False).step()
        return res

    def test_no_disagreement_no_call(self):
        res = self.result()
        RuleReferee(self.board, verbose=False).step()
        LLMReferee(self.board, client=FakeClient({AUDIT_ROLE: SOUND}), verbose=False).step()
        self.assertIsNone(disagreement(self.board.snapshot(), res))
        c = FakeClient({ADJUDICATOR_ROLE: SOUND})
        self.assertFalse(Adjudicator(self.board, client=c, verbose=False).step())
        self.assertEqual(c.calls, [])

    def test_upholds_llm_failure_and_requests_rerun(self):
        # The rules pass a method they can't see is wrong; the LLM fails it; the adjudicator upholds the failure.
        res = self.result(method={**GOOD, "protocol": "temperature ramp"})
        RuleReferee(self.board, verbose=False).step()
        self.assertEqual(self.checks(res, "referee_rules")[0].payload["verdict"], "pass")
        LLMReferee(self.board, client=FakeClient({AUDIT_ROLE: FLAWED}), verbose=False).step()
        conflict = disagreement(self.board.snapshot(), res)
        self.assertEqual({c.producer_agent for c in conflict}, {"referee_rules", "referee_llm"})
        c = FakeClient({ADJUDICATOR_ROLE: FLAWED})
        adj = Adjudicator(self.board, client=c, verbose=False)
        self.assertTrue(adj.step())
        self.assertFalse(adj.step())  # one ruling per result
        [ruling] = self.checks(res, "adjudicator")
        self.assertEqual(ruling.payload["verdict"], "fail")
        self.assertIn("settles: ", ruling.payload["note"])
        self.assertIn("referee_rules pass", ruling.payload["note"])
        self.assertEqual(len(ruling.needs), 1)
        self.assertEqual(ruling.needs[0]["property"], "melting_point")
        self.assertIn("referee_llm", c.calls[0]["messages"][0]["content"])
        self.assertEqual(derive_status(self.board.snapshot(), res, TRUST).status, "flagged")

    def test_no_second_rerun_when_rules_already_asked(self):
        res = self.result(method={**GOOD, "protocol": "single-phase heating"})
        RuleReferee(self.board, verbose=False).step()  # fails it and requests a rerun
        LLMReferee(self.board, client=FakeClient({AUDIT_ROLE: SOUND}), verbose=False).step()
        Adjudicator(self.board, client=FakeClient({ADJUDICATOR_ROLE: FLAWED}), verbose=False).step()
        [ruling] = self.checks(res, "adjudicator")
        self.assertEqual(ruling.needs, [])

    def test_ruling_pass_and_call_cap(self):
        for _ in range(3):
            res = self.result(method={**GOOD, "protocol": "temperature ramp"})
            RuleReferee(self.board, verbose=False).step()
            LLMReferee(self.board, client=FakeClient({AUDIT_ROLE: FLAWED}), verbose=False).step()
        c = FakeClient({ADJUDICATOR_ROLE: SOUND})
        adj = Adjudicator(self.board, client=c, max_calls=2, verbose=False)
        while adj.step():
            pass
        self.assertEqual(len(c.calls), 2)
        rulings = [k for k in self.board.snapshot().of_type("check") if k.producer_agent == "adjudicator"]
        self.assertTrue(all(r.payload["verdict"] == "pass" and r.needs == [] for r in rulings))
        del res


if __name__ == "__main__":
    unittest.main()
