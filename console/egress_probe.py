"""Checks a proxy egress through a temporary Xray (plan-egress-console §3.3); shared by the console and the node agent.

One local HTTP inbound per egress is routed to that egress, so any proxy type Xray speaks can be checked the same way,
and the check address tells the exit IP and country. The console's status service uses it for egress no node uses;
the node agent (docker/edge-tools/edge_egress.py) for the egress assigned to its node.

The node's tools image copies this file next to the agent (docker/edge-tools/Dockerfile), so it must stay a single
module with only the standard library and no imports from the console package.
"""
import http.client
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request

PORT = 21000                        # temporary HTTP inbounds on 127.0.0.1, one per egress
ATTEMPTS = 2                        # one retry: a single lost packet is not a failed egress


def parse_exit(text):
    """(ip, country) from a check response: Cloudflare's trace (key=value lines) or JSON with ip and country."""
    try:
        doc = json.loads(text)
        if isinstance(doc, dict):
            return str(doc.get("ip") or ""), str(doc.get("country") or doc.get("country_iso") or doc.get("loc") or "")
    except ValueError:
        pass
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    return fields.get("ip", ""), fields.get("loc", "")


def failed(error):
    return {"ok": False, "latency_ms": None, "exit_ip": "", "country": "", "error": error[:200]}


def probe(xray, outbounds, url, user_agent, timeout=10):
    """{tag: {"ok", "latency_ms", "exit_ip", "country", "error"}} for each Xray outbound."""
    if not outbounds:
        return {}
    config = {"log": {"loglevel": "none"}, "inbounds": [], "outbounds": [], "routing": {"rules": []}}
    ports = {}
    for i, outbound in enumerate(outbounds):
        ports[outbound["tag"]] = PORT + i
        config["inbounds"].append({"tag": f"p{i}", "listen": "127.0.0.1", "port": PORT + i, "protocol": "http"})
        config["outbounds"].append(dict(outbound, tag=f"o{i}"))
        config["routing"]["rules"].append({"type": "field", "inboundTag": [f"p{i}"], "outboundTag": f"o{i}"})
    try:
        proc = subprocess.Popen([xray, "run", "-c", "stdin:"], stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {tag: failed(f"xray: {type(exc).__name__}") for tag in ports}
    results = {}
    try:
        proc.stdin.write(json.dumps(config).encode())     # credentials stay off the command line
        proc.stdin.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _port_open(PORT + len(outbounds) - 1):
            time.sleep(0.1)
        for tag, port in ports.items():
            results[tag] = _check_through(port, url, user_agent, timeout)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return results


def _port_open(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def _check_through(port, url, user_agent, timeout):
    proxy = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    error = ""
    for _ in range(ATTEMPTS):
        start = time.monotonic()
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": user_agent}), timeout=timeout) as resp:
                ip, country = parse_exit(resp.read(4096).decode("utf-8", "replace"))
            return {"ok": True, "latency_ms": int((time.monotonic() - start) * 1000), "exit_ip": ip[:64],
                    "country": country[:8], "error": ""}
        except (OSError, ValueError, http.client.HTTPException) as exc:
            error = type(exc).__name__ + (f" {exc.code}" if isinstance(exc, urllib.error.HTTPError) else "")
    return failed(error)
