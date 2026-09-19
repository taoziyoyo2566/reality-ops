#!/usr/bin/env python3
"""Node agent for xray_edge: reports traffic and status to the console, and keeps this node's users in line with
the console (plan-console-phase1 §3.4, plan-console-phase2 §3.3).

Reports: per-user counters from Xray's metrics endpoint (/debug/vars, read-only) every REPORT_INTERVAL. Each report
carries an instance id (new whenever the spool state is lost) and a sequence number; unsent reports stay in the spool
directory and are retried in order, and the console ignores a report it has already stored.

User sync (SYNC_ENABLED): every SYNC_INTERVAL the agent posts the users running here to SYNC_URL and receives the
console's list for this node; differences are applied through the Xray API (add / remove users, never a restart, never
the configuration files). It refuses an empty list and removing more than half of the users at once; those need a
deployment (edge.yml). Users named in SYNC_KEEP (the status probe account) are never touched.

Proxy egress (plan-egress-console, EGRESS_ENABLED): with each sync the agent checks the egress the console assigned to
this node and switches them at runtime; see edge_egress.py.

Online places (plan-sharing-signals §3.2): with each sync the agent reads the online IP list of each user from Xray
and sends keyed hashes of the networks they belong to (IPv4 /24, IPv6 /48), never the addresses; the key comes from
the console and changes every day.

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
  SYNC_ENABLED       "true" to keep users in line with the console (default false)
  SYNC_URL           https://<report host>/sync
  SYNC_INTERVAL      seconds between syncs (default 60)
  SYNC_KEEP          comma-separated users the agent never adds or removes
  XHTTP_ENABLED      "true" when the node has the XHTTP inbound
  EGRESS_ENABLED     "true" to check and switch the proxy egress the console assigns (needs SYNC_ENABLED)
  XRAY_BIN           default /usr/local/bin/xray;  XRAY_API default 127.0.0.1:10085

The reporter shares Xray's network namespace. When Xray restarts, that namespace is replaced and the reporter
loses it, so after MAX_METRICS_FAILURES failed readings in a row it exits and Docker restarts it into the new one.
Standard library only, with edge_egress.py and egress_probe.py next to this file.
"""
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

import edge_egress              # next to this file in the tools image

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


def xray_runtime(cfg):
    """Xray's own memory, goroutines and uptime (`xray api statssys`); None when the API does not answer."""
    try:
        stats = json.loads(xray_api(cfg, "statssys") or "{}")
    except (SyncError, ValueError):
        return None
    keys = {"alloc": "Alloc", "sys": "Sys", "goroutines": "NumGoroutine", "uptime": "Uptime"}
    values = {k: stats.get(v) for k, v in keys.items()}
    return values if all(isinstance(v, int) and v >= 0 for v in values.values()) else None


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
    runtime = xray_runtime(cfg)
    if runtime:
        report["xray"] = runtime
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


# --------------------------------------------------------------------------
# User sync (plan-console-phase2 §3.3)
# --------------------------------------------------------------------------

REALITY_TAG = "vless-reality"
XHTTP_TAG = "vless-xhttp"
XHTTP_LISTEN = "@xray-edge-xhttp"
VISION_FLOW = "xtls-rprx-vision"
MIN_REMOVALS_REFUSED = 4


class SyncError(Exception):
    """A sync step that failed; the message is safe to log and send (no user ids)."""


def xray_api(cfg, command, *args, stdin=None):
    # Go flag parsing stops at the first positional argument, so -s must precede emails or files.
    try:
        proc = subprocess.run([cfg["xray_bin"], "api", command, "-s", cfg["xray_api"], *args], input=stdin,
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncError(f"xray api {command}: {type(exc).__name__}") from None
    if proc.returncode != 0:
        raise SyncError(f"xray api {command} failed (rc={proc.returncode})")
    return proc.stdout


def inbound_tags(cfg):
    return [REALITY_TAG] + ([XHTTP_TAG] if cfg["xhttp"] else [])


def running_users(cfg, tag):
    """{user name: uuid} of this node's users on one inbound (emails are <user>.<node>)."""
    try:
        data = json.loads(xray_api(cfg, "inbounduser", f"-tag={tag}") or "{}")
    except ValueError:
        raise SyncError("xray api inbounduser: unreadable output") from None
    suffix = f".{cfg['node']}"
    users = {}
    for user in data.get("users") or []:
        email = user.get("email") or ""
        if email.endswith(suffix):
            users[email[:-len(suffix)]] = str((user.get("account") or {}).get("id", "")).lower()
    return users


def sync_plan(desired, running, keep):
    """(remove, add) names for one inbound; a changed UUID is removed, then added. Users in keep are left alone."""
    want = {n: u["uuid"].lower() for n, u in desired.items() if n not in keep}
    have = {n: uid for n, uid in running.items() if n not in keep}
    remove = sorted(n for n in have if want.get(n) != have[n])
    add = sorted(n for n in want if have.get(n) != want[n])
    return remove, add


def check_plan(desired, running, remove, add, keep):
    """Refuse changes a deployment should make instead of the agent."""
    have = [n for n in running if n not in keep]
    if not [n for n in desired if n not in keep] and have:
        raise SyncError("refusing an empty user list; deploy with edge.yml if this is intended")
    dropped = set(remove) - set(add)
    if len(dropped) >= MIN_REMOVALS_REFUSED and len(dropped) * 2 > len(have):
        raise SyncError(f"refusing to remove {len(dropped)} of {len(have)} users at once; deploy with edge.yml")


def apply_tag(cfg, tag, desired, remove, add):
    node = cfg["node"]
    if remove:
        xray_api(cfg, "rmu", f"-tag={tag}", *[f"{n}.{node}" for n in remove])
    if add:
        clients = []
        for name in add:
            client = {"id": desired[name]["uuid"], "email": f"{name}.{node}", "level": 0}
            if tag == REALITY_TAG:
                client["flow"] = VISION_FLOW
            clients.append(client)
        inbound = {"tag": tag, "protocol": "vless", "settings": {"decryption": "none", "clients": clients}}
        # adu parses a complete inbound object and rejects one without port or listen.
        if tag == REALITY_TAG:
            inbound["port"] = cfg["listen_port"]
        else:
            inbound["listen"] = XHTTP_LISTEN
        xray_api(cfg, "adu", "stdin:", stdin=json.dumps({"inbounds": [inbound]}))


ONLINE_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
ONLINE_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ONLINE_NETWORKS = 64


def network_key(ip):
    """(family, network) of an address: IPv4 /24, IPv6 /48; None for anything that is not an address."""
    try:
        addr = ipaddress.ip_address(str(ip).strip("[]"))
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    prefix = 24 if addr.version == 4 else 48
    return addr.version, str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def online_networks(cfg, keep, key):
    """{user: ["4:<hash>" | "6:<hash>"]}: the networks each user has an open connection from right now.

    Xray keeps an address in a user's online list while a connection from it is open and drops it when the last one
    closes; the time next to it is when the latest connection opened, so it says nothing about being online now.
    """
    try:
        data = json.loads(xray_api(cfg, "statsgetallonlineusers") or "{}")
    except ValueError:
        raise SyncError("xray api statsgetallonlineusers: unreadable output") from None
    suffix = f".{cfg['node']}"
    out = {}
    for entry in data.get("users") or []:
        parts = str(entry).split(">>>")
        email = parts[1] if len(parts) >= 3 else str(entry)
        name = email[:-len(suffix)] if email.endswith(suffix) else None
        if not name or name in keep:
            continue
        try:
            ips = json.loads(xray_api(cfg, "statsonlineiplist", "-email", email) or "{}").get("ips") or {}
        except ValueError:
            continue
        tokens = set()
        for ip in ips:
            net = network_key(ip)
            if net is None:
                continue
            digest = hmac.new(key.encode(), net[1].encode(), hashlib.sha256).hexdigest()[:16]
            tokens.add(f"{net[0]}:{digest}")
        if tokens:
            out[name] = sorted(tokens)[:MAX_ONLINE_NETWORKS]
    return out


def post_json(url, token, doc):
    body = json.dumps(doc).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, None


class Sync:
    """Keeps the last list received from the console (memory only) and whether the node runs exactly that list."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.keep = set(cfg["sync_keep"])
        self.version = None
        self.desired = None
        self.verified = False
        self.error = ""
        self.online_key = None       # from the console; today's key for hashing networks
        self.online_day = None
        self.egress = edge_egress.Egress(cfg["xray_bin"], lambda *args, stdin=None: xray_api(cfg, *args, stdin=stdin),
                                         SyncError, log) if cfg.get("egress_enabled") else None

    def running(self):
        return {tag: running_users(self.cfg, tag) for tag in inbound_tags(self.cfg)}

    def sample_online(self):
        """This node's online networks, hashed with the last key the console sent; {} without a key or on error."""
        if not self.online_key:
            return {}
        try:
            return online_networks(self.cfg, self.keep, self.online_key)
        except SyncError as exc:
            log(f"online: {exc}")
            return {}

    def exchange(self, token, running, online=None):
        names = sorted(n for n in running.get(REALITY_TAG, {}) if n not in self.keep) if running is not None else None
        request = {"schema": SCHEMA, "node": self.cfg["node"], "applied": self.version if self.verified else None,
                   "running": names, "error": self.error[:200]}
        if online:
            request.update(online=online, online_day=self.online_day)
        if self.egress:
            request.update(self.egress.report())
        status, doc = post_json(self.cfg["sync_url"], token, request)
        if status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("version"), str):
            raise SyncError(f"console answered HTTP {status}")
        key, day = doc.get("online_key"), doc.get("online_day")
        if isinstance(key, str) and ONLINE_KEY_RE.match(key) and isinstance(day, str) and ONLINE_DAY_RE.match(day):
            self.online_key, self.online_day = key, day
        if self.egress:
            self.egress.update(doc)
        if not doc.get("unchanged"):
            users = doc.get("users")
            if not isinstance(users, list):
                raise SyncError("console sent no user list")
            self.desired = {u["name"]: u for u in users}
            self.version = doc["version"]
        elif self.desired is None:
            raise SyncError("console said unchanged but no list is held")

    def reconcile(self, running):
        """Apply the held list to every inbound; True when something was changed."""
        changed = False
        for tag in inbound_tags(self.cfg):
            remove, add = sync_plan(self.desired, running[tag], self.keep)
            check_plan(self.desired, running[tag], remove, add, self.keep)
            if remove or add:
                apply_tag(self.cfg, tag, self.desired, remove, add)
                log(f"sync {tag}: removed {len(remove)}, added {len(add)} (list {self.version})")
                changed = True
        return changed

    def tick(self, token):
        """One sync cycle; False when Xray's API is unreachable (the agent may have lost Xray's namespace)."""
        try:
            running = self.running()
        except SyncError as exc:
            self.verified, self.error = False, str(exc)
            log(f"sync: {exc}")
            return False
        try:
            self.exchange(token, running, self.sample_online())
            changed = self.reconcile(running)
            if changed:
                running = self.running()
                self.reconcile(running)            # verify: nothing should be left to change
                self.verified, self.error = True, ""
                self.exchange(token, running)       # tell the console right away what now runs here
            self.verified, self.error = True, ""
        except SyncError as exc:
            self.verified, self.error = False, str(exc)
            log(f"sync: {exc}")
        except (OSError, ValueError) as exc:
            self.verified, self.error = False, f"console unreachable: {type(exc).__name__}"
            log(f"sync: {self.error}")
        if self.egress:
            # with the last list held: a failed egress still falls back while the console is unreachable
            try:
                self.egress.check()
                self.egress.apply()
            except Exception as exc:  # noqa: BLE001 - egress must never stop the reports or the user sync
                log(f"egress: {type(exc).__name__}: {str(exc)[:200]}")
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
        "sync_enabled": env.get("SYNC_ENABLED", "false").lower() == "true",
        "sync_url": env.get("SYNC_URL", ""),
        "sync_interval": int(env.get("SYNC_INTERVAL", "60")),
        "sync_keep": [n for n in env.get("SYNC_KEEP", "").split(",") if n],
        "xhttp": env.get("XHTTP_ENABLED", "false").lower() == "true",
        "egress_enabled": env.get("EGRESS_ENABLED", "false").lower() == "true",
        "xray_bin": env.get("XRAY_BIN", "/usr/local/bin/xray"),
        "xray_api": env.get("XRAY_API", "127.0.0.1:10085"),
    }


def read_token(cfg):
    try:
        with open(cfg["token_file"]) as fh:
            return fh.read().strip()
    except OSError:
        return None


def main():
    cfg = config_from_env(os.environ)
    spool = Spool(cfg["spool_dir"], cfg["spool_max"])
    sync = Sync(cfg) if cfg["sync_enabled"] and cfg["sync_url"] else None
    log(f"node {cfg['node']}, reports every {cfg['interval']}s"
        + (f", user sync every {cfg['sync_interval']}s" if sync else ""))
    once = "--once" in sys.argv[1:]
    failures = 0
    next_report = 0.0
    while True:
        ok = True
        if time.monotonic() >= next_report:
            ok = run_once(cfg, spool)
            next_report = time.monotonic() + (cfg["interval"] if ok else min(cfg["interval"], 30))
        if sync:
            token = read_token(cfg)
            ok = (sync.tick(token) if token else True) and ok
        failures = 0 if ok else failures + 1
        if once:
            return 0 if failures == 0 else 1
        if failures >= MAX_METRICS_FAILURES:
            log("Xray unreachable repeatedly; exiting so Docker restarts the agent with Xray's network")
            return 1
        pause = min(cfg["sync_interval"], cfg["interval"]) if sync else cfg["interval"]
        time.sleep(pause if failures == 0 else min(pause, 30))


if __name__ == "__main__":
    sys.exit(main())
