// Cool Orbit v2: hash-routed landing page + dashboard. Reads window.COOL_ORBIT_DATA (dashboard/export.py +
// dashboard/context.py) and, for the chosen run, /data/<board>.json.
// Rule: no number is typed into this file. Every number shown comes from the export; if a value is missing, the
// element that would show it is not rendered. Board strings are agent-written, so all text goes in via textContent.
// The CSP forbids inline style attributes, so dynamic styles are set through the CSSOM (node.style.cssText).
"use strict";

const D = window.COOL_ORBIT_DATA || null;
const IDX = (D && D.index) || {};
const CTX = (D && D.context) || {};
const BOARDS = (D && D.boards) || {};
const PAGES = ["overview", "materials", "trust", "agents"];

// timings and layout (not data)
const FRESH_MINUTES = 15;       // the "Live" pill is green when the export is younger than this
const STEP_MS = 600;            // copper replay: one timeline step per 600 ms
const COUNT_MS = 600;           // stat count-up
const STAGGER_MS = 60;          // scroll reveal stagger
const PARALLAX = 0.15;          // hero image moves at 0.15x the scroll speed
const WIDE_MIN_PX = 900;        // below this the hero image is static (CSS shrinks it)
const GA3D_WAIT_MS = 4000;      // show the no-WebGL note if ga3d.js hasn't drawn by then
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
const svg = (tag, attrs = {}, ...kids) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined) n.append(kid instanceof Node ? kid : String(kid));
  return n;
};
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
const signedPct = (x, digits = 1) => (has(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(100 * x).toFixed(digits)}%` : null);
const UNITS = { "J/kg": "kJ/kg", m2: "m²", "W/m/K": "W/m·K" };
function fmtValue(value, units, unc) {
  if (!has(value)) return null;
  let v = value, u = units, e = unc;
  if (units === "J/kg") { v = value / 1000; e = has(unc) ? unc / 1000 : unc; u = "kJ/kg"; }
  u = UNITS[u] || u;
  const main = num(v);
  return has(e) && e > 0 ? `${main} ± ${num(e)} ${u}` : `${main} ${u || ""}`.trim();
}
function srcTag(source, label = "source") {
  return source ? el("span", { class: "src", title: source }, `[${label}]`) : null;
}
const STATUS_WORD = { trusted: "Trusted", under_review: "Under review", flagged: "Flagged", rejected: "Rejected",
                      simulated: "Simulated", failed: "Failed", stopped: "Stopped", refused: "Refused" };
const status = (s, word) => el("span", { class: `status s-${s}` }, word || STATUS_WORD[s] || s);
const NAMES = { Cu: "Copper", Al: "Aluminium", In: "Indium", Bi: "Bismuth", Sn: "Tin", Ga: "Gallium", Si: "Silicon",
                Li: "Lithium", LiF: "Lithium fluoride", BiInSn: "Field's metal (Bi-In-Sn)", InBi: "In-Bi alloy" };
const metalName = (c) => (NAMES[c] ? `${NAMES[c]}${NAMES[c].includes("(") || c.length > 2 ? "" : ` (${c})`}` : c);
const ts = (iso) => (iso ? Date.parse(iso) : NaN);
const cap1 = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

// ---------------------------------------------------------------- board helpers
const isScience = (b) => b && !b.arm && !b.synthetic;
const indexEntries = () => (IDX.boards || []).filter((b) => !b.synthetic);
const scienceBoards = () => Object.values(BOARDS).filter(isScience);
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
function liveResults(b) { return ((b && b.results) || []).filter((r) => !(r.superseded_by || []).length); }
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
// one status per board for the picker dot: the best result status, else what its runs did
const STATUS_RANK = ["trusted", "under_review", "flagged", "simulated", "rejected"];
function boardStatus(name) {
  const b = BOARDS[name];
  const entry = indexEntries().find((e) => e.name === name) || {};
  const seen = new Set([...(b ? liveResults(b).map((r) => r.status) : []), ...Object.keys((entry.counts || {}).by_status || {})]);
  const best = STATUS_RANK.find((s) => seen.has(s));
  if (best) return best;
  const reps = (b && b.run_reports) || [];
  if (reps.some((r) => r.status === "refused")) return "refused";
  if (reps.some((r) => r.status === "failed")) return "failed";
  return "";
}

// ---------------------------------------------------------------- friendly run names
const PROP_WORD = { melting_point: "melting point", latent_heat: "latent heat", lattice_constant: "lattice constant",
                    bulk_modulus: "bulk modulus", adsorption_energy: "adsorption energy", self_diffusion: "diffusion" };
const ARM_LABEL = { A: "A · single agent", B: "B · worker + checker", C: "C · our collective" };
function taskName(id) {
  if (!id) return null;
  const t = (IDX.tasks || []).find((x) => x.id === id);
  if (!t) return id.replace(/_/g, " ");
  const mat = (t.material || "").replace(/\s*\((liquid|crystal)\)/, "");
  const phase = /\(liquid\)/.test(t.material || "") ? " (liquid)" : "";
  const temp = (id.match(/_(\d+)k$/) || [])[1];
  const word = PROP_WORD[t.property] || (t.property || "").replace(/_/g, " ");
  const name = `${NAMES[mat] || cap1(mat)}${phase} ${word}${temp ? `, ${temp} K` : ""}`;
  return id.startsWith("control_") ? `Control: ${name}` : name;
}
function runLabel(name) {
  const b = BOARDS[name] || indexEntries().find((e) => e.name === name) || { name };
  if (!b.arm) return { main: `Science board ${b.name.replace(/^science-/, "")}`, raw: b.name };
  return { main: taskName(b.task_id) || b.name, raw: `${b.arm}-r${b.repeat} · ${b.task_id || ""}`, arm: b.arm, repeat: b.repeat };
}
// ?run= value: science boards by name, comparison boards as <arm>-r<repeat>.<task_id>
function runParam(name) {
  const b = indexEntries().find((e) => e.name === name);
  return b && b.arm ? `${b.arm}-r${b.repeat}.${b.task_id}` : name;
}
function boardFromParam(p) {
  if (!p) return null;
  const list = indexEntries();
  return (list.find((e) => e.name === p) || list.find((e) => e.arm && `${e.arm}-r${e.repeat}.${e.task_id}` === p) || {}).name || null;
}

// ---------------------------------------------------------------- state: page + run
let selected = null;
function defaultRun() {
  // the science board that holds copper's trusted melting point, else the most recent science board
  const sci = byRecency(scienceBoards());
  const cu = sci.find((b) => liveResults(b).some((r) => r.candidate === "Cu" && r.property === "melting_point" && r.status === "trusted"));
  return (cu || sci[0] || Object.values(BOARDS)[0] || {}).name || null;
}
const sel = () => BOARDS[selected] || null;

// ---------------------------------------------------------------- nav + router
function renderLive() {
  const live = document.getElementById("live");
  const t = ts(IDX.exported_at);
  if (!has(t)) return;
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  const fresh = mins < FRESH_MINUTES;
  live.className = `live${fresh ? " fresh" : ""}`;
  live.textContent = fresh ? "Live" : `Last export ${mins < 120 ? `${mins} min` : `${Math.round(mins / 60)} h`} ago`;
  live.title = `Board export ${IDX.exported_at}`;
  live.hidden = false;
}
function currentPage() {
  const p = (location.hash.match(/^#\/([a-z]+)/) || [])[1];
  return PAGES.includes(p) ? p : "overview";
}
function showPage(animate = true) {
  const page = currentPage();
  for (const sec of document.querySelectorAll("section.page")) {
    const on = sec.dataset.page === page;
    if (on && sec.hidden) {
      sec.hidden = false;
      if (animate && !REDUCED) { sec.classList.remove("enter"); void sec.offsetWidth; sec.classList.add("enter"); }
    } else if (!on) sec.hidden = true;
  }
  for (const a of document.querySelectorAll(".tabs a")) a.classList.toggle("active", a.dataset.page === page);
  document.title = `Cool Orbit · ${cap1(page)}`;
  observeReveals();
}

// ---------------------------------------------------------------- run picker
function renderPicker() {
  const btn = document.getElementById("picker-btn"), pop = document.getElementById("picker-pop");
  const list = document.getElementById("picker-list"), search = document.getElementById("picker-search");
  const entries = indexEntries();
  if (!entries.length) return;
  btn.hidden = false;
  const option = (name) => {
    const l = runLabel(name);
    return el("button", { class: `po${name === selected ? " sel" : ""}`, type: "button", "data-name": name,
                          "data-q": `${l.main} ${l.raw} ${ARM_LABEL[l.arm] || ""}`.toLowerCase(),
                          onclick: () => { close(); chooseRun(name, true); } },
      el("span", { class: `pdot ${boardStatus(name)}`, title: STATUS_WORD[boardStatus(name)] || "no result" }),
      el("span", {}, l.main), el("span", { class: "rid" }, name));
  };
  const build = () => {
    const sci = entries.filter((e) => !e.arm).map((e) => e.name);
    const groups = [];
    if (sci.length) groups.push(el("div", { class: "pg" }, "Science boards"), sci.map(option));
    for (const arm of Object.keys(ARM_LABEL)) {
      const mine = entries.filter((e) => e.arm === arm);
      if (!mine.length) continue;
      groups.push(el("div", { class: "pg" }, ARM_LABEL[arm]));
      const reps = [...new Set(mine.map((e) => e.repeat))].sort((a, b) => a - b);
      for (const r of reps) {
        groups.push(el("div", { class: "pr" }, `r${r}`),
          mine.filter((e) => e.repeat === r).sort((a, b) => (taskName(a.task_id) || "").localeCompare(taskName(b.task_id) || "")).map((e) => option(e.name)));
      }
    }
    fill(list, groups);
  };
  const filter = () => {
    const q = search.value.trim().toLowerCase();
    for (const o of list.querySelectorAll(".po")) o.hidden = q && !o.dataset.q.includes(q) && !o.dataset.name.includes(q);
    // hide headings whose options are all hidden
    let heading = null, rep = null, anyG = false, anyR = false;
    const flush = () => { if (rep) rep.hidden = !anyR; if (heading) heading.hidden = !anyG; };
    for (const n of list.children) {
      if (n.classList.contains("pg")) { if (rep) rep.hidden = !anyR; if (heading) heading.hidden = !anyG; heading = n; rep = null; anyG = false; anyR = false; }
      else if (n.classList.contains("pr")) { if (rep) rep.hidden = !anyR; rep = n; anyR = false; }
      else if (!n.hidden) { anyG = true; anyR = true; }
    }
    flush();
  };
  const open = () => { build(); pop.hidden = false; btn.setAttribute("aria-expanded", "true"); search.value = ""; filter(); search.focus();
    const s = list.querySelector(".po.sel"); if (s) s.scrollIntoView({ block: "center" }); };
  const close = () => { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.onclick = (e) => { e.stopPropagation(); pop.hidden ? open() : close(); };
  search.oninput = filter;
  search.onkeydown = (e) => {
    if (e.key === "Escape") { close(); btn.focus(); }
    if (e.key === "Enter") { const first = [...list.querySelectorAll(".po")].find((o) => !o.hidden); if (first) first.click(); }
  };
  pop.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => { if (!pop.hidden) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) close(); });
}
function updatePickerButton() {
  const l = runLabel(selected);
  document.getElementById("picker-label").textContent = l.main;
  document.getElementById("picker-btn").title = selected;
  document.getElementById("picker-dot").className = `pdot ${boardStatus(selected)}`;
}
async function chooseRun(name, push) {
  if (!name) return;
  selected = name;
  if (push) {
    const u = new URL(location.href);
    u.searchParams.set("run", runParam(name));
    history.pushState({ run: name }, "", u);
  }
  updatePickerButton();
  const main = document.getElementById("main");
  main.classList.add("loading");
  const entry = indexEntries().find((e) => e.name === name);
  try {
    if (entry && entry.file) {
      const r = await fetch(`/data/${encodeURIComponent(entry.file)}`, { cache: "no-store" });
      if (r.ok) BOARDS[name] = await r.json();
    }
  } catch (e) { /* keep the copy that came with data.js */ }
  if (selected !== name) return; // a newer choice won
  main.classList.remove("loading");
  renderRunViews();
}

// ---------------------------------------------------------------- count-up + reveal
function countUp(node, text) {
  node.textContent = text;
  const m = /(-?\d+(?:\.\d+)?)/.exec(text || "");
  if (!m || REDUCED) return;
  const target = parseFloat(m[1]); const decimals = (m[1].split(".")[1] || "").length;
  const before = text.slice(0, m.index), after = text.slice(m.index + m[1].length);
  let t0 = null;
  const step = (t) => {
    if (t0 === null) t0 = t;
    const k = Math.min(1, (t - t0) / COUNT_MS);
    const eased = 1 - Math.pow(1 - k, 3);
    node.textContent = `${before}${(target * eased).toFixed(decimals)}${after}`;
    if (k < 1) requestAnimationFrame(step); else node.textContent = text;
  };
  requestAnimationFrame(step);
}
let revealer = null;
function observeReveals() {
  const items = [...document.querySelectorAll(".reveal:not(.in)")];
  if (REDUCED || !("IntersectionObserver" in window)) { items.forEach((n) => n.classList.add("in")); return; }
  if (!revealer) {
    revealer = new IntersectionObserver((entries) => {
      const shown = entries.filter((e) => e.isIntersecting);
      shown.forEach((e, i) => {
        e.target.style.transitionDelay = `${i * STAGGER_MS}ms`;
        e.target.classList.add("in");
        revealer.unobserve(e.target);
      });
    }, { threshold: 0.08 });
  }
  items.forEach((n) => revealer.observe(n));
}

// ---------------------------------------------------------------- hero image
function setupHero() {
  const img = document.getElementById("planet-img");
  const pic = document.getElementById("planet-pic");
  const fallback = document.getElementById("css-planet");
  const useFallback = () => { pic.hidden = true; fallback.hidden = false; };
  img.addEventListener("error", useFallback);
  if (img.complete && img.naturalWidth === 0) useFallback();
  if (REDUCED) return;
  const layer = document.getElementById("parallax");
  let ticking = false;
  window.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      layer.style.transform = window.innerWidth >= WIDE_MIN_PX ? `translateY(${window.scrollY * PARALLAX}px)` : "";
      ticking = false;
    });
  }, { passive: true });
}

// ---------------------------------------------------------------- overview: stats
function mainComparison() {
  const runs = IDX.comparison || [];
  const real = runs.filter((r) => !r.synthetic && r.arms && r.arms.A && r.arms.C);
  return real.sort((a, b) => (b.arms.C.repeats || 0) - (a.arms.C.repeats || 0))[0] || null;
}
function copperResult() {
  const b0 = sel();
  const order = [...(isScience(b0) ? [b0] : []), ...byRecency(scienceBoards()).filter((b) => b !== b0)];
  for (const b of order) {
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
    if (C.trap_catch_rate && A.trap_catch_rate) {
      stats.push({ num: pct(C.trap_catch_rate.mean), lbl: "traps caught",
        sub: `vs ${pct(A.trap_catch_rate.mean)} single agent · ${C.repeats} repeats` });
    }
    if (has(C.total_usd) && has(A.total_usd) && C.repeats && A.repeats) {
      const missing = C.tasks_without_cost_log || 0;
      stats.push({ num: `~$${(C.total_usd / C.repeats).toFixed(2)}`, lbl: "LLM cost per run",
        sub: `vs ~$${(A.total_usd / A.repeats).toFixed(2)} single agent` + (missing ? ` · ${missing} refused task runs had no cost log` : "") });
    }
    if (C.silent_errors && has(C.silent_errors.max)) {
      stats.push({ num: num(C.silent_errors.max), lbl: "confident wrong answers", sub: `our collective, worst of ${C.silent_errors.n} repeats` });
    }
  }
  const cu = copperResult();
  if (cu && cu.result.status) {
    const diff = cu.ref ? (cu.result.value - cu.ref.value) / cu.ref.value : null;
    stats.push({ num: `Cu: ${STATUS_WORD[cu.result.status] || cu.result.status}`, lbl: "copper melting point",
      sub: [fmtValue(cu.result.value, cu.result.units), cu.ref ? `vs ${fmtValue(cu.ref.value, cu.ref.units)} measured` : null,
            diff !== null ? `(${signedPct(diff)})` : null, `· ${cu.board.name}`].filter(Boolean).join(" ") });
  }
  fill(box, stats.map((s) => {
    const n = el("div", { class: "num" });
    countUp(n, s.num);
    return el("div", { class: "stat" }, n, el("div", { class: "lbl" }, s.lbl), el("div", { class: "sub" }, s.sub));
  }), run ? el("div", { class: "stats-note" }, `Comparison ${run.run.replace(/^eval__/, "")}: all runs, not just the one selected.`) : null);
  box.hidden = !stats.length;
}

// ---------------------------------------------------------------- overview: materials so far
function materialsRows(boards) {
  const rows = new Map();
  for (const b of boards) {
    for (const r of liveResults(b)) {
      const prev = rows.get(r.candidate);
      const better = !prev || (prev.result && prev.result.property !== "melting_point" && r.property === "melting_point");
      if (!prev || (better && prev.board === b) || prev.stopped) {
        const checks = checksOf(b, r.id);
        const adjudicated = checks.some((c) => c.agent === "adjudicator" && ["fail", "reject", "disagree"].includes(c.payload.verdict));
        rows.set(r.candidate, { board: b, result: r, ref: reference(r.candidate, r.property, [b, ...scienceBoards()]), adjudicated });
      }
    }
    for (const rep of b.run_reports || []) {
      if (!["failed", "refused"].includes(rep.status)) continue;
      const cand = candidateOf(b, (b.lineage || {})[rep.id] || { parents: [] });
      if (cand && !rows.has(cand)) rows.set(cand, { board: b, stopped: rep, cand });
    }
  }
  return [...rows.values()];
}
function renderMaterialsSoFar() {
  const card = document.getElementById("materials-so-far");
  const b = sel();
  const boards = b ? [b] : byRecency(scienceBoards());
  const rows = materialsRows(boards);
  fill(card, el("h3", {}, "Materials so far"),
    el("p", { class: "muted small" }, b ? `Simulated vs measured on ${b.name}.` : "Simulated vs measured.", " ",
      el("a", { href: "#/materials" }, "Compare with paraffin →")),
    rows.length ? el("div", { class: "mrows" }, rows.map((row) => {
      if (row.stopped) {
        return el("div", { class: "mrow" }, el("div", { class: "metal" }, metalName(row.cand)),
          el("div", { class: "vals" }, "Run stopped before a result: ", row.stopped.reason || row.stopped.task || ""), status(row.stopped.status === "refused" ? "refused" : "stopped"));
      }
      const r = row.result;
      const prop = PROP_WORD[r.property] || r.property.replace(/_/g, " ");
      const sim = fmtValue(r.value, r.units, r.uncertainty);
      const ref = row.ref ? fmtValue(row.ref.value, row.ref.units) : null;
      const diff = row.ref && r.units === row.ref.units ? (r.value - row.ref.value) / row.ref.value : null;
      return el("div", { class: "mrow" },
        el("div", { class: "metal" }, metalName(r.candidate)),
        el("div", { class: "vals" }, `${prop}: ${sim}`, ref ? ` vs ${ref} measured` : "", diff !== null ? ` (${signedPct(diff)})` : ""),
        row.adjudicated ? status("flagged", "Flagged by adjudicator") : status(r.status));
    })) : el("p", { class: "empty" }, "No simulated results on this run."));
}

// ---------------------------------------------------------------- overview: copper timeline
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
  const card = document.getElementById("timeline-card");
  const btn = document.getElementById("replay");
  const steps = copperTimeline();
  if (steps.length < 2) { card.hidden = true; btn.hidden = true; return; }
  card.hidden = false;
  const tl = el("div", { class: "tl" }, steps.map((s) => el("div", { class: `tl-step ${s.tone}` },
    el("span", { class: "mark" }), el("div", { class: "val" }, s.val), el("div", { class: "cap" }, s.cap, srcTag(s.source)))));
  fill(card, el("h3", {}, "Copper, start to finish"),
    el("p", { class: "muted small" }, "The trap, the catch and the checks, read from the results board."), tl);
  btn.hidden = false;
  btn.disabled = false;
  btn.onclick = () => {
    const items = [...tl.children];
    card.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "center" });
    if (REDUCED) return;
    btn.disabled = true;
    tl.classList.add("replaying");
    items.forEach((it) => it.classList.remove("on"));
    items.forEach((it, i) => setTimeout(() => {
      it.classList.add("on");
      if (i === items.length - 1) setTimeout(() => { tl.classList.remove("replaying"); btn.disabled = false; }, STEP_MS);
    }, STEP_MS * (i + 1)));
  };
}

// ---------------------------------------------------------------- materials page
function shortName(full) { return (full || "").split(" ")[0]; }
function renderBeats() {
  const card = document.getElementById("beats");
  const intro = document.getElementById("materials-intro");
  const h = CTX.head_to_head;
  const rows = (h && h.rows) || [];
  if (!rows.length) { card.hidden = true; intro.textContent = ""; return; }
  const M = h.materials || {};
  const ga = shortName(M.gallium), pa = shortName(M.paraffin);
  intro.textContent = `${M.paraffin || "Paraffin"} is today's flight-proven thermal battery. ${M.gallium || "Gallium"} is the metal challenger.`;
  const gate = rows.find((r) => /window/i.test(r.aspect));
  const wins = (side) => rows.filter((r) => r.winner === side);
  const g = wins("gallium"), p = wins("paraffin"), n = wins("neither");
  const massRow = rows.find((r) => /mass/i.test(r.aspect));
  const leader = g.length >= p.length ? ga : pa;
  const lead = g.length >= p.length ? g : p;
  const heavier = leader === ga && massRow && massRow.winner === "paraffin";
  const headline = `${leader} wins ${lead.length} of ${rows.length} comparisons${heavier ? ", but it's heavier." : "."}`;
  const share = (k) => `width:${(100 * k / rows.length).toFixed(2)}%`;
  const mult = (r) => (r.ratio && has(r.ratio.value) ? `${num(r.ratio.value)}×` : null);
  const winRow = (r) => el("div", { class: "win" },
    el("div", { class: "top" }, el("span", { class: "crit" }, r.aspect), mult(r) ? el("span", { class: "mult" }, mult(r)) : null),
    el("div", { class: "why" }, r.why || "", !mult(r) && r[r.winner] && r[r.winner].text ? ` · ${r[r.winner].text}` : "",
      srcTag((r[r.winner] || {}).source)));
  const w = (CTX.trust_rule || {}).window;
  fill(card,
    el("p", { class: "eyebrow" }, "Which material beats paraffin?"),
    el("p", { class: "headline" }, headline, srcTag(h.source)),
    el("div", { class: "scorebar", role: "img", "aria-label": `${ga} ${g.length}, ${pa} ${p.length}, neither ${n.length}` },
      g.length ? el("span", { class: "g", style: share(g.length) }) : null,
      p.length ? el("span", { class: "p", style: share(p.length) }) : null,
      n.length ? el("span", { class: "n", style: share(n.length) }) : null),
    el("div", { class: "scorelabels" },
      el("span", {}, el("i", { class: "g-key", style: "background:var(--accent)" }), el("b", {}, g.length), ` ${ga}`),
      el("span", {}, el("i", { style: "background:#C9C5CF" }), el("b", {}, p.length), ` ${pa}`),
      n.length ? el("span", {}, el("i", { style: "background:repeating-linear-gradient(45deg,#E6E1EA 0 2px,#fff 2px 4px)" }), el("b", {}, n.length), " neither") : null),
    el("div", { class: "wins" },
      el("div", {}, el("h4", {}, `Where ${ga.toLowerCase()} wins`), g.map(winRow)),
      el("div", {}, el("h4", {}, `Where ${pa.toLowerCase()} wins`), p.map(winRow))),
    gate || (w && has(w.min)) ? el("div", { class: "callout" },
      el("b", {}, "Window gate: "),
      w && has(w.min) && has(w.max) ? `a thermal battery must melt in ${num(w.min)}–${num(w.max)} K (${num(w.min - 273.15, 0)}–${num(w.max - 273.15, 0)} °C). ` : "",
      gate ? `${pa}: ${gate.paraffin.text}; ${ga}: ${gate.gallium.text}. Neither passes yet.` : "", srcTag(w && w.source)) : null);
  card.hidden = false;
}
function renderMeasured() {
  const card = document.getElementById("measured");
  const h = CTX.head_to_head;
  const rows = ((h && h.rows) || []).filter((r) => !/window/i.test(r.aspect));
  if (!rows.length) { card.hidden = true; return; }
  const M = h.materials || {};
  const cell = (side, row) => {
    const v = row[side];
    if (!v) return el("td", { class: "v" }, "–");
    const text = v.text || [num(v.value), UNITS[row.units] || row.units].filter(Boolean).join(" ");
    return el("td", { class: `v${row.winner === side ? " win" : ""}` }, text, srcTag(v.source));
  };
  fill(card, el("h3", {}, "Measured against paraffin"),
    el("p", { class: "muted small" }, "Measured values and one sourced burst calculation. The better value is in bold."),
    el("table", { class: "mtable" },
      el("thead", {}, el("tr", {}, el("th", {}, ""), el("th", {}, shortName(M.paraffin)), el("th", {}, shortName(M.gallium)))),
      el("tbody", {}, rows.map((r) => el("tr", {}, el("td", {}, r.aspect), cell("paraffin", r), cell("gallium", r))))));
  card.hidden = false;
}
function renderGa3d() {
  const card = document.getElementById("ga3d-card");
  const g = CTX.gallium;
  if (!g) { card.hidden = true; return; }
  const pair = g.pair, shell = g.next_shell;
  fill(card, el("p", { class: "eyebrow" }, "The challenger in 3D"),
    el("h3", {}, `Gallium: ${g.phase || "crystal"}`),
    el("div", { class: "ga3d-stage", id: "ga3d-stage" },
      el("div", { class: "fallback", id: "ga3d-fallback", hidden: true }, "The 3D view needs WebGL. The structure is described below.")),
    el("p", { class: "ga3d-cap" }, "Drag to rotate. Green bonds are gallium's close pairs",
      pair && has(pair.value) ? ` (${num(pair.value)} ${pair.units})` : "",
      shell && has(shell.min) ? `; the next ${shell.count} neighbours sit at ${num(shell.min)}–${num(shell.max)} ${shell.units}` : "",
      ". The same crystal our simulations start from.", srcTag(g.source)),
    (g.caveats || []).length ? el("details", { class: "more" }, el("summary", {}, "Caveats"),
      g.caveats.map((c) => el("p", {}, c.text, srcTag(c.source)))) : null);
  setTimeout(() => {
    const stage = document.getElementById("ga3d-stage");
    if (stage && stage.dataset.ready !== "1") document.getElementById("ga3d-fallback").hidden = false;
  }, GA3D_WAIT_MS);
}

// ---------------------------------------------------------------- trust page
function renderTrustCards() {
  const box = document.getElementById("trust-cards");
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
  const b = sel();
  const boards = isScience(b) ? [b] : scienceBoards();
  const results = boards.flatMap((x) => liveResults(x).map((r) => ({ b: x, r })));
  const withRef = results.filter(({ b: x, r }) => reference(r.candidate, r.property, [x, ...scienceBoards()]));
  const tol = ((CTX.trust_rule || {}).tolerances || []).filter((t) => ["melting_point", "latent_heat"].includes(t.property));
  const cards = [
    { n: "1", title: "Method audit passes", big: caught && has(caught.value) ? `${caught.value}/${caught.of}` : null,
      text: [caught && has(caught.value) ? "superheated crystals caught by our ALCHEMI hook" : null,
             falseAlarms && has(falseAlarms.value) ? `${falseAlarms.value}/${falseAlarms.of} false alarms on Cu two-phase boxes` : null].filter(Boolean).join("; "),
      source: caught && caught.source },
    { n: "2", title: "A different method agrees", big: has(passed) ? `${passed} of ${total}` : null,
      text: has(passed) ? "classical potentials passed their own test against measurements; the rest were excluded automatically" : "",
      source: classical && classical.source },
    { n: "3", title: "Matches a real measurement", big: results.length ? `${withRef.length} of ${results.length}` : null,
      text: [results.length ? `simulated results ${isScience(b) ? `on ${b.name}` : "on the science boards"} have a sourced measurement to match` : null,
             tol.length ? `tolerance ${tol.map((t) => `±${pct(t.tier1_rel, 0)} ${(t.label || t.property.replace("_", " ")).toLowerCase()}`).join(", ")}` : null].filter(Boolean).join("; "),
      source: (CTX.trust_rule || {}).source },
  ];
  fill(box, cards.map((c) => el("div", { class: "card tcard reveal" },
    el("div", { class: "n" }, c.n), el("h3", {}, c.title),
    c.big ? el("div", { class: "big" }, c.big) : null,
    c.text ? el("p", {}, c.text, srcTag(c.source)) : null)));
}
// number line for the "model is accurate for these metals" card: sim vs measured melting points on the science
// boards that fall outside the pre-registered tolerance
function accuracyPoints() {
  const tolRow = ((CTX.trust_rule || {}).tolerances || []).find((t) => t.property === "melting_point");
  const tol = tolRow && tolRow.tier1_rel;
  if (!has(tol)) return null;
  const pts = new Map();
  for (const b of scienceBoards()) {
    for (const r of liveResults(b)) {
      if (r.property !== "melting_point") continue;
      const ref = reference(r.candidate, r.property, [b, ...scienceBoards()]);
      if (!ref || ref.units !== r.units) continue;
      const d = (r.value - ref.value) / ref.value;
      if (Math.abs(d) > tol && !pts.has(r.candidate)) pts.set(r.candidate, d);
    }
  }
  return pts.size ? { tol, pts: [...pts.entries()] } : null;
}
function numberLine(data) {
  const W = 400, H = 70, PAD = 22, mid = W / 2;
  const span = Math.max(data.tol, ...data.pts.map(([, d]) => Math.abs(d))) * 1.25;
  const x = (d) => mid + (d / span) * (mid - PAD);
  return el("div", { class: "numline" }, svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": `Simulated minus measured: ${data.pts.map(([c, d]) => `${c} ${signedPct(d, 0)}`).join(", ")}; tolerance ±${pct(data.tol)}` },
    svg("rect", { x: x(-data.tol), y: 26, width: x(data.tol) - x(-data.tol), height: 12, rx: 6, fill: "#EEEAF7" }),
    svg("line", { x1: PAD, x2: W - PAD, y1: 32, y2: 32, stroke: "#E6E1EA", "stroke-width": 1.5 }),
    svg("line", { x1: mid, x2: mid, y1: 24, y2: 40, stroke: "#6B6876", "stroke-width": 1 }),
    svg("text", { x: mid, y: 56, "text-anchor": "middle", "font-size": 11, fill: "#6B6876" }, "measured"),
    svg("text", { x: x(data.tol), y: 56, "text-anchor": "middle", "font-size": 10, fill: "#8B7BC8" }, `+${pct(data.tol)}`),
    svg("text", { x: x(-data.tol), y: 56, "text-anchor": "middle", "font-size": 10, fill: "#8B7BC8" }, `−${pct(data.tol)}`),
    data.pts.map(([c, d], i) => [
      svg("circle", { cx: x(d), cy: 32, r: 6, fill: "#CF222E" }),
      svg("text", { x: x(d), y: i % 2 ? 66 : 16, "text-anchor": "middle", "font-size": 11, fill: "#1B1A24" }, `${c} ${signedPct(d, 0)}`),
    ])));
}
const OUTCOME_STATUS = { ok: "trusted", warn: "under_review", bad: "failed" };
function outcomeStatus(o) {
  const label = (o.label || "").toLowerCase();
  const known = Object.keys(STATUS_WORD).find((s) => label.startsWith(STATUS_WORD[s].toLowerCase()));
  return known || OUTCOME_STATUS[o.tone] || "under_review";
}
function renderCatches() {
  const sec = document.getElementById("catches");
  const C = CTX.catches;
  if (!C || !(C.cases || []).length) { sec.hidden = true; return; }
  const acc = accuracyPoints();
  const step = (cls, label, part, extra) => el("div", { class: `ms ${cls}` }, el("span", { class: "d" }),
    el("div", { class: "lbl" }, label),
    part && part.key ? el("div", { class: "key" }, part.key.text) : null, extra || null);
  fill(sec,
    el("h3", { class: "sect reveal" }, C.title || "Caught by the loop"),
    el("p", { class: "intro reveal" }, C.lede || ""),
    C.nvidia ? el("p", { class: "intro small reveal" }, C.nvidia.text, srcTag(C.nvidia.source)) : null,
    el("div", { class: "catch-grid" }, C.cases.map((c) => el("div", { class: "card catch reveal" },
      el("h4", {}, c.title),
      el("div", { class: "mini" },
        c.shipped ? step("ship", "Would have shipped", c.shipped) : null,
        c.caught ? step("caught", "Caught by", c.caught) : null,
        c.outcome ? step("out", "Outcome", c.outcome, status(outcomeStatus(c.outcome), cap1(c.outcome.label || ""))) : null),
      c.id === "accuracy" && acc ? numberLine(acc) : null,
      el("details", { class: "more" }, el("summary", {}, "Details"),
        c.shipped ? el("p", {}, el("b", {}, "A single pass would ship: "), c.shipped.text, srcTag(c.shipped.source)) : null,
        c.caught ? el("p", {}, el("b", {}, `Caught by: ${c.caught.who || ""} `), c.caught.text, srcTag(c.caught.source)) : null,
        c.outcome ? el("p", {}, el("b", {}, `${cap1(c.outcome.label || "Outcome")}: `), c.outcome.text, srcTag(c.outcome.source)) : null)))));
  sec.hidden = false;
}

// ---------------------------------------------------------------- agents page
function postsBy(n, b) {
  return Object.values((b && b.lineage) || {}).filter((a) => (n.agents || []).includes(a.agent) && a.type !== "need_claim");
}
function captionFor(row, nodes) {
  const agents = nodes.flatMap((n) => n.agents || []);
  switch (row.id) {
    case "steers": return "People and the chemistry lead set the rules before any run";
    case "proposes": return agents.length ? `${agents.length} scouts, one per material family` : "Scouts pick candidates";
    case "gates": return "Asks whether the property is measurable at all";
    case "simulates": return "Runs ALCHEMI on the GB10 GPU";
    case "checks": return `${nodes.length} independent checkers`;
    case "keeps": return "Only evidence sets a status";
    default: return "";
  }
}
function renderFlow() {
  const box = document.getElementById("flow");
  const intro = document.getElementById("agents-intro");
  const G = CTX.agent_graph || {};
  const nodes = G.nodes || [];
  intro.textContent = (G.idea || {}).headline || "No boss: each agent owns one job and works only through the shared results board.";
  const stages = (G.rows || []).map((r) => ({ ...r, nodes: nodes.filter((n) => n.row === r.id) })).filter((r) => r.nodes.length);
  if (!stages.length) { box.hidden = true; return; }
  const b = sel();
  const tip = document.getElementById("tooltip");
  const showTip = (n, e) => {
    const posts = postsBy(n, b).length;
    tip.textContent = `${n.does || n.line || ""}${(n.agents || []).length ? ` · ${posts} post${posts === 1 ? "" : "s"} on ${b ? b.name : "this run"}` : ""}`;
    tip.hidden = false;
    const r = e.currentTarget.getBoundingClientRect();
    const left = Math.min(window.innerWidth - tip.offsetWidth - 8, Math.max(8, r.left));
    const top = r.bottom + 8 + tip.offsetHeight > window.innerHeight ? r.top - tip.offsetHeight - 8 : r.bottom + 8;
    tip.style.cssText = `left:${left}px;top:${top}px`;
  };
  const hideTip = () => { tip.hidden = true; };
  fill(box,
    (G.idea && (G.idea.points || []).length) ? el("div", { class: "points reveal" }, G.idea.points.map((pt) =>
      el("div", { class: "point" }, el("b", {}, pt.lead), el("span", {}, pt.text, srcTag(pt.source))))) : null,
    el("div", { class: "flow reveal" }, stages.map((st, i) => el("div", { class: `col${st.id === "checks" ? " hero-col" : ""}` },
      el("div", { class: "node" }, String(i + 1)),
      el("div", { class: "ch" }, st.label),
      el("div", { class: "cap" }, captionFor(st, st.nodes)),
      st.nodes.map((n) => el("div", { class: "chip", tabindex: "0",
          onmouseenter: (e) => showTip(n, e), onmouseleave: hideTip, onfocus: (e) => showTip(n, e), onblur: hideTip },
        el("span", {}, (n.name || "").replace(/\s*\(.*\)$/, "").replace(/^NVIDIA ALCHEMI Toolkit/, "ALCHEMI")),
        n.tag && !(n.name || "").includes(n.tag) ? el("span", { class: "tag" }, n.tag) : null))))),
    el("p", { class: "small muted reveal" }, "Hover a chip for its job. ",
      el("a", { href: "#/trust" }, "See where the loop caught ALCHEMI assumptions →")));
  box.hidden = false;
}
function renderAgentCounts() {
  const box = document.getElementById("agent-counts");
  const G = CTX.agent_graph || {};
  const listed = (G.nodes || []).filter((n) => (n.agents || []).length);
  if (!listed.length) { box.hidden = true; return; }
  const b = sel();
  const activeMin = (G.status_rule || {}).active_minutes;
  const exportT = ts(IDX.exported_at);
  const rows = listed.map((n) => {
    const mine = postsBy(n, b);
    const last = mine.reduce((m, a) => Math.max(m, ts(a.timestamp)), -Infinity);
    const active = mine.length && has(activeMin) && exportT - last <= activeMin * 60000;
    return { n, count: mine.length, active };
  }).sort((x, y) => y.count - x.count);
  fill(box,
    el("h3", { class: "sect reveal" }, "Posts per agent"),
    el("p", { class: "intro reveal" }, b ? `On ${b.name}${has(activeMin) ? `; green = posted in the last ${activeMin} minutes before the export` : ""}.` : ""),
    el("div", { class: "counts reveal" }, rows.map(({ n, count, active }) => el("div", { class: "agent" },
      el("span", { class: `adot ${active ? "active" : count ? "idle" : ""}` }),
      el("div", {}, el("div", { class: "aname" }, n.name), el("div", { class: "arole" }, n.line || n.role || "")),
      el("span", { class: "astate" }, count ? `${count} post${count === 1 ? "" : "s"}` : "not on this run")))));
  box.hidden = false;
}
function renderRaw() {
  const sec = document.getElementById("raw");
  const b = sel();
  if (!b) { sec.hidden = true; return; }
  const short = (id) => (id || "").slice(0, 8);
  const table = (heads, rows) => rows.length
    ? el("table", { class: "rawt" }, el("thead", {}, el("tr", {}, heads.map((h) => el("th", {}, h)))), el("tbody", {}, rows))
    : el("p", { class: "empty" }, "None.");
  sec.hidden = false;
  fill(sec, el("details", { class: "raw" },
    el("summary", {}, `Show raw board: ${b.name}`),
    el("h4", {}, "Results"),
    table(["ID", "Material", "Property", "Value", "Method", "Status", "Why"], liveResults(b).map((r) => el("tr", {},
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
      el("td", { class: "mono" }, short(r.id)), el("td", {}, status(r.status === "refused" ? "refused" : "failed", r.status)),
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
    el("p", {}, "Sources: every number carries its source (hover the [source] tags)",
      repo ? ["; the full list is the ", el("a", { href: `${repo}/blob/main/docs/pitch/outline.md` }, "claims register"), "."] : "."),
    repo ? el("p", {}, el("a", { href: repo }, repo.replace(/^https?:\/\//, ""))) : null,
    IDX.rules_frozen === false ? el("p", {}, "Provisional: tolerances not frozen.") : null,
    IDX.exported_at ? el("p", {}, `Board export ${IDX.exported_at.replace("T", " ").slice(0, 16)} UTC.`) : null,
    el("p", {}, el("a", { href: "/classic/" }, "Classic dashboard (all tables and replays)")));
}

// ---------------------------------------------------------------- start
function renderRunViews() {
  renderStats(); renderMaterialsSoFar(); renderTimeline();
  renderTrustCards();
  renderFlow(); renderAgentCounts(); renderRaw();
  observeReveals();
}
function start() {
  if (!D) {
    document.getElementById("main").replaceChildren(el("p", { class: "wrap empty", style: "padding:80px 32px" },
      "No board export yet. Run python -m dashboard.export (or wait for the GB10 push)."));
    return;
  }
  if (!location.hash) history.replaceState(null, "", `${location.pathname}${location.search}#/overview`);
  setupHero(); renderLive();
  selected = boardFromParam(new URLSearchParams(location.search).get("run")) || defaultRun();
  renderPicker(); if (selected) updatePickerButton();
  renderBeats(); renderMeasured(); renderGa3d(); renderCatches(); renderFooter();
  renderRunViews();
  showPage(false);
  window.addEventListener("hashchange", () => { showPage(true); window.scrollTo(0, 0); });
  window.addEventListener("popstate", () => {
    const name = boardFromParam(new URLSearchParams(location.search).get("run")) || defaultRun();
    if (name !== selected) chooseRun(name, false);
  });
}
start();
