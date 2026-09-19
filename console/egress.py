"""Proxy egress managed in the console (plan-egress-console): an egress pool, assignments to nodes, and what each
node's agent applies at runtime.

Nothing here knows a particular proxy. Proxies and assignments are rows in the console database; this module only
knows *types* (how a kind of proxy becomes an Xray outbound) and *conditions* (which traffic an assignment takes),
both held in registries. A new proxy type is one entry in TYPES; a new condition is one entry in CONDITIONS.

An assignment puts one egress on one node for the traffic its conditions match (all of them must hold, as in an Xray
rule; no condition means all traffic of the node). When the egress fails, the agent either falls back to direct,
which is the same as not having the assignment, or blocks that traffic (`on_failure`).
"""
import dataclasses
import hashlib
import ipaddress
import json
import re
import urllib.parse

from . import db, egress_probe

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$|^\[?[0-9A-Fa-f:]{2,39}\]?$")
TAG_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
DOMAIN_RE = re.compile(r"^(?:(?:domain|full|keyword|geosite):)?[A-Za-z0-9*._-]{1,253}$")
ON_FAILURE = {"direct": "回退直连", "block": "断开"}
NETWORKS = {"tcp": "只有 TCP", "tcp,udp": "TCP 和 UDP"}
TAG_PREFIX = "egress-"
MAX_VALUES = 500


class EgressError(ValueError):
    """A request the console refuses; the message is shown to the administrator."""


def _require(cond, message):
    if not cond:
        raise EgressError(message)


# --------------------------------------------------------------------------
# Types
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "text"          # text, int or secret; a secret is never shown back
    required: bool = True
    placeholder: str = ""


@dataclasses.dataclass(frozen=True)
class EgressType:
    key: str
    label: str
    fields: tuple
    clean: object                # (values) -> cleaned values; raises EgressError
    outbound: object             # (tag, values) -> Xray outbound
    parse_link: object           # (link) -> (name, values) or None
    summary: object              # (values) -> text without secrets

    def secret_fields(self):
        return {f.name for f in self.fields if f.kind == "secret"}


def _socks5_clean(values):
    host = str(values.get("host", "")).strip()
    _require(HOST_RE.match(host), "地址应为域名或 IP")
    try:
        port = int(values.get("port"))
    except (TypeError, ValueError):
        raise EgressError("端口应为 1–65535 的数字") from None
    _require(1 <= port <= 65535, "端口应为 1–65535 的数字")
    username = str(values.get("username") or "")
    password = str(values.get("password") or "")
    _require(len(username) <= 255 and len(password) <= 255, "账号或密码过长")
    _require(bool(username) == bool(password), "账号和密码要么都填，要么都不填")
    return {"host": host.strip("[]"), "port": port, "username": username, "password": password}


def _socks5_outbound(tag, values):
    settings = {"address": values["host"], "port": values["port"]}
    if values.get("username"):
        settings.update(user=values["username"], **{"pass": values["password"]})
    return {"tag": tag, "protocol": "socks", "settings": settings}


def _socks5_link(link):
    parts = urllib.parse.urlsplit(link.strip())
    if parts.scheme.lower() not in ("socks5", "socks5h", "socks"):
        return None
    try:
        port = parts.port
    except ValueError:
        raise EgressError("端口应为 1–65535 的数字") from None
    values = {"host": parts.hostname or "", "port": port,
              "username": urllib.parse.unquote(parts.username or ""), "password": urllib.parse.unquote(parts.password or "")}
    return urllib.parse.unquote(parts.fragment or ""), _socks5_clean(values)


TYPES = {
    "socks5": EgressType(
        key="socks5", label="SOCKS5",
        fields=(Field("host", "地址", placeholder="例如 203.0.113.10 或 proxy.example.com"),
                Field("port", "端口", "int", placeholder="1080"),
                Field("username", "账号", required=False),
                Field("password", "密码", "secret", required=False)),
        clean=_socks5_clean, outbound=_socks5_outbound, parse_link=_socks5_link,
        summary=lambda v: f"{v['host']}:{v['port']}" + ("（有账号）" if v.get("username") else "")),
}


# --------------------------------------------------------------------------
# Conditions: each becomes one field of the assignment's Xray rule
# --------------------------------------------------------------------------

def _clean_domains(values):
    out = []
    for value in values:
        _require(DOMAIN_RE.match(value), f"网站“{value}”不合法（可用 domain:、full:、keyword:、geosite: 写法）")
        out.append(value)
    return out


def _clean_ips(values):
    out = []
    for value in values:
        if value.startswith("geoip:"):
            _require(re.match(r"^geoip:!?[A-Za-z0-9_-]{1,32}$", value), f"IP 段“{value}”不合法")
        else:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError:
                raise EgressError(f"IP 段“{value}”不合法（用 CIDR 或 geoip: 写法）") from None
        out.append(value)
    return out


@dataclasses.dataclass(frozen=True)
class Condition:
    key: str
    label: str
    rule_field: str
    clean: object                # (values, node users) -> cleaned values
    to_rule: object              # (values, node) -> rule values


CONDITIONS = {
    "users": Condition("users", "用户", "user",
                       clean=lambda values, known: sorted(set(values)),
                       to_rule=lambda values, node: [f"{u}.{node}" for u in values]),
    "domains": Condition("domains", "网站", "domain", clean=lambda values, known: _clean_domains(values),
                         to_rule=lambda values, node: list(values)),
    "ips": Condition("ips", "IP 段", "ip", clean=lambda values, known: _clean_ips(values),
                     to_rule=lambda values, node: list(values)),
}


def split_values(text):
    """Values from a form field: one per line, or separated by commas or spaces."""
    return [v for v in re.split(r"[\s,，]+", text or "") if v]


# --------------------------------------------------------------------------
# The pool
# --------------------------------------------------------------------------

def _decode(row):
    item = dict(row)
    item["config"] = json.loads(item["config"])
    item["labels"] = json.loads(item["labels"])
    return item


def pool(conn):
    """{id: egress} ordered by name."""
    return {r["id"]: _decode(r) for r in conn.execute("SELECT * FROM egress ORDER BY name")}


def get(conn, egress_id):
    row = conn.execute("SELECT * FROM egress WHERE id = ?", (egress_id,)).fetchone()
    return _decode(row) if row else None


def clean_labels(text):
    labels = sorted(set(split_values(text)))
    _require(all(TAG_RE.match(t) for t in labels), "标签只能用字母、数字、下划线和连字符，最多 24 个")
    return labels


def save(conn, egress_id, name, type_key, values, labels, note, enabled=True):
    """Create (egress_id None) or update an egress; an empty secret field keeps the stored secret."""
    _require(type_key in TYPES, "没有这种出口类型")
    _require(isinstance(name, str) and NAME_RE.match(name), "名称只能用字母、数字、点、下划线和连字符，最多 48 个")
    kind = TYPES[type_key]
    current = get(conn, egress_id) if egress_id else None
    _require(egress_id is None or current is not None, "没有这个出口")
    if current:
        _require(current["type"] == type_key, "不能修改出口类型")
        for secret in kind.secret_fields():
            if not values.get(secret):
                values = dict(values, **{secret: current["config"].get(secret, "")})
    config = kind.clean(values)
    note = " ".join((note or "").split())
    _require(len(note) <= 200, "备注最多 200 个字")
    other = conn.execute("SELECT id FROM egress WHERE name = ?", (name,)).fetchone()
    _require(other is None or other["id"] == egress_id, f"已有名为 {name} 的出口")
    now = db.now()
    if current:
        conn.execute("UPDATE egress SET name = ?, config = ?, labels = ?, note = ?, enabled = ?, updated_at = ? WHERE id = ?",
                     (name, json.dumps(config), json.dumps(labels), note, int(bool(enabled)), now, egress_id))
        return egress_id
    cur = conn.execute("INSERT INTO egress (name, type, config, labels, note, enabled, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (name, type_key, json.dumps(config), json.dumps(labels), note, int(bool(enabled)), now, now))
    return cur.lastrowid


def import_links(conn, text, labels):
    """Add one egress per line (`socks5://user:pass@host:port#name`); returns (added names, [(line, error)])."""
    added, failed = [], []
    for number, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = None
        try:
            for key, kind in TYPES.items():
                parsed = kind.parse_link(line)
                if parsed:
                    name, values = parsed
                    name = re.sub(r"[^A-Za-z0-9_.-]", "-", name or f"{key}-{values['host']}-{values['port']}")[:48]
                    save(conn, None, name, key, values, labels, "")
                    added.append(name)
                    break
            if not parsed:
                failed.append((number, "无法识别的链接"))
        except EgressError as exc:
            failed.append((number, str(exc)))
    return added, failed


def delete(conn, egress_id):
    _require(get(conn, egress_id) is not None, "没有这个出口")
    conn.execute("DELETE FROM egress_assignment WHERE egress_id = ?", (egress_id,))
    conn.execute("DELETE FROM egress_check WHERE egress_id = ?", (egress_id,))
    conn.execute("DELETE FROM egress WHERE id = ?", (egress_id,))


def set_enabled(conn, egress_id, enabled):
    _require(get(conn, egress_id) is not None, "没有这个出口")
    conn.execute("UPDATE egress SET enabled = ?, updated_at = ? WHERE id = ?", (int(bool(enabled)), db.now(), egress_id))


# --------------------------------------------------------------------------
# Assignments
# --------------------------------------------------------------------------

def assignments(conn, node=None):
    sql, args = "SELECT * FROM egress_assignment", ()
    if node:
        sql, args = sql + " WHERE node = ?", (node,)
    out = []
    for r in conn.execute(sql + " ORDER BY node, priority, id", args):
        item = dict(r)
        item["conditions"] = json.loads(item["conditions"])
        out.append(item)
    return out


def clean_conditions(raw, node_users):
    """{kind: [values]} from the form; unknown users of the node are refused, empty kinds dropped."""
    out = {}
    for key, values in raw.items():
        _require(key in CONDITIONS, "没有这种分流条件")
        values = [str(v).strip() for v in values if str(v).strip()]
        _require(len(values) <= MAX_VALUES, f"{CONDITIONS[key].label}最多 {MAX_VALUES} 项")
        if values:
            out[key] = CONDITIONS[key].clean(values, node_users)
    unknown = sorted(set(out.get("users", [])) - set(node_users))
    _require(not unknown, f"这些用户不在这台节点上：{', '.join(unknown)}")
    return out


def assign(conn, assignment_id, egress_id, node, conditions, on_failure, network, priority, enabled=True):
    """Create (assignment_id None) or update an assignment."""
    _require(get(conn, egress_id) is not None, "没有这个出口")
    _require(on_failure in ON_FAILURE and network in NETWORKS, "失效行为或网络类型不合法")
    try:
        priority = int(priority)
    except (TypeError, ValueError):
        raise EgressError("优先级应为数字") from None
    _require(0 <= priority <= 1000, "优先级应在 0–1000")
    now = db.now()
    if assignment_id:
        cur = conn.execute("UPDATE egress_assignment SET egress_id = ?, conditions = ?, on_failure = ?, network = ?, "
                           "priority = ?, enabled = ?, updated_at = ? WHERE id = ? AND node = ?",
                           (egress_id, json.dumps(conditions), on_failure, network, priority, int(bool(enabled)), now,
                            assignment_id, node))
        _require(cur.rowcount == 1, "没有这条分配")
        return assignment_id
    cur = conn.execute("INSERT INTO egress_assignment (egress_id, node, conditions, on_failure, network, priority, enabled, "
                       "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (egress_id, node, json.dumps(conditions), on_failure, network, priority, int(bool(enabled)), now, now))
    return cur.lastrowid


def toggle_assignment(conn, assignment_id, node):
    """Flip an assignment between enabled and disabled; returns the new state."""
    row = conn.execute("SELECT enabled FROM egress_assignment WHERE id = ? AND node = ?", (assignment_id, node)).fetchone()
    _require(row is not None, "没有这条分配")
    conn.execute("UPDATE egress_assignment SET enabled = ?, updated_at = ? WHERE id = ?",
                 (0 if row["enabled"] else 1, db.now(), assignment_id))
    return not row["enabled"]


def unassign(conn, assignment_id, node):
    cur = conn.execute("DELETE FROM egress_assignment WHERE id = ? AND node = ?", (assignment_id, node))
    _require(cur.rowcount == 1, "没有这条分配")


def describe_conditions(conditions):
    if not conditions:
        return "整台节点的全部流量"
    return "；".join(f"{CONDITIONS[k].label} {'、'.join(v)}" for k, v in conditions.items() if k in CONDITIONS)


# --------------------------------------------------------------------------
# What a node runs
# --------------------------------------------------------------------------

def outbound_tag(egress_id):
    return f"{TAG_PREFIX}{egress_id}"


def node_payload(conn, node, node_users):
    """(version, payload) for one node: its enabled assignments of enabled egress, in priority order.

    payload = {"outbounds": [Xray outbound], "assignments": [{"id", "outbound", "rule", "on_failure"}]}; `rule` holds
    the Xray rule fields without ruleTag and outboundTag, which the agent (or the applier) sets. An assignment whose
    users all left the node is dropped: without its user field the rule would take the whole node.
    """
    items = pool(conn)
    outbounds, entries = {}, []
    for a in assignments(conn, node):
        egress = items.get(a["egress_id"])
        if not a["enabled"] or not egress or not egress["enabled"] or egress["type"] not in TYPES:
            continue
        conditions = dict(a["conditions"])
        if "users" in conditions:
            conditions["users"] = [u for u in conditions["users"] if u in node_users]
            if not conditions["users"]:
                continue
        rule = {"network": a["network"]}
        for key, values in conditions.items():
            if key in CONDITIONS:
                rule[CONDITIONS[key].rule_field] = CONDITIONS[key].to_rule(values, node)
        tag = outbound_tag(egress["id"])
        outbounds[tag] = TYPES[egress["type"]].outbound(tag, egress["config"])
        entries.append({"id": a["id"], "outbound": tag, "rule": rule, "on_failure": a["on_failure"]})
    payload = {"outbounds": [outbounds[t] for t in sorted(outbounds)], "assignments": entries}
    version = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    return version, payload


def deploy_rules(payload):
    """The rules written into the node's configuration at deployment: every assignment through its egress."""
    return [dict(a["rule"], ruleTag=f"{a['outbound']}-a{a['id']}", outboundTag=a["outbound"]) for a in payload["assignments"]]


# --------------------------------------------------------------------------
# Checks and what the agents report
# --------------------------------------------------------------------------

CHECK_KEYS = ("ok", "latency_ms", "exit_ip", "country", "error")
STATES = {"active": "经出口", "direct": "出口失效，已回退直连", "blocked": "出口失效，已断开", "pending": "同步中"}


def record_check(conn, egress_id, checker, result, at=None):
    conn.execute("INSERT OR REPLACE INTO egress_check (egress_id, checker, ok, latency_ms, exit_ip, country, error, checked_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (egress_id, checker, int(bool(result.get("ok"))), result.get("latency_ms"), str(result.get("exit_ip") or "")[:64],
                  str(result.get("country") or "")[:8], str(result.get("error") or "")[:200], at or db.now()))


def checks(conn):
    """{egress_id: [check rows, newest first]}."""
    out = {}
    for r in conn.execute("SELECT * FROM egress_check ORDER BY checked_at DESC"):
        out.setdefault(r["egress_id"], []).append(dict(r))
    return out


def record_node_state(conn, node, applied, states):
    conn.execute("INSERT OR REPLACE INTO egress_node (node, applied, states, checked_at) VALUES (?, ?, ?, ?)",
                 (node, applied, json.dumps(states), db.now()))


def node_states(conn):
    return {r["node"]: {"applied": r["applied"], "states": json.loads(r["states"]), "checked_at": r["checked_at"]}
            for r in conn.execute("SELECT * FROM egress_node")}


def validate_report(doc):
    """The agent's egress fields: ({egress_id: check}, applied version, {assignment_id: state}); raises ValueError."""
    raw_checks = doc.get("egress_checks") or {}
    raw_states = doc.get("egress_states") or {}
    applied = doc.get("egress_applied")
    if not (isinstance(raw_checks, dict) and isinstance(raw_states, dict) and len(raw_checks) <= 1000 and len(raw_states) <= 1000):
        raise ValueError("invalid egress report")
    if applied is not None and not (isinstance(applied, str) and re.match(r"^[0-9a-f]{16}$", applied)):
        raise ValueError("invalid egress_applied")
    found = {}
    for key, check in raw_checks.items():
        if not (str(key).isdigit() and isinstance(check, dict) and isinstance(check.get("ok"), bool)):
            raise ValueError("invalid egress check")
        if check.get("latency_ms") is not None and not isinstance(check["latency_ms"], int):
            raise ValueError("invalid egress check")
        found[int(key)] = {k: check.get(k) for k in CHECK_KEYS}
    states = {}
    for key, state in raw_states.items():
        if not (str(key).isdigit() and state in STATES):
            raise ValueError("invalid egress state")
        states[int(key)] = state
    return found, applied, states


def alerts(conn, now, registered):
    """Home page warnings: an egress a node uses that failed its last check there."""
    items = pool(conn)
    found = checks(conn)
    used = {(a["egress_id"], a["node"]): a for a in assignments(conn) if a["enabled"] and a["node"] in registered}
    out = []
    for (egress_id, node), a in sorted(used.items()):
        row = next((c for c in found.get(egress_id, []) if c["checker"] == node), None)
        if row and not row["ok"] and egress_id in items:
            what = "已回退直连" if a["on_failure"] == "direct" else "已断开走它的流量"
            out.append(f"出口 {items[egress_id]['name']} 在节点 {node} 上不可用（{row['error'] or '检测失败'}），{what}")
    return out


# --------------------------------------------------------------------------
# Checks from this host (spt) for egress no node uses; nodes check their own (docker/edge-tools/edge_egress.py)
# --------------------------------------------------------------------------

SPT_CHECK_SECONDS = 600
USER_AGENT = "reality-console-egress/1"


def check_one(xray, url, item):
    """Check one egress from this host now (the admin's button); the caller records the result as checker "spt".

    One attempt, so a dead egress answers within about 10 s; the admin can simply press again.
    """
    tag = outbound_tag(item["id"])
    return egress_probe.probe(xray, [TYPES[item["type"]].outbound(tag, item["config"])], url, USER_AGENT,
                              attempts=1)[tag]


def check_unassigned(conn, xray, url, registered, new_only=False):
    """Check the enabled egress no enabled assignment on a registered node uses; recorded with checker "spt".

    new_only: just those added or changed since their last check, so a new egress shows a result within a round.
    """
    used = {a["egress_id"] for a in assignments(conn) if a["enabled"] and a["node"] in registered}
    items = {i: e for i, e in pool(conn).items() if e["enabled"] and i not in used and e["type"] in TYPES}
    if new_only:
        latest = checks(conn)
        items = {i: e for i, e in items.items() if not latest.get(i) or latest[i][0]["checked_at"] <= e["updated_at"]}
    outbounds = [TYPES[e["type"]].outbound(outbound_tag(i), e["config"]) for i, e in items.items()]
    results = egress_probe.probe(xray, outbounds, url, USER_AGENT)
    for egress_id in items:
        if outbound_tag(egress_id) in results:
            record_check(conn, egress_id, "spt", results[outbound_tag(egress_id)])
    return len(results)

