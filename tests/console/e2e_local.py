#!/usr/bin/env python3
"""Local end-to-end check of the console phase 1 against real containers (plan-console-phase1 §6.1 item 3).

One xray_edge node (compose, applier, reporter), the console (web and report services) and the subscription
service run on this host. The check registers the node the way edge.yml does, issues a subscription in the
admin pages, connects a real Xray client with the published link, and follows the traffic into the console.
It also restarts Xray (the reporter must rejoin its network), rotates the report token, hides the node and
revokes the subscription.

Needs Docker with Compose and outbound HTTPS. Run with a Python that has Jinja2 and PyYAML:
  monitor_venv/bin/python tests/console/e2e_local.py [--keep]
Creates containers, compose projects, image tags named *console-e2e* and a temporary directory. On exit it
removes the containers, projects and directory (files owned by container users through a root container) and keeps
the image tags for the next run.
"""
import argparse
import base64
import importlib.util
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_compose as console_compose  # noqa: E402
from subs import links  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str((REPO / path).parent))
    spec.loader.exec_module(module)
    return module


edge = load("xray_edge_apply", "roles/xray_edge/files/xray_edge_apply.py")
edge_compose = load("edge_test_compose", "tests/edge/test_compose.py")

IMAGE = edge_compose.GROUP_VARS["edge_xray_image"]
TOOLS = "reality-edge-tools:console-e2e"
CONSOLE = "reality-console:console-e2e"
SUBS = "reality-subs:console-e2e"
TARGET = os.environ.get("E2E_TARGET", "www.costco.com")
CHECK_URL = os.environ.get("E2E_URL", "https://www.cloudflare.com/cdn-cgi/trace")
NODE = "e2e"
SRV = "console-e2e-xray"
CLI = "console-e2e-client"
EDGE_PROJECT = "console-e2e-edge"
CONSOLE_PROJECT = "console-e2e"
SUBS_PROJECT = "console-e2e-subs"
INTERVAL = 5
USERS = {
    "alice": {"name": "alice", "uuid": "11111111-1111-4111-8111-111111111111", "short_id": "a1a1a1a1"},
    "bob": {"name": "bob", "uuid": "22222222-2222-4222-8222-222222222222", "short_id": "a1a1a1a1"},
}

results = []


def run(cmd, check=True, **kw):
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd[:6]))} failed: {proc.stderr.strip()[-500:]}")
    return proc


def record(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + str(detail) if detail else ''}", flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def inspect(name, fmt):
    return run(["docker", "inspect", "-f", fmt, name], check=False).stdout.strip()


def ip_of(name, network="bridge"):
    return inspect(name, "{{(index .NetworkSettings.Networks \"%s\").IPAddress}}" % network)


def http(method, url, body=None, headers=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except OSError as exc:
        return 0, str(exc)


def wait_for(predicate, timeout, step=1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


class Layout:
    def __init__(self, root):
        self.root = root
        self.node = os.path.join(root, "node")
        self.console = os.path.join(root, "console")
        self.subs = os.path.join(root, "subs")
        self.web_port = free_port()
        self.subs_port = free_port()
        self.client_port = free_port()

    def as_root(self, script, stdin=None, check=True):
        return run(["docker", "run", "--rm", "-i", "--network", "none", "-v", f"{self.root}:{self.root}",
                    "-w", self.root, TOOLS, "sh", "-c", script], input=stdin, check=check)

    def put(self, path, content, owner="0:0", mode="0600"):
        self.as_root(f"cat > '{path}' && chown {owner} '{path}' && chmod {mode} '{path}'", stdin=content)

    def prepare(self):
        n, c, s = self.node, self.console, self.subs
        self.as_root(
            "set -e; chmod 0755 .;"
            f" install -d -m 0755 {n}; install -d -m 0700 {n}/state {n}/keys;"
            f" install -d -o 0 -g 10000 -m 0750 {n}/conf.d; install -d -o 10000 -g 10000 -m 0750 {n}/logs;"
            f" install -d -m 0755 {n}/rotate; install -d -o 10000 -g 10000 -m 0700 {n}/rotate/state {n}/spool;"
            f" install -d -m 0755 {c}; install -d -o 0 -g 10002 -m 0750 {c}/data {c}/registry;"
            f" install -d -o 10002 -g 10002 -m 0700 {c}/db {c}/import; install -d -m 0700 {c}/secrets;"
            f" install -d -m 0755 {s}; install -d -o 10002 -g 10001 -m 2750 {s}/data;"
            f" install -d -o 10001 -g 10002 -m 0750 {s}/db; install -d -m 0700 {s}/secrets")
        self.put(f"{c}/secrets/tunnel.env", "TUNNEL_TOKEN=unused\n")
        self.put(f"{c}/data/users.json", json.dumps({"users": [
            {"name": u, "groups": ["free"], "hosts": [], "deny_hosts": []} for u in USERS]}), "0:10002", "0640")
        self.put(f"{self.node}/rotate/logrotate.conf", "", mode="0644")
        subs_compose = console_compose.render_subs(
            subs_root_dir=s, subs_tunnel_enabled=False, subs_host_port=self.subs_port,
            subs_compose_project=SUBS_PROJECT, subs_container_name="console_e2e_subs", subs_image=SUBS,
            subs_public_base_url="https://sub.example.test")
        self.put(f"{s}/compose.yaml", subs_compose, mode="0644")
        # The subscription service listens on 8100 inside its container; publish it on a free host port.
        console = console_compose.render(
            console_root_dir=c, console_registry_dir=f"{c}/registry", subs_root_dir=s,
            console_web_port=self.web_port, console_allowed_hosts=[f"127.0.0.1:{self.web_port}"],
            console_compose_project=CONSOLE_PROJECT, console_container_name="console_e2e", console_image=CONSOLE,
            subs_public_base_url="https://sub.example.test")
        self.put(f"{c}/compose.yaml", console, mode="0644")

    def node_compose_file(self, report_url):
        text = edge_compose.render(NODE, [], edge_root_dir=self.node, edge_container_name=SRV,
                                   edge_compose_project=EDGE_PROJECT, edge_tools_image=TOOLS, edge_xray_image=IMAGE,
                                   edge_report_enabled=True, edge_report_url=report_url,
                                   edge_report_interval=INTERVAL)
        self.put(f"{self.node}/compose.yaml", text, mode="0644")

    def compose(self, where, *args, check=True):
        return run(["docker", "compose", "-f", os.path.join(where, "compose.yaml"), *args], check=check)

    def apply(self, users, command="apply", extra=()):
        state = {"schema": 1, "node": NODE, "reality": {"target": f"{TARGET}:443", "server_names": [TARGET]},
                 "listen": {"port": 443}, "xhttp": {"enabled": False, "path": "", "mode": "auto"},
                 "users": [USERS[u] for u in users], "socks5": [], "metrics": {"enabled": True},
                 "log": {"level": "info"}}
        self.put(f"{self.node}/state/desired.json", json.dumps(state))
        proc = self.compose(self.node, "run", "--rm", "-T", "--pull", "never", "applier", command,
                            "--root", self.node, "--container", SRV, "--image", IMAGE, *extra, check=False)
        try:
            return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            return proc.returncode, {"stderr": proc.stderr[-300:]}

    def register(self, users, public_key, digest, endpoint):
        doc = {"schema": 1, "node": NODE, "label": "e2e [test]", "endpoint": endpoint, "port": 443, "sni": TARGET,
               "public_key": public_key, "xhttp": {"enabled": False, "path": "", "mode": "auto"},
               "users": [USERS[u] for u in users], "report": {"enabled": True, "token_sha256": digest},
               "image": IMAGE}
        self.put(f"{self.console}/registry/{NODE}.json", json.dumps(doc), "0:10002", "0640")

    def console_query(self, code):
        # docker exec, not compose exec: compose would read the root-only tunnel env file as this user.
        proc = run(["docker", "exec", "console_e2e_web", "python", "-c", code], check=False)
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            return None

    def remove(self):
        for where in (self.node, self.console, self.subs):
            if os.path.exists(os.path.join(where, "compose.yaml")):
                self.compose(where, "--profile", "tools", "down", "--remove-orphans", check=False)
        run(["docker", "rm", "-f", CLI, SRV, f"{SRV}_logrotate", f"{SRV}_reporter"], check=False)
        self.as_root("rm -rf ./* ./.[!.]*", check=False)


class Admin:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.host = f"127.0.0.1:{port}"

    def get(self, path, host=None):
        return http("GET", self.base + path, headers={"Host": host or self.host})

    def post(self, path, **fields):
        status, page = self.get("/")
        csrf = re.search(r'name="csrf" value="([0-9a-f]{64})"', page).group(1)
        body = urllib.parse.urlencode(dict(fields, csrf=csrf)).encode()
        return http("POST", self.base + path, body,
                    {"Host": self.host, "Content-Type": "application/x-www-form-urlencoded"})[0]

    def token(self, user):
        match = re.search(r'value="https://sub\.example\.test/s/([A-Za-z0-9_-]{43})"', self.get(f"/users/{user}")[1])
        return match.group(1) if match else None


def subscription(port, token, fmt="v2ray"):
    status, body = http("GET", f"http://127.0.0.1:{port}/s/{token}/{fmt}")
    links_ = base64.b64decode(body).decode().split() if status == 200 else []
    return status, links_


def start_client(layout, link):
    run(["docker", "rm", "-f", CLI], check=False)
    p = links.parse_vless(link)
    config = {"log": {"loglevel": "warning"},
              "inbounds": [{"listen": "0.0.0.0", "port": 1080, "protocol": "socks", "settings": {}}],
              "outbounds": [{"protocol": "vless", "settings": {"vnext": [{
                  "address": p["server"], "port": p["port"],
                  "users": [{"id": p["uuid"], "encryption": "none", "flow": p["flow"]}]}]},
                  "streamSettings": {"network": "raw", "security": "reality", "realitySettings": {
                      "serverName": p["sni"], "fingerprint": p["fp"], "publicKey": p["public_key"],
                      "shortId": p["short_id"]}}}]}
    path = os.path.join(layout.root, "client.json")
    with open(path, "w") as fh:
        json.dump(config, fh)
    os.chmod(path, 0o644)
    run(["docker", "run", "-d", "--name", CLI, "-p", f"127.0.0.1:{layout.client_port}:1080",
         "-v", f"{path}:/config.json:ro", IMAGE])
    time.sleep(2)


def fetch(layout):
    return run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "20",
                "-x", f"socks5h://127.0.0.1:{layout.client_port}", CHECK_URL], check=False).stdout.strip()


TRAFFIC = ("import json; from console import config, db, queries; s = config.from_env();\n"
           "with db.connect(s.db_path) as c: print(json.dumps({'users': queries.traffic_by_user(c, queries.this_month()),"
           " 'status': queries.node_status(c)}))")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    for tag, dockerfile in ((TOOLS, "docker/edge-tools/Dockerfile"), (CONSOLE, "docker/console/Dockerfile"),
                            (SUBS, "docker/subs/Dockerfile")):
        run(["docker", "build", "-q", "-f", dockerfile, "-t", tag, "."], cwd=REPO)
    run(["docker", "rm", "-f", CLI, SRV, f"{SRV}_logrotate", f"{SRV}_reporter"], check=False)

    layout = Layout(tempfile.mkdtemp(prefix="console-e2e-"))
    admin = Admin(layout.web_port)
    try:
        layout.prepare()
        layout.compose(layout.subs, "up", "-d")
        layout.compose(layout.console, "up", "-d", "web", "report")
        run(["docker", "network", "connect", "bridge", "console_e2e_report"])
        report_ip = ip_of("console_e2e_report")
        report_url = f"http://{report_ip}:8201/report"
        record("console web and report services start",
               wait_for(lambda: admin.get("/healthz")[0] == 200, 30)
               and wait_for(lambda: http("GET", f"http://{report_ip}:8201/healthz")[0] == 200, 30))

        layout.node_compose_file(report_url)
        rc, out = layout.apply(["alice", "bob"])
        record("applier writes config with the metrics listener", rc == 0 and out.get("action") == "written", out)
        base = json.loads(layout.as_root(f"cat {layout.node}/conf.d/00-base.json").stdout)
        record("metrics listen only inside the container", base.get("metrics") == {"listen": "127.0.0.1:10086"})
        layout.compose(layout.node, "up", "-d")
        rc, out = layout.apply(["alice", "bob"], "verify")
        record("verify returns the public key and report token digest",
               rc == 0 and re.match(r"^[0-9a-f]{64}$", out.get("report_token_sha256") or ""), out)
        public_key, digest = out.get("public_key"), out.get("report_token_sha256")
        server_ip = ip_of(SRV)
        layout.register(["alice", "bob"], public_key, digest, server_ip)

        reporter = f"{SRV}_reporter"
        mounts = inspect(reporter, "{{json .Mounts}}")
        record("reporter runs as 10000 on a read-only root without the Docker socket",
               inspect(reporter, "{{.Config.User}}") == "10000:10000"
               and inspect(reporter, "{{.HostConfig.ReadonlyRootfs}}") == "true" and "docker.sock" not in mounts)
        record("reporter shares the xray network namespace",
               inspect(reporter, "{{.HostConfig.NetworkMode}}").startswith("container:"))

        # First initialization: no tokens yet, the node shown (console.admin import-initial).
        layout.put(f"{layout.console}/import/initial.json", json.dumps({"tokens": {}, "shown": [NODE]}),
                   "10002:10002", "0600")
        proc = layout.compose(layout.console, "run", "--rm", "-T", "--no-deps", "web",
                              "python", "-m", "console.admin", "import-initial", "/import/initial.json", check=False)
        record("initial import publishes the shown node", proc.returncode == 0 and '"published": true' in proc.stdout,
               proc.stdout.strip()[-200:])

        record("admin pages refuse other Host headers", admin.get("/", host="evil.example")[0] == 400)
        for path in ("/", "/users", "/nodes"):
            record(f"report service has no admin route {path}", http("GET", f"http://{report_ip}:8201{path}")[0] == 404)

        record("issuing alice's subscription in the admin page", admin.post("/users/alice/issue") == 303)
        token = admin.token("alice")
        status, published = subscription(layout.subs_port, token)
        record("subscription service serves the new token with the node's link",
               status == 200 and len(published) == 1 and f"@{server_ip}:443" in published[0], f"{status} {len(published)}")
        start_client(layout, published[0])
        record("client connects with the published link", fetch(layout) == "200")

        def alice_traffic():
            data = layout.console_query(TRAFFIC) or {}
            t = (data.get("users") or {}).get("alice")
            return t if t and t["down"] > 0 else None
        traffic = wait_for(alice_traffic, INTERVAL * 6, 2)
        record("reporter delivers alice's traffic to the console", traffic, traffic)
        data = layout.console_query(TRAFFIC) or {}
        record("node status shows 443 listening", ((data.get("status") or {}).get(NODE) or {})
               .get("report", {}).get("listening") is True)
        status, page = admin.get("/users")
        record("users page shows the subscription fetch from the service's access log",
               status == 200 and "v2ray" in page and "alice" in page)
        status, page = admin.get(f"/nodes/{NODE}")
        record("node page shows the node online with alice's traffic", status == 200 and "监听中" in page and "alice" in page)

        # Xray restart: the reporter loses the namespace and must come back in the new one.
        before = (data.get("status") or {}).get(NODE, {}).get("received_at", 0)
        restarts_before = int(inspect(reporter, "{{.RestartCount}}") or 0)
        run(["docker", "restart", SRV])
        time.sleep(3)
        start_client(layout, published[0])
        fetch(layout)

        def fresh_report():
            d = layout.console_query(TRAFFIC) or {}
            st = (d.get("status") or {}).get(NODE) or {}
            return st if st.get("received_at", 0) > before + INTERVAL and st.get("xray_restart_at") else None
        st = wait_for(fresh_report, 120, 3)
        record("reporter rejoins after Xray restarts and reports the restart", st,
               f"reporter restarts {restarts_before} -> {inspect(reporter, '{{.RestartCount}}')}")

        # Report token rotation: the old token stops working, the reporter continues with the new one.
        old_token = layout.as_root(f"cat {layout.node}/secrets/report-token").stdout.strip()
        rc, out = layout.apply(["alice", "bob"], extra=("--rotate-report-token",))
        record("rotating the report token changes its digest", rc == 0 and out.get("report_token_sha256") != digest, out)
        layout.register(["alice", "bob"], public_key, out.get("report_token_sha256"), server_ip)
        probe = json.dumps({"schema": 1, "node": NODE, "instance": "0" * 16, "seq": 1,
                            "period": {"from": None, "to": "2026-01-01T00:00:00Z"}, "traffic": {},
                            "listening": True, "xray_restarted": False, "baseline": True}).encode()
        code = wait_for(lambda: http("POST", report_url, probe, {"Authorization": f"Bearer {old_token}",
                                                                 "Content-Type": "application/json"})[0] == 401, 10)
        record("the old report token is refused", code)
        after_rotate = time.time()

        def report_after_rotation():
            d = layout.console_query(TRAFFIC) or {}
            st = (d.get("status") or {}).get(NODE) or {}
            return st.get("received_at", 0) > after_rotate + 1
        record("reporter keeps reporting with the rotated token", wait_for(report_after_rotation, INTERVAL * 6, 2))
        logs = run(["docker", "logs", reporter], check=False)
        record("report tokens never appear in the reporter log", old_token not in logs.stdout + logs.stderr)

        # Hide and show the node, then revoke.
        admin.post(f"/nodes/{NODE}/show", shown="0")
        record("hiding the node removes it from the subscription", subscription(layout.subs_port, token) == (200, []))
        admin.post(f"/nodes/{NODE}/show", shown="1")
        record("showing the node brings it back", len(subscription(layout.subs_port, token)[1]) == 1)
        admin.post("/users/alice/rotate")
        new_token = admin.token("alice")
        record("rotating the subscription invalidates the old address",
               new_token != token and subscription(layout.subs_port, token)[0] == 404
               and subscription(layout.subs_port, new_token)[0] == 200)
        admin.post("/users/alice/revoke", confirm="alice")
        record("revoking returns 404", subscription(layout.subs_port, new_token)[0] == 404)
        console_logs = layout.compose(layout.console, "logs", check=False).stdout
        record("subscription tokens never appear in console logs", token not in console_logs and new_token not in console_logs)
        mem = run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}",
                   "console_e2e_web", "console_e2e_report", reporter], check=False).stdout.strip().replace("\n", "; ")
        record("memory use recorded", bool(mem), mem)
    finally:
        if args.keep:
            print(f"kept: {layout.root} (web 127.0.0.1:{layout.web_port}, subs 127.0.0.1:{layout.subs_port})")
        else:
            layout.remove()
            shutil.rmtree(layout.root, ignore_errors=True)

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
