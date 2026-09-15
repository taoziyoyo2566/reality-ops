"""Catalog and token table: schema, validation and loading (plan-subscription-service §3.2–3.3).

The catalog holds what the renderer needs per user and node; the token table maps SHA-256 digests of
subscription tokens to user names. Neither may contain a private key or a token in clear text.
"""
import hashlib
import json
import re

from . import links

SCHEMA = 1
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{1,200}$")
STATES = ("legacy", "migrated")
FORBIDDEN = ("private_key", "privatekey", "privateKey")


class CatalogError(ValueError):
    pass


def _require(cond, msg):
    if not cond:
        raise CatalogError(msg)


def token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _validate_edge(name, edge):
    _require(isinstance(edge, dict), f"node {name}: edge must be an object")
    _require(isinstance(edge.get("endpoint"), str) and links.HOST_RE.match(edge["endpoint"]),
             f"node {name}: invalid endpoint")
    _require(isinstance(edge.get("port"), int) and 1 <= edge["port"] <= 65535, f"node {name}: invalid port")
    _require(isinstance(edge.get("sni"), str) and edge["sni"], f"node {name}: missing sni")
    _require(isinstance(edge.get("public_key"), str) and links.KEY_RE.match(edge["public_key"]),
             f"node {name}: invalid public key")
    xhttp = edge.get("xhttp") or {"enabled": False}
    _require(isinstance(xhttp.get("enabled"), bool), f"node {name}: xhttp.enabled must be a boolean")
    if xhttp["enabled"]:
        _require(isinstance(xhttp.get("path"), str) and PATH_RE.match(xhttp["path"]), f"node {name}: invalid xhttp path")


def validate_catalog(catalog):
    _require(isinstance(catalog, dict) and catalog.get("schema") == SCHEMA, "unsupported catalog schema")
    text = json.dumps(catalog)
    _require(not any(word in text for word in FORBIDDEN), "catalog must not contain private keys")
    nodes = catalog.get("nodes")
    _require(isinstance(nodes, dict), "nodes must be an object")
    for name, node in nodes.items():
        _require(NAME_RE.match(name), f"invalid node name {name!r}")
        _require(isinstance(node.get("label"), str) and node["label"], f"node {name}: missing label")
        _require(node.get("state") in STATES, f"node {name}: invalid state")
        if node["state"] == "migrated":
            _validate_edge(name, node.get("edge"))
    users = catalog.get("users")
    _require(isinstance(users, dict), "users must be an object")
    for user, data in users.items():
        _require(NAME_RE.match(user), f"invalid user name {user!r}")
        entries = (data or {}).get("nodes")
        _require(isinstance(entries, dict), f"user {user}: nodes must be an object")
        for node_name, entry in entries.items():
            _require(node_name in nodes, f"user {user}: unknown node {node_name}")
            state = nodes[node_name]["state"]
            if state == "migrated":
                _require(isinstance(entry.get("uuid"), str) and links.UUID_RE.match(entry["uuid"]),
                         f"user {user} on {node_name}: invalid uuid")
                _require(isinstance(entry.get("short_id"), str) and links.SHORT_ID_RE.match(entry["short_id"]),
                         f"user {user} on {node_name}: invalid short id")
            else:
                legacy = entry.get("legacy_links")
                _require(isinstance(legacy, list) and legacy, f"user {user} on {node_name}: no legacy links")
                for link in legacy:
                    try:
                        links.parse_vless(link)
                    except links.LinkError as exc:
                        raise CatalogError(f"user {user} on {node_name}: {exc}") from None
    return catalog


def validate_tokens(tokens, catalog):
    _require(isinstance(tokens, dict) and tokens.get("schema") == SCHEMA, "unsupported token table schema")
    table = tokens.get("tokens")
    _require(isinstance(table, dict), "tokens must be an object")
    for digest, user in table.items():
        _require(DIGEST_RE.match(digest), "token table keys must be SHA-256 hex digests")
        _require(user in catalog["users"], f"token for unknown user {user}")
    owners = list(table.values())
    _require(len(owners) == len(set(owners)), "a user has more than one token")
    missing = sorted(set(catalog["users"]) - set(owners))
    _require(not missing, f"users without a token: {missing}")
    return tokens


def load(catalog_path, tokens_path):
    with open(catalog_path) as fh:
        catalog = validate_catalog(json.load(fh))
    with open(tokens_path) as fh:
        tokens = validate_tokens(json.load(fh), catalog)
    return catalog, tokens["tokens"]
