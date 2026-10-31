# Teammate 2 handoff: evaluation, dashboard, classical-MD inputs, pitch (laptop, no GB10)

> **For you and your Claude Code session.** Read `CLAUDE.md` (project brief), then
> `docs/handoff-teammate.md` **sections 3 and 4 only** (laptop setup and the board contract: they apply to
> you too), then this file. Start Claude in the repo root and give it:
> *"Read CLAUDE.md, sections 3-4 of docs/handoff-teammate.md, and docs/handoff-teammate-2.md. Propose a plan
> for my stream. Don't run anything yet."*
>
> Written Sat Oct 31, 2026, ~17:00 UTC by the GB10 session (Madhan + Claude). The repo is **public**: never
> commit keys, passwords or private links.

---

## 1. Who does what (three parallel streams, no shared files)

| Stream | Who | Owns (create/edit freely) |
|---|---|---|
| GB10: simulations, hooks, Simulator agent, Brev, demo | Madhan + Claude on the GB10 | `coolorbit/board/`, `coolorbit/sim/`, `coolorbit/hooks/`, `scripts/`, `runs/` |
| Agents: Librarian, Scouts, Referees, Ranker, premise checker | Teammate 1 | `coolorbit/agents/`, `data/calibration/`, `data/structures/` |
| **Evaluation, dashboard, classical-MD inputs, pitch** | **You** | `coolorbit/eval/`, `dashboard/`, `data/nebius/`, `docs/pitch/`, `tests/test_eval*.py`, `tests/test_dashboard*.py` |

Everything else is read-only for you. Need a change elsewhere? Ask its owner (GitHub issue or message).
Git: one branch per task, `git pull --rebase origin main` often, PR to `main`, Madhan merges.

## 2. Context you need (short)

- The **results board** (`coolorbit/board/`) is the shared whiteboard. Every result, check, measured value,
  failure (`run_report`) and ranking is an immutable artifact with lineage. Status (simulated / under_review /
  trusted / flagged / rejected) is **derived** by the trust rule, never set. Read `coolorbit/board/schema.py`
  and `coolorbit/board/status.py`.
- How simulations are requested and reported: `docs/simulation-requests.md`.
- Melting workflow and its outputs: `coolorbit/sim/melting.py` (GB10 only; don't install nvalchemi on a laptop).
- The judging rubric gives **25% to "collective capability"**, measured by comparing arms. That is your T4.

---

## 3. Your tasks (all ✅ NO GB10 NEEDED to build)

### ✅ T4. Comparison harness (highest priority)
CLAUDE.md section 13 is the spec. Build `coolorbit/eval/`:
1. **Task set** `coolorbit/eval/tasks.json`, each task with: `id`, `prompt` (what the agent is asked),
   `kind` (`well_posed` | `ill_posed` | `trap`), `trap` (e.g. `single_phase_heating`, `langevin_transport`,
   `model_out_of_domain`, or null), `property`, `units`, and a **sourced** reference value (or null).
   Include: NVIDIA blog benchmarks (Si equation of state, O on Cu(111) adsorption, liquid-Li self-diffusion),
   NVIDIA's ill-posed LiF diffusion task, Tier-1 PCM tasks (coordinate the reference values with Teammate 1's
   `data/calibration/`; don't duplicate the lookups), and well-posed controls to measure false alarms.
2. **Metrics from boards** `coolorbit/eval/metrics.py`: for one board (one arm, one repeat) and the task set,
   compute error vs reference, trap catch rate, false-alarm rate on well-posed controls, **silent errors
   shipped** (confident wrong answers: no flags, no failure report, off by more than tolerance), and cost.
   Use `Board.snapshot()` + `derive_status()`; never edit boards.
3. **Arm definitions** `coolorbit/eval/arms.md` (+ prompts): A = single agent (Claude Code + ALCHEMI skills +
   NVIDIA's own recommended premise/validation prompt, *with web access*), B = one worker + one checker in a
   fixed pipeline, C = our collective. Same token budget, GPU hours and tools for all. The runs happen later on
   the GB10/Brev; you define *how* each arm records its outputs onto a board so the metrics compare like with like.
4. **Pre-registration**: freeze tasks + metrics + tolerances in a commit **before** any comparison run.
5. **Human action:** ask someone outside the team (an NVIDIA mentor or another team) to write a few
   **blind tasks** (some ill-posed, some fine), so nobody can say we designed the test our system passes.
Tests: metrics on small synthetic boards (label all synthetic data as such).

### ✅ T5. Dashboard
`dashboard/`: a static page the judges can open. Needs a small exporter (in `dashboard/`, using the board API)
that writes one JSON file per board; the page shows results with status **and reasons**, open needs, failed
runs, lineage, the latest ranking, and (later) the arm-comparison scorecard from T4. Hosting on Google Cloud Run
comes later and needs a team GCP account (max 2 per team): ask Madhan before creating one.

### ✅ T6. Classical-MD inputs for organic PCMs (Nebius)
MACE can't do waxes (out of domain; the Simulator refuses them and posts a need with `engine: "classical_md"`).
The independent check for organics is classical MD on Nebius Scientific AI: `submit_amber_workflow` can run
`antechamber` / `parmchk2` / `tleap` (GAFF2) and `pmemd`; `submit_openmm_workflow` runs OpenMM.
Prepare input bundles in `data/nebius/<molecule>/` (structure + scripts + README with sources) for a few
organic Tier-1 PCMs (e.g. n-octadecane, n-eicosane). Melting points need **two-phase coexistence** here too;
transport properties need NVE (`integrator: "verlet"`), never the Langevin default.
The team Nebius key is private: get it from Madhan directly (never in git or chat logs), or hand finished
bundles to the GB10 session to submit. **Ask Madhan before submitting any job** (billing unclear).

### ✅ T7. Pitch material with sources (`docs/pitch/`)
Final slides are due **Sun 1:00 PM**. Draft `docs/pitch/outline.md`: problem (orbital AI compute is heat-
limited), the thermal-battery idea, the agent collective, evidence slides (placeholders for numbers), demo
script (CLAUDE.md section 19), and honest limits (CLAUDE.md section 6). **Every factual claim needs a source
link** (e.g. Project Suncatcher, the NASA/ISS PCM experiment, NVIDIA's ALCHEMI agents blog). No source → leave
it out or mark it `[NEEDS SOURCE]`. Numbers from our runs get filled in Sunday morning.

### ⛔ Not yours
ALCHEMI runs, hooks, Brev instances, the live demo (GB10 stream); agents and calibration data (Teammate 1).

## 4. Ground rules (CLAUDE.md section 15)
Never fabricate values or citations; report failures and uncertainty; never use single-phase heating for a
melting point or Langevin production for transport; don't spend shared money (Brev, Nebius, Madhan's API
credits) without asking; keep claims honest: we simulate and rank, we don't discover materials.
