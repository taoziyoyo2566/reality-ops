# Plan: IPv6 Dual-Stack Consolidation And Per-Node Egress Family Control

- **Created**: 2026-07-26　**Revised**: 2026-07-26 (rev2 — direction changed after
  upstream research, see §2)
- **Status**: PROPOSAL — under discussion, not approved, nothing implemented
- **Branch**: none yet. Investigation ran from `fix/monitor-integrity`, which is
  unrelated. A dedicated branch is required before any edit.
- **Scope**: `jp10`, `usca`, `jpntt` (verified working container IPv6).
  `ams`, `dcc` are IPv4-optimised by operator decision.
  `de`, `jp05`, `kagoya` deferred — unreachable during investigation.

---

## 1. Verified Current State (measured 2026-07-26)

All facts measured on live nodes, not inferred from code.

### 1.1 IPv6 is ingress-only by construction

The `_IPv6` and `_IPv4` subscription entries carry the **same uuid, port, inbound
tag and Reality parameters**; only the address literal differs. Both reach the
same Xray process, inbound, and outbound.

Source: `roles/reality_single/tasks/main.yml:360-365`,
`roles/reality_multi/tasks/main.yml:333-338`, port binding at
`roles/reality_single/tasks/main.yml:254-266`.

**Consequence**: "IPv4 node" and "IPv6 node" are two door numbers on one node.
No outbound setting can make one entry behave differently from the other.

### 1.2 Per-node IPv6 status

| Node | Role | Host GUA | Container IPv6 egress | Emits `_IPv6` link |
|---|---|---|---|---|
| jp10 | single | `2a14:7dc0:160:11c1::6f05` | WORKS | yes |
| usca | single | `2602:fd37:10a:b6::a` | WORKS | yes |
| jpntt | single | `2001:2c0:100:417:18:cafe:609:1` | WORKS | yes |
| ams | multi | `2607:8700:9300:8888::2` | NONE | yes |
| dcc | multi | `2607:8700:5500:32ec::2` | NONE | yes |
| sg | single | `2a14:7dc0:101:11fd::b550` | NONE | yes |
| dzire, hk-hn, hk-hn2 | — | ULA only | n/a | no (correct) |
| de, jp05, kagoya, spt, hkcod12 | — | unknown | unknown | yes |

Verified inside the container network namespace on the WORKS nodes:

```
https://ipv6.icanhazip.com -> 2a14:7dc0:160:11c1::6f05  (200)
https://api6.ipify.org     -> 2a14:7dc0:160:11c1::6f05  (200)
https://v6.ipinfo.io       -> 200
```

The returned address is the host GUA, translated by NAT66. Docker 29 enables
`ip6tables` by default, so the masquerade rule exists without the playbook
asking for it.

`ams` / `dcc` lack container IPv6 because `reality_multi` never writes the docker
daemon IPv6 config — that task exists only in `reality_single`
(`roles/reality_single/tasks/main.yml:94-103`). `sg` is single-role but its
`daemon.json` holds only log options, so the task has not run there.

### 1.3 Why traffic still exits over IPv4 where IPv6 works

**Cause 1 — RFC 6724 destination sorting with a ULA source.** The container's
only IPv6 source address is the ULA `fd00::/80`. A ULA source paired with a GUA
destination is demoted (label 13 vs label 1) while IPv4 is self-matching.
Measured in the jp10 container netns:

```
getent ahosts google.com
  1. 142.250.21.138      <- all 6 IPv4 first
  ...
  7. 2404:6800:400b:c005::65   <- all 4 IPv6 after
```

Go's `net` package implements the same RFC 6724 sorting internally
(`sortByRFC6724`), so this is not a glibc-only effect — it applies to Xray's
own dialer. Note Go does **not** read `/etc/gai.conf`, so the usual
administrative override does not reach Xray; this is the substance of
[Xray-core issue #3052](https://github.com/XTLS/Xray-core/issues/3052).

The freedom outbound is `"settings": {}`
(`roles/reality_single/templates/config.json.j2:142-146`,
`roles/reality_multi/templates/config.json.j2:117-121`), so `domainStrategy`
defaults to `AsIs` and Xray inherits that ordering. Every established outbound
connection observed on jp10 was IPv4.

**Cause 2 — `ipinfo.io` has no AAAA record** (measured `AAAA_count=0`). The
"your IP" line there is structurally IPv4-only regardless of configuration.
Test with `test-ipv6.com`, `ipv6.icanhazip.com`, or `v6.ipinfo.io`.

### 1.4 Client source IP is preserved into Xray

Ingress uses `ip6tables` DNAT rather than the userland proxy, so the real client
address reaches Xray:

```
from [2001:268:9883:6042:c57a:e6fc:163c:c738]:43748 accepted tcp:... [user-reap >> direct]
from 61.53.130.223:20972 accepted tcp:... [user-hui >> direct]
```

At least one user connects over IPv6 today.

### 1.5 No node has a spare routable IPv6 prefix

| Node | Address | Prefix | Nature |
|---|---|---|---|
| jp10 | `2a14:7dc0:160:11c1::6f05` | /48 on-link | shared segment, one usable address |
| usca | `2602:fd37:10a:b6::a` | /64 on-link | one usable address |
| jpntt | `2001:2c0:100:417:18:cafe:609:1` | /64 on-link | one usable address |

`proxy_ndp=0` on all three. Giving containers real GUAs would need `ndppd` plus
provider cooperation. **NAT66 as configured is correct** and already yields the
host GUA as egress address. This is a configuration problem, not a topology one.

### 1.6 Node endpoint domains are A-only

`jp10<hash>.taoziyoyo.de`, `usca<hash>`, `jpntt<hash>` all resolve A-only, no
AAAA. Current `_IPv6` entries work only because they embed an IPv6 **literal**,
bypassing DNS. Consolidating onto the domain requires adding AAAA records —
routine DNS work, listed as a prerequisite in §3.

### 1.7 Xray version and feature availability

Nodes run **25.12.8**; latest upstream is **v26.3.27** (2026-03-27).
`docker-build/dockerfile` fetches `releases/latest` at build time, so a rebuild
picks up the new version with no Dockerfile change. `xray_image` is pinned to
`:latest` (`group_vars/all/main.yml:3`) — a rebuild silently changes every
node's future pull and leaves no rollback target.

`domainStrategy` valid values: `AsIs`, `UseIP`, `UseIPv4`, `UseIPv6`,
`UseIPv4v6`, `UseIPv6v4`, `ForceIP`, `ForceIPv4`, `ForceIPv6`, `ForceIPv4v6`,
`ForceIPv6v4`. **`PreferIPv6` does not exist in Xray** — that spelling is
sing-box. "Try IPv6 first, fall back to IPv4" is `UseIPv6v4`.

**Feature probe on the running 25.12.8 binary** (`docker exec … xray -test`,
plus symbol inspection — no running instance touched):

| Check | Result |
|---|---|
| `sockopt.domainStrategy: UseIP` + `happyEyeballs` block | `Configuration OK` |
| `sockopt.domainStrategy: UseIPv6v4` | `Configuration OK` |
| `sockopt.domainStrategy: TotallyBogus` | rejected — `unsupported domain strategy` |
| `prioritizeIPv6` misspelled as `prioritizeIPv6TYPO` | **`Configuration OK` — silently ignored** |
| `strings xray \| grep` for `happyEyeballs`, `prioritizeIPv6`, `tryDelayMs`, `maxConcurrentTry`, `interleave` | all present |

Two conclusions. First, **happyEyeballs is implemented in the version already
running** — this work does not depend on the upgrade. Second, **unknown keys
inside `happyEyeballs` are silently dropped**, so a typo disables the feature
with no error anywhere. `sockopt.domainStrategy` itself *is* validated. The
implementation must therefore verify behaviour, not just a green `-test`.

---

## 2. Direction (rev2)

Operator requirement, restated: **a per-node switch controlling whether that node
uses IPv4 or IPv6, adjustable over time**, because some VPS have better IPv6 than
IPv4 and that can change. Plus consolidation of the split `_IPv4` / `_IPv6`
subscription entries into one node entry.

### 2.1 Use `sockopt.happyEyeballs`, not a hard `domainStrategy` pin

Upstream now implements RFC 8305 connection racing at the sockopt layer. This
fits the requirement better than pinning a family:

- Both families are attempted concurrently with a staggered delay, and the first
  successful connection wins. If a node's IPv6 degrades, connections fall to
  IPv4 automatically — **per connection, without a hard failure or long
  timeout**. That is precisely the "IPv6 is not always better" concern, handled
  adaptively rather than by a static declaration.
- The per-node variable then expresses a *preference* (`prioritizeIPv6`), not a
  hard pin. Flipping a node is a one-line `host_vars` change.

**Critical constraint**: the two mechanisms are mutually exclusive. Setting
freedom's own `domainStrategy` to any non-`AsIs` value resolves the domain to an
IP first and **invalidates `sockopt.domainStrategy` and its happyEyeballs**.
So `settings` must stay `{}` and everything goes in `streamSettings.sockopt`.
Conveniently this means the existing `settings: {}` line is not touched at all.

Resulting outbound shape:

```json
{
  "protocol": "freedom",
  "settings": {},
  "streamSettings": {
    "sockopt": {
      "domainStrategy": "UseIP",
      "happyEyeballs": {
        "tryDelayMs": 250,
        "prioritizeIPv6": true,
        "interleave": 1,
        "maxConcurrentTry": 4
      }
    }
  },
  "tag": "direct"
}
```

`tryDelayMs: 0` (the default) disables racing; 250 is the documented
recommendation. `maxConcurrentTry: 0` also disables it.

### 2.2 One per-node variable

```yaml
# group_vars/all/main.yml
node_egress_family: "auto"    # auto | ipv6 | ipv4
```

| Value | Rendered outbound | Effect |
|---|---|---|
| `auto` (default) | no `streamSettings` block — unchanged | today's behaviour, byte-identical config, no restart |
| `ipv6` | sockopt + happyEyeballs, `prioritizeIPv6: true` | races both, prefers IPv6 |
| `ipv4` | sockopt + happyEyeballs, `prioritizeIPv6: false` | races both, prefers IPv4 |

```yaml
# host_vars/jp10.yml, usca.yml, jpntt.yml
node_egress_family: ipv6
```

`ams` / `dcc` stay at `auto` — they have no container IPv6, so there is nothing
to race and no reason to change their config.

**Why `auto` emits nothing**: every node that has not opted in keeps a
byte-identical `config.json`, needs no restart, and carries no risk. The change
surface stays limited to the nodes being modified. This is the main risk control
of the plan.

### 2.3 Known limitation — TCP only

Happy Eyeballs applies to TCP. For UDP, Freedom ignores the sockopt
`domainStrategy` and **forcibly prefers IPv4**. Any UDP that traverses the
tunnel (QUIC / HTTP-3) therefore continues to egress over IPv4 regardless of this
setting. How much traffic that represents in practice is unmeasured and should
be checked during validation rather than assumed.

### 2.4 Subscription consolidation

Drop the `_IPv6` duplicate; one entry per node using the node domain. For nodes
that should also accept IPv6 *clients*, add an AAAA record for that node's
endpoint domain (§1.6) — ordinary DNS work, no code involvement.

### 2.5 Xray upgrade — independent, still worth doing

Not a prerequisite (§1.7 proves the feature exists in 25.12.8), so it should be
sequenced separately rather than bundled:

1. Rebuild `taoziyoyo2566/xray_docker` via buildx (picks up v26.3.27
   automatically).
2. **Pin `xray_image` to a version tag** instead of `:latest`, so the running
   version is declared in the repo and rollback has a target.
3. Deploy with `--tags update_image` — the ordinary path is
   `when: images | length == 0` and will **not** upgrade an existing image.
4. Restart containers.

**Release landscape.** Running 25.12.8 (2025-12-08). Latest **stable** is
v26.3.27 (2026-03-27); v26.4.13 through v26.7.11 are all flagged `prerelease`,
so `releases/latest` in the Dockerfile correctly tracks stable only. The gap
spans three substantive releases: v26.1.23, v26.2.6, v26.3.27.

**Relevant to this deployment** (VLESS + REALITY + raw TCP):

| Change | Version | Relevance |
|---|---|---|
| REALITY: warnings for non-443 ports and Apple `dest` — both stated to easily get server IPs blocked | v26.3.27 | **High — see §6.4** |
| REALITY: server auto-probes target's `maxUselessRecords` in four tiers at startup, default 32 | v26.3.27 | camouflage improves with no config change |
| REALITY: fixed server not promptly closing the target connection after entering bidirectional copy | v26.3.27 | resource-leak fix on long-running nodes |
| uTLS updated — new Firefox/Safari fingerprints, X25519MLKEM768 | v26.3.27 | links use `fp=chrome` / `fp=firefox`; note `fp` is client-side, so client version matters too |
| Lower instantaneous memory at startup | v26.2.6 | matters most on `ams` / `dcc`, which run 23–25 containers each |
| REALITY client prints explicit alert on receiving the target's real certificate (potential MITM or redirection) | v26.1.23 | better field diagnostics |
| Go 1.26 migration, full inline compilation | v26.3.27 | general performance |
| `allowInsecure` removed | v26.2.6 | **not applicable** — verified zero occurrences in this repo |

Not relevant here: TUN inbound, Hysteria 2, XHTTP, Finalmask / XICMP / XDNS,
WireGuard, mKCP, `process` routing rules.

**Expected side effect of upgrading**: nodes matching the §6.4 conditions will
begin emitting REALITY warnings in their logs. That is the intended signal, not
a regression.

### 2.6 The prerelease line (v26.4.13 → v26.7.11) — do not follow it yet

Prerelease bodies are stubs (`See <link to the newest tag>`), so the only way to
read them is the commit range: 183 commits, 300 files between v26.3.27 and
v26.7.11.

**Hard blocker — `minClientVer` default.**
`REALITY server: Set default "minClientVer": "26.3.27" (change it at your own
risk)` landed after v26.6.27, so it ships in v26.7.11. A REALITY server that does
not explicitly set `minClientVer` now rejects any client older than 26.3.27.
The failure is silent: the client receives 0 bytes, traffic is pushed into the
REALITY fallback, and nothing is logged.

Worse, mihomo / Clash.Meta **hardcodes its REALITY client version as 1.8.2**, so
it can never satisfy the check — every mihomo user would be locked out
permanently, not merely until they update. See
[3x-ui#5922](https://github.com/MHSanaei/3x-ui/issues/5922) and
[mihomo#2967](https://github.com/MetaCubeX/mihomo/issues/2967).

Mitigation if this line is ever adopted: set `minClientVer` explicitly in the
REALITY server block rather than relying on the default.

**Where upstream development is actually going**, filtered to what touches this
deployment:

| Change | Version | Note |
|---|---|---|
| `Direct/Freedom outbound: Add ipsBlocked … apply a default safe policy` | v26.4.15 | applies **without config** |
| `Direct/Freedom outbound: Add finalRules … with default safe policies` | v26.5.3 | private/reserved IPs blocked by default for VLESS/VMess/Trojan/SS/Hysteria/WG inbound traffic; ordinary browsing unaffected |
| `Direct/Freedom outbound: Add blockDelay to finalRules (30~90s default)` | v26.5.9 | |
| `Direct/Freedom outbound: Prefer IPv4 for finalRules' "AsIs"` | v26.5.9 | interacts with §2.1 — revisit the happyEyeballs design if this line is adopted |
| `Config: Rename inbounds' clients/accounts to users` | v26.5.9 | templates use `"clients"`; backward compatibility unverified |
| Geodata / DomainMatcher memory and startup reductions | v26.4–26.6 | helps `ams` / `dcc` (23–25 containers each) |
| `Xray-core: Forbid unencrypted outbounds on public Internet for VLESS and Trojan` | v26.6.27+ | not applicable — outbound here is freedom |
| `XHTTP & WS & HU & gRPC servers: Require sockopt.trustedXForwardedFor` | v26.6.27+ | not applicable — raw TCP only |

The bulk of the line (TUN, Hysteria 2, Finalmask / XICMP / XDNS / XMC, XHTTP,
WireGuard) does not apply to a VLESS + REALITY + raw TCP server fleet.

**Known open issues against v26.3.27 itself** — the version this plan upgrades
*to*, so these should be tracked:

- [#6256](https://github.com/XTLS/Xray-core/issues/6256) REALITY client
  ClientHello too large, causing TCP fragmentation and dropped connections.
- [#6356](https://github.com/XTLS/Xray-core/issues/6356) REALITY fails when the
  target's certificate TLS record exceeds a hardcoded 8192-byte limit. Directly
  relevant to `dest` selection (§6.4) — a dest whose chain is too large breaks
  the node.
- [#5966](https://github.com/XTLS/Xray-core/issues/5966) XTLS Vision padding
  rejected by OpenSSL 3.5.5. This is **client-side**; Xray here runs as a static
  binary in an Alpine container, so the servers are not exposed. Noted because
  `usca` and `ams` hosts are Debian 13 / OpenSSL 3.5.6 — irrelevant to the
  containerised server, but relevant if a client is ever run on those hosts.

**Recommendation**: upgrade to stable v26.3.27 and stop there. Re-evaluate when
the v26.7 line is promoted to stable, and treat `minClientVer` as a prerequisite
decision at that point rather than a discovery.

---

## 3. Prerequisites

- AAAA records for the endpoint domains of nodes that should accept IPv6 clients
  (`jp10`, `usca`, `jpntt`). Currently A-only.
- A dedicated branch; the current one is unrelated.

---

## 4. Verification

A green `xray -test` is **not** sufficient evidence (§1.7 — happyEyeballs fields
are silently ignored when misspelled). Acceptance must be behavioural:

1. After restart, confirm the egress family actually changed, from inside the
   container netns: `curl -s https://ipv6.icanhazip.com` and a dual-stack target,
   comparing against the pre-change baseline where dual-stack destinations
   resolved IPv4-first.
2. Confirm established outbound connections now include IPv6 peers
   (`ss -tn` inside the netns) — the pre-change baseline was zero.
3. Confirm un-opted nodes produced a byte-identical `config.json` (diff before
   and after the template change).

---

## 5. Risks

1. **Config changes do not take effect on deploy.** `config.json` is written to
   disk but the container is not restarted; a manual `docker restart` is
   required.
2. **Any deploy reaching the gist step rewrites every user's subscription
   globally**, independent of `--limit`. Use `--skip-tags gist` while iterating;
   treat the consolidation rollout as one deliberate global rewrite.
3. **Subscription entries change identity.** Merging removes `_IPv6` entries from
   every user's client; a client's selected node may reset.
4. **Silent misconfiguration.** A typo in a `happyEyeballs` field name passes
   validation and disables the feature — hence §4.
5. **IPv6 egress can trip geo and risk checks.** Streaming and financial sites
   frequently score datacentre IPv6 ranges differently. Validate on jp10 alone
   before extending.
6. **`:latest` image tag has no rollback target** until §2.5 step 2 lands.

---

## 6. Collateral Findings (not required by this direction)

**6.1 `daemon.json` is written destructively.**
`roles/reality_single/tasks/main.yml:94-103` uses `copy` with a hardcoded
full-file `content:`. Measured drift a deploy would destroy: `sg` holds log
rotation `{"max-size":"5m","max-file":"2"}` (would be wiped, logs then grow
unbounded); `usca` was hand-tuned to
`{"ipv6":true,"ip6tables":true,"fixed-cidr-v6":"fd00:dead:beef::/64"}` (would
revert to `fd00::/80`). Fix: merge rather than replace.

**6.2 `reality_multi` never configures docker IPv6.** Moot while ams/dcc stay
IPv4-optimised.

**6.3 `_IPv6` links are emitted by nodes that cannot egress IPv6** (ams, dcc,
sg). Consolidation removes this class of problem by construction.

**6.4 REALITY blocking risk — non-443 ports and Apple `dest`.**
Surfaced by the v26.3.27 changelog, which states that non-443 ports and
"stealing Apple" as `dest` both easily lead to the server IP being blocked, and
adds warnings for each. This deployment does both.

*Non-443 ports*: every user gets a random high port (observed on jp10: 20824,
22479, 23684, 26738, 27315, 30845, 32351, 36370, 48684, 53346 …). No node
listens on 443.

*Apple dest*: `group_vars/all/main.yml:7` sets the global default
`reality_dest: "www.apple.com:443"`. Nodes with no `host_vars` override inherit
it — `ali`, `hkcod12`, `hyd13`, `hyu22`, `hyu24`. Additionally
`host_vars/hk-hn.yml:9` sets `www.icloud.com:443`, also Apple.

The two fixes differ sharply in cost. Changing the Apple `dest` values is cheap
and per-node, subject to the existing X25519 constraint on dest selection.
Moving to 443 is architectural: the current design is one inbound per user on a
dedicated port. VLESS inbounds do support multiple clients, and per-user stats
key on the `email` field rather than the port, so consolidating all users onto a
single 443 inbound is *technically* possible — but `speedlimit.sh` and the
monitor agent may key on per-user ports, so this needs its own analysis before
being treated as a plan.

**This finding is independent of IPv6 and is plausibly higher priority.**
Recorded here because it surfaced during the upgrade research; it deserves its
own plan rather than being folded into this one.

---

## 7. Not Doing

- No IPv6 network re-architecture (§1.5 — NAT66 is correct here).
- No source-based routing (`"source": ["2000::/3"]`); recorded in §1.4 as a
  viable fallback if the consolidation direction reverses.
- `de`, `jp05`, `kagoya` untouched; they follow once the pattern is proven.
- No changes to `domain_suffix` or `subs_base_url`.
