"""Console database (SQLite, WAL). The web and report services open it from separate processes."""
import contextlib
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    user TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, issued_at INTEGER NOT NULL, rotated_at INTEGER);
CREATE TABLE IF NOT EXISTS node_display (
    node TEXT PRIMARY KEY, shown INTEGER NOT NULL, changed_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS node_status (
    node TEXT PRIMARY KEY, received_at INTEGER NOT NULL, period_to TEXT NOT NULL, report TEXT NOT NULL,
    xray_restart_at INTEGER);
CREATE TABLE IF NOT EXISTS report_seq (
    node TEXT NOT NULL, instance TEXT NOT NULL, seq INTEGER NOT NULL, received_at INTEGER NOT NULL,
    PRIMARY KEY (node, instance, seq));
CREATE TABLE IF NOT EXISTS traffic_daily (
    day TEXT NOT NULL, node TEXT NOT NULL, user TEXT NOT NULL,
    up INTEGER NOT NULL DEFAULT 0, down INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, node, user));
CREATE TABLE IF NOT EXISTS publish_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, ok INTEGER NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '');
"""
REPORT_SEQ_DAYS = 30


def now():
    return int(time.time())


@contextlib.contextmanager
def connect(path):
    """A short-lived autocommit connection; group statements with transaction()."""
    os.umask(0o077)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init(path):
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def audit(conn, action, target, detail=""):
    conn.execute("INSERT INTO audit_log (at, action, target, detail) VALUES (?, ?, ?, ?)",
                 (now(), action, target, detail))


def tokens(conn):
    return {r["user"]: r["token"] for r in conn.execute("SELECT user, token FROM tokens")}


def token_rows(conn):
    return {r["user"]: dict(r) for r in conn.execute("SELECT * FROM tokens")}


def shown_nodes(conn):
    return {r["node"] for r in conn.execute("SELECT node FROM node_display WHERE shown = 1")}


def set_shown(conn, node, shown):
    conn.execute("INSERT INTO node_display (node, shown, changed_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(node) DO UPDATE SET shown = excluded.shown, changed_at = excluded.changed_at",
                 (node, 1 if shown else 0, now()))


def last_publish(conn):
    row = conn.execute("SELECT * FROM publish_log ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def purge(conn, days=REPORT_SEQ_DAYS):
    conn.execute("DELETE FROM report_seq WHERE received_at < ?", (now() - days * 86400,))


def backup(path, backup_dir, keep, stamp):
    """Copy the database with SQLite's online backup; keep the newest `keep` copies. Returns the new file."""
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(backup_dir, f"console-{stamp}.sqlite")
    if os.path.exists(target):
        return None
    with connect(path) as src:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    copies = sorted(n for n in os.listdir(backup_dir) if n.startswith("console-") and n.endswith(".sqlite"))
    for name in copies[:-keep] if keep > 0 else []:
        os.remove(os.path.join(backup_dir, name))
    return target
