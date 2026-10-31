"""Export boards and push them to the dashboard's Cloud Storage bucket (run on the GB10, e.g. every few minutes).

    pip install google-cloud-storage      # once, on the GB10
    export GOOGLE_APPLICATION_CREDENTIALS=~/.config/coolorbit/dashboard-push.json   # the bucket-only key; never in git
    python -m dashboard.push_exports --bucket <bucket> --board boards/science-1 --board boards/eval/<run> [--every 180]

Each round: export into a fresh temp folder (dashboard/export.py), refuse to publish anything SYNTHETIC, upload
every per-board file first and ``index.json`` / ``data.js`` last (so the page never points at a board that isn't
there yet), with ``Cache-Control: no-cache``. Objects are written under ``<prefix>/`` (default ``dashboard``).
Boards that disappear from the export are not deleted from the bucket (nothing on the live link vanishes).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

from coolorbit.board import load_rules
from coolorbit.eval.tasks import load_tasks
from dashboard.export import export

LAST = ("index.json", "data.js")
CONTENT_TYPES = {".json": "application/json; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


class Uploader(Protocol):
    def upload(self, local: Path, object_name: str, content_type: str) -> None: ...


class GcsUploader:
    """google-cloud-storage with Application Default Credentials (the bucket-only service-account key)."""

    def __init__(self, bucket: str) -> None:
        from google.cloud import storage  # imported here so the laptop tests don't need it

        self.bucket = storage.Client().bucket(bucket)

    def upload(self, local: Path, object_name: str, content_type: str) -> None:
        blob = self.bucket.blob(object_name)
        blob.cache_control = "no-cache"
        blob.upload_from_filename(str(local), content_type=content_type)


def push_once(boards: list[str], uploader: Uploader, prefix: str = "dashboard", *, allow_synthetic: bool = False,
              log: Callable[[str], None] = print) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="coolorbit-export-") as tmp:
        bundle = export(boards, tmp, rules=load_rules(), task_set=load_tasks())
        synthetic = [e["name"] for e in bundle["index"]["boards"] if e["synthetic"]]
        if synthetic and not allow_synthetic:
            raise SystemExit(f"refusing to publish SYNTHETIC boards to the judges' link: {synthetic}")
        files = sorted(Path(tmp).iterdir())
        ordered = [f for f in files if f.name not in LAST] + [Path(tmp) / n for n in LAST]
        names = []
        for f in ordered:
            obj = f"{prefix.strip('/')}/{f.name}" if prefix.strip("/") else f.name
            uploader.upload(f, obj, CONTENT_TYPES[f.suffix])
            names.append(obj)
        log(f"pushed {len(names)} files ({len(bundle['index']['boards'])} boards) to {prefix}/ at "
            f"{bundle['index']['exported_at']}")
        return names


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m dashboard.push_exports", description=__doc__.splitlines()[0])
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--prefix", default="dashboard")
    ap.add_argument("--board", action="append", required=True, help="board or folder of boards; repeatable")
    ap.add_argument("--every", type=float, default=0, help="seconds between pushes (0 = once)")
    ap.add_argument("--allow-synthetic", action="store_true", help="never for the judges' link")
    args = ap.parse_args(argv)
    uploader = GcsUploader(args.bucket)
    while True:
        try:
            push_once(args.board, uploader, args.prefix, allow_synthetic=args.allow_synthetic)
        except SystemExit:
            raise
        except Exception as exc:  # keep looping through transient upload/export errors
            print(f"push failed: {exc!r}", file=sys.stderr, flush=True)
            if not args.every:
                raise
        if not args.every:
            return
        time.sleep(args.every)


if __name__ == "__main__":
    main()
