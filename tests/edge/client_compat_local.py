#!/usr/bin/env python3
"""Local client compatibility check for xray_edge with XHTTP enabled (plan §6.3).

Runs one xray_edge server container rendered by the applier and connects to it with:
  - Xray-core client (the core used by v2rayN): Vision and XHTTP in several modes;
  - Mihomo (the core used by mainstream Clash clients), if MIHOMO points to a binary.
Shadowrocket is closed source and must be tested on a device.

Needs Docker and outbound HTTPS. Uses containers named xray-edge-compat-*, a temporary
directory and local ports 18400-18499; removes them on exit. Keys are generated per run.

Run: MIHOMO=/path/to/mihomo python3 tests/edge/client_compat_local.py
"""
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("xray_edge_apply", REPO / "roles/xray_edge/files/xray_edge_apply.py")
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)

IMAGE = os.environ.get("E2E_IMAGE",
                       "taoziyoyo2566/xray-docker@sha256:fd502666d1a9ca7ea772e2c1638bb83e79bdc12c97cddd5e3755edc7485b3ec7")
TARGET = os.environ.get("E2E_TARGET", "www.costco.com")
URL = os.environ.get("E2E_URL", "https://www.google.com/generate_204")
MIHOMO = os.environ.get("MIHOMO", "")
SRV = "xray-edge-compat-srv"
CLI = "xray-edge-compat-xray"
SERVER_PORT = 18443
PATH = "/compat-xhttp-path"
USER = {"name": "compat", "uuid": "33333333-3333-4333-8333-333333333333", "short_id": "c0c0c0c0"}

XRAY_CASES = [("xray vision", "vision", None), ("xray xhttp auto", "xhttp", "auto"),
              ("xray xhttp packet-up", "xhttp", "packet-up"), ("xray xhttp stream-up", "xhttp", "stream-up"),
              ("xray xhttp stream-one", "xhttp", "stream-one")]
MIHOMO_CASES = [("mihomo vision", "vision", None), ("mihomo xhttp stream-one", "xhttp", "stream-one"),
                ("mihomo xhttp stream-up", "xhttp", "stream-up"), ("mihomo xhttp packet-up", "xhttp", "packet-up")]

results = []


def run(cmd, check=True):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd[:3]))} failed: {proc.stderr.strip()[-300:]}")
    return proc


def record(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}", flush=True)


def fetch(port):
    return run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "20",
                "-x", f"socks5h://127.0.0.1:{port}", URL], check=False).stdout.strip()


def inbound_hits():
    log = run(["docker", "exec", SRV, "cat", "/var/log/xray/access.log"], check=False).stdout
    return {tag: log.count(f"[{tag} ->") for tag in ("vless-reality", "vless-xhttp")}


def start_server(root, private_key):
    desired = {"schema": 1, "node": "compat", "reality": {"target": f"{TARGET}:443", "server_names": [TARGET]},
               "listen": {"port": 443}, "xhttp": {"enabled": True, "path": PATH, "mode": "auto"},
               "users": [USER], "socks5": [], "log": {"level": "info"}}
    node = edge.Node(root, SRV, IMAGE, test_perms=True)
    node.write_dir(os.path.join(root, "conf.d"), edge.render(desired, private_key))
    logs = os.path.join(root, "logs")
    os.makedirs(logs, exist_ok=True)
    os.chmod(logs, 0o777)
    run(["docker", "run", "-d", "--name", SRV, "--user", "10000:10000", "--read-only", "--tmpfs", "/tmp",
         "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "-p", f"127.0.0.1:{SERVER_PORT}:443",
         "-v", f"{root}/conf.d:/etc/xray/conf.d:ro", "-v", f"{logs}:/var/log/xray", IMAGE])
    time.sleep(2)


def xray_client(root, public_key):
    outbounds, inbounds, rules, ports = [], [], [], {}
    for i, (name, kind, mode) in enumerate(XRAY_CASES):
        tag, port = f"c{i}", 18410 + i
        stream = {"network": "xhttp" if kind == "xhttp" else "raw", "security": "reality",
                  "realitySettings": {"serverName": TARGET, "fingerprint": "chrome",
                                      "publicKey": public_key, "shortId": USER["short_id"]}}
        if kind == "xhttp":
            stream["xhttpSettings"] = {"path": PATH, "mode": mode}
        outbounds.append({"tag": tag, "protocol": "vless", "streamSettings": stream, "settings": {"vnext": [{
            "address": "127.0.0.1", "port": SERVER_PORT,
            "users": [{"id": USER["uuid"], "encryption": "none", "flow": "xtls-rprx-vision" if kind == "vision" else ""}]}]}})
        inbounds.append({"tag": f"in-{tag}", "listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {}})
        rules.append({"type": "field", "inboundTag": [f"in-{tag}"], "outboundTag": tag})
        ports[name] = port
    path = os.path.join(root, "xray-client.json")
    with open(path, "w") as fh:
        json.dump({"log": {"loglevel": "warning"}, "inbounds": inbounds, "outbounds": outbounds,
                   "routing": {"rules": rules}}, fh)
    os.chmod(path, 0o644)
    run(["docker", "run", "-d", "--name", CLI, "--network", "host", "-v", f"{path}:/config.json:ro", IMAGE])
    time.sleep(2)
    return ports


def mihomo_client(root, public_key):
    proxies, listeners, ports = [], [], {}
    for i, (name, kind, mode) in enumerate(MIHOMO_CASES):
        pname, port = f"p{i}", 18450 + i
        proxy = {"name": pname, "type": "vless", "server": "127.0.0.1", "port": SERVER_PORT, "uuid": USER["uuid"],
                 "udp": True, "tls": True, "servername": TARGET, "client-fingerprint": "chrome", "encryption": "",
                 "reality-opts": {"public-key": public_key, "short-id": USER["short_id"]}}
        if kind == "vision":
            proxy.update({"network": "tcp", "flow": "xtls-rprx-vision"})
        else:
            proxy.update({"network": "xhttp", "alpn": ["h2"], "xhttp-opts": {"path": PATH, "mode": mode}})
        proxies.append(proxy)
        listeners.append({"name": f"l{i}", "type": "socks", "listen": "127.0.0.1", "port": port, "proxy": pname})
        ports[name] = port
    workdir = os.path.join(root, "mihomo")
    os.makedirs(workdir, exist_ok=True)
    cfg = os.path.join(workdir, "config.yaml")
    with open(cfg, "w") as fh:
        # JSON is valid YAML, so no YAML dependency is needed.
        json.dump({"log-level": "warning", "ipv6": False, "mode": "rule", "proxies": proxies,
                   "listeners": listeners, "rules": ["MATCH,DIRECT"]}, fh)
    proc = subprocess.Popen([MIHOMO, "-d", workdir, "-f", cfg], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"mihomo exited: {proc.stderr.read()[-300:]}")
    return ports, proc


def main():
    root = tempfile.mkdtemp(prefix="xray-edge-compat-")
    os.chmod(root, 0o755)
    run(["docker", "rm", "-f", SRV, CLI], check=False)
    mihomo_proc = None
    try:
        node = edge.Node(root, SRV, IMAGE, test_perms=True)
        private_key, public_key = node.keypair()
        start_server(root, private_key)

        for name, port in xray_client(root, public_key).items():
            before = inbound_hits()
            code = fetch(port)
            time.sleep(0.5)
            after = inbound_hits()
            want = "vless-xhttp" if "xhttp" in name else "vless-reality"
            record(name, code == "204" and after[want] > before[want], f"http={code} inbound={want}")

        if MIHOMO:
            ports, mihomo_proc = mihomo_client(root, public_key)
            for name, port in ports.items():
                before = inbound_hits()
                code = fetch(port)
                time.sleep(0.5)
                after = inbound_hits()
                want = "vless-xhttp" if "xhttp" in name else "vless-reality"
                record(name, code == "204" and after[want] > before[want], f"http={code} inbound={want}")
        else:
            print("[SKIP] mihomo cases: set MIHOMO to a mihomo binary")
    finally:
        if mihomo_proc and mihomo_proc.poll() is None:
            os.killpg(mihomo_proc.pid, signal.SIGTERM)
            mihomo_proc.wait(timeout=10)
        run(["docker", "rm", "-f", SRV, CLI], check=False)
        shutil.rmtree(root, ignore_errors=True)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
