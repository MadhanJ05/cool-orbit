"""Scout: owns one material family. Proposes candidates and requests simulations on the board.

Each Scout is independent: it decides which of its family's candidates to post (within its budget),
routes each one to an engine that can handle it (ALCHEMI for in-domain inorganic crystals, classical MD
for molecular organics), and follows up on its own candidates (latent heat once a melting point exists).
Requests follow docs/simulation-requests.md.

Evaluation tasks: the eval harness posts each task as a ``request`` whose need asks for a ``candidate``.
A family Scout claims a task when one of its seed names or aliases appears as a word in the task text
(``need["query"]``; element symbols match case-sensitively, so "In" is indium but "in" is not), posts the
candidate with the task request as parent (fulfilling the need) and asks for the task's property, if the
seed has a route and a structure. ``scout_general`` takes the tasks no family Scout owns.

Fairness: Scouts see only what is on the board, the same task text every arm gets. They never read
``coolorbit/eval/tasks.json`` (it holds trap labels and reference values the other arms don't get).
With ``eval_only`` a Scout answers tasks and proposes nothing else (comparison runs).

    python -m coolorbit.agents.scout --board boards/<id> --scout scout_metals [--once] [--eval-only]
    python -m coolorbit.agents.scout --list
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from coolorbit.agents.base import Agent, OpenNeed, agent_cli, norm
from coolorbit.agents.librarian import DEFAULT_DATA, index_records, load_calibration
from coolorbit.board import Board, BoardSnapshot, derive_status, load_rules
from coolorbit.board._scienceclaw import Artifact

SEEDS_PATH = Path(__file__).with_name("scout_seeds.json")
TRANSITION_PROPS = {"melting_point": "latent_heat", "transition_temperature": "transition_latent_heat"}
EVAL_AGENT = "eval_harness"
SYMBOL_RE = re.compile(r"^(?:[A-Z][a-z]?\d*(?:\.\d+)?)+$")  # Cu, LiF, Bi58Sn42: matched case-sensitively
FORMULA_TOKEN_RE = re.compile(r"(?<!\w)((?:[A-Z][a-z]?\d*)+)(?![a-z])")
STATE_WORDS = {"liquid": "liquid", "molten": "liquid", "crystal": "crystal", "crystalline": "crystal",
               "solid": "crystal"}


def load_seeds(path: str | Path = SEEDS_PATH) -> dict:
    return json.loads(Path(path).read_text())["scouts"]


def seed_names(seed: dict) -> set[str]:
    return {norm(n) for n in [seed["name"], *seed.get("aliases", [])]}


# Element symbols that are also English words. Capitalised at the start of a sentence ("In a perfect copper
# crystal...", "As the box grows...", "At 800 K...") they are almost always the word, and "I" is the pronoun
# anywhere, so these matches count only when the text names nothing else.
WORD_SYMBOLS = {"In", "As", "At", "Be", "He", "No", "Am", "I", "U"}
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?:;\n])[\s\"'(\[]*$")


def weak_match(text: str, token: str, pos: int) -> bool:
    """True if ``token`` at ``pos`` is probably an English word rather than an element symbol."""
    return token in WORD_SYMBOLS and (token == "I" or bool(_SENTENCE_START_RE.search(text[:pos])))


def mentions(text: str, name: str, allow_weak: bool = False) -> Optional[int]:
    """Position of ``name`` as a whole word in ``text`` (symbols/formulas case-sensitive), else None.
    A symbol that is probably an English word there (``weak_match``) is skipped unless ``allow_weak``."""
    flags = 0 if SYMBOL_RE.match(name) else re.IGNORECASE
    for m in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, flags):
        if allow_weak or not weak_match(text, m.group(0), m.start()):
            return m.start()
    return None


def periodic_table() -> tuple[set[str], dict[str, str]]:
    """(element symbols, {'lithium': 'Li', ...}) from ASE (generic chemistry, not task data); empty without ASE."""
    try:
        from ase.data import atomic_names, chemical_symbols
    except ImportError:
        return set(), {}
    return set(chemical_symbols[1:]), {n.lower(): sym for n, sym in zip(atomic_names, chemical_symbols) if n}


def material_from_text(text: str) -> Optional[str]:
    """Best guess of the material named in a task text: the formula token with most elements (LiF over Li),
    else an element name (silicon -> Si). Generic parsing only; None if nothing is recognised."""
    symbols, names = periodic_table()
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:K|°C|C|eV|GPa|ps|ns|fs)\b", " ", text)  # "800 K" is a temperature
    tokens = [(m.group(1), m.start()) for m in FORMULA_TOKEN_RE.finditer(text)
              if len(m.group(1)) <= 12 and all(e in symbols for e in re.findall(r"[A-Z][a-z]?", m.group(1)))]
    valid = [(t, pos) for t, pos in tokens if not weak_match(text, t, pos)]
    if valid:
        return max(valid, key=lambda tp: (len(re.findall(r"[A-Z]", tp[0])), -tp[1]))[0]
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in names:
            return names[word]
    return tokens[0][0] if tokens else None  # only a sentence-initial "In ..." etc.: take it as the element


def state_from_text(text: str) -> Optional[str]:
    for word, state in STATE_WORDS.items():
        if re.search(r"\b" + word + r"\b", text, re.IGNORECASE):
            return state
    return None


def is_eval_task(on: OpenNeed) -> bool:
    return (on.need.get("artifact_type") == "candidate" and bool(on.need.get("eval_task"))
            and on.artifact.producer_agent == EVAL_AGENT)


class Scout(Agent):
    role = "scout"

    def __init__(self, board: Board, name: str, *, seeds: Optional[dict] = None, calibration: Optional[list] = None,
                 rules: Optional[dict] = None, preset: Optional[str] = None, eval_only: bool = False, **kw) -> None:
        super().__init__(board, name, **kw)
        self.all_seeds = seeds if seeds is not None else load_seeds()
        if name not in self.all_seeds:
            raise ValueError(f"no seeds for {name!r}; known scouts: {sorted(self.all_seeds)}")
        self.config = self.all_seeds[name]
        self.enabled = self.config.get("enabled", True)
        self.general = bool(self.config.get("general"))
        self.budget = int(self.config.get("budget", len(self.config["candidates"])))
        self.refs = index_records(calibration if calibration is not None else load_calibration(DEFAULT_DATA))
        self.rules = rules
        self.preset = preset  # "quick" for pipeline tests; None = the Simulator's default (production)
        self.eval_only = eval_only

    # ------------------------------------------------------------------ helpers

    def _mine(self) -> list[Artifact]:
        return [a for a in self.board.snapshot().of_type("candidate") if a.producer_agent == self.name]

    def _eval_parent(self, snap: BoardSnapshot, cand: Artifact) -> Optional[Artifact]:
        return next((snap.artifacts[p] for p in cand.parent_artifact_ids if p in snap.artifacts
                     and snap.artifacts[p].producer_agent == EVAL_AGENT), None)

    def _audited_pass(self, snap: BoardSnapshot, res: Artifact) -> bool:
        """A method audit by someone other than the producer has passed this result."""
        return any(k.artifact_type == "check" and k.payload["check_type"] == "method_audit"
                   and k.payload["verdict"] == "pass" and k.producer_agent != res.producer_agent
                   for k in snap.children.get(res.artifact_id, []))

    def _guess(self, name: str, prop: str) -> Optional[float]:
        rec = self.refs.get((norm(name), prop))
        return rec["value"] if rec and rec["value"] is not None else None

    def _request(self, seed: dict, prop: str, **fields) -> Optional[dict]:
        need = {"artifact_type": "property_result", "property": prop,
                "query": f"{prop} of {seed['name']}", "rationale": f"{seed['family']} candidate", **fields}
        if seed.get("route") == "classical_md":
            need["engine"] = "classical_md"
        if self.preset:
            need["preset"] = self.preset
        if prop in TRANSITION_PROPS and "temps_K" not in need and "t_guess_K" not in need:
            guess = self._guess(seed["name"], prop)
            if guess is None:
                self.log("skip_request", candidate=seed["name"], property=prop,
                         reason="no literature value to centre the bracket on")
                return None
            need["t_guess_K"] = round(guess)
            need["rationale"] += f"; two-phase coexistence bracket centred on the literature value ({round(guess)} K)"
        return need

    def _owner(self, text: str) -> Optional[tuple[str, dict]]:
        """(scout name, seed) of the enabled family Scout whose seed is named earliest in the task text."""
        for allow_weak in (False, True):  # "In a perfect copper crystal": copper, not indium
            best = None
            for scout, cfg in self.all_seeds.items():
                if cfg.get("general") or not cfg.get("enabled", True):
                    continue
                for seed in cfg["candidates"]:
                    for n in [seed["name"], *seed.get("aliases", [])]:
                        pos = mentions(text, n, allow_weak)
                        if pos is not None and (best is None or pos < best[0]):
                            best = (pos, scout, seed)
            if best:
                return best[1], best[2]
        return None

    # ------------------------------------------------------------------ work

    def step(self) -> bool:
        if not self.enabled:
            return False
        if self._answer_task():
            return True
        if not self.eval_only and not self.general and self._propose():
            return True
        return self._follow_up()

    def _answer_task(self) -> bool:
        """Claim an eval task about one of my materials (or any unowned one, for scout_general)."""
        for on in self.board.open_needs(include_claimed=False):
            if not is_eval_task(on):
                continue
            text = on.need.get("query", "")
            owner = self._owner(text)
            if self.general:
                if owner is not None:
                    continue
                material = material_from_text(text) or f"material of task {on.need['eval_task']}"
                seed = {"name": material, "family": "eval_general", "tier": "trust",
                        "formula": material if SYMBOL_RE.match(material) else None,
                        "structure_source": None, "route": None,
                        "rationale": "evaluation task material outside every family Scout's seeds"}
            else:
                if owner is None or owner[0] != self.name:
                    continue
                seed = owner[1]
            if not self.claim(on, ttl_minutes=30):
                continue
            prop = on.need.get("property")
            first = "melting_point" if prop == "latent_heat" else prop  # latent heat waits for the melting point
            need = None
            if seed.get("route") and seed.get("structure_source"):  # same guard as _propose
                fields = {"query": text or f"{prop} of {seed['name']}",
                          "rationale": f"evaluation task {on.need['eval_task']}"}
                state = state_from_text(text)
                if state:
                    fields["state"] = state
                t = re.search(r"(\d+(?:\.\d+)?)\s*K\b", text)
                if t and first not in TRANSITION_PROPS:
                    fields["temperature_K"] = float(t.group(1))
                need = self._request(seed, first, **fields)
            payload = {k: seed[k] for k in ("name", "family", "tier", "formula", "structure_source", "rationale")
                       if seed.get(k) is not None}
            self.post("candidate", payload, parents=[on.artifact.artifact_id],
                      fulfills=(on.artifact.artifact_id, on.index), needs=[need] if need else [],
                      skill=f"coolorbit.scout.{seed['family']}")
            self.log("decision", eval_task=on.need["eval_task"], candidate=seed["name"],
                     action=f"answered task: requested {first}" if need else
                     "answered task: no runnable structure or route, so nothing requested",
                     route=seed.get("route"))
            return True
        return False

    def _propose(self) -> bool:
        """Post the next seed candidate not yet on the board (one per step, within budget)."""
        snap = self.board.snapshot()
        mine = [a for a in self._mine() if self._eval_parent(snap, a) is None]  # eval answers don't use the budget
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
        """Once a usable melting point exists for one of my candidates, request its latent heat there.

        Discovery candidates: if the seed lists the latent heat. Eval candidates: only if the task asks for it.
        """
        snap = self.board.snapshot()
        seeds = {norm(s["name"]): s for s in self.config["candidates"]}
        rules = self.rules if self.rules is not None else load_rules()
        for cand in self._mine():
            seed = seeds.get(norm(cand.payload["name"]))
            if not seed or not seed.get("route"):
                continue
            task = self._eval_parent(snap, cand)
            wanted = {n.get("property") for n in task.needs} if task is not None else set(seed.get("requests", []))
            kids = list(snap.children.get(cand.artifact_id, []))
            asked = {n.get("property") for k in [cand, *kids] for n in k.needs}
            for t_prop, l_prop in TRANSITION_PROPS.items():
                if l_prop not in wanted or l_prop in asked:
                    continue
                results = [k for k in kids if k.artifact_type == "property_result" and k.payload["property"] == t_prop]
                # Only at a melting point a referee has audited and passed (not one still awaiting review).
                usable = [r for r in results if self._audited_pass(snap, r)
                          and (rep := derive_status(snap, r, rules)).status not in ("flagged", "rejected")
                          and not rep.superseded_by]
                if not usable:
                    continue
                tm = usable[-1].payload["value"]
                need = self._request(seed, l_prop, temperature_K=round(tm, 1))
                need["rationale"] = (f"second figure-of-merit input, at the simulated {t_prop} "
                                     f"({tm:g} K, result {usable[-1].artifact_id[:8]})")
                if task is not None:
                    need["rationale"] += f"; evaluation task {task.needs[0].get('eval_task')}"
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
            kind = " (general: eval tasks outside every family)" if cfg.get("general") else ""
            print(f"{name}{state}{kind}: " + ", ".join(c["name"] for c in cfg["candidates"]))
        return

    def add(ap):
        ap.add_argument("--scout", required=True, help="which Scout, e.g. scout_metals (see --list)")
        ap.add_argument("--preset", choices=["quick", "production"], help="simulation preset to request")
        ap.add_argument("--eval-only", action="store_true", help="answer eval tasks only, propose nothing")

    agent_cli(__doc__.split("\n\n")[0], lambda a, b: Scout(b, a.scout, preset=a.preset, eval_only=a.eval_only),
              add, argv)


if __name__ == "__main__":
    main()
