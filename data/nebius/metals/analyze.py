"""Analyze the LAMMPS outputs of the metal bundles (standard library only, so it runs anywhere).

    python ../analyze.py coexist pe_T*.dat                         # melting point from the bracket
    python ../analyze.py latent --mass-amu 118.71 h_solid_T*.dat h_liquid_T*.dat [--log-solid L --log-liquid L]

The coexistence verdict is the same rule as coolorbit/sim/melting.py (GB10): drop the first 5% of production,
split into 10 block means, delta = last block - first block; noise = std(diff(blocks)) / sqrt(2);
threshold = max(0.002 eV/atom, 3 x noise); melting if delta > threshold, freezing if < -threshold, else stable.
Melting point = midpoint between the highest freezing and the lowest melting temperature; uncertainty = half
the gap. Prints JSON with the numbers a referee posts as a classical_md check (value, uncertainty, units,
method). It never posts anything itself.
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


def coexist_verdict(pe: list[float], threshold: float = PE_THRESHOLD) -> dict:
    pe = pe[int(len(pe) * 0.05):]
    means = block_means(pe, 10)
    delta = means[-1] - means[0]
    diffs = [b - a for a, b in zip(means, means[1:])]
    noise = statistics.stdev(diffs) / math.sqrt(2) if len(diffs) > 1 else 0.0
    thr = max(threshold, 3.0 * noise)
    verdict = "melting" if delta > thr else "freezing" if delta < -thr else "stable"
    return {"delta_pe_eV_per_atom": delta, "block_noise_eV_per_atom": noise, "threshold_eV_per_atom": thr,
            "verdict": verdict}


def analyze_coexist(paths: list[Path], threshold: float = PE_THRESHOLD) -> dict:
    per_t = []
    for p in paths:
        header, rows = read_columns(p)
        t_target = float(header.get("T_target_K", "nan"))
        r = coexist_verdict([row[2] for row in rows], threshold)
        r.update(file=str(p), T_target_K=t_target, T_measured_K=round(statistics.fmean(row[1] for row in rows), 1))
        per_t.append(r)
    per_t.sort(key=lambda r: r["T_target_K"])
    melt = [r["T_target_K"] for r in per_t if r["verdict"] == "melting"]
    freeze = [r["T_target_K"] for r in per_t if r["verdict"] == "freezing"]
    out = {"mode": "coexistence", "per_temperature": per_t}
    if not melt:
        out.update(status="failed", failure="above_bracket", reason="no temperature melted: Tm is above the bracket")
    elif not freeze:
        out.update(status="failed", failure="below_bracket", reason="no temperature froze: Tm is below the bracket")
    elif max(freeze) > min(melt):
        out.update(status="failed", failure="inconsistent",
                   reason=f"a freezing T ({max(freeze)} K) is above a melting T ({min(melt)} K): runs too short or noisy")
    else:
        lo, hi = max(freeze), min(melt)
        out.update(status="ok", property="melting_point", units="K", value=round((lo + hi) / 2, 1),
                   uncertainty=round((hi - lo) / 2, 1), bracket_K=[lo, hi])
    return out


def enthalpy_stats(path: Path) -> dict:
    header, rows = read_columns(path)
    rows = rows[int(len(rows) * 0.2):]
    h = [row[2] + row[3] for row in rows]
    blocks = block_means(h, 5)
    return {"file": str(path), "phase": header.get("phase"), "T_target_K": float(header.get("T_target_K", "nan")),
            "T_measured_K": statistics.fmean(row[1] for row in rows),
            "H_eV_per_atom": statistics.fmean(blocks), "H_sem": statistics.stdev(blocks) / math.sqrt(len(blocks)),
            "volume_A3": statistics.fmean(row[4] for row in rows)}


def moved_fraction(log: Path | None) -> float | None:
    if log is None:
        return None
    m = re.findall(r"PHASE_CHECK phase=\w+ moved_fraction (\S+)", Path(log).read_text(encoding="utf-8"))
    return float(m[-1]) if m else None


def analyze_latent(solid: Path, liquid: Path, mass_amu: float, log_solid: Path | None = None,
                   log_liquid: Path | None = None) -> dict:
    s, l = enthalpy_stats(solid), enthalpy_stats(liquid)
    dh = l["H_eV_per_atom"] - s["H_eV_per_atom"]
    err = math.hypot(l["H_sem"], s["H_sem"])
    to_j_per_kg = EV_TO_J * N_A / (mass_amu / 1000.0)
    out = {"mode": "latent", "solid": s, "liquid": l, "delta_H_eV_per_atom": dh}
    problems = []
    if s["phase"] != "solid" or l["phase"] != "liquid":
        problems.append(f"files are phases {s['phase']!r} and {l['phase']!r}, expected solid and liquid")
    if abs(s["T_target_K"] - l["T_target_K"]) > 1e-6:
        problems.append("solid and liquid were run at different temperatures")
    fs, fl = moved_fraction(log_solid), moved_fraction(log_liquid)
    out.update(solid_moved_fraction=fs, liquid_moved_fraction=fl)
    if fl is not None and fl < 0.5:
        problems.append("liquid may have recrystallized (few atoms moved)")
    if fs is not None and fs > 0.2:
        problems.append("solid may have melted (many atoms moved)")
    if fs is None or fl is None:
        out["warning"] = "phase checks skipped: pass --log-solid/--log-liquid (log.lammps of each run)"
    if dh <= 0:
        problems.append("non-positive enthalpy difference")
    if problems:
        out.update(status="failed", failure="phase_check", reason="; ".join(problems))
    else:
        out.update(status="ok", property="latent_heat", units="J/kg", value=round(dh * to_j_per_kg),
                   uncertainty=round(err * to_j_per_kg))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("coexist")
    c.add_argument("files", nargs="+", type=Path)
    c.add_argument("--threshold", type=float, default=PE_THRESHOLD)
    lt = sub.add_parser("latent")
    lt.add_argument("solid", type=Path)
    lt.add_argument("liquid", type=Path)
    lt.add_argument("--mass-amu", type=float, required=True)
    lt.add_argument("--log-solid", type=Path)
    lt.add_argument("--log-liquid", type=Path)
    args = ap.parse_args(argv)
    if args.mode == "coexist":
        out = analyze_coexist(args.files, args.threshold)
    else:
        out = analyze_latent(args.solid, args.liquid, args.mass_amu, args.log_solid, args.log_liquid)
    print(json.dumps(out, indent=2))
    return 0 if out["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
