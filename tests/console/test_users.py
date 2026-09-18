#!/usr/bin/env python3
"""Console phase 2a (plan-console-phase2 §3.1-3.4, §3.6; §6.1 item 1): users, tiers, import, web pages.

Run with the console's dependencies installed, like tests/console/test_console.py:
  python tests/console/test_users.py
All names, UUIDs and tokens are synthetic.
"""
import hashlib
import hmac
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from console import admin, auth, db, registry as reg, users, web_app  # noqa: E402
from test_console import HOST, UUIDS, Env, Server  # noqa: E402

RULES = {"free": ["free", "premium"], "premium": ["premium"]}
NODE_TIERS = {"alpha": ["free"], "beta": ["premium"]}
DAY = "2026-01-10"


def import_doc():
    """Reproduces Env's registration files: alpha has alice and test, beta has alice and bob."""
    return {
        "rules": RULES, "node_tiers": NODE_TIERS,
        "users": [
            {"name": "alice", "uuid": UUIDS["alice"], "short_id": "a1a1a1a1"},                  # no groups: all
            {"name": "bob", "uuid": UUIDS["bob"], "short_id": "a1a1a1a1", "groups": ["premium"],
             "deny_hosts": ["alpha"]},
            {"name": "test", "uuid": UUIDS["test"], "short_id": "a1a1a1a1", "groups": ["test"], "hosts": ["alpha"]},
        ],
    }


def record(tiers, allow=(), deny=()):
    return {"tiers": list(tiers), "allow_nodes": list(allow), "deny_nodes": list(deny)}


class AccessTest(unittest.TestCase):
    """can_use() must match the existing ACL in roles/xray_edge/tasks/acl.yml."""

    def test_tiers_follow_the_rules(self):
        self.assertTrue(users.can_use(record(["all"]), "beta", NODE_TIERS, RULES))
        self.assertTrue(users.can_use(record(["free"]), "alpha", NODE_TIERS, RULES))
        self.assertFalse(users.can_use(record(["free"]), "beta", NODE_TIERS, RULES))
        self.assertTrue(users.can_use(record(["premium"]), "alpha", NODE_TIERS, RULES))   # free nodes accept premium
        self.assertTrue(users.can_use(record(["premium"]), "beta", NODE_TIERS, RULES))
        self.assertFalse(users.can_use(record(["test"]), "alpha", NODE_TIERS, RULES))     # a tier no rule accepts
        self.assertFalse(users.can_use(record(["free"]), "gamma", NODE_TIERS, RULES))     # a node without tiers

    def test_allow_adds_and_deny_wins(self):
        self.assertTrue(users.can_use(record(["test"], allow=["alpha"]), "alpha", NODE_TIERS, RULES))
        self.assertFalse(users.can_use(record(["all"], deny=["alpha"]), "alpha", NODE_TIERS, RULES))
        self.assertFalse(users.can_use(record(["free"], allow=["alpha"], deny=["alpha"]), "alpha", NODE_TIERS, RULES))

    def test_effective(self):
        user = {"status": "active", "expires_on": "2026-01-10"}
        self.assertTrue(users.effective(user, "2026-01-10"))       # the whole last day is still served
        self.assertFalse(users.effective(user, "2026-01-11"))
        self.assertFalse(users.effective(dict(user, status="disabled", expires_on=None), DAY))

    def test_field_validation(self):
        ok = users.clean_fields(["free", " free", ""], ["alpha"], [], " 2026-02-01 ", "  a  note ", ["alpha", "beta"])
        self.assertEqual(ok, {"tiers": ["free"], "allow_nodes": ["alpha"], "deny_nodes": [], "expires_on": "2026-02-01",
                              "note": "a note"})
        self.assertEqual(users.clean_fields([], [], [], "", "")["tiers"], ["all"])
        for args, fragment in (((["free"], ["alpha"], ["alpha"], "", ""), "既单独允许又单独禁止"),
                               (([], ["ghost"], [], "", "", ["alpha"]), "没有这些节点"),
                               (([], [], [], "2026-13-01", ""), "不是有效日期"),
                               (([], [], [], "20260101", ""), "YYYY-MM-DD"),
                               ((["a b"], [], [], "", ""), "档位"),
                               (([], [], [], "", "x" * 201), "备注")):
            with self.subTest(args=args), self.assertRaises(users.UserError) as ctx:
                users.clean_fields(*args)
            self.assertIn(fragment, str(ctx.exception))


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.nodes = reg.Registry(self.env.dirs["registry"]).current()[0]

    def tearDown(self):
        self.env.close()

    def test_import_is_equivalent_and_once(self):
        with self.env.conn() as conn:
            self.assertEqual(users.import_doc(conn, import_doc()), 3)
            self.assertEqual(users.differences(conn, self.nodes, DAY), {})
            self.assertEqual([u["name"] for u in users.node_users(conn, "alpha", DAY)], ["alice", "test"])
            self.assertEqual(users.load(conn)["alice"]["tiers"], ["all"])
            with self.assertRaises(users.UserError):
                users.import_doc(conn, import_doc())

    def test_import_rejects_bad_input(self):
        for mutate, fragment in ((lambda d: d["users"][1].update(uuid=UUIDS["alice"]), "共用 UUID"),
                                 (lambda d: d["users"][0].update(short_id="xyz"), "short_id"),
                                 (lambda d: d["users"].append(dict(d["users"][0], uuid="44444444-4444-4444-8444-444444444444")),
                                  "用户名重复"),
                                 (lambda d: d.update(rules={}), "档位规则")):
            doc = import_doc()
            mutate(doc)
            with self.env.conn() as conn, self.subTest(fragment=fragment), self.assertRaises(users.UserError) as ctx:
                users.import_doc(conn, doc)
            self.assertIn(fragment, str(ctx.exception))
            with self.env.conn() as conn:
                self.assertFalse(users.imported(conn))

    def test_changes_show_up_as_pending_deployments(self):
        with self.env.conn() as conn:
            users.import_doc(conn, import_doc())
            users.update(conn, "bob", users.clean_fields(["premium"], [], [], "", ""))
            users.set_status(conn, "test", "disabled")
            new = users.create(conn, "carol", users.clean_fields(["premium"], [], [], "2026-01-09", ""))
            self.assertEqual(new["short_id"], users.shared_short_id(conn))
            self.assertEqual(users.shared_short_id(conn), new["short_id"])
            diff = users.differences(conn, self.nodes, DAY)
            self.assertEqual(diff, {"alpha": {"add": ["bob"], "remove": ["test"], "change": []}})
            self.assertEqual(users.differences(conn, self.nodes, "2026-01-09")["beta"]["add"], ["carol"])

    def test_delete_removes_the_token(self):
        with self.env.conn() as conn:
            users.import_doc(conn, import_doc())
            conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES ('bob', ?, 1)", ("b" * 43,))
            users.delete(conn, "bob")
            self.assertIsNone(users.get(conn, "bob"))
            self.assertEqual(db.tokens(conn), {})


class AdminCommandTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.path = os.path.join(self.env.dirs["data"], "users-import.json")
        with open(self.path, "w") as fh:
            json.dump(import_doc(), fh)

    def tearDown(self):
        self.env.close()

    def run_admin(self, *args):
        from contextlib import redirect_stdout
        import io
        out = io.StringIO()
        env = {k: v for k, v in os.environ.items()}
        with redirect_stdout(out):
            os.environ.update({"CONSOLE_DB": self.env.settings.db_path,
                               "CONSOLE_REGISTRY_DIR": self.env.settings.registry_dir})
            try:
                code = admin.main(list(args))
            finally:
                os.environ.clear()
                os.environ.update(env)
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_node_users_waits_for_the_import(self):
        self.assertEqual(self.run_admin("node-users", "alpha")[0], 3)
        code, out = self.run_admin("import-users", self.path)
        self.assertEqual((code, out["imported"], out["users"], out["differences"]), (0, True, 3, {}))
        code, out = self.run_admin("import-users", self.path)
        self.assertEqual((code, out["imported"]), (0, False))
        code, out = self.run_admin("node-users", "beta")
        self.assertEqual(code, 0)
        self.assertEqual(out["users"], [{"name": "alice", "uuid": UUIDS["alice"], "short_id": "a1a1a1a1"},
                                        {"name": "bob", "uuid": UUIDS["bob"], "short_id": "a1a1a1a1"}])


class MigrationTest(unittest.TestCase):
    def test_audit_log_gains_the_actor_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "console.sqlite")
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, "
                         "action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')")
            conn.execute("INSERT INTO audit_log (at, action, target) VALUES (1, 'issue', 'alice')")
            conn.commit()
            conn.close()
            db.init(path)
            db.init(path)   # idempotent
            with db.connect(path) as c:
                db.audit(c, "edit", "bob", actor="本机管理员")
                rows = [dict(r) for r in c.execute("SELECT action, actor FROM audit_log ORDER BY id")]
        self.assertEqual(rows, [{"action": "issue", "actor": ""}, {"action": "edit", "actor": "本机管理员"}])

    def test_only_the_none_auth_mode_exists(self):
        auth.check_mode("none")
        with self.assertRaises(SystemExit):
            auth.check_mode("oidc")


class WebTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        with self.env.conn() as conn:
            users.import_doc(conn, import_doc())
            db.set_shown(conn, "alpha", True)
            db.set_shown(conn, "beta", True)
        self.app = web_app.create_app(self.env.settings, start_watcher=False, csrf_secret=b"k" * 32)
        self.csrf = hmac.new(b"k" * 32, b"console-form", hashlib.sha256).hexdigest()

    def tearDown(self):
        self.env.close()

    def post(self, srv, path, fields):
        pairs = [("csrf", self.csrf)] + [(k, v) for k, vs in fields.items() for v in (vs if isinstance(vs, list) else [vs])]
        return srv.request("POST", path, urllib.parse.urlencode(pairs).encode(),
                           {"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://" + HOST})

    def served(self, user):
        """Whether the published subscription data serves this user."""
        catalog, digests = self.env.published()
        return user in catalog["users"]

    def test_create_edit_disable_enable_delete(self):
        with Server(self.app) as srv:
            self.assertIn("新增用户", srv.request("GET", "/users")[1])
            status, page, _ = self.post(srv, "/users/new", {"name": "carol", "tiers": ["premium"], "note": "朋友",
                                                            "expires_on": "", "deny_nodes": ["alpha"]})
            self.assertEqual(status, 303)
            page = srv.request("GET", "/users/carol")[1]
            self.assertIn("待加入", page)                          # beta will get carol at the next edge.yml
            self.assertIn("发放订阅地址", page)
            status, page, _ = self.post(srv, "/users/new", {"name": "carol", "tiers": ["free"]})
            self.assertIn("用户 carol 已存在", page)
            status, page, _ = self.post(srv, "/users/new", {"name": "dave", "allow_nodes": ["ghost"]})
            self.assertIn("没有这些节点：ghost", page)

            self.post(srv, "/users/carol/issue", {})
            self.assertTrue(self.served("carol"))
            self.post(srv, "/users/carol/edit", {"tiers": ["premium"], "expires_on": "2000-01-01", "note": "x"})
            self.assertFalse(self.served("carol"))                  # expired: the address stops working
            self.post(srv, "/users/carol/edit", {"tiers": ["premium"], "expires_on": ""})
            self.assertTrue(self.served("carol"))
            status, _, headers = self.post(srv, "/users/carol/edit", {"expires_on": "2026-02-30"})
            self.assertIn("error=", headers["location"])
            self.post(srv, "/users/carol/status", {"status": "disabled"})
            self.assertFalse(self.served("carol"))
            self.assertIn("暂停服务", srv.request("GET", "/users/carol")[1])
            self.post(srv, "/users/carol/status", {"status": "active"})
            self.assertTrue(self.served("carol"))
            self.post(srv, "/users/carol/delete", {"confirm": "wrong"})
            with self.env.conn() as conn:
                self.assertIsNotNone(users.get(conn, "carol"))
            self.post(srv, "/users/carol/delete", {"confirm": "carol"})
            with self.env.conn() as conn:
                self.assertIsNone(users.get(conn, "carol"))
                actions = [(r["action"], r["actor"]) for r in conn.execute("SELECT action, actor FROM audit_log ORDER BY id")]
            self.assertFalse(self.served("carol"))
            self.assertIn(("user-create", "本机管理员"), actions)
            self.assertIn(("user-delete", "本机管理员"), actions)

    def test_bulk_issue_and_export(self):
        with Server(self.app) as srv:
            self.post(srv, "/users/test/status", {"status": "disabled"})
            status, _, headers = self.post(srv, "/users/bulk-issue", {"names": ["alice", "bob", "test", "../x"]})
            self.assertEqual((status, headers["location"]), (303, "/users/export?names=alice%2Cbob%2Ctest"))
            with self.env.conn() as conn:
                tokens = db.tokens(conn)
            self.assertEqual(sorted(tokens), ["alice", "bob"])     # a disabled user gets no address
            page = srv.request("GET", "/users/export?names=alice,bob,test")[1]
            self.assertIn(f"alice\thttps://sub.example.test/s/{tokens['alice']}", page)
            text = srv.request("GET", "/users/export.txt?names=alice,bob")
            self.assertEqual(text[0], 200)
            self.assertIn("attachment", text[2]["content-disposition"])
            self.assertEqual(text[1], "".join(f"{n}\thttps://sub.example.test/s/{tokens[n]}\n" for n in ("alice", "bob")))
            self.assertTrue(self.served("alice") and self.served("bob"))

    def test_filters_node_tiers_and_alerts(self):
        with Server(self.app) as srv:
            page = srv.request("GET", "/users?tier=premium")[1]
            self.assertIn('href="/users/bob"', page)
            self.assertNotIn('href="/users/alice"', page)
            self.assertNotIn('href="/users/bob"', srv.request("GET", "/users?q=ali")[1])
            self.assertIn("与控制台一致", srv.request("GET", "/nodes/alpha")[1])
            self.post(srv, "/nodes/alpha/tiers", {"tiers": ["premium", "bogus"]})
            with self.env.conn() as conn:
                self.assertEqual(users.node_tiers(conn)["alpha"], ["premium"])
            self.assertIn("与控制台一致", srv.request("GET", "/nodes/alpha")[1])   # alice is `all`, test is allowed
            self.post(srv, "/nodes/beta/tiers", {"tiers": ["bogus"]})                # no tier: bob (premium) loses beta
            page = srv.request("GET", "/nodes/beta")[1]
            self.assertIn("下次运行 <code>edge.yml</code> 时：移除 bob", page)
            home = srv.request("GET", "/")[1]
            self.assertIn("台节点上的用户与控制台不一致", home)
            self.post(srv, "/users/new", {"name": "erin", "expires_on": users.today(8)})
            self.assertIn("用户 erin 将于", srv.request("GET", "/")[1])

    def test_before_the_import_the_pages_are_read_only(self):
        env = Env()
        try:
            app = web_app.create_app(env.settings, start_watcher=False, csrf_secret=b"k" * 32)
            with Server(app) as srv:
                self.assertIn("用户尚未导入控制台", srv.request("GET", "/users")[1])
                self.assertEqual(srv.request("GET", "/users/new")[0], 303)
                self.assertIn("运行 console.yml 完成导入", srv.request("GET", "/")[1])
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
