"""Board integrity around one agent session: everything on the board is append-only, and only the session's
agent may append.

The container mounts the task board's ``artifacts/`` read-write (the ``./board`` tool needs to post), so an
agent *could* edit other agents' records directly. The runner snapshots the board before each session and
checks afterwards:

- every file that existed is unchanged or only grew by appending (old bytes are an exact prefix);
- only the agent's own store (``artifacts/<agent>/store.jsonl``) and the shared index
  (``artifacts/global_index.jsonl``) may grow; the lock file is ignored;
- every appended line in either file is a JSON record produced by this agent;
- no new files other than the agent's own store; nothing deleted.

Violations are recorded in ``run_record.json`` and the pre-session bytes of every touched file are saved
under ``<board>/integrity/<session>/`` as evidence. The check never "repairs" the board.
"""

from __future__ import annotations

import json
from pathlib import Path

INDEX = "global_index.jsonl"
IGNORED = {".lock"}


def snapshot(artifacts: Path) -> dict[str, bytes]:
    """{relative path: bytes} of every file under ``artifacts/`` (boards are small)."""
    artifacts = Path(artifacts)
    if not artifacts.is_dir():
        return {}
    return {str(p.relative_to(artifacts)): p.read_bytes()
            for p in sorted(artifacts.rglob("*")) if p.is_file() and p.name not in IGNORED}


def _appended_lines(new: bytes, old: bytes) -> list[str]:
    return [l for l in new[len(old):].decode("utf-8", errors="replace").splitlines() if l.strip()]


def _producer(line: str) -> str | None:
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    return rec.get("producer_agent") if isinstance(rec, dict) else None


def check(before: dict[str, bytes], artifacts: Path, agent: str) -> list[str]:
    """Violations (empty list = clean) for one session by ``agent``."""
    after = snapshot(artifacts)
    own = f"{agent}/store.jsonl"
    allowed_growth = {own, INDEX}
    problems = []
    for rel, old in before.items():
        if rel not in after:
            problems.append(f"{rel}: deleted")
            continue
        new = after[rel]
        if new == old:
            continue
        if not new.startswith(old):
            problems.append(f"{rel}: existing bytes were modified (not append-only)")
            continue
        if rel not in allowed_growth:
            problems.append(f"{rel}: another agent's file grew ({len(new) - len(old)} bytes appended by {agent})")
            continue
        for line in _appended_lines(new, old):
            who = _producer(line)
            if who != agent:
                problems.append(f"{rel}: appended a record by {who!r}, not {agent!r}")
    for rel in sorted(set(after) - set(before)):
        if rel != own and rel != INDEX:
            problems.append(f"{rel}: new file not allowed for {agent}")
            continue
        for line in _appended_lines(after[rel], b""):
            who = _producer(line)
            if who != agent:
                problems.append(f"{rel}: new record by {who!r}, not {agent!r}")
    return problems


def save_evidence(before: dict[str, bytes], board_root: Path, session: str, problems: list[str]) -> Path:
    """Keep the pre-session bytes of every file named in ``problems`` (plus the list itself)."""
    out = Path(board_root) / "integrity" / session
    out.mkdir(parents=True, exist_ok=True)
    touched = {p.split(":", 1)[0] for p in problems}
    for rel in touched:
        if rel in before:
            dest = out / "before" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(before[rel])
    (out / "violations.json").write_text(json.dumps(problems, indent=1), encoding="utf-8")
    return out
