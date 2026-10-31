"""Tests for the arm A/B runners, the board tool and repeat scoring, with a FAKE LLM.

No real Claude Code session runs here: FakeExecutor plays scripted SYNTHETIC agents that post
through the real ./board wrapper, and a fake `claude` binary tests the subprocess plumbing.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board
from coolorbit.board._scienceclaw import REPO_ROOT
from coolorbit.eval.metrics import score_repeat
from coolorbit.eval.runner import (ArmRunner, Budget, ClaudeCodeExecutor, SessionResult, contamination,
                                   prepare_sandbox, render_prompt)
from coolorbit.eval.tasks import TaskSet, load_tasks, seed_board

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for eval_runner_helpers (see its docstring)
from eval_runner_helpers import (BUDGET, COEX, RULES, SINGLE, FakeExecutor, board_cmd,  # noqa: E402
                                 naive_answer, post, request_id)



class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        full = load_tasks(calibration_path=self.tmp / "none.json")
        keep = ["tier1_al_melting", "nvidia_lif_crystal_diffusion"]
        self.ts = TaskSet(version=1, frozen=False, tasks=[full.by_id()[k] for k in keep])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def runner(self, arm, executor, **kw) -> ArmRunner:
        kw.setdefault("keys", None)  # shared-key failover is tested in test_eval_runner_keys.py
        return ArmRunner(arm, "test-run", 1, executor, BUDGET, boards_root=self.tmp / "boards",
                         work_root=self.tmp / "work", skills_dir=None, log=lambda *_: None, **kw)


class PromptAndSandboxTest(Base):
    def test_prompts_render_completely(self) -> None:
        for name, fields in [("arm_a_single_agent.md", {}), ("arm_b_worker.md", {}),
                             ("arm_b_checker.md", {"result_id": "RES123"})]:
            text = render_prompt(name, task_prompt="Find the melting point of aluminum at ambient pressure.",
                                 board_path="./board", task_request_id="REQ123",
                                 calibration_path="/data/calibration/tier1.json", **fields)
            self.assertNotIn("<!--", text, name)
            self.assertIsNone(re.search(r"\{[a-z_]+\}", text), f"unfilled placeholder in {name}")
            self.assertIn("aluminum", text)
            self.assertIn("./board", text)
            self.assertIn("--fulfills REQ123:0", text)
            self.assertNotIn("coolorbit/eval", text, "prompts must not point agents at the eval code")
            self.assertIn("Measured reference values you may use: `/data/calibration/tier1.json`", text,
                          f"{name}: every arm is told about the calibration data (arm C's Librarian uses it)")
        a = render_prompt("arm_a_single_agent.md", task_prompt="x" * 12, board_path="b", task_request_id="r",
                          calibration_path="c")
        self.assertIn("require recovery of an independent known result", a)  # NVIDIA's guidance, verbatim

    def test_sandbox_refuses_repo_and_reuse(self) -> None:
        with self.assertRaises(ValueError):
            prepare_sandbox(REPO_ROOT / "tmp-sandbox-test", self.tmp / "b", "single_agent", None)
        self.assertFalse((REPO_ROOT / "tmp-sandbox-test").exists())
        prepare_sandbox(self.tmp / "sb", self.tmp / "b", "single_agent", None)
        with self.assertRaises(FileExistsError):
            prepare_sandbox(self.tmp / "sb", self.tmp / "b", "single_agent", None)

    def test_sandbox_copies_skills(self) -> None:
        skills = self.tmp / "skills" / "demo-skill"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("SYNTHETIC skill")
        sb = prepare_sandbox(self.tmp / "sb", self.tmp / "b", "worker", self.tmp / "skills")
        self.assertTrue((sb / ".claude" / "skills" / "demo-skill" / "SKILL.md").is_file())

    def test_board_tool_posts_as_the_fixed_agent_only(self) -> None:
        board = Board(self.tmp / "b")
        req = seed_board(board, self.ts)["tier1_al_melting"]
        sb = prepare_sandbox(self.tmp / "sb", board.root, "worker", None)
        cid = post(sb, "candidate", '{"name": "SYNTHETIC", "family": "t", "tier": "discovery"}', "--fulfills", f"{req}:0")
        art = board.snapshot().artifacts[cid]
        self.assertEqual(art.producer_agent, "worker")
        self.assertIn(cid, board_cmd(sb, "show").stdout)
        bad = board_cmd(sb, "post", "check", '{"verdict": "pass"}', "--parent", cid)  # missing check_type
        self.assertEqual(bad.returncode, 1)
        self.assertIn("error", bad.stderr)
        env = {k: v for k, v in os.environ.items() if not k.startswith("COOLORBIT_")}
        raw = subprocess.run([sys.executable, "-m", "coolorbit.eval.board_tool", "show"], capture_output=True,
                             text=True, env={**env, "PYTHONPATH": str(REPO_ROOT)})
        self.assertEqual(raw.returncode, 2)

    def test_unsandboxed_sandbox_gets_a_copy_of_the_calibration_file(self) -> None:
        from coolorbit.eval.runner import calibration_path_for
        from coolorbit.eval.sandbox import CALIBRATION_FILE
        sb = prepare_sandbox(self.tmp / "sbc", self.tmp / "b", "worker", None)
        copy = Path(calibration_path_for(sb, docker=False))
        self.assertEqual(copy.read_bytes(), CALIBRATION_FILE.read_bytes())
        self.assertEqual(calibration_path_for(sb, docker=True), "/data/calibration/tier1.json")

    def test_end_to_end_prompt_names_the_calibration_file(self) -> None:
        seen = {}
        def act(cwd, prompt, attempt):
            seen["prompt"] = prompt
            naive_answer()(cwd, prompt, attempt)
        self.runner("A", FakeExecutor({"single_agent": act})).run(self.ts, ["tier1_al_melting"])
        path = re.search(r"Measured reference values you may use: `([^`]+)`", seen["prompt"]).group(1)
        self.assertTrue(Path(path).is_file())

    def test_contamination_scan(self) -> None:
        self.assertEqual(contamination('{"tool": "Read", "path": "/x/coolorbit/eval/tasks.json"}'),
                         ["coolorbit/eval", "tasks.json"])
        self.assertEqual(contamination("ran lammps"), [])
        self.assertEqual(contamination("read /data/calibration/tier1.json"), [], "calibration data is allowed")
        for blocked in ("tasks.json", "coolorbit/eval", "coolorbit/agents", "trust_rules", "CLAUDE.md"):
            self.assertEqual(contamination(f"cat {blocked}"), [blocked])


class ArmATest(Base):
    def test_naive_single_agent_end_to_end(self) -> None:
        fake = FakeExecutor({"single_agent": naive_answer()})
        records = self.runner("A", fake).run(self.ts)
        self.assertEqual([c[1] for c in fake.calls], ["single_agent", "single_agent"])
        rep = self.tmp / "boards" / "test-run" / "A-r1"
        for r in records:
            board_dir = rep / r["task_id"]
            self.assertTrue((board_dir / "run_record.json").is_file())
            self.assertTrue((board_dir / "transcripts" / "single_agent-1.jsonl").is_file())
            (row,) = [json.loads(l) for l in (board_dir / "costs.jsonl").read_text().splitlines()]
            self.assertEqual((row["agent"], row["input_tokens"], row["usd"]), ("single_agent", 1000, 0.01))
        self.assertTrue((self.tmp / "work" / "test-run" / "A-r1" / "tier1_al_melting" / "single_agent-1" / "PROMPT.md").is_file())
        score = score_repeat(rep, self.ts, RULES)
        self.assertEqual((score.n_tasks, score.silent_errors, score.trap_catch_rate), (2, 2, 0.0))
        self.assertTrue(score.cost["recorded"])
        self.assertEqual(score.cost["total"]["input_tokens"], 2000)

    def test_each_task_gets_its_own_board(self) -> None:
        self.runner("A", FakeExecutor({"single_agent": naive_answer()})).run(self.ts)
        rep = self.tmp / "boards" / "test-run" / "A-r1"
        boards = sorted(p.name for p in rep.iterdir())
        self.assertEqual(boards, sorted(t.id for t in self.ts.tasks))

    def test_resume_skips_complete_and_refuses_interrupted(self) -> None:
        fake = FakeExecutor({"single_agent": naive_answer()})
        self.runner("A", fake).run(self.ts, ["tier1_al_melting"])
        self.runner("A", fake).run(self.ts, ["tier1_al_melting"])
        self.assertEqual(len(fake.calls), 1, "a complete task must not run twice")
        (self.tmp / "boards" / "test-run" / "A-r1" / "nvidia_lif_crystal_diffusion" / "artifacts").mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            self.runner("A", fake).run(self.ts, ["nvidia_lif_crystal_diffusion"])

    def test_contamination_is_recorded(self) -> None:
        fake = FakeExecutor({"single_agent": naive_answer()}, transcript_text='{"path": "../tasks.json"}\n')
        (rec,) = self.runner("A", fake).run(self.ts, ["tier1_al_melting"])
        self.assertEqual(rec["contamination"], ["tasks.json"])

    def test_only_arms_a_and_b(self) -> None:
        with self.assertRaises(ValueError):
            self.runner("C", FakeExecutor({}))


class ArmBTest(Base):
    def test_checker_fail_triggers_one_rerun_then_check(self) -> None:
        state = {}

        def worker(cwd, prompt, attempt):
            if attempt == 1:
                naive_answer()(cwd, prompt, attempt)
            else:
                self.assertIn("This is your one rerun", prompt)
                old = re.search(r"verdict on your result (\S+) was", prompt).group(1)
                post(cwd, "property_result", json.dumps({
                    "candidate": "SYNTHETIC", "family": "test", "tier": "discovery", "property": "melting_point",
                    "value": 930.0, "units": "K", "method": COEX, "note": "SYNTHETIC fake-LLM rerun"}),
                     "--parent", old)

        def checker(cwd, prompt, attempt):
            rid = re.search(r"--parent ([^\s`]+)`?, with", prompt).group(1)
            state.setdefault("checked", []).append(rid)
            verdict = "fail" if attempt == 1 else "pass"
            post(cwd, "check", json.dumps({"check_type": "method_audit", "verdict": verdict,
                                           "note": "SYNTHETIC fake checker"}), "--parent", rid)

        fake = FakeExecutor({"worker": worker, "checker": checker})
        (rec,) = self.runner("B", fake).run(self.ts, ["tier1_al_melting"])
        self.assertEqual([(c[1], c[2]) for c in fake.calls],
                         [("worker", 1), ("checker", 1), ("worker", 2), ("checker", 2)])
        self.assertEqual(len(set(state["checked"])), 2, "the second check is on the rerun")
        score = score_repeat(self.tmp / "boards" / "test-run" / "B-r1", self.ts, RULES)
        (al,) = [o for o in score.outcomes if o.task_id == "tier1_al_melting"]
        self.assertEqual((al.trap_outcome, al.shipped_value), ("caught", 930.0))
        self.assertEqual({r["agent"] for r in map(json.loads, (self.tmp / "boards" / "test-run" / "B-r1" /
                          "tier1_al_melting" / "costs.jsonl").read_text().splitlines())}, {"worker", "checker"})

    def test_checker_pass_ends_pipeline(self) -> None:
        def checker(cwd, prompt, attempt):
            rid = re.search(r"--parent ([^\s`]+)`?, with", prompt).group(1)
            post(cwd, "check", '{"check_type": "method_audit", "verdict": "pass", "note": "SYNTHETIC"}', "--parent", rid)
        fake = FakeExecutor({"worker": naive_answer(), "checker": checker})
        self.runner("B", fake).run(self.ts, ["tier1_al_melting"])
        self.assertEqual([c[1] for c in fake.calls], ["worker", "checker"])

    def test_worker_refusal_skips_checker(self) -> None:
        def refuse(cwd, prompt, attempt):
            cand = post(cwd, "candidate", '{"name": "SYNTHETIC", "family": "t", "tier": "discovery"}',
                        "--fulfills", f"{request_id(prompt)}:0")
            post(cwd, "run_report", '{"status": "refused", "task": "SYNTHETIC", "reason": "SYNTHETIC: ill-posed"}',
                 "--parent", cand)
        fake = FakeExecutor({"worker": refuse, "checker": lambda *a: self.fail("no checker after a refusal")})
        self.runner("B", fake).run(self.ts, ["nvidia_lif_crystal_diffusion"])
        self.assertEqual([c[1] for c in fake.calls], ["worker"])
        score = score_repeat(self.tmp / "boards" / "test-run" / "B-r1", self.ts, RULES)
        (lif,) = [o for o in score.outcomes if o.task_id == "nvidia_lif_crystal_diffusion"]
        self.assertEqual(lif.trap_outcome, "avoided")


class ClaudeExecutorTest(Base):
    def fake_claude(self, body: str) -> Path:
        path = self.tmp / "claude"
        path.write_text(f"#!{sys.executable}\nimport json, sys, time, pathlib\n"
                        f"pathlib.Path({str(self.tmp / 'args.json')!r}).write_text(json.dumps(sys.argv[1:]))\n" + body)
        path.chmod(0o755)
        return path

    def test_command_usage_and_cost_parsing(self) -> None:
        result = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 7, "total_cost_usd": 0.42,
                  "usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50,
                            "output_tokens": 300}, "modelUsage": {"fake-model": {}}}
        fake = self.fake_claude(f"print(json.dumps({{'type': 'system'}}))\nprint(json.dumps({result!r}))\n")
        sb = prepare_sandbox(self.tmp / "sb", self.tmp / "b", "single_agent", None)
        res = ClaudeCodeExecutor(str(fake), env_provider=lambda: dict(os.environ)).run("PROMPT TEXT", cwd=sb, budget=Budget(1.5, 30, "some-model"),
                                                transcript_path=self.tmp / "t.jsonl", board_root=self.tmp / "b")
        args = json.loads((self.tmp / "args.json").read_text())
        self.assertEqual(args[:2], ["-p", "PROMPT TEXT"])
        for flag in ("--max-budget-usd", "--setting-sources", "--output-format", "--model", "--no-session-persistence"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--max-budget-usd") + 1], "1.5")
        self.assertEqual(args[args.index("--setting-sources") + 1], "project")
        self.assertEqual((res.exit_code, res.timed_out, res.input_tokens, res.output_tokens, res.usd, res.num_turns,
                          res.model), (0, False, 1050, 300, 0.42, 7, "fake-model"))
        self.assertTrue((self.tmp / "t.jsonl").read_text().strip())

    def test_timeout_is_recorded_not_fatal(self) -> None:
        fake = self.fake_claude("time.sleep(30)\n")
        sb = prepare_sandbox(self.tmp / "sb", self.tmp / "b", "single_agent", None)
        res = ClaudeCodeExecutor(str(fake), env_provider=lambda: dict(os.environ)).run("p", cwd=sb, budget=Budget(1, 1), transcript_path=self.tmp / "t.jsonl", board_root=self.tmp / "b")
        self.assertTrue(res.timed_out)
        self.assertIsNone(res.input_tokens)
        self.assertIn("wall-clock", res.error)

    def test_missing_binary_is_an_error_not_a_crash(self) -> None:
        sb = prepare_sandbox(self.tmp / "sb", self.tmp / "b", "single_agent", None)
        res = ClaudeCodeExecutor(str(self.tmp / "nope"), env_provider=lambda: dict(os.environ)).run("p", cwd=sb, budget=BUDGET, transcript_path=self.tmp / "t.jsonl", board_root=self.tmp / "b")
        self.assertIn("could not start", res.error)


class ScoreRepeatTest(Base):
    def test_task_seeded_twice_in_one_repeat_is_an_error(self) -> None:
        rep = self.tmp / "rep"
        seed_board(Board(rep / "one"), self.ts)
        seed_board(Board(rep / "two"), self.ts)
        with self.assertRaises(ValueError):
            score_repeat(rep, self.ts, RULES)

    def test_not_a_board_or_folder_of_boards(self) -> None:
        (self.tmp / "empty").mkdir()
        with self.assertRaises(FileNotFoundError):
            score_repeat(self.tmp / "empty", self.ts, RULES)


if __name__ == "__main__":
    unittest.main()
