// Cool Orbit: the judges' console. Reads window.COOL_ORBIT_DATA, written by `python -m dashboard.export`: the boards
// with derived statuses and lineage, the comparison scorecards (pre-registered and post-hoc), and `context`
// (dashboard/context.py: the question, the burst calculation, the methods and the agent graph).
// Board payloads are agent-written, so every string goes in through textContent (el/svg helpers), never innerHTML.
// Charts and connectors are inline SVG built with DOM APIs. Every number carries its source: a numbered footnote
// (listed in the footer) with the same text on hover. Tabs are hash-routed (#overview, #agents, ...).
"use strict";

const DATA = window.COOL_ORBIT_DATA;
const CTX = DATA && DATA.context && !DATA.context.error ? DATA.context : null;
const G = CTX && CTX.agent_graph ? CTX.agent_graph : null;
const SVG_NS = "http://www.w3.org/2000/svg";
const GITHUB = "https://github.com/MadhanJ05/cool-orbit";

// ------------------------------------------------------------------ DOM helpers
function setAttrs(node, attrs) {
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.setAttribute("class", v);
    else if (k === "style") node.style.cssText = v; // CSSOM: allowed under the server's CSP (no inline style attrs)
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
}
function addKids(node, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false || kid === "") continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}
function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  setAttrs(node, attrs);
  return addKids(node, kids);
}
function svg(tag, attrs = {}, ...kids) {
  const node = document.createElementNS(SVG_NS, tag);
  setAttrs(node, attrs);
  return addKids(node, kids);
}
function mount(id, ...kids) {
  const host = document.getElementById(id);
  if (host) {
    host.replaceChildren();
    addKids(host, kids);
  }
  return host;
}

// ------------------------------------------------------------------ formatting
const nf = (opts) => new Intl.NumberFormat("en-US", opts);
const finite = (v) => typeof v === "number" && Number.isFinite(v);
function fmt(v, sig = 3) {
  if (!finite(v)) return "–";
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e6 || a < 1e-3) return v.toExponential(2);
  return nf({ maximumSignificantDigits: sig }).format(v);
}
const fmtExact = (v) => (finite(v) ? nf({ maximumFractionDigits: 4 }).format(v) : "–");
const fmt1 = (v) => (finite(v) ? nf({ minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(v) : "–");
const fmtCompact = (v) => (finite(v) ? nf({ notation: "compact", maximumSignificantDigits: 3 }).format(v) : "–");
function fmtPct(x, digits = 0, signed = false) {
  if (!finite(x)) return "n/a";
  const s = (100 * x).toFixed(digits).replace("-", "−");
  return `${signed && x > 0 ? "+" : ""}${s}%`;
}
const fmtRatio = (r) => (finite(r) && r > 0 ? `${r >= 10 ? Math.round(r) : r.toFixed(1)}×` : "–");
const UNIT = { "kg/m3": "kg/m³", "W/m/K": "W/m·K", m2: "m²", "g/cm3": "g/cm³", "cm2/s": "cm²/s", angstrom: "Å" };
const unit = (u) => UNIT[u] || u || "";
const shown = (value, units) => (units === "J/kg" ? { value: value / 1000, units: "kJ/kg" } : { value, units: unit(units) });
const fmtInput = (x) => { const v = shown(x.value, x.units); return `${fmtExact(v.value)} ${v.units}`; };
function listJoin(items) {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
const plural = (n, word, many) => `${n} ${n === 1 ? word : many || `${word}s`}`;
const pad2 = (n) => String(n).padStart(2, "0");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Python writes microseconds ("…:26.436231+00:00"); the ECMAScript date format has milliseconds, so trim to 3 digits.
const parseIso = (iso) => Date.parse(String(iso || "").replace(/(\.\d{3})\d+/, "$1"));
function fmtTime(iso) {
  const d = new Date(parseIso(iso));
  if (Number.isNaN(d.getTime())) return String(iso || "");
  const s = d.toISOString();
  return `${s.slice(0, 10)} ${s.slice(11, 16)} UTC`;
}
function fmtShort(iso) {
  const d = new Date(parseIso(iso));
  if (Number.isNaN(d.getTime())) return String(iso || "");
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC`;
}
function fmtSpan(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 48 * 3600) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}
function fmtGap(ms) {
  if (!finite(ms)) return null;
  return ms < 1000 ? `${Math.max(0, Math.round(ms))} ms` : fmtSpan(ms / 1000);
}
const clip = (text, n) => { const s = String(text || ""); return s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s; };
const PROPERTY = {
  melting_point: "Melting point", latent_heat: "Latent heat", transition_temperature: "Transition temperature",
  transition_latent_heat: "Transition latent heat", density: "Density", conductivity: "Thermal conductivity",
  lattice_constant: "Lattice constant", adsorption_energy: "Adsorption energy", self_diffusion: "Self-diffusion",
  bulk_modulus: "Bulk modulus",
};
const propLabel = (p) => PROPERTY[p] || String(p || "").replaceAll("_", " ");
const familyLabel = (f) => String(f || "").replaceAll("_", " ");
const normName = (s) => String(s || "").toLowerCase().split(/\s+/).filter(Boolean).join(" ");
const codeLink = (path) => el("a", { href: `${(G && G.code_url) || `${GITHUB}/blob/main/`}${path}`, rel: "noopener noreferrer" },
  el("code", {}, path));

// ------------------------------------------------------------------ sources (footnotes)
const SOURCES = [];
const SOURCE_KEYS = new Map();
const safeUrl = (u) => (typeof u === "string" && /^https:\/\/[^\s"'<>]+$/.test(u) ? u : null);
function cite(source, url) {
  if (!source) return null;
  const key = `${source}\u0000${url || ""}`;
  let n = SOURCE_KEYS.get(key);
  if (!n) {
    SOURCES.push({ source: String(source), url: safeUrl(url) });
    n = SOURCES.length;
    SOURCE_KEYS.set(key, n);
  }
  return el("sup", { class: "cite" },
    el("a", { href: `#src-${n}`, title: String(source), "aria-label": `source ${n}: ${source}` }, String(n)));
}

// ------------------------------------------------------------------ tooltip (enhances; every value is also in a table)
const tip = el("div", { class: "tip", "aria-hidden": "true" });
function showTip(evt, lines, anchor) {
  tip.replaceChildren(el("strong", {}, lines[0]), ...lines.slice(1).map((l) => el("span", {}, l)));
  tip.classList.add("on");
  let x;
  let y;
  if (evt && evt.type && evt.type.startsWith("pointer")) {
    x = evt.clientX;
    y = evt.clientY;
  } else {
    const r = anchor.getBoundingClientRect();
    x = r.left + Math.min(r.width / 2, 120);
    y = r.top;
  }
  const w = tip.offsetWidth;
  const h = tip.offsetHeight;
  tip.style.left = `${Math.max(8, Math.min(x + 14, window.innerWidth - w - 8))}px`;
  tip.style.top = `${y - h - 12 < 8 ? y + 18 : y - h - 12}px`;
}
const hideTip = () => tip.classList.remove("on");
function hoverable(node, lines) {
  node.addEventListener("pointermove", (e) => showTip(e, lines, node));
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("focus", (e) => showTip(e, lines, node));
  node.addEventListener("blur", hideTip);
  return node;
}

// ------------------------------------------------------------------ charts
// Bar with a 4px rounded data end and a square end on the baseline.
function barPath(x, y, w, h, r) {
  const rr = Math.max(0, Math.min(r, w, h / 2));
  if (!rr) return `M${x} ${y}h${w}v${h}h${-w}z`;
  return `M${x} ${y}H${x + w - rr}A${rr} ${rr} 0 0 1 ${x + w} ${y + rr}V${y + h - rr}` +
    `A${rr} ${rr} 0 0 1 ${x + w - rr} ${y + h}H${x}Z`;
}

/** Horizontal bars on one zero baseline, one row per item. Each row: {label, segments:[{value, cls}], total,
 *  display, displayLong?, tip:[lines], aria}. Segments stack with a 2px surface gap; slivers under 2.5px are not
 *  drawn (their values stay in the tooltip and the table). */
function barChart({ title, rows, max, track = false, valueAt = "tip" }) {
  const W = 320;
  const labelH = 18;
  const barH = 18;
  const gap = 16;
  const valueW = 72;
  const plotW = W - valueW;
  const H = rows.length * (labelH + barH + gap) - gap + 4;
  const scale = max || Math.max(...rows.map((r) => r.total || 0)) || 1;
  const root = svg("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "group", "aria-label": title });
  rows.forEach((r, i) => {
    const y0 = i * (labelH + barH + gap);
    const yb = y0 + labelH;
    const g = svg("g", { class: "mark", tabindex: "0", role: "img", "aria-label": r.aria });
    g.append(svg("rect", { class: "hit", x: 0, y: y0, width: W, height: labelH + barH + 4, rx: 6 }));
    g.append(svg("text", { x: 0, y: y0 + 12 }, r.label));
    if (track) g.append(svg("rect", { class: "track", x: 0, y: yb, width: plotW, height: barH, rx: 4 }));
    const segs = r.segments.map((s) => ({ ...s, w: (Math.max(0, s.value || 0) / scale) * plotW }))
      .filter((s) => s.w >= 2.5);
    let x = 0;
    segs.forEach((s, j) => {
      const x0 = j ? x + 2 : x;
      const w = Math.max(1, j ? s.w - 2 : s.w);
      g.append(svg("path", { class: `bar ${s.cls}`, d: barPath(x0, yb, w, barH, j === segs.length - 1 ? 4 : 0) }));
      x = x0 + w;
    });
    g.append(svg("line", { class: "baseline", x1: 0.5, x2: 0.5, y1: yb - 3, y2: yb + barH + 3 }));
    const vx = valueAt === "end" ? plotW + 8 : x + 8;
    const fits = r.displayLong && vx + r.displayLong.length * 7 <= W;
    g.append(svg("text", { class: "value", x: vx, y: yb + 13 }, fits ? r.displayLong : r.display));
    root.append(hoverable(g, r.tip));
  });
  return root;
}

function chartCard(title, sub, chart, source) {
  return el("figure", { class: "card chart-card" },
    el("figcaption", {}, el("h3", {}, title, cite(source)), el("p", { class: "fine" }, sub)),
    chart);
}

// ------------------------------------------------------------------ status vocabulary
const GROUPS = [
  { key: "trusted", label: "Trusted", tone: "ok", icon: "✓", statuses: ["trusted"],
    blurb: "A method audit passed, an independent method agreed within tolerance, and for known materials the value matches a sourced measurement.",
    empty: "Nothing yet. A result needs a passing audit, an agreeing independent method and, for known materials, a matching measurement." },
  { key: "pending", label: "Pending review", tone: "pend", icon: "○", statuses: ["under_review", "simulated"],
    blurb: "Simulated, and still waiting for the checks the trust rule needs.", empty: "None." },
  { key: "flagged", label: "Flagged", tone: "warn", icon: "!", statuses: ["flagged"],
    blurb: "A check failed or the numbers disagree. It needs a rerun and is never used for ranking.", empty: "None." },
  { key: "rejected", label: "Rejected", tone: "bad", icon: "✕", statuses: ["rejected"],
    blurb: "Rejected by independent referees.", empty: "None." },
];
const STATUS_TEXT = { trusted: "trusted", under_review: "under review", simulated: "awaiting checks",
                      flagged: "flagged", rejected: "rejected" };
const groupOf = (status) => (GROUPS.find((g) => g.statuses.includes(status)) || GROUPS[1]);
const badge = (tone, icon, text) => el("span", { class: `badge b-${tone}` },
  el("span", { class: "ico", "aria-hidden": "true" }, icon), text);
const statusBadge = (status) => { const g = groupOf(status); return badge(g.tone, g.icon, STATUS_TEXT[status] || status); };
function countGroups(results) {
  const c = { trusted: 0, pending: 0, flagged: 0, rejected: 0 };
  for (const r of results) c[groupOf(r.status).key] += 1;
  return c;
}

// ------------------------------------------------------------------ data helpers
function scienceBoards() {
  return DATA.index.boards.filter((e) => !e.arm && !e.task_id).map((e) => DATA.boards[e.name]).filter(Boolean);
}
const boardSource = (boards) =>
  `Results board export of ${fmtTime(DATA.index.exported_at)} (${boards.map((b) => b.name).join(", ") || "no science board"}); statuses derived by coolorbit/board/status.py`;
const exportedMs = () => parseIso(DATA.index.exported_at);
const runLabel = (run) => String(run || "").replace(/^eval__/, "");

// ------------------------------------------------------------------ top bar: tabs, data age, pills, countdown
// The tabs are whichever of these sections index.html holds (Comparison and Failures are commented out there for now).
const TABS = ["overview", "agents", "results", "comparison", "failures"].filter((t) => document.getElementById(t));
const hasTab = (t) => TABS.includes(t);
const TAB_TITLE = { overview: "Overview", agents: "Agents", results: "Results", comparison: "Comparison", failures: "Failures" };
let currentTab = null;

function locate() {
  const id = decodeURIComponent((window.location.hash || "").slice(1));
  if (!id || TABS.includes(id)) return { tab: id || "overview", target: null };
  const target = document.getElementById(id);
  if (!target) return { tab: currentTab || "overview", target: null };
  const panel = target.closest("[data-tab]");
  return { tab: panel ? panel.dataset.tab : (currentTab || "overview"), target };
}

function showTab(tab) {
  for (const t of TABS) {
    const on = t === tab;
    const panel = document.getElementById(t);
    const tabEl = document.getElementById(`tab-${t}`);
    if (panel) panel.hidden = !on;
    if (tabEl) {
      tabEl.setAttribute("aria-selected", on ? "true" : "false");
      tabEl.tabIndex = on ? 0 : -1;
    }
  }
  currentTab = tab;
  document.title = `${TAB_TITLE[tab] || "Overview"} · Cool Orbit console`;
  if (tab === "agents") requestAnimationFrame(drawEdges);
  else stopPlay();
}

function route() {
  const { tab, target } = locate();
  const changed = tab !== currentTab;
  showTab(tab);
  if (target) target.scrollIntoView({ block: "start" });
  else if (changed) window.scrollTo(0, 0);
}

function setupTabs() {
  const list = document.getElementById("tabs");
  if (!list) return;
  list.addEventListener("keydown", (e) => {
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    let j = null;
    if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = tabs.length - 1;
    else if (e.key === " ") { e.preventDefault(); tabs[i].click(); return; }
    if (j === null) return;
    e.preventDefault();
    tabs[j].focus();
    window.location.hash = tabs[j].getAttribute("href");
  });
  window.addEventListener("hashchange", route);
  route();
}

const DEADLINE_ISO = (CTX && CTX.deadline && CTX.deadline.iso) || "2026-11-01T18:00:00Z";
function setupCountdown() {
  const t = document.getElementById("countdown");
  const lbl = document.getElementById("countdown-label");
  if (!t || !lbl) return;
  const deadline = Date.parse(DEADLINE_ISO);
  const src = CTX && CTX.deadline ? `${CTX.deadline.text}. ${CTX.deadline.source}` :
    "Hacking ends Sunday Nov 1 2026, 1:00 PM in Cambridge, MA (EST, UTC-5): 18:00 UTC. CLAUDE.md section 2";
  lbl.replaceChildren("to the 1 PM deadline", cite(src));
  const tick = () => {
    const ms = deadline - Date.now();
    const s = Math.floor(Math.abs(ms) / 1000);
    t.textContent = `${ms >= 0 ? "T-" : "T+"}${pad2(Math.floor(s / 3600))}:${pad2(Math.floor((s % 3600) / 60))}:${pad2(s % 60)}`;
    if ((ms < 0) !== (lbl.dataset.past === "1")) {
      lbl.dataset.past = ms < 0 ? "1" : "0";
      lbl.firstChild.textContent = ms < 0 ? "past the 1 PM deadline" : "to the 1 PM deadline";
      t.setAttribute("aria-label", ms < 0 ? "Time since the 1 PM deadline" : "Time to the 1 PM deadline");
    }
  };
  tick();
  setInterval(tick, 1000);
}

const LIVE_MAX_S = 600; // an export older than 10 minutes is not called live
function updateDataAge() {
  const pill = document.getElementById("live-pill");
  const line = document.getElementById("data-age");
  if (!pill || !line || !DATA || !DATA.index) return;
  const age = (Date.now() - exportedMs()) / 1000;
  const known = finite(age);
  const fresh = known && age <= LIVE_MAX_S; // an unknown age is never called live
  pill.className = `pill ${fresh ? "live" : "stale"}`;
  pill.replaceChildren(el("span", { class: "dot", "aria-hidden": "true" }),
    fresh ? "Live" : known ? `Data ${fmtSpan(age)} old` : "Data age unknown");
  pill.title = `Board export of ${fmtTime(DATA.index.exported_at)} (index.exported_at, written by dashboard/export.py)`;
  line.className = `data-age${fresh ? "" : " stale"}`;
  line.title = pill.title;
  line.textContent = fresh ? `board export ${age < 5 ? "just now" : `${fmtSpan(age)} ago`}`
    : `export ${known ? `${fmtSpan(age)} old` : "of unknown age"} · not live`;
}

function renderPills(results) {
  const host = document.getElementById("pills");
  if (!host) return;
  const live = document.getElementById("live-pill");
  const pills = [live];
  // Counts live in the tabs, with their sources; the pill only says where the comparison stands (and links to it).
  for (const run of hasTab("comparison") ? DATA.index.comparison || [] : []) {
    if (run.synthetic) continue;
    const open = [...new Set((run.repeats || []).filter((d) => !d.complete).map((d) => d.repeat))].sort((a, b) => a - b);
    pills.push(el("a", { class: `pill cmp ${open.length ? "claim" : "ok"}`, href: "#comparison",
      title: `Comparison run ${runLabel(run.run)}: repeats with every pre-registered task finished, or still running` },
    el("span", { class: "dot", "aria-hidden": "true" }),
    open.length ? `${runLabel(run.run)} · ${open.map((r) => `r${r}`).join(", ")} in progress`
      : `${runLabel(run.run)} · repeats complete`));
  }
  host.replaceChildren(...pills);
}

// ------------------------------------------------------------------ banners
function renderBanners() {
  const items = [];
  if (DATA.index.any_synthetic) {
    items.push(el("div", { class: "banner bad", role: "alert" }, el("strong", {}, "Synthetic demo data in this export. "),
      "Boards marked SYNTHETIC hold invented numbers for testing the page. They are not results; do not cite them."));
  }
  if (!DATA.index.rules_frozen) {
    items.push(el("div", { class: "banner warn" }, el("strong", {}, "Provisional. "),
      "The trust tolerances are not frozen, so every status below is provisional."));
  }
  if (DATA.context && DATA.context.error) {
    items.push(el("div", { class: "banner warn" }, el("strong", {}, "Page context unavailable. "),
      `The export could not build it (${DATA.context.error}), so the burst, agents and methods sections are empty.`));
  } else if (!DATA.context) {
    items.push(el("div", { class: "banner warn" }, el("strong", {}, "Page context missing. "),
      "This export comes from an older exporter; the burst, agents and methods sections are empty."));
  } else if (!G) {
    items.push(el("div", { class: "banner warn" }, el("strong", {}, "Agent graph missing. "),
      "This export's context predates the Agents tab; re-run python -m dashboard.export."));
  }
  mount("banners", items);
}

// ------------------------------------------------------------------ overview
function trustSentence(c, n) {
  if (!n) return "No simulation result is on the results board yet.";
  const rest = [["pending", "pending review"], ["flagged", "flagged"], ["rejected", "rejected"]]
    .filter(([k]) => c[k]).map(([k, label]) => `${c[k]} ${label}`);
  const tail = rest.length ? ` (${listJoin(rest)})` : "";
  if (!c.trusted) {
    return n === 1 ? `The one simulated result on the board is not trusted yet${tail}.`
      : `None of the ${n} simulated results on the board is trusted yet${tail}.`;
  }
  return `${c.trusted} of ${n} simulated results ${c.trusted === 1 ? "is" : "are"} trusted so far${tail}.`;
}

function renderHero(results, boards) {
  const q = CTX && CTX.question;
  if (q) document.getElementById("question").textContent = q.title;
  const sub = document.getElementById("question-sub");
  if (sub && q && q.subtitle) sub.textContent = q.subtitle;
  const c = countGroups(results);
  const n = results.length;
  const src = boardSource(boards);
  const burst = CTX && CTX.burst && !CTX.burst.error ? CTX.burst : null;
  const hh = CTX && CTX.head_to_head;
  mount("hero-answer", el("p", { class: "answer" },
    el("span", { class: "answer-label" }, "Answer so far"),
    hh && hh.verdict ? [hh.verdict, cite(hh.source), " "]
      : burst && burst.headline ? [burst.headline, cite(burst.source), " "] : null,
    trustSentence(c, n), cite(src)));
  mount("hero-why", q ? [
    el("ul", { class: "why" }, q.lines.map((l) => el("li", {}, l.text, cite(l.source, l.url)))),
    q.physics ? el("p", { class: "fine physics" }, q.physics.text, cite(q.physics.source)) : null,
  ] : el("p", { class: "fine" }, "No framing lines in this export."));

  const runs = boards.flatMap((b) => b.run_reports);
  const failed = runs.filter((r) => r.status !== "refused").length;
  const refused = runs.length - failed;
  const stat = (v, label, tone, icon) => el("div", { class: "stat" },
    el("div", { class: "v" }, String(v)),
    el("div", { class: "l" }, icon ? el("span", { class: `ico t-${tone}`, "aria-hidden": "true" }, icon) : null, label));
  mount("hero-status",
    el("div", { class: "stats", role: "group", "aria-label": "Results board, by derived status" },
      stat(n, "simulated results"),
      stat(c.trusted, "trusted", "ok", "✓"),
      stat(c.pending, "pending review", "pend", "○"),
      stat(c.flagged, "flagged", "warn", "!"),
      stat(c.rejected, "rejected", "bad", "✕"),
      stat(failed, failed === 1 ? "failed run" : "failed runs", "bad", "✕")),
    el("p", { class: "fine stat-foot" },
      `From the results board${refused ? `, plus ${plural(refused, "run")} refused before running` : ""}.`, cite(src),
      " A status is derived from the evidence; no agent can set one. ",
      el("a", { href: "#agents" }, "How the agents work"), " · ", el("a", { href: "#results" }, "every result"),
      hasTab("failures") ? [" · ", el("a", { href: "#failures" }, "what failed")] : null));
}

const SERIES = ["s1", "s2", "s3", "s4"];
const seriesClass = (i) => SERIES[i] || SERIES[SERIES.length - 1];
const noteItem = (x) => el("li", {}, x.text, cite(x.source, x.url));

function kpiTiles(c) {
  const dir = (x, better, worse) => (x.value >= 1 ? [fmtRatio(x.value), better] : [fmtRatio(1 / x.value), worse]);
  const items = [
    [c.total_kg, ...dir(c.total_kg, `lighter in total: ${c.wax}, fins included`, `lighter in total: ${c.metal}`)],
    [c.volume, ...dir(c.volume, `smaller container: ${c.metal}`, `smaller container: ${c.wax}`)],
    [c.area, ...dir(c.area, `less heat-exchange area: ${c.metal}`, `less heat-exchange area: ${c.wax}`)],
    [c.conductivity, ...dir(c.conductivity, `higher liquid conductivity: ${c.metal}`, `higher liquid conductivity: ${c.wax}`)],
  ];
  return items.map(([x, v, label]) => el("div", { class: "card kpi" },
    el("div", { class: "v" }, v, cite(x.source)), el("div", { class: "l" }, label)));
}

function burstTable(b, mats) {
  const s = b.scenario;
  const inp = (key) => (m) => m.inputs[key];
  const res = (key) => (m) => ({ value: m.result[key], source: m.result.source });
  const rows = [
    ["group", "Measured inputs, each with its source"],
    ["Melts at", inp("melting_point_K"), (x) => `${fmtExact(x.value)} K`],
    ["Latent heat per kilogram", (m) => m.derived.latent_heat_kJ_per_kg, (x) => `${fmt(x.value)} kJ/kg`],
    ["Latent heat per litre", (m) => m.derived.latent_heat_kJ_per_L, (x) => `${fmt(x.value)} kJ/L`],
    ["Liquid thermal conductivity", inp("conductivity_liquid_W_mK"), (x) => `${fmtExact(x.value)} W/m·K`],
    ["Solid density", inp("density_solid_kg_m3"), (x) => `${fmtExact(x.value)} kg/m³`],
    ["Liquid density", inp("density_liquid_kg_m3"), (x) => `${fmtExact(x.value)} kg/m³`],
    ["group", `Computed for ${fmtExact(s.power_W)} W over ${fmtExact(s.minutes)} minutes`],
    ["PCM mass", res("pcm_kg"), (x) => `${fmt(x.value)} kg`],
    ["Aluminium fins", res("fin_kg"), (x) => `${fmt(x.value)} kg`],
    ["Total mass", res("total_kg"), (x) => `${fmt(x.value)} kg`],
    ["Container volume", res("pcm_volume_L"), (x) => `${fmt(x.value)} L`],
    ["Heat-exchange area", res("area_m2"), (x) => `${fmt(x.value)} m²`],
    ["Melted depth at the end of the burst", res("melt_depth_mm"), (x) => `${fmt(x.value)} mm`],
  ];
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", { scope: "col" }, el("span", { class: "muted" }, "Property")),
      mats.map((m, i) => el("th", { scope: "col", class: "num" }, el("span", { class: `sw ${seriesClass(i)}` }), " ", m.label)))),
    el("tbody", {}, rows.map((row) => (row[0] === "group"
      ? el("tr", { class: "group-row" }, el("th", { colspan: String(mats.length + 1), scope: "colgroup" }, row[1]))
      : el("tr", {}, el("th", { scope: "row" }, row[0]), mats.map((m) => {
        const x = row[1](m);
        return el("td", { class: "num" }, x && finite(x.value) ? [row[2](x), cite(x.source)] : "–");
      })))))));
}

function skippedList(b) {
  if (!b.skipped || !b.skipped.length) return null;
  return [
    el("h3", { class: "sub-h" }, "Not computed: inputs not sourced yet"),
    el("ul", { class: "skipped" }, b.skipped.map((s) => el("li", {},
      el("strong", {}, s.material),
      `: no sourced ${listJoin(s.missing_labels.map((x) => x.toLowerCase()))} yet`,
      s.notes.length ? ` (${s.notes.join("; ")})` : "",
      ", so it is left out rather than guessed.",
      s.sourced.length ? [" Sourced so far: ",
        s.sourced.map((x, i) => [i ? ", " : "", `${x.label.toLowerCase()} ${fmtInput(x)}`, cite(x.source)]), "."] : null))),
  ];
}

function renderBurst() {
  const b = CTX && CTX.burst;
  if (!b) {
    mount("burst-body", el("p", { class: "empty" }, "This export carries no burst calculation."));
    return;
  }
  document.getElementById("burst-h").textContent = b.title;
  const s = b.scenario;
  mount("burst-lede",
    `How much thermal battery does it take to absorb ${fmtExact(s.power_W)} W for ${fmtExact(s.minutes)} minutes ` +
    `(${fmtExact(s.energy_kJ)} kJ), if the hot wall may sit at most ${fmtExact(s.dT_K)} K above the melting point? ` +
    `Aluminium fins ${fmtExact(s.fin_mm)} mm thick carry the heat in.`, cite(s.source));
  if (b.error) {
    mount("burst-body", el("div", { class: "banner warn" }, `Not computed: ${b.error}.`));
    return;
  }
  const mats = b.materials || [];
  if (!mats.length) {
    mount("burst-body", el("p", { class: "empty" }, "No material has every burst input sourced yet."), skippedList(b));
    return;
  }
  const legend = el("ul", { class: "legend", "aria-label": "Legend" },
    mats.map((m, i) => el("li", {}, el("span", { class: `sw ${seriesClass(i)}`, "aria-hidden": "true" }), m.label)),
    el("li", {}, el("span", { class: "sw fins", "aria-hidden": "true" }), "aluminium fins"));
  const src = mats[0].result.source;
  const mass = barChart({
    title: "Total mass in kilograms",
    rows: mats.map((m, i) => {
      const r = m.result;
      return {
        label: m.label, total: r.total_kg, display: `${fmt1(r.total_kg)} kg`,
        displayLong: r.fin_kg >= 0.1 ? `${fmt1(r.total_kg)} kg (${fmt1(r.fin_kg)} kg fins)` : null,
        segments: [{ value: r.pcm_kg, cls: seriesClass(i) }, { value: r.fin_kg, cls: "fins" }],
        tip: [`${fmt(r.total_kg)} kg in total`, `${fmt(r.pcm_kg)} kg of ${m.label}`, `${fmt(r.fin_kg)} kg of aluminium fins`],
        aria: `${m.label}: ${fmt(r.total_kg)} kg in total, including ${fmt(r.fin_kg)} kg of aluminium fins`,
      };
    }),
  });
  const simple = (key, units, what) => barChart({
    title: what,
    rows: mats.map((m, i) => {
      const v = m.result[key];
      return { label: m.label, total: v, display: `${fmt(v, 2)} ${units}`, segments: [{ value: v, cls: seriesClass(i) }],
               tip: [`${fmt(v)} ${units}`, m.label, what], aria: `${m.label}: ${fmt(v)} ${units} ${what}` };
    }),
  });
  const charts = el("div", { class: "charts" },
    chartCard("Total mass", "Kilograms to launch: PCM plus aluminium fins. Shorter is lighter.", mass, src),
    chartCard("Container volume", "Litres of PCM, sized by its bulkier phase. Shorter is smaller.",
      simple("pcm_volume_L", "L", "container volume"), src),
    chartCard("Heat-exchange area",
      `Square metres of fin surface that keep the wall within ${fmtExact(s.dT_K)} K of the melting point. Shorter is simpler.`,
      simple("area_m2", "m²", "heat-exchange area"), src));
  const c0 = (b.comparisons || [])[0];
  mount("burst-body",
    legend, charts,
    c0 ? el("div", { class: "kpis" }, kpiTiles(c0)) : null,
    el("p", { class: "takeaway" }, b.takeaway),
    el("details", { class: "disclosure" },
      el("summary", {}, "The numbers behind the charts"),
      el("div", { class: "card" }, burstTable(b, mats))),
    skippedList(b),
    el("details", { class: "disclosure" },
      el("summary", {}, "How the calculation works, its assumptions and caveats"),
      el("div", { class: "two" },
        el("div", { class: "card" }, el("h3", {}, "Model"), el("ul", { class: "notes" }, (b.model || []).map(noteItem))),
        el("div", { class: "card" },
          el("h3", {}, "Assumptions, all in paraffin's favour"), el("ul", { class: "notes" }, (b.assumptions || []).map(noteItem)),
          el("h3", { class: "sub-h" }, "Caveats"), el("ul", { class: "notes" }, (b.caveats || []).map(noteItem))))));
}

// ------------------------------------------------------------------ agents: activity derived from the exported boards
let ACT = null;

/** Per agent: artifacts it posted on the science boards and arm C's comparison boards (our collective), by type,
 *  check verdicts, run-report statuses, its latest post, and the needs it held an unexpired claim on at export time. */
function buildActivity() {
  const agents = new Map();
  const claims = new Map();
  const scopes = { science: [], armC: [] };
  let artifacts = 0;
  let openNeeds = 0;
  for (const e of DATA.index.boards) {
    const scope = !e.arm && !e.task_id ? "science" : e.arm === "C" ? "armC" : null;
    const b = scope ? DATA.boards[e.name] : null;
    if (!b) continue;
    scopes[scope].push(b.name);
    openNeeds += (b.open_needs || []).length;
    for (const a of Object.values(b.lineage || {})) {
      artifacts += 1;
      let s = agents.get(a.agent);
      if (!s) {
        s = { total: 0, science: 0, armC: 0, byType: {}, verdicts: {}, runs: {}, refs: { sourced: 0, none: 0 }, last: null };
        agents.set(a.agent, s);
      }
      s.total += 1;
      s[scope] += 1;
      s.byType[a.type] = (s.byType[a.type] || 0) + 1;
      const p = a.payload || {};
      if (a.type === "check") s.verdicts[p.verdict] = (s.verdicts[p.verdict] || 0) + 1;
      if (a.type === "run_report") s.runs[p.status] = (s.runs[p.status] || 0) + 1;
      if (a.type === "reference_value") s.refs[p.value === null || p.value === undefined ? "none" : "sourced"] += 1;
      if (a.type === "property_result") { // where its results ran: a remote host is recorded in method.remote
        const remote = p.method && p.method.remote && p.method.remote.host;
        if (remote) s.remote = String(remote);
        else s.local = true;
      }
      if (!s.last || a.timestamp > s.last.timestamp) s.last = { ...a, board: b.name };
    }
    for (const n of b.open_needs || []) {
      if (!n.claimed_by) continue;
      if (!claims.has(n.claimed_by)) claims.set(n.claimed_by, []);
      claims.get(n.claimed_by).push({ ...n, board: b.name });
    }
  }
  return { agents, claims, scopes, artifacts, openNeeds };
}
const activitySource = () => {
  const sc = ACT ? ACT.scopes : { science: [], armC: [] };
  return `Results board export of ${fmtTime(DATA.index.exported_at)}: ${plural(sc.science.length, "science board")}` +
    ` (${sc.science.join(", ") || "none"}) and ${plural(sc.armC.length, "arm C comparison board")}; counted per producer_agent`;
};

/** ACTIVE / CLAIMED / IDLE / NO POSTS for a set of board agent names, derived from the export (never assumed). */
function agentState(names) {
  const acts = names.map((a) => ACT.agents.get(a)).filter(Boolean);
  const claims = names.flatMap((a) => ACT.claims.get(a) || []);
  const total = acts.reduce((t, s) => t + s.total, 0);
  const sum = (k) => acts.reduce((t, s) => t + s[k], 0);
  const last = acts.map((s) => s.last).filter(Boolean).sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1))[0] || null;
  const activeMin = G && G.status_rule ? G.status_rule.active_minutes : 60;
  if (!total) {
    return { key: "none", text: "no posts", total, science: 0, armC: 0, last, claims,
             sentence: "No posts on the exported boards." };
  }
  const ageMin = (exportedMs() - parseIso(last.timestamp)) / 60000;
  const base = { total, science: sum("science"), armC: sum("armC"), last, claims };
  if (claims.length) {
    const c = claims[0];
    return { ...base, key: "claimed", text: "claimed",
             sentence: `Claimed: at export time it held an unexpired claim on "${clip(c.query, 90)}"` +
               `${c.claim_expires_at ? ` (until ${fmtShort(c.claim_expires_at)})` : ""}. Last post ${fmtShort(last.timestamp)}.` };
  }
  if (!finite(ageMin)) {
    return { ...base, key: "idle", text: "idle", sentence: `Last post ${fmtShort(last.timestamp)} (its age could not be computed).` };
  }
  if (ageMin <= activeMin) {
    return { ...base, key: "active", text: "active",
             sentence: `Active: last post ${fmtShort(last.timestamp)}, ${fmtSpan(Math.max(0, ageMin) * 60)} before the export.` };
  }
  return { ...base, key: "idle", text: "idle",
           sentence: `Idle: last post ${fmtShort(last.timestamp)}, ${fmtSpan(ageMin * 60)} before the export.` };
}

// ------------------------------------------------------------------ agents: the diagram, an orbit around ALCHEMI
// Desktop: the steering band, then a three-column stage (style.css places each box by its node id) with ALCHEMI at
// the centre and the agents on a closed loop around it. The loop, the inner arcs (the loop-backs, G.loops) and the
// chemistry lead's rules lines are SVG drawn from the boxes' rendered positions (drawEdges); particles ride them with
// CSS offset-path. Below STACKED the boxes stack in one column and the drawing is hidden.
const STEER_IDS = ["humans", "chemlead"];
const STAGE_IDS = ["alchemi", "scouts", "premise", "simulator", "rules", "llm", "classical", "librarian", "adjudicator",
  "trust", "ranker", "worker", "board"];
const NODE_HUE = {
  humans: "steer", chemlead: "fable", scouts: "propose", premise: "gate", simulator: "sim", alchemi: "core",
  worker: "opus", rules: "check", llm: "opus", classical: "check", librarian: "check", adjudicator: "fable",
  trust: "keep", board: "keep", ranker: "keep",
};
const FIXED_BADGE = { humans: "people", chemlead: "ended", alchemi: "engine", board: "store", trust: "derived" };
const FIXED_NOTE = {
  humans: "no board posts: steers through frozen config files",
  chemlead: "no board posts: one literature review (memo of 2026-10-31); its rules live in the files it set",
  alchemi: "not an agent: it computes, it never decides",
  board: "agents talk only through it",
  trust: "no agent can set a status",
};
const KICKER = { premise: "parallel check", worker: "arm C only", board: "keeps", trust: "keeps" };
const NODE_EL = new Map();
const NODE_BY_ID = new Map();
let HOT = null;
const VERDICT_ORDER = ["pass", "agree", "fail", "disagree", "reject", "inconclusive"];

/** The board names a box stands for: its own, plus every exported agent with its prefix (the Brev H100 Simulators). */
function nodeAgents(n) {
  const names = new Set(n.agents);
  if (n.agent_prefix && ACT) for (const a of ACT.agents.keys()) if (a.startsWith(`${n.agent_prefix}_`)) names.add(a);
  return [...names];
}

const nchip = (label, n, cls, title) => el("li", { class: `nchip${cls ? ` ${cls}` : ""}`, title: title || null },
  label, n === undefined || n === null ? null : el("span", { class: "n" }, String(n)));

function verdictChips(names) {
  const tally = {};
  for (const a of names) {
    const s = ACT.agents.get(a);
    if (s) for (const [v, n] of Object.entries(s.verdicts)) tally[v] = (tally[v] || 0) + n;
  }
  const keys = Object.keys(tally).sort((a, b) => (VERDICT_ORDER.indexOf(a) + 99) % 99 - (VERDICT_ORDER.indexOf(b) + 99) % 99);
  return keys.length ? el("ul", { class: "chipset" }, keys.map((k) => nchip(k, tally[k]))) : null;
}

/** One chip per Simulator on the boards (GB10 or the remote H100, read from where its results ran), then totals. */
function simulatorChips(n) {
  const sims = nodeAgents(n).map((a) => [a, ACT.agents.get(a)]).filter(([, s]) => s);
  if (!sims.length) return null;
  const sum = (f) => sims.reduce((t, [, s]) => t + f(s), 0);
  const where = (s) => (s.remote ? "H100 · SSH" : s.local ? "GB10" : "no results");
  return [
    el("ul", { class: "chipset" }, sims.map(([a, s]) => nchip(`${a.replace(/^simulator_/, "")} · ${where(s)}`, s.total,
      s.remote ? "remote" : "", `${a}: ${plural(s.total, "post")}` + (s.remote
        ? `; its results ran on ${s.remote} over SSH (the Brev H100: scripts/run_remote_simulator.py)`
        : s.local ? "; its results ran on the GB10" : "; no results yet")))),
    el("ul", { class: "chipset" }, nchip("results", sum((s) => s.byType.property_result || 0)),
      nchip("failed", sum((s) => s.runs.failed || 0)), nchip("refused", sum((s) => s.runs.refused || 0))),
  ];
}

function nodeBody(n, results) {
  const act = (a) => ACT.agents.get(a) || null;
  switch (n.id) {
    case "humans":
      return el("ul", { class: "chipset" }, n.chips.map((c) => el("li", { class: "nchip" }, c.text, cite(c.source))));
    case "chemlead":
      return el("ul", { class: "chipset" }, (n.decisions || []).map((d) =>
        el("li", { class: "nchip rule", title: d.text }, d.short, cite(d.source))));
    case "scouts":
      return el("ul", { class: "chipset" }, (n.scouts || []).map((s) => {
        const a = act(s.agent);
        const label = s.general ? "general · tasks" : s.label;
        const title = `${s.agent}: ${s.families.map(familyLabel).join(", ") || "any family"}` +
          `${s.enabled ? "" : " (disabled in scout_seeds.json)"}${s.general ? " (answers comparison tasks no family owns)" : ""}`;
        return nchip(s.enabled ? label : `${label} · off`, s.enabled ? (a ? a.total : 0) : null, s.enabled ? "" : "off", title);
      }));
    case "simulator":
      return simulatorChips(n);
    case "worker": {
      const a = act(n.agents[0]);
      if (!a) return null;
      return el("ul", { class: "chipset" },
        nchip("results", a.byType.property_result || 0), nchip("failed", a.runs.failed || 0),
        nchip("refused", a.runs.refused || 0));
    }
    case "alchemi": {
      const t = n.throughput;
      return [
        (n.hosts || []).length ? el("ul", { class: "chipset hosts" },
          n.hosts.map((h) => el("li", { class: "nchip host" }, h.text, cite(h.source)))) : null,
        el("p", { class: "chipset-h" }, "ALCHEMI provides: what our code imports"),
        el("ul", { class: "chipset" }, (n.imports || []).map((i) => nchip(i.name, null, "api", `${i.what} (${i.module})`))),
        el("p", { class: "chipset-h" }, "The agents' protocol on it"),
        el("ul", { class: "chipset" }, (n.protocol || []).map((p) => nchip(p.text, null, "ours", p.source))),
        t ? el("p", { class: "rate" }, `${fmtExact(t.value)} ${t.units}`, cite(t.source),
          el("span", { class: "muted" }, " measured, MACE-MPA-0 on the GB10")) : null,
      ];
    }
    case "librarian": {
      const a = act("librarian");
      return a ? el("ul", { class: "chipset" }, nchip("sourced values", a.refs.sourced), nchip("not found", a.refs.none)) : null;
    }
    case "premise": case "rules": case "llm": case "classical": case "adjudicator":
      return verdictChips(nodeAgents(n));
    case "board":
      return el("ul", { class: "chipset" }, nchip("artifacts", ACT.artifacts),
        nchip("boards", ACT.scopes.science.length + ACT.scopes.armC.length), nchip("open needs", ACT.openNeeds));
    case "trust": {
      const c = countGroups(results);
      return el("ul", { class: "chipset" }, GROUPS.map((g) => nchip(g.label.toLowerCase(), c[g.key])));
    }
    case "ranker": {
      const a = act("ranker");
      return el("ul", { class: "chipset" }, nchip("rankings", a ? a.byType.ranking || 0 : 0));
    }
    default:
      return null;
  }
}

function nodeFoot(n, st) {
  if (!st) return FIXED_NOTE[n.id] || "";
  if (!st.total) return "no posts on the exported boards";
  const where = [st.science ? `science ${st.science}` : null, st.armC ? `arm C ${st.armC}` : null].filter(Boolean).join(" · ");
  const claim = st.claims.length ? ` · claim: ${clip(st.claims[0].query, 44)}` : "";
  return `${st.total} posts (${where}) · last ${fmtShort(st.last.timestamp)}${claim}`;
}

function renderNode(n, results) {
  const hue = NODE_HUE[n.id] || "keep";
  const agents = nodeAgents(n);
  const st = agents.length ? agentState(agents) : null;
  const fixed = FIXED_BADGE[n.id] || "";
  const badgeEl = st ? el("span", { class: `nbadge ${st.key}`, title: st.sentence }, st.text)
    : el("span", { class: `nbadge fixed ${fixed}`, title: FIXED_NOTE[n.id] || null }, fixed);
  const open = el("button", { type: "button", class: "node-title node-open", "aria-pressed": "false",
    "aria-label": `${n.name}: ${n.tag} · ${n.role}. ${st ? st.sentence : FIXED_NOTE[n.id] || ""} Show details.`,
    onclick: () => openDetail(n.id) }, `${n.tag} · ${n.role}`);
  const box = el("div", { class: `node h-${hue} n-${n.id}${n.id === "alchemi" ? " core" : ""}${st ? ` st-${st.key}` : ""}`,
    id: `node-${n.id}`, onclick: (e) => { if (!e.target.closest("a, button")) openDetail(n.id); } },
  n.id === "alchemi" ? el("span", { class: "core-glow", "aria-hidden": "true" }) : null,
  KICKER[n.id] ? el("p", { class: "kicker" }, KICKER[n.id]) : null,
  el("div", { class: "node-head" }, open, badgeEl),
  el("p", { class: "node-mono" }, n.line),
  nodeBody(n, results),
  el("p", { class: "node-foot" }, nodeFoot(n, st)));
  NODE_EL.set(n.id, box);
  return box;
}

// The drawing: computed from the boxes' rendered positions, redrawn on resize. Below STACKED (same breakpoint as
// style.css) the boxes stack in one column and the drawing is hidden.
const STACKED = "(max-width: 899px)";
const P = (v) => Math.round(v * 10) / 10;
const pt = (x, y) => ({ x, y });
const MOTION = !!(window.CSS && CSS.supports && CSS.supports("offset-path", "path('M0 0L1 1')"));
let ROUTES = []; // [{id, kind, ds:[path d], from, to, dots, label, labels, badge}] as drawn, for highlighting and particles
// The main cycle, clockwise: [anchor (a node id, or a waypoint that keeps the loop off a box), the route that starts
// there, its label]. The premise checker sits above the loop, not on it: on the science boards it is not a gate.
const RING = [
  ["scouts", "r_propose", "propose"], ["@top", null, null], ["simulator", "r_results", "results"],
  ["rules", "r_ch1", "challenge"], ["llm", "r_ch2", null], ["classical", "r_ch3", null],
  ["librarian", "r_evidence", "evidence"], ["trust", "r_status", "trusted only"], ["@bl", null, null],
  ["ranker", "r_read", "shortlist · board"], // the Scouts read the board, not the ranking: loop-back 5 is the real path
];
const RING_ENDS = { r_propose: ["scouts", "simulator"], r_results: ["simulator", "rules"], r_ch1: ["rules", "llm"],
  r_ch2: ["llm", "classical"], r_ch3: ["classical", "librarian"], r_evidence: ["librarian", "trust"],
  r_status: ["trust", "ranker"], r_read: ["ranker", "scouts"] };

function vpath(x1, y1, x2, y2) {
  if (Math.abs(x1 - x2) < 1.5) return `M${P(x1)} ${P(y1)}V${P(y2)}`;
  const ym = (y1 + y2) / 2;
  return `M${P(x1)} ${P(y1)}C${P(x1)} ${P(ym)} ${P(x2)} ${P(ym)} ${P(x2)} ${P(y2)}`;
}
const cub = (q) => `C${P(q.c1.x)} ${P(q.c1.y)} ${P(q.c2.x)} ${P(q.c2.y)} ${P(q.to.x)} ${P(q.to.y)}`;
const curve = (a, c1, c2, b) => `M${P(a.x)} ${P(a.y)}${cub({ c1, c2, to: b })}`;
const arcOut = (a, b, o) => curve(a, pt(a.x + o, a.y), pt(b.x + o, b.y), b); // o < 0 bulges left
function bez(q, t) {
  const u = 1 - t;
  const f = (a, b, c, d) => u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d;
  return pt(f(q.from.x, q.c1.x, q.c2.x, q.to.x), f(q.from.y, q.c1.y, q.c2.y, q.to.y));
}
/** A closed Catmull-Rom spline through the anchors, as one cubic piece per pair of neighbours. */
function catmull(A) {
  const n = A.length;
  return A.map((p1, i) => {
    const p0 = A[(i - 1 + n) % n];
    const p2 = A[(i + 1) % n];
    const p3 = A[(i + 2) % n];
    return { from: p1, to: p2, c1: pt(p1.x + (p2.x - p0.x) / 6, p1.y + (p2.y - p0.y) / 6),
             c2: pt(p2.x - (p3.x - p1.x) / 6, p2.y - (p3.y - p1.y) / 6) };
  });
}

/** Every line of the diagram, from the boxes' rectangles R (diagram coordinates), its size and the stage gutter. */
function geometry(R, W, H, gut) {
  const routes = [];
  const add = (id, kind, ds, extra = {}) => {
    const list = ds.filter(Boolean);
    if (list.length) routes.push({ id, kind, ds: list, ...extra });
  };
  const inside = (p) => Object.values(R).some((r) => p.x > r.x - 6 && p.x < r.r + 6 && p.y > r.y - 6 && p.y < r.b + 6);
  const core = R.alchemi;
  const sim = R.simulator;
  const way = {
    "@top": R.premise && core ? pt(R.premise.cx, (R.premise.b + core.y) / 2) : null,
    "@bl": R.worker && R.adjudicator ? pt((R.worker.r + R.adjudicator.x) / 2, R.worker.cy) : null,
  };
  // the main cycle
  const anchors = RING.map(([k, id, label]) => ({ p: k.startsWith("@") ? way[k] : R[k] ? pt(R[k].cx, R[k].cy) : null, id, label }))
    .filter((a) => a.p);
  let full = null;
  if (anchors.length >= 3) {
    const pieces = catmull(anchors.map((a) => a.p));
    full = `M${P(anchors[0].p.x)} ${P(anchors[0].p.y)}${pieces.map(cub).join("")}Z`;
    const byId = new Map();
    let cur = null;
    pieces.forEach((q, i) => {
      if (anchors[i].id) cur = anchors[i];
      if (!cur) return;
      if (!byId.has(cur.id)) byId.set(cur.id, { label: cur.label, pieces: [] });
      byId.get(cur.id).pieces.push(q);
    });
    for (const [id, r] of byId) {
      let lab = id === "r_propose" ? way["@top"] : null;
      if (r.label) {
        for (const q of r.pieces) {
          for (const t of [0.5, 0.42, 0.58, 0.34, 0.66]) {
            const p = bez(q, t);
            if (!lab && !inside(p)) lab = p;
          }
        }
      }
      const [from, to] = RING_ENDS[id] || [];
      add(id, "ring", [`M${P(r.pieces[0].from.x)} ${P(r.pieces[0].from.y)}${r.pieces.map(cub).join("")}`],
        { from, to, label: lab && r.label ? { x: lab.x, y: lab.y, text: r.label } : null });
    }
  }
  // the spokes: jobs into ALCHEMI, its output back to the Simulator
  let back = null;
  if (sim && core) {
    const a = pt(sim.x + sim.w * 0.3, sim.b);
    const b = pt(core.r - core.w * 0.24, core.y);
    const dy = (b.y - a.y) * 0.6;
    const q = { from: a, c1: pt(a.x, a.y + dy), c2: pt(b.x, b.y - dy), to: b };
    const lp = bez(q, 0.3);
    add("s_jobs", "spoke", [curve(a, q.c1, q.c2, b)], { from: "simulator", to: "alchemi", dots: [[b.x, b.y]],
      label: { x: lp.x, y: lp.y, text: "simulate" } });
    const gx = (core.r + sim.x) / 2;
    const a2 = pt(core.r, core.y + Math.min(34, core.h * 0.16));
    const b2 = pt(sim.x, sim.b - Math.min(14, sim.h * 0.1));
    back = { from: a2, c1: pt(gx + (sim.x - core.r) * 0.3, a2.y), c2: pt(gx - (sim.x - core.r) * 0.3, b2.y), to: b2 };
    add("s_back", "spoke", [curve(a2, back.c1, back.c2, b2)], { from: "alchemi", to: "simulator", dots: [[b2.x, b2.y]] });
  }
  // the loop-backs (G.loops), each a numbered arc
  const o = Math.max(26, gut * 0.6);
  const loop = (id, ds, at, extra = {}) => {
    const lp = (G.loops || []).find((x) => x.id === id);
    if (lp) add(`l_${id}`, "loop", ds, { from: lp.from, to: lp.to, badge: at ? { ...at, n: lp.n, text: lp.label } : null, ...extra });
  };
  if (sim) {
    const a = pt(sim.r, sim.y + sim.h * 0.18);
    const b = pt(sim.r, sim.y + sim.h * 0.46);
    loop("retry", [arcOut(a, b, o)], pt(a.x + o * 0.75, (a.y + b.y) / 2));
  }
  if (sim && R.rules) {
    const a = pt(R.rules.r, R.rules.cy);
    const b = pt(sim.r, sim.y + sim.h * 0.8);
    loop("rerun", [arcOut(a, b, o)], pt(a.x + o * 0.75, (a.y + b.y) / 2), { dots: [[b.x, b.y]] });
  }
  if (R.adjudicator) {
    const adj = R.adjudicator;
    const ds = [];
    let at = null;
    if (R.llm) {
      const a = pt(R.llm.x, R.llm.cy);
      const b = pt(adj.r, adj.y + adj.h * 0.62);
      const k = (a.x - b.x) * 0.5;
      ds.push(curve(a, pt(a.x - k, a.y), pt(b.x + k, b.y), b));
      at = pt((a.x + b.x) / 2, (a.y + b.y) / 2);
    }
    if (R.rules) {
      const a = pt(R.rules.x, R.rules.y + R.rules.h * 0.78);
      const b = pt(adj.r, adj.y + adj.h * 0.3);
      const k = (a.x - b.x) * 0.5;
      ds.push(curve(a, pt(a.x - k, a.y), pt(b.x + k, b.y), b));
    }
    if (R.trust) ds.push(`M${P(adj.cx)} ${P(adj.b)}V${P(R.trust.y)}`);
    loop("ruling", ds, at, { dots: R.trust ? [[adj.cx, R.trust.y]] : [] });
  }
  if (R.librarian && R.classical) {
    const a = pt(R.librarian.r, R.librarian.y + R.librarian.h * 0.4);
    const b = pt(R.classical.r, R.classical.y + R.classical.h * 0.72);
    loop("calibrate", [arcOut(a, b, o)], pt(a.x + o * 0.75, (a.y + b.y) / 2), { dots: [[b.x, b.y]] });
  }
  if (R.board && R.scouts) {
    const a = pt(R.board.x, R.board.cy);
    const b = pt(R.scouts.x, R.scouts.y + R.scouts.h * 0.62);
    loop("followup", [arcOut(a, b, -o)], pt(Math.min(a.x, b.x) - o * 0.75, (a.y + b.y) / 2), { dots: [[b.x, b.y]] });
  }
  if (back) loop("gate", [curve(back.from, back.c1, back.c2, back.to)], bez(back, 0.2));
  // people and the chemistry lead: dotted lines from files they edit
  const hu = R.humans;
  const ch = R.chemlead;
  if (hu && R.scouts) {
    add("e_seed", "config", [vpath(hu.cx, hu.b, R.scouts.cx, R.scouts.y)], { from: "humans", to: "scouts",
      dots: [[R.scouts.cx, R.scouts.y]], label: { x: Math.max(hu.cx, R.scouts.cx) + 8, y: (hu.b + R.scouts.y) / 2, text: "seeds · budgets", anchor: "start" } });
  }
  if (hu && ch) {
    const y = (Math.max(hu.y, ch.y) + Math.min(hu.b, ch.b)) / 2;
    add("e_designate", "config", [`M${P(hu.r)} ${P(y)}H${P(ch.x)}`], { from: "humans", to: "chemlead", dots: [[ch.x, y]],
      label: { x: (hu.r + ch.x) / 2, y: y - 11, text: "designates" } });
  }
  if (ch) {
    const rx = W - 12;
    const lx = 12;
    const yb = H - 16;
    const head = `M${P(ch.r)} ${P(ch.cy)}H${P(rx)}`;
    if (R.classical) {
      const y = R.classical.y + R.classical.h * 0.24;
      add("e_rules_gate", "rules", [`${head}V${P(y)}H${P(R.classical.r)}`], { from: "chemlead", to: "classical",
        dots: [[R.classical.r, y]], label: { x: rx - 6, y: y - 10, text: "gate", anchor: "end" },
        labels: [{ x: rx - 6, y: ch.cy - 10, text: "rules", anchor: "end" }] });
    }
    if (R.trust) {
      add("e_rules_trust", "rules", [`${head}V${P(yb)}H${P(R.trust.cx)}V${P(R.trust.b)}`], { from: "chemlead", to: "trust",
        dots: [[R.trust.cx, R.trust.b]], label: { x: R.trust.cx + 8, y: yb - 10, text: "tolerances", anchor: "start" } });
    }
    if (R.ranker) {
      add("e_rules_window", "rules", [`${head}V${P(yb)}H${P(lx)}V${P(R.ranker.cy)}H${P(R.ranker.x)}`], { from: "chemlead",
        to: "ranker", dots: [[R.ranker.x, R.ranker.cy]], label: { x: lx + 6, y: R.ranker.cy - 10, text: "window", anchor: "start" } });
    }
  }
  if (R.premise && way["@top"]) {
    add("premise_tick", "advise", [`M${P(R.premise.cx)} ${P(R.premise.b)}V${P(way["@top"].y - 8)}`], { from: "premise", to: "scouts" });
  }
  if (R.board && R.trust) {
    const y = R.board.cy;
    add("b_snapshot", "flow", [`M${P(R.board.r)} ${P(y)}H${P(R.trust.x)}`], { from: "board", to: "trust", dots: [[R.trust.x, y]],
      label: { x: (R.board.r + R.trust.x) / 2, y: y - 11, text: "snapshot" } });
  }
  return { routes, full };
}

// Slow, calm motion (Madhan, 2026-11-01: "the loop is running very fast"): every particle takes MOTION_SLOW times
// longer, delays scale with it so the spacing between dots is kept. Keep in step with the CSS durations in style.css.
const MOTION_SLOW = 3;

function particle(d, cls, seconds, delay) {
  const p = el("span", { class: `pt ${cls}` });
  p.style.offsetPath = `path("${d}")`; // CSSOM: allowed under the server's CSP
  p.style.animationDuration = `${seconds * MOTION_SLOW}s`;
  p.style.animationDelay = `${delay * MOTION_SLOW}s`;
  return p;
}

function drawEdges() {
  const host = document.getElementById("diagram");
  const layer = host && host.querySelector(".dsvg");
  const plx = host && host.querySelector(".plx");
  if (!layer || !G) return;
  layer.replaceChildren();
  if (plx) plx.replaceChildren();
  ROUTES = [];
  if (!host.getClientRects().length || window.matchMedia(STACKED).matches) return;
  const base = host.getBoundingClientRect();
  if (!base.width) return;
  const R = {};
  for (const [id, node] of NODE_EL) {
    const r = node.getBoundingClientRect();
    if (!r.width) continue;
    const x = r.left - base.left;
    const y = r.top - base.top;
    R[id] = { x, y, w: r.width, h: r.height, r: x + r.width, b: y + r.height, cx: x + r.width / 2, cy: y + r.height / 2 };
  }
  const stage = host.querySelector(".stage");
  const gut = (stage && parseFloat(window.getComputedStyle(stage).paddingLeft)) || 64;
  const W = base.width;
  const H = base.height;
  layer.setAttribute("viewBox", `0 0 ${P(W)} ${P(H)}`);
  const geo = geometry(R, W, H, gut);
  ROUTES = geo.routes;
  const k = R.alchemi;
  if (k) { // orbit art around the engine
    for (const [dx, dy, cls] of [[26, 22, "o1"], [54, 46, "o2"]]) {
      layer.append(svg("ellipse", { class: `orbit-art ${cls}`, cx: P(k.cx), cy: P(k.cy), rx: P(k.w / 2 + dx), ry: P(k.h / 2 + dy) }));
    }
  }
  if (geo.full) layer.append(svg("path", { class: "ring-glow", d: geo.full }));
  for (const r of ROUTES) {
    const g = svg("g", { class: `route ${r.kind}`, "data-route": r.id });
    for (const d of r.ds) g.append(svg("path", { d }));
    for (const [x, y] of r.dots || []) g.append(svg("circle", { class: "end", cx: P(x), cy: P(y), r: 2.6 }));
    for (const lab of [r.label, ...(r.labels || [])].filter(Boolean)) {
      const lg = svg("g", { class: "elabel" });
      lg.append(svg("text", { x: P(lab.x), y: P(lab.y), "text-anchor": lab.anchor || "middle", "dominant-baseline": "middle" },
        String(lab.text).toUpperCase()));
      g.append(lg);
    }
    if (r.badge) {
      const bg = svg("g", { class: "rbadge" });
      bg.append(svg("circle", { cx: P(r.badge.x), cy: P(r.badge.y), r: 9 }));
      bg.append(svg("text", { x: P(r.badge.x), y: P(r.badge.y + 0.5), "text-anchor": "middle", "dominant-baseline": "middle" },
        String(r.badge.n)));
      const lg = svg("g", { class: "elabel blabel" });
      lg.append(svg("text", { x: P(r.badge.x), y: P(r.badge.y + 19), "text-anchor": "middle", "dominant-baseline": "middle" },
        String(r.badge.text).toUpperCase()));
      bg.append(lg);
      g.append(bg);
    }
    layer.append(g);
  }
  for (const t of layer.querySelectorAll(".elabel text")) { // a panel-coloured plate under each label
    let bb = null;
    try { bb = t.getBBox(); } catch (err) { bb = null; } // not rendered (e.g. hidden mid-draw): label without a plate
    if (bb && bb.width) {
      t.parentNode.insertBefore(svg("rect", { x: P(bb.x - 4), y: P(bb.y - 2), width: P(bb.width + 8), height: P(bb.height + 4), rx: 3 }), t);
    }
  }
  if (plx && MOTION) { // particles: CSS offset-path + keyframes, off under prefers-reduced-motion (style.css)
    if (geo.full) for (let i = 0; i < 4; i += 1) plx.append(particle(geo.full, "ring", 18, -i * 4.5));
    for (const r of ROUTES) if (r.kind === "spoke") plx.append(particle(r.ds[0], "spoke", 3.2, r.id === "s_back" ? -1.6 : 0));
    if (k) {
      const rx = k.w / 2 + 54;
      const ry = k.h / 2 + 46;
      plx.append(particle(`M${P(k.cx - rx)} ${P(k.cy)}a${P(rx)} ${P(ry)} 0 1 0 ${P(2 * rx)} 0a${P(rx)} ${P(ry)} 0 1 0 ${P(-2 * rx)} 0`, "sat", 12, 0));
    }
  }
  applyHighlight(HOT);
}

function applyHighlight(step) {
  HOT = step || null;
  const host = document.getElementById("diagram");
  if (!host) return;
  host.classList.toggle("stepping", !!HOT);
  for (const [id, node] of NODE_EL) node.classList.toggle("hot", !!HOT && HOT.nodes.includes(id));
  for (const g of host.querySelectorAll(".route")) g.classList.toggle("hot", !!HOT && HOT.edges.includes(g.dataset.route));
  for (const li of document.querySelectorAll(".loop-item")) li.classList.toggle("hot", !!HOT && HOT.edges.includes(`l_${li.dataset.loop}`));
  const plx = host.querySelector(".plx");
  if (!plx) return;
  for (const p of plx.querySelectorAll(".pt.hotp")) p.remove();
  if (!HOT || !MOTION) return;
  for (const r of ROUTES) {
    if (HOT.edges.includes(r.id)) for (const delay of [0, -1.1]) plx.append(particle(r.ds[0], "hotp", 2.2, delay));
  }
}

// ------------------------------------------------------------------ agents: the replay (a real case from the board)
let CASES = []; // [{spec, board, result, steps} | {spec, missing: true}]
let CASE = null; // the selected replay
let PROOF = null;
let MODE = "idea"; // idea | step | detail (a box) | loop (a loop-back)
let STEP = 0;
let PLAYING = null;
let SELECTED = null;
const PLAY_MS = 4200;

function nodeOfAgent(agent) {
  if (!G || !agent) return null;
  const hit = G.nodes.find((n) => n.agents.includes(agent));
  if (hit) return hit.id;
  if (agent.startsWith("scout_")) return "scouts";
  if (agent.startsWith("simulator")) return "simulator";
  return null;
}

/** The replay cases (G.cases), each built from the exported lineage or marked missing (hidden, with a note). */
function buildCases() {
  const specs = (G && (G.cases || (G.case ? [G.case] : []))) || [];
  return specs.map((spec) => {
    const found = findCase(spec);
    return found ? { spec, ...found } : { spec, missing: true };
  });
}

function findCase(spec) {
  const all = scienceBoards();
  const named = all.filter((b) => b.name === spec.board);
  for (const b of [...named, ...all.filter((x) => !named.includes(x))]) {
    const hit = (b.results || []).find((r) => normName(r.candidate) === normName(spec.candidate) &&
      r.property === spec.property && (!spec.status || r.status === spec.status));
    if (hit && b.lineage && b.lineage[hit.id]) {
      const steps = caseSteps(b, hit);
      return steps.length ? { board: b, result: hit, steps } : null;
    }
  }
  return null;
}

const RING_INTO = { rules: "r_results", llm: "r_ch1", classical: "r_ch2", librarian: "r_ch3" };

function caseSteps(b, res) {
  const L = b.lineage;
  const R = L[res.id];
  const byTime = (x, y) => (x.timestamp < y.timestamp ? -1 : x.timestamp > y.timestamp ? 1 : 0);
  const kids = (a) => (a ? a.children || [] : []).map((id) => L[id]).filter(Boolean).sort(byTime);
  const ref = (a) => `results board ${b.name}: ${a.type} ${a.id.slice(0, 8)} by ${a.agent}, ${fmtTime(a.timestamp)}`;
  const steps = [];
  const cand = (R.parents || []).map((id) => L[id]).find((a) => a && a.type === "candidate");
  if (cand) {
    const need = (cand.needs || []).find((x) => x.property === res.property) || (cand.needs || [])[0];
    steps.push({ title: "A Scout proposes", nodes: ["scouts"], edges: ["r_propose"],
      text: `${cand.agent} posts the candidate ${cand.payload.name} (${familyLabel(cand.payload.family)}, ${cand.payload.tier} tier)` +
        `${need ? ` with a request: "${need.query}".` : "."}`,
      quote: need && need.rationale ? need.rationale : null, meta: fmtShort(cand.timestamp), cite: ref(cand) });
  }
  // the producer's latest claim on one of the result's parents, before the result
  const claim = Object.values(L).filter((a) => a.type === "need_claim" && a.agent === R.agent &&
    (R.parents || []).includes((a.payload || {}).target_artifact_id) && a.timestamp <= R.timestamp).sort(byTime).pop() || null;
  const premise = [...kids(cand), ...kids(R)].filter((a) => a.type === "check" && (a.payload || {}).check_type === "premise_check");
  const noGate = "On the science boards the premise checker advises; it does not gate the Simulator, which can claim a request before any premise check (in arm C it does gate the generalist worker).";
  const gateSrc = "coolorbit/sim/simulator_agent.py (SimulatorAgent.pending); scripts/run_arm_c.py (WorkerDispatcher)";
  if (premise.length) {
    const c = premise[0];
    const after = claim && c.timestamp > claim.timestamp ? fmtGap(parseIso(c.timestamp) - parseIso(claim.timestamp)) : null;
    steps.push({ title: "Premise check, in parallel", nodes: ["premise"], edges: ["premise_tick"],
      text: `${c.agent}: premise ${c.payload.verdict}` +
        `${premise.length > 1 ? ` (carried onto ${plural(premise.length - 1, "later artifact")})` : ""}.` +
        `${after ? ` It was posted ${after} after the Simulator had claimed the request.` : ""} ${noGate}`,
      quote: c.payload.note, meta: fmtShort(c.timestamp), cite: `${ref(c)}; ${gateSrc}` });
  } else {
    const gap = claim && cand ? fmtGap(parseIso(claim.timestamp) - parseIso(cand.timestamp)) : null;
    steps.push({ title: "Premise check, in parallel", nodes: ["premise"], edges: ["premise_tick"],
      text: "No premise check was posted on this request in the export." +
        `${gap ? ` The Simulator claimed it ${gap} after the Scout posted it.` : ""} ${noGate}`,
      cite: `${claim ? `${ref(claim)}; ` : ""}coolorbit/agents/premise.py (PremiseChecker); ${gateSrc}` });
  }
  const m = R.payload.method || {};
  const remote = m.remote && m.remote.host ? m.remote : null;
  steps.push({ title: "A Simulator claims it", nodes: ["simulator"], edges: ["r_propose"],
    text: `${R.agent} claims the request${claim ? ` at ${fmtShort(claim.timestamp)}` : ""}` + (remote
      ? `. Its GPU is remote: ${remote.host} over ${remote.transport || "SSH"}, the Brev H100 overflow` +
        `${remote.commit ? `, checked out at commit ${String(remote.commit).slice(0, 7)}` : ""}.`
      : ", and runs the job on the GB10's GPU.") +
      " No one assigned it: the claim on the board is the only coordination.",
    meta: claim ? fmtShort(claim.timestamp) : null,
    cite: `${claim ? ref(claim) : ref(R)}${remote ? "; scripts/run_remote_simulator.py and coolorbit/sim/ssh_runner.py (the remote Simulator)" : ""}` });
  const temps = Array.isArray(m.temperatures_K) && m.temperatures_K.length
    ? `${m.temperatures_K.length > 1 ? `${m.temperatures_K.length} temperatures` : "one temperature"} (${m.temperatures_K.map((t) => `${fmtExact(t)} K`).join(", ")})` : null;
  const how = [m.ensemble, finite(m.n_atoms) ? `${fmtExact(m.n_atoms)} atoms` : null, temps,
    finite(m.sim_length_ps) ? `${fmtExact(m.sim_length_ps)} ps` : null,
    finite(m.timestep_fs) ? `${fmtExact(m.timestep_fs)} fs steps` : null].filter(Boolean).join(", ");
  steps.push({ title: "NVIDIA ALCHEMI runs the physics", nodes: ["alchemi", "simulator"], edges: ["s_jobs"],
    text: `One GPU job on ${m.engine || "?"} with ${m.model || "?"}${/mpa-0/i.test(String(m.model)) ? " (MACE-MPA-0)" : ""}: ` +
      `${m.protocol || "protocol not recorded"}${m.protocol_version ? `, protocol v${m.protocol_version}` : ""}${how ? `; ${how}` : ""}.`,
    cite: ref(R) });
  const kj = res.units === "J/kg" ? ` (${fmt(res.value / 1000)} kJ/kg)` : "";
  const flags = (R.payload.flags || []).length ? ` It carries its own flag: ${R.payload.flags.join(", ")}.` : "";
  steps.push({ title: "A result comes back", nodes: ["simulator", "alchemi"], edges: ["s_back"],
    text: `Result ${res.id.slice(0, 8)}: ${propLabel(res.property).toLowerCase()} = ${fmtExact(res.value)}` +
      `${finite(res.uncertainty) ? ` ± ${fmtExact(res.uncertainty)}` : ""} ${unit(res.units)}${kj}, posted with its full method.${flags}`,
    meta: fmtShort(R.timestamp), cite: ref(R) });
  for (const c of kids(R)) {
    const node = nodeOfAgent(c.agent);
    const p = c.payload || {};
    if (c.type === "reference_value") {
      const v = p.value;
      steps.push({ title: "The Librarian brings a measurement", nodes: [node || "librarian"], edges: ["r_ch3"],
        text: v === null || v === undefined ? `${c.agent} finds no sourced measurement and posts a null reference.`
          : `${c.agent} posts the measured value: ${fmtExact(v)} ${unit(p.units)}${p.units === "J/kg" ? ` (${fmt(v / 1000)} kJ/kg)` : ""}.`,
        quote: p.source ? `Source: ${p.source}` : null, meta: fmtShort(c.timestamp), cite: ref(c) });
    } else if (c.type === "check" && p.check_type !== "premise_check") {
      const who = (NODE_BY_ID.get(node) || {}).name || c.agent;
      const fl = (p.flags || []).length ? ` [${p.flags.join(", ")}]` : "";
      const isAdj = node === "adjudicator";
      const isCls = p.check_type === "classical_md";
      const gated = isCls && (p.flags || []).includes("independent_method_unreliable");
      const calibrated = isCls && /within the tier1 tolerance/.test(String(p.note || ""));
      const mm = p.method || {};
      steps.push({ title: isAdj ? "The adjudicator rules" : `${who} tries to prove it wrong`,
        nodes: isAdj ? ["adjudicator", "rules", "llm"] : [node || "rules"],
        edges: isAdj ? ["l_ruling"] : isCls ? ["r_ch2", "l_calibrate"] : [RING_INTO[node] || "r_results"],
        text: `${who} (${c.agent}): ${String(p.check_type).replaceAll("_", " ")} ${p.verdict}${fl}` +
          `${finite(p.value) ? `, ${fmtExact(p.value)}${finite(p.uncertainty) ? ` ± ${fmtExact(p.uncertainty)}` : ""} ${unit(p.units)}` : ""}` +
          `${mm.engine ? ` with ${mm.engine}${mm.model ? ` / ${mm.model}` : ""}` : ""}.` +
          `${calibrated ? " Its potential first recovered the measured value within the tier1 tolerance, so it may count." : ""}` +
          `${gated ? " Its potential missed the measured value, so the gate posted it inconclusive: it can neither confirm nor flag." : ""}`,
        quote: p.note, meta: fmtShort(c.timestamp), cite: `${ref(c)}${isCls ? "; scripts/post_classical_checks.py (reliability gate)" : ""}` });
    } else if (c.type === "request" && /^scout_/.test(c.agent)) {
      const need = (c.needs || [])[0] || {};
      steps.push({ title: "Loop-back: the Scout follows up", nodes: ["scouts", "board"], edges: ["l_followup"],
        text: `${c.agent} reads the result on the board and asks for the next property: ${propLabel(need.property || "").toLowerCase() || "a follow-up"}` +
          `${finite(need.temperature_K) ? ` at ${fmtExact(need.temperature_K)} K, the simulated melting point` : ""}.`,
        quote: need.rationale || p.note, meta: fmtShort(c.timestamp), cite: `${ref(c)}; coolorbit/agents/scout.py (module docstring)` });
    } else if (c.type === "property_result" && p.property === res.property) {
      steps.push({ title: "Loop-back: a rerun", nodes: [node || "simulator"], edges: ["l_rerun"],
        text: `${c.agent} posts a rerun that supersedes it: ${fmtExact(p.value)} ${unit(p.units)}.`,
        meta: fmtShort(c.timestamp), cite: ref(c) });
    }
  }
  const g = groupOf(res.status);
  steps.push({ title: "The trust rule decides", nodes: ["trust", "board", "chemlead"], edges: ["r_evidence", "b_snapshot", "e_rules_trust"],
    text: `Status: ${String(STATUS_TEXT[res.status] || res.status).toUpperCase()}. No agent set it: the trust rule derived it from the evidence above, ` +
      "with the tolerances the chemistry lead set and the team froze before any comparison run." +
      `${g.key === "flagged" ? " A flagged result needs a rerun and is never ranked." : ""}`,
    list: (res.reasons || []).slice(0, 6), cite: `coolorbit/board/status.py; ${RULES_SOURCE()}; ${boardSource([b])}` });
  return steps;
}
const RULES_SOURCE = () => (CTX && CTX.trust_rule && CTX.trust_rule.tolerances[0] ? CTX.trust_rule.tolerances[0].source : "configs/trust_rules.json");

function stopPlay() {
  if (PLAYING) clearInterval(PLAYING);
  PLAYING = null;
}
function startStep(i, focusKey) {
  if (!CASE) return;
  MODE = "step";
  STEP = Math.max(0, Math.min(i, CASE.steps.length - 1));
  setPressed(null);
  applyHighlight(CASE.steps[STEP]);
  renderPanel(focusKey);
  const live = document.getElementById("case-live");
  if (live) live.textContent = `Step ${STEP + 1} of ${CASE.steps.length}: ${CASE.steps[STEP].title}. ${CASE.steps[STEP].text}`;
}
function nextStep(focusKey) {
  if (!CASE) return;
  if (MODE !== "step") { startStep(0, focusKey); return; }
  if (STEP >= CASE.steps.length - 1) { stopPlay(); renderPanel(focusKey); return; }
  startStep(STEP + 1, focusKey);
}
function togglePlay() {
  if (PLAYING) { stopPlay(); renderPanel("play"); return; }
  if (MODE !== "step" || STEP >= CASE.steps.length - 1) startStep(0, "play");
  PLAYING = setInterval(() => {
    if (!CASE || MODE !== "step" || STEP >= CASE.steps.length - 1) { stopPlay(); renderPanel(); return; }
    startStep(STEP + 1);
  }, PLAY_MS);
  renderPanel("play");
}
function exitStep() {
  stopPlay();
  MODE = "idea";
  applyHighlight(null);
  renderPanel("step");
}

function setPressed(id) {
  for (const [nid, node] of NODE_EL) {
    const b = node.querySelector(".node-open");
    if (b) b.setAttribute("aria-pressed", nid === id ? "true" : "false");
  }
}
function openDetail(id) {
  stopPlay();
  SELECTED = id;
  MODE = "detail";
  applyHighlight({ nodes: [id], edges: ROUTES.filter((r) => r.from === id || r.to === id).map((r) => r.id) });
  setPressed(id);
  renderPanel("back");
  const panel = document.getElementById("idea");
  if (panel && window.matchMedia("(max-width: 1319px)").matches) panel.scrollIntoView({ block: "start" });
}
const loopById = (id) => (G.loops || []).find((x) => x.id === id) || null;
const loopFocus = (lp) => ({ nodes: [...new Set([lp.from, lp.to, ...(lp.id === "ruling" ? ["rules"] : [])])], edges: [`l_${lp.id}`] });
function openLoop(id) {
  const lp = loopById(id);
  if (!lp) return;
  stopPlay();
  SELECTED = id;
  MODE = "loop";
  setPressed(null);
  applyHighlight(loopFocus(lp));
  renderPanel("back");
  const panel = document.getElementById("idea");
  if (panel && window.matchMedia("(max-width: 1319px)").matches) panel.scrollIntoView({ block: "start" });
}
function peekLoop(id) { // hover or focus on a loop-back in the list: light its arc while nothing else is shown
  if (MODE !== "idea") return;
  const lp = id ? loopById(id) : null;
  applyHighlight(lp ? loopFocus(lp) : null);
}
function closeDetail() {
  const back = SELECTED;
  const wasLoop = MODE === "loop";
  SELECTED = null;
  MODE = "idea";
  applyHighlight(null);
  setPressed(null);
  renderPanel();
  const preventScroll = window.matchMedia("(min-width: 1320px)").matches;
  const opener = wasLoop ? document.querySelector(`.loop-item[data-loop="${back}"] .loop-btn`)
    : (NODE_EL.get(back) || { querySelector: () => null }).querySelector(".node-open");
  if (opener) opener.focus({ preventScroll });
}

const btn = (label, key, onclick, cls = "", disabled = false) =>
  el("button", { type: "button", class: `btn ${cls}`.trim(), "data-key": key, onclick, disabled }, label);

function renderPanel(focusKey) {
  const host = document.getElementById("idea-content");
  if (!host || !G) return;
  const had = host.contains(document.activeElement) ? document.activeElement.getAttribute("data-key") : null;
  const view = MODE === "detail" && SELECTED ? detailView(SELECTED) : MODE === "loop" && SELECTED ? loopView(SELECTED)
    : MODE === "step" && CASE ? stepView() : ideaView();
  host.replaceChildren();
  addKids(host, view); // flattens nested lists and skips empty parts
  host.classList.toggle("detail", MODE === "detail" || MODE === "loop");
  const key = focusKey || had; // keep keyboard focus in the panel when its buttons are rebuilt (e.g. while playing)
  if (key) {
    const target = host.querySelector(`[data-key="${key}"]:not([disabled])`) || host.querySelector("button:not([disabled])");
    if (target) target.focus({ preventScroll: true });
  }
  if (document.getElementById("sources") && SOURCES.length) renderSources(); // the panel may cite new artifacts
}

function ideaView() {
  const idea = G.idea;
  const avail = CASES.filter((c) => !c.missing);
  const missing = CASES.filter((c) => c.missing);
  const pick = (c) => {
    stopPlay();
    CASE = c;
    renderPanel(`case-${c.spec.id}`);
  };
  return [
    el("p", { class: "eyebrow" }, "The idea"),
    el("h2", { id: "idea-h" }, idea.headline),
    el("div", { class: "idea-points" }, idea.points.map((p) => el("p", {}, el("strong", {}, p.lead), " ", p.text, cite(p.source)))),
    avail.length ? [
      el("p", { class: "pick-h" }, "Replay a real case from the board"),
      el("div", { class: "picks", role: "group", "aria-label": "Replay case" }, avail.map((c) =>
        el("button", { type: "button", class: "pick", "data-key": `case-${c.spec.id}`, "aria-pressed": CASE === c ? "true" : "false",
          onclick: () => pick(c) },
        el("span", { class: "pick-l" }, c.spec.label), statusBadge(c.result.status),
        el("span", { class: "pick-b" }, `${c.board.name} · ${c.result.agent}`)))),
      el("div", { class: "actions" },
        btn("Step through it", "step", () => nextStep("step"), "primary"),
        btn("Play", "play", togglePlay)),
    ] : el("p", { class: "fine" }, "No replay case is in this export, so there is nothing to step through."),
    missing.length ? el("p", { class: "fine" }, `Hidden because the export does not hold it: ${missing.map((c) => c.spec.label).join("; ")}.`) : null,
    el("p", { class: "hint" }, CASE ? `replays ${CASE.spec.label} from board ${CASE.board.name} · or click any box or loop-back for its details`
      : "click any box or loop-back for its details"),
  ];
}

function stepView() {
  const s = CASE.steps[STEP];
  const n = CASE.steps.length;
  const last = STEP >= n - 1;
  return [
    el("p", { class: "eyebrow" }, "Replay · a real case from the board"),
    el("h2", {}, CASE.spec.label, " ", statusBadge(CASE.result.status), cite(CASE.spec.source)),
    el("p", { class: "fine" }, `Built at load time from board ${CASE.board.name} (result ${CASE.result.id.slice(0, 8)}), in board order.`),
    el("div", { class: "case" },
      el("p", { class: "case-step" }, `Step ${STEP + 1} of ${n} · ${s.title}`),
      el("p", { class: "case-text" }, s.text, cite(s.cite)),
      s.quote ? el("blockquote", { class: "case-quote" }, clip(s.quote, 480)) : null,
      s.list && s.list.length ? el("ul", { class: "notes" }, s.list.map((x) => el("li", {}, clip(x, 240)))) : null,
      s.meta ? el("p", { class: "case-meta" }, s.meta) : null,
      el("div", { class: "dots", "aria-hidden": "true" }, CASE.steps.map((_, i) => el("span", { class: i <= STEP ? "on" : null })))),
    el("div", { class: "actions" },
      btn("Back", "prev", () => startStep(STEP - 1, "prev"), "small", STEP === 0),
      btn(last ? "Done" : "Next step", "next", () => (last ? exitStep() : nextStep("next")), "small primary"),
      btn(PLAYING ? "Pause" : "Play", "play", togglePlay, "small"),
      btn("Exit", "exit", exitStep, "small")),
  ];
}

function detailView(id) {
  const n = NODE_BY_ID.get(id);
  const hue = NODE_HUE[n.id] || "keep";
  const row = (G.rows || []).find((r) => r.id === n.row);
  const agents = nodeAgents(n);
  const st = agents.length ? agentState(agents) : null;
  const dd = (label, ...kids) => [el("dt", {}, label), el("dd", {}, ...kids)];
  const list = (items) => (items.length ? el("ul", {}, items.map((x) => el("li", {}, x))) : "none");
  const counts = () => {
    if (!st || !st.total) return st ? "none" : null;
    const types = {};
    for (const a of agents) {
      const s = ACT.agents.get(a);
      if (s) for (const [t, k] of Object.entries(s.byType)) types[t] = (types[t] || 0) + k;
    }
    return [Object.entries(types).sort((a, b) => b[1] - a[1]).map(([t, k]) => `${k} ${t.replaceAll("_", " ")}`).join(" · "),
      ` (science boards ${st.science}, arm C ${st.armC})`, cite(activitySource())];
  };
  const extra = [];
  if (n.id === "chemlead") {
    extra.push(...dd("What it decided, as the code uses it", el("ul", {}, (n.decisions || []).map((d) =>
      el("li", {}, d.text, el("span", { class: "muted" }, ` → ${d.where}`), cite(d.source))))));
  }
  if (n.gates) extra.push(...dd("Does it gate?", n.gates.text, cite(n.gates.source)));
  if (n.id === "simulator") {
    extra.push(...dd("The Simulators on the boards", agents.length ? el("ul", {}, agents.map((a) => {
      const s = ACT.agents.get(a);
      return el("li", {}, el("code", {}, a), s ? `: ${plural(s.total, "post")}` + (s.remote ? `; results ran on ${s.remote} (remote)`
        : s.local ? "; results ran on the GB10" : "; no results") : ": no posts", cite(activitySource()));
    })) : "none", n.remote ? el("p", { class: "fine" }, n.remote.text, cite(n.remote.source)) : null));
  }
  if (n.id === "alchemi" && (n.hosts || []).length) {
    extra.push(...dd("Where it runs", el("ul", {}, n.hosts.map((h) => el("li", {}, h.text, cite(h.source))))));
  }
  if (n.id === "alchemi") {
    extra.push(...dd("What our code imports from nvalchemi", el("ul", {}, (n.imports || []).map((i) =>
      el("li", {}, el("code", {}, i.name), `: ${i.what} `, el("span", { class: "muted" }, i.module), cite(i.source))))));
    extra.push(...dd("The protocol the agents run on it", el("ul", {}, (n.protocol || []).map((p) => el("li", {}, p.text, cite(p.source))))));
  }
  if (n.id === "scouts") {
    extra.push(...dd("The Scouts", el("ul", {}, (n.scouts || []).map((s) => {
      const a = ACT.agents.get(s.agent);
      return el("li", {}, el("code", {}, s.agent), `: ${s.families.map(familyLabel).join(", ") || "comparison tasks no family owns"}`,
        s.enabled ? "" : " (disabled)", finite(s.budget) ? `; budget ${s.budget}` : "", `; ${plural(a ? a.total : 0, "post")}`, cite(s.source));
    }))));
  }
  if (n.id === "trust" && CTX.trust_rule) {
    const t = CTX.trust_rule;
    extra.push(...dd("Trusted only if", el("ol", { class: "rules" }, t.rules.map((r) => el("li", {}, r))), cite(t.source)));
  }
  if (n.id === "ranker") {
    const ranked = scienceBoards().filter((b) => b.ranking);
    const trusted = countGroups(scienceBoards().flatMap((b) => b.results)).trusted;
    extra.push(...dd("Shortlist", ranked.length
      ? ranked.map((b) => el("p", {}, `${plural(b.ranking.entries.length, "entry", "entries")} in ranking ${b.ranking.id.slice(0, 8)} on ${b.name}`, cite(boardSource([b]))))
      : [`No ranking on the science board: it posts only when the shortlist changes${trusted ? "" : ", and no result is trusted yet"}.`,
        cite(boardSource(scienceBoards()))]));
  }
  const latest = st && st.last;
  return [
    btn("← The idea", "back", closeDetail, "small back"),
    el("p", { class: `tagline h-${hue}` }, `${row ? `${row.label} · ` : ""}${n.tag} · ${n.role}`),
    el("h3", {}, n.name),
    st ? el("p", {}, el("span", { class: `nbadge ${st.key}` }, st.text), " ", st.sentence, cite(activitySource()))
      : el("p", {}, el("span", { class: `nbadge fixed ${FIXED_BADGE[n.id] || ""}` }, FIXED_BADGE[n.id] || ""), " ", nodeFoot(n, null), "."),
    el("p", {}, n.does, cite(n.source)),
    n.note ? el("p", { class: "note" }, n.note) : null,
    el("dl", {},
      ...dd(n.model ? "Model" : "Runs on", n.model ? [`${n.model.full_name} (${n.model.role.includes(" ") ? n.model.role : `role ${n.model.role}`}` +
        `${n.model.effort ? `, effort ${n.model.effort}` : ""})`, cite(n.model.source)] : n.engine || "code"),
      ...(agents.length ? dd("Posts on the board as", agents.map((a, i) => [i ? ", " : "", el("code", {}, a)])) : []),
      ...dd("Reads", list(n.inputs)),
      ...dd("Produces", list(n.outputs)),
      ...dd("Code", el("ul", {}, n.code.map((p) => el("li", {}, codeLink(p))))),
      ...(st ? dd("On the exported boards", counts()) : []),
      ...(latest ? dd("Latest post", `${latest.summary || latest.type}`, el("span", { class: "muted" }, ` · ${latest.type} ${latest.id.slice(0, 8)} on ${latest.board}, ${fmtShort(latest.timestamp)}`)) : []),
      ...extra),
  ];
}

// Where a clean-looking run hid a wrong assumption, and who in the loop caught it (context.py catches()).
const CATCH_BADGE = { ok: "✓", warn: "!", bad: "✕", pend: "○" };
function catchesSection() {
  const C = CTX && CTX.catches;
  if (!C || !(C.cases || []).length) return null;
  const part = (label, ...kids) => [el("dt", {}, label), el("dd", {}, ...kids)];
  return el("section", { class: "card catches", "aria-labelledby": "catch-h" },
    el("header", { class: "catch-head" },
      el("p", { class: "eyebrow" }, C.eyebrow),
      el("h3", { id: "catch-h" }, C.title),
      el("p", { class: "lede" }, C.lede, C.nvidia ? [" ", C.nvidia.text, cite(C.nvidia.source, C.nvidia.url)] : null)),
    el("ol", { class: "catch-list" }, C.cases.map((c, i) => el("li", { class: `catch t-${c.outcome.tone}` },
      el("p", { class: "catch-n" }, `Catch ${i + 1}`),
      el("h4", {}, c.title),
      el("dl", {},
        part("A single pass would ship", c.shipped.text, cite(c.shipped.source)),
        part("Caught by", el("strong", {}, c.caught.who), " ", c.caught.text, cite(c.caught.source)),
        part("Then", badge(c.outcome.tone, CATCH_BADGE[c.outcome.tone] || "○", c.outcome.label), " ", c.outcome.text,
          cite(c.outcome.source)))))));
}

function vsTable() {
  const rows = G.vs || [];
  if (!rows.length) return null;
  return el("section", { class: "card vs-card", "aria-labelledby": "vs-h" },
    el("h3", { id: "vs-h" }, "ALCHEMI vs the agents: who does what"),
    el("div", { class: "table-wrap" }, el("table", { class: "vs" },
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "What NVIDIA ALCHEMI gives us"), el("th", { scope: "col" }, "What the agents add"))),
      el("tbody", {}, rows.map((r) => el("tr", {},
        el("td", {}, r.alchemi ? [r.alchemi, r.metric ? [" ", el("span", { class: "metric" }, `${fmtExact(r.metric.value)} ${r.metric.units}`), cite(r.metric.source)] : null,
          el("span", { class: "src" }, r.alchemi_source)] : el("span", { class: "none" }, "Not ALCHEMI's job")),
        el("td", {}, r.agents, cite(r.agents_source), el("span", { class: "src" }, r.agents_source))))))));
}

function evidenceItem(e) {
  const v = e.of !== undefined && e.of !== null ? `${e.value} of ${e.of}`
    : `${fmtExact(e.value)}${finite(e.uncertainty) ? ` ± ${fmtExact(e.uncertainty)}` : ""} ${unit(e.units)}`;
  return el("li", {}, el("span", { class: "ev-v" }, v, cite(e.source)), el("span", { class: "ev-l" }, e.label));
}

function methodCard(m) {
  return el("article", { class: "card method" },
    el("h3", {}, m.title, cite(m.source)),
    el("p", {}, m.text),
    m.trap ? el("p", { class: "trap" }, el("strong", {}, "The trap. "), m.trap) : null,
    m.alloy ? el("p", { class: "fine" }, m.alloy) : null,
    m.limit ? el("p", { class: "fine" }, m.limit) : null,
    m.evidence && m.evidence.length ? el("ul", { class: "evidence" }, m.evidence.map(evidenceItem)) : null,
    m.rows ? classicalTable(m) : null,
    m.caveat ? el("p", { class: "fine" }, m.caveat) : null,
    m.path ? el("p", { class: "path" }, codeLink(m.path)) : null);
}

function renderAgents(results) {
  if (!G) {
    mount("agents-body", el("p", { class: "empty" }, "This export carries no agent graph. Re-run python -m dashboard.export."));
    return;
  }
  for (const n of G.nodes) NODE_BY_ID.set(n.id, n);
  PROOF = buildProof();
  CASES = buildCases();
  CASE = CASES.find((c) => !c.missing) || null;
  const nodesOf = (ids) => ids.map((id) => NODE_BY_ID.get(id)).filter(Boolean);
  const diagram = el("div", { class: "diagram", id: "diagram" },
    svg("svg", { class: "dsvg", "aria-hidden": "true", focusable: "false" }),
    el("div", { class: "plx", "aria-hidden": "true" }),
    el("p", { class: "band-cap" }, el("span", {}, "Steers"), "people and the chemistry lead set the rules"),
    el("div", { class: "steer", role: "group", "aria-label": "Steers" }, nodesOf(STEER_IDS).map((n) => renderNode(n, results))),
    el("p", { class: "band-cap" }, el("span", {}, "The loop"), "agents around NVIDIA ALCHEMI; the bottom row keeps the record"),
    el("div", { class: "stage", role: "group", "aria-label": "The loop of agents around NVIDIA ALCHEMI" },
      nodesOf(STAGE_IDS).map((n) => renderNode(n, results))));
  // The badge legend and the coordination note were cut (Madhan, 2026-11-01: "too much info"); each agent's badge
  // still explains itself on hover (its title).
  const protocols = (CTX.methods || []).filter((m) => m.id !== "classical" && m.id !== "alchemi");
  mount("agents-body",
    proofStrip(),
    el("div", { class: "agents-grid" },
      el("div", { class: "card diagram-card" }, diagram, loopsList()),
      el("aside", { class: "card idea", id: "idea", "aria-label": "The idea, the replay and box details" },
        el("div", { id: "idea-content" }),
        el("p", { class: "sr-only", id: "case-live", "aria-live": "polite" }))),
    catchesSection(),
    vsTable(),
    protocols.length ? [el("h3", { class: "sub-h" }, "The protocols the agents chose, and the hook they built"),
      el("div", { class: "methods" }, protocols.map(methodCard))] : null);
  renderPanel();
  const host = document.getElementById("diagram");
  let queued = false;
  const redraw = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; drawEdges(); });
  };
  if (window.ResizeObserver && host) new ResizeObserver(redraw).observe(host);
  window.addEventListener("resize", redraw);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || currentTab !== "agents") return;
    if (MODE === "detail" || MODE === "loop") closeDetail();
    else if (MODE === "step") exitStep();
  });
}

// ------------------------------------------------------------------ agents: "not a wrapper", counted from the boards
const HARNESS = /harness/i; // the eval harness seeds comparison tasks; it is not one of our agents

/** Counts over our collective's boards (the science boards and arm C's comparison boards; arms A and B are left out),
 *  from each artifact's type, producer, needs and payload. Nothing here is typed in by hand. */
function buildProof() {
  const P = { agents: new Set(), checks: 0, checkTypes: {}, rulings: 0, classical: 0, gateInconclusive: 0, retries: 0,
    rerunRequests: 0, rerunsPosted: 0, followups: 0, runGate: 0, recovered: 0, results: [], runs: new Set() };
  const adjudicators = new Set((NODE_BY_ID.get("adjudicator") || { agents: ["adjudicator"] }).agents);
  const GATE_STOPS = ["crystal_lost_in_prep", "liquid_not_prepared"];
  for (const e of DATA.index.boards) {
    const scope = !e.arm && !e.task_id ? "science" : e.arm === "C" ? "armC" : null;
    const b = scope ? DATA.boards[e.name] : null;
    if (!b) continue;
    if (scope === "armC" && e.run) P.runs.add(runLabel(e.run));
    P.results.push(...(b.results || []));
    const L = b.lineage || {};
    for (const a of Object.values(L)) {
      const p = a.payload || {};
      if (a.agent && !HARNESS.test(a.agent)) P.agents.add(a.agent);
      if (a.type === "check") {
        P.checks += 1;
        const t = p.check_type || "check";
        P.checkTypes[t] = (P.checkTypes[t] || 0) + 1;
        if (adjudicators.has(a.agent)) P.rulings += 1;
        if (t === "classical_md") {
          P.classical += 1;
          if (p.verdict === "inconclusive" && (p.flags || []).includes("independent_method_unreliable")) P.gateInconclusive += 1;
        }
        P.rerunRequests += (a.needs || []).filter((n) => n.artifact_type === "property_result").length;
      } else if (a.type === "run_report") {
        P.retries += (a.needs || []).filter((n) => n.artifact_type === "property_result" && Number(n.attempt) > 1).length;
        const d = p.details || {};
        if (GATE_STOPS.includes(d.failure) || (d.flags || []).some((f) => GATE_STOPS.includes(f))) P.runGate += 1;
      } else if (a.type === "property_result") {
        const prev = (a.parents || []).map((id) => L[id]);
        if (prev.some((x) => x && x.type === "property_result" && (x.payload || {}).property === p.property)) P.rerunsPosted += 1;
        if (/^Recovered result/.test(String(p.note || ""))) P.recovered += 1;
      } else if (a.type === "request" && /^scout_/.test(a.agent || "")
        && (a.parents || []).some((id) => L[id] && L[id].type === "property_result")) {
        P.followups += 1;
      }
    }
  }
  P.loopBacks = P.retries + P.rerunRequests + P.rulings + P.followups;
  return P;
}

function proofSource() {
  const sc = ACT.scopes;
  const runs = PROOF && PROOF.runs.size ? ` (run ${[...PROOF.runs].join(", ")})` : "";
  return `Results board export of ${fmtTime(DATA.index.exported_at)}: ${plural(sc.science.length, "science board")}` +
    ` (${sc.science.join(", ") || "none"}) and ${plural(sc.armC.length, "arm C comparison board")}${runs}; counted in` +
    " dashboard/app.js (buildProof) from each artifact's type, producer, needs and payload";
}

/** What each loop-back's live count is, for the list under the diagram and its detail view. */
function loopCount(lp) {
  const P = PROOF;
  if (!P || !lp) return null;
  switch (lp.count) {
    case "retries": return { n: P.retries, short: plural(P.retries, "retry request"), text: `${plural(P.retries, "retry request")} posted on failed run reports (attempt 2 or later).` };
    case "reruns": return { n: P.rerunRequests, short: `${plural(P.rerunRequests, "request")} · ${P.rerunsPosted} posted`,
      text: `${plural(P.rerunRequests, "rerun request")} carried by failing checks; ${plural(P.rerunsPosted, "rerun")} posted that supersede an earlier result.` };
    case "rulings": return { n: P.rulings, short: plural(P.rulings, "ruling"), text: `${plural(P.rulings, "adjudicator ruling")} posted.` };
    case "gate": return { n: P.gateInconclusive, short: `${P.gateInconclusive} of ${P.classical} gated`,
      text: `${plural(P.classical, "classical-MD check")} posted; the calibration gate made ${P.gateInconclusive} of them inconclusive.` };
    case "followups": return { n: P.followups, short: plural(P.followups, "follow-up"), text: `${plural(P.followups, "follow-up request")} posted by Scouts on a result.` };
    case "run_gate": return { n: P.runGate, short: `${P.runGate} stopped`, text: `${plural(P.runGate, "run")} stopped by the run gate before production.` };
    default: return null;
  }
}

function proofStrip() {
  const P = PROOF;
  const src = proofSource();
  const c = countGroups(P.results);
  const groups = new Map();
  for (const a of P.agents) {
    const id = nodeOfAgent(a);
    const label = id ? (NODE_BY_ID.get(id).role || id).toLowerCase() : "other";
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(a);
  }
  const chips = (items) => el("ul", { class: "chipset" }, items.filter(Boolean).map(([label, n, title]) => nchip(label, n, "", title)));
  const tile = (v, label, sub, tone) => el("div", { class: `ptile${tone ? ` ${tone}` : ""}` },
    el("div", { class: "pv" }, String(v), cite(src)), el("div", { class: "pl" }, label), sub);
  // One footnote at the end of the line: a footnote after each number read as part of it when copied ("19" + "54").
  const strong = (v) => el("strong", {}, String(v));
  const kinds = Object.entries(P.checkTypes).sort((x, y) => y[1] - x[1]).map(([t]) => t.replaceAll("_", " "));
  const trustedNames = [...new Set(P.results.filter((r) => groupOf(r.status).key === "trusted")
    .map((r) => `${r.candidate} ${propLabel(r.property).toLowerCase()}`))];
  const explain = (n, word, text) => el("li", {}, el("span", { class: "ex-n" }, String(n)), el("span", { class: "ex-w" }, word),
    el("span", { class: "ex-t" }, text));
  return el("section", { class: "card proof", "aria-labelledby": "proof-h" },
    el("div", { class: "proof-head" },
      el("p", { class: "eyebrow", id: "proof-h" }, "Not a wrapper"),
      el("p", { class: "contrast" },
        el("span", { class: "ours-line" }, "Cool Orbit: ", strong(P.agents.size), " agents, ", strong(P.checks), " checks, ",
          strong(P.loopBacks), " loop-backs → ", strong(c.trusted), ` trusted result${c.trusted === 1 ? "" : "s"}.`, cite(src)))),
    el("div", { class: "proof-explain" },
      el("p", { class: "ex-h" }, "What these numbers mean"),
      el("ul", {},
        explain(P.agents.size, P.agents.size === 1 ? "agent" : "agents",
          "separate programs, each with one job: propose a material, run the simulation, check it, or look up the measured value. "
          + "There is no boss; they coordinate only through a shared results board."),
        explain(P.checks, P.checks === 1 ? "check" : "checks",
          `times an agent tried to prove a result wrong${kinds.length ? ` (${listJoin(kinds.slice(0, 5))}${kinds.length > 5 ? ", and more" : ""})` : ""}.`),
        explain(P.loopBacks, P.loopBacks === 1 ? "loop-back" : "loop-backs",
          "times the work went round again instead of being shipped: a retry, a rerun request, a ruling on a dispute between referees, or a follow-up request."),
        explain(c.trusted, "trusted", `result${c.trusted === 1 ? "" : "s"} that survived every check${trustedNames.length ? `: ${listJoin(trustedNames)}` : ""}. `
          + "Everything else stays on the page as pending, flagged or rejected, on purpose."))),
    el("div", { class: "ptiles" },
      tile(P.agents.size, "agents posted on the boards",
        chips([...groups].sort((x, y) => y[1].length - x[1].length).map(([label, names]) => [label, names.length, names.join(", ")]))),
      tile(P.checks, "checks posted",
        chips(Object.entries(P.checkTypes).sort((x, y) => y[1] - x[1]).map(([t, n]) => [t.replaceAll("_", " "), n]))),
      tile(P.loopBacks, "loop-backs", chips([["retries", P.retries], ["rerun requests", P.rerunRequests],
        ["adjudicator rulings", P.rulings], ["follow-ups", P.followups], ["reruns posted", P.rerunsPosted, "results that supersede an earlier one (not added to the loop-backs)"]])),
      tile(P.runGate + P.gateInconclusive, "times a gate said no", chips([["run gate stopped a box", P.runGate],
        ["classical checks gated inconclusive", P.gateInconclusive]]), "warn"),
      tile(c.trusted, "trusted results", chips([["pending review", c.pending], ["flagged", c.flagged], ["rejected", c.rejected]]), "ok"),
      tile(P.recovered, "recovered results",
        el("p", { class: "ps" }, "the remote run finished after the runner gave up; posted from the same run folder"))),
    el("p", { class: "fine proof-foot" }, "Counted at load time from the board export", cite(src),
      ": our collective's boards only (science boards and arm C; arms A and B are left out), each artifact counted once by its type, producer, needs and payload. "
      + "Loop-backs = retry requests + rerun requests + adjudicator rulings + follow-up requests. The eval harness that seeds comparison tasks is not counted as an agent."));
}

function loopsList() {
  const loops = G.loops || [];
  if (!loops.length) return null;
  return el("section", { class: "loops", "aria-labelledby": "loops-h" },
    el("h3", { id: "loops-h", class: "loops-h" }, "Loop-backs: where the work goes round again, each one in our code"),
    el("ol", { class: "loop-list" }, loops.map((lp) => {
      const c = loopCount(lp);
      return el("li", { class: "loop-item", "data-loop": lp.id },
        el("button", { type: "button", class: "loop-btn", "aria-label": `Loop-back ${lp.n}: ${lp.title}. ${c ? c.text : ""} Show details.`,
          onclick: () => openLoop(lp.id), onpointerenter: () => peekLoop(lp.id), onpointerleave: () => peekLoop(null),
          onfocus: () => peekLoop(lp.id), onblur: () => peekLoop(null) },
        el("span", { class: "lnum", "aria-hidden": "true" }, String(lp.n)),
        el("span", { class: "lt" }, lp.title),
        c ? el("span", { class: "lc" }, c.short) : null),
        c ? cite(proofSource()) : null);
    })));
}

function loopView(id) {
  const lp = loopById(id);
  if (!lp) return [btn("← The idea", "back", closeDetail, "small back")];
  const c = loopCount(lp);
  const name = (nid) => (NODE_BY_ID.get(nid) || { name: nid }).name;
  const dd = (label, ...kids) => [el("dt", {}, label), el("dd", {}, ...kids)];
  return [
    btn("← The idea", "back", closeDetail, "small back"),
    el("p", { class: "tagline h-loop" }, `Loop-back ${lp.n} · ${lp.label}`),
    el("h3", {}, lp.title),
    el("p", {}, lp.text, cite(lp.source)),
    el("dl", {},
      ...dd("From → to", `${name(lp.from)} → ${name(lp.to)}`),
      ...dd("On the exported boards", c ? [c.text, cite(proofSource())] : "not counted"),
      ...dd("Code", el("ul", {}, [...new Set(lp.evidence.map((e) => e.path))].map((p) => el("li", {}, codeLink(p)))))),
  ];
}

// ------------------------------------------------------------------ results by trust level
function methodLine(m) {
  if (!m) return "";
  return [m.engine && m.model ? `${m.engine} · ${m.model}` : m.engine, m.protocol, m.ensemble,
    m.n_atoms ? `${m.n_atoms} atoms` : null, m.sim_length_ps ? `${m.sim_length_ps} ps` : null,
    m.seeds ? plural(m.seeds, "seed") : null].filter(Boolean).join(" · ");
}

function resultRow(r, tol, multi) {
  const sv = shown(r.value, r.units);
  const su = finite(r.uncertainty) ? shown(r.uncertainty, r.units).value : null;
  const ref = r.reference && finite(r.reference.value) ? r.reference : null;
  const rv = ref ? shown(ref.value, ref.units) : null;
  const err = ref && ref.units === r.units && ref.value ? (r.value - ref.value) / ref.value : null;
  const t = tol[r.property];
  const lim = t && finite(t.tier1_rel) ? t.tier1_rel : null;
  const prediction = r.tier === "discovery";
  const resultSrc = `results board ${r.board}: result ${r.id.slice(0, 8)} by ${r.agent}, ${fmtTime(r.timestamp)}`;
  return el("article", { class: "res", id: `res-${r.id}` },
    el("div", { class: "r-name" },
      el("div", { class: "cand" }, r.candidate,
        // the property next to the name: one material can have several results (Cu: melting point AND latent heat)
        el("span", { class: "cand-prop" }, propLabel(r.property)),
        prediction ? badge("pred", "◇", "PREDICTION") : null,
        r.synthetic ? badge("bad", "!", "SYNTHETIC") : null),
      el("p", { class: "fine" }, familyLabel(r.family),
        prediction ? " · nobody has measured it" : " · known material")),
    el("div", { "data-label": "Simulated" },
      el("span", { class: "big" }, `${fmt(sv.value, 4)}${su !== null ? ` ± ${fmt(su, 2)}` : ""} ${sv.units}`, cite(resultSrc)),
      el("p", { class: "fine" }, methodLine(r.method))),
    el("div", { "data-label": "Measured" },
      ref ? [el("span", { class: "big" }, `${fmt(rv.value, 5)} ${rv.units}`, cite(ref.source)),
        el("p", { class: "fine" }, ref.origin === "board" ? `posted by ${ref.agent}` : "calibration set; not on the board yet")]
        : el("span", { class: "muted" }, prediction ? "none: a prediction" : "no sourced value yet")),
    el("div", { "data-label": "Error" },
      err !== null ? [el("span", { class: "big" }, fmtPct(err, 1, true), cite("(simulated − measured) / measured, from the two values in this row")),
        lim !== null ? el("p", { class: "fine" }, `${Math.abs(err) <= lim ? "within" : "outside"} ±${fmtPct(lim)}`, cite(t.source)) : null]
        : el("span", { class: "muted" }, "–")),
    el("div", { "data-label": "Status" }, statusBadge(r.status),
      r.superseded_by.length ? el("p", { class: "fine" }, "superseded by a rerun") : null),
    r.reasons.length
      ? el("details", { class: "why" },
        el("summary", {}, `Why ${STATUS_TEXT[r.status] || r.status}: ${plural(r.reasons.length, "reason")} from the referees and the trust rule`),
        el("ul", {}, r.reasons.map((x) => el("li", {}, x))),
        r.flags.length ? el("p", { class: "fine" }, "Flags on the result: ", r.flags.join(", ")) : null,
        r.note ? el("p", { class: "fine" }, "Simulator's note: ", r.note) : null,
        el("p", { class: "fine mono" }, `${r.agent} · ${fmtTime(r.timestamp)} · ${r.id}${multi ? ` · ${r.board}` : ""}`))
      : el("p", { class: "fine why" }, "No checks posted yet."));
}

function shortlistBlock(boards) {
  const win = CTX && CTX.trust_rule && CTX.trust_rule.window;
  const winText = win ? [` inside the ${fmtExact(win.min)}–${fmtExact(win.max)} K operating window`, cite(win.source)] : "";
  const ranked = boards.filter((b) => b.ranking);
  if (!ranked.length) {
    return el("div", { class: "card shortlist" }, el("h3", {}, "Shortlist"),
      el("p", { class: "fine" }, "No ranking posted yet. The Ranker lists only trusted results", winText, "."));
  }
  return ranked.map((b) => {
    const r = b.ranking;
    return el("div", { class: "card shortlist" },
      el("h3", {}, "Shortlist", cite(`ranking ${r.id.slice(0, 8)} by ${r.agent} on board ${b.name}, ${fmtTime(r.timestamp)}`)),
      r.entries.length
        ? el("ol", {}, r.entries.map((e) => el("li", {}, el("strong", {}, e.candidate), ` · score ${fmt(e.score, 4)} `,
          e.untrusted_result_ids.length ? badge("warn", "!", "cites a result that is not trusted") : badge("ok", "✓", "every cited result trusted"),
          e.note ? el("div", { class: "fine" }, e.note) : null)))
        : el("p", {}, "Empty so far: no candidate has both a trusted transition temperature", winText, " and a trusted latent heat."),
      el("details", {}, el("summary", {}, "The Ranker's criteria and exclusions"), el("p", { class: "fine" }, r.criteria)));
  });
}

function waitingBlock(boards) {
  const props = boards.flatMap((b) => b.proposed);
  const needs = boards.reduce((n, b) => n + b.open_needs.length, 0);
  if (!props.length && !needs) return null;
  return el("div", { class: "waiting" },
    el("h3", {}, `Proposed, not simulated yet (${props.length})`, cite(boardSource(boards))),
    props.length ? el("ul", { class: "chips" }, props.map((c) => el("li", { class: "chip", title: `${familyLabel(c.family)} · ${c.tier} tier · by ${c.agent}` },
      c.name, c.tier === "discovery" ? " (prediction)" : ""))) : null,
    el("p", { class: "fine" }, `${plural(needs, "open request")} on the board, waiting for any agent to claim.`));
}

function classicalTable(m) {
  const tolText = (tol, u) => (tol ? (finite(tol.rel) ? `±${fmtPct(tol.rel)}` : `±${fmtExact(tol.abs)} ${u}`) : null);
  const tm = tolText(m.tolerance, "K");
  const tl = tolText(m.latent_tolerance, "J/kg");
  return [
    el("div", { class: "table-wrap" }, el("table", { class: "mini" },
      el("thead", {}, el("tr", {}, ["Check", "MEAM", "Measured", "Off by", "Used?"].map((h, i) =>
        el("th", { scope: "col", class: i > 0 && i < 4 ? "num" : null }, h)))),
      el("tbody", {}, m.rows.map((r) => el("tr", {},
        el("th", { scope: "row" }, r.label || r.element),
        el("td", { class: "num" }, r.meam ? [`${fmtExact(r.meam.value)}${finite(r.meam.uncertainty) ? ` ± ${fmtExact(r.meam.uncertainty)}` : ""} ${r.meam.units || "K"}`, cite(r.meam.source)] : "–"),
        el("td", { class: "num" }, r.measured ? [`${fmtExact(r.measured.value)} ${r.measured.units || "K"}`, cite(r.measured.source)] : "–"),
        el("td", { class: "num" }, r.rel_error ? [fmtPct(r.rel_error.value, 1, true), cite(r.rel_error.source)] : "–"),
        el("td", {},
          r.counts ? badge("ok", "✓", "counts")
            : r.status === "dropped" ? badge("bad", "✕", "dropped") : badge("warn", "!", "not counted"),
          r.note ? el("div", { class: "fine" }, r.note, cite(r.source)) : null)))))),
    tm ? el("p", { class: "fine" }, `A potential counts only within ${tm} of the measured melting point`,
      tl ? ` (and ${tl} of the measured latent heat)` : "", ".", cite(m.tolerance.source)) : null,
  ];
}

function trustCard(t) {
  const tolText = (rel, abs, units) => (finite(rel) ? `±${fmtPct(rel)}` : finite(abs) ? `±${fmtExact(abs)} ${unit(units)}` : "–");
  return el("article", { class: "card" },
    el("h3", {}, "The trust rule", cite(t.source)),
    el("p", {}, "A result is ", statusBadge("trusted"), " only if"),
    el("ol", { class: "rules" }, t.rules.map((r) => el("li", {}, r))),
    el("p", { class: "fine" }, t.also, cite(t.rejection_min_referees.source)),
    t.tolerances.length ? el("div", { class: "table-wrap" }, el("table", { class: "mini" },
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Tolerance"),
        el("th", { scope: "col", class: "num" }, "vs measurement"), el("th", { scope: "col", class: "num" }, "vs other method"))),
      el("tbody", {}, t.tolerances.map((x) => el("tr", {}, el("th", { scope: "row" }, x.label),
        el("td", { class: "num" }, tolText(x.tier1_rel, x.tier1_abs, x.units), cite(x.source)),
        el("td", { class: "num" }, tolText(x.agree_rel, x.agree_abs, x.units), cite(x.source))))))) : null,
    t.frozen && t.preregistration
      ? el("p", { class: "fine" }, "Frozen before any comparison run, in commit ", el("code", {}, t.preregistration.commit), ".",
        cite(t.preregistration.source))
      : el("p", { class: "fine t-warn" }, "Not frozen yet, so every status is provisional."),
    t.window ? el("p", { class: "fine" },
      `Shortlist window: the transition must fall within ${fmtExact(t.window.min)}–${fmtExact(t.window.max)} K.`,
      cite(t.window.source)) : null);
}

// The Results tab's answer to "what could replace paraffin?" (Madhan, 2026-11-01). Gallium leads on MEASURED, sourced
// data (the Overview's head-to-head and the burst inputs). It is not one of our simulations, so it never enters the
// trust groups below: only the trust rule fills those, from evidence on the board.
function replacementCard() {
  const hh = CTX && CTX.head_to_head;
  const mats = (CTX && CTX.burst && CTX.burst.materials) || [];
  const ga = mats.find((m) => /gallium/i.test(`${m.name || ""} ${m.label || ""}`));
  if (!hh || !Array.isArray(hh.rows) || !ga) return null;
  const wins = hh.rows.filter((r) => r.winner === "gallium");
  const losses = hh.rows.filter((r) => r.winner === "paraffin");
  const fact = (label, x, f) => (x && finite(x.value)
    ? el("div", { class: "rp-fact" }, el("span", { class: "rp-v" }, f(x), cite(x.source)), el("span", { class: "rp-l" }, label)) : null);
  const derived = ga.derived || {};
  return el("section", { class: "card replace", "aria-labelledby": "rp-h" },
    el("p", { class: "eyebrow" }, "The probable replacement for paraffin"),
    el("h3", { id: "rp-h" }, "Gallium", el("span", { class: "rp-lead" }, "leading candidate"),
      badge("ok", "✓", "measured data · sourced")),
    el("p", { class: "rp-score" }, el("strong", {}, String(wins.length)), ` of ${hh.rows.length} head-to-head comparisons won against paraffin`,
      cite(hh.source), losses.length ? ` (paraffin wins ${losses.length}).` : "."),
    el("div", { class: "rp-facts" },
      fact("melts at", (ga.inputs || {}).melting_point_K, (x) => `${fmtExact(x.value)} K`),
      fact("heat stored per kilogram", derived.latent_heat_kJ_per_kg, (x) => `${fmt(x.value)} kJ/kg`),
      fact("heat stored per litre", derived.latent_heat_kJ_per_L, (x) => `${fmt(x.value)} kJ/L`),
      fact("liquid thermal conductivity", (ga.inputs || {}).conductivity_liquid_W_mK, (x) => `${fmtExact(x.value)} W/m·K`)),
    wins.length ? el("ul", { class: "rp-wins", "aria-label": "Where gallium beats paraffin" }, wins.map((r) => el("li", {},
      r.aspect, r.ratio && finite(r.ratio.value) ? el("span", { class: "rp-x" }, fmtRatio(r.ratio.value)) : null, cite(r.gallium.source)))) : null,
    losses.length ? el("p", { class: "rp-trade" }, el("strong", {}, "The trade-offs: "),
      `paraffin still wins on ${listJoin(losses.map((r) => r.aspect.toLowerCase()))}.`, cite(hh.source)) : null,
    el("p", { class: "rp-trust" }, el("strong", {}, "Why gallium is not in the Trusted list below: "),
      "that list holds our own simulations, and a simulation is trusted only after a method audit, an agreeing independent "
      + "method and a match with the measured value. Gallium's numbers above are measurements (NIST, JANAF and the published "
      + "literature), so they need no simulation to be believed. Our force fields do not reproduce gallium yet; training one "
      + "that does (the ALCHEMI Toolkit supports fine-tuning) is the next step.",
      cite("data/calibration/tier1.json (gallium's measured values and their sources); data/nebius/metals/Ga/stability/RESULT.md; "
        + "docs/pitch/outline.md slide 8; nvalchemi-toolkit FineTuningStrategy")));
}

function renderResults(results, boards) {
  const multi = boards.length > 1;
  const tol = CTX ? Object.fromEntries(CTX.trust_rule.tolerances.map((t) => [t.property, t])) : {};
  const order = (a, b) => a.candidate.localeCompare(b.candidate) || a.property.localeCompare(b.property)
    || a.timestamp.localeCompare(b.timestamp);
  const head = el("div", { class: "res-head", "aria-hidden": "true" },
    el("span", {}, "Candidate"), el("span", {}, "Simulated"), el("span", {}, "Measured"), el("span", {}, "Error"), el("span", {}, "Status"));
  const groups = GROUPS.map((g) => {
    const rows = results.filter((r) => g.statuses.includes(r.status)).sort(order);
    return el("section", { class: "group", "aria-labelledby": `g-${g.key}` },
      el("div", { class: "group-head" },
        el("h3", { id: `g-${g.key}` }, badge(g.tone, g.icon, g.label), el("span", { class: "count" }, String(rows.length))),
        el("p", { class: "fine" }, g.blurb)),
      rows.length ? el("div", { class: "res-list" }, head.cloneNode(true), rows.map((r) => resultRow(r, tol, multi)))
        : el("p", { class: "empty" }, g.empty));
  });
  const classical = CTX ? (CTX.methods || []).find((m) => m.id === "classical") : null;
  mount("results-body",
    replacementCard(),
    el("h3", { class: "sub-h res-sims-h" }, "Our simulations, by trust level"),
    el("p", { class: "fine" }, `${plural(results.length, "simulated result")} from the results board`, cite(boardSource(boards)),
      ". Error = (simulated − measured) / measured; the tolerances are the pre-registered ones."),
    shortlistBlock(boards), groups, waitingBlock(boards),
    CTX ? el("div", { class: "two results-extra" },
      classical ? methodCard(classical) : null,
      trustCard(CTX.trust_rule)) : null);
}

// ------------------------------------------------------------------ comparison
const ARM_SHORT = { A: "A · Single agent", B: "B · Pipeline", C: "C · Our collective", D: "D · Homogeneous collective" };
const armName = (a) => ARM_SHORT[a] || `Arm ${a}`;
const VIEWS = [
  { key: "pre", field: "scorecard", summary: "pre_registered", label: "Pre-registered" },
  { key: "post", field: "scorecard_post_hoc", summary: "post_hoc", label: "Post-hoc rule (disclosed)" },
];
let SCORING = "pre";

function taskGrid(runName, field) {
  const grid = new Map();
  for (const e of DATA.index.boards) {
    if (e.run !== runName || !e.arm) continue;
    const b = DATA.boards[e.name];
    const card = b && (b[field] || b.scorecard);
    if (!card) continue;
    for (const o of card.outcomes) {
      if (!o.seeded) continue;
      if (!grid.has(o.task_id)) grid.set(o.task_id, {});
      const cell = grid.get(o.task_id);
      (cell[e.arm] = cell[e.arm] || []).push({ ...o, repeat: e.repeat, finished: e.finished });
    }
  }
  return grid;
}

function outcomeOf(o) {
  if (o.finished === false) return ["pend", "○", "running"];
  if (o.trap_outcome === "reported_reference" || o.disposition === "reported_reference") return ["warn", "!", "reported a measured value"];
  if (o.trap_outcome === "avoided") return ["ok", "✓", "avoided"];
  if (o.trap_outcome === "caught") return ["ok", "✓", "caught"];
  if (o.trap_outcome === "missed") return ["bad", "✕", "missed: silent error"];
  if (o.trap_outcome === "no_answer") return ["pend", "–", "no answer"];
  if (o.false_alarm) return ["warn", "!", "false alarm"];
  if (o.silent_error) return ["bad", "✕", "silent error"];
  if (o.disposition === "no_answer") return ["pend", "–", "no answer"];
  if (o.wrong === true) return ["warn", "!", "wrong, but doubted"];
  if (o.wrong === false) return ["ok", "✓", "correct"];
  return ["pend", "○", "answered, unscored"];
}

function outcomeChip(o, many) {
  const [tone, icon, text] = outcomeOf(o);
  const shipped = finite(o.shipped_value) ? `shipped ${fmt(o.shipped_value, 4)} ${unit(o.shipped_units)}` : "nothing shipped";
  const detail = o.finished === false ? "exported before this task finished (no run_record.json yet), so it is not scored"
    : [shipped, ...(o.reasons || [])].join("; ");
  const node = badge(tone, icon, `${many ? `r${o.repeat} ` : ""}${text}`);
  node.setAttribute("title", detail);
  return node;
}

function metricCard(run, arms, summ, key, title, sub, kind, view) {
  const vals = arms.map((a) => summ[a] && summ[a][key]).filter(Boolean);
  const max = kind === "rate" ? 1 : Math.max(kind === "count" ? 1 : 0.01, ...vals.map((m) => m.max));
  const rows = arms.map((a) => {
    const s = summ[a];
    const m = s && s[key];
    const label = armName(a);
    if (!m) {
      const why = s && s.repeats === 0 ? "no complete repeat yet" : "nothing of this kind was scored";
      return { label, total: 0, segments: [], display: "n/a", tip: [`${label}: n/a`, why], aria: `${label}: not available, ${why}` };
    }
    const f = kind === "count" ? (v) => fmt(v, 3) : (v) => fmtPct(v);
    const range = m.n > 1 ? ` (${f(m.min)}–${f(m.max)})` : "";
    return { label, total: m.mean, display: f(m.mean), segments: [{ value: m.mean, cls: a === "C" ? "accent" : "neutral" }],
             tip: [`${f(m.mean)}${range}`, label, `mean over ${plural(m.n, "complete repeat")} with a value`],
             aria: `${label}: ${f(m.mean)}${range}, ${plural(m.n, "repeat")}` };
  });
  const src = `coolorbit/eval/metrics.py over the boards of run ${run.run}: ${view.label.toLowerCase()} scoring, complete repeats only, mean over repeats`;
  return chartCard(title, sub, barChart({ title, rows, max, track: kind === "rate", valueAt: kind === "rate" ? "end" : "tip" }), src);
}

function repeatsTable(run, view) {
  const det = run.repeats || [];
  if (!det.length) return null;
  const score = (d) => d[view.key];
  const frac = (num, den, rate) => (den ? `${num} of ${den}${finite(rate) ? ` (${fmtPct(rate)})` : ""}` : "–");
  const src = `dashboard/export.py with coolorbit/eval/metrics.py over run ${run.run}: ${view.label.toLowerCase()} scoring of each repeat's finished tasks`;
  return el("div", { class: "card repeats-card" },
    el("h3", {}, "Repeats", cite(src)),
    el("p", { class: "fine" }, "A task counts as finished once its run_record.json exists. A repeat enters the charts only when every pre-registered task has finished; until then its partial numbers stay here."),
    el("div", { class: "table-wrap" }, el("table", { class: "mini" },
      el("thead", {}, el("tr", {}, ["Arm", "Repeat", "Tasks finished", "Status", "Trap catch", "False alarms", "Silent errors"]
        .concat(view.key === "post" ? ["Reported a measured value"] : [])
        .map((h, i) => el("th", { scope: "col", class: i >= 4 ? "num" : null }, h)))),
      el("tbody", {}, det.map((d) => {
        const s = score(d);
        const extra = [d.in_progress.length ? `${d.in_progress.length} running` : null,
          d.not_started.length ? `${d.not_started.length} not started` : null].filter(Boolean).join(", ");
        return el("tr", {},
          el("th", { scope: "row" }, armName(d.arm)),
          el("td", {}, `r${d.repeat}`),
          el("td", {}, `${d.tasks_finished} of ${d.tasks_expected}`, extra ? el("span", { class: "kind" }, extra) : null),
          el("td", {}, d.complete ? badge("ok", "✓", "complete") : badge("pend", "○", "in progress")),
          el("td", { class: "num" }, s ? frac(s.trap_avoided + s.trap_caught, s.trap_tasks, s.trap_catch_rate) : "–"),
          el("td", { class: "num" }, s ? frac(s.false_alarms, s.control_tasks, s.false_alarm_rate) : "–"),
          el("td", { class: "num" }, s ? String(s.silent_errors) : "–"),
          view.key === "post" ? el("td", { class: "num" }, s ? String(s.reported_reference) : "–") : null);
      })))));
}

function runBlock(run) {
  const tasks = Object.fromEntries((DATA.index.tasks || []).map((t) => [t.id, t]));
  const arms = Array.from(new Set(["A", "B", "C", ...Object.keys(run.arms)])).sort();
  const scored = (DATA.index.scored_task_ids || []).slice();
  const preregistered = DATA.index.task_set_frozen && DATA.index.rules_frozen;
  const prereg = CTX && CTX.trust_rule && CTX.trust_rule.preregistration;
  const det = run.repeats || [];
  const incomplete = det.filter((d) => !d.complete);
  const present = arms.filter((a) => run.arms[a]);
  const repeatsText = present.map((a) => {
    const done = det.filter((d) => d.arm === a && d.complete).length;
    return `${a}: ${run.arms[a].repeats}${run.repeats ? ` (${done} complete)` : ""}`;
  }).join(", ");
  const src = `coolorbit/eval/metrics.py over the boards of run ${run.run}`;

  const chips = el("div", { class: "chips" },
    preregistered ? badge("ok", "✓", "pre-registered") : badge("warn", "!", "not pre-registered"),
    incomplete.length ? badge("pend", "○", `${[...new Set(incomplete.map((d) => `r${d.repeat}`))].join(", ")} in progress`)
      : badge("ok", "✓", "every exported repeat complete"),
    run.synthetic ? badge("bad", "!", "SYNTHETIC") : null);
  const notes = el("div", { class: "run-notes" },
    run.synthetic ? el("div", { class: "banner bad", role: "alert" },
      "SYNTHETIC comparison built from demo boards with invented numbers. Not a result; do not cite.") : null,
    el("p", { class: "fine" }, preregistered
      ? ["The task set and the trust tolerances were frozen before this run", prereg ? [" (commit ", el("code", {}, prereg.commit), ")", cite(prereg.source)] : "", "."]
      : "The task set or the tolerances are not frozen, so these numbers are not pre-registered.",
    ` Repeats exported per arm: ${repeatsText || "none"}.`, cite(src),
    run.scoring ? " Charts: complete repeats only." : " This export has no per-repeat detail, so the charts mix in any repeat still running."));

  const views = VIEWS.map((v) => {
    const summ = run.scoring ? run.scoring[v.summary] : (v.key === "pre" ? run.arms : null);
    const grid = taskGrid(run.run, v.field);
    const taskIds = scored.length ? [...scored, ...[...grid.keys()].filter((t) => !scored.includes(t))] : [...grid.keys()];
    const metrics = summ && present.length ? el("div", { class: "metrics" },
      metricCard(run, arms, summ, "trap_catch_rate", "Trap catch rate", "Traps and ill-posed tasks avoided or caught. Higher is better.", "rate", v),
      metricCard(run, arms, summ, "false_alarm_rate", "False-alarm rate", "Well-posed controls refused or wrongly doubted. Lower is better.", "rate", v),
      metricCard(run, arms, summ, "silent_errors", "Silent errors", "Confident wrong answers shipped, per repeat. Lower is better.", "count", v),
      metricCard(run, arms, summ, "mean_abs_rel_error", "Mean relative error", "|shipped − reference| / reference, where a sourced reference exists. Lower is better.", "pct", v))
      : el("p", { class: "empty" }, summ ? "No arm has a scored repeat in this run yet." : "This export carries no post-hoc scoring; re-run python -m dashboard.export.");
    const gridTable = taskIds.length ? el("div", { class: "card" },
      el("h3", {}, "Task by task", cite(`${src}: ${v.label.toLowerCase()} scoring of each task board`)),
      el("p", { class: "fine" }, "Hover a cell for what was shipped and why it was scored that way."),
      el("div", { class: "table-wrap" }, el("table", { class: "grid-table" },
        el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Task"), arms.map((a) => el("th", { scope: "col" }, armName(a))))),
        el("tbody", {}, taskIds.map((id) => {
          const t = tasks[id];
          const kind = !t ? "" : t.kind === "trap" ? `trap: ${String(t.trap || "").replaceAll("_", " ")}`
            : t.kind === "ill_posed" ? "ill-posed: should be refused" : "well-posed control";
          return el("tr", {},
            el("th", { scope: "row", title: t ? t.prompt : null },
              t ? `${t.material} · ${propLabel(t.property)}` : el("code", {}, id),
              el("span", { class: "kind" }, kind, t && t.origin === "nvidia_blog" ? " · NVIDIA blog task" : "",
                scored.length && !scored.includes(id) ? " · not pre-registered" : "")),
            arms.map((a) => {
              const os = ((grid.get(id) || {})[a] || []).slice().sort((x, y) => (x.repeat || 0) - (y.repeat || 0));
              return el("td", {}, os.length ? el("div", { class: "outcomes" }, os.map((o) => outcomeChip(o, os.length > 1)))
                : el("span", { class: "muted" }, "not run yet"));
            }));
        })))),
      el("p", { class: "fine" }, "Avoided or caught = the trap did not reach a confident answer. Missed = a confident wrong answer (a silent error). "
        + "False alarm = a correct control refused or doubted. No answer counts against the catch rate."
        + (v.key === "post" ? " Reported a measured value = shipped a measurement instead of a simulation (post-hoc rule): no error, and on a trap it is not a catch." : ""))) : null;
    return el("div", { class: `sv sv-${v.key}`, hidden: SCORING !== v.key },
      repeatsTable(run, v), metrics, gridTable);
  });

  const costTable = present.length ? el("div", { class: "card" }, el("h3", {}, "Cost", cite("costs.jsonl on each board, summed by coolorbit/eval/metrics.py over every exported repeat")),
    el("div", { class: "table-wrap" }, el("table", { class: "mini" },
      el("thead", {}, el("tr", {}, ["Arm", "Input tokens", "Output tokens", "GPU-hours"].map((h, i) =>
        el("th", { scope: "col", class: i > 0 ? "num" : null }, h)))),
      el("tbody", {}, present.map((a) => {
        const s = run.arms[a];
        const rec = s.cost_recorded_on_all_boards;
        const cell = (v, f) => el("td", { class: "num" }, rec ? f(v) : el("span", { class: "t-warn" }, "not recorded"));
        return el("tr", {}, el("th", { scope: "row" }, armName(a)), cell(s.total_input_tokens, fmtCompact),
          cell(s.total_output_tokens, fmtCompact), cell(s.total_gpu_hours, (v) => fmt(v, 3)));
      })))),
    el("p", { class: "fine" }, "Every exported repeat, running ones included. Costs missing on any board show as not recorded, never as zero.")) : null;

  return el("section", { class: "run", "aria-label": `Comparison run ${runLabel(run.run)}` },
    el("div", { class: "run-head" }, el("h3", {}, `Run ${runLabel(run.run)}`), chips),
    notes,
    present.length ? el("ul", { class: "legend" },
      el("li", {}, el("span", { class: "sw accent", "aria-hidden": "true" }), "C · our collective"),
      el("li", {}, el("span", { class: "sw neutral", "aria-hidden": "true" }), "the other arms")) : null,
    views, costTable);
}

function setScoring(key) {
  SCORING = key;
  for (const v of VIEWS) {
    for (const node of document.querySelectorAll(`.sv-${v.key}`)) node.hidden = key !== v.key;
  }
  for (const b of document.querySelectorAll(".seg button")) b.setAttribute("aria-pressed", b.dataset.view === key ? "true" : "false");
}

function renderCompare() {
  const runs = DATA.index.comparison || [];
  const excluded = DATA.index.excluded_from_comparison || [];
  const setup = CTX && CTX.comparison_setup;
  const intro = setup ? [
    el("div", { class: "arms" }, Object.entries(setup.arms).filter(([k]) => k !== "D" || runs.some((r) => r.arms.D))
      .map(([k, text]) => el("div", { class: `card arm${k === "C" ? " ours" : ""}` }, el("p", { class: "k" }, `Arm ${k}`), el("p", {}, text)))),
    el("p", { class: "fine fair" }, setup.fairness,
      setup.baseline_model ? ` Arms A and B run on ${setup.baseline_model}.` : "",
      setup.budget_usd_per_task_per_arm ? [` Budget: $${nf({ minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(setup.budget_usd_per_task_per_arm.value)} per task per arm`,
        cite(setup.budget_usd_per_task_per_arm.source), "."] : "",
      cite(setup.source)),
  ] : null;
  const sc = setup && setup.scoring;
  const toggle = el("div", { class: "toggle-bar" },
    el("div", { class: "seg", role: "group", "aria-label": "Scoring" },
      VIEWS.map((v) => el("button", { type: "button", "data-view": v.key, "aria-pressed": SCORING === v.key ? "true" : "false",
        onclick: () => setScoring(v.key) }, v.label))),
    setup && setup.report ? el("a", { href: setup.report.url, rel: "noopener noreferrer" }, setup.report.text) : null);
  const note = sc ? el("p", { class: "scoring-note" },
    el("span", { class: "sv sv-pre", hidden: SCORING !== "pre" }, sc.pre_registered),
    el("span", { class: "sv sv-post", hidden: SCORING !== "post" }, sc.post_hoc), cite(sc.source)) : null;
  const f = CTX && CTX.comparison_findings;
  const findings = f ? el("details", { class: "card findings", open: true },
    el("summary", {}, f.title),
    el("ol", {}, f.items.map((x) => el("li", {}, x))),
    el("p", { class: "fine" }, "As written up by the team", cite(f.source, f.url), ".")) : null;
  if (!runs.length) {
    mount("compare-body", intro, el("div", { class: "card" },
      el("p", {}, "No comparison run has been exported yet."),
      excluded.length ? el("p", { class: "fine" }, `Left out as synthetic: ${excluded.join(", ")}.`) : null), findings);
    return;
  }
  const ordered = [...runs.filter((r) => !r.synthetic), ...runs.filter((r) => r.synthetic)];
  mount("compare-body", intro, toggle, note, findings,
    excluded.length ? el("p", { class: "fine" }, `Synthetic boards left out of the comparison: ${excluded.join(", ")}.`) : null,
    ordered.map(runBlock));
}

// ------------------------------------------------------------------ failures
const NEGATIVE = /fail|reject|exceed|off from|mismatch|disagree|unit|flag/i;

function failCard(title, items, render, empty, sub) {
  return el("article", { class: "card" },
    el("h3", {}, `${title} (${items.length})`),
    sub ? el("p", { class: "fine" }, sub) : null,
    items.length ? el("ul", { class: "fail-list" }, items.map(render)) : el("p", { class: "empty" }, empty));
}

function runItem(r) {
  const refused = r.status === "refused";
  return el("li", {},
    badge(refused ? "pend" : "bad", refused ? "○" : "✕", r.status), " ", el("strong", {}, r.task || "run"),
    r.reason ? el("div", {}, r.reason) : null,
    el("div", { class: "fine" }, [r.agent, r.attempt ? `attempt ${r.attempt}` : null, fmtTime(r.timestamp)].filter(Boolean).join(" · ")));
}

function badResultItem(r) {
  const why = r.reasons.find((x) => NEGATIVE.test(x)) || r.reasons[0] || "";
  return el("li", {}, statusBadge(r.status), " ",
    el("a", { href: `#res-${r.id}` }, el("strong", {}, r.candidate), ` · ${propLabel(r.property)}`),
    why ? el("div", {}, why) : null,
    r.superseded_by.length ? el("div", { class: "fine" }, "A rerun has been posted.") : null);
}

function methodFailItem(row) {
  return el("li", {}, el("strong", {}, row.label || row.element), ": ",
    row.note ? [row.note, cite(row.source)]
      : [`MEAM gives ${fmtExact(row.meam.value)} ${row.meam.units || "K"} vs ${fmtExact(row.measured.value)} ${row.measured.units || "K"} measured`,
        cite(row.meam.source), ` (${fmtPct(row.rel_error.value, 1, true)}), outside the tolerance, so its checks are posted as inconclusive.`]);
}

function renderFailures(results, boards) {
  const runs = boards.flatMap((b) => b.run_reports);
  const failed = runs.filter((r) => r.status !== "refused");
  const refused = runs.filter((r) => r.status === "refused");
  const bad = results.filter((r) => r.status === "flagged" || r.status === "rejected");
  const classical = CTX ? ((CTX.methods || []).find((m) => m.id === "classical") || {}).rows || [] : [];
  const methodFails = classical.filter((r) => !r.counts);
  mount("failures-body",
    el("p", { class: "fine" }, "From the results board", cite(boardSource(boards)), " and the classical-MD calibration."),
    el("div", { class: "fail-grid" },
      failCard("Failed runs", failed, runItem, "No failed runs reported."),
      failCard("Flagged or rejected results", bad, badResultItem, "None so far."),
      failCard("Independent methods that failed calibration", methodFails, methodFailItem, "None.",
        "A classical potential that misses the measured value cannot vouch for anything."),
      failCard("Refused before running", refused, runItem, "None.",
        "Premise checks and domain checks doing their job: a property that cannot be measured this way is not run.")));
}

// ------------------------------------------------------------------ footer: every source cited above
function renderSources() {
  mount("sources", SOURCES.length
    ? el("ol", { class: "sources" }, SOURCES.map((s, i) => el("li", { id: `src-${i + 1}` },
      s.url ? el("a", { href: s.url, rel: "noopener noreferrer" }, s.source) : s.source)))
    : el("p", { class: "fine" }, "No sources: no export loaded."));
}

// ------------------------------------------------------------------ new exports: offer a reload, never re-render under the reader
function watchForUpdates() {
  const seen = DATA.index.exported_at;
  const timer = setInterval(() => {
    const s = document.createElement("script");
    s.src = `data/data.js?t=${Date.now()}`;
    s.onload = () => {
      const fresh = window.COOL_ORBIT_DATA;
      s.remove();
      if (fresh && fresh.index && fresh.index.exported_at && fresh.index.exported_at !== seen) {
        clearInterval(timer);
        const box = document.getElementById("update");
        box.replaceChildren(el("span", {}, `New export from ${fmtTime(fresh.index.exported_at)}`),
          el("button", { type: "button", onclick: () => window.location.reload() }, "Reload"));
        box.hidden = false;
      }
    };
    s.onerror = () => s.remove();
    document.head.append(s);
  }, 120000);
}

// ------------------------------------------------------------------ gallium vs paraffin (Overview)
const H2H_WINNER = { paraffin: "Paraffin", gallium: "Gallium", neither: "Neither", both: "Both", tie: "Tie" };

function renderHeadToHead() {
  if (!document.getElementById("h2h-body")) return;
  const hh = CTX && CTX.head_to_head;
  if (!hh || !Array.isArray(hh.rows) || !hh.rows.length) {
    mount("h2h-body", el("p", { class: "empty" }, "No head-to-head in this export (it needs sourced paraffin and gallium inputs)."));
    return;
  }
  const wins = { paraffin: 0, gallium: 0 };
  for (const r of hh.rows) if (r.winner in wins) wins[r.winner] += 1;
  const value = (side, r) => {
    const s = r[side] || {};
    const text = s.text ? s.text : `${fmt(s.value, 3)} ${UNIT[r.units] || r.units || ""}`.trim();
    return el("td", { class: `h2h-v${r.winner === side ? " win" : ""}` }, text, cite(s.source));
  };
  const badge = (r) => {
    const solo = r.winner === "paraffin" || r.winner === "gallium";
    const ratio = solo && r.ratio && finite(r.ratio.value) ? ` · ${fmtRatio(r.ratio.value)}` : "";
    return el("span", { class: `win-badge w-${r.winner}` }, (H2H_WINNER[r.winner] || r.winner) + ratio);
  };
  mount("h2h-body",
    el("div", { class: "h2h-top" },
      el("p", { class: "h2h-verdict" }, hh.verdict, cite(hh.source)),
      el("div", { class: "h2h-score", role: "group", "aria-label": "Aspects won" },
        el("span", { class: "sc w-paraffin" }, el("strong", {}, String(wins.paraffin)), " won by paraffin"),
        el("span", { class: "sc w-gallium" }, el("strong", {}, String(wins.gallium)), " won by gallium"))),
    el("div", { class: "table-wrap" }, el("table", { class: "h2h" },
      el("thead", {}, el("tr", {},
        el("th", { scope: "col" }, "What matters"),
        el("th", { scope: "col" }, (hh.materials && hh.materials.paraffin) || "Paraffin"),
        el("th", { scope: "col" }, "Gallium"),
        el("th", { scope: "col" }, "Winner"))),
      el("tbody", {}, hh.rows.map((r) => el("tr", {},
        el("th", { scope: "row" }, r.aspect, r.why ? el("span", { class: "kind" }, r.why) : null),
        value("paraffin", r), value("gallium", r), el("td", {}, badge(r))))))));
}

// ------------------------------------------------------------------ gallium in 3D (Overview)
// A small canvas viewer (no libraries: the server's CSP allows only our own scripts). Orthographic projection,
// spheres drawn back to front with a radial gradient, the close pairs as bonds, the block's box as a faint frame.
// Slow auto-spin, drag to rotate; no auto-spin under prefers-reduced-motion; pauses when the Overview is hidden.
function renderGallium() {
  const g = CTX && CTX.gallium;
  if (!document.getElementById("ga3d-body")) return;
  if (!g || !Array.isArray(g.atoms) || !g.atoms.length) {
    mount("ga3d-body", el("p", { class: "empty" }, "No gallium structure in this export."));
    return;
  }
  const nBlock = finite(g.n_block) ? g.n_block : g.atoms.length;
  const nGhost = g.atoms.length - nBlock;
  const label = `Rotating 3D model of ${g.phase}: ${nBlock} atoms, each drawn with its close partner as a bond.`;
  const canvas = el("div", { class: "ga3d-canvas ga3d-stage", id: "ga3d-stage", role: "img", "aria-label": label });
  const pair = g.pair || {};
  const shell = g.next_shell || {};
  const cell = g.cell || {};
  const facts = el("dl", { class: "ga3d-facts" },
    el("dt", {}, "Crystal"), el("dd", {}, g.phase, " · a ", fmtExact(cell.a), ", b ", fmtExact(cell.b), ", c ",
      fmtExact(cell.c), " Å", cite(cell.source)),
    el("dt", {}, "Closest neighbour"), el("dd", {}, el("strong", {}, `${fmtExact(pair.value)} Å`),
      ` · ${pair.count === 1 ? "one partner per atom (the pair, drawn as a bond)" : `${pair.count} per atom`}`,
      cite(pair.source)),
    el("dt", {}, "Next neighbours"), el("dd", {}, `${shell.count} more at ${fmtExact(shell.min)}–${fmtExact(shell.max)} Å`,
      cite(shell.source)),
    (g.measured || []).length ? [el("dt", {}, "Measured"), el("dd", {}, (g.measured || []).map((m, k) => [k ? " · " : "",
      `${m.label.toLowerCase()} ${fmtExact(m.value)} ${UNIT[m.units] || m.units || ""}`, cite(m.source)]))] : null);
  const notes = el("div", { class: "ga3d-notes" },
    (g.caveats || []).map((c) => el("p", { class: "fine" }, c.text, cite(c.source))));
  mount("ga3d-body", el("div", { class: "ga3d-grid" },
    el("figure", { class: "ga3d-view" }, canvas,
      el("figcaption", { class: "fine" }, `${cell.reps ? [cell.reps.a, cell.reps.b, cell.reps.c].join(" × ") : ""} unit cells, ${nBlock} atoms`,
        nGhost > 0 ? ` (plus ${nGhost} faded partners from the neighbouring cells)` : "", ". Drag to rotate.",
        cite(g.atoms[0] && g.atoms[0].source), el("span", { class: "ga3d-key" }, el("span", { class: "sw atom" }), "gallium atom",
          el("span", { class: "sw bond" }), `close pair (${fmtExact(pair.value)} Å)`))),
    el("div", { class: "ga3d-text" }, facts, notes)));
  // ga3d.js (a module: three.js, physically based) fills #ga3d-stage and sets data-ready. If it hasn't within a
  // moment (no WebGL, the module failed or was blocked), fall back to the small canvas viewer below.
  setTimeout(() => {
    if (canvas.dataset.ready === "1") return;
    const flat = el("canvas", { class: "ga3d-canvas", role: "img", "aria-label": label });
    canvas.replaceWith(flat);
    galliumViewer(flat, g);
  }, 2500);
}

function galliumViewer(canvas, g) {
  const ctx = canvas.getContext && canvas.getContext("2d");
  if (!ctx) return;
  const pts = g.atoms.map((a) => [a.x, a.y, a.z]);
  const isGhost = g.atoms.map((a) => Boolean(a.ghost));
  const pairs = (g.bonds || []).map((b) => [b.a, b.b]);
  const n = pts.length;
  const mid = [0, 1, 2].map((k) => pts.reduce((s, p) => s + p[k], 0) / n);
  const P = pts.map((p) => [p[0] - mid[0], p[1] - mid[1], p[2] - mid[2]]);
  const reach = Math.max(...P.map((p) => Math.hypot(p[0], p[1], p[2]))) || 1;
  const rp = (g.cell && g.cell.reps) || {};
  const reps = [rp.a || 1, rp.b || 1, rp.c || 1];
  const L = [g.cell.a * reps[0], g.cell.b * reps[1], g.cell.c * reps[2]];
  const box = [];
  for (const x of [0, 1]) for (const y of [0, 1]) for (const z of [0, 1]) box.push([x * L[0] - mid[0], y * L[1] - mid[1], z * L[2] - mid[2]]);
  const boxEdges = [[0, 1], [0, 2], [0, 4], [1, 3], [1, 5], [2, 3], [2, 6], [3, 7], [4, 5], [4, 6], [5, 7], [6, 7]];
  const still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let yaw = 0.7;
  let pitch = -0.38;
  let drag = null;
  let last = 0;
  let w = 0;
  let h = 0;

  function size() {
    const dpr = window.devicePixelRatio || 1;
    const cw = canvas.clientWidth || 480;
    const ch = Math.round(cw * 0.78);
    if (cw === w && ch === h) return;
    w = cw;
    h = ch;
    canvas.width = Math.round(cw * dpr);
    canvas.height = Math.round(ch * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function rot(p) {
    const [x, y, z] = p;
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const x1 = cy * x + sy * z, z1 = -sy * x + cy * z;
    return [x1, cp * y - sp * z1, sp * y + cp * z1];
  }
  function draw() {
    size();
    if (!w) return;
    const s = (Math.min(w, h) / 2 - 18) / reach;
    const cx = w / 2, cy = h / 2;
    const R = P.map(rot);
    const proj = R.map((p) => [cx + p[0] * s, cy - p[1] * s, p[2]]);
    ctx.clearRect(0, 0, w, h);
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(124, 196, 255, 0.18)";
    const B = box.map(rot).map((p) => [cx + p[0] * s, cy - p[1] * s]);
    ctx.beginPath();
    for (const [a, b] of boxEdges) { ctx.moveTo(B[a][0], B[a][1]); ctx.lineTo(B[b][0], B[b][1]); }
    ctx.stroke();
    const zr = reach || 1;
    const order = [...proj.keys()].sort((a, b) => proj[a][2] - proj[b][2]); // back (smaller z) first
    const bondsOf = new Map();
    for (const [a, b] of pairs) {
      bondsOf.set(a, [...(bondsOf.get(a) || []), b]);
    }
    const r0 = Math.max(4, 0.62 * s); // ~0.6 Å drawn radius: the pairs stay visible
    for (const i of order) {
      for (const j of bondsOf.get(i) || []) { // draw each bond once, with the farther atom of the pair
        const depth = (proj[i][2] + proj[j][2]) / (2 * zr);
        ctx.strokeStyle = `rgba(52, 211, 153, ${0.45 + 0.4 * (depth + 1) / 2})`;
        ctx.lineWidth = Math.max(2, 0.28 * s);
        ctx.beginPath();
        ctx.moveTo(proj[i][0], proj[i][1]);
        ctx.lineTo(proj[j][0], proj[j][1]);
        ctx.stroke();
      }
      const [x, y, z] = proj[i];
      const t = (z / zr + 1) / 2; // 0 back .. 1 front
      const r = r0 * (0.8 + 0.3 * t) * (isGhost[i] ? 0.85 : 1);
      const f = isGhost[i] ? 0.35 : 1; // partners from the neighbouring cells are drawn faded
      const grad = ctx.createRadialGradient(x - r * 0.35, y - r * 0.35, r * 0.1, x, y, r);
      grad.addColorStop(0, `rgba(240, 247, 255, ${(0.55 + 0.45 * t) * f})`);
      grad.addColorStop(0.55, `rgba(150, 182, 224, ${(0.5 + 0.45 * t) * f})`);
      grad.addColorStop(1, `rgba(52, 74, 112, ${(0.6 + 0.4 * t) * f})`);
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  function frame(ts) {
    const dt = last ? Math.min(0.05, (ts - last) / 1000) : 0;
    last = ts;
    const visible = canvas.offsetParent !== null && !document.hidden;
    if (visible) {
      if (!drag && !still) yaw += dt * 0.18; // about 35 s per turn: slow and calm
      draw();
    }
    requestAnimationFrame(frame);
  }
  canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    yaw += (e.clientX - drag.x) * 0.01;
    pitch = Math.max(-1.4, Math.min(1.4, pitch + (e.clientY - drag.y) * 0.01));
    drag = { x: e.clientX, y: e.clientY };
    if (still) draw();
  });
  const end = () => { drag = null; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  window.addEventListener("resize", () => { w = 0; draw(); });
  draw();
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ start
function renderNoData() {
  for (const id of ["h2h-body", "burst-body", "ga3d-body", "agents-body", "results-body", "compare-body", "failures-body"]) {
    mount(id, el("p", { class: "empty" }, "Waiting for an export."));
  }
}

function start() {
  document.body.append(tip);
  setupCountdown();
  if (!DATA || !DATA.index || !DATA.boards) {
    renderNoData();
    renderSources();
    setupTabs();
    return;
  }
  const boards = scienceBoards();
  const results = boards.flatMap((b) => b.results.map((r) => ({ ...r, board: b.name, synthetic: b.synthetic })));
  ACT = buildActivity();
  renderBanners();
  renderHero(results, boards);
  renderHeadToHead();
  renderBurst();
  renderGallium();
  renderAgents(results);
  renderResults(results, boards);
  if (hasTab("comparison")) renderCompare();
  if (hasTab("failures")) renderFailures(results, boards);
  renderSources(); // after every section, and before any tab is shown: lists every footnote cited above
  renderPills(results);
  updateDataAge();
  setInterval(updateDataAge, 15000);
  setupTabs();
  watchForUpdates();
}
start();
