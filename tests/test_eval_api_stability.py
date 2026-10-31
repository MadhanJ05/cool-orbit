"""Names other code imports from the eval runners (scripts/run_arm_c.py, the arm C runner). Keep them stable.

If this test fails, you renamed or removed something the arm C runner depends on: update both together.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import inspect
import unittest


class ArmCImportsTest(unittest.TestCase):
    def test_runner_names(self) -> None:
        from coolorbit.eval.runner import (BOARD_TOOL_HELP, Budget, ClaudeCodeExecutor, _parse_stream,  # noqa: F401
                                           contamination, prepare_sandbox)
        self.assertIn("{task_request_id}", BOARD_TOOL_HELP)
        self.assertEqual([f for f in ("usd", "timeout_s", "model", "effort")
                          if f in inspect.signature(Budget).parameters], ["usd", "timeout_s", "model", "effort"])
        for name in ("command", "run"):
            self.assertTrue(callable(getattr(ClaudeCodeExecutor, name)))
        self.assertEqual(_parse_stream('{"type": "result", "total_cost_usd": 0.1}\n')["total_cost_usd"], 0.1)
        self.assertIn("board_root", inspect.signature(prepare_sandbox).parameters)
        self.assertIn("docker", inspect.signature(prepare_sandbox).parameters)

    def test_sandbox_names(self) -> None:
        from coolorbit.eval.sandbox import DEFAULT_TAG, DockerSandbox
        self.assertIsInstance(DEFAULT_TAG, str)
        for name in ("preflight", "run_command", "kill"):
            self.assertTrue(callable(getattr(DockerSandbox, name)))


if __name__ == "__main__":
    unittest.main()
