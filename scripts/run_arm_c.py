"""Arm C runner: our decentralized collective on the comparison tasks (CLAUDE.md section 13).

Same board layout, budget and records as arms A/B (Teammate 2's coolorbit/eval/runner.py), so
``python -m coolorbit.eval.metrics --repeat boards/eval/<run>/C-r<k>`` scores all arms identically:

    boards/eval/<run>/C-r<k>/<task_id>/        one fresh board per task, seeded with seed_board()
        costs.jsonl  run_record.json  transcripts/  logs/

Who works (no boss: every agent only reads the board, claims needs and posts):
  premise checker, Scouts (eval-only), rule referee, Librarian      Teammate 1's agents
  Simulator (melting point / latent heat, real ALCHEMI on the GPU)   coolorbit/sim/simulator_agent.py
  worker_cc: the SAME sandboxed Claude Code worker arm A uses (same model, effort, tools, NVIDIA guidance,
             measured-reference file). It takes what no specialist owns: requested properties the Simulator
             doesn't compute (EOS, adsorption, diffusion, classical-MD reroutes), jobs the Simulator can't set
             up (no structure on file), tasks no Scout could turn into a request, and reruns of its own results
             when a referee fails them. It never starts work the premise checker refused.
So arm C = arm A's worker + a decentralized checking team; the comparison isolates what the team adds.

Borrowed from Karpathy's autoresearch: a fixed budget per experiment (here per task: $ of LLM spend across all
agents, enforced through coolorbit/llm_roles.py, plus the same wall clock as the other arms), an append-only
results.tsv to read in the morning, and an unattended, resumable loop that never deletes a board (a task is
done once run_record.json exists). Like arms A/B, an attempt that dies because the shared key ran out of credit
is moved to ``_infra_failed/`` (kept, not scored) and the task reruns once on the backup key.

    python scripts/run_arm_c.py --run 2026-11-01a --repeat 1 [--task ID ...] [--timeout-min 60] [--dry-run]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from coolorbit import llm_keys, llm_roles  # noqa: E402
from coolorbit.agents.referee_rules import rerun_depth  # noqa: E402
from coolorbit.board import Board, BoardSnapshot, OpenNeed  # noqa: E402
from coolorbit.board._scienceclaw import Artifact  # noqa: E402
from coolorbit.board.schema import NEGATIVE_VERDICTS  # noqa: E402
from coolorbit.eval.tasks import HARNESS_AGENT, Task, TaskSet, load_tasks, seed_board  # noqa: E402
from coolorbit.sim.simulator_agent import (JobError, SimulatorAgent, build_job, find_rerun_target,  # noqa: E402
                                           subprocess_runner)

ARM = "C"
WORKER = "worker_cc"
PREMISE_AGENT = "referee_premise"
MAX_WORKER_RERUNS = 2      # same lineage limit the rule referee applies to Simulator reruns
MIN_SESSION_USD = 0.05     # below this a Claude Code session can't do anything useful
CAL_TOKEN = "<<CALIBRATION_PATH>>"  # filled per session (container path or the sandbox copy)
PROMPT_FILE = REPO_ROOT / "scripts" / "prompts" / "arm_c_worker.md"
AGENTS_DIR = REPO_ROOT / "coolorbit" / "agents"
DEFAULT_BOARDS_ROOT = REPO_ROOT / "boards" / "eval"
DEFAULT_WORK_ROOT = Path.home() / "coolorbit-eval-work"
RECORD_FILE = "run_record.json"
COSTS_FILE = "costs.jsonl"

SessionFn = Callable[[str, int, float, float], dict]  # (prompt, attempt, usd cap, timeout s) -> session record


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- fairness gate


def reads_eval_tasks(src: str) -> bool:
    """True if Python code (not its docstrings or comments) imports the eval package or loads the task set."""
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("coolorbit.eval"):
            return True
        if isinstance(node, ast.Import) and any(a.name.startswith("coolorbit.eval") for a in node.names):
            return True
        if (isinstance(node, ast.Name) and node.id == "load_tasks") or (
                isinstance(node, ast.Attribute) and node.attr == "load_tasks"):
            return True
        if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                and any(s in node.value for s in ("tasks.json", "coolorbit.eval", "coolorbit/eval"))):
            return True
    return False


def known_contamination(agents_dir: Path = AGENTS_DIR) -> list[str]:
    """Fairness problems in arm C's own agents that would invalidate a real comparison run (read from code)."""
    return [f"coolorbit/agents/{p.name} reads the eval task set (coolorbit/eval/tasks.json); arms A and B only "
            "see the task prompt" for p in sorted(Path(agents_dir).glob("*.py"))
            if reads_eval_tasks(p.read_text(encoding="utf-8"))]


# --------------------------------------------------------------------------- the specialist Simulator


class ArmCSimulator(SimulatorAgent):
    """The GB10 Simulator, except that it leaves jobs it can't set up (no structure on file, missing inputs) on
    the board for the generalist worker instead of refusing them. Physics refusals (model out of its training
    domain, with a reroute to classical MD) stay its call."""

    def owns(self, snap: BoardSnapshot, on: OpenNeed) -> bool:
        try:
            job = build_job(snap, on)
        except JobError as err:
            return err.suggest_need is not None
        return job is not None and self.min_minutes <= job.estimate_minutes() < self.max_minutes

    def pending(self):
        return [(on, job) for on, job in super().pending()
                if not (isinstance(job, JobError) and job.suggest_need is None)]


def bounded_runner(inner: Callable, deadline: float, jobs: list[dict]) -> Callable:
    """Wrap a Simulator runner: never run past the task's wall clock, and log every GPU job's wall time."""

    def run(job, out_dir: Path, timeout_s: float):
        t0, status = time.time(), "error"
        try:
            result, settings, run_dir = inner(job, out_dir, max(1.0, min(timeout_s, deadline - time.time())))
            status = result.get("status", "unknown")
            return result, settings, run_dir
        finally:
            jobs.append({"out_dir": str(out_dir), "task": job.task, "status": status,
                         "wall_s": round(time.time() - t0, 1)})

    return run


# --------------------------------------------------------------------------- the generalist worker


def ancestor(snap: BoardSnapshot, art: Artifact, keep: Callable[[Artifact], bool]) -> Optional[Artifact]:
    """The nearest artifact (itself included) on the lineage that satisfies ``keep``."""
    queue, seen = [art], {art.artifact_id}
    while queue:
        cur = queue.pop(0)
        if keep(cur):
            return cur
        for pid in cur.parent_artifact_ids:
            if pid in snap.artifacts and pid not in seen:
                seen.add(pid)
                queue.append(snap.artifacts[pid])
    return None


def nearest(snap: BoardSnapshot, art: Artifact, artifact_type: str) -> Optional[Artifact]:
    return ancestor(snap, art, lambda a: a.artifact_type == artifact_type)


def premise_verdict(snap: BoardSnapshot, art: Artifact, need_index: int) -> Optional[str]:
    tag = f"[need {need_index}]"
    for k in snap.children.get(art.artifact_id, []):
        if (k.artifact_type == "check" and k.producer_agent == PREMISE_AGENT
                and k.payload.get("check_type") == "premise_check" and k.payload.get("note", "").startswith(tag)):
            return k.payload["verdict"]
    return None


def board_tool_help(request_id: str) -> str:
    try:
        from coolorbit.eval.runner import BOARD_TOOL_HELP  # Teammate 2's text, so arm C's worker reads the same
    except ImportError:
        return "\nRun `./board --help` for the board tool's commands.\n"
    return BOARD_TOOL_HELP.format(task_request_id=request_id)


class WorkerDispatcher:
    """worker_cc's board loop: picks up work no specialist owns and runs one Claude Code session per item."""

    def __init__(self, board: Board, task: Task, request_id: str, session_fn: SessionFn, *,
                 sim: Optional[ArmCSimulator], deadline: float, usd_left: Callable[[], float],
                 worker_usd: float, wait_for_premise: bool = True) -> None:
        self.board, self.task, self.request_id, self.session_fn = board, task, request_id, session_fn
        self.sim, self.deadline, self.usd_left, self.worker_usd = sim, deadline, usd_left, worker_usd
        self.wait_for_premise = wait_for_premise
        self.name = WORKER
        self.done: set[tuple] = set()
        self.sessions: list[dict] = []
        self.log_path = board.root / "logs" / f"{WORKER}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **data) -> None:
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": utc(), "agent": WORKER, "event": event, **data}, default=str) + "\n")

    def _premise(self, snap: BoardSnapshot, art: Artifact, need_index: Optional[int] = None) -> Optional[bool]:
        """None = wait for the premise checker; False = it refused (task or request); True = go."""
        if not self.wait_for_premise:
            return True
        req = ancestor(snap, art, lambda a: a.artifact_type == "request" and a.producer_agent == HARNESS_AGENT)
        gates = ([(req, 0)] if req is not None else []) + ([(art, need_index)] if need_index is not None else [])
        for target, idx in gates:
            verdict = premise_verdict(snap, target, idx)
            if verdict is None:
                return None
            if verdict in NEGATIVE_VERDICTS:
                return False
        return True

    # ---------------------------------------------------------------- what to do next

    def _gate(self, snap: BoardSnapshot, key: tuple, art: Artifact, need_index: Optional[int] = None) -> bool:
        ok = self._premise(snap, art, need_index)
        if ok is False:
            self.done.add(key)
            self.log("decision", item=str(key), action="declined: the premise checker refused this task/request")
        return bool(ok)

    def _from_needs(self, snap: BoardSnapshot):
        for on in self.board.open_needs(include_claimed=False):
            key = ("need", on.artifact.artifact_id, on.index)
            if key in self.done or on.need.get("artifact_type") != "property_result":
                continue
            if self.sim is not None and self.sim.owns(snap, on):
                continue
            if not self._gate(snap, key, on.artifact, on.index):
                continue
            cand = nearest(snap, on.artifact, "candidate")
            prop = on.need.get("property", self.task.property)
            flawed = find_rerun_target(snap, on.artifact, prop)
            text = (f"The team asked for: **{on.need.get('query', prop)}** (property `{prop}`), for candidate "
                    f"`{cand.artifact_id if cand else '?'}` ({cand.payload['name'] if cand else '?'}).")
            extra = {k: v for k, v in on.need.items() if k not in ("artifact_type", "query", "rationale", "property")}
            if on.need.get("rationale"):
                text += f"\nWhy: {on.need['rationale']}"
            if extra:
                text += f"\nRequest details: `{json.dumps(extra)}`"
            if flawed:
                text += (f"\nThis is a **rerun**: it replaces result `{flawed}`, which a referee rejected (read its "
                         "checks with `./board get`). Fix the method; don't repeat it.")
            parents = [p for p in ((cand.artifact_id if cand else None), flawed) if p]
            return key, (on.artifact.artifact_id, on.index), text, parents
        return None

    def _reruns(self, snap: BoardSnapshot):
        """My results a referee failed without asking for a specific rerun: I answer the challenge myself."""
        for res in snap.of_type("property_result"):
            key = ("rerun", res.artifact_id)
            if res.producer_agent != WORKER or key in self.done:
                continue
            kids = snap.children.get(res.artifact_id, [])
            if any(k.artifact_type == "property_result" and k.payload["property"] == res.payload["property"]
                   for k in kids):
                continue  # already superseded
            bad = [k for k in kids if k.artifact_type == "check" and k.payload["check_type"] == "method_audit"
                   and k.payload["verdict"] in NEGATIVE_VERDICTS and k.producer_agent != WORKER]
            if not bad or any(n.get("artifact_type") == "property_result" for k in bad for n in k.needs):
                continue  # nothing failed, or the referee asked for a rerun as a need (handled above)
            if rerun_depth(snap, res) >= MAX_WORKER_RERUNS:
                self.done.add(key)
                self.log("decision", result=res.artifact_id[:8], action=f"no rerun: {MAX_WORKER_RERUNS} already")
                continue
            cand = nearest(snap, res, "candidate")
            why = " | ".join(f"{k.producer_agent}: {k.payload.get('note', '')}" for k in bad)
            text = (f"Your earlier result `{res.artifact_id}` ({res.payload['property']} = {res.payload['value']:g} "
                    f"{res.payload['units']}) **failed a method audit**: {why}\nRerun `{res.payload['property']}` "
                    "with the method fixed, or post a run_report explaining why the audit is wrong.")
            parents = [p for p in ((cand.artifact_id if cand else None), res.artifact_id) if p]
            return key, None, text, parents
        return None

    def _orphans(self, snap: BoardSnapshot):
        """Task candidates a Scout posted without any request (material outside every family, no structure or no
        literature value to start from): nobody else will act, so the generalist tries."""
        for cand in snap.of_type("candidate"):
            key = ("orphan", cand.artifact_id)
            if key in self.done or cand.needs:
                continue
            if not any(snap.artifacts[p].producer_agent == HARNESS_AGENT
                       for p in cand.parent_artifact_ids if p in snap.artifacts):
                continue
            if any(k.artifact_type in ("property_result", "run_report", "request")
                   for k in snap.children.get(cand.artifact_id, [])):
                continue
            if not self._gate(snap, key, cand):
                continue
            text = (f"No specialist on the team could set this task up for candidate `{cand.artifact_id}` "
                    f"({cand.payload['name']}). Compute `{self.task.property}` ({self.task.units}) yourself, or "
                    "refuse if it is not well-posed.")
            return key, None, text, [cand.artifact_id]
        return None

    # ---------------------------------------------------------------- one step

    def prompt(self, assignment: str, parents: list[str], fulfills: Optional[tuple[str, int]]) -> str:
        post_args = " ".join(f"--parent {p}" for p in parents)
        if fulfills:
            post_args += f" --fulfills {fulfills[0]}:{fulfills[1]}"
        text = PROMPT_FILE.read_text(encoding="utf-8").format(
            task_prompt=self.task.prompt, assignment=assignment, post_args=post_args.strip(),
            task_request_id=self.request_id, calibration_path=CAL_TOKEN)
        return text + board_tool_help(self.request_id)

    def step(self) -> bool:
        if time.time() >= self.deadline:
            return False
        snap = self.board.snapshot()
        item = self._from_needs(snap) or self._reruns(snap) or self._orphans(snap)
        if item is None:
            return False
        key, fulfills, assignment, parents = item
        usd = min(self.worker_usd, self.usd_left())
        if usd < MIN_SESSION_USD:
            self.done.add(key)
            self.log("decision", item=str(key), action=f"skipped: ${usd:.2f} of the task budget left")
            return False
        minutes_left = max(1.0, (self.deadline - time.time()) / 60)
        if fulfills and not self.board.claim(WORKER, *fulfills, ttl_minutes=minutes_left):
            self.done.add(key)
            return True  # someone else holds it
        self.done.add(key)
        self.log("dispatch", item=str(key), usd_cap=round(usd, 3), minutes_left=round(minutes_left, 1))
        self.sessions.append(self.session_fn(self.prompt(assignment, parents, fulfills), len(self.sessions) + 1,
                                             usd, minutes_left * 60))
        return True


def claude_code_session_fn(board_root: Path, task: Task, *, work_dir: Path, model: str, effort: Optional[str],
                           sandbox: str, image: Optional[str], gpus: bool) -> SessionFn:
    """Real worker sessions through Teammate 2's sandbox + Claude Code executor (coolorbit/eval/runner.py), priced
    and checked for key exhaustion exactly like arms A/B (price_session, exhaustion_text)."""
    from coolorbit.eval.runner import (Budget, ClaudeCodeExecutor, calibration_path_for, contamination,
                                       exhaustion_text, prepare_sandbox, price_session)
    from coolorbit.eval.sandbox import DEFAULT_TAG, DockerSandbox

    box = DockerSandbox(image or DEFAULT_TAG, gpus=gpus) if sandbox == "docker" else None
    if box is not None:
        box.preflight()
    executor = ClaudeCodeExecutor(sandbox=box)  # passes the active shared key (llm_keys.subprocess_env)

    def run_session(prompt: str, attempt: int, usd: float, timeout_s: float) -> dict:
        work = Path(work_dir) / f"worker-{attempt}"
        prepare_sandbox(work, board_root, WORKER, docker=box is not None)
        prompt = prompt.replace(CAL_TOKEN, calibration_path_for(work, box is not None))
        (work / "PROMPT.md").write_text(prompt, encoding="utf-8")  # evidence of exactly what it was told
        rel = Path("transcripts") / f"worker-{attempt}.jsonl"
        key_name = llm_keys.active_key_name()
        res = executor.run(prompt, cwd=work, budget=Budget(usd=usd, timeout_s=timeout_s, model=model, effort=effort),
                           transcript_path=board_root / rel, board_root=board_root)
        res.key_name = key_name
        price_session(res, model, cap_usd=usd)  # charged = Claude Code's total_cost_usd, or the cap if no report
        infra = llm_keys.is_credit_exhausted(exhaustion_text(res))
        if infra and key_name == llm_keys.PRIMARY:
            llm_keys.mark_primary_exhausted("arm C worker session ran out of credit")
        llm_roles._append_cost(board_root / COSTS_FILE, {
            "agent": WORKER, "task_id": task.id, "kind": "llm", "model": res.model or model, "key_name": key_name,
            "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
            "cache_read_input_tokens": res.cache_read_input_tokens,
            "cache_creation_input_tokens": res.cache_creation_input_tokens, "gpu_hours": None,
            "usd": res.usd, "usd_source": res.usd_source, "usd_reported": res.usd_reported,
            "usd_check_cost_usd": res.usd_check, "wall_s": round(res.wall_s, 3), "timed_out": res.timed_out,
            "note": f"attempt {attempt}"})
        data = asdict(res) if is_dataclass(res) else dict(res)
        data.pop("transcript", None)
        return {"role": "worker", "agent": WORKER, "attempt": attempt, "sandbox": sandbox, "transcript": str(rel),
                "result": data, "contamination": contamination(res.transcript), "infra_failure": infra}

    return run_session


# --------------------------------------------------------------------------- one task


def run_task(task: Task, *, run: str, repeat: int, boards_root: Path, budget_usd: float, timeout_min: float,
             preset: str, llm_mode: str, worker_usd: float, model: str,
             session_fn_factory: Callable[[Path], SessionFn], sim_runner: Callable = subprocess_runner,
             premise_client: Any = "auto", seeds: Optional[dict] = None,
             runs_root: Path = REPO_ROOT / "runs" / "arm_c", max_rounds: int = 500, sandbox: str = "docker") -> dict:
    from coolorbit.agents.librarian import LibrarianAgent
    from coolorbit.agents.premise import PremiseChecker
    from coolorbit.agents.referee_rules import RuleReferee
    from coolorbit.agents.scout import Scout, load_seeds

    board_root = Path(boards_root) / run / f"{ARM}-r{repeat}" / task.id
    if (board_root / RECORD_FILE).exists():
        return {"task_id": task.id, "skipped": "already complete"}
    if board_root.exists():
        raise FileExistsError(f"{board_root} exists without {RECORD_FILE} (an interrupted attempt); "
                              "inspect it, then delete it yourself to redo this task")
    board = Board(board_root)
    req_id = seed_board(board, TaskSet(version=1, frozen=False, tasks=[task]))[task.id]
    started, t0 = utc(), time.time()
    deadline = t0 + timeout_min * 60
    costs = board_root / COSTS_FILE
    costs.touch()
    # Every llm_roles.create call (premise checker, adjudicator) is logged to this task's costs.jsonl and refused
    # once the task's spend reaches the budget.
    env = {llm_roles.COSTS_ENV: str(costs), llm_roles.TASK_ENV: task.id, llm_roles.BUDGET_ENV: str(budget_usd)}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    gpu_jobs: list[dict] = []
    stopped, rounds, errors, agents = "max_rounds", 0, [], []
    dispatcher = None
    try:
        seeds = seeds if seeds is not None else load_seeds()
        sim = ArmCSimulator(board, "simulator_gb10", runner=bounded_runner(sim_runner, deadline, gpu_jobs),
                            runs_root=Path(runs_root) / run / f"{ARM}-r{repeat}" / task.id, max_minutes=timeout_min)
        dispatcher = WorkerDispatcher(board, task, req_id, session_fn_factory(board_root), sim=sim,
                                      deadline=deadline, worker_usd=worker_usd,
                                      usd_left=lambda: budget_usd - llm_roles.spent_usd(costs, task.id))
        agents = ([PremiseChecker(board, client=None if llm_mode == "never" else premise_client, llm_mode=llm_mode,
                                  verbose=False)]
                  + [Scout(board, s, seeds=seeds, preset=preset, eval_only=True, verbose=False) for s in seeds]
                  + [RuleReferee(board, verbose=False), LibrarianAgent(board), sim, dispatcher])
        while rounds < max_rounds:
            if time.time() >= deadline:
                stopped = "deadline"
                break
            did = False
            for agent in agents:
                try:
                    did = bool(agent.step()) or did
                except llm_roles.BudgetExceeded:
                    stopped = "budget"
                    break
                except Exception as exc:  # one agent's bug must not kill the task; it is recorded
                    errors.append(f"{getattr(agent, 'name', type(agent).__name__)}: {exc!r}"[:300])
                    traceback.print_exc()
            rounds += 1
            if stopped == "budget":
                break
            if not did:
                stopped = "idle"
                break
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    for job in gpu_jobs:  # the GB10 is owned hardware: GPU hours are recorded, dollars are 0
        llm_roles._append_cost(costs, {"agent": "simulator_gb10", "task_id": task.id, "kind": "gpu", "model": None,
                                       "input_tokens": 0, "output_tokens": 0,
                                       "gpu_hours": round(job["wall_s"] / 3600, 4), "usd": 0.0,
                                       "wall_s": job["wall_s"], "note": f"GB10: {job['task']} ({job['status']})"})
    sessions = dispatcher.sessions if dispatcher else []
    record = {"run": run, "arm": ARM, "repeat": repeat, "task_id": task.id, "task_request_id": req_id,
              "started": started, "finished": utc(),
              "budget": {"usd": budget_usd, "timeout_s": timeout_min * 60, "model": model, "worker_usd": worker_usd},
              "sandbox": sandbox, "sessions": sessions,
              "contamination": sorted({c for s in sessions for c in s.get("contamination", [])}),
              "infra_failure": any(s.get("infra_failure") for s in sessions),
              "agents": [getattr(a, "name", type(a).__name__) for a in agents], "rounds": rounds,
              "stopped": stopped, "agent_errors": errors, "gpu_jobs": gpu_jobs,
              "known_issues": known_contamination(), "llm_usd": round(llm_roles.spent_usd(costs, task.id), 4),
              "wall_s": round(time.time() - t0, 1)}
    (board_root / RECORD_FILE).write_text(json.dumps(record, indent=1), encoding="utf-8")
    record["board"] = str(board_root)
    return record


def set_aside_infra_failure(record: dict, try_no: int) -> Path:
    """Move an attempt killed by an out-of-credit key out of the scored folder (kept as evidence, not scored)."""
    board = Path(record["board"])
    failed = board.parent / "_infra_failed" / f"{board.name}.try{try_no}"
    failed.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(board), str(failed))
    (failed / "INFRA_FAILURE.json").write_text(json.dumps({
        "task_id": record["task_id"], "reason": "shared Anthropic key out of credit mid-task; not scored",
        "at": utc()}, indent=1), encoding="utf-8")
    return failed


def log_result(path: Path, record: dict) -> None:
    """Append-only, human-readable log of every task run (autoresearch's results.tsv)."""
    snap = Board(record["board"]).snapshot()
    reports = snap.of_type("run_report")
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write("finished\trepeat\ttask_id\tstopped\tresults\trefused\tfailed\tsessions\tllm_usd\tgpu_min\t"
                     "wall_min\terrors\tinfra\n")
        fh.write("\t".join(str(x) for x in (
            record["finished"], record["repeat"], record["task_id"], record["stopped"],
            len(snap.of_type("property_result")), sum(r.payload["status"] == "refused" for r in reports),
            sum(r.payload["status"] == "failed" for r in reports), len(record["sessions"]),
            f"{record['llm_usd']:.3f}", f"{sum(j['wall_s'] for j in record['gpu_jobs']) / 60:.1f}",
            f"{record['wall_s'] / 60:.1f}", len(record["agent_errors"]), record["infra_failure"])) + "\n")


def main(argv: Optional[list[str]] = None) -> None:
    roles = llm_roles.load()
    baseline = llm_roles.role("arm_baseline")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--repeat", type=int, required=True)
    ap.add_argument("--task", action="append", help="only these task ids (repeatable)")
    ap.add_argument("--budget-usd", type=float, default=roles["comparison_budget_usd_per_task_per_arm"],
                    help="LLM $ per task across ALL agents (same as arms A/B)")
    ap.add_argument("--worker-usd", type=float, default=1.0, help="cap for one worker session (within the budget)")
    ap.add_argument("--timeout-min", type=float, default=60.0, help="wall clock per task (same as arms A/B)")
    ap.add_argument("--model", default=baseline["model"], help="worker model (= arms A/B)")
    ap.add_argument("--effort", default=baseline["effort"], help="worker effort (= arms A/B)")
    ap.add_argument("--preset", choices=["quick", "production"], default="production")
    ap.add_argument("--llm", choices=["inconclusive", "always", "never"], default="inconclusive",
                    help="when the premise checker asks Claude")
    ap.add_argument("--boards-root", default=str(DEFAULT_BOARDS_ROOT))
    ap.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    ap.add_argument("--image", help="sandbox image tag (default: the runner's)")
    ap.add_argument("--no-gpus", action="store_true")
    ap.add_argument("--sandbox", choices=["docker", "none"], default="docker")
    ap.add_argument("--allow-unsandboxed", action="store_true")
    ap.add_argument("--allow-known-contamination", action="store_true", help="development only: not a valid run")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.sandbox == "none" and not args.allow_unsandboxed:
        ap.error("--sandbox none lets the worker reach your credentials; add --allow-unsandboxed (development only)")
    if (args.model, args.effort) != (baseline["model"], baseline["effort"]):
        print("WARNING: worker model/effort differ from the arm_baseline role; arms are no longer comparable")
    task_set = load_tasks()
    tasks = [t for t in task_set.tasks if not args.task or t.id in args.task]
    if args.dry_run:
        for t in tasks:
            print(f"{t.id:42s} {t.kind:10s} {t.property:22s} -> {args.boards_root}/{args.run}/{ARM}-r{args.repeat}/{t.id}")
        print(f"{len(tasks)} task(s); ${args.budget_usd}/task (worker session <= ${args.worker_usd}), "
              f"{args.timeout_min} min/task, preset {args.preset}, worker {args.model} effort {args.effort}, "
              f"sandbox {args.sandbox}")
        for issue in known_contamination():
            print(f"NOT VALID FOR THE COMPARISON YET: {issue}")
        return
    issues = known_contamination()
    if issues and not args.allow_known_contamination:
        raise SystemExit("Refusing a real arm C run; it would not be a fair comparison:\n  - " + "\n  - ".join(issues))
    try:
        import coolorbit.eval.runner  # noqa: F401
    except ImportError:
        raise SystemExit("arm C's worker uses Teammate 2's sandbox and Claude Code executor (PR #5); merge it first")
    log_path = REPO_ROOT / "runs" / "arm_c" / args.run / "results.tsv"
    for t in tasks:
        for try_no in (1, 2):
            work = Path(args.work_root) / args.run / f"{ARM}-r{args.repeat}" / (t.id if try_no == 1 else f"{t.id}.try{try_no}")

            def factory(root: Path, t: Task = t, work: Path = work) -> SessionFn:
                return claude_code_session_fn(root, t, work_dir=work, model=args.model, effort=args.effort,
                                              sandbox=args.sandbox, image=args.image, gpus=not args.no_gpus)
            try:
                rec = run_task(t, run=args.run, repeat=args.repeat, boards_root=Path(args.boards_root),
                               budget_usd=args.budget_usd, timeout_min=args.timeout_min, preset=args.preset,
                               llm_mode=args.llm, worker_usd=args.worker_usd, model=args.model,
                               session_fn_factory=factory, sandbox=args.sandbox)
            except FileExistsError as exc:
                print(f"SKIP {t.id}: {exc}")
                break
            if rec.get("skipped"):
                print(f"skip {t.id}: {rec['skipped']}")
                break
            if not rec["infra_failure"]:
                log_result(log_path, rec)
                print(f"done {t.id}: stopped={rec['stopped']} sessions={len(rec['sessions'])} "
                      f"llm=${rec['llm_usd']:.2f} wall={rec['wall_s'] / 60:.1f} min")
                break
            failed = set_aside_infra_failure(rec, try_no)
            print(f"INFRA {t.id}: the shared key ran out of credit; attempt kept in {failed}, not scored")
            if try_no == 2 or llm_keys.active_key_name() != llm_keys.BACKUP:
                raise SystemExit("out of credit and no fresh key to rerun on; stopping the run")
    print(f"log: {log_path}")


if __name__ == "__main__":
    main()
