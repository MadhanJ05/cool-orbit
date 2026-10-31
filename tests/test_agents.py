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
from coolorbit.agents.referee_rules import RuleReferee, audit, load_audit_rules, rerun_need
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

    # Static properties (no MD): small cells are correct, MD fields are absent.
    STATIC = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "E(V) fit after FIRE2 relaxation",
              "n_atoms": 8, "fmax_eV_per_A": 0.01, "n_eos_points": 9}

    def test_static_properties_match_premise(self):
        from coolorbit.agents.premise import STATIC_PROPERTIES
        self.assertEqual(set(self.rules["static_properties"]), STATIC_PROPERTIES)

    def test_valid_static_results_pass(self):
        si = {"family": "eval", "formula": "Si"}
        for prop, value, units in (("lattice_constant", 5.43, "A"), ("bulk_modulus", 98.0, "GPa"),
                                   ("equation_of_state", 98.0, "GPa")):
            self.assertEqual(self.flags(result(prop=prop, value=value, units=units, method=self.STATIC,
                                               family="eval", cand="Si"), si), [], prop)
        slab = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "FIRE2 relaxation, slab + adsorbate",
                "n_atoms": 37, "slab_layers": 4, "vacuum_A": 15.0, "fmax_eV_per_A": 0.03}
        self.assertEqual(self.flags(result(prop="adsorption_energy", value=-4.5, units="eV", method=slab,
                                           family="eval", cand="O/Cu(111)"), {"formula": "CuO"}), [])
        # The bare minimum (engine, model, protocol) also passes: the optional fields are checked only when present.
        bare = {k: self.STATIC[k] for k in ("engine", "model", "protocol")}
        self.assertEqual(self.flags(result(prop="lattice_constant", value=5.43, units="A", method=bare)), [])

    def test_static_checks(self):
        def f(prop, **m):
            return self.flags(result(prop=prop, value=1.0, units="eV", method={**self.STATIC, **m}))
        self.assertEqual(f("lattice_constant", model=None), ["missing_method_metadata"])
        self.assertEqual(f("lattice_constant", fmax_eV_per_A=0.3), ["relaxation_not_converged"])
        self.assertEqual(f("bulk_modulus", n_eos_points=3), ["too_few_eos_points"])
        self.assertEqual(f("adsorption_energy", slab_layers=2), ["slab_too_thin"])
        self.assertEqual(f("adsorption_energy", vacuum_A=5.0), ["vacuum_too_small"])
        self.assertEqual(f("adsorption_energy", engine="VASP DFT"), ["missing_kpoints"])
        self.assertEqual(f("adsorption_energy", engine="VASP DFT", kpoints=[4, 4, 1]), [])
        # MD rules still apply to MD properties
        self.assertIn("system_too_small", self.flags(result(method={**GOOD, "n_atoms": 8})))

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
        self.assertFalse(s.step())  # not yet audited: wait instead of racing the referee
        self.board.post("referee_a", "check", {"check_type": "method_audit", "verdict": "pass"}, parents=[good.artifact_id])
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
        open_needs = self.board.open_needs()  # the audit need is fulfilled; the only open need is the rerun
        self.assertEqual(len(open_needs), 1)
        rerun = open_needs[0]
        self.assertEqual(rerun.artifact.artifact_id, check.artifact_id)  # on the check, per the Reruns contract
        self.assertEqual((rerun.need["property"], rerun.need["t_guess_K"]), ("melting_point", round(330.0 / 1.2)))
        self.assertNotIn("preset", rerun.need)  # the result was not a quick run, so the preset is not the fix
        self.assertIn("two-phase coexistence", rerun.need["query"])
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

    def test_synthetic_results_are_not_ranked(self):
        a = self.cand("alpha")
        for prop, val, units in (("melting_point", 330.0, "K"), ("latent_heat", 1.5e5, "J/kg")):
            res = self.trusted(a, prop, val, units)
        snap = self.board.snapshot()
        # mark both of alpha's results synthetic by re-posting them that way
        b = self.cand("synthium")
        for prop, val, units in (("melting_point", 320.0, "K"), ("latent_heat", 3.0e5, "J/kg")):
            r = self.board.post("simulator_fake", "property_result",
                                {**result(prop, val, units), "candidate": "synthium", "flags": ["synthetic_test_data"]},
                                parents=[b.artifact_id])
            self.board.post("referee_a", "check", {"check_type": "method_audit", "verdict": "pass"}, parents=[r.artifact_id])
            self.board.post("referee_b", "check", {"check_type": "classical_md", "verdict": "agree", "value": val,
                                                   "units": units, "method": {"engine": "OpenMM", "model": "GAFF2",
                                                                              "protocol": "two-phase coexistence"}},
                            parents=[r.artifact_id])
            self.board.post("librarian", "reference_value", {"candidate": "synthium", "property": prop, "value": val,
                                                             "units": units, "source": SRC})
        snap = self.board.snapshot()
        self.assertEqual([e["candidate"] for e in rank(snap, TRUST, self.CFG)[0]], ["alpha"])
        with_synth = rank(snap, TRUST, self.CFG, include_synthetic=True)[0]
        self.assertEqual(with_synth[0]["candidate"], "synthium")
        self.assertIn("SYNTHETIC", with_synth[0]["note"])
        del res

    def test_no_trusted_results_no_ranking(self):
        self.cand()
        self.assertFalse(Ranker(self.board, trust_rules=TRUST, config=self.CFG, **self.kw).step())


class RerunNeedTest(unittest.TestCase):
    rules = load_audit_rules()

    def need(self, res, flags, origin=None):
        return rerun_need(res, flags, self.rules, origin)

    def test_superheating_only_divided_for_single_phase(self):
        heat = result(value=1500.0, method={**GOOD, "protocol": "single-phase heating"})
        n = self.need(heat, ["single_phase_heating"])
        self.assertEqual(n["t_guess_K"], 1250)
        self.assertNotIn("preset", n)
        # unexpected_superheating_signal comes from a two-phase run: keep its bracket, don't divide by 1.2.
        two = result(value=1500.0, method={**GOOD, "temperatures_K": [1400, 1450, 1500, 1550]})
        self.assertIsNone(self.need(two, ["unexpected_superheating_signal"]))  # nothing a request can change
        n = self.need(two, ["unexpected_superheating_signal", "quick_settings_not_production"])
        self.assertEqual((n["temps_K"], n["preset"]), ([1400, 1450, 1500, 1550], "production"))
        self.assertNotIn("t_guess_K", n)

    def test_each_fix_changes_a_request_field(self):
        quick = result(method={**GOOD, "temperatures_K": [300, 320, 340, 360]})
        n = self.need(quick, ["quick_settings_not_production"])
        self.assertEqual((n["temps_K"], n["preset"]), ([300, 320, 340, 360], "production"))
        small = result(method={**GOOD, "n_atoms": 256, "temperatures_K": [300, 340]})
        n = self.need(small, ["system_too_small"], origin={"reps": [4, 4, 4]})
        self.assertNotIn("preset", n)
        self.assertGreaterEqual(256 / 64 * (n["reps"][0] * n["reps"][1] * n["reps"][2]), 500)
        self.assertEqual(self.need(small, ["system_too_small"])["reps"], [6, 6, 8])  # default [4,4,8], 2 atoms/cell -> >=250 cells
        short = result(method={**GOOD, "sim_length_ps": 5.0, "timestep_fs": 2.0, "temperatures_K": [300, 340]})
        self.assertEqual(self.need(short, ["run_too_short"])["prod_steps"], 10000)  # 20 ps at 2 fs
        h = result(family="paraffin", method={**GOOD, "engine": "OpenMM", "model": "GAFF2", "timestep_fs": 1.0,
                                              "temperatures_K": [300, 320]})
        n = self.need(h, ["timestep_too_large_for_hydrogen"])
        self.assertEqual((n["dt_fs"], n["engine"]), (0.5, "classical_md"))  # stays on the classical route
        n = self.need(result(method={**GOOD, "temperatures_K": [300, 340]}), ["timestep_too_large_for_hydrogen"])
        self.assertNotIn("engine", n)  # ALCHEMI result: smaller timestep, no engine switch
        self.assertEqual(self.need(result(family="paraffin"), ["model_out_of_domain"])["engine"], "classical_md")
        lat = result("latent_heat", 2e5, "J/kg", method={**GOOD, "temperatures_K": [1357.0], "sim_length_ps": 5.0})
        self.assertEqual(self.need(lat, ["run_too_short"])["temperature_K"], 1357.0)

    def test_no_rerun_when_nothing_can_change(self):
        prod = result(method={**GOOD, "temperatures_K": [300, 340]})
        for flag in ("liquid_half_incompletely_melted", "crystal_half_disturbed"):
            self.assertIsNone(self.need(prod, [flag]))  # production prep problem: a request can't set melt steps
        n = self.need(prod, ["liquid_half_incompletely_melted", "quick_settings_not_production"])
        self.assertEqual(n["preset"], "production")  # on a quick run, production settings do fix the prep
        self.assertIsNone(self.need(result(), ["missing_method_metadata"]))
        self.assertIsNone(self.need(result("self_diffusion", 1e-5, "cm2/s"), ["langevin_transport"]))  # no runner


class RerunCapTest(BoardCase):
    def test_no_rerun_after_max_reruns(self):
        cand = self.cand()
        heat = {**GOOD, "protocol": "single-phase heating"}
        first = self.board.post("simulator_x", "property_result", result(method=heat), parents=[cand.artifact_id])
        second = self.board.post("simulator_x", "property_result", result(method=heat),
                                 parents=[cand.artifact_id, first.artifact_id])
        third = self.board.post("simulator_x", "property_result", result(method=heat),
                                parents=[cand.artifact_id, second.artifact_id])
        RuleReferee(self.board, max_reruns=2, **self.kw).drain()
        checks = {c.parent_artifact_ids[0]: c for c in self.board.snapshot().of_type("check")}
        self.assertEqual(len(checks[first.artifact_id].needs), 1)
        self.assertEqual(len(checks[second.artifact_id].needs), 1)
        self.assertEqual(checks[third.artifact_id].needs, [])
        self.assertIn("no rerun requested", checks[third.artifact_id].payload["note"])


class EvalTaskTest(BoardCase):
    SEEDS = {**SEEDS, "scout_test": {**SEEDS["scout_test"], "candidates": [
                 {**SEEDS["scout_test"]["candidates"][0], "aliases": ["testium metal"]},
                 *SEEDS["scout_test"]["candidates"][1:]]},
             "scout_general": {"general": True, "budget": 0, "candidates": []}}

    def task(self, tid, prop, prompt):
        # exactly the need fields coolorbit.eval.tasks.seed_board posts: no material, kind or reference
        return self.board.post("eval_harness", "request", {"note": f"eval_task:{tid}"},
                               needs=[{"artifact_type": "candidate", "query": prompt, "rationale": "evaluation task",
                                       "eval_task": tid, "property": prop, "units": "K"}])

    def test_family_and_general_scouts_answer_tasks(self):
        melt = self.task("t_melt", "melting_point", "Find the melting point of testium metal.")
        lat = self.task("t_lat", "latent_heat", "Compute the latent heat of testium.")
        diff = self.task("t_lif", "self_diffusion",
                         "Compute the Li-ion diffusion coefficient in pristine crystalline LiF at 300 K.")
        fam = Scout(self.board, "scout_test", seeds=self.SEEDS, calibration=CAL, rules=TRUST, eval_only=True, **self.kw)
        gen = Scout(self.board, "scout_general", seeds=self.SEEDS, calibration=CAL, rules=TRUST, **self.kw)
        self.assertEqual(gen.drain(), 1)  # takes only the task no family Scout owns
        self.assertEqual(fam.drain(), 2)  # eval_only: answers its two tasks, proposes nothing
        snap = self.board.snapshot()
        cands = {c.parent_artifact_ids[0]: c for c in snap.of_type("candidate")}
        self.assertEqual(set(cands), {melt.artifact_id, lat.artifact_id, diff.artifact_id})
        self.assertEqual(self.board.open_needs(include_claimed=True) and
                         [n for n in self.board.open_needs() if n.need.get("artifact_type") == "candidate"], [])
        self.assertEqual(cands[melt.artifact_id].needs[0]["property"], "melting_point")
        self.assertEqual(cands[lat.artifact_id].needs[0]["property"], "melting_point")  # latent waits for Tm
        self.assertEqual(cands[melt.artifact_id].needs[0]["query"], "Find the melting point of testium metal.")
        lif = cands[diff.artifact_id]
        self.assertEqual((lif.payload["name"], lif.payload["family"]), ("LiF", "eval_general"))
        self.assertEqual(lif.needs, [])  # no route/structure: nothing for the Simulator to refuse
        # follow-up: latent heat only for the task that asked for it, and only at an audited melting point
        for c in (cands[melt.artifact_id], cands[lat.artifact_id]):
            r = self.board.post("simulator_x", "property_result", result(value=331.0), parents=[c.artifact_id],
                                fulfills=(c.artifact_id, 0))
            self.board.post("referee_a", "check", {"check_type": "method_audit", "verdict": "pass"},
                            parents=[r.artifact_id])
        self.assertEqual(fam.drain(), 1)
        req = self.board.snapshot().of_type("request")
        asked = [r for r in req if r.producer_agent == "scout_test"]
        self.assertEqual(len(asked), 1)
        self.assertIn(cands[lat.artifact_id].artifact_id, asked[0].parent_artifact_ids)
        self.assertEqual(asked[0].needs[0]["temperature_K"], 331.0)

    def test_discovery_budget_ignores_eval_answers(self):
        self.task("t1", "melting_point", "melting point of testium")
        s = Scout(self.board, "scout_test", seeds=self.SEEDS, calibration=CAL, rules=TRUST, **self.kw)
        s.drain()
        names = [c.payload["name"] for c in self.board.snapshot().of_type("candidate")]
        self.assertEqual(sorted(names), ["testium", "testium", "waxium"])  # 1 task answer + budget of 2


class FairnessTest(unittest.TestCase):
    def test_scouts_never_read_the_eval_task_set(self):
        import coolorbit.agents.scout as scout_mod
        src = Path(scout_mod.__file__).read_text()
        self.assertNotIn("import load_tasks", src)
        self.assertNotIn("from coolorbit.eval", src)

    def test_symbols_are_case_sensitive_words(self):
        from coolorbit.agents.scout import material_from_text, mentions
        self.assertIsNone(mentions("Find the melting point of gallium in air", "In"))
        self.assertIsNotNone(mentions("melting point of the Ga-In eutectic", "In"))
        self.assertIsNotNone(mentions("Find the melting point of Aluminum", "aluminum"))
        self.assertEqual(material_from_text("Compute the self-diffusion coefficient of liquid lithium at 800 K."), "Li")
        self.assertEqual(material_from_text("Li-ion diffusion in pristine crystalline LiF"), "LiF")


class LibrarianPropertyTest(BoardCase):
    def test_unknown_property_is_not_claimed(self):
        self.board.post("simulator_x", "property_result", result("self_diffusion", 1e-5, "cm2/s"),
                        needs=[{"artifact_type": "reference_value", "query": "measured self_diffusion",
                                "rationale": "trust rule (c) needs a measurement"}])
        self.assertFalse(LibrarianAgent(self.board, records=CAL, **self.kw).step())
        self.assertEqual(self.board.snapshot().of_type("need_claim"), [])


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
        res = {a.payload["method"]["protocol"]: a for a in snap.of_type("property_result")
               if a.payload["candidate"] == "testium" and a.payload["property"] == "melting_point"}
        bad, good = res["single-phase heating"], res["two-phase coexistence"]
        self.assertIn("synthetic_test_data", bad.payload["flags"])
        self.assertTrue(bad.payload["note"].startswith("SYNTHETIC"))
        self.assertNotIn("single_phase_heating", bad.payload["flags"])  # silent: the producer raised nothing
        old = derive_status(snap, bad, TRUST)
        self.assertEqual(old.status, "flagged")  # ...the referee caught it
        self.assertEqual(old.superseded_by, [good.artifact_id])  # ...and its rerun request replaced it
        self.assertIn(bad.artifact_id, good.parent_artifact_ids)
        self.assertNotEqual(derive_status(snap, good, TRUST).status, "flagged")
        # the latent heat is requested at the rerun's melting point, never at the superheated one
        req = snap.of_type("request")
        self.assertEqual(len(req), 1)
        self.assertEqual(req[0].needs[0]["temperature_K"], round(good.payload["value"], 1))
        log = (self.root / "logs" / "referee_rules.jsonl").read_text().splitlines()
        self.assertTrue(any(json.loads(line).get("verdict") == "fail" and json.loads(line).get("rerun")
                            for line in log))


if __name__ == "__main__":
    unittest.main()
