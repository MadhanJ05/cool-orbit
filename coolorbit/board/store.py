"""The shared results board: an append-only artifact DAG on disk.

Layout (mirrors ScienceClaw's ``~/.scienceclaw/artifacts/``, but one directory per board
so experiment arms and repeats never see each other's work)::

    <board>/artifacts/<agent>/store.jsonl     full artifacts, one JSON object per line
    <board>/artifacts/global_index.jsonl      metadata + needs, shared by all agents

Artifacts are immutable. Nothing on the board is ever edited; status is derived
(see ``status.py``), and coordination happens through needs and claims.
Unlike ScienceClaw's ``ArtifactStore.save``, posting never contacts Infinite or
runs the Paraxiom signing layer.
"""

from __future__ import annotations

import fcntl
import json
import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from pydantic import BaseModel, ValidationError

from coolorbit.board._scienceclaw import Artifact
from coolorbit.board.schema import PAYLOAD_MODELS, Need

_AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class BoardError(ValueError):
    """A post would break the board's rules (bad payload, unknown parent, bad need index, ...)."""


@dataclass
class OpenNeed:
    artifact: Artifact
    index: int
    need: dict
    claimed_by: Optional[str] = None
    claim_expires_at: Optional[str] = None


@dataclass
class BoardSnapshot:
    """A consistent in-memory view of the board at one moment."""

    artifacts: dict[str, Artifact]
    children: dict[str, list[Artifact]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kids: dict[str, list[Artifact]] = defaultdict(list)
        for art in self.ordered():
            for parent in art.parent_artifact_ids:
                kids[parent].append(art)
        self.children = dict(kids)

    def ordered(self) -> list[Artifact]:
        return sorted(self.artifacts.values(), key=lambda a: (a.timestamp, a.artifact_id))

    def of_type(self, artifact_type: str) -> list[Artifact]:
        return [a for a in self.ordered() if a.artifact_type == artifact_type]

    def find(self, prefix: str) -> Artifact:
        matches = [a for aid, a in self.artifacts.items() if aid.startswith(prefix)]
        if len(matches) != 1:
            raise KeyError(f"{len(matches)} artifacts match id prefix {prefix!r}")
        return matches[0]


class Board:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.artifacts_dir = self.root / "artifacts"
        self.index_path = self.artifacts_dir / "global_index.jsonl"
        self._lock_path = self.artifacts_dir / ".lock"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ write

    def post(
        self,
        agent: str,
        artifact_type: str,
        payload: dict | BaseModel,
        *,
        parents: Iterable[str] = (),
        needs: Iterable[dict] = (),
        skill: str = "",
        investigation_id: str = "",
        fulfills: Optional[tuple[str, int]] = None,
        summary: str = "",
    ) -> Artifact:
        """Validate and append one artifact. Returns it (with its new artifact_id)."""
        if not _AGENT_RE.match(agent):
            raise BoardError(f"invalid agent name {agent!r}")
        model = PAYLOAD_MODELS.get(artifact_type)
        if model is None:
            raise BoardError(f"unknown artifact type {artifact_type!r}; expected one of {sorted(PAYLOAD_MODELS)}")
        if isinstance(payload, BaseModel):
            payload = payload.model_dump()
        try:
            data = model.model_validate(payload).model_dump(mode="json")
            need_dicts = [Need.model_validate(n).model_dump(mode="json") for n in needs]
        except ValidationError as exc:
            raise BoardError(f"invalid {artifact_type}: {exc}") from exc

        parent_ids = list(dict.fromkeys(parents))
        with self._locked():
            entries = {e["artifact_id"]: e for e in self._read_index()}
            if fulfills is not None:
                target_id, need_index = fulfills
                target_needs = entries.get(target_id, {}).get("needs", [])
                if not 0 <= need_index < len(target_needs):
                    raise BoardError(f"artifact {target_id} has no need #{need_index}")
                need = target_needs[need_index]
                data["_fulfilled_need"] = {
                    "parent_artifact_id": target_id,
                    "artifact_type": need.get("artifact_type", ""),
                    "query": need.get("query", ""),
                }
                data["_need_index"] = need_index
                if target_id not in parent_ids:
                    parent_ids.append(target_id)
            missing = [p for p in parent_ids if p not in entries]
            if missing:
                raise BoardError(f"unknown parent artifact(s): {missing}")
            art = Artifact.create(
                artifact_type=artifact_type,
                producer_agent=agent,
                skill_used=skill or f"coolorbit.{artifact_type}",
                payload=data,
                investigation_id=investigation_id,
                parent_artifact_ids=parent_ids,
                needs=need_dicts,
                summary=summary or _auto_summary(artifact_type, data),
            )
            self._append(art)
        return art

    def claim(self, agent: str, target_id: str, need_index: int, ttl_minutes: float = 60.0) -> bool:
        """Claim an open need. Returns True only if this agent holds the winning (earliest live) claim."""
        if (target_id, need_index) in self._fulfilled(self.snapshot()):
            return False
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        self.post(
            agent,
            "need_claim",
            {"target_artifact_id": target_id, "need_index": need_index, "expires_at": expires.isoformat()},
            parents=[target_id],
        )
        winner = self._live_claims(self.snapshot(), datetime.now(timezone.utc)).get((target_id, need_index))
        return winner is not None and winner.producer_agent == agent

    # ------------------------------------------------------------------- read

    def snapshot(self) -> BoardSnapshot:
        arts: dict[str, Artifact] = {}
        for store in sorted(self.artifacts_dir.glob("*/store.jsonl")):
            for record in _read_jsonl(store):
                arts[record["artifact_id"]] = Artifact.from_dict(record)
        return BoardSnapshot(arts)

    def index(self) -> list[dict]:
        return list(self._read_index())

    def open_needs(self, *, include_claimed: bool = True, now: Optional[datetime] = None) -> list[OpenNeed]:
        snap = self.snapshot()
        now = now or datetime.now(timezone.utc)
        fulfilled = self._fulfilled(snap)
        claims = self._live_claims(snap, now)
        out = []
        for art in snap.ordered():
            for i, need in enumerate(art.needs):
                if (art.artifact_id, i) in fulfilled:
                    continue
                claim = claims.get((art.artifact_id, i))
                if claim is not None and not include_claimed:
                    continue
                out.append(
                    OpenNeed(
                        artifact=art,
                        index=i,
                        need=need,
                        claimed_by=claim.producer_agent if claim else None,
                        claim_expires_at=claim.payload["expires_at"] if claim else None,
                    )
                )
        return out

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _fulfilled(snap: BoardSnapshot) -> set[tuple[str, int]]:
        done = set()
        for art in snap.artifacts.values():
            fn = art.payload.get("_fulfilled_need") if isinstance(art.payload, dict) else None
            if isinstance(fn, dict):
                done.add((fn["parent_artifact_id"], art.payload["_need_index"]))
        return done

    @staticmethod
    def _live_claims(snap: BoardSnapshot, now: datetime) -> dict[tuple[str, int], Artifact]:
        """Earliest unexpired claim per need (ties broken by artifact_id)."""
        winners: dict[tuple[str, int], Artifact] = {}
        for claim in snap.of_type("need_claim"):  # ordered by (timestamp, id)
            p = claim.payload
            if datetime.fromisoformat(p["expires_at"]) <= now:
                continue
            winners.setdefault((p["target_artifact_id"], p["need_index"]), claim)
        return winners

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with open(self._lock_path, "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read_index(self) -> Iterator[dict]:
        if self.index_path.exists():
            yield from _read_jsonl(self.index_path)

    def _append(self, art: Artifact) -> None:
        """Write the full record to the agent's store, then the metadata to the shared index."""
        store = self.artifacts_dir / art.producer_agent / "store.jsonl"
        store.parent.mkdir(exist_ok=True)
        with open(store, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(art.to_dict(), ensure_ascii=False) + "\n")
        fn = art.payload.get("_fulfilled_need") or {}
        entry = {  # same fields as ScienceClaw's ArtifactStore._append_global_index, plus summary
            "artifact_id": art.artifact_id,
            "artifact_type": art.artifact_type,
            "producer_agent": art.producer_agent,
            "skill_used": art.skill_used,
            "investigation_id": art.investigation_id,
            "timestamp": art.timestamp,
            "content_hash": art.content_hash,
            "parent_artifact_ids": art.parent_artifact_ids,
            "needs": art.needs,
            "fulfilled_need_parent_id": fn.get("parent_artifact_id", ""),
            "fulfilled_need_index": art.payload.get("_need_index"),
            "fulfilled_need_type": fn.get("artifact_type", ""),
            "fulfilled_need_query": fn.get("query", "")[:180],
            "fulfillment_variant": {},
            "summary": art.summary,
        }
        with open(self.index_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _auto_summary(artifact_type: str, p: dict) -> str:
    if artifact_type == "candidate":
        return f"candidate {p['name']} ({p['family']}, {p['tier']} tier)"
    if artifact_type == "property_result":
        m = p["method"]
        return f"{p['candidate']} {p['property']} = {p['value']:g} {p['units']} via {m['engine']}/{m['model']} {m['protocol']}"
    if artifact_type == "check":
        return f"{p['check_type']}: {p['verdict']}"
    if artifact_type == "reference_value":
        if p["value"] is None:
            return f"no sourced measured {p['property']} found for {p['candidate']}"
        return f"measured {p['candidate']} {p['property']} = {p['value']:g} {p['units']} [{p['source']}]"
    if artifact_type == "ranking":
        return f"ranking of {len(p['entries'])} candidates"
    if artifact_type == "need_claim":
        return f"claims need #{p['need_index']} on {p['target_artifact_id'][:8]}"
    if artifact_type == "request":
        return f"request: {p['note'][:100]}" if p["note"] else "request"
    if artifact_type == "run_report":
        return f"{p['status']}: {p['task']} ({p['reason'][:100]})"
    return artifact_type
