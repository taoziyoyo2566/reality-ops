#!/usr/bin/env python3
"""Node agent user sync (plan-console-phase2 §3.3, §6.1 item 2): plans, guards and a full cycle against a fake
Xray API and a fake console.

Run: python3 -m unittest discover -s tests/edge -p 'test_agent.py'
All values are synthetic.
"""
import hashlib
import hmac
import importlib.util
import json
import pathlib
import sys
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]


def load(name, path):
    """The tools image puts the agent and its modules side by side; load them under the same names."""
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


egress_probe = load("egress_probe", "console/egress_probe.py")
edge_egress = load("edge_egress", "docker/edge-tools/edge_egress.py")
rep = load("edge_reporter", "docker/edge-tools/edge_reporter.py")

U = {n: f"{i:08d}-1111-4111-8111-111111111111" for i, n in enumerate(["alice", "bob", "carol", "dave", "erin",
                                                                        "frank", "gina", "hank", "status-probe"])}


def user(name, uuid=None):
    return {"name": name, "uuid": uuid or U[name], "short_id": "a1a1a1a1"}


class FakeXray:
    """The inbounds of one Xray process, driven through the same `xray api` calls the agent makes."""

    def __init__(self, node, inbounds):
        self.node = node
        self.inbounds = {tag: dict(users) for tag, users in inbounds.items()}   # tag -> {email: account}
        self.calls = []
        self.fail = None
        self.online = {}                                                        # email -> {ip: last seen}
        self.outbounds = ["direct", "blocked", "api"]                           # routing state for proxy egress
        self.rules = ["api", "block-bt", "block-private", "default"]
        self.egress_configs = {}

    def api(self, cfg, command, *args, stdin=None):
        self.calls.append((command, args, json.loads(stdin) if stdin else None))
        if self.fail == command:
            raise rep.SyncError(f"xray api {command} failed (rc=1)")
        if command == "inbounduser":
            tag = args[0].split("=", 1)[1]
            if tag not in self.inbounds:
                raise rep.SyncError("xray api inbounduser failed (rc=1)")
            return json.dumps({"users": [{"email": e, "account": {"id": a["id"], "flow": a.get("flow", "")}}
                                         for e, a in self.inbounds[tag].items()]})
        if command == "rmu":
            tag = args[0].split("=", 1)[1]
            for email in args[1:]:
                self.inbounds[tag].pop(email, None)
            return ""
        if command == "lsrules":
            return json.dumps({"rules": [{"ruleTag": t} for t in self.rules]})
        if command == "lso":
            return json.dumps({"outbounds": [{"tag": t} for t in self.outbounds]})
        if command == "rmrules":
            self.rules = [t for t in self.rules if t not in args]
            return ""
        if command == "rmo":
            self.outbounds = [t for t in self.outbounds if t not in args]
            return ""
        if command == "ado":
            for outbound in json.loads(stdin)["outbounds"]:
                self.outbounds.append(outbound["tag"])
                self.egress_configs[outbound["tag"]] = outbound
            return ""
        if command == "adrules":
            assert "-append" in args, "replacing the whole routing table would drop the base rules"
            for rule in json.loads(stdin)["routing"]["rules"]:
                self.rules.append(rule["ruleTag"])
                self.egress_configs[rule["ruleTag"]] = rule
            return ""
        if command == "statsgetallonlineusers":
            return json.dumps({"users": [f"user>>>{e}>>>online" for e in self.online]} if self.online else {})
        if command == "statsonlineiplist":
            email = args[args.index("-email") + 1]
            return json.dumps({"ips": self.online.get(email, {}), "name": f"user>>>{email}>>>online"})
        if command == "adu":
            for inbound in json.loads(stdin)["inbounds"]:
                for c in inbound["settings"]["clients"]:
                    self.inbounds[inbound["tag"]][c["email"]] = {"id": c["id"], "flow": c.get("flow", "")}
            return ""
        raise AssertionError(command)

    def names(self, tag):
        return sorted(e.split(".")[0] for e in self.inbounds[tag])


class FakeConsole:
    """/sync: answers with its list, or `unchanged` when the node says it runs that version."""

    def __init__(self, users):
        self.set(users)
        self.requests = []
        self.status = 200

    def set(self, users):
        self.users = users
        self.version = f"{abs(hash(json.dumps(users, sort_keys=True))) % 16 ** 16:016x}"

    def post(self, url, token, doc):
        self.requests.append(doc)
        if self.status != 200:
            return self.status, None
        key = {"online_key": "0123456789abcdef" * 2, "online_day": "2026-09-19"}
        if doc["applied"] == self.version:
            return 200, dict(key, version=self.version, unchanged=True)
        return 200, dict(key, version=self.version, users=self.users)


def emails(node, names, vision=True):
    return {f"{n}.{node}": {"id": U[n], "flow": "xtls-rprx-vision" if vision else ""} for n in names}


class PlanTest(unittest.TestCase):
    def test_plan_adds_removes_and_replaces_changed_uuids(self):
        desired = {"alice": user("alice"), "bob": user("bob", "99999999-1111-4111-8111-111111111111"), "carol": user("carol")}
        running = {"alice": U["alice"], "bob": U["bob"], "dave": U["dave"], "status-probe": U["status-probe"]}
        self.assertEqual(rep.sync_plan(desired, running, {"status-probe"}), (["bob", "dave"], ["bob", "carol"]))

    def test_guards(self):
        running = {n: U[n] for n in ("alice", "bob", "carol", "dave", "erin", "frank", "gina", "hank")}
        with self.assertRaisesRegex(rep.SyncError, "empty user list"):
            rep.check_plan({}, running, sorted(running), [], set())
        desired = {n: user(n) for n in ("alice", "bob", "carol")}
        remove, add = rep.sync_plan(desired, running, set())
        with self.assertRaisesRegex(rep.SyncError, "remove 5 of 8"):
            rep.check_plan(desired, running, remove, add, set())
        desired = {n: user(n) for n in ("alice", "bob", "carol", "dave", "erin")}
        remove, add = rep.sync_plan(desired, running, set())
        rep.check_plan(desired, running, remove, add, set())               # three removals: allowed
        rep.check_plan({}, {}, [], [], set())                              # nothing to run, nothing running
        small = {"alice": U["alice"], "bob": U["bob"]}
        rep.check_plan({"alice": user("alice")}, small, ["bob"], [], set())   # few users: removals allowed


class TickTest(unittest.TestCase):
    def setUp(self):
        self.cfg = rep.config_from_env({"REPORT_URL": "x", "REPORT_NODE": "n1", "SYNC_ENABLED": "true",
                                        "SYNC_URL": "http://console/sync", "SYNC_KEEP": "status-probe",
                                        "XHTTP_ENABLED": "true", "LISTEN_PORT": "443"})
        self.xray = FakeXray("n1", {
            rep.REALITY_TAG: {**emails("n1", ["alice", "bob", "status-probe"]), "zed.other": {"id": "x"}},
            rep.XHTTP_TAG: emails("n1", ["alice", "bob", "status-probe"], vision=False)})
        self.console = FakeConsole([user("alice"), user("carol")])
        self._api, self._post = rep.xray_api, rep.post_json
        rep.xray_api, rep.post_json = self.xray.api, self.console.post
        self.sync = rep.Sync(self.cfg)

    def tearDown(self):
        rep.xray_api, rep.post_json = self._api, self._post

    def test_a_cycle_applies_the_list_and_reports_it(self):
        self.assertTrue(self.sync.tick("tok"))
        for tag in (rep.REALITY_TAG, rep.XHTTP_TAG):
            self.assertEqual(self.xray.names(tag)[:3], ["alice", "carol", "status-probe"], tag)
        self.assertIn("zed.other", self.xray.inbounds[rep.REALITY_TAG])                # other nodes' emails untouched
        vision = self.xray.inbounds[rep.REALITY_TAG]["carol.n1"]
        self.assertEqual((vision["id"], vision["flow"]), (U["carol"], "xtls-rprx-vision"))
        self.assertEqual(self.xray.inbounds[rep.XHTTP_TAG]["carol.n1"]["flow"], "")
        adds = [c for c in self.xray.calls if c[0] == "adu"]
        self.assertEqual(adds[0][2]["inbounds"][0]["port"], 443)
        self.assertEqual(adds[1][2]["inbounds"][0]["listen"], "@xray-edge-xhttp")
        first, second = self.console.requests
        self.assertEqual((first["applied"], first["running"]), (None, ["alice", "bob"]))
        self.assertEqual((second["applied"], second["running"], second["error"]), (self.console.version, ["alice", "carol"], ""))
        # nothing changed: one request, no API writes
        calls = len(self.xray.calls)
        self.assertTrue(self.sync.tick("tok"))
        self.assertEqual(len(self.console.requests), 3)
        self.assertFalse([c for c in self.xray.calls[calls:] if c[0] in ("adu", "rmu")])

    def test_an_xray_restart_is_repaired_from_the_held_list(self):
        self.sync.tick("tok")
        self.xray.inbounds[rep.REALITY_TAG] = emails("n1", ["alice", "bob", "status-probe"])   # back to conf.d
        self.sync.tick("tok")
        self.assertEqual(self.xray.names(rep.REALITY_TAG), ["alice", "carol", "status-probe"])
        self.assertEqual(self.console.requests[-1]["applied"], self.console.version)

    def test_refused_and_failed_syncs_are_reported(self):
        self.console.set([])
        self.assertTrue(self.sync.tick("tok"))
        self.assertEqual(self.xray.names(rep.REALITY_TAG)[:2], ["alice", "bob"])          # nothing removed
        self.sync.tick("tok")
        self.assertIn("empty user list", self.console.requests[-1]["error"])
        self.assertIsNone(self.console.requests[-1]["applied"])
        self.console.status = 503
        self.assertTrue(self.sync.tick("tok"))
        self.assertIn("HTTP 503", self.sync.error)
        self.xray.fail = "inbounduser"
        self.assertFalse(self.sync.tick("tok"))                                     # Xray unreachable: counts as a failure

    def test_online_networks_are_hashed_and_sent_with_the_next_sync(self):
        now = int(time.time())
        self.xray.online = {
            "alice.n1": {"203.0.113.5": now, "203.0.113.9": now - 30, "2001:db8:1::5": now,
                         "198.51.100.1": now - 9000},                        # a connection open for hours: online
            "bob.n1": {"[::ffff:198.51.100.7]": now},
            "status-probe.n1": {"192.0.2.1": now},                             # the probe account: never reported
            "zed.other": {"192.0.2.2": now},                                  # another node's email
        }
        self.sync.tick("tok")
        self.assertNotIn("online", self.console.requests[0])                     # no key from the console yet
        self.sync.tick("tok")
        sent = self.console.requests[-1]
        digest = lambda net: hmac.new(b"0123456789abcdef" * 2, net.encode(), hashlib.sha256).hexdigest()[:16]
        self.assertEqual(sent["online_day"], "2026-09-19")
        self.assertEqual(sent["online"], {
            "alice": sorted([f"4:{digest('203.0.113.0/24')}", f"4:{digest('198.51.100.0/24')}",
                             f"6:{digest('2001:db8:1::/48')}"]),                      # two addresses, one /24
            "bob": [f"4:{digest('198.51.100.0/24')}"]})
        self.assertNotIn("203.0.113", json.dumps(sent))

    def test_online_errors_do_not_stop_the_sync(self):
        self.sync.tick("tok")
        self.xray.fail = "statsgetallonlineusers"
        self.assertTrue(self.sync.tick("tok"))
        self.assertNotIn("online", self.console.requests[-1])
        self.assertEqual(self.console.requests[-1]["error"], "")

    def test_without_xhttp_only_the_reality_inbound_is_used(self):
        cfg = dict(self.cfg, xhttp=False)
        self.xray.inbounds.pop(rep.XHTTP_TAG)
        self.assertTrue(rep.Sync(cfg).tick("tok"))
        self.assertEqual(self.xray.names(rep.REALITY_TAG)[:3], ["alice", "carol", "status-probe"])


class EgressSwitchTest(unittest.TestCase):
    """The agent's egress switch (EGRESS_ENABLED) and switching while the console cannot be reached."""

    def setUp(self):
        self.cfg = rep.config_from_env({"REPORT_URL": "x", "REPORT_NODE": "n1", "SYNC_ENABLED": "true",
                                        "SYNC_URL": "http://console/sync", "LISTEN_PORT": "443"})
        self.xray = FakeXray("n1", {rep.REALITY_TAG: emails("n1", ["alice", "bob"])})
        self.console = FakeConsole([user("alice"), user("bob")])
        self.ok = True
        self._api, self._post, self._probe = rep.xray_api, rep.post_json, egress_probe.probe
        rep.xray_api, rep.post_json = self.xray.api, self.console.post
        egress_probe.probe = lambda xray, outbounds, url, user_agent: {
            o["tag"]: {"ok": self.ok, "latency_ms": None, "exit_ip": "", "country": "", "error": "" if self.ok else "timeout"}
            for o in outbounds}

    def tearDown(self):
        rep.xray_api, rep.post_json, egress_probe.probe = self._api, self._post, self._probe

    def test_off_unless_enabled(self):
        sync = rep.Sync(self.cfg)
        self.assertIsNone(sync.egress)
        self.assertTrue(sync.tick("tok"))
        self.assertNotIn("egress_applied", self.console.requests[0])           # the console then sends no egress
        self.assertEqual(self.xray.rules, ["api", "block-bt", "block-private", "default"])

    def test_a_failed_egress_falls_back_while_the_console_is_unreachable(self):
        sync = rep.Sync(dict(self.cfg, egress_enabled=True))
        sync.egress.update({"egress": EgressTest.PAYLOAD, "egress_version": "0123456789abcdef",
                            "egress_check_url": "https://check.example.test/"})
        sync.tick("tok")
        self.assertEqual(self.xray.rules[-3:], ["egress-7-a3", "egress-7-a4", "default"])
        self.assertIn("egress_applied", self.console.requests[0])
        self.console.status, self.ok = 502, False
        sync.tick("tok")
        self.assertIn("HTTP 502", sync.error)
        self.assertEqual(self.xray.rules[-2:], ["egress-7-a4", "default"])
        self.assertEqual(self.xray.egress_configs["egress-7-a4"]["outboundTag"], "blocked")


class EgressTest(unittest.TestCase):
    """Proxy egress (plan-egress-console §3.3-3.4): runtime rules in order, fallback or block, recovery."""

    PAYLOAD = {"outbounds": [{"tag": "egress-7", "protocol": "socks", "settings": {"address": "192.0.2.9", "port": 1080}}],
               "assignments": [
                   {"id": 3, "outbound": "egress-7", "rule": {"network": "tcp", "user": ["bob.n1"]}, "on_failure": "direct"},
                   {"id": 4, "outbound": "egress-7", "rule": {"network": "tcp", "domain": ["geosite:amazon"]},
                    "on_failure": "block"}]}

    def setUp(self):
        self.xray = FakeXray("n1", {rep.REALITY_TAG: {}})
        self._probe = egress_probe.probe
        self.ok, self.udp = True, True
        egress_probe.probe = lambda xray, outbounds, url, user_agent: {
            o["tag"]: {"ok": self.ok, "latency_ms": 5 if self.ok else None, "exit_ip": "198.51.100.4" if self.ok else "",
                       "country": "JP", "error": "" if self.ok else "timeout", "udp": self.udp if self.ok else None}
            for o in outbounds}
        self.egress = self.make()
        self.egress.update({"egress": self.PAYLOAD, "egress_version": "0123456789abcdef",
                            "egress_check_url": "https://check.example.test/"})

    def tearDown(self):
        egress_probe.probe = self._probe

    def make(self):
        cfg = {"xray_bin": "xray", "xray_api": "127.0.0.1:10085", "node": "n1"}
        return edge_egress.Egress("xray", lambda *args, stdin=None: self.xray.api(cfg, *args, stdin=stdin), rep.SyncError,
                                  lambda message: None)

    def cycle(self):
        self.egress.check()
        return self.egress.apply()

    def test_rules_go_before_the_default_rule(self):
        self.assertTrue(self.cycle())
        self.assertEqual(self.xray.rules, ["api", "block-bt", "block-private", "egress-7-a3", "egress-7-a4", "default"])
        self.assertEqual(self.xray.egress_configs["egress-7-a3"]["outboundTag"], "egress-7")
        self.assertIn("egress-7", self.xray.outbounds)
        self.assertEqual(self.egress.report(), {"egress_applied": "0123456789abcdef",
                                                "egress_states": {"3": "active", "4": "active"},
                                                "egress_checks": {"7": {"ok": True, "latency_ms": 5, "exit_ip": "198.51.100.4",
                                                                        "country": "JP", "error": "", "udp": True}},
                                                "egress_schema": 2, "egress_error": ""})
        self.assertFalse(self.cycle())                                   # nothing changed: nothing touched

    def test_failure_falls_back_or_blocks_and_recovery_needs_two_checks(self):
        self.cycle()
        self.ok = False
        self.assertTrue(self.cycle())
        self.assertEqual(self.xray.rules[-2:], ["egress-7-a4", "default"])        # a3 falls back: its rule is gone
        self.assertEqual(self.xray.egress_configs["egress-7-a4"]["outboundTag"], "blocked")
        self.assertEqual(self.egress.report()["egress_states"], {"3": "direct", "4": "blocked"})
        self.ok = True
        self.assertFalse(self.cycle())                                   # one good check is not enough
        self.assertTrue(self.cycle())
        self.assertEqual(self.egress.report()["egress_states"], {"3": "active", "4": "active"})

    def test_an_xray_restart_is_repaired(self):
        self.cycle()
        self.xray.rules = ["api", "block-bt", "block-private", "default"]         # back to the configuration files
        self.xray.outbounds = ["direct", "blocked", "api"]
        self.assertTrue(self.cycle())
        self.assertEqual(self.xray.rules[3:], ["egress-7-a3", "egress-7-a4", "default"])

    def test_an_empty_payload_removes_what_it_added(self):
        self.cycle()
        self.egress.update({"egress": {"outbounds": [], "assignments": []}, "egress_version": "fedcba9876543210"})
        self.assertTrue(self.cycle())
        self.assertEqual(self.xray.rules, ["api", "block-bt", "block-private", "default"])
        self.assertNotIn("egress-7", self.xray.outbounds)

    def test_a_failed_apply_keeps_the_default_rule_and_the_blocks(self):
        self.xray.fail = "ado"
        self.assertFalse(self.cycle())
        # no rule to a missing outbound; the "block" assignment (a4) stays blocked instead of going direct
        self.assertEqual(self.xray.rules, ["api", "block-bt", "block-private", "egress-7-a4", "default"])
        self.assertEqual(self.xray.egress_configs["egress-7-a4"]["outboundTag"], "blocked")
        report = self.egress.report()
        self.assertIsNone(report["egress_applied"])
        self.assertIn("ado", report["egress_error"])

    def test_unchanged_outbounds_are_left_alone(self):
        self.cycle()
        calls = len(self.xray.calls)
        self.ok = False                                           # a4 blocks, a3 goes direct: rules change only
        self.cycle()
        self.assertNotIn("rmo", [c[0] for c in self.xray.calls[calls:]])
        self.assertNotIn("ado", [c[0] for c in self.xray.calls[calls:]])
        edited = dict(self.PAYLOAD, outbounds=[dict(self.PAYLOAD["outbounds"][0], settings={"address": "192.0.2.10", "port": 1080})])
        self.egress.update({"egress": edited, "egress_version": "3333333333333333"})
        calls = len(self.xray.calls)
        self.cycle()
        self.assertEqual([c[0] for c in self.xray.calls[calls:] if c[0] in ("rmo", "ado")], ["rmo", "ado"])  # replaced

    def test_a_disabled_egress_keeps_its_blocks(self):
        payload = {"outbounds": [], "assignments": [
            {"id": 4, "outbound": "egress-7", "on_failure": "block", "egress_off": True,
             "rules": [{"domain": ["geosite:amazon"], "network": "tcp", "action": "egress"}]}]}
        self.egress.update({"egress": payload, "egress_version": "4444444444444444"})
        self.cycle()
        self.assertEqual(self.xray.rules[-2:], ["egress-7-a4", "default"])
        self.assertEqual(self.xray.egress_configs["egress-7-a4"]["outboundTag"], "blocked")
        self.assertEqual(self.egress.report()["egress_states"], {"4": "blocked"})
        self.assertNotIn("egress-7", self.xray.outbounds)

    def test_udp_failure_affects_only_assignments_that_take_udp(self):
        payload = {"outbounds": self.PAYLOAD["outbounds"], "assignments": [
            {"id": 3, "outbound": "egress-7", "rule": {"network": "tcp", "user": ["bob.n1"]}, "on_failure": "direct"},
            {"id": 5, "outbound": "egress-7", "rule": {"network": "tcp,udp", "user": ["carol.n1"]}, "on_failure": "direct"},
            {"id": 6, "outbound": "egress-7", "rule": {"network": "tcp,udp", "domain": ["geosite:amazon"]},
             "on_failure": "block"}]}
        self.egress.update({"egress": payload, "egress_version": "1111111111111111"})
        self.udp = False
        self.cycle()
        self.assertEqual(self.egress.report()["egress_states"], {"3": "active", "5": "direct", "6": "blocked"})
        self.assertEqual(self.xray.rules[-3:], ["egress-7-a3", "egress-7-a6", "default"])
        self.assertEqual(self.xray.egress_configs["egress-7-a6"]["outboundTag"], "blocked")
        self.assertIs(self.egress.report()["egress_checks"]["7"]["udp"], False)
        self.udp = True
        self.cycle()
        self.assertEqual(self.egress.report()["egress_states"]["5"], "direct")          # UDP back once: not yet
        self.cycle()
        self.assertEqual(self.egress.report()["egress_states"], {"3": "active", "5": "active", "6": "active"})

    def test_rule_lists_block_udp_to_websites_and_fail_over_together(self):
        sites = {"domain": ["domain:chatgpt.com"]}
        payload = {"outbounds": self.PAYLOAD["outbounds"], "assignments": [
            {"id": 8, "outbound": "egress-7", "on_failure": "direct", "rule": dict(sites, network="tcp"),
             "rules": [dict(sites, network="tcp", action="egress"), dict(sites, network="udp", action="block"),
                       {"ip": ["203.0.113.0/24"], "network": "tcp", "action": "egress"},
                       {"ip": ["203.0.113.0/24"], "network": "udp", "action": "block"}]},
            {"id": 9, "outbound": "egress-7", "on_failure": "block",
             "rules": [dict(sites, user=["bob.n1"], network="tcp", action="egress"),
                       dict(sites, user=["bob.n1"], network="udp", action="block")]}]}
        self.egress.update({"egress": payload, "egress_version": "2222222222222222"})
        self.udp = False                                   # blocking UDP does not need an egress that relays UDP
        self.cycle()
        self.assertEqual(self.egress.report()["egress_states"], {"8": "active", "9": "active"})
        self.assertEqual(self.xray.rules[3:], ["egress-7-a8", "egress-7-a8-2", "egress-7-a8-3", "egress-7-a8-4",
                                               "egress-7-a9", "egress-7-a9-2", "default"])
        self.assertEqual([self.xray.egress_configs[t]["outboundTag"] for t in ("egress-7-a8", "egress-7-a8-2", "egress-7-a8-3")],
                         ["egress-7", "blocked", "egress-7"])
        self.assertEqual(self.xray.egress_configs["egress-7-a8-2"]["network"], "udp")
        self.ok = False
        self.cycle()
        self.assertEqual(self.egress.report()["egress_states"], {"8": "direct", "9": "blocked"})
        self.assertEqual(self.xray.rules[3:], ["egress-7-a9", "egress-7-a9-2", "default"])     # 8 gone, UDP too
        self.assertEqual({self.xray.egress_configs[t]["outboundTag"] for t in ("egress-7-a9", "egress-7-a9-2")}, {"blocked"})

    def test_nothing_is_touched_without_a_payload(self):
        idle = self.make()
        self.assertFalse(idle.apply())
        self.assertEqual(self.xray.calls, [])


class ProbeTest(unittest.TestCase):
    def test_exit_from_trace_or_json(self):
        self.assertEqual(egress_probe.parse_exit("fl=1\nip=198.51.100.4\nloc=JP\n"), ("198.51.100.4", "JP"))
        self.assertEqual(egress_probe.parse_exit('{"ip": "2001:db8::1", "country_iso": "DE"}'), ("2001:db8::1", "DE"))
        self.assertEqual(egress_probe.parse_exit("<html>"), ("", ""))

    def test_every_egress_fails_without_xray(self):
        outbounds = [{"tag": "egress-1", "protocol": "socks", "settings": {}},
                     {"tag": "egress-2", "protocol": "socks", "settings": {}}]
        found = egress_probe.probe("/nonexistent/xray", outbounds, "https://check.example.test/", "test/1")
        self.assertEqual({t: (r["ok"], r["error"]) for t, r in found.items()},
                         {"egress-1": (False, "xray: FileNotFoundError"), "egress-2": (False, "xray: FileNotFoundError")})


if __name__ == "__main__":
    unittest.main()
