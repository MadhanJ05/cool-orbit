"""Export results boards to static JSON for the dashboard (read-only; uses the board API).

    python -m dashboard.export --board boards/<id> [--board ...] [--out dashboard/data]

Writes, into ``--out``:
  <name>.json   one file per board: results with derived status and reasons, open needs,
                failed/refused runs, lineage (every artifact with parents and children),
                the latest ranking, and the T4 scorecard if the board was seeded with eval tasks
  index.json    the list of boards, plus the arm comparison (boards named <arm>-r<repeat>) and the task list
  data.js       the same data as one script, so index.html also works opened from disk (file://), plus the
                page's ``context`` (dashboard/context.py: the question, the burst calculation, the methods)

Each result also carries the measured value it is compared with: the latest sourced ``reference_value`` on its
board, else the sourced record in data/calibration/tier1.json (``origin`` says which; the trust rule itself only
uses the board's). A board whose artifacts mention "SYNTHETIC" is marked synthetic, and the page shows a banner.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from coolorbit.board import Board, derive_status, load_rules
from coolorbit.board._scienceclaw import REPO_ROOT
from coolorbit.eval.metrics import is_board, score_board, score_repeat, summarize_arm
from coolorbit.eval.tasks import DEFAULT_CALIBRATION_PATH, TaskSet, load_tasks, task_requests
from dashboard.context import SCHEMA as CONTEXT_SCHEMA
from dashboard.context import build_context, load_calibration

SCHEMA = "coolorbit.dashboard/1"
DEFAULT_OUT = Path(__file__).resolve().parent / "data"
ARM_RE = re.compile(r"^(?P<arm>[A-Z])-r(?P<repeat>\d+)$")
SYNTHETIC_MARK = "SYNTHETIC"


def _is_synthetic(artifacts) -> bool:
    return any(SYNTHETIC_MARK in json.dumps(a.payload) for a in artifacts)


def _board_name(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:  # outside the repo: keep enough of the path to tell runs apart (run/arm-repeat)
        rel = Path(*path.resolve().parts[-3:])
    parts = [p for p in rel.parts if p != "boards"]
    return "__".join(parts) or path.name


SKIP_DIRS = {"transcripts", "integrity", "artifacts"}


def discover(path: str | Path) -> list[tuple[Path, Optional[Path]]]:
    """Boards under ``path`` as (board, repeat folder or None).

    ``path`` may be one board, an arm-repeat folder of per-task boards (the runners' ``<arm>-r<k>/<task>/``), or
    any folder above them (e.g. ``boards/eval/<run>``). Folders starting with ``_`` (``_infra_failed``) are skipped:
    infrastructure failures are never shown as results.
    """
    path = Path(path)
    if is_board(path):
        parent = path.parent
        repeat = parent if ARM_RE.match(parent.name) and not ARM_RE.match(path.name) else None
        return [(path, repeat)]
    if not path.is_dir():
        raise FileNotFoundError(f"{path} is not a board or a folder of boards")
    found: list[tuple[Path, Optional[Path]]] = []
    for child in sorted(path.iterdir()):
        if child.is_dir() and not child.name.startswith(("_", ".")) and child.name not in SKIP_DIRS:
            found += discover(child)
    return found


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def _reference(refs: dict, calibration: Optional[dict], candidate: str, prop: str) -> Optional[dict]:
    """The measured value a result is shown against: the board's latest sourced one, else the calibration set's."""
    ref = refs.get((_norm(candidate), prop))
    if ref is not None:
        rp = ref.payload
        return {"value": rp["value"], "units": rp.get("units"), "source": rp.get("source"), "origin": "board",
                "id": ref.artifact_id, "agent": ref.producer_agent}
    rec = (calibration or {}).get((_norm(candidate), prop))
    if rec is not None:
        return {"value": rec["value"], "units": rec.get("units"), "source": rec["source"], "origin": "calibration",
                "id": None, "agent": None}
    return None


def _scored_task_ids(task_set: TaskSet) -> list[str]:
    """Task ids named in the task set's pre-registration note ("Scored set: the 8 tasks a, b, ..."), if any."""
    note = str((getattr(task_set, "model_extra", None) or {}).get("_preregistration") or "")
    m = re.search(r"Scored set:(.*?)\.(?:\s|$)", note)
    known = set(task_set.by_id())
    return [t for t in re.findall(r"[a-z0-9_]+", m.group(1)) if t in known] if m else []


def _task_list(task_set: TaskSet) -> list[dict]:
    out = []
    for t in task_set.tasks:
        ref = t.reference
        out.append({"id": t.id, "origin": t.origin, "kind": t.kind, "trap": t.trap, "expected": t.expected,
                    "prompt": t.prompt, "material": t.material, "property": t.property, "units": t.units,
                    "reference": ({"value": ref.value, "low": ref.low, "high": ref.high, "source": ref.source}
                                  if ref is not None and ref.known else None)})
    return out


def export_board(board: Board, rules: dict, task_set: Optional[TaskSet] = None,
                 *, name: Optional[str] = None, now: Optional[datetime] = None,
                 repeat_dir: Optional[Path] = None, calibration: Optional[dict] = None) -> dict:
    snap = board.snapshot()
    ordered = snap.ordered()
    name = name or _board_name(board.root)

    lineage = {}
    for a in ordered:
        lineage[a.artifact_id] = {
            "id": a.artifact_id, "type": a.artifact_type, "agent": a.producer_agent, "timestamp": a.timestamp,
            "summary": a.summary, "parents": list(a.parent_artifact_ids),
            "children": [k.artifact_id for k in snap.children.get(a.artifact_id, [])],
            "payload": a.payload, "needs": a.needs,
        }

    refs = {}  # (candidate, property) -> latest sourced reference_value on this board (board order: latest wins)
    for ref in snap.of_type("reference_value"):
        if ref.payload.get("value") is not None and str(ref.payload.get("source") or "").strip():
            refs[(_norm(ref.payload["candidate"]), ref.payload["property"])] = ref

    results, status_of = [], {}
    for r in snap.of_type("property_result"):
        rep = derive_status(snap, r, rules)
        status_of[r.artifact_id] = rep.status
        p = r.payload
        results.append({
            "id": r.artifact_id, "agent": r.producer_agent, "timestamp": r.timestamp,
            "candidate": p["candidate"], "family": p["family"], "tier": p["tier"], "property": p["property"],
            "value": p["value"], "uncertainty": p.get("uncertainty"), "units": p["units"],
            "method": p["method"], "flags": p.get("flags", []), "note": p.get("note", ""),
            "status": rep.status, "reasons": rep.reasons, "superseded_by": rep.superseded_by,
            "reference": _reference(refs, calibration, p["candidate"], p["property"]),
        })

    with_results = {pid for r in snap.of_type("property_result") for pid in r.parent_artifact_ids}
    proposed = [{"id": c.artifact_id, "agent": c.producer_agent, **c.payload}
                for c in snap.of_type("candidate") if c.artifact_id not in with_results]

    needs = [{"artifact_id": n.artifact.artifact_id, "index": n.index, "on_type": n.artifact.artifact_type,
              "kind": n.need.get("check_type") or n.need.get("artifact_type"), "query": n.need.get("query"),
              "rationale": n.need.get("rationale"), "claimed_by": n.claimed_by, "claim_expires_at": n.claim_expires_at}
             for n in board.open_needs(now=now)]

    runs = [{"id": r.artifact_id, "agent": r.producer_agent, "timestamp": r.timestamp, **r.payload}
            for r in snap.of_type("run_report")]

    ranking = None
    rankings = snap.of_type("ranking")
    if rankings:
        latest = rankings[-1]
        entries = sorted(latest.payload["entries"], key=lambda e: -e["score"])
        ranking = {
            "id": latest.artifact_id, "agent": latest.producer_agent, "timestamp": latest.timestamp,
            "criteria": latest.payload["criteria"],
            "entries": [{**e, "rank": i, "untrusted_result_ids": [rid for rid in e["result_ids"]
                                                                  if status_of.get(rid) != "trusted"]}
                        for i, e in enumerate(entries, 1)],
            "n_rankings": len(rankings),
        }

    scorecard = None
    if task_set is not None and task_requests(snap):
        scorecard = score_board(board, task_set, rules).to_dict()

    # Arm and repeat come from the board's folder (<arm>-r<k>) or, for the runners' per-task boards, its parent.
    arm_dir = Path(repeat_dir) if repeat_dir is not None else board.root
    m = ARM_RE.match(arm_dir.name)
    return {
        "schema": SCHEMA,
        "name": name,
        "path": str(board.root),
        "exported_at": (now or datetime.now(timezone.utc)).isoformat(),
        "synthetic": _is_synthetic(ordered),
        "rules_frozen": bool(rules.get("frozen")),
        "arm": m.group("arm") if m else None,
        "run": _board_name(arm_dir.parent) if m else None,
        "task_id": board.root.name if repeat_dir is not None else None,
        "repeat": int(m.group("repeat")) if m else None,
        "agents": sorted({a.producer_agent for a in ordered}),
        "counts": {"artifacts": len(ordered), "by_type": dict(Counter(a.artifact_type for a in ordered)),
                   "by_status": dict(Counter(status_of.values())), "open_needs": len(needs)},
        "results": results,
        "proposed": proposed,
        "open_needs": needs,
        "run_reports": runs,
        "ranking": ranking,
        "scorecard": scorecard,
        "lineage": lineage,
    }


def _context(rules: dict, calibration_path: Path) -> dict:
    try:
        return build_context(rules=rules, calibration_path=calibration_path)
    except Exception as exc:  # the boards still export; the page shows this error instead of the context sections
        return {"schema": CONTEXT_SCHEMA, "error": f"{type(exc).__name__}: {exc}"}


def export(board_paths: list[str | Path], out_dir: str | Path = DEFAULT_OUT, *, rules: Optional[dict] = None,
           task_set: Optional[TaskSet] = None, now: Optional[datetime] = None,
           include_synthetic_in_comparison: bool = False,
           calibration_path: Optional[str | Path] = None, context: Optional[dict] = None) -> dict:
    """Export boards. Arm comparison: repeats are grouped by (run folder, arm), never pooled across runs.

    Synthetic boards are left out of the comparison unless ``include_synthetic_in_comparison`` (the demo
    only); a comparison that includes them is marked ``synthetic`` and the page shows a banner.
    ``context`` defaults to ``dashboard.context.build_context()`` with the same rules and calibration set.
    """
    rules = rules if rules is not None else load_rules()
    task_set = task_set if task_set is not None else load_tasks()
    calibration_path = Path(calibration_path) if calibration_path else DEFAULT_CALIBRATION_PATH
    calibration = load_calibration(calibration_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)

    boards, entries = {}, []
    by_run: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    synthetic_runs: set[str] = set()
    excluded: list[str] = []
    repeats: dict[Path, dict] = {}  # per-task repeat folders: scored once, as a whole
    found = [hit for path in board_paths for hit in discover(path)]
    if not found:
        raise FileNotFoundError(f"no boards under {[str(p) for p in board_paths]}")
    for path, repeat_dir in found:
        board = Board(path)
        data = export_board(board, rules, task_set, now=now, repeat_dir=repeat_dir, calibration=calibration)
        if data["name"] in boards:
            raise ValueError(f"two boards export to the same name {data['name']!r}")
        boards[data["name"]] = data
        (out / f"{data['name']}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
        entries.append({"name": data["name"], "file": f"{data['name']}.json", "synthetic": data["synthetic"],
                        "arm": data["arm"], "repeat": data["repeat"], "run": data["run"], "task_id": data["task_id"],
                        "counts": data["counts"]})
        if repeat_dir is not None and data["arm"]:
            rec = repeats.setdefault(Path(repeat_dir), {"run": data["run"], "arm": data["arm"], "synthetic": False,
                                                         "names": []})
            rec["synthetic"] |= data["synthetic"]
            rec["names"].append(data["name"])
            continue
        if data["arm"] and data["scorecard"]:
            if data["synthetic"] and not include_synthetic_in_comparison:
                excluded.append(data["name"])
                continue
            run = data["run"]
            by_run[run][data["arm"]].append(score_board(board, task_set, rules))
            if data["synthetic"]:
                synthetic_runs.add(run)

    for repeat_dir, rec in sorted(repeats.items()):
        if rec["synthetic"] and not include_synthetic_in_comparison:
            excluded += rec["names"]
            continue
        by_run[rec["run"]][rec["arm"]].append(score_repeat(repeat_dir, task_set, rules))
        if rec["synthetic"]:
            synthetic_runs.add(rec["run"])

    comparison = [{"run": run, "synthetic": run in synthetic_runs,
                   "arms": {arm: summarize_arm(scores) for arm, scores in sorted(arms.items())}}
                  for run, arms in sorted(by_run.items())] or None
    index = {"schema": SCHEMA, "exported_at": now.isoformat(), "boards": entries, "comparison": comparison,
             "excluded_from_comparison": excluded,
             "rules_frozen": bool(rules.get("frozen")), "task_set_frozen": task_set.frozen,
             "any_synthetic": any(e["synthetic"] for e in entries),
             "tasks": _task_list(task_set), "scored_task_ids": _scored_task_ids(task_set)}
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    bundle = {"index": index, "boards": boards,
              "context": context if context is not None else _context(rules, calibration_path)}
    (out / "data.js").write_text("window.COOL_ORBIT_DATA = " + json.dumps(bundle) + ";\n", encoding="utf-8")
    return bundle


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m dashboard.export", description=__doc__.splitlines()[0])
    ap.add_argument("--board", action="append", required=True,
                    help="a board, an arm-repeat folder of per-task boards, or any folder above them; repeatable")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output directory (default: dashboard/data)")
    ap.add_argument("--rules", help="trust rules JSON (default: configs/trust_rules.json)")
    ap.add_argument("--tasks", help="eval task set (default: coolorbit/eval/tasks.json)")
    ap.add_argument("--calibration", help="calibration JSON (default: data/calibration/tier1.json)")
    args = ap.parse_args(argv)
    bundle = export(args.board, args.out, rules=load_rules(args.rules), task_set=load_tasks(args.tasks, args.calibration),
                    calibration_path=args.calibration)
    for e in bundle["index"]["boards"]:
        print(f"exported {e['name']}{' (SYNTHETIC)' if e['synthetic'] else ''}: {e['counts']['artifacts']} artifacts")
    print(f"wrote {args.out}/index.json and data.js; open dashboard/index.html")


if __name__ == "__main__":
    main()
