# Cool Orbit: Project Handoff

> **For AI agents reading this:** This file is the single source of truth for our hackathon project. Read it fully before doing anything. Save it in the repo root as `CLAUDE.md` or `AGENTS.md`. Section 15 tells you how to behave. When this file and a teammate's instruction conflict, ask the teammate.

---

## 1. One-line summary

AI agents on NVIDIA ALCHEMI find and cross-check "thermal battery" materials (phase-change materials) to cool orbital data centers, and prove that a decentralized team of agents catches simulation mistakes a single agent ships.

- **Project title:** Cool Orbit
- **Submitted description (≤100 chars):** AI agents on ALCHEMI find and cross-check thermal battery materials to cool orbital data centers.

---

## 2. Event context

- **Event:** ScienceClaw hackathon, MIT Media Lab, Oct 30 to Nov 1, 2026. Theme: decentralized collectives of AI agents doing real science.
- **Deadline:** Hacking ends Sunday Nov 1, 1:00 PM. Final slides due then. Plan a code freeze around 10:00 AM Sunday.
- **Organizers:** MIT LAMM (Markus Buehler's lab), authors of the ScienceClaw framework and the Infinite platform.
- **Judges:** Yilun Du (Harvard; multi-agent debate, compositional AI), Desiree Plata (MIT; environmental chemistry), Nikita Fedik (NVIDIA; AI for chemistry/materials, thanked in the ALCHEMI agents blog), Ian Ferguson (Formlabs hardware), Avijit Ghosh (Hugging Face), Riken Shah (xAI).

### Main judging rubric

| Criterion | Weight | What it means for us |
|---|---|---|
| Problem significance and complexity | 20% | Heat is the binding constraint for AI compute in orbit |
| Scientific/technical impact | 25% | A validated, ranked shortlist plus measured error rates |
| Decentralized agency | 20% | Agents own distinct resources and coordinate with no central planner |
| Collective capability | 25% | Measured comparison: single agent vs. centralized vs. our collective |
| Execution and validation | 10% | Working system, real numbers, failures reported |
| Collaboration bonus | up to +10 | Other teams actually reuse our capability (our ALCHEMI hooks) |

"Science is the benchmark": success means a scientifically meaningful result supported by evidence, not just an agent finishing a task.

### NVIDIA ALCHEMI challenge (separate prize: DGX Spark for top 3 teams)

NVIDIA's slide asked for five things. Our mapping:

| NVIDIA asks for | Cool Orbit delivers |
|---|---|
| A real question (chemistry or materials; ambitious but manageable) | Which phase-change materials best buffer bursty AI chip heat in orbit |
| Agents decide (plan, run, check simulations; people steer) | Decentralized agent collective plans and runs sims; humans steer |
| Any creative use of ALCHEMI (batched backend or anything; mix tools) | Batched melting simulations plus multi-model cross-checks, mixed with Nebius classical MD |
| Evidence (numbers, an independent check, failures reported) | Error vs. measured data, independent classical MD check, trap catch and false-alarm rates |
| A reusable artifact (tool, model, or harness) | New ALCHEMI dynamics hooks other teams can plug in, plus a rerunnable workflow |

Artifacts NVIDIA explicitly wants: "a framework to solve complex challenges", "a hook", "a model", "a workflow others can rerun".

---

## 3. The problem (why this matters)

- Google's Project Suncatcher launched its first prototype satellite on Oct 1, 2026: four TPUs, roughly 1 kW of solar power.
- It runs **one TPU at a time, in 15-minute bursts**, "to avoid straining the satellite's power and thermal management systems" (TechCrunch, Oct 1 2026: https://techcrunch.com/2026/10/01/google-thinks-spacexs-starship-has-to-launch-1600-times-before-space-data-centers-get-off-the-ground). The limit is power and thermal together; don't say it "shuts down to cool off".
- In space there is no air or water cooling. Heat must travel (via heat pipes) to radiators, which emit it as infrared radiation.
- Google is testing heat pipes and radiators and calls cooling a central research challenge.

**Key physics framing (say this correctly):**
- A better material does not make chips produce less heat.
- Radiator capacity is mostly set by radiator area and temperature. Materials do not eliminate radiators.
- The lever we target is **transient buffering**: absorb the heat spike during a compute burst, release it during the rest period. This lets the heat-rejection system be sized for average load instead of peak load.

---

## 4. The idea: thermal batteries (phase-change materials, PCMs)

A PCM absorbs heat by melting (latent heat) without its temperature rising much. During the chip's burst, the PCM melts and soaks up heat. During the rest period, it refreezes while the radiator sheds the stored heat.

- This is real flight technology. NASA flew a paraffin-wax PCM heat sink experiment on the ISS. Industry uses PCMs for pulsed electronics.
- **NASA's ISS flight experiment used paraffin wax** (NASA NTRS 20140016955, https://ntrs.nasa.gov/citations/20140016955). Paraffin stores a lot of heat per kilo, but conducts heat poorly (solid n-docosane: 0.49 W/m·K, Paul et al., J. Therm. Anal. Calorim. 148, 5687 (2023), doi:10.1007/s10973-023-12107-2). Don't call wax "the default" or say "every" space PCM is wax: no source.
- **Liquids are awkward in microgravity** (containment, voids, sloshing).

**Back-of-envelope (for the pitch):** 15 extra minutes at ~1 kW is ~900 kJ. Paraffin-class materials store on the order of 200 kJ/kg, so roughly 4 to 5 kg of PCM could hold an extra 15 minutes of full-power heat, before counting what the radiator rejects in parallel. Treat this as an order-of-magnitude illustration, not a design number.

---

## 5. The scientific question and goals

**Question:** Which materials make the best thermal battery for bursty AI chips in orbit?

**Figure of merit (rank candidates on):**
1. **Transition temperature in the operating window.** It must melt/transition below the chip's max allowed temperature and above the radiator's operating temperature. **The exact window is an open team decision** (see Section 14).
2. **Latent heat per kilogram** (J/kg). This is primary, because launch cost scales with mass.
3. **Thermal conductivity.** Secondary. Heat must get in and out fast.
4. **Stability over many cycles.** Qualitative or literature-based for v1.
5. **Microgravity friendliness.** Solid-solid transitions are preferred if they perform.

**Stretch target:** find a candidate that beats paraffin on BOTH heat stored per kilo AND conductivity, in the operating window. No known material clearly wins on both, so it is an open question with a clear finish line.

**Two-tier search:**
- **Tier 1, the trust tier:** known PCMs with measured values. Proves our simulations match reality and calibrates error bars.
- **Tier 2, the discovery tier:** under-characterized candidates (no measured melting point or latent heat in the literature), plus agent-proposed combinations such as eutectic blends. Any Tier 2 result is labeled a **prediction**, ready for lab testing.

**Ambition upgrade (pending chemistry lead's decision):** include **solid-solid PCMs** (e.g., plastic crystals such as neopentyl glycol). They absorb heat by rearranging their internal structure while staying solid, so there's no liquid to contain in zero-g. This is an active research frontier and much less explored than wax.

---

## 6. What we claim, and what we do NOT claim

**We claim:**
- A ranked, cross-checked shortlist of thermal battery candidates for orbital AI compute, with error bars.
- New predicted data for candidates nobody has measured (clearly labeled as predictions).
- Quantified evidence that our decentralized agent collective catches simulation errors a single agent ships.
- Reusable ALCHEMI hooks that detect common simulation failure modes.

**We do NOT claim:**
- That we invented or made a new material. We simulate, we don't synthesize.
- That we beat the known champions of thermal conductivity (diamond, cubic BN, BAs). That's not our target.
- Exact melting points. AI force fields have errors; we measure and report them.
- That materials eliminate radiators.

**If a judge asks "is this a new material?":** "No, and we don't claim it is. We find which materials work best for orbital AI chips, including some nobody has measured, and our agents prove the predictions can be trusted."

---

## 7. The gap we close (NVIDIA's own findings)

Source: NVIDIA Technical Blog, Aug 18, 2026, "How AI Coding Agents Can Unlock Materials Simulation with NVIDIA ALCHEMI Toolkit". It benchmarked Claude Code (Opus 4.8) writing 45 ALCHEMI pipelines (MACE-MPA-0, H200 GPUs). Nikita Fedik (our judge) is acknowledged. Key findings:

1. **No pushback on ill-posed tasks.** Across every tested condition, zero configurations questioned a physically ill-posed task. In an earlier probe, every agent complied with a request for Li-ion diffusion in pristine crystalline LiF, a property not measurable at the simulated timescale. (Caveat: their sandbox had no web access, and a web-equipped agent might catch it. **Our baseline must get web access too.**)
2. **Thermostat damping.** Scripts that defaulted to Langevin production dynamics damped liquid-lithium diffusion by 3 to 5x vs. NVE. The results were statistically clean but physically wrong, and only an independent experimental anchor exposed it.
3. **Self-tests can be fake.** One script validated its unit conversion with synthetic data generated using the same wrong constant, so its self-test passed.
4. **Defaults over improvements.** 38 of 45 scripts used FIRE; none used the documented improved FIRE2.
5. **GPU-only failures.** Seven scripts failed on GPU-specific paths that CPU self-tests didn't catch.
6. **Their recommendation:** explicitly ask agents for premise checks, validation, uncertainty estimates, and recovery of an independent known result.

**Our thesis:** a decentralized collective with independent referees catches these failures systematically, better than a single agent given NVIDIA's own recommended prompt.

---

## 8. System architecture

**Rule #1: the Dell Pro Max GB10 is the star.** NVIDIA's prize is for the best DGX Spark use case, so the full loop and the live demo must run on the GB10. Everything else is overflow or independent checking.

| Resource | Role |
|---|---|
| **Dell Pro Max GB10** (128 GB unified memory, Blackwell, ARM64) | Home base. Runs the agent collective, ALCHEMI batched simulations (MACE, AIMNet2), the results board, our hooks, and the live demo |
| **Claude API** ($100 per member) | Main reasoning for agents. Opus for referees and premise checks; Sonnet/Haiku for routine agents to stretch credits |
| **OpenAI API** ($50 per member), **Gemini** (Tier 1 key) | Reviewer diversity (different model families make different mistakes). Gemini's high rate limits suit bulk literature lookups of measured values |
| **Brev GPUs** ($150 per member) | Overflow: heavy melting runs, the separate UMA cross-check environment, overnight repeats of the comparison experiment |
| **Nebius Scientific AI Platform** (one credential set per team) | Independent second-opinion lab: classical MD (OpenMM, AMBER), a fundamentally different method from ALCHEMI's AI force fields. Agents call it via MCP |
| **Nebius Cloud H100s** (16 shared across ALL teams) | Low priority only. Jobs queue up to 30 min then fail. Non-critical extra runs |
| **Google Cloud** (max 2 accounts per team) | Cloud Run hosts the public demo dashboard; Cloud Storage backs up the results ledger. Accounts auto-delete Sunday night |

### Data flow

```
Brains (Claude / GPT / Gemini)  <-- ask/answer -->  GB10 home base
                                                     |-- Agent team (Scouts, Simulators, Referees, Ranker)
                                                     |-- ALCHEMI (MACE + AIMNet2)
                                                     |-- Shared results board (ScienceClaw)
                                                     |-- Our checking hooks
                                                     |-- Live demo
GB10 --heavy jobs--> Brev --results--> GB10
GB10 --check this--> Nebius (OpenMM/AMBER via MCP) --verdict--> GB10
Real-world measured data --compare--> Referees
GB10 --publish--> Google Cloud dashboard --live link--> Judges
GB10 --share hooks--> Other teams (collaboration bonus)
```

---

## 9. The agent team

All agents live on the GB10 and "think" by calling cloud LLMs. **There is no boss agent.** Agents coordinate only through the shared results board (ScienceClaw artifacts with lineage plus a needs index).

| Agent | Owns | Does | Model |
|---|---|---|---|
| **Scouts** (one per material family) | A family: paraffins/alkanes, fatty acids and sugar alcohols, salt hydrates, low-melting metals/alloys, solid-solid plastic crystals (if approved) | Proposes candidates, finds structures, posts to the board, decides where its family's compute budget goes | Claude Sonnet |
| **Simulators** | The ALCHEMI runtime and GPU queue | Turns candidates into simulation results (transition temperature, latent heat, density); sends overflow to Brev | Claude Sonnet + ALCHEMI agent skills |
| **Referees** (several, independent) | Distinct checking methods | Try to prove every result wrong (method audit, second force field, classical MD, literature anchor). Any referee can challenge any result | Claude Opus, GPT, Gemini (mixed on purpose) |
| **Ranker** | The shortlist | Maintains the best-first list from trusted results only, using the figure of merit | Claude Sonnet |
| **Librarian** (optional, can be a Scout tool) | The calibration dataset | Fetches measured values WITH sources for Tier 1 materials | Gemini |

**Decentralization requirements (for the rubric):**
- Each agent has distinct information, resources, or capabilities (a family, a GPU queue, a checking method, a different model).
- No single referee is a gatekeeper. Trust comes from independent reproductions agreeing.
- Agents post needs to the board (e.g., "need classical MD check on candidate X") and others pick them up.
- Log every decision, challenge, and rerun. The trajectory itself is evidence.

---

## 10. Shared results board protocol

Every result is one record. Suggested schema:

```json
{
  "id": "res_0042",
  "candidate": "n-eicosane",
  "family": "paraffin",
  "tier": "trust | discovery",
  "property": "melting_point | latent_heat | conductivity | density",
  "value": 0.0,
  "units": "K | J/kg | W/m/K | g/cm3",
  "uncertainty": 0.0,
  "method": {
    "engine": "ALCHEMI | OpenMM | AMBER",
    "model": "MACE-MPA-0 | AIMNet2 | UMA | GAFF | ...",
    "protocol": "two-phase coexistence | hysteresis | single-phase heating",
    "ensemble": "NVE | NVT-Langevin | NPT | NPH",
    "timestep_fs": 0.0,
    "n_atoms": 0,
    "sim_length_ps": 0,
    "seeds": 0
  },
  "produced_by": "simulator_2",
  "parents": ["cand_0017"],
  "reference": {"value": null, "source": "citation or null"},
  "checks": [
    {"by": "referee_opus", "type": "method_audit", "verdict": "pass | fail", "note": ""},
    {"by": "referee_gpt", "type": "second_mlip", "verdict": "agree | disagree", "delta": 0.0}
  ],
  "status": "proposed | simulated | under_review | trusted | flagged | rejected",
  "flags": ["superheating_risk"]
}
```

**Status lifecycle:** proposed → simulated → under_review → trusted / flagged (rerun) / rejected.

**Trust rule (proposal; tune it):** a result is **trusted** only if (a) the method audit passes, AND (b) at least one independent method (second MLIP or classical MD) agrees within a pre-set tolerance, AND (c) for Tier 1, it matches the measured value within the calibrated error. **Pre-register tolerances before running the comparison experiment.**

---

## 11. Simulation methods (chemistry lead has final say)

**Melting point**
- **Do NOT use single-phase heating** of a perfect crystal to read off the melting point. Without a nucleation site, crystals superheat, and the jump appears well above the true melting point. This is our signature natural trap.
- **Preferred:** two-phase (solid-liquid) coexistence: half crystal, half liquid in one box, find the temperature where the interface neither grows nor shrinks.
- **Cheaper cross-check:** the hysteresis method (combine superheating and supercooling temperatures).
- Use enough atoms and long enough runs. Report size and length.

**Latent heat**
- Enthalpy difference between liquid and solid at the transition temperature (NPT runs of each phase), converted to J/kg.

**Ensembles and protocols**
- Transport properties (diffusion, conductivity, viscosity) need NVE production, not Langevin (NVIDIA's documented 3 to 5x damping).
- Check energy conservation in NVE (drift).
- Choose timesteps appropriate for light atoms (hydrogen-rich organics need smaller timesteps).
- Prefer FIRE2 over FIRE for relaxations.

**Force fields (MLIPs) available in ALCHEMI Toolkit**
- **MACE (MACE-MPA-0):** strong for inorganic crystals (metals, salts).
- **AIMNet2:** organic molecules. **Verify periodic-system support** in the installed toolkit version before relying on it for molecular crystals.
- **UMA:** broad coverage. **Must be installed in its own environment** (the `uma` extra conflicts with `mace` and the CUDA extras). Weights are gated on Hugging Face, so request access early.
- **TensorNet (MatPES):** another inorganic option.
- **Out-of-distribution warning:** MACE-MP models were trained on inorganic materials. Using them for waxes is an automatic referee flag.

**Independent classical check (Nebius)**
- OpenMM or AMBER with established classical force fields for organics. Agreement between AI force fields and classical MD, anchored to experiment, is our strongest evidence.

**Thermal conductivity**
- Heavy and error-prone for AI force fields. v1: use literature values with sources. Stretch: NVE-based calculation for the top few candidates on Brev.

---

## 12. Trap catalog (what referees and hooks must catch)

| Trap | Why it's wrong | Catch it by |
|---|---|---|
| Single-phase heating used for melting point | Superheating overshoots | Protocol audit; rerun with coexistence |
| Langevin dynamics for transport properties | Thermostat friction damps transport | Ensemble guard; require NVE |
| Ill-posed premise (property not measurable at MD timescale, e.g., diffusion in a pristine crystal) | Physically meaningless number | Premise check before running |
| Force field out of its training domain | Unreliable energies and forces | Model-domain check; second MLIP |
| Box too small / run too short | Finite-size and statistics errors | Size and equilibration checks |
| Energy drift in NVE | Broken integration or timestep too large | Drift monitor |
| Self-validating tests | Same wrong constant in test and code | Independent reference values only |
| Unit or reference-convention errors | Silent order-of-magnitude errors | Unit audit; compare to known values |

### Hooks we build (the reusable artifact)

ALCHEMI already ships a hooks module (`nvalchemi.dynamics.hooks`, e.g., `EnergyDriftMonitorHook`, `NaNDetectorHook`). Build new hooks following the same pattern:
1. **SuperheatingDetectorHook** (top priority, ship first): flags melting estimates from single-phase heating of defect-free crystals.
2. **EnsembleGuardHook:** blocks transport properties measured under Langevin production.
3. **EquilibrationCheckHook / SizeCheckHook:** flags under-equilibrated or too-small systems.
4. **Premise checker** (agent-level, not a dynamics hook): asks "is this property measurable with this method and timescale?" before a run.

Read the actual hooks API in the cloned `nvalchemi-toolkit` repo before writing code. Do not guess the interface. **Goal: SuperheatingDetectorHook working and shared with other teams by Saturday afternoon.**

---

## 13. Evaluation protocol (the 25% "collective capability" score)

**Arms (equal budget: same LLM token budget, same GPU hours, same tools including web access):**
- **A. Single agent:** Claude Code + ALCHEMI skills + NVIDIA's own recommended premise-check/validation prompt.
- **B. Centralized:** one worker agent + one checker agent in a fixed pipeline.
- **C. Decentralized collective:** our system.
- **(Optional) D. Homogeneous collective:** same as C but all agents use one model. Shows whether model diversity matters.

**Task set:**
1. NVIDIA's blog benchmark tasks (silicon equation of state, O on Cu(111) adsorption, liquid-lithium self-diffusion). Compare directly with their published numbers.
2. The LiF ill-posed task.
3. Our Tier 1 PCM calibration tasks (known measured values).
4. **Blind tasks** written by someone outside the team (an NVIDIA mentor or another team), some ill-posed, some fine. Prevents "you designed the test your system passes".
5. **Well-posed controls** mixed in, to measure false alarms.

**Metrics:**
- Error vs. reference values
- Trap catch rate
- False-alarm rate on correct results
- Silent errors shipped (confident wrong answers)
- Cost (tokens, GPU-hours)

**Rules:** run multiple seeds and repeats (Brev overnight). Report everything, including where our collective fails. A clean negative result beats a fuzzy positive one.

---

## 14. Open decisions (owner: team, decide early)

1. **Include solid-solid PCMs?** Chemistry lead decides tonight.
2. **Operating temperature window** for the transition temperature (based on chip limits and radiator temperature).
3. **Candidate count:** set from the GB10 throughput test (aim 15 to 30 rigorous candidates, not hundreds).
4. **Organic force field:** AIMNet2 vs. UMA, depending on verified periodic support.
5. **Trust tolerances:** pre-register before the comparison runs.
6. **Model per agent:** given credit budgets.

---

## 15. Instructions for AI agents working on this repo

1. **Premise-check every task** before writing simulation code: is the property well-defined and measurable with this method, model, size, and timescale?
2. **Never fabricate experimental values or citations.** Every reference value needs a source. If you can't find one, record `null` and say so.
3. **Never use single-phase heating to report a melting point.** Never use Langevin production for transport properties.
4. **Log everything to the results board** with full method metadata and lineage.
5. **Prefer the GB10.** Ask a human before starting a Brev instance, state the hourly rate, and always stop it when done (it bills until stopped).
6. **Match ALCHEMI skill versions to the installed toolkit version.**
7. **Test on the GPU,** not just CPU. NVIDIA saw GPU-only failures.
8. **Report failures and uncertainty explicitly.** "Failures reported" is a judging criterion.
9. **Keep claims honest:** simulate and rank, never "discovered a new material".
10. **Push code to GitHub frequently.**

---

## 16. Setup (GB10 is ARM64 + Blackwell, so use CUDA 13 builds)

```bash
# 0. Check the box
nvidia-smi && uname -m && df -h ~

# 1. Base tools
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install -y git tmux nodejs npm
curl -fsSL https://claude.ai/install.sh | bash

# 2. Main env (MACE + AIMNet2)
mkdir ~/coolorbit && cd ~/coolorbit
uv venv --seed --python 3.12 && source .venv/bin/activate
uv pip install \
  --extra-index-url https://download.pytorch.org/whl/cu130 \
  --extra-index-url https://pypi.nvidia.com \
  'nvalchemi-toolkit[cu13,mace,aimnet,ase,pymatgen]'
uv pip install matplotlib pandas scipy
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# 3. Separate UMA env (uma extra conflicts with mace/CUDA extras)
mkdir ~/coolorbit-uma && cd ~/coolorbit-uma
uv venv --seed --python 3.12 && source .venv/bin/activate
uv pip install 'nvalchemi-toolkit[uma]'
# then: hf auth login, and request UMA access on Hugging Face

# 4. ALCHEMI agent skills (match YOUR installed version) + examples
cd ~/coolorbit
uv pip show nvalchemi-toolkit
npx degit NVIDIA/nvalchemi-toolkit/.claude/skills#v0.2.0 .claude/skills   # replace with your version
git clone https://github.com/NVIDIA/nvalchemi-toolkit.git
```

- **ScienceClaw:** use `lamm-mit/scienceclaw`. Other unrelated GitHub repos share the name, so don't clone the wrong one.
- **Brev:** install the CLI, `brev login`, `brev redeem <code>`, `brev agent-skill install`, and run `bash check.sh` to diagnose.
- **Nebius Scientific AI MCP:** get the team's Scientific API key (keep it private) and the MCP snippet from forge.nebius.cloud/mcp.
- **ARM fallback:** if a package won't install or the GPU isn't detected, run inside NVIDIA's NGC PyTorch container (`docker run --gpus all -it nvcr.io/nvidia/pytorch:<latest tag>`) and install there. Brev's x86 machines are also a known-good fallback.
- **Smoke tests:** run one official ALCHEMI MD example on the GPU, then time an MD run of a ~1,000 to 2,000 atom box for a few thousand steps. Record steps/second; it sets the candidate budget.

---

## 17. Timeline

| When | Milestone |
|---|---|
| Fri night | Installs and smoke tests on GB10; throughput number; team roles; open decisions 1 and 2 made; UMA access requested |
| Sat morning | Results board and agent skeletons running; Tier 1 calibration set gathered with sources; first coexistence melting run validated against a known material |
| Sat afternoon | SuperheatingDetectorHook shipped and announced to other teams; blind tasks requested from outsiders; Tier 2 screening starts |
| Sat night | Overnight comparison runs (arms A to C, repeats) on Brev + GB10; Nebius classical cross-checks |
| Sun ~10 AM | Code freeze; live demo rehearsed; dashboard live on Cloud Run |
| Sun 1 PM | Final slides submitted |

---

## 18. Team roles

- **Lead / pitch / demo:** Madhan
- **Computational chemistry lead:** picks methods, owns the trust tolerances and the solid-solid decision
- **Agent engineer(s):** ScienceClaw integration, agent roster, results board, comparison harness
- **Infra / compute:** GB10 setup, Brev, Nebius MCP, dashboard on Cloud Run

---

## 19. Demo script (live duel)

1. Same task to both: "Find the melting point of [known PCM]."
2. **Single agent:** heats a perfect crystal, reports a clean number with tight error bars, which is too high.
3. **Our collective:** a referee flags superheating, the hook fires, a simulator reruns with two-phase coexistence, a second method agrees, the result lands near the measured value and gets a "trusted" stamp.
4. Show the scorecard: catch rate, false alarms, error vs. experiment across all arms.
5. Show the ranked shortlist for orbital AI chips, with Tier 2 predictions labeled.
6. Show the hook in use by another team.

---

## 20. House rules

- The GB10 is the star. The demo runs there.
- Stop Brev instances when done. Billing runs until stopped.
- Push to GitHub often. Google Cloud accounts auto-delete Sunday night.
- Every service requires your registered hackathon email.
- Max 2 Google Cloud accounts per team (more disqualifies us from prizes).
- Nebius Scientific AI: one request per team. Keep the API key private.
- Never share the event-specific Claude credit link outside the hackathon.

---

## 21. Pitch lines

- **One-liner:** AI agents that find better cooling for space data centers, and check each other so the answer can be trusted.
- **Opening:** "Google's AI satellite can run one chip at a time, in 15-minute bursts, to spare its power and thermal systems. NASA's ISS thermal-battery experiment used paraffin wax. Wax stores heat well but moves it badly, and it melts into a liquid that's a pain in zero gravity." (Sources: TechCrunch, Oct 1 2026, see section 3; NASA NTRS 20140016955, https://ntrs.nasa.gov/citations/20140016955. Never say "shuts down every 15 minutes to cool off" or "every thermal battery in space is wax": neither has a source.)
- **The twist:** "NVIDIA showed AI agents run simulations confidently even when the science is wrong. Our agents check each other like scientists do."

---

## 22. Glossary (plain language)

- **Phase-change material (PCM) / thermal battery:** a material that soaks up heat by melting (or rearranging its structure) and releases it when it freezes back.
- **Latent heat:** heat absorbed during melting without the temperature rising.
- **Solid-solid PCM:** absorbs heat by changing its internal structure while staying solid.
- **Molecular dynamics (MD):** simulating atoms moving over time.
- **MLIP / AI force field:** a neural network that predicts the forces between atoms (MACE, AIMNet2, UMA). Fast, but can be wrong outside its training data.
- **Classical force field:** older hand-built formulas for atomic forces (used by OpenMM and AMBER). An independent check.
- **ALCHEMI Toolkit:** NVIDIA's GPU library for running MLIP simulations in big batches.
- **Superheating:** a perfect crystal in a simulation melts too late, because melting has nowhere to start.
- **Two-phase coexistence:** simulating solid and liquid side by side to find the true melting point.
- **NVE vs. Langevin:** NVE conserves energy (right for measuring motion); Langevin adds friction to control temperature (distorts motion-based properties).
- **Hook:** a plug-in that runs during a simulation to check or control it.
- **ScienceClaw / results board:** the shared space where agents post results with their history, and request help from other agents.
