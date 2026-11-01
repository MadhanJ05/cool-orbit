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
    if (kid.srcNote) { addSource(n, kid.srcNote); continue; }
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
  const list = kids.flat().filter((k) => k !== null && k !== undefined && k !== false);
  node.removeAttribute("title"); node.classList.remove("has-src");
  for (const k of list) if (k.srcNote) addSource(node, k.srcNote);
  node.replaceChildren(...list.filter((k) => !k.srcNote));
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
// Sources are not printed on the page: each one becomes the hover tooltip of the element it sits in.
function srcTag(source) { return source ? { srcNote: source } : null; }
function addSource(node, source) {
  node.title = node.title ? `${node.title}\n${source}` : `Source: ${source}`;
  node.classList.add("has-src");
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
// ---------------------------------------------------------------- friendly run names
const PROP_WORD = { melting_point: "melting point", latent_heat: "latent heat", lattice_constant: "lattice constant",
                    bulk_modulus: "bulk modulus", adsorption_energy: "adsorption energy", self_diffusion: "diffusion" };
// ?run=<board name> or <arm>-r<repeat>.<task_id> picks the board the run-scoped cards read (default: science-2)
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
  live.className = "live fresh";
  live.textContent = "Live";
  if (IDX.exported_at) live.title = `Board export ${IDX.exported_at.replace("T", " ").slice(0, 16)} UTC`;
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
  if (page === "agents") loadAgentsFrame();
  observeReveals();
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
const ratioText = (v) => (has(v) && v > 0 ? `${v >= 10 ? Math.round(v) : v.toFixed(1)}×` : null);
function burstPair() {
  const b = CTX.burst;
  const c = b && !b.error ? (b.comparisons || [])[0] : null;
  return c ? { b, c } : null;
}
function h2hRow(re) { return (((CTX.head_to_head || {}).rows) || []).find((r) => re.test(r.aspect)) || null; }
function pairText(row) {
  if (!row || !row.gallium || !row.paraffin) return "";
  const u = UNITS[row.units] || row.units || "";
  return `${num(row.gallium.value)} vs ${num(row.paraffin.value)} ${u}`.trim();
}
function renderStats() {
  const box = document.getElementById("stats");
  const bp = burstPair();
  const stats = [];
  if (bp) {
    const { c } = bp;
    const metal = shortName(c.metal), wax = "wax";
    if (c.conductivity) stats.push({ num: ratioText(c.conductivity.value), lbl: `${metal} conducts heat better than ${wax}`,
      sub: `liquid: ${pairText(h2hRow(/conduction/i))}`, source: c.conductivity.source });
    if (c.volume) stats.push({ num: ratioText(c.volume.value), lbl: "smaller container for one burst",
      sub: pairText(h2hRow(/volume$/i)) || pairText(h2hRow(/container/i)), source: c.volume.source });
    if (c.area) stats.push({ num: ratioText(c.area.value), lbl: "less heat-exchange area",
      sub: pairText(h2hRow(/area/i)), source: c.area.source });
    if (c.total_kg) stats.push({ num: ratioText(c.total_kg.value), lbl: "heavier: the trade-off",
      sub: `device mass ${pairText(h2hRow(/mass/i))}`, source: c.total_kg.source });
  }
  const s = bp && bp.b.scenario;
  fill(box, stats.map((x) => {
    const n = el("div", { class: "num" });
    countUp(n, x.num);
    return el("div", { class: "stat" }, n, el("div", { class: "lbl" }, x.lbl), el("div", { class: "sub" }, x.sub, srcTag(x.source)));
  }), s ? el("div", { class: "stats-note" },
    `${bp.c.metal} vs ${bp.c.wax}, for one ${num(s.minutes)}-minute burst at ${num(s.power_W)} W.`, srcTag(s.source)) : null);
  box.hidden = !stats.length;
}
function renderHeroAnswer() {
  const box = document.getElementById("hero-answer");
  const h = CTX.head_to_head;
  if (!h || !h.verdict) { box.hidden = true; return; }
  fill(box, el("span", { class: "albl" }, "Answer so far"), h.verdict, srcTag(h.source));
  box.hidden = false;
}
function renderWhy() {
  const box = document.getElementById("why");
  const q = CTX.question;
  const lines = (q && q.lines) || [];
  if (!lines.length) { box.hidden = true; return; }
  const LEADS = ["The problem", "No air in orbit", "Radiators are heavy", "The fix: a thermal battery"];
  fill(box, el("p", { class: "eyebrow reveal" }, "Why it matters"),
    el("div", { class: "why-points reveal" }, lines.map((l, i) => el("div", { class: "point" },
      el("b", {}, LEADS[i] || ""), el("span", {}, l.text, srcTag(l.source))))));
}
function renderBurst() {
  const card = document.getElementById("burst-card");
  const b = CTX.burst;
  const mats = (b && !b.error && b.materials) || [];
  if (!mats.length) { card.hidden = true; return; }
  const s = b.scenario || {};
  const cls = (m) => (/gallium/i.test(m.label) ? "ga" : "pa");
  const chart = (title, sub, key, units, withFins) => {
    const max = Math.max(...mats.map((m) => m.result[key]));
    return el("div", {}, el("h4", {}, title, el("span", { class: "muted" }, ` · ${sub}`)),
      mats.map((m) => {
        const r = m.result;
        const w = (v) => `width:${(100 * v / max).toFixed(2)}%`;
        return el("div", { class: "bar-row" }, el("span", { class: "bl", title: m.label }, m.label),
          el("span", { class: "bar-track" },
            withFins ? [el("span", { class: cls(m), style: w(r.pcm_kg) }), el("span", { class: "fin", style: w(r.fin_kg) })]
                     : el("span", { class: cls(m), style: w(r[key]) })),
          el("span", { class: "bv" }, `${num(r[key])} ${units}`));
      }));
  };
  fill(card,
    el("h3", {}, b.title || "One burst"),
    el("p", { class: "muted small" }, `How much thermal battery absorbs ${num(s.power_W)} W for ${num(s.minutes)} minutes (${num(s.energy_kJ)} kJ)?`, srcTag(s.source)),
    el("div", { class: "bar-key" },
      mats.map((m) => el("span", {}, el("i", { class: `bar-track ${cls(m)}`, style: cls(m) === "ga" ? "background:var(--accent)" : "background:#C9C5CF" }), m.label)),
      el("span", {}, el("i", { style: "background:#8E8899" }), "aluminium fins")),
    el("div", { class: "bars" },
      chart("Total mass", "shorter is lighter", "total_kg", "kg", true),
      chart("Container volume", "shorter is smaller", "pcm_volume_L", "L", false),
      chart("Heat-exchange area", "shorter is simpler", "area_m2", "m²", false)),
    b.takeaway ? el("p", { class: "takeaway" }, b.takeaway, srcTag(b.source)) : null,
    ((b.assumptions || []).length || (b.caveats || []).length) ? el("details", { class: "more" }, el("summary", {}, "Assumptions and caveats"),
      (b.assumptions || []).map((x) => el("p", {}, x.text, srcTag(x.source))),
      (b.caveats || []).map((x) => el("p", {}, x.text, srcTag(x.source)))) : null);
  card.hidden = false;
}
function renderTrustSummary() {
  const card = document.getElementById("trust-summary");
  const run = mainComparison();
  const rows = [];
  if (run) {
    const A = run.arms.A, C = run.arms.C;
    if (C.trap_catch_rate && A.trap_catch_rate) rows.push({ big: pct(C.trap_catch_rate.mean), t: "simulation traps caught",
      s: `by our collective, vs ${pct(A.trap_catch_rate.mean)} for a single agent · ${C.repeats} repeats` });
    if (C.silent_errors && has(C.silent_errors.max)) rows.push({ big: num(C.silent_errors.max), t: "confident wrong answers",
      s: `our collective, worst of ${C.silent_errors.n} repeats` });
    if (has(C.total_usd) && has(A.total_usd) && C.repeats && A.repeats) {
      const missing = C.tasks_without_cost_log || 0;
      rows.push({ big: `~$${(C.total_usd / C.repeats).toFixed(2)}`, t: "LLM cost per run",
        s: `vs ~$${(A.total_usd / A.repeats).toFixed(2)} single agent${missing ? ` · ${missing} refused task runs had no cost log` : ""}` });
    }
  }
  const cu = copperResult();
  if (cu && cu.result.status) {
    const diff = cu.ref ? (cu.result.value - cu.ref.value) / cu.ref.value : null;
    rows.push({ big: "Cu", t: `copper melting point: ${(STATUS_WORD[cu.result.status] || cu.result.status).toLowerCase()}`,
      s: [`our method check: ${fmtValue(cu.result.value, cu.result.units, cu.result.uncertainty)}`,
          cu.ref ? `vs ${fmtValue(cu.ref.value, cu.ref.units)} measured` : null, diff !== null ? `(${signedPct(diff)})` : null].filter(Boolean).join(" ") });
  }
  if (!rows.length) { card.hidden = true; return; }
  fill(card, el("h3", {}, "Why you can trust it"),
    el("p", { class: "muted small" }, run ? `Comparison ${run.run.replace(/^eval__/, "")}, all runs; copper is the known metal we use to prove the method.` : ""),
    el("div", { class: "tsum" }, rows.map((r) => el("div", { class: "row" },
      el("span", { class: "big" }, r.big), el("div", {}, el("div", { class: "t" }, r.t), el("div", { class: "s" }, r.s))))),
    el("p", { class: "card-cta small" }, el("a", { href: "#/trust" }, "How a result earns trust →")));
  card.hidden = false;
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
  const steps = copperTimeline();
  if (steps.length < 2) { card.hidden = true; return; }
  const btn = el("button", { class: "btn-ghost", type: "button" }, "▷ Replay");
  card.hidden = false;
  const tl = el("div", { class: "tl" }, steps.map((s) => el("div", { class: `tl-step ${s.tone}` },
    el("span", { class: "mark" }), el("div", { class: "val" }, s.val), el("div", { class: "cap" }, s.cap, srcTag(s.source)))));
  fill(card, el("div", { class: "card-head" }, el("h3", {}, "Copper: how we proved the method"), btn),
    el("p", { class: "muted small" }, "Before trusting any gallium number, the agents had to get a known metal right. The trap, the catch and the checks, read from the results board."), tl);
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
// the Agents page frames the original dashboard's loop diagram (/classic/?embed=agents), loaded on first view
function setupAgentsFrame() {
  const frame = document.getElementById("agents-frame");
  const intro = document.getElementById("agents-intro");
  intro.textContent = ((CTX.agent_graph || {}).idea || {}).headline || "No boss: each agent owns one job and works only through the shared results board.";
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || e.source !== frame.contentWindow) return;
    const h = e.data && e.data.coolorbitEmbedHeight;
    if (typeof h === "number" && h > 0) frame.style.height = `${h}px`;
  });
}
function loadAgentsFrame() {
  const frame = document.getElementById("agents-frame");
  if (frame && !frame.getAttribute("src")) frame.setAttribute("src", frame.dataset.src);
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
    el("p", { class: "small muted reveal" }, el("a", { href: "#/trust" }, "See where the loop caught ALCHEMI assumptions →")),
    el("p", { class: "intro reveal" }, b ? `On ${b.name}${has(activeMin) ? `; green = posted in the last ${activeMin} minutes before the export` : ""}.` : ""),
    el("div", { class: "counts reveal" }, rows.map(({ n, count, active }) => el("div", { class: "agent" },
      el("span", { class: `adot ${active ? "active" : count ? "idle" : ""}` }),
      el("div", {}, el("div", { class: "aname" }, n.name), el("div", { class: "arole" }, n.line || n.role || "")),
      el("span", { class: "astate" }, count ? `${count} post${count === 1 ? "" : "s"}` : "not on this run")))));
  box.hidden = false;
}
// ---------------------------------------------------------------- footer
function renderFooter() {
  const repo = CTX.repo;
  fill(document.getElementById("footer"), repo ? el("p", {}, el("a", { href: repo }, repo.replace(/^https?:\/\//, ""))) : null);
}

// ---------------------------------------------------------------- start
function renderRunViews() {
  renderMaterialsSoFar(); renderTimeline(); renderTrustSummary();
  renderTrustCards();
  renderAgentCounts();
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
  renderHeroAnswer(); renderStats(); renderWhy(); renderBurst();
  renderBeats(); renderMeasured(); renderGa3d(); renderCatches(); renderFooter(); setupAgentsFrame();
  renderRunViews();
  showPage(false);
  window.addEventListener("hashchange", () => { showPage(true); window.scrollTo(0, 0); });
}
start();
