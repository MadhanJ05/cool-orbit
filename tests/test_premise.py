"""Tests for the premise checker (T3). No network: the LLM is a fake client. All numbers are fixtures.

Run: .venv/bin/python -m unittest tests.test_premise -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from coolorbit.agents.premise import PremiseChecker, check_premise, premise_rules

try:  # only the API-error test needs the real SDK; everything else uses FakeClient
    import anthropic
    import httpx2
    HAVE_ANTHROPIC = True
except ImportError:
    HAVE_ANTHROPIC = False
from coolorbit.board import Board, derive_status

# NVIDIA's ill-posed probe (ALCHEMI agents blog): Li-ion diffusion in pristine crystalline LiF.
LIF = {"material": "LiF", "formula": "LiF", "property": "self_diffusion",
       "description": "Compute the Li-ion self-diffusion coefficient in pristine crystalline LiF at 300 K",
       "temperature_K": 300, "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "ensemble": "NVE",
                                        "sim_length_ps": 1000}}
# Well-posed controls (NVIDIA's benchmark tasks + our melting task).
CONTROLS = {
    "liquid Li diffusion (NVE)": {"material": "Li", "formula": "Li", "property": "self_diffusion", "state": "liquid",
                                  "temperature_K": 600, "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0",
                                                                   "ensemble": "NVE", "sim_length_ps": 200}},
    "Si equation of state": {"material": "Si", "formula": "Si", "property": "equation_of_state",
                             "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "FIRE2 relaxation"}},
    "O on Cu(111) adsorption": {"material": "O/Cu(111)", "formula": "CuO", "property": "adsorption_energy",
                                "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "slab relaxation"}},
    "Cu melting (coexistence)": {"material": "Cu", "formula": "Cu", "property": "melting_point",
                                 "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0",
                                            "protocol": "two-phase coexistence", "ensemble": "NPT"}},
    "Cu latent heat": {"material": "Cu", "formula": "Cu", "property": "latent_heat", "temperature_K": 1357,
                       "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "NPT enthalpy difference"}},
}


class FakeClient:
    """Mimics client.beta.messages.create; records calls."""

    def __init__(self, reply=None, stop_reason="end_turn", exc=None):
        self.calls = []
        self.reply, self.stop_reason, self.exc = reply, stop_reason, exc
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return SimpleNamespace(stop_reason=self.stop_reason, model=kw["model"],
                               content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=900, output_tokens=120))


WELL = {"verdict": "well_posed", "flags": [], "reasoning": "fine", "suggested_fix": ""}
ILL = {"verdict": "ill_posed", "flags": ["ill_posed_timescale"], "reasoning": "too rare", "suggested_fix": "kMC"}


class Rules(unittest.TestCase):
    def test_refuses_lif_without_llm(self):
        client = FakeClient(WELL)
        v = check_premise(LIF, client=client)
        self.assertEqual(v.verdict, "fail")
        self.assertIn("ill_posed_timescale", v.flags)
        self.assertEqual(client.calls, [])  # decided by rules: no tokens spent

    def test_accepts_controls(self):
        for name, task in CONTROLS.items():
            with self.subTest(name):
                v = premise_rules(task)
                self.assertEqual(v.verdict, "pass", v.reasons)
                self.assertEqual(v.flags, [])

    def test_arrhenius_estimate(self):
        task = {**LIF, "description": "Li diffusion in crystalline LiF", "activation_energy_eV": 0.7}
        self.assertEqual(premise_rules(task).verdict, "fail")  # ~1e-9 hops in 1 ns at 300 K
        hot = {**task, "activation_energy_eV": 0.2, "temperature_K": 1000}
        self.assertNotEqual(premise_rules(hot).verdict, "fail")  # ~1e4 hops: measurable
        defective = {**LIF, "description": "Li diffusion in LiF with 2% Li vacancies", "defects": "vacancies"}
        self.assertNotEqual(premise_rules(defective).verdict, "fail")

    def test_perfect_crystal_wording_and_thermal_conductivity(self):
        cu = {"material": "Cu", "formula": "Cu", "property": "self_diffusion", "temperature_K": 300,
              "description": "Compute the self-diffusion coefficient of copper atoms in a perfect fcc copper crystal "
                             "at 300 K using molecular dynamics."}
        self.assertIn("ill_posed_timescale", premise_rules(cu).flags)
        k = {"material": "Cu", "property": "conductivity", "state": "crystal",
             "description": "thermal conductivity of a perfect copper crystal", "method": {"ensemble": "NVE"}}
        self.assertNotEqual(premise_rules(k).verdict, "fail")  # phonon/electron transport: not timescale-limited
        k_lang = {**k, "method": {"ensemble": "NVT-Langevin"}}
        self.assertIn("langevin_transport", premise_rules(k_lang).flags)  # still needs NVE production

    def test_method_traps(self):
        heat = {**CONTROLS["Cu melting (coexistence)"]}
        heat["method"] = {**heat["method"], "protocol": "single-phase heating"}
        self.assertIn("single_phase_heating", premise_rules(heat).flags)
        lang = {**CONTROLS["liquid Li diffusion (NVE)"]}
        lang["method"] = {**lang["method"], "ensemble": "NVT-Langevin"}
        self.assertIn("langevin_transport", premise_rules(lang).flags)
        wax = {"material": "n-eicosane", "family": "paraffin", "formula": "C20H42", "property": "melting_point",
               "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence"}}
        self.assertIn("model_out_of_domain", premise_rules(wax).flags)

    def test_unknown_cases_are_inconclusive_without_llm(self):
        task = {"material": "erythritol", "property": "thermal_conductivity", "method": {"engine": "ALCHEMI"}}
        v = check_premise(task, client=None)
        self.assertEqual(v.verdict, "inconclusive")
        self.assertTrue(any("no LLM credentials" in r for r in v.reasons))


class Llm(unittest.TestCase):
    UNKNOWN = {"material": "erythritol", "property": "thermal_conductivity", "method": {"engine": "ALCHEMI"}}

    def test_llm_consulted_only_when_rules_undecided(self):
        client = FakeClient(ILL)
        v = check_premise(self.UNKNOWN, client=client)
        self.assertEqual((v.verdict, v.source), ("fail", "rules+llm"))
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(call["fallbacks"], "default")
        self.assertEqual(v.usage["input_tokens"], 900)
        check_premise(CONTROLS["Si equation of state"], client=client)
        self.assertEqual(len(client.calls), 1)  # rules decided: no call

    def test_llm_cannot_overturn_rule_fail(self):
        v = check_premise(LIF, client=FakeClient(WELL), llm_mode="always")
        self.assertEqual(v.verdict, "fail")

    def test_bad_answers_become_inconclusive(self):
        for c in (FakeClient(WELL, stop_reason="refusal"), FakeClient(WELL, stop_reason="max_tokens"),
                  FakeClient("not json")):
            with self.subTest(c=c.stop_reason):
                self.assertEqual(check_premise(self.UNKNOWN, client=c).verdict, "inconclusive")

    def test_non_api_errors_are_not_swallowed(self):
        with self.assertRaises(ZeroDivisionError):
            check_premise(self.UNKNOWN, client=FakeClient(exc=ZeroDivisionError()))

    @unittest.skipUnless(HAVE_ANTHROPIC, "needs the anthropic SDK")
    def test_api_errors_become_inconclusive(self):
        req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        down = FakeClient(exc=anthropic.APIConnectionError(request=req))
        v = check_premise(self.UNKNOWN, client=down)
        self.assertEqual(v.verdict, "inconclusive")
        self.assertIn("connection error", v.reasons[-1])
        # a rule pass survives an LLM outage in "always" mode
        self.assertEqual(check_premise(CONTROLS["Si equation of state"], client=down, llm_mode="always").verdict, "pass")


class BoardAgent(unittest.TestCase):
    TRUST = {"frozen": True, "independent_check_types": ["second_mlip", "classical_md"], "rejection_min_referees": 2,
             "properties": {"self_diffusion": {"units": ["cm2/s"], "agree": {"rel": 0.2}, "tier1": {"rel": 0.3}}}}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "dev-premise")

    def tearDown(self):
        self._tmp.cleanup()

    def test_checks_eval_task_requests_from_their_text(self):
        lif = self.board.post("eval_harness", "request", {"note": "eval_task:lif"},
                              needs=[{"artifact_type": "candidate", "eval_task": "lif", "property": "self_diffusion",
                                      "query": "Compute the Li-ion diffusion coefficient in pristine crystalline LiF "
                                               "using molecular dynamics.", "rationale": "evaluation task"}])
        li = self.board.post("eval_harness", "request", {"note": "eval_task:li"},
                             needs=[{"artifact_type": "candidate", "eval_task": "li", "property": "self_diffusion",
                                     "query": "Compute the self-diffusion coefficient of liquid lithium at 800 K.",
                                     "rationale": "evaluation task"}])
        not_eval = self.board.post("scout_x", "request", {"note": "not a task"},
                                   needs=[{"artifact_type": "candidate", "property": "self_diffusion",
                                           "query": "pristine crystalline LiF diffusion", "rationale": "not an eval task"}])
        pc = PremiseChecker(self.board, client=None, calibration=[], verbose=False)
        self.assertEqual(pc.drain(), 2)
        verdicts = {c.parent_artifact_ids[0]: c.payload["verdict"] for c in self.board.snapshot().of_type("check")}
        self.assertEqual(verdicts, {lif.artifact_id: "fail", li.artifact_id: "pass"})
        del not_eval

    def test_checks_requests_then_flags_results_of_failed_premises(self):
        lif = self.board.post("scout_x", "candidate", {"name": "LiF", "family": "test", "tier": "trust", "formula": "LiF",
                                                       "structure_source": "ase:LiF:rocksalt:4.03"},
                              needs=[{"artifact_type": "property_result", "property": "self_diffusion",
                                      "query": "Li-ion self-diffusion in pristine crystalline LiF at 300 K",
                                      "rationale": "blind task from another team", "temperature_K": 300}])
        cu = self.board.post("scout_x", "candidate", {"name": "Cu", "family": "validation_metal", "tier": "trust",
                                                      "formula": "Cu", "structure_source": "ase:Cu:fcc:3.61"},
                             needs=[{"artifact_type": "property_result", "property": "melting_point",
                                     "query": "melting_point of Cu", "rationale": "validate the method",
                                     "t_guess_K": 1358}])
        pc = PremiseChecker(self.board, client=None, calibration=[], verbose=False)
        self.assertEqual(pc.drain(), 2)
        self.assertFalse(pc.step())  # each request checked once
        snap = self.board.snapshot()
        verdicts = {snap.artifacts[c.parent_artifact_ids[0]].payload["name"]: c.payload
                    for c in snap.of_type("check")}
        self.assertEqual(verdicts["LiF"]["verdict"], "fail")
        self.assertTrue(verdicts["LiF"]["note"].startswith("[need 0]"))
        self.assertEqual(verdicts["Cu"]["verdict"], "pass")
        self.assertEqual(len(self.board.open_needs()), 2)  # advisory: the premise checker never claims

        # Someone runs the ill-posed request anyway: the result inherits the fail and is flagged.
        res = self.board.post("simulator_x", "property_result", {
            "candidate": "LiF", "family": "test", "tier": "trust", "property": "self_diffusion", "value": 1e-9,
            "units": "cm2/s", "note": "SYNTHETIC test data",
            "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "MSD", "ensemble": "NVE"}},
            parents=[lif.artifact_id], fulfills=(lif.artifact_id, 0))
        self.assertTrue(pc.step())
        carried = [c for c in self.board.snapshot().of_type("check") if res.artifact_id in c.parent_artifact_ids]
        self.assertEqual(carried[0].payload["verdict"], "fail")
        self.assertEqual(derive_status(self.board.snapshot(), res, self.TRUST).status, "flagged")
        del cu


if __name__ == "__main__":
    unittest.main()
