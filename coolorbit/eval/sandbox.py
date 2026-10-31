"""Docker sandbox for arm A/B sessions (required for real runs).

    python -m coolorbit.eval.sandbox build [--tag coolorbit-eval:0.2.0] [--no-alchemi]   # build the image
    python -m coolorbit.eval.sandbox check [--tag ...]                                   # is it there?

Each session runs as ``docker run --rm`` with:
  - mounts: the session's work dir at ``/work`` and the task board's ``artifacts/`` at
    ``/board/artifacts``; nothing else (no home dir, ~/.ssh, ~/.config/gh, ~/.brev, repo, .env,
    docker socket). Costs, transcripts and the run record stay outside the agent's reach.
  - environment: only ``-e ANTHROPIC_API_KEY`` (passed by name, so the value never appears on the
    command line); its value is the active shared key from ``coolorbit.llm_keys.subprocess_env()``
    (primary until out of credit, then backup). Everything else is baked into the image.
  - ``--gpus all`` (off only for laptop plumbing tests), host uid:gid so files stay yours,
    ``--cap-drop ALL``, ``no-new-privileges``, a throwaway HOME on tmpfs.
  - on the wall-clock limit the runner ``docker kill``s the container (killing the client alone
    would leave it running).

The image is built from a staged, minimal context (Dockerfile, ALCHEMI skills, the board tool and
the board package with ScienceClaw's artifact module), never from the repo, so tasks.json, CLAUDE.md,
.env and our referee code are not in the image.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from coolorbit.board._scienceclaw import REPO_ROOT, SCIENCECLAW_DIR

DOCKER_DIR = Path(__file__).with_name("docker")
DEFAULT_TAG = "coolorbit-eval:0.2.0"
DEFAULT_SKILLS = REPO_ROOT / ".claude" / "skills"
TOOL_DIR = "/opt/coolorbit-tool"
API_KEY_ENV = "ANTHROPIC_API_KEY"
# What the staged tool layer may contain, relative to the repo / ScienceClaw clone. Nothing else.
TOOL_FILES = ["coolorbit/__init__.py", "coolorbit/board/__init__.py", "coolorbit/board/_scienceclaw.py",
              "coolorbit/board/schema.py", "coolorbit/board/status.py", "coolorbit/board/store.py"]


def stage_context(dest: Path, skills_dir: Path = DEFAULT_SKILLS) -> Path:
    """Write the minimal build context into ``dest`` (must not exist or be empty)."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} is not empty")
    tool = dest / "tool"
    for rel in TOOL_FILES:
        (tool / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, tool / rel)
    shutil.copy2(Path(__file__).with_name("board_tool.py"), tool / "board_tool.py")
    shutil.copy2(DOCKER_DIR / "entrypoint.sh", tool / "entrypoint.sh")
    sc = tool / "scienceclaw" / "artifacts"
    sc.mkdir(parents=True)
    shutil.copy2(SCIENCECLAW_DIR / "artifacts" / "artifact.py", sc / "artifact.py")
    (sc / "__init__.py").write_text("", encoding="utf-8")  # upstream __init__ pulls in more than we need
    if Path(skills_dir).is_dir():
        shutil.copytree(skills_dir, dest / "skills")
    else:
        (dest / "skills").mkdir()
    shutil.copy2(DOCKER_DIR / "Dockerfile", dest / "Dockerfile")
    return dest


def _claude_version() -> Optional[str]:
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20).stdout
        return out.split()[0] if out.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def build_image(tag: str = DEFAULT_TAG, *, with_alchemi: bool = True, claude_code_version: Optional[str] = None,
                docker: str = "docker", platform: Optional[str] = None) -> None:
    version = claude_code_version or _claude_version() or "latest"
    with tempfile.TemporaryDirectory(prefix="coolorbit-eval-ctx-") as tmp:
        ctx = stage_context(Path(tmp) / "ctx")
        cmd = [docker, "build", "-t", tag, "--build-arg", f"WITH_ALCHEMI={int(with_alchemi)}",
               "--build-arg", f"CLAUDE_CODE_VERSION={version}"]
        if platform:
            cmd += ["--platform", platform]
        print("building:", " ".join(cmd + [str(ctx)]), flush=True)
        subprocess.run(cmd + [str(ctx)], check=True)


def image_exists(tag: str, docker: str = "docker") -> bool:
    try:
        return subprocess.run([docker, "image", "inspect", tag], capture_output=True).returncode == 0
    except OSError:
        return False


def board_wrapper(agent: str) -> str:
    """The ./board script inside the container (paths are container paths)."""
    return ("#!/bin/sh\n"
            f"COOLORBIT_BOARD=/board COOLORBIT_AGENT='{agent}' PYTHONPATH='{TOOL_DIR}' "
            f"exec python3 '{TOOL_DIR}/board_tool.py' \"$@\"\n")


def _forbidden_mount(path: Path) -> Optional[str]:
    path = path.resolve()
    home = Path.home().resolve()
    if path in (home, REPO_ROOT, Path("/")):
        return f"{path} is the home dir, the repo or /"
    for bad in (home / ".ssh", home / ".config", home / ".brev", home / ".claude", REPO_ROOT / ".env"):
        if path == bad or bad in path.parents or path in bad.parents:
            return f"{path} overlaps {bad}"
    return None


class DockerSandbox:
    """Builds the ``docker run`` command for one session and kills it on timeout."""

    def __init__(self, tag: str = DEFAULT_TAG, *, gpus: bool = True, docker: str = "docker",
                 memory: Optional[str] = None, network: str = "bridge") -> None:
        self.tag, self.gpus, self.docker, self.memory, self.network = tag, gpus, docker, memory, network

    def preflight(self) -> None:
        from coolorbit import llm_keys  # the key itself comes from llm_keys.subprocess_env() at run time
        try:
            llm_keys.active_key_name()
        except llm_keys.NoUsableKey as exc:
            raise RuntimeError(f"no usable Anthropic key for the container ({exc})") from exc
        if not image_exists(self.tag, self.docker):
            raise RuntimeError(f"docker image {self.tag} not found; build it with "
                               f"'python -m coolorbit.eval.sandbox build --tag {self.tag}'")

    def run_command(self, inner: list[str], *, work_dir: Path, board_root: Path, name: str) -> list[str]:
        artifacts = (Path(board_root) / "artifacts").resolve()
        for p in (Path(work_dir), artifacts):
            why = _forbidden_mount(p)
            if why:
                raise ValueError(f"refusing to mount {p}: {why}")
        cmd = [self.docker, "run", "--rm", "--init", "--name", name,
               "--network", self.network,
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--pids-limit", "4096",
               "--user", f"{os.getuid()}:{os.getgid()}",
               "--tmpfs", "/tmp:rw,exec,size=8g",
               "-v", f"{Path(work_dir).resolve()}:/work:rw",
               "-v", f"{artifacts}:/board/artifacts:rw",
               "-w", "/work",
               "-e", API_KEY_ENV]
        if self.gpus:
            cmd += ["--gpus", "all"]
        if self.memory:
            cmd += ["--memory", self.memory]
        return cmd + [self.tag] + inner

    def kill(self, name: str) -> None:
        subprocess.run([self.docker, "kill", name], capture_output=True)


def container_name(*parts: str) -> str:
    clean = "-".join(p for p in parts if p)
    clean = "".join(c if c.isalnum() or c in "_.-" else "-" for c in clean)
    return f"coolorbit-{clean[:80]}-{uuid.uuid4().hex[:8]}"


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m coolorbit.eval.sandbox", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the sandbox image from a staged minimal context")
    b.add_argument("--tag", default=DEFAULT_TAG)
    b.add_argument("--no-alchemi", action="store_true", help="skip the CUDA/ALCHEMI stack (laptop plumbing tests only)")
    b.add_argument("--claude-code-version", help="default: the host's `claude --version`")
    b.add_argument("--platform", help="e.g. linux/arm64")
    b.add_argument("--docker", default="docker")
    s = sub.add_parser("stage", help="only write the build context (to inspect it)")
    s.add_argument("dest")
    c = sub.add_parser("check", help="is the image there?")
    c.add_argument("--tag", default=DEFAULT_TAG)
    c.add_argument("--docker", default="docker")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        tag = args.tag if not args.no_alchemi or args.tag != DEFAULT_TAG else "coolorbit-eval:plumbing-no-alchemi"
        build_image(tag, with_alchemi=not args.no_alchemi, claude_code_version=args.claude_code_version,
                    docker=args.docker, platform=args.platform)
        print(f"built {tag}")
    elif args.cmd == "stage":
        print(stage_context(Path(args.dest)))
    else:
        ok = image_exists(args.tag, args.docker)
        print(f"{args.tag}: {'present' if ok else 'MISSING'}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
