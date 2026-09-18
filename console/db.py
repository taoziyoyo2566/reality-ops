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
-- Node status page (plan-node-status-page §3.5). ok is NULL when the round could not tell (no data).
CREATE TABLE IF NOT EXISTS probe_result (
    at INTEGER NOT NULL, node TEXT NOT NULL, transport TEXT NOT NULL, ok INTEGER, latency_ms INTEGER,
    error TEXT NOT NULL DEFAULT '', PRIMARY KEY (node, transport, at));
CREATE TABLE IF NOT EXISTS probe_state (
    node TEXT NOT NULL, transport TEXT NOT NULL, fails INTEGER NOT NULL DEFAULT 0, oks INTEGER NOT NULL DEFAULT 0,
    first_fail_at INTEGER, first_ok_at INTEGER, down_since INTEGER, last_known_at INTEGER,
    last_at INTEGER, last_ok INTEGER, last_latency_ms INTEGER, last_error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (node, transport));
CREATE TABLE IF NOT EXISTS incident (
    id INTEGER PRIMARY KEY AUTOINCREMENT, node TEXT NOT NULL, kind TEXT NOT NULL, started_at INTEGER NOT NULL,
    ended_at INTEGER, transports TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS incident_node ON incident (node, started_at);
CREATE TABLE IF NOT EXISTS status_daily (
    node TEXT NOT NULL, day TEXT NOT NULL, known_seconds INTEGER NOT NULL DEFAULT 0,
    unknown_seconds INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (node, day));
CREATE TABLE IF NOT EXISTS status_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- Users and tiers (plan-console-phase2 §3.1). List fields are JSON arrays; expires_on is YYYY-MM-DD.
CREATE TABLE IF NOT EXISTS users (
    name TEXT PRIMARY KEY, uuid TEXT NOT NULL UNIQUE, short_id TEXT NOT NULL,
    tiers TEXT NOT NULL DEFAULT '["all"]', allow_nodes TEXT NOT NULL DEFAULT '[]', deny_nodes TEXT NOT NULL DEFAULT '[]',
    expires_on TEXT, status TEXT NOT NULL DEFAULT 'active', note TEXT NOT NULL DEFAULT '',
    telegram_id INTEGER UNIQUE, oidc_subject TEXT UNIQUE,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS node_tiers (node TEXT PRIMARY KEY, tiers TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tier_rules (tier TEXT PRIMARY KEY, accepts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- Node agent user sync (plan-console-phase2 §3.3): what the node last reported and what it was sent.
CREATE TABLE IF NOT EXISTS node_sync (
    node TEXT PRIMARY KEY, checked_at INTEGER NOT NULL, desired TEXT NOT NULL, applied TEXT, running TEXT,
    pending TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '');
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


# Columns added after a table first shipped: (table, column, definition).
ADDED_COLUMNS = (
    ("audit_log", "actor", "TEXT NOT NULL DEFAULT ''"),
)


def init(path):
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        for table, column, definition in ADDED_COLUMNS:
            if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError as exc:  # another service added it first
                    if "duplicate column" not in str(exc):
                        raise


def audit(conn, action, target, detail="", actor=""):
    conn.execute("INSERT INTO audit_log (at, action, target, detail, actor) VALUES (?, ?, ?, ?, ?)",
                 (now(), action, target, detail, actor))


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
