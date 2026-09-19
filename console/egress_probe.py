"""Checks a proxy egress through a temporary Xray (plan-egress-console §3.3); shared by the console and the node agent.

One local HTTP inbound per egress is routed to that egress, so any proxy type Xray speaks can be checked the same way,
and the check address tells the exit IP and country. A second local inbound (SOCKS5 with UDP) per egress carries one
DNS query over UDP, which tells whether the egress relays UDP; many SOCKS5 proxies do not. The console's status service
uses it for egress no node uses; the node agent (docker/edge-tools/edge_egress.py) for the egress assigned to its node.

The node's tools image copies this file next to the agent (docker/edge-tools/Dockerfile), so it must stay a single
module with only the standard library and no imports from the console package.
"""
import http.client
import json
import os
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request

PORT = 21000                        # temporary HTTP inbounds on 127.0.0.1, one per egress
UDP_PORT = 21500                    # temporary SOCKS5 inbounds with UDP, one per egress
ATTEMPTS = 2                        # scheduled checks retry once: a single lost packet is not a failed egress
UDP_TARGET = ("1.1.1.1", 53)        # a public DNS resolver that answers the UDP check
UDP_TIMEOUT = 3


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
    return {"ok": False, "latency_ms": None, "exit_ip": "", "country": "", "error": error[:200], "udp": None}


def probe(xray, outbounds, url, user_agent, timeout=10, attempts=ATTEMPTS):
    """{tag: {"ok", "latency_ms", "exit_ip", "country", "error", "udp"}} for each Xray outbound.

    "udp" is True or False once TCP works, None when TCP already failed (UDP is not tried then).
    """
    if not outbounds:
        return {}
    config = {"log": {"loglevel": "none"}, "inbounds": [], "outbounds": [], "routing": {"rules": []}}
    ports = {}
    for i, outbound in enumerate(outbounds):
        ports[outbound["tag"]] = PORT + i
        config["inbounds"] += [
            {"tag": f"p{i}", "listen": "127.0.0.1", "port": PORT + i, "protocol": "http"},
            {"tag": f"u{i}", "listen": "127.0.0.1", "port": UDP_PORT + i, "protocol": "socks",
             "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"}}]
        config["outbounds"].append(dict(outbound, tag=f"o{i}"))
        config["routing"]["rules"].append({"type": "field", "inboundTag": [f"p{i}", f"u{i}"], "outboundTag": f"o{i}"})
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
        for i, (tag, port) in enumerate(ports.items()):
            results[tag] = _check_through(port, url, user_agent, timeout, attempts)
            if results[tag]["ok"]:
                results[tag]["udp"] = _udp_works(UDP_PORT + i, attempts)
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


def _check_through(port, url, user_agent, timeout, attempts):
    proxy = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    error = ""
    for _ in range(attempts):
        start = time.monotonic()
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": user_agent}), timeout=timeout) as resp:
                ip, country = parse_exit(resp.read(4096).decode("utf-8", "replace"))
            return {"ok": True, "latency_ms": int((time.monotonic() - start) * 1000), "exit_ip": ip[:64],
                    "country": country[:8], "error": "", "udp": None}
        except (OSError, ValueError, http.client.HTTPException) as exc:
            error = type(exc).__name__ + (f" {exc.code}" if isinstance(exc, urllib.error.HTTPError) else "")
    return failed(error)


def _udp_works(port, attempts):
    """Whether one DNS query over SOCKS5 UDP, through the local inbound and on through the egress, gets an answer."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=UDP_TIMEOUT) as control:
            control.sendall(b"\x05\x01\x00")
            if control.recv(2) != b"\x05\x00":
                return False
            control.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")        # UDP ASSOCIATE
            reply = control.recv(10)
            if len(reply) < 10 or reply[1] != 0:
                return False
            relay = ("127.0.0.1", struct.unpack(">H", reply[8:10])[0])
            query_id = os.urandom(2)
            query = query_id + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x0acloudflare\x03com\x00\x00\x01\x00\x01"
            packet = b"\x00\x00\x00\x01" + socket.inet_aton(UDP_TARGET[0]) + struct.pack(">H", UDP_TARGET[1]) + query
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.settimeout(UDP_TIMEOUT)
                for _ in range(attempts):
                    udp.sendto(packet, relay)
                    try:
                        data, _ = udp.recvfrom(2048)
                    except socket.timeout:
                        continue
                    # SOCKS5 UDP header for an IPv4 source (10 bytes), then the DNS answer with our id
                    if data[10:12] == query_id:
                        return True
    except OSError:
        return False
    return False


def test_rules(xray, rules):
    """"" when Xray accepts these routing rules (with its geosite / geoip data), else the reason it gives."""
    config = {"log": {"loglevel": "none"},
              "outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "blocked", "protocol": "blackhole"}],
              "routing": {"domainStrategy": "IPOnDemand", "rules": rules}}
    try:
        proc = subprocess.run([xray, "run", "-test", "-c", "stdin:"], input=json.dumps(config).encode(),
                              capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"xray: {type(exc).__name__}"
    if proc.returncode == 0:
        return ""
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip().splitlines()
    return (text[-1] if text else f"rc={proc.returncode}").split(" > ")[-1][:200]
