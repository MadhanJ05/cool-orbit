"""Simulator agent with coexistence protocol 2 (opt-in): how a job opts in, its CLI, retry_need v2, the posted
records, and the unchanged protocol-1 default. Fake runner: no GPU, no MD. All numbers are synthetic, except the
In a1 -> a2 retry chain, which replays the science-1 board (docs/results/coexistence-diagnosis.md, H5.4).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from coolorbit.board import Board

try:
    import ase  # noqa: F401

    from coolorbit.sim.simulator_agent import (COEX_PROTOCOL_ENV, MAX_ATTEMPTS, Job, SimulatorAgent, build_job,
                                               retry_decision_v2, retry_need)
    HAVE_ASE = True
except ImportError:
    HAVE_ASE = False

REQUEST = {"artifact_type": "property_result", "property": "melting_point", "query": "melting_point of Cu",
           "rationale": "validate the coexistence method", "temps_K": [1250, 1300, 1350, 1400], "preset": "quick"}
V2_REQUEST = {**{k: v for k, v in REQUEST.items() if k != "temps_K"}, "t_guess_K": 1358, "protocol_version": 2}
NO_ENV = {COEX_PROTOCOL_ENV: ""}


class FakeRunner:
    """Scripted results; settings mimic melting.py (protocol_version and git_commit only for protocol 2)."""

    def __init__(self, *outcomes) -> None:
        self.outcomes, self.jobs = list(outcomes), []

    def __call__(self, job, out_dir: Path, timeout_s: float):
        self.jobs.append(job)
        st = {"mode": job.mode, "model": "medium-mpa-0", "dt_fs": job.dt_fs, "temps": job.temps,
              "prod_steps": job.steps()["prod"]}
        if "--protocol" in job.cli_args(out_dir):
            st.update(protocol_version=2, git_commit="abc123")
        return self.outcomes.pop(0), {"settings": st, "n_atoms": job.n_atoms}, out_dir / "run"


OK = {"status": "ok", "property": "melting_point", "value": 1387.5, "uncertainty": 12.5, "units": "K",
      "bracket_K": [1375.0, 1400.0], "flags": [], "protocol_version": 2}


def job(temps, attempt=1, need=None, t_ref=429.75, t_melt=None, name="In") -> "Job":
    return Job(target=None, need_index=0, need=dict(need or {"property": "melting_point"}),
               candidate=SimpleNamespace(payload={"name": name}), property="melting_point", mode="coexistence",
               temps=[float(t) for t in temps], reps=[4, 4, 8], dt_fs=2.0, preset="production", prod_steps=None,
               attempt=attempt, structure_args=[], n_atoms=576, protocol=2, t_ref=t_ref, t_melt=t_melt)


def pts(temps, verdicts):
    return [{"T_target_K": float(t), "verdict": v} for t, v in zip(temps, verdicts)]


@unittest.skipUnless(HAVE_ASE, "needs ASE (GB10 environment)")
class OptInTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "board")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def built(self, need, source="ase:Cu:fcc:3.61"):
        self.board.post("scout_metals", "candidate", {"name": "Cu", "family": "metal", "tier": "trust",
                                                      "structure_source": source}, needs=[need])
        on = [o for o in self.board.open_needs() if o.need.get("query") == need["query"]][-1]
        return build_job(self.board.snapshot(), on)

    def test_default_is_protocol_1_with_unchanged_cli(self) -> None:
        with mock.patch.dict(os.environ, NO_ENV):
            j = self.built(REQUEST)
        self.assertEqual((j.protocol, j.t_ref, j.t_melt), (1, None, None))
        self.assertEqual(j.cli_args(Path("x")), [
            "coexistence", "--element", "Cu", "--structure", "fcc", "--a", "3.61", "--reps", "4", "4", "8",
            "--dt", "2", "--out", "x", "--label", "Cu", "--temps", "1250", "1300", "1350", "1400", "--quick"])
        self.assertAlmostEqual(j.estimate_minutes(), 512 * (500 + 1500 + 500 + 4 * 2500) / 20_000 / 60)

    def test_need_with_protocol_version_2(self) -> None:
        with mock.patch.dict(os.environ, NO_ENV):
            j = self.built({**V2_REQUEST, "t_melt_K": 2716})
        args = j.cli_args(Path("x"))
        self.assertEqual(j.temps, [round(1358 * f) for f in (0.85, 0.95, 1.05, 1.15)])
        self.assertEqual(args[args.index("--protocol"):],
                         ["--protocol", "2", "--seed", "2027", "--t-ref", "1358", "--t-melt", "2716"])

    def test_env_switches_coexistence_only(self) -> None:
        latent = {"artifact_type": "property_result", "property": "latent_heat", "query": "latent_heat of Cu",
                  "rationale": "figure-of-merit input", "temperature_K": 1358, "preset": "quick"}
        with mock.patch.dict(os.environ, {COEX_PROTOCOL_ENV: "2"}):
            coex = self.built(REQUEST)
            lat = self.built(latent)
        self.assertEqual(coex.protocol, 2)
        self.assertIn("--protocol", coex.cli_args(Path("x")))
        self.assertNotIn("--t-ref", coex.cli_args(Path("x")))  # this need has temps_K only: no material T
        self.assertEqual(lat.protocol, 1)
        self.assertNotIn("--protocol", lat.cli_args(Path("x")))


@unittest.skipUnless(HAVE_ASE, "needs ASE (GB10 environment)")
class RetryV2Test(unittest.TestCase):
    def test_artefacts_are_never_retried(self) -> None:
        j = job([366, 408, 452, 494])
        for failure in ("crystal_lost_in_prep", "liquid_not_prepared", "no_two_phase_box", "inconsistent"):
            need, why = retry_decision_v2(j, {"failure": failure, "flags": [failure]})
            self.assertIsNone(need, failure)
            self.assertIn("artefact", why)
        need, _ = retry_decision_v2(j, {"failure": "above_bracket", "flags": ["liquid_half_incompletely_melted"],
                                        "per_temperature": pts([366, 408, 452, 494], ["freezing"] * 4)})
        self.assertIsNone(need)

    def test_in_a1_a2_chain_stops_on_contradiction(self) -> None:
        """science-1: In a1 melted at 366 K and above; a2 (238-366 K) froze at 366 K. a3 would have been a1 again."""
        a1 = job([366, 408, 452, 494], t_melt=None)
        below = {"failure": "below_bracket", "upper_bound_K": 366.0, "t_melt_K": 859.5,
                 "per_temperature": pts([366, 408, 452, 494], ["melting"] * 4), "reason": "below"}
        need, why = retry_decision_v2(a1, below)
        self.assertEqual([round(t, 3) for t in need["temps_K"]], [238.0, 280.667, 323.333, 366.0])  # In a2's bracket
        self.assertEqual((need["attempt"], need["protocol_version"], need["t_ref_K"], need["t_melt_K"]),
                         (2, 2, 429.75, 859.5))
        self.assertEqual(need["bracket_history"], [{"attempt": 1, "temps_K": [366.0, 408.0, 452.0, 494.0],
                                                    "failure": "below_bracket", "seed": 2027, "upper_bound_K": 366.0}])
        self.assertEqual(why, "retry 2: bracket shifted down")

        a2 = job(need["temps_K"], attempt=2, need=need, t_melt=need["t_melt_K"])
        self.assertEqual(a2.cli_args(Path("x"))[-6:], ["--seed", "2028", "--t-ref", "429.75", "--t-melt", "859.5"])
        above = {"failure": "above_bracket", "lower_bound_K": 366.0,
                 "per_temperature": pts(need["temps_K"], ["freezing"] * 4)}
        need3, why3 = retry_decision_v2(a2, above)
        self.assertIsNone(need3)
        self.assertIn("contradictory brackets", why3)

    def test_direction_reversal_stops_even_without_shared_bounds(self) -> None:
        history = [{"attempt": 1, "temps_K": [500.0, 550.0], "failure": "below_bracket"}]
        a2 = job([400, 450], attempt=2, need={"bracket_history": history})
        need, why = retry_decision_v2(a2, {"failure": "above_bracket",
                                           "per_temperature": pts([400, 450], ["stable", "freezing"])})
        self.assertIsNone(need)
        self.assertIn("reversed", why)

    def test_no_repeat_of_an_earlier_bracket(self) -> None:
        history = [{"attempt": 1, "temps_K": [1100.0, 1125.0, 1150.0, 1175.0], "failure": "undecided"}]
        a2 = job([1025, 1050, 1075, 1100], attempt=2, need={"bracket_history": history})
        need, why = retry_decision_v2(a2, {"failure": "above_bracket", "lower_bound_K": 1100.0,
                                           "per_temperature": pts([1025, 1050, 1075, 1100], ["freezing"] * 4)})
        self.assertIsNone(need)
        self.assertIn("repeats", why)

    def test_undecided_runs_longer_and_stays_on_v2(self) -> None:
        j = job([1350, 1375, 1400, 1425], need={"property": "melting_point", "protocol_version": 2,
                                                 "t_guess_K": 1358}, t_ref=1358.0)
        need = retry_need(j, {"failure": "undecided", "t_melt_K": 2716.0,
                              "per_temperature": pts([1350, 1375, 1400, 1425], ["stable"] * 4)})
        self.assertEqual((need["temps_K"], need["prod_steps"], need["protocol_version"]),
                         ([1350.0, 1375.0, 1400.0, 1425.0], 50000, 2))
        self.assertEqual((need["t_ref_K"], need["t_melt_K"]), (1358.0, 2716.0))
        self.assertNotIn("t_guess_K", need)

    def test_attempt_limit(self) -> None:
        j = job([366, 408], attempt=MAX_ATTEMPTS)
        self.assertIsNone(retry_need(j, {"failure": "below_bracket", "per_temperature": pts([366, 408], ["melting"] * 2)}))

    def test_v1_retry_unchanged_for_artefact_flags(self) -> None:
        """Protocol 1 still retries In a2-like runs (that is the behaviour the default must keep)."""
        j = job([238, 281, 323, 366])
        j.protocol = 1
        need = retry_need(j, {"failure": "above_bracket", "flags": ["liquid_half_incompletely_melted"],
                              "per_temperature": pts([238, 281, 323, 366], ["freezing"] * 4)})
        self.assertEqual(need["temps_K"][0], 366.0)
        self.assertNotIn("protocol_version", need)
        self.assertNotIn("bracket_history", need)


@unittest.skipUnless(HAVE_ASE, "needs ASE (GB10 environment)")
class AgentV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "board")
        self._env = mock.patch.dict(os.environ, NO_ENV)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def candidate(self, need):
        return self.board.post("scout_metals", "candidate", {"name": "Cu", "family": "metal", "tier": "trust",
                                                             "structure_source": "ase:Cu:fcc:3.61"}, needs=[need])

    def agent(self, runner) -> SimulatorAgent:
        return SimulatorAgent(self.board, "simulator_gb10", runner=runner, runs_root=Path(self._tmp.name) / "runs")

    def test_gate_failure_is_reported_without_a_retry(self) -> None:
        self.candidate(V2_REQUEST)
        gate = {"status": "failed", "failure": "liquid_not_prepared", "reason": "liquid half not melted",
                "flags": ["liquid_not_prepared"], "protocol_version": 2, "t_melt_K": 2716.0,
                "stopped_after": "prep_melt_nvt"}
        self.assertTrue(self.agent(FakeRunner(gate)).step())
        (report,) = self.board.snapshot().of_type("run_report")
        d = report.payload["details"]
        self.assertEqual((d["failure"], d["protocol_version"], d["stopped_after"]),
                         ("liquid_not_prepared", 2, "prep_melt_nvt"))
        self.assertTrue(d["retry"].startswith("no retry"))
        self.assertEqual(report.needs, [])

    def test_shifted_retry_carries_protocol_melt_temperature_and_history(self) -> None:
        self.candidate(V2_REQUEST)
        temps = [round(1358 * f) for f in (0.85, 0.95, 1.05, 1.15)]
        above = {"status": "failed", "failure": "above_bracket", "reason": "nothing melted", "flags": [],
                 "protocol_version": 2, "t_melt_K": 2716.0, "lower_bound_K": float(temps[-1]),
                 "per_temperature": pts(temps, ["freezing"] * 4)}
        runner = FakeRunner(above, OK)
        agent = self.agent(runner)
        self.assertTrue(agent.step())
        self.assertTrue(agent.step())
        retry = runner.jobs[1]
        self.assertEqual((retry.attempt, retry.protocol, retry.t_melt, retry.t_ref), (2, 2, 2716.0, 1358.0))
        self.assertEqual(retry.temps[0], float(temps[-1]))
        self.assertEqual(retry.need["bracket_history"][0]["temps_K"], [float(t) for t in temps])
        self.assertIn("--seed", retry.cli_args(Path("x")))
        self.assertEqual(retry.cli_args(Path("x"))[retry.cli_args(Path("x")).index("--seed") + 1], "2028")
        (res,) = self.board.snapshot().of_type("property_result")
        m = res.payload["method"]
        self.assertEqual((m["protocol"], m["protocol_version"], m["git_commit"]),
                         ("two-phase coexistence", 2, "abc123"))

    def test_v1_report_details_unchanged(self) -> None:
        self.candidate(REQUEST)
        above = {"status": "failed", "failure": "above_bracket", "reason": "nothing melted", "flags": []}
        self.assertTrue(self.agent(FakeRunner(above)).step())
        (report,) = self.board.snapshot().of_type("run_report")
        self.assertEqual(sorted(report.payload["details"]), ["failure", "flags"])
        (need,) = report.needs
        self.assertNotIn("protocol_version", need)


if __name__ == "__main__":
    unittest.main()
