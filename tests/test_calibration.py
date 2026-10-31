"""Tests for the Tier-1 calibration dataset and the Librarian agent.

Fixture records below are SYNTHETIC (made-up names and numbers) and only exercise the rules.
The real dataset test validates data/calibration/tier1.json itself.

Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coolorbit.agents.librarian import (
    DEFAULT_DATA,
    CalibrationError,
    load_calibration,
    post_references,
    validate_dataset,
    validate_record,
)
from coolorbit.board import Board, derive_status

SRC = "SYNTHETIC test source, not a citation"


def rec(**kw) -> dict:
    base = {"candidate": "testium", "family": "test", "property": "melting_point",
            "value": 300.0, "units": "K", "source": SRC}
    base.update(kw)
    return base


class RecordRules(unittest.TestCase):
    def test_valid_record(self) -> None:
        validate_record(rec())

    def test_value_without_source_is_refused(self) -> None:
        for src in (None, "", "   "):
            with self.assertRaises(CalibrationError):
                validate_record(rec(source=src))

    def test_null_value_without_source_is_allowed(self) -> None:
        validate_record(rec(value=None, source=None, note="no verifiable measurement found"))

    def test_wrong_units_refused(self) -> None:
        with self.assertRaises(CalibrationError):
            validate_record(rec(units="C"))
        with self.assertRaises(CalibrationError):
            validate_record(rec(property="latent_heat", value=200.0, units="kJ/kg"))

    def test_unknown_property_and_keys_refused(self) -> None:
        with self.assertRaises(CalibrationError):
            validate_record(rec(property="boiling_point"))
        with self.assertRaises(CalibrationError):
            validate_record(rec(made_up_field=1))

    def test_density_needs_phase(self) -> None:
        with self.assertRaises(CalibrationError):
            validate_record(rec(property="density", value=0.8, units="g/cm3"))
        validate_record(rec(property="density", value=0.8, units="g/cm3", phase="solid"))

    def test_non_positive_or_non_numeric_value_refused(self) -> None:
        for bad in (0, -5.0, "300", True):
            with self.assertRaises(CalibrationError):
                validate_record(rec(value=bad))

    def test_duplicate_candidate_property_refused(self) -> None:
        # The board compares a result with every reference for (candidate, property).
        with self.assertRaises(CalibrationError):
            validate_dataset([rec(), rec(candidate="  Testium ", value=301.0)])
        validate_dataset([rec(), rec(property="latent_heat", value=1.0e5, units="J/kg")])


class LibrarianPosting(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "board")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_posts_links_and_is_idempotent(self) -> None:
        cand = self.board.post("scout_test", "candidate", {"name": "testium", "family": "test", "tier": "trust"})
        records = [rec(), rec(property="latent_heat", value=None, units="J/kg", source=None)]
        out = post_references(self.board, records)
        self.assertEqual((out["posted"], out["null_values"]), (2, 1))
        refs = self.board.snapshot().of_type("reference_value")
        self.assertEqual(len(refs), 2)
        self.assertTrue(all(cand.artifact_id in r.parent_artifact_ids for r in refs))
        again = post_references(self.board, records)
        self.assertEqual((again["posted"], again["skipped_existing"]), (0, 2))

    def test_only_filter(self) -> None:
        out = post_references(self.board, [rec(), rec(candidate="otherium")], only=["Otherium"])
        self.assertEqual(out["posted"], 1)
        self.assertEqual(self.board.snapshot().of_type("reference_value")[0].payload["candidate"], "otherium")

    def test_reference_feeds_trust_rule(self) -> None:
        rules = {"frozen": True, "independent_check_types": ["second_mlip", "classical_md"],
                 "rejection_min_referees": 2,
                 "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 15.0}, "tier1": {"abs": 20.0}}}}
        cand = self.board.post("scout_test", "candidate", {"name": "testium", "family": "test", "tier": "trust"})
        res = self.board.post("simulator_test", "property_result", {
            "candidate": "testium", "family": "test", "tier": "trust", "property": "melting_point",
            "value": 305.0, "units": "K", "note": "SYNTHETIC test data",
            "method": {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence"}},
            parents=[cand.artifact_id])
        before = derive_status(self.board.snapshot(), res, rules)
        self.assertTrue(any("no sourced measured value" in r for r in before.reasons))
        post_references(self.board, [rec()])
        after = derive_status(self.board.snapshot(), res, rules)
        self.assertTrue(any("matches measured 300" in r for r in after.reasons))


class RealDataset(unittest.TestCase):
    def test_tier1_json_is_valid(self) -> None:
        if not DEFAULT_DATA.exists():
            self.skipTest("data/calibration/tier1.json not created yet")
        records = load_calibration(DEFAULT_DATA)
        self.assertGreater(len(records), 0)
        for r in records:
            if r["value"] is not None:
                self.assertTrue(r["source"].strip())

    def test_metals_for_gb10_validation_present(self) -> None:
        if not DEFAULT_DATA.exists():
            self.skipTest("data/calibration/tier1.json not created yet")
        have = {(r["candidate"], r["property"]) for r in json.loads(DEFAULT_DATA.read_text())["records"]
                if r["value"] is not None}
        for metal in ("Cu", "Al"):
            self.assertIn((metal, "melting_point"), have)
            self.assertIn((metal, "latent_heat"), have)


if __name__ == "__main__":
    unittest.main()
