# Cool Orbit: pitch outline (draft v2, Sat Oct 31; slides due Sun Nov 1, 1:00 PM)

Rules for this deck (CLAUDE.md section 15): every factual claim carries a source; `[NEEDS SOURCE]` = don't
say it until someone finds one; `[RESULT PENDING]` = a number from our runs, filled in Sunday morning from the
dashboard export (never typed by hand). Sources are listed per slide and collected in the claims register at
the end. We simulate and rank; we don't discover or make materials.

**One-liner:** AI agents that find better cooling for space data centers, and check each other so the answer
can be trusted.

---

## Slide 1: Title
**Cool Orbit:** AI agents on NVIDIA ALCHEMI find and cross-check thermal-battery materials to cool orbital AI
compute. Team names, ScienceClaw hackathon, MIT Media Lab.

## Slide 2: Orbital AI compute is limited by heat (and power)
- Google's Project Suncatcher put its first prototype satellite in orbit on **Oct 1, 2026**: **four TPUs**, about
  **a kilowatt of continuous power**. [S1, S2]
- "The chips will run a version of the company's Gemma AI model **for 15 minutes at a time due to heat management
  constraints**" (NPR) [S2]; TechCrunch: one TPU at a time, in 15-minute bursts, "to avoid straining the
  satellite's power and thermal management systems" [S1].
- Google: "Cooling orbital data centers is a crucial research challenge." "In a vacuum, you can only diffuse
  heat via radiators." Their design: heat pipes + radiators. [S3]
- "The radiators are one of the heaviest components on the current mission" (Travis Beals, lead of Project
  Suncatcher, via NPR) [S2]. Mass is launch cost.

## Slide 3: The idea: a thermal battery between the chip and the radiator
- A phase-change material (PCM) soaks up heat by melting during a compute burst and gives it back while it
  refreezes. The radiator can then be sized for the **average** load, not the peak.
- Not a new idea in space: NASA built a **paraffin-wax** PCM heat sink for an ISS flight experiment, to "reduce
  the overall mass and volume of future exploration spacecraft thermal control systems" [S4]. And it works for
  duty-cycled payloads: paraffin PCM packs flown on the IceCube CubeSat in ISS orbit (2017), on an instrument
  switched on every other orbit, "maintained the instrument temperatures at about 18 degrees Centigrade most of
  the time" [S16]. Our chips are the same problem at ~1 kW.
- Physics, said correctly: a better material doesn't make chips produce less heat and doesn't remove the
  radiator (radiated power is set by radiator area and temperature). The lever is **transient buffering**.

## Slide 4: The trade-off nobody has solved: heat stored vs heat moved
All values measured and sourced (our Tier-1 calibration set, `data/calibration/tier1.json`):

| Material | Melts at | Latent heat | Per litre of container | Thermal conductivity |
|---|---|---|---|---|
| n-octadecane (paraffin) | 301 K [S6] | **236 kJ/kg** [S7] | 183 J/cm³ (liquid, 0.7768 g/cm³ [S17]) | **0.49 W/m·K** (solid n-docosane) [S9] |
| Gallium (metal) | 302.92 K [S10] | 80 kJ/kg [S11] | **473 J/cm³** (solid, 5.9037 g/cm³ [S18]) | **28.1 W/m·K** (liquid Ga at Tm) [S13] |

- One density rule for both: **the phase with the larger volume sets the container size**. Wax expands when it
  melts (use the liquid density); gallium expands when it freezes (use the solid density; liquid is 6.0947
  g/cm³ [S18]).
- Arithmetic (shown, not typed; checked in code): 236,155 J/kg × 0.7768 g/cm³ = 183 J/cm³; 80,174 J/kg × 5.9037
  g/cm³ = 473 J/cm³ (2.6x); 236,155 / 80,174 = 2.9x; **liquid Ga 28.1 vs solid n-docosane 0.49 W/m·K ≈ 57x**
  (different phases and different paraffins: say it that way).
- Wax stores ~3x more heat **per kilogram** (launch mass); gallium stores ~2.6x more **per litre** and moves
  heat ~57x faster. Wax also melts into a liquid that is awkward in microgravity.
- Back-of-envelope (illustration, not a design): if a full ~1 kW became heat for one 15-minute burst, that's
  1 kW x 900 s = 900 kJ: about 3.8 kg of octadecane (900/236) or 11.2 kg of gallium (900/80.2), before counting
  what the radiator rejects in parallel. [S1, S7, S11]
- **The question:** which materials make the best thermal battery for bursty AI chips in orbit, in the operating
  window, per kilogram, with enough conductivity? `[open team decision: operating window]`

## Slide 5: Why agents? And why one agent isn't enough
NVIDIA tested Claude (claude-opus-4-8) writing 45 ALCHEMI simulation pipelines on H200 GPUs [S14]:
- "Across every tested condition, **zero configurations pushed back** on a physically ill-posed task"; every
  agent complied with Li-ion diffusion in pristine crystalline LiF, "a property not measurable at the simulated
  timescale". (Their sandbox had no web access; ours does.) [S14]
- Langevin thermostats damped liquid-lithium diffusion **3-5x** vs NVE: statistically clean, physically wrong.
  [S14]
- "One script validated its unit conversion by generating synthetic data with the same wrong constant, so its
  self-test passed." 38 of 45 used FIRE, none FIRE2; seven failed only on GPU paths. [S14]
- Their advice: ask for premise checks, validation, uncertainty, and an independent known result. [S14]
- **Our thesis:** a decentralized team with independent referees catches these systematically, better than a
  single agent given NVIDIA's own advice.

## Slide 6: The collective (no boss)
- Scouts (one per material family) propose; Simulators run ALCHEMI (MACE) on the Dell Pro Max GB10; Referees try
  to prove results wrong: a **rule-based method audit** (single-phase heating, Langevin transport, out-of-domain
  model, timestep, size) and a **premise checker** (Claude Opus 5.5; undecided cases go to a Claude Fable 5.1
  adjudicator); a **Librarian** supplies sourced measured values; a Ranker keeps the shortlist.
  (`configs/llm_roles.json`, `coolorbit/agents/`)
- They coordinate only through a shared **results board** (ScienceClaw artifacts with lineage + open needs and
  claims). **No agent can set a result's status**: "trusted" is derived from evidence = method audit passed AND
  an independent method agrees AND (for known materials) it matches a sourced measurement. Rejection needs two
  independent referees. (`coolorbit/board/status.py`)
- Independent second opinion with a different physics: classical MD (MEAM potentials from the NIST
  Interatomic Potentials Repository, LAMMPS on Nebius) for In, Sn and Bi. Ga was dropped: its only available
  potential fails to hold alpha-Ga's structure (our stability check), a failure we report. [S15]
- Visual: live dashboard (results, reasons, lineage, needs, failures, shortlist).

## Slide 7: Evidence 1: collective capability (the 25% criterion)
Pre-registered comparison, same tasks, same budget and tools (web access for all) [`coolorbit/eval/arms.md`]:
A = single agent + NVIDIA's recommended prompt; B = worker + checker pipeline; C = our collective.
- Tasks: NVIDIA's benchmarks (Si EOS, O on Cu(111), liquid Li diffusion), their ill-posed LiF task, our Tier-1
  PCM tasks, well-posed controls, `[blind tasks from: NAME, if received]`.
- Scorecard `[RESULT PENDING: from the dashboard export of boards/eval/<run>/, n repeats]`:

| | A single | B pipeline | C collective |
|---|---|---|---|
| Trap catch rate | [RESULT PENDING] | [RESULT PENDING] | [RESULT PENDING] |
| False alarms on correct tasks | [RESULT PENDING] | [RESULT PENDING] | [RESULT PENDING] |
| Silent errors shipped | [RESULT PENDING] | [RESULT PENDING] | [RESULT PENDING] |
| Error vs reference | [RESULT PENDING] | [RESULT PENDING] | [RESULT PENDING] |
| Cost (tokens, $) | [RESULT PENDING] | [RESULT PENDING] | [RESULT PENDING] |

- Say: pre-registered in commit `[HASH]` before any run; "doing nothing" scores as a miss; single-agent
  self-refusals count (fairness). Report where C loses, too.

## Slide 8: Evidence 2: our simulations vs measurements
- Calibration (Tier 1): simulated melting point and latent heat vs measured, for Cu, Al and the low-melting metals
  `[RESULT PENDING: table + error bars from trusted results]`.
- The signature trap, live on our data: single-phase heating overshoots the melting point (superheating);
  two-phase coexistence lands near the measurement `[RESULT PENDING: both numbers for one metal]`.
- Independent classical check (MEAM/LAMMPS) `[RESULT PENDING; only if the Nebius runs happen]`.

## Slide 9: The shortlist
`[RESULT PENDING: Ranker's latest ranking, trusted results only, with error bars; Tier-2 entries labeled
PREDICTION]`. Figure of merit: transition in the operating window, latent heat per kg, conductivity, cycling,
microgravity friendliness.

## Slide 10: What others can reuse
- `SuperheatingDetectorHook` (ALCHEMI dynamics hook): flags melting points from single-phase heating
  (`coolorbit/hooks/superheating.py`). GPU positive control on Cu (quick settings): **3/3 melting crystals caught**
  (melted at 1630, 1873, 2042 K; naive heating reports 1600 ± 100 K vs measured 1357.77 K), **0/4 false alarms** on
  two-phase boxes [S19]. `[which teams used it: NEEDS CONFIRMATION]`
- The evaluation harness: task set, board-based metrics, sandboxed Docker runners for single-agent and pipeline
  baselines (`coolorbit/eval/`).
- The results board with derived trust (`coolorbit/board/`), the dashboard (`dashboard/`), classical-MD input
  bundles with cited potentials (`data/nebius/`). All public: github.com/MadhanJ05/cool-orbit.

## Slide 11: Honest limits
- We simulate and rank; we don't make materials or claim a new one. Tier-2 numbers are predictions for lab
  testing.
- AI force fields have errors; we measure them against data and report them. No exact melting points.
- MACE-MP is trained on inorganic materials: waxes are out of its domain (our Simulator refuses them and asks
  for classical MD).
- Alloy latent heats assume the solid is a mixture of the pure elements (intermetallics would lower them).
- Materials don't remove radiators; they buffer bursts.
- `[RESULT PENDING: list every failed run / flagged result category from the board]`.

## Slide 12: Demo (live duel, CLAUDE.md section 19)
1. Same task to both: "Find the melting point of [known metal]."
2. Single agent: heats a perfect crystal, reports a confident number that's too high.
3. Collective: the rule-based method audit flags single-phase heating, the result is **flagged**, a simulator
   reruns with two-phase coexistence, and the rerun lands close to the measured value. **Default ending: the
   rerun is "under review" (awaiting an independent method), not "trusted"**: "trusted" needs an agreeing
   independent method, and nothing produces one yet. Only if the MEAM/LAMMPS check runs for the demo metal
   (PR #7 has In/Sn/Bi bundles; Ga failed its stability check; none for Cu) can the demo end at "trusted".
4. The hook: it doesn't run inside the single agent's own script, so don't claim it "fires" there. Show the
   positive control instead (3/3 superheated crystals caught, 0/4 false alarms) [S19].
5. Scorecard; 6. shortlist; 7. the hook in another team's run (if confirmed).
Backup: a screen recording, in case the GB10 or network fails on stage.

---

## Lines to use / not to use

| Don't say | Why | Say instead |
|---|---|---|
| "Google's AI satellite has to shut down every 15 minutes to cool off" (CLAUDE.md section 21) | "15 minutes at a time due to heat management constraints" is sourced (NPR); "shut down to cool off" is our embellishment | "Google's AI satellite can only run its chips 15 minutes at a time, because of heat" [S2; S1 adds power] |
| "Every thermal battery in space today is basically wax" | No source for "every" | "NASA's ISS heat-sink experiment and the IceCube CubeSat used paraffin wax" [S4, S16] |
| GPT / Gemini referees | We have none | rule-based audit, premise checker (Opus 5.5 + Fable 5.1 adjudicator), Librarian |
| "the hook fires" in the single agent's run | The hook doesn't run inside its script | the positive control: 3/3 caught, 0/4 false alarms [S19] |
| "trusted" at the end of the demo | needs an agreeing independent method; none runs yet | "flagged, rerun, close to the measurement; awaiting an independent check" |
| "We discovered a new material" | We simulate and rank | "We found which known and under-measured materials work best, and checked the predictions" |
| "Wax conducts under 1 W/m·K" without a citation | Our calibration has no paraffin conductivity | cite n-docosane 0.49 W/m·K [S9] |

## Claims register (every source opened and read on 2026-10-31)

| ID | Claim used | Source | Checked how |
|---|---|---|---|
| S1 | Oct 1 2026 launch; four TPUs; one at a time; 15-minute bursts "to avoid straining the satellite's power and thermal management systems"; "a kilowatt of continuous power" | TechCrunch, Oct 1 2026, https://techcrunch.com/2026/10/01/google-thinks-spacexs-starship-has-to-launch-1600-times-before-space-data-centers-get-off-the-ground | page read; quotes verbatim. Note: AI Weekly reports "about 20 minutes", power-limited (https://aiweekly.co/alerts/googles-suncatcher-prototype-comes-online-runs-gemma-4-in-orbit); we use TechCrunch's original wording |
| S2 | "four specialty chips … TPUs"; "for 15 minutes at a time due to heat management constraints"; SpaceX launch, Planet; "the radiators are one of the heaviest components on the current mission" (Travis Beals, lead of Project Suncatcher) | NPR, Oct 1 2026 (corrected Oct 2), https://www.npr.org/2026/10/01/nx-s1-5983697/project-suncatcher-google-ai-data-center-space | **page read 2026-10-31; quotes verbatim**. (Google's X post: snippet only; not needed) |
| S3 | "Cooling orbital data centers is a crucial research challenge"; "In a vacuum, you can only diffuse heat via radiators"; heat pipes + radiators | Google, "Learn about Google's Project Suncatcher", https://blog.google/innovation-and-ai/models-and-research/google-research/google-project-suncatcher-facts/ | page read; quotes verbatim |
| S4 | NASA paraffin-wax PCM heat sink for an ISS flight experiment; goal "reduce the overall mass and volume of future exploration spacecraft thermal control systems" | Quinn, Stieber, Sheth, Ahlstrom, "Phase Change Material Heat Sink for an ISS Flight Experiment", NASA NTRS 20140016955, https://ntrs.nasa.gov/citations/20140016955 | NTRS record and abstract read |
| S5 | (not used) ISS heat sink flight results | "Phase Change Material Heat Sink Flight Experiment Results" (ICES 2017), https://ttu-ir.tdl.org/items/d7b9fe6f-937d-42ac-bf26-2b749ebe64e8 | re-checked: site returns 403 to every client; flight details ("SpaceX-9, 2016", "mixed results") seen only in search summaries, **so not on slides**. S16 replaces it |
| S6 | n-octadecane Tfus 301.0 K | NIST Chemistry WebBook, https://webbook.nist.gov/cgi/cbook.cgi?ID=C593453&Mask=4 | via calibration set (Teammate 1, spot-checked) |
| S7 | n-octadecane latent heat 236,155 J/kg | Mondieig et al., Chem. Mater. 16, 786 (2004), doi:10.1021/cm031169p | via calibration set |
| S8 | n-octadecane solid density 0.814 g/cm³ | Shlosinger & Bentilla 1965, as compiled in Hirschey et al. 2018 | via calibration set |
| S9 | solid n-docosane 0.49 W/m·K; solid-paraffin data sparse and contradictory | Paul et al., J. Therm. Anal. Calorim. 148, 5687 (2023), doi:10.1007/s10973-023-12107-2 | abstract read |
| S10 | Ga Tfus 302.92 K | NIST WebBook, https://webbook.nist.gov/cgi/cbook.cgi?ID=C7440553&Mask=4 | read (also for T6) |
| S11 | Ga latent heat 80,174 J/kg | NIST-JANAF, https://janaf.nist.gov/tables/Ga-001.txt | via calibration set |
| S12 | liquid Ga density 6.077 g/cm³ at Tm | Assael et al., J. Phys. Chem. Ref. Data 41, 033101 (2012), doi:10.1063/1.4729873 | via calibration set |
| S13 | liquid Ga conductivity 28.1 W/m·K at 302.93 K | Ho, Powell, Liley, J. Phys. Chem. Ref. Data 1, 279 (1972), doi:10.1063/1.3253100 | via calibration set (from OCR of a scan) |
| S14 | NVIDIA benchmark facts on slide 5 (45 pipelines, H200, claude-opus-4-8, zero pushback, LiF, 3-5x Langevin, synthetic-data self-test, 38/45 FIRE, seven GPU failures, advice) | NVIDIA Technical Blog, Aug 18 2026, https://developer.nvidia.com/blog/how-ai-coding-agents-can-unlock-materials-simulation-with-nvidia-alchemi-toolkit/ | raw page text grepped for each quote |
| S15 | MEAM potentials for In/Sn/Bi from NIST IPR; Ga's potential fails an alpha-Ga stability check | `data/nebius/README.md`, `data/nebius/metals/Ga/stability/RESULT.md` | entry pages read; stability check run on a laptop CPU |
| S16 | Paraffin PCM packs (40.69 g n-hexadecane) flown on IceCube in ISS orbit, 2017, instrument "Day-On Every Other Orbit"; "maintained the instrument temperatures at about 18 degrees Centigrade most of the time" | M.K. Choi, "Thermal Assessment of Paraffin Phase Change Material Mini-Packs on IceCube 3U CubeSat in Flight", NASA NTRS 20180004470, https://ntrs.nasa.gov/citations/20180004470 | NTRS record and abstract read |
| S17 | n-octadecane 0.7768 g/cm³ at 28 °C (used as the liquid) | CRC Handbook 95th ed. p. 3-424, via PubChem/HSDB (secondary), https://pubchem.ncbi.nlm.nih.gov/compound/11635 | PubChem record read. Caveat: 28 °C is at Tm, so the phase isn't stated; 5% below the 0.814 solid value [S8], consistent with liquid |
| S18 | Ga 5.9037 g/cm³ solid at 29.65 °C; 6.0947 liquid at 29.8 °C | The Merck Index (1996), via PubChem/HSDB (secondary), https://pubchem.ncbi.nlm.nih.gov/compound/5360835 | PubChem record read |
| S19 | Hook positive control: 3/3 melting crystals flagged `superheating_observed`, 0/4 two-phase boxes flagged; naive heating 1600 ± 100 K (quick settings) | `runs/melting/20261031T171151Z_Cu_heating_quick/result.json`, `runs/agent/e2e-cu-quick/.../20261031T170508Z_Cu_coexistence_quick/result.json`, `runs/hook_heating_cu_quick.log`; commit 2ebedd4 | counted from the result files; measured Cu Tm 1357.77 K from the calibration set |

Judge context (not for slides): Nikita Fedik is thanked in the NVIDIA blog's acknowledgments [S14].
