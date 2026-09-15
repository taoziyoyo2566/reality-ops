#!/usr/bin/env python3
"""Local end-to-end check of the subscription service compose project (plan-subscription-service §6.1 items 3, 4, 6).

Builds the image, renders roles/subs_service/templates/compose.yaml.j2 without the tunnel (127.0.0.1 port),
starts a real Xray server from the edge renderer, then fetches each format through HTTP and connects with an
Xray client (v2ray format) and, when MIHOMO points to a mihomo binary, a Mihomo client (both Clash profiles,
TUN off). Needs Docker with Compose, outbound HTTPS, Jinja2 and PyYAML (run with monitor_venv/bin/python).
Everything it creates is named reality-subs-e2e* or xray-subs-e2e* and removed on exit.

Run: MIHOMO=/path/to/mihomo monitor_venv/bin/python tests/subs/e2e_local.py [--keep]
"""
import argparse
import base64
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import jinja2
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixtures  # noqa: E402
from subs import build, links  # noqa: E402

SPEC = importlib.util.spec_from_file_location("xray_edge_apply", REPO / "roles/xray_edge/files/xray_edge_apply.py")
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)

GROUP_VARS = yaml.safe_load((REPO / "group_vars/all/subs.yml").read_text())
EDGE_VARS = yaml.safe_load((REPO / "group_vars/all/edge.yml").read_text())
XRAY_IMAGE = EDGE_VARS["edge_xray_image"]
IMAGE = "reality-subs:e2e"
PROJECT = "reality-subs-e2e"
CONTAINER = "reality-subs-e2e"
SRV = "xray-subs-e2e-srv"
CLI = "xray-subs-e2e-cli"
PORT = 18100
TARGET = os.environ.get("E2E_TARGET", "www.costco.com")
CHECK_URL = os.environ.get("E2E_URL", "https://www.cloudflare.com/cdn-cgi/trace")
MIHOMO = os.environ.get("MIHOMO")

results = []


def run(cmd, check=True, **kw):
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd[:5]))} failed: {proc.stderr.strip()[-400:]}")
    return proc


def record(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}", flush=True)


def render_compose(root, tunnel=False):
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
    variables = dict(GROUP_VARS, ansible_managed="e2e", subs_image=IMAGE, subs_root_dir=root,
                     subs_compose_project=PROJECT, subs_container_name=CONTAINER, subs_tunnel_enabled=tunnel,
                     subs_host_port=PORT)
    return env.from_string((REPO / "roles/subs_service/templates/compose.yaml.j2").read_text()).render(**variables)


def http_get(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def xray_client_config(link, port):
    parts = links.parse_vless(link)
    stream = {"network": "xhttp" if parts["type"] == "xhttp" else "raw", "security": "reality",
              "realitySettings": {"serverName": parts["sni"], "fingerprint": parts["fp"],
                                  "publicKey": parts["public_key"], "shortId": parts["short_id"]}}
    if parts["type"] == "xhttp":
        stream["xhttpSettings"] = {"path": parts["path"], "mode": "auto"}
    return {"log": {"loglevel": "warning"},
            "inbounds": [{"listen": "0.0.0.0", "port": port, "protocol": "socks", "settings": {}}],
            "outbounds": [{"protocol": "vless", "streamSettings": stream, "settings": {"vnext": [{
                "address": parts["server"], "port": parts["port"],
                "users": [{"id": parts["uuid"], "encryption": "none", "flow": parts["flow"]}]}]}}]}


def curl_via(proxy):
    return run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "20", "-x", proxy, CHECK_URL],
               check=False).stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    run(["docker", "build", "-q", "-f", "docker/subs/Dockerfile", "-t", IMAGE, "."], cwd=REPO)
    for name in (CONTAINER, SRV, CLI):
        run(["docker", "rm", "-f", name], check=False)

    root = tempfile.mkdtemp(prefix="reality-subs-e2e-")
    work = tempfile.mkdtemp(prefix="xray-subs-e2e-")
    os.chmod(work, 0o755)
    compose = ["docker", "compose", "-f", os.path.join(root, "compose.yaml")]
    mihomo_proc = None
    try:
        # A real edge server for the migrated node.
        node = edge.Node(work, SRV, XRAY_IMAGE, test_perms=True)
        private_key, public_key = node.keypair()
        state = {"schema": 1, "node": "alpha", "reality": {"target": f"{TARGET}:443", "server_names": [TARGET]},
                 "listen": {"port": 443}, "xhttp": {"enabled": True, "path": "/xp/synthetic", "mode": "auto"},
                 "users": [fixtures.member("test")], "socks5": [], "log": {"level": "warning"}}
        node.write_dir(os.path.join(work, "conf.d"), edge.render(state, private_key))
        logs = os.path.join(work, "logs")
        os.makedirs(logs)
        os.chmod(logs, 0o777)  # Xray exits when it cannot open its access log
        run(["docker", "run", "-d", "--name", SRV, "--user", "10000:10000", "--read-only", "--tmpfs", "/tmp",
             "-v", f"{work}/conf.d:/etc/xray/conf.d:ro", "-v", f"{logs}:/var/log/xray", XRAY_IMAGE])
        time.sleep(2)
        server_ip = run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", SRV]).stdout.strip()
        if not server_ip:
            raise RuntimeError("edge server did not start: " + run(["docker", "logs", SRV], check=False).stderr[-300:])

        collected = fixtures.collected()
        collected["nodes"]["alpha"]["edge"].update({"endpoint": server_ip, "public_key": public_key, "sni": TARGET})
        legacy = tempfile.mkdtemp()
        fixtures.write_legacy(legacy)
        legacy_links = build.legacy_files(legacy)
        shutil.rmtree(legacy)
        catalog, tokens, problems, _ = build.build(collected, fixtures.TOKENS, ["alice", "bob", "test"], legacy_links)
        record("catalog builds without problems", not problems, str(problems))

        # Node directory with the role's owners and modes.
        run(["docker", "run", "--rm", "--user", "0:0", "--network", "none", "--entrypoint", "sh", "-v", f"{root}:{root}",
             "-w", root, IMAGE, "-c", "install -d -o 0 -g 10001 -m 0750 data && install -d -o 10001 -g 10001 -m 0750 db"
             " && install -d -m 0700 secrets && chmod 0755 ."])

        def publish(doc_catalog, doc_tokens):
            for name, doc in (("catalog.json", doc_catalog), ("tokens.json", doc_tokens)):
                run(["docker", "run", "--rm", "-i", "--user", "0:0", "--network", "none", "--entrypoint", "sh",
                     "-v", f"{root}:{root}", "-w", root, IMAGE, "-c",
                     f"cat > data/.{name} && chown 0:10001 data/.{name} && chmod 0640 data/.{name} && mv data/.{name} data/{name}"],
                    input=json.dumps(doc))

        publish(catalog, tokens)
        compose_text = render_compose(root)
        run(["docker", "run", "--rm", "-i", "--user", "0:0", "--network", "none", "--entrypoint", "sh", "-v", f"{root}:{root}",
             "-w", root, IMAGE, "-c", "cat > compose.yaml && chmod 0644 compose.yaml"], input=compose_text)
        run(compose + ["up", "-d", "--wait", "--wait-timeout", "60"])
        info = json.loads(run(["docker", "inspect", CONTAINER]).stdout)[0]
        host = info["HostConfig"]
        record("service runs non-root, read-only, without capabilities or docker socket",
               info["Config"]["User"] == "10001:10001" and host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"]
               and not any("docker.sock" in m for m in host["Binds"] or []), info["Config"]["User"])
        record("service is healthy", info["State"]["Health"]["Status"] == "healthy", info["State"]["Health"]["Status"])
        write = run(["docker", "exec", CONTAINER, "sh", "-c", "touch /data/x"], check=False)
        record("data directory is read-only inside the container", write.returncode != 0)
        tunnel = yaml.safe_load(render_compose(root, tunnel=True))
        record("tunnel mode publishes no host port and isolates the service network",
               "ports" not in tunnel["services"]["subs"] and tunnel["networks"]["internal"]["internal"] is True
               and tunnel["services"]["subs"]["networks"] == ["internal"])
        proc = run(["docker", "compose", "-f", "-", "config", "--quiet"], input=render_compose(root, tunnel=True).replace(
            f"{root}/secrets/tunnel.env", os.devnull), check=False)
        record("tunnel-mode compose file is valid", proc.returncode == 0, proc.stderr.strip()[-200:])

        token = fixtures.TOKENS["test"]
        status, headers, body = http_get(f"/s/{token}")
        record("user page is served with a QR code per format", status == 200 and body.count(b"<svg") == 4, str(status))
        status, _, body = http_get(f"/s/{token}/v2ray")
        share = base64.b64decode(body).decode().splitlines() if status == 200 else []
        record("test user's v2ray subscription lists Vision and XHTTP for alpha and Vision for gamma",
               len(share) == 3 and sum("type=xhttp" in s for s in share) == 1, f"{len(share)} links")
        record("unknown token gets 404", http_get("/s/" + "q" * 43 + "/v2ray")[0] == 404)
        alice = base64.b64decode(http_get(f"/s/{fixtures.TOKENS['alice']}/v2ray")[2]).decode()
        record("alice's token does not return test's content", fixtures.UUIDS["test"] not in alice and fixtures.UUIDS["alice"] in alice)

        for link in share[:2]:
            name = links.parse_vless(link)["type"]
            port = 18110 if name == "tcp" else 18111
            path = os.path.join(work, f"client-{name}.json")
            with open(path, "w") as fh:
                json.dump(xray_client_config(link, port), fh)
            os.chmod(path, 0o644)
            run(["docker", "rm", "-f", CLI], check=False)
            run(["docker", "run", "-d", "--name", CLI, "-p", f"127.0.0.1:{port}:{port}", "-v", f"{path}:/config.json:ro", XRAY_IMAGE])
            time.sleep(2)
            record(f"Xray client from the v2ray subscription connects ({name})", curl_via(f"socks5h://127.0.0.1:{port}") == "200")

        if MIHOMO:
            for mode in ("privacy", "split"):
                status, _, body = http_get(f"/s/{token}/clash-{mode}")
                profile = yaml.safe_load(body)
                profile["tun"]["enable"] = False  # no root here; routing and DNS stay as served
                mixed = free_port()
                profile["mixed-port"] = mixed
                profile["proxy-groups"][0]["proxies"] = [profile["proxies"][0]["name"]]
                home = os.path.join(work, f"mihomo-{mode}")
                os.makedirs(home, exist_ok=True)
                path = os.path.join(home, "config.yaml")
                with open(path, "w") as fh:
                    yaml.safe_dump(profile, fh, allow_unicode=True)
                check = run([MIHOMO, "-d", home, "-f", path, "-t"], check=False)
                record(f"mihomo -t accepts the served {mode} profile", check.returncode == 0, check.stdout.strip()[-120:])
                mihomo_proc = subprocess.Popen([MIHOMO, "-d", home, "-f", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(6)
                record(f"Mihomo client with the {mode} profile connects", curl_via(f"http://127.0.0.1:{mixed}") == "200")
                mihomo_proc.send_signal(signal.SIGTERM)
                mihomo_proc.wait(timeout=10)
                mihomo_proc = None
        else:
            record("Mihomo client checks skipped (MIHOMO not set)", True)

        # Revocation and fail-closed behaviour with the container running.
        reduced = {k: v for k, v in fixtures.TOKENS.items() if k != "bob"}
        catalog2, tokens2, _, _ = build.build(collected, reduced, ["alice", "test"], legacy_links)
        publish(catalog2, tokens2)
        time.sleep(1)
        record("revoked token gets 404 without restart", http_get(f"/s/{fixtures.TOKENS['bob']}/v2ray")[0] == 404)
        run(["docker", "run", "--rm", "--user", "0:0", "--network", "none", "--entrypoint", "sh", "-v", f"{root}:{root}",
             "-w", root, IMAGE, "-c", "rm data/tokens.json"])
        record("missing token table gives 503", http_get(f"/s/{token}/v2ray")[0] == 503)
        publish(catalog2, tokens2)
        record("restored data serves again", http_get(f"/s/{token}/v2ray")[0] == 200)

        run(compose + ["restart"])
        time.sleep(3)
        rows = run(["docker", "exec", CONTAINER, "python", "-m", "subs.accesslog", "--days", "1"]).stdout
        record("access log survives a restart and records users, not tokens",
               "test\tv2ray" in rows and all(t not in rows for t in fixtures.TOKENS.values()), rows.replace("\n", " | ")[:200])
        logs = run(compose + ["logs"], check=False).stdout
        record("container logs contain no tokens", all(t not in logs for t in fixtures.TOKENS.values()))

        run(compose + ["down", "--remove-orphans"])
        run(["docker", "run", "--rm", "--user", "0:0", "--network", "none", "--entrypoint", "sh", "-v", f"{root}:{root}",
             "-w", root, IMAGE, "-c", "rm -rf ./* ./.[!.]*"])
        left = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}", "-q"]).stdout.split()
        record("removal leaves no containers or files", not left and not os.listdir(root))
    finally:
        if mihomo_proc:
            mihomo_proc.kill()
        if args.keep:
            print(f"kept: {root} {work}")
        else:
            run(compose + ["down", "--remove-orphans"], check=False)
            run(["docker", "rm", "-f", CONTAINER, SRV, CLI], check=False)
            if os.path.isdir(root) and os.listdir(root):
                run(["docker", "run", "--rm", "--user", "0:0", "--network", "none", "--entrypoint", "sh", "-v", f"{root}:{root}",
                     "-w", root, IMAGE, "-c", "rm -rf ./* ./.[!.]*"], check=False)
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
