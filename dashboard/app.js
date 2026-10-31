// Cool Orbit dashboard. Reads window.COOL_ORBIT_DATA written by `python -m dashboard.export`.
// All text is inserted with textContent (never innerHTML): board payloads are agent-written.
"use strict";

const DATA = window.COOL_ORBIT_DATA;
const STATUS_ORDER = ["trusted", "under_review", "simulated", "flagged", "rejected"];
const STATUS_LABEL = { trusted: "trusted", under_review: "under review", simulated: "simulated",
                       flagged: "flagged", rejected: "rejected" };

// ------------------------------------------------------------------ DOM helpers
function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v; // CSSOM: allowed under the server's CSP (no inline style attrs)
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}
const short = (id) => (id || "").slice(0, 8);
const badge = (status) => el("span", { class: `badge s-${status}` }, STATUS_LABEL[status] || status);
function fmtNum(v) {
  if (v === null || v === undefined) return "–";
  const a = Math.abs(v);
  return a !== 0 && (a < 1e-3 || a >= 1e5) ? v.toExponential(3) : String(+v.toPrecision(5));
}
const fmtVal = (r) => `${fmtNum(r.value)}${r.uncertainty != null ? " ± " + fmtNum(r.uncertainty) : ""} ${r.units}`;
const fmtPct = (x) => (x === null || x === undefined ? "n/a" : `${Math.round(100 * x)}%`);
const fmtMethod = (m) => [m.engine && m.model ? `${m.engine}/${m.model}` : m.engine, m.protocol, m.ensemble]
  .filter(Boolean).join(" · ");
function card(title, count, ...body) {
  return el("section", { class: "card" },
    el("h2", {}, title, count !== null && count !== undefined ? el("span", { class: "count" }, ` (${count})`) : null),
    ...body);
}
const empty = (text) => el("p", { class: "empty" }, text);

// ------------------------------------------------------------------ state
let board = null;
let selectedResult = null;

function boardNames() { return DATA.index.boards.map((b) => b.name); }
function initialBoard() {
  const fromHash = decodeURIComponent((location.hash.match(/board=([^&]+)/) || [])[1] || "");
  const names = boardNames();
  if (names.includes(fromHash)) return fromHash;
  return names.find((n) => !DATA.boards[n].arm) || names[0];
}

// ------------------------------------------------------------------ sections
function banners() {
  const box = document.getElementById("banners");
  box.replaceChildren();
  if (board.synthetic) {
    box.append(el("div", { class: "banner synthetic", role: "alert" },
      "SYNTHETIC DEMO DATA: invented numbers for testing the dashboard. Not results; do not cite."));
  } else if (DATA.index.any_synthetic) {
    box.append(el("div", { class: "banner synthetic", role: "alert" },
      "This export also contains SYNTHETIC boards (marked in the board picker). This board is not one of them."));
  }
  if (!board.rules_frozen) {
    box.append(el("div", { class: "banner provisional" },
      "Trust tolerances are not frozen yet (placeholders pending the chemistry lead), so statuses are provisional."));
  }
}

function summary() {
  const byStatus = board.counts.by_status || {};
  return card("Overview", null,
    el("div", { class: "chips" },
      el("span", { class: "chip" }, `${board.counts.artifacts} artifacts`),
      el("span", { class: "chip" }, `${board.agents.length} agents`),
      ...STATUS_ORDER.filter((s) => byStatus[s]).map((s) =>
        el("span", { class: "chip" }, badge(s), ` ${byStatus[s]}`)),
      el("span", { class: "chip" }, `${board.counts.open_needs} open needs`),
      el("span", { class: "chip" }, `${board.run_reports.length} failed/refused runs`)),
    el("p", { class: "muted" }, "Agents: ", board.agents.join(", ")));
}

function shortlist() {
  const r = board.ranking;
  if (!r) return card("Shortlist", 0, empty("No ranking posted yet."));
  const rows = r.entries.map((e) => el("tr", {},
    el("td", { class: "num" }, e.rank),
    el("td", {}, e.candidate),
    el("td", { class: "num" }, fmtNum(e.score)),
    el("td", {}, e.result_ids.map((id) => el("span", { class: "mono node", onclick: () => focusResult(id) }, short(id) + " "))),
    el("td", {}, e.untrusted_result_ids.length
      ? el("span", { class: "warn" }, `⚠ relies on non-trusted result(s) ${e.untrusted_result_ids.map(short).join(", ")}`)
      : el("span", { class: "s-trusted" }, "all cited results trusted"),
      e.note ? el("div", { class: "muted" }, e.note) : null)));
  return card("Shortlist", r.entries.length,
    el("p", { class: "muted" }, `Latest of ${r.n_rankings} ranking(s), by ${r.agent}: ${r.criteria}`),
    el("table", {}, el("thead", {}, el("tr", {}, ["#", "Candidate", "Score", "Cites", "Check"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows)));
}

function results() {
  const detail = el("div", { id: "result-detail" });
  if (!board.results.length) return card("Results", 0, empty("No results yet."));
  const rows = board.results.map((r) => el("tr", {
      class: "clickable" + (selectedResult === r.id ? " selected" : ""), tabindex: "0",
      onclick: () => focusResult(r.id), onkeydown: (ev) => { if (ev.key === "Enter") focusResult(r.id); } },
    el("td", {}, badge(r.status), r.superseded_by.length ? el("div", { class: "muted" }, "superseded by rerun") : null),
    el("td", {}, r.candidate, el("div", { class: "muted" }, `${r.family} · ${r.tier} tier`)),
    el("td", {}, r.property),
    el("td", { class: "num" }, fmtVal(r)),
    el("td", {}, fmtMethod(r.method)),
    el("td", { class: "flags" }, r.flags.map((f) => el("span", {}, f))),
    el("td", {}, r.agent),
    el("td", {}, el("ul", { class: "reasons" }, r.reasons.slice(0, 3).map((x) => el("li", {}, x))),
      r.reasons.length > 3 ? el("span", { class: "muted" }, `+${r.reasons.length - 3} more (click)`) : null)));
  return card("Results", board.results.length,
    el("p", { class: "muted" }, "Click a result for every reason behind its status and its full lineage."),
    el("table", {}, el("thead", {}, el("tr", {},
      ["Status", "Candidate", "Property", "Value", "Method", "Flags", "By", "Why"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows)),
    detail);
}

function lineageTree(focusId) {
  const L = board.lineage;
  const label = (id) => {
    const a = L[id];
    if (!a) return el("span", { class: "muted" }, `${short(id)} (not on this board)`);
    return el("span", { class: "node" + (id === focusId ? " focus" : ""), onclick: (ev) => { ev.stopPropagation(); focusArtifact(id); } },
      el("span", { class: "type" }, a.type), el("span", { class: "mono" }, short(id)), ` ${a.agent}: ${a.summary || ""}`);
  };
  // Ancestors (all parents, recursively), then the focus node with its descendants.
  const up = (id, seen) => {
    const a = L[id];
    if (!a || !a.parents.length) return null;
    return el("ul", {}, a.parents.filter((p) => !seen.has(p)).map((p) => {
      seen.add(p);
      return el("li", {}, label(p), up(p, seen));
    }));
  };
  const down = (id, seen, depth) => {
    const a = L[id];
    if (!a || !a.children.length || depth > 12) return null;
    return el("ul", {}, a.children.filter((c) => !seen.has(c)).map((c) => {
      seen.add(c);
      return el("li", {}, label(c), down(c, seen, depth + 1));
    }));
  };
  return el("div", {},
    el("h3", {}, "Comes from"), up(focusId, new Set()) || empty("No parents."),
    el("h3", {}, "This artifact and what followed"),
    el("ul", { class: "tree" }, el("li", {}, label(focusId), down(focusId, new Set([focusId]), 0))));
}

function focusArtifact(id) {
  const box = document.getElementById("result-detail");
  if (!box) return;
  const a = board.lineage[id];
  const res = board.results.find((r) => r.id === id);
  selectedResult = res ? id : selectedResult;
  box.replaceChildren(el("div", { class: "detail" },
    el("h3", {}, `${a ? a.type : "artifact"} ${short(id)}`, res ? " " : null, res ? badge(res.status) : null),
    res ? el("ul", { class: "reasons" }, res.reasons.map((x) => el("li", {}, x))) : null,
    a && !res ? el("pre", { class: "mono" }, JSON.stringify(a.payload, null, 2)) : null,
    lineageTree(id)));
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function focusResult(id) { selectedResult = id; render(); focusArtifact(id); }

function needs() {
  if (!board.open_needs.length) return card("Open needs", 0, empty("No open needs."));
  return card("Open needs", board.open_needs.length,
    el("p", { class: "muted" }, "Requests posted on the board; any agent can claim one. There is no central planner."),
    el("table", {}, el("thead", {}, el("tr", {}, ["Need", "Kind", "Query", "Why", "Claimed"].map((h) => el("th", {}, h)))),
      el("tbody", {}, board.open_needs.map((n) => el("tr", {},
        el("td", { class: "mono" }, `${short(n.artifact_id)}#${n.index}`),
        el("td", {}, n.kind), el("td", {}, n.query), el("td", { class: "muted" }, n.rationale),
        el("td", {}, n.claimed_by ? `${n.claimed_by} until ${n.claim_expires_at}` : el("span", { class: "muted" }, "unclaimed")))))));
}

function runs() {
  if (!board.run_reports.length) return card("Failed and refused runs", 0, empty("None reported."));
  return card("Failed and refused runs", board.run_reports.length,
    el("p", { class: "muted" }, "Failures are evidence: they are posted, never dropped."),
    el("table", {}, el("thead", {}, el("tr", {}, ["Run", "Status", "Task", "Reason", "Attempt", "By"].map((h) => el("th", {}, h)))),
      el("tbody", {}, board.run_reports.map((r) => el("tr", {},
        el("td", { class: "mono" }, short(r.id)),
        el("td", {}, el("span", { class: r.status === "refused" ? "badge s-under_review" : "badge s-flagged" }, r.status)),
        el("td", {}, r.task), el("td", {}, r.reason), el("td", { class: "num" }, r.attempt), el("td", {}, r.agent))))));
}

function proposed() {
  if (!board.proposed.length) return null;
  return card("Proposed, no results yet", board.proposed.length,
    el("table", {}, el("tbody", {}, board.proposed.map((c) => el("tr", {},
      el("td", {}, c.name), el("td", {}, c.family), el("td", {}, `${c.tier} tier`), el("td", { class: "muted" }, `by ${c.agent}`))))));
}

function comparison() {
  const runs = DATA.index.comparison;
  const excluded = DATA.index.excluded_from_comparison || [];
  if (!runs) {
    return excluded.length
      ? card("Arm comparison (collective capability)", 0,
          empty(`No real comparison runs. Left out as synthetic: ${excluded.join(", ")}.`))
      : null;
  }
  const wanted = board.run ? runs.filter((r) => r.run === board.run) : runs;
  return el("div", {}, ...(wanted.length ? wanted : runs).map((r) => comparisonRun(r, excluded)));
}

function comparisonRun(run, excluded) {
  const cmp = run.arms;
  const arms = Object.keys(cmp);
  const ARM_NAME = { A: "A · single agent", B: "B · worker + checker", C: "C · our collective", D: "D · homogeneous collective" };
  const metric = (label, key, kind, better) => el("tr", {},
    el("td", { class: "metric" }, label, el("div", { class: "muted" }, better)),
    arms.map((a) => {
      const m = cmp[a][key];
      if (!m) return el("td", {}, "n/a");
      const val = kind === "pct" ? fmtPct(m.mean) : fmtNum(m.mean);
      const range = m.n > 1 ? ` (${kind === "pct" ? fmtPct(m.min) : fmtNum(m.min)}–${kind === "pct" ? fmtPct(m.max) : fmtNum(m.max)}, n=${m.n})` : ` (n=${m.n})`;
      return el("td", {}, el("strong", {}, val), el("span", { class: "muted" }, range),
        kind === "pct" ? el("div", { class: "bar-wrap" }, el("div", { class: "bar", style: `width:${Math.round(100 * m.mean)}%` })) : null);
    }));
  const cost = (label, key) => el("tr", {}, el("td", { class: "metric" }, label),
    arms.map((a) => el("td", {}, cmp[a].cost_recorded_on_all_boards
      ? fmtNum(cmp[a][key])
      : el("span", { class: "warn" }, "not recorded (on at least one repeat), so not comparable"))));
  return card(`Arm comparison (collective capability): run ${run.run}`, arms.length,
    run.synthetic ? el("div", { class: "banner synthetic", role: "alert" },
      "SYNTHETIC comparison: built from demo boards with invented numbers. Not a result; do not cite.") : null,
    excluded.length && !run.synthetic ? el("p", { class: "muted" }, `Synthetic boards left out: ${excluded.join(", ")}.`) : null,
    el("p", { class: "muted" }, "Same tasks, same budgets. Repeats from this run only, never pooled across runs. Mean over repeats (range, number of repeats). ",
      DATA.index.task_set_frozen && DATA.index.rules_frozen ? "Task set and tolerances are pre-registered."
        : el("span", { class: "warn" }, "Not pre-registered yet: the task set or tolerances are not frozen.")),
    el("table", { class: "compare" },
      el("thead", {}, el("tr", {}, el("th", {}, "Metric"), arms.map((a) => el("th", {}, ARM_NAME[a] || a)))),
      el("tbody", {},
        metric("Trap catch rate", "trap_catch_rate", "pct", "higher is better"),
        metric("False-alarm rate", "false_alarm_rate", "pct", "lower is better"),
        metric("Silent errors shipped (per run)", "silent_errors", "num", "lower is better"),
        metric("Mean |relative error|", "mean_abs_rel_error", "pct", "lower is better"),
        cost("Input tokens (total)", "total_input_tokens"),
        cost("Output tokens (total)", "total_output_tokens"),
        cost("GPU-hours (total)", "total_gpu_hours"))));
}

function scorecard() {
  const s = board.scorecard;
  if (!s) return null;
  const tag = (o) => o.trap_outcome
    ? el("span", { class: `badge ${{ avoided: "s-trusted", caught: "s-trusted", missed: "s-rejected", no_answer: "s-simulated" }[o.trap_outcome]}` }, o.trap_outcome.replace("_", " "))
    : o.false_alarm ? el("span", { class: "badge s-flagged" }, "false alarm") : el("span", { class: "muted" }, "control");
  return card(`Task scorecard for this run${board.arm ? ` (arm ${board.arm}, repeat ${board.repeat})` : ""}`, s.n_tasks,
    el("div", { class: "chips" },
      el("span", { class: "chip" }, `trap catch ${fmtPct(s.trap_catch_rate)}`),
      el("span", { class: "chip" }, `false alarms ${s.false_alarms}/${s.control_tasks}`),
      el("span", { class: "chip" }, `silent errors ${s.silent_errors}`),
      el("span", { class: "chip" }, `cost ${s.cost.recorded ? "recorded" : "not recorded"}`)),
    el("table", {}, el("thead", {}, el("tr", {}, ["Task", "Kind", "What the arm did", "Outcome", "Shipped", "Error", "Why"].map((h) => el("th", {}, h)))),
      el("tbody", {}, s.outcomes.filter((o) => o.seeded).map((o) => el("tr", {},
        el("td", { class: "mono" }, o.task_id), el("td", {}, o.kind), el("td", {}, o.disposition.replaceAll("_", " ")),
        el("td", {}, tag(o), o.silent_error ? el("div", { class: "warn" }, "silent error") : null),
        el("td", { class: "num" }, o.shipped_value != null ? `${fmtNum(o.shipped_value)} ${o.shipped_units}` : "–"),
        el("td", { class: "num" }, o.error != null ? fmtNum(o.error) : "–"),
        el("td", { class: "muted" }, o.reasons.join("; ")))))));
}

// ------------------------------------------------------------------ render
function render() {
  banners();
  document.getElementById("exported").textContent = `exported ${board.exported_at.replace("T", " ").slice(0, 16)} UTC`;
  const parts = board.arm
    ? [comparison(), scorecard(), summary(), results(), runs(), needs()]
    : [summary(), shortlist(), results(), needs(), runs(), proposed(), comparison()];
  document.getElementById("app").replaceChildren(...parts.filter(Boolean));
}

function selectBoard(name) {
  board = DATA.boards[name];
  selectedResult = null;
  history.replaceState(null, "", `#board=${encodeURIComponent(name)}`);
  render();
}

function start() {
  if (!DATA || !DATA.index || !DATA.index.boards.length) return; // keep the "Loading…" help text
  const select = document.getElementById("board-select");
  for (const b of DATA.index.boards) {
    select.append(el("option", { value: b.name }, b.name + (b.synthetic ? " (synthetic)" : "")));
  }
  const first = initialBoard();
  select.value = first;
  select.addEventListener("change", () => selectBoard(select.value));
  selectBoard(first);
}
start();
