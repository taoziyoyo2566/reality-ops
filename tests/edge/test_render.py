#!/usr/bin/env python3
"""xray_edge renderer contract: validation, conf.d shape, change classification.

Run: python3 -m unittest discover -s tests/edge -v
Regenerate golden files after an intended change: UPDATE_GOLDEN=1 python3 -m unittest discover -s tests/edge
All values are synthetic; the private key is a placeholder, not a key.
"""
import copy
import importlib.util
import json
import os
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLDEN = pathlib.Path(__file__).resolve().parent / "golden"
SPEC = importlib.util.spec_from_file_location("xray_edge_apply", REPO / "roles/xray_edge/files/xray_edge_apply.py")
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)

PRIVATE_KEY = "PLACEHOLDER-NOT-A-KEY"


def desired(**overrides):
    base = {
        "schema": 1,
        "node": "node1",
        "generated_at": "2026-01-01T00:00:00Z",
        "reality": {"target": "www.example.com:443", "server_names": ["www.example.com"]},
        "listen": {"port": 443},
        "xhttp": {"enabled": False, "path": "", "mode": "auto"},
        "users": [
            {"name": "bob", "uuid": "22222222-2222-4222-8222-222222222222", "short_id": "b0b0b0b0"},
            {"name": "alice", "uuid": "11111111-1111-4111-8111-111111111111", "short_id": "a1a1a1a1"},
        ],
        "socks5": [],
        "log": {"level": "warning"},
    }
    base.update(overrides)
    return base


def inbound(files, tag):
    return next(i for i in files["10-inbounds.json"]["inbounds"] if i["tag"] == tag)


class ValidationTest(unittest.TestCase):
    def assertRejected(self, state, fragment):
        with self.assertRaises(edge.ApplyError) as ctx:
            edge.render(state, PRIVATE_KEY)
        self.assertIn(fragment, str(ctx.exception))

    def test_user_name_must_not_break_email_parsing(self):
        for bad in ("a.b", "a@b", "", "x" * 65):
            state = desired()
            state["users"][0]["name"] = bad
            self.assertRejected(state, "invalid user name")

    def test_duplicate_names_and_uuids(self):
        state = desired()
        state["users"][1]["name"] = "bob"
        self.assertRejected(state, "duplicate user")
        state = desired()
        state["users"][1]["uuid"] = state["users"][0]["uuid"].upper()
        self.assertRejected(state, "reuses another user's uuid")

    def test_invalid_fields(self):
        cases = [
            ({"schema": 2}, "unsupported schema"),
            ({"reality": {"target": "www.example.com", "server_names": ["www.example.com"]}}, "host:port"),
            ({"reality": {"target": "www.example.com:99999", "server_names": ["www.example.com"]}}, "target port"),
            ({"reality": {"target": "www.example.com:0", "server_names": ["www.example.com"]}}, "target port"),
            ({"reality": {"target": "www.example.com:443", "server_names": []}}, "must not be empty"),
            ({"listen": {"port": 0}}, "listen.port"),
            ({"xhttp": {"enabled": True, "path": ""}}, "xhttp.path"),
            ({"xhttp": {"enabled": True, "path": "no-slash"}}, "xhttp.path"),
            ({"log": {"level": "verbose"}}, "log.level"),
        ]
        for override, fragment in cases:
            with self.subTest(override=override):
                self.assertRejected(desired(**override), fragment)
        state = desired()
        state["users"][0]["short_id"] = "abc"
        self.assertRejected(state, "short_id")

    def test_socks5_gate(self):
        profile = {"name": "p1", "address": "192.0.2.1", "port": 1080, "users": [], "domains": []}
        self.assertRejected(desired(socks5=[profile]), "no route condition")
        profile = dict(profile, users=["carol"])
        self.assertRejected(desired(socks5=[profile]), "not on this node")
        profile = dict(profile, users=["alice"], port=70000)
        self.assertRejected(desired(socks5=[profile]), "port")


class RenderTest(unittest.TestCase):
    def test_step_one_shape(self):
        files = edge.render(desired(), PRIVATE_KEY)
        self.assertEqual(sorted(files), list(edge.CONF_FILES))
        tags = [i["tag"] for i in files["10-inbounds.json"]["inbounds"]]
        self.assertEqual(tags, ["api", "vless-reality"])
        reality = inbound(files, "vless-reality")
        self.assertEqual(reality["port"], 443)
        self.assertEqual(reality["streamSettings"]["network"], "raw")
        self.assertNotIn("fallbacks", reality["settings"])
        self.assertEqual([c["email"] for c in reality["settings"]["clients"]], ["alice.node1", "bob.node1"])
        self.assertTrue(all(c["flow"] == "xtls-rprx-vision" for c in reality["settings"]["clients"]))
        self.assertEqual(reality["streamSettings"]["realitySettings"]["shortIds"], ["a1a1a1a1", "b0b0b0b0"])
        api = inbound(files, "api")
        self.assertEqual((api["listen"], api["port"]), ("127.0.0.1", 10085))

    def test_private_key_only_in_inbounds(self):
        files = edge.render(desired(), PRIVATE_KEY)
        for name, doc in files.items():
            self.assertEqual(PRIVATE_KEY in json.dumps(doc), name == "10-inbounds.json", name)

    def test_xhttp_step(self):
        files = edge.render(desired(xhttp={"enabled": True, "path": "/p-abc123", "mode": "auto"}), PRIVATE_KEY)
        reality = inbound(files, "vless-reality")
        self.assertEqual(reality["settings"]["fallbacks"], [{"dest": "@xray-edge-xhttp", "xver": 1}])
        xhttp = inbound(files, "vless-xhttp")
        self.assertEqual(xhttp["listen"], "@xray-edge-xhttp")
        self.assertEqual(xhttp["streamSettings"]["xhttpSettings"]["path"], "/p-abc123")
        self.assertTrue(xhttp["streamSettings"]["sockopt"]["acceptProxyProtocol"])
        self.assertNotIn("port", xhttp)
        self.assertEqual({c["email"] for c in xhttp["settings"]["clients"]},
                         {c["email"] for c in reality["settings"]["clients"]})
        self.assertTrue(all("flow" not in c for c in xhttp["settings"]["clients"]))

    def test_socks5_outbounds_and_rules_come_together(self):
        profiles = [
            {"name": "late", "priority": 90, "address": "192.0.2.2", "port": 1080, "domains": ["domain:example.org"]},
            {"name": "early", "priority": 10, "address": "192.0.2.1", "port": 1081, "users": ["bob"],
             "username": "u", "password": "p", "network": "tcp"},
        ]
        files = edge.render(desired(socks5=profiles), PRIVATE_KEY)
        outbound_tags = [o["tag"] for o in files["20-outbounds.json"]["outbounds"]]
        self.assertEqual(outbound_tags, ["direct", "blocked", "socks5-early", "socks5-late"])
        rules = files["30-routing.json"]["routing"]["rules"]
        rule_tags = [r["ruleTag"] for r in rules]
        self.assertEqual(rule_tags, ["api", "block-bt", "block-private", "socks5-early", "socks5-late", "default"])
        early = rules[3]
        self.assertEqual((early["user"], early["network"], early["outboundTag"]), (["bob.node1"], "tcp", "socks5-early"))
        referenced = {r["outboundTag"] for r in rules}
        self.assertTrue({t for t in outbound_tags if t.startswith("socks5-")} <= referenced)
        self.assertTrue(referenced - {"api"} <= set(outbound_tags))
        self.assertEqual(rules[-1], {"type": "field", "ruleTag": "default", "network": "tcp,udp", "outboundTag": "direct"})

    def test_happy_eyeballs_egress(self):
        plain = edge.render(desired(), PRIVATE_KEY)["20-outbounds.json"]["outbounds"][0]
        self.assertNotIn("streamSettings", plain)
        raced = edge.render(desired(egress={"happy_eyeballs": True}), PRIVATE_KEY)["20-outbounds.json"]["outbounds"][0]
        sockopt = raced["streamSettings"]["sockopt"]
        self.assertEqual((raced["tag"], sockopt["domainStrategy"]), ("direct", "UseIP"))
        self.assertEqual(sockopt["happyEyeballs"], {"tryDelayMs": 250, "prioritizeIPv6": False, "interleave": 1, "maxConcurrentTry": 4})
        with self.assertRaises(edge.ApplyError):
            edge.render(desired(egress={"happy_eyeballs": "yes"}), PRIVATE_KEY)

    def test_render_is_order_independent(self):
        state = desired()
        reordered = copy.deepcopy(state)
        reordered["users"].reverse()
        self.assertEqual(edge.render(state, PRIVATE_KEY), edge.render(reordered, PRIVATE_KEY))


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        self.live = edge.render(desired(), PRIVATE_KEY)

    def test_identical_is_none(self):
        self.assertEqual(edge.classify(self.live, edge.render(desired(), PRIVATE_KEY)), "none")

    def test_first_install_restarts(self):
        self.assertEqual(edge.classify({}, self.live), "restart")

    def test_removing_a_user_is_api(self):
        state = desired()
        state["users"] = [u for u in state["users"] if u["name"] == "alice"]
        new = edge.render(state, PRIVATE_KEY)
        self.assertEqual(edge.classify(self.live, new), "api")
        self.assertEqual(edge.user_diff(self.live, new), {"vless-reality": (["bob.node1"], [])})

    def test_adding_a_user_with_known_short_id_is_api(self):
        state = desired()
        state["users"].append({"name": "carol", "uuid": "33333333-3333-4333-8333-333333333333", "short_id": "a1a1a1a1"})
        new = edge.render(state, PRIVATE_KEY)
        self.assertEqual(edge.classify(self.live, new), "api")
        remove, add = edge.user_diff(self.live, new)["vless-reality"]
        self.assertEqual((remove, [c["email"] for c in add]), ([], ["carol.node1"]))

    def test_new_short_id_needs_restart(self):
        state = desired()
        state["users"].append({"name": "carol", "uuid": "33333333-3333-4333-8333-333333333333", "short_id": "c0c0c0c0"})
        self.assertEqual(edge.classify(self.live, edge.render(state, PRIVATE_KEY)), "restart")

    def test_changed_uuid_is_remove_then_add(self):
        state = desired()
        state["users"][0]["uuid"] = "44444444-4444-4444-8444-444444444444"
        new = edge.render(state, PRIVATE_KEY)
        self.assertEqual(edge.classify(self.live, new), "api")
        remove, add = edge.user_diff(self.live, new)["vless-reality"]
        self.assertEqual((remove, [c["email"] for c in add]), (["bob.node1"], ["bob.node1"]))

    def test_non_user_changes_restart(self):
        for override in ({"listen": {"port": 8443}},
                         {"reality": {"target": "www.example.net:443", "server_names": ["www.example.net"]}},
                         {"xhttp": {"enabled": True, "path": "/p", "mode": "auto"}},
                         {"log": {"level": "info"}},
                         {"egress": {"happy_eyeballs": True}}):
            with self.subTest(override=override):
                self.assertEqual(edge.classify(self.live, edge.render(desired(**override), PRIVATE_KEY)), "restart")

    def test_key_rotation_restarts(self):
        self.assertEqual(edge.classify(self.live, edge.render(desired(), "OTHER-PLACEHOLDER")), "restart")


class GoldenTest(unittest.TestCase):
    CASES = {
        "step1": desired(),
        "step2-xhttp-socks5": desired(
            xhttp={"enabled": True, "path": "/p-golden", "mode": "auto"},
            socks5=[{"name": "p1", "priority": 40, "address": "192.0.2.1", "port": 1080,
                     "users": ["alice"], "username": "u", "password": "p", "network": "tcp"}],
        ),
    }

    def test_golden(self):
        for case, state in self.CASES.items():
            with self.subTest(case=case):
                files = edge.render(state, PRIVATE_KEY)
                for name, doc in files.items():
                    path = GOLDEN / case / name
                    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
                    if os.environ.get("UPDATE_GOLDEN"):
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(text)
                    self.assertEqual(path.read_text(), text, f"{case}/{name} differs from golden")


if __name__ == "__main__":
    unittest.main()
