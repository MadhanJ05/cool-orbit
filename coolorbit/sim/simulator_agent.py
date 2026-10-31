"""Simulator agent: turns simulation requests on the board into ALCHEMI runs on the GB10.

It owns the GPU queue and nothing else. It never invents work: it only picks up
open needs that ask for a ``property_result`` it can compute (see
docs/simulation-requests.md), claims one, runs it (one GPU job at a time, as a
subprocess so a crash or OOM never takes the agent down), and posts:

* success  -> a ``property_result`` (full method metadata, flags) that fulfils the
              need and broadcasts the needs of the trust rule (method audit,
              independent method, and a sourced measurement for the trust tier);
* failure  -> a ``run_report`` (status failed) that fulfils the need and, when the
              failure is fixable, carries a retry need (shifted bracket or longer
              run), up to MAX_ATTEMPTS;
* refusal  -> a ``run_report`` (status refused) explaining why (bad structure,
              model out of its training domain) and, where it helps, a need for a
              different engine.

    python -m coolorbit.sim.simulator_agent --board boards/<id> [--agent simulator_gb10]
                                            [--once] [--dry-run] [--poll 30]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from coolorbit.board import Board, BoardSnapshot, OpenNeed
from coolorbit.board._scienceclaw import REPO_ROOT, Artifact
from coolorbit.sim.results import PROTOCOLS, build_property_payload

SUPPORTED = {"melting_point": "coexistence", "latent_heat": "latent"}
GB10_ATOM_STEPS_PER_S = 20_000  # measured (runs/smoke)
MAX_ATTEMPTS = 3
PRESET_STEPS = {
    "quick": {"equil": 500, "melt": 1500, "quench": 500, "prod": 2500},
    "production": {"equil": 4000, "melt": 6000, "quench": 3000, "prod": 25000},
}
ORGANIC_ELEMENTS = {"C", "H", "O", "N"}
# Smallest box the rule referee accepts (coolorbit/agents/audit_rules.json min_atoms: "two-phase coexistence" and
# "default"); default supercells grow to meet it, so a small-cell crystal (In: 2 atoms/cell) isn't run just to be
# flagged "system_too_small" and rerun. A test keeps these equal to the audit rules.
MIN_ATOMS = {"coexistence": 500, "latent": 200}
DEFAULT_REPS = {"coexistence": [4, 4, 8], "latent": [4, 4, 4]}


def default_reps(atoms_per_cell: int, mode: str) -> list[int]:
    """The default supercell for a mode, grown (smallest axis first) until it has at least MIN_ATOMS[mode]."""
    reps = list(DEFAULT_REPS[mode])
    while atoms_per_cell * math.prod(reps) < MIN_ATOMS[mode]:
        reps[reps.index(min(reps))] += 1
    return reps

Runner = Callable[["Job", Path, float], tuple[dict, dict, Path]]


class JobError(Exception):
    """A request addressed to us that cannot be run as specified."""

    def __init__(self, reason: str, suggest_need: Optional[dict] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.suggest_need = suggest_need


@dataclass
class Job:
    target: Artifact
    need_index: int
    need: dict
    candidate: Artifact
    property: str
    mode: str
    temps: list[float]
    reps: list[int]
    dt_fs: float
    preset: str
    prod_steps: Optional[int]
    attempt: int
    structure_args: list[str]
    n_atoms: int
    elements: set[str] = field(default_factory=set)
    supersedes: Optional[str] = None  # the flawed result this run replaces (rerun requested on a check)

    @property
    def name(self) -> str:
        return self.candidate.payload["name"]

    @property
    def task(self) -> str:
        return f"{self.property} of {self.name} via {PROTOCOLS[self.mode]} (ALCHEMI)"

    def label(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", self.name)[:40]

    def steps(self) -> dict:
        s = dict(PRESET_STEPS[self.preset])
        if self.prod_steps:
            s["prod"] = self.prod_steps
        return s

    def estimate_minutes(self) -> float:
        s, n, m = self.steps(), self.n_atoms, len(self.temps)
        if self.mode == "coexistence":
            atom_steps = n * (s["equil"] + s["melt"] + s["quench"]) + m * n * s["prod"]
        else:
            atom_steps = n * s["melt"] + 2 * n * s["prod"]
        return atom_steps / GB10_ATOM_STEPS_PER_S / 60

    def cli_args(self, out_dir: Path) -> list[str]:
        args = [self.mode, *self.structure_args, "--reps", *map(str, self.reps), "--dt", f"{self.dt_fs:g}",
                "--out", str(out_dir), "--label", self.label()]
        if self.mode in ("latent", "alloy-latent"):
            args += ["--temperature", f"{self.temps[0]:g}"]
        else:
            args += ["--temps", *(f"{t:g}" for t in self.temps)]
        if self.preset == "quick":
            args.append("--quick")
        if self.prod_steps:
            args += ["--prod-steps", str(self.prod_steps)]
        return args


# --------------------------------------------------------------------------- request parsing


def find_candidate(snap: BoardSnapshot, art: Artifact) -> Optional[Artifact]:
    """The artifact itself if it is a candidate, else the nearest candidate ancestor."""
    seen, queue = set(), [art]
    while queue:
        cur = queue.pop(0)
        if cur.artifact_type == "candidate":
            return cur
        for pid in cur.parent_artifact_ids:
            if pid not in seen and pid in snap.artifacts:
                seen.add(pid)
                queue.append(snap.artifacts[pid])
    return None


def find_rerun_target(snap: BoardSnapshot, art: Artifact, prop: str) -> Optional[str]:
    """The result a rerun request is about: a same-property result the need's artifact hangs off.

    Walks up from the need's artifact and stops at the candidate; returns the first property_result for
    ``prop``. Linking the new result to it marks the old one superseded (board status and eval metrics both
    use this). A follow-up for a *different* property (latent heat requested from a melting result) is not a
    rerun and must not supersede anything.
    """
    seen, queue = set(), [art]
    while queue:
        cur = queue.pop(0)
        if cur.artifact_type == "property_result" and cur.payload.get("property") == prop:
            return cur.artifact_id
        if cur.artifact_type == "candidate":
            continue
        for pid in cur.parent_artifact_ids:
            if pid not in seen and pid in snap.artifacts:
                seen.add(pid)
                queue.append(snap.artifacts[pid])
    return None


def parse_structure(source: Optional[str]):
    """Structure source -> (melting CLI args, Atoms, kind).

    ``ase:<El>:<structure>:<a>``, ``ref:<El>`` (real crystal of Ga, In, beta-Sn, Bi, Cu, Al),
    ``alloy:wt:Bi32.5In51Sn16.5`` / ``alloy:at:...`` (Atoms = the mixed liquid-start box), or
    ``file:<repo-relative path>``.
    """
    if not source:
        raise JobError("the candidate has no structure_source")
    kind, _, rest = source.partition(":")
    if kind == "ref":
        from coolorbit.sim.alloys import element_crystal

        try:
            return ["--element", rest, "--structure", "ref"], element_crystal(rest), kind
        except KeyError as exc:
            raise JobError(str(exc)) from exc
    if kind == "alloy":
        from coolorbit.sim.alloys import mixed_alloy_box, parse_composition

        try:
            fractions = parse_composition(rest)
        except ValueError as exc:
            raise JobError(str(exc)) from exc
        return ["--composition", rest], mixed_alloy_box(fractions, n_atoms=500), kind
    if kind == "ase":
        parts = rest.split(":")
        if len(parts) != 3:
            raise JobError(f"bad structure_source {source!r}; expected ase:<El>:<structure>:<a>, e.g. ase:Cu:fcc:3.61")
        el, structure, a = parts
        from ase.build import bulk

        try:
            atoms = bulk(el, structure, a=float(a), cubic=True)
        except Exception as exc:  # unknown element/structure, non-numeric a
            raise JobError(f"ASE could not build {source!r}: {exc}") from exc
        return ["--element", el, "--structure", structure, "--a", a], atoms, kind
    if kind == "file":
        path = (REPO_ROOT / rest).resolve()
        if not path.is_relative_to(REPO_ROOT) or not path.is_file():
            raise JobError(f"structure file {rest!r} not found inside the repo (commit it under data/structures/)")
        from ase.io import read

        return ["--structure-file", str(path)], read(path), kind
    raise JobError(f"unsupported structure_source {source!r}; use ase:<El>:<structure>:<a>, ref:<El>, "
                   "alloy:wt:<composition> or file:<repo path>")


def ood_reason(elements: set[str]) -> Optional[str]:
    if {"C", "H"} <= elements and elements <= ORGANIC_ELEMENTS:
        return ("MACE-MPA-0 is trained on inorganic materials and is out of its domain for molecular organic "
                "crystals; use classical MD (Nebius OpenMM/AMBER with GAFF2) or a molecular MLIP")
    return None


def build_job(snap: BoardSnapshot, on: OpenNeed) -> Optional[Job]:
    """None if the need is not addressed to an ALCHEMI simulator; JobError if it is but can't be run."""
    need = on.need
    prop = need.get("property")
    if need.get("artifact_type") != "property_result" or prop not in SUPPORTED:
        return None
    if need.get("engine") not in (None, "", "ALCHEMI"):
        return None
    cand = find_candidate(snap, on.artifact)
    if cand is None:
        raise JobError("could not find the candidate this request is about")
    structure_args, unit, kind = parse_structure(cand.payload.get("structure_source"))
    elements = set(unit.get_chemical_symbols())
    if kind == "alloy":
        if prop != "latent_heat":
            raise JobError("alloy melting temperatures are not computed here (that needs phase-diagram methods); "
                           "request latent_heat at the alloy's measured melting temperature instead")
        if need.get("temperature_K") is None:
            raise JobError("an alloy latent_heat request needs temperature_K (the measured melting temperature)")
        n_solid = 300 * len(elements)
        return Job(target=on.artifact, need_index=on.index, need=need, candidate=cand, property=prop,
                   mode="alloy-latent", temps=[float(need["temperature_K"])], reps=[1, 1, 1],
                   dt_fs=float(need.get("dt_fs") or 2.0), preset=need.get("preset", "production"),
                   prod_steps=need.get("prod_steps"), attempt=int(need.get("attempt", 1)),
                   structure_args=structure_args, n_atoms=len(unit) + n_solid, elements=elements)
    reason = ood_reason(elements)
    if reason:
        alt = {k: v for k, v in need.items() if k not in ("attempt",)}
        alt.update(engine="classical_md", query=f"{prop} of {cand.payload['name']} with classical MD",
                   rationale="ALCHEMI/MACE refused: " + reason[:120])
        raise JobError(reason, suggest_need=alt)

    mode = SUPPORTED[prop]
    if mode == "coexistence":
        temps = need.get("temps_K")
        if not temps and need.get("t_guess_K"):
            t = float(need["t_guess_K"])
            temps = [round(t * f) for f in (0.85, 0.95, 1.05, 1.15)]
        if not temps or len(temps) < 2:
            raise JobError("a melting_point request needs temps_K (2+ temperatures) or t_guess_K")
    else:
        if need.get("temperature_K") is None:
            raise JobError("a latent_heat request needs temperature_K (ideally the melting point)")
        temps = [need["temperature_K"]]
    reps = need.get("reps") or default_reps(len(unit), mode)
    preset = need.get("preset", "production")
    if preset not in PRESET_STEPS:
        raise JobError(f"unknown preset {preset!r}; use quick or production")
    return Job(
        target=on.artifact, need_index=on.index, need=need, candidate=cand, property=prop, mode=mode,
        temps=[float(t) for t in temps], reps=[int(r) for r in reps],
        dt_fs=float(need.get("dt_fs") or (0.5 if "H" in elements else 2.0)), preset=preset,
        prod_steps=need.get("prod_steps"), attempt=int(need.get("attempt", 1)), structure_args=structure_args,
        n_atoms=len(unit) * math.prod(int(r) for r in reps), elements=elements,
        supersedes=find_rerun_target(snap, on.artifact, prop),
    )


def retry_need(job: Job, result: dict) -> Optional[dict]:
    """A follow-up request that could fix a coexistence failure, or None."""
    if job.attempt >= MAX_ATTEMPTS or job.mode != "coexistence":
        return None
    temps = sorted(job.temps)
    n = len(temps)
    spacing = (temps[-1] - temps[0]) / (n - 1)
    per_t = result.get("per_temperature") or []
    melting = sorted(p["T_target_K"] for p in per_t if p["verdict"] == "melting")
    freezing = sorted(p["T_target_K"] for p in per_t if p["verdict"] == "freezing")
    failure = result.get("failure")
    need = {k: v for k, v in job.need.items() if k != "t_guess_K"}
    # Anchor the new bracket at the nearest decisive temperature so the answer can't be skipped over
    # (seen in the first agent run: melting at 1350-1450 K, 1300 K undecided; a full-width shift jumped to 1100-1250).
    if failure == "above_bracket":  # nothing melted: T_m is above the highest freezing (or highest tried) T
        anchor = max(freezing) if freezing else temps[-1]
        need["temps_K"] = [anchor + k * spacing for k in range(n)]
        what = "bracket shifted up"
    elif failure == "below_bracket":  # nothing froze: T_m is below the lowest melting (or lowest tried) T
        anchor = min(melting) if melting else temps[0]
        need["temps_K"] = [anchor - k * spacing for k in reversed(range(n))]
        what = "bracket shifted down"
        if min(need["temps_K"]) <= 0:
            return None
    elif failure == "inconsistent":
        need["temps_K"] = temps
        need["prod_steps"] = 2 * job.steps()["prod"]
        what = "production run doubled"
    else:
        return None
    need.update(attempt=job.attempt + 1, query=f"{job.property} of {job.name} (retry {job.attempt + 1}: {what})",
                rationale=f"attempt {job.attempt} failed: {result.get('reason', failure)}")
    return need


def check_needs(job: Job) -> list[dict]:
    """What the trust rule still needs before this result can be trusted."""
    protocol = PROTOCOLS[job.mode]
    needs = [
        {"artifact_type": "check", "check_type": "method_audit",
         "query": f"audit the {protocol} {job.property} run for {job.name}",
         "rationale": "trust rule (a): another agent must audit the simulation method"},
        {"artifact_type": "check", "check_type": "classical_md",
         "query": f"independent {job.property} for {job.name} with a different method (classical MD or a second MLIP)",
         "rationale": "trust rule (b): an independent method must agree within the pre-registered tolerance"},
    ]
    if job.candidate.payload["tier"] == "trust":
        needs.append({"artifact_type": "reference_value", "query": f"measured {job.property} of {job.name} with a citable source",
                      "rationale": "trust rule (c): trust-tier results must match a sourced measurement"})
    return needs


# --------------------------------------------------------------------------- running


def subprocess_runner(job: Job, out_dir: Path, timeout_s: float) -> tuple[dict, dict, Path]:
    """Run coolorbit.sim.melting in its own process; return (result, settings, run_dir)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "agent_job.log"
    cmd = [sys.executable, "-m", "coolorbit.sim.melting", *job.cli_args(out_dir)]
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        fh.flush()
        proc = subprocess.run(cmd, cwd=REPO_ROOT, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout_s)
    runs = sorted(p for p in out_dir.iterdir() if p.is_dir() and (p / "result.json").exists())
    if proc.returncode != 0 or not runs:
        raise RuntimeError(f"melting workflow exited with code {proc.returncode}; see {log}")
    run_dir = runs[-1]
    result = json.loads((run_dir / "result.json").read_text())
    settings = json.loads((run_dir / "settings.json").read_text())
    return result, settings, run_dir


class BrevRunner:
    """Run a job on an existing Brev GPU instance at the exact local commit; copy results back.

    Never creates, stops or deletes instances (those cost money and are manual, approved steps). The instance
    must have been prepared with scripts/brev/setup_remote.sh, and the local commit must be pushed (the
    instance checks it out by hash, so the remote run is reproducible). ``run`` is injectable for tests.
    """

    def __init__(self, instance: str, brev: str = str(Path.home() / ".local/bin/brev"),
                 remote_dir: str = "~/cool-orbit", run: Callable = subprocess.run) -> None:
        self.instance, self.brev, self.remote_dir, self._run = instance, brev, remote_dir, run

    def _exec(self, command: str, timeout_s: float):
        return self._run([self.brev, "exec", self.instance, command], capture_output=True, text=True,
                         timeout=timeout_s)

    def __call__(self, job: Job, out_dir: Path, timeout_s: float) -> tuple[dict, dict, Path]:
        dirty = self._run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "coolorbit"],
                          capture_output=True, text=True).stdout.strip()
        if dirty:
            raise RuntimeError("coolorbit/ has uncommitted changes; commit and push before running on Brev")
        sha = self._run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True,
                        text=True).stdout.strip()
        remote_out = f"runs/remote/{out_dir.name}"
        # Repo-relative paths only: a local absolute path (file: structures) means nothing on the instance.
        args = [a.replace(str(REPO_ROOT) + "/", "") for a in job.cli_args(Path(remote_out))]
        cmd = (f"cd {self.remote_dir} && git fetch -q origin && git checkout -q --detach {sha} && mkdir -p "
               f"{remote_out} && .venv/bin/python -m coolorbit.sim.melting {shlex.join(args)} "
               f"> {remote_out}/agent_job.log 2>&1; echo REMOTE_EXIT=$? REMOTE_DIR=$(pwd)/{remote_out}")
        proc = self._exec(cmd, timeout_s)
        tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        fields = dict(f.split("=", 1) for f in tail[0].split() if "=" in f)
        if "REMOTE_DIR" not in fields:
            raise RuntimeError(f"brev exec on {self.instance} failed: {(proc.stderr or proc.stdout)[-300:]}")
        out_dir.mkdir(parents=True, exist_ok=True)
        copy = self._run([self.brev, "copy", f"{self.instance}:{fields['REMOTE_DIR']}/", str(out_dir)],
                         capture_output=True, text=True, timeout=600)
        if copy.returncode != 0:
            raise RuntimeError(f"brev copy failed: {(copy.stderr or copy.stdout)[-300:]}")
        runs = sorted(p for p in out_dir.rglob("result.json"))
        if fields.get("REMOTE_EXIT") != "0" or not runs:
            raise RuntimeError(f"remote melting run exited {fields.get('REMOTE_EXIT')}; see {out_dir}/agent_job.log")
        run_dir = runs[-1].parent
        result = json.loads((run_dir / "result.json").read_text())
        settings = json.loads((run_dir / "settings.json").read_text())
        result.setdefault("flags", [])
        settings["settings"]["remote"] = {"instance": self.instance, "commit": sha}
        return result, settings, run_dir


class SimulatorAgent:
    def __init__(self, board: Board, name: str = "simulator_gb10", runner: Runner = subprocess_runner,
                 runs_root: Optional[Path] = None, min_minutes: float = 0.0,
                 max_minutes: float = float("inf")) -> None:
        self.board = board
        self.name = name
        self.runner = runner
        self.runs_root = runs_root or REPO_ROOT / "runs" / "agent" / board.root.name
        # Job-size window (GB10-equivalent minutes): lets a GB10 agent and a Brev agent split the queue,
        # coordinating only through claims on the board.
        self.min_minutes, self.max_minutes = min_minutes, max_minutes

    def pending(self) -> list[tuple[OpenNeed, "Job | JobError"]]:
        """Unclaimed needs addressed to us, parsed (no claims, no side effects)."""
        snap = self.board.snapshot()
        out = []
        for on in self.board.open_needs(include_claimed=False):
            try:
                job = build_job(snap, on)
            except JobError as err:
                out.append((on, err))
                continue
            if job is not None and self.min_minutes <= job.estimate_minutes() < self.max_minutes:
                out.append((on, job))
        return out

    def step(self) -> bool:
        """Handle at most one request. Returns True if something was claimed and handled."""
        for on, job in self.pending():
            target, idx = on.artifact.artifact_id, on.index
            ttl = 15.0 if isinstance(job, JobError) else 3 * job.estimate_minutes() + 15
            if not self.board.claim(self.name, target, idx, ttl_minutes=ttl):
                continue  # another agent got it first
            if isinstance(job, JobError):
                self._refuse(on, job)
            else:
                self._run(job, ttl)
            return True
        return False

    def _report(self, *, status: str, task: str, reason: str, target_id: str, need_index: int,
                candidate: Optional[Artifact], attempt: int = 1, run_dir: Optional[str] = None,
                details: Optional[dict] = None, needs: tuple = (), supersedes: Optional[str] = None) -> Artifact:
        # Keep the link to the flawed result a rerun replaces, so a retry of a failed rerun still supersedes it.
        parents = ([candidate.artifact_id] if candidate else []) + ([supersedes] if supersedes else [])
        art = self.board.post(self.name, "run_report",
                              {"status": status, "task": task, "reason": reason, "run_dir": run_dir,
                               "attempt": attempt, "details": details or {}},
                              parents=parents, needs=needs, fulfills=(target_id, need_index))
        print(f"[{self.name}] {status.upper()}: {task}: {reason}", flush=True)
        return art

    def _refuse(self, on: OpenNeed, err: JobError) -> None:
        cand = find_candidate(self.board.snapshot(), on.artifact)
        prop = on.need.get("property", "?")
        name = cand.payload["name"] if cand else "?"
        self._report(status="refused", task=f"{prop} of {name} (ALCHEMI)", reason=err.reason,
                     target_id=on.artifact.artifact_id, need_index=on.index, candidate=cand,
                     needs=[err.suggest_need] if err.suggest_need else ())

    def _run(self, job: Job, ttl_minutes: float) -> None:
        out_dir = self.runs_root / f"{job.target.artifact_id[:8]}-n{job.need_index}-a{job.attempt}"
        print(f"[{self.name}] RUN: {job.task}, {job.n_atoms} atoms x {len(job.temps)} temps, "
              f"preset {job.preset}, attempt {job.attempt}, ~{job.estimate_minutes():.0f} min", flush=True)
        try:
            result, settings, run_dir = self.runner(job, out_dir, ttl_minutes * 60)
        except Exception as exc:  # crash, timeout, missing output: report it, never hide it
            self._report(status="failed", task=job.task, reason=f"run did not complete: {exc!r}"[:500],
                         target_id=job.target.artifact_id, need_index=job.need_index, candidate=job.candidate,
                         attempt=job.attempt, run_dir=str(out_dir), supersedes=job.supersedes)
            return
        if result.get("status") == "ok":
            payload = build_property_payload(job.candidate.payload, result, settings["settings"],
                                             settings["n_atoms"], str(run_dir))
            parents = [job.candidate.artifact_id] + ([job.supersedes] if job.supersedes else [])
            art = self.board.post(self.name, "property_result", payload, parents=parents,
                                  needs=check_needs(job), fulfills=(job.target.artifact_id, job.need_index),
                                  skill="coolorbit.sim.melting")
            print(f"[{self.name}] RESULT {art.artifact_id[:8]}: {payload['property']} = {payload['value']} "
                  f"+/- {payload['uncertainty']} {payload['units']} flags={payload['flags']}", flush=True)
            return
        retry = retry_need(job, result)
        self._report(status="failed", task=job.task, reason=result.get("reason", "unknown failure"),
                     target_id=job.target.artifact_id, need_index=job.need_index, candidate=job.candidate,
                     attempt=job.attempt, run_dir=str(run_dir),
                     details={k: result[k] for k in ("failure", "per_temperature", "flags") if k in result},
                     needs=[retry] if retry else (), supersedes=job.supersedes)


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m coolorbit.sim.simulator_agent", description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True)
    ap.add_argument("--agent", default="simulator_gb10")
    ap.add_argument("--once", action="store_true", help="handle at most one request, then exit")
    ap.add_argument("--dry-run", action="store_true", help="list the requests it would take; claim nothing")
    ap.add_argument("--poll", type=float, default=30.0, help="seconds between board scans when idle")
    ap.add_argument("--brev-instance", help="run jobs on this existing, prepared Brev instance (never created here)")
    ap.add_argument("--min-minutes", type=float, default=0.0, help="only take jobs estimated >= this (GB10 minutes)")
    ap.add_argument("--max-minutes", type=float, default=float("inf"), help="only take jobs estimated < this")
    args = ap.parse_args()
    runner = BrevRunner(args.brev_instance) if args.brev_instance else subprocess_runner
    agent = SimulatorAgent(Board(args.board), args.agent, runner=runner,
                           min_minutes=args.min_minutes, max_minutes=args.max_minutes)

    if args.dry_run:
        pending = agent.pending()
        print(f"{len(pending)} request(s) for {args.agent} on {args.board}")
        for on, job in pending:
            where = f"{on.artifact.artifact_id[:8]}#{on.index}"
            if isinstance(job, JobError):
                print(f"  {where}  WOULD REFUSE: {job.reason}")
            else:
                print(f"  {where}  {job.task}: {job.n_atoms} atoms, temps {job.temps}, preset {job.preset}, "
                      f"attempt {job.attempt}, ~{job.estimate_minutes():.0f} min on the GB10")
        return

    print(f"[{args.agent}] watching {args.board} (Ctrl+C to stop)", flush=True)
    while True:
        did = agent.step()
        if args.once:
            if not did:
                print(f"[{args.agent}] nothing to do")
            return
        if not did:
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
