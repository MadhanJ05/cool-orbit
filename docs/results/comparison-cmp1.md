# Comparison run cmp-1, repeat 1 (2026-10-31 21:45 UTC to 2026-11-01 02:30 UTC)

Arms (CLAUDE.md section 13): **A** single agent (Claude Code + ALCHEMI skills + NVIDIA's recommended prompt),
**B** worker + checker pipeline, **C** our decentralized collective. Same 8 pre-registered tasks (commit `2851934`),
same $1.50 LLM budget and 60-minute wall clock per task, same tools (web access, the ALCHEMI skills, the measured
reference values in `data/calibration/tier1.json`, mounted read-only in every sandbox). Claude Opus 5.5 as every
arm's worker. One repeat. The GPU was shared throughout with the science run and with the other two arms.

## Pre-registered scoring (`runs/cmp1_metrics.txt`, `.json`)

| | A: single agent | B: worker + checker | C: our collective |
|---|---|---|---|
| Trap catch | 60% (3 of 5; 2 unanswered) | 80% (4 of 5; 1 unanswered) | 60% (3 of 5; 2 unanswered) |
| False alarms (well-posed controls) | 1 of 3 | 1 of 3 | **0 of 3** |
| Silent errors (confident and wrong) | 0 | 0 | 0 |
| Mean \|relative error\| | 1% (n=2) | 0% (n=3) | 1% (n=2) |

## Post-hoc scoring (`runs/cmp1_metrics_posthoc.txt`, `.json`): NOT pre-registered

One rule added after seeing the results (`--reference-rule` in `coolorbit/eval/metrics.py`): a shipped answer that ran
no simulation and reports a measured value is scored "reported reference" (no error, counts against the catch rate).

| | A | B | C |
|---|---|---|---|
| Trap catch | 60% | **60%** (3 of 5; 1 unanswered, 1 reported reference) | 60% |
| False alarms | 1 of 3 | 1 of 3 | **0 of 3** |
| Silent errors | 0 | 0 | 0 |
| Mean \|relative error\| | 1% (n=2) | 0% (n=2) | 1% (n=2) |

**Why the rule:** on aluminium (`boards/eval/cmp-1/B-r1/tier1_al_melting/`), B's worker ran a proper two-phase
coexistence simulation: 862 ± 38 K (MACE-MP-0, 512 atoms), 7.7% below the measured 933.45 K and so within the
pre-registered ±10%. B's checker disagreed anyway. On the rerun the worker replaced its simulation with the measured
value from the calibration file and wrote "No new simulation was run for this rerun" (engine: "none ... measured
reference"). That is transparent, not fabricated, but it is not a simulation result, and the pre-registered scorer
counted it as a perfect one (error 0 K).

## What repeat 1 says

1. **No arm shipped a confident wrong answer.** All three refused the ill-posed tasks (LiF crystal diffusion, Cu
   crystal diffusion at 300 K, eicosane with MACE). With NVIDIA's recommended prompt and web access, a single agent
   already avoids these traps. This does not support our thesis that the collective catches more; we report it as is.
2. **The collective's measurable edge is false alarms: 0 of 3, against 1 of 3 for A and B.** A and B both doubted a
   correct O/Cu(111) adsorption energy.
3. **A fixed worker-checker pipeline "resolved" a disagreement by copying the measured value**, after its checker
   rejected a simulation that was within tolerance.
4. **Unanswered tasks are timeouts, and one is our own weakness.** The liquid-lithium Langevin trap (all arms) and
   aluminium (A and C) hit the 60-minute clock under GPU crowding. C's aluminium: our Simulator chose full production
   settings (~48 min estimated on an idle GPU), ran the whole hour and was cut off; C spent $0 of LLM on it.
5. Small sample: 8 tasks, one repeat. No statistical claims.

## Changes made after repeat 1 (disclosed; repeat 2 uses them)

- **Scorer:** the post-hoc rule above, opt-in. Without `--reference-rule` the scorer is the pre-registered one, so
  both scorecards are always reproducible.
- **Arm C's Simulator** (`scripts/run_arm_c.py`, `FIT_TO_BUDGET_NOTE`): before each GPU job it compares the job's
  idle-GPU estimate with half of the task's remaining wall clock and drops to the quick preset if it would not fit.
  Each task's `run_record.json` lists it under `post_hoc_changes`; `--fixed-preset` restores repeat-1 behaviour.
  Quick-preset results carry the `quick_settings_not_production` flag, so on a well-posed control a correct but
  flagged answer counts as a false alarm for C.
