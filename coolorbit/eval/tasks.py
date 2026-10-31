"""Comparison-experiment task set: load, validate, resolve references, seed a board.

The task set lives in ``coolorbit/eval/tasks.json`` (see its ``_note``). Each task is
posted on an arm's board as one ``request`` artifact by ``eval_harness``; everything
an arm does for that task must descend from it (lineage), which is how
``coolorbit.eval.metrics`` attributes results to tasks without editing the board.

The task's need asks for a ``candidate`` (not a ``property_result``), so the GB10
Simulator, which only claims ``property_result`` needs on candidates, never picks
up a raw task. A Scout (or the single agent) proposes the candidate with the task
request as its parent, then asks for the simulation as usual
(``docs/simulation-requests.md``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from coolorbit.board._scienceclaw import REPO_ROOT

if TYPE_CHECKING:
    from coolorbit.board import Board, BoardSnapshot
    from coolorbit.board._scienceclaw import Artifact

DEFAULT_TASKS_PATH = Path(__file__).with_name("tasks.json")
DEFAULT_CALIBRATION_PATH = REPO_ROOT / "data" / "calibration" / "tier1.json"
HARNESS_AGENT = "eval_harness"
TASK_NOTE_PREFIX = "eval_task:"

Kind = Literal["well_posed", "ill_posed", "trap"]
Expected = Literal["answer", "refuse", "answer_or_refuse"]


class _Loose(BaseModel):
    """Keys starting with '_' are comments and are kept; anything else unknown is an error."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _only_comment_extras(self):
        bad = [k for k in (self.model_extra or {}) if not k.startswith("_")]
        if bad:
            raise ValueError(f"unknown field(s) {bad}")
        return self


class Signature(_Loose):
    """The trap fired if the dotted ``field`` of a property_result contains ``contains`` (case-insensitive)."""

    field: str = Field(min_length=1)
    contains: str = Field(min_length=1)


class CalibrationKey(_Loose):
    candidate: str = Field(min_length=1)
    property: str = Field(min_length=1)


class Reference(_Loose):
    """A sourced measured value (point or range), or a pointer into the calibration dataset."""

    value: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None
    uncertainty: Optional[float] = Field(default=None, ge=0)
    source: Optional[str] = None
    note: str = ""
    from_calibration: Optional[CalibrationKey] = None

    @model_validator(mode="after")
    def _sourced(self):
        has_point = self.value is not None
        has_range = self.low is not None or self.high is not None
        if has_point and has_range:
            raise ValueError("give either value or low/high, not both")
        if has_range and (self.low is None or self.high is None or self.low > self.high):
            raise ValueError("a range needs low <= high")
        if (has_point or has_range) and not (self.source and self.source.strip()):
            raise ValueError("a reference value needs a source; use null if none was found")
        if self.from_calibration is not None and (has_point or has_range):
            raise ValueError("from_calibration references must not also carry a value")
        return self

    @property
    def known(self) -> bool:
        return self.value is not None or self.low is not None


class Tolerance(_Loose):
    """|delta| <= max(abs, rel*|ref|); or within a multiplicative factor; or taken from trust_rules.json."""

    abs: Optional[float] = Field(default=None, ge=0)
    rel: Optional[float] = Field(default=None, ge=0)
    factor: Optional[float] = Field(default=None, gt=1)
    from_trust_rules: Optional[Literal["tier1", "agree"]] = None

    @model_validator(mode="after")
    def _one_kind(self):
        if not any(v is not None for v in (self.abs, self.rel, self.factor, self.from_trust_rules)):
            raise ValueError("tolerance needs abs, rel, factor or from_trust_rules")
        return self


class Task(_Loose):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    origin: Literal["nvidia_blog", "team", "blind"]
    kind: Kind
    trap: Optional[str] = None
    trap_signature: list[Signature] = Field(default_factory=list)
    expected: Expected
    prompt: str = Field(min_length=10)
    material: str = Field(min_length=1)
    property: str = Field(min_length=1)
    units: str = Field(min_length=1)
    reference: Optional[Reference] = None
    tolerance: Optional[Tolerance] = None
    nvidia_published: Optional[dict] = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.kind == "well_posed" and (self.trap or self.trap_signature):
            raise ValueError(f"{self.id}: a well-posed control has no trap")
        if self.kind == "well_posed" and self.expected != "answer":
            raise ValueError(f"{self.id}: a well-posed control expects an answer")
        if self.kind == "trap" and not (self.trap and self.trap_signature):
            raise ValueError(f"{self.id}: a trap task needs a trap name and a trap_signature")
        if self.kind == "ill_posed":
            if self.expected != "refuse":
                raise ValueError(f"{self.id}: an ill-posed task expects a refusal")
            if self.reference is not None and self.reference.known:
                raise ValueError(f"{self.id}: an ill-posed task has no reference value")
        return self


class TaskSet(_Loose):
    version: int
    frozen: bool
    tasks: list[Task] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self):
        ids = [t.id for t in self.tasks]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate task ids: {dupes}")
        return self

    def by_id(self) -> dict[str, Task]:
        return {t.id: t for t in self.tasks}


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def _calibration_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["records"] if isinstance(data, dict) else data


def resolve_references(task_set: TaskSet, calibration_path: Optional[str | Path] = None) -> list[str]:
    """Fill ``from_calibration`` references from Teammate 1's dataset. Returns task ids still unresolved.

    Only records with a non-null value and a source are used; units must match the task.
    """
    records = _calibration_records(Path(calibration_path or DEFAULT_CALIBRATION_PATH))
    unresolved = []
    for task in task_set.tasks:
        ref = task.reference
        if ref is None or ref.from_calibration is None:
            continue
        key = ref.from_calibration
        hits = [r for r in records
                if _norm(r.get("candidate")) == _norm(key.candidate) and r.get("property") == key.property
                and r.get("value") is not None and (r.get("source") or "").strip()]
        if not hits:
            unresolved.append(task.id)
            continue
        if len(hits) > 1:
            raise ValueError(f"{task.id}: {len(hits)} calibration records match {key.candidate}/{key.property}; "
                             "the task set needs exactly one")
        rec = hits[0]
        if rec.get("units") != task.units:
            raise ValueError(f"{task.id}: calibration units {rec.get('units')!r} != task units {task.units!r}")
        ref.value, ref.uncertainty, ref.source = rec["value"], rec.get("uncertainty"), rec["source"]
        ref.note = f"from data/calibration ({key.candidate}/{key.property})" + (f"; {rec['note']}" if rec.get("note") else "")
    return unresolved


def load_tasks(path: Optional[str | Path] = None, calibration_path: Optional[str | Path] = None) -> TaskSet:
    task_set = TaskSet.model_validate(json.loads(Path(path or DEFAULT_TASKS_PATH).read_text(encoding="utf-8")))
    resolve_references(task_set, calibration_path)
    return task_set


def seed_board(board: "Board", task_set: TaskSet, *, agent: str = HARNESS_AGENT) -> dict[str, str]:
    """Post one ``request`` per task. Returns {task_id: request artifact id}.

    Call once per (arm, repeat) board, before the arm starts. Refuses to seed a board twice.
    """
    if task_requests(board.snapshot()):
        raise ValueError(f"board {board.root} already has eval tasks; use a fresh board per arm and repeat")
    out = {}
    for task in task_set.tasks:
        art = board.post(
            agent, "request", {"note": f"{TASK_NOTE_PREFIX}{task.id}"},
            needs=[{"artifact_type": "candidate",
                    "query": task.prompt,
                    "rationale": f"evaluation task {task.id}: propose the material as a candidate (parent: this "
                                 "request), then answer it on this board",
                    "eval_task": task.id, "property": task.property, "units": task.units}],
            summary=f"eval task {task.id}: {task.prompt}",
        )
        out[task.id] = art.artifact_id
    return out


def task_requests(snap: "BoardSnapshot") -> dict[str, "Artifact"]:
    """{task_id: request artifact} for every task the harness posted on this board."""
    out = {}
    for art in snap.of_type("request"):
        note = art.payload.get("note", "")
        if art.producer_agent == HARNESS_AGENT and note.startswith(TASK_NOTE_PREFIX):
            out[note[len(TASK_NOTE_PREFIX):]] = art
    return out
