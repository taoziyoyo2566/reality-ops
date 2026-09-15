"""VLESS share links and Mihomo proxies for the subscription service.

Pure functions only. Links are built with urllib so every value is percent-encoded the same way
(roadmap C13); legacy links from the old system are parsed, never rebuilt.
"""
import re
import urllib.parse

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SHORT_ID_RE = re.compile(r"^[0-9a-f]{0,16}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
HOST_RE = re.compile(r"^(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+)$")
FINGERPRINT = "chrome"
MIHOMO_XHTTP_MODE = "stream-one"  # Mihomo has no auto; Xray auto picks stream-one under REALITY.


class LinkError(ValueError):
    pass


def _host(endpoint):
    return f"[{endpoint}]" if ":" in endpoint and not endpoint.startswith("[") else endpoint


def edge_links(edge, user, label):
    """[(name, link)] for a migrated node: Vision, then XHTTP when enabled."""
    common = [("encryption", "none"), ("security", "reality"), ("sni", edge["sni"]), ("fp", FINGERPRINT),
              ("pbk", edge["public_key"]), ("sid", user["short_id"])]
    base = f"vless://{user['uuid']}@{_host(edge['endpoint'])}:{edge['port']}?"
    links = [(label, base + urllib.parse.urlencode(common + [("type", "tcp"), ("flow", "xtls-rprx-vision")],
                                                    quote_via=urllib.parse.quote)
              + "#" + urllib.parse.quote(label, safe=""))]
    xhttp = edge.get("xhttp") or {}
    if xhttp.get("enabled"):
        name = f"{label}-xhttp"
        query = common + [("type", "xhttp"), ("path", xhttp["path"]), ("mode", xhttp.get("mode", "auto"))]
        links.append((name, base + urllib.parse.urlencode(query, quote_via=urllib.parse.quote, safe="")
                      + "#" + urllib.parse.quote(name, safe="")))
    return links


def parse_vless(link):
    """Parse a vless:// REALITY link into its parts; raise LinkError when it is not one we can serve."""
    if not isinstance(link, str) or not link.startswith("vless://"):
        raise LinkError("not a vless link")
    parsed = urllib.parse.urlsplit(link)
    if not parsed.username or not UUID_RE.match(parsed.username):
        raise LinkError("invalid uuid")
    if not parsed.hostname or not parsed.port:
        raise LinkError("missing host or port")
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    if query.get("security") != "reality":
        raise LinkError("not a REALITY link")
    if not KEY_RE.match(query.get("pbk", "")) or not SHORT_ID_RE.match(query.get("sid", "")):
        raise LinkError("invalid REALITY public key or short id")
    if not query.get("sni"):
        raise LinkError("missing sni")
    if query.get("type", "tcp") not in ("tcp", "raw", "xhttp"):
        raise LinkError("unsupported transport")
    return {
        "uuid": parsed.username, "server": parsed.hostname, "port": parsed.port, "ipv6": ":" in parsed.hostname,
        "sni": query["sni"], "fp": query.get("fp") or FINGERPRINT, "public_key": query["pbk"],
        "short_id": query.get("sid", ""), "type": query.get("type", "tcp"), "flow": query.get("flow", ""),
        "path": query.get("path", ""), "name": urllib.parse.unquote(parsed.fragment),
    }


def mihomo_proxy(name, parts):
    """A Mihomo vless proxy from parse_vless() output."""
    proxy = {
        "name": name, "type": "vless", "server": parts["server"], "port": parts["port"], "uuid": parts["uuid"],
        "udp": True, "tls": True, "servername": parts["sni"], "client-fingerprint": parts["fp"],
        "encryption": "", "reality-opts": {"public-key": parts["public_key"], "short-id": parts["short_id"]},
    }
    if parts["type"] == "xhttp":
        proxy.update({"network": "xhttp", "alpn": ["h2"],
                      "xhttp-opts": {"path": parts["path"], "mode": MIHOMO_XHTTP_MODE}})
    else:
        proxy["network"] = "tcp"
        if parts["flow"]:
            proxy["flow"] = parts["flow"]
    return proxy
