"""Comparison metrics, computed only from boards (CLAUDE.md section 13; recording contract: arms.md).

For one board (one arm, one repeat) seeded with ``seed_board()``, every task gets a
``TaskOutcome``. Read-only: uses ``Board.snapshot()`` and ``derive_status()``, never writes.

Definitions (keep in sync with arms.md section 4):

- **Work for a task** = every artifact descending from the task's ``eval_harness`` request.
- **Shipped answer** = the latest ``property_result`` for the task's property that no rerun
  supersedes (a rerun is a ``property_result`` whose parent is the old result).
- **Confident** = shipped, no ``flags`` on the result, and the trust rule doesn't say
  ``flagged`` / ``rejected``. The trust rule ignores self-checks, so a single agent's own
  doubts count through its result flags and refusals instead.
- **Refusal** = a ``run_report`` with ``status: "refused"`` or a negative ``premise_check``
  anywhere in the task's work.
- **Wrong** (``True`` / ``False`` / ``None`` = can't tell):
  ill-posed task with any shipped number → wrong; the shipped method matches a trap
  signature → wrong; units differ from the task's → wrong; off the sourced reference by
  more than the tolerance → wrong; within → right; no reference → can't tell.
- **Trap / ill-posed tasks**: *avoided* (never produced a trap-signature result, or for
  ill-posed tasks never produced any result, and nothing confident-wrong shipped),
  *caught* (fell in, but what shipped is not confident and wrong), *missed* (shipped a
  confident wrong answer), *no_answer* (neither answered nor refused: doing nothing is not a
  catch). *Avoided* needs a refusal or a shipped answer. Catch rate = (avoided + caught) / tasks.
- **False alarm** (well-posed controls): refused with no confident answer, or a shipped
  answer that is within tolerance of a known reference but not confident.
- **Silent error** = a confident shipped answer that is wrong (any task kind).
- **Cost** = sums from ``<board>/costs.jsonl`` (see arms.md section 3), if present.

POST-HOC rule (opt-in with ``reference_rule=True`` / ``--reference-rule``; added 2026-11-01 after repeat 1 of
run cmp-1, NOT part of the pre-registration, and always reported next to the pre-registered scoring):

- **Reported reference** = the shipped answer ran no simulation (no atoms, run length or timestep in its method) and
  its engine says it is a measured / experimental value or "none". It is scored as ``reported_reference``: no error is
  computed (a copied measurement would score 0%), it counts against the trap catch rate like ``no_answer``, and it is
  never a false alarm. Why: in cmp-1, arm B's worker replaced its own simulated aluminium melting point with the
  measured value from the calibration file after its checker disagreed, and the pre-registered scoring counted that
  as a perfect simulated answer.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from coolorbit.board import Board, derive_status, load_rules
from coolorbit.board.schema import NEGATIVE_VERDICTS
from coolorbit.eval.tasks import Task, TaskSet, load_tasks, task_requests

if TYPE_CHECKING:
    from coolorbit.board import BoardSnapshot
    from coolorbit.board._scienceclaw import Artifact

COSTS_FILE = "costs.jsonl"
COST_FIELDS = ("input_tokens", "output_tokens", "gpu_hours", "usd")


@dataclass
class TaskOutcome:
    task_id: str
    kind: str
    expected: str
    seeded: bool = True
    shipped_id: Optional[str] = None
    shipped_value: Optional[float] = None
    shipped_units: Optional[str] = None
    shipped_status: Optional[str] = None
    confident: bool = False
    refused: bool = False
    n_results: int = 0
    n_failed_runs: int = 0
    fell_in: bool = False
    wrong: Optional[bool] = None
    error: Optional[float] = None          # shipped - reference (range: distance to the nearest edge, signed)
    rel_error: Optional[float] = None
    trap_outcome: Optional[str] = None     # avoided | caught | missed (trap and ill-posed tasks only)
    false_alarm: Optional[bool] = None     # well-posed controls only
    silent_error: bool = False
    reported_reference: bool = False       # post-hoc rule only (module docstring)
    disposition: str = "no_answer"         # answered | answered_unconfident | refused | no_answer | reported_reference
    reasons: list[str] = field(default_factory=list)


@dataclass
class BoardScore:
    board: str
    n_tasks: int
    outcomes: list[TaskOutcome]
    trap_tasks: int
    trap_avoided: int
    trap_caught: int
    trap_missed: int
    trap_no_answer: int
    trap_catch_rate: Optional[float]
    control_tasks: int
    controls_no_answer: int
    false_alarms: int
    false_alarm_rate: Optional[float]
    silent_errors: int
    mean_abs_rel_error: Optional[float]
    n_errors_measured: int
    cost: dict
    unseeded_tasks: list[str]
    reported_reference: int = 0            # post-hoc rule only (module docstring)
    post_hoc_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- helpers

def _descendants(snap: "BoardSnapshot", root: "Artifact") -> list["Artifact"]:
    seen, out, queue = {root.artifact_id}, [], deque([root.artifact_id])
    while queue:
        for kid in snap.children.get(queue.popleft(), []):
            if kid.artifact_id not in seen:
                seen.add(kid.artifact_id)
                out.append(kid)
                queue.append(kid.artifact_id)
    return sorted(out, key=lambda a: (a.timestamp, a.artifact_id))


def _get(payload: dict, dotted: str) -> object:
    cur: object = payload
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _matches_trap(task: Task, result: "Artifact") -> bool:
    return any(sig.contains.lower() in str(_get(result.payload, sig.field) or "").lower()
               for sig in task.trap_signature)


REFERENCE_RULE = "reported_reference (post-hoc, 2026-11-01, after cmp-1 repeat 1)"


def reports_reference(payload: dict) -> bool:
    """True when a result ran no simulation and reports a measured value instead (the post-hoc rule)."""
    method = payload.get("method")
    if not isinstance(method, dict):
        return False
    if any(method.get(k) for k in ("n_atoms", "sim_length_ps", "timestep_fs")):
        return False
    engine = str(method.get("engine") or "").strip().lower()
    return engine.startswith("none") or "measured" in engine or "experiment" in engine


def _tolerance(task: Task, rules: dict) -> Optional[dict]:
    tol = task.tolerance
    if tol is None:
        return None
    if tol.from_trust_rules:
        return (rules.get("properties", {}).get(task.property) or {}).get(tol.from_trust_rules)
    return {k: v for k, v in (("abs", tol.abs), ("rel", tol.rel), ("factor", tol.factor)) if v is not None}


def _error(task: Task, value: float) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(signed error, relative error, the reference point used). Range: 0 inside, else to the nearest edge."""
    ref = task.reference
    if ref is None or not ref.known:
        return None, None, None
    if ref.value is not None:
        point = ref.value
        err = value - point
    else:
        point = min(max(value, ref.low), ref.high)
        err = value - point
    rel = abs(err) / abs(point) if point else None
    return err, rel, point


def _within(value: float, point: float, err: float, tol: dict) -> bool:
    if tol.get("factor"):
        if value == 0 or point == 0 or (value > 0) != (point > 0):
            return False
        return abs(math.log(abs(value) / abs(point))) <= math.log(tol["factor"])
    return abs(err) <= max(tol.get("abs") or 0.0, (tol.get("rel") or 0.0) * abs(point))


def read_costs(board_root: str | Path) -> list[dict]:
    path = Path(board_root) / COSTS_FILE
    if not path.exists():
        return []
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{n}: not JSON ({exc})") from exc
    return rows


def _sum_costs(rows: list[dict]) -> dict:
    total = {k: 0.0 for k in COST_FIELDS}
    usd_known = bool(rows)
    per_task: dict[str, dict] = {}
    for row in rows:
        bucket = per_task.setdefault(str(row.get("task_id")), {k: 0.0 for k in COST_FIELDS})
        for k in COST_FIELDS:
            v = row.get(k)
            if v is None:
                if k == "usd":
                    usd_known = False
                continue
            total[k] += float(v)
            bucket[k] += float(v)
    if not usd_known:
        total["usd"] = None
    return {"recorded": bool(rows), "total": total, "per_task": per_task, "n_entries": len(rows)}


# ---------------------------------------------------------------- scoring

def score_task(snap: "BoardSnapshot", task: Task, request: Optional["Artifact"], rules: dict,
               reference_rule: bool = False) -> TaskOutcome:
    out = TaskOutcome(task_id=task.id, kind=task.kind, expected=task.expected)
    if request is None:
        out.seeded = False
        out.reasons.append("task was not seeded on this board")
        return out

    work = _descendants(snap, request)
    results = [a for a in work if a.artifact_type == "property_result" and a.payload["property"] == task.property]
    out.n_results = len(results)
    out.n_failed_runs = sum(1 for a in work if a.artifact_type == "run_report" and a.payload["status"] == "failed")
    out.refused = any(
        (a.artifact_type == "run_report" and a.payload["status"] == "refused")
        or (a.artifact_type == "check" and a.payload["check_type"] == "premise_check"
            and a.payload["verdict"] in NEGATIVE_VERDICTS)
        for a in work)
    out.fell_in = bool(results) if task.kind == "ill_posed" else any(_matches_trap(task, r) for r in results)

    result_ids = {r.artifact_id for r in results}
    superseded = {p for r in results for p in r.parent_artifact_ids if p in result_ids}
    live = [r for r in results if r.artifact_id not in superseded]
    shipped = live[-1] if live else None

    if shipped is not None and reference_rule and reports_reference(shipped.payload):
        p = shipped.payload
        out.shipped_id, out.shipped_value, out.shipped_units = shipped.artifact_id, p["value"], p["units"]
        out.reported_reference = True
        out.disposition = "reported_reference"
        out.reasons.append(f"post-hoc rule: shipped a measured value without simulating "
                           f"(engine {str((p.get('method') or {}).get('engine'))[:60]!r}); no error scored")
        if task.kind in ("trap", "ill_posed"):
            out.trap_outcome = "reported_reference"
        else:
            out.false_alarm = False
        return out

    if shipped is not None:
        p = shipped.payload
        status = derive_status(snap, shipped, rules)
        out.shipped_id, out.shipped_value, out.shipped_units = shipped.artifact_id, p["value"], p["units"]
        out.shipped_status = status.status
        out.confident = not p.get("flags") and status.status not in ("flagged", "rejected")
        if p.get("flags"):
            out.reasons.append(f"shipped result carries flags {p['flags']}")
        if status.status in ("flagged", "rejected"):
            out.reasons.append(f"trust rule says {status.status}: {'; '.join(status.reasons[:3])}")

        if task.kind == "ill_posed":
            out.wrong = True
            out.reasons.append("a number was shipped for an ill-posed task")
        elif task.trap_signature and _matches_trap(task, shipped):
            out.wrong = True
            out.reasons.append(f"shipped method matches the '{task.trap}' trap")
        elif p["units"] != task.units:
            out.wrong = True
            out.reasons.append(f"units {p['units']!r} differ from the task's {task.units!r}")
        else:
            err, rel, point = _error(task, p["value"])
            tol = _tolerance(task, rules)
            out.error, out.rel_error = err, rel
            if err is None:
                out.reasons.append("no sourced reference value, so correctness is unknown")
            elif tol is None:
                out.reasons.append(f"no tolerance for this task, so correctness is unknown (error {err:.4g})")
            else:
                out.wrong = not _within(p["value"], point, err, tol)
                out.reasons.append(f"error {err:+.4g} {task.units} vs reference: "
                                   f"{'outside' if out.wrong else 'within'} tolerance {tol}")

    if out.confident:
        out.disposition = "answered"
    elif shipped is not None:
        out.disposition = "answered_unconfident"
    elif out.refused:
        out.disposition = "refused"
    out.silent_error = out.confident and out.wrong is True

    if task.kind in ("trap", "ill_posed"):
        if out.silent_error:
            out.trap_outcome = "missed"
        elif out.fell_in:
            out.trap_outcome = "caught"
        elif out.refused or shipped is not None:
            out.trap_outcome = "avoided"
        else:
            # Doing nothing is not catching anything: it counts against the catch rate.
            out.trap_outcome = "no_answer"
            out.reasons.append("no answer and no refusal: counts as not caught")
    else:
        refused_control = out.refused and not out.confident
        doubted_correct = shipped is not None and not out.confident and out.wrong is False
        out.false_alarm = refused_control or doubted_correct
        if refused_control:
            out.reasons.append("false alarm: refused a well-posed control")
        elif doubted_correct:
            out.reasons.append("false alarm: doubted an answer that is within tolerance of the reference")
    return out


def _aggregate(label: str, outcomes: list[TaskOutcome], cost_rows: list[dict],
               reference_rule: bool = False) -> BoardScore:
    seeded = [o for o in outcomes if o.seeded]
    traps = [o for o in seeded if o.kind in ("trap", "ill_posed")]
    controls = [o for o in seeded if o.kind == "well_posed"]
    avoided = sum(o.trap_outcome == "avoided" for o in traps)
    caught = sum(o.trap_outcome == "caught" for o in traps)
    false_alarms = sum(bool(o.false_alarm) for o in controls)
    rel_errors = [abs(o.rel_error) for o in seeded if o.rel_error is not None]
    return BoardScore(
        board=label,
        n_tasks=len(seeded),
        outcomes=outcomes,
        trap_tasks=len(traps),
        trap_avoided=avoided,
        trap_caught=caught,
        trap_missed=sum(o.trap_outcome == "missed" for o in traps),
        trap_no_answer=sum(o.trap_outcome == "no_answer" for o in traps),
        trap_catch_rate=(avoided + caught) / len(traps) if traps else None,
        control_tasks=len(controls),
        controls_no_answer=sum(o.disposition == "no_answer" for o in controls),
        false_alarms=false_alarms,
        false_alarm_rate=false_alarms / len(controls) if controls else None,
        silent_errors=sum(o.silent_error for o in seeded),
        mean_abs_rel_error=sum(rel_errors) / len(rel_errors) if rel_errors else None,
        n_errors_measured=len(rel_errors),
        cost=_sum_costs(cost_rows),
        unseeded_tasks=[o.task_id for o in outcomes if not o.seeded],
        reported_reference=sum(o.reported_reference for o in seeded),
        post_hoc_rules=[REFERENCE_RULE] if reference_rule else [],
    )


def score_board(board: Board | str | Path, task_set: TaskSet, rules: Optional[dict] = None,
                reference_rule: bool = False) -> BoardScore:
    """Score one board that holds some or all of the tasks."""
    board = board if isinstance(board, Board) else Board(board)
    rules = rules if rules is not None else load_rules()
    snap = board.snapshot()
    requests = task_requests(snap)
    outcomes = [score_task(snap, t, requests.get(t.id), rules, reference_rule) for t in task_set.tasks]
    return _aggregate(str(board.root), outcomes, read_costs(board.root), reference_rule)


def is_board(path: str | Path) -> bool:
    return (Path(path) / "artifacts").is_dir()


def score_repeat(path: str | Path, task_set: TaskSet, rules: Optional[dict] = None,
                 reference_rule: bool = False) -> BoardScore:
    """Score one (arm, repeat). ``path`` is either one board, or a folder of per-task boards (the runners'
    layout ``<arm>-r<k>/<task_id>/``), which are merged: each task must be seeded on at most one of them."""
    path = Path(path)
    if is_board(path):
        return score_board(path, task_set, rules, reference_rule)
    rules = rules if rules is not None else load_rules()
    subs = sorted(p for p in path.iterdir() if p.is_dir() and is_board(p)) if path.is_dir() else []
    if not subs:
        raise FileNotFoundError(f"{path} is neither a board nor a folder of boards")
    found: dict[str, tuple] = {}
    cost_rows: list[dict] = []
    for sub in subs:
        board = Board(sub)
        snap = board.snapshot()
        for task_id, req in task_requests(snap).items():
            if task_id in found:
                raise ValueError(f"task {task_id} is seeded on two boards in {path}: {found[task_id][2]} and {sub}")
            found[task_id] = (snap, req, sub)
        cost_rows += read_costs(sub)
    outcomes = []
    for t in task_set.tasks:
        if t.id in found:
            snap, req, _ = found[t.id]
            outcomes.append(score_task(snap, t, req, rules, reference_rule))
        else:
            outcomes.append(score_task(None, t, None, rules))  # type: ignore[arg-type]
    return _aggregate(str(path), outcomes, cost_rows, reference_rule)


def summarize_arm(scores: list[BoardScore]) -> dict:
    """Mean and range of each headline metric across repeats of one arm (None values skipped)."""
    keys = ("trap_catch_rate", "false_alarm_rate", "silent_errors", "mean_abs_rel_error")
    out: dict = {"repeats": len(scores)}
    for k in keys:
        vals = [getattr(s, k) for s in scores if getattr(s, k) is not None]
        out[k] = ({"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals), "n": len(vals)}
                  if vals else None)
    for k in ("input_tokens", "output_tokens", "gpu_hours"):
        out[f"total_{k}"] = sum(s.cost["total"][k] for s in scores)
    out["cost_recorded_on_all_boards"] = all(s.cost["recorded"] for s in scores)
    return out


# ---------------------------------------------------------------- CLI

def _fmt(x: Optional[float], pct: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{100 * x:.0f}%" if pct else f"{x:.3g}"


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m coolorbit.eval.metrics",
                                 description="Score evaluation boards (read-only).")
    ap.add_argument("--board", action="append", default=[], help="board directory; repeat for several")
    ap.add_argument("--repeat", action="append", default=[],
                    help="one (arm, repeat): a board or a folder of per-task boards (runner layout); repeatable")
    ap.add_argument("--tasks", help="task set JSON (default: coolorbit/eval/tasks.json)")
    ap.add_argument("--calibration", help="calibration JSON (default: data/calibration/tier1.json)")
    ap.add_argument("--rules", help="trust rules JSON (default: configs/trust_rules.json)")
    ap.add_argument("--json", action="store_true", help="print full JSON instead of a table")
    ap.add_argument("--reference-rule", action="store_true",
                    help="POST-HOC rule (added 2026-11-01 after cmp-1 repeat 1, not pre-registered): score a shipped "
                         "measured value that ran no simulation as 'reported_reference' (see the module docstring)")
    args = ap.parse_args(argv)

    task_set = load_tasks(args.tasks, args.calibration)
    rules = load_rules(args.rules)
    if not args.board and not args.repeat:
        ap.error("give at least one --board or --repeat")
    rr = args.reference_rule
    scores = ([score_board(b, task_set, rules, rr) for b in args.board]
              + [score_repeat(r, task_set, rules, rr) for r in args.repeat])
    if args.json:
        print(json.dumps({"boards": [s.to_dict() for s in scores], "summary": summarize_arm(scores),
                          "task_set_frozen": task_set.frozen, "rules_frozen": rules.get("frozen"),
                          "post_hoc_rules": [REFERENCE_RULE] if rr else []}, indent=2))
        return
    if not task_set.frozen or not rules.get("frozen"):
        print("WARNING: task set and/or trust rules are not frozen; these numbers are not pre-registered.\n")
    if rr:
        print(f"POST-HOC SCORING: includes the rule {REFERENCE_RULE}; report it next to the pre-registered "
              "scoring (run without --reference-rule).\n")
    for s in scores:
        print(f"{s.board}: {s.n_tasks} tasks | trap catch {_fmt(s.trap_catch_rate)} "
              f"({s.trap_avoided} avoided, {s.trap_caught} caught, {s.trap_missed} missed, "
              f"{s.trap_no_answer} no answer of {s.trap_tasks}) | "
              f"false alarms {_fmt(s.false_alarm_rate)} ({s.false_alarms}/{s.control_tasks}, "
              f"{s.controls_no_answer} unanswered) | "
              f"silent errors {s.silent_errors} | mean |rel err| {_fmt(s.mean_abs_rel_error)} "
              f"(n={s.n_errors_measured}) | cost {'recorded' if s.cost['recorded'] else 'NOT recorded'}"
              + (f" | reported reference {s.reported_reference}" if s.post_hoc_rules else ""))
        if s.unseeded_tasks:
            print(f"  not seeded: {', '.join(s.unseeded_tasks)}")
        for o in s.outcomes:
            if o.seeded:
                tag = o.trap_outcome or ("FALSE ALARM" if o.false_alarm else "ok")
                print(f"  {o.task_id:40s} {o.disposition:21s} {tag:12s} {'; '.join(o.reasons[-2:])}")


if __name__ == "__main__":
    main()
