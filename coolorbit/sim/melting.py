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

Every run writes runs/melting/<stamp>_<label>_<mode>/ with settings.json,
timeseries.csv, result.json and final structures (extxyz). With --board/--agent/
--candidate-id the result is also posted to the results board.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
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
    melt_t = [p["T_target_K"] for p in per_t if p["verdict"] == "melting"]
    freeze_t = [p["T_target_K"] for p in per_t if p["verdict"] == "freezing"]
    if not melt_t:
        result.update(status="failed", failure="above_bracket",
                      reason="no temperature melted: the melting point is above the bracket")
    elif not freeze_t:
        result.update(status="failed", failure="below_bracket",
                      reason="no temperature froze: the melting point is below the bracket")
    elif max(freeze_t) >= min(melt_t):
        result.update(status="failed", failure="inconsistent",
                      reason="inconsistent: froze at or above a melting temperature (runs too short or too noisy)")
    else:
        lo, hi = max(freeze_t), min(melt_t)
        pts = [(p["T_target_K"], p["delta_pe_eV_per_atom"]) for p in per_t if lo <= p["T_target_K"] <= hi]
        slope, icept = np.polyfit([p[0] for p in pts], [p[1] for p in pts], 1)
        tm = float(np.clip(-icept / slope, lo, hi)) if slope > 0 else (lo + hi) / 2
        result.update(status="ok", value=round(tm, 1), uncertainty=round((hi - lo) / 2, 1), units="K",
                      property="melting_point", bracket_K=[lo, hi])
    return result, batch


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

    Assumes the alloy's solid is a mixture of the pure elements (true for e.g. Ga-In; intermetallics such as
    BiIn2 in Field's metal would lower the real latent heat). Flagged in every result.
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


# --------------------------------------------------------------------------- output


def post_to_board(args, result: dict, s: Settings, n_atoms: int, run_dir: Path) -> str:
    from coolorbit.board import Board
    from coolorbit.sim.results import build_property_payload

    board = Board(args.board)
    cand = board.snapshot().find(args.candidate_id)
    if cand.artifact_type != "candidate":
        raise SystemExit(f"{args.candidate_id} is a {cand.artifact_type}, not a candidate")
    payload = build_property_payload(cand.payload, result, s.__dict__, n_atoms, str(run_dir))
    art = board.post(args.agent, "property_result", payload, parents=[cand.artifact_id], skill="coolorbit.sim.melting")
    return art.artifact_id


def main() -> None:
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
    print(f"{s.mode}: {n} atoms x {m} system(s); ~{atom_steps / GB10_ATOM_STEPS_PER_S / 60:.0f} min "
          f"at the measured GB10 rate. Flags: {flags or 'none'}", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.out) / f"{stamp}_{s.label}_{s.mode}{'_quick' if args.quick else ''}"
    run_dir.mkdir(parents=True, exist_ok=True)
    env = {"torch": torch.__version__, "nvalchemi": version("nvalchemi-toolkit"), "mace_torch": version("mace-torch"),
           "gpu": torch.cuda.get_device_name(0), "quick": args.quick}
    (run_dir / "settings.json").write_text(json.dumps({"settings": s.__dict__, "args": vars(args), "env": env,
                                                       "n_atoms": n, "flags": flags}, indent=2))

    model = load_model(s.model)
    rows: list[dict] = []
    t0 = time.perf_counter()
    runner = {"coexistence": run_coexistence, "heating": run_heating, "latent": run_latent,
              "alloy-latent": run_alloy_latent}[s.mode]
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
        ase_write(run_dir / f"final_{i}_T{s.temps[i] if i < m else s.temps[0]:g}.extxyz", a)
    if args.board and result.get("status") == "ok":
        result["board_artifact_id"] = post_to_board(args, result, s, n, run_dir)
    (run_dir / "result.json").write_text(json.dumps(result, indent=2))

    print(f"\nRESULT ({result.get('status')}): " + (
        f"{result['property']} = {result['value']} +/- {result['uncertainty']} {result['units']}"
        if result.get("status") == "ok" else result.get("reason", "")))
    print(f"Flags: {result['flags'] or 'none'}   Wall time: {result['wall_s'] / 60:.1f} min   Output: {run_dir}")


if __name__ == "__main__":
    main()
