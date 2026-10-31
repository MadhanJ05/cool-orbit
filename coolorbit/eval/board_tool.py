"""The board tool agents use during comparison runs (arms A and B).

The runner writes a ``board`` wrapper script into each sandbox that runs this module with
the board path and agent name fixed in the environment, so an agent can only post as
itself (the worker can't post as the checker) and only to its own task's board.

    ./board show                      # every artifact on this board + open needs
    ./board get <id-prefix>           # one artifact, full JSON
    ./board post <type> '<json payload>' [--parent ID ...] [--fulfills ID:N]
    ./board post <type> @payload.json  ...

Types and fields: candidate, property_result, check, run_report (see ``./board schema``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from coolorbit.board import Board, BoardError
from coolorbit.board.schema import PAYLOAD_MODELS

ENV_BOARD = "COOLORBIT_BOARD"
ENV_AGENT = "COOLORBIT_AGENT"
AGENT_TYPES = ("candidate", "property_result", "check", "run_report")


def _load_payload(text: str) -> dict:
    if text.startswith("@"):
        text = Path(text[1:]).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    return data


def show(board: Board) -> str:
    snap = board.snapshot()
    lines = [f"{len(snap.artifacts)} artifacts on this board"]
    for a in snap.ordered():
        parents = ",".join(p[:8] for p in a.parent_artifact_ids) or "-"
        lines.append(f"{a.artifact_id}  {a.artifact_type:<16} by {a.producer_agent:<14} parents {parents}  {a.summary}")
    needs = board.open_needs()
    lines.append(f"\n{len(needs)} open need(s)")
    for n in needs:
        lines.append(f"{n.artifact.artifact_id}#{n.index}  [{n.need.get('check_type') or n.need['artifact_type']}] "
                     f"{n.need['query']}")
    return "\n".join(lines)


def schema_text() -> str:
    return "\n\n".join(f"{t}:\n{json.dumps(PAYLOAD_MODELS[t].model_json_schema(), indent=1)}" for t in AGENT_TYPES)


def main(argv: Optional[list[str]] = None) -> int:
    board_path, agent = os.environ.get(ENV_BOARD), os.environ.get(ENV_AGENT)
    if not board_path or not agent:
        print(f"error: {ENV_BOARD} and {ENV_AGENT} must be set (use the ./board wrapper)", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(prog="./board", description="Post to and read this task's results board.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    sub.add_parser("schema")
    g = sub.add_parser("get")
    g.add_argument("id")
    p = sub.add_parser("post")
    p.add_argument("type", choices=AGENT_TYPES)
    p.add_argument("payload", help="JSON object, or @file.json")
    p.add_argument("--parent", action="append", default=[], help="parent artifact id (repeatable)")
    p.add_argument("--fulfills", help="ID:N, the need this artifact fulfils")
    args = ap.parse_args(argv)

    board = Board(board_path)
    try:
        if args.cmd == "show":
            print(show(board))
        elif args.cmd == "schema":
            print(schema_text())
        elif args.cmd == "get":
            print(json.dumps(board.snapshot().find(args.id).to_dict(), indent=1))
        else:
            fulfills = None
            if args.fulfills:
                target, _, idx = args.fulfills.rpartition(":")
                fulfills = (target, int(idx))
            art = board.post(agent, args.type, _load_payload(args.payload), parents=args.parent, fulfills=fulfills)
            print(f"posted {art.artifact_type} {art.artifact_id}")
    except (BoardError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
