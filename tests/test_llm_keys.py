"""Tests for the shared Anthropic key failover (no network; fake keys and a fake client)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coolorbit import llm_keys

FAKE_PRIMARY, FAKE_BACKUP = "sk-ant-FAKE-primary-0000", "sk-ant-FAKE-backup-1111"
OUT_OF_CREDIT = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': "
                 "'Your credit balance is too low to access the Anthropic API.'}}")


class KeyFailoverTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.env = root / ".env"
        self.state = root / "runs" / "llm_key_state.json"
        self.env.write_text(f"NEBIUS_API_KEY=x\n{llm_keys.PRIMARY}={FAKE_PRIMARY}\n{llm_keys.BACKUP}='{FAKE_BACKUP}'\n")
        self._patches = [mock.patch.object(llm_keys, "ENV_PATH", self.env),
                         mock.patch.object(llm_keys, "STATE_PATH", self.state),
                         mock.patch.dict(os.environ, {}, clear=False)]
        for p in self._patches:
            p.start()
        for name in (llm_keys.PRIMARY, llm_keys.BACKUP):
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def test_primary_first_then_backup(self) -> None:
        self.assertEqual(llm_keys.active_key(self.env, self.state), FAKE_PRIMARY)
        self.assertTrue(llm_keys.mark_primary_exhausted("test", self.state))
        self.assertFalse(llm_keys.mark_primary_exhausted("again", self.state))  # idempotent
        self.assertEqual(llm_keys.active_key(self.env, self.state), FAKE_BACKUP)

    def test_state_file_never_contains_a_key(self) -> None:
        llm_keys.mark_primary_exhausted(f"oops {FAKE_PRIMARY}"[:5], self.state)
        text = self.state.read_text()
        self.assertNotIn(FAKE_PRIMARY, text)
        self.assertNotIn(FAKE_BACKUP, text)
        self.assertIn("primary_exhausted_at", json.loads(text))

    def test_detects_out_of_credit_errors_only(self) -> None:
        self.assertTrue(llm_keys.is_credit_exhausted(RuntimeError(OUT_OF_CREDIT)))
        self.assertTrue(llm_keys.is_credit_exhausted("... Credit balance is too low ..."))
        self.assertFalse(llm_keys.is_credit_exhausted(RuntimeError("rate_limit_error: 429")))

    def test_failover_retries_once_on_the_backup(self) -> None:
        used = []

        def fn(client_key):
            used.append(client_key)
            if client_key == FAKE_PRIMARY:
                raise RuntimeError(OUT_OF_CREDIT)
            return "ok"

        out = llm_keys.call_with_failover(fn, make_client=lambda: llm_keys.active_key(self.env, self.state),
                                          state_path=self.state, env_path=self.env)
        self.assertEqual((out, used), ("ok", [FAKE_PRIMARY, FAKE_BACKUP]))
        self.assertTrue(llm_keys.primary_exhausted(self.state))

    def test_other_errors_are_not_swallowed(self) -> None:
        def fn(_):
            raise RuntimeError("rate_limit_error: 429")

        with self.assertRaises(RuntimeError):
            llm_keys.call_with_failover(fn, make_client=lambda: None, state_path=self.state, env_path=self.env)
        self.assertFalse(llm_keys.primary_exhausted(self.state))

    def test_subprocess_env_exposes_only_the_active_key(self) -> None:
        env = llm_keys.subprocess_env({"PATH": "/bin", llm_keys.PRIMARY: "x", llm_keys.BACKUP: "y"})
        # Compare booleans so a failure can never print a key into the test log.
        self.assertTrue(env["ANTHROPIC_API_KEY"] == FAKE_PRIMARY, "subprocess env did not use the fake test key")
        self.assertNotIn(llm_keys.PRIMARY, env)
        self.assertNotIn(llm_keys.BACKUP, env)

    def test_exhausted_without_backup_is_a_clear_error(self) -> None:
        self.env.write_text(f"{llm_keys.PRIMARY}={FAKE_PRIMARY}\n")
        llm_keys.mark_primary_exhausted("test", self.state)
        with self.assertRaises(llm_keys.NoUsableKey):
            llm_keys.active_key(self.env, self.state)


if __name__ == "__main__":
    unittest.main()
