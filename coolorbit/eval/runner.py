"""Runners for comparison arms A (single agent) and B (worker + checker). See arms.md.

    python -m coolorbit.eval.runner --arm A --run 2026-11-01a --repeat 1 \\
        --budget-usd 2 --timeout-min 45 [--model ...] [--task ID ...] [--dry-run]

For every task: a **fresh board** ``boards/eval/<run>/<arm>-r<repeat>/<task_id>/`` seeded with
that one task, and a fresh **sandbox** working directory *outside the repo*
(``~/coolorbit-eval-work/...`` by default) holding only the ALCHEMI skills and a ``./board``
wrapper (fixed board and agent name). The agent is a headless Claude Code session
(``claude -p``) with the arm's prompt; each session's token use and cost go to the board's
``costs.jsonl`` and its full transcript to ``<board>/transcripts/``.

- Arm A: one ``single_agent`` session per task.
- Arm B: ``worker`` session; if it posted a result, a ``checker`` session on it; if the
  checker's verdict is negative, one worker rerun and one more check. Fixed order, no
  board needs involved.

Limits per session: ``--max-budget-usd`` (Claude Code stops itself) and a wall-clock timeout
(the runner kills it). A task is complete once ``run_record.json`` exists; reruns skip complete
tasks and refuse to touch an incomplete board (delete it yourself if you want to redo it).

**Sandbox.** Real runs use ``--sandbox docker`` (the default; see ``coolorbit/eval/sandbox.py``):
each session is a container that sees only its work dir and its task board's ``artifacts/``, gets
only ``ANTHROPIC_API_KEY``, and the GPU. No home dir, ssh/gh/brev credentials, repo or ``.env``.
``--sandbox none --allow-unsandboxed`` runs sessions as you, with all of that in reach: laptop
development only. Either way the runner scans every transcript for paths such as ``tasks.json``
and records hits in ``run_record.json`` (``contamination``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

from coolorbit import llm_keys, llm_roles
from coolorbit.board import Board
from coolorbit.board._scienceclaw import REPO_ROOT
from coolorbit.board.schema import NEGATIVE_VERDICTS
from coolorbit.eval.metrics import COSTS_FILE
from coolorbit.eval.sandbox import (CALIBRATION_FILE, CONTAINER_CALIBRATION, DEFAULT_TAG, DockerSandbox,
                                    board_wrapper, container_name)
from coolorbit.eval.tasks import Task, TaskSet, load_tasks, seed_board, task_requests

PROMPTS_DIR = Path(__file__).with_name("prompts")
DEFAULT_BOARDS_ROOT = REPO_ROOT / "boards" / "eval"
DEFAULT_WORK_ROOT = Path.home() / "coolorbit-eval-work"
DEFAULT_SKILLS = REPO_ROOT / ".claude" / "skills"
RECORD_FILE = "run_record.json"
# Paths an arm-A/B agent should never read: answers, trap labels, our referees' logic, the brief.
# (data/calibration is NOT here: every arm gets the measured references, see arms.md.)
CONTAMINATION_PATTERNS = ("coolorbit/eval", "tasks.json", "trust_rules", "coolorbit/agents", "CLAUDE.md",
                          "BLIND_TASKS")

BOARD_TOOL_HELP = """
## The board tool
Your working directory has a `./board` command (already set up for this task and for you):
- `./board show`: everything on the board, with ids and open needs.
- `./board get <id>`: one artifact in full.
- `./board schema`: the exact fields of each artifact type.
- `./board post candidate '{{"name": "...", "family": "...", "tier": "trust"}}' --fulfills {task_request_id}:0`
- `./board post property_result '{{...}}' --parent <candidate id>` (fields: candidate, family, tier, property,
  value, units, uncertainty, method {{engine, model, protocol, ensemble, timestep_fs, n_atoms, sim_length_ps}},
  flags, note)
- `./board post run_report '{{"status": "refused", "task": "...", "reason": "..."}}' --parent <candidate id>`
- `./board post check '{{"check_type": "...", "verdict": "...", "note": "..."}}' --parent <result id>`
A payload can also be a file: `./board post property_result @result.json --parent <id>`.
"""


# ------------------------------------------------------------------ executors

@dataclass
class Budget:
    """Limits for one Claude Code session (the runner derives ``usd`` per session from the per-task budget)."""

    usd: float
    timeout_s: float
    model: Optional[str] = None
    effort: Optional[str] = None


ROLE = "arm_baseline"          # configs/llm_roles.json: same model and effort for arms A and B
MIN_SESSION_USD = 0.02         # don't start a session with less than this left of the task budget


def role_settings(path: Optional[str] = None) -> dict:
    """Model, effort and per-task budget for the comparison arms, from configs/llm_roles.json."""
    cfg = llm_roles.load(path)
    r = llm_roles.role(ROLE, path)
    return {"model": r["model"], "effort": r["effort"],
            "task_budget_usd": float(cfg["comparison_budget_usd_per_task_per_arm"])}


def session_caps(arm: str) -> list[bool]:
    """For each possible session of a task, whether it is the last one that could run (may spend all that's left).

    A: one session. B: worker, checker, worker rerun, checker. Every other session may spend at most half of
    what's left, so a pipeline can't starve its checker and never exceeds the task budget.
    """
    return [True] if arm == "A" else [False, False, False, True]


@dataclass
class SessionResult:
    exit_code: Optional[int]
    timed_out: bool
    wall_s: float
    input_tokens: Optional[int] = None          # all input incl. cache reads/writes
    output_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    usd: Optional[float] = None
    num_turns: Optional[int] = None
    model: Optional[str] = None
    error: Optional[str] = None
    transcript: Optional[str] = None
    key_name: Optional[str] = None              # which shared key (by name, never the key) the session used
    uncached_input_tokens: Optional[int] = None
    model_usage: Optional[dict] = None          # per model: input/output/cache_read/cache_creation tokens
    usd_reported: Optional[float] = None        # Claude Code's own total_cost_usd (prices cache writes)
    unpriced_models: Optional[list] = None      # models without a price in llm_roles.json (cross-check only)
    usd_check: Optional[float] = None           # llm_roles.cost_usd per model: cross-check, never charged
    usd_source: Optional[str] = None            # what `usd` (the charged figure) is: reported | session_cap


class Executor(Protocol):
    def run(self, prompt: str, *, cwd: Path, budget: Budget, transcript_path: Path,
            board_root: Path) -> SessionResult: ...


def _parse_stream(text: str) -> dict:
    """The last ``{"type": "result"}`` line of Claude Code's stream-json output (or {})."""
    final = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            final = obj
    return final


def _usage_fields(final: dict) -> dict:
    u = final.get("usage") or {}
    def num(d, *keys):
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return None
    raw = num(u, "input_tokens")
    parts = [raw, num(u, "cache_read_input_tokens"), num(u, "cache_creation_input_tokens")]
    per_model = {}
    for model, mu in (final.get("modelUsage") or {}).items():
        if isinstance(mu, dict):
            per_model[model] = {
                "input_tokens": num(mu, "inputTokens", "input_tokens") or 0,
                "output_tokens": num(mu, "outputTokens", "output_tokens") or 0,
                "cache_read_input_tokens": num(mu, "cacheReadInputTokens", "cache_read_input_tokens") or 0,
                "cache_creation_input_tokens": num(mu, "cacheCreationInputTokens", "cache_creation_input_tokens") or 0,
                "reported_usd": mu.get("costUSD"),
            }
    return {
        "input_tokens": sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None,
        "uncached_input_tokens": raw,
        "output_tokens": num(u, "output_tokens"),
        "cache_read_input_tokens": parts[1],
        "cache_creation_input_tokens": parts[2],
        "usd_reported": final.get("total_cost_usd"),
        "usd": final.get("total_cost_usd"),  # callers that use the executor directly (arm C) read this
        "usd_source": "reported" if final.get("total_cost_usd") is not None else None,
        "num_turns": final.get("num_turns"),
        "model": ",".join(per_model) or None,
        "model_usage": per_model or None,
    }


def price_session(res: "SessionResult", default_model: Optional[str], cap_usd: Optional[float] = None) -> None:
    """Set the charged figure ``res.usd`` = Claude Code's ``total_cost_usd`` (it prices cache writes).

    If the session left no report (killed at the wall-clock limit, or died before its final event), charge
    the session's cap: ``--max-budget-usd`` means it can't have spent more, and charging 0 would let a task
    overspend. ``res.usd_check`` = llm_roles.cost_usd per model, kept only as a cross-check.
    """
    prices = llm_roles.load()["prices_per_mtok"]
    check, unpriced = None, []
    if res.model_usage:
        check = 0.0
        for model, mu in res.model_usage.items():
            if model in prices:
                check += llm_roles.cost_usd(model, mu)
            else:
                unpriced.append(model)
    elif res.uncached_input_tokens is not None and default_model in prices:
        check = llm_roles.cost_usd(default_model, {"input_tokens": res.uncached_input_tokens,
                                                   "cache_read_input_tokens": res.cache_read_input_tokens or 0,
                                                   "output_tokens": res.output_tokens or 0})
    res.usd_check, res.unpriced_models = check, unpriced or None
    if res.usd_reported is not None:
        res.usd, res.usd_source = float(res.usd_reported), "reported"
    elif cap_usd is not None:
        res.usd, res.usd_source = float(cap_usd), "session_cap"
    else:
        res.usd_source = "unknown" if res.usd is None else res.usd_source


class ClaudeCodeExecutor:
    """Runs one headless Claude Code session: ``claude -p <prompt>`` in the sandbox."""

    def __init__(self, claude_bin: str = "claude", extra_args: tuple[str, ...] = (),
                 sandbox: Optional[DockerSandbox] = None,
                 env_provider: Optional[Callable[[], dict]] = None) -> None:
        self.claude_bin = "claude" if sandbox else claude_bin  # inside the image it's on PATH
        self.extra_args = tuple(extra_args)
        self.sandbox = sandbox
        # Default: llm_keys.subprocess_env() -> ANTHROPIC_API_KEY = the active shared key (primary until out of
        # credit, then backup). docker run's `-e ANTHROPIC_API_KEY` (by name) passes exactly that into the container.
        self.env_provider = env_provider or llm_keys.subprocess_env

    def command(self, prompt: str, budget: Budget) -> list[str]:
        cmd = [self.claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--max-budget-usd", f"{budget.usd:g}",
               "--setting-sources", "project",          # only the sandbox's .claude/ (ALCHEMI skills)
               "--permission-mode", "bypassPermissions",  # headless; the sandbox is the boundary
               "--no-session-persistence"]
        if budget.model:
            cmd += ["--model", budget.model]
        if budget.effort:
            cmd += ["--effort", budget.effort]
        return cmd + list(self.extra_args)

    def full_command(self, prompt: str, budget: Budget, *, cwd: Path, board_root: Path,
                     name: str) -> list[str]:
        inner = self.command(prompt, budget)
        if self.sandbox is None:
            return inner
        return self.sandbox.run_command(inner, work_dir=cwd, board_root=board_root, name=name)

    def run(self, prompt: str, *, cwd: Path, budget: Budget, transcript_path: Path,
            board_root: Path) -> SessionResult:
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        name = container_name(Path(board_root).name, Path(cwd).name)
        cmd = self.full_command(prompt, budget, cwd=cwd, board_root=board_root, name=name)
        start = time.monotonic()
        timed_out, code, error = False, None, None
        with open(transcript_path, "w", encoding="utf-8") as log:
            try:
                proc = subprocess.run(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, timeout=budget.timeout_s, env=self.env_provider())
                code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out, error = True, f"killed after {budget.timeout_s:g} s wall-clock limit"
                if self.sandbox is not None:
                    self.sandbox.kill(name)  # the client dying does not stop the container
            except OSError as exc:
                error = f"could not start {self.claude_bin}: {exc}"
        wall = time.monotonic() - start
        text = transcript_path.read_text(encoding="utf-8", errors="replace")
        final = _parse_stream(text)
        if final.get("is_error") and not error:
            error = str(final.get("subtype") or "session reported an error")
        return SessionResult(exit_code=code, timed_out=timed_out, wall_s=wall, error=error, transcript=text,
                             **_usage_fields(final))


# ------------------------------------------------------------------ sandbox + prompts

def prepare_sandbox(work_dir: Path, board_root: Path, agent: str, skills_dir: Optional[Path] = DEFAULT_SKILLS,
                    python: str = sys.executable, docker: bool = False,
                    calibration: Optional[Path] = CALIBRATION_FILE) -> Path:
    """Fresh work dir with a ./board wrapper. ``docker=True``: container paths, skills come from the image and
    the calibration file is mounted; otherwise both are copied in (``calibration_path_for`` gives the path)."""
    try:
        Path(work_dir).resolve().relative_to(REPO_ROOT)
        inside = True
    except ValueError:
        inside = False
    if inside:
        raise ValueError(f"sandbox {work_dir} is inside the repo; agents must not start next to tasks.json")
    if work_dir.exists():
        raise FileExistsError(f"sandbox {work_dir} already exists; use a fresh --work-root or delete it yourself")
    work_dir.mkdir(parents=True)
    wrapper = work_dir / "board"
    if docker:
        wrapper.write_text(board_wrapper(agent), encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return work_dir
    if skills_dir and Path(skills_dir).is_dir():
        shutil.copytree(skills_dir, work_dir / ".claude" / "skills")
    if calibration is not None:
        (work_dir / "calibration").mkdir()
        shutil.copy2(calibration, work_dir / "calibration" / "tier1.json")
    wrapper.write_text(
        "#!/bin/sh\n"
        f"COOLORBIT_BOARD='{board_root.resolve()}' COOLORBIT_AGENT='{agent}' PYTHONPATH='{REPO_ROOT}' "
        f"exec '{python}' -m coolorbit.eval.board_tool \"$@\"\n", encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return work_dir


def calibration_path_for(sandbox: Path, docker: bool) -> str:
    """Where the agent finds the measured references (same file in both modes)."""
    return CONTAINER_CALIBRATION if docker else str((Path(sandbox) / "calibration" / "tier1.json").resolve())


_COMMENT = re.compile(r"<!--.*?-->", re.S)


def render_prompt(name: str, **fields: str) -> str:
    text = _COMMENT.sub("", (PROMPTS_DIR / name).read_text(encoding="utf-8"))
    text = re.sub(r"^# .*\n", "", text, count=1).strip()  # drop the file's title line
    return (text + "\n" + BOARD_TOOL_HELP).format(**fields).strip() + "\n"


def contamination(transcript: Optional[str]) -> list[str]:
    if not transcript:
        return []
    return sorted({p for p in CONTAMINATION_PATTERNS if p in transcript})


# ------------------------------------------------------------------ runs

class TaskBudgetSpent(Exception):
    """Less than MIN_SESSION_USD of the task budget is left, so the next session is not started."""


class CreditExhausted(Exception):
    """A session died because the shared key it used ran out of credit (an infrastructure failure)."""

    def __init__(self, key_name: Optional[str]) -> None:
        super().__init__(f"{key_name or 'the active key'} is out of credit")
        self.key_name = key_name


def exhaustion_text(res: SessionResult) -> str:
    """Text to test for an out-of-credit error: only a FAILED session's final result, error and stderr lines.

    Never the whole transcript: an agent reading a web page that says "credit balance is too low" must not flip
    every runner to the backup key.
    """
    if not res.error and res.exit_code in (0, None):
        return ""
    final = _parse_stream(res.transcript or "")
    stderr = [l for l in (res.transcript or "").splitlines() if l.strip() and not l.lstrip().startswith("{")]
    return " ".join([str(final.get("result") or ""), res.error or "", *stderr[-20:]])


@dataclass
class SessionRecord:
    role: str
    agent: str
    sandbox: str
    transcript: str
    result: dict
    contamination: list[str] = field(default_factory=list)


def _log_cost(board_root: Path, task_id: str, agent: str, res: SessionResult) -> None:
    row = {"agent": agent, "task_id": task_id, "kind": "llm", "model": res.model, "key_name": res.key_name,
           "input_tokens": res.input_tokens, "uncached_input_tokens": res.uncached_input_tokens,
           "output_tokens": res.output_tokens, "cache_read_input_tokens": res.cache_read_input_tokens,
           "cache_creation_input_tokens": res.cache_creation_input_tokens,
           "gpu_hours": None, "usd": res.usd, "usd_source": res.usd_source, "usd_reported": res.usd_reported,
           "usd_check_cost_usd": res.usd_check, "unpriced_models": res.unpriced_models,
           "wall_s": round(res.wall_s, 3), "timed_out": res.timed_out,
           "note": "usd (charged) = Claude Code total_cost_usd, or the session cap if no report; "
                   "usd_check_cost_usd = llm_roles.cost_usd cross-check (no cache-write price); "
                   "gpu_hours unknown: GPU jobs run inside the session, see wall_s"}
    with open(board_root / COSTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


class ArmRunner:
    def __init__(self, arm: str, run_id: str, repeat: int, executor: Executor, budget: Budget, *,
                 boards_root: Path = DEFAULT_BOARDS_ROOT, work_root: Path = DEFAULT_WORK_ROOT,
                 skills_dir: Optional[Path] = DEFAULT_SKILLS, log: Callable[[str], None] = print,
                 docker: bool = False, keys=llm_keys) -> None:
        if arm not in ("A", "B"):
            raise ValueError("only arms A and B have runners; arm C is the collective itself")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id):
            raise ValueError(f"bad run id {run_id!r}")
        self.arm, self.run_id, self.repeat = arm, run_id, repeat
        self.executor, self.budget = executor, budget
        self.repeat_dir = Path(boards_root) / run_id / f"{arm}-r{repeat}"
        self.work_dir = Path(work_root) / run_id / f"{arm}-r{repeat}"
        self.skills_dir, self.log, self.docker = skills_dir, log, docker
        self.keys = keys  # coolorbit.llm_keys (None: no shared-key handling, for fake-LLM tests)
        self._try = 1     # infrastructure retry number of the task being run (1 = first try)
        self._spent, self._session_no = 0.0, 0   # per task attempt: dollars spent, sessions started

    # one session ------------------------------------------------------------------
    def _session(self, task: Task, board_root: Path, role: str, agent: str, prompt_file: str,
                 attempt: int, addendum: str = "", **fields: str) -> tuple[SessionResult, SessionRecord]:
        if self._remaining() < MIN_SESSION_USD:
            raise TaskBudgetSpent(f"${self._remaining():.3f} left of ${self.budget.usd:g}")
        task_dir = task.id if self._try == 1 else f"{task.id}.try{self._try}"
        sandbox = prepare_sandbox(self.work_dir / task_dir / f"{role}-{attempt}", board_root, agent, self.skills_dir,
                                  docker=self.docker)
        prompt = render_prompt(prompt_file, task_prompt=task.prompt, board_path="./board (this task's board)",
                               calibration_path=calibration_path_for(sandbox, self.docker), **fields)
        if addendum:
            prompt += "\n" + addendum.strip() + "\n"
        (sandbox / "PROMPT.md").write_text(prompt, encoding="utf-8")  # evidence of exactly what it was told
        transcript = board_root / "transcripts" / f"{role}-{attempt}.jsonl"
        self.log(f"  [{task.id}] {role} attempt {attempt}: running in {sandbox}")
        key_name = self.keys.active_key_name() if self.keys else None
        session_budget = self._next_session_budget()
        self.log(f"  [{task.id}] {role}: cap ${session_budget.usd:.3f} of ${self._remaining():.3f} left "
                 f"(task budget ${self.budget.usd:g})")
        res = self.executor.run(prompt, cwd=sandbox, budget=session_budget, transcript_path=transcript,
                                board_root=board_root)
        res.key_name = key_name
        price_session(res, self.budget.model, cap_usd=session_budget.usd)
        self._spent += res.usd or 0.0  # Claude Code's total_cost_usd, or the session cap if it left no report
        _log_cost(board_root, task.id, agent, res)  # dollars and tokens per session, failed ones too
        if self.keys and self.keys.is_credit_exhausted(exhaustion_text(res)):
            raise CreditExhausted(key_name)
        hits = contamination(res.transcript)
        if hits:
            self.log(f"  [{task.id}] WARNING: transcript mentions {hits} (possible contamination)")
        data = asdict(res)
        data.pop("transcript")
        return res, SessionRecord(role, agent, str(sandbox), str(transcript.relative_to(board_root)), data, hits)

    def _remaining(self) -> float:
        return max(0.0, self.budget.usd - self._spent)

    def _next_session_budget(self) -> Budget:
        lasts = session_caps(self.arm)
        last = self._session_no >= len(lasts) - 1 or lasts[self._session_no]
        self._session_no += 1
        cap = self._remaining() if last else self._remaining() / 2
        return Budget(usd=round(cap, 4), timeout_s=self.budget.timeout_s, model=self.budget.model,
                      effort=self.budget.effort)

    def _latest_result(self, board: Board, agent: str) -> Optional[str]:
        snap = board.snapshot()
        mine = [a for a in snap.of_type("property_result") if a.producer_agent == agent]
        ids = {a.artifact_id for a in mine}
        live = [a for a in mine if not any(k.artifact_id in ids for k in snap.children.get(a.artifact_id, []))]
        return live[-1].artifact_id if live else None

    def _check_verdict(self, board: Board, result_id: str) -> Optional[str]:
        checks = [k for k in board.snapshot().children.get(result_id, [])
                  if k.artifact_type == "check" and k.producer_agent == "checker"]
        return checks[-1].payload["verdict"] if checks else None

    # one task ---------------------------------------------------------------------
    def run_task(self, task: Task) -> dict:
        board_root = self.repeat_dir / task.id
        record_path = board_root / RECORD_FILE
        if record_path.exists():
            self.log(f"  [{task.id}] already complete, skipping")
            return json.loads(record_path.read_text(encoding="utf-8"))
        if board_root.exists():
            raise FileExistsError(f"{board_root} exists but has no {RECORD_FILE} (an interrupted attempt). "
                                  "Inspect it, then delete it yourself to redo this task.")
        infra_failed: list[str] = []
        for self._try in (1, 2):
            try:
                return self._attempt(task, board_root, record_path, infra_failed)
            except CreditExhausted as exc:
                failed = self.repeat_dir / "_infra_failed" / f"{task.id}.try{self._try}"
                failed.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(board_root), str(failed))  # out of the scored folder, kept as evidence
                (failed / "INFRA_FAILURE.json").write_text(json.dumps({
                    "task_id": task.id, "reason": "shared Anthropic key out of credit mid-task; not scored",
                    "key_name": exc.key_name, "at": datetime.now(timezone.utc).isoformat()}, indent=1),
                    encoding="utf-8")
                infra_failed.append(str(failed.relative_to(self.repeat_dir)))
                if self.keys is None or exc.key_name != self.keys.PRIMARY:
                    raise llm_keys.NoUsableKey(f"{exc.key_name} is out of credit too; stopping the run "
                                               f"(failed attempt kept in {failed})") from exc
                self.keys.mark_primary_exhausted("claude -p session")
                self.log(f"  [{task.id}] primary key out of credit: attempt moved to {failed}; "
                         "rerunning the task on the backup key")
        raise llm_keys.NoUsableKey(f"{task.id}: out of credit twice")  # unreachable in practice

    def _attempt(self, task: Task, board_root: Path, record_path: Path, infra_failed: list[str]) -> dict:
        board = Board(board_root)
        req_id = seed_board(board, TaskSet(version=1, frozen=False, tasks=[task]))[task.id]
        started = datetime.now(timezone.utc).isoformat()
        sessions: list[SessionRecord] = []
        self._spent, self._session_no = 0.0, 0
        budget_note = None

        try:
            if self.arm == "A":
                _, rec = self._session(task, board_root, "single_agent", "single_agent", "arm_a_single_agent.md", 1,
                                       task_request_id=req_id)
                sessions.append(rec)
            else:
                result_id, verdict = None, None
                for attempt in (1, 2):
                    addendum = "" if attempt == 1 else (
                        f"## This is your one rerun\nThe checker's verdict on your result {result_id} was '{verdict}'. "
                        f"Read its check (`./board show`, `./board get <id>`). If you agree, post a corrected "
                        f"property_result with `--parent {result_id}`, or a run_report if the task should be refused. "
                        "If you disagree, explain why in a run_report and post nothing else.")
                    _, rec = self._session(task, board_root, "worker", "worker", "arm_b_worker.md", attempt,
                                           addendum=addendum, task_request_id=req_id)
                    sessions.append(rec)
                    latest = self._latest_result(board, "worker")
                    if latest is None or latest == result_id:
                        self.log(f"  [{task.id}] worker posted no new result; pipeline ends")
                        break
                    result_id = latest
                    _, rec = self._session(task, board_root, "checker", "checker", "arm_b_checker.md", attempt,
                                           task_request_id=req_id, result_id=result_id)
                    sessions.append(rec)
                    verdict = self._check_verdict(board, result_id)
                    if verdict not in NEGATIVE_VERDICTS:
                        break
                    if attempt == 1:
                        self.log(f"  [{task.id}] checker said '{verdict}': one worker rerun")

        except TaskBudgetSpent as exc:
            budget_note = f"task budget used up before the next session ({exc})"
            self.log(f"  [{task.id}] {budget_note}")

        record = {"run": self.run_id, "arm": self.arm, "repeat": self.repeat, "task_id": task.id,
                  "task_request_id": req_id, "started": started, "finished": datetime.now(timezone.utc).isoformat(),
                  "budget": {"task_budget_usd": self.budget.usd, "spent_usd": round(self._spent, 4),
                             "stopped_by_budget": budget_note, "timeout_s": self.budget.timeout_s,
                             "model": self.budget.model, "effort": self.budget.effort, "role": ROLE},
                  "sandbox": "docker" if self.docker else "none",
                  "sessions": [asdict(s) for s in sessions],
                  "infra_failed_attempts": list(infra_failed),
                  "contamination": sorted({h for s in sessions for h in s.contamination})}
        record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        return record

    def run(self, task_set: TaskSet, task_ids: Optional[list[str]] = None) -> list[dict]:
        tasks = task_set.tasks if not task_ids else [task_set.by_id()[t] for t in task_ids]
        if self.keys is not None:
            self.log(f"active shared key: {self.keys.active_key_name()}")  # raises NoUsableKey if none
        sandbox = getattr(self.executor, "sandbox", None)
        if sandbox is not None:
            sandbox.preflight()
        self.log(f"arm {self.arm}, run {self.run_id}, repeat {self.repeat}: {len(tasks)} task(s) -> {self.repeat_dir}")
        return [self.run_task(t) for t in tasks]


# ------------------------------------------------------------------ CLI

def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m coolorbit.eval.runner", description="Run comparison arm A or B.")
    ap.add_argument("--arm", required=True, choices=["A", "B"])
    ap.add_argument("--run", required=True, help="run id, e.g. 2026-11-01a (boards/eval/<run>/<arm>-r<repeat>/)")
    ap.add_argument("--repeat", type=int, required=True)
    ap.add_argument("--budget-usd", type=float, default=None,
                    help="per-task budget for this arm (default: comparison_budget_usd_per_task_per_arm in "
                         "configs/llm_roles.json; arm B splits it between worker and checker)")
    ap.add_argument("--timeout-min", type=float, required=True, help="wall-clock limit per session")
    ap.add_argument("--model", default=None, help="default: the arm_baseline role in configs/llm_roles.json")
    ap.add_argument("--effort", default=None, help="default: the arm_baseline role's effort")
    ap.add_argument("--task", action="append", help="only these task ids (repeatable); default: all")
    ap.add_argument("--boards-root", default=str(DEFAULT_BOARDS_ROOT))
    ap.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT), help="sandboxes; must be outside the repo")
    ap.add_argument("--skills", default=str(DEFAULT_SKILLS), help="ALCHEMI skills copied into each sandbox")
    ap.add_argument("--claude-bin", default="claude", help="only for --sandbox none")
    ap.add_argument("--sandbox", choices=["docker", "none"], default="docker",
                    help="docker (default, required for real runs) or none (laptop development only)")
    ap.add_argument("--allow-unsandboxed", action="store_true",
                    help="required with --sandbox none: sessions then run as you, with your credentials in reach")
    ap.add_argument("--image", default=DEFAULT_TAG, help="sandbox image tag")
    ap.add_argument("--no-gpus", action="store_true", help="omit --gpus all (laptop plumbing tests only)")
    ap.add_argument("--memory", help="container memory limit, e.g. 64g")
    ap.add_argument("--tasks-file", help="task set JSON (default: coolorbit/eval/tasks.json)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and the first prompt; run nothing")
    args = ap.parse_args(argv)

    if args.sandbox == "none" and not args.allow_unsandboxed:
        ap.error("--sandbox none lets the agent reach your credentials (gh, brev, .env, ssh); "
                 "add --allow-unsandboxed only for laptop development")
    task_set = load_tasks(args.tasks_file)
    if not task_set.frozen:
        print("WARNING: the task set is not frozen (not pre-registered). Fine for smoke tests, not for the comparison.")
    cfg = role_settings()
    for flag, value, default in (("--budget-usd", args.budget_usd, cfg["task_budget_usd"]),
                                 ("--model", args.model, cfg["model"]), ("--effort", args.effort, cfg["effort"])):
        if value is not None and value != default:
            print(f"WARNING: {flag} {value} differs from configs/llm_roles.json ({default}); the arms are only "
                  "comparable if every arm uses the config values.")
    budget = Budget(usd=args.budget_usd if args.budget_usd is not None else cfg["task_budget_usd"],
                    timeout_s=args.timeout_min * 60, model=args.model or cfg["model"],
                    effort=args.effort or cfg["effort"])
    docker = args.sandbox == "docker"
    sandbox = DockerSandbox(args.image, gpus=not args.no_gpus, memory=args.memory) if docker else None
    executor = ClaudeCodeExecutor(args.claude_bin, sandbox=sandbox)
    runner = ArmRunner(args.arm, args.run, args.repeat, executor, budget, boards_root=Path(args.boards_root),
                       work_root=Path(args.work_root), skills_dir=Path(args.skills) if args.skills else None,
                       docker=docker)
    if args.dry_run:
        tasks = task_set.tasks if not args.task else [task_set.by_id()[t] for t in args.task]
        prompt_file = "arm_a_single_agent.md" if args.arm == "A" else "arm_b_worker.md"
        print(f"would run {len(tasks)} task(s) into {runner.repeat_dir}, sandboxes under {runner.work_dir}")
        print(f"model {budget.model}, effort {budget.effort}, ${budget.usd:g} per task "
              + ("(one session)" if args.arm == "A" else "(each session at most half of what's left; the last "
                 "possible one may use the rest)"))
        try:
            print("active shared key:", llm_keys.active_key_name())
        except llm_keys.NoUsableKey as exc:
            print(f"active shared key: NONE ({exc})")
        print("sandbox:", f"docker image {args.image}" + ("" if sandbox and sandbox.gpus else " (no GPU)")
              if docker else "NONE (unsandboxed)")
        cmd = executor.command("<prompt>", Budget(usd=budget.usd, timeout_s=budget.timeout_s,
                                                  model=budget.model, effort=budget.effort))
        if sandbox:
            cmd = sandbox.run_command(cmd, work_dir=runner.work_dir / "<task>" / "<role>-<n>",
                                      board_root=runner.repeat_dir / "<task>", name="coolorbit-<...>")
        print("command:", " ".join(cmd))
        print("\n--- first prompt ---\n" + render_prompt(prompt_file, task_prompt=tasks[0].prompt,
              calibration_path=CONTAINER_CALIBRATION if docker else "<sandbox>/calibration/tier1.json",
              board_path="./board (this task's board)", task_request_id="<request id>"))
        return
    records = runner.run(task_set, args.task)
    bad = [r["task_id"] for r in records if r["contamination"]]
    print(f"done: {len(records)} task(s) in {runner.repeat_dir}")
    if bad:
        print(f"WARNING: possible contamination in {bad}; see run_record.json")
    print(f"score: python -m coolorbit.eval.metrics --repeat {runner.repeat_dir}")


if __name__ == "__main__":
    main()
