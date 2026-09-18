#!/usr/bin/env python3
"""Console phase 2b (plan-console-phase2 §3.3, §6.1 item 1): the /sync endpoint, effective node users, republishing.

Run with the console's dependencies installed, like tests/console/test_console.py:
  python tests/console/test_sync.py
All names, UUIDs and tokens are synthetic.
"""
import hashlib
import hmac
import json
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from console import db, registry as reg, report_app, users, web_app  # noqa: E402
from test_console import PBK_A, PBK_B, REPORT_TOKENS, Env, Server  # noqa: E402
from test_users import import_doc  # noqa: E402


class SyncEnv(Env):
    """Env with alpha syncing (short ids a1a1a1a1 and the console's shared one) and beta deployed only."""

    def __init__(self, imported=True):
        super().__init__()
        with self.conn() as conn:
            if imported:
                users.import_doc(conn, import_doc())
            self.shared = users.shared_short_id(conn)
            db.set_shown(conn, "alpha", True)
            db.set_shown(conn, "beta", True)
        self.flag("alpha", sync=True, short_ids=["a1a1a1a1", self.shared])

    def flag(self, node, **fields):
        path = os.path.join(self.dirs["registry"], f"{node}.json")
        with open(path) as fh:
            doc = json.load(fh)
        doc.update(fields)
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def post(self, srv, doc, node="alpha", token=None):
        body = json.dumps(dict({"schema": 1, "node": node, "applied": None, "running": [], "error": ""}, **doc)).encode()
        status, text, _ = srv.request("POST", "/sync", body, {
            "Authorization": f"Bearer {token or REPORT_TOKENS[node]}", "Content-Type": "application/json"})
        return status, json.loads(text) if text.startswith("{") else text


class SyncEndpointTest(unittest.TestCase):
    def setUp(self):
        self.env = SyncEnv()

    def tearDown(self):
        self.env.close()

    def test_auth_validation_and_refusals(self):
        with Server(report_app.create_app(self.env.settings)) as srv:
            self.assertEqual(self.env.post(srv, {}, token="w" * 43)[0], 401)
            self.assertEqual(self.env.post(srv, {"node": "beta"})[0], 400)          # alpha's token, beta's name
            self.assertEqual(self.env.post(srv, {"running": ["../x"]})[0], 400)
            self.assertEqual(self.env.post(srv, {"applied": "not-a-version"})[0], 400)
            self.assertEqual(self.env.post(srv, {}, node="beta")[0], 409)             # beta does not sync
        env = SyncEnv(imported=False)
        try:
            with Server(report_app.create_app(env.settings)) as srv:
                status, body = env.post(srv, {})
                self.assertEqual((status, body["error"]), (409, "the console has no users yet"))
        finally:
            env.close()

    def test_list_pending_and_unchanged(self):
        with self.env.conn() as conn:
            users.create(conn, "carol", users.clean_fields(["all"], [], [], "", ""))            # shared short id: sendable
            conn.execute("INSERT INTO users (name, uuid, short_id, created_at, updated_at) VALUES "
                         "('dave', '44444444-4444-4444-8444-444444444444', 'd4d4', 1, 1)")      # unknown short id: pending
        with Server(report_app.create_app(self.env.settings)) as srv:
            status, body = self.env.post(srv, {"running": ["alice", "test"]})
            self.assertEqual(status, 200)
            self.assertEqual([u["name"] for u in body["users"]], ["alice", "carol", "test"])
            version = body["version"]
            status, body = self.env.post(srv, {"applied": version, "running": ["alice", "carol", "test"]})
            self.assertEqual(body, {"version": version, "unchanged": True})
        with self.env.conn() as conn:
            row = users.sync_rows(conn)["alpha"]
        self.assertEqual((row["applied"], row["desired"], row["running"], row["pending"]),
                         (version, version, ["alice", "carol", "test"], ["dave"]))


class EffectiveUsersTest(unittest.TestCase):
    def setUp(self):
        self.env = SyncEnv()
        self.app = web_app.create_app(self.env.settings, start_watcher=False, csrf_secret=b"k" * 32)
        self.csrf = hmac.new(b"k" * 32, b"console-form", hashlib.sha256).hexdigest()

    def tearDown(self):
        self.env.close()

    def test_subscriptions_follow_what_the_agent_reports(self):
        with self.env.conn() as conn:
            carol = users.create(conn, "carol", users.clean_fields(["all"], [], [], "", ""))
            for name in ("alice", "carol"):
                conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES (?, ?, 1)", (name, name[0] * 43))
        watcher = self.app.state.watcher
        watcher.tick()
        catalog, _ = self.env.published()
        self.assertEqual(sorted(catalog["users"]["carol"]["nodes"]), [])                   # not on any node yet
        self.assertEqual(sorted(catalog["users"]["alice"]["nodes"]), ["alpha", "beta"])
        with Server(report_app.create_app(self.env.settings)) as srv:
            status, body = self.env.post(srv, {"running": ["alice", "test"]})
            self.env.post(srv, {"applied": body["version"], "running": ["alice", "carol", "test"]})
        watcher.tick()                                                                      # republished: carol now runs on alpha
        catalog, _ = self.env.published()
        self.assertEqual(catalog["users"]["carol"]["nodes"], {"alpha": {"uuid": carol["uuid"], "short_id": self.env.shared}})
        with self.env.conn() as conn:
            self.assertEqual(db.last_publish(conn)["detail"].split("：")[0], "节点用户同步")
            nodes = users.effective_nodes(conn, reg.Registry(self.env.dirs["registry"]).current()[0])
        self.assertEqual(sorted(nodes["alpha"].users), ["alice", "carol", "test"])
        self.assertEqual(sorted(nodes["beta"].users), ["alice", "bob"])                     # beta: its deployment

    def test_pages_show_sync_state_and_alerts(self):
        with self.env.conn() as conn:
            conn.execute("INSERT INTO users (name, uuid, short_id, created_at, updated_at) VALUES "
                         "('dave', '44444444-4444-4444-8444-444444444444', 'd4d4', 1, 1)")
        with Server(self.app) as srv:
            self.assertIn("节点 alpha 的用户同步超过 5 分钟没有联系控制台", srv.request("GET", "/")[1])
            self.assertIn("未联系", srv.request("GET", "/nodes")[1])
            with Server(report_app.create_app(self.env.settings)) as rsrv:
                status, body = self.env.post(rsrv, {"running": ["alice", "test"]})
                page = srv.request("GET", "/nodes")[1]
                self.assertIn("同步中", page)
                self.env.post(rsrv, {"applied": body["version"], "running": ["alice", "test"], "error": ""})
                self.assertIn("已同步", srv.request("GET", "/nodes")[1])
                home = srv.request("GET", "/")[1]
                self.assertIn("1 个用户需要运行 edge.yml 后才能加入节点 alpha", home)
                self.assertNotIn("正在同步用户", home)
                self.env.post(rsrv, {"running": ["alice", "test"], "error": "refusing an empty user list"})
                self.assertIn("节点 alpha 用户同步失败：refusing an empty user list", srv.request("GET", "/")[1])
                node_page = srv.request("GET", "/nodes/alpha")[1]
                self.assertIn("需要运行 <code>edge.yml</code> 后才能加入：dave", node_page)


class RegistryTest(unittest.TestCase):
    def test_sync_fields(self):
        env = SyncEnv()
        try:
            nodes = reg.Registry(env.dirs["registry"]).current()[0]
            self.assertTrue(nodes["alpha"].sync)
            self.assertEqual(nodes["alpha"].short_ids, tuple(sorted({"a1a1a1a1", env.shared})))
            self.assertFalse(nodes["beta"].sync)
            env.flag("beta", short_ids=["xyz"])
            _, problems, _ = reg.Registry(env.dirs["registry"]).current()
            self.assertTrue(any("short_ids" in p for p in problems))
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
