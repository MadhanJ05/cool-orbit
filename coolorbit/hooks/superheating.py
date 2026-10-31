"""SuperheatingDetectorHook: catch melting points read off perfect crystals.

A defect-free crystal has nowhere to start melting, so in MD it stays solid well
above its true melting point and then melts abruptly. Reading the melting point
off that jump ("single-phase heating") overestimates it, and the result looks
clean and confident. This is the classic trap the hook catches.

How it decides, per system in the batch (no protocol metadata needed):

1. Reference: atom positions at the first call.
2. Start state (``probe_step`` steps later): if almost no atom has moved more than
   ``early_factor`` x nearest-neighbour distance, the system started as a
   defect-free crystal ("crystalline"). A two-phase solid|liquid box or a liquid
   already has many mobile atoms ("disordered_or_interface") and is never flagged.
3. Melting event: a crystalline-start system in which at least
   ``melted_min_mobile`` of the atoms have moved more than ``mobile_factor`` x the
   nearest-neighbour distance has melted from a perfect crystal, so it superheated.

Flags in each :class:`SystemReport`:
  ``superheating_risk``      started as a perfect crystal (no nucleation site), so
                             "no melting observed at T" also says nothing about T_m
  ``superheating_observed``  and then melted: the melting temperature it suggests is too high

Displacements use minimum-image fractional coordinates, so periodic wrapping and
barostat cell changes are handled. Hydrogen is ignored by default (fast vibrations).
Thresholds were chosen for atomic and ionic crystals; validate them before
relying on them for molecular crystals.

Usage (any nvalchemi dynamics)::

    hook = SuperheatingDetectorHook(action="warn")     # or "raise" / "record"
    dynamics.register_hook(hook)
    dynamics.run(batch)
    hook.report()   # per-system dicts; hook.flags() -> set of flags
"""

from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

import torch

from nvalchemi.dynamics.base import DynamicsStage
from nvalchemi.dynamics.hooks._utils import KB_EV, kinetic_energy_per_graph

__all__ = ["SuperheatingDetected", "SuperheatingDetectorHook", "SystemReport"]


class SuperheatingDetected(RuntimeError):
    """Raised (with ``action="raise"``) when a perfect crystal melts during the run."""


@dataclass
class SystemReport:
    system: int
    n_atoms_tracked: int
    nn_distance_A: float
    start_state: str = "unknown"  # crystalline | disordered_or_interface | unknown
    early_mobile_fraction: Optional[float] = None
    max_mobile_fraction: float = 0.0
    melted: bool = False
    melt_step: Optional[int] = None
    melt_temperature_K: Optional[float] = None
    flags: list[str] = field(default_factory=list)


class SuperheatingDetectorHook:
    """Flag systems that start as perfect crystals and then melt. See the module docstring."""

    stage = DynamicsStage.AFTER_STEP

    def __init__(
        self,
        frequency: int = 50,
        probe_step: int = 100,
        early_factor: float = 0.3,
        crystalline_max_mobile: float = 0.05,
        mobile_factor: float = 0.5,
        melted_min_mobile: float = 0.5,
        ignore_hydrogen: bool = True,
        action: Literal["warn", "raise", "record"] = "warn",
        n_sample: int = 128,
    ) -> None:
        if action not in ("warn", "raise", "record"):
            raise ValueError(f"action must be warn, raise or record, not {action!r}")
        self.frequency = frequency
        self.probe_step = probe_step
        self.early_factor = early_factor
        self.crystalline_max_mobile = crystalline_max_mobile
        self.mobile_factor = mobile_factor
        self.melted_min_mobile = melted_min_mobile
        self.ignore_hydrogen = ignore_hydrogen
        self.action = action
        self.n_sample = n_sample
        self.reports: list[SystemReport] = []
        self._ref_frac: Optional[torch.Tensor] = None
        self._shape: Optional[tuple[int, int]] = None

    # ------------------------------------------------------------------ hook

    def __call__(self, ctx: Any, stage: Any) -> None:
        b = ctx.batch
        step = int(ctx.step_count)
        with torch.no_grad():
            if self._ref_frac is None or self._shape != (int(b.num_graphs), int(b.positions.shape[0])):
                if self._ref_frac is not None:
                    warnings.warn("SuperheatingDetectorHook: batch shape changed; restarting detection.", UserWarning)
                self._initialize(b, step)
                return
            elapsed = step - self._ref_step
            early = self._mobile_fraction(b, self.early_factor)
            mobile = self._mobile_fraction(b, self.mobile_factor)
            temps = self._temperatures(b)

        for rep, f_early, f_mob, temp in zip(self.reports, early, mobile, temps):
            rep.max_mobile_fraction = max(rep.max_mobile_fraction, f_mob)
            if rep.start_state == "unknown" and elapsed >= self.probe_step:
                rep.early_mobile_fraction = f_early
                if f_early <= self.crystalline_max_mobile:
                    rep.start_state = "crystalline"
                    rep.flags.append("superheating_risk")
                else:
                    rep.start_state = "disordered_or_interface"
            if rep.start_state == "crystalline" and not rep.melted and f_mob >= self.melted_min_mobile:
                rep.melted, rep.melt_step, rep.melt_temperature_K = True, step, round(temp, 1)
                rep.flags.append("superheating_observed")
                self._fire(rep)

    # --------------------------------------------------------------- results

    def report(self) -> list[dict]:
        return [asdict(r) for r in self.reports]

    def flags(self) -> set[str]:
        return {f for r in self.reports for f in r.flags}

    # --------------------------------------------------------------- helpers

    def _fire(self, rep: SystemReport) -> None:
        msg = (f"SuperheatingDetectorHook: system {rep.system} started as a defect-free crystal and melted at "
               f"step {rep.melt_step} (T = {rep.melt_temperature_K:.0f} K). A perfect crystal superheats, so this "
               "temperature overestimates the melting point. Use two-phase (solid-liquid) coexistence.")
        if self.action == "raise":
            raise SuperheatingDetected(msg)
        if self.action == "warn":
            warnings.warn(msg, UserWarning, stacklevel=2)

    def _fractional(self, b: Any) -> torch.Tensor:
        inv = torch.linalg.inv(b.cell.to(torch.float64))
        return torch.einsum("ni,nij->nj", b.positions.to(torch.float64), inv[b.batch_idx])

    def _initialize(self, b: Any, step: int) -> None:
        self._ref_step = step
        self._shape = (int(b.num_graphs), int(b.positions.shape[0]))
        self._ref_frac = self._fractional(b)
        z = b.atomic_numbers
        self._mask = (z != 1) if self.ignore_hydrogen else torch.ones_like(z, dtype=torch.bool)
        cells = b.cell.to(torch.float64)
        nn, counts = [], []
        for i in range(int(b.num_graphs)):
            idx = torch.nonzero((b.batch_idx == i) & self._mask).squeeze(-1)
            frac = self._ref_frac[idx]
            d = frac[: self.n_sample, None, :] - frac[None, :, :]
            d -= torch.round(d)
            dist = (d @ cells[i]).norm(dim=-1)
            dist[dist < 1e-6] = float("inf")  # self-distance
            nn.append(float(dist.min(dim=1).values.median()))
            counts.append(int(idx.numel()))
        self._nn = torch.tensor(nn, dtype=torch.float64, device=b.positions.device)
        self.reports = [SystemReport(system=i, n_atoms_tracked=c, nn_distance_A=round(d, 3))
                        for i, (c, d) in enumerate(zip(counts, nn))]

    def _mobile_fraction(self, b: Any, factor: float) -> list[float]:
        d = self._fractional(b) - self._ref_frac
        d -= torch.round(d)
        disp = torch.einsum("ni,nij->nj", d, b.cell.to(torch.float64)[b.batch_idx]).norm(dim=-1)
        mobile = ((disp > factor * self._nn[b.batch_idx]) & self._mask).to(torch.float64)
        m = int(b.num_graphs)
        num = torch.zeros(m, dtype=torch.float64, device=disp.device).index_add_(0, b.batch_idx, mobile)
        den = torch.zeros(m, dtype=torch.float64, device=disp.device).index_add_(0, b.batch_idx,
                                                                                  self._mask.to(torch.float64))
        return (num / den.clamp(min=1)).tolist()

    @staticmethod
    def _temperatures(b: Any) -> list[float]:
        ke = kinetic_energy_per_graph(velocities=b.velocities, masses=b.atomic_masses,
                                      batch_idx=b.batch_idx, num_graphs=b.num_graphs).squeeze(-1)
        n = torch.bincount(b.batch_idx, minlength=int(b.num_graphs)).to(ke.dtype)
        return (2.0 * ke / (3.0 * n * KB_EV)).tolist()
