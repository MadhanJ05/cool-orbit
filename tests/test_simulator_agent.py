"""Simulator agent tests with a fake runner (no GPU, no MD). All numbers are synthetic fixtures.

Needs ASE (structure parsing); skipped without it. Run: .venv/bin/python -m unittest tests.test_simulator_agent -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board, derive_status

try:
    import ase  # noqa: F401

    from coolorbit.sim.simulator_agent import MAX_ATTEMPTS, SimulatorAgent, ood_reason
    HAVE_ASE = True
except ImportError:
    HAVE_ASE = False

RULES = {"frozen": True, "independent_check_types": ["second_mlip", "classical_md"], "rejection_min_referees": 2,
         "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 15.0}, "tier1": {"abs": 20.0}}}}
REQUEST = {"artifact_type": "property_result", "property": "melting_point", "query": "melting_point of Cu",
           "rationale": "validate the coexistence method", "temps_K": [1250, 1300, 1350, 1400], "preset": "quick"}


def _settings(job) -> dict:
    return {"settings": {"mode": job.mode, "model": "medium-mpa-0", "dt_fs": job.dt_fs, "temps": job.temps,
                         "prod_steps": job.steps()["prod"]}, "n_atoms": job.n_atoms}


class FakeRunner:
    """Returns scripted outcomes in order; records the jobs it was given."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.jobs = []

    def __call__(self, job, out_dir: Path, timeout_s: float):
        self.jobs.append(job)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, _settings(job), out_dir / "run"


OK = {"status": "ok", "property": "melting_point", "value": 1296.0, "uncertainty": 25.0, "units": "K",
      "flags": ["quick_settings_not_production"]}
ABOVE = {"status": "failed", "failure": "above_bracket", "reason": "no temperature melted"}


@unittest.skipUnless(HAVE_ASE, "needs ASE (GB10 environment)")
class SimulatorAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "board")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def candidate(self, source: str = "ase:Cu:fcc:3.61", need: dict = REQUEST, tier: str = "trust"):
        return self.board.post("scout_metals", "candidate",
                               {"name": "Cu", "family": "metal", "tier": tier, "structure_source": source},
                               needs=[need])

    def agent(self, runner) -> SimulatorAgent:
        return SimulatorAgent(self.board, "simulator_gb10", runner=runner, runs_root=Path(self._tmp.name) / "runs")

    def test_success_posts_result_and_trust_needs(self) -> None:
        cand = self.candidate()
        runner = FakeRunner(OK)
        self.assertTrue(self.agent(runner).step())
        job = runner.jobs[0]
        self.assertEqual(job.n_atoms, 4 * 4 * 4 * 8)
        self.assertIn("--quick", job.cli_args(Path("x")))
        snap = self.board.snapshot()
        (res,) = snap.of_type("property_result")
        self.assertEqual(res.parent_artifact_ids, [cand.artifact_id])
        self.assertEqual(res.payload["method"]["protocol"], "two-phase coexistence")
        self.assertEqual(derive_status(snap, res, RULES).status, "simulated")
        open_kinds = sorted(n.need.get("check_type") or n.need["artifact_type"] for n in self.board.open_needs())
        self.assertEqual(open_kinds, ["classical_md", "method_audit", "reference_value"])  # request itself fulfilled
        self.assertFalse(self.agent(FakeRunner()).step())  # nothing left for a simulator

    def test_failed_run_posts_report_and_retry_with_shifted_bracket(self) -> None:
        self.candidate()
        runner = FakeRunner(ABOVE, OK)
        agent = self.agent(runner)
        self.assertTrue(agent.step())
        (report,) = self.board.snapshot().of_type("run_report")
        self.assertEqual(report.payload["status"], "failed")
        self.assertEqual(report.payload["details"]["failure"], "above_bracket")
        self.assertTrue(agent.step())  # picks up its own retry need
        retry_job = runner.jobs[1]
        self.assertEqual(retry_job.attempt, 2)
        self.assertEqual(retry_job.temps, [1400.0, 1450.0, 1500.0, 1550.0])  # anchored at the highest tried T
        (res,) = self.board.snapshot().of_type("property_result")
        self.assertIn(report.artifact_id, res.parent_artifact_ids)  # lineage: result <- failed attempt

    def test_retry_keeps_the_nearest_decisive_temperature(self) -> None:
        """Replays the first real agent run: 1300 K undecided, 1350-1450 K melting -> retry 1200-1350 K."""
        self.candidate(need={**REQUEST, "temps_K": [1300, 1350, 1400, 1450]})
        below = {"status": "failed", "failure": "below_bracket", "reason": "no temperature froze",
                 "per_temperature": [{"T_target_K": 1300.0, "verdict": "stable"},
                                     {"T_target_K": 1350.0, "verdict": "melting"},
                                     {"T_target_K": 1400.0, "verdict": "melting"},
                                     {"T_target_K": 1450.0, "verdict": "melting"}]}
        runner = FakeRunner(below, OK)
        agent = self.agent(runner)
        agent.step()
        agent.step()
        self.assertEqual(runner.jobs[1].temps, [1200.0, 1250.0, 1300.0, 1350.0])

    def test_gives_up_after_max_attempts(self) -> None:
        self.candidate()
        agent = self.agent(FakeRunner(*[ABOVE] * MAX_ATTEMPTS))
        for _ in range(MAX_ATTEMPTS):
            self.assertTrue(agent.step())
        self.assertFalse(agent.step())
        reports = self.board.snapshot().of_type("run_report")
        self.assertEqual([r.payload["attempt"] for r in reports], [1, 2, 3])
        self.assertEqual(reports[-1].needs, [])  # no endless retries

    def test_crash_is_reported_not_hidden(self) -> None:
        self.candidate()
        self.assertTrue(self.agent(FakeRunner(RuntimeError("CUDA out of memory"))).step())
        (report,) = self.board.snapshot().of_type("run_report")
        self.assertIn("CUDA out of memory", report.payload["reason"])

    def test_bad_structure_source_is_refused(self) -> None:
        self.candidate(source="mp:mp-30")
        runner = FakeRunner()
        self.assertTrue(self.agent(runner).step())
        self.assertEqual(runner.jobs, [])
        (report,) = self.board.snapshot().of_type("run_report")
        self.assertEqual(report.payload["status"], "refused")
        self.assertEqual(self.board.open_needs(), [])

    def test_t_guess_brackets_and_ignores_other_engines(self) -> None:
        self.candidate(need={**{k: v for k, v in REQUEST.items() if k != "temps_K"}, "t_guess_K": 1000})
        runner = FakeRunner(OK)
        self.assertTrue(self.agent(runner).step())
        self.assertEqual(runner.jobs[0].temps, [850.0, 950.0, 1050.0, 1150.0])

        self.candidate(need={**REQUEST, "engine": "classical_md"})
        self.assertFalse(self.agent(FakeRunner()).step())  # not addressed to an ALCHEMI simulator

    def test_follow_up_request_reaches_the_candidate(self) -> None:
        cand = self.candidate(need={**REQUEST, "engine": "classical_md"})  # not ours; ignored
        self.board.post("scout_metals", "request", {"note": "latent heat at the melting point"},
                        parents=[cand.artifact_id],
                        needs=[{"artifact_type": "property_result", "property": "latent_heat",
                                "query": "latent_heat of Cu at 1296 K", "rationale": "figure-of-merit input",
                                "temperature_K": 1296, "preset": "quick"}])
        latent_ok = {"status": "ok", "property": "latent_heat", "value": 2.0e5, "uncertainty": 1.0e4, "units": "J/kg"}
        runner = FakeRunner(latent_ok)
        self.assertTrue(self.agent(runner).step())
        job = runner.jobs[0]
        self.assertEqual((job.mode, job.temps, job.n_atoms), ("latent", [1296.0], 256))
        self.assertEqual(job.candidate.artifact_id, cand.artifact_id)

    def test_rerun_requested_on_a_check_supersedes_the_flawed_result(self) -> None:
        cand = self.candidate()
        agent = self.agent(FakeRunner(OK, {**OK, "value": 1350.0}))
        self.assertTrue(agent.step())
        (old,) = self.board.snapshot().of_type("property_result")
        self.board.post("referee_a", "check",
                        {"check_type": "method_audit", "verdict": "fail", "flags": ["single_phase_heating"]},
                        parents=[old.artifact_id],
                        needs=[{"artifact_type": "property_result", "property": "melting_point",
                                "query": "rerun melting_point of Cu with two-phase coexistence",
                                "rationale": "the first result superheated", "temps_K": [1300, 1350, 1400, 1450],
                                "preset": "quick"}])
        self.assertTrue(agent.step())
        snap = self.board.snapshot()
        new = [r for r in snap.of_type("property_result") if r.artifact_id != old.artifact_id][0]
        self.assertIn(old.artifact_id, new.parent_artifact_ids)
        self.assertIn(cand.artifact_id, new.parent_artifact_ids)
        rep = derive_status(snap, old, RULES)
        self.assertEqual(rep.status, "flagged")
        self.assertEqual(rep.superseded_by, [new.artifact_id])

    def test_follow_up_for_another_property_does_not_supersede(self) -> None:
        """A latent-heat request hanging off the melting result must not mark the melting result superseded."""
        cand = self.candidate()
        latent_ok = {"status": "ok", "property": "latent_heat", "value": 2.0e5, "uncertainty": 1.0e4, "units": "J/kg"}
        agent = self.agent(FakeRunner(OK, latent_ok))
        agent.step()
        (melt,) = self.board.snapshot().of_type("property_result")
        self.board.post("scout_metals", "request", {"note": "latent heat at the coexistence melting point"},
                        parents=[cand.artifact_id, melt.artifact_id],
                        needs=[{"artifact_type": "property_result", "property": "latent_heat",
                                "query": "latent_heat of Cu at 1296 K", "rationale": "figure-of-merit input",
                                "temperature_K": 1296, "preset": "quick"}])
        agent.step()
        snap = self.board.snapshot()
        latent = [r for r in snap.of_type("property_result") if r.payload["property"] == "latent_heat"][0]
        self.assertNotIn(melt.artifact_id, latent.parent_artifact_ids)
        self.assertEqual(derive_status(snap, melt, RULES).superseded_by, [])

    def test_alloy_latent_heat_request_becomes_an_alloy_job(self) -> None:
        self.board.post("scout_alloys", "candidate",
                        {"name": "Field's metal", "family": "low-melting alloy", "tier": "trust",
                         "structure_source": "alloy:wt:Bi32.5In51Sn16.5"},
                        needs=[{"artifact_type": "property_result", "property": "latent_heat",
                                "query": "latent_heat of Field's metal", "rationale": "unmeasured-ish alloy latent heat",
                                "temperature_K": 335.0, "preset": "quick"}])
        latent_ok = {"status": "ok", "property": "latent_heat", "value": 3.0e4, "uncertainty": 2.0e3, "units": "J/kg"}
        runner = FakeRunner(latent_ok)
        self.assertTrue(self.agent(runner).step())
        job = runner.jobs[0]
        args = job.cli_args(Path("x"))
        self.assertEqual(job.mode, "alloy-latent")
        self.assertEqual(args[:3], ["alloy-latent", "--composition", "wt:Bi32.5In51Sn16.5"])
        self.assertIn("--temperature", args)
        (res,) = self.board.snapshot().of_type("property_result")
        self.assertEqual(res.payload["method"]["protocol"], "NPT enthalpy difference (liquid alloy vs pure-element solids)")

    def test_alloy_melting_point_is_refused_honestly(self) -> None:
        self.board.post("scout_alloys", "candidate",
                        {"name": "EGaIn", "family": "low-melting alloy", "tier": "trust",
                         "structure_source": "alloy:wt:Ga75.5In24.5"},
                        needs=[{**REQUEST, "query": "melting_point of EGaIn"}])
        runner = FakeRunner()
        self.assertTrue(self.agent(runner).step())
        self.assertEqual(runner.jobs, [])
        (report,) = self.board.snapshot().of_type("run_report")
        self.assertEqual(report.payload["status"], "refused")
        self.assertIn("phase-diagram", report.payload["reason"])

    def test_reference_crystal_source(self) -> None:
        self.candidate(source="ref:Ga", need={**REQUEST, "temps_K": [280, 295, 310, 325]})
        runner = FakeRunner(OK)
        self.assertTrue(self.agent(runner).step())
        args = runner.jobs[0].cli_args(Path("x"))
        self.assertIn("ref", args)
        self.assertEqual(args[args.index("--element") + 1], "Ga")

    def test_brev_runner_runs_the_pushed_commit_and_copies_results_back(self) -> None:
        """Fake `git` and `brev` (no network, no cost): checks the remote command and the result round trip."""
        import json
        from types import SimpleNamespace

        from coolorbit.sim.simulator_agent import REPO_ROOT, BrevRunner

        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[0] == "git" and "status" in argv:
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            if argv[0] == "git" and "rev-parse" in argv:
                return SimpleNamespace(stdout="abc123\n", stderr="", returncode=0)
            if argv[1] == "exec":
                return SimpleNamespace(stdout="REMOTE_EXIT=0 REMOTE_DIR=/root/cool-orbit/runs/remote/x\n",
                                       stderr="", returncode=0)
            if argv[1] == "copy":  # pretend the remote run dir arrived locally
                run_dir = Path(argv[3]) / "x" / "20261031T000000Z_Cu_coexistence"
                run_dir.mkdir(parents=True)
                (run_dir / "result.json").write_text(json.dumps(OK))
                (run_dir / "settings.json").write_text(json.dumps({"settings": {}, "n_atoms": 512}))
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            raise AssertionError(argv)

        from coolorbit.sim.simulator_agent import build_job

        self.candidate()
        (on,) = self.board.open_needs()
        job = build_job(self.board.snapshot(), on)
        # As a file: structure would be: a local absolute path that must become repo-relative on the instance.
        job.structure_args = ["--structure-file", str(REPO_ROOT / "data" / "structures" / "x.cif")]
        result, settings, run_dir = BrevRunner("h100-test", brev="brev", run=fake_run)(
            job, Path(self._tmp.name) / "out", 60)
        exec_cmd = next(c for c in calls if c[1] == "exec")[3]
        self.assertIn("git checkout -q --detach abc123", exec_cmd)
        self.assertNotIn(str(REPO_ROOT) + "/", exec_cmd)  # no local absolute paths sent to the instance
        self.assertEqual(result["value"], OK["value"])
        self.assertEqual(settings["settings"]["remote"], {"instance": "h100-test", "commit": "abc123"})

    def test_job_size_window_splits_work_between_agents(self) -> None:
        self.candidate()  # quick Cu job, ~5 GB10-minutes
        big_only = SimulatorAgent(self.board, "simulator_brev", runner=FakeRunner(), min_minutes=30)
        self.assertEqual(big_only.pending(), [])
        self.assertEqual(len(self.agent(FakeRunner()).pending()), 1)

    def test_organics_are_out_of_domain(self) -> None:
        self.assertIsNotNone(ood_reason({"C", "H"}))
        self.assertIsNotNone(ood_reason({"C", "H", "O"}))
        self.assertIsNone(ood_reason({"Cu"}))
        self.assertIsNone(ood_reason({"Na", "C", "H", "O"}))  # salts of organics are not flagged by this rule


if __name__ == "__main__":
    unittest.main()
