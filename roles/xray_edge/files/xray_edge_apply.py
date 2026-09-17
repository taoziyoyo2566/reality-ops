#!/usr/bin/env python3
"""Converge the xray_edge instance on this node to a desired-state file.

The REALITY private key is generated and kept on the node; it never appears in
stdout, in the desired state, or in any file outside ROOT/keys and ROOT/conf.d.
Standard library only: the same code is meant to become the agent's convergence
core, with the desired state fetched instead of pushed.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

SCHEMA = 1
CONF_FILES = ("00-base.json", "10-inbounds.json", "20-outbounds.json", "30-routing.json")
REALITY_TAG = "vless-reality"
XHTTP_TAG = "vless-xhttp"
USER_INBOUND_TAGS = (REALITY_TAG, XHTTP_TAG)
XHTTP_SOCKET = "@xray-edge-xhttp"
API_LISTEN = "127.0.0.1"
API_PORT = 10085
# Read-only stats endpoint for the reporter (plan-console-phase1 §3.4); never published to the host.
METRICS_LISTEN = "127.0.0.1:10086"
CONTAINER_GID = 10000
CONTAINER_UID = 10000

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SHORT_ID_RE = re.compile(r"^(?:[0-9a-f]{2}){1,8}$")
HOST_PORT_RE = re.compile(r"^[A-Za-z0-9.-]+:[0-9]{1,5}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{1,128}$")
SOCKS_NETWORKS = ("", "tcp", "udp", "tcp,udp")
# Xray-docs sockopt.md / RFC 8305 recommended values; tryDelayMs 0 would disable racing.
HAPPY_EYEBALLS = {"tryDelayMs": 250, "prioritizeIPv6": False, "interleave": 1, "maxConcurrentTry": 4}


class ApplyError(Exception):
    """A failure the operator must see; never carries secret values."""


# --------------------------------------------------------------------------
# Validation and rendering (pure)
# --------------------------------------------------------------------------

def _require(cond, message):
    if not cond:
        raise ApplyError(message)


def _str_list(value, field):
    _require(isinstance(value, list) and all(isinstance(v, str) for v in value),
             f"{field} must be a list of strings")
    return value


def validate(desired):
    """Reject a desired state the renderer must not guess about."""
    _require(isinstance(desired, dict), "desired state must be an object")
    _require(desired.get("schema") == SCHEMA, f"unsupported schema {desired.get('schema')!r}")
    node = desired.get("node")
    _require(isinstance(node, str) and NAME_RE.match(node), "node must be a valid name")

    reality = desired.get("reality") or {}
    _require(isinstance(reality.get("target"), str) and HOST_PORT_RE.match(reality["target"]),
             "reality.target must be host:port")
    # xray run -test accepts an out-of-range port here, which silently breaks the REALITY fallback.
    _require(1 <= int(reality["target"].rsplit(":", 1)[1]) <= 65535, "reality.target port must be 1-65535")
    names = _str_list(reality.get("server_names"), "reality.server_names")
    _require(len(names) > 0, "reality.server_names must not be empty")

    port = (desired.get("listen") or {}).get("port")
    _require(isinstance(port, int) and 1 <= port <= 65535, "listen.port must be 1-65535")

    xhttp = desired.get("xhttp") or {}
    _require(isinstance(xhttp.get("enabled"), bool), "xhttp.enabled must be a boolean")
    if xhttp["enabled"]:
        _require(isinstance(xhttp.get("path"), str) and PATH_RE.match(xhttp["path"]),
                 "xhttp.path must be set when xhttp is enabled")

    users = desired.get("users")
    _require(isinstance(users, list), "users must be a list")
    seen_names, seen_ids = set(), set()
    for user in users:
        name = user.get("name")
        _require(isinstance(name, str) and NAME_RE.match(name),
                 f"invalid user name {name!r} (letters, digits, '_' and '-' only)")
        _require(name not in seen_names, f"duplicate user {name}")
        _require(isinstance(user.get("uuid"), str) and UUID_RE.match(user["uuid"]),
                 f"user {name} has an invalid uuid")
        _require(user["uuid"].lower() not in seen_ids, f"user {name} reuses another user's uuid")
        _require(isinstance(user.get("short_id"), str) and SHORT_ID_RE.match(user["short_id"]),
                 f"user {name} has an invalid short_id")
        seen_names.add(name)
        seen_ids.add(user["uuid"].lower())

    socks5 = desired.get("socks5", [])
    _require(isinstance(socks5, list), "socks5 must be a list")
    seen_profiles = set()
    for profile in socks5:
        pname = profile.get("name")
        _require(isinstance(pname, str) and NAME_RE.match(pname), f"invalid socks5 profile name {pname!r}")
        _require(pname not in seen_profiles, f"duplicate socks5 profile {pname}")
        seen_profiles.add(pname)
        _require(isinstance(profile.get("address"), str) and profile["address"],
                 f"socks5 {pname} needs an address")
        _require(isinstance(profile.get("port"), int) and 1 <= profile["port"] <= 65535,
                 f"socks5 {pname} needs a port 1-65535")
        route_users = _str_list(profile.get("users", []), f"socks5 {pname} users")
        missing = sorted(set(route_users) - seen_names)
        _require(not missing, f"socks5 {pname} routes users not on this node: {missing}")
        conditions = [route_users]
        for key in ("domains", "ips", "protocols"):
            conditions.append(_str_list(profile.get(key, []), f"socks5 {pname} {key}"))
        # D12: an outbound without a rule would only carry credentials; refuse it.
        _require(any(conditions), f"socks5 {pname} has no route condition")
        _require(profile.get("network", "") in SOCKS_NETWORKS, f"socks5 {pname} has an invalid network")

    metrics = desired.get("metrics", {})
    _require(isinstance(metrics, dict) and isinstance(metrics.get("enabled", False), bool),
             "metrics.enabled must be a boolean")

    egress = desired.get("egress", {})
    _require(isinstance(egress, dict) and isinstance(egress.get("happy_eyeballs", False), bool),
             "egress.happy_eyeballs must be a boolean")

    level = (desired.get("log") or {}).get("level", "warning")
    _require(level in ("debug", "info", "warning", "error", "none"), "log.level is invalid")
    return desired


def _email(user, node):
    return f"{user['name']}.{node}"


def _sniffing():
    return {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}


def render(desired, private_key):
    """Return {filename: document} for conf.d. Pure: no I/O."""
    validate(desired)
    node = desired["node"]
    users = sorted(desired["users"], key=lambda u: u["name"])
    xhttp = desired["xhttp"]
    socks5 = sorted(desired.get("socks5", []), key=lambda p: (p.get("priority", 100), p["name"]))

    base = {
        "log": {
            "loglevel": (desired.get("log") or {}).get("level", "warning"),
            "access": "/var/log/xray/access.log",
            "error": "/var/log/xray/error.log",
        },
        "api": {"tag": "api", "services": ["HandlerService", "StatsService", "LoggerService", "RoutingService"]},
        "stats": {},
        "policy": {
            # Same connection timeouts as the existing single/multi configs, so the
            # canary compares data plane shape rather than timeout behaviour.
            "levels": {"0": {
                "handshake": 4, "connIdle": 300, "uplinkOnly": 0, "downlinkOnly": 0, "bufferSize": 0,
                "statsUserUplink": True, "statsUserDownlink": True, "statsUserOnline": True,
            }},
            "system": {
                "statsInboundUplink": True, "statsInboundDownlink": True,
                "statsOutboundUplink": True, "statsOutboundDownlink": True,
            },
        },
    }
    if (desired.get("metrics") or {}).get("enabled"):
        base["metrics"] = {"listen": METRICS_LISTEN}

    reality_settings = {"decryption": "none", "clients": [
        {"id": u["uuid"], "email": _email(u, node), "flow": "xtls-rprx-vision", "level": 0} for u in users
    ]}
    if xhttp["enabled"]:
        # xver 1 carries the client address through the unix socket; without it
        # every XHTTP connection is logged from 0.0.0.0 and online-IP stats are lost.
        reality_settings["fallbacks"] = [{"dest": XHTTP_SOCKET, "xver": 1}]

    inbounds = [
        {"tag": "api", "listen": API_LISTEN, "port": API_PORT, "protocol": "dokodemo-door",
         "settings": {"address": API_LISTEN}},
        {
            "tag": REALITY_TAG,
            "port": desired["listen"]["port"],
            "protocol": "vless",
            "settings": reality_settings,
            "streamSettings": {
                "network": "raw",
                "security": "reality",
                "realitySettings": {
                    "show": False,
                    "target": desired["reality"]["target"],
                    "xver": 0,
                    "serverNames": list(desired["reality"]["server_names"]),
                    "privateKey": private_key,
                    "shortIds": sorted({u["short_id"] for u in users}),
                },
            },
            "sniffing": _sniffing(),
        },
    ]
    if xhttp["enabled"]:
        inbounds.append({
            "tag": XHTTP_TAG,
            "listen": XHTTP_SOCKET,
            "protocol": "vless",
            "settings": {"decryption": "none", "clients": [
                {"id": u["uuid"], "email": _email(u, node), "level": 0} for u in users
            ]},
            "streamSettings": {
                "network": "xhttp",
                "security": "none",
                "xhttpSettings": {"path": xhttp["path"], "mode": xhttp.get("mode", "auto")},
                "sockopt": {"acceptProxyProtocol": True},
            },
            "sniffing": _sniffing(),
        })

    direct = {"tag": "direct", "protocol": "freedom", "settings": {}}
    if (desired.get("egress") or {}).get("happy_eyeballs"):
        # happyEyeballs only applies when domainStrategy is not AsIs; UseIP keeps both
        # families so IPv4 and IPv6 race instead of AsIs's IPv4-first ordering.
        direct["streamSettings"] = {"sockopt": {"domainStrategy": "UseIP", "happyEyeballs": dict(HAPPY_EYEBALLS)}}
    outbounds = [
        direct,
        {"tag": "blocked", "protocol": "blackhole", "settings": {}},
    ]
    rules = [
        {"type": "field", "ruleTag": "api", "inboundTag": ["api"], "outboundTag": "api"},
        {"type": "field", "ruleTag": "block-bt", "protocol": ["bittorrent"], "outboundTag": "blocked"},
        {"type": "field", "ruleTag": "block-private", "ip": ["geoip:private"], "outboundTag": "blocked"},
    ]
    by_name = {u["name"]: u for u in users}
    for profile in socks5:
        tag = f"socks5-{profile['name']}"
        server = {"address": profile["address"], "port": profile["port"]}
        if profile.get("username") or profile.get("password"):
            server["user"] = profile.get("username", "")
            server["pass"] = profile.get("password", "")
        outbounds.append({"tag": tag, "protocol": "socks", "settings": server})
        rule = {"type": "field", "ruleTag": tag, "outboundTag": tag}
        if profile.get("users"):
            rule["user"] = sorted(_email(by_name[n], node) for n in profile["users"])
        for src, dst in (("domains", "domain"), ("ips", "ip"), ("protocols", "protocol")):
            if profile.get(src):
                rule[dst] = list(profile[src])
        if profile.get("network"):
            rule["network"] = profile["network"]
        rules.append(rule)
    # Explicit last rule: outbound ordering across merged files no longer decides the default exit.
    rules.append({"type": "field", "ruleTag": "default", "network": "tcp,udp", "outboundTag": "direct"})

    return {
        "00-base.json": base,
        "10-inbounds.json": {"inbounds": inbounds},
        "20-outbounds.json": {"outbounds": outbounds},
        "30-routing.json": {"routing": {"domainStrategy": "IPIfNonMatch", "rules": rules}},
    }


def user_clients(files):
    """{inbound tag: {email: client}} for the inbounds that carry users."""
    result = {}
    for inbound in (files.get("10-inbounds.json") or {}).get("inbounds", []):
        if inbound.get("tag") in USER_INBOUND_TAGS:
            clients = (inbound.get("settings") or {}).get("clients", [])
            result[inbound["tag"]] = {c["email"]: c for c in clients}
    return result


def _without_clients(files):
    stripped = copy.deepcopy(files)
    for inbound in (stripped.get("10-inbounds.json") or {}).get("inbounds", []):
        if inbound.get("tag") in USER_INBOUND_TAGS:
            inbound.get("settings", {}).pop("clients", None)
        if inbound.get("tag") == REALITY_TAG:
            # shortIds follow the user list; a user change alone must stay an API change.
            inbound.get("streamSettings", {}).get("realitySettings", {}).pop("shortIds", None)
    return stripped


def classify(live, rendered):
    """'none', 'api' (only users differ) or 'restart'."""
    if live == rendered:
        return "none"
    if not live or set(live) != set(rendered):
        return "restart"
    if _without_clients(live) != _without_clients(rendered):
        return "restart"
    # REALITY shortIds cannot be changed through the API: a new short id needs a restart,
    # while ids left over from removed users are harmless until the next restart.
    if not set(_short_ids(rendered)) <= set(_short_ids(live)):
        return "restart"
    return "api"


def _reality_inbound(files):
    for inbound in (files.get("10-inbounds.json") or {}).get("inbounds", []):
        if inbound.get("tag") == REALITY_TAG:
            return inbound
    return {}


def _short_ids(files):
    return _reality_inbound(files).get("streamSettings", {}).get("realitySettings", {}).get("shortIds", [])


def user_diff(live, rendered):
    """Per inbound tag: (emails to remove, clients to add). A changed client is removed then added."""
    old, new = user_clients(live), user_clients(rendered)
    plan = {}
    for tag in USER_INBOUND_TAGS:
        before, after = old.get(tag, {}), new.get(tag, {})
        remove = sorted(e for e in before if e not in after or before[e] != after[e])
        add = [after[e] for e in sorted(after) if e not in before or before[e] != after[e]]
        if remove or add:
            plan[tag] = (remove, add)
    return plan


# --------------------------------------------------------------------------
# Node side effects
# --------------------------------------------------------------------------

KEY_LIKE_RE = re.compile(r"[A-Za-z0-9_+/=-]{40,}")


def _run(cmd, *, input_text=None, check=True, timeout=120):
    proc = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        # Keep the lines that say why it failed; config errors can quote the file, so mask anything key-shaped.
        lines = [l for l in (proc.stdout + "\n" + proc.stderr).strip().splitlines() if l.strip()]
        reasons = [l for l in lines if re.search(r"fail|error|invalid", l, re.I)]
        tail = " | ".join((reasons or lines)[-3:])
        raise ApplyError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed: {KEY_LIKE_RE.sub('<redacted>', tail)}")
    return proc


class Node:
    def __init__(self, root, container, image, test_perms=False):
        self.root = root
        self.container = container
        self.image = image
        self.test_perms = test_perms
        self.conf = os.path.join(root, "conf.d")
        self.last_good = os.path.join(root, "last-good")
        self.keys = os.path.join(root, "keys")
        self.secrets = os.path.join(root, "secrets")
        if not test_perms and os.geteuid() != 0:
            raise ApplyError("must run as root (use --test-perms only for local tests)")

    # -- permissions -------------------------------------------------------
    def _secure_dir(self, path, container_readable):
        os.makedirs(path, exist_ok=True)
        if self.test_perms:
            os.chmod(path, 0o755 if container_readable else 0o700)
            return
        os.chown(path, 0, CONTAINER_GID if container_readable else 0)
        os.chmod(path, 0o750 if container_readable else 0o700)

    def _secure_file(self, path, container_readable):
        if self.test_perms:
            os.chmod(path, 0o644 if container_readable else 0o600)
            return
        os.chown(path, 0, CONTAINER_GID if container_readable else 0)
        os.chmod(path, 0o640 if container_readable else 0o600)

    # -- keys --------------------------------------------------------------
    def keypair(self):
        self._secure_dir(self.keys, container_readable=False)
        path = os.path.join(self.keys, "reality.json")
        if os.path.exists(path):
            with open(path) as fh:
                data = json.load(fh)
            return data["private_key"], data["public_key"]
        out = _run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "xray",
                    self.image, "x25519"]).stdout
        private = re.search(r"Private ?[Kk]ey:\s*(\S+)", out)
        public = re.search(r"(?:Public ?[Kk]ey|Password(?: \(PublicKey\))?):\s*(\S+)", out)
        if not private or not public:
            raise ApplyError("could not parse xray x25519 output")
        fd, tmp = tempfile.mkstemp(dir=self.keys)
        with os.fdopen(fd, "w") as fh:
            json.dump({"private_key": private.group(1), "public_key": public.group(1)}, fh)
        self._secure_file(tmp, container_readable=False)
        os.replace(tmp, path)
        return private.group(1), public.group(1)

    def report_token_digest(self, rotate=False, create=True):
        """SHA-256 of the node's report token; the token itself never leaves this file.

        apply creates the token when missing (or replaces it when rotating); verify only reads it.
        """
        path = os.path.join(self.secrets, "report-token")
        if not create and not os.path.exists(path):
            return None
        self._secure_dir(self.secrets, container_readable=True)
        if rotate or not os.path.exists(path):
            fd, tmp = tempfile.mkstemp(dir=self.secrets)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_urlsafe(32) + "\n")
            self._secure_file(tmp, container_readable=True)
            os.replace(tmp, path)
        with open(path) as fh:
            token = fh.read().strip()
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    # -- files -------------------------------------------------------------
    def read_live(self):
        files = {}
        for name in CONF_FILES:
            path = os.path.join(self.conf, name)
            if os.path.exists(path):
                with open(path) as fh:
                    files[name] = json.load(fh)
        return files

    def write_dir(self, path, files):
        self._secure_dir(path, container_readable=True)
        for name, doc in files.items():
            fd, tmp = tempfile.mkstemp(dir=path, prefix=f".{name}.")
            with os.fdopen(fd, "w") as fh:
                json.dump(doc, fh, indent=2, sort_keys=True)
                fh.write("\n")
            self._secure_file(tmp, container_readable=True)
            os.replace(tmp, os.path.join(path, name))

    def test_config(self, directory):
        _run(["docker", "run", "--rm", "--network", "none", "--user", f"{CONTAINER_UID}:{CONTAINER_GID}",
              "--entrypoint", "xray", "-v", f"{directory}:/etc/xray/conf.d:ro", self.image,
              "run", "-test", "-confdir", "/etc/xray/conf.d"])

    def install(self, files):
        """Replace conf.d file by file inside the same directory, keeping the bind mount valid."""
        live = self.read_live()
        if live:
            if os.path.isdir(self.last_good):
                shutil.rmtree(self.last_good)
            self.write_dir(self.last_good, live)
            self._secure_dir(self.last_good, container_readable=False)
        self.write_dir(self.conf, files)
        for name in os.listdir(self.conf):
            if name.endswith(".json") and name not in files:
                os.remove(os.path.join(self.conf, name))

    def restore_last_good(self):
        if not os.path.isdir(self.last_good):
            return False
        files = {}
        for name in CONF_FILES:
            path = os.path.join(self.last_good, name)
            if os.path.exists(path):
                with open(path) as fh:
                    files[name] = json.load(fh)
        self.write_dir(self.conf, files)
        return True

    # -- container ---------------------------------------------------------
    def state(self):
        proc = _run(["docker", "inspect", "-f", "{{.State.Running}} {{.RestartCount}}", self.container], check=False)
        if proc.returncode != 0:
            return None
        running, restarts = proc.stdout.split()
        return {"running": running == "true", "restarts": int(restarts)}

    def api(self, command, *args, input_text=None, check=True):
        # Go flag parsing stops at the first positional argument, so -s must precede emails or files.
        return _run(["docker", "exec", "-i", self.container, "xray", "api", command,
                     "-s", f"{API_LISTEN}:{API_PORT}", *args], input_text=input_text, check=check)

    def wait_api(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.api("lsi", check=False).returncode == 0:
                return
            time.sleep(1)
        raise ApplyError("xray API did not become ready")

    def restart(self):
        _run(["docker", "restart", self.container])
        self.wait_api()

    def apply_users(self, plan, rendered):
        for tag, (remove, add) in plan.items():
            if remove:
                self.api("rmu", f"-tag={tag}", *remove)
            if add:
                inbound = {"tag": tag, "protocol": "vless", "settings": {"decryption": "none", "clients": add}}
                # adu parses a complete inbound object and rejects one without port or listen.
                if tag == REALITY_TAG:
                    inbound["port"] = _reality_inbound(rendered)["port"]
                else:
                    inbound["listen"] = XHTTP_SOCKET
                self.api("adu", "stdin:", input_text=json.dumps({"inbounds": [inbound]}))

    def live_emails(self, tag):
        out = self.api("inbounduser", f"-tag={tag}").stdout
        data = json.loads(out or "{}")
        return sorted(u.get("email", "") for u in data.get("users", []))


def verify(node, rendered):
    status = node.state()
    if not status or not status["running"]:
        raise ApplyError(f"container {node.container} is not running")
    node.wait_api()
    expected = {tag: sorted(clients) for tag, clients in user_clients(rendered).items()}
    for tag, emails in expected.items():
        actual = node.live_emails(tag)
        if actual != emails:
            raise ApplyError(f"{tag}: running users differ from desired state "
                             f"(missing={sorted(set(emails) - set(actual))}, extra={sorted(set(actual) - set(emails))})")
    return status


def load_desired(path):
    with open(path) as fh:
        return validate(json.load(fh))


def cmd_apply(args):
    node = Node(args.root, args.container, args.image, args.test_perms)
    desired = load_desired(args.desired)
    private_key, public_key = node.keypair()
    report_digest = node.report_token_digest(rotate=args.rotate_report_token)
    rendered = render(desired, private_key)
    live = node.read_live()
    action = classify(live, rendered)
    result = {"changed": action != "none" or args.rotate_report_token, "action": action, "public_key": public_key,
              "report_token_sha256": report_digest,
              "users": len(desired["users"]), "xhttp": desired["xhttp"]["enabled"]}
    if action == "none":
        return result

    candidate = tempfile.mkdtemp(dir=args.root, prefix=".candidate-")
    try:
        node.write_dir(candidate, rendered)
        node.test_config(candidate)
    finally:
        shutil.rmtree(candidate, ignore_errors=True)

    status = node.state()
    node.install(rendered)
    if not status or not status["running"]:
        result["action"] = "written"
        return result
    try:
        if action == "api":
            try:
                node.apply_users(user_diff(live, rendered), rendered)
            except ApplyError:
                # Files already match the desired state; a restart makes the runtime match them.
                result["action"] = action = "restart"
        if action == "restart":
            node.restart()
        verify(node, rendered)
    except ApplyError:
        if node.restore_last_good():
            node.restart()
        raise
    return result


def cmd_verify(args):
    node = Node(args.root, args.container, args.image, args.test_perms)
    desired = load_desired(args.desired)
    private_key, public_key = node.keypair()
    status = verify(node, render(desired, private_key))
    return {"changed": False, "running": status["running"], "restarts": status["restarts"],
            "public_key": public_key, "report_token_sha256": node.report_token_digest(create=False)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("apply", "verify"))
    parser.add_argument("--root", default="/opt/xray-edge")
    parser.add_argument("--desired", default=None)
    parser.add_argument("--container", default="xray_edge")
    parser.add_argument("--image", required=True)
    parser.add_argument("--rotate-report-token", action="store_true",
                        help="apply only: replace the node's report token (re-run edge.yml so the console learns the new hash)")
    parser.add_argument("--test-perms", action="store_true",
                        help="local tests only: world-readable conf.d instead of root:10000 ownership")
    args = parser.parse_args(argv)
    args.desired = args.desired or os.path.join(args.root, "state", "desired.json")
    try:
        result = cmd_apply(args) if args.command == "apply" else cmd_verify(args)
    except (ApplyError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"failed": True, "msg": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
