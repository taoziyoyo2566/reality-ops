"""Node reports: authentication, validation, de-duplication and storage (plan-console-phase1 §3.4)."""
import datetime
import hashlib
import hmac
import json
import re

from . import db

SCHEMA = 1
MAX_BODY = 256 * 1024
MAX_COUNTER = 10 ** 15
MAX_TAIL_LINES = 20
MAX_TAIL_CHARS = 1000
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
INSTANCE_RE = re.compile(r"^[0-9a-f]{16}$")
USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


XRAY_KEYS = {"alloc", "sys", "goroutines", "uptime"}   # the agent's `xray api statssys` summary


class ReportError(ValueError):
    pass


def _require(cond, msg):
    if not cond:
        raise ReportError(msg)


def node_for_token(nodes, authorization):
    """The registered node whose report token matches the bearer token, or None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    digest = hashlib.sha256(authorization[7:].strip().encode("utf-8", "replace")).hexdigest()
    match = None
    for node in nodes.values():
        # compare against every node so the time taken does not depend on which one matches
        if node.report_enabled and node.report_digest and hmac.compare_digest(node.report_digest, digest):
            match = node
    return match


def validate(doc, node):
    _require(isinstance(doc, dict) and doc.get("schema") == SCHEMA, "unsupported schema")
    _require(doc.get("node") == node.name, "report is for another node")
    _require(isinstance(doc.get("instance"), str) and INSTANCE_RE.match(doc["instance"]), "invalid instance")
    _require(isinstance(doc.get("seq"), int) and doc["seq"] > 0, "invalid seq")
    period = doc.get("period") or {}
    _require(isinstance(period.get("to"), str) and ISO_RE.match(period["to"]), "invalid period.to")
    _require(period.get("from") is None or (isinstance(period["from"], str) and ISO_RE.match(period["from"])),
             "invalid period.from")
    traffic = doc.get("traffic")
    _require(isinstance(traffic, dict), "traffic must be an object")
    for user, inc in traffic.items():
        _require(isinstance(user, str) and USER_RE.match(user), "invalid user in traffic")
        _require(isinstance(inc, dict) and all(isinstance(inc.get(k), int) and 0 <= inc[k] < MAX_COUNTER
                                               for k in ("up", "down")), f"invalid traffic for {user}")
    for key in ("listening", "xray_restarted", "baseline"):
        _require(isinstance(doc.get(key), bool), f"{key} must be a boolean")
    tail = doc.get("error_tail", [])
    _require(isinstance(tail, list) and all(isinstance(l, str) for l in tail), "error_tail must be a list of strings")
    _require(isinstance(doc.get("dropped_reports", 0), int), "dropped_reports must be an integer")
    runtime = doc.get("xray")
    _require(runtime is None or (isinstance(runtime, dict) and set(runtime) == XRAY_KEYS
                                 and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v < MAX_COUNTER
                                         for v in runtime.values())), "invalid xray runtime")
    return doc


def store(conn, node, doc, received_at=None, users=None):
    """Store one validated report; returns False when it was already stored.

    Sequence numbers are per reporter instance: a reporter that lost its spool starts a new instance at 1.
    Traffic counts for `users` (default: the node's registered users); users a node no longer carries are left out.
    """
    users = set(node.users) if users is None else set(users)
    received_at = received_at or db.now()
    with db.transaction(conn):
        cur = conn.execute("INSERT OR IGNORE INTO report_seq (node, instance, seq, received_at) VALUES (?, ?, ?, ?)",
                           (node.name, doc["instance"], doc["seq"], received_at))
        if cur.rowcount == 0:
            return False
        day = doc["period"]["to"][:10]
        for user, inc in doc["traffic"].items():
            if user not in users:
                continue  # counters for users this node no longer carries are not attributed
            conn.execute(
                "INSERT INTO traffic_daily (day, node, user, up, down) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(day, node, user) DO UPDATE SET up = up + excluded.up, down = down + excluded.down",
                (day, node.name, user, inc["up"], inc["down"]))
        snapshot = {
            "period": doc["period"], "listening": doc["listening"], "xray_restarted": doc["xray_restarted"],
            "dropped_reports": doc.get("dropped_reports", 0),
            "error_tail": [l[:MAX_TAIL_CHARS] for l in doc.get("error_tail", [])][-MAX_TAIL_LINES:],
            "users_reporting": sorted(doc["traffic"]),
            "xray": doc.get("xray"),
        }
        restart_at = _epoch(doc["period"]["to"]) if doc["xray_restarted"] else None
        # A retried older report still counts its traffic and restart, but does not replace a newer status.
        conn.execute(
            "INSERT INTO node_status (node, received_at, period_to, report, xray_restart_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(node) DO UPDATE SET received_at = excluded.received_at, period_to = excluded.period_to, "
            "report = excluded.report WHERE excluded.period_to >= node_status.period_to",
            (node.name, received_at, doc["period"]["to"], json.dumps(snapshot), restart_at))
        if restart_at:
            conn.execute("UPDATE node_status SET xray_restart_at = MAX(COALESCE(xray_restart_at, 0), ?) WHERE node = ?",
                         (restart_at, node.name))
    return True


def _epoch(stamp):
    return int(datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=datetime.timezone.utc).timestamp())
