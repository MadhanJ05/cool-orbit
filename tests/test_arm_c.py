"""Tests for the arm C runner (scripts/run_arm_c.py): fake Claude Code worker, fake GPU, no LLM calls, no spend."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from coolorbit import llm_roles
from coolorbit.board import Board
from coolorbit.eval.metrics import score_task
from coolorbit.eval.tasks import Task, task_requests
from coolorbit.board import load_rules

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_arm_c", REPO / "scripts" / "run_arm_c.py")
arm_c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arm_c)

GENERAL_ONLY = {"scout_general": {"general": True, "candidates": []}}


def make_task(**kw) -> Task:
    base = {"id": "test_li_liquid", "origin": "team", "kind": "well_posed", "expected": "answer",
            "prompt": "Compute the self-diffusion coefficient of liquid lithium at 470 K.",
            "material": "lithium (liquid)", "property": "self_diffusion", "units": "cm2/s"}
    base.update(kw)
    return Task.model_validate(base)


LI_LIQUID = make_task()
LIF_PRISTINE = make_task(id="test_lif_pristine", kind="ill_posed", expected="refuse", material="LiF (crystal)",
                         prompt="Compute the Li self-diffusion coefficient in pristine crystalline LiF at 300 K.")


def no_gpu(*_a, **_k):
    raise AssertionError("these tasks must not reach the GPU")


class FakeWorker:
    """Plays the Claude Code worker: posts what its prompt tells it to, with the links it was given."""

    def __init__(self, board_root: Path, ensembles: list[str], usd: float = 0.4) -> None:
        self.board, self.ensembles, self.usd = Board(board_root), list(ensembles), usd
        self.prompts: list[str] = []

    def __call__(self, prompt: str, attempt: int, usd_cap: float, timeout_s: float) -> dict:
        self.prompts.append(prompt)
        args = re.search(r"`\./board post property_result '<payload>' ([^`]*)`", prompt).group(1)
        parents = re.findall(r"--parent (\S+)", args)
        m = re.search(r"--fulfills (\S+):(\d+)", args)
        fulfills = (m.group(1), int(m.group(2))) if m else None
        cand = self.board.snapshot().artifacts[parents[0]].payload
        self.board.post("worker_cc", "property_result", {
            "candidate": cand["name"], "family": cand["family"], "tier": cand["tier"], "property": "self_diffusion",
            "value": 6.0e-5, "units": "cm2/s", "uncertainty": 5e-6,
            "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "MSD", "ensemble": self.ensembles.pop(0),
                       "timestep_fs": 1.0, "n_atoms": 432, "sim_length_ps": 50.0}}, parents=parents, fulfills=fulfills)
        llm_roles._append_cost(Path(os.environ[llm_roles.COSTS_ENV]),
                               {"agent": "worker_cc", "task_id": os.environ[llm_roles.TASK_ENV], "kind": "llm",
                                "usd": self.usd})
        return {"role": "worker", "agent": "worker_cc", "attempt": attempt, "contamination": [], "infra_failure": False}


class ArmCRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workers: dict[str, FakeWorker] = {}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_task(self, task: Task, ensembles=("NVE",), **kw) -> dict:
        def factory(board_root: Path):
            self.workers[task.id] = FakeWorker(board_root, list(ensembles))
            return self.workers[task.id]

        opts = dict(run="t", repeat=1, boards_root=self.root / "boards", budget_usd=1.5, timeout_min=5,
                    preset="quick", llm_mode="never", worker_usd=1.0, model="claude-opus-5-5",
                    session_fn_factory=factory, sim_runner=no_gpu, seeds=GENERAL_ONLY, runs_root=self.root / "runs",
                    sandbox="none")
        opts.update(kw)
        return arm_c.run_task(task, **opts)

    def outcome(self, rec: dict, task: Task):
        snap = Board(rec["board"]).snapshot()
        return score_task(snap, task, task_requests(snap)[task.id], load_rules()), snap

    def test_ill_posed_task_is_refused_without_running_anything(self) -> None:
        rec = self.run_task(LIF_PRISTINE)
        self.assertEqual(rec["sessions"], [])
        self.assertEqual(rec["stopped"], "idle")
        self.assertEqual(rec["agent_errors"], [])
        out, _ = self.outcome(rec, LIF_PRISTINE)
        self.assertTrue(out.refused)
        self.assertEqual(out.trap_outcome, "avoided")

    def test_control_goes_to_the_worker_and_is_answered(self) -> None:
        rec = self.run_task(LI_LIQUID)
        self.assertEqual(len(rec["sessions"]), 1)
        out, _ = self.outcome(rec, LI_LIQUID)
        self.assertFalse(out.refused)
        self.assertTrue(out.confident)
        self.assertAlmostEqual(rec["llm_usd"], 0.4)
        costs = [json.loads(line) for line in (Path(rec["board"]) / "costs.jsonl").read_text().splitlines()]
        self.assertEqual([c["agent"] for c in costs], ["worker_cc"])
        self.assertTrue((Path(rec["board"]) / "run_record.json").exists())

    def test_failed_audit_on_worker_result_triggers_a_rerun_that_supersedes_it(self) -> None:
        rec = self.run_task(LI_LIQUID, ensembles=("NVT-Langevin", "NVE"))
        self.assertEqual(len(rec["sessions"]), 2)
        first, second = self.workers[LI_LIQUID.id].prompts
        self.assertNotIn("failed a method audit", first)
        self.assertIn("failed a method audit", second)
        self.assertIn("langevin", second.lower())
        out, snap = self.outcome(rec, LI_LIQUID)
        old, new = snap.of_type("property_result")
        self.assertIn(old.artifact_id, new.parent_artifact_ids)  # the rerun supersedes the flawed result
        self.assertEqual(out.shipped_id, new.artifact_id)
        self.assertTrue(out.confident)

    def test_out_of_budget_stops_the_task(self) -> None:
        rec = self.run_task(LI_LIQUID, budget_usd=0.0, llm_mode="always")  # premise LLM call refused pre-network
        self.assertEqual(rec["stopped"], "budget")
        self.assertEqual(rec["sessions"], [])

    def test_resume_skips_done_tasks_and_refuses_interrupted_boards(self) -> None:
        self.run_task(LIF_PRISTINE)
        self.assertEqual(self.run_task(LIF_PRISTINE)["skipped"], "already complete")
        (self.root / "boards" / "t" / "C-r1" / LI_LIQUID.id).mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            self.run_task(LI_LIQUID)

    def test_results_tsv_is_appended(self) -> None:
        log = self.root / "results.tsv"
        arm_c.log_result(log, self.run_task(LIF_PRISTINE))
        arm_c.log_result(log, self.run_task(LI_LIQUID))
        lines = log.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("finished\trepeat\ttask_id"))


class PiecesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "b")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def need_on(self, name: str, structure=None):
        payload = {"name": name, "family": "metal", "tier": "trust"}
        if structure:
            payload["structure_source"] = structure
        self.board.post("scout_x", "candidate", payload, needs=[{
            "artifact_type": "property_result", "property": "melting_point", "query": f"melting point of {name}",
            "rationale": "test request for the runner", "temps_K": [650, 700], "reps": [2, 2, 4]}])
        return next(on for on in self.board.open_needs() if on.artifact.payload.get("name") == name)

    def test_simulator_leaves_jobs_it_cannot_set_up_to_the_worker(self) -> None:
        sim = arm_c.ArmCSimulator(self.board, runner=no_gpu)
        gap = self.need_on("Zn")                       # no structure on file
        cu = self.need_on("Cu", "ase:Cu:fcc:3.61")
        snap = self.board.snapshot()
        self.assertFalse(sim.owns(snap, gap))
        self.assertTrue(sim.owns(snap, cu))
        self.assertEqual([on.artifact.payload["name"] for on, _ in sim.pending()], ["Cu"])
        self.assertFalse(arm_c.ArmCSimulator(self.board, runner=no_gpu, max_minutes=1e-6).owns(snap, cu))

        prompts = []
        task = make_task(property="melting_point", units="K", prompt="Find the melting point of zinc metal.")
        disp = arm_c.WorkerDispatcher(self.board, task, "req", lambda p, *a: prompts.append(p) or {}, sim=sim,
                                      deadline=time.time() + 60, usd_left=lambda: 1.5, worker_usd=1.0,
                                      wait_for_premise=False)
        self.assertTrue(disp.step())
        self.assertIn(f"--fulfills {gap.artifact.artifact_id}:0", prompts[0])
        self.assertFalse(disp.step())  # Cu is the Simulator's; nothing else to do

    def test_no_session_without_budget(self) -> None:
        self.need_on("Zn")
        calls = []
        disp = arm_c.WorkerDispatcher(self.board, LI_LIQUID, "req", lambda *a: calls.append(a) or {}, sim=None,
                                      deadline=time.time() + 60, usd_left=lambda: 0.0, worker_usd=1.0,
                                      wait_for_premise=False)
        self.assertFalse(disp.step())
        self.assertEqual(calls, [])
        self.assertIn("budget", disp.log_path.read_text())

    def test_bounded_runner_never_outlives_the_task(self) -> None:
        seen, jobs = [], []
        run = arm_c.bounded_runner(lambda job, out, t: seen.append(t) or ({"status": "ok"}, {}, out),
                                   time.time() + 30, jobs)
        run(SimpleNamespace(task="melting_point of Cu"), Path(self.tmp.name), 3600)
        self.assertLessEqual(seen[0], 30)
        self.assertEqual(jobs[0]["status"], "ok")

    def test_premise_llm_calls_go_through_llm_roles(self) -> None:
        with mock.patch.object(arm_c.llm_roles, "create", return_value=("resp", 0.1)) as create:
            client = arm_c.RolesClient("premise_checker", "referee_premise")
            self.assertEqual(client.beta.messages.create(model="m", messages=[]), "resp")
        create.assert_called_once_with("premise_checker", agent="referee_premise", model="m", messages=[])

    def test_known_contamination_reads_agent_sources(self) -> None:
        agents = Path(self.tmp.name) / "agents"
        agents.mkdir()
        (agents / "clean.py").write_text("x = 1\n")
        self.assertEqual(arm_c.known_contamination(agents), [])
        (agents / "leaky.py").write_text("from coolorbit.eval.tasks import load_tasks\n")
        self.assertEqual(len(arm_c.known_contamination(agents)), 1)


if __name__ == "__main__":
    unittest.main()
