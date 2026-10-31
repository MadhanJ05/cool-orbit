"""Tests for the dashboard exporter on SYNTHETIC demo boards (dashboard/demo.py). Not results.

Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board
from coolorbit.eval.tasks import load_tasks
from dashboard.demo import build_all
from dashboard.export import export

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

    def test_eval_boards_get_scorecard_and_comparison(self) -> None:
        a = self.boards[str(self.paths[1])]
        c = self.boards[str(self.paths[2])]
        self.assertEqual((a["arm"], a["repeat"], c["arm"]), ("A", 1, "C"))
        self.assertGreater(a["scorecard"]["silent_errors"], 0)
        self.assertEqual(c["scorecard"]["silent_errors"], 0)
        cmp = self.bundle["index"]["comparison"]
        self.assertEqual(set(cmp), {"A", "C"})
        self.assertGreater(cmp["C"]["trap_catch_rate"]["mean"], cmp["A"]["trap_catch_rate"]["mean"])
        self.assertFalse(cmp["A"]["cost_recorded_on_all_boards"])

    def test_frozen_flags_are_passed_through(self) -> None:
        self.assertFalse(self.bundle["index"]["rules_frozen"])
        self.assertFalse(self.bundle["index"]["task_set_frozen"])

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


if __name__ == "__main__":
    unittest.main()
