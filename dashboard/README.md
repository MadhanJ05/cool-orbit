# Dashboard

One static page for the judges, readable in a minute, top to bottom: the question and the answer so far with
live counts; wax vs metals for one compute burst (charts from `scripts/burst_calc.py`); how it works on ALCHEMI
(pipeline, methods, the SuperheatingDetectorHook, the classical check, the trust rule); results by trust level
with measured values, error and the referees' reasons; the single agent vs pipeline vs collective scorecard with a
task-by-task grid; and what failed. Every number carries a numbered source (footnotes in the footer, same text on
hover). No build step, no external assets; charts are inline SVG.

The page's fixed content (question, burst calculation, methods, agents, tolerances) comes from
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
- A comparison run with missing arms or task boards is labelled "in progress", and one whose task set or
  tolerances are not frozen says it is not pre-registered.
- Costs that weren't recorded show as "not recorded", never as 0.
- Agent-written text is inserted as text, never as HTML.

## Hosting (later)
Google Cloud Run (or any static host) serves this folder after an export. The team GCP account is limited
(max 2 per team): ask Madhan before creating one.
