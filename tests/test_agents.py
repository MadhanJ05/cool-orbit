"""Tests for the agents (Scouts, rule referee, Librarian agent, Ranker, FakeSimulator).

All numbers here are SYNTHETIC fixtures. Run: .venv/bin/python -m unittest tests.test_agents -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coolorbit.agents.base import run_round_robin
from coolorbit.agents.librarian import LibrarianAgent
from coolorbit.agents.ranker import Ranker, rank
from coolorbit.agents.referee_rules import RuleReferee, audit, load_audit_rules
from coolorbit.agents.scout import Scout
from coolorbit.board import Board, derive_status

try:
    import ase  # noqa: F401

    from coolorbit.agents.fake_simulator import FakeSimulatorAgent, SyntheticBoard
    HAVE_ASE = True
except ImportError:
    HAVE_ASE = False

TRUST = {"frozen": True, "independent_check_types": ["second_mlip", "classical_md"], "rejection_min_referees": 2,
         "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 20.0}, "tier1": {"abs": 25.0}},
                        "latent_heat": {"units": ["J/kg"], "agree": {"rel": 0.15}, "tier1": {"rel": 0.2}}}}
SRC = "SYNTHETIC test source"
CAL = [
    {"candidate": "testium", "family": "test_metal", "property": "melting_point", "value": 330.0, "units": "K",
     "source": SRC, "aliases": ["Tm"]},
    {"candidate": "testium", "family": "test_metal", "property": "latent_heat", "value": 2.0e5, "units": "J/kg",
     "source": SRC, "aliases": ["Tm"]},
    {"candidate": "waxium", "family": "paraffin", "property": "melting_point", "value": 310.0, "units": "K", "source": SRC},
]
SEEDS = {
    "scout_test": {"budget": 2, "candidates": [
        {"name": "testium", "family": "test_metal", "tier": "trust", "formula": "Cu", "structure_source": "ase:Cu:fcc:3.61",
         "route": "ALCHEMI", "rationale": "synthetic", "requests": ["melting_point", "latent_heat"]},
        {"name": "waxium", "family": "paraffin", "tier": "trust", "formula": "C20H42", "structure_source": "pubchem:0",
         "route": "classical_md", "rationale": "synthetic", "requests": ["melting_point"]},
        {"name": "overbudget", "family": "test_metal", "tier": "trust", "structure_source": None, "route": None,
         "rationale": "synthetic", "requests": []}]},
    "scout_off": {"enabled": False, "candidates": [{"name": "x", "family": "f", "tier": "trust", "requests": []}]},
}
GOOD = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence", "ensemble": "NPT",
        "timestep_fs": 2.0, "n_atoms": 512, "sim_length_ps": 50.0}


def result(prop="melting_point", value=330.0, units="K", method=None, flags=(), family="test_metal", cand="testium"):
    return {"candidate": cand, "family": family, "tier": "trust", "property": prop, "value": value, "units": units,
            "method": dict(method or GOOD), "flags": list(flags), "note": "SYNTHETIC test data"}


class AuditRules(unittest.TestCase):
    rules = load_audit_rules()

    def flags(self, res, cand=None):
        return audit(res, self.rules, cand)[0]

    def test_good_coexistence_run_passes(self):
        self.assertEqual(self.flags(result(), {"family": "test_metal", "formula": "Cu"}), [])

    def test_single_phase_heating(self):
        self.assertIn("single_phase_heating", self.flags(result(method={**GOOD, "protocol": "single-phase heating"})))

    def test_langevin_transport(self):
        res = result(prop="self_diffusion", units="cm2/s", value=1e-5, method={**GOOD, "ensemble": "NVT-Langevin"})
        self.assertIn("langevin_transport", self.flags(res))
        nve = result(prop="self_diffusion", units="cm2/s", value=1e-5, method={**GOOD, "ensemble": "NVE"})
        self.assertNotIn("langevin_transport", self.flags(nve))

    def test_out_of_domain_model_and_hydrogen_timestep(self):
        f = self.flags(result(family="paraffin"), {"family": "paraffin", "formula": "C20H42"})
        self.assertIn("model_out_of_domain", f)
        self.assertIn("timestep_too_large_for_hydrogen", f)
        ok = {**GOOD, "engine": "OpenMM", "model": "GAFF2", "timestep_fs": 0.5}
        self.assertEqual(self.flags(result(family="paraffin", method=ok), {"family": "paraffin", "formula": "C20H42"}), [])

    def test_size_length_and_missing_metadata(self):
        f = self.flags(result(method={**GOOD, "n_atoms": 64, "sim_length_ps": 2.0}))
        self.assertIn("system_too_small", f)
        self.assertIn("run_too_short", f)
        m = dict(GOOD)
        del m["ensemble"]
        self.assertIn("missing_method_metadata", self.flags(result(method=m)))

    def test_producer_flags_block(self):
        self.assertIn("superheating_observed", self.flags(result(flags=["superheating_observed"])))
        self.assertEqual(self.flags(result(flags=["synthetic_test_data"])), [])


class BoardCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "dev-test"
        self.board = Board(self.root)
        self.kw = {"verbose": False}

    def tearDown(self):
        self._tmp.cleanup()

    def cand(self, name="testium", family="test_metal", formula="Cu"):
        return self.board.post("scout_test", "candidate", {"name": name, "family": family, "tier": "trust",
                                                           "formula": formula})

    def trusted(self, cand, prop, value, units):
        """Post a result plus everything the trust rule needs (audit, independent check, reference)."""
        res = self.board.post("simulator_x", "property_result", {**result(prop, value, units), "candidate": cand.payload["name"]},
                              parents=[cand.artifact_id])
        self.board.post("referee_a", "check", {"check_type": "method_audit", "verdict": "pass"}, parents=[res.artifact_id])
        self.board.post("referee_b", "check", {"check_type": "classical_md", "verdict": "agree", "value": value,
                                               "units": units, "method": {"engine": "OpenMM", "model": "GAFF2",
                                                                          "protocol": "two-phase coexistence"}},
                        parents=[res.artifact_id])
        self.board.post("librarian", "reference_value", {"candidate": cand.payload["name"], "property": prop,
                                                         "value": value, "units": units, "source": SRC})
        self.assertEqual(derive_status(self.board.snapshot(), res, TRUST).status, "trusted")
        return res


class ScoutTest(BoardCase):
    def test_proposes_within_budget_and_routes(self):
        s = Scout(self.board, "scout_test", seeds=SEEDS, calibration=CAL, rules=TRUST, **self.kw)
        self.assertEqual(s.drain(), 2)  # budget 2: the third seed is never posted
        cands = {a.payload["name"]: a for a in self.board.snapshot().of_type("candidate")}
        self.assertEqual(set(cands), {"testium", "waxium"})
        metal = cands["testium"].needs[0]
        self.assertEqual((metal["property"], metal["t_guess_K"]), ("melting_point", 330))
        self.assertNotIn("engine", metal)
        self.assertEqual(cands["waxium"].needs[0]["engine"], "classical_md")
        self.assertFalse(Scout(self.board, "scout_off", seeds=SEEDS, calibration=CAL, **self.kw).step())

    def test_follow_up_latent_heat_only_for_usable_results(self):
        s = Scout(self.board, "scout_test", seeds=SEEDS, calibration=CAL, rules=TRUST, **self.kw)
        s.drain()
        cand = next(a for a in self.board.snapshot().of_type("candidate") if a.payload["name"] == "testium")
        bad = self.board.post("simulator_x", "property_result",
                              result(method={**GOOD, "protocol": "single-phase heating"}), parents=[cand.artifact_id])
        for who in ("referee_a", "referee_b"):  # flagged (and rejected) results must not trigger follow-up
            self.board.post(who, "check", {"check_type": "method_audit", "verdict": "reject"}, parents=[bad.artifact_id])
        self.assertFalse(s.step())
        good = self.board.post("simulator_x", "property_result", result(value=333.0), parents=[cand.artifact_id])
        self.assertTrue(s.step())
        req = self.board.snapshot().of_type("request")[-1]
        self.assertEqual(req.needs[0]["temperature_K"], 333.0)
        self.assertIn(good.artifact_id, req.parent_artifact_ids)
        self.assertFalse(s.step())  # asked once only


class LibrarianAgentTest(BoardCase):
    def test_answers_needs_by_alias_and_admits_gaps(self):
        cand = self.cand(name="Tm")
        res = self.board.post("simulator_x", "property_result", result(cand="Tm"), parents=[cand.artifact_id],
                              needs=[{"artifact_type": "reference_value", "query": "measured melting_point of Tm",
                                      "rationale": "trust rule (c) needs a measurement"}])
        other = self.board.post("simulator_x", "property_result", result(cand="unobtainium"),
                                needs=[{"artifact_type": "reference_value", "query": "measured melting_point",
                                        "rationale": "trust rule (c) needs a measurement"}])
        lib = LibrarianAgent(self.board, records=CAL, **self.kw)
        self.assertEqual(lib.drain(), 2)
        refs = {a.payload["candidate"]: a.payload for a in self.board.snapshot().of_type("reference_value")}
        self.assertEqual(refs["Tm"]["value"], 330.0)  # posted under the result's own name
        self.assertIsNone(refs["unobtainium"]["value"])
        self.assertIn("NOT FOUND", refs["unobtainium"]["note"])
        self.assertEqual(self.board.open_needs(), [])
        self.assertTrue(any("matches measured 330" in r for r in derive_status(self.board.snapshot(), res, TRUST).reasons))
        del other


class RefereeAgentTest(BoardCase):
    def test_audits_each_result_once_and_fulfils_need(self):
        cand = self.cand()
        need = {"artifact_type": "check", "check_type": "method_audit", "query": "audit this run",
                "rationale": "trust rule (a) needs an audit"}
        res = self.board.post("simulator_x", "property_result", result(method={**GOOD, "protocol": "single-phase heating"}),
                              parents=[cand.artifact_id], needs=[need])
        ref = RuleReferee(self.board, **self.kw)
        self.assertEqual(ref.drain(), 1)
        check = self.board.snapshot().of_type("check")[0]
        self.assertEqual((check.payload["verdict"], check.payload["flags"]), ("fail", ["single_phase_heating"]))
        self.assertEqual(self.board.open_needs(), [])
        self.assertEqual(derive_status(self.board.snapshot(), res, TRUST).status, "flagged")
        own = self.board.post("referee_rules", "property_result", result())
        self.assertFalse(ref.step())  # never audits its own results
        del own


class RankerTest(BoardCase):
    CFG = {"decided": False, "window_K": {"min": 290, "max": 370},
           "transition_pairs": [{"temperature": "melting_point", "latent_heat": "latent_heat", "kind": "solid-liquid"}]}

    def test_trusted_only_window_and_order(self):
        a, b, hot = self.cand("alpha"), self.cand("beta"), self.cand("hotium")
        self.trusted(a, "melting_point", 330.0, "K")
        self.trusted(a, "latent_heat", 1.5e5, "J/kg")
        self.trusted(b, "melting_point", 300.0, "K")
        self.trusted(b, "latent_heat", 2.5e5, "J/kg")
        self.trusted(hot, "melting_point", 900.0, "K")
        self.trusted(hot, "latent_heat", 4.0e5, "J/kg")
        untrusted = self.cand("gamma")
        self.board.post("simulator_x", "property_result", {**result(value=320.0), "candidate": "gamma"},
                        parents=[untrusted.artifact_id])
        entries, excluded = rank(self.board.snapshot(), TRUST, self.CFG)
        self.assertEqual([e["candidate"] for e in entries], ["beta", "alpha"])
        self.assertEqual(entries[0]["score"], 250.0)
        self.assertTrue(any("hotium" in x and "outside window" in x for x in excluded))
        r = Ranker(self.board, trust_rules=TRUST, config=self.CFG, **self.kw)
        self.assertTrue(r.step())
        self.assertFalse(r.step())  # unchanged shortlist: no new ranking
        self.assertIn("PLACEHOLDER", self.board.snapshot().of_type("ranking")[0].payload["criteria"])

    def test_no_trusted_results_no_ranking(self):
        self.cand()
        self.assertFalse(Ranker(self.board, trust_rules=TRUST, config=self.CFG, **self.kw).step())


@unittest.skipUnless(HAVE_ASE, "needs ASE")
class FakeSimulatorTest(BoardCase):
    def test_refuses_non_dev_boards(self):
        with self.assertRaises(ValueError):
            SyntheticBoard(Path(self._tmp.name) / "real-run")

    def test_end_to_end_trap_is_caught(self):
        agents = [Scout(self.board, "scout_test", seeds=SEEDS, calibration=CAL, rules=TRUST, **self.kw),
                  FakeSimulatorAgent(self.root, traps={"testium": "silent_heating"}),
                  RuleReferee(self.board, **self.kw), LibrarianAgent(self.board, records=CAL, **self.kw)]
        run_round_robin(agents)
        snap = self.board.snapshot()
        res = [a for a in snap.of_type("property_result") if a.payload["candidate"] == "testium"]
        self.assertEqual(len(res), 1)
        p = res[0].payload
        self.assertEqual(p["method"]["protocol"], "single-phase heating")
        self.assertIn("synthetic_test_data", p["flags"])
        self.assertTrue(p["note"].startswith("SYNTHETIC"))
        self.assertNotIn("single_phase_heating", p["flags"])  # silent: the producer raised nothing
        self.assertEqual(derive_status(snap, res[0], TRUST).status, "flagged")  # ...the referee caught it
        self.assertEqual(snap.of_type("request"), [])  # no latent-heat follow-up on a flagged result
        log = (self.root / "logs" / "referee_rules.jsonl").read_text().splitlines()
        self.assertTrue(any(json.loads(line).get("verdict") == "fail" for line in log))


if __name__ == "__main__":
    unittest.main()
