"""Build the classical-MD (LAMMPS + MEAM) input bundles for the low-melting metals Ga, In, Sn, Bi.

    .venv/bin/python data/nebius/build_metal_bundles.py          # (re)writes data/nebius/metals/<El>/

This is the independent check for the metal-alloy track: a fundamentally different method (classical
MEAM potentials from the NIST Interatomic Potentials Repository) from the MLIP (MACE) runs on the GB10.
Files only: nothing here submits a job. See data/nebius/README.md.

Per element it writes: data.coexist.lmp (orthogonal supercell, elongated along z), data.solid.lmp (smaller
cube-ish cell for the latent-heat runs), potential.mod, the potential files' SHA-256 check, and
manifest.json (sources, settings, every job's command line). The potential files themselves were
downloaded from NIST and are committed under <El>/potential/ with their citation in SOURCES.md.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from ase.build import make_supercell  # noqa: E402
from ase.io import write  # noqa: E402

from coolorbit.sim.alloys import ELEMENT_CRYSTALS, element_crystal  # noqa: E402

OUT = HERE / "metals"
NIST = "https://www.ctcms.nist.gov/potentials/entry/"

# Settings mirror coolorbit/sim/melting.py production defaults (dt 2 fs, thermostat 100 fs, barostat 1000 fs,
# equil 4000 / melt 6000 / quench 3000 / production 25000 steps, record every 50, |dPE| threshold 0.002 eV/atom,
# T_melt = 1.6 x T_prep). Same protocol, different method.
SETTINGS = {"dt_ps": 0.002, "tdamp_ps": 0.1, "pdamp_ps": 1.0, "equil_steps": 4000, "melt_steps": 6000,
            "quench_steps": 3000, "prod_steps": 25000, "record_every": 50, "pe_threshold_eV_per_atom": 0.002,
            "t_melt_factor": 3.0, "latent_t_hot_factor": 3.0, "seed": 2026}
# T_melt = 3 x T_prep (not melting.py's 1.6x): with the thermostat now measuring only the liquid half (fix_modify temp
# tliq), 1.6x is the real temperature of the melting half, and perfect-crystal halves superheat well past that.
CALIBRATION = REPO / "data" / "calibration" / "tier1.json"
DROPPED = {
    "Ga": "failed the alpha-Ga stability check (metals/Ga/stability/RESULT.md): the relaxed 0 K cell is off by -8.4% (b) "
          "and +6.6% (c) and the 2.44 A dimer is lost (first neighbour peak 2.71 A, ~1.9 neighbours within 2.6 A), so "
          "this potential's 'alpha-Ga' is a different structure; its melting point would not be gallium's.",
}


def tm_guess(symbol: str) -> tuple[float, str]:
    """Measured melting point from the Tier-1 calibration set (Teammate 1), used only to place the bracket."""
    data = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    recs = data["records"] if isinstance(data, dict) else data
    hits = [r for r in recs if r["candidate"] == symbol and r["property"] == "melting_point" and r["value"] is not None]
    if len(hits) != 1:
        raise ValueError(f"{symbol}: expected one sourced melting_point in {CALIBRATION}, found {len(hits)}")
    return float(hits[0]["value"]), f"data/calibration/tier1.json ({hits[0]['source']})"
BRACKET_FACTORS = [0.80, 0.90, 1.00, 1.10, 1.20]  # wide: the MEAM models' own melting points are not published
COEXIST_MIN_LATERAL_A, COEXIST_MIN_Z_FACTOR = 24.0, 2.0
SOLID_MIN_SIDE_A = 20.0

ELEMENTS = {
    "Ga": {
        "nist_entry": "2009--Do-E-C-Shin-Y-H-Lee-B-J--Ga-In",
        "nist_implementation": "2009--Do-E-C--Ga-In--LAMMPS--ipr1",
        "citation": "E.C. Do, Y.-H. Shin, and B.-J. Lee (2009), \"Atomistic modeling of III-V nitrides: modified "
                    "embedded-atom method interatomic potentials for GaN, InN and Ga1-xInxN\", J. Phys.: Condens. "
                    "Matter 21(32), 325801.",
        "doi": "10.1088/0953-8984/21/32/325801",
        "files": ["library.meam", "GaIn.meam"],
        "pair_coeff": "pair_coeff * * potential/library.meam Ga In potential/GaIn.meam Ga",
        "caveats": ["The Ga parameters come from a Ga-In(-N) alloy fit. NIST warns that elemental potentials taken "
                    "from alloy descriptions may not work well for the pure species; validate before trusting.",
                    "alpha-Ga is a covalent-dimer (Cmce) metal; 2NN MEAM fitted with an fcc reference may not "
                    "reproduce its structure. Check the solid stays alpha-Ga during the solid NPT stage.",
                    "No NIST potential for pure Ga ships LAMMPS files (Belashchenko 2012 EAM is liquid-only, no files)."],
    },
    "In": {
        "nist_entry": "2008--Do-E-C-Shin-Y-H-Lee-B-J--In",
        "nist_implementation": "2008--Do-E-C--In--LAMMPS--ipr1",
        "citation": "E.C. Do, Y.-H. Shin, and B.-J. Lee (2008), \"A modified embedded-atom method interatomic "
                    "potential for indium\", Calphad 32(1), 82-88.",
        "doi": "10.1016/j.calphad.2007.08.004",
        "files": ["library.meam", "In.meam"],
        "pair_coeff": "pair_coeff * * potential/library.meam In potential/In.meam In",
        "caveats": [],
    },
    "Sn": {
        "nist_entry": "2018--Ko-W-S-Kim-D-H-Kwon-Y-J-Lee-M--Sn",
        "nist_implementation": "2018--Ko-W-S--Sn--LAMMPS--ipr1",
        "citation": "W.-S. Ko, D.-H. Kim, Y.-J. Kwon, and M. Lee (2018), \"Atomistic Simulations of Pure Tin Based "
                    "on a New Modified Embedded-Atom Method Interatomic Potential\", Metals 8(11), 900.",
        "doi": "10.3390/met8110900",
        "files": ["library.meam", "Sn.meam"],
        "pair_coeff": "pair_coeff * * potential/library.meam Sn potential/Sn.meam Sn",
        "caveats": ["Starts from beta-Sn (white tin), the phase that melts; alpha-Sn is irrelevant above 286 K."],
    },
    "Bi": {
        "nist_entry": "2021--Zhou-H-Dickel-D-E-Baskes-M-I-et-al--Bi",
        "nist_implementation": "2021--Zhou-H--Bi--LAMMPS--ipr1",
        "citation": "H. Zhou, D.E. Dickel, M.I. Baskes, S. Mun, and M. Asle Zaeem (2021), \"A modified "
                    "embedded-atom method interatomic potential for bismuth\", Modelling Simul. Mater. Sci. Eng. "
                    "29(6), 065008.",
        "doi": "10.1088/1361-651x/ac095c",
        "files": ["Bi.library.meam", "Bi.parameter.meam"],
        "pair_coeff": "pair_coeff * * potential/Bi.library.meam Bi potential/Bi.parameter.meam Bi",
        "caveats": ["The paper's abstract says the potential reproduces the melting point of pure Bi, so the "
                    "classical check is partly fitted to the experimental value for Bi; say so when comparing."],
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def orthogonal_cell(symbol: str):
    """The reference crystal as an orthogonal cell (Bi's hexagonal cell becomes orthohexagonal, 2x atoms)."""
    atoms = element_crystal(symbol)
    angles = atoms.cell.cellpar()[3:]
    if np.allclose(angles, 90.0):
        return atoms
    if np.allclose(angles, [90.0, 90.0, 120.0]):
        ortho = make_supercell(atoms, [[1, 0, 0], [1, 2, 0], [0, 0, 1]])
        if not np.allclose(ortho.cell.cellpar()[3:], 90.0, atol=1e-6):
            raise AssertionError(f"{symbol}: orthohexagonal transform did not give an orthogonal cell")
        return ortho
    raise ValueError(f"{symbol}: no orthogonalization for cell angles {angles}")


def reps_for(cell_lengths, min_xy: float, z_factor: float | None, min_side: float | None = None) -> tuple:
    a, b, c = cell_lengths
    if z_factor is None:
        return tuple(max(1, math.ceil(min_side / L)) for L in (a, b, c))
    nx, ny = max(1, math.ceil(min_xy / a)), max(1, math.ceil(min_xy / b))
    nz = max(1, math.ceil(z_factor * max(nx * a, ny * b) / c))
    return nx, ny, nz


def build(symbol: str, out_root: Path = OUT) -> dict:
    info = ELEMENTS[symbol]
    d = out_root / symbol
    pot = d / "potential"
    missing = [f for f in info["files"] if not (pot / f).is_file()]
    if missing:
        raise FileNotFoundError(f"{symbol}: potential file(s) {missing} not in {pot}; download them from "
                                f"{NIST}{info['nist_entry']}/")
    unit = orthogonal_cell(symbol)
    L = unit.cell.lengths()
    coexist_reps = reps_for(L, COEXIST_MIN_LATERAL_A, COEXIST_MIN_Z_FACTOR)
    solid_reps = reps_for(L, 0, None, SOLID_MIN_SIDE_A)
    coexist, solid = unit.repeat(coexist_reps), unit.repeat(solid_reps)
    for name, atoms in (("data.coexist.lmp", coexist), ("data.solid.lmp", solid)):
        write(d / name, atoms, format="lammps-data", atom_style="atomic", masses=True, specorder=[symbol])
    (d / "potential.mod").write_text(
        f"# {symbol}: 2NN MEAM, NIST IPR {info['nist_implementation']} (doi:{info['doi']}). Needs the MEAM package.\n"
        f"pair_style meam\n{info['pair_coeff']}\n", encoding="utf-8")

    tg, tg_source = tm_guess(symbol)
    temps = [round(f * tg, 1) for f in BRACKET_FACTORS]
    t_prep = round(tg, 1)
    t_melt = round(SETTINGS["t_melt_factor"] * t_prep, 1)
    common = (f"-var Tprep {t_prep} -var Tmelt {t_melt} -var seed {SETTINGS['seed']}")
    jobs = []
    if symbol not in DROPPED:
        for t in temps:
            jid = f"{symbol}-coexist-T{t:g}"
            jobs.append({"id": jid, "kind": "coexistence", "T_K": t, "status": "ready (needs approval to submit)",
                         "command": f"lmp -in ../in.coexist.lmp -log log_{jid}.lammps -var T {t:g} {common}",
                         "outputs": [f"pe_T{t:g}.dat", f"log_{jid}.lammps"]})
        for phase in ("solid", "liquid"):
            jid = f"{symbol}-latent-{phase}"
            jobs.append({"id": jid, "kind": "latent", "phase": phase, "T_K": "<Tm_MEAM>",
                         "status": "BLOCKED until this potential's own melting point is known from the coexistence "
                                   "jobs (latent heat is defined at the model's Tm, not the experimental one)",
                         "command": f"lmp -in ../in.latent.lmp -log log_{jid}.lammps -var phase {phase} "
                                    f"-var T <Tm_MEAM> -var seed {SETTINGS['seed']}",
                         "outputs": [f"h_{phase}_T<Tm_MEAM>.dat", f"log_{jid}.lammps"]})
    phase_name = ELEMENT_CRYSTALS[symbol][0]
    manifest = {
        "element": symbol,
        "purpose": "Independent classical-MD check (MEAM) of the GB10's MACE melting point and latent heat.",
        "status": (f"DROPPED: {DROPPED[symbol]}" if symbol in DROPPED else
                   "inputs only; NOT submitted. Ask Madhan before submitting any Nebius job."),
        "engine": "LAMMPS (needs the MEAM package: check `lmp -h | grep -i meam` on the Nebius image)",
        "potential": {k: info[k] for k in ("citation", "doi", "nist_entry", "nist_implementation", "pair_coeff")}
                     | {"nist_url": f"{NIST}{info['nist_entry']}/",
                        "files": {f: sha256(pot / f) for f in info["files"]},
                        "caveats": info["caveats"]},
        "structure": {"phase": phase_name, "source": "coolorbit/sim/alloys.py ELEMENT_CRYSTALS (Donohue, The "
                      "Structures of the Elements, 1974; alpha-Ga: Sharma & Donohue 1962). Starting lattice "
                      "constants; NPT relaxes them.",
                      "orthogonal_unit_cell_atoms": len(unit),
                      "coexist": {"file": "data.coexist.lmp", "reps": list(coexist_reps), "n_atoms": len(coexist),
                                  "box_A": [round(float(x), 3) for x in coexist.cell.lengths()]},
                      "solid": {"file": "data.solid.lmp", "reps": list(solid_reps), "n_atoms": len(solid),
                                "box_A": [round(float(x), 3) for x in solid.cell.lengths()]}},
        "melting_point_guess": {"value_K": tg, "source": tg_source,
                                "use": "ONLY to place the temperature bracket. Not a reference value: the "
                                       "calibration references live in data/calibration/ (Teammate 1)."},
        "settings": SETTINGS | {"bracket_factors": BRACKET_FACTORS, "T_prep_K": t_prep, "T_melt_K": t_melt},
        "jobs": jobs,
        "atomic_mass_amu": round(float(coexist.get_masses()[0]), 4),
        "method": {"engine": "LAMMPS", "model": f"2NN MEAM, NIST IPR {info['nist_implementation']}",
                   "nist_implementation": info["nist_implementation"], "doi": info["doi"],
                   "timestep_fs": SETTINGS["dt_ps"] * 1000, "seed": SETTINGS["seed"],
                   "coexistence": {"protocol": "two-phase coexistence", "ensemble": "NPT (Nose-Hoover, aniso)",
                                   "n_atoms": len(coexist), "sim_length_ps": SETTINGS["prod_steps"] * SETTINGS["dt_ps"]},
                   "latent": {"protocol": "solid vs liquid enthalpy at Tm",
                              "ensemble": "NPT (Nose-Hoover; solid aniso, liquid iso)", "n_atoms": len(solid),
                              "sim_length_ps": SETTINGS["prod_steps"] * SETTINGS["dt_ps"]}},
        "analysis": {"coexistence": "python ../analyze.py coexist pe_T*.dat",
                     "latent": f"python ../analyze.py latent h_solid_T<Tm_MEAM>.dat h_liquid_T<Tm_MEAM>.dat "
                               f"--log-solid log_{symbol}-latent-solid.lammps --log-liquid log_{symbol}-latent-liquid.lammps"},
        "if_bracket_missed": "all melting -> shift the bracket down by 20%; all freezing -> up by 20% (same rule "
                             "as the GB10 Simulator), then refine around the crossover with 4 temperatures 2% apart.",
    }
    caveats = "".join(f"- {c}\n" for c in info["caveats"]) or "- none noted\n"
    (pot / "SOURCES.md").write_text(
        f"# {symbol} potential: 2NN MEAM (LAMMPS pair_style meam)\n\n"
        f"Downloaded unchanged from the NIST Interatomic Potentials Repository, implementation "
        f"`{info['nist_implementation']}`:\n{NIST}{info['nist_entry']}/\n\n"
        f"**Cite:** {info['citation']} DOI: [{info['doi']}](https://doi.org/{info['doi']})\n\n"
        f"**LAMMPS:** `pair_style meam` + `{info['pair_coeff']}`\n\n"
        "**SHA-256:**\n" + "".join(f"- `{f}`: `{sha256(pot / f)}`\n" for f in info["files"]) +
        f"\n**Caveats:**\n{caveats}", encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    for el in ELEMENTS:
        m = build(el)
        s = m["structure"]
        print(f"{el}: coexist {s['coexist']['n_atoms']} atoms {s['coexist']['box_A']} A, solid {s['solid']['n_atoms']} "
              f"atoms; bracket {[j['T_K'] for j in m['jobs'] if j['kind'] == 'coexistence']} K")


if __name__ == "__main__":
    main()
