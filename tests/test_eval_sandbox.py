"""Tests for the Docker sandbox (coolorbit/eval/sandbox.py) without Docker or a GPU.

A fake `docker` binary records its arguments; the staged board tool is exercised for real.
No real container or Claude Code session runs here. All data is SYNTHETIC.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coolorbit.board import Board
from coolorbit.board._scienceclaw import REPO_ROOT
from coolorbit.eval import runner as runner_mod
from coolorbit.eval.runner import ArmRunner, Budget, ClaudeCodeExecutor, prepare_sandbox
from coolorbit.eval.sandbox import API_KEY_ENV, DockerSandbox, _forbidden_mount, stage_context
from coolorbit.eval.tasks import TaskSet, load_tasks

FAKE_RESULT = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "total_cost_usd": 0.05,
               "usage": {"input_tokens": 10, "output_tokens": 5}, "modelUsage": {"fake-model": {}}}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def fake_docker(self, run_body: str = f"print(json.dumps({FAKE_RESULT!r}))", image_ok: bool = True) -> Path:
        log = self.tmp / "docker.log"
        path = self.tmp / "docker"
        path.write_text(
            f"#!{sys.executable}\nimport json, sys, time\n"
            f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "cmd = sys.argv[1:3]\n"
            f"if cmd == ['image', 'inspect']: sys.exit(0 if {image_ok} else 1)\n"
            "if sys.argv[1] == 'kill': sys.exit(0)\n"
            "if sys.argv[1] == 'run':\n"
            + "".join(f"    {line}\n" for line in run_body.splitlines()))
        path.chmod(0o755)
        return path

    def calls(self) -> list[list[str]]:
        log = self.tmp / "docker.log"
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


class StageContextTest(Base):
    def test_context_is_minimal(self) -> None:
        ctx = stage_context(self.tmp / "ctx")
        files = {str(p.relative_to(ctx)) for p in ctx.rglob("*") if p.is_file()}
        self.assertIn("Dockerfile", files)
        self.assertIn("tool/board_tool.py", files)
        self.assertIn("tool/entrypoint.sh", files)
        self.assertIn("tool/coolorbit/board/store.py", files)
        self.assertIn("tool/scienceclaw/artifacts/artifact.py", files)
        self.assertTrue(any(f.startswith("skills/") for f in files), "ALCHEMI skills must be in the image")
        for f in files:
            for bad in ("tasks.json", "metrics.py", "runner.py", "CLAUDE.md", ".env", "calibration", "trust_rules",
                        "arms.md", "prompts/", "BLIND_TASKS", "coolorbit/eval", "coolorbit/agents"):
                self.assertNotIn(bad, f, f"{f} must not be in the image")

    def test_staged_tool_runs_without_the_repo(self) -> None:
        ctx = stage_context(self.tmp / "ctx")
        board = Board(self.tmp / "board")
        env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(ctx / "tool"),
               "COOLORBIT_BOARD": str(board.root), "COOLORBIT_AGENT": "single_agent"}
        tool = str(ctx / "tool" / "board_tool.py")
        out = subprocess.run([sys.executable, tool, "post", "candidate",
                              '{"name": "SYNTHETIC", "family": "t", "tier": "discovery"}'],
                             capture_output=True, text=True, env=env, cwd=self.tmp)
        self.assertEqual(out.returncode, 0, out.stderr)
        where = subprocess.run([sys.executable, "-c", "import coolorbit.board, sys; print(coolorbit.board.__file__)"],
                               capture_output=True, text=True, env=env, cwd=self.tmp).stdout
        self.assertTrue(where.startswith(str(ctx)), f"imported from {where!r}, not the staged copy")
        (art,) = board.snapshot().artifacts.values()
        self.assertEqual(art.producer_agent, "single_agent")

    def test_refuses_non_empty_destination(self) -> None:
        (self.tmp / "ctx").mkdir()
        (self.tmp / "ctx" / "x").write_text("x")
        with self.assertRaises(FileExistsError):
            stage_context(self.tmp / "ctx")


class RunCommandTest(Base):
    def cmd(self, **kw) -> list[str]:
        work, board = self.tmp / "work", self.tmp / "board"
        (board / "artifacts").mkdir(parents=True)
        work.mkdir()
        return DockerSandbox("img:tag", **kw).run_command(["claude", "-p", "x"], work_dir=work, board_root=board,
                                                         name="coolorbit-test")

    def test_only_two_mounts_one_env_var_and_the_gpu(self) -> None:
        cmd = self.cmd()
        mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a in ("-v", "--volume", "--mount")]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(mounts[0].endswith(":/work:rw"))
        self.assertTrue(mounts[1].endswith("/board/artifacts:/board/artifacts:rw"))
        envs = [cmd[i + 1] for i, a in enumerate(cmd) if a in ("-e", "--env")]
        self.assertEqual(envs, [API_KEY_ENV], "only the API key, passed by name (no value on the command line)")
        self.assertFalse(any(a.startswith("--env-file") for a in cmd))
        self.assertEqual(cmd[cmd.index("--gpus") + 1], "all")
        self.assertEqual(cmd[cmd.index("--cap-drop") + 1], "ALL")
        self.assertEqual(cmd[cmd.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
        self.assertIn("--rm", cmd)
        self.assertEqual(cmd[cmd.index("img:tag") + 1:], ["claude", "-p", "x"])
        joined = " ".join(cmd)
        for bad in ("docker.sock", ".ssh", ".config/gh", ".brev", "/.env", "--privileged", str(REPO_ROOT) + ":"):
            self.assertNotIn(bad, joined)
        self.assertNotIn(f"{Path.home()}:", joined)

    def test_no_gpus_for_plumbing_tests(self) -> None:
        self.assertNotIn("--gpus", self.cmd(gpus=False))

    def test_refuses_sensitive_mounts(self) -> None:
        for bad in (Path.home(), REPO_ROOT, Path.home() / ".ssh" / "x", Path.home() / ".config" / "gh"):
            self.assertIsNotNone(_forbidden_mount(bad), bad)
        self.assertIsNone(_forbidden_mount(self.tmp / "work"))
        with self.assertRaises(ValueError):
            DockerSandbox().run_command(["x"], work_dir=Path.home(), board_root=self.tmp / "b", name="n")

    def test_container_board_wrapper_uses_container_paths(self) -> None:
        sb = prepare_sandbox(self.tmp / "sb", self.tmp / "board", "checker", docker=True)
        text = (sb / "board").read_text()
        self.assertIn("COOLORBIT_BOARD=/board", text)
        self.assertIn("COOLORBIT_AGENT='checker'", text)
        self.assertNotIn(str(REPO_ROOT), text)
        self.assertNotIn(str(Path.home()), text)
        self.assertFalse((sb / ".claude").exists(), "skills come from the image in docker mode")


class DockerExecutorTest(Base):
    def setUp(self) -> None:
        super().setUp()
        full = load_tasks(calibration_path=self.tmp / "none.json")
        self.ts = TaskSet(version=1, frozen=False, tasks=[full.by_id()["tier1_al_melting"]])

    def runner(self, docker_bin: Path, budget=Budget(1, 30)) -> ArmRunner:
        ex = ClaudeCodeExecutor(sandbox=DockerSandbox("img:tag", docker=str(docker_bin), gpus=False))
        return ArmRunner("A", "dock", 1, ex, budget, boards_root=self.tmp / "boards", work_root=self.tmp / "work",
                         skills_dir=None, log=lambda *_: None, docker=True)

    def test_session_runs_through_docker(self) -> None:
        with mock.patch.dict(os.environ, {API_KEY_ENV: "sk-SYNTHETIC-test-key"}):
            (rec,) = self.runner(self.fake_docker()).run(self.ts)
        runs = [c for c in self.calls() if c[0] == "run"]
        self.assertEqual(len(runs), 1)
        self.assertIn("claude", runs[0])
        self.assertNotIn("sk-SYNTHETIC-test-key", json.dumps(self.calls()), "the key must not be on the command line")
        self.assertEqual(rec["sandbox"], "docker")
        self.assertEqual(rec["sessions"][0]["result"]["usd"], 0.05)
        board = self.tmp / "boards" / "dock" / "A-r1" / "tier1_al_melting"
        mount = runs[0][runs[0].index("-v", runs[0].index("-v") + 1) + 1]
        self.assertEqual(mount, f"{(board / 'artifacts').resolve()}:/board/artifacts:rw")

    def test_timeout_kills_the_container(self) -> None:
        with mock.patch.dict(os.environ, {API_KEY_ENV: "sk-SYNTHETIC"}):
            (rec,) = self.runner(self.fake_docker("time.sleep(30)"), Budget(1, 1)).run(self.ts)
        run = next(c for c in self.calls() if c[0] == "run")
        name = run[run.index("--name") + 1]
        self.assertIn(["kill", name], self.calls())
        self.assertTrue(rec["sessions"][0]["result"]["timed_out"])

    def test_preflight_needs_key_and_image(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != API_KEY_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, API_KEY_ENV):
                self.runner(self.fake_docker()).run(self.ts)
        with mock.patch.dict(os.environ, {API_KEY_ENV: "sk-SYNTHETIC"}):
            with self.assertRaisesRegex(RuntimeError, "not found"):
                self.runner(self.fake_docker(image_ok=False)).run(self.ts)
        self.assertFalse((self.tmp / "boards").exists(), "nothing is created when preflight fails")


class CliTest(Base):
    def test_unsandboxed_needs_explicit_flag(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner_mod.main(["--arm", "A", "--run", "x", "--repeat", "1", "--budget-usd", "1", "--timeout-min", "1",
                             "--sandbox", "none", "--dry-run"])

    def test_dry_run_defaults_to_docker_with_gpus(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            runner_mod.main(["--arm", "B", "--run", "x", "--repeat", "1", "--budget-usd", "1", "--timeout-min", "1",
                             "--task", "tier1_al_melting", "--dry-run", "--boards-root", str(self.tmp / "b"),
                             "--work-root", str(self.tmp / "w")])
        out = buf.getvalue()
        self.assertIn("docker run", out)
        self.assertIn("--gpus all", out)
        self.assertIn("-e ANTHROPIC_API_KEY", out)
        self.assertFalse((self.tmp / "b").exists() or (self.tmp / "w").exists(), "dry run creates nothing")


if __name__ == "__main__":
    unittest.main()
