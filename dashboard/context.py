"""Context for the judges' dashboard: the question, the wax-vs-metal burst calculation and the methods, with sources.

Built at export time from repo files only and carried in ``data.js`` as ``context`` (dashboard/export.py), so the
GB10 push loop publishes it together with the boards:

  scripts/burst_calc.py + data/burst/inputs.json        one compute burst, paraffin vs metals (its evaluate())
  configs/trust_rules.json                              the frozen tolerances (the pre-registration)
  configs/llm_roles.json                                which Claude model each agent role uses
  coolorbit/agents/ranker_config.json                   the operating window
  coolorbit/agents/scout_seeds.json                     the Scouts (one per material family)
  coolorbit/sim/melting.py, coolorbit/hooks/superheating.py   the ALCHEMI (nvalchemi) APIs our code imports
  data/calibration/tier1.json                           measured values (classical-check gate, hook control)
  runs/classical/<run>/metals/<El>/analysis_{coexistence,latent}.json   classical MD (MEAM/LAMMPS) checks
  docs/pitch/outline.md (claims register)               wording and sources of the framing lines (S1, S2, ...)
  docs/results/comparison-cmp1.md                       the written findings of comparison run cmp-1, repeat 1
  docs/chemistry-lead-memo-2026-10-31.md                the chemistry lead's model (author line) and decisions

``agent_graph`` describes the Agents tab: who does what around ALCHEMI, the model each role uses, the flows between
them, and the real case the page replays from the exported board. Live counts and statuses are not here: the page
derives them from the exported boards.

Rule (tested in tests/test_dashboard_context.py): every dict in the context that holds a number also carries a
non-empty ``source``. A material with any unsourced burst input is listed under ``skipped`` with what is missing,
never computed and never filled in.

    python -m dashboard.context          # print the context as JSON
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
SCHEMA = "coolorbit.context/1"
BURST_SCRIPT = REPO / "scripts" / "burst_calc.py"
BURST_INPUTS = REPO / "data" / "burst" / "inputs.json"
TRUST_RULES = REPO / "configs" / "trust_rules.json"
LLM_ROLES = REPO / "configs" / "llm_roles.json"
RANKER_CONFIG = REPO / "coolorbit" / "agents" / "ranker_config.json"
CALIBRATION = REPO / "data" / "calibration" / "tier1.json"
MELTING_PY = REPO / "coolorbit" / "sim" / "melting.py"
HOOK_PY = REPO / "coolorbit" / "hooks" / "superheating.py"
SCOUT_SEEDS = REPO / "coolorbit" / "agents" / "scout_seeds.json"
CLASSICAL_ROOT = REPO / "runs" / "classical"
# Runs in priority order: each element is read from the first run that has it (the refined Bi bracket and Bi's
# latent heat are in meam-bi-refine; In and Sn's widest brackets are in meam-1).
CLASSICAL_RUN = ("meam-bi-refine", "meam-1")
GITHUB = "https://github.com/MadhanJ05/cool-orbit"

# scripts/burst_calc.py's argparse defaults (a test checks they still match the script).
DEFAULT_SCENARIO = {"power_W": 1000.0, "minutes": 15.0, "dT_K": 10.0, "fin_mm": 0.5}

OUTLINE = "docs/pitch/outline.md"
MEMO = "docs/chemistry-lead-memo-2026-10-31.md"
CMP1_DOC = "docs/results/comparison-cmp1.md"
BURST_SRC = "scripts/burst_calc.py evaluate() with data/burst/inputs.json"
RULES_SRC = "configs/trust_rules.json (pre-registered by the chemistry lead, " + MEMO + ")"
PREREGISTRATION = {
    "commit": "2851934",
    "source": "git commit 2851934 'PRE-REGISTRATION: freeze trust tolerances, the 8-task comparison set and the "
              "operating window before any comparison run'; coolorbit/eval/tasks.json (_preregistration)",
}

# Claims register in docs/pitch/outline.md (each source opened and read by the team on 2026-10-31).
CLAIMS = {
    "S1": ("TechCrunch, Oct 1 2026",
           "https://techcrunch.com/2026/10/01/google-thinks-spacexs-starship-has-to-launch-1600-times-before-"
           "space-data-centers-get-off-the-ground"),
    "S2": ("NPR, Oct 1 2026 (corrected Oct 2)",
           "https://www.npr.org/2026/10/01/nx-s1-5983697/project-suncatcher-google-ai-data-center-space"),
    "S3": ("Google, \"Learn about Google's Project Suncatcher\"",
           "https://blog.google/innovation-and-ai/models-and-research/google-research/google-project-suncatcher-facts/"),
    "S4": ("Quinn, Stieber, Sheth, Ahlstrom, \"Phase Change Material Heat Sink for an ISS Flight Experiment\", "
           "NASA NTRS 20140016955", "https://ntrs.nasa.gov/citations/20140016955"),
}
HOOK_CONTROL_SRC = ("GPU positive control on Cu, quick settings: runs/melting/20261031T171151Z_Cu_heating_quick/result.json, "
                    "runs/agent/e2e-cu-quick/.../20261031T170508Z_Cu_coexistence_quick/result.json, "
                    "runs/hook_heating_cu_quick.log; commit 2ebedd4 [S19, " + OUTLINE + " claims register]")

FIELDS = [  # (key in data/burst/inputs.json, label, units)
    ("melting_point_K", "Melting point", "K"),
    ("latent_heat_J_per_kg", "Latent heat", "J/kg"),
    ("density_solid_kg_m3", "Solid density", "kg/m3"),
    ("density_liquid_kg_m3", "Liquid density", "kg/m3"),
    ("conductivity_liquid_W_mK", "Liquid thermal conductivity", "W/m/K"),
]
FIELD_LABEL = {k: label for k, label, _ in FIELDS}


# --------------------------------------------------------------------------- helpers

def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def _claim(sid: str, extra: str = "") -> dict:
    text, url = CLAIMS[sid]
    return {"source": f"{text} [{sid}, {OUTLINE} claims register]{extra}", "url": url}


def _within(value: float, ref: float, tol: dict) -> bool:
    return abs(value - ref) <= max(tol.get("abs") or 0.0, (tol.get("rel") or 0.0) * abs(ref))


def load_calibration(path: str | Path = CALIBRATION) -> dict:
    """{(normalised candidate, property): record} for every calibration record with a value AND a source."""
    path = Path(path)
    if not path.exists():
        return {}
    data = _read_json(path)
    records = data["records"] if isinstance(data, dict) else data
    return {(_norm(r.get("candidate")), r.get("property")): r for r in records
            if r.get("value") is not None and str(r.get("source") or "").strip()}


def load_burst_calc(path: Path = BURST_SCRIPT) -> ModuleType:
    """Import scripts/burst_calc.py by path (scripts/ is not a package). Its main() only runs under __main__."""
    spec = importlib.util.spec_from_file_location("coolorbit_scripts_burst_calc", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_name(model_id: Optional[str]) -> Optional[str]:
    """'claude-opus-5-5' -> 'Claude Opus 5.5'."""
    if not model_id:
        return None
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)-(\d+)", model_id)
    return f"Claude {m.group(1).capitalize()} {m.group(2)}.{m.group(3)}" if m else model_id


def short_model_name(model_id: Optional[str]) -> Optional[str]:
    """'claude-opus-5-5' -> 'Opus 5.5' (the Agents tab's box labels)."""
    if not model_id:
        return None
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)-(\d+)", model_id)
    return f"{m.group(1).capitalize()} {m.group(2)}.{m.group(3)}" if m else model_id


# --------------------------------------------------------------------------- the question

def question() -> dict:
    return {
        "title": "Which thermal battery best buffers AI-chip heat in orbit, and can we trust the simulations?",
        "subtitle": "A thermal battery (a phase-change material) melts to soak up a burst of chip heat and refreezes "
                    "while the radiator catches up.",
        "lines": [
            {"text": "Google's Project Suncatcher put its first prototype satellite in orbit on Oct 1, 2026: four TPUs "
                     "on about a kilowatt of continuous power. It runs one TPU at a time, in 15-minute bursts, \"to "
                     "avoid straining the satellite's power and thermal management systems.\"", **_claim("S1")},
            {"text": "\"In a vacuum, you can only diffuse heat via radiators.\" Google calls cooling orbital data "
                     "centers \"a crucial research challenge.\"", **_claim("S3")},
            {"text": "\"The radiators are one of the heaviest components on the current mission\" (Travis Beals, lead "
                     "of Project Suncatcher).", **_claim("S2")},
            {"text": "A thermal battery (a phase-change material) melts to soak up a burst and refreezes while the "
                     "radiator catches up, so the radiator can be sized for the average load instead of the peak. "
                     "NASA built a paraffin-wax one for an ISS flight experiment.",
             **_claim("S4", "; buffering idea: CLAUDE.md sections 3-4")},
        ],
        "physics": {"text": "A better material doesn't make the chips produce less heat and doesn't remove the "
                            "radiator; it buffers the bursts.",
                    "source": "CLAUDE.md section 3; " + OUTLINE + " slide 3"},
    }


# --------------------------------------------------------------------------- burst: wax vs metals

def _kind(name: str) -> str:
    low = name.lower()
    if "paraffin" in low:
        return "paraffin"
    if re.search(r"\b(metal|alloy|gallium|indium|tin|bismuth)\b", low):
        return "metal"
    return "other"


def _label(name: str, kind: str) -> str:
    base = name.partition(" (")[0]
    if kind == "paraffin" and base:
        return f"Paraffin ({base})"
    return name[:1].upper() + name[1:]


def _sourced_input(key: str, entry: dict, label: str, units: str) -> dict:
    return {"key": key, "label": label, "value": entry["value"], "units": units,
            "source": str(entry["source"]).strip()}


def burst(inputs_path: str | Path = BURST_INPUTS, scenario: Optional[dict] = None,
          window: Optional[tuple[float, float]] = None) -> dict:
    bc = load_burst_calc()
    sc = {**DEFAULT_SCENARIO, **(scenario or {})}
    scen = {
        "power_W": sc["power_W"], "minutes": sc["minutes"], "dT_K": sc["dT_K"], "fin_mm": sc["fin_mm"],
        "energy_kJ": sc["power_W"] * sc["minutes"] * 60 / 1000,
        "source": "scripts/burst_calc.py default scenario: about 1 kW for one 15-minute burst (Project Suncatcher, "
                  "TechCrunch [S1]); the 10 K wall superheat and 0.5 mm aluminium fins are model choices",
    }
    out: dict = {"title": f"Wax vs metals for a {sc['minutes']:g}-minute burst", "scenario": scen,
                 "script": "scripts/burst_calc.py", "inputs_file": _rel(Path(inputs_path))}
    data = _read_json(inputs_path)
    fin = data["fin_material"]
    if not all(bc.sourced(fin.get(k, {})) for k in ("density_kg_m3", "conductivity_W_mK")):
        out["error"] = "the fin material (aluminium) values are not sourced yet; see data/burst/inputs.json"
        return out
    out["fin"] = {"name": fin["name"],
                  "density": _sourced_input("density_kg_m3", fin["density_kg_m3"], "Density", "kg/m3")}

    rows, skipped = [], []
    for mat in data["materials"]:
        name = mat["name"]
        missing = [k for k in bc.NEEDED if not bc.sourced(mat.get(k, {}))]
        sourced_inputs = {k: _sourced_input(k, mat[k], label, units)
                          for k, label, units in FIELDS if bc.sourced(mat.get(k, {}))}
        if missing:
            skipped.append({
                "material": name,
                "missing": missing,
                "missing_labels": [FIELD_LABEL.get(k, k) for k in missing],
                # Only the status part of each note ("pending: chemistry lead"): the rest can quote unsourced numbers.
                "notes": sorted({str((mat.get(k) or {}).get("note")).split(" (")[0].strip() for k in missing
                                 if (mat.get(k) or {}).get("note")}),
                "sourced": list(sourced_inputs.values()),
            })
            continue
        r = bc.evaluate(mat, fin, sc["power_W"], sc["minutes"] * 60, sc["dT_K"], sc["fin_mm"] / 1000)
        v = {k: mat[k]["value"] for k in bc.NEEDED}
        kind = _kind(name)
        rows.append({
            "name": name, "label": _label(name, kind), "kind": kind,
            "result": {**{k: val for k, val in r.items() if k != "material"},
                       "source": BURST_SRC + " (scenario and inputs shown here)"},
            "inputs": sourced_inputs,
            "derived": {
                "latent_heat_kJ_per_kg": {
                    "value": v["latent_heat_J_per_kg"] / 1000, "units": "kJ/kg",
                    "source": "latent heat from data/burst/inputs.json, divided by 1000"},
                "latent_heat_kJ_per_L": {
                    "value": round(v["latent_heat_J_per_kg"] * min(v["density_solid_kg_m3"],
                                                                   v["density_liquid_kg_m3"]) / 1e6, 3),
                    "units": "kJ/L",
                    "source": "latent heat x the lower of the solid and liquid densities (the bulkier phase sets "
                              "the container, as in scripts/burst_calc.py); inputs from data/burst/inputs.json"},
            },
        })

    waxes = [m for m in rows if m["kind"] == "paraffin"]
    metals = [m for m in rows if m["kind"] == "metal"]
    ratio_src = "ratio of the two " + BURST_SRC + " results shown here"
    comparisons = []
    for w in waxes:
        for m in metals:
            wr, mr = w["result"], m["result"]
            comparisons.append({
                "wax": w["label"], "metal": m["label"],
                "total_kg": {"value": round(mr["total_kg"] / wr["total_kg"], 3),
                             "meaning": f"{m['label']} total mass / {w['label']} total mass", "source": ratio_src},
                "area": {"value": round(wr["area_m2"] / mr["area_m2"], 3),
                         "meaning": f"{w['label']} heat-exchange area / {m['label']} area", "source": ratio_src},
                "volume": {"value": round(wr["pcm_volume_L"] / mr["pcm_volume_L"], 3),
                           "meaning": f"{w['label']} container volume / {m['label']} volume", "source": ratio_src},
                "conductivity": {
                    "value": round(m["inputs"]["conductivity_liquid_W_mK"]["value"]
                                   / w["inputs"]["conductivity_liquid_W_mK"]["value"], 1),
                    "meaning": f"{m['label']} / {w['label']} liquid thermal conductivity",
                    "source": "ratio of the two sourced liquid conductivities in data/burst/inputs.json"},
            })

    split = bool(comparisons) and len(waxes) + len(metals) == len(rows) and all(
        c["total_kg"]["value"] > 1 and c["area"]["value"] > 1 and c["volume"]["value"] > 1 for c in comparisons)
    if split:
        out["headline"] = ("Per kilogram, paraffin wax still beats the metals we could source, even counting its fins; "
                           "metals win on space and simplicity.")
        out["takeaway"] = ("Per kilogram, wax wins even with fins; metals win on space and simplicity. Our search "
                           "targets the gap: metal-like conductivity with more heat per kilogram, or solid-solid "
                           "PCMs with no liquid at all.")
    elif comparisons:
        out["headline"] = "The wax-vs-metal burst comparison below is computed from sourced inputs only."
        out["takeaway"] = ("With the current inputs the comparison no longer splits simply (wax lighter, metals "
                           "smaller); read the numbers below.")
    else:
        out["headline"] = "Not enough sourced materials for a wax-vs-metal burst comparison yet."
        out["takeaway"] = out["headline"]

    bc_src = "scripts/burst_calc.py (model docstring)"
    out["model"] = [
        {"text": "Quasi-steady 1-D melting (a Stefan problem): constant power P for the whole burst t; heat enters "
                 "from a wall of area A and crosses the already-melted layer by conduction.", "source": bc_src},
        {"text": "PCM mass m = P t / L (latent heat only).", "source": bc_src},
        {"text": "Area that keeps the wall within dT of the melting point: A = P sqrt(t / (L rho_liquid k_liquid dT)).",
         "source": bc_src},
        {"text": "Fins: aluminium sheet of thickness d giving A on two faces, m_fin = rho_Al d A / 2. Container "
                 "volume = m / min(rho_solid, rho_liquid): the bulkier phase sets the container.", "source": bc_src},
    ]
    out["assumptions"] = [
        {"text": "Fin efficiency 1 (no temperature drop along the fins). Favours paraffin, which needs far more fin "
                 "area.", "source": bc_src},
        {"text": "No container or interface mass. Favours paraffin's larger container.", "source": bc_src},
        {"text": "No sensible heat, latent heat only. The script lists this among the assumptions that favour "
                 "paraffin, so a metal win is conservative.", "source": bc_src},
    ]
    caveats = []
    if window is not None:
        lo, hi = window
        outside = [(m["label"], m["inputs"]["melting_point_K"]["value"]) for m in rows
                   if "melting_point_K" in m["inputs"] and not lo <= m["inputs"]["melting_point_K"]["value"] <= hi]
        if outside:
            which = "Both computed materials" if len(outside) == len(rows) == 2 else (
                "All computed materials" if len(outside) == len(rows) else "Some computed materials")
            caveats.append({
                "text": f"{which} melt outside the pre-registered operating window of {lo:g}-{hi:g} K ("
                        + ", ".join(f"{label} {t:g} K" for label, t in outside)
                        + "), so this shows the trade-off, not shortlist candidates.",
                "source": "coolorbit/agents/ranker_config.json (window decided 2026-10-31, " + MEMO + "); melting "
                          "points from data/burst/inputs.json"})
    if any("gallium" in m["name"].lower() for m in metals):
        caveats.append({
            "text": "Liquid gallium penetrates aluminium without applied stress, so a real gallium device cannot use "
                    "the aluminium fins assumed here.",
            "source": MEMO + ", 'What a chemistry judge will challenge' item 3 (Hugo & Hoagland, Scripta Mater. "
                             "38(3), 1998; abstract)"})
    caveats.append({
        "text": "Per-kilogram figures ignore the container. NASA's flown ISS PCM heat sink was mostly aluminium: 856 kJ "
                "of latent capacity in a 24.2 L envelope, about 35 kJ/L for the device vs about 183 kJ/L for bulk wax.",
        "source": MEMO + ", burst constants (Quinn et al., ICES-2015-167, NASA NTRS 20150003481 [memo S20])"})
    out.update({"materials": rows, "comparisons": comparisons, "skipped": skipped, "caveats": caveats,
                "source": BURST_SRC})
    return out


# --------------------------------------------------------------------------- methods on ALCHEMI

def _gb10_throughput() -> Optional[dict]:
    if not MELTING_PY.exists():
        return None
    m = re.search(r"^GB10_ATOM_STEPS_PER_S\s*=\s*([\d_]+)\s*#\s*(.*)$", MELTING_PY.read_text(encoding="utf-8"),
                  re.MULTILINE)
    if not m:
        return None
    return {"label": "measured GB10 throughput, MACE-MPA-0", "value": int(m.group(1).replace("_", "")),
            "units": "atom-steps/s",
            "source": f"coolorbit/sim/melting.py GB10_ATOM_STEPS_PER_S ({m.group(2).strip()})"}


CLASSICAL_PROPS = (  # (analysis file, property, label, display units, divide the J/kg or K value by)
    ("analysis_coexistence.json", "melting_point", "melting point", "K", 1.0),
    ("analysis_latent.json", "latent_heat", "latent heat", "kJ/kg", 1000.0),
)


def _classical_row(f: Path, element: str, prop: str, label: str, units: str, scale: float,
                   rules: dict, calibration: dict) -> dict:
    tol = ((rules.get("properties") or {}).get(prop) or {}).get("tier1") or {}
    a = _read_json(f)
    path = _rel(f)
    base = {"element": element, "property": prop, "label": f"{element} {label}"}
    if a.get("status") != "ok" or a.get("value") is None:
        return {**base, "counts": False, "status": "no result",
                "note": f"analysis status {a.get('status')}: {a.get('reason')} ({path})"}
    protocol = "two-phase coexistence" if prop == "melting_point" else "NPT solid vs liquid enthalpy at its own Tm"
    conv = f", J/kg / {scale:g}" if scale != 1 else ""
    unc = a.get("uncertainty")
    row = {**base, "status": "ok",
           "meam": {"value": round(a["value"] / scale, 3), "uncertainty": round(unc / scale, 3) if unc is not None else None,
                    "units": units,
                    "source": f"{path} ({(a.get('method') or {}).get('model', 'MEAM')}, LAMMPS, {protocol}{conv})"}}
    meas = calibration.get((_norm(element), prop))
    if meas and tol:
        row["measured"] = {"value": round(meas["value"] / scale, 3), "units": units,
                           "source": meas["source"] + (conv and f" ({conv[2:]})")}
        row["rel_error"] = {"value": round((a["value"] - meas["value"]) / meas["value"], 4),
                            "source": "MEAM value vs measured value, both shown here"}
        row["counts"] = _within(a["value"], meas["value"], tol)
    else:
        row["counts"] = False
        row["note"] = "no sourced measured value or no tier1 tolerance, so this potential cannot be calibrated"
    return row


def classical_rows(rules: dict, calibration: dict, root: Path = CLASSICAL_ROOT,
                   run: "str | tuple[str, ...]" = CLASSICAL_RUN) -> list[dict]:
    """MEAM melting points (and latent heats where run) vs the measured values, with the gate of
    scripts/post_classical_checks.py. ``run`` is one run or several in priority order: each element is read from the
    first run that has its coexistence analysis, and its latent heat from that same run."""
    runs = (run,) if isinstance(run, str) else tuple(run)
    chosen: dict[str, Path] = {}
    for r in runs:
        for f in sorted((Path(root) / r / "metals").glob("*/analysis_coexistence.json")):
            chosen.setdefault(f.parent.name, f.parent)
    rows = []
    for element in sorted(chosen):
        for fname, prop, label, units, scale in CLASSICAL_PROPS:
            f = chosen[element] / fname
            if f.exists():
                rows.append(_classical_row(f, element, prop, label, units, scale, rules, calibration))
    if not any(r["element"] == "Ga" for r in rows):
        rows.append({"element": "Ga", "label": "Ga", "counts": False, "status": "dropped",
                     "note": "No run: the only LAMMPS-ready potential distorts alpha-Ga (relaxed cell off by -8.4% and "
                             "+6.6%; the next neighbour shell collapses toward the close pair), so its melting point "
                             "would not be gallium's.",
                     "source": "data/nebius/README.md; data/nebius/metals/Ga/stability/RESULT.md"})
    return rows


def methods(rules: dict, calibration: dict, classical_root: Path = CLASSICAL_ROOT,
            classical_run: "str | tuple[str, ...]" = CLASSICAL_RUN) -> list[dict]:
    cu = calibration.get(("cu", "melting_point"))
    tol = ((rules.get("properties") or {}).get("melting_point") or {}).get("tier1") or {}
    ltol = ((rules.get("properties") or {}).get("latent_heat") or {}).get("tier1") or {}
    throughput = _gb10_throughput()
    return [
        {"id": "alchemi", "title": "NVIDIA ALCHEMI on the Dell Pro Max GB10",
         "text": "Every MLIP simulation runs on the GB10 (Blackwell GPU, ARM64) through the ALCHEMI Toolkit's batched "
                 "GPU dynamics, with the MACE-MPA-0 machine-learned force field: many temperatures in one GPU job.",
         "limit": "MACE-MP models are trained on inorganic materials. Waxes are out of their domain (no dispersion), so "
                  "the Simulator refuses them and asks for classical MD.",
         "path": "coolorbit/sim/melting.py",
         "evidence": [throughput] if throughput else [],
         "source": "coolorbit/sim/melting.py (nvalchemi NPT dynamics, MACEWrapper); CLAUDE.md sections 8 and 16; "
                   + OUTLINE + " slide 11"},
        {"id": "coexistence", "title": "Melting point: two-phase coexistence",
         "text": "Half crystal, half liquid in one box, held at several temperatures at once. Above the melting point "
                 "the liquid grows; below it the crystal grows; the melting point is where the interface stays put. "
                 "Temperatures where neither side wins within the run are reported as undecided and rerun longer.",
         "trap": "Never single-phase heating: a perfect crystal has nowhere to start melting, superheats, and reports "
                 "a melting point that is too high. That is the trap the referees and the hook look for.",
         "path": "coolorbit/sim/melting.py",
         "source": "coolorbit/sim/melting.py; CLAUDE.md section 11; commit 35e20a6 (undecided brackets)"},
        {"id": "latent", "title": "Latent heat: enthalpy difference",
         "text": "Solid and liquid are simulated side by side (NPT) at the transition temperature; latent heat = "
                 "H(liquid) - H(solid), converted to J/kg. The solid's cell shape is relaxed anisotropically first, "
                 "so non-cubic crystals are not squeezed.",
         "alloy": "Alloys: H(liquid alloy) - sum of x_i H(pure solid i). A known approximation: it treats the solid as "
                  "a mixture of the pure elements. Real eutectic solids can contain intermetallic compounds that are "
                  "more stable than the separate metals, so the true latent heat is likely higher and ours an "
                  "underestimate; referees flag it.",
         "path": "coolorbit/sim/melting.py",
         "source": "coolorbit/sim/melting.py; " + OUTLINE + " slide 11 (alloy approximation); the Scouts' alloy "
                   "requests on the board"},
        {"id": "hook", "title": "SuperheatingDetectorHook, an ALCHEMI dynamics hook",
         "text": "Plugs into any nvalchemi dynamics run. It notices when a system starts as a perfect crystal and then "
                 "melts: that crystal superheated, so the temperature it melted at overstates the melting point. "
                 "Two-phase boxes and liquids are never flagged. Packaged for other teams to reuse "
                 "(coolorbit/hooks/README.md).",
         "path": "coolorbit/hooks/superheating.py",
         "evidence": [e for e in (
             {"label": "melting perfect Cu crystals flagged on the GPU", "value": 3, "of": 3,
              "source": HOOK_CONTROL_SRC},
             {"label": "two-phase Cu boxes flagged (false alarms)", "value": 0, "of": 4, "source": HOOK_CONTROL_SRC},
             {"label": "what naive single-phase heating reports for Cu (quick settings)", "value": 1600,
              "uncertainty": 100, "units": "K", "source": HOOK_CONTROL_SRC},
             {"label": "measured Cu melting point", "value": cu["value"], "units": cu.get("units"),
              "source": cu["source"]} if cu else None,
         ) if e],
         "source": "coolorbit/hooks/superheating.py"},
        {"id": "classical", "title": "Independent check: classical MD",
         "text": "A fundamentally different method from an MLIP: classical molecular dynamics with published 2NN-MEAM "
                 "potentials from the NIST Interatomic Potentials Repository, run with LAMMPS on the GB10's CPU (the "
                 "Nebius LAMMPS app rejected every submission format we tried), same two-phase coexistence protocol. "
                 "A potential counts as an independent check only if it recovers the measured value within the "
                 "pre-registered tier1 tolerance; otherwise its check is posted as inconclusive and cannot confirm or "
                 "flag anything. Only bismuth's passes; tin's, indium's and gallium's were excluded by that gate.",
         "caveat": "Bi's potential was fitted to reproduce Bi's melting point, so for Bi the check is partly anchored "
                   "to the experiment it is meant to test independently (data/nebius/README.md, citing Zhou et al., "
                   "MSMSE 29, 065008 (2021)).",
         "path": "scripts/post_classical_checks.py",
         "tolerance": {"rel": tol.get("rel"), "abs": tol.get("abs"), "source": RULES_SRC} if tol else None,
         "latent_tolerance": ({"rel": ltol.get("rel"), "abs": ltol.get("abs"), "source": RULES_SRC}
                              if ltol else None),
         "rows": classical_rows(rules, calibration, classical_root, classical_run),
         "source": "data/nebius/README.md; scripts/post_classical_checks.py; docs/results/classical-check.md; "
                   + "; ".join("runs/classical/" + r + "/" for r in
                               ((classical_run,) if isinstance(classical_run, str) else classical_run))},
    ]


# --------------------------------------------------------------------------- the Agents tab: who does what around ALCHEMI

# Every line of text below paraphrases the docstring or code of the file named in its "source".
NVALCHEMI_IMPORT = re.compile(r"^[ \t]*from[ \t]+(nvalchemi[\w.]*)[ \t]+import[ \t]+([^\n#]+)", re.MULTILINE)
ALCHEMI_WHAT = {  # what each imported nvalchemi name does in OUR code (read in coolorbit/sim/melting.py, hooks/)
    "NPT": ("dynamics", "Batched NPT dynamics: one temperature per system, all systems in one GPU job; isotropic or "
                        "anisotropic barostat (npt() in melting.py)."),
    "NVTLangevin": ("dynamics", "Langevin NVT. Used only to melt and quench the preparation boxes (nvt() in melting.py); "
                               "no reported number is measured under it."),
    "MACEWrapper": ("model", "Loads the MACE-MPA-0 checkpoint (medium-mpa-0) with cuEquivariance kernels in float32, "
                             "returning energies, forces and stress (load_model() in melting.py)."),
    "AtomicData": ("data", "One atomic system as a graph: positions, cell, species, velocities."),
    "Batch": ("data", "Many systems batched together for the GPU (one per trial temperature)."),
    "AtomCategory": ("data", "Marks atoms as frozen or mobile (the crystal half of a two-phase box)."),
    "DynamicsStage": ("hooks", "The points in every step where hooks run (BEFORE_COMPUTE, AFTER_STEP, ...)."),
    "NeighborListHook": ("hooks", "Rebuilds MACE's neighbour list before every force call."),
    "WrapPeriodicHook": ("hooks", "Wraps atoms back into the periodic cell after each update."),
    "FreezeAtomsHook": ("hooks", "Holds the crystal half fixed while the other half is melted."),
    "NaNDetectorHook": ("hooks", "Checks the run for NaNs every 100 steps."),
    "kinetic_energy_per_graph": ("hooks", "Per-system kinetic energy, for the temperature (a helper in "
                                          "nvalchemi.dynamics.hooks._utils)."),
    "KB_EV": ("hooks", "Boltzmann constant in eV/K."),
}
ALCHEMI_GROUPS = ("dynamics", "model", "data", "hooks")


def alchemi_imports(paths: tuple[Path, ...] = (MELTING_PY, HOOK_PY)) -> list[dict]:
    """Every name our code imports from nvalchemi (the ALCHEMI Toolkit), read from the source files themselves."""
    found: dict[str, dict] = {}
    for path in paths:
        if not Path(path).exists():
            continue
        for module, names in NVALCHEMI_IMPORT.findall(Path(path).read_text(encoding="utf-8")):
            for raw in names.strip().strip("()").split(","):
                name = raw.strip().split(" as ")[0].strip()
                if not name:
                    continue
                group, what = ALCHEMI_WHAT.get(name, ("hooks", f"imported from {module}"))
                entry = found.setdefault(name, {"name": name, "module": module, "group": group, "what": what,
                                                "files": []})
                if _rel(Path(path)) not in entry["files"]:
                    entry["files"].append(_rel(Path(path)))
    out = sorted(found.values(), key=lambda e: (ALCHEMI_GROUPS.index(e["group"]) if e["group"] in ALCHEMI_GROUPS
                                                else len(ALCHEMI_GROUPS), e["name"].lower()))
    for e in out:
        e["source"] = f"from {e['module']} import {e['name']} in " + ", ".join(e["files"])
    return out


def scouts() -> list[dict]:
    """The Scouts: one per family in coolorbit/agents/scout_seeds.json, plus the alloy Scout (its own module)."""
    seeds = _read_json(SCOUT_SEEDS).get("scouts", {}) if SCOUT_SEEDS.exists() else {}
    out = []
    for name, cfg in seeds.items():
        cands = cfg.get("candidates", [])
        out.append({"agent": name, "label": name.removeprefix("scout_").replace("_", " "),
                    "families": sorted({c["family"] for c in cands if c.get("family")}),
                    "enabled": bool(cfg.get("enabled", True)), "general": bool(cfg.get("general")),
                    "budget": cfg.get("budget"), "seeds": len(cands),
                    "source": "coolorbit/agents/scout_seeds.json"})
        if name == "scout_metals":
            out.append({"agent": "scout_alloys", "label": "alloys", "families": ["low_melting_alloy"],
                        "enabled": True, "general": False, "budget": None, "seeds": None,
                        "source": "coolorbit/agents/alloy_scout.py (its candidates are the low_melting_alloy records "
                                  "in data/calibration/tier1.json)"})
    return out


def memo_author(path: Optional[Path] = None) -> Optional[str]:
    """The chemistry lead's model as the memo's author line names it ('Claude Fable 5.1'), or None."""
    path = Path(path) if path else REPO / MEMO
    if not path.exists():
        return None
    m = re.search(r"\*\*Author:\*\*\s*(Claude [A-Z][a-z]+ \d+\.\d+)", path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def lead_decisions(rules: dict, ranker: dict) -> list[dict]:
    """What the chemistry lead decided (docs/chemistry-lead-memo-2026-10-31.md), with the numbers read from the files
    the decisions were applied to, so the page shows what the code actually uses."""
    props = rules.get("properties") or {}

    def rel(prop: str, kind: str) -> Optional[float]:
        return ((props.get(prop) or {}).get(kind) or {}).get("rel")

    rules_src = RULES_SRC + "; " + MEMO + " 'Decisions applied'"
    out = []
    for prop, short, label in (("melting_point", "Tm", "melting point"), ("latent_heat", "L", "latent heat")):
        t1, ag = rel(prop, "tier1"), rel(prop, "agree")
        if t1 is None:
            continue
        out.append({"id": f"tol_{prop}", "short": f"{short} ±{t1:.0%}",
                    "text": f"{label} within ±{t1:.0%} of the measured value"
                            + (f" and ±{ag:.0%} of an independent method" if ag is not None else ""),
                    "value": t1, "applies_to": "trust", "where": "configs/trust_rules.json", "source": rules_src})
    win = ranker.get("window_K") or {}
    if win.get("min") is not None and win.get("max") is not None:
        soft = " (soft)" if "soft" in str(ranker.get("_decision", "")).lower() else ""
        out.append({"id": "window", "short": f"window {win['min']:g}–{win['max']:g} K",
                    "text": f"operating window {win['min']:g}–{win['max']:g} K{soft}: below the accelerator's limit, "
                            "above what a radiator can refreeze against",
                    "min": win["min"], "max": win["max"], "applies_to": "ranker",
                    "where": "coolorbit/agents/ranker_config.json",
                    "source": "coolorbit/agents/ranker_config.json (_decision); " + MEMO + " 'Window'"})
    out += [
        {"id": "gate", "short": "calibrated checks only",
         "text": "an independent check counts only if its own method first recovers the sourced measured value of "
                 "that material within the tier1 tolerance",
         "applies_to": "classical", "where": "scripts/post_classical_checks.py (reliability gate)",
         "source": MEMO + " 'Decisions applied'; configs/trust_rules.json (_note)"},
        {"id": "latent_at_tm", "short": "L at the method's own Tm",
         "text": "latent heat at each method's own melting point (the Scouts request it at the simulated Tm)",
         "applies_to": "scouts", "where": "the Scouts' latent-heat requests (scripts/run_science.py)",
         "source": MEMO + " 'Decisions applied'"},
        {"id": "solid_solid", "short": "solid-solid: NPG in",
         "text": "solid-solid PCMs: include neopentyl glycol (classical MD only; not run that night), exclude "
                 "pentaerythritol",
         "applies_to": None, "where": "decision recorded in the memo", "source": MEMO + " 'Decisions applied'"},
        {"id": "burst", "short": "burst constants",
         "text": "burst constants: liquid n-octadecane conductivity, aluminium density and conductivity, the flown ISS "
                 "heat sink's envelope; Field's metal and In-Bi density and conductivity reported as not found",
         "applies_to": None, "where": "reported in the memo; the burst inputs carry their own sources",
         "source": MEMO + " 'Burst constants'"},
    ]
    return out


# The recursions drawn as inner arcs on the Agents tab. Each names the code that makes it; "evidence" is text that must
# appear in that file (tests/test_dashboard_context.py checks it), so an arc can't outlive the code behind it. "count"
# names the live counter the page computes from the exported boards (dashboard/app.js buildProof).
GRAPH_LOOPS = [
    {"id": "retry", "n": 1, "label": "retry", "title": "Bracket missed → shifted retry", "from": "simulator",
     "to": "simulator", "count": "retries",
     "text": "When nothing froze (or nothing melted) the melting point lies outside the bracket. The Simulator's failed "
             "run report carries a retry request anchored at the nearest decisive temperature, shifted up or down, or a "
             "doubled production run when the interface barely moved; at most three attempts. Protocol 2 refuses a "
             "retry that would repeat a set-up artefact or contradict an earlier bracket.",
     "evidence": [{"path": "coolorbit/sim/simulator_agent.py", "text": "def retry_need("},
                  {"path": "coolorbit/sim/simulator_agent.py", "text": "def retry_decision_v2("},
                  {"path": "coolorbit/sim/simulator_agent.py", "text": "MAX_ATTEMPTS = 3"}],
     "source": "coolorbit/sim/simulator_agent.py (retry_need, retry_decision_v2, MAX_ATTEMPTS)"},
    {"id": "rerun", "n": 2, "label": "rerun", "title": "Referee fail → rerun", "from": "rules", "to": "simulator",
     "count": "reruns",
     "text": "A failing method audit carries a rerun request when the flaw is fixable (two-phase coexistence instead of "
             "single-phase heating, production instead of quick settings, classical MD for an out-of-domain model). "
             "The Simulator links the rerun's result to the flawed one, which marks it superseded. In arm C the "
             "worker reruns its own results when a referee fails them.",
     "evidence": [{"path": "coolorbit/agents/referee_rules.py", "text": "A failing check also carries a **rerun need**"},
                  {"path": "coolorbit/sim/simulator_agent.py", "text": "def find_rerun_target("},
                  {"path": "scripts/run_arm_c.py", "text": "reruns of its own results"}],
     "source": "coolorbit/agents/referee_rules.py (module docstring); coolorbit/sim/simulator_agent.py "
               "(find_rerun_target); scripts/run_arm_c.py (module docstring)"},
    {"id": "ruling", "n": 3, "label": "ruling", "title": "Dispute → adjudicator ruling", "from": "llm",
     "to": "adjudicator", "count": "rulings",
     "text": "When two referees' method audits of the same result disagree, the adjudicator reads the result and both "
             "arguments and rules. The trust rule then counts the ruling instead of the audits it settles; if it "
             "upholds a failure the rules missed, it attaches the rerun request.",
     "evidence": [{"path": "coolorbit/agents/referee_llm.py", "text": "``Adjudicator`` (agent ``adjudicator``"},
                  {"path": "coolorbit/board/status.py", "text": "def settled_audits("}],
     "source": "coolorbit/agents/referee_llm.py (Adjudicator); coolorbit/board/status.py (settled_audits)"},
    {"id": "calibrate", "n": 4, "label": "calibrate", "title": "The checker must pass its own calibration first",
     "from": "librarian", "to": "classical", "count": "gate",
     "text": "A classical potential counts as a second opinion only if it first recovers the sourced measured value of "
             "that material within the tier1 tolerance (the Librarian's value on the board, else the same calibration "
             "set). Otherwise its check is posted inconclusive, without a value, so it can neither confirm nor flag.",
     "evidence": [{"path": "scripts/post_classical_checks.py", "text": "independent_method_unreliable"},
                  {"path": "scripts/post_classical_checks.py", "text": "def gate_reference("}],
     "source": "scripts/post_classical_checks.py (reliability gate, gate_reference)"},
    {"id": "followup", "n": 5, "label": "follow-up", "title": "Melting point passed → latent heat at the simulated Tm",
     "from": "board", "to": "scouts", "count": "followups",
     "text": "Each Scout follows up on its own candidates from the board: once a referee has passed a melting point, it "
             "requests the latent heat at that simulated melting point (the chemistry lead's rule: latent heat at each "
             "method's own Tm).",
     "evidence": [{"path": "coolorbit/agents/scout.py",
                   "text": "follows up on its own candidates (latent heat once a melting point exists)"},
                  {"path": "scripts/run_science.py",
                   "text": "at the simulated melting point once a referee has passed it"}],
     "source": "coolorbit/agents/scout.py (module docstring); scripts/run_science.py (module docstring)"},
    {"id": "gate", "n": 6, "label": "run gate", "title": "Run gate: a bad box stops before production",
     "from": "alchemi", "to": "simulator", "count": "run_gate",
     "text": "Coexistence protocol 2 checks the two-phase box before spending production time. If the crystal half did "
             "not survive the solid preparation (crystal_lost_in_prep) or the liquid half was not prepared "
             "(liquid_not_prepared), the run stops early and says why, and the Simulator does not retry it: a shifted "
             "or longer run would repeat the artefact.",
     "evidence": [{"path": "coolorbit/sim/simulator_agent.py",
                   "text": 'V2_NO_RETRY_FAILURES = frozenset({"crystal_lost_in_prep", "liquid_not_prepared"'},
                  {"path": "coolorbit/sim/melting.py",
                   "text": "Coexistence protocol 2 (opt-in; the fix in docs/results/coexistence-diagnosis.md)"}],
     "source": "coolorbit/sim/melting.py (--protocol 2); coolorbit/sim/simulator_agent.py (V2_NO_RETRY_FAILURES)"},
]


def _role_model(roles: dict, role: str) -> Optional[dict]:
    cfg = (roles.get("roles") or {}).get(role) or {}
    if not cfg.get("model"):
        return None
    return {"id": cfg["model"], "name": short_model_name(cfg["model"]), "full_name": model_name(cfg["model"]),
            "role": role, "effort": cfg.get("effort"), "source": f"configs/llm_roles.json (role {role})"}


GRAPH_ROWS = [
    {"id": "steers", "label": "Steers", "hue": "steer"},
    {"id": "proposes", "label": "Proposes", "hue": "propose"},
    {"id": "gates", "label": "Gates", "hue": "gate"},
    {"id": "simulates", "label": "Simulates", "hue": "sim"},
    {"id": "checks", "label": "Tries to prove it wrong", "hue": "check"},
    {"id": "keeps", "label": "Keeps", "hue": "keep"},
]

GRAPH_EDGES = [  # (id, from, to, label, kind, what carries it). Agents never message each other: see "via".
    ("e_seed", "humans", "scouts", "seeds · budgets", "config", "coolorbit/agents/scout_seeds.json (a file people edit)"),
    ("e_prereg", "humans", "trust", "frozen tolerances", "config", "configs/trust_rules.json, frozen in commit 2851934"),
    ("e_req_prem", "scouts", "premise", "requests", "flow", "a need on a candidate (property_result)"),
    ("e_req_sim", "scouts", "simulator", "requests", "flow", "a need on a candidate, claimed by the Simulator"),
    ("e_go", "premise", "worker", "go / refuse", "flow", "premise_check (arm C's worker waits for it)"),
    ("e_jobs", "simulator", "alchemi", "jobs", "flow", "one subprocess per job: python -m coolorbit.sim.melting"),
    ("e_res", "simulator", "checks", "results", "bus", "property_result and run_report artifacts"),
    ("e_res_w", "worker", "checks", "arm C", "bus", "property_result and run_report artifacts (arm C only)"),
    ("e_dispute", "llm", "adjudicator", "disputes", "flow", "method audits that disagree"),
    ("e_dispute_r", "rules", "adjudicator", "", "flow", "method audits that disagree"),
    ("e_checks", "checks", "board", "audits · rulings · checks · measured", "bus",
     "check and reference_value artifacts (one labelled drop per referee)"),
    ("e_evidence", "board", "trust", "evidence", "flow", "the board snapshot"),
    ("e_status", "trust", "ranker", "trusted", "flow", "the derived status of each result (trusted only)"),
    ("e_designate", "humans", "chemlead", "designates", "config", MEMO + " (designated by Madhan; applied by the GB10 "
                                                                         "Claude session)"),
    ("e_rules_trust", "chemlead", "trust", "rules · tolerances", "config", "configs/trust_rules.json (" + MEMO + ")"),
    ("e_rules_gate", "chemlead", "classical", "rules · gate", "config",
     "scripts/post_classical_checks.py reliability gate (" + MEMO + ")"),
    ("e_rules_window", "chemlead", "ranker", "rules · window", "config",
     "coolorbit/agents/ranker_config.json (" + MEMO + ")"),
]


def agent_graph(roles: dict, rules: dict, ranker: dict) -> dict:
    win = ranker.get("window_K") or {}
    lo, hi = win.get("min"), win.get("max")
    window_text = f"{lo:g}–{hi:g} K" if lo is not None and hi is not None else "the operating window"
    n_reject = rules.get("rejection_min_referees", 2)
    throughput = _gb10_throughput()
    audit_path = REPO / "coolorbit" / "agents" / "audit_rules.json"
    audit_frozen = bool(_read_json(audit_path).get("frozen")) if audit_path.exists() else False
    scout_role = _role_model(roles, "scout")
    librarian_role = _role_model(roles, "librarian")
    unused = ("configs/llm_roles.json defines a '{role}' role ({model}), but this agent's code makes no LLM call: "
              "{why}")

    def node(nid, row, tag, role, name, agents, line, does, source, *, model=None, engine=None, inputs=(),
             outputs=(), code=(), note=None, chips=None, **extra):
        return {"id": nid, "row": row, "tag": tag, "role": role, "name": name, "agents": list(agents), "line": line,
                "does": does, "model": model, "engine": engine, "inputs": list(inputs), "outputs": list(outputs),
                "code": list(code), "note": note, "chips": chips or [], "source": source, **extra}

    def tag(model, fallback):
        return model["name"].upper() if model else fallback

    premise_m = _role_model(roles, "premise_checker")
    llm_m = _role_model(roles, "referee_llm_audit")
    adj_m = _role_model(roles, "adjudicator")
    worker_m = _role_model(roles, "arm_baseline")
    author = memo_author()
    lead_m = ({"id": None, "name": author.removeprefix("Claude "), "full_name": author,
               "role": "chemistry lead, one literature review with web search", "effort": None,
               "source": MEMO + " (Author line)"} if author else None)
    remote_src = "scripts/run_remote_simulator.py; coolorbit/sim/ssh_runner.py"
    nodes = [
        node("chemlead", "steers", tag(lead_m, "LLM"), "CHEMISTRY LEAD",
             f"Chemistry lead ({author or 'model not named in the memo'})", [],
             "literature review → pre-registered rules",
             "Acting as the team's computational-chemistry lead (designated by Madhan), it reviewed the literature with "
             "web search and decided the numbers the trust rule, the classical-check gate and the Ranker use: the "
             "tolerances, that an independent check counts only if its own method first recovers the measured value, "
             "the operating window, the solid-solid question and the burst constants. A Claude session on the GB10 "
             "applied the decisions that evening, and the team froze them in the pre-registration commit before any "
             "comparison run. It is not a board agent: it made no posts and set no status.",
             MEMO + "; configs/trust_rules.json (_note); coolorbit/agents/ranker_config.json (_decision); "
             + PREREGISTRATION["source"],
             model=lead_m, engine=None if lead_m else "not named in the memo",
             inputs=["published benchmarks and measurements (web search; the memo lists its sources)"],
             outputs=["configs/trust_rules.json tolerances", "the classical-check reliability gate",
                      "coolorbit/agents/ranker_config.json operating window", "burst constants"],
             code=[MEMO, "configs/trust_rules.json", "coolorbit/agents/ranker_config.json"],
             note="From the memo itself: sources are as the reviewer reported them and the team spot-checks any source "
                  "before it goes on a slide; the reviewer ran one read-only command despite a no-shell instruction, "
                  "and nothing was changed.",
             decisions=lead_decisions(rules, ranker), date="2026-10-31"),
        node("humans", "steers", "TEAM", "STEERS", "The team (people)", [],
             "pre-register · decide · approve",
             "People steer; agents do the work. Before any comparison run the team froze the trust tolerances, the "
             "8-task comparison set and the operating window. They decide the open questions (the operating window, "
             "whether to include solid-solid PCMs), curate the Scouts' seed lists and approve paid GPU time.",
             PREREGISTRATION["source"] + "; CLAUDE.md sections 14 and 15; coolorbit/agents/ranker_config.json",
             engine="people, no board posts",
             inputs=["the results, failures and comparison on this dashboard"],
             outputs=["configs/trust_rules.json (frozen)", "coolorbit/eval/tasks.json (frozen task set)",
                      "coolorbit/agents/ranker_config.json (operating window)", "coolorbit/agents/scout_seeds.json"],
             code=["configs/trust_rules.json", "coolorbit/eval/tasks.json", "coolorbit/agents/ranker_config.json"],
             chips=[{"text": f"pre-registered · {PREREGISTRATION['commit']}", "source": PREREGISTRATION["source"]},
                    {"text": f"window {window_text}",
                     "source": "coolorbit/agents/ranker_config.json (decided 2026-10-31, " + MEMO + ")"}]),
        node("scouts", "proposes", "CODE", "SCOUTS", "Scouts", [s["agent"] for s in scouts()],
             "one per material family · candidates + requests",
             "Each Scout owns one material family: it posts candidates within its budget, sends each to an engine "
             "that can handle it (ALCHEMI for inorganic crystals, classical MD for molecular organics) and asks for "
             "the latent heat once a referee has passed a melting point. The alloy Scout takes its alloys from the "
             "sourced calibration set and asks for the latent heat at the measured melting point. In comparison runs "
             "a Scout answers only tasks about its own materials; scout_general takes the rest.",
             "coolorbit/agents/scout.py; coolorbit/agents/alloy_scout.py; coolorbit/agents/scout_seeds.json",
             engine="code, no LLM call",
             inputs=["seed lists (scout_seeds.json; alloys from data/calibration/tier1.json)",
                     "comparison task requests", "audited melting points on the board"],
             outputs=["candidate", "simulation requests (needs on the candidate)"],
             code=["coolorbit/agents/scout.py", "coolorbit/agents/alloy_scout.py", "coolorbit/agents/scout_seeds.json"],
             note=unused.format(role="scout", model=scout_role["name"], why="its candidates come from "
                                "coolorbit/agents/scout_seeds.json and data/calibration/tier1.json.")
             if scout_role else None,
             scouts=scouts()),
        node("premise", "gates", tag(premise_m, "CODE"), "PREMISE CHECK", "Premise checker", ["referee_premise"],
             "measurable at all? a parallel check, not a gate",
             "Before a run: is this property well-defined and measurable with this method, model, size and timescale? "
             "Rules decide what they can (diffusion in a perfect crystal at MD timescales, single-phase heating, a "
             "thermostat on a transport property, an inorganic-only model on a molecular organic). Claude is asked "
             "only when the rules can't decide, and the adjudicator when Claude is unsure. It advises rather than "
             "claims: a fail is carried onto any result made from that request, so the trust rule flags it. In arm C "
             "the generalist worker waits for its verdict.",
             "coolorbit/agents/premise.py; scripts/run_arm_c.py (WorkerDispatcher._premise)",
             model=premise_m, inputs=["every simulation request and comparison task on the board"],
             outputs=["premise_check: pass / fail / inconclusive"], code=["coolorbit/agents/premise.py"],
             gates={"text": "On the science boards it does not gate the Simulator, which can claim a request before any "
                            "premise check is posted; a fail is carried onto the result instead. In arm C it does gate "
                            "the generalist worker, which never starts work the premise checker refused.",
                    "source": "coolorbit/sim/simulator_agent.py (SimulatorAgent.pending); scripts/run_arm_c.py "
                              "(module docstring, WorkerDispatcher)"}),
        node("simulator", "simulates", "GB10 GPU", "SIMULATOR", "Simulator", ["simulator_gb10"],
             "claims requests · one GPU job at a time",
             "Owns the GPU queue and nothing else. It claims the requests it can compute (melting point, latent "
             "heat), runs each as one ALCHEMI job in its own process, and posts a result with full method metadata, "
             "a failed run as a run report with a retry request (a shifted bracket or a longer run), or a refusal: "
             "MACE-MPA-0 is out of its training domain for molecular organics, so those are rerouted to classical MD. "
             "Overflow: more Simulators run the same code with an SSH runner on a Brev H100 and split the queue with "
             "the GB10's through claims on the board, with no coordinator.",
             "coolorbit/sim/simulator_agent.py; " + remote_src, engine="code, no LLM call",
             inputs=["simulation requests (property_result needs)"],
             outputs=["property_result", "run_report (failed or refused)", "the checks the trust rule still needs"],
             code=["coolorbit/sim/simulator_agent.py", "coolorbit/sim/melting.py", "scripts/run_remote_simulator.py",
                   "coolorbit/sim/ssh_runner.py"],
             agent_prefix="simulator",
             remote={"text": "a Simulator whose results record a remote host ran on the Brev H100 over SSH",
                     "source": remote_src + " (method.remote on each result)"}),
        node("alchemi", "simulates", "NVIDIA ALCHEMI", "MACE-MPA-0", "NVIDIA ALCHEMI Toolkit · MACE-MPA-0",
             [], "batched MLIP dynamics on the GPU",
             "The engine. NVIDIA's ALCHEMI Toolkit runs the MACE-MPA-0 machine-learned force field on the GB10's "
             "Blackwell GPU, with dynamics batched over systems: each trial temperature is its own system in one GPU "
             "job. Its hooks API runs code inside every step; our SuperheatingDetectorHook is built on it. ALCHEMI "
             "computes; it does not decide what to run, which protocol to use or whether to trust the answer.",
             "coolorbit/sim/melting.py; coolorbit/hooks/superheating.py; coolorbit/hooks/README.md "
             "(tested with nvalchemi-toolkit 0.2.0)",
             engine="nvalchemi-toolkit 0.2.0 (not an agent)",
             inputs=["one job from the Simulator: a structure, temperatures, a protocol"],
             outputs=["trajectories and energies (runs/melting/... result.json), read back by the Simulator"],
             code=["coolorbit/sim/melting.py", "coolorbit/hooks/superheating.py"],
             imports=alchemi_imports(), throughput=throughput,
             hosts=[{"text": "Dell Pro Max GB10 · home",
                     "source": "CLAUDE.md section 8; coolorbit/sim/melting.py (GB10_ATOM_STEPS_PER_S, measured there)"},
                    {"text": "Brev H100 · overflow over SSH", "source": remote_src}],
             protocol=[
                 {"text": "two-phase coexistence, several temperatures per job",
                  "source": "coolorbit/sim/melting.py run_coexistence(); the Simulator runs only this for melting "
                            "points (coolorbit/sim/simulator_agent.py SUPPORTED)"},
                 {"text": "coexistence protocol v2 (the opt-in fix)",
                  "source": "coolorbit/sim/melting.py (--protocol 2; docs/results/coexistence-diagnosis.md); "
                            "coolorbit/sim/simulator_agent.py (coex_protocol)"},
                 {"text": "latent heat = H(liquid) − H(solid), NPT", "source": "coolorbit/sim/melting.py run_latent()"},
                 {"text": "alloys: H(liquid) − Σ xᵢ H(pure solid i)",
                  "source": "coolorbit/sim/melting.py run_alloy_latent() (an approximation the referees flag)"},
                 {"text": "SuperheatingDetectorHook (ours)", "source": "coolorbit/hooks/superheating.py"},
             ]),
        node("worker", "simulates", tag(worker_m, "CODE"), "WORKER · ARM C", "Generalist worker (arm C only)",
             ["worker_cc"], "sandboxed Claude Code · what no specialist owns",
             "Arm C only: the same sandboxed Claude Code worker arm A uses (same model, effort, tools, NVIDIA guidance "
             "and measured-reference file). It takes requests no specialist owns (equation of state, adsorption, "
             "diffusion, classical-MD reroutes), jobs the Simulator can't set up, and reruns of its own results when "
             "a referee fails them. It never starts work the premise checker refused.",
             "scripts/run_arm_c.py (module docstring, WorkerDispatcher)", model=worker_m,
             inputs=["requests no specialist owns", "failed method audits of its own results"],
             outputs=["property_result", "run_report"],
             code=["scripts/run_arm_c.py", "scripts/prompts/arm_c_worker.md"]),
        node("rules", "checks", "CODE", "RULE REFEREE", "Rule referee", ["referee_rules"],
             "method audit by fixed rules",
             "Audits every result's method metadata: single-phase heating for a melting point, a thermostat during a "
             "transport measurement, an inorganic-only model on a molecular organic, a hydrogen timestep, box size, "
             "run length, and the producer's own blocking flags. A fail carries a rerun request when the flaw is "
             "fixable. It never checks its own results.",
             "coolorbit/agents/referee_rules.py", engine="code, no LLM call",
             inputs=["every property_result on the board"], outputs=["method_audit: pass / fail (+ rerun request)"],
             code=["coolorbit/agents/referee_rules.py", "coolorbit/agents/audit_rules.json"],
             note=None if audit_frozen else "Its audit thresholds are placeholders, not frozen "
                                            "(coolorbit/agents/audit_rules.json); the trust-rule tolerances are the "
                                            "pre-registered ones."),
        node("llm", "checks", tag(llm_m, "CODE"), "LLM REFEREE", "LLM referee", ["referee_llm"],
             "names the flaw the rules miss",
             "Reads each result's method metadata and tries to name a concrete physical flaw the fixed rules can "
             "miss: a wrong protocol for the property, mismatched phases, a model outside its domain, a "
             "self-validating test, a unit error. It never sees the other referees' checks, so its verdict is "
             "independent.",
             "coolorbit/agents/referee_llm.py", model=llm_m,
             inputs=["every property_result's method metadata"], outputs=["method_audit: pass / fail / inconclusive"],
             code=["coolorbit/agents/referee_llm.py"]),
        node("classical", "checks", "GB10 CPU", "CLASSICAL MD", "Classical-MD referee", ["referee_classical"],
             "LAMMPS + MEAM/EAM · must pass its own test first",
             "A different physics: classical molecular dynamics with published MEAM and EAM potentials (NIST "
             "Interatomic Potentials Repository), LAMMPS on the GB10's CPU, same two-phase protocol. A potential counts "
             "only if it first recovers the measured value within the pre-registered tolerance; otherwise its check is "
             "posted as inconclusive. Copper's EAM (Mishin 2001) passed and agrees with MACE, which made copper "
             "trusted; bismuth's MEAM passed (it was fitted to bismuth's melting point) but disagrees with MACE; "
             "aluminium's EAM and tin's, indium's and gallium's MEAM failed their own test. Posted by a script, not a "
             "polling agent.",
             "scripts/post_classical_checks.py; docs/results/classical-check.md; docs/results/classical-check-eam.md",
             engine="LAMMPS on the GB10 CPU",
             inputs=["MACE melting-point and latent-heat results", "MEAM/EAM analyses in runs/classical/"],
             outputs=["classical_md check: agree / disagree / inconclusive"],
             code=["scripts/post_classical_checks.py", "docs/results/classical-check.md",
                   "docs/results/classical-check-eam.md"]),
        node("librarian", "checks", "CODE", "LIBRARIAN", "Librarian", ["librarian"],
             "measured values, each with a source",
             "Answers every request for a measured value from the curated calibration set, where each value carries "
             "a checkable source. When there is none it posts a null reference instead of guessing.",
             "coolorbit/agents/librarian.py; data/calibration/tier1.json", engine="code, no LLM call",
             inputs=["reference_value needs on trust-tier results"], outputs=["reference_value (or null)"],
             code=["coolorbit/agents/librarian.py", "data/calibration/tier1.json"],
             note=unused.format(role="librarian", model=librarian_role["name"], why="every value comes from "
                                "data/calibration/tier1.json.") if librarian_role else None),
        node("adjudicator", "checks", tag(adj_m, "CODE"), "ADJUDICATOR", "Adjudicator", ["adjudicator"],
             "settles referee disputes",
             "When two referees' method audits of a result disagree, it reads the result and both arguments and "
             "rules; a decisive ruling settles the audits posted before it. It also settles premise questions the "
             "premise checker can't decide. The most expensive model, so it is called rarely.",
             "coolorbit/agents/referee_llm.py (Adjudicator); coolorbit/board/status.py (settled_audits); "
             "coolorbit/agents/premise.py", model=adj_m,
             inputs=["a result with a passing and a failing method audit"], outputs=["method_audit ruling"],
             code=["coolorbit/agents/referee_llm.py", "coolorbit/board/status.py"]),
        node("board", "keeps", "SCIENCECLAW", "RESULTS BOARD", "Results board", [],
             "append-only artifacts with lineage",
             "Every candidate, request, claim, result, check and measured value is an immutable artifact that names "
             "its parents, so every number keeps its lineage. Agents coordinate only through open needs and claims "
             "on it: no boss, no direct messages. Nothing on it is ever edited.",
             "coolorbit/board/store.py; coolorbit/agents/base.py", engine="files on the GB10 (boards/<id>/artifacts)",
             inputs=["every agent's posts"], outputs=["snapshots, open needs, claims"],
             code=["coolorbit/board/store.py"]),
        node("trust", "keeps", "CODE", "TRUST RULE", "Trust rule", [],
             "status is derived, never set",
             "A result is trusted only if another agent's method audit passed, an independent method (a different "
             "engine or model, from a different agent) agrees within tolerance, and for known materials the value "
             f"matches a sourced measurement. Any failed check flags it; rejection needs {n_reject} independent "
             "referees; an adjudicator's ruling settles disputed audits. The tolerances were frozen before the "
             "comparison runs.",
             "coolorbit/board/status.py; configs/trust_rules.json", engine="code (not an agent)",
             inputs=["the board: checks, measured values, reruns"], outputs=["trusted / under review / flagged / rejected"],
             code=["coolorbit/board/status.py", "configs/trust_rules.json"]),
        node("ranker", "keeps", "CODE", "RANKER", "Ranker", ["ranker"],
             "trusted only · inside the window",
             f"Keeps the shortlist from trusted results only: a trusted transition temperature inside the {window_text} "
             "operating window plus a trusted latent heat, most heat per kilogram first. It posts a new ranking only "
             "when the shortlist changes.",
             "coolorbit/agents/ranker.py; coolorbit/agents/ranker_config.json", engine="code, no LLM call",
             inputs=["results the trust rule says are trusted"], outputs=["ranking"],
             code=["coolorbit/agents/ranker.py", "coolorbit/agents/ranker_config.json"]),
    ]

    gh = GITHUB + "/blob/main/"
    return {
        "rows": GRAPH_ROWS,
        "nodes": nodes,
        "edges": [{"id": i, "from": a, "to": b, "label": label, "kind": kind, "via": via}
                  for i, a, b, label, kind, via in GRAPH_EDGES],
        "code_url": gh,
        "status_rule": {"active_minutes": 60,
                        "source": "dashboard/context.py (Agents tab rule: ACTIVE = posted within this many minutes "
                                  "before the export)"},
        "idea": {
            "headline": "ALCHEMI runs the physics. The agents decide what is worth running, and try to prove every "
                        "answer wrong.",
            "points": [
                {"lead": "ALCHEMI is the engine.",
                 "text": "On the Dell Pro Max GB10 it runs MACE-MPA-0 molecular dynamics batched on the GPU: every "
                         "trial temperature of a two-phase box in one job.",
                 "source": "coolorbit/sim/melting.py"},
                {"lead": "The agents decide what runs, and how.",
                 "text": "Scouts choose candidates; the premise checker asks whether a property is measurable at all; "
                         "the Simulator refuses models outside their training domain and uses two-phase coexistence, "
                         "never single-phase heating, so the superheating trap is avoided by design.",
                 "source": "coolorbit/agents/scout.py; coolorbit/agents/premise.py; coolorbit/sim/simulator_agent.py"},
                {"lead": "Then they try to prove it wrong.",
                 "text": "A rule audit, an LLM audit, a classical-MD check that must first recover the measured value, "
                         "and a sourced measurement. An adjudicator settles audit disputes.",
                 "source": "coolorbit/agents/referee_rules.py; coolorbit/agents/referee_llm.py; "
                           "scripts/post_classical_checks.py; coolorbit/agents/librarian.py"},
                {"lead": "It is a loop, not a pipeline.",
                 "text": "A missed bracket is retried, a failed audit is rerun, a dispute goes to the adjudicator, a "
                         "checker must first pass its own calibration, and a passed melting point triggers the "
                         "latent-heat run. Every loop-back is an artifact on the board.",
                 "source": "; ".join(sorted({e["path"] for lp in GRAPH_LOOPS for e in lp["evidence"]}))},
                {"lead": "No agent sets a status.",
                 "text": "The trust rule derives it from the evidence on the board, so a confident number without "
                         "independent support never counts.",
                 "source": "coolorbit/board/status.py"},
            ],
        },
        "case": {"candidate": "BiInSn", "property": "latent_heat", "label": "Field's metal (BiInSn), latent heat",
                 "source": OUTLINE + " claims register S24 (result 7ed6f997 and its checks on boards/science-1)"},
        # Replays built at load time from the exported lineage; the first one found is the default.
        "cases": [
            {"id": "cu", "candidate": "Cu", "property": "melting_point", "board": "science-2", "status": "trusted",
             "label": "Copper (Cu), melting point",
             "source": "boards/science-2: the Cu melting_point result whose derived status is trusted, with its "
                       "parents and checks (found at load time)"},
            {"id": "fields", "candidate": "BiInSn", "property": "latent_heat", "board": "science-1", "status": None,
             "label": "Field's metal (BiInSn), latent heat",
             "source": OUTLINE + " claims register S24 (result 7ed6f997 and its checks on boards/science-1)"},
        ],
        "loops": GRAPH_LOOPS,
        "vs": [
            {"alchemi": "Batched GPU dynamics: NPT with one temperature per system, all systems in one job.",
             "alchemi_source": "coolorbit/sim/melting.py (npt(): NPT, temperature per system)",
             "agents": "The protocol: two-phase coexistence at several temperatures for a melting point, never "
                       "single-phase heating; latent heat as an NPT enthalpy difference.",
             "agents_source": "coolorbit/sim/simulator_agent.py (SUPPORTED); coolorbit/sim/melting.py"},
            {"alchemi": "The MACE-MPA-0 force field through its MACE wrapper, with cuEquivariance kernels.",
             "alchemi_source": "coolorbit/sim/melting.py (load_model(): MACEWrapper.from_checkpoint, enable_cueq)",
             "agents": "Judgement about the model's domain: the Simulator refuses molecular organics and reroutes them "
                       "to classical MD; the premise checker and the rule referee flag the same trap.",
             "agents_source": "coolorbit/sim/simulator_agent.py (ood_reason); coolorbit/agents/premise.py; "
                              "coolorbit/agents/referee_rules.py"},
            {"alchemi": "Speed on the GB10:", "metric": throughput,
             "alchemi_source": throughput["source"] if throughput else "coolorbit/sim/melting.py",
             "agents": "A budget: the Simulator estimates each job's GPU minutes from that rate; in arm C it drops to "
                       "the quick preset when a job would not fit half the task's remaining time (a disclosed "
                       "post-hoc change).",
             "agents_source": "coolorbit/sim/simulator_agent.py (Job.estimate_minutes); scripts/run_arm_c.py "
                              "(FIT_TO_BUDGET_NOTE)"},
            {"alchemi": "A dynamics-hooks API: code that runs at set points of every step (neighbour lists, periodic "
                        "wrapping, frozen atoms, NaN checks).",
             "alchemi_source": "coolorbit/sim/melting.py (NeighborListHook, WrapPeriodicHook, FreezeAtomsHook, "
                               "NaNDetectorHook)",
             "agents": "SuperheatingDetectorHook, built on that API and shared with other teams: it flags a perfect "
                       "crystal that melts.",
             "agents_source": "coolorbit/hooks/superheating.py; coolorbit/hooks/README.md"},
            {"alchemi": None, "alchemi_source": None,
             "agents": "Premise checks before any GPU time is spent: is the property measurable with this method at "
                       "this timescale?",
             "agents_source": "coolorbit/agents/premise.py"},
            {"alchemi": None, "alchemi_source": None,
             "agents": "Independent referees: a rule audit, an LLM audit, classical MD gated on its own calibration, "
                       "sourced measurements and an adjudicator.",
             "agents_source": "coolorbit/agents/referee_rules.py; coolorbit/agents/referee_llm.py; "
                              "scripts/post_classical_checks.py; coolorbit/agents/librarian.py"},
            {"alchemi": None, "alchemi_source": None,
             "agents": "Lineage and the trust rule: every number keeps its parents, and its status is derived from "
                       "the evidence, never set by an agent.",
             "agents_source": "coolorbit/board/store.py; coolorbit/board/status.py"},
        ],
        "coordination": {"text": "No boss: agents coordinate only through the shared results board (artifacts with "
                                 "lineage, open needs and claims). Every line in the diagram is a board artifact or "
                                 "a file people edit; there are no direct messages.",
                         "source": "coolorbit/board/store.py; coolorbit/agents/base.py"},
        "source": "coolorbit/agents/, coolorbit/sim/, coolorbit/board/, scripts/run_science.py, scripts/run_arm_c.py, "
                  "configs/llm_roles.json",
    }


def trust_rule(rules: dict, ranker: dict) -> dict:
    props = rules.get("properties") or {}
    tolerances = []
    for key, label in (("melting_point", "Melting point"), ("latent_heat", "Latent heat")):
        p = props.get(key)
        if not p:
            continue
        t1, ag = p.get("tier1") or {}, p.get("agree") or {}
        tolerances.append({"property": key, "label": label, "tier1_rel": t1.get("rel"), "tier1_abs": t1.get("abs"),
                           "agree_rel": ag.get("rel"), "agree_abs": ag.get("abs"), "units": (p.get("units") or [None])[0],
                           "source": RULES_SRC if rules.get("frozen") else "configs/trust_rules.json (NOT frozen)"})
    win = ranker.get("window_K") or {}
    window = None
    if win.get("min") is not None and win.get("max") is not None:
        window = {"min": win["min"], "max": win["max"], "units": "K", "decided": bool(ranker.get("decided")),
                  "source": "coolorbit/agents/ranker_config.json; " + MEMO + " (HBM max operating 80 C minus a "
                            "junction-to-PCM margin; the PCM must refreeze against a ~40 C radiator)"}
    n_reject = rules.get("rejection_min_referees", 2)
    return {
        "rules": ["another agent's method audit passed;",
                  "an independent method (a second force field or classical MD, from a different agent) agrees within "
                  "tolerance;",
                  "for known materials, the value matches a sourced measurement within tolerance."],
        "also": f"Any failed check or numeric disagreement flags the result for a rerun. Rejection needs {n_reject} "
                "independent referees, so no single referee is a gatekeeper; an adjudicator's ruling settles disputed "
                "method audits. No agent can set a status: it is derived from the evidence on the board.",
        "frozen": bool(rules.get("frozen")),
        "tolerances": tolerances,
        "rejection_min_referees": {"value": n_reject, "source": "configs/trust_rules.json"},
        "window": window,
        "preregistration": PREREGISTRATION if rules.get("frozen") else None,
        "source": "coolorbit/board/status.py; configs/trust_rules.json",
    }


def comparison_setup(roles: dict) -> dict:
    base = (roles.get("roles") or {}).get("arm_baseline") or {}
    budget = roles.get("comparison_budget_usd_per_task_per_arm")
    return {
        "arms": {"A": "Single agent: Claude Code + ALCHEMI skills + NVIDIA's recommended premise-check and "
                      "validation prompt",
                 "B": "Pipeline: one worker agent + one checker agent",
                 "C": "Our decentralized collective",
                 "D": "Homogeneous collective: like C, one model for every agent"},
        "fairness": "Same tasks, same budget and the same tools for every arm, web access included.",
        "baseline_model": model_name(base.get("model")),
        "budget_usd_per_task_per_arm": {"value": budget, "units": "USD",
                                        "source": "configs/llm_roles.json"} if budget is not None else None,
        "scoring": {
            "pre_registered": "Pre-registered scoring: the task set and tolerances frozen in commit "
                              f"{PREREGISTRATION['commit']}, before any comparison run.",
            "post_hoc": "Post-hoc rule (added 2026-11-01 after repeat 1, not pre-registered): an answer that ran no "
                        "simulation and reports a measured value scores as a 'reported reference': no error is "
                        "computed, and on a trap task it counts against the catch rate.",
            "source": "coolorbit/eval/metrics.py (module docstring, reference_rule=True); " + CMP1_DOC,
        },
        "report": {"text": "Written findings of run cmp-1 (all repeats)", "url": GITHUB + "/blob/main/" + CMP1_DOC,
                   "source": CMP1_DOC},
        "source": "CLAUDE.md section 13; coolorbit/eval/arms.md; " + OUTLINE + " slide 7",
    }


def comparison_findings() -> dict:
    """What run cmp-1 showed over its complete repeats, as the team wrote it up (docs/results/comparison-cmp1.md)."""
    return {
        "title": "What cmp-1 says (3 complete repeats)",
        "items": [
            "Trap catch: the collective (C) 80% against 60% for the single agent (A) and the worker + checker pipeline "
            "(B) under the post-hoc rule (pre-registered scoring: 80 / 60 / 67%). C's lead appears after a disclosed "
            "post-hoc fix (its Simulator now fits its run to the time left): repeat 1 60%, repeats 2 and 3 100% and 80%.",
            "False alarms on correct tasks: C 11% and A 11%, B 22%. No arm shipped a confident wrong answer: with "
            "NVIDIA's recommended prompt and web access, the single agent already refuses the ill-posed tasks.",
            "B's pipeline once 'resolved' a disagreement by replacing its own aluminium simulation (862 ± 38 K, within "
            "the ±10% tolerance) with the measured value from the calibration file; the pre-registered scorer counted "
            "that as a perfect answer, which is why the post-hoc rule exists. Both scorecards are shown.",
            "Cost: C is the cheapest arm, about $2.40 of LLM spend per repeat against about $4.57 for A and $3.50 for B.",
            "Small sample: 8 tasks x 3 repeats. No statistical claims.",
        ],
        "source": CMP1_DOC + " (repeats 1-3, 2026-10-31 21:45 UTC to 2026-11-01 10:19 UTC)",
        "url": GITHUB + "/blob/main/" + CMP1_DOC,
    }


def head_to_head(b: dict, window: Optional[tuple] = None) -> Optional[dict]:
    """Paraffin vs gallium, aspect by aspect, from the burst calculation's own sourced numbers (nothing typed in)."""
    mats = (b or {}).get("materials") or []
    wax = next((m for m in mats if m.get("kind") == "paraffin"), None)
    ga = next((m for m in mats if "gallium" in m.get("name", "").lower()), None)
    if not wax or not ga:
        return None
    W, G_, Wi, Gi = wax["result"], ga["result"], wax["inputs"], ga["inputs"]
    sc = b.get("scenario") or {}
    calc = f"{BURST_SRC}: one {sc.get('minutes', 15):g}-minute burst at {sc.get('power_W', 1000):g} W"

    def num(aspect, why, wv, gv, units, better, wsrc, gsrc):
        if better == "high":
            winner = "paraffin" if wv > gv else "gallium" if gv > wv else "tie"
        else:
            winner = "paraffin" if wv < gv else "gallium" if gv < wv else "tie"
        lo, hi = min(wv, gv), max(wv, gv)
        return {"aspect": aspect, "why": why, "units": units, "better": better, "winner": winner,
                "paraffin": {"value": wv, "source": wsrc}, "gallium": {"value": gv, "source": gsrc},
                "ratio": {"value": round(hi / lo, 1) if lo else None, "source": "ratio of the two values in this row"}}

    def inp(m, key):
        return m["inputs"][key]["value"], m["inputs"][key]["source"]

    rows = []
    (wl, wls), (gl, gls) = inp(wax, "latent_heat_J_per_kg"), inp(ga, "latent_heat_J_per_kg")
    rows.append(num("Heat stored per kilogram", "what each kilogram launched buys", round(wl / 1000, 1),
                    round(gl / 1000, 1), "kJ/kg", "high", wls, gls))
    rows.append(num("Device mass for one burst", "PCM + aluminium fins: the launch-cost number", W["total_kg"],
                    G_["total_kg"], "kg", "low", calc, calc))
    rows.append(num("Container volume", "space inside the satellite", W["pcm_volume_L"], G_["pcm_volume_L"], "L",
                    "low", calc, calc))
    rows.append(num("Heat-exchange area needed", "fin surface to soak up the burst in time", W["area_m2"],
                    G_["area_m2"], "m2", "low", calc, calc))
    (wk, wks), (gk, gks) = inp(wax, "conductivity_liquid_W_mK"), inp(ga, "conductivity_liquid_W_mK")
    rows.append(num("Heat conduction (liquid)", "how fast heat gets in and out", wk, gk, "W/m/K", "high", wks, gks))
    rows.append(num("Aluminium fins needed", "extra structure to carry", W["fin_kg"], G_["fin_kg"], "kg", "low",
                    calc, calc))
    # Volume change between solid and liquid: the free space (or compliance) the container must allow. A different
    # design problem from the container's size, computed from the same sourced solid and liquid densities.
    if all(k in m["inputs"] for m in (wax, ga) for k in ("density_solid_kg_m3", "density_liquid_kg_m3")):
        def swell(m):
            (s, ss), (lq, lqs) = inp(m, "density_solid_kg_m3"), inp(m, "density_liquid_kg_m3")
            return round(100 * (s / lq - 1), 1), f"{ss}; {lqs} (liquid/solid volume ratio = solid/liquid density)"
        (wsw, wsws), (gsw, gsws) = swell(wax), swell(ga)
        vrow = num("Volume change on melting", "free space the container must leave; paraffin swells when it melts, "
                   "gallium (unusually) swells when it freezes", abs(wsw), abs(gsw), "%", "low", wsws, gsws)
        vrow["paraffin"]["text"] = f"{wsw:+g}% on melting"
        vrow["gallium"]["text"] = f"{gsw:+g}% on melting ({-gsw:+g}% on freezing)"
        rows.append(vrow)
    if window and "melting_point_K" in Wi and "melting_point_K" in Gi:
        lo_w, hi_w = window
        (wt, wts), (gt, gts) = inp(wax, "melting_point_K"), inp(ga, "melting_point_K")
        ins = lambda t: lo_w <= t <= hi_w  # noqa: E731
        win = ("both" if ins(wt) and ins(gt) else "paraffin" if ins(wt) else "gallium" if ins(gt) else "neither")
        rows.append({"aspect": f"Melts inside our {lo_w:g}-{hi_w:g} K operating window",
                     "why": "it must melt above the radiator and below the chip limit", "winner": win,
                     "paraffin": {"text": f"{'Yes' if ins(wt) else 'No'}, {wt:g} K", "value": wt, "source": wts},
                     "gallium": {"text": f"{'Yes' if ins(gt) else 'No'}, {gt:g} K", "value": gt, "source": gts},
                     "window": {"min": lo_w, "max": hi_w, "source": "coolorbit/agents/ranker_config.json; " + MEMO}})
    rows.append({"aspect": "Safe with aluminium hardware", "why": "fins, heat pipes and housings are aluminium",
                 "winner": "paraffin",
                 "paraffin": {"text": "Yes", "source": "inert hydrocarbon (" + MEMO + ")"},
                 "gallium": {"text": "No: liquid gallium penetrates aluminium",
                             "source": MEMO + ", 'What a chemistry judge will challenge' item 3 (Hugo & Hoagland, "
                                              "Scripta Mater. 38(3), 1998; abstract)"}})
    r = {x["aspect"]: x for x in rows}
    heat, mass, vol, area, cond = (r["Heat stored per kilogram"], r["Device mass for one burst"],
                                   r["Container volume"], r["Heat-exchange area needed"], r["Heat conduction (liquid)"])
    ok = (heat["winner"] == "paraffin" and mass["winner"] == "paraffin" and vol["winner"] == "gallium"
          and area["winner"] == "gallium" and cond["winner"] == "gallium")
    window_row = next((x for x in rows if x["aspect"].startswith("Melts inside")), None)
    tail = " Neither melts inside our operating window." if window_row and window_row["winner"] == "neither" else ""
    n_ga = sum(x["winner"] == "gallium" for x in rows)
    tail = (f" Like wax, it melts below our {window[0]:g}-{window[1]:g} K operating window."
            if window and window_row and window_row["winner"] == "neither" else "")
    minutes = sc.get("minutes", 15)
    verdict = (f"Gallium wins {n_ga} of our {len(rows)} comparisons. Liquid gallium conducts heat "
               f"{cond['ratio']['value']:.0f}x better than wax, so one {minutes:g}-minute burst fits in a block "
               f"{vol['ratio']['value']:g}x smaller, through {area['ratio']['value']:.0f}x less heat-exchange area, "
               "with almost no aluminium fins, and it swells less when it melts. The trade-off is weight: the "
               f"gallium device is {mass['ratio']['value']:g}x heavier." + tail) if ok else (
        "With the current inputs the comparison does not split simply; read the rows.")
    return {"title": "Gallium vs paraffin: who wins what", "verdict": verdict, "rows": rows,
            "materials": {"paraffin": wax["label"], "gallium": ga["label"]},
            "source": calc + "; latent heat, conductivity and melting points as cited in each row"}


GA_STRUCTURE_SRC = ("coolorbit/sim/alloys.py element_crystal('Ga'): the alpha-Ga crystal our simulations start from "
                    "(Cmce; lattice and position from Sharma & Donohue, Z. Kristallogr. 117, 293 (1962))")


def gallium_structure(reps: tuple = (2, 2, 2), calibration: Optional[dict] = None) -> Optional[dict]:
    """alpha-Ga for the 3D view, built by the same code our simulations use, with its neighbour shells computed here.

    Returns None if ASE (or the structure code) can't be imported, so the rest of the context still builds.
    """
    try:
        import numpy as np
        from ase.neighborlist import neighbor_list

        from coolorbit.sim.alloys import ELEMENT_CRYSTALS, element_crystal
    except Exception:  # noqa: BLE001
        return None
    unit = element_crystal("Ga")
    i, j, d = neighbor_list("ijd", unit, 3.2)
    first = d[i == 0]
    pair = float(np.round(first.min(), 3))
    shell = np.sort(first[first > pair + 0.1])
    block = unit.repeat(reps)
    block.pbc = True
    pos = [p for p in block.get_positions()]
    n_block = len(pos)
    # Each atom's close partner, through the periodic boundary if need be: a partner outside the block is added as a
    # "ghost" (a real atom of the neighbouring cell, drawn faded), so every atom of the block shows its pair.
    bi, bj, bD = neighbor_list("ijD", block, pair + 0.05)
    ghost = [False] * n_block
    bonds = set()
    for a_i, vec in zip(bi, bD):
        target = pos[a_i] + vec
        k = next((m for m, p in enumerate(pos) if float(np.linalg.norm(p - target)) < 0.01), None)
        if k is None:
            pos.append(target)
            ghost.append(True)
            k = len(pos) - 1
        bonds.add(tuple(sorted((int(a_i), int(k)))))
    atom_src = "alpha-Ga block built by coolorbit/sim/alloys.py element_crystal('Ga'), repeated " + "x".join(map(str, reps))
    bond_src = f"close pair (<= {pair + 0.05:.2f} Å), found with ase.neighborlist on that block"
    a, b, c = (float(x) for x in unit.cell.lengths())
    calibration = calibration if calibration is not None else load_calibration()
    measured = []
    for prop, label in (("melting_point", "Melting point"), ("latent_heat", "Latent heat")):
        rec = calibration.get(("ga", prop))
        if rec:
            measured.append({"label": label, "value": rec["value"], "units": rec.get("units"), "source": rec["source"]})
    return {
        "phase": ELEMENT_CRYSTALS["Ga"][0],
        "atoms": [{"x": round(float(p[0]), 3), "y": round(float(p[1]), 3), "z": round(float(p[2]), 3),
                   "ghost": g_, "source": atom_src} for p, g_ in zip(pos, ghost)],
        "n_block": n_block,
        "bonds": [{"a": a_, "b": b_, "source": bond_src} for a_, b_ in sorted(bonds)],
        "cell": {"a": round(a, 3), "b": round(b, 3), "c": round(c, 3),
                 "reps": {"a": int(reps[0]), "b": int(reps[1]), "c": int(reps[2]),
                          "source": "unit cells shown along a, b, c (dashboard/context.py gallium_structure)"},
                 "units": "Å",
                 "source": GA_STRUCTURE_SRC},
        "pair": {"value": pair, "units": "Å", "count": int((first <= pair + 0.05).sum()),
                 "source": "nearest-neighbour distance computed with ase.neighborlist from " + GA_STRUCTURE_SRC},
        "next_shell": {"min": round(float(shell[:6].min()), 3), "max": round(float(shell[:6].max()), 3), "count": 6,
                       "units": "Å", "source": "the next six neighbours, computed with ase.neighborlist from "
                                               + GA_STRUCTURE_SRC},
        "measured": measured,
        "caveats": [
            {"text": "Gallium melts at 302.9 K, below the 315-345 K operating window, and liquid gallium penetrates "
                     "aluminium, so a real device needs a different window or container.",
             "source": "data/calibration/tier1.json (NIST WebBook); coolorbit/agents/ranker_config.json; " + MEMO},
        ],
        "source": GA_STRUCTURE_SRC,
    }


# --------------------------------------------------------------------------- the Agents tab: what the loop caught

NVIDIA_BLOG = ("https://developer.nvidia.com/blog/how-ai-coding-agents-can-unlock-materials-simulation-with-nvidia-"
               "alchemi-toolkit/")


def _cr(ids: str, extra: str = "") -> str:
    """A source line pointing at entries of the pitch's claims register (each checked against its file or paper)."""
    return f"[{ids}, {OUTLINE} claims register]" + (f" {extra}" if extra else "")


def catches() -> dict:
    """Real cases where a clean-looking run rested on a wrong assumption, who in the loop caught it and what happened.

    Every sentence restates a claim from the pitch's claims register (docs/pitch/outline.md, S-ids), which records
    the file or paper each number was read from, or describes the code that does the catching."""
    cases = [
        {"id": "superheating", "title": "“A perfect crystal melts at its melting point.”",
         "shipped": {"text": "Heat a perfect copper crystal on ALCHEMI and it reports 1600 ± 100 K: clean, "
                             "confident, and 18% above the measured 1357.77 K. With nowhere to start melting, the "
                             "crystal superheats.",
                     "source": HOOK_CONTROL_SRC + "; measured value from data/calibration/tier1.json (ITS-90)"},
         "caught": {"who": "Method audit and our SuperheatingDetectorHook.",
                    "text": "The audit fails any melting point from single-phase heating and asks for a two-phase "
                            "rerun. The hook catches it inside any ALCHEMI run: 3 of 3 superheated crystals flagged, "
                            "0 of 4 two-phase boxes.",
                    "source": "coolorbit/agents/referee_rules.py (module docstring); coolorbit/hooks/superheating.py; "
                              + HOOK_CONTROL_SRC},
         "outcome": {"tone": "ok", "label": "trusted",
                     "text": "Two-phase coexistence on the same engine and model gives 1290-1330 K, and an independent "
                             "classical check agrees (1327 K). Copper's melting point is trusted.",
                     "source": _cr("S26, S27, S29", "boards/science-2 result 94311022; "
                                   "docs/results/classical-check-eam.md; runs/remote/refine")}},
        {"id": "domain", "title": "“One AI force field fits every material.”",
         "shipped": {"text": "ALCHEMI will run MACE-MPA-0 on paraffin wax if asked. MACE-MP models were trained on "
                             "inorganic crystals and miss the weak forces that hold wax together, so the number would "
                             "look fine and mean nothing.",
                     "source": "coolorbit/sim/melting.py; CLAUDE.md section 11 (out-of-distribution warning); "
                               + OUTLINE + " slide 11"},
         "caught": {"who": "Premise checker, Scouts and Simulator.",
                    "text": "The premise checker and the method audit flag an inorganic-only model on a molecular "
                            "organic; the Scouts send waxes to classical MD, and the Simulator refuses them.",
                    "source": "coolorbit/agents/premise.py; coolorbit/agents/scout.py; coolorbit/sim/simulator_agent.py"},
         "outcome": {"tone": "warn", "label": "refused, rerouted",
                     "text": "Waxes go to classical MD instead. Fair note: in our comparison, with web access and "
                             "NVIDIA's own prompt, the single agent refused MACE on eicosane too.",
                     "source": _cr("S25", CMP1_DOC)}},
        {"id": "accuracy", "title": "“The model is accurate for these metals.”",
         "shipped": {"text": "MACE-MPA-0 puts indium's melting point at 344.7 ± 21.3 K (measured 429.75 K) and "
                             "bismuth's at 408.3 ± 54.7 K (measured 544.56 K): 20% and 25% low, with error bars "
                             "that look reasonable.",
                     "source": _cr("S26", "boards/science-2; measured values from data/calibration/tier1.json")},
         "caught": {"who": "Sourced measurements and the trust rule.",
                    "text": "Measured values with sources and the pre-registered ±10% tolerance flag both. For "
                            "bismuth, a classical check that first passed its own calibration disagrees too.",
                    "source": _cr("S22, S26", "configs/trust_rules.json")},
         "outcome": {"tone": "warn", "label": "flagged",
                     "text": "Flagged and never ranked. MACE is within about 3% for copper and aluminium but 15-28% "
                             "low on indium, bismuth and tin, the very metals that matter for metal thermal batteries.",
                     "source": _cr("S26, S29", OUTLINE + " slide 8")}},
        {"id": "prep", "title": "“The crystal in the box is still a crystal.”",
         "shipped": {"text": "At tin's real melting point (505 K), MACE's tin crystal does not survive preparation. A "
                             "melting point read from that box would describe a liquid, not a crystal melting.",
                     "source": _cr("S26, S29", "boards/science-2 artifact 41ee63bb")},
         "caught": {"who": "The run gate (coexistence protocol 2).",
                    "text": "It checks the two-phase box before production. The crystal was lost in preparation, so "
                            "the run stops, says why, and is not retried: a longer run would repeat the artefact.",
                    "source": "coolorbit/sim/melting.py (--protocol 2); coolorbit/sim/simulator_agent.py "
                              "(V2_NO_RETRY_FAILURES)"},
         "outcome": {"tone": "bad", "label": "stopped, reported",
                     "text": "Reported as a failure, not a number. Extra runs show MACE's tin melts below 380 K, about "
                             "25% low or more.",
                     "source": _cr("S29", "runs/remote/supp")}},
        {"id": "approximation", "title": "“An alloy's solid is just its metals mixed together.”",
         "shipped": {"text": "Field's metal (bismuth, indium and tin): the simulated latent heat, 25.3 ± 0.2 "
                             "kJ/kg, lands close to the measurements (27.1 and 25.4 kJ/kg). But the calculation treats "
                             "the solid as a mix of pure metals, ignoring the compounds a real alloy forms.",
                     "source": _cr("S21, S23, S24")},
         "caught": {"who": "The LLM referee and the Fable adjudicator.",
                    "text": "The rule-based audit passed it; the LLM referee failed it on that approximation. They "
                            "disagreed, so the adjudicator (Claude Fable 5.1) read both arguments and upheld the fail.",
                    "source": _cr("S24", "boards/science-1 checks ada06981, 8af1ea18, 65e3e208")},
         "outcome": {"tone": "warn", "label": "flagged",
                     "text": "Flagged despite a good-looking number: it may be right for the wrong reason. Our alloy "
                             "latent heats are labelled as likely underestimates.",
                     "source": _cr("S24", OUTLINE + " slide 11")}},
        {"id": "checker", "title": "“The second opinion is reliable.”",
         "shipped": {"text": "A classical potential is only as good as its fit. The aluminium EAM potential melts "
                             "aluminium at 1041.4 K (+11.6%). Of four NIST MEAM potentials only bismuth's recovers its metal's measured "
                             "values: indium's is 63-77% high, tin's 17% or more low, gallium's cannot hold the "
                             "crystal.",
                     "source": _cr("S15, S22, S27")},
         "caught": {"who": "The calibration gate (the chemistry lead's rule).",
                    "text": "A checker counts only if it first recovers the measured value of the same material within "
                            "the pre-registered tolerance. Otherwise its check is posted as inconclusive: it can "
                            "neither confirm nor flag.",
                    "source": "scripts/post_classical_checks.py (reliability gate); " + MEMO + " 'Decisions applied'"},
         "outcome": {"tone": "warn", "label": "under review",
                     "text": "Aluminium stays under review even though MACE matches its measurement: a bad checker "
                             "may not confirm a good result.",
                     "source": _cr("S27", "docs/results/classical-check-eam.md")}},
    ]
    # One short key per step for the dashboard's mini timeline. Each key restates a value already in that step's
    # sourced text above (same source), so the page shows it without parsing sentences or typing numbers in.
    keys = {
        "superheating": ("1600 ± 100 K", "SuperheatingDetectorHook", "1327 K agrees"),
        "domain": ("MACE-MPA-0 on paraffin wax", "Premise checker", "Rerouted to classical MD"),
        "accuracy": ("20% and 25% low", "±10% tolerance + measurements", "Never ranked"),
        "prep": ("Tin crystal lost at 505 K", "Run gate (protocol 2)", "Reported as a failure"),
        "approximation": ("25.3 ± 0.2 kJ/kg", "Fable adjudicator", "Labelled an underestimate"),
        "checker": ("Al EAM 1041.4 K (+11.6%)", "Calibration gate", "Al stays under review"),
    }
    for case in cases:
        for step, text in zip(("shipped", "caught", "outcome"), keys[case["id"]]):
            case[step]["key"] = {"text": text, "source": case[step]["source"]}
    return {"eyebrow": "Caught by the loop",
            "title": "Where a clean-looking run hid a wrong assumption",
            "lede": "ALCHEMI computes exactly what it is given; it cannot tell a sound set-up from a flawed one. "
                    "These are real cases from our runs: what a single pass would have shipped, which agent or rule "
                    "caught it, and what happened next.",
            "nvidia": {"text": "NVIDIA saw the same pattern in 45 agent-written ALCHEMI pipelines: no configuration "
                               "pushed back on a physically ill-posed task.",
                       "source": "NVIDIA Technical Blog, Aug 18 2026 " + _cr("S14"), "url": NVIDIA_BLOG},
            "cases": cases,
            "source": OUTLINE + " claims register"}


# --------------------------------------------------------------------------- the whole context

def build_context(*, inputs_path: str | Path = BURST_INPUTS, rules: Optional[dict] = None,
                  rules_path: str | Path = TRUST_RULES, roles_path: str | Path = LLM_ROLES,
                  ranker_path: str | Path = RANKER_CONFIG, calibration_path: str | Path = CALIBRATION,
                  classical_root: str | Path = CLASSICAL_ROOT,
                  classical_run: "str | tuple[str, ...]" = CLASSICAL_RUN,
                  scenario: Optional[dict] = None) -> dict:
    rules = rules if rules is not None else _read_json(rules_path)
    roles = _read_json(roles_path)
    ranker = _read_json(ranker_path)
    calibration = load_calibration(calibration_path)
    win = ranker.get("window_K") or {}
    window = (win["min"], win["max"]) if win.get("min") is not None and win.get("max") is not None else None
    burst_out = burst(inputs_path, scenario, window)
    return {
        "schema": SCHEMA,
        "question": question(),
        "burst": burst_out,
        "head_to_head": head_to_head(burst_out, window),
        "methods": methods(rules, calibration, Path(classical_root), classical_run),
        "agent_graph": agent_graph(roles, rules, ranker),
        "trust_rule": trust_rule(rules, ranker),
        "comparison_setup": comparison_setup(roles),
        "comparison_findings": comparison_findings(),
        "gallium": gallium_structure(calibration=calibration),
        "catches": catches(),
        "deadline": {"iso": "2026-11-01T18:00:00Z",
                     "text": "Hacking ends Sunday Nov 1 2026 at 1:00 PM in Cambridge, MA: EST (UTC-5; daylight time "
                             "ends at 2 AM that day), so 18:00 UTC",
                     "source": "CLAUDE.md section 2 (Deadline)"},
        "claims": "We simulate and rank; we don't claim new materials.",
        "repo": GITHUB,
    }


def main() -> None:
    print(json.dumps(build_context(), indent=1))


if __name__ == "__main__":
    main()
