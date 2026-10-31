"""Alloy Scout: owns the low-melting-alloy family (the Tier-2 "blend" track).

Candidates come from the sourced calibration set (``data/calibration/tier1.json``): every ``low_melting_alloy``
record, at the composition its ``formula`` states (e.g. ``Bi32.5In51Sn16.5 (wt%)``). Nothing is typed in here.

Alloy melting points are not simulated (an alloy melts over a range; that needs phase-diagram methods, and the
Simulator refuses it), so this Scout requests the **latent heat at the measured melting temperature**:
H(liquid alloy) - sum_i x_i H(solid i), the Simulator's alloy-latent mode. An alloy with a sourced latent heat
(Field's metal, ``BiInSn``) is a trust-tier check of that method; the others are discovery-tier predictions.
An alloy with no sourced melting point is posted without a request, so the gap shows on the board.

    python -m coolorbit.agents.alloy_scout --board boards/<id> [--once] [--only BiInSn InBi] [--preset production]
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from coolorbit.agents.base import Agent, agent_cli, norm
from coolorbit.agents.librarian import DEFAULT_DATA, load_calibration
from coolorbit.board import Board

AGENT_NAME = "scout_alloys"
FAMILY = "low_melting_alloy"
FORMULA_RE = re.compile(r"^\s*((?:[A-Z][a-z]?\d+(?:\.\d+)?)+)\s*\((wt|at)%\)\s*$")


def alloy_seeds(records: Iterable[dict]) -> list[dict]:
    """One seed per alloy in the calibration set (file order): name, formula, sourced melting point, whether a
    sourced latent heat exists."""
    seeds: dict[str, dict] = {}
    for rec in records:
        if rec.get("family") != FAMILY:
            continue
        seed = seeds.setdefault(norm(rec["candidate"]), {"name": rec["candidate"], "formula": rec.get("formula"),
                                                         "melting_point_K": None, "latent_measured": False})
        if rec.get("value") is None or not (rec.get("source") or "").strip():
            continue
        if rec["property"] == "melting_point":
            seed["melting_point_K"] = float(rec["value"])
        elif rec["property"] == "latent_heat":
            seed["latent_measured"] = True
    return list(seeds.values())


def structure_source(formula: Optional[str]) -> Optional[str]:
    """'Bi32.5In51Sn16.5 (wt%)' -> 'alloy:wt:Bi32.5In51Sn16.5' (the Simulator's alloy source), else None."""
    m = FORMULA_RE.match(formula or "")
    if not m:
        return None
    try:
        from coolorbit.sim.alloys import parse_composition

        parse_composition(f"{m.group(2)}:{m.group(1)}")
    except (ImportError, ValueError, KeyError):
        return None
    return f"alloy:{m.group(2)}:{m.group(1)}"


class AlloyScout(Agent):
    role = "scout"

    def __init__(self, board: Board, name: str = AGENT_NAME, *, records: Optional[list[dict]] = None,
                 only: Optional[Iterable[str]] = None, preset: Optional[str] = None, **kw) -> None:
        super().__init__(board, name, **kw)
        self.seeds = alloy_seeds(records if records is not None else load_calibration(DEFAULT_DATA))
        if only:
            order = [norm(n) for n in only]
            by_name = {norm(s["name"]): s for s in self.seeds}
            missing = [n for n in order if n not in by_name]
            if missing:
                raise ValueError(f"no calibration alloy named {missing}; known: {[s['name'] for s in self.seeds]}")
            self.seeds = [by_name[n] for n in order]
        self.preset = preset  # None = the Simulator's default (production)

    def step(self) -> bool:
        posted = {norm(a.payload["name"]) for a in self.board.snapshot().of_type("candidate")
                  if a.producer_agent == self.name}
        for seed in self.seeds:
            if norm(seed["name"]) in posted:
                continue
            source = structure_source(seed["formula"])
            tier = "trust" if seed["latent_measured"] else "discovery"
            payload = {"name": seed["name"], "family": FAMILY, "tier": tier, "formula": seed["formula"],
                       "rationale": ("low-melting alloy PCM; latent heat simulated at the measured melting point"
                                     + ("; a measured latent heat exists, so this checks the method"
                                        if tier == "trust" else "; no measured latent heat: a prediction"))}
            if source:
                payload["structure_source"] = source
            needs, why = [], None
            if source is None:
                why = f"composition {seed['formula']!r} is not a parseable wt%/at% formula"
            elif seed["melting_point_K"] is None:
                why = "no sourced melting point to run the latent heat at"
            else:
                tm = seed["melting_point_K"]
                need = {"artifact_type": "property_result", "property": "latent_heat", "temperature_K": tm,
                        "query": f"latent_heat of {seed['name']} at its measured melting point ({tm:g} K)",
                        "rationale": "alloy latent heat H(liquid) - sum x_i H(solid i) at the measured melting "
                                     "temperature; alloy melting points are not simulated"}
                if self.preset:
                    need["preset"] = self.preset
                needs = [need]
            art = self.post("candidate", payload, needs=needs, skill=f"coolorbit.scout.{FAMILY}")
            self.log("decision", candidate=seed["name"], tier=tier, id=art.artifact_id[:8],
                     action="requested latent_heat at the measured melting point" if needs else
                     f"posted without a request: {why}")
            return True
        return False


def main(argv: Optional[list[str]] = None) -> None:
    def add(ap):
        ap.add_argument("--only", nargs="*", help="only these calibration alloys, in this order")
        ap.add_argument("--preset", choices=["quick", "production"])

    agent_cli(__doc__.split("\n\n")[0], lambda a, b: AlloyScout(b, only=a.only, preset=a.preset), add, argv)


if __name__ == "__main__":
    main()
