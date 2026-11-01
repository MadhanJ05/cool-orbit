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
const TABS = ["overview", "agents", "results", "comparison", "failures"];
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
  // Counts live in the tabs, with their sources; the pill only says where the comparison stands.
  for (const run of DATA.index.comparison || []) {
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
  const c = countGroups(results);
  const n = results.length;
  const src = boardSource(boards);
  const burst = CTX && CTX.burst && !CTX.burst.error ? CTX.burst : null;
  mount("hero-answer", el("p", { class: "answer" },
    el("span", { class: "answer-label" }, "Answer so far"),
    burst && burst.headline ? [burst.headline, cite(burst.source), " "] : null,
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
      el("a", { href: "#agents" }, "How the agents work"), " · ", el("a", { href: "#results" }, "every result"), " · ",
      el("a", { href: "#failures" }, "what failed")));
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

// ------------------------------------------------------------------ agents: the diagram
// [line within its row, first column, column span] on a 12-column grid. The connectors in EDGE_ROUTES assume these
// positions (columns kept free for vertical drops and gaps wide enough for labels).
const NODE_LAYOUT = {
  humans: [1, 4, 6], scouts: [1, 1, 12], premise: [1, 9, 4],
  simulator: [1, 1, 3], alchemi: [1, 5, 5], worker: [1, 10, 3],
  rules: [1, 1, 3], llm: [1, 4, 3], classical: [1, 7, 3], librarian: [1, 10, 3], adjudicator: [2, 3, 3],
  board: [1, 1, 3], trust: [1, 5, 4], ranker: [1, 10, 3],
};
const FIXED_BADGE = { humans: "people", alchemi: "engine", board: "store", trust: "derived" };
const NODE_EL = new Map();
const NODE_BY_ID = new Map();
const ROW_HUE = {};
let HOT = null;
const VERDICT_ORDER = ["pass", "agree", "fail", "disagree", "reject", "inconclusive"];

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

function nodeBody(n, results) {
  const act = (a) => ACT.agents.get(a) || null;
  switch (n.id) {
    case "humans":
      return el("ul", { class: "chipset" }, n.chips.map((c) => el("li", { class: "nchip" }, c.text, cite(c.source))));
    case "scouts":
      return el("ul", { class: "chipset" }, (n.scouts || []).map((s) => {
        const a = act(s.agent);
        const label = s.general ? "general · tasks" : s.label;
        const title = `${s.agent}: ${s.families.map(familyLabel).join(", ") || "any family"}` +
          `${s.enabled ? "" : " (disabled in scout_seeds.json)"}${s.general ? " (answers comparison tasks no family owns)" : ""}`;
        return nchip(s.enabled ? label : `${label} · off`, s.enabled ? (a ? a.total : 0) : null, s.enabled ? "" : "off", title);
      }));
    case "simulator":
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
        el("p", { class: "chipset-h" }, "ALCHEMI provides"),
        el("ul", { class: "chipset" }, (n.imports || []).map((i) => nchip(i.name, null, "", `${i.what} (${i.module})`))),
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
      return verdictChips(n.agents);
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
  if (!st) {
    return {
      humans: "no board posts: steers through frozen config files",
      alchemi: "not an agent: it computes, it never decides",
      board: "agents talk only through it",
      trust: "no agent can set a status",
    }[n.id] || "";
  }
  if (!st.total) return "no posts on the exported boards";
  const where = [st.science ? `science ${st.science}` : null, st.armC ? `arm C ${st.armC}` : null].filter(Boolean).join(" · ");
  const claim = st.claims.length ? ` · claim: ${clip(st.claims[0].query, 44)}` : "";
  return `${st.total} posts (${where}) · last ${fmtShort(st.last.timestamp)}${claim}`;
}

function renderNode(n, hue, results) {
  const [line, col, span] = NODE_LAYOUT[n.id] || [1, 1, 12];
  const st = n.agents.length ? agentState(n.agents) : null;
  const badgeEl = st ? el("span", { class: `nbadge ${st.key}`, title: st.sentence }, st.text)
    : el("span", { class: "nbadge" }, FIXED_BADGE[n.id] || "");
  const open = el("button", { type: "button", class: "node-title node-open", "aria-pressed": "false",
    "aria-label": `${n.name}: ${n.tag} · ${n.role}. ${st ? st.sentence : FIXED_BADGE[n.id] || ""} Show details.`,
    onclick: () => openDetail(n.id) }, `${n.tag} · ${n.role}`);
  const box = el("div", { class: `node r-${hue}${n.id === "alchemi" ? " engine" : ""}`, id: `node-${n.id}`,
    onclick: (e) => { if (!e.target.closest("a, button")) openDetail(n.id); } },
  el("div", { class: "node-head" }, open, badgeEl),
  el("p", { class: "node-mono" }, n.line),
  nodeBody(n, results),
  el("p", { class: "node-foot" }, nodeFoot(n, st)));
  box.style.gridColumn = `${col} / span ${span}`;
  box.style.gridRow = String(line);
  NODE_EL.set(n.id, box);
  return box;
}

// connectors: computed from the boxes' rendered positions; redrawn on resize. Below STACKED (same breakpoint as
// style.css) the rows stack into one column and the connectors are hidden.
const STACKED = "(max-width: 899px)";
const P = (v) => Math.round(v * 10) / 10;
function vpath(x1, y1, x2, y2) {
  if (Math.abs(x1 - x2) < 1.5) return `M${P(x1)} ${P(y1)}V${P(y2)}`;
  const ym = (y1 + y2) / 2;
  return `M${P(x1)} ${P(y1)}C${P(x1)} ${P(ym)} ${P(x2)} ${P(ym)} ${P(x2)} ${P(y2)}`;
}
const hpath = (x1, x2, y) => `M${P(x1)} ${P(y)}H${P(x2)}`;
const atX = (n, f) => n.x + n.w * f;
function down(x1, y1, x2, y2, label, side = "right") {
  const lab = side === "left" ? { x: Math.min(x1, x2) - 8, y: (y1 + y2) / 2, text: label, anchor: "end" }
    : { x: Math.max(x1, x2) + 8, y: (y1 + y2) / 2, text: label };
  return { paths: [vpath(x1, y1, x2, y2)], dots: [[x2, y2]], labels: label ? [lab] : [] };
}
function across(a, b, label) {
  if (!a || !b) return null;
  const y = Math.max(a.y, b.y) + 26;
  return { paths: [hpath(a.r, b.x, y)], dots: [[b.x, y]], labels: [{ x: (a.r + b.x) / 2, y: y - 11, text: label, anchor: "middle" }] };
}
function resultsBusY(R) {
  const tops = ["rules", "llm", "classical", "librarian"].map((k) => R[k]).filter(Boolean).map((n) => n.y);
  return tops.length ? Math.min(...tops) - 20 : null;
}
// Each route gets the boxes' rectangles, the layout context and the edge (its label comes from the context).
const EDGE_ROUTES = {
  e_seed: (R, c, e) => R.humans && R.scouts && down(R.humans.cx, R.humans.b, R.humans.cx, R.scouts.y, e.label),
  e_req_sim: (R, c, e) => R.scouts && R.simulator && down(R.simulator.cx, R.scouts.b, R.simulator.cx, R.simulator.y, e.label),
  e_req_prem: (R, c, e) => R.scouts && R.premise && down(R.premise.cx, R.scouts.b, R.premise.cx, R.premise.y, e.label),
  e_go: (R, c, e) => R.premise && R.worker && down(R.premise.cx, R.premise.b, R.worker.cx, R.worker.y, e.label),
  e_jobs: (R, c, e) => across(R.simulator, R.alchemi, e.label),
  e_res: (R, c, e) => {
    const s = R.simulator;
    const y = resultsBusY(R);
    if (!s || y === null) return null;
    const checks = ["rules", "llm", "classical", "librarian"].map((k) => R[k]).filter(Boolean);
    const xs = [s.cx, ...checks.map((k) => k.cx), ...(R.worker ? [R.worker.cx] : [])];
    return { paths: [vpath(s.cx, s.b, s.cx, y), hpath(Math.min(...xs), Math.max(...xs), y),
      ...checks.map((k) => vpath(k.cx, y, k.cx, k.y))],
    dots: checks.map((k) => [k.cx, k.y]), labels: [{ x: s.cx + 8, y: (s.b + y) / 2, text: e.label }] };
  },
  e_res_w: (R, c, e) => {
    const w = R.worker;
    const y = resultsBusY(R);
    return w && y !== null ? { paths: [vpath(w.cx, w.b, w.cx, y)], dots: [],
      labels: [{ x: w.cx + 8, y: (w.b + y) / 2, text: e.label }] } : null;
  },
  e_dispute: (R, c, e) => R.llm && R.adjudicator &&
    down(atX(R.llm, 0.25), R.llm.b, atX(R.adjudicator, 0.8), R.adjudicator.y, e.label, "left"),
  e_dispute_r: (R, c, e) => R.rules && R.adjudicator &&
    down(atX(R.rules, 0.8), R.rules.b, atX(R.adjudicator, 0.2), R.adjudicator.y, e.label),
  e_checks: (R) => {
    const board = R.board;
    if (!board) return null;
    const top = Math.min(...["board", "trust", "ranker"].map((k) => R[k]).filter(Boolean).map((n) => n.y));
    const y = top - 20;
    const drops = [];
    if (R.rules) drops.push([atX(R.rules, 0.2), R.rules.b, "audits"]);
    if (R.llm) drops.push([atX(R.llm, 0.8), R.llm.b, "audits"]);
    if (R.adjudicator) drops.push([R.adjudicator.cx, R.adjudicator.b, "rulings"]);
    if (R.classical) drops.push([R.classical.cx, R.classical.b, "checks"]);
    if (R.librarian) drops.push([R.librarian.cx, R.librarian.b, "measured"]);
    const xs = [...drops.map((d) => d[0]), board.cx];
    return { paths: [...drops.map(([x, y0]) => vpath(x, y0, x, y)), hpath(Math.min(...xs), Math.max(...xs), y),
      vpath(board.cx, y, board.cx, board.y)],
    dots: [[board.cx, board.y]], labels: drops.map(([x, y0, t]) => ({ x: x + 8, y: (y0 + y) / 2, text: t })) };
  },
  e_evidence: (R, c, e) => across(R.board, R.trust, e.label),
  e_status: (R, c, e) => across(R.trust, R.ranker, e.label),
  e_prereg: (R, ctx, e) => {
    const h = R.humans;
    const t = R.trust;
    if (!h || !t) return null;
    const yb = ctx.height - 16;
    return { paths: [`M${P(h.x)} ${P(h.cy)}H${P(ctx.railX)}V${P(yb)}H${P(t.cx)}V${P(t.b)}`], dots: [[t.cx, t.b]],
             labels: [{ x: ctx.railX - 9, y: (h.cy + yb) / 2, text: e.label, anchor: "middle", rotate: true }] };
  },
};

function drawEdges() {
  const host = document.getElementById("diagram");
  const layer = host && host.querySelector(".dsvg");
  if (!layer || !G) return;
  layer.replaceChildren();
  if (!host.getClientRects().length || window.matchMedia(STACKED).matches) return;
  const base = host.getBoundingClientRect();
  if (!base.width) return;
  const R = {};
  for (const [id, node] of NODE_EL) {
    const r = node.getBoundingClientRect();
    const x = r.left - base.left;
    const y = r.top - base.top;
    R[id] = { x, y, w: r.width, h: r.height, r: x + r.width, b: y + r.height, cx: x + r.width / 2, cy: y + r.height / 2 };
  }
  const cells = host.querySelector(".dcells");
  const ctx = { railX: (cells ? cells.getBoundingClientRect().left - base.left : 120) - 20, height: base.height };
  layer.setAttribute("viewBox", `0 0 ${P(base.width)} ${P(base.height)}`);
  for (const e of G.edges) {
    const route = EDGE_ROUTES[e.id];
    const spec = route ? route(R, ctx, e) : null;
    if (!spec) continue;
    const g = svg("g", { class: `edge ${e.kind}`, "data-edge": e.id });
    for (const d of spec.paths) g.append(svg("path", { d }));
    for (const [cx, cy] of spec.dots || []) g.append(svg("circle", { cx: P(cx), cy: P(cy), r: 2.6 }));
    for (const lab of spec.labels || []) {
      const lg = svg("g", { class: "elabel", transform: lab.rotate ? `rotate(-90 ${P(lab.x)} ${P(lab.y)})` : null });
      lg.append(svg("text", { x: P(lab.x), y: P(lab.y), "text-anchor": lab.anchor || "start", "dominant-baseline": "middle" },
        String(lab.text).toUpperCase()));
      g.append(lg);
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
  applyHighlight(HOT);
}

function applyHighlight(step) {
  HOT = step || null;
  const host = document.getElementById("diagram");
  if (!host) return;
  host.classList.toggle("stepping", !!HOT);
  for (const [id, node] of NODE_EL) node.classList.toggle("hot", !!HOT && HOT.nodes.includes(id));
  for (const g of host.querySelectorAll(".edge")) g.classList.toggle("hot", !!HOT && HOT.edges.includes(g.dataset.edge));
}

// ------------------------------------------------------------------ agents: the replay (a real case from the board)
let CASE = null;
let MODE = "idea";
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

function buildCase() {
  if (!G || !G.case) return null;
  for (const b of scienceBoards()) {
    const hit = (b.results || []).find((r) => normName(r.candidate) === normName(G.case.candidate) && r.property === G.case.property);
    if (hit && b.lineage && b.lineage[hit.id]) {
      const steps = caseSteps(b, hit);
      return steps.length ? { board: b, result: hit, steps } : null;
    }
  }
  return null;
}

function caseSteps(b, res) {
  const L = b.lineage;
  const R = L[res.id];
  const kids = (a) => (a ? a.children || [] : []).map((id) => L[id]).filter(Boolean)
    .sort((x, y) => (x.timestamp < y.timestamp ? -1 : x.timestamp > y.timestamp ? 1 : 0));
  const ref = (a) => `results board ${b.name}: ${a.type} ${a.id.slice(0, 8)} by ${a.agent}, ${fmtTime(a.timestamp)}`;
  const steps = [];
  const cand = (R.parents || []).map((id) => L[id]).find((a) => a && a.type === "candidate");
  if (cand) {
    const need = (cand.needs || []).find((x) => x.property === res.property) || (cand.needs || [])[0];
    steps.push({ title: "A Scout proposes", nodes: ["scouts"], edges: ["e_req_sim", "e_req_prem"],
      text: `${cand.agent} posts the candidate ${cand.payload.name} (${familyLabel(cand.payload.family)}, ${cand.payload.tier} tier)` +
        `${need ? ` with a request: "${need.query}".` : "."}`,
      meta: fmtShort(cand.timestamp), cite: ref(cand) });
  }
  const claim = kids(cand).find((a) => a.type === "need_claim" && a.agent === R.agent);
  const premise = [...kids(cand), ...kids(R)].filter((a) => a.type === "check" && a.payload.check_type === "premise_check");
  if (premise.length) {
    for (const c of premise) {
      steps.push({ title: "Premise check", nodes: ["premise"], edges: ["e_req_prem"],
        text: `${c.agent}: premise ${c.payload.verdict}.`, quote: c.payload.note, meta: fmtShort(c.timestamp), cite: ref(c) });
    }
  } else {
    const gap = claim && cand ? fmtGap(parseIso(claim.timestamp) - parseIso(cand.timestamp)) : null;
    steps.push({ title: "Premise check", nodes: ["premise"], edges: ["e_req_prem"],
      text: "No premise check was posted on this request in the export." +
        `${gap ? ` The Simulator claimed it ${gap} after the Scout posted it.` : ""}` +
        " The premise checker advises and never claims requests, so it did not block this run.",
      cite: `${claim ? `${ref(claim)}; ` : ""}coolorbit/agents/premise.py (PremiseChecker); coolorbit/sim/simulator_agent.py (SimulatorAgent.pending)` });
  }
  const m = R.payload.method || {};
  const temps = Array.isArray(m.temperatures_K) && m.temperatures_K.length
    ? `at ${m.temperatures_K.map((t) => `${fmtExact(t)} K`).join(", ")}` : null;
  const how = [m.ensemble, finite(m.n_atoms) ? `${fmtExact(m.n_atoms)} atoms` : null,
    finite(m.sim_length_ps) ? `${fmtExact(m.sim_length_ps)} ps` : null,
    finite(m.timestep_fs) ? `${fmtExact(m.timestep_fs)} fs steps` : null, temps].filter(Boolean).join(", ");
  steps.push({ title: "The Simulator runs it on ALCHEMI", nodes: ["simulator", "alchemi"], edges: ["e_req_sim", "e_jobs"],
    text: `${R.agent}${claim ? ` claims the request (${fmtShort(claim.timestamp)}) and` : ""} runs one GPU job: ` +
      `${m.engine || "?"} with ${m.model || "?"}, ${m.protocol || "protocol not recorded"}${how ? `; ${how}` : ""}.`,
    meta: claim ? fmtShort(claim.timestamp) : null, cite: claim ? ref(claim) : ref(R) });
  const kj = res.units === "J/kg" ? ` (${fmt(res.value / 1000)} kJ/kg)` : "";
  const flags = (R.payload.flags || []).length ? ` It carries its own flag: ${R.payload.flags.join(", ")}.` : "";
  steps.push({ title: "A result is posted", nodes: ["simulator"], edges: ["e_res"],
    text: `Result ${res.id.slice(0, 8)}: ${propLabel(res.property).toLowerCase()} = ${fmtExact(res.value)}` +
      `${finite(res.uncertainty) ? ` ± ${fmtExact(res.uncertainty)}` : ""} ${unit(res.units)}${kj}.${flags}`,
    meta: fmtShort(R.timestamp), cite: ref(R) });
  for (const c of kids(R)) {
    const node = nodeOfAgent(c.agent);
    if (c.type === "reference_value") {
      const v = c.payload.value;
      steps.push({ title: "The Librarian brings a measurement", nodes: [node || "librarian"], edges: ["e_res"],
        text: v === null || v === undefined ? `${c.agent} finds no sourced measurement and posts a null reference.`
          : `${c.agent} posts the measured value: ${fmtExact(v)} ${unit(c.payload.units)}${c.payload.units === "J/kg" ? ` (${fmt(v / 1000)} kJ/kg)` : ""}.`,
        quote: c.payload.source ? `Source: ${c.payload.source}` : null, meta: fmtShort(c.timestamp), cite: ref(c) });
    } else if (c.type === "check" && c.payload.check_type !== "premise_check") {
      const who = (NODE_BY_ID.get(node) || {}).name || c.agent;
      const fl = (c.payload.flags || []).length ? ` [${c.payload.flags.join(", ")}]` : "";
      const isAdj = node === "adjudicator";
      steps.push({ title: isAdj ? "The adjudicator rules" : `${who} checks it`, nodes: isAdj ? ["adjudicator", "rules", "llm"] : [node || "rules"],
        edges: isAdj ? ["e_dispute", "e_dispute_r"] : ["e_res"],
        text: `${who} (${c.agent}): ${String(c.payload.check_type).replaceAll("_", " ")} ${c.payload.verdict}${fl}.`,
        quote: c.payload.note, meta: fmtShort(c.timestamp), cite: ref(c) });
    } else if (c.type === "property_result" && c.payload.property === res.property) {
      steps.push({ title: "A rerun", nodes: [node || "simulator"], edges: ["e_res"],
        text: `${c.agent} posts a rerun that supersedes it: ${fmtExact(c.payload.value)} ${unit(c.payload.units)}.`,
        meta: fmtShort(c.timestamp), cite: ref(c) });
    }
  }
  const g = groupOf(res.status);
  steps.push({ title: "The trust rule decides", nodes: ["trust", "board"], edges: ["e_checks", "e_evidence"],
    text: `Status: ${String(STATUS_TEXT[res.status] || res.status).toUpperCase()}. No agent set it: the trust rule derived it from the checks above.` +
      `${g.key === "flagged" ? " A flagged result needs a rerun and is never ranked." : ""}`,
    list: (res.reasons || []).slice(0, 5), cite: `coolorbit/board/status.py; ${boardSource([b])}` });
  return steps;
}

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
  applyHighlight({ nodes: [id], edges: (G.edges || []).filter((e) => e.from === id || e.to === id).map((e) => e.id) });
  setPressed(id);
  renderPanel("back");
  const panel = document.getElementById("idea");
  if (panel && window.matchMedia("(max-width: 1319px)").matches) panel.scrollIntoView({ block: "start" });
}
function closeDetail() {
  const back = SELECTED;
  SELECTED = null;
  MODE = "idea";
  applyHighlight(null);
  setPressed(null);
  renderPanel();
  const node = back && NODE_EL.get(back);
  const opener = node && node.querySelector(".node-open");
  if (opener) opener.focus({ preventScroll: window.matchMedia("(min-width: 1320px)").matches });
}

const btn = (label, key, onclick, cls = "", disabled = false) =>
  el("button", { type: "button", class: `btn ${cls}`.trim(), "data-key": key, onclick, disabled }, label);

function renderPanel(focusKey) {
  const host = document.getElementById("idea-content");
  if (!host || !G) return;
  const had = host.contains(document.activeElement) ? document.activeElement.getAttribute("data-key") : null;
  if (MODE === "detail" && SELECTED) host.replaceChildren(...detailView(SELECTED));
  else if (MODE === "step" && CASE) host.replaceChildren(...stepView());
  else host.replaceChildren(...ideaView());
  const key = focusKey || had; // keep keyboard focus in the panel when its buttons are rebuilt (e.g. while playing)
  if (key) {
    const target = host.querySelector(`[data-key="${key}"]:not([disabled])`) || host.querySelector("button:not([disabled])");
    if (target) target.focus({ preventScroll: true });
  }
  if (document.getElementById("sources") && SOURCES.length) renderSources(); // the panel may cite new artifacts
}

function ideaView() {
  const idea = G.idea;
  return [
    el("p", { class: "eyebrow" }, "The idea"),
    el("h2", { id: "idea-h" }, idea.headline),
    el("div", { class: "idea-points" }, idea.points.map((p) => el("p", {}, el("strong", {}, p.lead), " ", p.text, cite(p.source)))),
    CASE ? el("div", { class: "actions" },
      btn("Step through it", "step", () => nextStep("step"), "primary"),
      btn("Play", "play", togglePlay))
      : el("p", { class: "fine" }, `The replay case (${G.case ? G.case.label : "none"}) is not in this export, so there is nothing to step through.`),
    el("p", { class: "hint" }, CASE ? `replays ${G.case.label} from board ${CASE.board.name} · or click any box for its details`
      : "click any box for its details"),
  ];
}

function stepView() {
  const s = CASE.steps[STEP];
  const n = CASE.steps.length;
  const last = STEP >= n - 1;
  return [
    el("p", { class: "eyebrow" }, "Replay · a real case from the board"),
    el("h2", {}, G.case.label, cite(G.case.source)),
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
  const hue = ROW_HUE[n.row] || "keep";
  const row = (G.rows || []).find((r) => r.id === n.row);
  const st = n.agents.length ? agentState(n.agents) : null;
  const dd = (label, ...kids) => [el("dt", {}, label), el("dd", {}, ...kids)];
  const list = (items) => (items.length ? el("ul", {}, items.map((x) => el("li", {}, x))) : "none");
  const counts = () => {
    if (!st || !st.total) return st ? "none" : null;
    const types = {};
    for (const a of n.agents) {
      const s = ACT.agents.get(a);
      if (s) for (const [t, k] of Object.entries(s.byType)) types[t] = (types[t] || 0) + k;
    }
    return [Object.entries(types).sort((a, b) => b[1] - a[1]).map(([t, k]) => `${k} ${t.replaceAll("_", " ")}`).join(" · "),
      ` (science boards ${st.science}, arm C ${st.armC})`, cite(activitySource())];
  };
  const extra = [];
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
    el("p", { class: `tagline r-${hue}` }, `${row ? `${row.label} · ` : ""}${n.tag} · ${n.role}`),
    el("h3", {}, n.name),
    st ? el("p", {}, el("span", { class: `nbadge ${st.key}` }, st.text), " ", st.sentence, cite(activitySource()))
      : el("p", {}, el("span", { class: "nbadge" }, FIXED_BADGE[n.id] || ""), " ", nodeFoot(n, null), "."),
    el("p", {}, n.does, cite(n.source)),
    n.note ? el("p", { class: "note" }, n.note) : null,
    el("dl", {},
      ...dd(n.model ? "Model" : "Runs on", n.model ? [`${n.model.full_name} (role ${n.model.role}, effort ${n.model.effort})`, cite(n.model.source)]
        : n.engine || "code"),
      ...(n.agents.length ? dd("Posts on the board as", n.agents.map((a, i) => [i ? ", " : "", el("code", {}, a)])) : []),
      ...dd("Reads", list(n.inputs)),
      ...dd("Produces", list(n.outputs)),
      ...dd("Code", el("ul", {}, n.code.map((p) => el("li", {}, codeLink(p))))),
      ...(st ? dd("On the exported boards", counts()) : []),
      ...(latest ? dd("Latest post", `${latest.summary || latest.type}`, el("span", { class: "muted" }, ` · ${latest.type} ${latest.id.slice(0, 8)} on ${latest.board}, ${fmtShort(latest.timestamp)}`)) : []),
      ...extra),
  ];
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
  for (const r of G.rows) ROW_HUE[r.id] = r.hue;
  for (const n of G.nodes) NODE_BY_ID.set(n.id, n);
  CASE = buildCase();
  const activeMin = G.status_rule ? G.status_rule.active_minutes : 60;
  const diagram = el("div", { class: "diagram", id: "diagram" },
    svg("svg", { class: "dsvg", "aria-hidden": "true", focusable: "false" }),
    G.rows.map((row) => el("div", { class: "drow", role: "group", "aria-label": row.label },
      el("div", { class: "dlabel" }, row.label),
      el("div", { class: "dcells" }, G.nodes.filter((n) => n.row === row.id).map((n) => renderNode(n, row.hue, results))))));
  const legend = el("p", { class: "diagram-legend" },
    el("span", {}, el("span", { class: "nbadge active" }, "active"), `posted in the ${activeMin} min before the export`, cite(G.status_rule.source)),
    el("span", {}, el("span", { class: "nbadge claimed" }, "claimed"), "held an unexpired claim on a request at export time"),
    el("span", {}, el("span", { class: "nbadge idle" }, "idle"), "posted earlier"),
    el("span", {}, el("span", { class: "nbadge none" }, "no posts"), "nothing on the exported boards"),
    el("span", {}, "Counts: artifacts each agent posted", cite(activitySource()), ". Dotted lines: files people edit; dashed: board artifacts."));
  const protocols = (CTX.methods || []).filter((m) => m.id !== "classical" && m.id !== "alchemi");
  mount("agents-body",
    el("div", { class: "agents-grid" },
      el("div", { class: "card diagram-card" }, diagram, legend,
        el("p", { class: "coord" }, G.coordination.text, cite(G.coordination.source))),
      el("aside", { class: "card idea", id: "idea", "aria-label": "The idea, the replay and box details" },
        el("div", { id: "idea-content" }),
        el("p", { class: "sr-only", id: "case-live", "aria-live": "polite" }))),
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
    if (MODE === "detail") closeDetail();
    else if (MODE === "step") exitStep();
  });
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
        prediction ? badge("pred", "◇", "PREDICTION") : null,
        r.synthetic ? badge("bad", "!", "SYNTHETIC") : null),
      el("p", { class: "fine" }, `${propLabel(r.property)} · ${familyLabel(r.family)}`,
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

// ------------------------------------------------------------------ start
function renderNoData() {
  for (const id of ["burst-body", "agents-body", "results-body", "compare-body", "failures-body"]) {
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
  renderBurst();
  renderAgents(results);
  renderResults(results, boards);
  renderCompare();
  renderFailures(results, boards);
  renderSources(); // after every section, and before any tab is shown: lists every footnote cited above
  renderPills(results);
  updateDataAge();
  setInterval(updateDataAge, 15000);
  setupTabs();
  watchForUpdates();
}
start();
