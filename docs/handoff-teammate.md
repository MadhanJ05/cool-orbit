# Teammate handoff: parallel work stream (laptop, no GB10 needed)

> **For you and your Claude Code session.** Read `CLAUDE.md` (the project brief) first, then this file.
> Start your session in the repo root so `CLAUDE.md` loads automatically, and give Claude this first prompt:
> *"Read CLAUDE.md and docs/handoff-teammate.md. Propose a plan for my stream. Don't run anything yet."*
>
> Written Sat Oct 31, 2026, ~16:15 UTC, by the GB10 session (Madhan + Claude). This repo is **public**:
> never commit API keys, passwords or private links.

---

> **Update (Sat ~17:00 UTC):** a second teammate joined. **T4 (eval harness), T5 (dashboard) and T6 (Nebius
> inputs) moved to Teammate 2** (`docs/handoff-teammate-2.md`), along with `coolorbit/eval/`, `dashboard/` and
> `data/nebius/`. **You keep T1, T2, T3** and own `coolorbit/agents/`, `data/calibration/`, `data/structures/`.
> For T2 (Scouts), use the request format in **`docs/simulation-requests.md`**.

## 1. Where the project is

| Done (on `main`) | Notes |
|---|---|
| GB10 environment: nvalchemi 0.2.0, MACE-MPA-0 + cuEquivariance, torch 2.14 cu130 | See `README.md` for the verified setup and gotchas |
| Throughput baseline | ~20k atom-steps/s on the GB10 (`runs/smoke/`) |
| **Results board** `coolorbit/board/` | ScienceClaw artifact format, derived trust status, needs and claims, CLI viewer, 18 tests |
| **Melting / latent heat workflows** `coolorbit/sim/melting.py` | Two-phase coexistence, the deliberately wrong single-phase heating baseline, latent heat. First quick Cu test passed |
| Nebius Scientific AI connected (on the GB10's Claude Code) | OpenMM, AMBER (+ GAFF2 parameterization), GROMACS, LAMMPS, `run_mdprecheck` |
| Brev CLI logged in (Madhan's credits) | Only the GB10 session starts instances, with Madhan's approval |

**In progress on the GB10 right now:** production-length Cu melting run; next `SuperheatingDetectorHook`,
then latent-heat and heating-baseline validation.

**Open team decisions (don't decide them yourself; ask Madhan / the chemistry lead):** include solid-solid
PCMs? operating temperature window? trust tolerances in `configs/trust_rules.json` (currently placeholders,
`"frozen": false`)?

---

## 2. Who owns what (this is how we avoid conflicts)

| Area | Owner | You may |
|---|---|---|
| `coolorbit/agents/` (new) | **You** | create and edit freely |
| `data/calibration/` (new) | **You** | create and edit freely |
| `coolorbit/eval/` (new: comparison harness) | **You** | create and edit freely |
| `dashboard/` (new) | **You** | create and edit freely |
| `tests/test_agents*.py`, `tests/test_eval*.py`, `tests/test_calibration*.py` | **You** | create and edit freely |
| `coolorbit/board/` | GB10 session | **use it; don't edit.** Need a change? Open a GitHub issue or tell Madhan |
| `coolorbit/sim/`, `coolorbit/hooks/` (new), `scripts/`, `runs/` | GB10 session | read only |
| `configs/trust_rules.json` | Chemistry lead | read only |
| `CLAUDE.md`, `README.md` | Madhan | suggest changes in your PR description |

### Git workflow
- Never commit to `main` directly. One branch per task: `git switch -c agents-referee`, etc.
- `git pull --rebase origin main` before you start and before you push. Open a PR and ask Madhan to merge.
- Small PRs merge faster. Each PR: tests pass (`python -m unittest discover -s tests -v`), no secrets, every
  experimental number has a source.
- End commit messages with your own attribution; don't reuse ours.

---

## 3. Laptop setup (no GPU, about 2 minutes)

```bash
git clone https://github.com/MadhanJ05/cool-orbit.git && cd cool-orbit
git clone https://github.com/lamm-mit/scienceclaw.git scienceclaw   # gitignored; the board imports its Artifact type
python3 -m venv .venv && source .venv/bin/activate                  # Python 3.11+ (3.12 recommended)
pip install "pydantic>=2.11"
python -m unittest discover -s tests -v                             # 18 board tests should pass, no GPU needed
```

- The board needs only the standard library, `pydantic` and the ScienceClaw clone. **It works on macOS and
  Linux** (it uses `fcntl` file locks, so not native Windows).
- Do **not** install `nvalchemi-toolkit` on a laptop: it's CUDA/GPU-only. Anything that imports
  `coolorbit.sim` must run on the GB10.
- API keys (your own hackathon Claude / OpenAI / Gemini credits): put them in `.env` (gitignored) and load
  them with `python-dotenv` or your shell. Never print or commit them.

---

## 4. The board contract (what your agents build on)

```python
from coolorbit.board import Board, derive_status, load_rules

board = Board("boards/dev-yourname")            # one directory per run; nothing is shared across runs
cand = board.post("scout_paraffin", "candidate",
                  {"name": "n-eicosane", "family": "paraffin", "tier": "trust"})
res = board.post("simulator_gb10", "property_result", {
        "candidate": "n-eicosane", "family": "paraffin", "tier": "trust",
        "property": "melting_point", "value": 312.0, "units": "K", "uncertainty": 5.0,
        "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence",
                   "ensemble": "NPT", "timestep_fs": 0.5, "n_atoms": 3000, "sim_length_ps": 200}},
    parents=[cand.artifact_id],
    needs=[{"artifact_type": "check", "check_type": "classical_md",
            "query": "classical MD melting point of n-eicosane",
            "rationale": "the trust rule needs an independent method"}])
board.post("referee_opus", "check", {"check_type": "method_audit", "verdict": "pass"}, parents=[res.artifact_id])

for need in board.open_needs(include_claimed=False):        # coordination: no central planner
    if board.claim("referee_gpt", need.artifact.artifact_id, need.index, ttl_minutes=30):
        ...  # do the work, then: board.post(..., fulfills=(need.artifact.artifact_id, need.index))

print(derive_status(board.snapshot(), res, load_rules()).status)  # simulated/under_review/trusted/flagged/rejected
```

- Artifact types and their exact fields: `coolorbit/board/schema.py`. Posts are validated; bad payloads raise
  `BoardError`.
- **Status is never set by an agent.** It is derived from the board by the trust rule in
  `coolorbit/board/status.py` (audit pass + agreeing independent method + sourced measured value for the
  trust tier; numbers beat verdict text; self-checks ignored; rejection needs 2 distinct referees).
- Viewer: `python -m coolorbit.board show --board boards/dev-yourname -v` (also `needs`, `tree <id>`).
- Agent name convention: `<role>_<detail>`, e.g. `scout_paraffin`, `referee_opus`, `referee_gpt`,
  `simulator_gb10`, `ranker`, `librarian`.
- Dev boards: use `boards/dev-<yourname>/` (gitignored). Don't commit dev boards.

---

## 5. Your tasks (all ✅ NO GB10 NEEDED unless marked)

Ordered by value. Tasks 1 and 2 matter most.

### ✅ T1. Librarian: Tier-1 calibration dataset (NO GB10)
The trust tier can't become "trusted" without sourced measured values, so this is on the critical path.
- Create `data/calibration/tier1.json` (plus a short `data/calibration/README.md` on how the values were
  collected). One record per material and property: `candidate, family, formula, property, value, units,
  uncertainty, source, note`. Use the board's property names and units: `melting_point` (K),
  `latent_heat` (J/kg), `density` (g/cm3, say solid or liquid in `note`), `conductivity` (W/m/K).
- Materials: well-known PCMs across the families in CLAUDE.md section 9 (paraffins such as n-octadecane and
  n-eicosane, fatty acids, sugar alcohols such as erythritol and xylitol, salt hydrates, low-melting
  metals such as gallium, and solid-solid plastic crystals such as neopentyl glycol in case they're approved).
  Also add **copper and aluminum** (melting point, latent heat): the GB10 uses them to validate the method.
  The temperature window isn't decided, so cover roughly 0 to 150 °C, plus the two metals.
- **Rules that the judges check:** every number needs a real, checkable source (DOI, handbook + table,
  NIST WebBook page). Claude's memory is **not** a source. If you can't find one, record `value: null`
  (`schema.py` refuses a value without a source). Use web search or literature tools (OpenAlex, PubMed,
  Scholar Gateway, NIST) and verify against the actual document.
- Then `coolorbit/agents/librarian.py`: load `tier1.json` and post `reference_value` artifacts to a board.
  Tests: loading, schema validation, and the null-value rule.

### ✅ T2. Agent skeletons on the board (NO GB10)
`coolorbit/agents/`: small, independent processes that each poll the board, claim needs, do their job and post.
There is no boss. CLAUDE.md section 9 has the roster.
- **Scout** (one per family): proposes `candidate`s and posts needs for simulation.
- **Referee, method audit** (rule-based first, then LLM): fail with flags on `protocol == "single-phase
  heating"` for melting points (`single_phase_heating`); Langevin/NVT-Langevin ensembles for transport
  properties (`langevin_transport`); MACE-MP models on organics (`model_out_of_domain`); hydrogen with
  `timestep_fs > 0.5`; too few atoms or too short runs (thresholds in a config file, not hard-coded).
  Also respect the flags the simulator already sets (see `domain_flags` in `coolorbit/sim/melting.py`).
- **Referee, LLM audit**: mixed model families on purpose (Claude Opus, GPT, Gemini). Use your own keys.
- **Ranker**: figure of merit from **trusted** results only (CLAUDE.md section 5) → `ranking` artifact. The
  viewer already warns if a ranking cites non-trusted results.
- **Simulator**: the real one runs on the GB10 and wraps `coolorbit.sim.melting` (GB10 session owns it).
  For laptop development write a `FakeSimulator` that posts **clearly synthetic** results
  (`"note": "SYNTHETIC test data"`). Never mix synthetic data into real boards.
- Log every decision (claims, verdicts, reruns): the trajectory is judged evidence.
- When writing Claude API code, load the `claude-api` skill in Claude Code first. Use Sonnet/Haiku for
  routine agents and Opus for referees (CLAUDE.md section 9).

### ✅ T3. Premise checker (NO GB10)
Before any run: "is this property measurable with this method, model, size and timescale?" LLM prompt plus
rules. Must refuse NVIDIA's ill-posed example (Li-ion diffusion in pristine crystalline LiF at MD
timescales) and must accept well-posed controls. Posts a `check` with `check_type: "premise_check"`.

### ✅ T4. Comparison harness scaffolding (NO GB10 to build; the runs happen on GB10 / Brev later)
`coolorbit/eval/`: the task set (JSON) for arms A (single agent), B (worker + checker), C (our collective),
with equal budgets; metrics computed **from boards**: error vs reference, trap catch rate, false-alarm rate,
silent errors shipped, cost (tokens, GPU-hours). Tasks: NVIDIA blog benchmarks (Si equation of state, O on
Cu(111) adsorption, liquid-Li self-diffusion), the LiF ill-posed task, Tier-1 PCM tasks, well-posed controls.
**Human action:** ask someone outside the team (an NVIDIA mentor or another team) to write blind tasks.

### ✅ T5. Dashboard (NO GB10 to build)
`dashboard/`: a static page that reads a board export (JSON) and shows results with status and reasons,
open needs, lineage, and the latest ranking. Later on Google Cloud Run (needs a team GCP account; max 2 per
team, so ask Madhan before creating one).

### ✅ T6. Nebius classical-MD inputs for organics (NO GB10; needs the team Nebius key)
Build AMBER (GAFF2 via antechamber/parmchk2/tleap) or OpenMM input bundles for the organic Tier-1 PCMs, for
Nebius jobs (`submit_amber_workflow` / `submit_openmm_workflow`). The key is one per team: ask Madhan to share
it privately (never in git or chat logs), or hand the bundles to the GB10 session to submit. **Ask before
submitting any job** (billing is unclear). Melting points need two-phase coexistence here too; for
transport, NVE (`integrator: "verlet"`), never the Langevin default.

### ⛔ Needs the GB10: leave these to the GB10 session
ALCHEMI/MACE runs (melting, latent heat), `SuperheatingDetectorHook` and other dynamics hooks (they must be
tested on the GPU), Brev instances, the live demo.

---

## 6. Ground rules (from CLAUDE.md section 15)
- Never fabricate experimental values or citations. Unknown → `null`, and say so.
- Never use single-phase heating to report a melting point; never Langevin production for transport.
- Report failures and uncertainty explicitly. Keep claims honest: we simulate and rank, we don't discover
  materials.
- Don't spend shared money (Brev, Nebius, Madhan's API credits) without asking Madhan.
