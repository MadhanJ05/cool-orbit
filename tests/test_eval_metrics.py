"""Tests for coolorbit.eval.metrics on small SYNTHETIC boards.

Every number, reference and source below is a synthetic test fixture, not data.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board
from coolorbit.eval.metrics import main, read_costs, score_board, summarize_arm
from coolorbit.eval.tasks import TaskSet, seed_board

SRC = "SYNTHETIC test source"
RULES = {
    "frozen": True,
    "independent_check_types": ["second_mlip", "classical_md"],
    "rejection_min_referees": 2,
    "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 15.0}, "tier1": {"abs": 20.0}}},
}
GOOD = {"engine": "ALCHEMI", "model": "EAM-test", "protocol": "two-phase coexistence", "ensemble": "NPT"}
SINGLE = {"engine": "ALCHEMI", "model": "EAM-test", "protocol": "single-phase heating", "ensemble": "NPT"}

TASKS = {
    "version": 1, "frozen": False, "tasks": [
        {"id": "ctrl", "origin": "team", "kind": "well_posed", "expected": "answer", "prompt": "SYNTHETIC control task",
         "material": "Xm", "property": "melting_point", "units": "K",
         "reference": {"value": 1000.0, "source": SRC}, "tolerance": {"from_trust_rules": "tier1"}},
        {"id": "trap", "origin": "team", "kind": "trap", "trap": "single_phase_heating",
         "trap_signature": [{"field": "method.protocol", "contains": "single-phase"}],
         "expected": "answer", "prompt": "SYNTHETIC trap task", "material": "Ym", "property": "melting_point",
         "units": "K", "reference": {"value": 900.0, "source": SRC}, "tolerance": {"from_trust_rules": "tier1"}},
        {"id": "ill", "origin": "team", "kind": "ill_posed", "trap": "unmeasurable_premise", "expected": "refuse",
         "prompt": "SYNTHETIC ill-posed task", "material": "Zc", "property": "self_diffusion", "units": "cm2/s"},
        {"id": "rng", "origin": "team", "kind": "well_posed", "expected": "answer", "prompt": "SYNTHETIC range task",
         "material": "O/Xs", "property": "adsorption_energy", "units": "eV",
         "reference": {"low": -4.6, "high": -4.46, "source": SRC}, "tolerance": {"abs": 0.3}},
        {"id": "fac", "origin": "team", "kind": "well_posed", "expected": "answer", "prompt": "SYNTHETIC factor task",
         "material": "Wl", "property": "self_diffusion", "units": "cm2/s",
         "reference": {"value": 1e-4, "source": SRC}, "tolerance": {"factor": 2.0}},
        {"id": "noref", "origin": "team", "kind": "well_posed", "expected": "answer", "prompt": "SYNTHETIC no-ref task",
         "material": "Vm", "property": "melting_point", "units": "K", "reference": None,
         "tolerance": {"from_trust_rules": "tier1"}},
    ]}


class MetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ts = TaskSet.model_validate(TASKS)
        self.board = Board(self.tmp / "A-r1")
        self.req = seed_board(self.board, self.ts)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers (all posted data is SYNTHETIC) ---------------------------
    def cand(self, task_id: str, agent: str = "single_agent"):
        return self.board.post(agent, "candidate", {"name": task_id, "family": "test", "tier": "discovery",
                                                     "rationale": "SYNTHETIC"},
                               fulfills=(self.req[task_id], 0))

    def result(self, parent, task_id: str, value: float, *, method=GOOD, agent="single_agent", flags=(),
               units=None, prop=None):
        task = self.ts.by_id()[task_id]
        payload = {"candidate": task_id, "family": "test", "tier": "discovery",
                   "property": prop or task.property, "value": value, "units": units or task.units,
                   "method": method, "flags": list(flags), "note": "SYNTHETIC test data"}
        return self.board.post(agent, "property_result", payload, parents=[parent.artifact_id])

    def refuse(self, parent, agent="single_agent"):
        return self.board.post(agent, "run_report", {"status": "refused", "task": "SYNTHETIC",
                                                     "reason": "SYNTHETIC: premise not measurable"},
                               parents=[parent.artifact_id])

    def check(self, res, agent, check_type="method_audit", verdict="fail"):
        return self.board.post(agent, "check", {"check_type": check_type, "verdict": verdict},
                               parents=[res.artifact_id])

    def score(self):
        s = score_board(self.board, self.ts, RULES)
        return s, {o.task_id: o for o in s.outcomes}

    # -- traps ---------------------------------------------------------------
    def test_naive_single_agent_ships_silent_errors(self) -> None:
        self.result(self.cand("trap"), "trap", 1010.0, method=SINGLE)
        self.result(self.cand("ill"), "ill", 1e-9)
        s, o = self.score()
        self.assertEqual(o["trap"].trap_outcome, "missed")
        self.assertTrue(o["trap"].silent_error)
        self.assertIn("single_phase_heating", " ".join(o["trap"].reasons))
        self.assertEqual(o["ill"].trap_outcome, "missed")
        self.assertEqual((s.trap_missed, s.trap_catch_rate, s.silent_errors), (2, 0.0, 2))

    def test_doing_nothing_is_not_a_catch(self) -> None:
        s, o = self.score()  # seeded board, the arm posted nothing
        self.assertEqual((o["trap"].trap_outcome, o["ill"].trap_outcome), ("no_answer", "no_answer"))
        self.assertEqual((s.trap_catch_rate, s.trap_no_answer), (0.0, 2))
        self.assertEqual((s.false_alarms, s.controls_no_answer), (0, 4))

    def test_trap_avoided_by_a_clean_answer(self) -> None:
        self.result(self.cand("trap"), "trap", 905.0)
        _, o = self.score()
        self.assertEqual((o["trap"].fell_in, o["trap"].trap_outcome), (False, "avoided"))

    def test_self_refusal_counts_for_a_single_agent(self) -> None:
        self.refuse(self.cand("ill"))
        s, o = self.score()
        self.assertEqual((o["ill"].disposition, o["ill"].trap_outcome), ("refused", "avoided"))

    def test_negative_premise_check_counts_as_refusal(self) -> None:
        c = self.cand("ill", agent="scout_x")
        self.board.post("referee_premise", "check", {"check_type": "premise_check", "verdict": "fail"},
                        parents=[c.artifact_id])
        _, o = self.score()
        self.assertTrue(o["ill"].refused)
        self.assertEqual(o["ill"].trap_outcome, "avoided")

    def test_rerun_after_trap_is_caught_and_shipped(self) -> None:
        bad = self.result(self.cand("trap"), "trap", 1010.0, method=SINGLE)
        self.result(bad, "trap", 905.0)  # rerun: parent = the old result
        _, o = self.score()
        t = o["trap"]
        self.assertEqual((t.trap_outcome, t.shipped_value, t.wrong, t.silent_error), ("caught", 905.0, False, False))
        self.assertAlmostEqual(t.error, 5.0)

    def test_referee_flag_turns_a_trap_into_a_catch(self) -> None:
        bad = self.result(self.cand("trap", agent="scout_y"), "trap", 1010.0, method=SINGLE, agent="simulator_1")
        self.check(bad, "referee_opus")
        _, o = self.score()
        self.assertEqual((o["trap"].shipped_status, o["trap"].confident, o["trap"].trap_outcome),
                         ("flagged", False, "caught"))

    def test_self_check_does_not_unflag_or_flag(self) -> None:
        bad = self.result(self.cand("trap"), "trap", 1010.0, method=SINGLE)
        self.check(bad, "single_agent")  # ignored by the trust rule; must not count as a catch
        _, o = self.score()
        self.assertEqual(o["trap"].trap_outcome, "missed")

    def test_own_flag_on_trap_result_is_a_catch(self) -> None:
        self.result(self.cand("trap"), "trap", 1010.0, method=SINGLE, flags=["superheating_risk"])
        _, o = self.score()
        self.assertEqual(o["trap"].trap_outcome, "caught")

    # -- controls / false alarms ----------------------------------------------
    def test_correct_confident_control_is_not_a_false_alarm(self) -> None:
        self.result(self.cand("ctrl"), "ctrl", 1015.0)
        s, o = self.score()
        self.assertEqual((o["ctrl"].wrong, o["ctrl"].false_alarm, o["ctrl"].disposition), (False, False, "answered"))
        self.assertEqual(s.false_alarms, 0)

    def test_refusing_a_control_is_a_false_alarm(self) -> None:
        self.refuse(self.cand("ctrl"))
        s, o = self.score()
        self.assertTrue(o["ctrl"].false_alarm)
        self.assertAlmostEqual(s.false_alarm_rate, 1 / 4)

    def test_doubting_a_correct_answer_is_a_false_alarm_but_doubting_a_wrong_one_is_not(self) -> None:
        self.result(self.cand("ctrl"), "ctrl", 1015.0, flags=["paranoid"])
        self.result(self.cand("noref"), "noref", 1.0, flags=["paranoid"])  # unknown truth: not counted
        _, o = self.score()
        self.assertTrue(o["ctrl"].false_alarm)
        self.assertFalse(o["noref"].false_alarm)

    def test_wrong_flagged_control_is_a_true_positive(self) -> None:
        self.result(self.cand("ctrl"), "ctrl", 1100.0, flags=["drift"])
        _, o = self.score()
        self.assertEqual((o["ctrl"].wrong, o["ctrl"].false_alarm, o["ctrl"].silent_error), (True, False, False))

    # -- error arithmetic ------------------------------------------------------
    def test_range_reference_uses_distance_to_range(self) -> None:
        self.result(self.cand("rng"), "rng", -4.5)
        _, o = self.score()
        self.assertEqual((o["rng"].error, o["rng"].wrong), (0.0, False))

    def test_range_reference_outside_tolerance(self) -> None:
        self.result(self.cand("rng"), "rng", -5.0)
        _, o = self.score()
        self.assertAlmostEqual(o["rng"].error, -0.4)
        self.assertTrue(o["rng"].wrong)

    def test_factor_tolerance(self) -> None:
        self.result(self.cand("fac"), "fac", 1.9e-4)
        _, o = self.score()
        self.assertFalse(o["fac"].wrong)

    def test_factor_tolerance_outside(self) -> None:
        self.result(self.cand("fac"), "fac", 0.3e-4)
        _, o = self.score()
        self.assertTrue(o["fac"].wrong)

    def test_unit_mismatch_is_wrong_and_the_trust_rule_flags_it(self) -> None:
        self.result(self.cand("ctrl"), "ctrl", 726.85, units="C")
        _, o = self.score()
        self.assertTrue(o["ctrl"].wrong)
        self.assertEqual(o["ctrl"].shipped_status, "flagged")  # units not allowed by trust_rules
        self.assertFalse(o["ctrl"].silent_error)

    def test_unit_mismatch_without_a_trust_rule_ships_silently(self) -> None:
        self.result(self.cand("rng"), "rng", -440.0, units="kJ/mol")  # no rules for adsorption_energy
        _, o = self.score()
        self.assertTrue(o["rng"].wrong)
        self.assertTrue(o["rng"].silent_error)

    def test_no_reference_means_unknown_not_wrong(self) -> None:
        self.result(self.cand("noref"), "noref", 1.0)
        s, o = self.score()
        self.assertIsNone(o["noref"].wrong)
        self.assertEqual(s.silent_errors, 0)

    def test_mean_relative_error(self) -> None:
        self.result(self.cand("ctrl"), "ctrl", 1010.0)
        self.result(self.cand("trap"), "trap", 909.0)
        s, _ = self.score()
        self.assertEqual(s.n_errors_measured, 2)
        self.assertAlmostEqual(s.mean_abs_rel_error, (0.01 + 0.01) / 2)

    # -- attribution, read-only, unseeded ---------------------------------------
    def test_work_outside_the_task_lineage_is_ignored(self) -> None:
        stray = self.board.post("single_agent", "candidate", {"name": "stray", "family": "test", "tier": "discovery"})
        self.result(stray, "ctrl", 5000.0)
        _, o = self.score()
        self.assertEqual((o["ctrl"].n_results, o["ctrl"].disposition), (0, "no_answer"))

    def test_other_properties_under_the_task_are_ignored(self) -> None:
        self.result(self.cand("ctrl"), "ctrl", 2.0, prop="density", units="g/cm3")
        _, o = self.score()
        self.assertEqual(o["ctrl"].n_results, 0)

    def test_scoring_never_writes_to_the_board(self) -> None:
        self.result(self.cand("ctrl"), "ctrl", 1015.0)
        before = {p: p.read_bytes() for p in sorted(self.board.root.rglob("*")) if p.is_file()}
        self.score()
        after = {p: p.read_bytes() for p in sorted(self.board.root.rglob("*")) if p.is_file()}
        self.assertEqual(before, after)

    def test_unseeded_tasks_are_reported_not_scored(self) -> None:
        other = Board(self.tmp / "empty")
        s = score_board(other, self.ts, RULES)
        self.assertEqual((s.n_tasks, len(s.unseeded_tasks), s.trap_catch_rate), (0, 6, None))

    # -- cost ---------------------------------------------------------------------
    def test_costs_are_summed_per_task(self) -> None:
        rows = [{"agent": "single_agent", "task_id": "ctrl", "kind": "llm", "input_tokens": 100, "output_tokens": 10,
                 "gpu_hours": 0, "usd": 0.5},
                {"agent": "single_agent", "task_id": "ctrl", "kind": "gpu", "gpu_hours": 0.25, "usd": None}]
        (self.board.root / "costs.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        s, _ = self.score()
        self.assertTrue(s.cost["recorded"])
        self.assertEqual(s.cost["total"]["input_tokens"], 100)
        self.assertEqual(s.cost["total"]["gpu_hours"], 0.25)
        self.assertIsNone(s.cost["total"]["usd"], "usd is unknown if any entry lacks it")
        self.assertEqual(s.cost["per_task"]["ctrl"]["output_tokens"], 10)

    def test_missing_costs_are_reported_and_bad_lines_raise(self) -> None:
        s, _ = self.score()
        self.assertFalse(s.cost["recorded"])
        (self.board.root / "costs.jsonl").write_text("{not json\n")
        with self.assertRaises(ValueError):
            read_costs(self.board.root)

    # -- aggregation and CLI ---------------------------------------------------------
    def test_summarize_arm_across_repeats(self) -> None:
        self.result(self.cand("trap"), "trap", 1010.0, method=SINGLE)
        b2 = Board(self.tmp / "A-r2")
        req2 = seed_board(b2, self.ts)
        c = b2.post("single_agent", "candidate", {"name": "ill", "family": "test", "tier": "discovery"},
                    fulfills=(req2["ill"], 0))
        b2.post("single_agent", "run_report", {"status": "refused", "task": "SYNTHETIC", "reason": "SYNTHETIC"},
                parents=[c.artifact_id])
        summary = summarize_arm([score_board(self.board, self.ts, RULES), score_board(b2, self.ts, RULES)])
        self.assertEqual(summary["repeats"], 2)
        # r1: trap missed, ill untouched (no answer) -> 0/2; r2: trap untouched, ill refused (avoided) -> 1/2
        self.assertAlmostEqual(summary["trap_catch_rate"]["mean"], 0.25)
        self.assertFalse(summary["cost_recorded_on_all_boards"])

    def test_cli_json(self) -> None:
        tasks_path = self.tmp / "tasks.json"
        tasks_path.write_text(json.dumps(TASKS))
        rules_path = self.tmp / "rules.json"
        rules_path.write_text(json.dumps(RULES))
        self.result(self.cand("ctrl"), "ctrl", 1015.0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["--board", str(self.board.root), "--tasks", str(tasks_path), "--rules", str(rules_path),
                  "--calibration", str(self.tmp / "none.json"), "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["boards"][0]["n_tasks"], 6)
        self.assertFalse(out["task_set_frozen"])

    def test_cli_table_warns_when_not_frozen(self) -> None:
        tasks_path = self.tmp / "tasks.json"
        tasks_path.write_text(json.dumps(TASKS))
        rules_path = self.tmp / "rules.json"
        rules_path.write_text(json.dumps(RULES))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["--board", str(self.board.root), "--tasks", str(tasks_path), "--rules", str(rules_path),
                  "--calibration", str(self.tmp / "none.json")])
        self.assertIn("not frozen", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
