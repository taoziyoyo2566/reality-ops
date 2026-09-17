#!/usr/bin/env python3
"""Management console, phase 1 (plan-console-phase1 §6.1 items 1-2).

Run with the console's dependencies installed (docker/console/requirements.txt), e.g. in a venv from them:
  python tests/console/test_console.py
All hosts, keys, UUIDs and tokens are synthetic.
"""
import hashlib
import hmac
import json
import os
import pathlib
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

import uvicorn

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from console import admin, config, db, publish as pub, queries, registry as reg, report_app, reports, web_app
from subs import catalog as cat

PBK_A = "A" * 42 + "E"
PBK_B = "B" * 42 + "E"
UUIDS = {"alice": "11111111-1111-4111-8111-111111111111", "bob": "22222222-2222-4222-8222-222222222222",
         "test": "33333333-3333-4333-8333-333333333333"}
REPORT_TOKENS = {"alpha": "r" * 43, "beta": "s" * 43, "gamma": "g" * 43}
HOST = "127.0.0.1:8200"


def member(name, sid="a1a1a1a1"):
    return {"name": name, "uuid": UUIDS[name], "short_id": sid}


def registration(node, users, pbk, report=True, xhttp=False):
    return {
        "schema": 1, "node": node, "label": f"{node} [tag]", "endpoint": f"{node}.example.test", "port": 443,
        "sni": "www.example.com", "public_key": pbk,
        "xhttp": {"enabled": xhttp, "path": "/xp/synthetic" if xhttp else "", "mode": "auto"},
        "users": [member(u) for u in users],
        "report": {"enabled": report, "token_sha256": hashlib.sha256(REPORT_TOKENS[node].encode()).hexdigest()},
        "image": "example/xray@sha256:" + "0" * 64,
    }


class Env:
    """A temporary console layout mirroring the container mounts."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.dirs = {k: os.path.join(root, k) for k in ("data", "registry", "db", "subs-data", "subs-db")}
        for d in self.dirs.values():
            os.makedirs(d)
        self.settings = config.from_env({
            "CONSOLE_USERS_FILE": os.path.join(self.dirs["data"], "users.json"),
            "CONSOLE_REGISTRY_DIR": self.dirs["registry"],
            "CONSOLE_DB": os.path.join(self.dirs["db"], "console.sqlite"),
            "CONSOLE_BACKUP_DIR": os.path.join(self.dirs["db"], "backup"),
            "CONSOLE_SUBS_DATA_DIR": self.dirs["subs-data"],
            "CONSOLE_SUBS_ACCESS_DB": os.path.join(self.dirs["subs-db"], "access.sqlite"),
            "CONSOLE_PUBLIC_BASE_URL": "https://sub.example.test",
        })
        self.write_users(["alice", "bob", "test"])
        self.register("alpha", ["alice", "test"], PBK_A, xhttp=True)
        self.register("beta", ["alice", "bob"], PBK_B)
        db.init(self.settings.db_path)

    def write_users(self, names):
        with open(self.settings.users_file, "w") as fh:
            json.dump({"users": [{"name": n, "groups": ["free"], "hosts": [], "deny_hosts": []} for n in names]}, fh)

    def register(self, node, users, pbk, **kw):
        path = os.path.join(self.dirs["registry"], f"{node}.json")
        with open(path, "w") as fh:
            json.dump(registration(node, users, pbk, **kw), fh)

    def conn(self):
        return db.connect(self.settings.db_path)

    def published(self):
        return cat.load(os.path.join(self.dirs["subs-data"], "catalog.json"),
                        os.path.join(self.dirs["subs-data"], "tokens.json"))

    def close(self):
        self.tmp.cleanup()


class Server:
    """Run an ASGI app on a free localhost port for the duration of a `with` block."""

    def __init__(self, app):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning",
                                                    access_log=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)

    def request(self, method, path, body=None, headers=None, host=HOST):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body, method=method,
                                     headers=dict({"Host": host}, **(headers or {})))
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, resp.read().decode(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(), dict(exc.headers)


def report_doc(node, seq=1, traffic=None, to="2026-01-02T03:05:00Z", instance="0123456789abcdef", **kw):
    doc = {"schema": 1, "node": node, "instance": instance, "seq": seq,
           "period": {"from": "2026-01-02T03:00:00Z", "to": to}, "xray_restarted": False, "baseline": False,
           "traffic": traffic or {}, "listening": True, "error_tail": ["line one"], "dropped_reports": 0}
    doc.update(kw)
    return doc


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_valid_files_load_and_bad_ones_are_reported(self):
        nodes, problems, _ = reg.Registry(self.env.dirs["registry"]).current()
        self.assertEqual(sorted(nodes), ["alpha", "beta"])
        self.assertEqual(nodes["alpha"].users["test"], {"uuid": UUIDS["test"], "short_id": "a1a1a1a1"})
        self.assertEqual(problems, [])
        bad = registration("gamma", ["bob"], PBK_B)
        bad["public_key"] = "short"
        with open(os.path.join(self.env.dirs["registry"], "gamma.json"), "w") as fh:
            json.dump(bad, fh)
        with open(os.path.join(self.env.dirs["registry"], "delta.json"), "w") as fh:
            json.dump(registration("beta", ["bob"], PBK_B), fh)  # file name and node differ
        nodes, problems, _ = reg.Registry(self.env.dirs["registry"]).current()
        self.assertEqual(sorted(nodes), ["alpha", "beta"])
        self.assertEqual(len(problems), 2)

    def test_report_enabled_needs_a_digest(self):
        doc = registration("alpha", ["alice"], PBK_A)
        doc["report"]["token_sha256"] = ""
        with self.assertRaises(reg.RegistryError):
            reg.parse("alpha", doc)
        doc["report"]["enabled"] = False
        self.assertFalse(reg.parse("alpha", doc).report_enabled)

    def test_reload_follows_file_changes(self):
        registry = reg.Registry(self.env.dirs["registry"])
        _, _, first = registry.current()
        self.env.register("alpha", ["alice"], PBK_A)
        nodes, _, second = registry.current()
        self.assertNotEqual(first, second)
        self.assertEqual(sorted(nodes["alpha"].users), ["alice"])


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.registry = reg.Registry(self.env.dirs["registry"])

    def tearDown(self):
        self.env.close()

    def test_catalog_has_only_shown_nodes_and_users_with_tokens(self):
        nodes, _, _ = self.registry.current()
        tokens = {"alice": "a" * 43, "test": "t" * 43}
        catalog, token_doc = pub.build(nodes, {"alpha"}, tokens, "2026-01-01T00:00:00Z")
        self.assertEqual(list(catalog["nodes"]), ["alpha"])
        self.assertEqual(catalog["nodes"]["alpha"]["state"], "migrated")
        self.assertEqual(catalog["users"]["alice"]["nodes"], {"alpha": {"uuid": UUIDS["alice"], "short_id": "a1a1a1a1"}})
        self.assertNotIn("bob", catalog["users"])
        self.assertEqual(set(token_doc["tokens"].values()), {"alice", "test"})
        text = json.dumps([catalog, token_doc])
        self.assertNotIn("a" * 43, text)
        self.assertNotIn("private", text.lower())

    def test_shown_node_without_registration_stops_publishing(self):
        nodes, _, _ = self.registry.current()
        with self.assertRaises(pub.PublishError):
            pub.build(nodes, {"alpha", "gone"}, {"alice": "a" * 43}, "2026-01-01T00:00:00Z")

    def test_publish_writes_files_the_subscription_service_accepts(self):
        with self.env.conn() as conn:
            conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES ('alice', ?, 1)", ("a" * 43,))
            db.set_shown(conn, "alpha", True)
            db.set_shown(conn, "beta", True)
            ok, detail = pub.publish(self.env.settings, conn, self.registry, "test")
        self.assertTrue(ok, detail)
        catalog, digests = self.env.published()
        self.assertEqual(sorted(catalog["users"]["alice"]["nodes"]), ["alpha", "beta"])
        self.assertEqual(digests, {cat.token_digest("a" * 43): "alice"})
        mode = stat.S_IMODE(os.stat(os.path.join(self.env.dirs["subs-data"], "catalog.json")).st_mode)
        self.assertEqual(mode, 0o640)

    def test_failed_publish_keeps_previous_files_and_is_logged(self):
        with self.env.conn() as conn:
            conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES ('alice', ?, 1)", ("a" * 43,))
            db.set_shown(conn, "alpha", True)
            self.assertTrue(pub.publish(self.env.settings, conn, self.registry, "first")[0])
            before = self.env.published()
            db.set_shown(conn, "gone", True)
            ok, detail = pub.publish(self.env.settings, conn, self.registry, "second")
            self.assertFalse(ok)
            self.assertIn("gone", detail)
            self.assertEqual(db.last_publish(conn)["ok"], 0)
        self.assertEqual(self.env.published(), before)


class ReportStoreTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.nodes, _, _ = reg.Registry(self.env.dirs["registry"]).current()

    def tearDown(self):
        self.env.close()

    def test_token_selects_its_node_only(self):
        self.assertEqual(reports.node_for_token(self.nodes, f"Bearer {REPORT_TOKENS['beta']}").name, "beta")
        for bad in (None, "", "Bearer ", "Bearer wrong", f"Basic {REPORT_TOKENS['beta']}"):
            self.assertIsNone(reports.node_for_token(self.nodes, bad))

    def test_validation(self):
        node = self.nodes["alpha"]
        cases = [
            report_doc("beta"),
            report_doc("alpha", seq=0),
            report_doc("alpha", instance="nothex"),
            report_doc("alpha", to="yesterday"),
            report_doc("alpha", traffic={"alice": {"up": -1, "down": 0}}),
            report_doc("alpha", traffic={"a.b": {"up": 1, "down": 0}}),
            report_doc("alpha", listening="yes"),
        ]
        for doc in cases:
            with self.subTest(doc=doc):
                with self.assertRaises(reports.ReportError):
                    reports.validate(doc, node)
        reports.validate(report_doc("alpha"), node)

    def test_traffic_sums_and_duplicates_are_ignored(self):
        node = self.nodes["alpha"]
        with self.env.conn() as conn:
            self.assertTrue(reports.store(conn, node, report_doc("alpha", 1, {"alice": {"up": 5, "down": 7}})))
            self.assertFalse(reports.store(conn, node, report_doc("alpha", 1, {"alice": {"up": 5, "down": 7}})))
            self.assertTrue(reports.store(conn, node, report_doc("alpha", 2, {"alice": {"up": 1, "down": 1},
                                                                             "bob": {"up": 9, "down": 9}})))
            # a new reporter instance restarts its sequence numbers
            self.assertTrue(reports.store(conn, node, report_doc("alpha", 1, {"test": {"up": 2, "down": 3}},
                                                                 instance="fedcba9876543210")))
            totals = queries.traffic_by_user(conn, "2026-01")
        self.assertEqual(totals, {"alice": {"up": 6, "down": 8}, "test": {"up": 2, "down": 3}})  # bob is not on alpha

    def test_older_report_does_not_replace_newer_status(self):
        node = self.nodes["alpha"]
        with self.env.conn() as conn:
            reports.store(conn, node, report_doc("alpha", 2, to="2026-01-02T03:10:00Z", listening=False))
            reports.store(conn, node, report_doc("alpha", 1, to="2026-01-02T03:05:00Z",
                                                 traffic={"alice": {"up": 1, "down": 1}}, xray_restarted=True))
            status = queries.node_status(conn)["alpha"]
            self.assertEqual(status["period_to"], "2026-01-02T03:10:00Z")
            self.assertFalse(status["report"]["listening"])
            self.assertIsNotNone(status["xray_restart_at"])
            self.assertEqual(queries.traffic_by_user(conn, "2026-01-02"), {"alice": {"up": 1, "down": 1}})


class ReportAppTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def post(self, srv, doc, token):
        body = json.dumps(doc).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return srv.request("POST", "/report", body, headers, host="report.example.test")[0]

    def test_authentication_duplicates_and_limits(self):
        with Server(report_app.create_app(self.env.settings)) as srv:
            self.assertEqual(self.post(srv, report_doc("alpha"), None), 401)
            self.assertEqual(self.post(srv, report_doc("alpha"), "wrong"), 401)
            self.assertEqual(self.post(srv, report_doc("alpha"), REPORT_TOKENS["beta"]), 400)  # beta's token
            self.assertEqual(self.post(srv, report_doc("alpha", traffic={"alice": {"up": 3, "down": 4}}),
                                       REPORT_TOKENS["alpha"]), 200)
            self.assertEqual(self.post(srv, report_doc("alpha"), REPORT_TOKENS["alpha"]), 409)
            huge = report_doc("alpha", seq=2, error_tail=["x" * 1000] * 400)
            self.assertEqual(self.post(srv, huge, REPORT_TOKENS["alpha"]), 413)
            self.assertEqual(srv.request("GET", "/healthz")[0], 200)
            for path in ("/", "/users", "/nodes", "/docs", "/openapi.json"):
                self.assertEqual(srv.request("GET", path)[0], 404, path)
        with self.env.conn() as conn:
            self.assertEqual(queries.traffic_by_user(conn, "2026"), {"alice": {"up": 3, "down": 4}})

    def test_disabled_reporting_refuses_the_token(self):
        self.env.register("alpha", ["alice"], PBK_A, report=False)
        with Server(report_app.create_app(self.env.settings)) as srv:
            self.assertEqual(self.post(srv, report_doc("alpha"), REPORT_TOKENS["alpha"]), 401)


class WebAppTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.app = web_app.create_app(self.env.settings, start_watcher=False, csrf_secret=b"k" * 32)
        self.csrf = hmac.new(b"k" * 32, b"console-form", hashlib.sha256).hexdigest()

    def tearDown(self):
        self.env.close()

    def post(self, srv, path, **fields):
        body = urllib.parse.urlencode(fields).encode()
        return srv.request("POST", path, body, {"Content-Type": "application/x-www-form-urlencoded"})

    def token(self, user):
        with self.env.conn() as conn:
            return db.tokens(conn).get(user)

    def test_host_and_cross_site_protection(self):
        with Server(self.app) as srv:
            self.assertEqual(srv.request("GET", "/", host="evil.example")[0], 400)
            self.assertEqual(srv.request("GET", "/healthz", host="evil.example")[0], 200)
            status, _, headers = srv.request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertEqual(self.post(srv, "/users/alice/issue")[0], 403)                   # no form token
            self.assertEqual(self.post(srv, "/users/alice/issue", csrf="0" * 64)[0], 403)
            body = urllib.parse.urlencode({"csrf": self.csrf}).encode()
            status = srv.request("POST", "/users/alice/issue", body,
                                 {"Content-Type": "application/x-www-form-urlencoded",
                                  "Origin": "https://evil.example"})[0]
            self.assertEqual(status, 403)
        self.assertIsNone(self.token("alice"))

    def test_issue_rotate_revoke_publish_and_audit(self):
        with self.env.conn() as conn:
            db.set_shown(conn, "alpha", True)
        with Server(self.app) as srv:
            self.assertEqual(self.post(srv, "/users/alice/issue", csrf=self.csrf)[0], 303)
            first = self.token("alice")
            self.assertRegex(first, r"^[A-Za-z0-9_-]{43}$")
            catalog, digests = self.env.published()
            self.assertEqual(digests, {cat.token_digest(first): "alice"})
            self.assertEqual(list(catalog["users"]["alice"]["nodes"]), ["alpha"])
            status, page, _ = srv.request("GET", "/users/alice")
            self.assertEqual(status, 200)
            self.assertIn(f"https://sub.example.test/s/{first}", page)
            self.assertIn("<svg", page)

            self.assertEqual(self.post(srv, "/users/alice/rotate", csrf=self.csrf)[0], 303)
            second = self.token("alice")
            self.assertNotEqual(first, second)
            self.assertEqual(self.env.published()[1], {cat.token_digest(second): "alice"})

            self.post(srv, "/users/alice/revoke", csrf=self.csrf, confirm="wrong")
            self.assertEqual(self.token("alice"), second)
            self.post(srv, "/users/alice/revoke", csrf=self.csrf, confirm="alice")
            self.assertIsNone(self.token("alice"))
            self.assertEqual(self.env.published()[1], {})

            self.post(srv, "/users/nobody/issue", csrf=self.csrf)          # no profile: refused
            self.assertIsNone(self.token("nobody"))
            status, page, _ = srv.request("GET", "/audit")
            self.assertEqual(status, 200)
        with self.env.conn() as conn:
            actions = [e["action"] for e in queries.audit_entries(conn)]
        self.assertEqual(actions, ["revoke", "rotate", "issue"])
        self.assertNotIn(second, page)

    def test_node_toggle_and_pages(self):
        with self.env.conn() as conn:
            conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES ('bob', ?, 1)", ("b" * 43,))
            reports.store(conn, reg.Registry(self.env.dirs["registry"]).current()[0]["beta"],
                          report_doc("beta", traffic={"bob": {"up": 1024, "down": 2048}}))
        with Server(self.app) as srv:
            self.assertEqual(self.post(srv, "/nodes/beta/show", csrf=self.csrf, shown="1")[0], 303)
            self.assertEqual(list(self.env.published()[0]["users"]["bob"]["nodes"]), ["beta"])
            self.post(srv, "/nodes/beta/show", csrf=self.csrf, shown="0")
            self.assertEqual(self.env.published()[0]["users"]["bob"]["nodes"], {})
            self.post(srv, "/nodes/ghost/show", csrf=self.csrf, shown="1")   # unregistered: refused
            with self.env.conn() as conn:
                self.assertNotIn("ghost", db.shown_nodes(conn))
            for path in ("/", "/users", "/users/bob", "/nodes", "/nodes/beta", "/traffic", "/traffic?month=2026-01",
                         "/audit"):
                status, page, _ = srv.request("GET", path)
                self.assertEqual(status, 200, path)
            self.assertEqual(srv.request("GET", "/nodes/ghost")[0], 404)
            self.assertEqual(srv.request("GET", "/users/..%2Fetc")[0], 404)
            status, page, _ = srv.request("GET", "/nodes/beta")
            self.assertIn("line one", page)


class AccessLogTest(unittest.TestCase):
    def test_reads_a_wal_database_in_a_read_only_directory(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "access.sqlite")
            writer = sqlite3.connect(path)
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE hits (ts INTEGER NOT NULL, user TEXT NOT NULL, fmt TEXT NOT NULL, ip TEXT, user_agent TEXT)")
            now = int(time.time())
            writer.executemany("INSERT INTO hits VALUES (?, ?, ?, '', '')",
                               [(now - 100, "alice", "v2ray"), (now - 50, "alice", "clash-split"), (now - 10, "bob", "page")])
            writer.commit()  # writer stays open, so -wal and -shm exist as while the service runs
            os.chmod(d, 0o555)
            try:
                result = queries.last_fetches(path)
            finally:
                os.chmod(d, 0o755)
                writer.close()
        self.assertEqual(result["alice"], {"at": now - 50, "fmt": "clash-split", "count": 2})
        self.assertEqual(result["bob"]["fmt"], "page")
        self.assertEqual(queries.last_fetches(os.path.join(d, "missing.sqlite")), {})


class AdminAndWatcherTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_initial_import_runs_once(self):
        path = os.path.join(self.env.tmp.name, "initial.json")
        with open(path, "w") as fh:
            json.dump({"tokens": {"test": "t" * 43}, "shown": ["alpha"]}, fh)
        self.assertEqual(admin.import_initial(self.env.settings, path), 0)
        catalog, digests = self.env.published()
        self.assertEqual(digests, {cat.token_digest("t" * 43): "test"})
        self.assertEqual(list(catalog["users"]["test"]["nodes"]), ["alpha"])
        with open(path, "w") as fh:
            json.dump({"tokens": {"test": "u" * 43}, "shown": ["beta"]}, fh)
        admin.import_initial(self.env.settings, path)
        with self.env.conn() as conn:
            self.assertEqual(db.tokens(conn), {"test": "t" * 43})
            self.assertEqual(db.shown_nodes(conn), {"alpha"})

    def test_watcher_republishes_on_registry_change_only_when_initialized(self):
        registry = reg.Registry(self.env.dirs["registry"])
        watcher = web_app.Watcher(self.env.settings, registry)
        watcher.tick()
        self.assertFalse(os.path.exists(os.path.join(self.env.dirs["subs-data"], "catalog.json")))
        self.assertTrue(os.listdir(self.env.settings.backup_dir))
        with self.env.conn() as conn:
            conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES ('test', ?, 1)", ("t" * 43,))
            db.set_shown(conn, "alpha", True)
        watcher.tick()
        self.assertEqual(self.env.published()[0]["users"]["test"]["nodes"]["alpha"]["uuid"], UUIDS["test"])
        self.env.register("alpha", ["alice"], PBK_A)          # test removed from alpha by a redeploy
        watcher.tick()
        self.assertEqual(self.env.published()[0]["users"]["test"]["nodes"], {})


if __name__ == "__main__":
    unittest.main()
