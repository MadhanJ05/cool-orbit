"""Analyze the LAMMPS outputs of the metal bundles (standard library only, so it runs anywhere).

Run from an element folder (it reads manifest.json there for the method block):

    python ../analyze.py coexist pe_T*.dat
    python ../analyze.py latent h_solid_T<Tm>.dat h_liquid_T<Tm>.dat --log-solid <log> --log-liquid <log>

Coexistence: same verdict rule as coolorbit/sim/melting.py (GB10): drop the first 5% of production, 10 block
means, delta = last block - first block, noise = std(diff(blocks)) / sqrt(2), threshold = max(0.002 eV/atom,
3 x noise); melting if delta > threshold, freezing if < -threshold, else stable. Melting point: bracket =
[highest freezing T, lowest melting T]; Tm = where a straight line through delta(T) of the runs in the bracket
crosses zero (clipped to the bracket; midpoint if the slope isn't positive), uncertainty = half the bracket.
**A run whose upper half did not melt (MELT_CHECK in its header) fails the whole analysis**: that box was a
perfect crystal heated at T, i.e. single-phase heating.

Latent: H = PE + KE per atom; L = H_liquid - H_solid converted with the atomic mass from manifest.json. Fails on
phase checks (moved fractions from the logs), enthalpy drift during production, or a box-shape change of the
solid (structure change). Records mean box lengths and the thermostat/barostat energy exchange (ecouple).

Prints JSON, including a `method` block for a classical_md check; it never posts anything itself.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

EV_TO_J = 1.602176634e-19
N_A = 6.02214076e23
PE_THRESHOLD = 0.002
MELT_LIQUID_MIN, MELT_CRYSTAL_MAX = 0.8, 0.05
SHAPE_TOL = 0.03          # solid box-length ratios may change by at most 3% during production


def read_columns(path: Path) -> tuple[dict, list[list[float]]]:
    header, rows = {}, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            header.update(dict(re.findall(r"(\w+)=(\S+)", line)))
            continue
        rows.append([float(x) for x in line.split()])
    if not rows:
        raise ValueError(f"{path}: no data rows")
    return header, rows


def block_means(values: list[float], n_blocks: int) -> list[float]:
    n = len(values)
    if n < n_blocks:
        raise ValueError(f"only {n} samples for {n_blocks} blocks")
    edges = [round(i * n / n_blocks) for i in range(n_blocks + 1)]
    return [statistics.fmean(values[edges[i]:edges[i + 1]]) for i in range(n_blocks)]


def drift(values: list[float], n_blocks: int, threshold: float) -> dict:
    means = block_means(values, n_blocks)
    delta = means[-1] - means[0]
    diffs = [b - a for a, b in zip(means, means[1:])]
    noise = statistics.stdev(diffs) / math.sqrt(2) if len(diffs) > 1 else 0.0
    thr = max(threshold, 3.0 * noise)
    return {"delta": delta, "noise": noise, "threshold": thr, "significant": abs(delta) > thr}


def coexist_verdict(pe: list[float], threshold: float = PE_THRESHOLD) -> dict:
    d = drift(pe[int(len(pe) * 0.05):], 10, threshold)
    verdict = "melting" if d["delta"] > d["threshold"] else "freezing" if d["delta"] < -d["threshold"] else "stable"
    return {"delta_pe_eV_per_atom": d["delta"], "block_noise_eV_per_atom": d["noise"],
            "threshold_eV_per_atom": d["threshold"], "verdict": verdict}


def melt_check(header: dict) -> tuple[bool, str]:
    try:
        liq, cry = float(header["melt_liquid_moved"]), float(header["melt_crystal_moved"])
    except (KeyError, ValueError):
        return False, "no MELT_CHECK in the header (old input?): can't prove the upper half melted"
    if liq < MELT_LIQUID_MIN:
        return False, f"upper half did not melt ({liq:.2f} < {MELT_LIQUID_MIN} of atoms moved): single-phase heating"
    if cry > MELT_CRYSTAL_MAX:
        return False, f"crystal half disturbed during the melt ({cry:.2f} > {MELT_CRYSTAL_MAX})"
    return True, ""


def interpolate(per_t: list[dict], lo: float, hi: float) -> float:
    """Zero crossing of a least-squares line through (T, delta PE) of the runs in [lo, hi] (like melting.py)."""
    pts = [(p["T_target_K"], p["delta_pe_eV_per_atom"]) for p in per_t if lo <= p["T_target_K"] <= hi]
    n = len(pts)
    mx, my = sum(x for x, _ in pts) / n, sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    slope = sum((x - mx) * (y - my) for x, y in pts) / sxx if sxx else 0.0
    if slope <= 0:
        return (lo + hi) / 2
    return min(max(mx - my / slope, lo), hi)


def load_manifest(path: Path | None) -> dict:
    if path is None or not Path(path).is_file():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def method_block(manifest: dict, kind: str, header: dict) -> dict | None:
    m = manifest.get("method")
    if not m:
        return None
    sub = m.get(kind, {})
    dt_ps = float(header.get("dt_ps", m["timestep_fs"] / 1000))
    prod = int(header["prod_steps"]) if "prod_steps" in header else None
    return {"engine": m["engine"], "model": m["model"], "nist_implementation": m["nist_implementation"],
            "doi": m["doi"], "protocol": sub.get("protocol"), "ensemble": sub.get("ensemble"),
            "timestep_fs": dt_ps * 1000, "n_atoms": int(header.get("n_atoms", sub.get("n_atoms", 0))) or None,
            "sim_length_ps": prod * dt_ps if prod else sub.get("sim_length_ps"), "seeds": 1,
            "seed": int(header["seed"]) if "seed" in header else m.get("seed")}


def analyze_coexist(paths: list[Path], threshold: float = PE_THRESHOLD, manifest: dict | None = None) -> dict:
    per_t, invalid, header0 = [], [], {}
    for p in paths:
        header, rows = read_columns(p)
        header0 = header0 or header
        t_target = float(header.get("T_target_K", "nan"))
        ok, why = melt_check(header)
        try:
            r = coexist_verdict([row[2] for row in rows], threshold)
        except ValueError as exc:  # e.g. a run that died early: too few samples for the blocks
            r = {"verdict": "invalid", "delta_pe_eV_per_atom": None}
            ok, why = False, f"too little production data ({exc})"
        r.update(file=str(p), T_target_K=t_target, T_measured_K=round(statistics.fmean(row[1] for row in rows), 1),
                 melt_check_ok=ok)
        if len(rows[0]) >= 8:
            r["ecouple_change_eV"] = rows[-1][7] - rows[0][7]
        if not ok:
            r["melt_check_problem"] = why
            invalid.append(f"T={t_target:g} K: {why}")
        per_t.append(r)
    per_t.sort(key=lambda r: r["T_target_K"])
    out = {"mode": "coexistence", "per_temperature": per_t,
           "method": method_block(manifest or {}, "coexistence", header0)}
    if invalid:
        out.update(status="failed", failure="melt_check",
                   reason="; ".join(invalid) + ". Rerun those temperatures with a higher Tmelt or longer melt.")
        return out
    melt = [r["T_target_K"] for r in per_t if r["verdict"] == "melting"]
    freeze = [r["T_target_K"] for r in per_t if r["verdict"] == "freezing"]
    if not melt:
        out.update(status="failed", failure="above_bracket", reason="no temperature melted: Tm is above the bracket")
    elif not freeze:
        out.update(status="failed", failure="below_bracket", reason="no temperature froze: Tm is below the bracket")
    elif max(freeze) >= min(melt):
        out.update(status="failed", failure="inconsistent",
                   reason=f"froze at {max(freeze)} K, at or above a melting T ({min(melt)} K): runs too short or noisy")
    else:
        lo, hi = max(freeze), min(melt)
        out.update(status="ok", property="melting_point", units="K", value=round(interpolate(per_t, lo, hi), 1),
                   uncertainty=round((hi - lo) / 2, 1), bracket_K=[lo, hi])
    return out


def enthalpy_stats(path: Path) -> dict:
    header, rows = read_columns(path)
    rows = rows[int(len(rows) * 0.2):]
    h = [row[2] + row[3] for row in rows]
    blocks = block_means(h, 5)
    d = drift(h, 5, PE_THRESHOLD)
    out = {"file": str(path), "phase": header.get("phase"), "header": header,
           "T_target_K": float(header.get("T_target_K", "nan")),
           "T_measured_K": statistics.fmean(row[1] for row in rows),
           "H_eV_per_atom": statistics.fmean(blocks), "H_sem": statistics.stdev(blocks) / math.sqrt(len(blocks)),
           "H_drift_eV_per_atom": d["delta"], "H_drift_significant": d["significant"],
           "volume_A3": statistics.fmean(row[4] for row in rows)}
    if len(rows[0]) >= 9:
        first, last = rows[: max(1, len(rows) // 10)], rows[-max(1, len(rows) // 10):]
        box0 = [statistics.fmean(r[i] for r in first) for i in (5, 6, 7)]
        box1 = [statistics.fmean(r[i] for r in last) for i in (5, 6, 7)]
        out["box_A_mean"] = [round(statistics.fmean(r[i] for r in rows), 4) for i in (5, 6, 7)]
        shape0 = [box0[1] / box0[0], box0[2] / box0[0]]
        shape1 = [box1[1] / box1[0], box1[2] / box1[0]]
        out["box_shape_change"] = [round(b / a - 1, 4) for a, b in zip(shape0, shape1)]
        out["ecouple_change_eV"] = rows[-1][8] - rows[0][8]
    return out


def moved_fraction(log: Path | None) -> float | None:
    if log is None:
        return None
    m = re.findall(r"PHASE_CHECK phase=\w+ moved_fraction ([0-9.eE+-]+)", Path(log).read_text(encoding="utf-8"))
    return float(m[-1]) if m else None


def analyze_latent(solid: Path, liquid: Path, mass_amu: float | None = None, log_solid: Path | None = None,
                   log_liquid: Path | None = None, manifest: dict | None = None) -> dict:
    manifest = manifest or {}
    if mass_amu is None:
        mass_amu = manifest.get("atomic_mass_amu")
    if mass_amu is None:
        raise ValueError("atomic mass unknown: pass --mass-amu or run from the element folder (manifest.json)")
    s, l = enthalpy_stats(solid), enthalpy_stats(liquid)
    dh = l["H_eV_per_atom"] - s["H_eV_per_atom"]
    err = math.hypot(l["H_sem"], s["H_sem"])
    to_j_per_kg = EV_TO_J * N_A / (mass_amu / 1000.0)
    out = {"mode": "latent", "solid": s, "liquid": l, "delta_H_eV_per_atom": dh,
           "method": method_block(manifest, "latent", s["header"])}
    problems = []
    if s["phase"] != "solid" or l["phase"] != "liquid":
        problems.append(f"files are phases {s['phase']!r} and {l['phase']!r}, expected solid and liquid")
    if abs(s["T_target_K"] - l["T_target_K"]) > 1e-6:
        problems.append("solid and liquid were run at different temperatures")
    if s["header"].get("barostat") not in (None, "aniso"):
        problems.append(f"solid used a {s['header'].get('barostat')} barostat; non-cubic solids need aniso")
    fs, fl = moved_fraction(log_solid), moved_fraction(log_liquid)
    out.update(solid_moved_fraction=fs, liquid_moved_fraction=fl)
    if fl is not None and fl < 0.5:
        problems.append("liquid may have recrystallized (few atoms moved)")
    if fs is not None and fs > 0.2:
        problems.append("solid may have melted (many atoms moved)")
    if fs is None or fl is None:
        out["warning"] = "moved-fraction phase checks skipped: pass --log-solid/--log-liquid"
    for name, ph in (("solid", s), ("liquid", l)):
        if ph["H_drift_significant"]:
            problems.append(f"{name} enthalpy drifts by {ph['H_drift_eV_per_atom']:+.4f} eV/atom during production "
                            "(a phase change in progress?)")
    if any(abs(x) > SHAPE_TOL for x in s.get("box_shape_change", [])):
        problems.append(f"solid box shape changed by {s['box_shape_change']} (structure change?)")
    if dh <= 0:
        problems.append("non-positive enthalpy difference")
    if problems:
        out.update(status="failed", failure="phase_check", reason="; ".join(problems))
    else:
        out.update(status="ok", property="latent_heat", units="J/kg", value=round(dh * to_j_per_kg),
                   uncertainty=round(err * to_j_per_kg))
    for ph in (s, l):
        ph.pop("header", None)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", type=Path, default=Path("manifest.json"), help="default: ./manifest.json")
    sub = ap.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("coexist")
    c.add_argument("files", nargs="+", type=Path)
    c.add_argument("--threshold", type=float, default=PE_THRESHOLD)
    lt = sub.add_parser("latent")
    lt.add_argument("solid", type=Path)
    lt.add_argument("liquid", type=Path)
    lt.add_argument("--mass-amu", type=float, help="default: from manifest.json")
    lt.add_argument("--log-solid", type=Path)
    lt.add_argument("--log-liquid", type=Path)
    args = ap.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.mode == "coexist":
        out = analyze_coexist(args.files, args.threshold, manifest)
    else:
        out = analyze_latent(args.solid, args.liquid, args.mass_amu, args.log_solid, args.log_liquid, manifest)
    print(json.dumps(out, indent=2))
    return 0 if out["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
