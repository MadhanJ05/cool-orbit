"""Per-task budget, role model/effort and consistent pricing in the arm runners. FAKE LLM, SYNTHETIC usage.

Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from coolorbit import llm_roles
from coolorbit.eval import runner as runner_mod
from coolorbit.eval.runner import (ArmRunner, Budget, ClaudeCodeExecutor, SessionResult, _usage_fields,
                                   price_session, role_settings)
from coolorbit.eval.tasks import TaskSet, load_tasks
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for eval_runner_helpers (see its docstring)
from eval_runner_helpers import naive_answer, post  # noqa: E402


class SpendingExecutor:
    """Fake LLM that records each session's cap and reports a scripted spend (SYNTHETIC)."""

    def __init__(self, behavior: dict, spend: dict) -> None:
        self.behavior, self.spend, self.caps = behavior, spend, []

    def run(self, prompt, *, cwd, budget, transcript_path, board_root):
        role, attempt = cwd.name.rsplit("-", 1)
        self.caps.append((role, int(attempt), budget.usd, budget.model, budget.effort))
        self.behavior[role](cwd, prompt, int(attempt))
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text('{"type": "result"}\n')
        usd = self.spend.get((role, int(attempt)), 0.0)
        return SessionResult(exit_code=0, timed_out=False, wall_s=0.01, usd_reported=usd,
                             transcript='{"type": "result"}\n')


def fail_then_pass_checker(cwd, prompt, attempt):
    rid = re.search(r"--parent ([^\s`]+)`?, with", prompt).group(1)
    verdict = "fail" if attempt == 1 else "pass"
    post(cwd, "check", json.dumps({"check_type": "method_audit", "verdict": verdict, "note": "SYNTHETIC"}),
         "--parent", rid)


def rerun_worker(cwd, prompt, attempt):
    if attempt == 1:
        return naive_answer()(cwd, prompt, attempt)
    old = re.search(r"verdict on your result (\S+) was", prompt).group(1)
    post(cwd, "property_result", json.dumps({
        "candidate": "SYNTHETIC", "family": "t", "tier": "discovery", "property": "melting_point", "value": 930.0,
        "units": "K", "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence"},
        "note": "SYNTHETIC"}), "--parent", old)


class BudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        full = load_tasks(calibration_path=self.tmp / "none.json")
        self.ts = TaskSet(version=1, frozen=False, tasks=[full.by_id()["tier1_al_melting"]])
        cfg = role_settings()
        self.budget = Budget(usd=cfg["task_budget_usd"], timeout_s=30, model=cfg["model"], effort=cfg["effort"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_arm(self, arm, ex):
        r = ArmRunner(arm, "run", 1, ex, self.budget, boards_root=self.tmp / "boards", work_root=self.tmp / "work",
                      skills_dir=None, log=lambda *_: None, keys=None)
        (rec,) = r.run(self.ts)
        return rec

    def test_config_values(self) -> None:
        cfg = role_settings()
        raw = json.loads(llm_roles.CONFIG_PATH.read_text())
        self.assertEqual(cfg["task_budget_usd"], raw["comparison_budget_usd_per_task_per_arm"])
        self.assertEqual((cfg["model"], cfg["effort"]),
                         (raw["roles"]["arm_baseline"]["model"], raw["roles"]["arm_baseline"]["effort"]))

    def test_arm_a_single_session_gets_the_whole_task_budget(self) -> None:
        ex = SpendingExecutor({"single_agent": naive_answer()}, {("single_agent", 1): 0.9})
        rec = self.run_arm("A", ex)
        (cap,) = ex.caps
        self.assertEqual(cap[2:], (self.budget.usd, self.budget.model, self.budget.effort))
        self.assertAlmostEqual(rec["budget"]["spent_usd"], 0.9)

    def test_arm_b_splits_the_budget_and_never_exceeds_it(self) -> None:
        spend = {("worker", 1): 0.6, ("checker", 1): 0.3, ("worker", 2): 0.2}
        ex = SpendingExecutor({"worker": rerun_worker, "checker": fail_then_pass_checker}, spend)
        rec = self.run_arm("B", ex)
        caps = [round(c[2], 4) for c in ex.caps]
        # worker1: 1.5/2; checker1: (1.5-0.6)/2; worker2: (0.9-0.3)/2; checker2 (last): all that's left
        self.assertEqual(caps, [0.75, 0.45, 0.3, 0.4])
        self.assertLessEqual(sum(spend.values()) + caps[-1], self.budget.usd + 1e-9)
        self.assertTrue(all(c[3:] == (self.budget.model, self.budget.effort) for c in ex.caps), "same model/effort")
        self.assertIsNone(rec["budget"]["stopped_by_budget"])

    def test_spent_budget_stops_the_pipeline_and_is_recorded(self) -> None:
        ex = SpendingExecutor({"worker": naive_answer(), "checker": fail_then_pass_checker},
                              {("worker", 1): 1.49})
        rec = self.run_arm("B", ex)
        self.assertEqual([c[0] for c in ex.caps], ["worker"])
        self.assertIn("task budget used up", rec["budget"]["stopped_by_budget"])

    def test_command_carries_role_model_and_effort(self) -> None:
        cmd = ClaudeCodeExecutor().command("p", self.budget)
        self.assertEqual(cmd[cmd.index("--model") + 1], self.budget.model)
        self.assertEqual(cmd[cmd.index("--effort") + 1], self.budget.effort)


class PricingTest(unittest.TestCase):
    def test_charged_figure_is_claude_codes_total_and_cost_usd_is_a_cross_check(self) -> None:
        final = {"type": "result", "total_cost_usd": 9.99, "usage": {"input_tokens": 1}, "modelUsage": {
            "claude-opus-5-5": {"inputTokens": 1_000_000, "outputTokens": 100_000, "cacheReadInputTokens": 2_000_000,
                                "cacheCreationInputTokens": 500_000, "costUSD": 9.0},
            "claude-haiku-5-5": {"inputTokens": 1_000_000, "outputTokens": 0, "costUSD": 0.99}}}
        res = SessionResult(exit_code=0, timed_out=False, wall_s=1.0, **_usage_fields(final))
        price_session(res, "claude-opus-5-5", cap_usd=1.5)
        self.assertEqual((res.usd, res.usd_source), (9.99, "reported"))
        expected = (llm_roles.cost_usd("claude-opus-5-5", res.model_usage["claude-opus-5-5"])
                    + llm_roles.cost_usd("claude-haiku-5-5", res.model_usage["claude-haiku-5-5"]))
        self.assertAlmostEqual(res.usd_check, expected)
        self.assertIsNone(res.unpriced_models)

    def test_no_report_charges_the_session_cap(self) -> None:
        res = SessionResult(exit_code=None, timed_out=True, wall_s=60.0, error="killed")  # no final event
        price_session(res, "claude-opus-5-5", cap_usd=0.75)
        self.assertEqual((res.usd, res.usd_source), (0.75, "session_cap"))

    def test_unpriced_model_is_listed_for_the_cross_check(self) -> None:
        final = {"type": "result", "total_cost_usd": 0.05, "usage": {"input_tokens": 10, "output_tokens": 5},
                 "modelUsage": {"mystery-model": {}}}
        res = SessionResult(exit_code=0, timed_out=False, wall_s=1.0, **_usage_fields(final))
        price_session(res, "claude-opus-5-5", cap_usd=1.5)
        self.assertEqual((res.usd, res.unpriced_models), (0.05, ["mystery-model"]))

    def test_timed_out_session_charges_its_cap_against_the_task_budget(self) -> None:
        class Killed:
            def run(self, prompt, *, cwd, budget, transcript_path, board_root):
                transcript_path.parent.mkdir(parents=True, exist_ok=True)
                transcript_path.write_text("")
                return SessionResult(exit_code=None, timed_out=True, wall_s=1.0, error="killed after 1 s")
        with tempfile.TemporaryDirectory() as tmp:
            full = load_tasks(calibration_path=Path(tmp) / "none.json")
            ts = TaskSet(version=1, frozen=False, tasks=[full.by_id()["tier1_al_melting"]])
            r = ArmRunner("A", "run", 1, Killed(), Budget(1.5, 1, "claude-opus-5-5", "high"),
                          boards_root=Path(tmp) / "b", work_root=Path(tmp) / "w", skills_dir=None,
                          log=lambda *_: None, keys=None)
            (rec,) = r.run(ts)
            row = json.loads((Path(tmp) / "b" / "run" / "A-r1" / "tier1_al_melting" / "costs.jsonl").read_text())
        self.assertEqual((rec["budget"]["spent_usd"], row["usd"], row["usd_source"]), (1.5, 1.5, "session_cap"))


class CliTest(unittest.TestCase):
    def test_overrides_warn(self) -> None:
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buf):
            runner_mod.main(["--arm", "A", "--run", "x", "--repeat", "1", "--timeout-min", "1", "--budget-usd", "3",
                             "--model", "claude-sonnet-5-5", "--task", "tier1_al_melting", "--dry-run",
                             "--boards-root", f"{tmp}/b", "--work-root", f"{tmp}/w"])
        out = buf.getvalue()
        self.assertIn("WARNING: --budget-usd 3.0 differs", out)
        self.assertIn("WARNING: --model claude-sonnet-5-5 differs", out)

    def test_defaults_come_from_the_config(self) -> None:
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buf):
            runner_mod.main(["--arm", "A", "--run", "x", "--repeat", "1", "--timeout-min", "1",
                             "--task", "tier1_al_melting", "--dry-run", "--boards-root", f"{tmp}/b",
                             "--work-root", f"{tmp}/w"])
        out = buf.getvalue()
        cfg = role_settings()
        self.assertNotIn("differs", out)
        self.assertIn(f"--model {cfg['model']}", out)
        self.assertIn(f"--effort {cfg['effort']}", out)
        self.assertIn(f"--max-budget-usd {cfg['task_budget_usd']:g}", out)


if __name__ == "__main__":
    unittest.main()
