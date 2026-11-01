"""Coexistence protocol 2 (opt-in) in coolorbit/sim/melting.py: verdict, monotone bracket, prep gates, melt
temperature, re-analysis, and the unchanged protocol-1 default. CPU only: no model, no dynamics (MD is mocked).

Real-data regression tests use tests/data/coexistence_runs_2026-10-31.json: production PE (rounded to 1e-6 eV),
the production hook's max_mobile_fraction, the saved prep block and the saved protocol-1 verdicts of the seven runs
in docs/results/coexistence-diagnosis.md. Needs the simulation environment (imports coolorbit.sim.melting).
"""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import numpy as np
    import torch
    from ase.build import bulk

    from coolorbit.sim import melting
    HAVE_SIM = True
except ImportError:
    HAVE_SIM = False

FIXTURE = Path(__file__).parent / "data" / "coexistence_runs_2026-10-31.json"
F, S, M = "freezing", "stable", "melting"


def fixture_runs() -> dict:
    return json.loads(FIXTURE.read_text())["runs"]


def rows_from(run: dict) -> list[dict]:
    """Production rows in the live Recorder's format (T_K set to the target: the fixture keeps PE only)."""
    st = run["steps"]
    rows = []
    for i, (t, pe) in enumerate(zip(run["temps"], run["pe"])):
        for k, e in enumerate(pe):
            rows.append({"stage": "production_npt", "step": st["first"] + k * st["every"], "system": i,
                         "T_target": t, "pe_per_atom_eV": e, "T_K": t, "volume_per_atom_A3": 1.0, "Lz_A": 1.0})
    return rows


def analyse(run: dict) -> dict:
    detector = [{"max_mobile_fraction": m} for m in run["max_mobile_fraction"]]
    return melting.analyse_coexistence_v2(rows_from(run), run["temps"], run["dt_fs"], run["pe_threshold"],
                                          detector, run["prep"])


def pt(t, verdict, **kw):
    return {"T_target_K": t, "verdict": verdict, "delta_pe_eV_per_atom": 0.0, **kw}


@unittest.skipUnless(HAVE_SIM, "needs the simulation environment")
class SavedRunsTest(unittest.TestCase):
    """The diagnosis's expected protocol-2 outcomes on the real runs (docs/results/coexistence-diagnosis.md)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.runs = fixture_runs()

    def verdicts(self, label):
        return [p["verdict"] for p in analyse(self.runs[label])["per_temperature"]]

    def test_protocol1_recomputation_matches_the_saved_verdicts(self) -> None:
        for label, run in self.runs.items():
            per_t = melting.per_temperature_v1(rows_from(run), run["temps"], run["pe_threshold"])
            self.assertEqual([p["verdict"] for p in per_t], run["saved_v1"]["verdicts"], label)
            np.testing.assert_allclose([p["delta_pe_eV_per_atom"] for p in per_t], run["saved_v1"]["delta_pe"],
                                       atol=1e-5, err_msg=label)

    def test_cu_a2_freezes_twice_then_melts_twice_and_brackets_tm(self) -> None:
        out = analyse(self.runs["Cu a2"])
        self.assertEqual([p["verdict"] for p in out["per_temperature"]], [F, F, M, M])
        self.assertEqual((out["status"], out["bracket_K"], out["value"], out["uncertainty"]),
                         ("ok", [1375.0, 1400.0], 1387.5, 12.5))

    def test_cu_a1_froze_everywhere_is_above_the_bracket(self) -> None:
        self.assertEqual(self.verdicts("Cu a1"), [F, F, F, F])
        self.assertEqual(analyse(self.runs["Cu a1"])["failure"], "above_bracket")

    def test_bi_and_in_a1_melted_everywhere_are_below_the_bracket(self) -> None:
        for label in ("Bi a1", "In a1"):
            out = analyse(self.runs[label])
            self.assertEqual([p["verdict"] for p in out["per_temperature"]], [M, M, M, M], label)
            self.assertEqual(out["failure"], "below_bracket", label)
            self.assertNotIn("unexpected_superheating_signal", out["flags"], label)

    def test_sn_never_held_a_crystal(self) -> None:
        out = analyse(self.runs["Sn a1"])
        self.assertEqual(out["failure"], "no_two_phase_box")
        self.assertIn("no_two_phase_box", out["flags"])

    def test_in_a2_stops_at_the_liquid_gate(self) -> None:
        gates = melting.prep_gates_from_saved(self.runs["In a2"]["prep"])
        self.assertTrue(gates["liquid_not_prepared"]["fired"])
        self.assertIsNone(gates["crystal_lost_in_prep"]["fired"])  # protocol 1 saved no prep displacements
        for label in ("Cu a1", "Cu a2", "Bi a1", "In a1", "Sn a1"):
            self.assertFalse(melting.prep_gates_from_saved(self.runs[label]["prep"])["liquid_not_prepared"]["fired"])


@unittest.skipUnless(HAVE_SIM, "needs the simulation environment")
class VerdictTest(unittest.TestCase):
    def test_a_transition_inside_protocol1_block0_is_seen_by_protocol2(self) -> None:
        """The crystal half melts between 1.5 and 3 ps: protocol 1's block 0 (2.5-7.2 ps) hides it."""
        rng = np.random.default_rng(0)
        steps = np.arange(500) * 50
        t_ps = steps * 2.0 / 1000
        pe = -3.80 + 0.05 * np.clip((t_ps - 1.5) / 1.5, 0, 1) + rng.normal(0, 3e-4, 500)
        rows = [{"stage": "production_npt", "step": int(s), "system": 0, "pe_per_atom_eV": float(e), "T_K": 900.0}
                for s, e in zip(steps, pe)]
        self.assertEqual(melting.per_temperature_v1(rows, [900.0], 0.002)[0]["verdict"], S)
        change = melting.v2_pe_change(steps, pe, 2.0, 0.002)
        self.assertEqual(change["pe_verdict"], M)
        self.assertAlmostEqual(change["delta_pe_eV_per_atom"], 0.05, delta=0.005)

    def test_noise_excludes_a_drift_in_the_last_half(self) -> None:
        rng = np.random.default_rng(1)
        ramp = np.linspace(0, 0.03, 500) + rng.normal(0, 2e-4, 500)  # a slow transition over the whole run
        self.assertLess(melting.v2_noise(ramp, 50, 11), 3e-4)

    def test_mobility_cross_check(self) -> None:
        v = melting.v2_verdict
        self.assertEqual(v(S, 0.001, 0.95)["verdict"], M)               # fully mobile: melted (even if PE flat)
        self.assertEqual(v(F, -0.01, 0.95)["verdict"], "conflict")      # fully mobile but PE fell
        self.assertEqual(v(S, -0.001, 0.20)["verdict"], F)              # liquid froze before it could move
        self.assertEqual(v(S, +0.001, 0.20)["verdict"], S)              # slow liquid, PE did not fall: no call
        self.assertEqual(v(M, +0.02, 0.20)["verdict"], "conflict")      # barely moved but PE rose
        self.assertEqual(v(S, 0.0, 0.60)["verdict"], S)                 # half liquid: the PE decides
        self.assertEqual(v(M, 0.02, None)["verdict"], M)                # no hook data: PE only
        self.assertEqual((melting.MOBILE_MELTED, melting.MOBILE_FROZEN), (0.9, 0.35))

    def test_no_two_phase_box_needs_every_system_mobile_and_no_pe_rise(self) -> None:
        mobile_flat = [{"max_mobile_fraction": 0.99, "pe_verdict": S}] * 3
        self.assertTrue(melting.no_two_phase_box(mobile_flat))
        self.assertFalse(melting.no_two_phase_box(mobile_flat + [{"max_mobile_fraction": 0.99, "pe_verdict": M}]))
        self.assertFalse(melting.no_two_phase_box(mobile_flat + [{"max_mobile_fraction": 0.5, "pe_verdict": S}]))
        self.assertFalse(melting.no_two_phase_box([{"max_mobile_fraction": None, "pe_verdict": S}]))


@unittest.skipUnless(HAVE_SIM, "needs the simulation environment")
class MonotoneBracketTest(unittest.TestCase):
    def test_hot_stable_above_a_cold_melt_is_inconsistent_in_v2_only(self) -> None:
        per_t = [pt(366, M), pt(408, M), pt(452, S), pt(494, S)]  # In a1 under protocol 1
        self.assertEqual(melting.bracket_verdict(per_t)["failure"], "below_bracket")  # default unchanged
        v2 = melting.bracket_verdict(per_t, protocol=2)
        self.assertEqual(v2["failure"], "inconsistent")
        self.assertEqual(v2["verdict_sequence"], [M, M, S, S])

    def test_non_monotone_and_conflict_are_inconsistent(self) -> None:
        for seq in ([F, F, S, F], [S, F], [F, M, S], [F, "conflict", M]):
            per_t = [pt(1000 + 25 * i, v) for i, v in enumerate(seq)]
            self.assertEqual(melting.bracket_verdict(per_t, protocol=2)["failure"], "inconsistent", seq)

    def test_monotone_outcomes(self) -> None:
        b = lambda seq: melting.bracket_verdict([pt(1000 + 50 * i, v) for i, v in enumerate(seq)], protocol=2)  # noqa
        self.assertEqual(b([F, F, F])["failure"], "above_bracket")
        self.assertEqual(b([M, M])["failure"], "below_bracket")
        self.assertEqual(b([F, S, S])["failure"], "undecided")
        ok = b([F, S, M])
        self.assertEqual((ok["status"], ok["value"], ok["uncertainty"], ok["bracket_K"]), ("ok", 1050.0, 50.0,
                                                                                           [1000, 1100]))
        # input order does not matter: verdicts are sorted by temperature first
        self.assertEqual(melting.bracket_verdict([pt(1100, M), pt(1000, F)], protocol=2)["bracket_K"], [1000, 1100])


@unittest.skipUnless(HAVE_SIM, "needs the simulation environment")
class MeltTemperatureTest(unittest.TestCase):
    def test_tied_to_the_material_not_the_bracket(self) -> None:
        mt = melting.melt_temperature_v2
        self.assertAlmostEqual(mt(545.0, 544.6, None), 1089.2)            # Bi: 2 x Tm (attempt 1 needed 1090 K)
        self.assertAlmostEqual(mt(302.0, 429.75, None), 859.5)            # In a2's lowered bracket: not 483 K
        self.assertAlmostEqual(mt(302.0, 429.75, None), mt(430.0, 429.75, None))  # bracket shift: same T_melt
        self.assertAlmostEqual(mt(1387.5, 600.0, None), 1.6 * 1387.5)     # never below protocol 1's 1.6 x T_prep
        self.assertEqual(mt(545.0, 544.6, 1200.0), 1200.0)                # --t-melt wins
        self.assertAlmostEqual(mt(545.0, None, None), 872.0)              # no T_ref: protocol 1's rule

    def test_escalation_cap_never_below_the_requested_melt(self) -> None:
        s = SimpleNamespace(t_prep=302.0, t_ref=None, t_melt=1090.0)
        self.assertGreaterEqual(melting.MAX_MELT_FACTOR * melting.melt_cap_reference(s), 1090.0)
        s = SimpleNamespace(t_prep=302.0, t_ref=429.75, t_melt=859.5)
        self.assertAlmostEqual(melting.melt_cap_reference(s), 429.75)


def _settings(**kw):
    base = dict(mode="coexistence", label="Cu", model="m", dt_fs=2.0, temps=[1350.0, 1400.0], t_prep=1375.0,
                t_melt=2200.0, equil_steps=10, melt_steps=10, quench_steps=10, prod_steps=10, record_every=5,
                friction_per_fs=0.01, thermostat_fs=100.0, barostat_fs=1000.0, pe_threshold=0.002,
                jump_threshold=0.03, seed=2027)
    base.update(kw)
    return melting.SettingsV2(**base)


class _FakeHook:
    """Stands in for SuperheatingDetectorHook: report() returns the next scripted report list."""

    scripted: list = []

    def __init__(self, *a, **k) -> None:
        self._report = _FakeHook.scripted.pop(0) if _FakeHook.scripted else []

    def report(self):
        return self._report

    def flags(self):
        return {f for r in self._report for f in r.get("flags", [])}


@unittest.skipUnless(HAVE_SIM, "needs the simulation environment")
class PrepGateTest(unittest.TestCase):
    """prepare_coexistence_v2 / run_coexistence_v2 with every MD stage mocked: the gates stop before production."""

    def _prep(self, solid_mobile: float, melted: float, flags=()):
        atoms = bulk("Cu", "fcc", a=3.61, cubic=True).repeat((2, 2, 4))
        batch = SimpleNamespace(positions=torch.zeros(len(atoms), 3), cell=torch.eye(3)[None] * 10)
        calls = []
        _FakeHook.scripted = [[{"max_mobile_fraction": solid_mobile, "flags": list(flags)}]]
        with mock.patch.object(melting, "SuperheatingDetectorHook", _FakeHook), \
             mock.patch.object(melting, "make_data", return_value=None), \
             mock.patch.object(melting.Batch, "from_data_list", return_value=batch), \
             mock.patch.object(melting, "npt", side_effect=lambda *a, **k: calls.append(("npt", a[4]))), \
             mock.patch.object(melting, "nvt", side_effect=lambda *a, **k: calls.append(("quench",))), \
             mock.patch.object(melting, "run_stage", side_effect=lambda dyn, b, *a, **k: b), \
             mock.patch.object(melting, "batch_to_atoms", return_value=[atoms]), \
             mock.patch.object(melting, "displaced_fraction", return_value=0.0), \
             mock.patch.object(melting, "melt_until_liquid",
                               side_effect=lambda b, *a: calls.append(("melt", a[-1])) or (b, melted, [2750.0])), \
             redirect_stdout(io.StringIO()):
            _, prep = melting.prepare_coexistence_v2(atoms, None, _settings(vc_relax_steps=0, t_ref=1357.77,
                                                                            t_melt=2715.54), [])
        return prep, calls

    def test_crystal_lost_in_prep_stops_before_melting(self) -> None:
        prep, calls = self._prep(solid_mobile=0.98, melted=0.95)
        self.assertEqual((prep["gate"], prep["stopped_after"]), ("crystal_lost_in_prep", "prep_solid_npt"))
        self.assertEqual(calls, [("npt", "anisotropic")])  # no melt, no quench

    def test_superheating_flag_in_prep_also_stops(self) -> None:
        prep, _ = self._prep(solid_mobile=0.3, melted=0.95, flags=["superheating_risk", "superheating_observed"])
        self.assertEqual(prep["gate"], "crystal_lost_in_prep")

    def test_liquid_not_prepared_stops_before_quench_and_production(self) -> None:
        prep, calls = self._prep(solid_mobile=0.01, melted=0.56)
        self.assertEqual((prep["gate"], prep["stopped_after"]), ("liquid_not_prepared", "prep_melt_nvt"))
        self.assertNotIn(("quench",), calls)
        self.assertEqual(prep["melted_half_moved_fraction_after_melt"], 0.56)

    def test_good_box_passes_both_gates(self) -> None:
        prep, calls = self._prep(solid_mobile=0.01, melted=0.92)
        self.assertNotIn("gate", prep)
        self.assertIn(("quench",), calls)
        self.assertGreaterEqual(melting.MAX_MELT_FACTOR * dict(c for c in calls if c[0] == "melt")["melt"], 2715.54)

    def test_run_stops_with_the_gate_as_failure(self) -> None:
        gate_prep = {"gate": "liquid_not_prepared", "stopped_after": "prep_melt_nvt"}
        with mock.patch.object(melting, "prepare_coexistence_v2", return_value=("batch", gate_prep)), \
             mock.patch.object(melting, "npt", side_effect=AssertionError("production must not start")):
            result, final = melting.run_coexistence_v2(None, None, _settings(), [])
        self.assertEqual((result["status"], result["failure"], result["flags"]),
                         ("failed", "liquid_not_prepared", ["liquid_not_prepared"]))
        self.assertEqual((result["protocol_version"], final), (2, "batch"))


@unittest.skipUnless(HAVE_SIM, "needs the simulation environment")
class MainAndDefaultTest(unittest.TestCase):
    """main() with the GPU, the model and the runners mocked: protocol 1 writes what it wrote before."""

    V1_SETTINGS_KEYS = ["mode", "label", "model", "dt_fs", "temps", "t_prep", "t_melt", "equil_steps", "melt_steps",
                        "quench_steps", "prod_steps", "record_every", "friction_per_fs", "thermostat_fs",
                        "barostat_fs", "pe_threshold", "jump_threshold", "seed", "liquid_expansion", "composition",
                        "solid_atoms"]
    V1_ARGS_KEYS = ["mode", "element", "structure", "composition", "liquid_atoms", "solid_atoms", "a",
                    "structure_file", "reps", "label", "temps", "temperature", "t_prep", "t_melt", "model", "dt",
                    "equil_steps", "melt_steps", "quench_steps", "prod_steps", "record_every", "liquid_expansion",
                    "pe_threshold", "jump_threshold", "seed", "quick", "board", "agent", "candidate_id", "out"]

    def _main(self, extra: list[str]):
        used = {}

        def fake(name):
            def run(atoms, model, s, rows):
                used["runner"], used["settings"] = name, s
                rows.append({"stage": "x", "step": 0})
                return {"status": "failed", "failure": "undecided", "reason": "fake", "flags": []}, None
            return run

        with tempfile.TemporaryDirectory() as tmp:
            argv = ["melting", "coexistence", "--element", "Cu", "--a", "3.61", "--temps", "1350", "1400",
                    "--quick", "--out", tmp, *extra]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(melting.torch.cuda, "is_available", return_value=True), \
                 mock.patch.object(melting.torch.cuda, "get_device_name", return_value="fake"), \
                 mock.patch.object(melting, "load_model", return_value=None), \
                 mock.patch.object(melting, "run_coexistence", fake("v1")), \
                 mock.patch.object(melting, "run_coexistence_v2", fake("v2")), \
                 mock.patch.object(melting, "batch_to_atoms", return_value=[]), \
                 redirect_stdout(io.StringIO()):
                melting.main()
            (run_dir,) = Path(tmp).iterdir()
            return used, json.loads((run_dir / "settings.json").read_text())

    def test_default_is_protocol_1_with_unchanged_settings(self) -> None:
        used, settings = self._main([])
        self.assertEqual(used["runner"], "v1")
        self.assertIs(type(used["settings"]), melting.Settings)
        self.assertEqual(list(settings["settings"]), self.V1_SETTINGS_KEYS)
        self.assertEqual(list(settings["args"]), self.V1_ARGS_KEYS)
        self.assertAlmostEqual(settings["settings"]["t_melt"], 1.6 * 1375.0)
        self.assertEqual(settings["settings"]["seed"], 2026)

    def test_protocol_2_records_version_commit_and_material_melt(self) -> None:
        used, settings = self._main(["--protocol", "2", "--t-ref", "1357.77", "--seed", "2027"])
        self.assertEqual(used["runner"], "v2")
        st = settings["settings"]
        self.assertEqual((st["protocol_version"], st["t_ref"], st["seed"]), (2, 1357.77, 2027))
        self.assertEqual((st["vc_relax_steps"], st["prep_coupling"]), (300, "anisotropic"))
        self.assertAlmostEqual(st["t_melt"], 2 * 1357.77)
        self.assertEqual(st["git_commit"], melting.git_commit())
        self.assertEqual(settings["args"]["protocol"], 2)

    def test_v2_options_are_rejected_without_protocol_2(self) -> None:
        for extra in (["--t-ref", "1300"], ["--prep-coupling", "isotropic"], ["--vc-relax-steps", "0"]):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
                self._main(extra)

    def test_protocol_2_is_coexistence_only(self) -> None:
        argv = ["melting", "heating", "--element", "Cu", "--a", "3.61", "--temps", "1300", "--protocol", "2"]
        with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit), \
             mock.patch("sys.stderr", io.StringIO()):
            melting.main()


@unittest.skipUnless(HAVE_SIM, "needs the simulation environment")
class GitCommitTest(unittest.TestCase):
    def test_reads_branch_ref_packed_ref_and_detached_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            git = Path(tmp) / ".git"
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n")
            (git / "refs" / "heads" / "main").write_text("abc123\n")
            self.assertEqual(melting.git_commit(Path(tmp)), "abc123")
            (git / "refs" / "heads" / "main").unlink()
            (git / "packed-refs").write_text("# pack-refs\ndef456 refs/heads/main\n")
            self.assertEqual(melting.git_commit(Path(tmp)), "def456")
            (git / "HEAD").write_text("0123abcd\n")
            self.assertEqual(melting.git_commit(Path(tmp)), "0123abcd")
            self.assertIsNone(melting.git_commit(Path(tmp) / "nowhere"))


@unittest.skipUnless(HAVE_SIM, "needs the simulation environment")
class ReanalyzeTest(unittest.TestCase):
    """reanalyze on a run directory rebuilt from the fixture: same answers, nothing written into the run dir."""

    def _run_dir(self, tmp: Path, label: str) -> Path:
        run = fixture_runs()[label]
        d = tmp / f"run_{label.replace(' ', '_')}"
        d.mkdir()
        (d / "settings.json").write_text(json.dumps({"settings": {"mode": "coexistence", "temps": run["temps"],
                                                                  "dt_fs": run["dt_fs"],
                                                                  "pe_threshold": run["pe_threshold"]}}))
        (d / "result.json").write_text(json.dumps({
            "prep": run["prep"], "status": "failed", "failure": run["saved_v1"]["failure"],
            "per_temperature": [{"verdict": v} for v in run["saved_v1"]["verdicts"]],
            "superheating_detector": [{"max_mobile_fraction": m} for m in run["max_mobile_fraction"]]}))
        rows = rows_from(run)
        with open(d / "timeseries.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        return d

    def test_v2_reanalysis_and_no_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = self._run_dir(tmp, "Cu a2")
            before = sorted((p.name, p.stat().st_mtime_ns) for p in d.iterdir())
            report = melting.reanalyze(d, protocol=2)
            self.assertEqual((report["result"]["status"], report["result"]["bracket_K"]), ("ok", [1375.0, 1400.0]))
            self.assertEqual(report["saved"]["verdicts"], [S, S, S, S])
            out = tmp / "cu_a2_v2.json"
            with mock.patch.object(sys, "argv", ["melting", "reanalyze", str(d), "--protocol", "2", "--out", str(out)]), \
                 redirect_stdout(io.StringIO()) as printed:
                melting.main()
            self.assertIn("1387.5 +/- 12.5 K", printed.getvalue())
            self.assertEqual(json.loads(out.read_text())["result"]["value"], 1387.5)
            self.assertEqual(sorted((p.name, p.stat().st_mtime_ns) for p in d.iterdir()), before)
            for bad in (str(d / "x.json"), str(out)):  # inside the run dir / existing file
                with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()), \
                     redirect_stdout(io.StringIO()):
                    melting.reanalyze_main([str(d), "--protocol", "2", "--out", bad])

    def test_gate_reported_with_informational_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = melting.reanalyze(self._run_dir(Path(tmp), "In a2"), protocol=2)
        self.assertEqual(report["result"]["failure"], "liquid_not_prepared")
        self.assertEqual(len(report["per_temperature"]), 4)
        self.assertIn("production_analysis_informational", report)

    def test_protocol_1_reanalysis_reproduces_saved_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = melting.reanalyze(self._run_dir(Path(tmp), "In a1"), protocol=1)
        self.assertEqual([p["verdict"] for p in report["per_temperature"]], [M, M, S, S])
        self.assertEqual(report["result"]["failure"], "below_bracket")


if __name__ == "__main__":
    unittest.main()
