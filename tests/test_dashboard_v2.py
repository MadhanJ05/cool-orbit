"""The v2 landing page + dashboard: routes, the classic page kept at /classic/, and the no-hardcoded-numbers rule.

Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from dashboard import server

DASH = Path(__file__).resolve().parents[1] / "dashboard"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        Path(cls._tmp.name, "data.js").write_text("window.COOL_ORBIT_DATA = null;\n")
        cache = server.Cache(server.DirSource(cls._tmp.name), ttl_s=30)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cache))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.opener = urllib.request.build_opener(NoRedirect)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls._tmp.cleanup()

    def get(self, path: str):
        try:
            with self.opener.open(self.base + path, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_home_is_v2(self) -> None:
        code, _, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"/v2/app.js", body)
        self.assertIn(b"cool in orbit", body)

    def test_v2_assets_and_fonts(self) -> None:
        for p in ("/v2/app.js", "/v2/style.css"):
            self.assertEqual(self.get(p)[0], 200, p)
        code, headers, body = self.get("/v2/fonts/inter-latin-var.woff2")
        self.assertEqual((code, headers["Content-Type"]), (200, "font/woff2"))
        self.assertTrue(body.startswith(b"wOF2"))

    def test_classic_page_is_kept(self) -> None:
        code, headers, _ = self.get("/classic")
        self.assertEqual((code, headers.get("Location")), (301, "/classic/"))
        code, _, body = self.get("/classic/")
        self.assertEqual(code, 200)
        self.assertIn(b'src="app.js"', body, "the classic page, with its relative links")
        for p in ("/classic/app.js", "/classic/style.css", "/classic/ga3d.js", "/classic/data/data.js",
                  "/classic/vendor/three/three.module.min.js"):
            self.assertEqual(self.get(p)[0], 200, p)

    def test_still_closed(self) -> None:
        for p in ("/v2/../server.py", "/v2/fonts/OFL-Inter.txt", "/classic/server.py", "/classic/../server.py",
                  "/v2/index.html", "/classic/data/../server.py"):
            self.assertEqual(self.get(p)[0], 404, p)


class PackagingTest(unittest.TestCase):
    def test_every_whitelisted_file_exists_and_is_in_the_image(self) -> None:
        dockerfile = (DASH / "Dockerfile").read_text()
        self.assertIn("COPY v2 ./v2", dockerfile)
        for route, rel in server.STATIC.items():
            if route in server.OPTIONAL:  # the hero image may be added later; the page falls back to a CSS planet
                continue
            self.assertTrue((DASH / rel).is_file(), f"{route} -> {rel} missing")

    def test_fonts_are_self_hosted_with_their_licences(self) -> None:
        for f in ("OFL-Inter.txt", "OFL-SourceSerif4.txt"):
            self.assertIn("SIL Open Font License", (DASH / "v2" / "fonts" / f).read_text())
        css = (DASH / "v2" / "style.css").read_text()
        self.assertNotIn("googleapis", css + (DASH / "v2" / "index.html").read_text())


class NoHardcodedNumbersTest(unittest.TestCase):
    """v2/app.js must not type in data: every number shown comes from the export (the brief's rule)."""

    ALLOWED = {"0", "1", "2", "3", "4", "6", "8", "10", "12", "14", "15", "18", "22", "26", "34", "100", "120",
               "60", "600", "900", "1000", "4000", "60000", "273.15", "0.15"}  # formatting, timings, minutes/hour, layout (900px breakpoint, 0.15x parallax), K -> °C

    def test_only_formatting_and_timing_constants(self) -> None:
        src = (DASH / "v2" / "app.js").read_text()
        code = re.sub(r"//[^\n]*", "", src)
        code = re.sub(r"`(?:\\.|[^`\\])*`", "``", code)           # template literals (text)
        code = re.sub(r"\"(?:\\.|[^\"\\])*\"", "\"\"", code)        # double-quoted strings (text, CSS)
        code = re.sub(r"'(?:\\.|[^'\\])*'", "''", code)
        nums = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", code))
        self.assertEqual(nums - self.ALLOWED, set(), "a data-looking number is typed into v2/app.js")


if __name__ == "__main__":
    unittest.main()
