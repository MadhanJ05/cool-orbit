"""Verdict for the alpha-Ga stability check (standard library only). Run from metals/Ga/ after the LAMMPS run:

    python stability/check_ga.py stability/log.ga_stability stability/rdf_T302.92.dat

Pass only if (1) alpha-Ga is lower in energy than fcc at 0 K, (1b) its relaxed 0 K cell is within 3% of the
experimental cell (a, b, c), and at T: (2) < 5% of atoms moved > 1.5 A, (3) box lengths within 3% of the relaxed
alpha cell, (4) the close pair survives: the RDF running count reaches one neighbour by 2.55 A (alpha-Ga's
dimer is 2.44 A), and (5) the next shell stays beyond it: about one neighbour (<= 1.2) within 2.6 A.
The first local maximum of g(r) is reported too (at T the pair can be a shoulder of the next shell).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DIMER_A = 2.44  # alpha-Ga first-neighbour (dimer) distance, Donohue, The Structures of the Elements (1974)


def rdf_rows(rdf_path: Path) -> list[tuple[float, float, float]]:
    """(r, g(r), running coordination count) of the last time block of LAMMPS compute rdf output."""
    rows = [l.split() for l in Path(rdf_path).read_text().splitlines() if l.strip() and not l.startswith("#")]
    block = [r for r in rows if len(r) >= 4][-200:]
    return [(float(x[1]), float(x[2]), float(x[3])) for x in block]


def first_peak(rdf_path: Path) -> float:
    """r of the FIRST local maximum of g(r) above the mean density (g > 1), not the tallest peak.

    The g > 1 floor skips counting noise in the empty region before the first shell."""
    rows = rdf_rows(rdf_path)
    for i in range(1, len(rows) - 1):
        if rows[i][1] > 1.0 and rows[i][1] > rows[i - 1][1] and rows[i][1] >= rows[i + 1][1]:
            return rows[i][0]
    raise ValueError("g(r) has no local maximum")


def pair_distance(rdf_path: Path) -> float:
    """r at which the running coordination count first reaches 1 (where the closest neighbour sits)."""
    for r, _, count in rdf_rows(rdf_path):
        if count >= 1.0:
            return r
    raise ValueError("running count never reaches 1")


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
    pair = pair_distance(rdf_path)
    relaxed_vs_exp = [round(r / e, 4) for r, e in zip(relaxed, exp)]
    checks = {
        "alpha_below_fcc_at_0K": ea < ef,
        "relaxed_cell_within_3pct_of_experiment": all(abs(x - 1) < 0.03 for x in relaxed_vs_exp),
        "atoms_stay_put_at_T": moved < 0.05,
        "box_shape_kept_at_T": all(abs(x - 1) < 0.03 for x in ratios),
        "close_pair_survives_at_T": pair <= 2.55,
        "next_shell_stays_beyond_2.6A_at_T": coord <= 1.2,
    }
    return {"relaxed_cell_vs_experiment_abc": relaxed_vs_exp, "alpha_E0_eV_per_atom": ea, "fcc_E0_eV_per_atom": ef, "alpha_minus_fcc_meV_per_atom": round(1000 * (ea - ef), 2),
            "T_K": t, "moved_fraction": moved, "coord_within_2.6A": coord, "box_ratio_vs_relaxed": ratios,
            "rdf_first_local_max_A": peak, "pair_distance_running_count_1_A": pair, "checks": checks, "passed": all(checks.values())}


if __name__ == "__main__":
    out = verdict(Path(sys.argv[1]).read_text(), Path(sys.argv[2]))
    print(json.dumps(out, indent=2))
    sys.exit(0 if out["passed"] else 1)
