"""Synthetic catalog inputs for the subscription service tests. No real hosts, keys or tokens."""
import json
import os

PBK_A = "A" * 42 + "E"
PBK_B = "B" * 42 + "E"
PBK_LEGACY = "C" * 42 + "E"
TOKENS = {"alice": "a" * 42 + "A", "bob": "b" * 42 + "B", "test": "t" * 42 + "T"}
UUIDS = {"alice": "11111111-1111-4111-8111-111111111111", "bob": "22222222-2222-4222-8222-222222222222",
         "test": "33333333-3333-4333-8333-333333333333"}


def member(name):
    return {"name": name, "uuid": UUIDS[name], "short_id": "a1a1a1a1"}


def collected(alpha_state="migrated"):
    return {
        "generated_at": "2026-01-01T00:00:00Z",
        "nodes": {
            "alpha": {"label": "alpha [tag]", "state": alpha_state,
                      "edge": {"endpoint": "alpha.example.test", "port": 443, "sni": "www.example.com",
                               "public_key": PBK_A, "xhttp": {"enabled": True, "path": "/xp/synthetic", "mode": "auto"}},
                      "users": [member("alice"), member("test")]},
            "beta": {"label": "beta", "state": "legacy", "edge": None,
                     "users": [member("alice"), member("bob")]},
            "gamma": {"label": "gamma", "state": "migrated",
                      "edge": {"endpoint": "gamma.example.test", "port": 443, "sni": "www.example.org",
                               "public_key": PBK_B, "xhttp": {"enabled": False, "path": "", "mode": "auto"}},
                      "users": [member("test")]},
        },
    }


def legacy_link(user, host, port, label):
    return (f"vless://{UUIDS[user]}@{host}:{port}?encryption=none&security=reality&type=tcp&sni=www.example.net"
            f"&fp=chrome&pbk={PBK_LEGACY}&sid=b1b1b1b1&flow=xtls-rprx-vision#{label}")


def write_legacy(directory, extra=None):
    files = {
        "alice_beta.json": [{"user": "alice", "id": UUIDS["alice"], "subscription": legacy_link("alice", "198.51.100.7", 20001, "alice.beta")},
                            {"user": "alice", "id": UUIDS["alice"], "subscription": legacy_link("alice", "[2001:db8::7]", 20001, "alice.beta_IPv6")}],
        "bob_beta.json": [{"user": "bob", "id": UUIDS["bob"], "subscription": legacy_link("bob", "198.51.100.7", 20002, "bob.beta")}],
    }
    files.update(extra or {})
    for name, content in files.items():
        with open(os.path.join(directory, name), "w") as fh:
            json.dump(content, fh)
