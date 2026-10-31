"""Context for the judges' dashboard: the question, the wax-vs-metal burst calculation and the methods, with sources.

Built at export time from repo files only and carried in ``data.js`` as ``context`` (dashboard/export.py), so the
GB10 push loop publishes it together with the boards:

  scripts/burst_calc.py + data/burst/inputs.json        one compute burst, paraffin vs metals (its evaluate())
  configs/trust_rules.json                              the frozen tolerances (the pre-registration)
  configs/llm_roles.json                                which Claude model each agent role uses
  coolorbit/agents/ranker_config.json                   the operating window
  data/calibration/tier1.json                           measured values (classical-check gate, hook control)
  runs/classical/<run>/metals/<El>/analysis_{coexistence,latent}.json   classical MD (MEAM/LAMMPS) checks
  docs/pitch/outline.md (claims register)               wording and sources of the framing lines (S1, S2, ...)

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
CLASSICAL_ROOT = REPO / "runs" / "classical"
# Runs in priority order: each element is read from the first run that has it (the refined Bi bracket and Bi's
# latent heat are in meam-bi-refine; In and Sn's widest brackets are in meam-1).
CLASSICAL_RUN = ("meam-bi-refine", "meam-1")
GITHUB = "https://github.com/MadhanJ05/cool-orbit"

# scripts/burst_calc.py's argparse defaults (a test checks they still match the script).
DEFAULT_SCENARIO = {"power_W": 1000.0, "minutes": 15.0, "dT_K": 10.0, "fin_mm": 0.5}

OUTLINE = "docs/pitch/outline.md"
MEMO = "docs/chemistry-lead-memo-2026-10-31.md"
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


# --------------------------------------------------------------------------- the question

def question() -> dict:
    return {
        "title": "Which thermal battery best buffers bursty AI-chip heat in orbit, and can we trust the simulations?",
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


# --------------------------------------------------------------------------- agents, pipeline, trust rule

AGENTS = [  # (id, name, what it does, llm role in configs/llm_roles.json or None, engine when no LLM)
    ("scouts", "Scouts", "One per material family. Propose candidates and post simulation requests.", "scout", None),
    ("premise", "Premise checker", "Before any run: is this property measurable with this method, model and "
                                   "timescale? Unsure cases go to the adjudicator.", "premise_checker", None),
    ("simulator", "Simulator", "Claims requests and runs ALCHEMI + MACE-MPA-0 on the GB10 GPU; posts every result "
                               "with its method, and every failure as a run report.", None, "code + GB10 GPU"),
    ("rules", "Rule referee", "Method audit by rules: single-phase heating, Langevin for transport, out-of-domain "
                              "model, timestep, box size.", None, "rules, no LLM"),
    ("llm_referee", "LLM referee", "Audits every result's method for subtler errors.", "referee_llm_audit", None),
    ("classical", "Classical referee", "Posts LAMMPS/MEAM melting points as independent checks, only from potentials "
                                       "that recover the measured value.", None, "LAMMPS on the GB10 CPU"),
    ("adjudicator", "Adjudicator", "Settles referee disagreements and premise questions the others can't decide.",
     "adjudicator", None),
    ("librarian", "Librarian", "Supplies measured values with their sources (data/calibration/tier1.json).",
     "librarian", None),
    ("ranker", "Ranker", "Keeps the shortlist: trusted results only, inside the operating window, most heat per "
                         "kilogram first.", None, "code"),
]

PIPELINE = [
    {"title": "Propose", "text": "Scouts post candidates and simulation requests to the board.", "agents": ["scouts"]},
    {"title": "Premise check", "text": "Is the property measurable with this method and timescale?",
     "agents": ["premise", "adjudicator"]},
    {"title": "Simulate on the GB10", "text": "ALCHEMI + MACE-MPA-0, batched on the GPU.", "agents": ["simulator"]},
    {"title": "Try to prove it wrong", "text": "Rule audit, LLM audit, classical MD and measured values; disputes go "
                                               "to the adjudicator.",
     "agents": ["rules", "llm_referee", "classical", "librarian", "adjudicator"]},
    {"title": "Trust rule", "text": "The status is derived from the evidence. No agent can set it.", "agents": []},
    {"title": "Shortlist", "text": "Trusted results only, inside the operating window.", "agents": ["ranker"]},
]


def agents(roles: dict) -> list[dict]:
    out = []
    for aid, name, does, role, engine in AGENTS:
        cfg = (roles.get("roles") or {}).get(role) if role else None
        out.append({"id": aid, "name": name, "does": does,
                    "model": model_name(cfg.get("model")) if cfg else None,
                    "engine": engine if not cfg else None,
                    "source": ("configs/llm_roles.json (role " + role + ")") if cfg else
                              "coolorbit/agents/ and scripts/post_classical_checks.py (no LLM role in configs/llm_roles.json)"})
    return out


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
        "source": "CLAUDE.md section 13; coolorbit/eval/arms.md; " + OUTLINE + " slide 7",
    }


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
    return {
        "schema": SCHEMA,
        "question": question(),
        "burst": burst(inputs_path, scenario, window),
        "methods": methods(rules, calibration, Path(classical_root), classical_run),
        "agents": agents(roles),
        "pipeline": PIPELINE,
        "coordination": {"text": "No boss: agents coordinate only through the shared results board (artifacts with "
                                 "lineage, open needs and claims).",
                         "source": "coolorbit/board/store.py; coolorbit/agents/__init__.py"},
        "trust_rule": trust_rule(rules, ranker),
        "comparison_setup": comparison_setup(roles),
        "claims": "We simulate and rank; we don't claim new materials.",
        "repo": GITHUB,
    }


def main() -> None:
    print(json.dumps(build_context(), indent=1))


if __name__ == "__main__":
    main()
