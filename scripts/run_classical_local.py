"""Run the classical-MD (MEAM / LAMMPS) bundles on the GB10's CPU cores: the independent check of the MACE results.

Same inputs and commands as the Nebius bundles (data/nebius/metals/<El>/manifest.json, Sia's), run locally with the
pip LAMMPS build (MEAM included) in its own environment (~/lammps-venv; its MPICH library is put on the path).
Works on a copy under runs/classical/<run>/ so the committed bundles stay untouched; each job runs in its element
folder with one OpenMP thread, many jobs in parallel. After an element's jobs finish, Sia's analyze.py is run there
and its JSON saved (analysis_<kind>.json). Nothing is posted to any board here.

    python scripts/run_classical_local.py --run meam-1 --elements In Sn Bi --kind coexistence --parallel 15 [--dry-run]
    python scripts/run_classical_local.py --run meam-1 --elements Sn --kind latent --tm Sn=540.2   # at the MEAM Tm
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLES = REPO_ROOT / "data" / "nebius" / "metals"
LAMMPS_VENV = Path.home() / "lammps-venv"
SHARED = ("in.coexist.lmp", "in.latent.lmp", "analyze.py")


def stage(run_dir: Path, elements: list[str]) -> Path:
    """Copy the shared inputs and each element folder (without validation/) into runs/classical/<run>/metals."""
    metals = run_dir / "metals"
    metals.mkdir(parents=True, exist_ok=True)
    for name in SHARED:
        shutil.copy2(BUNDLES / name, metals / name)
    for el in elements:
        if not (metals / el).exists():
            shutil.copytree(BUNDLES / el, metals / el, ignore=shutil.ignore_patterns("validation"))
    return metals


def with_temperature(command: str, el: str, t: str) -> tuple[str, str]:
    """A manifest coexistence command re-targeted to temperature ``t`` (same Tprep/Tmelt/seed); (job id, command)."""
    argv = shlex.split(command)
    for i, a in enumerate(argv):
        if a == "-var" and argv[i + 1] == "T":
            argv[i + 2] = t
        if a == "-log":
            argv[i + 1] = f"log_{el}-coexist-T{t}.lammps"
    return f"{el}-coexist-T{t}", shlex.join(argv)


def jobs_for(metals: Path, el: str, kind: str, tm: dict[str, str],
             temps: dict[str, list[str]] | None = None) -> list[dict]:
    manifest = json.loads((metals / el / "manifest.json").read_text())
    listed = [j for j in manifest["jobs"] if j["kind"] == kind]
    if kind == "coexistence" and temps and el in temps:  # extra bracket points, same protocol as the manifest
        template = listed[0]["command"]
        listed = [{"id": jid, "kind": kind, "command": cmd}
                  for jid, cmd in (with_temperature(template, el, t) for t in temps[el])]
    out = []
    for job in listed:
        cmd = job["command"]
        if "TM_MEAM_K" in cmd:
            if el not in tm:
                raise SystemExit(f"{job['id']} needs this potential's own melting point: pass --tm {el}=<K>")
            cmd = cmd.replace("TM_MEAM_K", tm[el])
        argv = shlex.split(cmd)
        if argv[0] != "lmp":
            raise SystemExit(f"unexpected command in {job['id']}: {cmd}")
        argv[0] = str(LAMMPS_VENV / "bin" / "lmp")
        out.append({"id": job["id"], "element": el, "argv": argv, "cwd": metals / el})
    return out


def run_job(job: dict, env: dict) -> dict:
    t0 = time.time()
    with open(job["cwd"] / f"job_{job['id']}.out", "w") as fh:
        code = subprocess.run(job["argv"], cwd=job["cwd"], env=env, stdout=fh, stderr=subprocess.STDOUT).returncode
    res = {"id": job["id"], "exit": code, "wall_min": round((time.time() - t0) / 60, 1)}
    print(f"{job['id']}: exit {code}, {res['wall_min']} min", flush=True)
    return res


def analyze(metals: Path, el: str, kind: str, tm: dict[str, str]) -> dict:
    folder = metals / el
    if kind == "coexistence":
        args = ["coexist", *sorted(p.name for p in folder.glob("pe_T*.dat"))]
    else:
        t = tm[el]
        args = ["latent", f"h_solid_T{t}.dat", f"h_liquid_T{t}.dat", "--log-solid", f"log_{el}-latent-solid.lammps",
                "--log-liquid", f"log_{el}-latent-liquid.lammps"]
    proc = subprocess.run([sys.executable, "../analyze.py", *args], cwd=folder, capture_output=True, text=True)
    (folder / f"analysis_{kind}.json").write_text(proc.stdout)
    (folder / f"analysis_{kind}.stderr").write_text(proc.stderr)
    return {"element": el, "kind": kind, "exit": proc.returncode, "stdout_tail": proc.stdout[-400:]}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--elements", nargs="+", default=["In", "Sn", "Bi"])
    ap.add_argument("--kind", choices=["coexistence", "latent"], default="coexistence")
    ap.add_argument("--tm", nargs="*", default=[], help="El=K, this potential's own melting point (latent jobs)")
    ap.add_argument("--temps", nargs="*", default=[],
                    help="El=T1,T2,... extra coexistence temperatures (replace the manifest's list for that element)")
    ap.add_argument("--parallel", type=int, default=15, help="jobs at once (one CPU core each)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    tm = dict(x.split("=", 1) for x in args.tm)
    temps = {el: ts.split(",") for el, ts in (x.split("=", 1) for x in args.temps)}

    run_dir = REPO_ROOT / "runs" / "classical" / args.run
    metals = stage(run_dir, args.elements)
    jobs = [j for el in args.elements for j in jobs_for(metals, el, args.kind, tm, temps)]
    for j in jobs:
        print(f"{j['id']:24s} cwd={j['cwd'].relative_to(REPO_ROOT)}  {' '.join(j['argv'][1:])}")
    if args.dry_run:
        print(f"{len(jobs)} job(s), {args.parallel} at a time (dry run: nothing started)")
        return
    env = {**os.environ, "OMP_NUM_THREADS": "1",
           "LD_LIBRARY_PATH": f"{LAMMPS_VENV / 'lib'}:{os.environ.get('LD_LIBRARY_PATH', '')}"}
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        results = list(pool.map(lambda j: run_job(j, env), jobs))
    summary = {"run": args.run, "kind": args.kind, "jobs": results,
               "analysis": [analyze(metals, el, args.kind, tm) for el in args.elements]}
    (run_dir / f"summary_{args.kind}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary["analysis"], indent=1))


if __name__ == "__main__":
    main()
