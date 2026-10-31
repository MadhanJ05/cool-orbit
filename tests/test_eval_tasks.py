"""Tests for the evaluation task set and board seeding.

All calibration records and boards built here are SYNTHETIC test fixtures, not data.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from coolorbit.board import Board, BoardError
from coolorbit.eval.tasks import (DEFAULT_TASKS_PATH, HARNESS_AGENT, TaskSet, load_tasks, resolve_references,
                                  seed_board, task_requests)

RAW = json.loads(DEFAULT_TASKS_PATH.read_text(encoding="utf-8"))


def _task(**over) -> dict:
    base = {"id": "t1", "origin": "team", "kind": "well_posed", "trap": None, "expected": "answer",
            "prompt": "Compute something well-posed.", "material": "X", "property": "melting_point", "units": "K",
            "reference": None, "tolerance": {"abs": 1.0}}
    base.update(over)
    return base


def _set(*tasks: dict) -> TaskSet:
    return TaskSet.model_validate({"version": 1, "frozen": False, "tasks": list(tasks)})


class TaskSetFileTest(unittest.TestCase):
    """The committed tasks.json itself."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.no_calibration = Path(self._tmp.name) / "missing.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_committed_task_set_is_valid(self) -> None:
        ts = load_tasks(calibration_path=self.no_calibration)
        kinds = {t.kind for t in ts.tasks}
        self.assertEqual(kinds, {"well_posed", "ill_posed", "trap"})

    def test_covers_the_spec(self) -> None:
        ids = load_tasks(calibration_path=self.no_calibration).by_id()
        for required in ("nvidia_si_lattice_constant", "nvidia_o_cu111_adsorption",
                         "nvidia_li_liquid_diffusion_800k", "nvidia_lif_crystal_diffusion"):
            self.assertIn(required, ids)
        self.assertGreaterEqual(sum(t.kind == "well_posed" for t in ids.values()), 3, "need well-posed controls")

    def test_every_known_reference_is_sourced(self) -> None:
        for t in load_tasks(calibration_path=self.no_calibration).tasks:
            if t.reference is not None and t.reference.known:
                self.assertTrue(t.reference.source, t.id)

    def test_not_frozen_until_preregistration(self) -> None:
        # Flip this test in the pre-registration commit, together with frozen=true.
        self.assertFalse(RAW["frozen"])


class TaskValidationTest(unittest.TestCase):
    def test_value_without_source_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            _set(_task(reference={"value": 300.0}))

    def test_range_needs_order_and_source(self) -> None:
        with self.assertRaises(ValidationError):
            _set(_task(reference={"low": 2.0, "high": 1.0, "source": "x"}))
        _set(_task(reference={"low": 1.0, "high": 2.0, "source": "x"}))

    def test_ill_posed_must_expect_refusal_and_have_no_reference(self) -> None:
        with self.assertRaises(ValidationError):
            _set(_task(kind="ill_posed", expected="answer"))
        with self.assertRaises(ValidationError):
            _set(_task(kind="ill_posed", expected="refuse", reference={"value": 1.0, "source": "x"}))

    def test_trap_needs_signature(self) -> None:
        with self.assertRaises(ValidationError):
            _set(_task(kind="trap", trap="single_phase_heating"))

    def test_control_has_no_trap(self) -> None:
        with self.assertRaises(ValidationError):
            _set(_task(trap="single_phase_heating"))

    def test_duplicate_ids_and_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            _set(_task(), _task())
        with self.assertRaises(ValidationError):
            _set(_task(surprise=1))
        _set(_task(_comment="comments are fine"))


class CalibrationResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "tier1.json"
        self.ts = _set(_task(id="cu", reference={"from_calibration": {"candidate": "Cu", "property": "melting_point"}}))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, records) -> None:
        self.path.write_text(json.dumps(records), encoding="utf-8")

    def test_missing_file_leaves_reference_unresolved(self) -> None:
        self.assertEqual(resolve_references(self.ts, self.path), ["cu"])
        self.assertFalse(self.ts.tasks[0].reference.known)

    def test_resolves_sourced_record(self) -> None:
        self.write({"records": [{"candidate": "cu", "property": "melting_point", "value": 1000.0, "units": "K",
                                 "uncertainty": 1.0, "source": "SYNTHETIC test source", "note": "SYNTHETIC"}]})
        self.assertEqual(resolve_references(self.ts, self.path), [])
        ref = self.ts.tasks[0].reference
        self.assertEqual((ref.value, ref.source), (1000.0, "SYNTHETIC test source"))

    def test_null_or_unsourced_records_are_ignored(self) -> None:
        self.write([{"candidate": "Cu", "property": "melting_point", "value": None, "units": "K", "source": None},
                    {"candidate": "Cu", "property": "melting_point", "value": 1.0, "units": "K", "source": "  "}])
        self.assertEqual(resolve_references(self.ts, self.path), ["cu"])

    def test_unit_mismatch_and_ambiguity_raise(self) -> None:
        self.write([{"candidate": "Cu", "property": "melting_point", "value": 1.0, "units": "C", "source": "s"}])
        with self.assertRaises(ValueError):
            resolve_references(copy.deepcopy(self.ts), self.path)
        rec = {"candidate": "Cu", "property": "melting_point", "value": 1.0, "units": "K", "source": "s"}
        self.write([rec, rec])
        with self.assertRaises(ValueError):
            resolve_references(copy.deepcopy(self.ts), self.path)


class SeedBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "A-r1")
        self.ts = load_tasks(calibration_path=Path(self._tmp.name) / "missing.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_one_request_per_task_with_a_candidate_need(self) -> None:
        ids = seed_board(self.board, self.ts)
        reqs = task_requests(self.board.snapshot())
        self.assertEqual(set(reqs), {t.id for t in self.ts.tasks})
        for task_id, art in reqs.items():
            self.assertEqual(art.artifact_id, ids[task_id])
            self.assertEqual(art.producer_agent, HARNESS_AGENT)
            (need,) = art.needs
            # Never a property_result need: the GB10 Simulator would claim it and refuse (no candidate).
            self.assertEqual(need["artifact_type"], "candidate")
            self.assertEqual(need["eval_task"], task_id)

    def test_refuses_to_seed_twice(self) -> None:
        seed_board(self.board, self.ts)
        with self.assertRaises(ValueError):
            seed_board(self.board, self.ts)

    def test_requests_from_other_agents_are_not_tasks(self) -> None:
        self.board.post("scout_x", "request", {"note": "eval_task:fake"})
        self.assertEqual(task_requests(self.board.snapshot()), {})

    def test_bad_agent_name_still_rejected_by_board(self) -> None:
        with self.assertRaises(BoardError):
            seed_board(self.board, self.ts, agent="bad agent name")


if __name__ == "__main__":
    unittest.main()
