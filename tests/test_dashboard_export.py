"""Tests for the dashboard exporter on SYNTHETIC demo boards (dashboard/demo.py). Not results.

Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board
from coolorbit.eval.metrics import REFERENCE_RULE
from coolorbit.eval.tasks import DEFAULT_TASKS_PATH, TaskSet, load_tasks, seed_board
from dashboard.demo import MARKER, build_all, build_eval
from dashboard.export import _scored_task_ids, export

RULES = {
    "frozen": False,
    "independent_check_types": ["second_mlip", "classical_md"],
    "rejection_min_referees": 2,
    "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 20.0}, "tier1": {"abs": 25.0}},
                   "latent_heat": {"units": ["J/kg"], "agree": {"rel": 0.15}, "tier1": {"rel": 0.20}}},
}


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.paths = build_all(tmp / "boards")
        cls.out = tmp / "out"
        cls.tasks = load_tasks(calibration_path=tmp / "none.json")
        cls.bundle = export(cls.paths, cls.out, rules=RULES, task_set=cls.tasks)
        cls.boards = {b["path"]: b for b in cls.bundle["boards"].values()}
        cls.collective = cls.boards[str(cls.paths[0])]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_files_written(self) -> None:
        names = {p.name for p in self.out.iterdir()}
        self.assertTrue({"index.json", "data.js"} <= names)
        self.assertEqual(len([n for n in names if n not in ("index.json", "data.js")]), 3)
        js = (self.out / "data.js").read_text()
        self.assertTrue(js.startswith("window.COOL_ORBIT_DATA = "))
        payload = json.loads(js[len("window.COOL_ORBIT_DATA = "):].rstrip().rstrip(";"))
        self.assertEqual(payload["index"]["boards"], json.loads((self.out / "index.json").read_text())["boards"])

    def test_statuses_are_derived_with_reasons(self) -> None:
        by = {(r["candidate"], r["property"], r["method"]["protocol"], r["agent"]): r for r in self.collective["results"]}
        statuses = sorted(r["status"] for r in self.collective["results"])
        self.assertEqual(statuses, sorted(["trusted", "simulated", "flagged", "under_review", "rejected"]))
        for r in self.collective["results"]:
            self.assertTrue(r["reasons"], f"{r['id']} has no reasons")
        flagged = by[("DEMO-wax-1", "melting_point", "single-phase heating", "simulator_gb10")]
        self.assertEqual(len(flagged["superseded_by"]), 1)

    def test_synthetic_boards_are_marked(self) -> None:
        self.assertTrue(all(b["synthetic"] for b in self.bundle["boards"].values()))
        self.assertTrue(self.bundle["index"]["any_synthetic"])

    def test_real_board_is_not_marked_synthetic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            b = Board(Path(tmp) / "real")
            b.post("scout_x", "candidate", {"name": "n-eicosane", "family": "paraffin", "tier": "trust"})
            bundle = export([b.root], Path(tmp) / "out", rules=RULES, task_set=self.tasks)
            (only,) = bundle["boards"].values()
            self.assertFalse(only["synthetic"])
            self.assertIsNone(only["scorecard"])
            self.assertIsNone(bundle["index"]["comparison"])

    def test_ranking_warns_about_untrusted_citations(self) -> None:
        entries = {e["candidate"]: e for e in self.collective["ranking"]["entries"]}
        self.assertEqual(entries["DEMO-metal-1"]["untrusted_result_ids"], [])
        self.assertEqual(len(entries["DEMO-wax-1"]["untrusted_result_ids"]), 1)
        self.assertEqual([e["rank"] for e in self.collective["ranking"]["entries"]], [1, 2])

    def test_needs_runs_and_lineage(self) -> None:
        self.assertEqual(self.collective["counts"]["open_needs"], len(self.collective["open_needs"]))
        self.assertEqual(sorted(r["status"] for r in self.collective["run_reports"]), ["failed", "refused"])
        lin = self.collective["lineage"]
        for aid, a in lin.items():
            for p in a["parents"]:
                self.assertIn(aid, lin[p]["children"], "parent/child links must be symmetric")
        self.assertEqual(len(self.collective["proposed"]), 1)

    def test_eval_boards_get_scorecard(self) -> None:
        a = self.boards[str(self.paths[1])]
        c = self.boards[str(self.paths[2])]
        self.assertEqual((a["arm"], a["repeat"], c["arm"]), ("A", 1, "C"))
        self.assertEqual(a["run"], c["run"])
        self.assertGreater(a["scorecard"]["silent_errors"], 0)
        self.assertEqual(c["scorecard"]["silent_errors"], 0)

    def test_synthetic_boards_are_excluded_from_the_comparison_by_default(self) -> None:
        self.assertIsNone(self.bundle["index"]["comparison"])
        self.assertEqual(len(self.bundle["index"]["excluded_from_comparison"]), 2)

    def test_demo_comparison_is_marked_synthetic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = export(self.paths, tmp, rules=RULES, task_set=self.tasks, include_synthetic_in_comparison=True)
        (run,) = bundle["index"]["comparison"]
        self.assertTrue(run["synthetic"])
        self.assertEqual(set(run["arms"]), {"A", "C"})
        self.assertGreater(run["arms"]["C"]["trap_catch_rate"]["mean"], run["arms"]["A"]["trap_catch_rate"]["mean"])
        self.assertFalse(run["arms"]["A"]["cost_recorded_on_all_boards"])
        # One board per repeat (no run records): every seeded task counts as finished, so each repeat is complete
        # and the complete-repeats headline equals the all-repeats one. No demo answer copies a measured value.
        self.assertEqual({(d["arm"], d["complete"]) for d in run["repeats"]}, {("A", True), ("C", True)})
        for key in ("pre_registered", "post_hoc"):
            self.assertEqual(run["scoring"][key]["A"]["repeats"], 1)
            self.assertEqual(run["scoring"][key]["C"]["trap_catch_rate"], run["arms"]["C"]["trap_catch_rate"])
        self.assertEqual(run["scoring"]["post_hoc_rules"], [REFERENCE_RULE])

    def test_eval_boards_carry_both_scorecards(self) -> None:
        a = self.boards[str(self.paths[1])]
        self.assertIsNotNone(a["scorecard_post_hoc"])
        self.assertEqual(a["scorecard"]["post_hoc_rules"], [])
        self.assertEqual(len(a["scorecard_post_hoc"]["post_hoc_rules"]), 1)
        self.assertIsNone(self.collective["scorecard_post_hoc"], "a science board has no eval tasks")
        self.assertIsNone(a["finished"], "single-board repeats have no run records")

    def test_repeats_are_grouped_by_run_never_pooled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            r1, r2 = tmp / "run1" / "A-r1", tmp / "run2" / "A-r1"
            build_eval(r1, "A")
            build_eval(r2, "C")  # different behavior, same arm label A
            bundle = export([r1, r2], tmp / "out", rules=RULES, task_set=self.tasks,
                            include_synthetic_in_comparison=True)
        runs = bundle["index"]["comparison"]
        self.assertEqual(len(runs), 2)
        self.assertEqual([r["arms"]["A"]["repeats"] for r in runs], [1, 1])

    def test_frozen_flags_are_passed_through(self) -> None:
        self.assertEqual(self.bundle["index"]["rules_frozen"], RULES["frozen"])
        actual = json.loads(DEFAULT_TASKS_PATH.read_text(encoding="utf-8"))["frozen"]
        self.assertEqual(self.bundle["index"]["task_set_frozen"], actual)

    def test_export_is_read_only(self) -> None:
        before = {p: p.read_bytes() for path in self.paths for p in sorted(path.rglob("*")) if p.is_file()}
        with tempfile.TemporaryDirectory() as tmp:
            export(self.paths, tmp, rules=RULES, task_set=self.tasks)
        after = {p: p.read_bytes() for path in self.paths for p in sorted(path.rglob("*")) if p.is_file()}
        self.assertEqual(before, after)

    def test_not_a_board_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                export([Path(tmp) / "nope"], Path(tmp) / "out", rules=RULES, task_set=self.tasks)


class PostHocAndPartialRepeatTest(unittest.TestCase):
    """SYNTHETIC per-task boards in the runners' layout: one finished task whose shipped answer copies a measured
    value instead of simulating (arm B in cmp-1 repeat 1), and one task still running (no run_record.json)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.tasks = load_tasks(calibration_path=tmp / "none.json")
        rep = tmp / "eval" / "run1" / "B-r1"
        al = Board(rep / "tier1_al_melting")
        one = TaskSet(version=1, frozen=False, tasks=[cls.tasks.by_id()["tier1_al_melting"]])
        req = seed_board(al, one)["tier1_al_melting"]
        cand = al.post("worker", "candidate", {"name": "SYNTHETIC-Al", "family": "t", "tier": "discovery"},
                       fulfills=(req, 0))
        al.post("worker", "property_result", {
            "candidate": "SYNTHETIC-Al", "family": "t", "tier": "discovery", "property": "melting_point",
            "value": 933.45, "units": "K", "note": "SYNTHETIC test: a copied measured value, no simulation",
            "method": {"engine": "none (measured reference)", "model": "none", "protocol": "copied from a file"}},
            parents=[cand.artifact_id])
        (al.root / "run_record.json").write_text("{}", encoding="utf-8")
        running = Board(rep / "nvidia_lif_crystal_diffusion")
        seed_board(running, TaskSet(version=1, frozen=False, tasks=[cls.tasks.by_id()["nvidia_lif_crystal_diffusion"]]))
        cls.bundle = export([tmp / "eval"], tmp / "out", rules=RULES, task_set=cls.tasks,
                            include_synthetic_in_comparison=True)
        (cls.cmp_run,) = cls.bundle["index"]["comparison"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_finished_comes_from_the_run_record(self) -> None:
        finished = {e["task_id"]: e["finished"] for e in self.bundle["index"]["boards"]}
        self.assertEqual(finished, {"tier1_al_melting": True, "nvidia_lif_crystal_diffusion": False})

    def test_partial_repeat_is_reported_and_kept_out_of_the_headline(self) -> None:
        (rep,) = self.cmp_run["repeats"]
        self.assertEqual((rep["arm"], rep["repeat"], rep["complete"]), ("B", 1, False))
        self.assertEqual((rep["tasks_seeded"], rep["tasks_finished"]), (2, 1))
        self.assertEqual(rep["in_progress"], ["nvidia_lif_crystal_diffusion"])
        expected = _scored_task_ids(self.tasks)
        self.assertEqual(rep["tasks_expected"], len(expected))
        self.assertEqual(set(rep["not_started"]), set(expected) - {"tier1_al_melting", "nvidia_lif_crystal_diffusion"})
        self.assertEqual(rep["pre"]["n_tasks"], 1, "scored over the finished task only")
        for key in ("pre_registered", "post_hoc"):
            self.assertEqual(self.cmp_run["scoring"][key]["B"]["repeats"], 0, "an incomplete repeat is not in the headline")
        self.assertEqual(self.cmp_run["arms"]["B"]["repeats"], 1, "the all-repeats summary is unchanged")

    def test_post_hoc_rule_scores_a_copied_measurement(self) -> None:
        (rep,) = self.cmp_run["repeats"]
        self.assertEqual(rep["pre"]["trap_catch_rate"], 1.0, "pre-registered: counted as an avoided trap")
        self.assertEqual(rep["post"]["trap_catch_rate"], 0.0, "post-hoc: a reported reference is not a catch")
        self.assertEqual((rep["pre"]["reported_reference"], rep["post"]["reported_reference"]), (0, 1))
        al = next(b for b in self.bundle["boards"].values() if b["task_id"] == "tier1_al_melting")
        pre = {o["task_id"]: o for o in al["scorecard"]["outcomes"]}["tier1_al_melting"]
        post = {o["task_id"]: o for o in al["scorecard_post_hoc"]["outcomes"]}["tier1_al_melting"]
        self.assertEqual((pre["disposition"], post["disposition"]), ("answered", "reported_reference"))
        self.assertEqual(post["trap_outcome"], "reported_reference")


class DemoSafetyTest(unittest.TestCase):
    def test_refuses_to_delete_a_folder_it_did_not_create(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "boards"
            Board(real / "real-run").post("scout_x", "candidate", {"name": "x", "family": "f", "tier": "trust"})
            with self.assertRaises(FileExistsError):
                build_all(real)
            self.assertTrue((real / "real-run" / "artifacts").is_dir(), "real board must survive")

    def test_rebuilds_its_own_folder_and_accepts_an_empty_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            build_all(root)
            self.assertTrue((root / MARKER).is_file())
            (root / "stale").mkdir()
            build_all(root)  # marker present: safe to rebuild
            self.assertFalse((root / "stale").exists())


if __name__ == "__main__":
    unittest.main()
