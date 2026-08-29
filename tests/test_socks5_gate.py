#!/usr/bin/env python3
"""D12 回归测试：socks5 出站与引用它的 routing 规则必须同门同出。

对 reality_single 的 config.json.j2 渲染多组场景，断言：
  A. 输出永远是合法 JSON
  B. routing 里引用的每个 socks5 outboundTag 都存在于 outbounds  （否则 Xray 起不来）
  C. outbounds 里的每个 socks5 tag 都被至少一条 routing 规则引用  （否则凭据白写 —— D12）
"""
import json, sys, pathlib
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                    else pathlib.Path(__file__).resolve().parent.parent).resolve()

USER_A = {"name": "reap", "uuid": "1" * 8 + "-2222-3333-4444-" + "5" * 12,
          "private_key": "STUB", "short_id": "abcdef01", "port": 8443, "expire": ""}
USER_B = {"name": "bob", "uuid": "9" * 8 + "-2222-3333-4444-" + "5" * 12,
          "private_key": "STUB", "short_id": "abcdef02", "port": 8444, "expire": ""}

BASE = {
    "inventory_hostname": "jp10", "domain_suffix": "example.test",
    "server_hash_suffix": "s", "reality_server_names": ["www.apple.com"],
    "reality_dest": "www.apple.com:443", "reality_root_dir": "/opt/reality",
    "reality_data_dir": "/opt/reality/data", "reality_logs_dir": "/opt/reality/logs",
    "reality_log_level": "warning", "reality_instances": [USER_A, USER_B],
    "reality_users": [USER_A, USER_B], "node_alias": "jp10",
    "node_endpoint": "jp10.example.test", "global_ipv4": "203.0.113.10",
    "global_ipv6": "", "acl_matrix": {}, "item": USER_A,
    "reality_socks5": {"enabled": False, "address": "", "port": 1080,
                       "username": "", "password": "", "target_users": []},
}


def profile(**kw):
    p = {"enabled": True, "address": "10.0.0.1", "port": 1080,
         "username": "u", "password": "p", "priority": 40,
         "route": {"hosts": ["jp10"], "users": ["reap"], "domains": [],
                   "ips": [], "protocols": [], "network": "tcp"}}
    p.update(kw)
    return p


SCENARIOS = [
    ("本机命中 + 有规则 → 出站与规则都应存在", "jp10", True,
     {"jpntt_isp": profile()}, True),
    ("本机不命中（route.hosts=[jp05]）→ 两者都不应存在  ← D12", "jp10", True,
     {"jpntt_isp": profile(route={"hosts": ["jp05"], "users": ["reap"], "domains": [],
                                  "ips": [], "protocols": [], "network": "tcp"})}, False),
    ("配置完整、主机命中，但无任何规则条件 → 两者都不应存在", "jp10", True,
     {"jpntt_isp": profile(route={"hosts": ["jp10"], "users": [], "domains": [],
                                  "ips": [], "protocols": [], "network": "tcp"})}, False),
    ("profile 未启用 → 两者都不应存在", "jp10", True,
     {"jpntt_isp": profile(enabled=False)}, False),
    ("模块整体关闭 → 两者都不应存在", "jp10", False,
     {"jpntt_isp": profile()}, False),
    ("route.hosts 为空（全体生效）+ 有用户规则 → 都应存在", "kagoya", True,
     {"jpntt_isp": profile(route={"hosts": [], "users": ["bob"], "domains": [],
                                  "ips": [], "protocols": [], "network": "tcp"})}, True),
    ("两个 profile，只有一个命中本机 → 只应出现命中的那个", "jp10", True,
     {"jpntt_isp": profile(),
      "other_isp": profile(priority=50,
                           route={"hosts": ["sg"], "users": ["bob"], "domains": [],
                                  "ips": [], "protocols": [], "network": "tcp"})}, True),
]


def render(host, enabled, profiles):
    env = Environment(loader=FileSystemLoader(str(REPO / "roles/reality_single/templates")),
                      undefined=StrictUndefined, trim_blocks=True, keep_trailing_newline=True)
    env.filters["to_json"] = lambda v, **k: json.dumps(v)
    env.filters["bool"] = bool
    env.filters["dict2items"] = lambda d: [{"key": k, "value": v} for k, v in d.items()]
    ctx = dict(BASE)
    ctx["inventory_hostname"] = host
    ctx["socks5_egress"] = {"enabled": enabled, "profiles": profiles}
    return env.get_template("config.json.j2").render(**ctx)


def main():
    failures = 0
    for name, host, enabled, profiles, expect_present in SCENARIOS:
        try:
            out = render(host, enabled, profiles)
        except Exception as e:
            print(f"FAIL  {name}\n      渲染异常 {type(e).__name__}: {e}")
            failures += 1
            continue
        try:
            doc = json.loads(out)
        except json.JSONDecodeError as e:
            print(f"FAIL  {name}\n      非法 JSON: {e}")
            failures += 1
            continue

        ob_tags = {o.get("tag") for o in doc.get("outbounds", [])}
        socks_ob = {t for t in ob_tags if t and t.startswith("socks5-profile-")}
        rule_tags = {r.get("outboundTag") for r in doc.get("routing", {}).get("rules", [])}
        socks_rule = {t for t in rule_tags if t and t.startswith("socks5-profile-")}

        errs = []
        # B: 规则引用的必须存在
        for t in socks_rule - socks_ob:
            errs.append(f"规则引用了不存在的 outbound {t}（Xray 会起不来）")
        # C: 出站必须被引用  ← D12
        for t in socks_ob - socks_rule:
            errs.append(f"outbound {t} 存在但无任何规则引用它 —— 凭据白写（D12）")
        if expect_present and not socks_ob:
            errs.append("预期应出现 socks5 出站，实际没有")
        if not expect_present and socks_ob:
            errs.append(f"预期不应出现 socks5 出站，实际有 {sorted(socks_ob)}")

        if errs:
            failures += 1
            print(f"FAIL  {name}")
            for e in errs:
                print(f"      - {e}")
        else:
            print(f"ok    {name}"
                  f"{'  [socks5 tags: ' + ', '.join(sorted(socks_ob)) + ']' if socks_ob else '  [无 socks5 出站]'}")

    print()
    print(f"{len(SCENARIOS) - failures}/{len(SCENARIOS)} 通过")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
