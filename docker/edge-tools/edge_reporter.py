#!/usr/bin/env python3
"""Report per-user traffic and node status from an xray_edge node to the console (plan-console-phase1 §3.4).

Reads only Xray's metrics endpoint (/debug/vars, read-only) and never the Xray API, which can add or remove users.
Each report carries an instance id (new whenever the spool state is lost) and a sequence number; unsent reports
stay in the spool directory and are retried in order, and the console ignores a report it has already stored.

Environment:
  REPORT_URL         https://<report host>/report
  REPORT_NODE        inventory name of this node
  REPORT_TOKEN_FILE  file with the node's report token (default /run/report/report-token)
  METRICS_URL        default http://127.0.0.1:10086/debug/vars
  REPORT_INTERVAL    seconds between reports (default 300)
  SPOOL_DIR          writable directory for state and unsent reports (default /spool)
  SPOOL_MAX          unsent reports kept before the oldest is dropped (default 2016, one week at 5 minutes)
  ERROR_LOG          Xray error log (default /var/log/xray/error.log)
  LISTEN_PORT        port whose listening state is reported (default 443)

The reporter shares Xray's network namespace. When Xray restarts, that namespace is replaced and the reporter
loses it, so after MAX_METRICS_FAILURES failed readings in a row it exits and Docker restarts it into the new one.
Standard library only.
"""
import datetime
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

SCHEMA = 1
ERROR_TAIL_LINES = 20
ERROR_TAIL_BYTES = 4096
MAX_METRICS_FAILURES = 3
MAX_SCAN_BYTES = 1 << 20
# Cloudflare's browser integrity check refuses Python's default User-Agent (error 1010).
USER_AGENT = "reality-edge-reporter/1"
STARTUP_MARK = b"] core: Xray "


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def iso(ts):
    return ts.isoformat().replace("+00:00", "Z")


def log(message):
    print(f"{iso(utcnow())} reporter: {message}", flush=True)


def user_counters(metrics, node):
    """{user name: {"up": int, "down": int}} from /debug/vars; emails are <user>.<node>."""
    users = (metrics.get("stats") or {}).get("user") or {}
    suffix = f".{node}"
    result = {}
    for email, counters in users.items():
        if not isinstance(counters, dict) or not email.endswith(suffix):
            continue
        name = email[: -len(suffix)]
        result[name] = {"up": int(counters.get("uplink", 0)), "down": int(counters.get("downlink", 0))}
    return result


def traffic_delta(previous, current, started=False):
    """(per-user increments, restarted).

    Counters only grow while Xray runs. After a restart every current value is new traffic. A restart is known from
    Xray's own start line (`started`), or from a smaller value or a vanished user; the start line matters when the
    traffic since the restart already exceeds the old totals. Without a previous reading (first start) the current
    values only become the baseline.
    """
    if previous is None:
        return {}, False
    restarted = started or any(
        name not in current or current[name][k] < previous[name][k]
        for name in previous for k in ("up", "down")
    )
    delta = {}
    for name, now in current.items():
        before = {"up": 0, "down": 0} if restarted else previous.get(name, {"up": 0, "down": 0})
        inc = {k: now[k] - before[k] for k in ("up", "down")}
        if inc["up"] or inc["down"]:
            delta[name] = inc
    return delta, restarted


def listening(port, proc_net="/proc/net"):
    """True when a TCP socket in this network namespace listens on the port (IPv4 or IPv6)."""
    wanted = f"{port:04X}"
    for name in ("tcp", "tcp6"):
        try:
            with open(os.path.join(proc_net, name)) as fh:
                next(fh, None)
                for line in fh:
                    fields = line.split()
                    # fields: sl local_address rem_address st ...; state 0A is LISTEN
                    if len(fields) > 3 and fields[3] == "0A" and fields[1].rsplit(":", 1)[-1] == wanted:
                        return True
        except OSError:
            continue
    return False


def log_startups(path, position):
    """(started, new position): whether Xray logged a start line since `position`.

    `position` is {"ino", "offset"} from the previous cycle; None (first cycle) only records the end of the file.
    Another inode, or a file shorter than the offset (rotated with copytruncate), is read from the start.
    Only whole lines are consumed, at most MAX_SCAN_BYTES per cycle.
    """
    try:
        with open(path, "rb") as fh:
            size, ino = os.fstat(fh.fileno()).st_size, os.fstat(fh.fileno()).st_ino
            if position is None:
                return False, {"ino": ino, "offset": size}
            offset = position.get("offset", 0)
            if position.get("ino") != ino or size < offset:
                offset = 0
            fh.seek(offset)
            data = fh.read(MAX_SCAN_BYTES)
    except OSError:
        return False, position
    end = data.rfind(b"\n") + 1
    if end == 0 and len(data) == MAX_SCAN_BYTES:
        end = len(data)  # a single oversized line; skip it rather than stall
    started = any(STARTUP_MARK in line and line.rstrip().endswith(b" started") for line in data[:end].split(b"\n"))
    return started, {"ino": ino, "offset": offset + end}


def error_tail(path):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - ERROR_TAIL_BYTES))
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    if size > ERROR_TAIL_BYTES and lines:
        lines = lines[1:]  # the first line may be cut
    return lines[-ERROR_TAIL_LINES:]


class Spool:
    def __init__(self, directory, limit):
        self.dir = directory
        self.limit = limit
        self.state_path = os.path.join(directory, "state.json")

    def _write(self, path, doc):
        tmp = f"{path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, sort_keys=True)
        os.replace(tmp, path)

    def load_state(self):
        try:
            with open(self.state_path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def save_state(self, state):
        self._write(self.state_path, state)

    def pending(self):
        return sorted(n for n in os.listdir(self.dir) if n.startswith("report-") and n.endswith(".json"))

    def add(self, report):
        self._write(os.path.join(self.dir, f"report-{report['seq']:012d}.json"), report)
        dropped = 0
        names = self.pending()
        while len(names) > self.limit:
            os.remove(os.path.join(self.dir, names.pop(0)))
            dropped += 1
        return dropped

    def read(self, name):
        with open(os.path.join(self.dir, name)) as fh:
            return json.load(fh)

    def remove(self, name):
        os.remove(os.path.join(self.dir, name))


def fetch_metrics(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.load(resp)


def post(url, token, report):
    body = json.dumps(report).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def collect(cfg, spool, now):
    """Build the next report from metrics and the stored state; returns (report, new state)."""
    state = spool.load_state() or {"instance": secrets.token_hex(8), "seq": 0, "counters": None, "at": None,
                                    "dropped": 0, "errlog": None}
    metrics = fetch_metrics(cfg["metrics_url"])
    current = user_counters(metrics, cfg["node"])
    started, errlog = log_startups(cfg["error_log"], state.get("errlog"))
    delta, restarted = traffic_delta(state.get("counters"), current, started)
    seq = int(state.get("seq", 0)) + 1
    report = {
        "schema": SCHEMA,
        "node": cfg["node"],
        "instance": state["instance"],
        "seq": seq,
        "period": {"from": state.get("at"), "to": iso(now)},
        "xray_restarted": restarted,
        "baseline": state.get("counters") is None,
        "traffic": delta,
        "listening": listening(cfg["listen_port"]),
        "error_tail": error_tail(cfg["error_log"]),
        "dropped_reports": int(state.get("dropped", 0)),
    }
    return report, {"instance": state["instance"], "seq": seq, "counters": current, "at": iso(now), "dropped": 0,
                    "errlog": errlog}


def flush(cfg, spool, token):
    """Send pending reports in order; stop at the first failure so order is kept."""
    for name in spool.pending():
        status = post(cfg["url"], token, spool.read(name))
        if status in (200, 201, 204, 409):
            spool.remove(name)
            continue
        log(f"report {name} not accepted (HTTP {status}); will retry")
        return False
    return True


def run_once(cfg, spool):
    """One cycle; False when the metrics endpoint could not be read."""
    now = utcnow()
    try:
        report, state = collect(cfg, spool, now)
    except (OSError, ValueError) as exc:
        log(f"metrics unavailable: {type(exc).__name__}")
        return False
    dropped = spool.add(report)
    if dropped:
        state["dropped"] = dropped
        log(f"spool full, dropped {dropped} oldest report(s)")
    spool.save_state(state)
    try:
        with open(cfg["token_file"]) as fh:
            token = fh.read().strip()
    except OSError:
        log("report token unreadable; keeping reports in the spool")
        return True
    try:
        flush(cfg, spool, token)
    except (OSError, ValueError) as exc:
        log(f"console unreachable: {type(exc).__name__}")
    return True


def config_from_env(env):
    return {
        "url": env["REPORT_URL"],
        "node": env["REPORT_NODE"],
        "token_file": env.get("REPORT_TOKEN_FILE", "/run/report/report-token"),
        "metrics_url": env.get("METRICS_URL", "http://127.0.0.1:10086/debug/vars"),
        "interval": int(env.get("REPORT_INTERVAL", "300")),
        "spool_dir": env.get("SPOOL_DIR", "/spool"),
        "spool_max": int(env.get("SPOOL_MAX", "2016")),
        "error_log": env.get("ERROR_LOG", "/var/log/xray/error.log"),
        "listen_port": int(env.get("LISTEN_PORT", "443")),
    }


def main():
    cfg = config_from_env(os.environ)
    spool = Spool(cfg["spool_dir"], cfg["spool_max"])
    log(f"node {cfg['node']}, every {cfg['interval']}s")
    once = "--once" in sys.argv[1:]
    failures = 0
    while True:
        failures = 0 if run_once(cfg, spool) else failures + 1
        if once:
            return 0 if failures == 0 else 1
        if failures >= MAX_METRICS_FAILURES:
            log("metrics unreachable repeatedly; exiting so Docker restarts the reporter with Xray's network")
            return 1
        time.sleep(cfg["interval"] if failures == 0 else min(cfg["interval"], 30))


if __name__ == "__main__":
    sys.exit(main())
