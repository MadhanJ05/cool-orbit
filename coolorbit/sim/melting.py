"""Melting point and latent heat with ALCHEMI + MACE.

Three modes, all batched over temperatures in a single GPU job:

coexistence  (the correct melting-point method) A box that is half crystal, half liquid
             is held at each trial temperature (NPT, anisotropic barostat so the box
             length along the interface normal can relax). Above the melting point the
             liquid grows and the potential energy rises; below it the crystal grows and
             it falls. The melting point is where it does neither.
heating      (WRONG ON PURPOSE: the superheating baseline) Perfect crystals held at
             increasing temperatures; the "melting point" is read off the jump in
             potential energy. Perfect crystals superheat, so this overshoots. Results
             are labeled protocol="single-phase heating" with a superheating flag.
latent       Solid and liquid held side by side (separate systems) at one temperature;
             latent heat = enthalpy difference, converted to J/kg.

Examples (GB10, quick settings that test the code path, not production physics):

    python -m coolorbit.sim.melting coexistence --element Cu --a 3.61 --reps 4 4 8 \
        --temps 1200 1300 1400 1500 --quick
    python -m coolorbit.sim.melting heating --element Cu --a 3.61 --reps 4 4 4 \
        --temps 1300 1450 1600 1750 1900 --quick
    python -m coolorbit.sim.melting latent --element Cu --a 3.61 --reps 4 4 4 \
        --temperature 1350 --quick

Coexistence protocol 2 (opt-in; the fix in docs/results/coexistence-diagnosis.md): add ``--protocol 2``
(optionally ``--t-ref <material T, K>``, ``--t-melt``, ``--vc-relax-steps``, ``--prep-coupling``). The default
(protocol 1) is unchanged. Re-analyse a saved coexistence run on the CPU, without dynamics and without writing
into the run directory:

    python -m coolorbit.sim.melting reanalyze runs/<...>_coexistence [--protocol 2] [--out new_file.json]

Every run writes runs/melting/<stamp>_<label>_<mode>/ with settings.json,
timeseries.csv, result.json and final structures (extxyz). With --board/--agent/
--candidate-id the result is also posted to the results board.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import torch
from ase import Atoms
from ase.build import bulk
from ase.io import read as ase_read
from ase.io import write as ase_write

from nvalchemi._typing import AtomCategory
from nvalchemi.data import AtomicData, Batch
from nvalchemi.dynamics import NPT, NVTLangevin
from nvalchemi.dynamics.base import DynamicsStage
from nvalchemi.dynamics.hooks import FreezeAtomsHook, NaNDetectorHook
from nvalchemi.dynamics.hooks._utils import kinetic_energy_per_graph
from nvalchemi.hooks import NeighborListHook, WrapPeriodicHook

from coolorbit.hooks import SuperheatingDetectorHook
from coolorbit.sim.alloys import (composition_label, element_crystal, mean_atomic_mass, mixed_alloy_box,
                                  parse_composition)

KB_EV = 8.617333262e-5  # eV/K
EV_TO_J = 1.602176634e-19
N_A = 6.02214076e23
AMU_PER_A3_TO_G_PER_CM3 = 1.66053906660
GB10_ATOM_STEPS_PER_S = 20_000  # measured: MACE-MPA-0 + cuEquivariance, float32 (runs/smoke)
REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- setup


@dataclass
class Settings:
    mode: str
    label: str
    model: str
    dt_fs: float
    temps: list[float]
    t_prep: float
    t_melt: float
    equil_steps: int
    melt_steps: int
    quench_steps: int
    prod_steps: int
    record_every: int
    friction_per_fs: float
    thermostat_fs: float
    barostat_fs: float
    pe_threshold: float
    jump_threshold: float
    seed: int
    liquid_expansion: float = 0.05
    composition: str | None = None
    solid_atoms: int = 300


MELT_TARGET_FRACTION = 0.8  # share of the melting half that must have moved > 1.5 A
MAX_MELT_ROUNDS = 3
MELT_ESCALATION = 1.25  # each extra melt round is this much hotter...
MAX_MELT_FACTOR = 3.0   # ...up to this multiple of the reference temperature

# ---- coexistence protocol 2 (opt-in: --protocol 2). See docs/results/coexistence-diagnosis.md, "Minimal fix".
PROTOCOL_V2 = 2
REF_WINDOW_PS = (0.5, 1.5)  # reference PE: after the velocity re-draw at the start of production has settled
END_BLOCKS = 10             # end PE = mean of the last 1/END_BLOCKS of production (protocol 1 used the same block)
# Structural cross-check from the production SuperheatingDetectorHook's max_mobile_fraction (share of atoms that
# moved > 0.5 nearest-neighbour distances; independent of its unreliable start-state label). In a box that stays
# half liquid, the liquid half moves, so the fraction sits near 0.5. Observed (diagnosis): boxes that melted
# completely 0.94-1.0 (Bi, In, Cu a2 1400/1425 K); boxes whose liquid froze completely 0.17-0.29 (Cu a1, Cu a2
# 1350/1375 K, In a2 238-323 K).
MOBILE_MELTED = 0.9   # at or above: the whole box melted
MOBILE_FROZEN = 0.35  # below: the liquid half froze, if the PE did not rise (a slow liquid in a short run moves little)
PREP_CRYSTAL_LOST_MOBILE = 0.5  # = the hook's melted_min_mobile: half the prepared "crystal" moved > 0.5 nn
MELT_REF_FACTOR = 2.0  # protocol 2 melts at >= 2 x the material's reference temperature (Bi, Sn needed ~2.0 x Tm)
VC_RELAX_DEFAULT_STEPS = 300
VC_RELAX_DT = 0.05     # FIRE2 initial adaptive timestep (toolkit defaults: tmin 0.005, tmax 0.08)
VC_RELAX_MAX_VOLUME_CHANGE = 0.15  # a 0 K variable-cell relaxation that changes the volume more is rejected
V2_ONLY_ARGS = ("protocol", "t_ref", "vc_relax_steps", "prep_coupling")
GATE_REASONS = {
    "crystal_lost_in_prep": "the crystal did not survive the solid preparation (half or more of its atoms moved > 0.5 "
                            "nearest-neighbour distances), so the box would never hold a crystal half; stopped "
                            "before melting and production",
    "liquid_not_prepared": f"the liquid half was not melted (< {MELT_TARGET_FRACTION:.0%} of its atoms moved > 1.5 A "
                           "after all melt rounds), so the box would not be two-phase; stopped before production",
    "no_two_phase_box": "no two-phase box: every system was fully mobile (max_mobile_fraction >= "
                        f"{MOBILE_MELTED}) and none showed the PE rise of a crystal half melting, so the box held no "
                        "crystal during production and the verdicts say nothing about the melting point",
}


@dataclass
class SettingsV2(Settings):
    """Protocol-2 coexistence settings (written to settings.json only for --protocol 2)."""

    protocol_version: int = PROTOCOL_V2
    t_ref: float | None = None
    vc_relax_steps: int = VC_RELAX_DEFAULT_STEPS
    prep_coupling: str = "anisotropic"
    git_commit: str | None = None


def expand_top_half(atoms: Atoms, frozen: np.ndarray, expansion: float) -> Atoms:
    """Stretch the non-frozen half (fractional z >= 0.5) along the third cell vector by `expansion`.

    A liquid is less dense than its crystal; melting it inside a box sized for the crystal
    squeezes it and can leave part of it solid. The frozen half keeps its Cartesian positions;
    the third cell vector grows by expansion/2 so the stretched half fits exactly.
    """
    scaled = atoms.get_scaled_positions(wrap=True)
    cell = np.asarray(atoms.cell)
    pos = scaled @ cell
    top = ~frozen
    pos[top] += ((scaled[top, 2] - 0.5) * expansion)[:, None] * cell[2]
    new_cell = cell.copy()
    new_cell[2] *= 1.0 + expansion / 2.0
    return Atoms(numbers=atoms.numbers, positions=pos, cell=new_cell, pbc=True)


def load_model(name: str):
    from nvalchemi.models.mace import MACEWrapper

    model = MACEWrapper.from_checkpoint(name, device=torch.device("cuda"), dtype=torch.float32, enable_cueq=True)
    model.set_config("active_outputs", {"energy", "forces", "stress"})  # barostat needs stress
    return model


def build_atoms(args) -> Atoms:
    if args.structure_file:
        atoms = ase_read(args.structure_file)
    elif args.structure == "ref":
        atoms = element_crystal(args.element)  # correct crystal for Ga, In, beta-Sn, Bi, Cu, Al
    else:
        atoms = bulk(args.element, args.structure, a=args.a, cubic=True)
    return atoms.repeat(tuple(args.reps))


def solid_reps(atoms: Atoms, target: int, min_length: float = 13.0) -> tuple[int, int, int]:
    """Supercell repeats with every box length >= min_length (above 2x the MACE cutoff) and >= target atoms."""
    lengths = atoms.cell.lengths()
    reps = np.array([max(1, math.ceil(min_length / L)) for L in lengths])
    while len(atoms) * reps.prod() < target:
        reps[np.argmin(lengths * reps)] += 1  # grow the shortest side
    return tuple(int(r) for r in reps)


def domain_flags(atoms: Atoms, dt_fs: float) -> list[str]:
    """Automatic trap flags we can detect from the inputs alone."""
    symbols = set(atoms.get_chemical_symbols())
    flags = []
    if {"C", "H"} <= symbols and symbols <= {"C", "H", "O", "N"}:
        flags.append("model_out_of_domain")  # MACE-MP family is trained on inorganic materials
    if "H" in symbols and dt_fs > 0.5:
        flags.append("timestep_too_large_for_hydrogen")
    return flags


def make_data(atoms: Atoms, temperature: float, seed: int, frozen: np.ndarray | None = None) -> AtomicData:
    """AtomicData with Maxwell-Boltzmann velocities at `temperature`, zero net momentum."""
    n = len(atoms)
    masses = torch.tensor(atoms.get_masses(), dtype=torch.float32)
    gen = torch.Generator().manual_seed(seed)
    v = torch.randn(n, 3, generator=gen) * torch.sqrt(KB_EV * temperature / masses).unsqueeze(-1)
    v -= (masses.unsqueeze(-1) * v).sum(0) / masses.sum()
    data = AtomicData(
        positions=torch.tensor(atoms.positions, dtype=torch.float32),
        atomic_numbers=torch.tensor(atoms.numbers, dtype=torch.long),
        forces=torch.zeros(n, 3),
        energy=torch.zeros(1, 1),
        cell=torch.tensor(np.asarray(atoms.cell), dtype=torch.float32).unsqueeze(0),
        pbc=torch.tensor([[True, True, True]]),
    )
    data["stress"] = torch.zeros(1, 3, 3)
    data.add_node_property("velocities", v)
    cats = np.where(frozen, AtomCategory.SPECIAL.value, AtomCategory.GAS.value) if frozen is not None \
        else np.full(n, AtomCategory.GAS.value)
    data.atom_categories = torch.tensor(cats, dtype=torch.long)
    return data


def batch_to_atoms(batch: Batch) -> list[Atoms]:
    out = []
    for d in batch.to_data_list():
        out.append(Atoms(numbers=d.atomic_numbers.cpu().numpy(), positions=d.positions.detach().cpu().numpy(),
                         cell=d.cell.squeeze(0).detach().cpu().numpy(), pbc=True))
    return out


class Recorder:
    """Per-system potential energy, temperature, volume and box height every `every` steps."""

    stage = DynamicsStage.AFTER_STEP

    def __init__(self, stage_name: str, every: int, targets: list[float]) -> None:
        self.frequency = every
        self.stage_name = stage_name
        self.targets = targets
        self.rows: list[dict] = []

    def __call__(self, ctx, stage) -> None:
        b = ctx.batch
        n = b.num_nodes_per_graph.to(torch.float32)
        ke = kinetic_energy_per_graph(velocities=b.velocities, masses=b.atomic_masses,
                                      batch_idx=b.batch_idx, num_graphs=b.num_graphs).squeeze(-1)
        pe = b.energy.squeeze(-1) / n
        temp = 2.0 * ke / (3.0 * n * KB_EV)
        vol = torch.linalg.det(b.cell).abs() / n
        lz = torch.linalg.norm(b.cell[:, 2, :], dim=-1)
        for i, (p, t, v, z) in enumerate(zip(pe.tolist(), temp.tolist(), vol.tolist(), lz.tolist())):
            self.rows.append({"stage": self.stage_name, "step": ctx.step_count, "system": i,
                              "T_target": self.targets[i], "pe_per_atom_eV": p, "T_K": t,
                              "volume_per_atom_A3": v, "Lz_A": z})


def run_stage(dyn, batch: Batch, model, recorder: Recorder, freeze: bool = False, extra_hooks=()) -> Batch:
    dyn.register_hook(NeighborListHook(model.model_config.neighbor_config, stage=DynamicsStage.BEFORE_COMPUTE))
    if freeze:
        dyn.register_hook(FreezeAtomsHook())  # before wrapping, per FreezeAtomsHook docs
    dyn.register_hook(WrapPeriodicHook(stage=DynamicsStage.AFTER_POST_UPDATE))
    dyn.register_hook(NaNDetectorHook(frequency=100))
    dyn.register_hook(recorder)
    for hook in extra_hooks:
        dyn.register_hook(hook)
    t0 = time.perf_counter()
    batch = dyn.run(batch)
    torch.cuda.synchronize()
    steps = getattr(dyn, "step_count", "?")
    print(f"  [{recorder.stage_name}] {steps} steps x {batch.num_nodes} atoms in {time.perf_counter() - t0:.0f} s",
          flush=True)
    return batch


def melt_until_liquid(batch: Batch, model, s: Settings, rows: list, stage: str, mask: torch.Tensor,
                      freeze: bool, t_ref: float) -> tuple[Batch, float, list[float]]:
    """Melt a single-system batch in NVT rounds until >= MELT_TARGET_FRACTION of ``mask`` atoms moved > 1.5 A.

    Each extra round is hotter (x MELT_ESCALATION, capped at MAX_MELT_FACTOR x t_ref): a perfect crystal can
    superheat far beyond 1.6 x T_m (Teammate 2 saw Ga, In and Bi stay solid at 1.6 x T_m with MEAM), so
    repeating the same temperature may never melt it. Returns (batch, melted fraction, temperatures used).
    """
    pos0 = batch.positions.detach().clone()
    temps_used, melted = [], 0.0
    for r in range(MAX_MELT_ROUNDS):
        t = min(s.t_melt * MELT_ESCALATION ** r, MAX_MELT_FACTOR * t_ref)
        steps = s.melt_steps if r == 0 else max(s.melt_steps // 2, 1)
        if r:
            print(f"  only {melted:.0%} melted; melting {steps} more steps at {t:.0f} K", flush=True)
        rec = Recorder(f"{stage}_r{r + 1}", s.record_every, [t] * batch.num_graphs)
        batch = run_stage(nvt(model, s, t, steps, s.seed + 2 + r), batch, model, rec, freeze=freeze)
        rows += rec.rows
        temps_used.append(round(t, 1))
        melted = displaced_fraction(pos0, batch.positions, batch.cell[0], mask)
        if melted >= MELT_TARGET_FRACTION:
            break
    return batch, melted, temps_used


def npt(model, s: Settings, temps: list[float], steps: int, coupling: str):
    m = len(temps)
    pressure = torch.zeros(m, 3) if coupling == "anisotropic" else 0.0
    return NPT(model=model, dt=s.dt_fs, temperature=torch.tensor(temps), pressure=pressure,
               barostat_time=s.barostat_fs, thermostat_time=s.thermostat_fs,
               pressure_coupling=coupling, n_steps=steps)


def nvt(model, s: Settings, temp: float, steps: int, seed: int):
    return NVTLangevin(model=model, dt=s.dt_fs, temperature=temp, friction=s.friction_per_fs,
                       random_seed=seed, n_steps=steps)


def displaced_fraction(pos0: torch.Tensor, pos1: torch.Tensor, cell: torch.Tensor, mask: torch.Tensor,
                       threshold_a: float = 1.5) -> float:
    """Fraction of masked atoms that moved more than `threshold_a` (minimum image, fixed cell)."""
    frac = (pos1 - pos0) @ torch.linalg.inv(cell)
    frac = frac - torch.round(frac)
    disp = (frac @ cell).norm(dim=-1)
    return float((disp[mask] > threshold_a).float().mean())


def block_means(values: np.ndarray, n_blocks: int = 5) -> np.ndarray:
    return np.array([b.mean() for b in np.array_split(values, n_blocks)])


def production_series(rows: list[dict], stage: str, system: int, key: str, discard: float = 0.2) -> np.ndarray:
    vals = np.array([r[key] for r in rows if r["stage"] == stage and r["system"] == system])
    return vals[int(len(vals) * discard):]


# --------------------------------------------------------------------------- modes


def prepare_coexistence(atoms: Atoms, model, s: Settings, rows: list) -> tuple[Batch, dict]:
    """Equilibrate the crystal, then melt the upper half (z) with the lower half frozen."""
    print(f"Preparing two-phase box: {len(atoms)} atoms, T_prep={s.t_prep:g} K, T_melt={s.t_melt:g} K", flush=True)
    rec = Recorder("prep_solid_npt", s.record_every, [s.t_prep])
    batch = Batch.from_data_list([make_data(atoms, s.t_prep, s.seed)], device="cuda")
    batch = run_stage(npt(model, s, [s.t_prep], s.equil_steps, "isotropic"), batch, model, rec)
    rows += rec.rows

    solid = batch_to_atoms(batch)[0]
    frozen = solid.get_scaled_positions(wrap=True)[:, 2] < 0.5
    box = expand_top_half(solid, frozen, s.liquid_expansion)
    batch = Batch.from_data_list([make_data(box, s.t_melt, s.seed + 1, frozen)], device="cuda")
    pos0 = batch.positions.detach().clone()
    free = torch.tensor(~frozen, device=batch.positions.device)
    batch, _, melt_temps = melt_until_liquid(batch, model, s, rows, "prep_melt_nvt", free, True, s.t_prep)

    rec = Recorder("prep_quench_nvt", s.record_every, [s.t_prep])
    batch = run_stage(nvt(model, s, s.t_prep, s.quench_steps, s.seed + 9), batch, model, rec, freeze=True)
    rows += rec.rows
    cell = batch.cell[0]
    prep = {
        "liquid_expansion": s.liquid_expansion,
        "melt_rounds": len(melt_temps),
        "melt_temperatures_K": melt_temps,
        "frozen_half_moved_fraction": displaced_fraction(pos0, batch.positions, cell, ~free),
        "melted_half_moved_fraction": displaced_fraction(pos0, batch.positions, cell, free),
    }
    print(f"  liquid half: {prep['melted_half_moved_fraction']:.0%} of atoms moved > 1.5 A; "
          f"crystal half: {prep['frozen_half_moved_fraction']:.0%}", flush=True)
    return batch, prep


def run_coexistence(atoms: Atoms, model, s: Settings, rows: list) -> tuple[dict, Batch]:
    prepared, prep = prepare_coexistence(atoms, model, s, rows)
    box = batch_to_atoms(prepared)[0]
    data = [make_data(box, t, s.seed + 10 + i) for i, t in enumerate(s.temps)]
    batch = Batch.from_data_list(data, device="cuda")
    rec = Recorder("production_npt", s.record_every, s.temps)
    detector = SuperheatingDetectorHook(action="record")  # negative control: must stay silent here
    batch = run_stage(npt(model, s, s.temps, s.prod_steps, "anisotropic"), batch, model, rec,
                      extra_hooks=[detector])
    rows += rec.rows

    per_t = []
    for i, t in enumerate(s.temps):
        # Skip only the barostat settling time: with little liquid in the box, the whole phase change can be
        # over within the first fifth of the run (seen in the first production Cu run), so a 20% cut hides it.
        pe = production_series(rows, "production_npt", i, "pe_per_atom_eV", discard=0.05)
        means = block_means(pe, n_blocks=10)
        delta = float(means[-1] - means[0])
        noise = float(np.std(np.diff(means)) / math.sqrt(2)) if len(means) > 2 else 0.0
        threshold = max(s.pe_threshold, 3.0 * noise)  # a verdict must beat the run's own noise
        verdict = "melting" if delta > threshold else "freezing" if delta < -threshold else "stable"
        t_meas = float(production_series(rows, "production_npt", i, "T_K").mean())
        per_t.append({"T_target_K": t, "T_measured_K": round(t_meas, 1), "delta_pe_eV_per_atom": delta,
                      "block_noise_eV_per_atom": noise, "threshold_eV_per_atom": threshold, "verdict": verdict})
        print(f"  T={t:7.1f} K (measured {t_meas:7.1f}): dPE = {delta:+.4f} eV/atom -> {verdict}", flush=True)

    result = {"mode": "coexistence", "prep": prep, "per_temperature": per_t, "flags": []}
    if prep["melted_half_moved_fraction"] < MELT_TARGET_FRACTION:
        result["flags"].append("liquid_half_incompletely_melted")
        print("WARNING: the liquid half was not fully melted before production "
              "(increase --melt-steps or --t-melt); verdicts near the melting point are unreliable.")
    if prep["frozen_half_moved_fraction"] > 0.05:
        result["flags"].append("crystal_half_disturbed")
    result["superheating_detector"] = detector.report()
    if "superheating_observed" in detector.flags():
        result["flags"].append("unexpected_superheating_signal")  # should never fire on a two-phase box
    result.update(bracket_verdict(per_t))
    return result, batch


def bracket_verdict(per_t: list[dict], protocol: int = 1) -> dict:
    """Melting point (or the failure) from per-temperature verdicts (melting / freezing / stable).

    "Above the bracket" needs the hottest run to have decisively FROZEN, "below" the coldest to have decisively
    MELTED. When the end of the bracket is only "stable" (the interface barely moved: near the melting point the
    driving force is small), the answer is "undecided": the known bounds are reported and the fix is a longer run
    at the same temperatures, not a shifted bracket (Cu, 2026-10-31: 1350-1425 K all stable after 1275-1300 K froze).

    ``protocol=2`` (opt-in) first requires the verdicts, ordered by temperature, to be monotone (see
    ``bracket_verdict_v2``); the default is unchanged.
    """
    if protocol == PROTOCOL_V2:
        return bracket_verdict_v2(per_t)
    melt_t = [p["T_target_K"] for p in per_t if p["verdict"] == "melting"]
    freeze_t = [p["T_target_K"] for p in per_t if p["verdict"] == "freezing"]
    hottest = max(per_t, key=lambda p: p["T_target_K"])
    coldest = min(per_t, key=lambda p: p["T_target_K"])
    bounds = {**({"lower_bound_K": max(freeze_t)} if freeze_t else {}),
              **({"upper_bound_K": min(melt_t)} if melt_t else {})}
    if not melt_t and hottest["verdict"] == "freezing":
        return {"status": "failed", "failure": "above_bracket", **bounds,
                "reason": "no temperature melted and the hottest run froze: the melting point is above the bracket"}
    if not freeze_t and coldest["verdict"] == "melting":
        return {"status": "failed", "failure": "below_bracket", **bounds,
                "reason": "no temperature froze and the coldest run melted: the melting point is below the bracket"}
    if not melt_t or not freeze_t:
        end = "hottest" if not melt_t else "coldest"
        return {"status": "failed", "failure": "undecided", **bounds,
                "reason": f"undecided: the interface barely moved at the {end} temperatures, so the melting point is "
                          "near them; rerun the same temperatures longer"}
    if max(freeze_t) >= min(melt_t):
        return {"status": "failed", "failure": "inconsistent", **bounds,
                "reason": "inconsistent: froze at or above a melting temperature (runs too short or too noisy)"}
    lo, hi = max(freeze_t), min(melt_t)
    pts = [(p["T_target_K"], p["delta_pe_eV_per_atom"]) for p in per_t if lo <= p["T_target_K"] <= hi]
    slope, icept = np.polyfit([p[0] for p in pts], [p[1] for p in pts], 1)
    tm = float(np.clip(-icept / slope, lo, hi)) if slope > 0 else (lo + hi) / 2
    return {"status": "ok", "value": round(tm, 1), "uncertainty": round((hi - lo) / 2, 1), "units": "K",
            "property": "melting_point", "bracket_K": [lo, hi]}


def run_heating(atoms: Atoms, model, s: Settings, rows: list) -> tuple[dict, Batch]:
    print("NOTE: single-phase heating of a perfect crystal superheats; this is the deliberately wrong baseline.")
    data = [make_data(atoms, t, s.seed + i) for i, t in enumerate(s.temps)]
    batch = Batch.from_data_list(data, device="cuda")
    rec = Recorder("heating_npt", s.record_every, s.temps)
    detector = SuperheatingDetectorHook(action="warn")  # positive control: should fire on melting crystals
    batch = run_stage(npt(model, s, s.temps, s.prod_steps, "isotropic"), batch, model, rec,
                      extra_hooks=[detector])
    rows += rec.rows

    curve = []
    for i, t in enumerate(s.temps):
        pe = production_series(rows, "heating_npt", i, "pe_per_atom_eV", discard=0.5)
        curve.append({"T_target_K": t, "pe_per_atom_eV": float(pe.mean())})
    curve.sort(key=lambda c: c["T_target_K"])
    result = {"mode": "heating", "curve": curve, "superheating_detector": detector.report(),
              "flags": sorted({"single_phase_heating", "superheating_risk"} | detector.flags())}
    for a, b in zip(curve, curve[1:]):
        # Subtract the ordinary heat-up of a harmonic solid (PE rises by 3/2 kB per K per atom).
        jump = b["pe_per_atom_eV"] - a["pe_per_atom_eV"] - 1.5 * KB_EV * (b["T_target_K"] - a["T_target_K"])
        if jump > s.jump_threshold:
            mid = (a["T_target_K"] + b["T_target_K"]) / 2
            result.update(status="ok", property="melting_point", units="K", value=mid,
                          uncertainty=(b["T_target_K"] - a["T_target_K"]) / 2, jump_eV_per_atom=jump)
            break
    else:
        result.update(status="failed", failure="no_jump",
                      reason=f"no jump > {s.jump_threshold} eV/atom up to {curve[-1]['T_target_K']} K")
    for c in curve:
        print(f"  T={c['T_target_K']:7.1f} K: PE = {c['pe_per_atom_eV']:.4f} eV/atom", flush=True)
    return result, batch


def relax_solid_shapes(solids: list[Atoms], model, s: Settings, rows: list, t: float,
                       stage: str) -> tuple[list[Atoms], list[dict]]:
    """Let each crystal find the model's own axis ratios at T (anisotropic NPT) before the isotropic enthalpy run.

    The reference crystals carry the measured lattice. For non-cubic ones (alpha-Ga, In, beta-Sn, Bi) an isotropic
    barostat keeps the measured axis ratios, so the solid stays strained: its enthalpy is too high and the latent
    heat too low. The joint solid+liquid stage stays isotropic (a liquid's box has no shape to relax).
    """
    batch = Batch.from_data_list([make_data(a, t, s.seed + 7 + i) for i, a in enumerate(solids)], device="cuda")
    rec = Recorder(stage, s.record_every, [t] * len(solids))
    batch = run_stage(npt(model, s, [t] * len(solids), s.equil_steps, "anisotropic"), batch, model, rec)
    rows += rec.rows
    relaxed = batch_to_atoms(batch)
    shapes = [{"cell_lengths_before_A": [round(float(x), 4) for x in a.cell.lengths()],
               "cell_lengths_after_A": [round(float(x), 4) for x in r.cell.lengths()]}
              for a, r in zip(solids, relaxed)]
    return relaxed, shapes


def run_latent(atoms: Atoms, model, s: Settings, rows: list) -> tuple[dict, Batch]:
    t = s.temps[0]
    print(f"Latent heat at T={t:g} K: preparing liquid from T_melt={s.t_melt:g} K", flush=True)
    roomy = atoms.copy()  # the liquid needs more volume than the crystal
    roomy.set_cell(np.asarray(atoms.cell) * (1.0 + s.liquid_expansion) ** (1 / 3), scale_atoms=True)
    liq = Batch.from_data_list([make_data(roomy, s.t_melt, s.seed + 1)], device="cuda")
    everything = torch.ones(liq.num_nodes, dtype=torch.bool, device=liq.positions.device)
    liq, liq_melted, liq_temps = melt_until_liquid(liq, model, s, rows, "prep_liquid_nvt", everything, False, t)

    (solid,), shapes = relax_solid_shapes([atoms], model, s, rows, t, "solid_shape_npt")
    data = [make_data(solid, t, s.seed + 3), make_data(batch_to_atoms(liq)[0], t, s.seed + 4)]
    batch = Batch.from_data_list(data, device="cuda")
    pos0 = batch.positions.detach().clone()
    rec = Recorder("latent_npt", s.record_every, [t, t])
    batch = run_stage(npt(model, s, [t, t], s.prod_steps, "isotropic"), batch, model, rec)
    rows += rec.rows

    # Enthalpy per atom at P = 0: H = PE + KE (+ PV, zero here).
    phases = {}
    sizes = batch.num_nodes_per_graph.tolist()
    offsets = np.cumsum([0] + sizes)
    for i, name in enumerate(("solid", "liquid")):
        pe = production_series(rows, "latent_npt", i, "pe_per_atom_eV")
        temp = production_series(rows, "latent_npt", i, "T_K")
        h = pe + 1.5 * KB_EV * temp
        hb = block_means(h)
        vol = production_series(rows, "latent_npt", i, "volume_per_atom_A3").mean()
        sl = slice(offsets[i], offsets[i + 1])
        cell = batch.cell[i]
        moved = displaced_fraction(pos0[sl], batch.positions[sl], cell, torch.ones(sizes[i], dtype=torch.bool,
                                                                                   device=cell.device))
        phases[name] = {"H_eV_per_atom": float(hb.mean()), "H_sem": float(hb.std(ddof=1) / math.sqrt(len(hb))),
                        "T_measured_K": float(temp.mean()), "volume_per_atom_A3": float(vol),
                        "moved_fraction": moved}
    mass_amu = float(np.mean(atoms.get_masses()))
    density = {k: mass_amu / v["volume_per_atom_A3"] * AMU_PER_A3_TO_G_PER_CM3 for k, v in phases.items()}
    dh = phases["liquid"]["H_eV_per_atom"] - phases["solid"]["H_eV_per_atom"]
    dh_err = math.hypot(phases["liquid"]["H_sem"], phases["solid"]["H_sem"])
    to_j_per_kg = EV_TO_J * N_A / (mass_amu / 1000.0)
    result = {"mode": "latent", "phases": phases, "density_g_cm3": density, "delta_H_eV_per_atom": dh,
              "liquid_prep": {"melted_fraction": liq_melted, "melt_temperatures_K": liq_temps},
              "solid_shape_relaxation": shapes[0]}
    problems = []
    if phases["liquid"]["moved_fraction"] < 0.5:
        problems.append("liquid may have recrystallized or never melted (few atoms diffused)")
    if phases["solid"]["moved_fraction"] > 0.2:
        problems.append("solid may have melted (many atoms diffused)")
    if dh <= 0:
        problems.append("non-positive enthalpy difference")
    if problems:
        result.update(status="failed", failure="phase_check", reason="; ".join(problems))
    else:
        result.update(status="ok", property="latent_heat", units="J/kg", value=round(dh * to_j_per_kg),
                      uncertainty=round(dh_err * to_j_per_kg))
    print(f"  dH = {dh:.4f} +/- {dh_err:.4f} eV/atom = {dh * to_j_per_kg / 1000:.1f} kJ/kg; "
          f"densities solid {density['solid']:.3f}, liquid {density['liquid']:.3f} g/cm3", flush=True)
    return result, batch


def run_alloy_latent(atoms: Atoms, model, s: Settings, rows: list) -> tuple[dict, Batch]:
    """Latent heat of an alloy: H(liquid alloy) - sum_i x_i H(solid element i), all at the alloy's melting T.

    Assumes the alloy's solid is a mixture of the pure elements (true for e.g. Ga-In). Where the real eutectic
    solid contains intermetallics (e.g. BiIn2 in Field's metal), they are more stable than the separate metals, so
    the real latent heat is likely higher and this estimate an underestimate. Flagged in every result.
    """
    fractions = parse_composition(s.composition)
    t = s.temps[0]
    print(f"Alloy latent heat: {composition_label(fractions)} (at%) at T={t:g} K; "
          f"melting the mixed box at {s.t_melt:g} K", flush=True)
    liq = Batch.from_data_list([make_data(atoms, s.t_melt, s.seed + 1)], device="cuda")
    everything = torch.ones(liq.num_nodes, dtype=torch.bool, device=liq.positions.device)
    liq, liq_melted, liq_temps = melt_until_liquid(liq, model, s, rows, "prep_liquid_alloy_nvt", everything,
                                                   False, t)

    elements = sorted(fractions)
    solids = []
    for el in elements:
        cell = element_crystal(el)
        solids.append(cell.repeat(solid_reps(cell, s.solid_atoms)))
    solids, shapes = relax_solid_shapes(solids, model, s, rows, t, "solid_shape_npt")
    systems = [batch_to_atoms(liq)[0]] + solids
    names = ["liquid_alloy"] + [f"solid_{el}" for el in elements]
    data = [make_data(a, t, s.seed + 3 + i) for i, a in enumerate(systems)]
    batch = Batch.from_data_list(data, device="cuda")
    pos0 = batch.positions.detach().clone()
    rec = Recorder("alloy_latent_npt", s.record_every, [t] * len(systems))
    batch = run_stage(npt(model, s, [t] * len(systems), s.prod_steps, "isotropic"), batch, model, rec)
    rows += rec.rows

    sizes = batch.num_nodes_per_graph.tolist()
    offsets = np.cumsum([0] + sizes)
    phases = {}
    for i, name in enumerate(names):
        pe = production_series(rows, "alloy_latent_npt", i, "pe_per_atom_eV")
        temp = production_series(rows, "alloy_latent_npt", i, "T_K")
        hb = block_means(pe + 1.5 * KB_EV * temp)
        sl = slice(offsets[i], offsets[i + 1])
        moved = displaced_fraction(pos0[sl], batch.positions[sl], batch.cell[i],
                                   torch.ones(sizes[i], dtype=torch.bool, device=batch.cell.device))
        phases[name] = {"n_atoms": sizes[i], "H_eV_per_atom": float(hb.mean()),
                        "H_sem": float(hb.std(ddof=1) / math.sqrt(len(hb))), "T_measured_K": float(temp.mean()),
                        "volume_per_atom_A3": float(production_series(rows, "alloy_latent_npt", i,
                                                                      "volume_per_atom_A3").mean()),
                        "moved_fraction": moved}

    h_solid = sum(fractions[el] * phases[f"solid_{el}"]["H_eV_per_atom"] for el in elements)
    dh = phases["liquid_alloy"]["H_eV_per_atom"] - h_solid
    dh_err = math.sqrt(phases["liquid_alloy"]["H_sem"] ** 2
                       + sum((fractions[el] * phases[f"solid_{el}"]["H_sem"]) ** 2 for el in elements))
    mass = mean_atomic_mass(fractions)
    v_solid = sum(fractions[el] * phases[f"solid_{el}"]["volume_per_atom_A3"] for el in elements)
    rho_solid = mass / v_solid * AMU_PER_A3_TO_G_PER_CM3
    rho_liquid = mass / phases["liquid_alloy"]["volume_per_atom_A3"] * AMU_PER_A3_TO_G_PER_CM3
    to_j_per_kg = EV_TO_J * N_A / (mass / 1000.0)
    l_kg = dh * to_j_per_kg
    result = {"mode": "alloy-latent", "composition_at": fractions, "composition_spec": s.composition,
              "phases": phases, "delta_H_eV_per_atom": dh, "density_g_cm3": {"solid_mixture": rho_solid,
                                                                             "liquid": rho_liquid},
              "latent_heat_J_per_L_solid": l_kg * rho_solid,  # (J/kg) x (g/cm3 = kg/L)
              "liquid_prep": {"melted_fraction": liq_melted, "melt_temperatures_K": liq_temps},
              "solid_shape_relaxation": dict(zip(elements, shapes)),
              "flags": ["eutectic_solid_assumed_pure_elements"]}
    problems = []
    if phases["liquid_alloy"]["moved_fraction"] < 0.5:
        problems.append("liquid alloy may have crystallized or never melted (few atoms diffused)")
    for el in elements:
        if phases[f"solid_{el}"]["moved_fraction"] > 0.2:
            problems.append(f"solid {el} may have melted at {t:g} K")
    if dh <= 0:
        problems.append("non-positive enthalpy difference")
    if problems:
        result.update(status="failed", failure="phase_check", reason="; ".join(problems))
    else:
        result.update(status="ok", property="latent_heat", units="J/kg", value=round(l_kg),
                      uncertainty=round(dh_err * to_j_per_kg))
    print(f"  dH = {dh:.4f} +/- {dh_err:.4f} eV/atom = {l_kg / 1000:.1f} kJ/kg = {l_kg * rho_solid / 1e6:.0f} MJ/m3; "
          f"density solid mix {rho_solid:.2f}, liquid {rho_liquid:.2f} g/cm3", flush=True)
    for name, p in phases.items():
        print(f"    {name:<14} {p['n_atoms']:4d} atoms  H = {p['H_eV_per_atom']:.4f} eV/atom  "
              f"moved {p['moved_fraction']:.0%}", flush=True)
    return result, batch


# --------------------------------------------------------------------------- coexistence protocol 2 (opt-in)
#
# What changes against protocol 1 (docs/results/coexistence-diagnosis.md, "Minimal fix"; numbers there):
#  1. per-temperature verdict: PE change from the start of production (0.5-1.5 ps), not from block 0; noise from the
#     detrended last half (without the transition signal); cross-checked with the production hook's
#     max_mobile_fraction; no "unexpected_superheating_signal"; failure "no_two_phase_box" (the Sn signature);
#  2. bracket: "inconsistent" unless the verdicts ordered by temperature are freezing* stable* melting*;
#  3. gates that stop before production: "crystal_lost_in_prep" (hook on the solid preparation) and
#     "liquid_not_prepared" (melted share of the liquid half under MELT_TARGET_FRACTION after all melt rounds);
#  4. melt temperature tied to the material: --t-melt, else max(1.6 x T_prep, 2 x --t-ref);
#  6. solid preparation: optional 0 K FIRE2VariableCell relaxation, then anisotropic NPT (--prep-coupling).
# (5, the retry logic, lives in coolorbit/sim/simulator_agent.py.)


def git_commit(root: Path = REPO_ROOT) -> str | None:
    """HEAD commit read from .git without a subprocess; None if it can't be read. Says nothing about local edits."""
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head or None
        ref = head[len("ref: "):]
        if (git / ref).is_file():
            return (git / ref).read_text().strip() or None
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        return None
    return None


def melt_temperature_v2(t_prep: float, t_ref: float | None, t_melt: float | None) -> float:
    """Protocol-2 melt temperature: an explicit --t-melt wins; else max(1.6 x T_prep, 2 x T_ref).

    T_ref is the material's own temperature (the request's t_guess_K or the measured melting point), so shifting the
    bracket down can no longer lower the melt temperature (In a2: 483-755 K melted 56% where In a1 needed 1075 K).
    Without T_ref this falls back to protocol 1's 1.6 x T_prep (the Simulator then carries it through retries).
    """
    if t_melt:
        return float(t_melt)
    if t_ref:
        return max(1.6 * t_prep, MELT_REF_FACTOR * float(t_ref))
    return 1.6 * t_prep


def melt_cap_reference(s: Settings) -> float:
    """Reference passed to melt_until_liquid (rounds are capped at MAX_MELT_FACTOR x it); never caps below T_melt."""
    return max(s.t_prep, getattr(s, "t_ref", None) or 0.0, s.t_melt / MAX_MELT_FACTOR)


def settings_v2(s: Settings, args) -> SettingsV2:
    """Protocol-1 settings plus the protocol-2 fields (melt temperature per melt_temperature_v2)."""
    base = dict(s.__dict__)
    base["t_melt"] = melt_temperature_v2(s.t_prep, args.t_ref, args.t_melt)
    return SettingsV2(**base, t_ref=args.t_ref,
                      vc_relax_steps=VC_RELAX_DEFAULT_STEPS if args.vc_relax_steps is None else args.vc_relax_steps,
                      prep_coupling=args.prep_coupling or "anisotropic", git_commit=git_commit())


def vc_relax_v2(atoms: Atoms, model, s: SettingsV2, rows: list) -> tuple[Atoms, dict]:
    """0 K variable-cell relaxation (nvalchemi FIRE2VariableCell + AlignCellHook, fixed step count).

    The reference crystals carry the measured lattice; the barostat then started 8-11% off in volume (Bi, Sn). The
    relaxation is rejected (the input kept, the reason recorded) if it changes the volume by more than
    VC_RELAX_MAX_VOLUME_CHANGE, raises the energy, or produces non-finite numbers.
    """
    from nvalchemi.dynamics import FIRE2VariableCell
    from nvalchemi.dynamics.hooks import AlignCellHook

    batch = Batch.from_data_list([make_data(atoms, 0.0, s.seed)], device="cuda")  # zero velocities
    every = max(1, min(s.record_every, s.vc_relax_steps // 10 or 1))
    rec = Recorder("prep_vc_relax_fire2", every, [0.0])
    dyn = FIRE2VariableCell(model=model, dt=VC_RELAX_DT, n_steps=s.vc_relax_steps)
    batch = run_stage(dyn, batch, model, rec, extra_hooks=[AlignCellHook()])
    rows += rec.rows
    relaxed = batch_to_atoms(batch)[0]
    pe = [r["pe_per_atom_eV"] for r in rec.rows]
    v0, v1 = float(atoms.get_volume()), float(relaxed.get_volume())
    info = {"optimizer": "FIRE2VariableCell", "steps": s.vc_relax_steps, "dt": VC_RELAX_DT,
            "cell_lengths_before_A": [round(float(x), 4) for x in atoms.cell.lengths()],
            "cell_lengths_after_A": [round(float(x), 4) for x in relaxed.cell.lengths()],
            "cell_angles_before_deg": [round(float(x), 3) for x in atoms.cell.angles()],
            "cell_angles_after_deg": [round(float(x), 3) for x in relaxed.cell.angles()],
            "volume_change": v1 / v0 - 1.0 if v0 > 0 else None,
            "pe_first_eV_per_atom": pe[0] if pe else None, "pe_last_eV_per_atom": pe[-1] if pe else None}
    problems = []
    if not (np.isfinite(relaxed.positions).all() and np.isfinite(np.asarray(relaxed.cell)).all()):
        problems.append("non-finite positions or cell")
    elif info["volume_change"] is None or abs(info["volume_change"]) > VC_RELAX_MAX_VOLUME_CHANGE:
        problems.append(f"volume changed by {info['volume_change']}, more than {VC_RELAX_MAX_VOLUME_CHANGE:.0%}")
    if pe and not (math.isfinite(pe[-1]) and pe[-1] <= pe[0] + 1e-3):
        problems.append(f"energy did not decrease ({pe[0]:.4f} -> {pe[-1]:.4f} eV/atom)")
    info["rejected"] = "; ".join(problems) or None
    print(f"  0 K variable-cell relaxation: volume {info['volume_change']:+.1%}" if info["volume_change"] is not None
          else "  0 K variable-cell relaxation: volume unknown", flush=True)
    if problems:
        print(f"  WARNING: relaxation rejected ({info['rejected']}); keeping the input cell", flush=True)
        return atoms, info
    return relaxed, info


def prepare_coexistence_v2(atoms: Atoms, model, s: SettingsV2, rows: list) -> tuple[Batch, dict]:
    """Protocol-2 preparation with fail-fast gates. ``prep["gate"]`` names the gate that stopped it, if any."""
    print(f"Preparing two-phase box (protocol 2): {len(atoms)} atoms, T_prep={s.t_prep:g} K, T_melt={s.t_melt:g} K, "
          f"T_ref={s.t_ref}, solid barostat {s.prep_coupling}", flush=True)
    prep: dict = {"protocol_version": PROTOCOL_V2, "liquid_expansion": s.liquid_expansion, "t_melt_K": s.t_melt,
                  "t_ref_K": s.t_ref, "prep_coupling": s.prep_coupling}
    if s.vc_relax_steps > 0:
        atoms, prep["vc_relaxation"] = vc_relax_v2(atoms, model, s, rows)

    rec = Recorder("prep_solid_npt", s.record_every, [s.t_prep])
    batch = Batch.from_data_list([make_data(atoms, s.t_prep, s.seed)], device="cuda")
    solid_hook = SuperheatingDetectorHook(action="record")  # a perfect-crystal start: what the hook was built for
    batch = run_stage(npt(model, s, [s.t_prep], s.equil_steps, s.prep_coupling), batch, model, rec,
                      extra_hooks=[solid_hook])
    rows += rec.rows
    solid = batch_to_atoms(batch)[0]
    reports = solid_hook.report()
    rep = reports[0] if reports else None
    prep["solid_detector"] = rep
    prep["solid_shape"] = {"cell_lengths_before_A": [round(float(x), 4) for x in atoms.cell.lengths()],
                           "cell_lengths_after_A": [round(float(x), 4) for x in solid.cell.lengths()]}
    if rep is not None and (rep["max_mobile_fraction"] >= PREP_CRYSTAL_LOST_MOBILE
                            or "superheating_observed" in rep["flags"]):
        prep.update(gate="crystal_lost_in_prep", stopped_after="prep_solid_npt")
        print(f"  GATE crystal_lost_in_prep: max_mobile_fraction {rep['max_mobile_fraction']:.2f} during the solid "
              f"preparation; hook flags {rep['flags']}", flush=True)
        return batch, prep

    frozen = solid.get_scaled_positions(wrap=True)[:, 2] < 0.5
    box = expand_top_half(solid, frozen, s.liquid_expansion)
    batch = Batch.from_data_list([make_data(box, s.t_melt, s.seed + 1, frozen)], device="cuda")
    pos0 = batch.positions.detach().clone()
    free = torch.tensor(~frozen, device=batch.positions.device)
    batch, melted, melt_temps = melt_until_liquid(batch, model, s, rows, "prep_melt_nvt", free, True,
                                                  melt_cap_reference(s))
    prep.update(melt_rounds=len(melt_temps), melt_temperatures_K=melt_temps,
                melted_half_moved_fraction_after_melt=melted)
    if melted < MELT_TARGET_FRACTION:
        prep.update(gate="liquid_not_prepared", stopped_after="prep_melt_nvt")
        print(f"  GATE liquid_not_prepared: only {melted:.0%} of the liquid half moved > 1.5 A after "
              f"{len(melt_temps)} melt round(s) at {melt_temps} K", flush=True)
        return batch, prep

    rec = Recorder("prep_quench_nvt", s.record_every, [s.t_prep])
    batch = run_stage(nvt(model, s, s.t_prep, s.quench_steps, s.seed + 9), batch, model, rec, freeze=True)
    rows += rec.rows
    cell = batch.cell[0]
    prep.update(frozen_half_moved_fraction=displaced_fraction(pos0, batch.positions, cell, ~free),
                melted_half_moved_fraction=displaced_fraction(pos0, batch.positions, cell, free))
    print(f"  liquid half: {prep['melted_half_moved_fraction']:.0%} of atoms moved > 1.5 A; "
          f"crystal half: {prep['frozen_half_moved_fraction']:.0%}", flush=True)
    return batch, prep


def v2_noise(pe: np.ndarray, n_end: int, n_ref: int) -> float:
    """Uncertainty (1 sigma) of end mean - reference mean, from the linearly detrended last half of production.

    Detrending keeps a transition's drift out of the noise (protocol 1's std of block-mean differences grew with
    the signal: Cu a1 1350 K threshold 0.0165). Blocks have the end window's length; the reference window's error
    is scaled from it by sqrt(n_end / n_ref).
    """
    half = np.asarray(pe, dtype=float)[len(pe) // 2:]
    if len(half) < 3 or n_end < 1:
        return 0.0
    x = np.arange(len(half))
    resid = half - np.polyval(np.polyfit(x, half, 1), x)
    n_blocks = len(resid) // n_end
    if n_blocks >= 2:
        sigma_end = float(np.std([resid[k * n_end:(k + 1) * n_end].mean() for k in range(n_blocks)], ddof=1))
    else:
        sigma_end = float(np.std(resid, ddof=1) / math.sqrt(n_end))
    return sigma_end * math.sqrt(1.0 + n_end / max(n_ref, 1))


def v2_pe_change(steps, pe, dt_fs: float, pe_threshold: float) -> dict:
    """PE change of one system from the start of production (REF_WINDOW_PS) to its last 1/END_BLOCKS."""
    pe = np.asarray(pe, dtype=float)
    t_ps = np.asarray(steps, dtype=float) * dt_fs / 1000.0
    n_blocks = max(1, min(END_BLOCKS, len(pe)))
    blocks = np.array_split(pe, n_blocks)
    means = np.array([b.mean() for b in blocks])
    in_window = (t_ps >= REF_WINDOW_PS[0]) & (t_ps <= REF_WINDOW_PS[1])
    if in_window.sum() >= 2:
        ref_rows, window = pe[in_window], [REF_WINDOW_PS[0], REF_WINDOW_PS[1]]
    else:  # production shorter than the window: first block without its first row (the velocity re-draw)
        ref_rows = blocks[0][1:] if len(blocks[0]) > 1 else blocks[0]
        window = [float(t_ps[1]) if len(t_ps) > 1 else 0.0, float(t_ps[len(blocks[0]) - 1])]
    ref = float(ref_rows.mean())
    end = float(means[-1])
    delta = end - ref
    noise = v2_noise(pe, len(blocks[-1]), len(ref_rows))
    threshold = max(pe_threshold, 3.0 * noise)
    pe_verdict = "melting" if delta > threshold else "freezing" if delta < -threshold else "stable"
    return {"ref_pe_eV_per_atom": ref, "end_pe_eV_per_atom": end, "delta_pe_eV_per_atom": delta,
            "block_means_eV_per_atom": [float(m) for m in means], "block_noise_eV_per_atom": noise,
            "threshold_eV_per_atom": threshold, "pe_verdict": pe_verdict, "ref_window_ps": window,
            "n_ref_rows": int(len(ref_rows)), "n_end_rows": int(len(blocks[-1]))}


def v2_verdict(pe_verdict: str, delta: float, mobility: float | None) -> dict:
    """Per-temperature verdict from the PE change and the hook's max_mobile_fraction.

    * mobility >= MOBILE_MELTED: "melting" (the whole box melted, also when it finished before the reference
      window), unless the PE fell significantly: then "conflict".
    * mobility < MOBILE_FROZEN: "freezing" if the PE did not rise (delta <= 0); "conflict" if it rose significantly;
      otherwise the PE verdict ("stable"): low mobility alone is not proof of freezing, because a slow, heavy liquid
      in a short run also moves little (diagnosis H3: Bi/In/Sn liquids move < 0.3 nn in 0.2 ps).
    * otherwise (or without mobility data): the PE verdict.
    """
    out = {"max_mobile_fraction": mobility, "mobility_verdict": None}
    if mobility is None:
        return {**out, "verdict": pe_verdict, "basis": "pe (no mobility data)"}
    if mobility >= MOBILE_MELTED:
        out["mobility_verdict"] = "melting"
        if pe_verdict == "freezing":
            return {**out, "verdict": "conflict", "basis": "box fully mobile but PE fell significantly"}
        return {**out, "verdict": "melting", "basis": "mobility and pe" if pe_verdict == "melting" else "mobility"}
    if mobility < MOBILE_FROZEN:
        out["mobility_verdict"] = "freezing"
        if pe_verdict == "melting":
            return {**out, "verdict": "conflict", "basis": "liquid half barely moved but PE rose significantly"}
        if delta > 0:
            return {**out, "verdict": pe_verdict, "basis": "pe (low mobility without a PE fall)"}
        return {**out, "verdict": "freezing", "basis": "mobility and pe" if pe_verdict == "freezing" else "mobility"}
    return {**out, "verdict": pe_verdict, "basis": "pe"}


def no_two_phase_box(per_t: list[dict]) -> bool:
    """The Sn signature: every system fully mobile and none with the PE rise of a crystal half melting."""
    mob = [p.get("max_mobile_fraction") for p in per_t]
    return (bool(per_t) and all(m is not None and m >= MOBILE_MELTED for m in mob)
            and not any(p.get("pe_verdict") == "melting" for p in per_t))


def bracket_verdict_v2(per_t: list[dict]) -> dict:
    """Protocol-2 bracket: monotone verdicts (freezing* stable* melting*) or "inconsistent"; Tm = bracket midpoint.

    A complete transition saturates the PE change (and one that ends before the reference window shows none), so
    the PE changes are not interpolated: the melting point is the middle of [highest freezing, lowest melting].
    """
    order = sorted(per_t, key=lambda p: p["T_target_K"])
    seq = [p["verdict"] for p in order]
    rank = {"freezing": 0, "stable": 1, "melting": 2}
    pattern = ", ".join(f"{p['T_target_K']:g} K {p['verdict']}" for p in order)
    if any(v not in rank for v in seq) or any(rank[a] > rank[b] for a, b in zip(seq, seq[1:])):
        return {"status": "failed", "failure": "inconsistent", "verdict_sequence": seq,
                "reason": "inconsistent: ordered by temperature the verdicts must go freezing -> stable -> melting "
                          f"(a 'conflict' means PE and mobility disagree); got {pattern}"}
    melt_t = [p["T_target_K"] for p in order if p["verdict"] == "melting"]
    freeze_t = [p["T_target_K"] for p in order if p["verdict"] == "freezing"]
    bounds = {**({"lower_bound_K": max(freeze_t)} if freeze_t else {}),
              **({"upper_bound_K": min(melt_t)} if melt_t else {})}
    if not melt_t and order[-1]["verdict"] == "freezing":
        return {"status": "failed", "failure": "above_bracket", **bounds,
                "reason": "no temperature melted and the hottest run froze: the melting point is above the bracket"}
    if not freeze_t and order[0]["verdict"] == "melting":
        return {"status": "failed", "failure": "below_bracket", **bounds,
                "reason": "no temperature froze and the coldest run melted: the melting point is below the bracket"}
    if not melt_t or not freeze_t:
        end = "hottest" if not melt_t else "coldest"
        return {"status": "failed", "failure": "undecided", **bounds,
                "reason": f"undecided: the interface barely moved at the {end} temperatures, so the melting point is "
                          "near them; rerun the same temperatures longer"}
    lo, hi = max(freeze_t), min(melt_t)
    return {"status": "ok", "value": round((lo + hi) / 2, 1), "uncertainty": round((hi - lo) / 2, 1), "units": "K",
            "property": "melting_point", "bracket_K": [lo, hi], "estimate": "bracket midpoint"}


def analyse_coexistence_v2(rows: list[dict], temps: list[float], dt_fs: float, pe_threshold: float,
                           detector_report: list[dict] | None, prep: dict) -> dict:
    """Protocol-2 verdicts from production rows + the production hook report (pure: also used by ``reanalyze``)."""
    reports = detector_report or []
    mobility = ([r.get("max_mobile_fraction") for r in reports] if len(reports) == len(temps)
                else [None] * len(temps))
    per_t = []
    for i, t in enumerate(temps):
        steps = production_series(rows, "production_npt", i, "step", discard=0.0)
        pe = production_series(rows, "production_npt", i, "pe_per_atom_eV", discard=0.0)
        change = v2_pe_change(steps, pe, dt_fs, pe_threshold)
        verdict = v2_verdict(change["pe_verdict"], change["delta_pe_eV_per_atom"], mobility[i])
        t_meas = float(production_series(rows, "production_npt", i, "T_K").mean())
        per_t.append({"T_target_K": t, "T_measured_K": round(t_meas, 1), **change, **verdict})
    out: dict = {"per_temperature": per_t, "flags": []}
    if prep.get("frozen_half_moved_fraction", 0.0) > 0.05:
        out["flags"].append("crystal_half_disturbed")
    if no_two_phase_box(per_t):
        out["flags"].append("no_two_phase_box")
        out.update(status="failed", failure="no_two_phase_box", reason=GATE_REASONS["no_two_phase_box"])
    else:
        out.update(bracket_verdict(per_t, protocol=PROTOCOL_V2))
    return out


def run_coexistence_v2(atoms: Atoms, model, s: SettingsV2, rows: list) -> tuple[dict, Batch]:
    prepared, prep = prepare_coexistence_v2(atoms, model, s, rows)
    result: dict = {"mode": "coexistence", "protocol_version": PROTOCOL_V2, "t_melt_K": s.t_melt, "t_ref_K": s.t_ref,
                    "seed": s.seed, "prep": prep, "per_temperature": [], "flags": []}
    gate = prep.get("gate")
    if gate:
        result.update(status="failed", failure=gate, reason=GATE_REASONS[gate], stopped_after=prep["stopped_after"],
                      flags=[gate])
        return result, prepared

    box = batch_to_atoms(prepared)[0]
    data = [make_data(box, t, s.seed + 10 + i) for i, t in enumerate(s.temps)]
    batch = Batch.from_data_list(data, device="cuda")
    rec = Recorder("production_npt", s.record_every, s.temps)
    detector = SuperheatingDetectorHook(action="record")  # only its max_mobile_fraction is used (cross-check)
    batch = run_stage(npt(model, s, s.temps, s.prod_steps, "anisotropic"), batch, model, rec,
                      extra_hooks=[detector])
    rows += rec.rows
    result["superheating_detector"] = detector.report()
    analysis = analyse_coexistence_v2(rows, s.temps, s.dt_fs, s.pe_threshold, result["superheating_detector"], prep)
    result.update(analysis)
    for p in result["per_temperature"]:
        mob = "n/a" if p["max_mobile_fraction"] is None else f"{p['max_mobile_fraction']:.2f}"
        print(f"  T={p['T_target_K']:7.1f} K (measured {p['T_measured_K']:7.1f}): dPE = "
              f"{p['delta_pe_eV_per_atom']:+.4f} eV/atom (threshold {p['threshold_eV_per_atom']:.4f}), mobility {mob} "
              f"-> {p['verdict']} [{p['basis']}]", flush=True)
    return result, batch


# --------------------------------------------------------------------------- re-analysis (CPU, no dynamics)


def load_run(run_dir: Path) -> tuple[dict, dict, list[dict]]:
    """(settings.json, result.json, timeseries rows with the types the live Recorder writes)."""
    settings = json.loads((run_dir / "settings.json").read_text())
    result = json.loads((run_dir / "result.json").read_text())
    rows = []
    with open(run_dir / "timeseries.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            row = {"stage": r["stage"], "step": int(r["step"]), "system": int(r["system"])}
            row.update({k: float(v) for k, v in r.items() if k not in row})
            rows.append(row)
    return settings, result, rows


def per_temperature_v1(rows: list[dict], temps: list[float], pe_threshold: float) -> list[dict]:
    """Protocol-1 per-temperature verdicts, computed exactly as run_coexistence does (for ``reanalyze``)."""
    per_t = []
    for i, t in enumerate(temps):
        pe = production_series(rows, "production_npt", i, "pe_per_atom_eV", discard=0.05)
        means = block_means(pe, n_blocks=10)
        delta = float(means[-1] - means[0])
        noise = float(np.std(np.diff(means)) / math.sqrt(2)) if len(means) > 2 else 0.0
        threshold = max(pe_threshold, 3.0 * noise)
        verdict = "melting" if delta > threshold else "freezing" if delta < -threshold else "stable"
        t_meas = float(production_series(rows, "production_npt", i, "T_K").mean())
        per_t.append({"T_target_K": t, "T_measured_K": round(t_meas, 1), "delta_pe_eV_per_atom": delta,
                      "block_noise_eV_per_atom": noise, "threshold_eV_per_atom": threshold, "verdict": verdict})
    return per_t


def prep_gates_from_saved(prep: dict) -> dict:
    """Re-check the protocol-2 preparation gates from what a run saved: {gate: {"fired": bool | None, "note"}}."""
    gates = {}
    rep = prep.get("solid_detector")
    if rep is None:
        gates["crystal_lost_in_prep"] = {"fired": None, "note": "not checkable: the run saved no displacement data for "
                                         "the solid preparation (protocol 1 records only PE, T, V and Lz there)"}
    else:
        fired = rep["max_mobile_fraction"] >= PREP_CRYSTAL_LOST_MOBILE or "superheating_observed" in rep["flags"]
        gates["crystal_lost_in_prep"] = {"fired": fired, "note": f"solid-prep max_mobile_fraction "
                                         f"{rep['max_mobile_fraction']:.3f} (stops at >= {PREP_CRYSTAL_LOST_MOBILE})"}
    if "melted_half_moved_fraction_after_melt" in prep:
        frac, when = prep["melted_half_moved_fraction_after_melt"], "after the melt rounds"
    else:
        frac, when = prep.get("melted_half_moved_fraction"), "after the quench (protocol 1 saves only this value)"
    if frac is None:
        gates["liquid_not_prepared"] = {"fired": None, "note": "not checkable: no melted fraction saved"}
    else:
        gates["liquid_not_prepared"] = {"fired": frac < MELT_TARGET_FRACTION,
                                        "note": f"melted half {frac:.3f} {when} (stops at < {MELT_TARGET_FRACTION})"}
    return gates


def reanalyze(run_dir: Path, protocol: int = 1) -> dict:
    """Recompute the verdicts and the bracket of a saved coexistence run (no dynamics, nothing written)."""
    settings, saved, rows = load_run(run_dir)
    s = settings["settings"]
    if s.get("mode") != "coexistence":
        raise ValueError(f"{run_dir} is a {s.get('mode')!r} run, not coexistence")
    temps = [float(t) for t in s["temps"]]
    prep = saved.get("prep") or {}
    has_production = any(r["stage"] == "production_npt" for r in rows)
    report = {"run_dir": str(run_dir), "protocol_version": protocol,
              "saved": {"protocol_version": saved.get("protocol_version", 1),
                        **{k: saved[k] for k in ("status", "failure", "value", "uncertainty", "flags") if k in saved},
                        "verdicts": [p["verdict"] for p in saved.get("per_temperature") or []]},
              "temps_K": temps}
    if protocol != PROTOCOL_V2:
        per_t = per_temperature_v1(rows, temps, s["pe_threshold"]) if has_production else []
        report["per_temperature"] = per_t
        report["result"] = (bracket_verdict(per_t) if per_t else
                            {k: saved.get(k) for k in ("status", "failure", "reason")})
        return report
    gates = prep_gates_from_saved(prep)
    report["prep_gates"] = gates
    analysis = (analyse_coexistence_v2(rows, temps, s["dt_fs"], s["pe_threshold"],
                                       saved.get("superheating_detector"), prep) if has_production else None)
    report["per_temperature"] = analysis["per_temperature"] if analysis else []
    fired = next((g for g in ("crystal_lost_in_prep", "liquid_not_prepared") if gates[g]["fired"]), None)
    if fired:
        report["result"] = {"status": "failed", "failure": fired, "reason": GATE_REASONS[fired],
                            "note": "protocol 2 would have stopped before production; the per-temperature verdicts "
                                    "below are informational only"}
        if analysis:
            report["production_analysis_informational"] = {k: v for k, v in analysis.items()
                                                           if k != "per_temperature"}
    elif analysis:
        report["result"] = {k: v for k, v in analysis.items() if k != "per_temperature"}
    else:
        report["result"] = {k: saved.get(k) for k in ("status", "failure", "reason")}
    return report


def format_reanalysis(report: dict) -> str:
    sv = report["saved"]
    lines = [f"Re-analysis of {report['run_dir']}",
             f"  saved: protocol {sv['protocol_version']}, status {sv.get('status')}"
             f"{' (' + sv['failure'] + ')' if sv.get('failure') else ''}, verdicts {sv['verdicts']}",
             f"  re-analysed with protocol {report['protocol_version']} (no dynamics; nothing written to the run dir)"]
    for name, g in (report.get("prep_gates") or {}).items():
        state = {True: "FIRED", False: "pass", None: "n/a"}[g["fired"]]
        lines.append(f"  prep gate {name}: {state}: {g['note']}")
    per_t = report.get("per_temperature") or []
    saved_v = sv["verdicts"]
    if per_t and report["protocol_version"] == PROTOCOL_V2:
        lines.append(f"  {'T (K)':>8} {'ref PE':>9} {'end PE':>9} {'dPE':>8} {'thresh':>7} {'mobility':>8} "
                     f"{'PE call':>9} {'mob call':>9} {'verdict':>9}  {'saved':>8}  basis")
        for i, p in enumerate(per_t):
            mob = "n/a" if p["max_mobile_fraction"] is None else f"{p['max_mobile_fraction']:.3f}"
            lines.append(f"  {p['T_target_K']:8.1f} {p['ref_pe_eV_per_atom']:9.4f} {p['end_pe_eV_per_atom']:9.4f} "
                         f"{p['delta_pe_eV_per_atom']:+8.4f} {p['threshold_eV_per_atom']:7.4f} {mob:>8} "
                         f"{p['pe_verdict']:>9} {str(p['mobility_verdict'] or '-'):>9} {p['verdict']:>9}  "
                         f"{saved_v[i] if i < len(saved_v) else '-':>8}  {p['basis']}")
    elif per_t:
        lines.append(f"  {'T (K)':>8} {'dPE':>8} {'thresh':>7} {'verdict':>9}  {'saved':>8}")
        for i, p in enumerate(per_t):
            lines.append(f"  {p['T_target_K']:8.1f} {p['delta_pe_eV_per_atom']:+8.4f} {p['threshold_eV_per_atom']:7.4f} "
                         f"{p['verdict']:>9}  {saved_v[i] if i < len(saved_v) else '-':>8}")
    else:
        lines.append("  (no production rows saved)")
    r = report["result"]
    if r.get("status") == "ok":
        lines.append(f"  result: ok: melting point {r['value']} +/- {r['uncertainty']} K (bracket {r['bracket_K']}"
                     f"{', ' + r['estimate'] if r.get('estimate') else ''})")
    else:
        lines.append(f"  result: {r.get('status')} ({r.get('failure')}): {r.get('reason')}")
        if r.get("note"):
            lines.append(f"          {r['note']}")
    info = report.get("production_analysis_informational")
    if info:
        lines.append(f"  production analysis (informational): {info.get('status')} ({info.get('failure')})")
    flags = r.get("flags") or (info or {}).get("flags")
    if flags:
        lines.append(f"  flags: {flags}")
    return "\n".join(lines)


def reanalyze_main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="python -m coolorbit.sim.melting reanalyze",
                                 description="Recompute the per-temperature verdicts and the bracket of a saved "
                                             "coexistence run (CPU, no dynamics; nothing is written to the run dir).")
    ap.add_argument("run_dir", help="a coexistence run directory (settings.json, result.json, timeseries.csv)")
    ap.add_argument("--protocol", type=int, choices=[1, PROTOCOL_V2], default=1,
                    help="1 reproduces the saved analysis; 2 applies the protocol-2 verdict, gates and bracket")
    ap.add_argument("--out", help="also write the re-analysis as JSON to this NEW file (outside the run dir)")
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir)
    if not all((run_dir / f).is_file() for f in ("settings.json", "result.json", "timeseries.csv")):
        ap.error(f"{run_dir} needs settings.json, result.json and timeseries.csv")
    report = reanalyze(run_dir, a.protocol)
    print(format_reanalysis(report))
    if a.out:
        out = Path(a.out).resolve()
        if out.is_relative_to(run_dir.resolve()):
            ap.error("--out must be outside the run directory (re-analysis never writes into it)")
        if out.exists():
            ap.error(f"{out} exists; --out only writes a new file")
        if not out.parent.is_dir():
            ap.error(f"{out.parent} is not a directory")
        out.write_text(json.dumps(report, indent=2))
        print(f"  written: {out}")


# --------------------------------------------------------------------------- output


def post_to_board(args, result: dict, s: Settings, n_atoms: int, run_dir: Path) -> str:
    from coolorbit.board import Board
    from coolorbit.sim.results import build_property_payload

    board = Board(args.board)
    cand = board.snapshot().find(args.candidate_id)
    if cand.artifact_type != "candidate":
        raise SystemExit(f"{args.candidate_id} is a {cand.artifact_type}, not a candidate")
    payload = build_property_payload(cand.payload, result, s.__dict__, n_atoms, str(run_dir))
    if getattr(s, "protocol_version", 1) == PROTOCOL_V2:
        payload["method"]["protocol_version"] = PROTOCOL_V2
        if getattr(s, "git_commit", None):
            payload["method"]["git_commit"] = s.git_commit
    art = board.post(args.agent, "property_result", payload, parents=[cand.artifact_id], skill="coolorbit.sim.melting")
    return art.artifact_id


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "reanalyze":
        reanalyze_main(sys.argv[2:])
        return
    ap = argparse.ArgumentParser(prog="python -m coolorbit.sim.melting", description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["coexistence", "heating", "latent", "alloy-latent"])
    g = ap.add_argument_group("structure")
    g.add_argument("--element", help="element symbol for an ASE bulk crystal, e.g. Cu")
    g.add_argument("--structure", default="fcc",
                   help="ASE bulk structure (fcc, bcc, hcp, diamond, ...) or 'ref' for the element's real crystal "
                        "(Ga, In, beta-Sn, Bi, Cu, Al; see coolorbit/sim/alloys.py)")
    g.add_argument("--composition", help="alloy-latent: e.g. wt:Bi32.5In51Sn16.5 or at:Ga86In14")
    g.add_argument("--liquid-atoms", type=int, default=500, help="alloy-latent: atoms in the liquid alloy box")
    g.add_argument("--solid-atoms", type=int, default=300, help="alloy-latent: minimum atoms per element solid")
    g.add_argument("--a", type=float, help="starting lattice constant (A); NPT relaxes it")
    g.add_argument("--structure-file", help="any ASE-readable periodic unit cell instead of --element")
    g.add_argument("--reps", type=int, nargs=3, default=[4, 4, 8], help="supercell repeats; coexistence uses z")
    g.add_argument("--label", default=None)
    g = ap.add_argument_group("protocol")
    g.add_argument("--temps", type=float, nargs="+", help="trial temperatures (K), one system each")
    g.add_argument("--temperature", type=float, help="latent mode: the single temperature (K)")
    g.add_argument("--t-prep", type=float, help="coexistence: preparation temperature (default: mean of --temps)")
    g.add_argument("--t-melt", type=float, help="temperature used to melt (default 1.6 x T_prep)")
    g.add_argument("--model", default="medium-mpa-0")
    g.add_argument("--dt", type=float, default=2.0, help="timestep (fs); use <= 0.5 with hydrogen")
    g.add_argument("--equil-steps", type=int, help="default 4000 (quick: 500)")
    g.add_argument("--melt-steps", type=int, help="default 6000 (quick: 1500)")
    g.add_argument("--quench-steps", type=int, help="default 3000 (quick: 500)")
    g.add_argument("--prod-steps", type=int, help="default 25000 (quick: 2500)")
    g.add_argument("--record-every", type=int, default=50)
    g.add_argument("--liquid-expansion", type=float, default=0.05,
                   help="volume increase on melting used to give the liquid room (metals ~0.05, organics ~0.10-0.15)")
    g.add_argument("--pe-threshold", type=float, default=0.002, help="coexistence: |dPE| (eV/atom) to call melt/freeze")
    g.add_argument("--jump-threshold", type=float, default=0.03, help="heating: PE jump (eV/atom) that marks melting")
    g.add_argument("--seed", type=int, default=2026)
    g.add_argument("--quick", action="store_true", help="short runs to test the code path (not production physics)")
    g = ap.add_argument_group("coexistence protocol 2 (opt-in; docs/results/coexistence-diagnosis.md)")
    g.add_argument("--protocol", type=int, choices=[1, PROTOCOL_V2], default=1,
                   help="1 = unchanged default; 2 = start-referenced verdict + mobility cross-check, monotone bracket, "
                        "fail-fast preparation gates, material-based melt temperature, relaxed solid shape")
    g.add_argument("--t-ref", type=float,
                   help="protocol 2: the material's reference temperature (K; t_guess or measured Tm). Without "
                        "--t-melt, T_melt = max(1.6 x T_prep, 2 x T_ref)")
    g.add_argument("--vc-relax-steps", type=int,
                   help=f"protocol 2: FIRE2VariableCell steps at 0 K before the solid NPT (default "
                        f"{VC_RELAX_DEFAULT_STEPS}; 0 = off)")
    g.add_argument("--prep-coupling", choices=["anisotropic", "isotropic"],
                   help="protocol 2: barostat of the solid preparation (default anisotropic; isotropic = protocol 1)")
    g = ap.add_argument_group("board (optional)")
    g.add_argument("--board")
    g.add_argument("--agent")
    g.add_argument("--candidate-id")
    ap.add_argument("--out", default="runs/melting")
    args = ap.parse_args()

    if args.mode == "alloy-latent":
        if not args.composition:
            ap.error("alloy-latent needs --composition (e.g. wt:Bi32.5In51Sn16.5)")
    elif not (args.element or args.structure_file):
        ap.error("give --element (with --a, or --structure ref) or --structure-file")
    elif args.element and args.structure != "ref" and args.a is None:
        ap.error("--element needs --a unless --structure ref")
    temps = [args.temperature] if args.mode in ("latent", "alloy-latent") else args.temps
    if not temps or any(t is None for t in temps):
        ap.error("coexistence/heating need --temps; latent needs --temperature")
    if args.board and not (args.agent and args.candidate_id):
        ap.error("--board needs --agent and --candidate-id")
    if args.protocol == PROTOCOL_V2 and args.mode != "coexistence":
        ap.error("--protocol 2 changes only the coexistence workflow")
    if args.protocol != PROTOCOL_V2 and any(getattr(args, k) is not None for k in V2_ONLY_ARGS if k != "protocol"):
        ap.error("--t-ref, --vc-relax-steps and --prep-coupling need --protocol 2")
    if not torch.cuda.is_available():
        raise SystemExit("CUDA not available: these runs must use the GPU.")
    q = args.quick  # explicit step counts always win over the presets
    args.equil_steps = args.equil_steps or (500 if q else 4000)
    args.melt_steps = args.melt_steps or (1500 if q else 6000)
    args.quench_steps = args.quench_steps or (500 if q else 3000)
    args.prod_steps = args.prod_steps or (2500 if q else 25000)

    t_prep = args.t_prep or float(np.mean(temps))
    default_label = (composition_label(parse_composition(args.composition)) if args.mode == "alloy-latent"
                     else args.element or Path(args.structure_file).stem)
    s = Settings(mode=args.mode, label=args.label or default_label,
                 model=args.model, dt_fs=args.dt, temps=[float(t) for t in temps], t_prep=t_prep,
                 t_melt=args.t_melt or 1.6 * t_prep, equil_steps=args.equil_steps, melt_steps=args.melt_steps,
                 quench_steps=args.quench_steps, prod_steps=args.prod_steps, record_every=args.record_every,
                 friction_per_fs=0.01, thermostat_fs=100.0, barostat_fs=1000.0, pe_threshold=args.pe_threshold,
                 jump_threshold=args.jump_threshold, seed=args.seed, liquid_expansion=args.liquid_expansion,
                 composition=args.composition, solid_atoms=args.solid_atoms)
    if args.protocol == PROTOCOL_V2:
        s = settings_v2(s, args)

    if s.mode == "alloy-latent":
        atoms = mixed_alloy_box(parse_composition(args.composition), n_atoms=args.liquid_atoms,
                                expansion=args.liquid_expansion, seed=args.seed)
    else:
        atoms = build_atoms(args)
    flags = domain_flags(atoms, s.dt_fs)
    if s.mode == "coexistence" and args.reps[2] < 2 * max(args.reps[:2]):
        print("WARNING: coexistence works best with the box ~2x longer along z than x/y")
    n = len(atoms)
    m = len(s.temps)
    atom_steps = {"coexistence": n * (s.equil_steps + s.melt_steps + s.quench_steps) + m * n * s.prod_steps,
                  "heating": m * n * s.prod_steps,
                  "latent": n * s.melt_steps + 2 * n * s.prod_steps,
                  "alloy-latent": n * s.melt_steps + (n + (len(parse_composition(s.composition)) * s.solid_atoms
                                                           if s.composition else 0)) * s.prod_steps}[s.mode]
    if args.protocol == PROTOCOL_V2:
        atom_steps += n * s.vc_relax_steps
    print(f"{s.mode}: {n} atoms x {m} system(s); ~{atom_steps / GB10_ATOM_STEPS_PER_S / 60:.0f} min "
          f"at the measured GB10 rate. Flags: {flags or 'none'}", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.out) / f"{stamp}_{s.label}_{s.mode}{'_quick' if args.quick else ''}"
    run_dir.mkdir(parents=True, exist_ok=True)
    env = {"torch": torch.__version__, "nvalchemi": version("nvalchemi-toolkit"), "mace_torch": version("mace-torch"),
           "gpu": torch.cuda.get_device_name(0), "quick": args.quick}
    arg_dict = (vars(args) if args.protocol == PROTOCOL_V2
                else {k: v for k, v in vars(args).items() if k not in V2_ONLY_ARGS})  # protocol 1: as before
    (run_dir / "settings.json").write_text(json.dumps({"settings": s.__dict__, "args": arg_dict, "env": env,
                                                       "n_atoms": n, "flags": flags}, indent=2))

    model = load_model(s.model)
    rows: list[dict] = []
    t0 = time.perf_counter()
    runner = {"coexistence": run_coexistence, "heating": run_heating, "latent": run_latent,
              "alloy-latent": run_alloy_latent}[s.mode]
    if args.protocol == PROTOCOL_V2:
        runner = run_coexistence_v2
    result, final = runner(atoms, model, s, rows)
    result["wall_s"] = round(time.perf_counter() - t0, 1)
    result["flags"] = sorted(set(result.get("flags", [])) | set(flags))
    if args.quick:
        result["flags"].append("quick_settings_not_production")

    with open(run_dir / "timeseries.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for i, a in enumerate(batch_to_atoms(final)):
        name = (f"final_{result['stopped_after']}.extxyz" if result.get("stopped_after")  # protocol-2 gate stop
                else f"final_{i}_T{s.temps[i] if i < m else s.temps[0]:g}.extxyz")
        ase_write(run_dir / name, a)
    if args.board and result.get("status") == "ok":
        result["board_artifact_id"] = post_to_board(args, result, s, n, run_dir)
    (run_dir / "result.json").write_text(json.dumps(result, indent=2))

    print(f"\nRESULT ({result.get('status')}): " + (
        f"{result['property']} = {result['value']} +/- {result['uncertainty']} {result['units']}"
        if result.get("status") == "ok" else result.get("reason", "")))
    print(f"Flags: {result['flags'] or 'none'}   Wall time: {result['wall_s'] / 60:.1f} min   Output: {run_dir}")


if __name__ == "__main__":
    main()
