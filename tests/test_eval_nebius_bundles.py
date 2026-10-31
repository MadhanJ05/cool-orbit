"""Tests for the Nebius metal bundles (data/nebius/) and their analysis script. No LAMMPS needed.

All PE/enthalpy series below are SYNTHETIC test fixtures, not results.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import re
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
METALS = REPO / "data" / "nebius" / "metals"
ELEMENTS = ["Ga", "In", "Sn", "Bi"]

spec = importlib.util.spec_from_file_location("nebius_analyze", METALS / "analyze.py")
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


def read_data_header(path: Path) -> dict:
    text = path.read_text()
    out = {"atoms": int(re.search(r"(\d+) atoms", text).group(1))}
    for ax in "xyz":
        lo, hi = re.search(rf"(\S+)\s+(\S+)\s+{ax}lo {ax}hi", text).groups()
        out[ax] = float(hi) - float(lo)
    out["triclinic"] = "xy xz yz" in text
    return out


class BundleTest(unittest.TestCase):
    def manifest(self, el: str) -> dict:
        return json.loads((METALS / el / "manifest.json").read_text())

    def test_every_element_is_complete_and_unsubmitted(self) -> None:
        for el in ELEMENTS:
            d = METALS / el
            for f in ("data.coexist.lmp", "data.solid.lmp", "potential.mod", "manifest.json", "potential/SOURCES.md"):
                self.assertTrue((d / f).is_file(), f"{el}/{f}")
            m = self.manifest(el)
            self.assertIn("NOT submitted", m["status"])
            self.assertEqual(m["element"], el)

    def test_potential_files_match_their_recorded_hashes(self) -> None:
        for el in ELEMENTS:
            for name, digest in self.manifest(el)["potential"]["files"].items():
                data = (METALS / el / "potential" / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, f"{el}/{name} changed")

    def test_potential_mod_points_at_existing_files(self) -> None:
        for el in ELEMENTS:
            mod = (METALS / el / "potential.mod").read_text()
            self.assertIn("pair_style meam", mod)
            line = next(l for l in mod.splitlines() if l.startswith("pair_coeff"))
            self.assertTrue(line.rstrip().endswith(f" {el}"), f"{el}: the single atom type must map to {el}")
            for tok in line.split():
                if tok.startswith("potential/"):
                    self.assertTrue((METALS / el / tok).is_file(), f"{el}: {tok}")

    def test_every_potential_is_cited(self) -> None:
        for el in ELEMENTS:
            p = self.manifest(el)["potential"]
            self.assertTrue(p["citation"] and p["doi"])
            self.assertTrue(p["nist_url"].startswith("https://www.ctcms.nist.gov/potentials/entry/"))

    def test_structures_match_the_manifest_and_are_orthogonal(self) -> None:
        for el in ELEMENTS:
            s = self.manifest(el)["structure"]
            for key in ("coexist", "solid"):
                h = read_data_header(METALS / el / s[key]["file"])
                self.assertEqual(h["atoms"], s[key]["n_atoms"], f"{el} {key}")
                self.assertFalse(h["triclinic"], f"{el} {key}: LAMMPS box must be orthogonal")
            h = read_data_header(METALS / el / "data.coexist.lmp")
            self.assertGreaterEqual(h["z"], 1.9 * max(h["x"], h["y"]), f"{el}: coexistence box must be long in z")

    def test_bracket_brackets_the_sourced_guess(self) -> None:
        for el in ELEMENTS:
            m = self.manifest(el)
            g = m["melting_point_guess"]
            self.assertTrue(g["source"] and "Not a reference value" in g["use"])
            temps = [j["T_K"] for j in m["jobs"] if j["kind"] == "coexistence"]
            self.assertLess(min(temps), g["value_K"])
            self.assertGreater(max(temps), g["value_K"])

    def test_inputs_never_use_single_phase_heating_or_langevin(self) -> None:
        for name in ("in.coexist.lmp", "in.latent.lmp"):
            body = "\n".join(l for l in (METALS / name).read_text().splitlines() if not l.lstrip().startswith("#"))
            self.assertNotIn("langevin", body.lower())
        coexist = (METALS / "in.coexist.lmp").read_text()
        self.assertIn("region          upper", coexist)
        self.assertIn("setforce", coexist)
        self.assertNotIn("dilate partial", coexist, "obsolete LAMMPS syntax")
        latent = (METALS / "in.latent.lmp").read_text()
        self.assertIn("Thot", latent)
        self.assertIn("MELT_CHECK", latent)

    def test_job_commands_reference_existing_inputs(self) -> None:
        for el in ELEMENTS:
            for job in self.manifest(el)["jobs"]:
                inp = re.search(r"-in (\S+)", job["command"]).group(1)
                self.assertTrue((METALS / el / inp).resolve().is_file(), job["id"])
                self.assertIn("-var T ", job["command"])


def series(n: int, start: float, end: float, noise: float, seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    return [start + (end - start) * i / (n - 1) + rng.gauss(0, noise) for i in range(n)]


def write_pe(path: Path, t: float, pe: list[float]) -> Path:
    path.write_text(f"# coexistence T_target_K={t} n_atoms=1000 | step T_K pe_per_atom_eV volume_A3 lz_A\n" +
                    "".join(f"{i * 50} {t} {v} 1.0 1.0\n" for i, v in enumerate(pe)))
    return path


class AnalyzeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_verdicts(self) -> None:
        self.assertEqual(analyze.coexist_verdict(series(500, -3.00, -2.97, 1e-4))["verdict"], "melting")
        self.assertEqual(analyze.coexist_verdict(series(500, -3.00, -3.03, 1e-4))["verdict"], "freezing")
        self.assertEqual(analyze.coexist_verdict(series(500, -3.00, -3.0005, 1e-4))["verdict"], "stable")

    def test_noise_raises_the_threshold(self) -> None:
        r = analyze.coexist_verdict(series(500, -3.00, -2.995, 5e-3))
        self.assertGreater(r["threshold_eV_per_atom"], analyze.PE_THRESHOLD)

    def test_melting_point_from_bracket(self) -> None:
        files = [write_pe(self.tmp / f"pe_T{t}.dat", t, series(500, -3, -3 + d, 1e-4, seed=int(t)))
                 for t, d in [(400, -0.03), (450, -0.02), (500, 0.02), (550, 0.03)]]
        out = analyze.analyze_coexist(files)
        self.assertEqual((out["status"], out["value"], out["uncertainty"], out["bracket_K"]),
                         ("ok", 475.0, 25.0, [450.0, 500.0]))

    def test_bracket_failures(self) -> None:
        up = [write_pe(self.tmp / f"a{t}.dat", t, series(500, -3, -2.97, 1e-4)) for t in (400, 500)]
        self.assertEqual(analyze.analyze_coexist(up)["failure"], "below_bracket")
        down = [write_pe(self.tmp / f"b{t}.dat", t, series(500, -3, -3.03, 1e-4)) for t in (400, 500)]
        self.assertEqual(analyze.analyze_coexist(down)["failure"], "above_bracket")
        mixed = [write_pe(self.tmp / "c1.dat", 400, series(500, -3, -2.97, 1e-4)),
                 write_pe(self.tmp / "c2.dat", 500, series(500, -3, -3.03, 1e-4))]
        self.assertEqual(analyze.analyze_coexist(mixed)["failure"], "inconsistent")

    def write_h(self, phase: str, pe: float, t: float = 500.0) -> Path:
        path = self.tmp / f"h_{phase}.dat"
        rng = random.Random(phase)
        path.write_text(f"# latent phase={phase} T_target_K={t} n_atoms=400 | step T_K pe ke vol\n" +
                        "".join(f"{i} {t} {pe + rng.gauss(0, 1e-4)} 0.0646 7000\n" for i in range(400)))
        return path

    def test_latent_heat_conversion_and_phase_checks(self) -> None:
        s, l = self.write_h("solid", -3.10), self.write_h("liquid", -3.03)
        log_s, log_l = self.tmp / "s.log", self.tmp / "l.log"
        log_s.write_text("PHASE_CHECK phase=solid moved_fraction 0.01 (x)\n")
        log_l.write_text("PHASE_CHECK phase=liquid moved_fraction 0.95 (x)\n")
        out = analyze.analyze_latent(s, l, 118.71, log_s, log_l)
        self.assertEqual(out["status"], "ok")
        expected = 0.07 * analyze.EV_TO_J * analyze.N_A / 0.11871
        self.assertTrue(math.isclose(out["value"], expected, rel_tol=0.01), (out["value"], expected))
        log_l.write_text("PHASE_CHECK phase=liquid moved_fraction 0.10 (x)\n")
        self.assertEqual(analyze.analyze_latent(s, l, 118.71, log_s, log_l)["failure"], "phase_check")

    def test_latent_without_logs_warns(self) -> None:
        out = analyze.analyze_latent(self.write_h("solid", -3.1), self.write_h("liquid", -3.0), 100.0)
        self.assertIn("warning", out)

    def test_swapped_phases_fail(self) -> None:
        out = analyze.analyze_latent(self.write_h("liquid", -3.0), self.write_h("solid", -3.1), 100.0)
        self.assertEqual(out["status"], "failed")


if __name__ == "__main__":
    unittest.main()
