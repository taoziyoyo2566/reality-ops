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
import sqlite3
import sys
import unittest
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from console import db, queries, registry as reg, report_app, sharing, users, web_app  # noqa: E402
from test_console import PBK_A, PBK_B, REPORT_TOKENS, Env, Server, report_doc  # noqa: E402
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
            self.assertEqual({k: body[k] for k in ("version", "unchanged")}, {"version": version, "unchanged": True})
            self.assertNotIn("users", body)
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


class AgentAddedUsersTest(unittest.TestCase):
    """Users the agent added after the last deployment are not in the registration file."""

    def setUp(self):
        self.env = SyncEnv()
        with self.env.conn() as conn:
            self.carol = users.create(conn, "carol", users.clean_fields(["all"], [], [], "", ""))
            conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES ('carol', ?, 1)", ("c" * 43,))
        with Server(report_app.create_app(self.env.settings)) as srv:
            body = self.env.post(srv, {"running": ["alice", "test"]})[1]
            self.env.post(srv, {"applied": body["version"], "running": ["alice", "carol", "test"]})

    def tearDown(self):
        self.env.close()

    def test_their_traffic_is_counted(self):
        doc = report_doc("alpha", traffic={"carol": {"up": 10, "down": 20}, "zed": {"up": 1, "down": 1},
                                           "alice": {"up": 1, "down": 2}})
        with Server(report_app.create_app(self.env.settings)) as srv:
            status, _, _ = srv.request("POST", "/report", json.dumps(doc).encode(), {
                "Authorization": f"Bearer {REPORT_TOKENS['alpha']}", "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        with self.env.conn() as conn:
            rows = {r["user"]: (r["up"], r["down"]) for r in conn.execute("SELECT * FROM traffic_daily WHERE node = 'alpha'")}
        self.assertEqual(rows, {"carol": (10, 20), "alice": (1, 2)})         # zed is on no list: left out

    def test_resetting_the_address_replaces_the_credentials(self):
        app = web_app.create_app(self.env.settings, start_watcher=False, csrf_secret=b"k" * 32)
        csrf = hmac.new(b"k" * 32, b"console-form", hashlib.sha256).hexdigest()
        with self.env.conn() as conn:
            before = users.node_payload(conn, reg.Registry(self.env.dirs["registry"]).current()[0]["alpha"],
                                        users.today(8))
        with Server(app) as srv:
            status = srv.request("POST", "/users/carol/rotate", f"csrf={csrf}".encode(),
                                 {"Content-Type": "application/x-www-form-urlencoded"})[0]
        self.assertEqual(status, 303)
        with self.env.conn() as conn:
            carol = users.get(conn, "carol")
            token = db.tokens(conn)["carol"]
            after = users.node_payload(conn, reg.Registry(self.env.dirs["registry"]).current()[0]["alpha"],
                                       users.today(8))
        self.assertNotEqual(carol["uuid"], self.carol["uuid"])
        self.assertNotEqual(token, "c" * 43)
        self.assertNotEqual(before[0], after[0])                                 # the agent gets a new list
        self.assertIn({"name": "carol", "uuid": carol["uuid"], "short_id": carol["short_id"]}, after[1])
        catalog, tokens = self.env.published()
        self.assertEqual(catalog["users"]["carol"]["nodes"]["alpha"]["uuid"], carol["uuid"])


class OnlinePlacesTest(unittest.TestCase):
    """plan-sharing-signals §3.2: hashed networks from the agents, places per slot, the threshold alert."""

    def setUp(self):
        self.env = SyncEnv()

    def tearDown(self):
        self.env.close()

    def sample(self, srv, online, day=None):
        return self.env.post(srv, {"running": ["alice", "test"], "online": online,
                                   "online_day": day or sharing.day_of(db.now())})

    def test_the_key_the_samples_and_the_places(self):
        with Server(report_app.create_app(self.env.settings)) as srv:
            status, body = self.env.post(srv, {"running": ["alice", "test"]})
            self.assertRegex(body["online_key"], r"^[0-9a-f]{32}$")
            self.assertEqual(body["online_day"], sharing.day_of(db.now()))
            self.assertEqual(self.env.post(srv, {})[1]["online_key"], body["online_key"])     # stable within a day
            self.sample(srv, {"alice": ["4:" + "a" * 16, "6:" + "b" * 16, "6:" + "c" * 16],
                              "test": ["4:" + "d" * 16], "bob": ["4:" + "e" * 16]})       # bob is not on alpha
            self.sample(srv, {"alice": ["4:" + "f" * 16]}, day="2000-01-01")              # an old key: left out
            self.assertEqual(self.env.post(srv, {"online": {"alice": ["1.2.3.4"]}, "online_day": "2026-01-01"})[0], 400)
            self.assertEqual(self.env.post(srv, {"online": {"alice": ["4:" + "a" * 16]}})[0], 400)   # no day
        with self.env.conn() as conn:
            self.assertEqual(sharing.peaks(conn, 0), {"alice": 2, "test": 1})       # IPv6 2, IPv4 1: two places
            self.assertEqual(sharing.alerts(conn, 3, db.now()), [])
            conn.executemany("INSERT INTO online_seen (user, slot, family, token) VALUES ('alice', ?, 4, ?)",
                             [(db.now() // 600 * 600, t) for t in ("1" * 16, "2" * 16)])
            self.assertEqual(sharing.peaks(conn, 0)["alice"], 3)
            self.assertEqual(sharing.alerts(conn, 3, db.now()),
                             ["用户 alice 近 24 小时最多同时在 3 处在线（提醒阈值 3 处），可能与他人共用"])
            sharing.purge(conn, db.now() + 9 * 86400)
            self.assertEqual(sharing.peaks(conn, 0), {})

    def test_pages_show_places_and_fetch_sources(self):
        now = db.now()
        with self.env.conn() as conn:
            conn.executemany("INSERT INTO online_seen (user, slot, family, token) VALUES ('alice', ?, 4, ?)",
                             [(now // 600 * 600, t) for t in ("1" * 16, "2" * 16, "3" * 16)])
            conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES ('alice', ?, 1)", ("a" * 43,))
        writer = sqlite3.connect(self.env.settings.subs_access_db)
        try:
            with writer:
                writer.execute("CREATE TABLE hits (ts INTEGER NOT NULL, user TEXT NOT NULL, fmt TEXT NOT NULL, "
                               "ip TEXT, user_agent TEXT)")
                writer.executemany("INSERT INTO hits VALUES (?, 'alice', 'v2ray', ?, ?)", [
                    (now, "203.0.113.5", "Shadowrocket/2070 CFNetwork/1.0 Darwin/24.0"),
                    (now, "203.0.113.99", "Shadowrocket/2071 CFNetwork/1.0"),
                    (now, "2001:db8:1:2::5", "v2rayNG/1.9.30"),
                    (now, "198.51.100.1", "Mozilla/5.0 (Windows NT 10.0)"),
                    (now - 40 * 86400, "192.0.2.1", "Clash/1.0")])                       # older than 30 days
        finally:
            writer.close()
        self.assertEqual(queries.fetch_sources(self.env.settings.subs_access_db)["alice"],
                         {"networks": 3, "clients": ["shadowrocket", "v2rayng", "浏览器"], "count": 4})
        settings = self.env.settings.__class__(**dict(self.env.settings.__dict__, sharing_threshold=3))
        app = web_app.create_app(settings, start_watcher=False, csrf_secret=b"k" * 32)
        with Server(app) as srv:
            listing = srv.request("GET", "/users")[1]
            self.assertIn("<span class=\"warn\">3 处</span>", listing)
            self.assertIn("30 天：3 个网络 · 3 种客户端", listing)
            page = srv.request("GET", "/users/alice")[1]
            self.assertIn("近 30 天从 3 个不同网络、3 种客户端（shadowrocket、v2rayng、浏览器）拉取，共 4 次", page)
            self.assertIn("近 24 小时最多 <span class=\"warn\">3 处</span>", page)
            self.assertIn("用户 alice 近 24 小时最多同时在 3 处在线", srv.request("GET", "/")[1])
            self.assertNotIn("203.0.113", listing + page)                                 # counts only, never addresses


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
