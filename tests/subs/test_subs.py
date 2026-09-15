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
from subs import build, catalog as cat, links, render, server  # noqa: E402

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
