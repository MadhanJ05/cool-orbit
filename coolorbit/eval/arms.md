# Comparison arms: what each arm is, and how it must record its work

Spec: `CLAUDE.md` section 13. Task set: `coolorbit/eval/tasks.json`. Metrics: `coolorbit/eval/metrics.py`
(next PR). The runs happen later on the GB10 / Brev; this file fixes **how** every arm records its work so
the metrics compare like with like.

> Status: **draft, not pre-registered.** Budgets and tolerances below marked `TBD` are team decisions
> (Madhan / computational chemistry lead). Freeze this file, `tasks.json` and `configs/trust_rules.json` in
> one commit before the first comparison run.

## 1. The arms

| Arm | Who works | Who checks | Prompt |
|---|---|---|---|
| **A. Single agent** | One Claude Code session with the ALCHEMI agent skills and **web access** | Itself only | [`prompts/arm_a_single_agent.md`](prompts/arm_a_single_agent.md): the task plus NVIDIA's own recommended premise-check / validation guidance |
| **B. Centralized pipeline** | One worker agent | One checker agent, in a fixed order: worker → checker → (one rerun if the checker fails it) → done | [`prompts/arm_b_worker.md`](prompts/arm_b_worker.md), [`prompts/arm_b_checker.md`](prompts/arm_b_checker.md) |
| **C. Decentralized collective** | Our Scouts, Simulators, Referees, Ranker (Teammate 1 + GB10 stream) | Any referee, via needs and claims; status from the trust rule | Each agent's own prompt (`coolorbit/agents/`); the task arrives only as a board need |
| D. (optional) Homogeneous collective | As C, every agent on one model family | As C | As C |

**Equal resources for every arm** (TBD numbers, same for all): LLM token budget per task, GPU-hours per
task, wall-clock limit, the same tools (ALCHEMI skills, web access, Nebius classical MD if any arm gets it,
the calibration dataset). An arm that runs out of budget stops and records that (see section 3).

## 2. Boards: one per arm and repeat

```
boards/eval/<run_id>/<arm>-r<repeat>/      e.g. boards/eval/2026-11-01a/A-r1/
```

- Fresh board per (arm, repeat); nothing is shared across arms. `seed_board()` refuses an already-seeded board.
- Before the arm starts, the harness seeds the board: `eval_harness` posts one `request` per task with
  note `eval_task:<task_id>` and one need (`artifact_type: "candidate"`, query = the task prompt).
- These are evidence boards: commit them after the run (only `boards/dev-*` is gitignored).

## 3. Recording contract (all arms)

Everything an arm does for a task must **descend from that task's request** (lineage). That is the only way
the metrics attribute work to tasks.

| The arm wants to... | It posts | Parents |
|---|---|---|
| Start work on a task | `candidate` (the material), fulfilling the task need | the task request |
| Report a number | `property_result` with full `method` (engine, model, protocol, ensemble, timestep, atoms, length) and honest `flags` | the candidate |
| Refuse the task (ill-posed premise, out-of-domain model, ...) | `run_report` with `status: "refused"` and the reason | the candidate (or the task request) |
| Report a failed run | `run_report` with `status: "failed"` | the candidate |
| Check a result | `check` (`method_audit`, `premise_check`, `second_mlip`, `classical_md`, `literature_anchor`) | the result |
| Rerun after a problem | a new `property_result` | the **old result** (this marks it superseded) |

Rules:
- **The answer the arm "ships"** for a task is its latest `property_result` under that task that has not been
  superseded by a rerun. No result and a refusal → the arm refused. Neither → no answer.
- **Agent names** follow `<role>_<detail>`. Arm A posts everything as `single_agent`; arm B as `worker` and
  `checker`; arm C uses its normal names. The board ignores self-checks when deriving status, so arm A's own
  refusals and flags are counted by the metrics directly (refusals and result flags), not through status.
  This keeps arm A from being penalized just for being one agent.
- **Never edit or delete** anything on a board, including failures. Failures are evidence.
- **Cost:** each arm's runner appends one line per LLM call / GPU job to `<board>/costs.jsonl`:
  `{"agent": "...", "task_id": "...", "kind": "llm" | "gpu", "model": "...", "input_tokens": 0,
  "output_tokens": 0, "gpu_hours": 0.0, "usd": null}`. (A sidecar file, because the board has no cost
  artifact type; if Madhan adds one, the metrics will read that instead.)

## 4. What the metrics count (summary; exact definitions in `metrics.py`)

- **Error vs reference:** shipped value vs the task's sourced reference (range references: distance to the range).
- **Trap catch rate** (trap and ill-posed tasks): the arm avoided the trap, or fell in and then caught it
  (refusal, own flag, a negative check, or a superseding correct rerun) before shipping. **Doing nothing is
  not a catch:** a task with neither an answer nor a refusal counts as `no_answer`, against the arm.
- **False-alarm rate** (well-posed controls): refused a well-posed task, or flagged/rejected a shipped answer
  that is within tolerance of the reference.
- **Silent errors shipped:** a shipped answer with no flags, not flagged or rejected by the trust rule, and
  wrong (any number for an ill-posed task, a trap signature in its method, or off by more than tolerance).
- **Cost:** tokens and GPU-hours from `costs.jsonl`.

## 5. Open items (not decided here)
- Budget numbers per task (tokens, GPU-hours, wall clock). **TBD, Madhan.**
- Tolerances marked `PLACEHOLDER` in `tasks.json`; PCM tasks reuse `configs/trust_rules.json`. **TBD, chemistry lead.**
- Repeats per arm (suggest ≥ 3) and seeds. **TBD.**
- Baseline strength: arms A and B get NVIDIA's own guidance, but **not** our trap catalog (`CLAUDE.md`
  section 12), while arm C's referees encode it. Giving A and B the catalog too makes a stronger, more
  convincing baseline (any win then comes from the collective, not from knowledge we withheld). **TBD, team.**
- Blind tasks from outside the team: see [`BLIND_TASKS_REQUEST.md`](BLIND_TASKS_REQUEST.md).
