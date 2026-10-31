"""Tests for the alloy Scout, default supercell sizes and the science run (fake GPU runner, no LLM, no spend).

Alloy records below are synthetic fixtures (shape of data/calibration/tier1.json), not data.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from coolorbit.agents.referee_rules import load_audit_rules
from coolorbit.board import Board

try:
    import ase  # noqa: F401

    from coolorbit.agents.alloy_scout import AlloyScout, alloy_seeds, structure_source
    from coolorbit.sim.simulator_agent import MIN_ATOMS, build_job, default_reps
    HAVE_ASE = True
except ImportError:
    HAVE_ASE = False

REPO = Path(__file__).resolve().parents[1]

RECORDS = [  # synthetic fixtures
    {"candidate": "AlloyA", "family": "low_melting_alloy", "formula": "Bi32.5In51Sn16.5 (wt%)",
     "property": "melting_point", "value": 333.7, "units": "K", "source": "fixture"},
    {"candidate": "AlloyA", "family": "low_melting_alloy", "formula": "Bi32.5In51Sn16.5 (wt%)",
     "property": "latent_heat", "value": 27000, "units": "J/kg", "source": "fixture"},
    {"candidate": "AlloyB", "family": "low_melting_alloy", "formula": "In66Bi34 (wt%)",
     "property": "melting_point", "value": 345.0, "units": "K", "source": "fixture"},
    {"candidate": "AlloyB", "family": "low_melting_alloy", "formula": "In66Bi34 (wt%)",
     "property": "latent_heat", "value": None, "units": "J/kg", "source": None},
    {"candidate": "AlloyC", "family": "low_melting_alloy", "formula": "Ga68In22Sn10 (wt%)",
     "property": "melting_point", "value": None, "units": "K", "source": None},
    {"candidate": "Cu", "family": "validation_metal", "formula": "Cu",
     "property": "melting_point", "value": 1357.77, "units": "K", "source": "fixture"},
]


@unittest.skipUnless(HAVE_ASE, "needs ASE (GB10 environment)")
class AlloyScoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "b")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_seeds_come_from_the_calibration_records(self) -> None:
        seeds = {s["name"]: s for s in alloy_seeds(RECORDS)}
        self.assertEqual(sorted(seeds), ["AlloyA", "AlloyB", "AlloyC"])
        self.assertEqual((seeds["AlloyA"]["melting_point_K"], seeds["AlloyA"]["latent_measured"]), (333.7, True))
        self.assertFalse(seeds["AlloyB"]["latent_measured"])
        self.assertIsNone(seeds["AlloyC"]["melting_point_K"])
        self.assertEqual(structure_source("Bi32.5In51Sn16.5 (wt%)"), "alloy:wt:Bi32.5In51Sn16.5")
        self.assertIsNone(structure_source("Field's metal"))

    def test_posts_latent_requests_at_the_measured_melting_point(self) -> None:
        scout = AlloyScout(self.board, records=RECORDS, verbose=False)
        while scout.step():
            pass
        snap = self.board.snapshot()
        cands = {c.payload["name"]: c for c in snap.of_type("candidate")}
        self.assertEqual((cands["AlloyA"].payload["tier"], cands["AlloyB"].payload["tier"]), ("trust", "discovery"))
        self.assertEqual(cands["AlloyA"].needs[0]["temperature_K"], 333.7)
        self.assertEqual(cands["AlloyC"].needs, [])  # no measured melting point: the gap shows
        on = next(o for o in self.board.open_needs() if o.artifact.artifact_id == cands["AlloyA"].artifact_id)
        job = build_job(snap, on)  # the Simulator accepts it as an alloy latent-heat job
        self.assertEqual((job.mode, job.temps), ("alloy-latent", [333.7]))

    def test_only_rejects_unknown_alloys(self) -> None:
        with self.assertRaises(ValueError):
            AlloyScout(self.board, records=RECORDS, only=["Unobtainium"], verbose=False)


@unittest.skipUnless(HAVE_ASE, "needs ASE (GB10 environment)")
class DefaultSizeTest(unittest.TestCase):
    def test_minimums_match_the_referee(self) -> None:
        rules = load_audit_rules()["min_atoms"]
        self.assertEqual(MIN_ATOMS, {"coexistence": rules["two-phase coexistence"], "latent": rules["default"]})

    def test_small_cells_grow_and_cu_is_unchanged(self) -> None:
        self.assertEqual(default_reps(4, "coexistence"), [4, 4, 8])  # Cu: 512 atoms, as before
        self.assertEqual(default_reps(4, "latent"), [4, 4, 4])
        self.assertGreaterEqual(2 * __import__("math").prod(default_reps(2, "coexistence")), 500)  # In
        self.assertGreaterEqual(2 * __import__("math").prod(default_reps(2, "latent")), 200)


_spec = importlib.util.spec_from_file_location("run_science", REPO / "scripts" / "run_science.py")


@unittest.skipUnless(HAVE_ASE, "needs ASE (GB10 environment)")
class ScienceRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sci = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(self.sci)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_plan_lists_gpu_jobs_without_running_anything(self) -> None:
        rows = self.sci.plan(self.sci.science_seeds(["In"], organics=False), ["BiInSn"], None)
        got = {(r["candidate"], r["property"]): r for r in rows}
        self.assertEqual(got[("In", "melting_point")]["gpu"], "GB10")
        self.assertGreaterEqual(got[("In", "melting_point")]["atoms"], 500)
        self.assertEqual(got[("In", "latent_heat")]["mode"], "latent")
        self.assertEqual(got[("BiInSn", "latent_heat")]["mode"], "alloy-latent")

    def test_team_runs_melting_then_latent_heat_until_idle(self) -> None:
        jobs = []

        def fake_gpu(job, out_dir, timeout_s):
            jobs.append(job.mode)
            settings = {"settings": {"mode": job.mode, "model": "medium-mpa-0", "dt_fs": job.dt_fs,
                                     "temps": job.temps, "prod_steps": job.steps()["prod"]}, "n_atoms": job.n_atoms}
            if job.mode == "coexistence":
                return {"status": "ok", "property": "melting_point", "value": 429.0, "uncertainty": 5.0,
                        "units": "K", "flags": []}, settings, out_dir / "run"
            return {"status": "ok", "property": "latent_heat", "value": 28000.0, "uncertainty": 500.0,
                    "units": "J/kg", "flags": []}, settings, out_dir / "run"

        board = Board(Path(self.tmp.name) / "science")
        agents = self.sci.build_agents(board, self.sci.science_seeds(["In"], organics=False), [], preset=None,
                                       llm="never", runner=fake_gpu, runs_root=Path(self.tmp.name) / "runs")
        out = self.sci.run_until_idle(agents, 200)
        self.assertEqual((out["stopped"], out["errors"]), ("idle", []))
        self.assertEqual(jobs, ["coexistence", "latent"])  # latent heat only after a referee passed the melting point
        props = [r.payload["property"] for r in board.snapshot().of_type("property_result")]
        self.assertEqual(props, ["melting_point", "latent_heat"])


if __name__ == "__main__":
    unittest.main()
