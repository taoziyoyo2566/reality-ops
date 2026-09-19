"""Node status: probes through each node, events, daily summaries and status.json (plan-node-status-page §3).

The `status` service runs `python -m console.status`. Every interval it checks the check URL directly from this
host, then once through each node and transport with the status probe account, using an Xray client it runs as
a child process. Results go to the console database; status.json goes to the subscription service's data
directory, which renders it at /s/<token>/status.
"""
import concurrent.futures
import datetime
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import config, db, egress, publish as pub, registry as reg

SCHEMA = 1
TRANSPORTS = ("vision", "xhttp")
TRANSPORT_NAMES = {"vision": "Vision", "xhttp": "XHTTP"}
KINDS = ("outage", "partial", "maintenance")
RECENT_HOURS = 24          # the hour view; the minute view covers the last hour, one cell per round
RECENT_ROUNDS = 120        # at most this many rounds in the minute view (60 at the default 60 s interval)
USER_AGENT = "reality-status-probe/1"
NOTE_MAX = 500


# --------------------------------------------------------------------------
# Pure parts: targets, client configuration, event logic, days
# --------------------------------------------------------------------------

def probe_nodes(nodes):
    """Registered nodes that carry the probe account, by name."""
    return {name: node for name, node in nodes.items() if node.status_probe}


def targets(nodes):
    """[(node name, transport)] in a stable order."""
    out = []
    for name in sorted(probe_nodes(nodes)):
        out.append((name, "vision"))
        if nodes[name].xhttp["enabled"]:
            out.append((name, "xhttp"))
    return out


def client_config(nodes, probe, base_port):
    """Xray client: one local HTTP proxy port per target, each routed only to its node. No direct outbound."""
    inbounds, outbounds, rules = [], [{"tag": "none", "protocol": "blackhole", "settings": {}}], []
    for index, (name, transport) in enumerate(targets(nodes)):
        node = nodes[name]
        stream = {"network": "xhttp" if transport == "xhttp" else "raw", "security": "reality",
                  "realitySettings": {"serverName": node.sni, "fingerprint": "chrome",
                                      "publicKey": node.public_key, "shortId": probe["short_id"]}}
        if transport == "xhttp":
            stream["xhttpSettings"] = {"path": node.xhttp["path"], "mode": node.xhttp.get("mode", "auto")}
        tag = f"t{index}"
        outbounds.append({"tag": tag, "protocol": "vless", "streamSettings": stream, "settings": {"vnext": [{
            "address": node.endpoint.strip("[]"), "port": node.port,
            "users": [{"id": probe["uuid"], "encryption": "none",
                       "flow": "xtls-rprx-vision" if transport == "vision" else ""}]}]}})
        inbounds.append({"tag": f"in-{tag}", "listen": "127.0.0.1", "port": base_port + index,
                         "protocol": "http", "settings": {}})
        rules.append({"type": "field", "inboundTag": [f"in-{tag}"], "outboundTag": tag})
    return {"log": {"loglevel": "error", "access": "none"}, "inbounds": inbounds, "outbounds": outbounds,
            "routing": {"rules": rules}}


STATE_KEYS = ("fails", "oks", "first_fail_at", "first_ok_at", "down_since", "last_known_at")


def new_state():
    return {key: 0 if key in ("fails", "oks") else None for key in STATE_KEYS}


def advance(state, ok, at, fail_count, recover_count):
    """One probe result for one target. Returns (state, down_from, up_at).

    down_from is set when this result starts an event (the time of the first failure in the run);
    up_at is set when it ends one (the time of the first success in the run). ok=None changes nothing.
    """
    s = dict(state)
    if ok is None:
        return s, None, None
    s["last_known_at"] = at
    if ok:
        s["fails"], s["first_fail_at"] = 0, None
        if s["oks"] == 0:
            s["first_ok_at"] = at
        s["oks"] += 1
        if s["down_since"] is not None and s["oks"] >= recover_count:
            s["down_since"] = None
            return s, None, s["first_ok_at"]
        return s, None, None
    s["oks"], s["first_ok_at"] = 0, None
    if s["fails"] == 0:
        s["first_fail_at"] = at
    s["fails"] += 1
    if s["down_since"] is None and s["fails"] >= fail_count:
        s["down_since"] = s["first_fail_at"]
        return s, s["down_since"], None
    return s, None, None


def zone(offset_hours):
    return datetime.timezone(datetime.timedelta(hours=offset_hours))


def day_of(epoch, offset_hours):
    return datetime.datetime.fromtimestamp(epoch, zone(offset_hours)).strftime("%Y-%m-%d")


def day_bounds(day, offset_hours):
    start = datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=zone(offset_hours))
    return int(start.timestamp()), int((start + datetime.timedelta(days=1)).timestamp())


def hour_start(epoch, offset_hours):
    """The start of the local clock hour containing epoch (offsets such as +5:30 shift the boundary)."""
    off = int(round(offset_hours * 3600))
    return (epoch + off) // 3600 * 3600 - off


def round_state(results, node_targets):
    """(state, failed transport names, checked count) of one round of a node: ok, partial, outage or nodata."""
    known = {t: results[t] for t in node_targets if results.get(t) is not None}
    failed = [TRANSPORT_NAMES[t] for t in node_targets if known.get(t) == 0]
    if not known:
        return "nodata", [], 0
    if not failed:
        return "ok", [], len(known)
    return ("outage" if len(failed) == len(known) else "partial"), failed, len(known)


def last_days(now, count, offset_hours):
    today = datetime.datetime.fromtimestamp(now, zone(offset_hours)).date()
    return [(today - datetime.timedelta(days=count - 1 - i)).isoformat() for i in range(count)]


def overlap(start, end, lo, hi):
    return max(0, min(end, hi) - max(start, lo))


# --------------------------------------------------------------------------
# Database updates
# --------------------------------------------------------------------------

def load_states(conn):
    return {(r["node"], r["transport"]): dict(r) for r in conn.execute("SELECT * FROM probe_state")}


def save_state(conn, name, transport, state, result, at):
    ok, latency, error = result
    conn.execute(
        "INSERT OR REPLACE INTO probe_state (node, transport, fails, oks, first_fail_at, first_ok_at, down_since, "
        "last_known_at, last_at, last_ok, last_latency_ms, last_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, transport, *(state[k] for k in STATE_KEYS), at, None if ok is None else int(ok), latency, error))


def open_incident(conn, name, maintenance=False):
    op = "=" if maintenance else "!="
    row = conn.execute(f"SELECT * FROM incident WHERE node = ? AND ended_at IS NULL AND kind {op} 'maintenance' "
                       "ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    return dict(row) if row else None


def close_incident(conn, incident, at):
    """End an event at `at` (never before its start); returns the end. An empty one without a note is dropped."""
    end = max(at, incident["started_at"])
    if end == incident["started_at"] and not incident.get("note"):
        conn.execute("DELETE FROM incident WHERE id = ?", (incident["id"],))
    else:
        conn.execute("UPDATE incident SET ended_at = ? WHERE id = ?", (end, incident["id"]))
    return end


def start_incident(conn, name, kind, at, transports=()):
    cur = conn.execute("INSERT INTO incident (node, kind, started_at, transports) VALUES (?, ?, ?, ?)",
                       (name, kind, at, ",".join(transports)))
    return {"id": cur.lastrowid, "node": name, "kind": kind, "started_at": at, "ended_at": None,
            "transports": ",".join(transports), "note": ""}


def display_changes(conn):
    return {r["node"]: (bool(r["shown"]), r["changed_at"])
            for r in conn.execute("SELECT node, shown, changed_at FROM node_display")}


def meta(conn, key):
    row = conn.execute("SELECT value FROM status_meta WHERE key = ?", (key,)).fetchone()
    return int(row["value"]) if row else None


def set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO status_meta (key, value) VALUES (?, ?)", (key, str(value)))


def settle(conn, name, node_targets, down, current, at, floor):
    """Make the open event match the transports that are down as of `at`. Returns (current, floor).

    floor is the latest end of this node's events: a new event never starts before it.
    """
    transports = [t for t in node_targets if t in down]
    kind = ("outage" if len(transports) == len(node_targets) else "partial") if transports else None
    if current and (kind != current["kind"] or ",".join(transports) != current["transports"]):
        floor = max(floor, close_incident(conn, current, at))
        current = None
    if kind and not current:
        current = start_incident(conn, name, kind, max(at, floor), transports)
    return current, floor


def record_round(conn, st, nodes, results, at):
    """Store one round and move events forward. results: {(node, transport): (ok|None, latency_ms, error)}."""
    probed = probe_nodes(nodes)
    wanted = set(targets(nodes))
    display = display_changes(conn)
    states = load_states(conn)
    for (name, transport), (ok, latency, error) in results.items():
        conn.execute("INSERT OR REPLACE INTO probe_result (at, node, transport, ok, latency_ms, error) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (at, name, transport, None if ok is None else int(ok), latency, error))
    # Whatever left the probe set keeps its history and nothing else; a node or transport that comes back starts fresh.
    for key in [k for k in states if k not in wanted]:
        conn.execute("DELETE FROM probe_state WHERE node = ? AND transport = ?", key)
        del states[key]
    for row in conn.execute("SELECT * FROM incident WHERE ended_at IS NULL").fetchall():
        if row["node"] not in probed:
            close_incident(conn, dict(row), at)
    for row in conn.execute("SELECT key FROM status_meta WHERE key LIKE 'joined:%'").fetchall():
        if row["key"].split(":", 1)[1] not in probed:
            conn.execute("DELETE FROM status_meta WHERE key = ?", (row["key"],))

    for name in sorted(probed):
        node_targets = [t for n, t in targets(nodes) if n == name]
        joined = meta(conn, f"joined:{name}")
        if joined is None:
            joined = at
            set_meta(conn, f"joined:{name}", at)
        shown, changed_at = display.get(name, (False, joined))
        # maintenance is never dated before the node joined the probe set, nor after this round
        changed_at = min(max(changed_at, joined), at)
        maintenance = open_incident(conn, name, maintenance=True)
        current = open_incident(conn, name)
        before = {t: {k: (states.get((name, t)) or new_state())[k] for k in STATE_KEYS} for t in node_targets}
        if not shown:
            if current:
                close_incident(conn, current, changed_at)
            if not maintenance:
                start_incident(conn, name, "maintenance", changed_at)
            for transport in node_targets:
                save_state(conn, name, transport, new_state(), results.get((name, transport), (None, None, "")), at)
            continue
        if maintenance:
            close_incident(conn, maintenance, changed_at)

        # A gap in the data (rounds without a result, or the service not running) ends what was open one interval
        # after the last known round; failures before the gap do not count towards a new event.
        known_before = [s["last_known_at"] for s in before.values() if s["last_known_at"] is not None]
        if known_before and at - max(known_before) > 2 * st.interval:
            if current:
                close_incident(conn, current, max(known_before) + st.interval)
                current = None
            before = {t: new_state() for t in node_targets}
        floor = conn.execute("SELECT MAX(ended_at) FROM incident WHERE node = ?", (name,)).fetchone()[0] or 0
        down = {t for t in node_targets if before[t]["down_since"] is not None}
        transitions, known = [], False
        for transport in node_targets:
            result = results.get((name, transport), (None, None, "no result"))
            state, down_from, up_at = advance(before[transport], result[0], at, st.fail_count, st.recover_count)
            if up_at is not None:
                transitions.append((up_at, 0, transport))
            if down_from is not None:
                transitions.append((down_from, 1, transport))
            known = known or result[0] is not None
            save_state(conn, name, transport, state, result, at)
        # Replay the changes in time order (a recovery before a failure at the same time): a recovery and a
        # failure found in the same round can overlap in the past.
        for when, is_down, transport in sorted(transitions):
            (down.add if is_down else down.discard)(transport)
            current, floor = settle(conn, name, node_targets, down, current, when, floor)
        current, floor = settle(conn, name, node_targets, down, current, at, floor)

        column = "known_seconds" if known else "unknown_seconds"
        conn.execute(f"INSERT INTO status_daily (node, day, {column}) VALUES (?, ?, ?) "
                     f"ON CONFLICT(node, day) DO UPDATE SET {column} = {column} + excluded.{column}",
                     (name, day_of(at, st.utc_offset_hours), st.interval))
    set_meta(conn, "last_round", at)


def purge(conn, st, now):
    conn.execute("DELETE FROM probe_result WHERE at < ?", (now - st.raw_days * 86400,))
    conn.execute("DELETE FROM incident WHERE ended_at IS NOT NULL AND ended_at < ?", (now - st.keep_days * 86400,))
    conn.execute("DELETE FROM status_daily WHERE day < ?", (day_of(now - st.keep_days * 86400, st.utc_offset_hours),))


def last_round(conn):
    return meta(conn, "last_round")


def set_note(conn, incident_id, note):
    note = " ".join((note or "").split())[:NOTE_MAX]
    cur = conn.execute("UPDATE incident SET note = ? WHERE id = ?", (note, incident_id))
    return cur.rowcount == 1, note


# --------------------------------------------------------------------------
# The published document
# --------------------------------------------------------------------------

def alerts(conn, st, nodes, now):
    """Home page warnings from the status service."""
    if not st.enabled:
        return []
    out = []
    last = last_round(conn)
    if last is None or now - last > 5 * 60:
        out.append("状态检测超过 5 分钟没有完成一轮（status 服务），状态页不再更新")
    probed = probe_nodes(nodes)
    for row in conn.execute("SELECT * FROM incident WHERE ended_at IS NULL AND kind != 'maintenance' ORDER BY node"):
        if row["node"] in probed:
            names = "、".join(TRANSPORT_NAMES[t] for t in row["transports"].split(",") if t)
            kind = "故障" if row["kind"] == "outage" else "部分异常"
            out.append(f"节点 {row['node']} {kind}（{names}），已持续 {max(0, now - row['started_at']) // 60} 分钟")
    return out


def incident_rows(conn, since, node=None):
    sql, args = "SELECT * FROM incident WHERE (ended_at IS NULL OR ended_at >= ?)", [since]
    if node:
        sql += " AND node = ?"
        args.append(node)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY started_at DESC", args)]
    for row in rows:
        row["transport_names"] = [TRANSPORT_NAMES[t] for t in row["transports"].split(",") if t]
    return rows


def build_document(conn, st, nodes, now):
    """status.json: node labels, current state, daily cells and events. No addresses or credentials."""
    probed = probe_nodes(nodes)
    days = last_days(now, st.show_days, st.utc_offset_hours)
    window_start = day_bounds(days[0], st.utc_offset_hours)[0]
    shown = db.shown_nodes(conn)
    states = load_states(conn)
    last = last_round(conn)
    stale = last is None or last < now - 3 * st.interval
    # without recent rounds nothing is known past the last one
    horizon = min(now, last + st.interval) if stale and last is not None else now
    labels = {name: node.label for name, node in probed.items()}
    # the minute and hour views (plan-node-status-page §10): the last hour round by round, the last 24 clock hours
    end = last if last is not None and not stale else now // st.interval * st.interval
    slots = [end - (n - 1) * st.interval for n in range(max(1, min(3600 // st.interval, RECENT_ROUNDS)), 0, -1)]
    hours_from = hour_start(end, st.utc_offset_hours) - (RECENT_HOURS - 1) * 3600
    rounds = {}   # node -> at -> {transport: ok}
    for r in conn.execute("SELECT at, node, transport, ok FROM probe_result WHERE at >= ?",
                          (min(hours_from, slots[0]),)):
        rounds.setdefault(r["node"], {}).setdefault(r["at"], {})[r["transport"]] = r["ok"]
    doc_nodes, events = [], []
    for name in sorted(probed, key=lambda n: (labels[n], n)):
        node_targets = [t for n, t in targets(nodes) if n == name]
        incidents = [dict(r) for r in conn.execute(
            "SELECT * FROM incident WHERE node = ? AND (ended_at IS NULL OR ended_at > ?) ORDER BY started_at",
            (name, window_start))]
        daily = {r["day"]: dict(r) for r in conn.execute(
            "SELECT * FROM status_daily WHERE node = ? AND day >= ?", (name, days[0]))}
        cells, known_total, outage_total = [], 0, 0
        for day in days:
            lo, hi = day_bounds(day, st.utc_offset_hours)
            seconds = {kind: 0 for kind in KINDS}
            for inc in incidents:
                seconds[inc["kind"]] += overlap(inc["started_at"], inc["ended_at"] or horizon, lo, min(hi, horizon))
            known = (daily.get(day) or {}).get("known_seconds", 0)
            known_total += known
            outage_total += min(seconds["outage"], known)
            if seconds["outage"]:
                state = "outage"
            elif seconds["partial"]:
                state = "partial"
            elif seconds["maintenance"]:
                state = "maintenance"
            elif known:
                state = "ok"
            else:
                state = "nodata"
            cells.append([state] + [seconds[kind] // 60 for kind in KINDS])
        open_now = [i["kind"] for i in incidents if i["ended_at"] is None and i["kind"] != "maintenance"]
        node_states = [states.get((name, t)) or {} for t in node_targets]
        if name not in shown:
            current = "maintenance"
        elif stale or not node_states or all(s.get("last_ok") is None for s in node_states):
            current = "nodata"
        elif open_now:
            current = open_now[0]
        else:
            current = "ok"
        availability = None
        if known_total:
            availability = round(100 * (known_total - outage_total) / known_total, 2)
        maintenance = [(i["started_at"], i["ended_at"]) for i in incidents if i["kind"] == "maintenance"]
        minutes = []
        for at in slots:
            if any(lo <= at and (hi is None or at < hi) for lo, hi in maintenance):
                minutes.append(["maintenance", [], 0])
            else:
                state, failed, checked = round_state(rounds.get(name, {}).get(at, {}), node_targets)
                minutes.append([state, failed, checked])
        node_rounds = [(at, round_state(got, node_targets)) for at, got in rounds.get(name, {}).items()]
        hours = []
        for k in range(RECENT_HOURS):
            lo = hours_from + k * 3600
            hi = min(lo + 3600, horizon)
            seconds = {kind: 0 for kind in KINDS}
            for inc in incidents:
                seconds[inc["kind"]] += overlap(inc["started_at"], inc["ended_at"] or horizon, lo, hi)
            checked = [s for at, s in node_rounds if lo <= at < lo + 3600 and s[2]]
            succeeded = sum(1 for s in checked if s[0] == "ok")
            if seconds["outage"]:
                state = "outage"
            elif seconds["partial"]:
                state = "partial"
            elif seconds["maintenance"]:
                state = "maintenance"
            else:
                state = "ok" if checked else "nodata"
            hours.append([state, succeeded, len(checked)] + [seconds[kind] // 60 for kind in KINDS])
        doc_nodes.append({"label": labels[name], "state": current, "availability": availability, "days": cells,
                          "hours": hours, "minutes": minutes})
        for inc in incidents:
            events.append({"node": labels[name], "kind": inc["kind"], "started_at": inc["started_at"],
                           "ended_at": inc["ended_at"],
                           "transports": [TRANSPORT_NAMES[t] for t in inc["transports"].split(",") if t],
                           "all_transports": len(node_targets), "note": inc["note"]})
    events.sort(key=lambda e: (e["started_at"], e["node"]), reverse=True)
    return {"schema": SCHEMA, "generated_at": now, "interval": st.interval, "fail_count": st.fail_count,
            "tz": {"offset_hours": st.utc_offset_hours, "label": st.tz_label},
            "days": days, "hours": {"start": hours_from, "count": RECENT_HOURS},
            "minutes": {"start": slots[0], "step": st.interval, "count": len(slots)},
            "nodes": doc_nodes, "events": events}


def latency_hours(conn, st, nodes, now):
    """Admin page only: per node and transport, the median and highest time of successful checks in each of the
    last 24 clock hours. Measured from this host, so it shows this host's path to the node, not a user's."""
    start = hour_start(now, st.utc_offset_hours) - (RECENT_HOURS - 1) * 3600
    values = {}
    for r in conn.execute("SELECT at, node, transport, latency_ms FROM probe_result "
                          "WHERE at >= ? AND ok = 1 AND latency_ms IS NOT NULL", (start,)):
        k = (r["at"] - start) // 3600
        if k < RECENT_HOURS:
            values.setdefault((r["node"], r["transport"]), [[] for _ in range(RECENT_HOURS)])[k].append(r["latency_ms"])
    rows = []
    for name, transport in targets(nodes):
        hours = values.get((name, transport)) or [[] for _ in range(RECENT_HOURS)]
        everything = sorted(v for h in hours for v in h)
        cells = []
        for k, hour in enumerate(hours):
            hour.sort()
            cells.append({"start": start + k * 3600, "count": len(hour),
                          "median": hour[len(hour) // 2] if hour else None, "max": hour[-1] if hour else None})
        rows.append({"node": name, "transport": TRANSPORT_NAMES[transport], "hours": cells,
                     "median": everything[len(everything) // 2] if everything else None})
    return rows


def publish_document(settings, doc):
    pub._write(settings.subs_data_dir, "status.json", doc)


def remove_document(settings):
    try:
        os.remove(os.path.join(settings.subs_data_dir, "status.json"))
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url, proxy, timeout, expect):
    """(ok, latency_ms, error). ok is None when the local proxy itself is not answering."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy} if proxy else {}), _NoRedirect)
    start = time.monotonic()
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except urllib.error.URLError as exc:
        reason = exc.reason
        if proxy and isinstance(reason, ConnectionRefusedError):
            return None, None, "local client"
        return False, None, "timeout" if isinstance(reason, (socket.timeout, TimeoutError)) else "connect"
    except (socket.timeout, TimeoutError):
        return False, None, "timeout"
    except (OSError, http.client.HTTPException):
        return False, None, "connect"
    latency = int((time.monotonic() - start) * 1000)
    if status != expect:
        return False, latency, f"http {status}"
    return True, latency, ""


class XrayClient:
    """The Xray client child process; restarted when its configuration changes or it has exited."""

    def __init__(self, binary, workdir):
        self.binary = binary
        self.path = os.path.join(workdir, "status-client.json")
        self.text = None
        self.proc = None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def ensure(self, cfg, first_port, wait=5.0):
        text = json.dumps(cfg, sort_keys=True)
        if text == self.text and self.running():
            return True
        self.stop()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        self.text = text
        if not cfg["inbounds"]:
            return False
        self.proc = subprocess.Popen([self.binary, "run", "-c", self.path], stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and self.running():
            try:
                socket.create_connection(("127.0.0.1", first_port), timeout=0.5).close()
                return True
            except OSError:
                time.sleep(0.1)
        return self.running()

    def stop(self):
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def load_probe(path):
    with open(path) as fh:
        doc = json.load(fh)
    from subs import links
    if not (isinstance(doc, dict) and links.UUID_RE.match(str(doc.get("uuid", "")))
            and links.SHORT_ID_RE.match(str(doc.get("short_id", ""))) and doc.get("short_id")):
        raise ValueError("probe credential file is invalid")
    return {"uuid": doc["uuid"], "short_id": doc["short_id"]}


class Prober:
    def __init__(self, settings, st, client=None, fetcher=fetch):
        self.settings = settings
        self.st = st
        self.registry = reg.Registry(settings.registry_dir)
        self.client = client or XrayClient(st.xray, st.workdir)
        self.fetch = fetcher

    def run_round(self, at):
        """One round at `at`; returns the results, or None when this round was already processed (a restart)."""
        with db.connect(self.settings.db_path) as conn:
            last = last_round(conn)
        if last is not None and at <= last:
            return None
        nodes, _, _ = self.registry.current()
        work = targets(nodes)
        results = {}
        if not work:
            self.client.stop()
        else:
            direct = self.fetch(self.st.check_url, None, self.st.timeout, self.st.check_status)
            probe = load_probe(self.st.probe_file)
            ready = self.client.ensure(client_config(nodes, probe, self.st.base_port), self.st.base_port)
            if not direct[0]:
                results = {t: (None, None, "local network") for t in work}
            elif not ready:
                results = {t: (None, None, "local client") for t in work}
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(work)) as pool:
                    futures = {t: pool.submit(self.fetch, self.st.check_url,
                                              f"http://127.0.0.1:{self.st.base_port + i}",
                                              self.st.timeout, self.st.check_status)
                               for i, t in enumerate(work)}
                    results = {t: f.result() for t, f in futures.items()}
        with db.connect(self.settings.db_path) as conn:
            with db.transaction(conn):
                record_round(conn, self.st, nodes, results, at)
                if at % 3600 < self.st.interval:
                    purge(conn, self.st, at)
            doc = build_document(conn, self.st, nodes, at)
        publish_document(self.settings, doc)
        return results


def healthy(settings, st, now=None):
    now = now or db.now()
    with db.connect(settings.db_path) as conn:
        last = last_round(conn)
    return last is not None and now - last <= 3 * st.interval


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    settings, st = config.from_env(), config.status_from_env()
    if argv == ["--check"]:
        return 0 if healthy(settings, st) else 1
    db.init(settings.db_path)
    prober = Prober(settings, st)
    signal.signal(signal.SIGTERM, lambda *_: (prober.client.stop(), sys.exit(0)))
    print(f"status: probing every {st.interval}s", flush=True)
    last_egress = 0
    while True:
        at = int(time.time()) // st.interval * st.interval
        try:
            prober.run_round(at)
        except Exception as exc:  # one bad round must not stop the service; the log says why
            print(f"status: round failed: {type(exc).__name__}: {exc}", flush=True)
        # proxy egress no node uses (plan-egress-console §3.3): all of them every 10 minutes, new or changed ones
        # every round; the nodes check their own
        full = time.time() - last_egress >= egress.SPT_CHECK_SECONDS
        if full:
            last_egress = time.time()
        try:
            with db.connect(settings.db_path) as conn:
                egress.check_unassigned(conn, st.xray, settings.egress_check_url, set(prober.registry.current()[0]),
                                        new_only=not full)
        except Exception as exc:  # the status page must keep running whatever a proxy does
            print(f"status: egress check failed: {type(exc).__name__}", flush=True)
        time.sleep(max(1.0, at + st.interval - time.time()))


if __name__ == "__main__":
    sys.exit(main())
