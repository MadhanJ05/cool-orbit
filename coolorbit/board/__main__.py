"""Command-line viewer for a results board.

    python -m coolorbit.board show  --board boards/<id> [-v]
    python -m coolorbit.board needs --board boards/<id>
    python -m coolorbit.board tree  --board boards/<id> <artifact-id-prefix>
"""

from __future__ import annotations

import argparse
from collections import Counter

from coolorbit.board import Board, derive_status, load_rules


def _value(p: dict) -> str:
    v = f"{p['value']:g}"
    if p.get("uncertainty") is not None:
        v += f" ± {p['uncertainty']:g}"
    return f"{v} {p['units']}"


def _method(m: dict) -> str:
    return " ".join(x for x in (f"{m['engine']}/{m['model']}", m.get("protocol"), m.get("ensemble")) if x)


def show(board: Board, rules: dict, verbose: bool) -> None:
    snap = board.snapshot()
    agents = {a.producer_agent for a in snap.artifacts.values()}
    print(f"Board {board.root}: {len(snap.artifacts)} artifacts from {len(agents)} agents")
    if not rules.get("frozen"):
        print("WARNING: trust tolerances are NOT frozen (placeholders pending the chemistry lead); statuses are provisional.")

    results = snap.of_type("property_result")
    with_results = {pid for r in results for pid in r.parent_artifact_ids}
    proposed = [c for c in snap.of_type("candidate") if c.artifact_id not in with_results]
    if proposed:
        print("\nPROPOSED (no results yet)")
        for c in proposed:
            print(f"  {c.artifact_id[:8]}  {c.payload['name']:<24} {c.payload['family']:<16} {c.payload['tier']:<9} by {c.producer_agent}")

    print("\nRESULTS")
    print(f"  {'id':<8}  {'candidate':<20} {'property':<15} {'value':<20} {'method':<44} {'by':<14} status")
    statuses: dict[str, str] = {}
    for r in results:
        rep = derive_status(snap, r, rules)
        statuses[r.artifact_id] = rep.status
        p = r.payload
        print(f"  {r.artifact_id[:8]}  {p['candidate']:<20} {p['property']:<15} {_value(p):<20} "
              f"{_method(p['method'])[:44]:<44} {r.producer_agent:<14} {rep.status.upper()}")
        if verbose:
            for reason in rep.reasons:
                print(f"            - {reason}")
    if results:
        print("  " + ", ".join(f"{n} {s}" for s, n in Counter(statuses.values()).most_common()))

    rankings = snap.of_type("ranking")
    if rankings:
        latest = rankings[-1]
        print(f"\nLATEST RANKING {latest.artifact_id[:8]} by {latest.producer_agent}: {latest.payload['criteria']}")
        for i, e in enumerate(sorted(latest.payload["entries"], key=lambda e: -e["score"]), 1):
            untrusted = [rid[:8] for rid in e["result_ids"] if statuses.get(rid) != "trusted"]
            warn = f"  !! relies on non-trusted result(s) {untrusted}" if untrusted else ""
            print(f"  {i}. {e['candidate']:<24} score {e['score']:g}{warn}")


def needs(board: Board) -> None:
    open_needs = board.open_needs()
    print(f"{len(open_needs)} open need(s) on {board.root}")
    for n in open_needs:
        who = f"claimed by {n.claimed_by} until {n.claim_expires_at}" if n.claimed_by else "UNCLAIMED"
        kind = n.need.get("check_type") or n.need["artifact_type"]
        print(f"  {n.artifact.artifact_id[:8]}#{n.index}  [{kind}] {n.need['query']}  ({who})")


def tree(board: Board, prefix: str) -> None:
    snap = board.snapshot()
    root = snap.find(prefix)

    def label(a) -> str:
        return f"{a.artifact_id[:8]} {a.artifact_type:<16} {a.producer_agent:<14} {a.summary}"

    chain, cur = [], root
    while cur.parent_artifact_ids:
        cur = snap.artifacts[cur.parent_artifact_ids[0]]
        chain.append(cur)
    for depth, a in enumerate(reversed(chain)):
        print("  " * depth + label(a))

    def down(a, depth: int) -> None:
        print("  " * depth + ("* " if a is root else "") + label(a))
        for kid in snap.children.get(a.artifact_id, []):
            down(kid, depth + 1)

    down(root, len(chain))


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m coolorbit.board", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["show", "needs", "tree"])
    ap.add_argument("artifact", nargs="?", help="artifact id prefix (for tree)")
    ap.add_argument("--board", required=True)
    ap.add_argument("--rules", default=None, help="trust rules JSON (default: configs/trust_rules.json)")
    ap.add_argument("-v", "--verbose", action="store_true", help="show why each result has its status")
    args = ap.parse_args()
    board = Board(args.board)
    if args.command == "show":
        show(board, load_rules(args.rules), args.verbose)
    elif args.command == "needs":
        needs(board)
    else:
        if not args.artifact:
            ap.error("tree needs an artifact id prefix")
        tree(board, args.artifact)


if __name__ == "__main__":
    main()
