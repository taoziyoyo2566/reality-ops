# Plan: REALITY `dest` And Listening-Port Blocking Risk

- **Created**: 2026-07-26
- **Status**: DECIDED — direction approved 2026-07-26 (§8). **Implementation not
  started and not authorised**; this document records decisions only.
- **Branch**: none yet. A dedicated branch is required before any edit.
- **Origin**: surfaced while researching the Xray upgrade for
  [`plan-ipv6-dualstack-2026-07-26.md`](../ipv6-dualstack/plan-ipv6-dualstack-2026-07-26.md)
  §6.4, then split out because it is independent of IPv6 and higher priority.

---

## 1. Why This Exists

Two upstream facts, both landing on this deployment.

**Xray-core v26.3.27 REALITY changelog:**

> 基于前段时间的经验，非 443 端口、"偷苹果"极易导致服务器 IP 被封锁，故对这两个
> 行为输出警告信息

Non-443 listening ports and Apple domains as `dest` are both stated to easily
get the server IP blocked. Warnings are emitted for each from v26.3.27 onward.

**Xray-core [#6356](https://github.com/XTLS/Xray-core/issues/6356):** REALITY
fails when the target's Certificate TLS record exceeds a **hardcoded 8192-byte
limit**. This is a silent, total failure for the affected node.

---

## 2. Measurement Method

Two traps make naive probing useless; both are handled below.

**X25519 must be probed explicitly.** A default `openssl s_client` handshake
false-positives — it reports success while negotiating some other group. The
correct test restricts the offer to X25519 alone and confirms the negotiated
group. Validated with a negative control: `www.hktvmall.com` returns
`x25519=NO`, matching the historical incident where it broke `hk-hn2`.

**Certificate size must be measured with OCSP stapling requested.** Summing the
chain's DER bytes understates the real record by 1–2.5 KB, because the TLS
Certificate message also carries SCTs and the stapled OCSP response that a
Chrome-fingerprinted client requests. Measure the handshake message directly:

```
openssl s_client -connect <host>:443 -servername <host> -msg -status -alpn h2 </dev/null \
  | grep -oP 'Handshake \[length \K[0-9a-fA-F]+(?=\], Certificate)'
```

Calibration: `www.microsoft.com` measures **8251 bytes** by this method against
the **8273** reported in #6356 — close enough to trust, and correctly over the
8192 limit. The same host measures only 5902 without `-status`, which would have
wrongly cleared it. Residual variance comes from CDN node and SCT count, so
treat every figure as indicative and prefer margin over precision.

**Two portability notes for the probe**, both hit during this investigation:

- OpenSSL 3.x prints `Peer Temp Key:`; OpenSSL 1.1.1 (still on `ali`) prints
  `Server Temp Key:`. Match both, or the probe silently reports `x25519=NO` for
  every host.
- A separate "is TLS 1.3 supported" check is redundant and error-prone: when the
  handshake is already forced with `-tls1_3`, a reported X25519 group *is* the
  TLS 1.3 evidence. An independent grep for the protocol line misfires across
  OpenSSL versions and produced a false `tls13=no` on hosts that were fine.

The probe currently lives only in the session scratchpad. §8 item 6 commits it
to `scripts/`.

---

## 3. Measured Current State (2026-07-26)

All current dests pass TLS 1.3, X25519 and h2. None is broken today.

| Node(s) | `dest` | Cert msg | Margin to 8192 | Apple |
|---|---|---:|---:|:--:|
| dcc | `www.ebay.com` | 7714 | **478** | |
| de | `www.ebay.de` | 7592 | **600** | |
| jp05, jp10, jpntt, kagoya | `www.yahoo.co.jp` | 6598 | 1594 | |
| sg | `shopee.sg` | 5330 | 2862 | |
| ali, hkcod12, hyd13, hyu22, hyu24 | `www.apple.com` *(inherited default)* | 4716 | 3476 | **yes** |
| hk-hn | `www.icloud.com` | 4715 | 3477 | **yes** |
| dzire | `www.flipkart.com` | 4152 | 4040 | |
| spt | `www.walmart.com` | 3722 | 4470 | |
| ams | `www.booking.com` | 3469 | 4723 | |
| hk-hn2 | `www.cathaypacific.com` | 2879 | 5313 | |
| usca | `www.costco.com` | 2759 | 5433 | |

`reality_server_names` is overridden alongside `dest` in 13 `host_vars` files and
matches in each. The five nodes with no override inherit
`group_vars/all/main.yml:6-7` — `["www.apple.com","images.apple.com"]` /
`www.apple.com:443`.

---

## 4. Three Distinct Problems

### P1 — Apple `dest` on six nodes

`ali`, `hkcod12`, `hyd13`, `hyu22`, `hyu24` inherit `www.apple.com`; `hk-hn`
sets `www.icloud.com`. Upstream states this class of dest easily gets the server
IP blocked.

Production exposure is narrower than the count suggests: `hkcod12`, `hyd13`,
`hyu22`, `hyu24` are in `[test_nodes]`. The real targets are **`ali`**
(`[special]`) and **`hk-hn`** (`[normal]`).

Cheap to fix — a per-node `dest` + `serverNames` pair, subject to §2's
constraints.

### P2 — Certificate size headroom

`dcc` (478 bytes) and `de` (600 bytes) sit close to a hard limit whose breach
mode is silent and total. One CA rotation adding a cross-signed intermediate, or
a few extra SCTs, pushes them over. Neither is broken now; both are fragile.

Separately, `www.yahoo.co.jp` at 1594 bytes of margin is used by **four nodes at
once** (`jp05`, `jp10`, `jpntt`, `kagoya`). The margin is adequate, but the
concentration means a single upstream change takes down four nodes
simultaneously. Worth diversifying regardless of the margin.

### P3 — Non-443 listening ports (all nodes)

Every user is assigned a random high port; nothing listens on 443. This is the
other behaviour upstream flags.

**The coupling is weaker than expected.** Investigated:

- `speedlimit.sh` takes `<docker_container> <rate>` and shapes with `tc`. **No
  port dependency.**
- The monitor agent reads `docker exec reality_core xray api statsquery` and
  keys on the user/email string, not the port
  (`roles/monitor/templates/agent.py.j2:127`). **No port dependency.**

**The real blocker is elsewhere**: SOCKS5 egress routing builds
`inboundTag: ["user-<name>"]` from `reality_instances`
(`roles/reality_single/templates/config.json.j2:64-67`), which requires one
inbound per user. Consolidating onto a single 443 inbound breaks per-user SOCKS5
routing. It is migratable — Xray routing can match on `user` (the email field)
instead of `inboundTag` — but it is a real change to a working feature.

Two further consequences of consolidation:

- All users would share one REALITY keypair, distinguished by per-user
  `shortIds`, instead of today's per-user keypair. This is the standard
  multi-user REALITY model, but it *is* a change to the current isolation
  posture.
- `spt` runs nginx on 443 already, so it cannot host REALITY there without
  resolving that conflict.

**Recommendation: treat P3 as out of scope for this plan.** It is a real risk but
an architectural change with its own migration, and P1/P2 deliver most of the
risk reduction at a fraction of the cost. Revisit P3 as its own plan.

---

## 5. Candidate Replacements (measured)

| Candidate | Cert msg | Margin | Note |
|---|---:|---:|---|
| `www.lego.com` | 2816 | 5376 | Google Trust Services |
| `www.zara.com` | 2940 | 5252 | DigiCert |
| `www.mtr.com.hk` | 3134 | 5058 | HK-local — fits `hk-hn` |
| `www.uniqlo.com` | 3475 | 4717 | DigiCert |
| `www.hangseng.com` | 4783 | 3409 | HK-local, larger chain |

All pass TLS 1.3 + X25519 + h2.

Selection should also weigh two things this probe cannot measure: whether the
domain is plausible traffic for that node's region, and whether it is already
over-used as a REALITY dest (over-used dests attract fingerprinting). `ali` sits
in China and needs a dest that is both reachable and unremarkable from there —
it deserves individual treatment rather than a fleet-wide default.

---

## 6. Proposed Change

1. **Replace the inherited Apple default.** Change
   `group_vars/all/main.yml:6-7` away from Apple so that no node silently
   inherits it, and give each of the five inheriting nodes an explicit
   `host_vars` pair. Leaving a global default at all is arguably the deeper
   problem — a node that forgets to override gets a dest chosen for someone else.
2. **Give `hk-hn` a non-Apple dest** (`www.mtr.com.hk` fits its region).
3. **Re-point `dcc` and `de`** away from the eBay chains onto candidates with
   >4 KB margin.
4. **Diversify the four `yahoo.co.jp` nodes** so one upstream change cannot take
   all four down.
5. **Commit the probe script** so dest selection is repeatable rather than
   re-derived each time.
6. Optionally add a deploy-time assertion that the chosen dest passes X25519 and
   sits under a configurable cert-size ceiling — this is the check that would
   have caught the historical `hktvmall` incident before rollout.

---

## 7. Risks

1. **Changing `dest` without changing `serverNames` breaks the node silently.**
   `deploy.yml:100-119` already asserts they stay consistent; keep that green.
2. **Config changes do not take effect on deploy** — `config.json` is written but
   the container is not restarted. A manual `docker restart` is required, and
   per project practice the server must be restarted **before** subscriptions are
   refreshed when SNI changes.
3. **Any deploy reaching the gist step rewrites every user's subscription
   globally**, independent of `--limit`. Use `--skip-tags gist` while iterating.
4. **Measurements are point-in-time.** `www.flipkart.com` returned two different
   chains across consecutive probes, indicating CDN variance. Re-probe before
   committing to a candidate.
5. **The 8192 limit may move.** It is a hardcoded constant upstream; a future
   release may raise it, which would change the priority of P2 but not P1.

---

## 8. Decisions (operator, 2026-07-26)

**D1 — Replace the global `reality_dest` default; do not remove it.**
`group_vars/all/main.yml:6-7` moves off Apple to a non-Apple pair. Candidates
measured in §5: `www.lego.com` (2816) and `www.zara.com` (2940) are the smallest
and both pass X25519 + h2. Proposal: `www.zara.com`, on the grounds that its
DigiCert chain matches the CA already in use across most nodes and its cert size
leaves >5 KB of margin.

Caveat to carry: a shared default reproduces the same concentration risk as
`www.yahoo.co.jp` (§4 P2) if many nodes end up inheriting it. The default is a
safety net, not a recommendation — new nodes should still declare their own.

**D2 — `ali` gets a China-domestic dest.**
Probed **from `ali` itself** (`101.132.117.241`, confirmed `CN`), because
probing from outside reaches different CDN nodes and would not reflect what the
node actually sees:

| Candidate | X25519 | h2 | Cert msg |
|---|:--:|:--:|---:|
| `www.aliyun.com` | yes | yes | 2840 |
| `www.bilibili.com` | yes | yes | 3891 |
| `www.jd.com` | yes | **no** | 3416 |
| `www.taobao.com` | yes | yes | 5295 |
| `www.zhihu.com` | yes | yes | 5540 |
| `www.baidu.com`, `www.qq.com`, `www.xiaomi.com`, `www.meituan.com` | **no** | — | — |

Four major domestic sites fail X25519 outright — a fresh confirmation that the
explicit probe in §2 is mandatory rather than a formality.

Proposal: **`www.bilibili.com`**. `www.aliyun.com` has the smaller chain, but
`ali` runs on Alibaba Cloud, so a REALITY dest pointing at the provider's own
site is a narrower, more distinctive traffic pattern. `bilibili.com` is
high-volume consumer traffic that is unremarkable from any Chinese IP, and its
3891-byte chain still leaves >4 KB of margin. `www.jd.com` is excluded for
lacking h2.

Open sub-question: `ali` sits in `[special]` and its role is not documented
here. If it serves clients *inside* China rather than acting as an exit, the
plausibility argument above may need revisiting.

**D3 — The four `[test_nodes]` are deferred.**
`hkcod12`, `hyd13`, `hyu22`, `hyu24` keep the inherited default for now and are
handled when promoted. Note they will inherit whatever D1 lands on, so they stop
being Apple-based as a side effect of D1 without any per-node work.

**D4 — P3 (443 consolidation) is deferred to its own plan**, not accepted as a
standing risk. §4 P3 records the investigation so that plan does not restart
from zero: `speedlimit.sh` and the monitor agent are **not** port-coupled; the
blocker is SOCKS5 routing via `inboundTag`, plus the shared-keypair and
`spt`-nginx-on-443 consequences.

### Resulting work list (not started)

| # | Change | Nodes |
|---|---|---|
| 1 | Replace global `reality_dest` / `reality_server_names` default | `group_vars/all/main.yml:6-7` |
| 2 | Non-Apple dest | `hk-hn` (`www.icloud.com` → `www.mtr.com.hk`) |
| 3 | China-domestic dest | `ali` (→ `www.bilibili.com`) |
| 4 | Move off tight eBay chains | `dcc` (478 B margin), `de` (600 B margin) |
| 5 | Diversify shared dest | 2 of `jp05` / `jp10` / `jpntt` / `kagoya` |
| 6 | Commit the probe script | `scripts/` |
| 7 | Optional deploy-time X25519 + cert-size assertion | `deploy.yml` |

Every item requires a manual `docker restart` to take effect (§7.2), and the
SNI-change ordering rule applies: restart the server before refreshing
subscriptions.

---

## 9. Not Doing

- No 443 migration in this plan (§4 P3).
- No changes to `domain_suffix`, `subs_base_url`, or the subscription structure.
- No IPv6 work — see the sibling plan.
