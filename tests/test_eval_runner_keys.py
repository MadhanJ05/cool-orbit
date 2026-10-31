"""Shared-key failover in the arm A/B runners (coolorbit.llm_keys), with a FAKE LLM and SYNTHETIC keys.

llm_keys is pointed at temp files, so the real .env and runs/llm_key_state.json are never read or written.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coolorbit import llm_keys
from coolorbit.eval.metrics import score_repeat
from coolorbit.eval.runner import ArmRunner, Budget, ClaudeCodeExecutor, SessionResult, prepare_sandbox
from coolorbit.eval.tasks import TaskSet, load_tasks
from tests.test_eval_runner import RULES, naive_answer, post, request_id

OUT_OF_CREDIT = '{"type": "result", "is_error": true, "result": "Credit balance is too low"}\n'
PRIMARY_KEY, BACKUP_KEY = "sk-SYNTHETIC-primary", "sk-SYNTHETIC-backup"


class ScriptedExecutor:
    """Fake LLM: runs ``behavior[role]`` unless the key in use is listed in ``broke`` (then: out of credit)."""

    def __init__(self, behavior: dict, broke: set, transcript_ok: str = '{"type": "result"}\n') -> None:
        self.behavior, self.broke, self.transcript_ok, self.calls = behavior, broke, transcript_ok, []

    def run(self, prompt, *, cwd, budget, transcript_path, board_root):
        key = llm_keys.active_key_name()
        role, attempt = cwd.name.rsplit("-", 1)
        self.calls.append((cwd.parent.name, role, int(attempt), key))
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        if (key, role) in self.broke or (key, "*") in self.broke:
            transcript_path.write_text(OUT_OF_CREDIT)
            return SessionResult(exit_code=1, timed_out=False, wall_s=0.01, input_tokens=50, output_tokens=0,
                                 usd_reported=0.0, error="error_during_execution", transcript=OUT_OF_CREDIT)
        self.behavior[role](cwd, prompt, int(attempt))
        transcript_path.write_text(self.transcript_ok)
        return SessionResult(exit_code=0, timed_out=False, wall_s=0.01, input_tokens=1000, output_tokens=100,
                             usd_reported=0.01, transcript=self.transcript_ok)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "llm_key_state.json"
        for name, value in (("ENV_PATH", self.tmp / "no.env"), ("STATE_PATH", self.state)):
            p = mock.patch.object(llm_keys, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {llm_keys.PRIMARY: PRIMARY_KEY, llm_keys.BACKUP: BACKUP_KEY})
        p.start()
        self.addCleanup(p.stop)
        full = load_tasks(calibration_path=self.tmp / "none.json")
        self.ts = TaskSet(version=1, frozen=False, tasks=[full.by_id()["tier1_al_melting"]])
        self.rep = self.tmp / "boards" / "run" / "A-r1"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def runner(self, arm: str, executor) -> ArmRunner:
        return ArmRunner(arm, "run", 1, executor, Budget(1, 30), boards_root=self.tmp / "boards",
                         work_root=self.tmp / "work", skills_dir=None, log=lambda *_: None)


class FailoverTest(Base):
    def test_primary_out_of_credit_reruns_on_backup_without_scoring_the_failure(self) -> None:
        ex = ScriptedExecutor({"single_agent": naive_answer()}, broke={(llm_keys.PRIMARY, "*")})
        (rec,) = self.runner("A", ex).run(self.ts)
        self.assertEqual([c[3] for c in ex.calls], [llm_keys.PRIMARY, llm_keys.BACKUP])
        self.assertTrue(llm_keys.primary_exhausted(self.state))
        self.assertEqual(rec["infra_failed_attempts"], ["_infra_failed/tier1_al_melting.try1"])
        self.assertEqual(rec["sessions"][0]["result"]["key_name"], llm_keys.BACKUP)
        failed = self.rep / "_infra_failed" / "tier1_al_melting.try1"
        self.assertTrue((failed / "INFRA_FAILURE.json").is_file())
        self.assertTrue((failed / "costs.jsonl").is_file(), "the failed session's cost is kept with it")
        self.assertTrue((self.tmp / "work" / "run" / "A-r1" / "tier1_al_melting.try2").is_dir(), "fresh sandbox")
        # Scoring sees only the backup attempt.
        score = score_repeat(self.rep, self.ts, RULES)
        self.assertEqual((score.n_tasks, score.silent_errors), (1, 1))
        costs = [json.loads(l) for l in (self.rep / "tier1_al_melting" / "costs.jsonl").read_text().splitlines()]
        self.assertEqual([c["key_name"] for c in costs], [llm_keys.BACKUP])

    def test_arm_b_out_of_credit_at_the_checker_reruns_the_whole_task(self) -> None:
        def checker(cwd, prompt, attempt):
            rid = re.search(r"--parent ([^\s`]+)`?, with", prompt).group(1)
            post(cwd, "check", '{"check_type": "method_audit", "verdict": "pass", "note": "SYNTHETIC"}', "--parent", rid)
        ex = ScriptedExecutor({"worker": naive_answer(), "checker": checker}, broke={(llm_keys.PRIMARY, "checker")})
        (rec,) = self.runner("B", ex).run(self.ts)
        self.assertEqual([(c[1], c[3]) for c in ex.calls], [("worker", llm_keys.PRIMARY), ("checker", llm_keys.PRIMARY),
                                                            ("worker", llm_keys.BACKUP), ("checker", llm_keys.BACKUP)])
        self.assertEqual(len(rec["sessions"]), 2)
        self.assertEqual(len(rec["infra_failed_attempts"]), 1)

    def test_backup_out_of_credit_stops_the_run(self) -> None:
        ex = ScriptedExecutor({"single_agent": naive_answer()},
                              broke={(llm_keys.PRIMARY, "*"), (llm_keys.BACKUP, "*")})
        with self.assertRaises(llm_keys.NoUsableKey):
            self.runner("A", ex).run(self.ts)
        failed = sorted(p.name for p in (self.rep / "_infra_failed").iterdir())
        self.assertEqual(failed, ["tier1_al_melting.try1", "tier1_al_melting.try2"])
        self.assertFalse((self.rep / "tier1_al_melting").exists())

    def test_already_on_backup_and_out_of_credit_stops(self) -> None:
        llm_keys.mark_primary_exhausted("SYNTHETIC earlier switch", self.state)
        ex = ScriptedExecutor({"single_agent": naive_answer()}, broke={(llm_keys.BACKUP, "*")})
        with self.assertRaises(llm_keys.NoUsableKey):
            self.runner("A", ex).run(self.ts)
        self.assertEqual(len(ex.calls), 1)

    def test_text_in_a_successful_transcript_does_not_switch_keys(self) -> None:
        noisy = ('{"type": "assistant", "message": "the web page said: Credit balance is too low"}\n'
                 '{"type": "result", "is_error": false}\n')
        ex = ScriptedExecutor({"single_agent": naive_answer()}, broke=set(), transcript_ok=noisy)
        (rec,) = self.runner("A", ex).run(self.ts)
        self.assertFalse(llm_keys.primary_exhausted(self.state))
        self.assertEqual(rec["infra_failed_attempts"], [])

    def test_no_keys_fails_before_creating_anything(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in (llm_keys.PRIMARY, llm_keys.BACKUP)}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(llm_keys.NoUsableKey):
                self.runner("A", ScriptedExecutor({}, set())).run(self.ts)
        self.assertFalse((self.tmp / "boards").exists())


class ExecutorEnvTest(Base):
    def test_child_gets_only_the_active_key_as_anthropic_api_key(self) -> None:
        seen = self.tmp / "seen.json"
        fake = self.tmp / "claude"
        fake.write_text(f"#!{sys.executable}\nimport json, os\n"
                        f"open({str(seen)!r}, 'w').write(json.dumps({{k: os.environ.get(k) for k in "
                        f"('ANTHROPIC_API_KEY', {llm_keys.PRIMARY!r}, {llm_keys.BACKUP!r})}}))\n"
                        "print(json.dumps({'type': 'result', 'is_error': False}))\n")
        fake.chmod(0o755)
        sb = prepare_sandbox(self.tmp / "sb", self.tmp / "b", "single_agent", None)
        ClaudeCodeExecutor(str(fake)).run("p", cwd=sb, budget=Budget(1, 30), transcript_path=self.tmp / "t.jsonl",
                                          board_root=self.tmp / "b")
        got = json.loads(seen.read_text())
        self.assertEqual(got, {"ANTHROPIC_API_KEY": PRIMARY_KEY, llm_keys.PRIMARY: None, llm_keys.BACKUP: None})
        llm_keys.mark_primary_exhausted("SYNTHETIC", self.state)
        ClaudeCodeExecutor(str(fake)).run("p", cwd=sb, budget=Budget(1, 30), transcript_path=self.tmp / "t2.jsonl",
                                          board_root=self.tmp / "b")
        self.assertEqual(json.loads(seen.read_text())["ANTHROPIC_API_KEY"], BACKUP_KEY)


if __name__ == "__main__":
    unittest.main()
