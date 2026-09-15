"""Subscription HTTP service (plan-subscription-service §3.4, §3.6).

GET /s/<token>            user page
GET /s/<token>/<format>   subscription body (render.FORMATS)
GET /healthz              catalog loaded or not, no user data

Unknown tokens, unknown formats and revoked users all get the same 404. A missing or invalid catalog or
token table makes every subscription request 503. Paths are never logged because they carry the token.
"""
import hmac
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import accesslog, catalog as cat, render

TOKEN_PATH_RE = re.compile(r"^/s/([A-Za-z0-9_-]{43})(?:/([a-z0-9-]{1,20}))?/?$")
COMMON_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
}
PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
NOT_FOUND = b"not found\n"


class Store:
    """Catalog and token table, reloaded when either file changes.

    Publishing replaces files atomically, so the inode changes; mtime alone is not enough because file
    timestamps come from a coarse clock and two replacements a few milliseconds apart can share one.
    """

    def __init__(self, data_dir):
        self.catalog_path = os.path.join(data_dir, "catalog.json")
        self.tokens_path = os.path.join(data_dir, "tokens.json")
        self._lock = threading.Lock()
        self._stamp = None
        self.catalog = None
        self.digests = {}
        self.error = "not loaded"

    def _stamp_now(self):
        try:
            return tuple((st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                         for st in (os.stat(self.catalog_path), os.stat(self.tokens_path)))
        except OSError:
            return None

    def current(self):
        stamp = self._stamp_now()
        with self._lock:
            if stamp != self._stamp:
                self._stamp = stamp
                try:
                    if stamp is None:
                        raise cat.CatalogError("catalog or token table missing")
                    self.catalog, self.digests = cat.load(self.catalog_path, self.tokens_path)
                    self.error = None
                except (OSError, ValueError) as exc:
                    self.catalog, self.digests = None, {}
                    self.error = type(exc).__name__
                    print(f"subs: data not loaded: {exc}", file=sys.stderr, flush=True)
            return self.catalog, self.digests

    def user_for(self, token):
        catalog, digests = self.current()
        if catalog is None:
            return None, None
        digest = cat.token_digest(token)
        for known, user in digests.items():
            if hmac.compare_digest(known, digest):
                return catalog, user
        return catalog, ""


def make_handler(store, log, public_base_url):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def version_string(self):
            return "subs"

        def log_message(self, *args):  # the default logs the request line, which contains the token
            pass

        def _send(self, status, content_type, payload, extra=None):
            self.send_response(status)
            headers = dict(COMMON_HEADERS, **(extra or {}))
            headers.update({"Content-Type": content_type, "Content-Length": str(len(payload))})
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _base_url(self, token):
            if public_base_url:
                root = public_base_url.rstrip("/")
            else:
                proto = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
                root = f"{proto}://{self.headers.get('Host', 'localhost')}"
            return f"{root}/s/{token}"

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                catalog, _ = store.current()
                doc = {"ok": catalog is not None,
                       "generated_at": catalog.get("generated_at") if catalog else None}
                status = 200 if catalog is not None else 503
                return self._send(status, "application/json", json.dumps(doc).encode())
            match = TOKEN_PATH_RE.match(path)
            if not match:
                return self._send(404, "text/plain; charset=utf-8", NOT_FOUND)
            token, fmt = match.group(1), match.group(2) or "page"
            catalog, user = store.user_for(token)
            if catalog is None:
                return self._send(503, "text/plain; charset=utf-8", b"unavailable\n")
            if not user or (fmt != "page" and fmt not in render.FORMATS):
                return self._send(404, "text/plain; charset=utf-8", NOT_FOUND)
            if fmt == "page":
                content_type, text = "text/html; charset=utf-8", render.page(catalog, user, self._base_url(token))
                extra = {"Content-Security-Policy": PAGE_CSP}
            else:
                content_type, text = render.body(catalog, user, fmt)
                extra = {"Content-Disposition": f'inline; filename="{fmt}.{"yaml" if fmt.startswith("clash") else "txt"}"',
                         "profile-update-interval": "12"}
            ip = self.headers.get("CF-Connecting-IP") or self.client_address[0]
            try:
                log.record(user, fmt, ip, self.headers.get("User-Agent"))
            except Exception as exc:  # logging must not break a subscription fetch
                print(f"subs: access log failed: {type(exc).__name__}", file=sys.stderr, flush=True)
            return self._send(200, content_type, text.encode(), extra)

        do_HEAD = do_GET

    return Handler


def serve(data_dir, db_dir, host="0.0.0.0", port=8100, public_base_url=""):
    store = Store(data_dir)
    store.current()
    log = accesslog.AccessLog(os.path.join(db_dir, "access.sqlite"))
    server = ThreadingHTTPServer((host, port), make_handler(store, log, public_base_url))
    server.daemon_threads = True
    return server


def main():
    server = serve(os.environ.get("SUBS_DATA_DIR", "/data"), os.environ.get("SUBS_DB_DIR", "/db"),
                   os.environ.get("SUBS_LISTEN", "0.0.0.0"), int(os.environ.get("SUBS_PORT", "8100")),
                   os.environ.get("SUBS_PUBLIC_BASE_URL", ""))
    print(f"subs: listening on {server.server_address[0]}:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
