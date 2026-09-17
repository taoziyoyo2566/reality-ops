"""Read-side data for the admin pages: users, nodes, traffic and subscription fetches."""
import datetime
import json
import os
import shutil
import sqlite3
import tempfile

from . import db


def load_users(path):
    """{name: {"groups", "hosts", "deny_hosts"}} exported by console.yml; missing file -> {}."""
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    users = {}
    for item in doc.get("users", []) if isinstance(doc, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            users[item["name"]] = {k: list(item.get(k) or []) for k in ("groups", "hosts", "deny_hosts")}
    return users


def last_fetches(path, days=90):
    """{user: {"at": epoch, "fmt": str, "count": int}} from the subscription service's access log (read only).

    The log is a WAL database owned by the subscription service; if it cannot be opened read-only in place,
    a private copy is read instead.
    """
    query = ("SELECT user, fmt, MAX(ts) AS at, COUNT(*) AS n FROM hits WHERE ts >= ? GROUP BY user, fmt")
    since = db.now() - days * 86400

    def read(uri):
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            return conn.execute(query, (since,)).fetchall()
        finally:
            conn.close()

    try:
        rows = read(f"file:{path}?mode=ro")
    except sqlite3.Error:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for suffix in ("", "-wal", "-shm"):
                    if os.path.exists(path + suffix):
                        shutil.copyfile(path + suffix, os.path.join(tmp, "access.sqlite" + suffix))
                copy = os.path.join(tmp, "access.sqlite")
                rows = read(f"file:{copy}?mode=ro")
        except (OSError, sqlite3.Error):
            return {}
    result = {}
    for user, fmt, at, n in rows:
        entry = result.setdefault(user, {"at": 0, "fmt": "", "count": 0})
        entry["count"] += n
        if at > entry["at"]:
            entry["at"], entry["fmt"] = at, fmt
    return result


def node_status(conn):
    return {r["node"]: {"received_at": r["received_at"], "period_to": r["period_to"],
                        "report": json.loads(r["report"]), "xray_restart_at": r["xray_restart_at"]}
            for r in conn.execute("SELECT * FROM node_status")}


def today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def this_month():
    return today()[:7]


def traffic_by_user(conn, prefix):
    """{user: {"up", "down"}} for days starting with prefix (YYYY-MM or YYYY-MM-DD)."""
    return {r["user"]: {"up": r["up"], "down": r["down"]} for r in conn.execute(
        "SELECT user, SUM(up) AS up, SUM(down) AS down FROM traffic_daily WHERE day LIKE ? GROUP BY user",
        (prefix + "%",))}


def traffic_by_node(conn, prefix):
    return {r["node"]: {"up": r["up"], "down": r["down"]} for r in conn.execute(
        "SELECT node, SUM(up) AS up, SUM(down) AS down FROM traffic_daily WHERE day LIKE ? GROUP BY node",
        (prefix + "%",))}


def traffic_matrix(conn, prefix):
    """[(user, node, up, down)] for the period, largest first."""
    return [tuple(r) for r in conn.execute(
        "SELECT user, node, SUM(up) AS up, SUM(down) AS down FROM traffic_daily WHERE day LIKE ? "
        "GROUP BY user, node ORDER BY SUM(up) + SUM(down) DESC", (prefix + "%",))]


def daily_totals(conn, days=31, user=None, node=None):
    """[(day, up, down)] for the last `days` days, oldest first, optionally for one user or node."""
    start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    sql = "SELECT day, SUM(up), SUM(down) FROM traffic_daily WHERE day >= ?"
    args = [start]
    if user:
        sql += " AND user = ?"
        args.append(user)
    if node:
        sql += " AND node = ?"
        args.append(node)
    return [tuple(r) for r in conn.execute(sql + " GROUP BY day ORDER BY day", args)]


def months(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT substr(day, 1, 7) FROM traffic_daily ORDER BY 1 DESC")]


def audit_entries(conn, limit=200):
    return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]


def publish_entries(conn, limit=50):
    return [dict(r) for r in conn.execute("SELECT * FROM publish_log ORDER BY id DESC LIMIT ?", (limit,))]


def alerts(settings, conn, nodes, problems, users):
    """Home page warnings, most important first."""
    out = []
    last = db.last_publish(conn)
    if last and not last["ok"]:
        out.append(f"最近一次订阅发布失败：{last['detail']}")
    for p in problems:
        out.append(f"注册文件无法读取：{p}")
    status = node_status(conn)
    now = db.now()
    for name, node in sorted(nodes.items()):
        if not node.report_enabled:
            continue
        seen = status.get(name, {}).get("received_at")
        if seen is None:
            out.append(f"节点 {name} 已开启上报，但还没有收到上报")
        elif now - seen > settings.offline_minutes * 60:
            out.append(f"节点 {name} 已 {(now - seen) // 60} 分钟没有上报")
        elif not status[name]["report"].get("listening"):
            out.append(f"节点 {name} 上报 443 未在监听")
    shown = db.shown_nodes(conn)
    for name in sorted(shown - set(nodes)):
        out.append(f"节点 {name} 设为显示，但没有注册文件（发布会被拒绝）")
    for user in sorted(set(db.tokens(conn)) - set(users)):
        out.append(f"用户 {user} 已发放订阅，但用户档案中已不存在")
    return out
