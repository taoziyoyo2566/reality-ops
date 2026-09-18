"""Users, tiers and node access for the new system (plan-console-phase2 §3.1-3.2).

The console database is where users live (D-P2-1). Access keeps the existing ACL (D-P2-3): a node has one or more
tiers; `tier_rules` maps a node tier to the user tiers it accepts; a user whose tiers include `all` may use every
node; `allow` adds nodes and `deny` removes them, deny first. Only active users whose expiry date has not passed
are put on nodes and in subscriptions.
"""
import datetime
import json
import re
import secrets
import uuid as uuidlib

from . import db

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SHORT_ID_RE = re.compile(r"^(?:[0-9a-f]{2}){1,8}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ALL = "all"
STATUSES = ("active", "disabled")
NOTE_MAX = 200
EXPIRY_WARN_DAYS = 7


class UserError(ValueError):
    """A request the console refuses; the message is shown to the administrator."""


def _require(cond, msg):
    if not cond:
        raise UserError(msg)


def today(offset_hours):
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=offset_hours))).date().isoformat()


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def _decode(row):
    user = dict(row)
    for key in ("tiers", "allow_nodes", "deny_nodes"):
        user[key] = json.loads(user[key])
    return user


def load(conn):
    """{name: user} with list fields decoded."""
    return {r["name"]: _decode(r) for r in conn.execute("SELECT * FROM users ORDER BY name")}


def get(conn, name):
    row = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    return _decode(row) if row else None


def imported(conn):
    return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def rules(conn):
    return {r["tier"]: json.loads(r["accepts"]) for r in conn.execute("SELECT * FROM tier_rules ORDER BY tier")}


def node_tiers(conn):
    return {r["node"]: json.loads(r["tiers"]) for r in conn.execute("SELECT * FROM node_tiers ORDER BY node")}


def known_tiers(conn):
    """Every tier a user can be given: `all`, the rule tiers and the tiers they accept."""
    found = set()
    for tier, accepts in rules(conn).items():
        found.add(tier)
        found.update(accepts)
    return [ALL] + sorted(found - {ALL})


# --------------------------------------------------------------------------
# Access
# --------------------------------------------------------------------------

def effective(user, day):
    return user["status"] == "active" and (not user.get("expires_on") or user["expires_on"] >= day)


def can_use(user, node, tiers_of_nodes, tier_rules):
    """The existing ACL: deny wins, then allow, then `all`, then the node tiers' accepted user tiers."""
    if node in user["deny_nodes"]:
        return False
    if node in user["allow_nodes"] or ALL in user["tiers"]:
        return True
    accepted = set()
    for tier in tiers_of_nodes.get(node, []):
        accepted.update(tier_rules.get(tier, []))
    return bool(accepted & set(user["tiers"]))


def node_users(conn, node, day):
    """The users that belong on `node` today: [{name, uuid, short_id}], sorted by name."""
    tiers_of_nodes, tier_rules = node_tiers(conn), rules(conn)
    return [{"name": u["name"], "uuid": u["uuid"], "short_id": u["short_id"]}
            for u in load(conn).values()
            if effective(u, day) and can_use(u, node, tiers_of_nodes, tier_rules)]


def user_nodes(conn, user, nodes):
    """Registered nodes the user may use (ignoring status and expiry)."""
    tiers_of_nodes, tier_rules = node_tiers(conn), rules(conn)
    return [n for n in sorted(nodes) if can_use(user, n, tiers_of_nodes, tier_rules)]


def differences(conn, nodes, day):
    """Per registered node, how the deployed users differ from what the console says.

    {node: {"add": [...], "remove": [...], "change": [...]}} for nodes that differ. `add` are users the next
    deployment puts on the node, `remove` the ones it takes off, `change` users whose UUID or short id differs.
    """
    out = {}
    for name, node in sorted(nodes.items()):
        want = {u["name"]: (u["uuid"].lower(), u["short_id"]) for u in node_users(conn, name, day)}
        have = {n: (v["uuid"].lower(), v["short_id"]) for n, v in node.users.items()}
        diff = {"add": sorted(set(want) - set(have)), "remove": sorted(set(have) - set(want)),
                "change": sorted(n for n in set(want) & set(have) if want[n] != have[n])}
        if any(diff.values()):
            out[name] = diff
    return out


# --------------------------------------------------------------------------
# Changes
# --------------------------------------------------------------------------

def shared_short_id(conn):
    """The short id every user created in the console gets (D-P2-5); created once."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'shared_short_id'").fetchone()
    if row:
        return row["value"]
    value = secrets.token_hex(4)
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('shared_short_id', ?)", (value,))
    return conn.execute("SELECT value FROM settings WHERE key = 'shared_short_id'").fetchone()["value"]


def _clean_list(values, pattern, what):
    items = []
    for value in values or []:
        value = str(value).strip()
        if value:
            _require(pattern.match(value), f"{what}“{value}”不合法")
            if value not in items:
                items.append(value)
    return items


def clean_fields(tiers, allow_nodes, deny_nodes, expires_on, note, known_nodes=None):
    tiers = _clean_list(tiers, TIER_RE, "档位") or [ALL]
    allow = _clean_list(allow_nodes, NAME_RE, "节点")
    deny = _clean_list(deny_nodes, NAME_RE, "节点")
    if known_nodes is not None:
        unknown = sorted(set(allow + deny) - set(known_nodes))
        _require(not unknown, f"没有这些节点：{', '.join(unknown)}")
    _require(not (set(allow) & set(deny)), "同一节点不能既单独允许又单独禁止")
    expires_on = (expires_on or "").strip() or None
    if expires_on:
        _require(DATE_RE.match(expires_on), "到期日格式应为 YYYY-MM-DD")
        try:
            datetime.date.fromisoformat(expires_on)
        except ValueError:
            raise UserError("到期日不是有效日期") from None
    note = " ".join((note or "").split())
    _require(len(note) <= NOTE_MAX, f"备注最多 {NOTE_MAX} 个字")
    return {"tiers": tiers, "allow_nodes": allow, "deny_nodes": deny, "expires_on": expires_on, "note": note}


def create(conn, name, fields):
    _require(isinstance(name, str) and NAME_RE.match(name), "用户名只能用字母、数字、下划线和连字符，最多 64 个")
    _require(get(conn, name) is None, f"用户 {name} 已存在")
    now = db.now()
    conn.execute(
        "INSERT INTO users (name, uuid, short_id, tiers, allow_nodes, deny_nodes, expires_on, status, note, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (name, str(uuidlib.uuid4()), shared_short_id(conn), json.dumps(fields["tiers"]),
         json.dumps(fields["allow_nodes"]), json.dumps(fields["deny_nodes"]), fields["expires_on"], fields["note"],
         now, now))
    return get(conn, name)


def update(conn, name, fields):
    _require(get(conn, name) is not None, f"没有用户 {name}")
    conn.execute(
        "UPDATE users SET tiers = ?, allow_nodes = ?, deny_nodes = ?, expires_on = ?, note = ?, updated_at = ? "
        "WHERE name = ?",
        (json.dumps(fields["tiers"]), json.dumps(fields["allow_nodes"]), json.dumps(fields["deny_nodes"]),
         fields["expires_on"], fields["note"], db.now(), name))
    return get(conn, name)


def set_status(conn, name, status):
    _require(status in STATUSES, "状态不合法")
    _require(get(conn, name) is not None, f"没有用户 {name}")
    conn.execute("UPDATE users SET status = ?, updated_at = ? WHERE name = ?", (status, db.now(), name))


def delete(conn, name):
    """Remove the user and their subscription token; traffic and audit history stay."""
    _require(get(conn, name) is not None, f"没有用户 {name}")
    conn.execute("DELETE FROM users WHERE name = ?", (name,))
    conn.execute("DELETE FROM tokens WHERE user = ?", (name,))


def set_node_tiers(conn, node, tiers):
    tiers = _clean_list(tiers, TIER_RE, "档位")
    conn.execute("INSERT INTO node_tiers (node, tiers, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(node) DO UPDATE SET tiers = excluded.tiers, updated_at = excluded.updated_at",
                 (node, json.dumps(tiers), db.now()))
    return tiers


# --------------------------------------------------------------------------
# One-time import from the repository's user files (plan §3.2)
# --------------------------------------------------------------------------

def import_doc(conn, doc):
    """Import {users, node_tiers, rules} into an empty console. Returns the number of users imported.

    users: [{name, uuid, short_id, groups?, hosts?, deny_hosts?}] as in users/*.yml (a missing `groups` is `all`,
    as in the existing ACL); node_tiers: {node: [inventory groups]}; rules: the acl_matrix.
    """
    _require(not imported(conn), "控制台已有用户，不再导入")
    _require(isinstance(doc, dict), "导入文件格式不对")
    rules_in = doc.get("rules") or {}
    _require(isinstance(rules_in, dict) and rules_in, "缺少档位规则（acl_matrix）")
    tiers_in = doc.get("node_tiers") or {}
    _require(isinstance(tiers_in, dict), "node_tiers 格式不对")
    seen_uuids = set()
    rows = []
    for item in doc.get("users") or []:
        _require(isinstance(item, dict), "用户条目格式不对")
        name = item.get("name")
        _require(isinstance(name, str) and NAME_RE.match(name), f"用户名不合法：{name!r}")
        _require(isinstance(item.get("uuid"), str) and UUID_RE.match(item["uuid"]), f"{name} 的 UUID 不合法")
        _require(item["uuid"].lower() not in seen_uuids, f"{name} 与其他用户共用 UUID")
        seen_uuids.add(item["uuid"].lower())
        short_id = str(item.get("short_id", ""))
        _require(SHORT_ID_RE.match(short_id), f"{name} 的 short_id 不合法")
        groups = item.get("groups")
        fields = clean_fields(groups if groups else [ALL], item.get("hosts"), item.get("deny_hosts"), None, "")
        rows.append((name, item["uuid"], short_id, fields))
    _require(len({r[0] for r in rows}) == len(rows), "用户名重复")
    now = db.now()
    with db.transaction(conn):
        for tier, accepts in rules_in.items():
            conn.execute("INSERT INTO tier_rules (tier, accepts) VALUES (?, ?)",
                         (tier, json.dumps(_clean_list(accepts, TIER_RE, "档位"))))
        for node, tiers in tiers_in.items():
            _require(NAME_RE.match(node), f"节点名不合法：{node!r}")
            conn.execute("INSERT INTO node_tiers (node, tiers, updated_at) VALUES (?, ?, ?)",
                         (node, json.dumps(_clean_list(tiers, TIER_RE, "档位")), now))
        for name, uuid_value, short_id, fields in rows:
            conn.execute(
                "INSERT INTO users (name, uuid, short_id, tiers, allow_nodes, deny_nodes, expires_on, status, note, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, NULL, 'active', '', ?, ?)",
                (name, uuid_value, short_id, json.dumps(fields["tiers"]), json.dumps(fields["allow_nodes"]),
                 json.dumps(fields["deny_nodes"]), now, now))
    return len(rows)
