#!/usr/bin/env python3
"""Local end-to-end check of xray_edge_apply against real Xray containers (plan §6.1 items 2-3).

Needs Docker and outbound HTTPS from this host. Creates only containers, a network
and a temporary directory named xray-edge-e2e*, and removes them on exit. Keys are
generated for the run and deleted with the directory.

Run: python3 tests/edge/e2e_local.py [--keep]
"""
import argparse
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("xray_edge_apply", REPO / "roles/xray_edge/files/xray_edge_apply.py")
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_render  # noqa: E402  (golden cases)

IMAGE = os.environ.get("E2E_IMAGE",
                       "taoziyoyo2566/xray-docker@sha256:fd502666d1a9ca7ea772e2c1638bb83e79bdc12c97cddd5e3755edc7485b3ec7")
TARGET = os.environ.get("E2E_TARGET", "www.costco.com")
CHECK_URL = os.environ.get("E2E_URL", "https://www.cloudflare.com/cdn-cgi/trace")
NET = "xray-edge-e2e"
SRV = "xray-edge-e2e-srv"
CLI = "xray-edge-e2e-cli"
APPLIER = REPO / "roles/xray_edge/files/xray_edge_apply.py"
USERS = {
    "alice": {"name": "alice", "uuid": "11111111-1111-4111-8111-111111111111", "short_id": "a1a1a1a1"},
    "bob": {"name": "bob", "uuid": "22222222-2222-4222-8222-222222222222", "short_id": "a1a1a1a1"},
}
XHTTP_PATH = "/e2e-xhttp-path"
# client socks port on 127.0.0.1 -> (user, transport)
ROUTES = {18190: ("alice", "vision"), 18191: ("alice", "xhttp"), 18192: ("bob", "vision"), 18193: ("bob", "xhttp")}

results = []


def run(cmd, check=True, **kw):
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd[:4]))} failed: {proc.stderr.strip()[-300:]}")
    return proc


def record(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}", flush=True)


def desired(users, xhttp=False):
    return {
        "schema": 1, "node": "e2e", "generated_at": "2026-01-01T00:00:00Z",
        "reality": {"target": f"{TARGET}:443", "server_names": [TARGET]},
        "listen": {"port": 443},
        "xhttp": {"enabled": xhttp, "path": XHTTP_PATH, "mode": "auto"},
        "users": [USERS[u] for u in users],
        "socks5": [], "log": {"level": "info"},
    }


def apply(root, state, command="apply"):
    path = os.path.join(root, "state", "desired.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(state, fh)
    proc = run([sys.executable, str(APPLIER), command, "--root", root, "--container", SRV,
                "--image", IMAGE, "--test-perms"], check=False)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    return proc.returncode, out


def fetch(port):
    proc = run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "20",
                "-x", f"socks5h://127.0.0.1:{port}", CHECK_URL], check=False)
    return proc.stdout.strip()


def restarts():
    return run(["docker", "inspect", "-f", "{{.RestartCount}} {{.State.StartedAt}}", SRV]).stdout.strip()


def start_server(root):
    logs = os.path.join(root, "logs")
    os.makedirs(logs, exist_ok=True)
    os.chmod(logs, 0o777)
    run(["docker", "run", "-d", "--name", SRV, "--network", NET, "--network-alias", "edge-srv",
         "--user", "10000:10000", "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/run",
         "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256",
         "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
         "-v", f"{root}/conf.d:/etc/xray/conf.d:ro", "-v", f"{logs}:/var/log/xray", IMAGE])


def start_client(root, public_key):
    outbounds, inbounds, rules = [], [], []
    for port, (user, transport) in ROUTES.items():
        tag = f"{user}-{transport}"
        stream = {"network": "xhttp" if transport == "xhttp" else "raw", "security": "reality",
                  "realitySettings": {"serverName": TARGET, "fingerprint": "chrome",
                                      "publicKey": public_key, "shortId": USERS[user]["short_id"]}}
        if transport == "xhttp":
            stream["xhttpSettings"] = {"path": XHTTP_PATH, "mode": "auto"}
        outbounds.append({"tag": tag, "protocol": "vless", "streamSettings": stream, "settings": {"vnext": [{
            "address": "edge-srv", "port": 443,
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
    run(["docker", "run", "-d", "--name", CLI, "--network", NET, *publish,
         "-v", f"{path}:/config.json:ro", IMAGE])
    time.sleep(2)


def cleanup(root, keep):
    if keep:
        print(f"kept: {root}, containers {SRV} {CLI}, network {NET}")
        return
    run(["docker", "rm", "-f", SRV, CLI], check=False)
    run(["docker", "network", "rm", NET], check=False)
    shutil.rmtree(root, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    root = tempfile.mkdtemp(prefix="xray-edge-e2e-")
    os.chmod(root, 0o755)
    run(["docker", "rm", "-f", SRV, CLI], check=False)
    run(["docker", "network", "rm", NET], check=False)
    run(["docker", "network", "create", NET])
    try:
        node = edge.Node(root, SRV, IMAGE, test_perms=True)
        private_key, public_key = node.keypair()

        for case, state in test_render.GoldenTest.CASES.items():
            cand = tempfile.mkdtemp(dir=root, prefix=".golden-")
            node.write_dir(cand, edge.render(state, private_key))
            try:
                node.test_config(cand)
                record(f"xray -test accepts golden case {case}", True)
            except edge.ApplyError as exc:
                record(f"xray -test accepts golden case {case}", False, str(exc))
            finally:
                shutil.rmtree(cand)

        bad = tempfile.mkdtemp(dir=root, prefix=".bad-")
        bad_key = "A" * 42  # key-shaped but decodes to 31 bytes, so Xray rejects it and quotes it
        node.write_dir(bad, edge.render(desired(["alice"]), bad_key))
        try:
            node.test_config(bad)
            record("xray -test rejects an invalid config", False)
        except edge.ApplyError as exc:
            record("xray -test rejects an invalid config and the error masks the key", bad_key not in str(exc))
        finally:
            shutil.rmtree(bad)

        rc, out = apply(root, desired(["alice"]))
        record("first apply writes config before the container exists", rc == 0 and out.get("action") == "written", str(out))
        start_server(root)
        rc, out = apply(root, desired(["alice"]), "verify")
        record("verify matches running users", rc == 0, str(out))
        rc, out = apply(root, desired(["alice"]))
        record("re-apply of the same state is a no-op", rc == 0 and out.get("action") == "none", str(out))

        start_client(root, public_key)
        record("alice connects over RAW+Vision", fetch(18190) == "200")
        record("bob is rejected before being added", fetch(18192) != "200")

        before = restarts()
        rc, out = apply(root, desired(["alice", "bob"]))
        record("adding bob is applied through the API", rc == 0 and out.get("action") == "api", str(out))
        record("bob connects after API add", fetch(18192) == "200")
        rc, out = apply(root, desired(["bob"]))
        record("removing alice is applied through the API", rc == 0 and out.get("action") == "api", str(out))
        record("alice is rejected after API remove", fetch(18190) != "200")
        record("user changes did not restart the container", restarts() == before, restarts())

        rc, out = apply(root, desired(["bob"], xhttp=True))
        record("enabling XHTTP is applied with a restart", rc == 0 and out.get("action") == "restart", str(out))
        record("bob connects over XHTTP on the same port", fetch(18193) == "200")
        record("bob still connects over RAW+Vision", fetch(18192) == "200")
        record("alice is rejected over XHTTP", fetch(18191) != "200")

        time.sleep(1)
        # The log belongs to the container user; read it the way an operator on the node would.
        log = run(["docker", "exec", SRV, "cat", "/var/log/xray/access.log"]).stdout.splitlines()
        xhttp_lines = [line for line in log if "[vless-xhttp" in line and "accepted" in line]
        record("XHTTP access log keeps the client source address",
               bool(xhttp_lines) and all(" from 0.0.0.0:" not in line for line in xhttp_lines), f"{len(xhttp_lines)} lines")
        online = node.api("statsonlineiplist", "-email", "bob.e2e", check=False)
        record("online IP statistics are available for bob", online.returncode == 0 and "bob.e2e" in online.stdout,
               online.stdout.strip().replace("\n", " ")[:200] or online.stderr.strip()[-200:])

        rc, out = apply(root, desired(["bob"], xhttp=True), "verify")
        record("final verify", rc == 0, str(out))
        allowed = {"keys", "conf.d", "last-good"}
        leaked = [p for p in pathlib.Path(root).rglob("*") if p.is_file() and not allowed & set(p.parts)
                  and os.access(p, os.R_OK) and private_key in p.read_text(errors="ignore")]
        unreadable = [p.name for p in pathlib.Path(root).rglob("*") if p.is_file() and not os.access(p, os.R_OK)]
        if unreadable:
            log = run(["docker", "exec", SRV, "cat", "/var/log/xray/access.log", "/var/log/xray/error.log"], check=False).stdout
            if private_key in log:
                leaked.append("container logs")
        record("private key only in keys/, conf.d/ and last-good/", not leaked, str(leaked))
    finally:
        cleanup(root, args.keep)

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
