"""Subscription access log in SQLite (plan-subscription-service §3.6). Never stores tokens or request paths.

Operator query on the host: docker compose exec subs python -m subs.accesslog [--days 30]
prints the last fetch per user and format.
"""
import argparse
import os
import sqlite3
import sys
import threading
import time

RETENTION_DAYS = 90
_lock = threading.Lock()


class AccessLog:
    def __init__(self, path):
        self.path = path
        self._last_purge = 0
        with self._conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS hits (ts INTEGER NOT NULL, user TEXT NOT NULL, fmt TEXT NOT NULL,"
                         " ip TEXT, user_agent TEXT)")
            conn.execute("CREATE INDEX IF NOT EXISTS hits_user_ts ON hits (user, ts)")

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def record(self, user, fmt, ip, user_agent):
        now = int(time.time())
        with _lock, self._conn() as conn:
            conn.execute("INSERT INTO hits VALUES (?, ?, ?, ?, ?)", (now, user, fmt, ip, (user_agent or "")[:200]))
            if now - self._last_purge > 86400:
                conn.execute("DELETE FROM hits WHERE ts < ?", (now - RETENTION_DAYS * 86400,))
                self._last_purge = now

    def last_fetch(self, days):
        with self._conn() as conn:
            return conn.execute("SELECT user, fmt, MAX(ts), COUNT(*) FROM hits WHERE ts >= ? GROUP BY user, fmt"
                                " ORDER BY user, fmt", (int(time.time()) - days * 86400,)).fetchall()


def main(argv=None):
    parser = argparse.ArgumentParser(description="last subscription fetch per user")
    parser.add_argument("--days", type=int, default=RETENTION_DAYS)
    parser.add_argument("--db", default=os.path.join(os.environ.get("SUBS_DB_DIR", "/db"), "access.sqlite"))
    args = parser.parse_args(argv)
    for user, fmt, ts, count in AccessLog(args.db).last_fetch(args.days):
        print(f"{user}\t{fmt}\t{time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}\t{count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
