"""Ranker: owns the shortlist. Ranks candidates from TRUSTED results only.

A candidate is ranked when it has a trusted transition temperature inside the operating window AND a
trusted latent heat for the same transition (solid-liquid or solid-solid). Score = latent heat in kJ/kg
(primary criterion; launch cost scales with mass); trusted conductivity is reported and breaks ties.
Everything else is listed in the ranking's criteria text as excluded, with the reason, so the gap is
visible. A new ``ranking`` is posted only when the shortlist changes.

    python -m coolorbit.agents.ranker --board boards/<id> [--once]
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

from coolorbit.agents.base import Agent, agent_cli, norm
from coolorbit.board import Board, BoardSnapshot, load_rules, summarize
from coolorbit.board._scienceclaw import Artifact

CONFIG_PATH = Path(__file__).with_name("ranker_config.json")
AGENT_NAME = "ranker"


def load_ranker_config(path: str | Path = CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text())


SYNTHETIC_FLAG = "synthetic_test_data"


def rank(snap: BoardSnapshot, trust_rules: dict, cfg: dict, include_synthetic: bool = False) -> tuple[list[dict], list[str]]:
    """Pure function: (entries best-first, exclusion notes). Synthetic (FakeSimulator) results are skipped
    unless ``include_synthetic`` (dev boards only); then every entry using one is labelled SYNTHETIC."""
    trusted: dict[str, dict[str, Artifact]] = defaultdict(dict)
    names: dict[str, str] = {}
    for art, report in summarize(snap, trust_rules):
        if report.status != "trusted" or report.superseded_by:
            continue
        if SYNTHETIC_FLAG in art.payload.get("flags", []) and not include_synthetic:
            continue
        key = norm(art.payload["candidate"])
        names.setdefault(key, art.payload["candidate"])
        trusted[key][art.payload["property"]] = art  # summarize is ordered, so the latest wins

    lo, hi = cfg["window_K"]["min"], cfg["window_K"]["max"]
    entries, excluded = [], []
    for key, props in sorted(trusted.items()):
        name = names[key]
        best = None
        for pair in cfg["transition_pairs"]:
            t, lh = props.get(pair["temperature"]), props.get(pair["latent_heat"])
            if t is None:
                continue
            tv = t.payload["value"]
            if not lo <= tv <= hi:
                excluded.append(f"{name}: trusted {pair['temperature']} {tv:g} K outside window [{lo:g}, {hi:g}] K")
                continue
            if lh is None:
                excluded.append(f"{name}: no trusted {pair['latent_heat']} yet")
                continue
            score = lh.payload["value"] / 1000.0
            if best is None or score > best["score"]:
                best = {"pair": pair, "t": t, "lh": lh, "score": score}
        if best is None:
            if not any(props.get(p["temperature"]) for p in cfg["transition_pairs"]):
                excluded.append(f"{name}: no trusted transition temperature yet")
            continue
        ids = [best["t"].artifact_id, best["lh"].artifact_id]
        k = props.get("conductivity")
        if k is not None:
            ids.append(k.artifact_id)
        k_text = f"{k.payload['value']:g} W/m/K" if k else "not trusted yet"
        note = (f"{best['pair']['kind']}: T = {best['t'].payload['value']:g} K, "
                f"L = {best['score']:.1f} kJ/kg, k = {k_text}")
        if any(SYNTHETIC_FLAG in best[x].payload.get("flags", []) for x in ("t", "lh")):
            note += " [SYNTHETIC test data, not a result]"
        if best["t"].payload.get("tier") == "discovery" or best["lh"].payload.get("tier") == "discovery":
            note += " [PREDICTION: discovery tier, not measured]"
        entries.append({"candidate": name, "score": round(best["score"], 3), "result_ids": ids, "note": note,
                        "_k": k.payload["value"] if k else -1.0})
    entries.sort(key=lambda e: (-e["score"], -e["_k"], e["candidate"]))
    for e in entries:
        e.pop("_k")
    return entries, excluded


class Ranker(Agent):
    role = "ranker"

    def __init__(self, board: Board, name: str = AGENT_NAME, *, trust_rules: Optional[dict] = None,
                 config: Optional[dict] = None, include_synthetic: bool = False, **kw) -> None:
        super().__init__(board, name, **kw)
        self.include_synthetic = include_synthetic
        self.trust_rules = trust_rules
        self.cfg = config if config is not None else load_ranker_config()
        self._last_excluded: Optional[list[str]] = None

    def _latest_mine(self, snap: BoardSnapshot) -> Optional[Artifact]:
        mine = [a for a in snap.of_type("ranking") if a.producer_agent == self.name]
        return mine[-1] if mine else None

    def step(self) -> bool:
        snap = self.board.snapshot()
        rules = self.trust_rules if self.trust_rules is not None else load_rules()
        entries, excluded = rank(snap, rules, self.cfg, self.include_synthetic)
        if excluded != self._last_excluded:
            self._last_excluded = excluded
            if excluded:
                self.log("excluded", reasons=excluded)
        last = self._latest_mine(snap)
        sig = [(e["candidate"], e["score"], tuple(e["result_ids"])) for e in entries]
        if last is not None:
            old = [(e["candidate"], e["score"], tuple(e["result_ids"])) for e in last.payload["entries"]]
            if old == sig:
                return False
        elif not entries:
            return False
        lo, hi = self.cfg["window_K"]["min"], self.cfg["window_K"]["max"]
        criteria = (f"trusted results only; transition temperature in [{lo:g}, {hi:g}] K"
                    f"{'' if self.cfg.get('decided') else ' (PLACEHOLDER window, team decision pending)'}; "
                    f"score = latent heat (kJ/kg); ties broken by trusted conductivity.")
        if excluded:
            criteria += " Excluded: " + "; ".join(excluded)
        parents = sorted({i for e in entries for i in e["result_ids"]})
        self.post("ranking", {"criteria": criteria[:4000], "entries": entries}, parents=parents,
                  skill="coolorbit.ranker")
        self.log("decision", ranked=[f"{e['candidate']}={e['score']}" for e in entries])
        return True


def main(argv: Optional[list[str]] = None) -> None:
    agent_cli(__doc__.split("\n\n")[0], lambda a, b: Ranker(b), argv=argv)


if __name__ == "__main__":
    main()
