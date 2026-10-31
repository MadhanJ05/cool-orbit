// Cool Orbit: the judges' page. Reads window.COOL_ORBIT_DATA, written by `python -m dashboard.export`: the boards with
// derived statuses, the comparison scorecards, and `context` (dashboard/context.py: question, burst calc, methods).
// Board payloads are agent-written, so every string goes in through textContent (el/svg helpers), never innerHTML.
// Charts are inline SVG built with DOM APIs. Every number carries its source: a numbered footnote (listed in the
// footer) with the same text on hover.
"use strict";

const DATA = window.COOL_ORBIT_DATA;
const CTX = DATA && DATA.context && !DATA.context.error ? DATA.context : null;
const SVG_NS = "http://www.w3.org/2000/svg";

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
function fmtTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso || "");
  const s = d.toISOString();
  return `${s.slice(0, 10)} ${s.slice(11, 16)} UTC`;
}
function ago(iso) {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (!(mins >= 0)) return "";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}
const PROPERTY = {
  melting_point: "Melting point", latent_heat: "Latent heat", transition_temperature: "Transition temperature",
  transition_latent_heat: "Transition latent heat", density: "Density", conductivity: "Thermal conductivity",
  lattice_constant: "Lattice constant", adsorption_energy: "Adsorption energy", self_diffusion: "Self-diffusion",
  bulk_modulus: "Bulk modulus",
};
const propLabel = (p) => PROPERTY[p] || String(p || "").replaceAll("_", " ");
const familyLabel = (f) => String(f || "").replaceAll("_", " ");

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
      `The export could not build it (${DATA.context.error}), so the burst and methods sections are empty.`));
  } else if (!DATA.context) {
    items.push(el("div", { class: "banner warn" }, el("strong", {}, "Page context missing. "),
      "This export comes from an older exporter; the burst and methods sections are empty."));
  }
  mount("banners", items);
}

// ------------------------------------------------------------------ 1. hero
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
  ] : null);

  const runs = boards.flatMap((b) => b.run_reports);
  const failed = runs.filter((r) => r.status !== "refused").length;
  const refused = runs.length - failed;
  const tile = (v, label, tone, icon, wide) => el("div", { class: `tile${wide ? " wide" : ""}` },
    el("div", { class: "v" }, String(v)),
    el("div", { class: "l" }, icon ? el("span", { class: `ico t-${tone}`, "aria-hidden": "true" }, icon) : null, label));
  const when = ago(DATA.index.exported_at);
  mount("hero-status", el("div", { class: "card" },
    el("p", { class: "status-head" }, el("span", { class: "live", "aria-hidden": "true" }), "Live from the results board", cite(src)),
    el("p", { class: "stamp" }, `Exported ${fmtTime(DATA.index.exported_at)}${when ? ` · ${when}` : ""}`),
    el("div", { class: "tiles" },
      tile(n, "simulated results on the board", null, null, true),
      tile(c.trusted, "trusted", "ok", "✓"),
      tile(c.pending, "pending review", "pend", "○"),
      tile(c.flagged, "flagged", "warn", "!"),
      tile(c.rejected, "rejected", "bad", "✕"),
      tile(failed, `${failed === 1 ? "failed run" : "failed runs"}${refused ? `, plus ${refused} refused before running` : ""}`, "bad", "✕", true)),
    el("p", { class: "fine status-foot" }, "A status is derived from the evidence; no agent can set one. ",
      el("a", { href: "#results" }, "Every result"), " · ", el("a", { href: "#failures" }, "what failed"))));
}

// ------------------------------------------------------------------ 2. wax vs metals
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
    el("h3", { class: "sub-h" }, "The numbers behind the charts"),
    el("div", { class: "card" }, burstTable(b, mats)),
    skippedList(b),
    el("details", { class: "disclosure" },
      el("summary", {}, "How the calculation works, its assumptions and caveats"),
      el("div", { class: "two" },
        el("div", { class: "card" }, el("h3", {}, "Model"), el("ul", { class: "notes" }, (b.model || []).map(noteItem))),
        el("div", { class: "card" },
          el("h3", {}, "Assumptions, all in paraffin's favour"), el("ul", { class: "notes" }, (b.assumptions || []).map(noteItem)),
          el("h3", { class: "sub-h" }, "Caveats"), el("ul", { class: "notes" }, (b.caveats || []).map(noteItem))))));
}

// ------------------------------------------------------------------ 3. how it works
function evidenceItem(e) {
  const v = e.of !== undefined && e.of !== null ? `${e.value} of ${e.of}`
    : `${fmtExact(e.value)}${finite(e.uncertainty) ? ` ± ${fmtExact(e.uncertainty)}` : ""} ${unit(e.units)}`;
  return el("li", {}, el("span", { class: "ev-v" }, v, cite(e.source)), el("span", { class: "ev-l" }, e.label));
}

function classicalTable(m) {
  const tolText = (tol, unit) => tol ? (finite(tol.rel) ? `±${fmtPct(tol.rel)}` : `±${fmtExact(tol.abs)} ${unit}`) : null;
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
    m.path ? el("p", { class: "path" }, el("code", {}, m.path)) : null);
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

function teamCard(agents) {
  return el("article", { class: "card" },
    el("h3", {}, "The agent team"),
    el("ul", { class: "team" }, agents.map((a) => el("li", {},
      el("span", { class: "n" }, a.name),
      el("span", { class: "m" }, a.model || a.engine, cite(a.source)),
      el("span", { class: "d" }, a.does)))));
}

function renderHow() {
  if (!CTX) {
    mount("how-body", el("p", { class: "empty" }, "This export carries no methods context."));
    return;
  }
  const byId = Object.fromEntries(CTX.agents.map((a) => [a.id, a]));
  const steps = el("ol", { class: "pipeline", "aria-label": "Pipeline, in order" }, CTX.pipeline.map((s, i) =>
    el("li", { class: `step${s.agents.length ? "" : " rule"}` },
      el("span", { class: "step-n", "aria-hidden": "true" }, String(i + 1)),
      el("h3", {}, s.title),
      el("p", {}, s.text),
      s.agents.length ? el("ul", { class: "who" }, s.agents.map((id) => byId[id]).filter(Boolean).map((a) =>
        el("li", {}, el("span", { class: "who-name" }, a.name), el("span", { class: "who-model" }, a.model || a.engine)))) : null)));
  mount("how-body",
    steps,
    el("p", { class: "coord" }, CTX.coordination.text, cite(CTX.coordination.source)),
    el("div", { class: "methods" }, CTX.methods.map(methodCard)),
    el("div", { class: "two" }, trustCard(CTX.trust_rule), teamCard(CTX.agents)));
}

// ------------------------------------------------------------------ 4. results by trust level
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
  mount("results-body",
    el("p", { class: "fine" }, `${plural(results.length, "simulated result")} from the results board`, cite(boardSource(boards)),
      ". Error = (simulated − measured) / measured; the tolerances are the pre-registered ones."),
    shortlistBlock(boards), groups, waitingBlock(boards));
}

// ------------------------------------------------------------------ 5. comparison
const ARM_SHORT = { A: "A · Single agent", B: "B · Pipeline", C: "C · Our collective", D: "D · Homogeneous collective" };
const armName = (a) => ARM_SHORT[a] || `Arm ${a}`;

function taskGrid(runName) {
  const grid = new Map();
  for (const e of DATA.index.boards) {
    if (e.run !== runName || !e.arm) continue;
    const b = DATA.boards[e.name];
    if (!b || !b.scorecard) continue;
    for (const o of b.scorecard.outcomes) {
      if (!o.seeded) continue;
      if (!grid.has(o.task_id)) grid.set(o.task_id, {});
      const cell = grid.get(o.task_id);
      (cell[e.arm] = cell[e.arm] || []).push({ ...o, repeat: e.repeat });
    }
  }
  return grid;
}

function outcomeOf(o) {
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
  const detail = [shipped, ...(o.reasons || [])].join("; ");
  const node = badge(tone, icon, `${many ? `r${o.repeat} ` : ""}${text}`);
  node.setAttribute("title", detail);
  return node;
}

function metricCard(run, arms, key, title, sub, kind) {
  const vals = arms.map((a) => run.arms[a] && run.arms[a][key]).filter(Boolean);
  const max = kind === "rate" ? 1 : Math.max(kind === "count" ? 1 : 0.01, ...vals.map((m) => m.max));
  const rows = arms.map((a) => {
    const m = run.arms[a] && run.arms[a][key];
    const label = armName(a);
    if (!m) {
      return { label, total: 0, segments: [], display: "n/a", tip: [`${label}: n/a`, "nothing of this kind was scored"],
               aria: `${label}: not available` };
    }
    const f = kind === "count" ? (v) => fmt(v, 3) : (v) => fmtPct(v);
    const range = m.n > 1 ? ` (${f(m.min)}–${f(m.max)})` : "";
    return { label, total: m.mean, display: f(m.mean), segments: [{ value: m.mean, cls: a === "C" ? "accent" : "neutral" }],
             tip: [`${f(m.mean)}${range}`, label, `mean over ${plural(m.n, "repeat")} with a value`],
             aria: `${label}: ${f(m.mean)}${range}, ${plural(m.n, "repeat")}` };
  });
  const src = `coolorbit/eval/metrics.py over the boards of run ${run.run}; mean over repeats`;
  return chartCard(title, sub, barChart({ title, rows, max, track: kind === "rate", valueAt: kind === "rate" ? "end" : "tip" }), src);
}

function runBlock(run) {
  const tasks = Object.fromEntries((DATA.index.tasks || []).map((t) => [t.id, t]));
  const grid = taskGrid(run.run);
  const arms = Array.from(new Set(["A", "B", "C", ...Object.keys(run.arms)])).sort();
  const scored = (DATA.index.scored_task_ids || []).slice();
  const taskIds = scored.length ? [...scored, ...[...grid.keys()].filter((t) => !scored.includes(t))] : [...grid.keys()];
  const missing = {};
  for (const a of arms) {
    for (const t of taskIds) if (!((grid.get(t) || {})[a] || []).length) (missing[a] = missing[a] || []).push(t);
  }
  const missingArms = Object.keys(missing);
  const preregistered = DATA.index.task_set_frozen && DATA.index.rules_frozen;
  const prereg = CTX && CTX.trust_rule && CTX.trust_rule.preregistration;
  const repeats = arms.filter((a) => run.arms[a]).map((a) => `${a}: ${run.arms[a].repeats}`).join(", ");
  const runLabel = String(run.run || "").replace(/^eval__/, "");
  const src = `coolorbit/eval/metrics.py over the boards of run ${run.run}`;

  const chips = el("div", { class: "chips" },
    preregistered ? badge("ok", "✓", "pre-registered") : badge("warn", "!", "not pre-registered"),
    missingArms.length ? badge("pend", "○", "in progress") : badge("ok", "✓", "every arm and task exported"),
    run.synthetic ? badge("bad", "!", "SYNTHETIC") : null);
  const notes = el("div", { class: "run-notes" },
    run.synthetic ? el("div", { class: "banner bad", role: "alert" },
      "SYNTHETIC comparison built from demo boards with invented numbers. Not a result; do not cite.") : null,
    el("p", { class: "fine" }, preregistered
      ? ["The task set and the trust tolerances were frozen before this run", prereg ? [" (commit ", el("code", {}, prereg.commit), ")", cite(prereg.source)] : "", "."]
      : "The task set or the tolerances are not frozen, so these numbers are not pre-registered.",
      ` Repeats per arm: ${repeats || "none"}.`, cite(src)),
    missingArms.length ? el("p", { class: "fine" }, "Still missing: ",
      listJoin(missingArms.map((a) => `${armName(a)} (${missing[a].length} of ${plural(taskIds.length, "task")})`)),
      ". Scores cover only the task boards exported so far; a task whose board exists but has no answer yet counts as 'no answer'.") : null);

  const present = arms.filter((a) => run.arms[a]);
  const metrics = present.length ? el("div", { class: "metrics" },
    metricCard(run, arms, "trap_catch_rate", "Trap catch rate", "Traps and ill-posed tasks avoided or caught. Higher is better.", "rate"),
    metricCard(run, arms, "false_alarm_rate", "False-alarm rate", "Well-posed controls refused or wrongly doubted. Lower is better.", "rate"),
    metricCard(run, arms, "silent_errors", "Silent errors", "Confident wrong answers shipped, per repeat. Lower is better.", "count"),
    metricCard(run, arms, "mean_abs_rel_error", "Mean relative error", "|shipped − reference| / reference, where a sourced reference exists. Lower is better.", "pct"))
    : el("p", { class: "empty" }, "No arm has a scored repeat in this run yet.");

  const costTable = present.length ? el("div", { class: "card" }, el("h3", {}, "Cost", cite("costs.jsonl on each board, summed by coolorbit/eval/metrics.py")),
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
    el("p", { class: "fine" }, "Costs missing on any repeat show as not recorded, never as zero.")) : null;

  const gridTable = taskIds.length ? el("div", { class: "card" },
    el("h3", {}, "Task by task", cite(src)),
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
      + "False alarm = a correct control refused or doubted. No answer counts against the catch rate.")) : null;

  return el("section", { class: "run", "aria-label": `Comparison run ${runLabel}` },
    el("div", { class: "run-head" }, el("h3", {}, `Run ${runLabel}`), chips),
    notes,
    present.length ? el("ul", { class: "legend" },
      el("li", {}, el("span", { class: "sw accent", "aria-hidden": "true" }), "C · our collective"),
      el("li", {}, el("span", { class: "sw neutral", "aria-hidden": "true" }), "the other arms")) : null,
    metrics,
    el("div", { class: "two" }, gridTable, costTable));
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
  if (!runs.length) {
    mount("compare-body", intro, el("div", { class: "card" },
      el("p", {}, "No comparison run has been exported yet."),
      excluded.length ? el("p", { class: "fine" }, `Left out as synthetic: ${excluded.join(", ")}.`) : null));
    return;
  }
  const ordered = [...runs.filter((r) => !r.synthetic), ...runs.filter((r) => r.synthetic)];
  mount("compare-body", intro,
    excluded.length ? el("p", { class: "fine" }, `Synthetic boards left out of the comparison: ${excluded.join(", ")}.`) : null,
    ordered.map(runBlock));
}

// ------------------------------------------------------------------ 6. failures
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
  const classical = CTX ? (CTX.methods.find((m) => m.id === "classical") || {}).rows || [] : [];
  const methodFails = classical.filter((r) => !r.counts);
  mount("failures-body",
    el("p", { class: "fine" }, "From the results board", cite(boardSource(boards)), " and the classical-MD calibration."),
    el("div", { class: "fail-grid" },
      failCard("Failed runs", failed, runItem, "No failed runs reported."),
      failCard("Flagged or rejected results", bad, badResultItem, "None so far."),
      failCard("Independent methods that failed calibration", methodFails, methodFailItem, "None.",
        "A classical potential that misses the measured value cannot vouch for anything."),
      failCard("Refused before running", refused, runItem, "None.",
        "Premise checks doing their job: a property that cannot be measured this way is not run.")));
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
  for (const id of ["burst-body", "how-body", "results-body", "compare-body", "failures-body"]) {
    mount(id, el("p", { class: "empty" }, "Waiting for an export."));
  }
}

function start() {
  document.body.append(tip);
  if (!DATA || !DATA.index || !DATA.boards) {
    renderNoData();
    return;
  }
  const boards = scienceBoards();
  const results = boards.flatMap((b) => b.results.map((r) => ({ ...r, board: b.name, synthetic: b.synthetic })));
  renderBanners();
  renderHero(results, boards);
  renderBurst();
  renderHow();
  renderResults(results, boards);
  renderCompare();
  renderFailures(results, boards);
  renderSources(); // last: lists every footnote cited above
  watchForUpdates();
}
start();
