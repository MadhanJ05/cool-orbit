"""Cloud Run server, bucket reader, push script and per-task export discovery. No network, no GCP.

All boards here are SYNTHETIC (dashboard.demo or seeded test boards).
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from coolorbit.board import Board
from coolorbit.eval.tasks import TaskSet, load_tasks, seed_board
from dashboard import push_exports, server
from dashboard.demo import build_all
from dashboard.export import discover, export

RULES = {"frozen": False, "independent_check_types": ["second_mlip", "classical_md"], "rejection_min_referees": 2,
         "properties": {"melting_point": {"units": ["K"], "agree": {"abs": 20.0}, "tier1": {"abs": 25.0}}}}


class LocalServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        export(build_all(tmp / "boards"), tmp / "export", rules=RULES,
               task_set=load_tasks(calibration_path=tmp / "none.json"))
        cache = server.Cache(server.DirSource(tmp / "export"), ttl_s=30)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cache))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls._tmp.cleanup()

    def get(self, path: str, method: str = "GET"):
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_page_and_static_files_with_security_headers(self) -> None:
        code, headers, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Cool Orbit", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        for path in ("/app.js", "/style.css", "/index.html"):
            self.assertEqual(self.get(path)[0], 200, path)

    def test_exports_are_served(self) -> None:
        code, headers, body = self.get("/data/data.js")
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"window.COOL_ORBIT_DATA = "))
        self.assertIn("javascript", headers["Content-Type"])
        code, _, body = self.get("/data/index.json")
        self.assertEqual((code, json.loads(body)["schema"]), (200, "coolorbit.dashboard/1"))

    def test_nothing_else_is_reachable(self) -> None:
        for path in ("/server.py", "/export.py", "/Dockerfile", "/data/../server.py", "/data/%2e%2e/server.py",
                     "/data/x.py", "/data/", "/data/.hidden.json", "/../README.md", "/data/missing.json"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_health_and_head(self) -> None:
        self.assertEqual(self.get("/health")[:1], (200,))
        self.assertEqual(self.get("/healthz")[:1], (200,))
        code, _, body = self.get("/", method="HEAD")
        self.assertEqual((code, body), (200, b""))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class GcsSourceTest(unittest.TestCase):
    def test_token_reused_and_object_name_encoded(self) -> None:
        calls = []

        def opener(req, timeout):
            calls.append((req.full_url, dict(req.header_items())))
            if "metadata.google.internal" in req.full_url:
                return FakeResponse(json.dumps({"access_token": "tok", "expires_in": 3600}).encode())
            return FakeResponse(b"{}")

        src = server.GcsSource("coolorbit-dash", "dashboard", opener=opener)
        src.get("index.json")
        src.get("science-1.json")
        token_calls = [c for c in calls if "metadata.google.internal" in c[0]]
        self.assertEqual(len(token_calls), 1, "token cached")
        self.assertEqual(token_calls[0][1].get("Metadata-flavor"), "Google")
        obj_calls = [c for c in calls if "storage.googleapis.com" in c[0]]
        self.assertTrue(obj_calls[0][0].endswith("/b/coolorbit-dash/o/dashboard%2Findex.json?alt=media"))
        self.assertEqual(obj_calls[0][1]["Authorization"], "Bearer tok")

    def test_missing_object_is_not_found(self) -> None:
        def opener(req, timeout):
            if "metadata" in req.full_url:
                return FakeResponse(b'{"access_token": "t", "expires_in": 3600}')
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        with self.assertRaises(server.NotFound):
            server.GcsSource("coolorbit-dash", opener=opener).get("index.json")

    def test_bad_bucket_name(self) -> None:
        with self.assertRaises(ValueError):
            server.GcsSource("Bad/Bucket")

    def test_source_from_env(self) -> None:
        self.assertIsInstance(server.source_from_env({"BUCKET": "coolorbit-dash"}), server.GcsSource)
        self.assertIsInstance(server.source_from_env({"DATA_DIR": "/tmp"}), server.DirSource)
        with self.assertRaises(SystemExit):
            server.source_from_env({})


class RecordingUploader:
    def __init__(self) -> None:
        self.objects = []

    def upload(self, local, object_name, content_type) -> None:
        self.objects.append((object_name, content_type, Path(local).stat().st_size))


class PushTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_refuses_synthetic_boards(self) -> None:
        paths = build_all(self.tmp / "demo")
        up = RecordingUploader()
        with self.assertRaises(SystemExit):
            push_exports.push_once([str(p) for p in paths], up, log=lambda *_: None)
        self.assertEqual(up.objects, [], "nothing uploaded")

    def test_index_and_bundle_go_last(self) -> None:
        b = Board(self.tmp / "real")
        b.post("scout_x", "candidate", {"name": "n-eicosane", "family": "paraffin", "tier": "trust"})
        up = RecordingUploader()
        names = push_exports.push_once([str(b.root)], up, prefix="dashboard", log=lambda *_: None)
        self.assertEqual([n for n, _, _ in up.objects][-2:], ["dashboard/index.json", "dashboard/data.js"])
        self.assertEqual(names, [n for n, _, _ in up.objects])
        self.assertTrue(all(n.startswith("dashboard/") for n in names))
        types = {n.rsplit(".", 1)[1]: t for n, t, _ in up.objects}
        self.assertIn("json", types["json"])
        self.assertIn("javascript", types["js"])


class GcloudUploaderTest(unittest.TestCase):
    def test_command(self) -> None:
        seen = []
        up = push_exports.GcloudUploader("coolorbit-dash", runner=seen.append)
        up.upload(Path("/tmp/index.json"), "dashboard/index.json", "application/json; charset=utf-8")
        self.assertEqual(seen[0][:5], ["gcloud", "storage", "cp", "/tmp/index.json", "gs://coolorbit-dash/dashboard/index.json"])
        self.assertIn("--cache-control=no-cache", seen[0])
        self.assertIn("--content-type=application/json; charset=utf-8", seen[0])


class DiscoveryTest(unittest.TestCase):
    def test_runner_layout_is_exported_and_scored_per_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            full = load_tasks(calibration_path=tmp / "none.json")
            run = tmp / "eval" / "run1"
            for task_id in ("tier1_al_melting", "nvidia_lif_crystal_diffusion"):
                b = Board(run / "A-r1" / task_id)
                req = seed_board(b, TaskSet(version=1, frozen=False, tasks=[full.by_id()[task_id]]))[task_id]
                c = b.post("single_agent", "candidate", {"name": "SYNTHETIC", "family": "t", "tier": "discovery"},
                           fulfills=(req, 0))
                b.post("single_agent", "run_report", {"status": "refused", "task": task_id, "reason": "SYNTHETIC"},
                       parents=[c.artifact_id])
            failed = Board(run / "A-r1" / "_infra_failed" / "tier1_al_melting.try1")
            seed_board(failed, TaskSet(version=1, frozen=False, tasks=[full.by_id()["tier1_al_melting"]]))
            found = discover(tmp / "eval")
            self.assertEqual(sorted(p.name for p, _ in found), ["nvidia_lif_crystal_diffusion", "tier1_al_melting"])
            self.assertTrue(all(r is not None and r.name == "A-r1" for _, r in found))
            bundle = export([tmp / "eval"], tmp / "out", rules=RULES, task_set=full,
                            include_synthetic_in_comparison=True)
            entries = bundle["index"]["boards"]
            self.assertEqual({e["arm"] for e in entries}, {"A"})
            self.assertEqual({e["task_id"] for e in entries}, {"tier1_al_melting", "nvidia_lif_crystal_diffusion"})
            (run_cmp,) = bundle["index"]["comparison"]
            self.assertEqual(run_cmp["arms"]["A"]["repeats"], 1, "one repeat made of two per-task boards")
            self.assertEqual(run_cmp["arms"]["A"]["trap_catch_rate"]["mean"], 1.0)
            # neither SYNTHETIC task board wrote a cost log: the summary says so instead of reporting $0
            self.assertEqual(run_cmp["arms"]["A"]["tasks_without_cost_log"], 2)
            self.assertIsNone(run_cmp["arms"]["A"]["total_usd"])

    def test_synthetic_runner_repeats_are_excluded_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            full = load_tasks(calibration_path=tmp / "none.json")
            b = Board(tmp / "run1" / "B-r1" / "tier1_al_melting")
            seed_board(b, TaskSet(version=1, frozen=False, tasks=[full.by_id()["tier1_al_melting"]]))
            b.post("worker", "candidate", {"name": "SYNTHETIC", "family": "t", "tier": "discovery"})
            bundle = export([tmp / "run1"], tmp / "out", rules=RULES, task_set=full)
            self.assertIsNone(bundle["index"]["comparison"])
            self.assertEqual(len(bundle["index"]["excluded_from_comparison"]), 1)


if __name__ == "__main__":
    unittest.main()
