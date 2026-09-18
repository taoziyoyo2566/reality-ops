#!/usr/bin/env python3
"""A fake Telegram Bot API for tests/console/e2e_local.py (stdlib only; runs in the console image).

Bot API:  POST /bot<FAKE_TOKEN>/<method>   getMe, getUpdates (waits up to 2 s), sendMessage, sendPhoto (multipart),
                                           editMessageText, answerCallbackQuery, setMyCommands; a wrong token gets 401.
Control:  POST /control/update             queue an update (update_id is assigned)
          GET  /control/calls              every call the bot made: [{"method", "params"}]
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ["FAKE_TOKEN"]
LOCK = threading.Condition()
UPDATES = []
CALLS = []


def multipart(body, content_type):
    boundary = content_type.split("boundary=", 1)[1].encode()
    fields = {}
    for part in body.split(b"--" + boundary):
        head, _, value = part.partition(b"\r\n\r\n")
        if b'name="' not in head:
            continue
        name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
        value = value[:-2] if value.endswith(b"\r\n") else value
        fields[name] = {"png_bytes": len(value), "png": value.startswith(b"\x89PNG")} if name == "photo" else value.decode()
    return fields


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, doc):
        body = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/control/calls":
            with LOCK:
                return self.reply(200, list(CALLS))
        self.reply(404, {"ok": False})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/control/update":
            with LOCK:
                update = dict(json.loads(body), update_id=len(UPDATES) + 1)
                UPDATES.append(update)
                LOCK.notify_all()
            return self.reply(200, {"ok": True, "update_id": update["update_id"]})
        prefix = f"/bot{TOKEN}/"
        if not self.path.startswith(prefix):
            return self.reply(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
        method = self.path[len(prefix):]
        ctype = self.headers.get("Content-Type", "")
        params = multipart(body, ctype) if ctype.startswith("multipart/") else json.loads(body or b"{}")
        if method == "getUpdates":
            offset = int(params.get("offset") or 0)
            deadline = time.time() + 2
            with LOCK:
                while not [u for u in UPDATES if u["update_id"] >= offset] and time.time() < deadline:
                    LOCK.wait(deadline - time.time())
                return self.reply(200, {"ok": True, "result": [u for u in UPDATES if u["update_id"] >= offset]})
        with LOCK:
            CALLS.append({"method": method, "params": params})
        if method == "getMe":
            return self.reply(200, {"ok": True, "result": {"id": 42, "is_bot": True, "username": "e2e_test_bot"}})
        self.reply(200, {"ok": True, "result": {"message_id": len(CALLS)} if method.startswith(("send", "edit")) else True})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
