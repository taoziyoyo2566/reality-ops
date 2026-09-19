"""Signs of a shared account (plan-sharing-signals §3.2): in how many places a user is online at the same time.

Node agents read Xray's online IP list of each user at every sync (about once a minute) and send keyed hashes of
the networks those addresses belong to (IPv4 /24, IPv6 /48), never the addresses. The key changes every UTC day,
so a network cannot be followed from one day to the next. The console keeps the hashes per 10-minute slot for
KEEP_DAYS days. A user's places in a slot are the larger of the IPv4 and IPv6 network counts: one device may
connect over both, while devices in the same home share a network.
"""
import datetime
import hashlib
import hmac
import re
import secrets
import time

from . import db

SLOT = 600
KEEP_DAYS = 8
TOKEN_RE = re.compile(r"^[46]:[0-9a-f]{16}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_TOKENS = 64


def day_of(epoch):
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def key_for(conn, day):
    """The key agents hash networks with on `day` (UTC); derived from a secret created once."""
    value = db.setting(conn, "online_secret")
    if value is None:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('online_secret', ?)", (secrets.token_hex(32),))
        value = db.setting(conn, "online_secret")
    return hmac.new(bytes.fromhex(value), day.encode(), hashlib.sha256).hexdigest()[:32]


def validate(online, day):
    """The agent's {user: ["4:<hash>" | "6:<hash>"]} and the day its key was for; raises ValueError."""
    if online is None:
        return {}
    if not (isinstance(online, dict) and len(online) <= 10000 and isinstance(day, str) and DAY_RE.match(day)):
        raise ValueError("invalid online")
    for user, tokens in online.items():
        if not (isinstance(user, str) and isinstance(tokens, list) and len(tokens) <= MAX_TOKENS
                and all(isinstance(t, str) and TOKEN_RE.match(t) for t in tokens)):
            raise ValueError("invalid online entry")
    return online


def record(conn, online, day, users, now):
    """Store one agent's sample for the current slot; users outside `users` and hashes made with another day's key
    (an agent that has not heard today's key yet) are left out."""
    if day != day_of(now):
        return 0
    slot = now // SLOT * SLOT
    stored = 0
    for user, tokens in online.items():
        if user not in users:
            continue
        for token in set(tokens):
            family, value = token.split(":")
            stored += conn.execute("INSERT OR IGNORE INTO online_seen (user, slot, family, token) VALUES (?, ?, ?, ?)",
                                   (user, slot, int(family), value)).rowcount
    return stored


def places(conn, since):
    """{user: {slot: places}} for slots from `since`."""
    out = {}
    for r in conn.execute("SELECT user, slot, family, COUNT(*) AS n FROM online_seen WHERE slot >= ? "
                          "GROUP BY user, slot, family", (since,)):
        per = out.setdefault(r["user"], {})
        per[r["slot"]] = max(per.get(r["slot"], 0), r["n"])
    return out


def peaks(conn, since):
    """{user: the most places at once since `since`}."""
    return {user: max(slots.values()) for user, slots in places(conn, since).items()}


def daily_peaks(conn, user, now, days, offset_hours):
    """[(day, most places at once)] for the last `days` local days, oldest first; 0 when not seen online."""
    zone = datetime.timezone(datetime.timedelta(hours=offset_hours))
    today = datetime.datetime.fromtimestamp(now, zone).date()
    out = {(today - datetime.timedelta(days=k)).isoformat(): 0 for k in range(days - 1, -1, -1)}
    for slot, n in places(conn, now - (days + 1) * 86400).get(user, {}).items():
        day = datetime.datetime.fromtimestamp(slot, zone).date().isoformat()
        if day in out:
            out[day] = max(out[day], n)
    return list(out.items())


def alerts(conn, threshold, now):
    return [f"用户 {user} 近 24 小时最多同时在 {n} 处在线（提醒阈值 {threshold} 处），可能与他人共用"
            for user, n in sorted(peaks(conn, now - 86400).items()) if n >= threshold]


def purge(conn, now):
    conn.execute("DELETE FROM online_seen WHERE slot < ?", (now - KEEP_DAYS * 86400,))
