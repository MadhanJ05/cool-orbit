# Diagnosis: backwards and contradictory coexistence verdicts (Bi, In, Sn), 2026-11-01

Read-only diagnosis. No command was run and no code was changed. Sources are the code (`coolorbit/sim/melting.py`,
`coolorbit/sim/simulator_agent.py`, `coolorbit/hooks/superheating.py`, `coolorbit/sim/alloys.py`), the board
`boards/science-1/artifacts/simulator_gb10/store.jsonl`, `runs/science-1.log`, and each run's `result.json`,
`settings.json`, `timeseries.csv` (one row per system every 50 steps: stage, step, PE/atom, T, V/atom, Lz) and
`agent_job.log`. All numbers below are read from those files. Where I derive a number, I say so. I did not analyse the
`final_*.extxyz` structures (that needs code), so **no phase assignment here comes from a structural order parameter**:
they come from potential energy (PE), volume, box shape and the hook's displacement counts.

Run directories (all under `runs/`):

| Label | Directory | Atoms | Temps (K) | T_prep / melt rounds (K) |
|---|---|---|---|---|
| Bi a1 | `science/science-1/577df9a1-n0-a1/20261101T021736Z_Bi_coexistence` | 768 | 463 518 572 627 | 545 / 872, 1090 |
| In a1 | `science/science-1/f3b5d7b8-n0-a1/20261031T211253Z_In_coexistence` | 576 | 366 408 452 494 | 430 / 688, 860, 1075 |
| In a2 | `science/science-1/33a4ea1a-n0-a2/20261101T013024Z_In_coexistence` | 576 | 238 281 323 366 | 302 / 483, 604, 755 |
| Sn a1 | `science/science-1/0e254158-n0-a1/20261101T000322Z_Sn_coexistence` | 512 | 429 480 530 581 | 505 / 808, 1010 |
| Cu a1 | `agent/cu-validation/4d28857e-n0-a1/20261031T174139Z_Cu_coexistence` | 512 | 1275 1300 1325 1350 | 1312.5 / 1 round |
| Cu a2 | `agent/cu-validation/27d6b68a-n0-a2/20261031T195736Z_Cu_coexistence` | 512 | 1350 1375 1400 1425 | 1387.5 / 2220 |
| Cu quick | `agent/e2e-cu-quick/734306e1-n0-a1/20261031T170508Z_Cu_coexistence_quick` | 512 | 1300 1350 1400 1450 | quick preset |

I could **not** find the Cu runs described as working (quick, 512 and 1024 atoms, 1296-1350 K). The
`boards/cu-validation` board holds only the two failed production runs (Cu a1, Cu a2; no `property_result`), and
`boards/e2e-cu-quick` holds one failed quick run. Without `ls` I could not search `runs/melting/`. The Cu contrast below
therefore uses Cu a1, a2 and quick. It turns out to be informative: production Cu fails the same way.

## How the verdict is computed (melting.py `run_coexistence`)

- Production is 25,000 steps (50 ps), one row every 50 steps, so 500 rows per system. `production_series(...,
  discard=0.05)` drops the first 25 rows (steps 0-1200). `block_means(pe, 10)` splits the rest into 10 blocks of
  about 48 rows. **Block 0 covers steps about 1250-3600 (2.5-7.2 ps).**
- `delta = means[-1] - means[0]`, `noise = std(diff(means))/sqrt(2)`, `threshold = max(0.002, 3*noise)`. The result
  is "melting", "freezing" or "stable".
- Consequence: **any phase change that finishes inside the first ~3600 steps (7 ps) reads as "stable"**, whether it
  went fully to liquid or fully to crystal. Nothing compares the end of the run with its start.

## Hypothesis 1: the whole box melts early, so the hottest temperatures read "stable"

**Supported, with a correction. The crystal half does not melt during preparation. It melts (or, for Cu, the liquid
freezes) in the first few ps of production, inside the discarded rows plus block 0. Sn is the exception: its crystal
was already gone before the split (see H4).**

- During preparation the crystal half is frozen: `frozen_half_moved_fraction` = 0.0 in all four runs.
- **Bi a1, 627 K:** PE starts at -3.717 eV/atom (the box prepared at 545 K). It averages about -3.700 at steps 250-750,
  reaches -3.676 to -3.680 by steps 3000-3500, and ends at about -3.683. Most of the rise happened before or during
  block 0, so delta = +0.0032 against a threshold of 0.0046, giving "stable". The hook's `max_mobile_fraction` is
  0.999, meaning almost every atom moved more than 0.5 nearest-neighbour distances.
- **Bi a1, 463/518/572 K:** these melted more slowly, so the rise landed between block 0 and block 9 and was reported
  as "melting". By the end, 518/572/627 K sit on one PE line (-3.697, -3.688, -3.683; slope about 1.3e-4 eV/K), which
  I read as fully liquid. At 463 K the PE ends at -3.723: it started two-phase at -3.733, and the liquid line
  extrapolates to about -3.704 (derived), so roughly a third of the crystal half melted. Melting speed rises steadily
  with T, which is what real melting does.
- **In a1, 452 and 494 K:** "stable" (+0.0017, +0.0011) while `max_mobile_fraction` = 0.998 at both. The 494 K PE goes
  from about -2.629 (steps 250-550) to -2.619 at the end, and the box ends with Lz 55 Å (it started at 41.9 Å). A fully
  liquid box has no resistance to changing shape under the anisotropic barostat, so its Lz can drift freely. The final
  PEs at 366/408/452/494 K (-2.6465, -2.6375, -2.628, -2.619) lie on one liquid-like line, so all four boxes ended
  liquid.
- **Cu a2 (the clearest case, including the freezing mirror image):** PE at step 0 is -3.843 for all four systems. By
  step 1250, the first row the analysis keeps, it is -3.888 (1350 K), -3.877 (1375 K), -3.799 (1400 K) and -3.776
  (1425 K). `max_mobile_fraction` is 0.172, 0.172, 1.0, 1.0. So the box froze completely at 1350 and 1375 K and melted
  completely at 1400 and 1425 K, all within 2.5 ps. The verdicts were stable, stable, stable, stable, and the board
  recorded "above_bracket". Cu a1 (1275-1350 K) has `max_mobile_fraction` 0.20-0.29 at every temperature: it froze
  everywhere, which the analysis reported as F, F, S, F.

## Hypothesis 2: a lower bracket lowers the melt temperature, the "liquid" recrystallises, and everything reads "freezing"

**Supported for In a2 (a validity failure the run already flagged). Whether its "freezing" is entirely an artefact:
can't tell.**

- Mechanism (code): `t_prep = mean(temps)`, `t_melt = 1.6*t_prep`, then rounds at x1.25, capped at `3*t_prep`, up to 3
  rounds. Shifting the bracket down therefore lowers every melt temperature.
- In a2: T_prep 302 K, so melt rounds at 483, 604 and 755 K. The melted fraction (atoms moved more than 1.5 Å) went
  7%, 25%, then 56% after the quench, which raised `liquid_half_incompletely_melted`. In a1 at 688/860/1075 K reached
  55%, 76%, then 91%. For contrast: Bi needed 1090 K (91%), Sn 1010 K (90%), Cu 2220 K (90%, one round).
- The production run went ahead anyway: the flag is only a warning. The final PEs (-2.692, -2.686, -2.679, -2.671 at
  238-366 K) lie on or below the crystal line (the 302 K solid is -2.680), and `max_mobile_fraction` is 0.23-0.47. So
  all four boxes ended crystalline.
- Caveat: the free half's energy excess after the quench (box -2.665 vs solid -2.680, so about 0.030 eV/atom on the
  free half; derived) is similar to In a1's (about 0.032; derived). The In a2 free half was therefore substantially
  disordered, not just a slightly damaged crystal. Its crystallisation at 238-323 K may be real (T < Tm). Its
  "freezing" at 366 K contradicts In a1, where the box melted completely at 366 K (see the bounds below).

## Hypothesis 3: what `unexpected_superheating_signal` means

**It comes from `melting.py`, which appends it when the hook reports `superheating_observed`. In these runs it is a
false positive of the hook, not superheating. It effectively means "the hook mistook the two-phase box for a perfect
crystal, and later more than half the atoms became mobile".**

- The hook takes a reference at its first call. 100 steps later (0.2 ps) it labels a system "crystalline" if 5% or
  fewer atoms have moved more than 0.3 nearest-neighbour distances. A crystalline-labelled system in which 50% of atoms
  later move more than 0.5 nn gets `superheating_observed`.
- In 0.2 ps, a heavy liquid at 360-630 K barely moves:

  | Run | early_mobile_fraction | start_state |
  |---|---|---|
  | Bi a1 | 0.0026 to 0.013 | 4 of 4 "crystalline" |
  | In a1 | 0.010 to 0.031 | 4 of 4 |
  | In a2 | 0 to 0.007 | 4 of 4 |
  | Sn | 0.0078 to 0.045 | 3 of 4 (the 581 K system had 0.0566, just over 0.05) |
  | Cu a1, a2, quick | 0.27 to 0.35 | 0 of 12 |

- Across the three runs that melted (Bi a1, In a1, Sn), 11 of 12 two-phase systems got `superheating_observed`, and 15
  of 16 across all four runs got `superheating_risk`. `coolorbit/hooks/README.md` says a two-phase box "is recognised
  and never flagged" (validated on Cu only). **That public claim is false for Bi/In/Sn and should be corrected before
  other teams rely on it.**
- The `melt_step` values follow temperature (Bi: 6250, 2850, 2100, 1450 steps for 463 to 627 K; In a1: 5900, 2650,
  1900, 1400). That fits a crystal half melting faster when hotter, not superheating.

## Hypothesis 4: is MACE-MPA-0's crystal stable?

**Sn: supported. The crystal did not survive `prep_solid_npt` at 505 K. In: refuted below 366 K (the crystal grows and
stays). Bi: can't tell.**

- **Sn:** the static measured lattice gives PE -3.9556. By step 50 the barostat has swung the volume from 27.06 to
  30.13 Å³/atom (+11% in 100 fs). By step 300, T is 484 K and PE is -3.897. PE then keeps rising at constant T to
  -3.844 by step 1350, with V going from 29.43 to 30.42, and stays there until step 3950. A rise of about 0.05 eV/atom
  at constant T with +3% volume looks like melting or amortisation. The end-of-prep "solid" (PE about -3.844, V about
  30.5) sits on the same PE/V line as the production boxes at 480/530 K, and those have `max_mobile_fraction` 1.0.
  Production PE is flat from 0.4 ps on at every temperature (for example -3.855 to -3.862 at 429 K). **The Sn box was
  liquid at the start of production at all four temperatures. "Undecided" was an artefact**, and the box never
  contained a crystal. The run cannot tell melting below 505 K apart from mechanical failure of a strained crystal: the
  isotropic barostat keeps the measured c/a and started about 11% off in volume.
- **In:** the 302 K solid is harmonic (PE -2.680 against an estimate of -2.7185 + 1.5 kB*302 = -2.679). The In a2
  boxes crystallised completely at 238-366 K and stayed crystalline. While crystallising, the 366 K box changed shape
  (Lz 41.54 to about 39.3 Å, in-plane area +4.4%). Assuming a single crystal with c along z, that implies MACE prefers
  c/a of about 1.44 against the measured 1.52 (derived, unverified). The isotropic prep therefore builds a crystal with
  the wrong axis ratio.
- **Bi:** the 545 K solid plateaus by step 550 (PE about -3.750 to -3.755, V 36.4-36.9) with no later step. But it sits
  at least 0.026 eV/atom above the harmonic estimate from its static energy (-3.8489 + 1.5 kB*545 = -3.778). That
  could be strain (measured c/a, isotropic barostat, +7.9% volume swing at step 50), anharmonicity or partial
  disorder. At 463 K the box stayed roughly half crystalline for at least 7 ps (PE flat at -3.733), so a crystal was
  present. Its Lz dropped from 98.1 to about 89.8 Å by step 3000 while the volume fell only 2%: the in-plane area grew
  about 7% while still partly crystalline, which I cannot explain without structures. The anisotropic barostat applies
  a diagonal strain rate (3 degrees of freedom, `nvalchemi/dynamics/integrators/npt.py`) to Bi's hexagonal cell. That
  is valid but untested here; a "triclinic" mode also exists.

## Hypothesis 5: other analysis and retry logic

1. **The discard plus block-0 reference** (H1) is the main cause of the backwards verdicts.
2. **The noise estimate includes the signal.** `std(diff(block means))` grows when a transition happens mid-run, which
   raises the threshold (Cu a1 at 1350 K: noise 0.0055, threshold 0.0165).
3. **`bracket_verdict` has no monotonicity check.** In a1's melt, melt, stable, stable became "below_bracket"; nothing
   flags a hot "stable" sitting above a colder "melting".
4. **`retry_need` has no memory.** In a1 (below) led to a2 = [238, 281, 323, 366]. In a2 (above, anchor = max(freezing)
   = 366) led to a3 = [366, 408.7, 451.3, 494], which is a1 again. a3 also has the same T_prep (430), T_melt (688) and
   seed (2026), so it is close to a replicate and not an independent check. It is running now (`4384c0ea-n0-a3`; its
   log shows only `prep_solid_npt` done). Bi's retry need (attempt 2, [299, 353.7, 408.3, 463]) gets T_prep 381 and
   melt rounds at 610/762/953 K, all below the 1090 K that Bi a1 needed. **Prediction, not observed:** the liquid will
   not melt fully, the run will read "freezing" and "above_bracket", and attempt 3 will be [463, 517.7, 572.3, 627],
   which is Bi a1 again.
5. **Artefact flags don't stop retries.** In a2's retry was posted despite `liquid_half_incompletely_melted`.
6. **Sn's "undecided" got no retry need on the board** (`ba6dfd8f`, `needs: []`), although the current `retry_need`
   handles "undecided". The bracket-verdict text also changed between In a1 (21:12) and Bi a1 (02:17). My guess is
   that the long-running agent process kept older code while each `melting.py` subprocess loaded the new code; I can't
   verify this without git history. `settings.json` does not record the git commit.
7. **Box geometry.** Sn's 4x4x8 tI4 cell is 23.3 x 23.3 x 25.5 Å, so each half is about 13 Å thick. The "z >= 2x"
   warning checks repeat counts, not lengths, so it stayed silent.
8. **Recorded temperatures during frozen stages are about half the real value.** `Recorder` divides kinetic energy by
   all atoms, frozen ones included. For example Sn's 808 K melt is logged at about 390 K. Anyone auditing
   `timeseries.csv` can be misled.
9. **The 5% liquid expansion has the wrong sign for Bi.** Liquid Bi is denser than solid Bi, so the expansion leaves
   the molten half under tension.

## What the data say about MACE-MPA-0 melting points (honest bounds)

- **Bi:** no temperature froze. The crystal half melted at 463-627 K, faster when hotter (fully by the end at 518 K,
  about a third at 463 K). *If* the prepared crystal half is representative of MACE's relaxed A7 Bi, then Tm(MACE) is
  below 463 K, at least 15% under the measured 544.6 K. The H4 confounders mean this is an upper bound for this
  preparation, not a model property. No lower bound.
- **In:** contradictory. a1 says it melts at 366 K and above (and at 366 K the final PE is on the liquid line). a2 says
  it freezes at 366 K and below. A tentative range is 323 K < Tm < 408 K, but each end comes from a run with a validity
  problem. **Can't tell.**
- **Sn:** **no information.** There was never a crystal in the coexistence box. A liquid supercooled to 429 K that
  doesn't crystallise in 50 ps says nothing about Tm.
- **Cu, for contrast:** a2's traces imply freezing at 1350 and 1375 K and melting at 1400 and 1425 K, so 1375-1400 K
  (derived; measured 1357.77 K). a1 agrees (froze at 1350 K and below). The quick run disagrees (melted at 1350 K). The
  transitions finished in 2.5 ps in 14 Å slabs, which is fast for interface-limited growth, so treat 1375-1400 K as
  indicative only. Both production Cu runs posted "above_bracket" because of H1.

## Minimal fix (described, not applied)

1. **`melting.py` `run_coexistence`, per-temperature verdict.**
   - Measure from the start of production, not from block 0: `ref` = mean PE over 0.5-1.5 ps (rows 5-15 at 50 steps
     x 2 fs), `delta = block_means(pe_all)[-1] - ref`.
   - Add a structural cross-check from the hook's own `max_mobile_fraction` (it does not depend on the hook's broken
     start-state label): 0.9 or more means "melting (complete)"; under 0.35 means "freezing (complete)"; otherwise use
     `delta`.
   - Save `ref`, the block means and the mobility in `per_temperature`.
   - Estimate noise from the detrended last half of the run.
   - Stop turning the hook's `superheating_observed` into `unexpected_superheating_signal` in coexistence mode.
   - Add a flag `no_two_phase_box` when every system has `max_mobile_fraction` of 0.9 or more and a flat PE (the Sn
     signature).
2. **`bracket_verdict`:** return "inconsistent" when the verdicts, ordered by T, are not freezing* then stable* then
   melting*.
3. **Gates in `prepare_coexistence` / `run_coexistence`. Fail fast; don't spend production GPU time on a bad box.**
   - (a) Register `SuperheatingDetectorHook(action="record")` on `prep_solid_npt`. That is a perfect-crystal start, the
     case the hook was built for. If it reports `superheating_observed`, fail with `crystal_lost_in_prep` (Sn would
     stop after the solid stage, saving about 55 of its 87 minutes).
   - (b) If `melted_half_moved_fraction` < `MELT_TARGET_FRACTION` after the rounds, fail with `liquid_not_prepared`
     before production (In a2 spent 34 minutes of production on a bad box).
4. **Decouple the melt temperature from the bracket.**
   - Base `t_melt` on the material: for example `max(1.6*t_prep, 2.0*t_ref)`, and cap at `MAX_MELT_FACTOR*t_ref`, where
     `t_ref` is the candidate's `t_guess_K` (new `--t-ref` argument passed by `Job.cli_args`).
   - Or have the Simulator pass the attempt-1 `--t-melt` and keep it in the retry need. Observed needs: Bi 1090 K, Sn
     1010 K, In 1075 K, Cu 2220 K.
5. **`simulator_agent.retry_need`.**
   - Return `None` when `failure` is `liquid_not_prepared`, `crystal_lost_in_prep` or `inconsistent`, or when flags
     include `liquid_half_incompletely_melted` or `no_two_phase_box`.
   - Keep a `history` list in the need. If the new direction reverses an earlier one (below then above), stop with
     "contradictory brackets" instead of re-proposing an old bracket.
   - Pass `--seed 2026+attempt` so that repeats are independent.
6. **Prep, needed for Sn and Bi to mean anything.** Relax the solid's shape anisotropically before the split (reuse
   `relax_solid_shapes`, already used by latent mode), ideally after a 0 K variable-cell relaxation (`FIRE2VariableCell`
   is exported by nvalchemi 0.2.0), so the barostat does not start 8-11% off in volume.
7. **Smaller items:**
   - Choose repeats by length so that Lz >= 2*max(Lx, Ly).
   - Divide kinetic energy by mobile atoms in `Recorder`.
   - Record the git commit in `settings.json`.
   - Allow negative or zero `liquid_expansion` for Bi.
   - Correct `coolorbit/hooks/README.md` (H3).

### Cheap validation

- **CPU, no GPU, minutes.** Apply the new verdict, as a pure function, to the saved `timeseries.csv` plus
  `superheating_detector` data of the seven runs above. Add regression tests from small extracted arrays. Expected:
  - Cu a2: F, F, M, M, giving "ok", 1375-1400 K.
  - Cu a1: all F, "above_bracket".
  - Bi a1: all M, "below_bracket".
  - In a1: all M.
  - In a2: `liquid_not_prepared`.
  - Sn: `no_two_phase_box`.
- **GPU, about 20 minutes in total.** Stop the science-run Simulator first: it holds the GPU with In a3, about 86
  minutes, and has Bi a2 queued, about 80 minutes, both repeats of known-bad setups. That is the team's call. Then:

  | Run | Atom-steps | Estimate |
  |---|---|---|
  | (i) Cu quick, 512 atoms, 1350/1375/1400/1425 K | about 6.4M | about 5-7 min (the earlier quick run took 393 s) |
  | (ii) Sn gate only, `--equil-steps 2000` (the crystal was lost between steps 300 and 1350; the quick 500 steps would miss it) | about 1M | 1-2 min; the gate should fire |
  | (iii) Bi quick, 768 atoms, 463-627 K, `--t-melt 1090` | about 9.6M | about 8-10 min |

  These estimates use `GB10_ATOM_STEPS_PER_S = 20,000`. The real Bi a1 run took 83 minutes against 72 estimated.
- **What the quick runs can and can't show.** They check the code paths and gates. A melting point still needs
  production length: Bi's 463-572 K melting took tens of ps.

## Not verified

- No structural order parameter on any frame. Every phase call comes from PE, volume, Lz and displacement counts.
- Bi at 463 K: displacement says 94% of atoms became mobile; PE says about a third of the crystal half melted.
  Unresolved.
- The "working" Cu quick runs (512/1024 atoms, 1296-1350 K) were not found.
- Why Sn got no retry need (agent process code version).
- Whether the anisotropic barostat treats the hexagonal Bi cell well.
- The MACE In c/a of about 1.44 (derived from box shape).
- The outcome of In a3 (running) and the Bi a2 prediction.

## Fix applied (2026-11-01): coexistence protocol 2, opt-in

The minimal fix above is implemented as **protocol 2**, used only when asked for. The default (protocol 1) is
unchanged, so runs in flight (cmp-1 repeat 2) behave as before. Code: `coolorbit/sim/melting.py` (verdict, gates,
melt temperature, solid preparation, `reanalyze`), `coolorbit/sim/simulator_agent.py` (opt-in, CLI, retries).
Tests: `tests/test_melting_protocol_v2.py`, `tests/test_simulator_protocol_v2.py`, with real-run fixtures in
`tests/data/coexistence_runs_2026-10-31.json` (production PE rounded to 1e-6 eV, hook `max_mobile_fraction`, saved
prep block and protocol-1 verdicts of the seven runs above). **None of the GPU code paths of protocol 2 has run yet.**

**How to enable.** `python -m coolorbit.sim.melting coexistence ... --protocol 2 [--t-ref <K>] [--t-melt <K>]
[--vc-relax-steps N] [--prep-coupling anisotropic|isotropic]`. In the Simulator: a melting-point need with
`"protocol_version": 2`, or the agent process started with `COOLORBIT_COEX_PROTOCOL=2` (coexistence jobs only).
`settings.json` then records `protocol_version` and `git_commit` (read from `.git`, no subprocess; it does not say
whether the tree had local edits), and a posted result's `method` gets `protocol_version: 2` (and `git_commit`);
`method.protocol` stays "two-phase coexistence".

What each item became:

1. **Verdict.** `delta` = mean PE of the last tenth of production minus the mean over 0.5-1.5 ps. Noise from the
   linearly detrended last half (blocks as long as the end window; the reference window's error scaled from it),
   threshold = max(0.002, 3 x noise). Cross-check with the production hook's `max_mobile_fraction` (its start-state
   label is not used): >= 0.9 gives "melting" unless the PE fell significantly ("conflict"); < 0.35 gives "freezing"
   if the PE did not rise, "conflict" if it rose significantly, and otherwise the PE verdict. **Deviation:** the
   PE-sign condition on the low-mobility call is mine, not the diagnosis's. A slow, heavy liquid in a short run also
   moves little (H3), so low mobility alone is not taken as freezing. It changes none of the expected outcomes below.
   No `unexpected_superheating_signal` in protocol 2. `no_two_phase_box`: every system >= 0.9 and **no system with a
   significant PE rise**, which is how I read "flat PE" (Sn at 429 K has a significant PE *fall*, -0.0037 against a
   threshold of 0.0022, and is a "conflict").
2. **Bracket.** "inconsistent" unless the verdicts, ordered by T, read freezing* stable* melting* (a "conflict"
   counts as non-monotone). **Deviation:** the melting point is the bracket midpoint, not the interpolated zero of
   delta: complete transitions saturate delta, so interpolating it would bias the estimate.
3. **Gates.** The hook (`action="record"`) runs on the solid preparation. `crystal_lost_in_prep` fires when its
   `max_mobile_fraction` >= 0.5 or it reports `superheating_observed` (the mobility test also covers a crystal that
   falls apart within the 0.2 ps probe and so never gets the "crystalline" label). `liquid_not_prepared` fires when
   under 80% of the liquid half moved > 1.5 A after the melt rounds. Both stop before production, write
   `final_<stage>.extxyz` and report `status: failed` with the gate as `failure`.
4. **Melt temperature.** `--t-melt` if given, else max(1.6 x T_prep, 2 x T_ref) with T_ref = `--t-ref` (the
   Simulator passes the need's `t_ref_K` or `t_guess_K`). Without T_ref it stays at 1.6 x T_prep. The escalation cap
   is never below the requested melt temperature. Retries carry `t_melt_K` and `t_ref_K` unchanged.
5. **Retries** (`retry_decision_v2`). None for `crystal_lost_in_prep`, `liquid_not_prepared`, `no_two_phase_box`,
   `inconsistent`, or the flag `liquid_half_incompletely_melted`. The need carries `bracket_history`. Retries stop
   when the bounds contradict across attempts (froze at or above a T that melted), the direction reverses, or the
   shifted bracket repeats an earlier one. The run report's `details.retry` says why. Seed = 2026 + attempt
   (**note:** attempt 1 therefore uses 2027, not protocol 1's 2026).
6. **Solid preparation.** Optional 0 K `FIRE2VariableCell` relaxation with `AlignCellHook` (both exported by the
   installed nvalchemi 0.2.0; 300 steps, initial dt 0.05). It is rejected, keeping the input cell, if the volume
   changes by more than 15%, the energy rises, or anything is non-finite. Then the NPT at T_prep is anisotropic by
   default (`--prep-coupling isotropic --vc-relax-steps 0` reproduces protocol 1's preparation).
7. **Smaller items** done: git commit in `settings.json`; hook README corrected (`coolorbit/hooks/README.md`; the
   hook's code and docstring are unchanged). **Not done:** length-based repeats, the `Recorder` temperature with
   frozen atoms, negative `liquid_expansion` for Bi.

**Re-analysis (CPU, no dynamics):** `python -m coolorbit.sim.melting reanalyze <run_dir> [--protocol 2] [--out
new.json]`. Protocol 1 reproduces all 28 saved per-temperature verdicts. The bracket labels differ for Cu a2 and Cu
quick only, because those runs predate the current "undecided" rule. Protocol 2 on the saved runs:

| Run | Protocol-2 verdicts | Result | Expected (above) |
|---|---|---|---|
| Cu a2 | F F M M | ok, 1387.5 +/- 12.5 K (bracket 1375-1400 K) | F, F, M, M; ok 1375-1400 K |
| Cu a1 | F F F F | above_bracket | all F, above_bracket |
| Bi a1 | M M M M (463 K on mobility alone: dPE +0.0084 < threshold 0.0098) | below_bracket | all M, below_bracket |
| In a1 | M M M M | below_bracket | all M |
| In a2 | (F F F S, informational) | liquid_not_prepared (melted half 0.556, saved after the quench) | liquid_not_prepared |
| Sn a1 | conflict M M M | no_two_phase_box | no_two_phase_box |
| Cu quick (5 ps) | F M M M | ok, 1325 +/- 25 K (bracket 1300-1350 K) | not given |

Caveats. The Cu quick 1300 K "freezing" rests on mobility 0.3496 against the 0.35 cut, with an insignificant PE
fall (-0.0118, threshold 0.0144). Its bracket (1300-1350 K) excludes both the measured 1357.77 K and Cu a2's
1375-1400 K, so it is borderline and not production evidence. `crystal_lost_in_prep` cannot be re-checked on these
runs: protocol 1 saved no displacement data for the solid preparation. The liquid gate uses the only saved fraction,
measured after the quench. A protocol-2 run measures it after the melt rounds.

**GPU validation (not yet run; minutes at 20,000 atom-steps/s; Bi a1 ran about 15% slower than its estimate).**
These check the code paths and gates. Quick runs (5 ps) cannot give a production melting point, and in 5 ps a cool
Bi liquid may not move far, so some Bi temperatures may come out "stable" or "conflict".

| Run | Command (`.venv/bin/python -m coolorbit.sim.melting coexistence ... --out runs/melting/v2-validation`) | Estimate |
|---|---|---|
| Cu quick | `--element Cu --a 3.61 --reps 4 4 8 --temps 1350 1375 1400 1425 --quick --protocol 2 --t-ref 1357.77` (T_melt = 2715.5 K) | about 6-7 min |
| Sn gate, protocol-2 preparation | `--element Sn --structure ref --reps 4 4 8 --temps 429 480 530 581 --quick --equil-steps 2000 --protocol 2 --t-ref 505` | about 1 min if the gate fires, about 6 min if the crystal survives |
| Sn gate, protocol-1 preparation | the same plus `--prep-coupling isotropic --vc-relax-steps 0` (the gate should fire if the crystal is lost as in Sn a1) | about 1 min if it fires |
| Bi quick | `--element Bi --structure ref --reps 4 4 8 --temps 463 518 572 627 --quick --protocol 2 --t-ref 544.6 --t-melt 1090` | about 8-10 min |
