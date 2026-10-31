"""Serve the dashboard on Cloud Run, reading board exports from a private Cloud Storage bucket.

    BUCKET=<bucket> [PREFIX=dashboard] [PORT=8080] python dashboard/server.py     # on Cloud Run
    DATA_DIR=dashboard/data python dashboard/server.py                            # locally, from an export folder

Standard library only (no google-cloud packages): on Cloud Run it gets an access token for its own service
account from the metadata server and reads ``gs://<bucket>/<prefix>/<file>`` through the Cloud Storage JSON API.
The bucket stays private; the service account needs only ``roles/storage.objectViewer`` on that bucket.

Routes: ``/`` and the four static files (whitelist), ``/data/<name>.json|.js`` (exports; names validated),
``/healthz``. Everything else is 404. Exports are cached for ``CACHE_S`` seconds (default 30).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}
DATA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,200}\.(json|js)$")
METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class NotFound(Exception):
    pass


class GcsSource:
    """Reads objects from a private bucket with the service account's token (Cloud Run metadata server)."""

    def __init__(self, bucket: str, prefix: str = "", opener: Callable = urllib.request.urlopen) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket):
            raise ValueError(f"bad bucket name {bucket!r}")
        self.bucket, self.prefix, self.opener = bucket, prefix.strip("/"), opener
        self._token: Optional[str] = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    def _access_token(self) -> str:
        with self._lock:
            if self._token is None or time.time() > self._token_expiry - 60:
                req = urllib.request.Request(METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
                with self.opener(req, timeout=5) as resp:
                    tok = json.loads(resp.read())
                self._token, self._token_expiry = tok["access_token"], time.time() + float(tok.get("expires_in", 300))
            return self._token

    def get(self, name: str) -> bytes:
        obj = f"{self.prefix}/{name}" if self.prefix else name
        url = (f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/"
               f"{urllib.parse.quote(obj, safe='')}?alt=media")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._access_token()}"})
        try:
            with self.opener(req, timeout=10) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFound(name) from exc
            raise


class DirSource:
    """Reads exports from a local folder (development and tests)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def get(self, name: str) -> bytes:
        path = (self.root / name).resolve()
        if path.parent != self.root or not path.is_file():
            raise NotFound(name)
        return path.read_bytes()


class Cache:
    def __init__(self, source, ttl_s: float) -> None:
        self.source, self.ttl_s = source, ttl_s
        self._items: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> bytes:
        now = time.monotonic()
        with self._lock:
            hit = self._items.get(name)
            if hit and now - hit[0] < self.ttl_s:
                return hit[1]
        data = self.source.get(name)
        with self._lock:
            self._items[name] = (now, data)
        return data


def make_handler(cache: Cache, static_root: Path = HERE):
    class Handler(BaseHTTPRequestHandler):
        server_version = "coolorbit-dashboard"
        sys_version = ""

        def _send(self, code: int, body: bytes, ctype: str, cache_s: int = 0) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", f"public, max-age={cache_s}" if cache_s else "no-store")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                return self._send(200, b"ok\n", "text/plain; charset=utf-8")
            if path in STATIC:
                f = static_root / STATIC[path]
                return self._send(200, f.read_bytes(), TYPES[f.suffix], cache_s=60)
            if path.startswith("/data/"):
                name = path[len("/data/"):]
                if not DATA_NAME.match(name):
                    return self._send(404, b"not found\n", "text/plain; charset=utf-8")
                try:
                    body = cache.get(name)
                except NotFound:
                    return self._send(404, b"no export yet\n", "text/plain; charset=utf-8")
                except Exception:  # upstream trouble: don't leak details to the page
                    return self._send(502, b"export store unavailable\n", "text/plain; charset=utf-8")
                return self._send(200, body, TYPES["." + name.rsplit(".", 1)[1]], cache_s=15)
            return self._send(404, b"not found\n", "text/plain; charset=utf-8")

        def log_message(self, fmt: str, *args) -> None:  # one line per request on stderr (Cloud Logging)
            super().log_message(fmt, *args)

    return Handler


def source_from_env(env: dict = os.environ):
    if env.get("DATA_DIR"):
        return DirSource(env["DATA_DIR"])
    if env.get("BUCKET"):
        return GcsSource(env["BUCKET"], env.get("PREFIX", "dashboard"))
    raise SystemExit("set BUCKET (Cloud Run) or DATA_DIR (local)")


def main() -> None:
    cache = Cache(source_from_env(), float(os.environ.get("CACHE_S", "30")))
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(cache))
    print(f"dashboard on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
