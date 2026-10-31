# Dashboard

A static page that shows a results board to the judges: results with their **derived** status and every
reason behind it, open needs, failed and refused runs, lineage, the latest ranking, and (for evaluation
boards) the T4 task scorecard and the arm comparison. No server, no build step.

## Use

```bash
# real boards (one or more)
.venv/bin/python -m dashboard.export --board boards/<id> [--board boards/eval/<run>/A-r1 ...]
open dashboard/index.html                # works straight from disk (file://)

# synthetic demo data for development (boards/dev-demo, gitignored)
.venv/bin/python -m dashboard.demo
```

The exporter writes `dashboard/data/` (gitignored): one `<board>.json` per board, `index.json`, and
`data.js` (the same data as one script, so the page works without a web server). Evaluation boards named
`<arm>-r<repeat>` (e.g. `A-r1`, `C-r2`, see `coolorbit/eval/arms.md`) are grouped into the arm comparison.

## Honesty guards
- Any board whose artifacts say `SYNTHETIC` shows a red **SYNTHETIC DEMO DATA** banner and is labeled in
  the board picker.
- If `configs/trust_rules.json` or the eval task set isn't frozen, the page says the statuses and scores are
  provisional / not pre-registered.
- The shortlist warns when a ranking cites a result that isn't trusted.
- Costs that weren't recorded show as "not recorded", never as 0.
- Agent-written text is inserted as text, never as HTML.

## Hosting (later)
Google Cloud Run (or any static host) serves this folder after an export. The team GCP account is limited
(max 2 per team): ask Madhan before creating one.
