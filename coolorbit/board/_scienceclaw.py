"""Import ScienceClaw's Artifact record type from the cloned repo.

The board stores every record as a ScienceClaw ``Artifact`` (same fields, same
SHA-256 content hash, same per-agent ``store.jsonl`` + shared
``global_index.jsonl`` layout), so ScienceClaw tooling can read our boards.

ScienceClaw is not pip-installable; clone it next to this package:

    git clone https://github.com/lamm-mit/scienceclaw.git scienceclaw

or point ``SCIENCECLAW_DIR`` at an existing clone.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCIENCECLAW_DIR = Path(os.environ.get("SCIENCECLAW_DIR", REPO_ROOT / "scienceclaw")).resolve()

if not (SCIENCECLAW_DIR / "artifacts" / "artifact.py").is_file():
    raise ImportError(
        f"ScienceClaw not found at {SCIENCECLAW_DIR}. Clone it with "
        f"'git clone https://github.com/lamm-mit/scienceclaw.git {REPO_ROOT / 'scienceclaw'}' "
        "or set SCIENCECLAW_DIR."
    )

# Append (not prepend) so ScienceClaw's generic top-level module names
# (core, memory, reasoning, ...) can never shadow installed packages.
if str(SCIENCECLAW_DIR) not in sys.path:
    sys.path.append(str(SCIENCECLAW_DIR))

from artifacts.artifact import Artifact  # noqa: E402

if not Path(sys.modules[Artifact.__module__].__file__).resolve().is_relative_to(SCIENCECLAW_DIR):
    raise ImportError(
        f"'artifacts.artifact' resolved to {sys.modules[Artifact.__module__].__file__}, "
        f"not the ScienceClaw clone at {SCIENCECLAW_DIR}."
    )

__all__ = ["Artifact", "SCIENCECLAW_DIR", "REPO_ROOT"]
