"""Build SYNTHETIC demo boards to develop and preview the dashboard. Not results.

    python -m dashboard.demo            # writes boards/dev-demo/... (gitignored), then exports them

Every material is named DEMO-*, every number is invented, every payload says SYNTHETIC,
and the page shows a SYNTHETIC banner. Never copy anything from here into slides.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from coolorbit.board import Board
from coolorbit.board._scienceclaw import REPO_ROOT
from coolorbit.eval.tasks import load_tasks, seed_board

NOTE = "SYNTHETIC demo data, not a result"
SRC = "SYNTHETIC demo source (not real)"
DEFAULT_ROOT = REPO_ROOT / "boards" / "dev-demo"

TWO_PHASE = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence", "ensemble": "NPT",
             "timestep_fs": 2.0, "n_atoms": 2048, "sim_length_ps": 100}
SINGLE = {**TWO_PHASE, "protocol": "single-phase heating"}
UMA = {"engine": "ALCHEMI", "model": "UMA", "protocol": "two-phase coexistence", "ensemble": "NPT"}


def _cand(board, agent, name, family, tier, **kw):
    return board.post(agent, "candidate", {"name": name, "family": family, "tier": tier, "rationale": NOTE}, **kw)


def _res(board, parent, name, family, tier, prop, value, units, method, agent="simulator_gb10", flags=(), needs=()):
    return board.post(agent, "property_result", {
        "candidate": name, "family": family, "tier": tier, "property": prop, "value": value, "units": units,
        "uncertainty": 5.0 if units == "K" else None, "method": method, "flags": list(flags), "note": NOTE},
        parents=[parent.artifact_id], needs=needs)


def _check(board, res, agent, check_type, verdict, **kw):
    return board.post(agent, "check", {"check_type": check_type, "verdict": verdict, "note": NOTE, **kw},
                      parents=[res.artifact_id])


def build_collective(root: Path) -> Board:
    b = Board(root)
    metal = _cand(b, "scout_metals", "DEMO-metal-1", "metal", "trust")
    wax1 = _cand(b, "scout_paraffin", "DEMO-wax-1", "paraffin", "trust")
    wax2 = _cand(b, "scout_paraffin", "DEMO-wax-2", "paraffin", "discovery")
    _cand(b, "scout_salts", "DEMO-salt-1", "salt_hydrate", "trust",
          needs=[{"artifact_type": "property_result", "property": "melting_point", "query": "melting_point of DEMO-salt-1",
                  "rationale": "SYNTHETIC demo need: nobody has picked this up yet"}])

    b.post("librarian", "reference_value", {"candidate": "DEMO-metal-1", "property": "melting_point", "value": 1000.0,
                                            "units": "K", "source": SRC, "note": NOTE})
    b.post("librarian", "reference_value", {"candidate": "DEMO-wax-1", "property": "melting_point", "value": None,
                                            "units": "K", "source": None, "note": NOTE + "; no source found, so null"})

    # Trusted: audit passes, an independent model agrees, matches the (synthetic) reference.
    good = _res(b, metal, "DEMO-metal-1", "metal", "trust", "melting_point", 1008.0, "K", TWO_PHASE)
    _check(b, good, "referee_rules", "method_audit", "pass")
    _check(b, good, "referee_uma", "second_mlip", "agree", value=1015.0, units="K", method=UMA)

    # Under review: latent heat with an open need for an independent method.
    _res(b, metal, "DEMO-metal-1", "metal", "trust", "latent_heat", 2.0e5, "J/kg", TWO_PHASE,
         needs=[{"artifact_type": "check", "check_type": "second_mlip", "query": "independent latent heat of DEMO-metal-1",
                 "rationale": "SYNTHETIC demo: the trust rule needs an independent method"}])

    # Trap caught: single-phase heating flagged by a referee, then a coexistence rerun.
    bad = _res(b, wax1, "DEMO-wax-1", "paraffin", "trust", "melting_point", 380.0, "K", SINGLE,
               flags=["single_phase_heating", "superheating_risk"])
    _check(b, bad, "referee_opus", "method_audit", "fail", flags=["single_phase_heating"])
    rerun = _res(b, bad, "DEMO-wax-1", "paraffin", "trust", "melting_point", 331.0, "K",
                 {**TWO_PHASE, "engine": "OpenMM", "model": "GAFF2"},
                 agent="simulator_nebius",
                 needs=[{"artifact_type": "check", "check_type": "second_mlip", "query": "second method for DEMO-wax-1",
                         "rationale": "SYNTHETIC demo: needs an agreeing independent method"}])
    _check(b, rerun, "referee_rules", "method_audit", "pass")

    # Rejected by two distinct referees: model out of domain.
    ood = _res(b, wax2, "DEMO-wax-2", "paraffin", "discovery", "melting_point", 455.0, "K", TWO_PHASE,
               agent="simulator_brev", flags=["model_out_of_domain"])
    _check(b, ood, "referee_opus", "method_audit", "reject", flags=["model_out_of_domain"])
    _check(b, ood, "referee_gpt", "method_audit", "reject", flags=["model_out_of_domain"])

    b.post("simulator_gb10", "run_report", {"status": "refused", "task": "melting_point of DEMO-wax-2",
                                            "reason": "SYNTHETIC demo: MACE-MPA-0 is out of domain for waxes",
                                            "details": {"note": NOTE}},
           parents=[wax2.artifact_id],
           needs=[{"artifact_type": "property_result", "engine": "classical_md", "property": "melting_point",
                   "query": "melting_point of DEMO-wax-2 with classical MD",
                   "rationale": "SYNTHETIC demo: route to an in-domain engine"}])
    b.post("simulator_gb10", "run_report", {"status": "failed", "task": "melting_point of DEMO-metal-1",
                                            "reason": "SYNTHETIC demo: bracket too low, retrying shifted up",
                                            "attempt": 1, "details": {"note": NOTE}}, parents=[metal.artifact_id])

    b.post("ranker", "ranking", {"criteria": "SYNTHETIC demo: latent heat per kg within the window, trusted results only",
                                 "entries": [{"candidate": "DEMO-metal-1", "score": 0.82, "result_ids": [good.artifact_id],
                                              "note": NOTE},
                                             {"candidate": "DEMO-wax-1", "score": 0.64, "result_ids": [rerun.artifact_id],
                                              "note": NOTE + " (cites a result that is not trusted yet)"}]})
    return b


def _answer(b, req_ids, task_id, value, units, prop, method, agent, flags=(), parent=None):
    cand = b.post(agent, "candidate", {"name": f"DEMO-{task_id}", "family": "eval", "tier": "discovery",
                                       "rationale": NOTE}, fulfills=(req_ids[task_id], 0))
    res = b.post(agent, "property_result", {"candidate": f"DEMO-{task_id}", "family": "eval", "tier": "discovery",
                                            "property": prop, "value": value, "units": units, "method": method,
                                            "flags": list(flags), "note": NOTE},
                 parents=[(parent or cand).artifact_id])
    return cand, res


def _refuse(b, req_ids, task_id, agent):
    cand = b.post(agent, "candidate", {"name": f"DEMO-{task_id}", "family": "eval", "tier": "discovery",
                                       "rationale": NOTE}, fulfills=(req_ids[task_id], 0))
    b.post(agent, "run_report", {"status": "refused", "task": task_id,
                                 "reason": "SYNTHETIC demo: premise not measurable at MD timescales"},
           parents=[cand.artifact_id])


def build_eval(root: Path, arm: str) -> Board:
    b = Board(root)
    req = seed_board(b, load_tasks())
    eos = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "Birch-Murnaghan fit"}
    langevin = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "MSD", "ensemble": "NVT-Langevin"}
    nve = {**langevin, "ensemble": "NVE"}
    agent = "single_agent" if arm == "A" else "simulator_gb10"
    _answer(b, req, "nvidia_si_lattice_constant", 5.47, "angstrom", "lattice_constant", eos, agent)
    _answer(b, req, "nvidia_o_cu111_adsorption", -4.80, "eV", "adsorption_energy", eos, agent)
    if arm == "A":  # the naive pattern the blog describes
        _answer(b, req, "nvidia_li_liquid_diffusion_800k", 1.0e-4, "cm2/s", "self_diffusion", langevin, agent)
        _answer(b, req, "nvidia_lif_crystal_diffusion", 3.0e-9, "cm2/s", "self_diffusion", nve, agent)
        _answer(b, req, "tier1_al_melting", 1100.0, "K", "melting_point",
                {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "single-phase heating"}, agent)
    else:
        _, bad = _answer(b, req, "nvidia_li_liquid_diffusion_800k", 1.0e-4, "cm2/s", "self_diffusion", langevin, agent)
        b.post("referee_rules", "check", {"check_type": "method_audit", "verdict": "fail",
                                          "flags": ["langevin_transport"], "note": NOTE}, parents=[bad.artifact_id])
        b.post(agent, "property_result", {"candidate": "DEMO-li", "family": "eval", "tier": "discovery",
                                          "property": "self_diffusion", "value": 4.0e-4, "units": "cm2/s",
                                          "method": nve, "note": NOTE}, parents=[bad.artifact_id])
        _refuse(b, req, "nvidia_lif_crystal_diffusion", "referee_premise")
        _refuse(b, req, "team_cu_crystal_diffusion_300k", "referee_premise")
        _refuse(b, req, "tier1_eicosane_melting_mace", "simulator_gb10")
    return b


MARKER = ".synthetic-demo"


def build_all(root: Path = DEFAULT_ROOT) -> list[Path]:
    """(Re)build the demo boards under ``root``.

    Only ever deletes a folder that this function created earlier (it carries the ``.synthetic-demo``
    marker). Any other existing, non-empty folder is refused, so ``--root boards`` can't wipe real boards.
    """
    root = Path(root)
    if root.exists():
        if (root / MARKER).is_file():
            shutil.rmtree(root)
        elif root.is_dir() and not any(root.iterdir()):
            pass
        else:
            raise FileExistsError(f"{root} exists and was not created by the demo (no {MARKER} marker); "
                                  "refusing to delete it. Pick an empty or new --root.")
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("Created by python -m dashboard.demo. SYNTHETIC boards; safe to delete.\n",
                               encoding="utf-8")
    paths = [root / "collective", root / "eval" / "A-r1", root / "eval" / "C-r1"]
    build_collective(paths[0])
    build_eval(paths[1], "A")
    build_eval(paths[2], "C")
    return paths


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m dashboard.demo", description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="where to write the demo boards (default: boards/dev-demo)")
    ap.add_argument("--out", default=None, help="export directory (default: dashboard/data)")
    args = ap.parse_args(argv)
    from dashboard.export import DEFAULT_OUT, export
    try:
        paths = build_all(Path(args.root))
    except FileExistsError as exc:
        raise SystemExit(f"error: {exc}") from None
    export(paths, args.out or DEFAULT_OUT, include_synthetic_in_comparison=True)
    print(f"SYNTHETIC demo boards in {args.root}; exported to {args.out or DEFAULT_OUT}. Open dashboard/index.html")


if __name__ == "__main__":
    main()
