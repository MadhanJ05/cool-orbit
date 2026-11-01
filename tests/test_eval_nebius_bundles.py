"""Tests for the Nebius metal bundles (data/nebius/) and their analysis script. No LAMMPS needed.

Synthetic PE/enthalpy series below are SYNTHETIC test fixtures. Real LAMMPS outputs (laptop validation, not
results) are read from data/nebius/metals/Sn/validation/.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import importlib.util
import json
import math
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
METALS = REPO / "data" / "nebius" / "metals"
EAM = ["Cu", "Al"]                  # high-melting validation metals, Mishin EAM (pair_style eam/alloy)
ACTIVE = ["In", "Sn", "Bi"] + EAM
ALL = ["Ga", "In", "Sn", "Bi"] + EAM
VALIDATION = METALS / "Sn" / "validation"

spec = importlib.util.spec_from_file_location("nebius_analyze", METALS / "analyze.py")
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


def manifest(el: str) -> dict:
    return json.loads((METALS / el / "manifest.json").read_text())


def read_data_header(path: Path) -> dict:
    text = path.read_text()
    out = {"atoms": int(re.search(r"(\d+) atoms", text).group(1))}
    for ax in "xyz":
        lo, hi = re.search(rf"(\S+)\s+(\S+)\s+{ax}lo {ax}hi", text).groups()
        out[ax] = float(hi) - float(lo)
    out["triclinic"] = "xy xz yz" in text
    return out


def code(name: str) -> str:
    return "\n".join(l for l in (METALS / name).read_text().splitlines() if not l.lstrip().startswith("#"))


class BundleTest(unittest.TestCase):
    def test_files_and_nothing_submitted(self) -> None:
        for el in ALL:
            for f in ("data.coexist.lmp", "data.solid.lmp", "potential.mod", "manifest.json", "potential/SOURCES.md"):
                self.assertTrue((METALS / el / f).is_file(), f"{el}/{f}")
            self.assertNotIn("submitted", manifest(el)["status"].replace("NOT submitted", ""))

    def test_ga_is_dropped_with_evidence(self) -> None:
        m = manifest("Ga")
        self.assertTrue(m["status"].startswith("DROPPED"))
        self.assertEqual(m["jobs"], [])
        for f in ("RESULT.md", "in.ga_stability.lmp", "check_ga.py", "log.ga_stability", "rdf_T302.92.dat"):
            self.assertTrue((METALS / "Ga" / "stability" / f).is_file(), f)
        out = subprocess.run([sys.executable, "stability/check_ga.py", "stability/log.ga_stability",
                              "stability/rdf_T302.92.dat"], cwd=METALS / "Ga", capture_output=True, text=True)
        self.assertEqual(out.returncode, 1, "the committed evidence must reproduce the failed verdict")
        v = json.loads(out.stdout)
        self.assertTrue(v["checks"]["alpha_below_fcc_at_0K"])
        self.assertTrue(v["checks"]["close_pair_survives_at_T"], "the close pair survives (Madhan's re-review)")
        self.assertFalse(v["checks"]["relaxed_cell_within_3pct_of_experiment"])
        self.assertFalse(v["checks"]["next_shell_stays_beyond_2.6A_at_T"])
        self.assertAlmostEqual(v["rdf_first_local_max_A"], 2.67, places=2)
        self.assertAlmostEqual(v["pair_distance_running_count_1_A"], 2.51, places=2)
        self.assertTrue((METALS / "Ga" / "stability" / "alpha_relaxed.lmp").is_file())
        status = manifest("Ga")["status"]
        self.assertIn("collapses toward the close pair", status)
        self.assertNotIn("dimer is lost", status)

    def test_first_peak_is_the_first_local_maximum_not_the_tallest(self) -> None:
        spec2 = importlib.util.spec_from_file_location("check_ga", METALS / "Ga" / "stability" / "check_ga.py")
        check_ga = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(check_ga)
        with tempfile.TemporaryDirectory() as tmp:
            rdf = Path(tmp) / "rdf.dat"
            g = [0.0] * 200
            g[5] = 0.004               # counting noise below the first shell: ignored (g <= 1)
            g[120], g[121], g[122] = 1.5, 2.0, 1.6    # small first peak at r = 2.43 A
            g[135], g[136], g[137] = 3.0, 4.0, 3.0    # tallest peak at r = 2.73 A
            count = 0.0
            lines = ["# Time-averaged data", "# TimeStep Number-of-rows", "# Row r g count", "10000 200"]
            for i in range(200):
                count += g[i] * 0.05
                lines.append(f"{i + 1} {0.01 + 0.02 * i:.2f} {g[i]} {count}")
            rdf.write_text("\n".join(lines) + "\n")
            self.assertAlmostEqual(check_ga.first_peak(rdf), 2.43, places=2)

    def test_potential_files_match_their_recorded_hashes(self) -> None:
        for el in ALL:
            for name, digest in manifest(el)["potential"]["files"].items():
                data = (METALS / el / "potential" / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, f"{el}/{name} changed")

    def test_potential_mod_points_at_existing_files(self) -> None:
        for el in ALL:
            mod = (METALS / el / "potential.mod").read_text()
            line = next(l for l in mod.splitlines() if l.startswith("pair_coeff"))
            self.assertTrue(line.rstrip().endswith(f" {el}"))
            for tok in line.split():
                if tok.startswith("potential/"):
                    self.assertTrue((METALS / el / tok).is_file(), f"{el}: {tok}")

    def test_structures_match_the_manifest_and_are_orthogonal(self) -> None:
        for el in ALL:
            s = manifest(el)["structure"]
            for key in ("coexist", "solid"):
                h = read_data_header(METALS / el / s[key]["file"])
                self.assertEqual(h["atoms"], s[key]["n_atoms"], f"{el} {key}")
                self.assertFalse(h["triclinic"])

    def test_guesses_come_from_the_calibration_set(self) -> None:
        cal = json.loads((REPO / "data" / "calibration" / "tier1.json").read_text())
        recs = cal["records"] if isinstance(cal, dict) else cal
        for el in ACTIVE:
            m = manifest(el)
            (ref,) = [r["value"] for r in recs if r["candidate"] == el and r["property"] == "melting_point"]
            self.assertEqual(m["melting_point_guess"]["value_K"], ref)
            self.assertIn("tier1.json", m["melting_point_guess"]["source"])
            temps = [j["T_K"] for j in m["jobs"] if j["kind"] == "coexistence"]
            self.assertLess(min(temps), ref)
            self.assertGreater(max(temps), ref)

    def test_jobs_have_own_logs_and_latent_is_blocked(self) -> None:
        for el in ACTIVE:
            jobs = manifest(el)["jobs"]
            logs = [re.search(r"-log (\S+)", j["command"]).group(1) for j in jobs]
            self.assertEqual(len(set(logs)), len(jobs), "one log per job")
            for j in jobs:
                self.assertIn(f"log_{j['id']}.lammps", j["command"])
                inp = re.search(r"-in (\S+)", j["command"]).group(1)
                self.assertTrue((METALS / el / inp).resolve().is_file())
                self.assertNotRegex(j["command"], r"[<>]", "no angle brackets: they break shells")
                if j["kind"] == "latent":
                    self.assertEqual(j["T_K"], "TM_MEAM_K")
                    self.assertTrue(j["status"].startswith("BLOCKED"))
                    self.assertIn("-var T TM_MEAM_K", j["command"])

    def test_melt_temperature_is_three_times_prep(self) -> None:
        for el in ACTIVE:
            s = manifest(el)["settings"]
            self.assertAlmostEqual(s["T_melt_K"], round(3.0 * s["T_prep_K"], 1))
            for j in manifest(el)["jobs"]:
                if j["kind"] == "coexistence":
                    self.assertIn(f"-var Tmelt {s['T_melt_K']}", j["command"])

    def test_method_block_and_mass(self) -> None:
        for el in ACTIVE:
            m = manifest(el)
            self.assertEqual(m["method"]["engine"], "LAMMPS")
            self.assertIn(m["potential"]["nist_implementation"], m["method"]["model"])
            from ase.data import atomic_masses, atomic_numbers
            self.assertAlmostEqual(m["atomic_mass_amu"], atomic_masses[atomic_numbers[el]], places=2)

    def test_eam_bundles_use_eam_alloy_and_record_their_nist_source(self) -> None:
        for el in EAM:
            m, mod = manifest(el), (METALS / el / "potential.mod").read_text()
            self.assertIn("\npair_style eam/alloy\n", mod)
            self.assertNotIn("meam", mod.lower())
            self.assertTrue(m["method"]["model"].startswith("EAM, NIST IPR "))
            p = m["potential"]
            for name in p["files"]:
                self.assertTrue(p["download_urls"][name].startswith("https://www.ctcms.nist.gov/potentials/Download/"))
                header = (METALS / el / "potential" / name).read_text().splitlines()[3].split()
                self.assertEqual(header, ["1", el], "setfl header: one element, this one")
            self.assertTrue(p["nist_implementation_url"].endswith(f"{p['nist_implementation']}.html"))
            self.assertEqual(len(p["release_notes"]["sha256"]), 64)

    def test_coexist_thermostat_measures_only_the_liquid(self) -> None:
        c = code("in.coexist.lmp")
        self.assertIn("compute         tliq liquid temp", c)
        self.assertIn("fix_modify      hot temp tliq", c)
        self.assertIn("fix_modify      cool temp tliq", c)
        self.assertNotIn("dilate partial", c)
        self.assertIn("melt_liquid_moved=${melt_liq}", c)

    def test_latent_solid_uses_an_anisotropic_barostat(self) -> None:
        c = code("in.latent.lmp")
        self.assertIn('"${phase} == solid" then "variable couple string aniso"', c)
        self.assertIn("${couple} 0.0 0.0 ${pdamp}", c)
        self.assertIn("Thot", c)

    def test_headers_are_written_with_print_not_fix_print_title(self) -> None:
        for name in ("in.coexist.lmp", "in.latent.lmp"):
            c = code(name)
            self.assertNotIn('title "#', c, "fix print writes its title without substituting variables")
            self.assertRegex(c, r'print\s+"# \w+ .*T_target_K=\$\{T\}.*" file ')
            self.assertIn(" append ", c)

    def test_no_wrong_density_comment_and_no_bad_methods(self) -> None:
        for name in ("in.coexist.lmp", "in.latent.lmp"):
            text = (METALS / name).read_text()
            self.assertNotIn("liquid is less dense", text)
            self.assertNotIn("langevin", code(name).lower())


class RealOutputTest(unittest.TestCase):
    """Real LAMMPS outputs from the laptop validation (marked validation=1; not results)."""

    def test_real_headers_carry_numbers(self) -> None:
        header, rows = analyze.read_columns(VALIDATION / "pe_T505.1.dat")
        self.assertEqual(float(header["T_target_K"]), 505.1)
        self.assertGreater(float(header["melt_liquid_moved"]), 0.8)
        self.assertEqual(header["validation"], "1")
        self.assertEqual(len(rows[0]), 8)

    def test_real_validation_coexistence_is_refused(self) -> None:
        out = analyze.analyze_coexist([VALIDATION / "pe_T505.1.dat"], manifest=manifest("Sn"))
        self.assertEqual((out["status"], out["failure"]), ("failed", "melt_check"))
        self.assertIn("VALIDATION", out["reason"])

    def test_real_validation_latent_is_refused(self) -> None:
        out = analyze.analyze_latent(VALIDATION / "h_solid_T505.1.dat", VALIDATION / "h_liquid_T505.1.dat",
                                     log_solid=VALIDATION / "log_val_solid.lammps",
                                     log_liquid=VALIDATION / "log_val_liquid.lammps", manifest=manifest("Sn"))
        self.assertEqual(out["status"], "failed")
        self.assertIn("VALIDATION", out["reason"])
        self.assertIn("MEASURED melting point", out["reason"])
        self.assertNotIn("value", out, "no J/kg for validation data")
        # the file parsing itself still works on real output
        self.assertLess(max(abs(x) for x in out["solid"]["box_shape_change"]), 0.03)
        self.assertEqual((out["method"]["n_atoms"], out["method"]["timestep_fs"]), (448, 2.0))

    def test_unmarked_run_at_the_measured_tm_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for ph in ("solid", "liquid"):
                text = (VALIDATION / f"h_{ph}_T505.1.dat").read_text().replace(" validation=1", "", 1)
                (Path(tmp) / f"h_{ph}.dat").write_text(text)
            out = analyze.analyze_latent(Path(tmp) / "h_solid.dat", Path(tmp) / "h_liquid.dat",
                                         log_solid=VALIDATION / "log_val_solid.lammps",
                                         log_liquid=VALIDATION / "log_val_liquid.lammps", manifest=manifest("Sn"))
        self.assertEqual(out["status"], "failed")
        self.assertIn("MEASURED melting point", out["reason"])
        self.assertNotIn("VALIDATION", out["reason"])

    def test_in_and_bi_validation_logs_are_committed(self) -> None:
        for el in ("In", "Bi"):
            log = (METALS / el / "validation" / "log_val_prep.lammps").read_text()
            moved = re.findall(r"^MELT_CHECK liquid_moved_fraction ([0-9.]+) crystal_moved_fraction ([0-9.]+)", log, re.M)
            self.assertTrue(moved, el)
            self.assertGreaterEqual(float(moved[-1][0]), 0.8)
            self.assertLessEqual(float(moved[-1][1]), 0.05)

    def test_missing_manifest_warns(self) -> None:
        analyze.WARNINGS.clear()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(analyze.load_manifest(Path("/nonexistent/manifest.json")), {})
        self.assertIn("manifest", err.getvalue())
        self.assertTrue(analyze.WARNINGS)


def series(n: int, start: float, end: float, noise: float, seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    return [start + (end - start) * i / (n - 1) + rng.gauss(0, noise) for i in range(n)]


def write_pe(path: Path, t: float, pe: list[float], melt_liq: float | None = 0.95, melt_cry: float = 0.0,
             melt_t: float = 1500.0) -> Path:
    melt = (f" Tmelt_K=1500 melt_liquid_moved={melt_liq} melt_crystal_moved={melt_cry} melt_liquid_T_K={melt_t}"
            if melt_liq is not None else "")
    path.write_text(f"# coexistence T_target_K={t} n_atoms=1000{melt} dt_ps=0.002 prod_steps=25000 seed=1 | cols\n"
                    "# Fix print output for fix log\n" +
                    "".join(f"{i * 50} {t} {v} 1.0 1.0 1.0 1.0 0.0\n" for i, v in enumerate(pe)))
    return path


class AnalyzeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def bracket(self, deltas, **kw):
        return [write_pe(self.tmp / f"pe_T{t}.dat", t, series(500, -3, -3 + d, 1e-4, seed=int(t)), **kw)
                for t, d in deltas]

    def test_verdicts(self) -> None:
        self.assertEqual(analyze.coexist_verdict(series(500, -3.00, -2.97, 1e-4))["verdict"], "melting")
        self.assertEqual(analyze.coexist_verdict(series(500, -3.00, -3.03, 1e-4))["verdict"], "freezing")
        self.assertEqual(analyze.coexist_verdict(series(500, -3.00, -3.0005, 1e-4))["verdict"], "stable")

    def test_melting_point_is_interpolated_within_the_bracket(self) -> None:
        out = analyze.analyze_coexist(self.bracket([(400, -0.03), (450, -0.02), (500, 0.06), (550, 0.08)]))
        self.assertEqual((out["status"], out["bracket_K"], out["uncertainty"]), ("ok", [450.0, 500.0], 25.0))
        # line through (450, -0.02) and (500, +0.06) crosses zero at 462.5 K, not the 475 K midpoint
        self.assertAlmostEqual(out["value"], 462.5, delta=1.0)

    def test_failed_melt_check_fails_the_analysis(self) -> None:
        files = self.bracket([(400, -0.03), (500, 0.03)])
        files.append(write_pe(self.tmp / "pe_T450.dat", 450, series(500, -3, -3.02, 1e-4), melt_liq=0.3))
        out = analyze.analyze_coexist(files)
        self.assertEqual((out["status"], out["failure"]), ("failed", "melt_check"))
        self.assertIn("single-phase heating", out["reason"])

    def test_wrong_liquid_temperature_during_the_melt_fails(self) -> None:
        out = analyze.analyze_coexist(self.bracket([(400, -0.03), (500, 0.03)], melt_t=3000.0))
        self.assertEqual(out["failure"], "melt_check")
        self.assertIn("not the set", out["reason"])

    def test_validation_flag_is_refused(self) -> None:
        files = self.bracket([(400, -0.03), (500, 0.03)])
        text = files[0].read_text().replace("T_target_K=400", "T_target_K=400 validation=1", 1)
        files[0].write_text(text)
        self.assertIn("VALIDATION", analyze.analyze_coexist(files)["reason"])

    def test_missing_melt_check_fails(self) -> None:
        out = analyze.analyze_coexist(self.bracket([(400, -0.03), (500, 0.03)], melt_liq=None))
        self.assertEqual(out["failure"], "melt_check")

    def test_bracket_failures(self) -> None:
        self.assertEqual(analyze.analyze_coexist(self.bracket([(400, 0.03), (500, 0.03)]))["failure"], "below_bracket")

    def write_h(self, phase: str, pe: float, *, drift: float = 0.0, shape: float = 0.0, barostat: str | None = None,
                t: float = 500.0) -> Path:
        b = barostat or ("aniso" if phase == "solid" else "iso")
        path = self.tmp / f"h_{phase}.dat"
        rng = random.Random(phase)
        rows = []
        for i in range(500):
            f = i / 499
            rows.append(f"{i} {t} {pe + drift * f + rng.gauss(0, 1e-4)} 0.0646 7000 20 {20 * (1 + shape * f)} 20 0\n")
        path.write_text(f"# latent phase={phase} T_target_K={t} n_atoms=400 barostat={b} dt_ps=0.002 "
                        "prod_steps=25000 seed=1 | cols\n# Fix print output for fix log\n" + "".join(rows))
        return path

    def logs(self, solid=0.0, liquid=0.95):
        ls, ll = self.tmp / "s.log", self.tmp / "l.log"
        ls.write_text(f"PHASE_CHECK phase=solid moved_fraction {solid} (x)\n")
        ll.write_text(f"PHASE_CHECK phase=liquid moved_fraction {liquid} (x)\n")
        return ls, ll

    def test_latent_heat_conversion(self) -> None:
        out = analyze.analyze_latent(self.write_h("solid", -3.10), self.write_h("liquid", -3.03), 118.71, *self.logs())
        self.assertEqual(out["status"], "ok")
        expected = 0.07 * analyze.EV_TO_J * analyze.N_A / 0.11871
        self.assertTrue(math.isclose(out["value"], expected, rel_tol=0.01))

    def test_enthalpy_drift_is_flagged(self) -> None:
        out = analyze.analyze_latent(self.write_h("solid", -3.10, drift=0.02), self.write_h("liquid", -3.03),
                                     118.71, *self.logs())
        self.assertIn("enthalpy drifts", out["reason"])

    def test_solid_shape_change_is_flagged(self) -> None:
        out = analyze.analyze_latent(self.write_h("solid", -3.10, shape=0.08), self.write_h("liquid", -3.03),
                                     118.71, *self.logs())
        self.assertIn("box shape changed", out["reason"])

    def test_isotropic_solid_is_flagged(self) -> None:
        out = analyze.analyze_latent(self.write_h("solid", -3.10, barostat="iso"), self.write_h("liquid", -3.03),
                                     118.71, *self.logs())
        self.assertIn("aniso", out["reason"])

    def test_recrystallized_liquid_is_flagged(self) -> None:
        out = analyze.analyze_latent(self.write_h("solid", -3.10), self.write_h("liquid", -3.03), 118.71,
                                     *self.logs(liquid=0.1))
        self.assertEqual(out["failure"], "phase_check")


if __name__ == "__main__":
    unittest.main()
