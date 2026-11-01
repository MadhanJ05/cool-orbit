"""Run Simulator jobs on a remote GPU over plain SSH (same contract as simulator_agent.BrevRunner).

Why: on 2026-11-01 Brev's SSH proxy route (global.prd.ga.run.brev.nvidia.com:24969) timed out from the GB10's network,
so ``brev exec`` / ``brev copy`` (BrevRunner) could not reach the H100, while the instance's own address answered on
port 22. This runner uses ``ssh``/``rsync`` with Brev's generated config (~/.brev/ssh_config) and an explicit port.

Like BrevRunner it never creates, stops or deletes instances, refuses to run with uncommitted changes under
``coolorbit/``, checks the exact local commit out on the remote (so the remote run is reproducible; the commit must be
pushed), runs ``coolorbit.sim.melting`` there, and copies the run folder back into ``out_dir``. ``run`` is injectable
for tests.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Callable

from coolorbit.board._scienceclaw import REPO_ROOT


class SshRunner:
    def __init__(self, host: str, ssh_config: str | Path = Path.home() / ".brev" / "ssh_config", port: int = 22,
                 remote_dir: str = "~/cool-orbit", run: Callable = subprocess.run) -> None:
        self.host, self.ssh_config, self.port, self.remote_dir, self._run = host, str(ssh_config), port, remote_dir, run

    def _ssh_base(self) -> list[str]:
        return ["ssh", "-F", self.ssh_config, "-T", "-p", str(self.port), "-o", "ConnectTimeout=30",
                "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=6"]

    def __call__(self, job, out_dir: Path, timeout_s: float):
        import json

        git = ["git", "-C", str(REPO_ROOT)]
        if self._run(git + ["status", "--porcelain", "coolorbit"], capture_output=True, text=True).stdout.strip():
            raise RuntimeError("coolorbit/ has uncommitted changes; commit and push before running remotely")
        sha = self._run(git + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        if not self._run(git + ["branch", "-r", "--contains", sha], capture_output=True, text=True).stdout.strip():
            raise RuntimeError(f"commit {sha[:8]} is not on any remote branch; push it so the remote can check it out")
        remote_out = f"runs/remote/{out_dir.name}"
        # Repo-relative paths only: a local absolute path (file: structures) means nothing on the remote.
        args = [a.replace(str(REPO_ROOT) + "/", "") for a in job.cli_args(Path(remote_out))]
        cmd = (f"cd {self.remote_dir} && git fetch -q origin && git checkout -q --detach {sha} && mkdir -p "
               f"{remote_out} && .venv/bin/python -m coolorbit.sim.melting {shlex.join(args)} "
               f"> {remote_out}/agent_job.log 2>&1; echo REMOTE_EXIT=$? REMOTE_DIR=$(pwd)/{remote_out}")
        proc = self._run(self._ssh_base() + [self.host, cmd], capture_output=True, text=True, timeout=timeout_s)
        tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        fields = dict(f.split("=", 1) for f in tail[0].split() if "=" in f)
        if "REMOTE_DIR" not in fields:
            raise RuntimeError(f"ssh to {self.host} failed: {(proc.stderr or proc.stdout or '')[-300:]}")
        out_dir.mkdir(parents=True, exist_ok=True)
        rsh = " ".join(shlex.quote(a) for a in self._ssh_base())
        copy = self._run(["rsync", "-a", "-e", rsh, f"{self.host}:{fields['REMOTE_DIR']}/", str(out_dir) + "/"],
                         capture_output=True, text=True, timeout=900)
        if copy.returncode != 0:
            raise RuntimeError(f"rsync from {self.host} failed: {(copy.stderr or copy.stdout)[-300:]}")
        runs = sorted(p for p in out_dir.rglob("result.json"))
        if fields.get("REMOTE_EXIT") != "0" or not runs:
            raise RuntimeError(f"remote melting run exited {fields.get('REMOTE_EXIT')}; see {out_dir}/agent_job.log")
        run_dir = runs[-1].parent
        result = json.loads((run_dir / "result.json").read_text())
        settings = json.loads((run_dir / "settings.json").read_text())
        result.setdefault("flags", [])
        settings["settings"]["remote"] = {"host": self.host, "transport": "ssh", "commit": sha}
        return result, settings, run_dir
