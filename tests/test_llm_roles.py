"""Tests for the role -> model config (no network, no keys)."""

from __future__ import annotations

import unittest

from coolorbit import llm_roles


class LlmRolesTest(unittest.TestCase):
    def test_every_role_has_a_priced_model_and_valid_effort(self) -> None:
        cfg = llm_roles.load()
        for name, r in cfg["roles"].items():
            self.assertIn(r["model"], cfg["prices_per_mtok"], name)
            self.assertIn(r["effort"], ("low", "medium", "high", "xhigh", "max"), name)

    def test_request_kwargs_shape(self) -> None:
        kw = llm_roles.request_kwargs("referee_llm_audit")
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["output_config"], {"effort": "high"})
        self.assertEqual((kw["betas"], kw["fallbacks"]), (["server-side-fallback-2026-07-01"], "default"))
        self.assertNotIn("thinking", kw)  # Opus 5.5 / Fable 5.1: thinking is always on; never send disabled

    def test_haiku_gets_no_server_side_fallback(self) -> None:
        kw = llm_roles.request_kwargs("summarizer")
        self.assertNotIn("fallbacks", kw)
        self.assertNotIn("betas", kw)

    def test_unknown_role(self) -> None:
        with self.assertRaises(KeyError):
            llm_roles.request_kwargs("oracle")

    def test_cost(self) -> None:
        usage = {"input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000, "output_tokens": 100_000}
        self.assertAlmostEqual(llm_roles.cost_usd("claude-opus-5-5", usage), 4.0 + 0.20 + 2.0)


class CostAccountingTest(unittest.TestCase):
    """create() with a fake API call: logs each call and enforces the per-task budget."""

    def test_logs_calls_and_enforces_budget(self) -> None:
        import json
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        fake = SimpleNamespace(model="claude-opus-5-5", stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=100_000, output_tokens=20_000,
                                                     cache_read_input_tokens=0))
        with tempfile.TemporaryDirectory() as tmp:
            costs = Path(tmp) / "costs.jsonl"
            env = {llm_roles.COSTS_ENV: str(costs), llm_roles.TASK_ENV: "t1", llm_roles.BUDGET_ENV: "1.0"}
            with mock.patch.dict(os.environ, env), \
                 mock.patch.object(llm_roles.llm_keys, "call_with_failover", return_value=fake):
                _, usd = llm_roles.create("referee_llm_audit", agent="referee_opus", messages=[])
                self.assertAlmostEqual(usd, 0.8)  # 0.1M x $4 + 0.02M x $20
                row = json.loads(costs.read_text().splitlines()[0])
                self.assertEqual((row["agent"], row["task_id"], row["kind"], row["model"]),
                                 ("referee_opus", "t1", "llm", "claude-opus-5-5"))
                llm_roles.create("referee_llm_audit", messages=[])        # 0.8 spent < 1.0: allowed
                with self.assertRaises(llm_roles.BudgetExceeded):          # 1.6 spent >= 1.0: refused
                    llm_roles.create("referee_llm_audit", messages=[])
            self.assertEqual(len(costs.read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
