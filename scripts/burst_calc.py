"""Burst calculation: can a thermal battery soak up a compute burst fast enough, and what does the whole device weigh?

Per kilogram, paraffin stores more heat than low-melting metals. But in a short burst the heat has to reach the
PCM through what has already melted, and paraffin conducts heat ~50x worse. This script asks, for each material:
how much PCM, how much heat-exchange area (fins) it needs so the hot wall stays within dT of the melting point,
and the total mass (PCM + aluminium fins). Every input value carries a source (data/burst/inputs.json); a material
with any unsourced value is skipped and reported, never filled in.

Model (quasi-steady Stefan problem, 1-D, constant power P for a burst of duration t, heat entering from a wall of
area A and crossing the melted layer of thickness s by conduction):
    PCM mass         m = P t / L                       (latent heat only; sensible heat neglected)
    melted depth     s = m / (rho_liquid A)
    wall superheat   dT = P s / (k_liquid A)  ->  A_min = P * sqrt(t / (L rho_liquid k_liquid dT))
    fins             aluminium sheet of thickness d providing A_min of surface (two faces): m_fin = rho_Al d A_min / 2
    container volume V = m / min(rho_solid, rho_liquid)   (the bulkier phase sets the container)
Assumptions that favour paraffin (so a metal win is conservative): fin efficiency 1 (no temperature drop along the
fin), no container or interface mass, no sensible heat. Fin spacing ~2 s.

    python scripts/burst_calc.py [--power 1000] [--minutes 15] [--dT 10] [--fin-mm 0.5] [--json]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

INPUTS = Path(__file__).resolve().parents[1] / "data" / "burst" / "inputs.json"
NEEDED = ("latent_heat_J_per_kg", "density_solid_kg_m3", "density_liquid_kg_m3", "conductivity_liquid_W_mK")


def sourced(entry: dict) -> bool:
    return entry.get("value") is not None and bool(str(entry.get("source") or "").strip())


def evaluate(mat: dict, fin: dict, power: float, t_s: float, dT: float, fin_m: float) -> dict:
    v = {k: mat[k]["value"] for k in NEEDED}
    m = power * t_s / v["latent_heat_J_per_kg"]
    area = power * math.sqrt(t_s / (v["latent_heat_J_per_kg"] * v["density_liquid_kg_m3"]
                                    * v["conductivity_liquid_W_mK"] * dT))
    depth = m / (v["density_liquid_kg_m3"] * area)
    fin_mass = fin["density_kg_m3"]["value"] * fin_m * area / 2
    volume_l = m / min(v["density_solid_kg_m3"], v["density_liquid_kg_m3"]) * 1000
    return {"material": mat["name"], "pcm_kg": round(m, 3), "area_m2": round(area, 4),
            "melt_depth_mm": round(depth * 1000, 2), "fin_kg": round(fin_mass, 3),
            "total_kg": round(m + fin_mass, 3), "pcm_volume_L": round(volume_l, 3),
            "figure_rhoLk": v["latent_heat_J_per_kg"] * v["density_liquid_kg_m3"] * v["conductivity_liquid_W_mK"]}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--power", type=float, default=1000.0, help="burst heat load, W")
    ap.add_argument("--minutes", type=float, default=15.0, help="burst duration")
    ap.add_argument("--dT", type=float, default=10.0, help="allowed wall superheat above the melting point, K")
    ap.add_argument("--fin-mm", type=float, default=0.5, help="aluminium fin thickness, mm")
    ap.add_argument("--inputs", default=str(INPUTS))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.inputs).read_text())
    fin = data["fin_material"]
    if not all(sourced(fin[k]) for k in ("density_kg_m3", "conductivity_W_mK")):
        raise SystemExit("fin material (aluminium) values are not sourced yet; see data/burst/inputs.json")
    rows, skipped = [], []
    for mat in data["materials"]:
        missing = [k for k in NEEDED if not sourced(mat.get(k, {}))]
        if missing:
            skipped.append({"material": mat["name"], "missing": missing})
            continue
        rows.append(evaluate(mat, fin, args.power, args.minutes * 60, args.dT, args.fin_mm / 1000))
    out = {"scenario": {"power_W": args.power, "minutes": args.minutes, "dT_K": args.dT, "fin_mm": args.fin_mm},
           "results": rows, "skipped_unsourced": skipped}
    if args.json:
        print(json.dumps(out, indent=1))
        return
    print(f"Burst {args.power:g} W for {args.minutes:g} min, wall <= {args.dT:g} K above Tm, Al fins {args.fin_mm:g} mm")
    print(f"{'material':22s} {'PCM kg':>8s} {'area m2':>8s} {'depth mm':>9s} {'fins kg':>8s} {'TOTAL kg':>9s} {'PCM L':>7s}")
    for r in rows:
        print(f"{r['material']:22s} {r['pcm_kg']:8.2f} {r['area_m2']:8.3f} {r['melt_depth_mm']:9.1f} "
              f"{r['fin_kg']:8.2f} {r['total_kg']:9.2f} {r['pcm_volume_L']:7.2f}")
    for s in skipped:
        print(f"skipped {s['material']}: unsourced {', '.join(s['missing'])}")


if __name__ == "__main__":
    main()
