#!/usr/bin/env python3
"""Local end-to-end check of the xray_edge compose project against real Xray containers.

Covers plan-single-instance-443 §6.1 items 2-3 and plan-edge-compose §6.1 items 3-5: the applier
runs from the tools image through `docker compose run`, Xray and logrotate run as compose services.

Needs Docker with Compose, outbound HTTPS from this host, and Jinja2/PyYAML (run with
monitor_venv/bin/python). Creates only containers, a compose project, an image tag and a temporary
directory named xray-edge-e2e*, and removes them on exit. Files under the temporary directory are
created by root inside containers, the same ownership the node uses, and are removed the same way.

Run: monitor_venv/bin/python tests/edge/e2e_local.py [--keep] [--tools-image IMAGE]
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import jinja2

REPO = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("xray_edge_apply", REPO / "roles/xray_edge/files/xray_edge_apply.py")
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_compose  # noqa: E402  (compose rendering)
import test_render  # noqa: E402  (golden cases)

IMAGE = os.environ.get("E2E_IMAGE", test_compose.GROUP_VARS["edge_xray_image"])
TARGET = os.environ.get("E2E_TARGET", "www.costco.com")
CHECK_URL = os.environ.get("E2E_URL", "https://www.cloudflare.com/cdn-cgi/trace")
PROJECT = "xray-edge-e2e"
SRV = "xray-edge-e2e-srv"
CLI = "xray-edge-e2e-cli"
SOCKS = "xray-edge-e2e-socks"
USERS = {
    "alice": {"name": "alice", "uuid": "11111111-1111-4111-8111-111111111111", "short_id": "a1a1a1a1"},
    "bob": {"name": "bob", "uuid": "22222222-2222-4222-8222-222222222222", "short_id": "a1a1a1a1"},
}
BOB_NEW_UUID = "33333333-3333-4333-8333-333333333333"
BOB_NEW_SHORT_ID = "b2b2b2b2"
XHTTP_PATH = "/e2e-xhttp-path"
ROTATE_INTERVAL = 3
# client socks port on 127.0.0.1 -> (user, transport)
ROUTES = {18190: ("alice", "vision"), 18191: ("alice", "xhttp"), 18192: ("bob", "vision"), 18193: ("bob", "xhttp")}

results = []


def run(cmd, check=True, **kw):
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd[:5]))} failed: {proc.stderr.strip()[-400:]}")
    return proc


def record(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}", flush=True)


class Node:
    """A temporary node root driven the way roles/xray_edge drives /opt/xray-edge."""

    def __init__(self, root, tools):
        self.root = root
        self.tools = tools
        self.compose_file = os.path.join(root, "compose.yaml")

    def as_root(self, script, stdin=None, check=True):
        return run(["docker", "run", "--rm", "-i", "--network", "none", "-v", f"{self.root}:{self.root}",
                    "-w", self.root, self.tools, "sh", "-c", script], input=stdin, check=check)

    def put(self, rel, content, owner="0:0", mode="0600"):
        path = os.path.join(self.root, rel)
        self.as_root(f"cat > '{path}' && chown {owner} '{path}' && chmod {mode} '{path}'", stdin=content)

    def prepare(self):
        # Same directories, owners and modes as roles/xray_edge/tasks/main.yml.
        self.as_root("set -e; chmod 0755 .; install -d -m 0700 state keys; install -d -o 0 -g 10000 -m 0750 conf.d;"
                     " install -d -o 10000 -g 10000 -m 0750 logs; install -d -m 0755 rotate;"
                     " install -d -o 10000 -g 10000 -m 0700 rotate/state")
        compose = test_compose.render("e2e", [], edge_root_dir=self.root, edge_container_name=SRV,
                                      edge_compose_project=PROJECT, edge_tools_image=self.tools,
                                      edge_xray_image=IMAGE, edge_logrotate_interval=ROTATE_INTERVAL)
        self.put("compose.yaml", compose, mode="0644")
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        conf = env.from_string((REPO / "roles/xray_edge/templates/logrotate.conf.j2").read_text()).render(
            ansible_managed="e2e", edge_logrotate_interval=ROTATE_INTERVAL, edge_root_dir=self.root,
            edge_log_rotate_keep=14, edge_log_rotate_maxsize="2k")
        self.put("rotate/logrotate.conf", conf, mode="0644")

    def compose(self, *args, check=True):
        return run(["docker", "compose", "-f", self.compose_file, *args], check=check)

    def apply(self, state, command="apply"):
        self.put("state/desired.json", json.dumps(state))
        proc = self.compose("run", "--rm", "-T", "--pull", "never", "applier", command, "--root", self.root,
                            "--container", SRV, "--image", IMAGE, check=False)
        lines = proc.stdout.strip().splitlines()
        try:
            out = json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError):
            out = {"stdout": proc.stdout[-300:], "stderr": proc.stderr[-300:]}
        return proc.returncode, out

    def sha(self, rel):
        return self.as_root(f"cat {rel}/*.json | sha256sum").stdout.split()[0]

    def read(self, rel):
        return self.as_root(f"cat '{rel}'").stdout

    def remove(self):
        self.compose("--profile", "tools", "down", "--remove-orphans", check=False)
        run(["docker", "rm", "-f", SRV, f"{SRV}_logrotate"], check=False)
        self.as_root("rm -rf ./* ./.[!.]*", check=False)


def desired(users, xhttp=False, socks5=None, port=443):
    return {
        "schema": 1, "node": "e2e", "generated_at": "2026-01-01T00:00:00Z",
        "reality": {"target": f"{TARGET}:443", "server_names": [TARGET]},
        "listen": {"port": port},
        "xhttp": {"enabled": xhttp, "path": XHTTP_PATH, "mode": "auto"},
        "users": [USERS[u] for u in users],
        "socks5": socks5 or [], "log": {"level": "info"},
    }


def fetch(port):
    return run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "20",
                "-x", f"socks5h://127.0.0.1:{port}", CHECK_URL], check=False).stdout.strip()


def inspect(name, fmt):
    return run(["docker", "inspect", "-f", fmt, name], check=False).stdout.strip()


def restarts():
    return inspect(SRV, "{{.RestartCount}} {{.State.StartedAt}}")


def start_client(root, server_ip, public_key):
    run(["docker", "rm", "-f", CLI], check=False)
    outbounds, inbounds, rules = [], [], []
    for port, (user, transport) in ROUTES.items():
        tag = f"{user}-{transport}"
        stream = {"network": "xhttp" if transport == "xhttp" else "raw", "security": "reality",
                  "realitySettings": {"serverName": TARGET, "fingerprint": "chrome",
                                      "publicKey": public_key, "shortId": USERS[user]["short_id"]}}
        if transport == "xhttp":
            stream["xhttpSettings"] = {"path": XHTTP_PATH, "mode": "auto"}
        outbounds.append({"tag": tag, "protocol": "vless", "streamSettings": stream, "settings": {"vnext": [{
            "address": server_ip, "port": 443,
            "users": [{"id": USERS[user]["uuid"], "encryption": "none",
                       "flow": "xtls-rprx-vision" if transport == "vision" else ""}]}]}})
        inbounds.append({"tag": f"in-{tag}", "listen": "0.0.0.0", "port": port, "protocol": "socks", "settings": {}})
        rules.append({"type": "field", "inboundTag": [f"in-{tag}"], "outboundTag": tag})
    path = os.path.join(root, "client.json")
    with open(path, "w") as fh:
        json.dump({"log": {"loglevel": "warning"}, "inbounds": inbounds, "outbounds": outbounds,
                   "routing": {"rules": rules}}, fh)
    os.chmod(path, 0o644)
    publish = sum((["-p", f"127.0.0.1:{p}:{p}"] for p in ROUTES), [])
    run(["docker", "run", "-d", "--name", CLI, *publish, "-v", f"{path}:/config.json:ro", IMAGE])
    time.sleep(2)


def start_socks_egress(root):
    """A plain SOCKS5 server whose access log shows which requests the node routed through it."""
    path = os.path.join(root, "socks.json")
    with open(path, "w") as fh:
        json.dump({"log": {"loglevel": "info", "access": ""},
                   "inbounds": [{"listen": "0.0.0.0", "port": 1080, "protocol": "socks", "settings": {"udp": True}}],
                   "outbounds": [{"protocol": "freedom"}]}, fh)
    os.chmod(path, 0o644)
    run(["docker", "run", "-d", "--name", SOCKS, "-v", f"{path}:/config.json:ro", IMAGE])
    time.sleep(2)
    return inspect(SOCKS, "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}")


def socks_hits():
    return run(["docker", "logs", SOCKS], check=False).stdout.count("accepted")


def golden_and_invalid_configs(tools_root):
    """xray -test over the golden cases, run in-process like the unit tests (no compose needed)."""
    node = edge.Node(tools_root, SRV, IMAGE, test_perms=True)
    private_key, _ = node.keypair()
    for case, state in test_render.GoldenTest.CASES.items():
        cand = tempfile.mkdtemp(dir=tools_root, prefix=".golden-")
        node.write_dir(cand, edge.render(state, private_key))
        try:
            node.test_config(cand)
            record(f"xray -test accepts golden case {case}", True)
        except edge.ApplyError as exc:
            record(f"xray -test accepts golden case {case}", False, str(exc))
        finally:
            shutil.rmtree(cand)
    bad = tempfile.mkdtemp(dir=tools_root, prefix=".bad-")
    bad_key = "A" * 42  # key-shaped but decodes to 31 bytes, so Xray rejects it and quotes it
    node.write_dir(bad, edge.render(desired(["alice"]), bad_key))
    try:
        node.test_config(bad)
        record("xray -test rejects an invalid config", False)
    except edge.ApplyError as exc:
        record("xray -test rejects an invalid config and the error masks the key", bad_key not in str(exc))
    finally:
        shutil.rmtree(bad)


def s3_container(root):
    """The S3 container form (docker_container, no compose labels), for the takeover check."""
    run(["docker", "run", "-d", "--name", SRV, "--network", "bridge", "--restart", "unless-stopped",
         "--user", "10000:10000", "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/run",
         "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256",
         "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
         "-v", f"{root}/conf.d:/etc/xray/conf.d:ro", "-v", f"{root}/logs:/var/log/xray", IMAGE])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--tools-image", help="use an existing tools image instead of building one")
    args = parser.parse_args()

    tools = args.tools_image
    if not tools:
        run(["docker", "build", "-q", "-f", "docker/edge-tools/Dockerfile", "-t", "reality-edge-tools:e2e", "."],
            cwd=REPO)
        tools = "reality-edge-tools:e2e"
    for name in (SRV, CLI, SOCKS, f"{SRV}_logrotate"):
        run(["docker", "rm", "-f", name], check=False)

    root = tempfile.mkdtemp(prefix="xray-edge-e2e-")
    tools_root = tempfile.mkdtemp(prefix="xray-edge-e2e-golden-")
    node = Node(root, tools)
    try:
        golden_and_invalid_configs(tools_root)
        node.prepare()

        rc, out = node.apply(desired(["alice"]))
        record("first apply writes config before the container exists", rc == 0 and out.get("action") == "written", str(out))
        public_key = out.get("public_key", "")
        conf_hash = node.sha("conf.d")

        # S3 form first, then the takeover the role performs: remove the unlabeled container, compose up.
        s3_container(root)
        rc, out = node.apply(desired(["alice"]), "verify")
        record("applier container verifies an S3-form container", rc == 0, str(out))
        run(["docker", "rm", "-f", SRV])
        node.compose("up", "-d")
        record("xray and logrotate run as compose services",
               inspect(SRV, '{{index .Config.Labels "com.docker.compose.project"}}') == PROJECT
               and inspect(f"{SRV}_logrotate", "{{.State.Running}}") == "true")
        rc, out = node.apply(desired(["alice"]), "verify")
        record("verify matches running users after takeover", rc == 0, str(out))
        record("takeover keeps the public key and conf.d",
               out.get("public_key") == public_key and node.sha("conf.d") == conf_hash)
        rc, out = node.apply(desired(["alice"]))
        record("re-apply of the same state is a no-op", rc == 0 and out.get("action") == "none", str(out))

        server_ip = inspect(SRV, "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}")
        start_client(root, server_ip, public_key)
        record("alice connects over RAW+Vision", fetch(18190) == "200")
        record("bob is rejected before being added", fetch(18192) != "200")

        before = restarts()
        rc, out = node.apply(desired(["alice", "bob"]))
        record("adding bob is applied through the API", rc == 0 and out.get("action") == "api", str(out))
        record("bob connects after API add", fetch(18192) == "200")
        rc, out = node.apply(desired(["bob"]))
        record("removing alice is applied through the API", rc == 0 and out.get("action") == "api", str(out))
        record("alice is rejected after API remove", fetch(18190) != "200")
        record("user changes did not restart the container", restarts() == before, restarts())

        rc, out = node.apply(desired(["bob"], xhttp=True))
        record("enabling XHTTP is applied with a restart", rc == 0 and out.get("action") == "restart", str(out))
        record("bob connects over XHTTP on the same port", fetch(18193) == "200")
        record("bob still connects over RAW+Vision", fetch(18192) == "200")
        record("alice is rejected over XHTTP", fetch(18191) != "200")

        time.sleep(1)
        # maxsize is 2k in this test, so the lines may already sit in a rotated, compressed file.
        log = node.as_root("cat logs/access.log; for f in logs/access.log.*.gz; do [ -e \"$f\" ] && zcat \"$f\"; done; true"
                           ).stdout.splitlines()
        xhttp_lines = [line for line in log if "[vless-xhttp" in line and "accepted" in line]
        record("XHTTP access log keeps the client source address",
               bool(xhttp_lines) and all(" from 0.0.0.0:" not in line for line in xhttp_lines), f"{len(xhttp_lines)} lines")
        online = run(["docker", "exec", SRV, "xray", "api", "statsonlineiplist", "-s", "127.0.0.1:10085",
                      "-email", "bob.e2e"], check=False)
        record("online IP statistics are available for bob", online.returncode == 0 and "bob.e2e" in online.stdout,
               online.stdout.strip().replace("\n", " ")[:200] or online.stderr.strip()[-200:])

        # S3 gap: modifying a user through the API (new UUID), then a new short id (restart).
        old_bob = dict(USERS["bob"])
        USERS["bob"] = dict(old_bob, uuid=BOB_NEW_UUID)
        before = restarts()
        rc, out = node.apply(desired(["bob"], xhttp=True))
        record("changing bob's UUID is applied through the API", rc == 0 and out.get("action") == "api", str(out))
        record("bob's old UUID is rejected after the change", fetch(18192) != "200")
        start_client(root, server_ip, public_key)
        record("bob connects with the new UUID without a restart", fetch(18192) == "200" and restarts() == before)
        USERS["bob"] = dict(USERS["bob"], short_id=BOB_NEW_SHORT_ID)
        rc, out = node.apply(desired(["bob"], xhttp=True))
        record("changing bob's short id is applied with a restart", rc == 0 and out.get("action") == "restart", str(out))
        start_client(root, server_ip, public_key)
        record("bob connects with the new short id", fetch(18192) == "200")

        # S3 gap: SOCKS5 membership changes on a running node.
        socks_ip = start_socks_egress(root)
        profile = {"name": "egress", "priority": 100, "address": socks_ip, "port": 1080, "username": "", "password": "",
                   "users": ["bob"], "domains": [], "ips": [], "protocols": [], "network": ""}
        rc, out = node.apply(desired(["alice", "bob"], xhttp=True, socks5=[profile]))
        record("adding a SOCKS5 profile for bob is applied", rc == 0 and out.get("action") == "restart", str(out))
        hits = socks_hits()
        bob_ok, alice_ok = fetch(18192) == "200", fetch(18190) == "200"
        bob_hits = socks_hits()
        record("bob egresses through the SOCKS5 profile and alice does not",
               bob_ok and alice_ok and bob_hits == hits + 1, f"hits {hits} -> {bob_hits}")
        profile["users"] = ["alice"]
        rc, out = node.apply(desired(["alice", "bob"], xhttp=True, socks5=[profile]))
        record("moving the SOCKS5 profile from bob to alice is applied", rc == 0 and out.get("changed"), str(out))
        hits = socks_hits()
        bob_ok = fetch(18192) == "200"
        after_bob = socks_hits()
        alice_ok = fetch(18190) == "200"
        after_alice = socks_hits()
        record("after the change only alice egresses through SOCKS5",
               bob_ok and alice_ok and after_bob == hits and after_alice == hits + 1,
               f"hits {hits} -> {after_bob} -> {after_alice}")
        good_state = desired(["alice", "bob"], xhttp=True, socks5=[profile])

        # S3 gap: last-good recovery. A change that passes xray -test but cannot start: the log
        # directory is unreachable for the container user, so Xray fails to open its access log.
        good_hash = node.sha("conf.d")
        node.as_root("chmod 0000 logs")
        rc, out = node.apply(desired(["alice", "bob"], xhttp=False, socks5=[profile]))
        record("a change that fails only at runtime makes apply fail", rc != 0, str(out)[:200])
        record("last-good is restored into conf.d", node.sha("conf.d") == good_hash)
        node.as_root("chmod 0750 logs")
        run(["docker", "restart", SRV])
        rc, out = node.apply(good_state, "verify")
        record("after the fault is fixed the node runs the last-good config", rc == 0, str(out))
        record("bob still connects over XHTTP after recovery", fetch(18193) == "200")

        # Log rotation inside compose: maxsize 2k, interval 3 s.
        for _ in range(12):
            fetch(18192)
        time.sleep(ROTATE_INTERVAL * 3)
        listing = node.as_root("ls -1 logs rotate/state").stdout.split()
        size_before = int(node.as_root("wc -c < logs/access.log").stdout.strip() or 0)
        fetch(18192)
        time.sleep(1)
        size_after = int(node.as_root("wc -c < logs/access.log").stdout.strip() or 0)
        record("logrotate container rotates and compresses by size",
               "access.log.1.gz" in listing and "status" in listing, " ".join(listing))
        record("Xray keeps writing to access.log after copytruncate", size_after > size_before,
               f"{size_before} -> {size_after} bytes")
        mem = run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", f"{SRV}_logrotate"], check=False)
        record("logrotate container memory recorded", mem.returncode == 0, mem.stdout.strip())

        rc, out = node.apply(good_state, "verify")
        record("final verify", rc == 0, str(out))
        private_key = json.loads(node.read("keys/reality.json"))["private_key"]
        leaked = node.as_root(f"grep -rlF '{private_key}' . --exclude-dir=keys --exclude-dir=conf.d "
                              "--exclude-dir=last-good || true").stdout.split()
        record("private key only in keys/, conf.d/ and last-good/", not leaked, str(leaked))
        digest = hashlib.sha256(private_key.encode()).hexdigest()[:8]
        compose_logs = node.compose("--profile", "tools", "logs", check=False).stdout
        record("private key absent from compose logs", private_key not in compose_logs, f"key sha {digest}")

        node.remove()
        left = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}", "-q"]).stdout.split()
        record("removal leaves no compose containers or node files", not left and not os.listdir(root), str(left))
    finally:
        if args.keep:
            print(f"kept: {root}, compose project {PROJECT}, containers {CLI} {SOCKS}")
        else:
            node.remove()
            for name in (CLI, SOCKS):
                run(["docker", "rm", "-f", name], check=False)
            shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tools_root, ignore_errors=True)

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
