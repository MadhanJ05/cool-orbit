"""Board integrity checks and the docker warm-up / in-container time limit. FAKE LLM and fake docker, SYNTHETIC data.

Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coolorbit import llm_keys
from coolorbit.board import Board
from coolorbit.eval import integrity
from coolorbit.eval.runner import ArmRunner, Budget, ClaudeCodeExecutor, parse_warmup, prepare_sandbox
from coolorbit.eval.sandbox import DockerSandbox, stage_context
from coolorbit.eval.tasks import TaskSet, load_tasks

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for eval_runner_helpers (see its docstring)
from eval_runner_helpers import FakeExecutor, naive_answer  # noqa: E402

CAND = {"name": "SYNTHETIC", "family": "t", "tier": "discovery"}


class IntegrityUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "b")
        self.board.post("eval_harness", "candidate", CAND)
        self.art = self.board.artifacts_dir

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_posting_through_the_board_is_clean(self) -> None:
        before = integrity.snapshot(self.art)
        self.board.post("worker", "candidate", CAND)
        self.board.post("worker", "candidate", CAND)
        self.assertEqual(integrity.check(before, self.art, "worker"), [])

    def test_editing_another_agents_record_is_caught(self) -> None:
        before = integrity.snapshot(self.art)
        store = self.art / "eval_harness" / "store.jsonl"
        store.write_text(store.read_text().replace("SYNTHETIC", "TAMPERED"))
        (v,) = integrity.check(before, self.art, "worker")
        self.assertIn("eval_harness/store.jsonl: existing bytes were modified", v)

    def test_appending_to_another_agents_store_is_caught(self) -> None:
        before = integrity.snapshot(self.art)
        with open(self.art / "eval_harness" / "store.jsonl", "a") as f:
            f.write(json.dumps({"producer_agent": "eval_harness"}) + "\n")
        self.assertIn("another agent's file grew", integrity.check(before, self.art, "worker")[0])

    def test_impersonation_and_deletion_are_caught(self) -> None:
        before = integrity.snapshot(self.art)
        self.board.post("checker", "candidate", CAND)  # the worker posting as the checker (bypassing ./board)
        problems = integrity.check(before, self.art, "worker")
        self.assertTrue(any("new file not allowed" in p for p in problems))
        self.assertTrue(any("record by 'checker'" in p for p in problems))
        before = integrity.snapshot(self.art)
        (self.art / "global_index.jsonl").unlink()
        self.assertIn("global_index.jsonl: deleted", integrity.check(before, self.art, "checker"))

    def test_truncating_own_store_is_caught(self) -> None:
        self.board.post("worker", "candidate", CAND)
        before = integrity.snapshot(self.art)
        (self.art / "worker" / "store.jsonl").write_text("")
        self.assertIn("not append-only", integrity.check(before, self.art, "worker")[0])

    def test_evidence_keeps_the_original_bytes(self) -> None:
        before = integrity.snapshot(self.art)
        store = self.art / "eval_harness" / "store.jsonl"
        original = store.read_bytes()
        store.write_text("")
        problems = integrity.check(before, self.art, "worker")
        out = integrity.save_evidence(before, self.board.root, "try1-worker-1", problems)
        self.assertEqual((out / "before" / "eval_harness" / "store.jsonl").read_bytes(), original)
        self.assertEqual(json.loads((out / "violations.json").read_text()), problems)


class IntegrityInRunnerTest(unittest.TestCase):
    def test_violation_is_recorded_in_run_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            full = load_tasks(calibration_path=tmp / "none.json")
            ts = TaskSet(version=1, frozen=False, tasks=[full.by_id()["tier1_al_melting"]])

            def tamper(cwd, prompt, attempt):
                naive_answer()(cwd, prompt, attempt)
                board_dir = Path(tmp) / "b" / "run" / "A-r1" / "tier1_al_melting" / "artifacts"
                store = board_dir / "eval_harness" / "store.jsonl"
                store.write_text(store.read_text().replace("Find the melting point", "SYNTHETIC tampered"))

            r = ArmRunner("A", "run", 1, FakeExecutor({"single_agent": tamper}), Budget(1.5, 30),
                          boards_root=tmp / "b", work_root=tmp / "w", skills_dir=None, log=lambda *_: None, keys=None)
            (rec,) = r.run(ts)
            self.assertEqual(len(rec["integrity_violations"]), 1)
            self.assertIn("eval_harness/store.jsonl", rec["integrity_violations"][0])
            self.assertTrue((tmp / "b" / "run" / "A-r1" / "tier1_al_melting" / "integrity").is_dir())

    def test_clean_run_has_no_violations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            full = load_tasks(calibration_path=tmp / "none.json")
            ts = TaskSet(version=1, frozen=False, tasks=[full.by_id()["tier1_al_melting"]])
            r = ArmRunner("A", "run", 1, FakeExecutor({"single_agent": naive_answer()}), Budget(1.5, 30),
                          boards_root=tmp / "b", work_root=tmp / "w", skills_dir=None, log=lambda *_: None, keys=None)
            (rec,) = r.run(ts)
            self.assertEqual(rec["integrity_violations"], [])


class WarmupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        (self.tmp / "b" / "artifacts").mkdir(parents=True)
        (self.tmp / "w").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_warmup_runs_before_the_agent_with_its_own_limit(self) -> None:
        cmd = DockerSandbox("img", warmup=True, warmup_timeout_s=600).run_command(
            ["claude", "-p", "x"], work_dir=self.tmp / "w", board_root=self.tmp / "b", name="n", session_timeout_s=1200)
        i = cmd.index("img")
        self.assertEqual(cmd[i + 1:i + 3], ["sh", "-c"])
        script = cmd[i + 3]
        self.assertLess(script.index("warmup.py"), script.index('exec timeout -k 10 1200s "$@"'))
        self.assertIn("timeout -k 10 600s python3", script)
        self.assertEqual(cmd[-3:], ["claude", "-p", "x"])
        self.assertEqual(DockerSandbox("img", warmup=True, warmup_timeout_s=600).outer_timeout_s(1200), 1830)

    def test_default_is_unchanged_for_other_callers(self) -> None:
        cmd = DockerSandbox("img").run_command(["claude"], work_dir=self.tmp / "w", board_root=self.tmp / "b", name="n")
        self.assertEqual(cmd[cmd.index("img") + 1:], ["claude"], "arm C's existing calls get the same command")

    def test_ro_mounts_are_read_only_and_checked(self) -> None:
        weights = self.tmp / "weights"
        weights.mkdir()
        cmd = DockerSandbox("img", ro_mounts=[(weights, "/opt/weights")]).run_command(
            ["claude"], work_dir=self.tmp / "w", board_root=self.tmp / "b", name="n")
        self.assertIn(f"{weights.resolve()}:/opt/weights:ro", cmd)
        for bad_target in ("/work", "/board/x", "relative"):
            with self.assertRaises(ValueError):
                DockerSandbox("img", ro_mounts=[(weights, bad_target)])
        with self.assertRaises(ValueError):
            DockerSandbox("img", ro_mounts=[(Path.home() / ".ssh", "/opt/x")])

    def test_warmup_script_is_staged_into_the_image(self) -> None:
        ctx = stage_context(self.tmp / "ctx")
        self.assertTrue((ctx / "tool" / "warmup.py").is_file())

    def test_parse_warmup(self) -> None:
        self.assertEqual(parse_warmup('x\nCOOLORBIT_WARMUP {"ok": true, "seconds": 42.0}\n{"type": "result"}'),
                         {"ok": True, "seconds": 42.0})
        self.assertIsNone(parse_warmup('{"type": "result"}'))

    def fake_docker(self, run_body: str) -> Path:
        path = self.tmp / "docker"
        path.write_text(f"#!{sys.executable}\nimport json, sys, time\n"
                        "if sys.argv[1:3] == ['image', 'inspect']: sys.exit(0)\n"
                        "if sys.argv[1] == 'kill': sys.exit(0)\n" + run_body + "\n")
        path.chmod(0o755)
        return path

    def test_warmup_time_is_recorded_and_excluded_from_agent_time(self) -> None:
        body = ("print('COOLORBIT_WARMUP ' + json.dumps({'ok': True, 'seconds': 0.5}), flush=True)\n"
                "time.sleep(0.6)\n"
                "print(json.dumps({'type': 'result', 'is_error': False, 'total_cost_usd': 0.1}))")
        ex = ClaudeCodeExecutor(sandbox=DockerSandbox("img", docker=str(self.fake_docker(body)), gpus=False,
                                                      warmup=True), env_provider=lambda: dict(os.environ))
        sb = prepare_sandbox(self.tmp / "sb", self.tmp / "b", "single_agent", docker=True)
        res = ex.run("p", cwd=sb, budget=Budget(1, 30), transcript_path=self.tmp / "t.jsonl", board_root=self.tmp / "b")
        self.assertEqual(res.warmup, {"ok": True, "seconds": 0.5})
        self.assertLess(res.wall_s, 0.6 + 0.5, "agent time excludes the warm-up")
        self.assertEqual(res.usd, 0.1)

    def test_inner_session_limit_counts_as_timed_out(self) -> None:
        ex = ClaudeCodeExecutor(sandbox=DockerSandbox("img", docker=str(self.fake_docker("sys.exit(124)")),
                                                      gpus=False), env_provider=lambda: dict(os.environ))
        sb = prepare_sandbox(self.tmp / "sb", self.tmp / "b", "single_agent", docker=True)
        res = ex.run("p", cwd=sb, budget=Budget(1, 30), transcript_path=self.tmp / "t.jsonl", board_root=self.tmp / "b")
        self.assertTrue(res.timed_out)
        self.assertIn("inside the container", res.error)


if __name__ == "__main__":
    unittest.main()
