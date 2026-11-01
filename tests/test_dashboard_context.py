"""Tests for dashboard/context.py (the page's question, burst calculation and methods) and its place in the export.

No network. Boards here are SYNTHETIC (dashboard.demo or seeded test boards); test-only inputs say SYNTHETIC.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from coolorbit.board import Board
from coolorbit.eval.tasks import load_tasks
from dashboard.context import (BURST_INPUTS, DEFAULT_SCENARIO, LLM_ROLES, REPO, SCHEMA, SCOUT_SEEDS, build_context,
                               burst, classical_rows, load_burst_calc, short_model_name)
from dashboard.demo import build_all
from dashboard.export import _scored_task_ids, export

DATA_JS_PREFIX = "window.COOL_ORBIT_DATA = "


def _numeric(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def unsourced_numbers(node, path: str = "context") -> list[str]:
    """Paths of dicts that hold a number but no non-empty 'source', and of bare numbers inside lists."""
    bad = []
    if isinstance(node, dict):
        if any(_numeric(v) for v in node.values()) and not str(node.get("source") or "").strip():
            bad.append(path)
        for k, v in node.items():
            bad += unsourced_numbers(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            bad += [f"{path}[{i}]"] if _numeric(v) else unsourced_numbers(v, f"{path}[{i}]")
    return bad


def run_script(*argv: str) -> dict:
    """scripts/burst_calc.py's own --json output."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        load_burst_calc().main(["--json", *argv])
    return json.loads(out.getvalue())


def write_inputs(folder: Path, materials: list[dict], fin_patch: dict | None = None) -> Path:
    data = json.loads(BURST_INPUTS.read_text(encoding="utf-8"))
    data["materials"] = materials
    if fin_patch:
        data["fin_material"]["density_kg_m3"].update(fin_patch)
    path = Path(folder) / "inputs.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class BurstMatchesScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ctx = build_context()
        cls.script = run_script()

    def test_default_scenario_is_the_scripts(self) -> None:
        self.assertEqual(self.script["scenario"], DEFAULT_SCENARIO)
        scen = self.ctx["burst"]["scenario"]
        for k, v in self.script["scenario"].items():
            self.assertEqual(scen[k], v, k)
        self.assertEqual(scen["energy_kJ"], DEFAULT_SCENARIO["power_W"] * DEFAULT_SCENARIO["minutes"] * 60 / 1000)

    def test_every_computed_number_matches_the_script(self) -> None:
        mats = {m["name"]: m for m in self.ctx["burst"]["materials"]}
        self.assertEqual(set(mats), {r["material"] for r in self.script["results"]})
        self.assertTrue(mats, "the repo inputs should compute at least one material")
        for row in self.script["results"]:
            got = mats[row["material"]]["result"]
            for k, v in row.items():
                if k != "material":
                    self.assertEqual(got[k], v, f"{row['material']}: {k}")

    def test_skipped_materials_match_the_script(self) -> None:
        self.assertEqual([(s["material"], s["missing"]) for s in self.ctx["burst"]["skipped"]],
                         [(s["material"], s["missing"]) for s in self.script["skipped_unsourced"]])

    def test_other_scenarios_match_too(self) -> None:
        script = run_script("--power", "500", "--minutes", "5", "--dT", "5", "--fin-mm", "1")
        got = burst(scenario={"power_W": 500.0, "minutes": 5.0, "dT_K": 5.0, "fin_mm": 1.0})
        mats = {m["name"]: m["result"] for m in got["materials"]}
        for row in script["results"]:
            for k, v in row.items():
                if k != "material":
                    self.assertEqual(mats[row["material"]][k], v, f"{row['material']}: {k}")

    def test_inputs_shown_are_the_sourced_inputs(self) -> None:
        raw = {m["name"]: m for m in json.loads(BURST_INPUTS.read_text(encoding="utf-8"))["materials"]}
        for m in self.ctx["burst"]["materials"]:
            for key, x in m["inputs"].items():
                self.assertEqual(x["value"], raw[m["name"]][key]["value"])
                self.assertEqual(x["source"], raw[m["name"]][key]["source"].strip())

    def test_ratios_and_per_litre_values_are_computed_from_the_inputs(self) -> None:
        b = self.ctx["burst"]
        res = {m["label"]: m["result"] for m in b["materials"]}
        for c in b["comparisons"]:
            w, m = res[c["wax"]], res[c["metal"]]
            self.assertAlmostEqual(c["total_kg"]["value"], m["total_kg"] / w["total_kg"], places=2)
            self.assertAlmostEqual(c["area"]["value"], w["area_m2"] / m["area_m2"], places=2)
            self.assertAlmostEqual(c["volume"]["value"], w["pcm_volume_L"] / m["pcm_volume_L"], places=2)
        for m in b["materials"]:
            i = m["inputs"]
            per_l = i["latent_heat_J_per_kg"]["value"] * min(i["density_solid_kg_m3"]["value"],
                                                             i["density_liquid_kg_m3"]["value"]) / 1e6
            self.assertAlmostEqual(m["derived"]["latent_heat_kJ_per_L"]["value"], per_l, places=2)


class SourcesTest(unittest.TestCase):
    def test_every_number_in_the_context_has_a_source(self) -> None:
        self.assertEqual(unsourced_numbers(build_context()), [])

    def test_the_check_itself_catches_a_bare_number(self) -> None:
        self.assertEqual(unsourced_numbers({"a": {"value": 1.0}, "b": [2]}), ["context.a", "context.b[0]"])
        self.assertEqual(unsourced_numbers({"a": {"value": 1.0, "source": "x"}, "ok": True}), [])

    def test_framing_lines_cite_the_claims_register(self) -> None:
        q = build_context()["question"]
        self.assertTrue(q["title"].startswith("Which thermal battery"))
        for line in q["lines"]:
            self.assertIn("claims register", line["source"])
            self.assertTrue(line["url"].startswith("https://"))


class UnsourcedMaterialsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = json.loads(BURST_INPUTS.read_text(encoding="utf-8"))["materials"]
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_repo_inputs_list_every_unsourced_material_as_skipped(self) -> None:
        bc = load_burst_calc()
        expected = [m["name"] for m in self.base if any(not bc.sourced(m.get(k, {})) for k in bc.NEEDED)]
        b = build_context()["burst"]
        self.assertEqual([s["material"] for s in b["skipped"]], expected)
        self.assertFalse({m["name"] for m in b["materials"]} & set(expected))
        for s in b["skipped"]:
            self.assertTrue(s["missing"])
            self.assertEqual(len(s["missing_labels"]), len(s["missing"]))

    def test_a_value_without_a_source_is_skipped_never_computed(self) -> None:
        wax = copy.deepcopy(self.base[0])
        ghost = copy.deepcopy(self.base[1])
        ghost["name"] = "SYNTHETIC test metal"
        ghost["conductivity_liquid_W_mK"] = {"value": 31.4159, "source": "  "}
        b = burst(write_inputs(self._tmp.name, [wax, ghost]))
        self.assertEqual([m["name"] for m in b["materials"]], [wax["name"]])
        (skip,) = b["skipped"]
        self.assertEqual((skip["material"], skip["missing"]), ("SYNTHETIC test metal", ["conductivity_liquid_W_mK"]))
        self.assertNotIn("conductivity_liquid_W_mK", {x["key"] for x in skip["sourced"]})
        self.assertNotIn("31.4159", json.dumps(b), "an unsourced value must not reach the page")
        self.assertEqual(b["comparisons"], [])

    def test_unsourced_fin_material_is_an_error_not_a_guess(self) -> None:
        b = burst(write_inputs(self._tmp.name, self.base, fin_patch={"source": None}))
        self.assertIn("error", b)
        self.assertNotIn("materials", b)

    def test_takeaway_only_when_the_numbers_support_it(self) -> None:
        wax = copy.deepcopy(self.base[0])
        metal = copy.deepcopy(self.base[1])
        metal["name"] = "SYNTHETIC test metal"
        metal["latent_heat_J_per_kg"] = {"value": 900000, "source": "SYNTHETIC test value"}  # lighter than wax
        b = burst(write_inputs(self._tmp.name, [wax, metal]))
        self.assertLess(b["comparisons"][0]["total_kg"]["value"], 1)
        self.assertNotIn("wax wins", b["takeaway"])
        repo = build_context()["burst"]
        if all(c["total_kg"]["value"] > 1 and c["area"]["value"] > 1 and c["volume"]["value"] > 1
               for c in repo["comparisons"]) and repo["comparisons"]:
            self.assertIn("wax wins", repo["takeaway"])


class ClassicalGateTest(unittest.TestCase):
    def test_a_potential_counts_only_within_the_tier1_tolerance(self) -> None:
        rules = {"properties": {"melting_point": {"tier1": {"rel": 0.10}}}}
        calibration = {("bi", "melting_point"): {"value": 544.556, "units": "K", "source": "SYNTHETIC test source"},
                       ("sn", "melting_point"): {"value": 505.078, "units": "K", "source": "SYNTHETIC test source"}}
        with tempfile.TemporaryDirectory() as tmp:
            for el, value in (("Bi", 581.4), ("Sn", 357.6), ("Xx", 400.0)):
                f = Path(tmp) / "run" / "metals" / el / "analysis_coexistence.json"
                f.parent.mkdir(parents=True)
                f.write_text(json.dumps({"status": "ok", "value": value, "uncertainty": 10.0, "units": "K",
                                         "method": {"model": "SYNTHETIC"}}))
            rows = {r["element"]: r for r in classical_rows(rules, calibration, Path(tmp), "run")}
        self.assertTrue(rows["Bi"]["counts"])
        self.assertFalse(rows["Sn"]["counts"])
        self.assertFalse(rows["Xx"]["counts"], "no measured value: cannot be calibrated, so never counted")
        self.assertEqual(rows["Ga"]["status"], "dropped")
        self.assertEqual(unsourced_numbers(list(rows.values())), [])


class AgentGraphTest(unittest.TestCase):
    """The Agents tab's description of who does what around ALCHEMI must match the code it describes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ctx = build_context()
        cls.graph = cls.ctx["agent_graph"]
        cls.nodes = {n["id"]: n for n in cls.graph["nodes"]}
        cls.roles = json.loads(LLM_ROLES.read_text(encoding="utf-8"))["roles"]

    def test_short_model_names(self) -> None:
        self.assertEqual(short_model_name("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(short_model_name("claude-fable-5-1"), "Fable 5.1")
        self.assertEqual(short_model_name("gpt-x"), "gpt-x")
        self.assertIsNone(short_model_name(None))

    def test_models_come_from_llm_roles(self) -> None:
        for nid, role in (("premise", "premise_checker"), ("llm", "referee_llm_audit"),
                          ("adjudicator", "adjudicator"), ("worker", "arm_baseline")):
            model = self.roles[role]["model"]
            self.assertEqual(self.nodes[nid]["model"]["id"], model, nid)
            self.assertEqual(self.nodes[nid]["tag"], short_model_name(model).upper(), nid)
        for nid in ("scouts", "simulator", "alchemi", "rules", "classical", "librarian", "ranker", "trust", "board"):
            self.assertIsNone(self.nodes[nid]["model"], f"{nid}: its code makes no LLM call")

    def test_board_agent_names_are_the_names_the_code_posts_under(self) -> None:
        expect = {  # node: (agent name, file that defines it, the defining text)
            "premise": ("referee_premise", "coolorbit/agents/premise.py", 'AGENT_NAME = "referee_premise"'),
            "rules": ("referee_rules", "coolorbit/agents/referee_rules.py", 'AGENT_NAME = "referee_rules"'),
            "llm": ("referee_llm", "coolorbit/agents/referee_llm.py", 'AGENT_NAME = "referee_llm"'),
            "adjudicator": ("adjudicator", "coolorbit/agents/referee_llm.py", 'ADJUDICATOR_NAME = "adjudicator"'),
            "librarian": ("librarian", "coolorbit/agents/librarian.py", 'AGENT_NAME = "librarian"'),
            "ranker": ("ranker", "coolorbit/agents/ranker.py", 'AGENT_NAME = "ranker"'),
            "classical": ("referee_classical", "scripts/post_classical_checks.py", 'AGENT = "referee_classical"'),
            "worker": ("worker_cc", "scripts/run_arm_c.py", 'WORKER = "worker_cc"'),
            "simulator": ("simulator_gb10", "coolorbit/sim/simulator_agent.py", 'name: str = "simulator_gb10"'),
        }
        for nid, (agent, path, text) in expect.items():
            self.assertEqual(self.nodes[nid]["agents"], [agent], nid)
            self.assertIn(text, (REPO / path).read_text(encoding="utf-8"), path)
        seeds = json.loads(SCOUT_SEEDS.read_text(encoding="utf-8"))["scouts"]
        self.assertEqual(set(self.nodes["scouts"]["agents"]), set(seeds) | {"scout_alloys"})
        self.assertIn('AGENT_NAME = "scout_alloys"',
                      (REPO / "coolorbit/agents/alloy_scout.py").read_text(encoding="utf-8"))

    def test_rows_edges_and_code_paths(self) -> None:
        rows = {r["id"] for r in self.graph["rows"]}
        self.assertEqual(len(self.nodes), len(self.graph["nodes"]), "node ids are unique")
        for n in self.graph["nodes"]:
            self.assertIn(n["row"], rows, n["id"])
            self.assertTrue(n["source"].strip(), n["id"])
            self.assertTrue(n["code"], n["id"])
            for path in n["code"]:
                self.assertTrue((REPO / path).exists(), f"{n['id']}: {path}")
        for e in self.graph["edges"]:
            self.assertIn(e["from"], set(self.nodes) | rows, e["id"])
            self.assertIn(e["to"], set(self.nodes) | rows, e["id"])

    def test_alchemi_chips_are_imports_our_code_really_makes(self) -> None:
        alchemi = self.nodes["alchemi"]
        names = {i["name"] for i in alchemi["imports"]}
        self.assertTrue({"NPT", "MACEWrapper", "AtomicData", "Batch", "NeighborListHook", "DynamicsStage"} <= names)
        for i in alchemi["imports"]:
            self.assertTrue(i["module"].startswith("nvalchemi"), i)
            for path in i["files"]:
                text = (REPO / path).read_text(encoding="utf-8")
                self.assertIn(f"from {i['module']} import", text, i["name"])
                self.assertIn(i["name"], text)
        t = alchemi["throughput"]
        self.assertGreater(t["value"], 0)
        self.assertIn("GB10_ATOM_STEPS_PER_S", t["source"])

    def test_chemistry_lead_is_the_memo_author_and_its_numbers_are_the_applied_ones(self) -> None:
        lead = self.nodes["chemlead"]
        memo = (REPO / "docs/chemistry-lead-memo-2026-10-31.md").read_text(encoding="utf-8")
        self.assertIn(f"**Author:** {lead['model']['full_name']}", memo)
        self.assertEqual(lead["tag"], lead["model"]["name"].upper())
        self.assertEqual(lead["agents"], [], "the chemistry lead posts nothing on the board")
        rules = json.loads((REPO / "configs/trust_rules.json").read_text(encoding="utf-8"))
        ranker = json.loads((REPO / "coolorbit/agents/ranker_config.json").read_text(encoding="utf-8"))
        d = {x["id"]: x for x in lead["decisions"]}
        self.assertEqual(d["tol_melting_point"]["value"], rules["properties"]["melting_point"]["tier1"]["rel"])
        self.assertEqual(d["tol_latent_heat"]["value"], rules["properties"]["latent_heat"]["tier1"]["rel"])
        self.assertEqual((d["window"]["min"], d["window"]["max"]), (ranker["window_K"]["min"], ranker["window_K"]["max"]))
        for x in lead["decisions"]:
            self.assertIn("chemistry-lead-memo", x["source"], x["id"])
        edges = {e["id"]: e for e in self.graph["edges"]}
        for eid, to in (("e_rules_trust", "trust"), ("e_rules_gate", "classical"), ("e_rules_window", "ranker")):
            self.assertEqual((edges[eid]["from"], edges[eid]["to"]), ("chemlead", to))

    def test_every_loop_arc_is_backed_by_the_code_it_names(self) -> None:
        loops = self.graph["loops"]
        self.assertEqual([lp["n"] for lp in loops], list(range(1, len(loops) + 1)))
        ends = set(self.nodes)
        for lp in loops:
            self.assertIn(lp["from"], ends, lp["id"])
            self.assertIn(lp["to"], ends, lp["id"])
            self.assertTrue(lp["evidence"], lp["id"])
            for ev in lp["evidence"]:
                self.assertIn(ev["text"], (REPO / ev["path"]).read_text(encoding="utf-8"), f"{lp['id']}: {ev['path']}")

    def test_replay_cases(self) -> None:
        cases = self.graph["cases"]
        self.assertEqual((cases[0]["candidate"], cases[0]["property"], cases[0]["status"]), ("Cu", "melting_point", "trusted"))
        self.assertEqual({c["id"] for c in cases}, {"cu", "fields"})
        for c in cases:
            self.assertTrue(c["source"].strip())
        sim = self.nodes["simulator"]
        self.assertEqual(sim["agent_prefix"], "simulator")
        self.assertIn("simulator_h100", (REPO / "scripts/run_remote_simulator.py").read_text(encoding="utf-8"))

    def test_case_findings_and_deadline(self) -> None:
        case = self.graph["case"]
        self.assertEqual((case["candidate"], case["property"]), ("BiInSn", "latent_heat"))
        self.assertIn("S24", case["source"])
        findings = self.ctx["comparison_findings"]
        self.assertTrue(findings["url"].startswith("https://"))
        self.assertTrue((REPO / "docs/results/comparison-cmp1.md").exists())
        self.assertEqual(self.ctx["deadline"]["iso"], "2026-11-01T18:00:00Z")
        self.assertIn("CLAUDE.md", self.ctx["deadline"]["source"])


class ExportCarriesContextTest(unittest.TestCase):
    def test_data_js_carries_the_context_and_measured_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            paths = build_all(tmp / "boards")
            bundle = export(paths, tmp / "out", task_set=load_tasks(calibration_path=tmp / "none.json"))
            js = (tmp / "out" / "data.js").read_text(encoding="utf-8")
            index = json.loads((tmp / "out" / "index.json").read_text(encoding="utf-8"))
        payload = json.loads(js[len(DATA_JS_PREFIX):].rstrip().rstrip(";"))
        self.assertIn("context", payload)
        self.assertEqual(payload["context"], bundle["context"])
        self.assertEqual(payload["context"]["schema"], SCHEMA)
        self.assertNotIn("error", payload["context"])
        self.assertTrue(payload["context"]["burst"]["materials"])
        self.assertNotIn("context", index, "the context rides in data.js only")
        self.assertTrue(payload["index"]["tasks"])
        task_ids = {t["id"] for t in payload["index"]["tasks"]}
        self.assertLessEqual(set(payload["index"]["scored_task_ids"]), task_ids)

        collective = next(b for b in payload["boards"].values() if b["name"].endswith("collective"))
        refs = {(r["candidate"], r["property"]): r["reference"] for r in collective["results"]}
        self.assertEqual(refs[("DEMO-metal-1", "melting_point")]["value"], 1000.0)
        self.assertEqual(refs[("DEMO-metal-1", "melting_point")]["origin"], "board")
        self.assertIsNone(refs[("DEMO-wax-1", "melting_point")], "a null reference_value is not a measurement")

    def test_scored_task_ids_come_from_the_preregistration_note(self) -> None:
        class Stub:
            model_extra = {"_preregistration": "FROZEN before run x. Scored set: the 2 tasks task_a, task_b. "
                                               "The other tasks (task_c) are not run."}

            def by_id(self):
                return {"task_a": 1, "task_b": 2, "task_c": 3}

        self.assertEqual(_scored_task_ids(Stub()), ["task_a", "task_b"])
        Stub.model_extra = {}
        self.assertEqual(_scored_task_ids(Stub()), [])

    def test_calibration_set_is_the_fallback_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cal = tmp / "cal.json"
            cal.write_text(json.dumps({"records": [
                {"candidate": "SYNTHETIC-wax", "property": "melting_point", "value": 301.0, "units": "K",
                 "source": "SYNTHETIC test source"},
                {"candidate": "SYNTHETIC-wax", "property": "latent_heat", "value": None, "units": "J/kg",
                 "source": None}]}))
            b = Board(tmp / "real")
            cand = b.post("scout_x", "candidate", {"name": "SYNTHETIC-wax", "family": "paraffin", "tier": "trust"})
            for prop, value, units in (("melting_point", 300.0, "K"), ("latent_heat", 2.0e5, "J/kg")):
                b.post("simulator_x", "property_result", {
                    "candidate": "SYNTHETIC-wax", "family": "paraffin", "tier": "trust", "property": prop,
                    "value": value, "units": units, "note": "SYNTHETIC test",
                    "method": {"engine": "LAMMPS", "model": "SYNTHETIC", "protocol": "two-phase coexistence"}},
                    parents=[cand.artifact_id])
            bundle = export([b.root], tmp / "out", task_set=load_tasks(calibration_path=tmp / "none.json"),
                            calibration_path=cal, context={"schema": SCHEMA})
        (board,) = bundle["boards"].values()
        refs = {r["property"]: r["reference"] for r in board["results"]}
        self.assertEqual((refs["melting_point"]["value"], refs["melting_point"]["origin"]), (301.0, "calibration"))
        self.assertIsNone(refs["latent_heat"], "a calibration record without a value is not a measurement")
        self.assertEqual(bundle["context"], {"schema": SCHEMA})


class CatchesTests(unittest.TestCase):
    def test_every_catch_is_sourced_and_the_code_it_cites_exists(self) -> None:
        cases = build_context()["catches"]["cases"]
        self.assertGreaterEqual(len(cases), 3)
        for c in cases:
            self.assertIn(c["outcome"]["tone"], {"ok", "warn", "bad", "pend"})
            for part in ("shipped", "caught", "outcome"):
                self.assertTrue(c[part]["text"].strip(), f"{c['id']}.{part} has no text")
                self.assertTrue(c[part]["source"].strip(), f"{c['id']}.{part} has no source")
                for path in re.findall(r"\b(?:coolorbit|scripts|dashboard)/[\w/]+\.py\b", c[part]["source"]):
                    self.assertTrue((REPO / path).exists(), f"{c['id']}.{part} cites {path}, which does not exist")


if __name__ == "__main__":
    unittest.main()
