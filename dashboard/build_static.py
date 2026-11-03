"""Build the dashboard as a static site (GitHub Pages), from the boards committed in this repo.

The Cloud Run copy served the pages and a live export through dashboard/server.py. This writes the same pages with
relative paths (so they work under https://<user>.github.io/<repo>/) and a frozen export of the boards:

    _site/index.html            the v2 dashboard (Overview, Materials, Trust, Agents)
    _site/v2/                   its script, styles, fonts and hero image
    _site/data/                 data.js + per-board JSON (python -m dashboard.export)
    _site/classic/              the original dashboard (also framed by the Agents page)
    _site/ga3d.js, vendor/three the 3D gallium viewer

    python -m dashboard.build_static                                   # default boards, into _site/
    python -m dashboard.build_static --board boards/science-2 --out /tmp/site
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_BOARDS = ["boards/science-1", "boards/science-2", "boards/eval/cmp-1"]


def rewrite(path: Path, pairs: list[tuple[str, str]]) -> None:
    text = path.read_text()
    for old, new in pairs:
        if old not in text:
            raise SystemExit(f"build_static: expected {old!r} in {path}; the page changed, update this script")
        text = text.replace(old, new)
    path.write_text(text)


def build(out: Path, boards: list[str]) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "classic").mkdir(parents=True)
    # data: the same exporter the Cloud Run push used (it refuses nothing here; synthetic boards are not passed in)
    args = [sys.executable, "-m", "dashboard.export", "--out", str(out / "data")]
    for b in boards:
        args += ["--board", b]
    subprocess.run(args, cwd=ROOT, check=True)

    # v2 at the root
    shutil.copy(HERE / "v2" / "index.html", out / "index.html")
    shutil.copytree(HERE / "v2", out / "v2", ignore=shutil.ignore_patterns("index.html"))
    rewrite(out / "index.html", [('"/v2/', '"v2/'), ('"/data/data.js"', '"data/data.js"'), ('"/ga3d.js"', '"ga3d.js"'),
                                 ('data-src="/classic/?embed=agents"', 'data-src="classic/index.html?embed=agents"')])
    rewrite(out / "v2" / "style.css", [('url("/v2/fonts/', 'url("fonts/')])

    # 3D viewer (imports ./vendor/three relative to itself)
    shutil.copy(HERE / "ga3d.js", out / "ga3d.js")
    shutil.copytree(HERE / "vendor", out / "vendor")

    # the original dashboard, under classic/
    for f in ("index.html", "app.js", "style.css"):
        shutil.copy(HERE / f, out / "classic" / f)
    rewrite(out / "classic" / "index.html", [('src="data/data.js"', 'src="../data/data.js"'), ('src="ga3d.js"', 'src="../ga3d.js"')])
    rewrite(out / "classic" / "style.css", [('url("/v2/fonts/', 'url("../v2/fonts/')])

    (out / ".nojekyll").write_text("")  # serve files as they are (no Jekyll processing)
    leftovers = [p for p in (out / "index.html", out / "v2" / "app.js") if '"/v2/' in p.read_text() or '"/data/' in p.read_text()]
    if leftovers:
        raise SystemExit(f"build_static: absolute paths left in {leftovers}")
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", action="append", help="board or folder to export (repeatable; default: the science "
                    "boards and comparison cmp-1)")
    ap.add_argument("--out", default=str(ROOT / "_site"))
    a = ap.parse_args()
    build(Path(a.out), a.board or DEFAULT_BOARDS)


if __name__ == "__main__":
    main()
