"""Subscription bodies and the user page (plan-subscription-service §3.4–3.5). Pure functions of the catalog."""
import base64
import html
import io
import urllib.parse

import yaml

from . import links

FORMATS = ("v2ray", "v2ray-full", "clash-split", "clash-privacy")
# User-Agent substrings (lower case) of clients that import a subscription from the page address, so one
# QR code works everywhere (2026-09-16). Clash-family first: several of them embed other names.
CLASH_AGENTS = ("clash", "mihomo", "stash")
V2RAY_AGENTS = ("shadowrocket", "v2rayn", "v2box", "hiddify", "streisand", "nekobox", "nekoray")
PRIVATE_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
                 "100.64.0.0/10", "224.0.0.0/4", "fc00::/7", "fe80::/10", "ff00::/8"]
FOREIGN_DOH = ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"]
# Domestic resolvers, used for node domains (both modes) and for China domains (split mode only).
DOMESTIC_DOH = ["https://223.5.5.5/dns-query", "https://119.29.29.29/dns-query"]
DOMESTIC_BOOTSTRAP = ["223.5.5.5", "119.29.29.29"]


class _NoAliasDumper(yaml.SafeDumper):
    """Write repeated lists in full instead of YAML anchors, which clients display poorly."""

    def ignore_aliases(self, data):
        return True


def user_entries(catalog, user):
    """[(node_name, node, entry)] ordered by label."""
    entries = catalog["users"][user]["nodes"]
    return sorted(((n, catalog["nodes"][n], e) for n, e in entries.items()), key=lambda item: item[1]["label"])


def share_links(catalog, user, full=False):
    """[(name, link)] in display order. full adds the old system's IPv6 literal links."""
    out = []
    for _, node, entry in user_entries(catalog, user):
        if node["state"] == "migrated":
            out.extend(links.edge_links(node["edge"], entry, node["label"]))
            continue
        for link in entry["legacy_links"]:
            parts = links.parse_vless(link)
            if parts["ipv6"] and not full:
                continue
            out.append((node["label"] + (" IPv6" if parts["ipv6"] else ""), link))
    return out


def v2ray(catalog, user, full=False):
    body = "\n".join(link for _, link in share_links(catalog, user, full))
    return base64.b64encode(body.encode()).decode() + "\n"


def _mihomo_proxies(catalog, user):
    proxies, seen = [], set()
    for name, link in share_links(catalog, user, full=False):
        unique, index = name, 2
        while unique in seen:
            unique, index = f"{name} #{index}", index + 1
        seen.add(unique)
        proxies.append(links.mihomo_proxy(unique, links.parse_vless(link)))
    return proxies


def clash_profile(catalog, user, mode):
    """Complete Mihomo profile. privacy: everything through the node; split: China and LAN direct."""
    if mode not in ("split", "privacy"):
        raise ValueError(mode)
    proxies = _mihomo_proxies(catalog, user)
    names = [p["name"] for p in proxies]
    groups = [{"name": "PROXY", "type": "select", "proxies": (["AUTO"] + names) if names else ["REJECT"]}]
    if names:
        groups.append({"name": "AUTO", "type": "url-test", "proxies": names,
                       "url": "https://www.gstatic.com/generate_204", "interval": 300, "tolerance": 50})
    dns = {
        "enable": True, "ipv6": True, "enhanced-mode": "fake-ip", "fake-ip-range": "198.18.0.1/16",
        "fake-ip-filter": ["*.lan", "+.local"], "respect-rules": True,
        "default-nameserver": DOMESTIC_BOOTSTRAP, "nameserver": FOREIGN_DOH,
        "proxy-server-nameserver": DOMESTIC_DOH,
    }
    rules = [f"IP-CIDR{'6' if ':' in cidr else ''},{cidr},DIRECT,no-resolve" for cidr in PRIVATE_CIDRS]
    profile = {
        "mixed-port": 7890, "allow-lan": False, "mode": "rule", "log-level": "warning", "ipv6": True,
        "unified-delay": True,
        "profile": {"store-selected": True},
        "tun": {"enable": True, "stack": "mixed", "auto-route": True, "auto-detect-interface": True,
                "strict-route": True, "dns-hijack": ["any:53", "tcp://any:53"]},
        "dns": dns,
        "sniffer": {"enable": True, "force-dns-mapping": True, "parse-pure-ip": True,
                    "sniff": {"HTTP": {"ports": [80, "8080-8880"], "override-destination": True},
                              "TLS": {"ports": [443, 8443]}, "QUIC": {"ports": [443, 8443]}}},
    }
    if mode == "split":
        profile["geodata-mode"] = True
        dns["fake-ip-filter"] = dns["fake-ip-filter"] + ["geosite:cn"]
        dns["nameserver-policy"] = {"geosite:cn,private": DOMESTIC_DOH}
        rules += ["GEOSITE,cn,DIRECT", "GEOIP,CN,DIRECT"]
    rules.append("MATCH,PROXY")
    profile.update({"proxies": proxies, "proxy-groups": groups, "rules": rules})
    header = ("# 分流模式：国内网站与局域网直连（这些网站会看到你的真实 IP），其余经节点。\n" if mode == "split" else
              "# 隐私模式：除局域网外全部经节点，包括国内网站；TUN 接管 TCP/UDP/IPv6，DNS 经节点规则远端解析。\n")
    return header + yaml.dump(profile, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True, width=1000)


def format_for_agent(user_agent):
    """Subscription format for a client fetching the page address, or None for a browser or unknown client.

    Clash-family clients get the split profile, matching the page's default mode.
    """
    agent = (user_agent or "").lower()
    if any(name in agent for name in CLASH_AGENTS):
        return "clash-split"
    if any(name in agent for name in V2RAY_AGENTS):
        return "v2ray"
    return None


def body(catalog, user, fmt):
    """(content type, text) for a subscription format."""
    if fmt == "v2ray":
        return "text/plain; charset=utf-8", v2ray(catalog, user)
    if fmt == "v2ray-full":
        return "text/plain; charset=utf-8", v2ray(catalog, user, full=True)
    if fmt == "clash-split":
        return "text/yaml; charset=utf-8", clash_profile(catalog, user, "split")
    if fmt == "clash-privacy":
        return "text/yaml; charset=utf-8", clash_profile(catalog, user, "privacy")
    raise KeyError(fmt)


def _qr_svg(text):
    import segno  # only the page needs it
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", xmldecl=False, scale=4, border=2)
    return buf.getvalue().decode()


PAGE_CSS = """
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#5f5f5a;--card:#fff;--line:#deded8;--accent:#1f5f8b}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecece8;--muted:#a3a39c;--card:#20201f;--line:#34342f;--accent:#7fb6dc}}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,sans-serif}
main{max-width:720px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 8px}
p,li{color:var(--muted)}section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0}
input{width:100%;box-sizing:border-box;font:13px ui-monospace,monospace;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
.qr{background:#fff;display:inline-block;padding:4px;border-radius:6px;margin-top:10px}a{color:var(--accent)}
.tag{display:inline-block;font-size:12px;border:1px solid var(--line);border-radius:4px;padding:0 6px;margin-left:6px;color:var(--muted)}
"""


def page(catalog, user, base_url):
    """User page. base_url already contains the token path, e.g. https://host/s/<token>."""
    nodes = [node["label"] for _, node, _ in user_entries(catalog, user)]

    def section(title, fmt, text, clash=False, tag=""):
        url = f"{base_url}/{fmt}"
        deeplink = ""
        if clash:
            link = "clash://install-config?url=" + urllib.parse.quote(url, safe="") + "&name=" + urllib.parse.quote(fmt)
            deeplink = f'<p><a href="{html.escape(link)}">在 Clash 类客户端中打开</a></p>'
        return (f"<section><h2>{html.escape(title)}{f'<span class=tag>{tag}</span>' if tag else ''}</h2>"
                f"<p>{text}</p><input readonly value=\"{html.escape(url)}\">{deeplink}"
                f"<div class=qr>{_qr_svg(url)}</div></section>")

    node_list = "".join(f"<li>{html.escape(n)}</li>" for n in nodes) or "<li>当前没有可用节点</li>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex"><title>订阅</title><style>{PAGE_CSS}</style></head><body><main>
<h1>订阅</h1><p>本页地址与下方地址、二维码都包含你的个人凭据，不要分享或截图发给他人。</p>
<p>本页地址也可以直接作为订阅添加到 Shadowrocket、v2rayN 或 Clash 类客户端（Clash 类默认使用分流模式）；需要其他格式时用下方对应地址。</p>
<section><h2>可用节点</h2><ul>{node_list}</ul>
<p><a href="{html.escape(base_url)}/status">查看所有节点的运行状态与历史</a></p></section>
{section("Clash 类客户端 · 分流模式", "clash-split", "国内网站和局域网直连，其余经节点。国内网站会看到你的真实 IP，但速度快、不易触发国内账号风控。", clash=True, tag="默认")}
{section("Clash 类客户端 · 隐私模式", "clash-privacy", "除局域网外全部经节点，包括国内网站；接管 UDP、IPv6 与 DNS，尽量避免网站获得真实 IP。国内服务会变慢，也可能被要求验证。", clash=True)}
{section("v2rayN / Shadowrocket", "v2ray", "节点订阅。分流与防泄漏由客户端自身的路由设置决定。")}
{section("v2rayN / Shadowrocket · 含 IPv6 地址", "v2ray-full", "同上，另含尚未迁移节点的 IPv6 地址链接，仅在双栈网络使用。")}
<section><h2>选节点的提示</h2>
<p>网站除了看出口 IP，还能读到你设备的系统时区。两者差得越远（例如设备在 UTC+8、出口在美国），
越容易被要求二次验证或被判定为可疑登录。</p><ul>
<li>银行、支付、电商、公司账号这类要紧的，尽量选和你所在时区接近的节点。</li>
<li>分流模式下国内网站直连，时区本来就一致，不受影响。</li>
<li>不建议为此修改设备时区，日历和提醒会跟着错；需要严格一致时，用带防指纹的浏览器。</li></ul></section>
<section><h2>无法通过网络层解决的识别</h2><ul>
<li>手机号、SIM 卡、定位、Wi-Fi 信息与系统时区、语言和地区设置</li>
<li>浏览器指纹、账号历史与登录记录</li>
<li>你在客户端里自行设置为直连的应用或网站</li></ul></section>
</main></body></html>
"""
