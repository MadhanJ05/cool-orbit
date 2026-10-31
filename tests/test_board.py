"""Tests for the results board. All numbers here are synthetic test fixtures, not results.

Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import multiprocessing as mp
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coolorbit.board import Board, BoardError, derive_status
from coolorbit.board._scienceclaw import Artifact

RULES = {
    "frozen": True,
    "independent_check_types": ["second_mlip", "classical_md"],
    "rejection_min_referees": 2,
    "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 15.0}, "tier1": {"abs": 20.0}}},
}
MACE = {"engine": "ALCHEMI", "model": "MACE-MPA-0", "protocol": "two-phase coexistence", "ensemble": "NPT"}
OPENMM = {"engine": "OpenMM", "model": "GAFF2", "protocol": "two-phase coexistence", "ensemble": "NPT"}
NEED = {"artifact_type": "check", "check_type": "classical_md",
        "query": "classical MD melting point of n-eicosane", "rationale": "the trust rule needs an independent method"}


def _post_many(root: str, agent: str, n: int) -> None:
    board = Board(root)
    for i in range(n):
        board.post(agent, "candidate", {"name": f"{agent}-{i}", "family": "test", "tier": "discovery"})


class BoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self._tmp.name) / "board")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers -----------------------------------------------------------
    def candidate(self, tier: str = "discovery") -> Artifact:
        return self.board.post("scout_paraffin", "candidate", {"name": "n-eicosane", "family": "paraffin", "tier": tier})

    def result(self, parent: Artifact, value: float = 310.0, tier: str = "discovery", method: dict = MACE,
               agent: str = "simulator_1", prop: str = "melting_point", units: str = "K", needs=()) -> Artifact:
        payload = {"candidate": "n-eicosane", "family": "paraffin", "tier": tier, "property": prop,
                   "value": value, "units": units, "uncertainty": 3.0, "method": method}
        return self.board.post(agent, "property_result", payload, parents=[parent.artifact_id], needs=needs)

    def check(self, res: Artifact, check_type: str, verdict: str, agent: str, value=None,
              method=None, units: str = "K", flags=()) -> Artifact:
        payload = {"check_type": check_type, "verdict": verdict, "flags": list(flags)}
        if value is not None:
            payload.update(value=value, units=units, method=method)
        return self.board.post(agent, "check", payload, parents=[res.artifact_id])

    def status(self, res: Artifact):
        return derive_status(self.board.snapshot(), res.artifact_id, RULES)

    # -- storage -----------------------------------------------------------
    def test_scienceclaw_compatible_records(self) -> None:
        cand = self.candidate()
        store = self.board.artifacts_dir / "scout_paraffin" / "store.jsonl"
        record = json.loads(store.read_text().splitlines()[0])
        restored = Artifact.from_dict(record)
        self.assertEqual(restored.artifact_id, cand.artifact_id)
        self.assertEqual(restored.content_hash, Artifact._hash_payload(record["payload"]))
        self.assertEqual([e["artifact_id"] for e in self.board.index()], [cand.artifact_id])

    def test_unknown_parent_rejected(self) -> None:
        with self.assertRaises(BoardError):
            self.board.post("x", "candidate", {"name": "a", "family": "b", "tier": "trust"}, parents=["nope"])

    def test_unknown_type_and_bad_payload_rejected(self) -> None:
        with self.assertRaises(BoardError):
            self.board.post("x", "gossip", {})
        with self.assertRaises(BoardError):
            self.board.post("x", "candidate", {"name": "a", "family": "b", "tier": "maybe"})

    def test_reference_value_requires_source(self) -> None:
        bad = {"candidate": "n-eicosane", "property": "melting_point", "value": 310.0, "units": "K"}
        with self.assertRaises(BoardError):
            self.board.post("librarian", "reference_value", bad)
        self.board.post("librarian", "reference_value", {**bad, "value": None})  # "not found" is allowed

    def test_boards_are_isolated(self) -> None:
        other = Board(Path(self._tmp.name) / "other")
        self.candidate()
        self.assertEqual(other.snapshot().artifacts, {})

    def test_concurrent_posts_from_many_processes(self) -> None:
        root = str(self.board.root)
        procs = [mp.Process(target=_post_many, args=(root, f"agent{i}", 25)) for i in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
            self.assertEqual(p.exitcode, 0)
        ids = [e["artifact_id"] for e in self.board.index()]
        self.assertEqual(len(ids), 100)
        self.assertEqual(len(set(ids)), 100)
        self.assertEqual(len(self.board.snapshot().artifacts), 100)

    # -- status ------------------------------------------------------------
    def test_simulated_then_under_review(self) -> None:
        res = self.result(self.candidate())
        self.assertEqual(self.status(res).status, "simulated")
        self.check(res, "method_audit", "pass", "referee_a")
        self.assertEqual(self.status(res).status, "under_review")

    def test_trusted_needs_audit_and_independent_agreement(self) -> None:
        res = self.result(self.candidate())
        self.check(res, "method_audit", "pass", "referee_a")
        self.check(res, "classical_md", "agree", "referee_b", value=318.0, method=OPENMM)
        self.assertEqual(self.status(res).status, "trusted")

    def test_self_checks_are_ignored(self) -> None:
        res = self.result(self.candidate())
        self.check(res, "method_audit", "pass", "simulator_1")
        self.check(res, "classical_md", "agree", "simulator_1", value=311.0, method=OPENMM)
        rep = self.status(res)
        self.assertNotEqual(rep.status, "trusted")
        self.assertTrue(any("cannot check their own" in r for r in rep.reasons))

    def test_same_engine_and_model_is_not_independent(self) -> None:
        res = self.result(self.candidate())
        self.check(res, "method_audit", "pass", "referee_a")
        self.check(res, "second_mlip", "agree", "referee_b", value=311.0, method=MACE)
        self.assertEqual(self.status(res).status, "under_review")

    def test_numbers_beat_verdict_text(self) -> None:
        res = self.result(self.candidate())
        self.check(res, "method_audit", "pass", "referee_a")
        self.check(res, "classical_md", "agree", "referee_b", value=355.0, method=OPENMM)
        rep = self.status(res)
        self.assertEqual(rep.status, "flagged")
        self.assertTrue(any("although the referee said agree" in r for r in rep.reasons))

    def test_unit_mismatch_flags(self) -> None:
        res = self.result(self.candidate())
        self.check(res, "classical_md", "agree", "referee_b", value=37.0, method=OPENMM, units="C")
        self.assertEqual(self.status(res).status, "flagged")

    def test_trust_tier_needs_matching_sourced_reference(self) -> None:
        cand = self.candidate(tier="trust")
        res = self.result(cand, value=312.0, tier="trust")
        self.check(res, "method_audit", "pass", "referee_a")
        self.check(res, "classical_md", "agree", "referee_b", value=316.0, method=OPENMM)
        self.assertEqual(self.status(res).status, "under_review")
        self.board.post("librarian", "reference_value", {"candidate": "N-Eicosane", "property": "melting_point",
                                                         "value": 309.0, "units": "K", "source": "test fixture"})
        self.assertEqual(self.status(res).status, "trusted")

    def test_disagreement_with_experiment_flags(self) -> None:
        cand = self.candidate(tier="trust")
        res = self.result(cand, value=360.0, tier="trust")
        self.board.post("librarian", "reference_value", {"candidate": "n-eicosane", "property": "melting_point",
                                                         "value": 309.0, "units": "K", "source": "test fixture"})
        self.assertEqual(self.status(res).status, "flagged")

    def test_superheating_story(self) -> None:
        """Single-phase heating gets flagged; the coexistence rerun becomes trusted."""
        cand = self.candidate()
        heated = self.result(cand, value=365.0, method={**MACE, "protocol": "single-phase heating"})
        self.check(heated, "method_audit", "fail", "referee_a", flags=["single_phase_heating"])
        rerun = self.board.post("simulator_2", "property_result",
                                {**heated.payload, "value": 312.0, "method": MACE}, parents=[heated.artifact_id])
        self.check(rerun, "method_audit", "pass", "referee_a")
        self.check(rerun, "classical_md", "agree", "referee_b", value=318.0, method=OPENMM)
        old, new = self.status(heated), self.status(rerun)
        self.assertEqual(old.status, "flagged")
        self.assertEqual(old.superseded_by, [rerun.artifact_id])
        self.assertTrue(any("single_phase_heating" in r for r in old.reasons))
        self.assertEqual(new.status, "trusted")

    def test_rejection_needs_two_distinct_referees(self) -> None:
        res = self.result(self.candidate())
        self.check(res, "premise_check", "reject", "referee_a")
        self.assertEqual(self.status(res).status, "flagged")
        self.check(res, "premise_check", "reject", "referee_a")  # same referee twice does not count
        self.assertEqual(self.status(res).status, "flagged")
        self.check(res, "premise_check", "reject", "referee_b")
        self.assertEqual(self.status(res).status, "rejected")

    def test_property_without_registered_tolerance_cannot_be_trusted(self) -> None:
        res = self.result(self.candidate(), prop="viscosity", units="Pa*s")
        self.check(res, "method_audit", "pass", "referee_a")
        self.check(res, "classical_md", "agree", "referee_b", value=310.0, method=OPENMM, units="Pa*s")
        self.assertNotEqual(self.status(res).status, "trusted")

    # -- needs and claims --------------------------------------------------
    def test_need_claim_expiry_and_fulfilment(self) -> None:
        res = self.result(self.candidate(), needs=[NEED])
        self.assertEqual(len(self.board.open_needs(include_claimed=False)), 1)
        self.assertTrue(self.board.claim("referee_b", res.artifact_id, 0, ttl_minutes=60))
        self.assertFalse(self.board.claim("referee_c", res.artifact_id, 0))  # earlier live claim wins
        self.assertEqual(self.board.open_needs(include_claimed=False), [])
        later = datetime.now(timezone.utc) + timedelta(hours=2)
        self.assertEqual(len(self.board.open_needs(include_claimed=False, now=later)), 1)  # claim expired
        self.board.post("referee_b", "check", {"check_type": "classical_md", "verdict": "agree", "value": 318.0,
                                               "units": "K", "method": OPENMM}, fulfills=(res.artifact_id, 0))
        self.assertEqual(self.board.open_needs(), [])
        with self.assertRaises(BoardError):
            self.board.post("referee_b", "check", {"check_type": "method_audit", "verdict": "pass"},
                            fulfills=(res.artifact_id, 5))


if __name__ == "__main__":
    unittest.main()
