"""Shared SYNTHETIC fixtures for the eval-runner tests (fake LLM agents posting through ./board).

Not a test module (no test_ prefix). Test files import it as ``from eval_runner_helpers import ...`` after
putting their own folder on sys.path, so it works with ``discover -s tests`` and with
``python -m unittest tests.<module>``, and can't be shadowed by another package named ``tests``.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from coolorbit.eval.runner import Budget, SessionResult

RULES = {"frozen": False, "independent_check_types": ["second_mlip", "classical_md"], "rejection_min_referees": 2,
         "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 20.0}, "tier1": {"abs": 25.0}}}}
SINGLE = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "single-phase heating"}
COEX = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence", "ensemble": "NPT"}
BUDGET = Budget(usd=0.5, timeout_s=30)


def board_cmd(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(cwd / "board"), *args], capture_output=True, text=True)


def post(cwd: Path, *args: str) -> str:
    out = board_cmd(cwd, "post", *args)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()[-1]


class FakeExecutor:
    """Scripted SYNTHETIC agents. ``behavior[role]`` gets (cwd, prompt, attempt) and posts via ./board."""

    def __init__(self, behavior: dict, transcript_text: str = '{"type": "result"}\n') -> None:
        self.behavior, self.calls, self.transcript_text = behavior, [], transcript_text

    def run(self, prompt, *, cwd, budget, transcript_path, board_root):
        role, attempt = cwd.name.rsplit("-", 1)
        self.calls.append((cwd.parent.name, role, int(attempt)))
        self.behavior[role](cwd, prompt, int(attempt))
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(self.transcript_text)
        return SessionResult(exit_code=0, timed_out=False, wall_s=0.01, input_tokens=1000, output_tokens=100,
                             usd_reported=0.01, num_turns=3, model="fake-llm", transcript=self.transcript_text)


def request_id(prompt: str) -> str:
    return re.search(r"--fulfills (\S+):0", prompt).group(1)


def naive_answer(protocol=SINGLE, value=1100.0):
    """SYNTHETIC naive agent: answers every task with one confident number."""
    def act(cwd, prompt, attempt):
        task = re.search(r"## Your task\n(.*)\n", prompt).group(1)
        cand = post(cwd, "candidate", json.dumps({"name": "SYNTHETIC", "family": "test", "tier": "discovery"}),
                    "--fulfills", f"{request_id(prompt)}:0")
        prop = "melting_point" if "melting" in task else "self_diffusion"
        units = "K" if prop == "melting_point" else "cm2/s"
        post(cwd, "property_result", json.dumps({
            "candidate": "SYNTHETIC", "family": "test", "tier": "discovery", "property": prop,
            "value": value if prop == "melting_point" else 1e-9, "units": units, "method": protocol,
            "note": "SYNTHETIC fake-LLM output"}), "--parent", cand)
    return act
