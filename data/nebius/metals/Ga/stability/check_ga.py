"""Verdict for the alpha-Ga stability check (standard library only). Run from metals/Ga/ after the LAMMPS run:

    python stability/check_ga.py stability/log.ga_stability stability/rdf_T302.92.dat

Pass only if (1) alpha-Ga is lower in energy than fcc at 0 K, (1b) its relaxed 0 K cell is within 3% of the
experimental cell (a, b, c), and at T: (2) < 5% of atoms moved > 1.5 A,
(3) box lengths within 3% of the relaxed alpha cell, (4) about one neighbour within 2.6 A per atom (the dimer),
(5) the first RDF peak within 0.15 A of the measured alpha-Ga dimer distance, 2.44 A.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DIMER_A = 2.44  # alpha-Ga first-neighbour (dimer) distance, Donohue, The Structures of the Elements (1974)


def first_peak(rdf_path: Path) -> float:
    rows = [l.split() for l in Path(rdf_path).read_text().splitlines() if l.strip() and not l.startswith("#")]
    blocks = [r for r in rows if len(r) >= 3]
    last = blocks[-200:]  # the last time block (200 bins)
    r = [float(x[1]) for x in last]
    g = [float(x[2]) for x in last]
    return r[max(range(len(g)), key=g.__getitem__)]


NUM = r"(-?[0-9][0-9.eE+-]*)"


def last(pattern: str, text: str) -> re.Match:
    """The last match whose groups are numbers (LAMMPS echoes the print command before its output)."""
    found = list(re.finditer(pattern.replace("(N)", NUM), text))
    if not found:
        raise ValueError(f"no line matching {pattern!r} in the log")
    return found[-1]


def verdict(log_text: str, rdf_path: Path) -> dict:
    exp = [float(last(r"STABILITY experimental_box (N) (N) (N)", log_text).group(i)) for i in (1, 2, 3)]
    m0 = last(r"STABILITY alpha_E0_eV_per_atom (N) box (N) (N) (N)", log_text)
    ea, relaxed = float(m0.group(1)), [float(m0.group(i)) for i in (2, 3, 4)]
    ef = float(last(r"STABILITY fcc_E0_eV_per_atom (N)", log_text).group(1))
    m = last(r"STABILITY alpha_at_T T (N) moved_fraction (N) coord_2.6A (N) box_ratio (N) (N) (N)", log_text)
    t, moved, coord = float(m.group(1)), float(m.group(2)), float(m.group(3))
    ratios = [float(m.group(i)) for i in (4, 5, 6)]
    peak = first_peak(rdf_path)
    relaxed_vs_exp = [round(r / e, 4) for r, e in zip(relaxed, exp)]
    checks = {
        "alpha_below_fcc_at_0K": ea < ef,
        "relaxed_cell_within_3pct_of_experiment": all(abs(x - 1) < 0.03 for x in relaxed_vs_exp),
        "atoms_stay_put_at_T": moved < 0.05,
        "box_shape_kept_at_T": all(abs(x - 1) < 0.03 for x in ratios),
        "one_dimer_neighbour_within_2.6A": 0.8 <= coord <= 1.2,
        "first_peak_near_2.44A": abs(peak - DIMER_A) <= 0.15,
    }
    return {"relaxed_cell_vs_experiment_abc": relaxed_vs_exp, "alpha_E0_eV_per_atom": ea, "fcc_E0_eV_per_atom": ef, "alpha_minus_fcc_meV_per_atom": round(1000 * (ea - ef), 2),
            "T_K": t, "moved_fraction": moved, "coord_within_2.6A": coord, "box_ratio_vs_relaxed": ratios,
            "rdf_first_peak_A": peak, "checks": checks, "passed": all(checks.values())}


if __name__ == "__main__":
    out = verdict(Path(sys.argv[1]).read_text(), Path(sys.argv[2]))
    print(json.dumps(out, indent=2))
    sys.exit(0 if out["passed"] else 1)
