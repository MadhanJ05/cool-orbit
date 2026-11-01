// Cool Orbit v2: landing page + dashboard. Reads window.COOL_ORBIT_DATA (dashboard/export.py + dashboard/context.py).
// Rule: no number is typed into this file. Every number shown comes from the export; if a value is missing, the
// element that would show it is not rendered. Board strings are agent-written, so all text goes in via textContent.
"use strict";

const D = window.COOL_ORBIT_DATA || null;
const IDX = (D && D.index) || {};
const CTX = (D && D.context) || {};
const BOARDS = (D && D.boards) || {};
const FRESH_MINUTES = 15; // the "Live" pill is green when the export is younger than this
const STEP_MS = 600;      // copper replay: one timeline step per 600 ms

// ---------------------------------------------------------------- helpers
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "style") n.style.cssText = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
// replaceChildren() turns null into the text "null", so every section is filled through fill(), which drops
// missing pieces (that is how "hide the element if its value isn't in the data" is enforced).
function fill(node, ...kids) {
  node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
  return node;
}
const has = (x) => x !== null && x !== undefined && !(typeof x === "number" && Number.isNaN(x));
function num(v, digits) {
  if (!has(v)) return null;
  if (digits !== undefined) return Number(v).toFixed(digits);
  const a = Math.abs(v);
  // up to 6 significant figures for big values (1357.77 K stays 1357.77), 3 for small ones (0.149, 28.1)
  return String(+Number(v).toPrecision(a >= 100 ? 6 : 3));
}
const pct = (x, digits = 0) => (has(x) ? `${(100 * x).toFixed(digits)}%` : null);
const signedPct = (x) => (has(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(100 * x).toFixed(1)}%` : null);
const UNITS = { "J/kg": "kJ/kg", m2: "m²", "W/m/K": "W/m·K" };
function fmtValue(value, units, unc) {
  if (!has(value)) return null;
  let v = value, u = units, e = unc;
  if (units === "J/kg") { v = value / 1000; e = has(unc) ? unc / 1000 : unc; u = "kJ/kg"; }
  u = UNITS[u] || u;
  const main = num(v);
  return has(e) && e > 0 ? `${main} ± ${num(e)} ${u}` : `${main} ${u}`;
}
function srcTag(source, label = "source") {
  return source ? el("span", { class: "src", title: source }, `[${label}]`) : null;
}
const STATUS_WORD = { trusted: "Trusted", under_review: "Under review", flagged: "Flagged", rejected: "Rejected",
                      simulated: "Simulated", failed: "Failed", stopped: "Stopped" };
const status = (s, word) => el("span", { class: `status s-${s}` }, word || STATUS_WORD[s] || s);
const NAMES = { Cu: "Copper", Al: "Aluminium", In: "Indium", Bi: "Bismuth", Sn: "Tin", Ga: "Gallium",
                BiInSn: "Field's metal (Bi-In-Sn)", InBi: "In-Bi alloy" };
const metalName = (c) => (NAMES[c] ? `${NAMES[c]}${NAMES[c].includes("(") || c.length > 2 ? "" : ` (${c})`}` : c);
const ts = (iso) => (iso ? Date.parse(iso) : NaN);

// ---------------------------------------------------------------- board helpers
const scienceBoards = () => Object.values(BOARDS).filter((b) => !b.arm && !b.synthetic);
function lastActivity(b) {
  let t = -Infinity;
  for (const a of Object.values(b.lineage || {})) t = Math.max(t, ts(a.timestamp));
  return t;
}
const byRecency = (list) => [...list].sort((a, b) => lastActivity(b) - lastActivity(a));
function refsOf(b) {
  return Object.values(b.lineage || {}).filter((a) => a.type === "reference_value" && has(a.payload.value));
}
function reference(cand, prop, boards) {
  for (const b of boards) {
    const r = refsOf(b).find((a) => a.payload.candidate === cand && a.payload.property === prop);
    if (r) return r.payload;
  }
  return null;
}
function liveResults(b) { return (b.results || []).filter((r) => !(r.superseded_by || []).length); }
function checksOf(b, resultId) {
  const L = b.lineage || {};
  return ((L[resultId] || {}).children || []).map((id) => L[id]).filter((a) => a && a.type === "check");
}
function candidateOf(b, art) {
  const L = b.lineage || {};
  const seen = new Set(); const queue = [...(art.parents || [])];
  while (queue.length) {
    const id = queue.shift();
    if (seen.has(id)) continue; seen.add(id);
    const a = L[id];
    if (!a) continue;
    if (a.type === "candidate") return a.payload.name;
    queue.push(...(a.parents || []));
  }
  return null;
}

// ---------------------------------------------------------------- nav
let selected = null;
function renderNav() {
  const live = document.getElementById("live");
  const t = ts(IDX.exported_at);
  if (has(t)) {
    const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
    const fresh = mins < FRESH_MINUTES;
    live.className = `live${fresh ? " fresh" : ""}`;
    live.textContent = fresh ? "Live" : `Last export ${mins < 120 ? `${mins} min` : `${Math.round(mins / 60)} h`} ago`;
    live.title = `Board export ${IDX.exported_at}`;
    live.hidden = false;
  }
  const sel = document.getElementById("board-select");
  const sci = byRecency(scienceBoards());
  const evalBoards = Object.values(BOARDS).filter((b) => b.arm && !b.synthetic);
  if (sci.length) sel.append(el("optgroup", { label: "Science boards" }, sci.map((b) => el("option", { value: b.name }, b.name))));
  const runs = {};
  for (const b of evalBoards) (runs[b.run || "eval"] = runs[b.run || "eval"] || []).push(b);
  for (const [run, list] of Object.entries(runs)) {
    sel.append(el("optgroup", { label: `Comparison ${run.replace(/^eval__/, "")}` },
      list.sort((a, b) => a.name.localeCompare(b.name)).map((b) =>
        el("option", { value: b.name }, `${b.arm}-r${b.repeat}${b.task_id ? ` · ${b.task_id}` : ""}`))));
  }
  selected = (sci[0] || Object.values(BOARDS)[0] || {}).name || null;
  if (selected) sel.value = selected;
  sel.hidden = !sel.options.length;
  sel.addEventListener("change", () => { selected = sel.value; renderBoardViews(); });
}

// ---------------------------------------------------------------- stats
function mainComparison() {
  const runs = IDX.comparison || [];
  const real = runs.filter((r) => !r.synthetic && r.arms && r.arms.A && r.arms.C);
  return real.sort((a, b) => (b.arms.C.repeats || 0) - (a.arms.C.repeats || 0))[0] || null;
}
function copperResult() {
  for (const b of byRecency(scienceBoards())) {
    const r = liveResults(b).filter((x) => x.candidate === "Cu" && x.property === "melting_point").pop();
    if (r) return { board: b, result: r, ref: reference("Cu", "melting_point", [b, ...scienceBoards()]) };
  }
  return null;
}
function renderStats() {
  const box = document.getElementById("stats");
  const stats = [];
  const run = mainComparison();
  if (run) {
    const A = run.arms.A, C = run.arms.C;
    const reps = C.repeats;
    if (C.trap_catch_rate && A.trap_catch_rate) {
      stats.push({ num: pct(C.trap_catch_rate.mean), lbl: "traps caught",
        sub: `vs ${pct(A.trap_catch_rate.mean)} single agent · ${reps} repeats` });
    }
    if (has(C.total_usd) && has(A.total_usd) && C.repeats && A.repeats) {
      const missing = C.tasks_without_cost_log || 0;
      stats.push({ num: `~$${(C.total_usd / C.repeats).toFixed(2)}`, lbl: "LLM cost per run",
        sub: `vs ~$${(A.total_usd / A.repeats).toFixed(2)} single agent` +
             (missing ? ` · ${missing} refused task runs had no cost log` : "") });
    }
    if (C.silent_errors && has(C.silent_errors.max)) {
      stats.push({ num: num(C.silent_errors.max), lbl: "confident wrong answers",
        sub: `our collective, worst of ${C.silent_errors.n} repeats` });
    }
  }
  const cu = copperResult();
  if (cu && cu.result.status) {
    const diff = cu.ref ? (cu.result.value - cu.ref.value) / cu.ref.value : null;
    stats.push({ num: `Cu = ${STATUS_WORD[cu.result.status] || cu.result.status}`, lbl: "copper melting point",
      sub: [fmtValue(cu.result.value, cu.result.units), cu.ref ? `vs ${fmtValue(cu.ref.value, cu.ref.units)} measured` : null,
            diff !== null ? `(${signedPct(diff)})` : null].filter(Boolean).join(" ") });
  }
  fill(box, ...stats.map((s) => el("div", { class: "stat" },
    el("div", { class: "num" }, s.num), el("div", { class: "lbl" }, s.lbl), el("div", { class: "sub" }, s.sub))));
  box.hidden = !stats.length;
}

// ---------------------------------------------------------------- workflow stepper
const STEPS = [
  { name: "Scout", what: "proposes materials", agents: (a) => a.startsWith("scout_") },
  { name: "Simulate", what: "ALCHEMI / MACE", agents: (a) => a === "simulator_gb10" || a.startsWith("simulator") || a === "worker_cc" },
  { name: "Audit", what: "rules, premise, LLM, adjudicator", agents: (a) => ["referee_rules", "referee_premise", "referee_llm", "adjudicator"].includes(a) },
  { name: "Cross-check", what: "classical MD", agents: (a) => a === "referee_classical" },
  { name: "Measure", what: "Librarian: sourced values", agents: (a) => a === "librarian" },
  { name: "Rank", what: "trusted results only", agents: (a) => a === "ranker" },
];
function renderWorkflow(b) {
  const card = document.getElementById("workflow-card");
  const arts = Object.values((b && b.lineage) || {}).filter((a) => a.type !== "need_claim");
  const activeMin = ((CTX.agent_graph || {}).status_rule || {}).active_minutes;
  const exportT = ts(IDX.exported_at);
  const latest = arts.reduce((m, a) => (ts(a.timestamp) > ts((m || {}).timestamp) ? a : m), null);
  const steps = STEPS.map((s) => {
    const mine = arts.filter((a) => s.agents(a.agent)).sort((x, y) => ts(x.timestamp) - ts(y.timestamp));
    const last = mine[mine.length - 1];
    let state = mine.length ? "done" : "todo";
    if (last && last.type === "run_report" && ["failed", "refused"].includes(last.payload.status)) state = "failed";
    if (latest && last && latest.id === last.id && has(activeMin) && exportT - ts(last.timestamp) <= activeMin * 60000) state = "active";
    return { ...s, state, count: mine.length };
  });
  fill(card, 
    el("h3", {}, "Agent workflow"),
    el("p", { class: "muted small" }, b ? `What the agents have done on ${b.name}. ` : "", "Filled = working now, ✓ = done, red = its last run failed."),
    el("div", { class: "stepper" }, steps.map((s) => el("div", { class: `step ${s.state}` },
      el("div", { class: "dot" }, s.state === "done" ? "✓" : s.state === "failed" ? "!" : ""),
      el("div", { class: "name" }, s.name),
      el("div", { class: "what" }, s.what),
      s.count ? el("div", { class: "what" }, `${s.count} post${s.count === 1 ? "" : "s"}`) : null))));
}

// ---------------------------------------------------------------- materials so far
function materialsRows() {
  const boards = byRecency(scienceBoards());
  const rows = new Map();
  for (const b of boards) {
    for (const r of liveResults(b)) {
      const prev = rows.get(r.candidate);
      const better = !prev || (prev.result.property !== "melting_point" && r.property === "melting_point");
      if (!prev || (better && prev.board === b)) {
        const checks = checksOf(b, r.id);
        const adjudicated = checks.some((c) => c.agent === "adjudicator" && ["fail", "reject", "disagree"].includes(c.payload.verdict));
        rows.set(r.candidate, { board: b, result: r, ref: reference(r.candidate, r.property, [b, ...boards]), adjudicated });
      }
    }
    for (const rep of b.run_reports || []) {
      if (!["failed", "refused"].includes(rep.status)) continue;
      const cand = candidateOf(b, (b.lineage || {})[rep.id] || { parents: [] });
      if (cand && !rows.has(cand)) rows.set(cand, { board: b, stopped: rep });
    }
  }
  return [...rows.values()];
}
function renderResultsCard() {
  const card = document.getElementById("results-card");
  const rows = materialsRows();
  fill(card, el("h3", {}, "Materials so far"),
    el("p", { class: "muted small" }, "Simulated vs measured, newest science board per material."),
    rows.length ? el("div", { class: "mrows" }, rows.map((row) => {
      if (row.stopped) {
        return el("div", { class: "mrow" }, el("div", { class: "metal" }, metalName(candidateOrKey(row))),
          el("div", { class: "vals" }, "Run stopped before a result: ", row.stopped.reason || row.stopped.task || ""),
          status("stopped"));
      }
      const r = row.result;
      const prop = r.property === "melting_point" ? "melting point" : r.property.replace(/_/g, " ");
      const sim = fmtValue(r.value, r.units, r.uncertainty);
      const ref = row.ref ? fmtValue(row.ref.value, row.ref.units) : null;
      const diff = row.ref && r.units === row.ref.units ? (r.value - row.ref.value) / row.ref.value : null;
      return el("div", { class: "mrow" },
        el("div", { class: "metal" }, metalName(r.candidate)),
        el("div", { class: "vals" }, `${prop}: ${sim}`, ref ? ` vs ${ref} measured` : "", diff !== null ? ` (${signedPct(diff)})` : ""),
        row.adjudicated ? status("flagged", "Flagged by adjudicator") : status(r.status));
    })) : el("p", { class: "empty" }, "No simulated results on the science boards yet."));
}
function candidateOrKey(row) { return row.stopped ? candidateOf(row.board, (row.board.lineage || {})[row.stopped.id] || { parents: [] }) : row.result.candidate; }

// ---------------------------------------------------------------- overview: which material beats paraffin
function renderAnswer() {
  const sec = document.getElementById("answer");
  const h = CTX.head_to_head;
  const g = CTX.gallium;
  if (!h && !g) { sec.hidden = true; return; }
  const rows = (h && h.rows) || [];
  const wins = (side) => rows.filter((r) => r.winner === side);
  const ratio = (r) => (r.ratio && has(r.ratio.value) ? ` (${num(r.ratio.value)}×)` : "");
  const item = (r) => el("li", {}, el("b", {}, r.aspect), ratio(r), r.why ? el("span", { class: "muted" }, ` · ${r.why}`) : "");
  const neither = rows.filter((r) => r.winner === "neither");
  const verdictCard = h ? el("div", { class: "card" },
    el("p", { class: "eyebrow", style: "margin-bottom:8px" }, "Which material beats paraffin?"),
    el("p", { class: "verdict" }, h.verdict || ""),
    rows.length ? el("div", { class: "tally" },
      el("span", { class: "pill" }, `${h.materials.gallium} better on ${wins("gallium").length} of ${rows.length}`),
      el("span", { class: "pill" }, `${h.materials.paraffin} better on ${wins("paraffin").length} of ${rows.length}`),
      neither.length ? el("span", { class: "pill" }, `neither on ${neither.length}`) : null) : null,
    el("div", { class: "trade" },
      el("div", {}, el("h4", {}, `Where ${h.materials.gallium.split(" ")[0].toLowerCase()} wins`), el("ul", {}, wins("gallium").map(item))),
      el("div", {}, el("h4", {}, `Where ${h.materials.paraffin.split(" ")[0].toLowerCase()} wins`), el("ul", {}, wins("paraffin").map(item)))),
    (neither.length || (g && (g.caveats || []).length)) ? el("div", { class: "caveats" },
      neither.map((r) => el("p", { style: "margin:4px 0" }, el("b", {}, `${r.aspect}: `),
        `${h.materials.paraffin} ${r.paraffin.text || ""}; ${h.materials.gallium} ${r.gallium.text || ""}.`, srcTag(r.paraffin.source))),
      ((g && g.caveats) || []).map((c) => el("p", { style: "margin:4px 0" }, c.text, srcTag(c.source)))) : null,
    el("p", { class: "small", style: "margin-top:14px" }, el("a", { href: "#materials" }, "Full side-by-side table →"))) : null;
  const pair = g && g.pair, shell = g && g.next_shell;
  const ga3d = g ? el("div", { class: "card ga3d-card" },
    el("p", { class: "eyebrow", style: "margin-bottom:8px" }, "The challenger in 3D"),
    el("h3", {}, `Gallium: ${g.phase || "crystal"}`),
    el("div", { class: "ga3d-stage", id: "ga3d-stage" }, el("div", { class: "fallback", id: "ga3d-fallback", hidden: true },
      "The 3D view needs WebGL. The structure is described below.")),
    el("p", { class: "ga3d-cap" }, "Drag to rotate. Green bonds are gallium's close pairs",
      pair && has(pair.value) ? ` (${num(pair.value)} ${pair.units})` : "",
      shell && has(shell.min) ? `; the next ${shell.count} neighbours sit at ${num(shell.min)}–${num(shell.max)} ${shell.units}` : "",
      ". The same crystal our simulations start from.", srcTag(g.source))) : null;
  fill(sec, el("div", { class: "answer" }, verdictCard, ga3d));
  // ga3d.js (module) fills #ga3d-stage and sets data-ready="1"; show a note if it never does (no WebGL).
  setTimeout(() => {
    const stage = document.getElementById("ga3d-stage");
    if (stage && stage.dataset.ready !== "1") document.getElementById("ga3d-fallback").hidden = false;
  }, 4000);
}

// ---------------------------------------------------------------- materials (paraffin vs gallium)
function renderMaterials() {
  const sec = document.getElementById("materials");
  const h = CTX.head_to_head;
  if (!h || !(h.rows || []).length) { sec.hidden = true; return; }
  const w = (CTX.trust_rule || {}).window;
  const cell = (side, row) => {
    const v = row[side];
    if (!v) return el("td", { class: "v" }, "–");
    const text = v.text || [num(v.value), UNITS[row.units] || row.units].filter(Boolean).join(" ");
    return el("td", { class: `v${row.winner === side ? " win" : ""}` }, text, srcTag(v.source));
  };
  const gateRow = h.rows.find((r) => /window/i.test(r.aspect));
  const rows = h.rows.filter((r) => r !== gateRow);
  fill(sec, 
    el("h2", {}, "Measured against paraffin"),
    el("p", { class: "intro" }, "Paraffin wax is today's flight-proven thermal battery. Gallium is the metal challenger. ",
      "Measured values and one sourced burst calculation; the better value of each row is in bold."),
    w && has(w.min) && has(w.max) ? el("div", { class: "gate" }, el("span", { class: "pill" }, "Gate"),
      `Must melt in ${num(w.min)}–${num(w.max)} K (${num(w.min - 273.15, 0)}–${num(w.max - 273.15, 0)} °C)`,
      gateRow ? el("span", { class: "muted" }, ` · ${h.materials.paraffin}: ${gateRow.paraffin.text} · ${h.materials.gallium}: ${gateRow.gallium.text}`) : null,
      srcTag(w.source)) : null,
    el("table", { class: "compare" },
      el("thead", {}, el("tr", {}, el("th", {}, ""), el("th", {}, h.materials.paraffin), el("th", {}, h.materials.gallium))),
      el("tbody", {}, rows.map((r) => el("tr", {},
        el("td", { class: "aspect" }, r.aspect, r.units ? ` (${UNITS[r.units] || r.units})` : "", el("div", { class: "why" }, r.why || "")),
        cell("paraffin", r), cell("gallium", r))))),
    h.verdict ? el("p", { class: "muted small", style: "margin-top:14px" }, h.verdict, srcTag(h.source)) : null);
}

// ---------------------------------------------------------------- trust
function renderTrust() {
  const sec = document.getElementById("trust");
  const methods = CTX.methods || [];
  const hook = methods.find((m) => m.id === "hook");
  const classical = methods.find((m) => m.id === "classical");
  const ev = (hook && hook.evidence) || [];
  const caught = ev.find((e) => /flagged on the GPU/i.test(e.label));
  const falseAlarms = ev.find((e) => /false alarm/i.test(e.label));
  let passed = null, total = null;
  if (classical && classical.rows) {
    const els = {};
    for (const r of classical.rows) els[r.element] = (els[r.element] !== false) && !!r.counts;
    total = Object.keys(els).length; passed = Object.values(els).filter(Boolean).length;
  }
  const sci = scienceBoards();
  const results = sci.flatMap((b) => liveResults(b).map((r) => ({ b, r })));
  const withRef = results.filter(({ b, r }) => reference(r.candidate, r.property, [b, ...sci]));
  const tol = ((CTX.trust_rule || {}).tolerances || []).filter((t) => ["melting_point", "latent_heat"].includes(t.property));
  const cards = [
    { n: "1", title: "Method audit passes",
      big: caught && has(caught.value) ? `${caught.value}/${caught.of}` : null,
      text: [caught && has(caught.value) ? "superheated crystals caught by our ALCHEMI hook" : null,
             falseAlarms && has(falseAlarms.value) ? `${falseAlarms.value}/${falseAlarms.of} false alarms on Cu two-phase boxes` : null].filter(Boolean).join("; "),
      source: caught && caught.source },
    { n: "2", title: "A different method agrees",
      big: has(passed) ? `${passed} of ${total}` : null,
      text: has(passed) ? "classical potentials passed their own test against measurements; the rest were excluded automatically" : "",
      source: classical && classical.source },
    { n: "3", title: "Matches a real measurement",
      big: results.length ? `${withRef.length} of ${results.length}` : null,
      text: [results.length ? "simulated results on the science boards have a sourced measurement to match" : null,
             tol.length ? `tolerance ${tol.map((t) => `±${pct(t.tier1_rel, 0)} ${t.label || t.property.replace("_", " ")}`).join(", ")}` : null].filter(Boolean).join("; "),
      source: (CTX.trust_rule || {}).source },
  ];
  fill(sec, 
    el("h2", {}, "How a result earns trust"),
    el("p", { class: "intro" }, "A result is trusted only when all three hold."),
    el("div", { class: "three" }, cards.map((c) => el("div", { class: "card tcard" },
      el("div", { class: "n" }, c.n), el("h3", {}, c.title),
      c.big ? el("div", { class: "big" }, c.big) : null,
      c.text ? el("p", {}, c.text, srcTag(c.source)) : null))),
    el("p", { class: "rule-line" }, "No agent can set a status. Failures are posted, never dropped."));
}

// ---------------------------------------------------------------- copper timeline
function copperTimeline() {
  const cu = copperResult();
  const steps = [];
  const hook = (CTX.methods || []).find((m) => m.id === "hook");
  const naive = ((hook && hook.evidence) || []).find((e) => /naive/i.test(e.label));
  if (naive && has(naive.value)) {
    steps.push({ tone: "bad", val: fmtValue(naive.value, naive.units, naive.uncertainty), cap: "Naive: heat a perfect crystal (separate control run)", source: naive.source });
    steps.push({ tone: "bad", val: "Flagged", cap: "Method audit: single-phase heating superheats" });
  }
  if (cu) {
    const r = cu.result;
    steps.push({ tone: "", val: fmtValue(r.value, r.units, r.uncertainty), cap: `Rerun: ${(r.method && r.method.protocol) || "two-phase coexistence"} on ALCHEMI` });
    const check = checksOf(cu.board, r.id).find((c) => c.payload.check_type === "classical_md" && has(c.payload.value));
    if (check) {
      const model = ((check.payload.method || {}).model || "").split(",")[0];
      steps.push({ tone: "", val: fmtValue(check.payload.value, check.payload.units), cap: `Classical check${model ? ` (${model.split(" ")[0]})` : ""}: ${check.payload.verdict}` });
    }
    if (cu.ref) steps.push({ tone: "", val: fmtValue(cu.ref.value, cu.ref.units), cap: "Measured", source: cu.ref.source });
    if (r.status) steps.push({ tone: r.status === "trusted" ? "good" : "", val: (STATUS_WORD[r.status] || r.status).toUpperCase(), cap: `Board status on ${cu.board.name}` });
  }
  return steps;
}
function renderTimeline() {
  const sec = document.getElementById("timeline");
  const steps = copperTimeline();
  if (steps.length < 2) { sec.hidden = true; document.getElementById("replay").hidden = true; return; }
  fill(sec, 
    el("h2", {}, "Copper, start to finish"),
    el("p", { class: "intro" }, "The trap, the catch and the checks, read from the results board."),
    el("div", { class: "tl", id: "tl" }, steps.map((s) => el("div", { class: `tl-step ${s.tone}` },
      el("span", { class: "mark" }), el("div", { class: "val" }, s.val), el("div", { class: "cap" }, s.cap, srcTag(s.source))))));
  const btn = document.getElementById("replay");
  btn.hidden = false;
  btn.onclick = () => {
    const tl = document.getElementById("tl");
    const items = [...tl.children];
    sec.scrollIntoView({ behavior: "smooth", block: "center" });
    btn.disabled = true;
    tl.classList.add("replaying");
    items.forEach((it) => it.classList.remove("on"));
    items.forEach((it, i) => setTimeout(() => {
      it.classList.add("on");
      if (i === items.length - 1) setTimeout(() => { tl.classList.remove("replaying"); btn.disabled = false; }, STEP_MS);
    }, STEP_MS * (i + 1)));
  };
}

// ---------------------------------------------------------------- agents
function renderAgents(b) {
  const sec = document.getElementById("agents");
  const G = CTX.agent_graph || {};
  const nodes = G.nodes || [];
  const C = CTX.catches;
  if (!nodes.length && !C) { sec.hidden = true; return; }
  const arts = Object.values((b && b.lineage) || {});
  const activeMin = (G.status_rule || {}).active_minutes;
  const exportT = ts(IDX.exported_at);
  const idea = G.idea || {};
  // the loop: the agent-graph rows in order, with the nodes in each
  const stages = (G.rows || []).map((r) => ({ ...r, nodes: nodes.filter((n) => n.row === r.id) })).filter((r) => r.nodes.length);
  const listed = nodes.filter((n) => (n.agents || []).length);
  sec.hidden = false;
  fill(sec, 
    el("h2", {}, "How our agents work, in a loop"),
    el("p", { class: "intro" }, idea.headline || "No boss: each agent owns one job and works only through the shared results board."),
    (idea.points || []).length ? el("div", { class: "points" }, idea.points.map((pt) =>
      el("div", { class: "point" }, el("b", {}, pt.lead), el("span", {}, pt.text, srcTag(pt.source))))) : null,
    stages.length ? el("div", { class: "loop" }, stages.map((st) => el("div", { class: "stage" },
      el("div", { class: "slabel" }, st.label),
      st.nodes.map((n) => el("span", { class: "chip" }, n.name, n.model && n.model.name ? el("small", {}, n.model.name) : null))))) : null,
    C ? el("div", {},
      el("h3", { style: "font-family:var(--serif);font-weight:500;font-size:26px;margin:10px 0 6px" }, C.title || "Caught by the loop"),
      el("p", { class: "intro", style: "margin-bottom:22px" }, C.lede || ""),
      C.nvidia ? el("p", { class: "nvidia" }, C.nvidia.text, srcTag(C.nvidia.source)) : null,
      el("div", { class: "catches" }, (C.cases || []).map((c) => el("div", { class: "card catch" },
        el("h4", {}, c.title),
        c.shipped ? el("div", { class: "row" }, el("b", {}, "A single pass would ship"), c.shipped.text, srcTag(c.shipped.source)) : null,
        c.caught ? el("div", { class: "row" }, el("b", {}, `Caught by: ${c.caught.who || ""}`), c.caught.text, srcTag(c.caught.source)) : null,
        c.outcome ? el("div", { class: "row" }, el("b", { class: `tone-${c.outcome.tone}` }, c.outcome.label), c.outcome.text, srcTag(c.outcome.source)) : null)))) : null,
    listed.length ? el("h3", { style: "font-family:var(--serif);font-weight:500;font-size:26px;margin:10px 0 6px" }, "Who is working now") : null,
    listed.length ? el("p", { class: "intro", style: "margin-bottom:10px" }, b ? `Activity on ${b.name}; green = posted in the last ${activeMin} minutes before the export.` : "") : null,
    listed.length ? el("div", { class: "agents" }, listed.map((n) => {
      const mine = arts.filter((a) => n.agents.includes(a.agent));
      const last = mine.reduce((m, a) => Math.max(m, ts(a.timestamp)), -Infinity);
      const active = mine.length && has(activeMin) && exportT - last <= activeMin * 60000;
      const state = !mine.length ? "not on this board" : active ? "working now" : `${mine.length} post${mine.length === 1 ? "" : "s"}`;
      return el("div", { class: "agent" },
        el("span", { class: `adot ${active ? "active" : mine.length ? "idle" : ""}`, title: state }),
        el("div", {}, el("div", { class: "aname" }, n.name), el("div", { class: "arole" }, n.line || n.does || n.role || "",
          n.model && n.model.name ? ` · ${n.model.name}` : "")),
        el("span", { class: "astate" }, state));
    })) : null);
}

// ---------------------------------------------------------------- raw board
function renderRaw(b) {
  const sec = document.getElementById("raw");
  if (!b) { sec.hidden = true; return; }
  const short = (id) => (id || "").slice(0, 8);
  const table = (heads, rows) => rows.length
    ? el("table", { class: "rawt" }, el("thead", {}, el("tr", {}, heads.map((h) => el("th", {}, h)))), el("tbody", {}, rows))
    : el("p", { class: "empty" }, "None.");
  sec.hidden = false;
  fill(sec, el("details", { class: "raw" },
    el("summary", {}, `Show raw board: ${b.name}`),
    el("h4", {}, "Results"),
    table(["ID", "Material", "Property", "Value", "Method", "Status", "Why"], (b.results || []).map((r) => el("tr", {},
      el("td", { class: "mono" }, short(r.id)), el("td", {}, r.candidate), el("td", {}, r.property),
      el("td", {}, fmtValue(r.value, r.units, r.uncertainty) || ""),
      el("td", {}, [r.method && r.method.engine, r.method && r.method.model, r.method && r.method.protocol].filter(Boolean).join(" · ")),
      el("td", {}, status(r.status)), el("td", { class: "muted" }, (r.reasons || []).join("; "))))),
    el("h4", {}, "Open needs"),
    table(["Need", "Kind", "Query", "Claimed by"], (b.open_needs || []).map((n) => el("tr", {},
      el("td", { class: "mono" }, `${short(n.artifact_id)}#${n.index}`), el("td", {}, n.kind || ""), el("td", {}, n.query || ""),
      el("td", {}, n.claimed_by || "unclaimed")))),
    el("h4", {}, "Failed and refused runs"),
    table(["Run", "Status", "Task", "Reason", "By"], (b.run_reports || []).map((r) => el("tr", {},
      el("td", { class: "mono" }, short(r.id)), el("td", {}, status(r.status === "refused" ? "under_review" : "failed", r.status)),
      el("td", {}, r.task || ""), el("td", {}, r.reason || ""), el("td", {}, r.agent || "")))),
    el("h4", {}, "Proposed, no results yet"),
    table(["Material", "Family", "Tier", "By"], (b.proposed || []).map((c) => el("tr", {},
      el("td", {}, c.name), el("td", {}, c.family || ""), el("td", {}, c.tier || ""), el("td", {}, c.agent || ""))))));
}

// ---------------------------------------------------------------- footer
function renderFooter() {
  const f = document.getElementById("footer");
  const repo = CTX.repo;
  fill(f, 
    CTX.claims ? el("p", {}, CTX.claims) : null,
    el("p", {}, "Sources: every number above carries its source (hover the [source] tags)",
      repo ? ["; the full list is the ", el("a", { href: `${repo}/blob/main/docs/pitch/outline.md` }, "claims register"), "."] : "."),
    repo ? el("p", {}, el("a", { href: repo }, repo.replace(/^https?:\/\//, ""))) : null,
    IDX.rules_frozen === false ? el("p", {}, "Provisional: tolerances not frozen.") : null,
    IDX.exported_at ? el("p", {}, `Board export ${IDX.exported_at.replace("T", " ").slice(0, 16)} UTC.`) : null,
    el("p", {}, el("a", { href: "/classic/" }, "Classic dashboard (all tables and replays)")));
}

// ---------------------------------------------------------------- start
function renderBoardViews() {
  const b = BOARDS[selected] || null;
  renderWorkflow(b); renderAgents(b); renderRaw(b);
}
function start() {
  if (!D) {
    document.querySelector("main").replaceChildren(el("p", { class: "wrap empty", style: "padding:80px 32px" },
      "No board export yet. Run python -m dashboard.export (or wait for the GB10 push)."));
    return;
  }
  renderNav(); renderStats(); renderAnswer(); renderResultsCard(); renderMaterials(); renderTrust(); renderTimeline(); renderFooter();
  renderBoardViews();
}
start();
