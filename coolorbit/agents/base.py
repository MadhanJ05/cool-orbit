"""Shared plumbing for Cool Orbit agents.

Every agent is an independent process that only talks to the results board: it polls the board,
claims open needs it can serve, does its job and posts. There is no boss and no direct messaging.
Every decision (claims, posts, skips with a reason) is appended to ``<board>/logs/<agent>.jsonl``,
because the trajectory itself is judged evidence.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from coolorbit.board import Board, BoardSnapshot, OpenNeed
from coolorbit.board._scienceclaw import Artifact

ORGANIC_ELEMENTS = frozenset({"C", "H", "O", "N"})


def norm(text: object) -> str:
    """Name normalisation used by the board's trust rule (case and extra spaces ignored)."""
    return " ".join(str(text or "").lower().split())


def candidate_of(snap: BoardSnapshot, art: Artifact) -> Optional[Artifact]:
    """The artifact itself if it is a candidate, else the nearest candidate ancestor."""
    seen, queue = {art.artifact_id}, [art]
    while queue:
        cur = queue.pop(0)
        if cur.artifact_type == "candidate":
            return cur
        for pid in cur.parent_artifact_ids:
            if pid not in seen and pid in snap.artifacts:
                seen.add(pid)
                queue.append(snap.artifacts[pid])
    return None


def formula_elements(formula: Optional[str]) -> set[str]:
    """Element symbols in a formula string like 'C20H42' or 'CaCl2.6H2O' (empty if unknown)."""
    import re

    return set(re.findall(r"[A-Z][a-z]?", formula or ""))


class Agent:
    """Base class. Subclasses implement ``step()``: do at most one unit of work, return True if they did."""

    role = "agent"

    def __init__(self, board: Board, name: str, *, log_dir: Optional[Path] = None, verbose: bool = True) -> None:
        self.board = board
        self.name = name
        self.verbose = verbose
        self.log_path = Path(log_dir or board.root / "logs") / f"{name}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ logging

    def log(self, event: str, **data) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "agent": self.name,
               "event": event, **data}
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        if self.verbose:
            extra = ", ".join(f"{k}={v}" for k, v in data.items() if k not in ("payload",))
            print(f"[{self.name}] {event}: {extra}", flush=True)

    # ------------------------------------------------------------------ board helpers

    def claim(self, on: OpenNeed, ttl_minutes: float = 30.0) -> bool:
        ok = self.board.claim(self.name, on.artifact.artifact_id, on.index, ttl_minutes=ttl_minutes)
        self.log("claim" if ok else "claim_lost", target=on.artifact.artifact_id[:8], need_index=on.index,
                 query=on.need.get("query"))
        return ok

    def post(self, artifact_type: str, payload: dict, **kw) -> Artifact:
        art = self.board.post(self.name, artifact_type, payload, **kw)
        self.log("post", artifact_type=artifact_type, id=art.artifact_id[:8], summary=art.summary,
                 parents=[p[:8] for p in art.parent_artifact_ids],
                 fulfills=(kw["fulfills"][0][:8], kw["fulfills"][1]) if kw.get("fulfills") else None)
        return art

    def open_needs(self, predicate: Callable[[OpenNeed], bool]) -> list[OpenNeed]:
        return [on for on in self.board.open_needs(include_claimed=False) if predicate(on)]

    # ------------------------------------------------------------------ loop

    def step(self) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def drain(self, max_steps: int = 1000) -> int:
        """Run ``step`` until there is nothing left to do. Returns the number of steps that did work."""
        n = 0
        while n < max_steps and self.step():
            n += 1
        return n

    def run(self, *, once: bool = False, poll_s: float = 30.0) -> None:
        self.log("start", board=str(self.board.root), once=once)
        if once:
            self.drain()
            return
        while True:
            if not self.step():
                time.sleep(poll_s)


def run_round_robin(agents: Iterable[Agent], max_rounds: int = 100) -> int:
    """Single-process helper for laptop development: step every agent in turn until a full round is idle.

    In production each agent runs as its own process (``python -m coolorbit.agents.<agent>``).
    """
    agents = list(agents)
    for rnd in range(1, max_rounds + 1):
        busy = [a.step() for a in agents]
        if not any(busy):
            return rnd
    return max_rounds


def agent_cli(description: str, build: Callable[[argparse.Namespace, Board], Agent],
              add_args: Optional[Callable[[argparse.ArgumentParser], None]] = None,
              argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--board", required=True, help="board directory")
    ap.add_argument("--once", action="store_true", help="do all available work, then exit")
    ap.add_argument("--poll", type=float, default=30.0, help="seconds between polls when idle")
    if add_args:
        add_args(ap)
    args = ap.parse_args(argv)
    agent = build(args, Board(args.board))
    agent.run(once=args.once, poll_s=args.poll)
