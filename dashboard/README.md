# Dashboard

> **Two pages.** `/` is the v2 landing page + dashboard (`dashboard/v2/`: calm light theme, self-hosted Source Serif 4
> and Inter under the OFL). `/classic/` is the previous dashboard, unchanged (all tabs, tables and replays). Both
> read the same export (`/data/data.js`). v2 never types a number in: every value comes from the export or the
> element is hidden (`tests/test_dashboard_v2.py` guards this).


One static page for the judges: a dark console with a top bar (data age, never called live when the export is
older than 10 minutes; a countdown to the 1 PM deadline, 18:00 UTC) and five hash-routed tabs (`#overview`,
`#agents`, `#results`, `#comparison`, `#failures`; arrow keys move between them):

- **Overview**: the question, the answer so far with counts by derived status, wax vs metals for one compute
  burst (charts from `scripts/burst_calc.py`), the framing lines with their sources.
- **Agents**: a layered diagram of how the agents wrap around NVIDIA ALCHEMI (steers, proposes, gates, simulates,
  tries to prove it wrong, keeps). Box labels come from `configs/llm_roles.json`; the ALCHEMI chips are the names
  our code really imports from `nvalchemi`; statuses (active / claimed / idle / no posts) and counts are derived from
  the exported boards. "Step through it" replays a real case from the science board (Field's metal latent heat),
  built at load time from the board's lineage. Click any box for its details. Below: ALCHEMI vs the agents.
- **Results**: results by trust level, PREDICTION labels, the classical-check gate and the trust rule.
- **Comparison**: arms, budget, a toggle between the pre-registered scoring and the disclosed post-hoc rule,
  per-repeat status (a repeat still running is listed with its partial numbers and kept out of the charts), the
  task-by-task grid and costs.
- **Failures**: failed runs, flagged results, classical potentials that failed calibration, refused runs.

Every number carries a numbered source (footnotes in the footer, same text on hover). No build step, no external
assets; charts and connectors are inline SVG. At phone width the diagram stacks and the connectors are hidden.

The page's fixed content (question, burst calculation, methods, agent graph, tolerances) comes from
`dashboard/context.py`, built from repo files at export time and shipped in `data.js` as `context`; every number
in it carries a source (tested), and a material with an unsourced input is listed as skipped, never computed.

## Use

```bash
# real boards (one or more)
.venv/bin/python -m dashboard.export --board boards/<id> [--board boards/eval/<run>/A-r1 ...]
open dashboard/index.html                # works straight from disk (file://)

# synthetic demo data for development (boards/dev-demo, gitignored)
.venv/bin/python -m dashboard.demo
```

The exporter writes `dashboard/data/` (gitignored): one `<board>.json` per board, `index.json`, and
`data.js` (the same data plus the page `context`, as one script, so the page works without a web server).
Evaluation boards named `<arm>-r<repeat>` (e.g. `A-r1`, `C-r2`, see `coolorbit/eval/arms.md`) are grouped into
the arm comparison. Each result carries the measured value it is shown against (`reference`): the board's
latest sourced `reference_value`, else the sourced record in `data/calibration/tier1.json` (`origin` says which).

Preview locally with the same security headers as Cloud Run:
`DATA_DIR=dashboard/data python dashboard/server.py`, then open http://localhost:8080. The page offers a reload
when a newer export appears (it never re-renders under the reader).

## Honesty guards
- Any board whose artifacts say `SYNTHETIC` shows a red **SYNTHETIC DEMO DATA** banner and is labeled in
  the board picker.
- If `configs/trust_rules.json` or the eval task set isn't frozen, the page says the statuses and scores are
  provisional / not pre-registered.
- The shortlist warns when a ranking cites a result that isn't trusted.
- Discovery-tier results are labelled PREDICTION; flagged and rejected results, failed and refused runs, and
  classical potentials that fail their calibration are listed under "What failed".
- A comparison repeat is "complete" only when every pre-registered task has its `run_record.json`; the charts use
  complete repeats only, running repeats show their partial numbers in the Repeats table, and a task board
  exported mid-run shows "running", not a score. A run whose task set or tolerances are not frozen says it is not
  pre-registered. The post-hoc scoring (`reference_rule=True` in `coolorbit/eval/metrics.py`) is always labelled
  as such, and the page opens on the pre-registered one.
- Costs that weren't recorded show as "not recorded", never as 0.
- Agent-written text is inserted as text, never as HTML.

## Hosting (later)
Google Cloud Run (or any static host) serves this folder after an export. The team GCP account is limited
(max 2 per team): ask Madhan before creating one.
