"""SshRunner (coolorbit/sim/ssh_runner.py) with a fake `run`: no network, no GPU. All data SYNTHETIC."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from coolorbit.sim.ssh_runner import SshRunner


class FakeJob:
    def cli_args(self, out_dir: Path) -> list[str]:
        return ["coexistence", "--element", "Cu", "--structure", "ref", "--out", str(out_dir), "--quick"]


class SshRunnerTest(unittest.TestCase):
    def make_run(self, out_dir: Path, *, dirty: str = "", on_remote: str = "  origin/main", exit_code: str = "0"):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[:3] == ["git", "-C", cmd[2]] and "status" in cmd:
                return SimpleNamespace(stdout=dirty, stderr="", returncode=0)
            if "rev-parse" in cmd:
                return SimpleNamespace(stdout="abc123def\n", stderr="", returncode=0)
            if "branch" in cmd:
                return SimpleNamespace(stdout=on_remote, stderr="", returncode=0)
            if cmd[0] == "ssh":
                return SimpleNamespace(stdout=f"REMOTE_EXIT={exit_code} REMOTE_DIR=/home/u/cool-orbit/runs/remote/x\n",
                                       stderr="", returncode=0)
            if cmd[0] == "rsync":
                run_dir = out_dir / "20261101T000000Z_Cu_coexistence"
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "result.json").write_text(json.dumps({"status": "ok", "value": 1.0}))
                (run_dir / "settings.json").write_text(json.dumps({"settings": {"mode": "coexistence"}, "n_atoms": 4}))
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            raise AssertionError(f"unexpected command {cmd}")

        return run, calls

    def test_runs_the_exact_commit_over_port_22_and_copies_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "job-a1"
            run, calls = self.make_run(out)
            result, settings, run_dir = SshRunner("h100-host", ssh_config="/cfg", run=run)(FakeJob(), out, 60)
        ssh = next(c for c in calls if c[0] == "ssh")
        self.assertIn("-p", ssh)
        self.assertEqual(ssh[ssh.index("-p") + 1], "22")
        self.assertIn("git checkout -q --detach abc123def", ssh[-1])
        self.assertIn("python -m coolorbit.sim.melting coexistence", ssh[-1])
        self.assertEqual(result["flags"], [])
        self.assertEqual(settings["settings"]["remote"], {"host": "h100-host", "transport": "ssh", "commit": "abc123def"})
        self.assertEqual(run_dir.name, "20261101T000000Z_Cu_coexistence")

    def test_refuses_uncommitted_or_unpushed_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "job"
            run, _ = self.make_run(out, dirty=" M coolorbit/sim/melting.py")
            with self.assertRaisesRegex(RuntimeError, "uncommitted"):
                SshRunner("h", run=run)(FakeJob(), out, 60)
            run, _ = self.make_run(out, on_remote="")
            with self.assertRaisesRegex(RuntimeError, "not on any remote branch"):
                SshRunner("h", run=run)(FakeJob(), out, 60)

    def test_a_failed_remote_run_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "job"
            run, _ = self.make_run(out, exit_code="1")
            with self.assertRaisesRegex(RuntimeError, "exited 1"):
                SshRunner("h", run=run)(FakeJob(), out, 60)


if __name__ == "__main__":
    unittest.main()
