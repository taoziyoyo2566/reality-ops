#!/usr/bin/env python3
"""Proxy egress in the console (plan-egress-console): types, the pool, assignments, /sync delivery and reports,
checks from spt, and the pages.

Run with the console's dependencies installed, like tests/console/test_console.py:
  python tests/console/test_egress.py
All addresses, names and credentials are synthetic.
"""
import hashlib
import hmac
import json
import pathlib
import sys
import unittest
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from console import admin, db, egress, egress_probe, report_app, users, web_app  # noqa: E402
from test_console import Server  # noqa: E402
from test_sync import SyncEnv  # noqa: E402

SECRET = "not-a-real-password-7431"


class TypeTest(unittest.TestCase):
    def test_socks5(self):
        kind = egress.TYPES["socks5"]
        values = kind.clean({"host": "203.0.113.10", "port": "1080", "username": "u", "password": SECRET})
        self.assertEqual(kind.outbound("egress-1", values), {"tag": "egress-1", "protocol": "socks", "settings": {
            "address": "203.0.113.10", "port": 1080, "user": "u", "pass": SECRET}})
        self.assertEqual(kind.outbound("egress-1", kind.clean({"host": "p.example.test", "port": 1080}))["settings"],
                         {"address": "p.example.test", "port": 1080})
        self.assertNotIn(SECRET, kind.summary(values))
        for bad in ({"host": "a b", "port": 1}, {"host": "h.test", "port": "0"}, {"host": "h.test", "port": "x"},
                    {"host": "h.test", "port": 1, "username": "u"}):
            with self.subTest(bad=bad), self.assertRaises(egress.EgressError):
                kind.clean(bad)
        name, parsed = kind.parse_link(f"socks5://u:{SECRET}@[2001:db8::7]:1081#jp-1")
        self.assertEqual((name, parsed["host"], parsed["port"], parsed["password"]), ("jp-1", "2001:db8::7", 1081, SECRET))
        self.assertIsNone(kind.parse_link("http://h.test:8080"))


class PoolTest(unittest.TestCase):
    def setUp(self):
        self.env = SyncEnv()

    def tearDown(self):
        self.env.close()

    def test_save_keeps_the_secret_and_refuses_duplicates(self):
        with self.env.conn() as conn:
            egress_id = egress.save(conn, None, "jp-1", "socks5", {"host": "203.0.113.10", "port": "1080",
                                                                     "username": "u", "password": SECRET}, ["jp"], " a  note ")
            egress.save(conn, egress_id, "jp-1b", "socks5", {"host": "203.0.113.11", "port": "1080", "username": "u",
                                                              "password": ""}, [], "")
            item = egress.get(conn, egress_id)
            self.assertEqual((item["name"], item["config"]["host"], item["config"]["password"]), ("jp-1b", "203.0.113.11", SECRET))
            with self.assertRaises(egress.EgressError):
                egress.save(conn, None, "jp-1b", "socks5", {"host": "h.test", "port": 1}, [], "")
            with self.assertRaises(egress.EgressError):
                egress.save(conn, egress_id, "jp-1b", "http", {}, [], "")

    def test_import_links(self):
        with self.env.conn() as conn:
            added, failed = egress.import_links(conn, "\n".join([
                f"socks5://u:{SECRET}@203.0.113.20:1080#jp-2", "socks5://203.0.113.21:1080", "# comment", "",
                "ftp://nope", "socks5://203.0.113.22:99999#bad-port"]), ["bulk"])
            names = sorted(e["name"] for e in egress.pool(conn).values())
        self.assertEqual(added, ["jp-2", "socks5-203.0.113.21-1080"])
        self.assertEqual([n for n, _ in failed], [5, 6])
        self.assertEqual(names, ["jp-2", "socks5-203.0.113.21-1080"])


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.env = SyncEnv()
        with self.env.conn() as conn:
            self.a = egress.save(conn, None, "a", "socks5", {"host": "192.0.2.1", "port": 1080}, [], "")
            self.b = egress.save(conn, None, "b", "socks5", {"host": "192.0.2.2", "port": 1080}, [], "")

    def tearDown(self):
        self.env.close()

    def test_rules_follow_conditions_priority_and_the_node_users(self):
        with self.env.conn() as conn:
            egress.assign(conn, None, self.a, "alpha", {"users": ["alice", "carol"], "domains": ["geosite:amazon"]},
                          "block", "tcp", 50)
            egress.assign(conn, None, self.b, "alpha", {}, "direct", "tcp,udp", 10)
            egress.assign(conn, None, self.b, "alpha", {"users": ["carol"]}, "direct", "tcp", 5)        # carol not here
            off = egress.assign(conn, None, self.a, "alpha", {"ips": ["203.0.113.0/24"]}, "direct", "tcp", 1)
            egress.toggle_assignment(conn, off, "alpha")
            version, payload = egress.node_payload(conn, "alpha", ["alice", "test"])
        self.assertEqual([o["tag"] for o in payload["outbounds"]], [f"egress-{self.a}", f"egress-{self.b}"])
        self.assertEqual([(a["outbound"], a["rule"], a["on_failure"]) for a in payload["assignments"]], [
            (f"egress-{self.b}", {"network": "tcp,udp"}, "direct"),
            (f"egress-{self.a}", {"network": "tcp", "user": ["alice.alpha"], "domain": ["geosite:amazon"]}, "block")])
        self.assertEqual([r["ruleTag"] for r in egress.deploy_rules(payload)],
                         [f"egress-{self.b}-a{payload['assignments'][0]['id']}", f"egress-{self.a}-a{payload['assignments'][1]['id']}"])
        with self.env.conn() as conn:
            egress.set_enabled(conn, self.b, False)
            self.assertNotEqual(egress.node_payload(conn, "alpha", ["alice", "test"])[0], version)

    def test_websites_or_ip_ranges_and_blocking_udp(self):
        with self.env.conn() as conn:
            conditions = egress.clean_conditions({"users": ["alice"], "domains": ["chatgpt.com", "domain:chatgpt.com"],
                                                  "ips": ["203.0.113.0/24"]}, ["alice"])
            self.assertEqual(conditions["domains"], ["domain:chatgpt.com"])              # a bare name means the site
            egress.assign(conn, None, self.a, "alpha", conditions, "block", "tcp-block-udp", 10)
            _, payload = egress.node_payload(conn, "alpha", ["alice"])
            with self.assertRaises(egress.EgressError):                                   # would block DNS as well
                egress.assign(conn, None, self.a, "alpha", {"users": ["alice"]}, "direct", "tcp-block-udp", 10)
            with self.assertRaises(egress.EgressError):
                egress.assign(conn, None, self.a, "alpha", {}, "direct", "tcp-block-udp", 10)
        user = ["alice.alpha"]
        self.assertEqual(payload["assignments"][0]["rules"], [
            {"user": user, "domain": ["domain:chatgpt.com"], "network": "tcp", "action": "egress"},
            {"user": user, "domain": ["domain:chatgpt.com"], "network": "udp", "port": "443", "action": "block"},
            {"user": user, "ip": ["203.0.113.0/24"], "network": "tcp", "action": "egress"},
            {"user": user, "ip": ["203.0.113.0/24"], "network": "udp", "port": "443", "action": "block"}])
        self.assertEqual(payload["assignments"][0]["rule"],                                # for agents before rule lists
                         {"user": user, "domain": ["domain:chatgpt.com"], "network": "tcp"})
        tag = f"egress-{self.a}-a{payload['assignments'][0]['id']}"
        self.assertEqual([(r["ruleTag"], r["network"], r["outboundTag"]) for r in egress.deploy_rules(payload)], [
            (tag, "tcp", f"egress-{self.a}"), (f"{tag}-2", "udp", "blocked"), (f"{tag}-3", "tcp", f"egress-{self.a}"),
            (f"{tag}-4", "udp", "blocked")])
        self.assertEqual(egress.describe_conditions(conditions), "用户 alice；访问网站 domain:chatgpt.com 或 IP 段 203.0.113.0/24")

    def test_schemes(self):
        sites = {"domains": ["domain:chatgpt.com", "domain:claude.ai"]}
        with self.env.conn() as conn:
            scheme = egress.save_scheme(conn, None, "AI 与 ABEMA", sites, "tcp-block-udp", "block", "")
            with self.assertRaises(egress.EgressError):
                egress.save_scheme(conn, None, "AI 与 ABEMA", sites, "tcp", "block", "")      # the name is taken
            with self.assertRaises(egress.EgressError):
                egress.save_scheme(conn, None, "empty", {}, "tcp", "block", "")               # needs websites or IPs
            everyone = egress.assign(conn, None, self.a, "alpha", {}, "direct", "tcp", 100, scheme_id=scheme)
            only_alice = egress.assign(conn, None, self.b, "alpha", {"users": ["alice"], "domains": ["x.test"]}, "direct",
                                       "tcp", 50, scheme_id=scheme)
            rows = {a["id"]: a for a in egress.assignments(conn, "alpha")}
            self.assertEqual((rows[everyone]["network"], rows[everyone]["on_failure"], rows[everyone]["scheme"]),
                             ("tcp-block-udp", "block", "AI 与 ABEMA"))
            self.assertEqual(rows[only_alice]["conditions"], {"users": ["alice"], **sites})  # its own websites dropped
            _, before = egress.node_payload(conn, "alpha", ["alice", "test"])
            egress.save_scheme(conn, scheme, "AI 与 ABEMA", {"domains": ["domain:chatgpt.com"]}, "tcp-block-udp", "block", "")
            version, after = egress.node_payload(conn, "alpha", ["alice", "test"])
            self.assertEqual([len(a["rules"]) for a in before["assignments"]], [2, 2])
            self.assertEqual(after["assignments"][1]["rules"][0]["domain"], ["domain:chatgpt.com"])  # follows the scheme
            with self.assertRaises(egress.EgressError) as refused:
                egress.delete_scheme(conn, scheme)
            self.assertIn("alpha", str(refused.exception))
            egress.record_check(conn, self.b, "alpha", {"ok": True, "latency_ms": 1, "exit_ip": "", "country": "",
                                                        "error": "", "udp": False})
            with self.assertRaises(egress.EgressError):                        # b cannot take UDP through the scheme
                egress.save_scheme(conn, scheme, "AI 与 ABEMA", sites, "tcp,udp", "block", "")
            egress.unassign(conn, everyone, "alpha")
            egress.unassign(conn, only_alice, "alpha")
            egress.delete_scheme(conn, scheme)
            self.assertEqual(egress.schemes(conn), {})

    def test_inactive_reasons(self):
        with self.env.conn() as conn:
            gone = egress.assign(conn, None, self.a, "alpha", {"users": ["carol"]}, "direct", "tcp", 100)
            egress.set_enabled(conn, self.b, False)
            off = egress.assign(conn, None, self.b, "alpha", {}, "direct", "tcp", 100)
            rows = {a["id"]: a for a in egress.assignments(conn, "alpha")}
            items = egress.pool(conn)
        self.assertEqual(egress.inactive_reason(rows[gone], items[self.a], ["alice"]), "所选用户都不在这台节点上")
        self.assertEqual(egress.inactive_reason(rows[off], items[self.b], ["alice"]), "出口已停用，这部分流量直连")
        with self.env.conn() as conn:
            blocks = egress.assign(conn, None, self.b, "alpha", {"domains": ["x.test"]}, "block", "tcp", 100)
            payload, notes = egress.plan(conn, "alpha", ["alice"])
            old_agent, old_notes = egress.plan(conn, "alpha", ["alice"], schema=1)
        entry = next(e for e in payload["assignments"] if e["id"] == blocks)
        self.assertTrue(entry["egress_off"])                        # set up never to go direct: stays, blocked
        self.assertEqual(notes[blocks], "出口已停用，这部分流量按“断开”阻断")
        self.assertEqual([r["outboundTag"] for r in egress.deploy_rules(payload) if r["ruleTag"].endswith(f"a{blocks}")],
                         ["blocked"])
        self.assertNotIn(f"egress-{self.b}", [o["tag"] for o in payload["outbounds"]])
        self.assertNotIn(blocks, [e["id"] for e in old_agent["assignments"]])  # an old agent cannot run it
        self.assertIn("edge.yml", old_notes[blocks])

    def test_conditions_are_checked(self):
        with self.assertRaises(egress.EgressError):
            egress.clean_conditions({"users": ["zed"]}, ["alice"])
        with self.assertRaises(egress.EgressError):
            egress.clean_conditions({"domains": ["a b"]}, [])
        with self.assertRaises(egress.EgressError):
            egress.clean_conditions({"ips": ["300.1.1.1/8"]}, [])
        self.assertEqual(egress.clean_conditions({"users": ["alice", "alice"], "domains": [], "ips": ["geoip:jp"]}, ["alice"]),
                         {"users": ["alice"], "ips": ["geoip:jp"]})
        self.assertEqual(egress.describe_conditions({}), "整台节点的全部流量")

    def test_node_users_output_for_edge_yml(self):
        with self.env.conn() as conn:
            egress.assign(conn, None, self.a, "alpha", {"users": ["alice"]}, "direct", "tcp", 100)
        out = self._admin(["node-users", "alpha"])
        rules = out["proxy_egress"]["rules"]
        self.assertEqual(out["proxy_egress"]["outbounds"][0]["tag"], f"egress-{self.a}")
        self.assertEqual((rules[0]["outboundTag"], rules[0]["user"]), (f"egress-{self.a}", ["alice.alpha"]))

    def _admin(self, argv):
        import contextlib
        import io
        import os
        env = {"CONSOLE_DB": self.env.settings.db_path, "CONSOLE_REGISTRY_DIR": self.env.dirs["registry"]}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                admin.main(argv)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return json.loads(buf.getvalue().strip().splitlines()[-1])


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.env = SyncEnv()
        with self.env.conn() as conn:
            self.a = egress.save(conn, None, "a", "socks5", {"host": "192.0.2.1", "port": 1080, "username": "u",
                                                              "password": SECRET}, [], "")
            other = egress.save(conn, None, "other", "socks5", {"host": "192.0.2.3", "port": 1080}, [], "")
            self.assignment = egress.assign(conn, None, self.a, "alpha", {"users": ["alice"]}, "direct", "tcp", 100)
            egress.assign(conn, None, other, "beta", {}, "direct", "tcp", 100)
            self.other = other

    def tearDown(self):
        self.env.close()

    def test_payload_until_applied_and_reports_recorded(self):
        with Server(report_app.create_app(self.env.settings)) as srv:
            status, body = self.env.post(srv, {"running": ["alice", "test"]})
            self.assertEqual(status, 200)
            self.assertNotIn("egress", body)                            # an agent without egress gets no credentials
            status, body = self.env.post(srv, {"running": ["alice", "test"], "egress_applied": None})
            self.assertEqual(body["egress"]["outbounds"][0]["settings"]["pass"], SECRET)    # only to the node that uses it
            self.assertEqual(body["egress_check_url"], "https://www.cloudflare.com/cdn-cgi/trace")
            version = body["egress_version"]
            status, body = self.env.post(srv, {
                "running": ["alice", "test"], "egress_applied": version,
                "egress_states": {str(self.assignment): "direct"},
                "egress_checks": {str(self.a): {"ok": False, "latency_ms": None, "exit_ip": "", "country": "",
                                                "error": "timeout", "udp": None},
                                  str(self.other): {"ok": True, "latency_ms": 3, "exit_ip": "x", "country": "", "error": ""}}})
            self.assertNotIn("egress", body)                                               # applied: not sent again
        with self.env.conn() as conn:
            found = egress.checks(conn)
            self.assertEqual([(c["checker"], c["ok"], c["error"]) for c in found[self.a]], [("alpha", 0, "timeout")])
            self.assertNotIn(self.other, found)                                            # not alpha's egress
            self.assertEqual(egress.node_states(conn)["alpha"]["states"], {str(self.assignment): "direct"})
            self.assertEqual(egress.alerts(conn, db.now(), {"alpha", "beta"}),
                             ["出口 a 在节点 alpha 上不可用（timeout），已回退直连"])
        # a bad egress report is recorded as the node's egress error and does not stop the user sync
        with Server(report_app.create_app(self.env.settings)) as srv:
            for bad in ({"egress_states": {"1": "weird"}}, {"egress_checks": {"x": {"ok": True}}},
                        {"egress_checks": {"1": {"ok": True, "udp": "yes"}}}, {"egress_applied": "not-a-version"},
                        {"egress_schema": "two"}):
                status, body = self.env.post(srv, dict({"running": ["alice", "test"], "egress_applied": None}, **bad))
                self.assertEqual((status, "version" in body), (200, True), bad)
                with self.env.conn() as conn:
                    self.assertIn("出口数据无效", egress.node_states(conn)["alpha"]["error"], bad)
            self.env.post(srv, {"running": ["alice", "test"], "egress_applied": None, "egress_schema": 2,
                                "egress_error": "adrules: failed"})
        with self.env.conn() as conn:
            self.assertEqual((egress.node_states(conn)["alpha"]["schema"], egress.node_states(conn)["alpha"]["error"]),
                             (2, "adrules: failed"))
            self.assertIn("节点 alpha 的出口规则应用失败：adrules: failed", egress.alerts(conn, db.now(), {"alpha"}))

    def test_spt_checks_only_unassigned_egress(self):
        seen = []
        saved = egress_probe.probe
        egress_probe.probe = lambda xray, outbounds, url, user_agent: (
            seen.extend(o["tag"] for o in outbounds) or {o["tag"]: {"ok": True, "latency_ms": 9, "exit_ip": "198.51.100.9",
                                                                    "country": "JP", "error": ""} for o in outbounds})
        try:
            with self.env.conn() as conn:
                spare = egress.save(conn, None, "spare", "socks5", {"host": "192.0.2.4", "port": 1080}, [], "")
                egress.check_unassigned(conn, "xray", "https://check.example.test/", {"alpha", "beta"})
                self.assertEqual(seen, [f"egress-{spare}"])
                self.assertEqual(egress.checks(conn)[spare][0]["checker"], "spt")
                egress.check_unassigned(conn, "xray", "https://check.example.test/", {"alpha"})   # beta gone: other too
                self.assertEqual(sorted(seen[1:]), sorted([f"egress-{spare}", f"egress-{self.other}"]))
                del seen[:]
                conn.execute("UPDATE egress SET updated_at = 900")
                conn.execute("UPDATE egress_check SET checked_at = 1000")
                egress.check_unassigned(conn, "xray", "https://check.example.test/", {"alpha"}, new_only=True)
                self.assertEqual(seen, [])                                                      # nothing changed
                egress.save(conn, spare, "spare", "socks5", {"host": "192.0.2.5", "port": 1080}, [], "")
                egress.check_unassigned(conn, "xray", "https://check.example.test/", {"alpha"}, new_only=True)
                self.assertEqual(seen, [f"egress-{spare}"])                                     # changed: checked again
        finally:
            egress_probe.probe = saved


class PagesTest(unittest.TestCase):
    def setUp(self):
        self.env = SyncEnv()
        self.app = web_app.create_app(self.env.settings, start_watcher=False, csrf_secret=b"k" * 32)
        self.csrf = hmac.new(b"k" * 32, b"console-form", hashlib.sha256).hexdigest()
        self.rules_checked = []
        self._test_rules = egress_probe.test_rules                   # Xray with its geo data is not needed here
        egress_probe.test_rules = lambda xray, rules: self.rules_checked.append(rules) or ""

    def tearDown(self):
        egress_probe.test_rules = self._test_rules
        self.env.close()

    def post(self, srv, path, **fields):
        body = urllib.parse.urlencode(dict(fields, csrf=self.csrf), doseq=True).encode()
        return srv.request("POST", path, body, {"Content-Type": "application/x-www-form-urlencoded"})

    def test_pool_node_and_user_pages(self):
        with Server(self.app) as srv:
            status, _, headers = self.post(srv, "/egress/new", type="socks5", name="jp-1", f_host="203.0.113.10",
                                           f_port="1080", f_username="u", f_password=SECRET, labels="jp isp")
            self.assertEqual((status, headers["location"]), (303, "/egress"))
            self.assertIn("error=", self.post(srv, "/egress/new", type="socks5", name="jp-1", f_host="h.test",
                                              f_port="1")[2]["location"])                            # duplicate name
            with self.env.conn() as conn:
                egress_id = next(iter(egress.pool(conn)))
            listing = srv.request("GET", "/egress")[1]
            self.assertIn("203.0.113.10:1080（有账号）", listing)
            self.assertIn("未分配", listing)
            item = srv.request("GET", f"/egress/{egress_id}")[1]
            self.assertIn("已设置，留空表示不改", item)
            self.post(srv, f"/egress/{egress_id}/edit", name="jp-1", f_host="203.0.113.12", f_port="1080",
                      f_username="u", f_password="", labels="jp")
            with self.env.conn() as conn:
                self.assertEqual(egress.get(conn, egress_id)["config"]["password"], SECRET)

            status, _, headers = self.post(srv, "/nodes/alpha/egress/new", egress_id=str(egress_id), users=["alice"],
                                           domains="geosite:amazon", ips="", network="tcp", on_failure="block", priority="10")
            self.assertEqual((status, headers["location"]), (303, "/nodes/alpha#egress"))
            self.assertIn("error=", self.post(srv, "/nodes/alpha/egress/new", egress_id=str(egress_id), users=["zed"],
                                              network="tcp", on_failure="direct")[2]["location"])
            node = srv.request("GET", "/nodes/alpha")[1]
            self.assertIn("用户 alice；访问网站 geosite:amazon", node)
            self.assertIn("断开", node)
            self.assertIn("同步中", node)
            self.assertIn("jp-1（部分流量）", srv.request("GET", "/users/alice")[1])
            with self.env.conn() as conn:
                assignment = egress.assignments(conn, "alpha")[0]["id"]
            self.post(srv, f"/nodes/alpha/egress/{assignment}/toggle")
            self.assertIn(">启用</button>", srv.request("GET", "/nodes/alpha")[1])
            self.post(srv, f"/nodes/alpha/egress/{assignment}/delete")
            self.assertIn("这台节点没有出口分配", srv.request("GET", "/nodes/alpha")[1])

            self.assertIn("error=", self.post(srv, f"/egress/{egress_id}/delete", confirm="nope")[2]["location"])
            self.post(srv, f"/egress/{egress_id}/delete", confirm="jp-1")
            self.assertIn("出口池是空的", srv.request("GET", "/egress")[1])
            pages = "".join(srv.request("GET", p)[1] for p in ("/egress", "/nodes/alpha", "/users/alice", "/audit", "/"))
            self.assertNotIn(SECRET, pages)
        with self.env.conn() as conn:
            actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log WHERE action LIKE 'egress%'")]
            details = " ".join(r["detail"] for r in conn.execute("SELECT detail FROM audit_log"))
        self.assertEqual(actions, ["egress-add", "egress-edit", "egress-assign", "egress-assign-disable", "egress-unassign",
                                   "egress-delete"])
        self.assertNotIn(SECRET, details)

    def test_check_button(self):
        answers = []
        saved = egress_probe.probe
        egress_probe.probe = lambda xray, outbounds, url, user_agent, attempts=2: (
            self.assertEqual(attempts, 1) or {o["tag"]: answers.pop(0) for o in outbounds})
        try:
            with self.env.conn() as conn:
                egress_id = egress.save(conn, None, "jp-1", "socks5", {"host": "203.0.113.10", "port": 1080}, [], "")
            with Server(self.app) as srv:
                self.assertIn("等待检测（约一分钟内）", srv.request("GET", "/egress")[1])
                self.assertIn("约一分钟内自动检测", srv.request("GET", f"/egress/{egress_id}")[1])
                answers.append({"ok": True, "latency_ms": 812, "exit_ip": "198.51.100.7", "country": "JP", "error": "",
                                "udp": False})
                status, _, headers = self.post(srv, f"/egress/{egress_id}/check", back="list")
                notice = urllib.parse.unquote(headers["location"])
                self.assertEqual((status, notice),
                                 (303, "/egress?notice=jp-1：可用，出口 IP 198.51.100.7（JP），耗时 812 ms，UDP 不支持"))
                listing = srv.request("GET", "/egress")[1]
                self.assertIn('<span class="ok">可用</span>', listing)
                self.assertIn('<span class="warn">不支持</span>', listing)                    # the UDP column
                answers.append({"ok": False, "latency_ms": None, "exit_ip": "", "country": "", "error": "timeout", "udp": None})
                _, _, headers = self.post(srv, f"/egress/{egress_id}/check")
                self.assertEqual(urllib.parse.unquote(headers["location"]), f"/egress/{egress_id}?error=jp-1：不可用（timeout）")
                page = srv.request("GET", headers["location"])[1]
                self.assertIn("jp-1：不可用（timeout）", page)
                self.assertNotIn("切换以节点每分钟的检测为准", page)
                with self.env.conn() as conn:
                    egress.assign(conn, None, egress_id, "alpha", {}, "direct", "tcp", 100)
                self.assertIn("切换以节点每分钟的检测为准", srv.request("GET", f"/egress/{egress_id}")[1])
            with self.env.conn() as conn:
                self.assertEqual([(c["checker"], c["ok"], c["error"], c["udp"]) for c in egress.checks(conn)[egress_id]],
                                 [("spt", 0, "timeout", 0)])                     # UDP not tried: the earlier finding stays
                self.assertIs(egress.udp_support(conn, egress_id), False)
        finally:
            egress_probe.probe = saved

    def test_udp_support_is_shown_and_guards_assignments(self):
        with self.env.conn() as conn:
            egress_id = egress.save(conn, None, "jp-1", "socks5", {"host": "203.0.113.10", "port": 1080}, [], "")
            self.assertIsNone(egress.udp_support(conn, egress_id))
            egress.assign(conn, None, egress_id, "alpha", {"users": ["test"]}, "direct", "tcp,udp", 100)   # not known yet
            egress.record_check(conn, egress_id, "alpha", {"ok": True, "latency_ms": 9, "exit_ip": "", "country": "",
                                                           "error": "", "udp": False})
            with self.assertRaises(egress.EgressError) as refused:
                egress.assign(conn, None, egress_id, "alpha", {"users": ["alice"]}, "direct", "tcp,udp", 100)
            self.assertIn("不支持 UDP", str(refused.exception))
            egress.assign(conn, None, egress_id, "alpha", {"users": ["alice"]}, "direct", "tcp", 100)
            self.assertEqual(egress.alerts(conn, db.now(), {"alpha"}),
                             ["出口 jp-1 在节点 alpha 上不支持 UDP，选了“TCP 和 UDP”的分配已回退直连"])
            egress.save(conn, egress_id, "jp-1", "socks5", {"host": "203.0.113.10", "port": 1080}, ["x"], "")  # same proxy
            self.assertIs(egress.udp_support(conn, egress_id), False)
            egress.save(conn, egress_id, "jp-1", "socks5", {"host": "203.0.113.11", "port": 1080}, [], "")     # another one
            self.assertIsNone(egress.udp_support(conn, egress_id))
            egress.record_check(conn, egress_id, "alpha", {"ok": True, "latency_ms": 9, "exit_ip": "", "country": "",
                                                           "error": "", "udp": False})
        with Server(self.app) as srv:
            node = srv.request("GET", "/nodes/alpha")[1]
            self.assertIn("jp-1（可用，不支持 UDP）", node)                               # the egress choice
            self.assertIn('<span class="warn">不支持 UDP</span>', node)                  # the TCP and UDP assignment
            self.assertIn("只列出这台节点上的用户", node)
            _, _, headers = self.post(srv, "/nodes/alpha/egress/new", egress_id=str(egress_id), users=["test"],
                                      network="tcp,udp", on_failure="direct")
            self.assertIn("不支持 UDP", urllib.parse.unquote(headers["location"]))

    def test_scheme_pages(self):
        with self.env.conn() as conn:
            egress_id = egress.save(conn, None, "r6s", "socks5", {"host": "203.0.113.10", "port": 1080}, [], "")
        with Server(self.app) as srv:
            status, _, headers = self.post(srv, "/egress/schemes/new", name="AI 与 ABEMA", domains="chatgpt.com claude.ai",
                                           ips="", network="tcp-block-udp", on_failure="block", note="")
            self.assertEqual((status, headers["location"]), (303, "/egress#schemes"))
            with self.env.conn() as conn:
                scheme_id = next(iter(egress.schemes(conn)))
            listing = srv.request("GET", "/egress")[1]
            self.assertIn("访问网站 domain:chatgpt.com、domain:claude.ai", listing)
            self.assertIn("只有 TCP，阻断 QUIC", listing)
            self.post(srv, "/nodes/alpha/egress/new", egress_id=str(egress_id), scheme_id=str(scheme_id), domains="", ips="",
                      network="tcp", on_failure="direct", priority="100")
            node = srv.request("GET", "/nodes/alpha")[1]
            self.assertIn("方案“AI 与 ABEMA”：访问网站 domain:chatgpt.com、domain:claude.ai", node)
            self.assertIn("AI 与 ABEMA（只有 TCP，阻断 QUIC，失效时断开）", node)          # the picker
            location = self.post(srv, "/nodes/alpha/egress/new", egress_id=str(egress_id), scheme_id=str(scheme_id),
                                 domains="x.test", network="tcp", on_failure="direct")[2]["location"]
            self.assertIn("以方案为准", urllib.parse.unquote(location))
            self.assertIn("alpha", srv.request("GET", f"/egress/schemes/{scheme_id}")[1])
            self.post(srv, f"/egress/schemes/{scheme_id}/edit", name="AI 与 ABEMA", domains="chatgpt.com\nabema.tv", ips="",
                      network="tcp-block-udp", on_failure="block", note="旧系统 jp10 的网站")
            self.assertIn("domain:abema.tv", srv.request("GET", "/nodes/alpha")[1])
            location = self.post(srv, f"/egress/schemes/{scheme_id}/delete", confirm="AI 与 ABEMA")[2]["location"]
            self.assertIn("还在用这个方案", urllib.parse.unquote(location))
        with self.env.conn() as conn:
            actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log WHERE action LIKE 'egress-%'")]
        self.assertEqual(actions, ["egress-scheme-add", "egress-assign", "egress-scheme-edit"])

    def test_xray_checks_conditions_before_they_are_saved(self):
        with self.env.conn() as conn:
            egress_id = egress.save(conn, None, "r6s", "socks5", {"host": "203.0.113.10", "port": 1080}, [], "")
        egress_probe.test_rules = lambda xray, rules: "failed to parse domain rule: geosite:netflixx"
        with Server(self.app) as srv:
            location = self.post(srv, "/egress/schemes/new", name="bad", domains="geosite:netflixx", ips="",
                                 network="tcp-block-udp", on_failure="block")[2]["location"]
            self.assertIn("Xray 不接受这些条件：failed to parse domain rule: geosite:netflixx", urllib.parse.unquote(location))
            location = self.post(srv, "/nodes/alpha/egress/new", egress_id=str(egress_id), domains="geosite:netflixx",
                                 network="tcp", on_failure="direct")[2]["location"]
            self.assertIn("Xray 不接受这些条件", urllib.parse.unquote(location))
        with self.env.conn() as conn:
            self.assertEqual((egress.schemes(conn), egress.assignments(conn)), ({}, []))

    def test_select_all_script(self):
        with Server(self.app) as srv:
            status, body, headers = srv.request("GET", "/static/select-all.js")
            self.assertEqual((status, headers["content-type"]), (200, "text/javascript; charset=utf-8"))
            self.assertIn("data-select-all", body)
            _, page, headers = srv.request("GET", "/users")
            policy = headers["content-security-policy"]
            self.assertIn("script-src 'self';", policy)
            self.assertNotIn("unsafe-inline';", policy.split("script-src")[1].split(";")[0] + ";")
            self.assertIn('<script src="/static/select-all.js" defer></script>', page)
            self.assertNotIn("<script>", page)                                             # no inline script anywhere
            self.assertIn('data-select-all="names"', srv.request("GET", "/nodes")[1])

    def test_bulk_import_page(self):
        with Server(self.app) as srv:
            status, _, headers = self.post(srv, "/egress/import", links=f"socks5://u:{SECRET}@203.0.113.30:1080#a\nnope",
                                           labels="")
            notice = urllib.parse.unquote(headers["location"])
        self.assertIn("导入 1 个出口；1 行未导入：第 2 行 无法识别的链接", notice)


if __name__ == "__main__":
    unittest.main()
