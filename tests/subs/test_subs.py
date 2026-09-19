#!/usr/bin/env python3
"""Subscription service unit, golden and HTTP tests (plan-subscription-service §6.1 items 1-2).

Run with PyYAML and segno installed, e.g. inside the subs image or a venv from docker/subs/requirements.txt:
  python tests/subs/test_subs.py            (UPDATE_GOLDEN=1 rewrites tests/subs/golden)
"""
import base64
import http.client
import io
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixtures  # noqa: E402
from subs import build, catalog as cat, links, render, server, statuspage  # noqa: E402

GOLDEN = pathlib.Path(__file__).resolve().parent / "golden"
ENABLED = ["alice", "bob", "test"]


def built(alpha_state="migrated", extra_legacy=None, tokens=None, enabled=ENABLED):
    tmp = tempfile.mkdtemp()
    try:
        fixtures.write_legacy(tmp, extra_legacy)
        return build.build(fixtures.collected(alpha_state), tokens or fixtures.TOKENS, enabled, build.legacy_files(tmp))
    finally:
        shutil.rmtree(tmp)


class BuildTest(unittest.TestCase):
    def test_catalog_shape(self):
        catalog, tokens, problems, ignored = built()
        self.assertEqual((problems, ignored), ([], []))
        self.assertEqual(set(catalog["users"]["alice"]["nodes"]), {"alpha", "beta"})
        self.assertIn("uuid", catalog["users"]["alice"]["nodes"]["alpha"])
        self.assertIn("legacy_links", catalog["users"]["alice"]["nodes"]["beta"])
        self.assertEqual(set(catalog["users"]["test"]["nodes"]), {"alpha", "gamma"})
        self.assertEqual(sorted(tokens["tokens"].values()), ENABLED)
        text = json.dumps([catalog, tokens])
        for token in fixtures.TOKENS.values():
            self.assertNotIn(token, text)

    def test_acl_decides_over_legacy_files(self):
        extra = {"bob_alpha.json": [{"subscription": fixtures.legacy_link("bob", "198.51.100.8", 1, "x")}],
                 "alice_retired.json": [{"subscription": fixtures.legacy_link("alice", "198.51.100.9", 1, "y")}]}
        catalog, _, problems, ignored = built(alpha_state="legacy", extra_legacy=extra)
        self.assertEqual(problems, ["alice on alpha: allowed by ACL but no legacy file",
                                    "test on alpha: allowed by ACL but no legacy file"])
        self.assertEqual(sorted(ignored), ["alice: legacy file for unknown node retired",
                                           "bob on alpha: legacy file not allowed by ACL"])
        self.assertNotIn("alpha", catalog["users"]["bob"]["nodes"])
        self.assertNotIn("retired", catalog["nodes"])

    def test_enabled_user_without_token_fails(self):
        with self.assertRaisesRegex(cat.CatalogError, "without a token"):
            built(tokens={"alice": fixtures.TOKENS["alice"]})

    def test_duplicate_and_malformed_tokens_fail(self):
        with self.assertRaisesRegex(cat.CatalogError, "share a token"):
            built(tokens=dict(fixtures.TOKENS, bob=fixtures.TOKENS["alice"]))
        with self.assertRaisesRegex(cat.CatalogError, "43 URL-safe"):
            built(tokens=dict(fixtures.TOKENS, bob="short"))

    def test_private_key_is_refused(self):
        catalog, _, _, _ = built()
        catalog["nodes"]["alpha"]["edge"]["private_key"] = "x"
        with self.assertRaisesRegex(cat.CatalogError, "private keys"):
            cat.validate_catalog(catalog)

    def test_invalid_legacy_link_is_refused(self):
        catalog, _, _, _ = built()
        catalog["users"]["bob"]["nodes"]["beta"]["legacy_links"] = ["vless://not-a-uuid@h:1?security=reality"]
        with self.assertRaises(cat.CatalogError):
            cat.validate_catalog(catalog)

    def test_token_table_must_cover_exactly_the_users(self):
        catalog, tokens, _, _ = built()
        tokens["tokens"].pop(cat.token_digest(fixtures.TOKENS["bob"]))
        with self.assertRaisesRegex(cat.CatalogError, "without a token"):
            cat.validate_tokens(tokens, catalog)


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.catalog, _, _, _ = built()

    def decode(self, text):
        return base64.b64decode(text).decode().splitlines()

    def test_v2ray_mixes_migrated_and_legacy(self):
        lines = self.decode(render.v2ray(self.catalog, "alice"))
        self.assertEqual(len(lines), 3)  # alpha Vision + XHTTP, beta IPv4
        self.assertTrue(lines[0].startswith(f"vless://{fixtures.UUIDS['alice']}@alpha.example.test:443?"))
        self.assertIn("path=%2Fxp%2Fsynthetic", lines[1])
        self.assertTrue(lines[1].endswith("#alpha%20%5Btag%5D-xhttp"))
        self.assertIn("@198.51.100.7:20001", lines[2])
        full = self.decode(render.v2ray(self.catalog, "alice", full=True))
        self.assertEqual(len(full), 4)
        self.assertIn("@[2001:db8::7]:20001", full[3])

    def test_edge_links_round_trip_and_hide_user_names(self):
        for name, link in render.share_links(self.catalog, "test"):
            parts = links.parse_vless(link)
            self.assertNotIn("test", parts["name"])
            self.assertEqual(parts["short_id"], "a1a1a1a1")
        xhttp = links.parse_vless(render.share_links(self.catalog, "test")[1][1])
        self.assertEqual((xhttp["type"], xhttp["path"]), ("xhttp", "/xp/synthetic"))

    def test_a_user_only_sees_their_nodes(self):
        self.assertEqual([n for n, _, _ in render.user_entries(self.catalog, "bob")], ["beta"])
        text = render.clash_profile(self.catalog, "bob", "privacy")
        self.assertNotIn(fixtures.UUIDS["alice"], text)
        self.assertNotIn("alpha", text)

    def test_clash_modes(self):
        privacy = yaml.safe_load(render.clash_profile(self.catalog, "alice", "privacy"))
        split = yaml.safe_load(render.clash_profile(self.catalog, "alice", "split"))
        self.assertEqual(privacy["rules"][-1], "MATCH,PROXY")
        self.assertFalse(any(r.startswith(("GEOSITE", "GEOIP")) for r in privacy["rules"]))
        self.assertIn("GEOSITE,cn,DIRECT", split["rules"])
        self.assertTrue(privacy["tun"]["strict-route"] and split["tun"]["strict-route"])
        self.assertEqual([p["name"] for p in privacy["proxies"]], ["alpha [tag]", "alpha [tag]-xhttp", "beta"])
        self.assertEqual(privacy["proxies"][1]["xhttp-opts"], {"path": "/xp/synthetic", "mode": "stream-one"})
        self.assertEqual(privacy["proxy-groups"][0]["proxies"][0], "AUTO")

    def test_user_without_nodes_rejects_instead_of_going_direct(self):
        self.catalog["users"]["bob"]["nodes"] = {}
        profile = yaml.safe_load(render.clash_profile(self.catalog, "bob", "privacy"))
        self.assertEqual(profile["proxy-groups"], [{"name": "PROXY", "type": "select", "proxies": ["REJECT"]}])

    def test_page_has_qr_codes_deeplinks_and_split_first(self):
        html = render.page(self.catalog, "alice", "https://subs.example.test/s/" + fixtures.TOKENS["alice"])
        self.assertEqual(html.count("<svg"), 4)
        self.assertIn("clash://install-config?url=https%3A%2F%2Fsubs.example.test%2Fs%2F", html)
        self.assertLess(html.index("clash-split"), html.index("clash-privacy"))
        self.assertNotIn("<script", html)
        self.assertNotIn(fixtures.UUIDS["alice"], html)

    def test_page_explains_timezone_and_unsolvable_signals(self):
        html = render.page(self.catalog, "alice", "https://subs.example.test/s/" + fixtures.TOKENS["alice"])
        self.assertIn("选节点的提示", html)
        self.assertIn("系统时区", html)
        self.assertIn("无法通过网络层解决的识别", html)
        self.assertLess(html.index("选节点的提示"), html.index("无法通过网络层解决的识别"))

    def test_golden(self):
        GOLDEN.mkdir(exist_ok=True)
        for name, text in {"alice.v2ray.txt": "\n".join(self.decode(render.v2ray(self.catalog, "alice", full=True))) + "\n",
                           "alice.clash-split.yaml": render.clash_profile(self.catalog, "alice", "split"),
                           "alice.clash-privacy.yaml": render.clash_profile(self.catalog, "alice", "privacy")}.items():
            path = GOLDEN / name
            if os.environ.get("UPDATE_GOLDEN"):
                path.write_text(text)
            self.assertEqual(text, path.read_text(), name)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.db = tempfile.mkdtemp()
        catalog, tokens, _, _ = built()
        self.write("catalog.json", catalog)
        self.write("tokens.json", tokens)
        self.stderr = io.StringIO()
        with redirect_stderr(self.stderr):
            self.server = server.serve(self.data, self.db, "127.0.0.1", 0, "https://subs.example.test")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.data)
        shutil.rmtree(self.db)

    def write(self, name, doc):
        path = os.path.join(self.data, name)
        with open(path + ".tmp", "w") as fh:
            fh.write(doc if isinstance(doc, str) else json.dumps(doc))
        os.replace(path + ".tmp", path)

    def get(self, path):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        with redirect_stderr(self.stderr):
            conn.request("GET", path, headers={"User-Agent": "test-agent", "CF-Connecting-IP": "203.0.113.5"})
            resp = conn.getresponse()
            body = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), body

    def test_each_token_gets_only_its_user(self):
        status, headers, body = self.get(f"/s/{fixtures.TOKENS['bob']}/v2ray")
        self.assertEqual(status, 200)
        text = base64.b64decode(body).decode()
        self.assertIn(fixtures.UUIDS["bob"], text)
        self.assertNotIn(fixtures.UUIDS["alice"], text)
        for key in ("Cache-Control", "Referrer-Policy", "X-Robots-Tag", "X-Content-Type-Options"):
            self.assertIn(key, headers)

    def test_page_address_serves_subscription_clients(self):
        path = f"/s/{fixtures.TOKENS['alice']}"
        for agent, expect in (("Shadowrocket/3445 CFNetwork/3860.700.1 Darwin/25.6.0", "v2ray"),
                              ("v2rayN/7.24.9", "v2ray"), ("v2rayNG/1.10.0", "v2ray"),
                              ("clash-verge/v2.4.0", "clash"), ("FlClash/v0.8", "clash"),
                              ("mihomo.party/v1.8", "clash"), ("Stash/3.1.1 Clash/1.9.0", "clash"),
                              ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) Safari/604.1", "page"),
                              ("", "page"), ("curl/8.5.0", "page")):
            conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
            conn.request("GET", path, headers={"User-Agent": agent} if agent else {})
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            self.assertEqual(resp.status, 200, agent)
            kind = ("page" if body.startswith(b"<!doctype html>") else
                    "clash" if b"proxy-groups:" in body else "v2ray")
            self.assertEqual(kind, expect, agent)
            if kind == "clash":
                self.assertIn(b"GEOSITE,cn,DIRECT", body)
            if kind == "v2ray":
                self.assertIn(fixtures.UUIDS["alice"], base64.b64decode(body).decode())

    def test_unknown_token_and_format_look_the_same(self):
        a = self.get("/s/" + "z" * 43 + "/v2ray")
        b = self.get(f"/s/{fixtures.TOKENS['alice']}/nope")
        c = self.get("/anything")
        self.assertEqual({a[0], b[0], c[0]}, {404})
        self.assertEqual(a[2], b[2])
        self.assertEqual(a[2], c[2])

    def test_revocation_applies_without_restart(self):
        catalog, tokens, _, _ = built(enabled=["alice", "test"], tokens={k: v for k, v in fixtures.TOKENS.items() if k != "bob"})
        self.write("catalog.json", catalog)
        self.write("tokens.json", tokens)
        self.assertEqual(self.get(f"/s/{fixtures.TOKENS['bob']}/v2ray")[0], 404)
        self.assertEqual(self.get(f"/s/{fixtures.TOKENS['alice']}/v2ray")[0], 200)

    def test_missing_or_corrupt_data_fails_closed(self):
        os.remove(os.path.join(self.data, "tokens.json"))
        self.assertEqual(self.get(f"/s/{fixtures.TOKENS['alice']}/v2ray")[0], 503)
        self.assertEqual(self.get("/healthz")[0], 503)
        catalog, tokens, _, _ = built()
        self.write("tokens.json", tokens)
        self.write("catalog.json", "{not json")
        self.assertEqual(self.get(f"/s/{fixtures.TOKENS['alice']}/clash-split")[0], 503)
        self.write("catalog.json", catalog)
        self.assertEqual(self.get(f"/s/{fixtures.TOKENS['alice']}/clash-split")[0], 200)

    def status_doc(self):
        return {"schema": 1, "generated_at": 1767283380, "interval": 60, "tz": {"offset_hours": 8, "label": "北京时间"},
                "days": ["2026-01-01", "2026-01-02"],
                "nodes": [{"label": "Alpha 节点", "state": "partial", "availability": 99.5,
                           "days": [["ok", 0, 0, 0], ["partial", 0, 3, 0]]}],
                "events": [{"node": "Alpha 节点", "kind": "partial", "started_at": 1767283200, "ended_at": None,
                            "transports": ["XHTTP"], "all_transports": 2, "note": "<b>x</b>"}]}

    def test_status_page_expires(self):
        doc = self.status_doc()   # generated in January 2026, long before any test run
        self.write("status.json", doc)
        self.assertIn("状态数据已过期", self.get(f"/s/{fixtures.TOKENS['alice']}/status")[2].decode())

    def test_status_page(self):
        token = fixtures.TOKENS["alice"]
        status, headers, body = self.get(f"/s/{token}/status")
        self.assertEqual(status, 200)
        self.assertIn("暂无状态数据", body.decode())
        self.write("status.json", self.status_doc())
        status, headers, body = self.get(f"/s/{token}/status?day=2026-01-02")
        text = body.decode()
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("Alpha 节点", text)
        self.assertIn("XHTTP 链接无法连接", text)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", text)
        self.assertIn(f'href="https://subs.example.test/s/{token}"', text)
        self.assertNotIn("<script", text)
        self.assertEqual(self.get("/s/" + "z" * 43 + "/status")[0], 404)
        # the user page links to it; status views are not subscription fetches
        self.assertIn(f"https://subs.example.test/s/{token}/status", self.get(f"/s/{token}")[2].decode())
        conn = sqlite3.connect(os.path.join(self.db, "access.sqlite"))
        self.assertEqual([r[0] for r in conn.execute("SELECT fmt FROM hits")], ["page"])
        conn.close()

    def test_status_page_views(self):
        token = fixtures.TOKENS["alice"]
        doc = self.status_doc()
        doc.update(fail_count=3, hours={"start": 1767283200 - 3600, "count": 2},
                   minutes={"start": 1767283200, "step": 60, "count": 2})
        doc["nodes"][0].update(hours=[["ok", 60, 60, 0, 0, 0], ["partial", 55, 60, 0, 5, 0]],
                               minutes=[["ok", [], 2], ["partial", ["XHTTP"], 2]])
        self.write("status.json", doc)
        text = self.get(f"/s/{token}/status")[2].decode()
        self.assertIn("<b>最近 2 小时</b>", text)
        self.assertIn("检测 60 次，成功 55 次", text)
        text = self.get(f"/s/{token}/status?view=minute")[2].decode()
        self.assertIn("<b>最近 2 分钟</b>", text)                     # two rounds in this document
        self.assertIn("部分失败（XHTTP 失败）", text)
        self.assertIn("连续 3 次失败才记为故障", text)
        self.assertIn("<b>最近 2 天</b>", self.get(f"/s/{token}/status?view=day&day=2026-01-02")[2].decode())
        self.assertIn("<b>最近 2 小时</b>", self.get(f"/s/{token}/status?view=%3Cscript%3E")[2].decode())

    def test_invalid_status_document_is_not_served(self):
        for mutate in (lambda d: d["nodes"][0].update(state="<script>"), lambda d: d.update(nodes=[1]),
                       lambda d: d["events"][0].update(transports=[1])):
            doc = self.status_doc()
            mutate(doc)
            self.write("status.json", doc)
            with redirect_stderr(self.stderr):
                status, _, body = self.get(f"/s/{fixtures.TOKENS['alice']}/status")
            self.assertEqual(status, 200)
            self.assertIn("暂无状态数据", body.decode())
            self.assertNotIn("<script>", body.decode())
        self.write("status.json", self.status_doc())
        self.assertIn("Alpha 节点", self.get(f"/s/{fixtures.TOKENS['alice']}/status")[2].decode())
        statuspage.validate(self.status_doc())

    def test_page_and_access_log_do_not_leak_tokens(self):
        status, headers, body = self.get(f"/s/{fixtures.TOKENS['alice']}")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn(b"https://subs.example.test/s/", body)
        self.get(f"/s/{fixtures.TOKENS['alice']}/clash-privacy")
        conn = sqlite3.connect(os.path.join(self.db, "access.sqlite"))
        rows = conn.execute("SELECT user, fmt, ip, user_agent FROM hits ORDER BY ts").fetchall()
        conn.close()
        self.assertEqual([r[:2] for r in rows], [("alice", "page"), ("alice", "clash-privacy")])
        self.assertEqual(rows[0][2:], ("203.0.113.5", "test-agent"))
        dump = json.dumps(rows) + self.stderr.getvalue()
        for token in fixtures.TOKENS.values():
            self.assertNotIn(token, dump)


if __name__ == "__main__":
    unittest.main()
