"""Scout: owns one material family. Proposes candidates and requests simulations on the board.

Each Scout is independent: it decides which of its family's candidates to post (within its budget),
routes each one to an engine that can handle it (ALCHEMI for in-domain inorganic crystals, classical MD
for molecular organics), and follows up on its own candidates (latent heat once a melting point exists).
Requests follow docs/simulation-requests.md.

    python -m coolorbit.agents.scout --board boards/<id> --scout scout_metals [--once]
    python -m coolorbit.agents.scout --list
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from coolorbit.agents.base import Agent, agent_cli, candidate_of, norm
from coolorbit.agents.librarian import DEFAULT_DATA, index_records, load_calibration
from coolorbit.board import Board, derive_status, load_rules
from coolorbit.board._scienceclaw import Artifact

SEEDS_PATH = Path(__file__).with_name("scout_seeds.json")
TRANSITION_PROPS = {"melting_point": "latent_heat", "transition_temperature": "transition_latent_heat"}


def load_seeds(path: str | Path = SEEDS_PATH) -> dict:
    return json.loads(Path(path).read_text())["scouts"]


class Scout(Agent):
    role = "scout"

    def __init__(self, board: Board, name: str, *, seeds: Optional[dict] = None, calibration: Optional[list] = None,
                 rules: Optional[dict] = None, preset: Optional[str] = None, **kw) -> None:
        super().__init__(board, name, **kw)
        all_seeds = seeds if seeds is not None else load_seeds()
        if name not in all_seeds:
            raise ValueError(f"no seeds for {name!r}; known scouts: {sorted(all_seeds)}")
        self.config = all_seeds[name]
        self.enabled = self.config.get("enabled", True)
        self.budget = int(self.config.get("budget", len(self.config["candidates"])))
        self.refs = index_records(calibration if calibration is not None else load_calibration(DEFAULT_DATA))
        self.rules = rules
        self.preset = preset  # "quick" for pipeline tests; None = the Simulator's default (production)

    # ------------------------------------------------------------------ helpers

    def _mine(self) -> list[Artifact]:
        return [a for a in self.board.snapshot().of_type("candidate") if a.producer_agent == self.name]

    def _guess(self, name: str, prop: str) -> Optional[float]:
        rec = self.refs.get((norm(name), prop))
        return rec["value"] if rec and rec["value"] is not None else None

    def _request(self, seed: dict, prop: str, **fields) -> Optional[dict]:
        need = {"artifact_type": "property_result", "property": prop,
                "query": f"{prop} of {seed['name']}", "rationale": "", **fields}
        if seed.get("route") == "classical_md":
            need["engine"] = "classical_md"
        if self.preset:
            need["preset"] = self.preset
        if prop in TRANSITION_PROPS:
            guess = self._guess(seed["name"], prop)
            if guess is None:
                self.log("skip_request", candidate=seed["name"], property=prop,
                         reason="no literature value to centre the bracket on")
                return None
            need["t_guess_K"] = round(guess)
            need["rationale"] = (f"{seed['family']} candidate; two-phase coexistence bracket centred on the "
                                 f"literature value ({round(guess)} K)")
        return need

    # ------------------------------------------------------------------ work

    def step(self) -> bool:
        if not self.enabled:
            return False
        return self._propose() or self._follow_up()

    def _propose(self) -> bool:
        """Post the next seed candidate not yet on the board (one per step, within budget)."""
        mine = self._mine()
        if len(mine) >= self.budget:
            return False
        posted = {norm(a.payload["name"]) for a in mine}
        for seed in self.config["candidates"]:
            if norm(seed["name"]) in posted:
                continue
            needs = []
            # Latent heat is requested later, at the melting point the run finds (see _follow_up).
            first = [p for p in seed.get("requests", []) if p in TRANSITION_PROPS]
            if seed.get("route") and seed.get("structure_source"):
                needs = [n for n in (self._request(seed, p) for p in first) if n]
            payload = {k: seed[k] for k in ("name", "family", "tier", "formula", "structure_source", "rationale")
                       if seed.get(k) is not None}
            art = self.post("candidate", payload, needs=needs, skill=f"coolorbit.scout.{seed['family']}")
            if not needs:
                why = "no runnable structure yet (add a periodic cell under data/structures/)" \
                    if not seed.get("structure_source") or not seed.get("route") else "nothing requested"
                self.log("decision", candidate=seed["name"], action="posted without requests", reason=why)
            else:
                self.log("decision", candidate=seed["name"], action=f"requested {[n['property'] for n in needs]}",
                         route=seed.get("route"), id=art.artifact_id[:8])
            return True
        return False

    def _follow_up(self) -> bool:
        """Once a usable melting point exists for one of my candidates, request its latent heat there."""
        snap = self.board.snapshot()
        seeds = {norm(s["name"]): s for s in self.config["candidates"]}
        rules = self.rules if self.rules is not None else load_rules()
        for cand in self._mine():
            seed = seeds.get(norm(cand.payload["name"]))
            if not seed or not seed.get("route"):
                continue
            kids = [k for k in snap.children.get(cand.artifact_id, [])]
            asked = {n.get("property") for k in [cand, *kids] for n in k.needs}
            for t_prop, l_prop in TRANSITION_PROPS.items():
                if l_prop not in seed.get("requests", []) or l_prop in asked:
                    continue
                results = [k for k in kids if k.artifact_type == "property_result" and k.payload["property"] == t_prop]
                usable = [r for r in results if derive_status(snap, r, rules).status not in ("flagged", "rejected")]
                if not usable:
                    continue
                tm = usable[-1].payload["value"]
                need = self._request(seed, l_prop, temperature_K=round(tm, 1))
                need["rationale"] = (f"second figure-of-merit input, at the simulated {t_prop} "
                                     f"({tm:g} K, result {usable[-1].artifact_id[:8]})")
                self.post("request", {"note": f"{l_prop} of {seed['name']} at the simulated {t_prop}"},
                          parents=[cand.artifact_id, usable[-1].artifact_id], needs=[need])
                self.log("decision", candidate=seed["name"], action=f"requested {l_prop}", at_K=tm)
                return True
        return False


def main(argv: Optional[list[str]] = None) -> None:
    import sys

    if argv is None and "--list" in sys.argv[1:]:
        for name, cfg in load_seeds().items():
            state = "" if cfg.get("enabled", True) else " (disabled)"
            print(f"{name}{state}: " + ", ".join(c["name"] for c in cfg["candidates"]))
        return

    def add(ap):
        ap.add_argument("--scout", required=True, help="which Scout, e.g. scout_metals (see --list)")
        ap.add_argument("--preset", choices=["quick", "production"], help="simulation preset to request")

    agent_cli(__doc__.split("\n\n")[0], lambda a, b: Scout(b, a.scout, preset=a.preset), add, argv)


if __name__ == "__main__":
    main()
