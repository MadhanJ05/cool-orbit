# Cool Orbit: pitch outline (draft, Sat Oct 31; slides due Sun Nov 1, 1:00 PM)

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

## Slide 2: Orbital AI compute is limited by power and heat
- Google's Project Suncatcher put its first prototype satellite in orbit on **Oct 1, 2026**: **four TPUs**, about
  **a kilowatt of continuous power**. [S1, S2]
- It can run **one TPU at a time, in 15-minute bursts**, "to avoid straining the satellite's power and thermal
  management systems". [S1]
- Google: "Cooling orbital data centers is a crucial research challenge." "In a vacuum, you can only diffuse
  heat via radiators." Their design: heat pipes + radiators. [S3]
- Say it right: the bursts are set by **power and thermal** limits together (TechCrunch), not "it shuts down to
  cool off".

## Slide 3: The idea: a thermal battery between the chip and the radiator
- A phase-change material (PCM) soaks up heat by melting during a compute burst and gives it back while it
  refreezes. The radiator can then be sized for the **average** load, not the peak.
- Not a new idea in space: NASA built a **paraffin-wax** PCM heat sink for an ISS flight experiment, to "reduce
  the overall mass and volume of future exploration spacecraft thermal control systems". [S4, S5]
- Physics, said correctly: a better material doesn't make chips produce less heat and doesn't remove the
  radiator (radiated power is set by radiator area and temperature). The lever is **transient buffering**.

## Slide 4: The trade-off nobody has solved: heat stored vs heat moved
All values measured and sourced (our Tier-1 calibration set, `data/calibration/tier1.json`):

| Material | Melts at | Latent heat | Per litre | Thermal conductivity |
|---|---|---|---|---|
| n-octadecane (paraffin) | 301 K [S6] | **236 kJ/kg** [S7] | 192 J/cm³ (solid density 0.814 g/cm³ [S8]) | solid paraffin **~0.5 W/m·K** (n-docosane 0.49) [S9] |
| Gallium (metal) | 302.92 K [S10] | 80 kJ/kg [S11] | **487 J/cm³** (liquid density 6.077 g/cm³ [S12]) | **28.1 W/m·K** (liquid, at Tm) [S13] |

- Arithmetic (shown, not typed): 236,155 J/kg × 0.814 g/cm³ = 192 J/cm³; 80,174 J/kg × 6.077 g/cm³ = 487 J/cm³;
  28.1 / 0.49 ≈ 57.
- Wax stores ~3x more heat **per kilogram** (launch mass); gallium stores ~2.5x more **per litre** and moves
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
- Scouts (one per material family) propose; Simulators run ALCHEMI (MACE) on the Dell Pro Max GB10; Referees
  (different methods and model families: Claude, GPT, Gemini) try to prove results wrong; a Ranker keeps the
  shortlist; a Librarian supplies sourced measured values.
- They coordinate only through a shared **results board** (ScienceClaw artifacts with lineage + open needs and
  claims). **No agent can set a result's status**: "trusted" is derived from evidence = method audit passed AND
  an independent method agrees AND (for known materials) it matches a sourced measurement. Rejection needs two
  independent referees. (`coolorbit/board/status.py`)
- Independent second opinion with a different physics: classical MD (MEAM potentials from the NIST
  Interatomic Potentials Repository, LAMMPS on Nebius) for the metals. [S15]
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
  (`coolorbit/hooks/superheating.py`). `[which teams used it: NEEDS CONFIRMATION]`
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
3. Collective: a referee flags superheating, the hook fires, a simulator reruns with two-phase coexistence, a
   second method agrees, the result lands near the measured value and becomes "trusted" on the dashboard.
4. Scorecard; 5. shortlist; 6. the hook in another team's run.
Backup: a screen recording, in case the GB10 or network fails on stage.

---

## Lines to use / not to use

| Don't say | Why | Say instead |
|---|---|---|
| "Google's AI satellite has to shut down every 15 minutes to cool off" (CLAUDE.md section 21) | The source says bursts avoid straining **power and thermal** systems; "shut down to cool off" is ours | "Google's AI satellite can run one chip at a time, in 15-minute bursts, to spare its power and thermal systems" [S1] |
| "Every thermal battery in space today is basically wax" | No source for "every" | "NASA's ISS PCM heat-sink experiment used paraffin wax" [S4] |
| "chips run in 15-minute sessions because of heat limits" (CLAUDE.md section 3) | Same as above | as above |
| "We discovered a new material" | We simulate and rank | "We found which known and under-measured materials work best, and checked the predictions" |
| "Wax conducts under 1 W/m·K" without a citation | Our calibration has no paraffin conductivity | cite n-docosane 0.49 W/m·K [S9] |

## Claims register (every source opened and read on 2026-10-31)

| ID | Claim used | Source | Checked how |
|---|---|---|---|
| S1 | Oct 1 2026 launch; four TPUs; one at a time; 15-minute bursts "to avoid straining the satellite's power and thermal management systems"; "a kilowatt of continuous power" | TechCrunch, Oct 1 2026, https://techcrunch.com/2026/10/01/google-thinks-spacexs-starship-has-to-launch-1600-times-before-space-data-centers-get-off-the-ground | page read; quotes verbatim. Note: AI Weekly reports "about 20 minutes", power-limited (https://aiweekly.co/alerts/googles-suncatcher-prototype-comes-online-runs-gemma-4-in-orbit); we use TechCrunch's original wording |
| S2 | Launch on SpaceX Transporter-18 with Planet; four TPUs | Google on X, https://x.com/Google/status/2105803583648100611 ; NPR, Oct 1 2026, https://www.npr.org/2026/10/01/nx-s1-5983697/project-suncatcher-google-ai-data-center-space | search snippets only (NPR page timed out): **re-check before the slide** |
| S3 | "Cooling orbital data centers is a crucial research challenge"; "In a vacuum, you can only diffuse heat via radiators"; heat pipes + radiators | Google, "Learn about Google's Project Suncatcher", https://blog.google/innovation-and-ai/models-and-research/google-research/google-project-suncatcher-facts/ | page read; quotes verbatim |
| S4 | NASA paraffin-wax PCM heat sink for an ISS flight experiment; goal "reduce the overall mass and volume of future exploration spacecraft thermal control systems" | Quinn, Stieber, Sheth, Ahlstrom, "Phase Change Material Heat Sink for an ISS Flight Experiment", NASA NTRS 20140016955, https://ntrs.nasa.gov/citations/20140016955 | NTRS record and abstract read |
| S5 | It flew and has flight results | "Phase Change Material Heat Sink Flight Experiment Results", TTU repository (ICES), https://ttu-ir.tdl.org/items/d7b9fe6f-937d-42ac-bf26-2b749ebe64e8 | title only (page returned 403): `[NEEDS SOURCE]` for any detail (e.g. SpaceX-9, 2016) |
| S6 | n-octadecane Tfus 301.0 K | NIST Chemistry WebBook, https://webbook.nist.gov/cgi/cbook.cgi?ID=C593453&Mask=4 | via calibration set (Teammate 1, spot-checked) |
| S7 | n-octadecane latent heat 236,155 J/kg | Mondieig et al., Chem. Mater. 16, 786 (2004), doi:10.1021/cm031169p | via calibration set |
| S8 | n-octadecane solid density 0.814 g/cm³ | Shlosinger & Bentilla 1965, as compiled in Hirschey et al. 2018 | via calibration set |
| S9 | solid n-docosane 0.49 W/m·K; solid-paraffin data sparse and contradictory | Paul et al., J. Therm. Anal. Calorim. 148, 5687 (2023), doi:10.1007/s10973-023-12107-2 | abstract read |
| S10 | Ga Tfus 302.92 K | NIST WebBook, https://webbook.nist.gov/cgi/cbook.cgi?ID=C7440553&Mask=4 | read (also for T6) |
| S11 | Ga latent heat 80,174 J/kg | NIST-JANAF, https://janaf.nist.gov/tables/Ga-001.txt | via calibration set |
| S12 | liquid Ga density 6.077 g/cm³ at Tm | Assael et al., J. Phys. Chem. Ref. Data 41, 033101 (2012), doi:10.1063/1.4729873 | via calibration set |
| S13 | liquid Ga conductivity 28.1 W/m·K at 302.93 K | Ho, Powell, Liley, J. Phys. Chem. Ref. Data 1, 279 (1972), doi:10.1063/1.3253100 | via calibration set (from OCR of a scan) |
| S14 | NVIDIA benchmark facts on slide 5 (45 pipelines, H200, claude-opus-4-8, zero pushback, LiF, 3-5x Langevin, synthetic-data self-test, 38/45 FIRE, seven GPU failures, advice) | NVIDIA Technical Blog, Aug 18 2026, https://developer.nvidia.com/blog/how-ai-coding-agents-can-unlock-materials-simulation-with-nvidia-alchemi-toolkit/ | raw page text grepped for each quote |
| S15 | MEAM potentials for Ga/In/Sn/Bi from NIST IPR | `data/nebius/README.md` (citations + NIST URLs) | entry pages read |

Judge context (not for slides): Nikita Fedik is thanked in the NVIDIA blog's acknowledgments [S14].
