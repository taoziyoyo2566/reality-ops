# Project Memory

Last updated: 2026-08-28 JST

## Xray Image State (Consumer Side)

Last updated: 2026-09-14 JST.

Image build and publication left this repository entirely and now belong to
[`taoziyoyo2566/xray-docker`](https://github.com/taoziyoyo2566/xray-docker).
This repository is only a consumer: it owns no Dockerfile, release pipeline, or
tag contract. The contract and upgrade procedure live in
[`docs/runbooks/xray-image-consumption.md`](runbooks/xray-image-consumption.md).

- The existing single/multi deployment keeps the floating
  `taoziyoyo2566/xray-docker:latest` (`group_vars/all/main.yml:3`). Per roadmap
  §4.1.3 decisions 7 and 9 (2026-09-15) the old implementation is not modified;
  the new single-instance implementation gets its own image variable pinned by
  digest (candidate `v26.3.27` =
  `sha256:fd502666d1a9ca7ea772e2c1638bb83e79bdc12c97cddd5e3755edc7485b3ec7`).
- `[实测]` 2026-09-15 00:00 JST read-only snapshot of the 12 reachable inventory
  nodes: every `reality_*` container reports
  Xray `26.3.27` with restart count 0. Ten nodes run the `fd502666` image;
  `spt` and `kagoya` run the older `b891c9781882` build (image created
  2026-08-27). On `spt` the local `latest` tag already points at `fd502666`, but
  `reality_core` (created 2026-09-12) was not recreated. Per-node detail is in
  roadmap §3.3.
  - This supersedes the 2026-09-14 record that `spt` ran `fd502666`; that record
    described the local tag, not the running container.
- The old repository `taoziyoyo2566/xray_docker` is frozen; its `latest` stopped
  at 2026-08-26. It had roughly 2236 pulls and may have users outside this
  project, but receives no further updates.
- This repository's `Sync Xray Release Images` and `Audit Xray Image Tags` were
  disabled on 2026-08-29 and their files were removed. This repository no
  longer produces any image.
- Nodes do not re-pull automatically; `latest` on different nodes may resolve to
  different digests. Per-node digest reconciliation is tracked in roadmap C12.
- Migration acceptance -- geodata resolution under `--read-only --cap-drop ALL`,
  image size, bad-config behaviour, `XRAY_HEALTH_PORT` -- has still not been
  executed. The three probed nodes starting the image and serving traffic is
  not that acceptance. **Do not record it as passed on the strength of "the new
  repository already fixed it".**

Publisher-side history is retained in
[`phase1-image-release-2026-08-26.md`](reviews/roadmap-xray-xhttp-ipv6/phase1-image-release-2026-08-26.md)
and in this file's git history; current publisher state is authoritative in the
new repository. Planning lives in
[`roadmap-unified-2026-09-18.md`](reviews/roadmap-unified-2026-09-18.md) (supersedes the 2026-08-27 version).

## xray_edge Canary State

Last updated: 2026-09-15 JST. Contract:
[`plan-single-instance-443-2026-09-15.md`](reviews/data-plane-edge/plan-single-instance-443-2026-09-15.md).
Live truth is the node; re-check with the commands below instead of trusting this record.

- `[实测]` `dzire`: `xray_edge` runs from `edge.yml` at `ops@de2b4b4`. First deployed
  2026-09-15 01:11 JST; the unit-file failures of the first two runs were fixed by
  `1ce07d4`. Current container `5a18c7d30da8` (recreated by the remove/redeploy drill),
  image `fd502666`, restart count 0, host port 443 only (API not published), 33 users
  (32 ACL users + `test`), XHTTP disabled, `xray-edge-logrotate.timer` active.
- The old `reality_*` multi instance on `dzire` was fingerprinted before the first run
  (32 containers: ids, creation, restarts, config file list) and stayed identical through
  every run and drill below.
- From the control host: a plain TLS probe to `dzire:443` returns the real
  `www.flipkart.com` certificate; the `test` Vision link connects and egresses from
  dzire's IP. The operator imported the link into a real client and confirmed normal use
  (about 11.7 MB down / 4.1 MB up counted for `test.dzire`).
- Plan §6.2 drills, 2026-09-15 08:47-08:59 JST, all as expected: applier user add and
  remove went through the API without a restart (33 -> 34 -> 33, conf.d restored
  byte-identical); a SOCKS5 route with an invalid IP was rejected by `xray run -test`
  with conf.d and the container unchanged; a container restart kept 33 users; a manual
  `xray-edge-logrotate.service` run succeeded; `edge-remove.yml` (keys kept) removed the
  container, units and files, and the redeploy restored the same public key and conf.d.
- The node REALITY key lives only in `/opt/xray-edge/keys` on `dzire`. The operator's
  test link is `~/.local/share/reality-ops/edge-subs/test_dzire.txt` on `spt` (0600).
- `[实测]` `usca` (original single node, 1 core): `xray_edge` deployed with `edge.yml` at
  `ops@f542911` on 2026-09-15 09:02 JST in one run. Image `fd502666`, restart count 0,
  host ports `0.0.0.0:443` and `[global v6]:443` (API not published; the old
  `reality_core` keeps `127.0.0.1:10085`), 33 users (32 ACL + `test`), timer active.
  The drift check read 32 users from the old single instance through `lsi`, matching ACL.
  The old `reality_core` was fingerprinted before and was identical after.
- From the control host: plain TLS probes to `usca:443` over IPv4 and IPv6 return the real
  `www.costco.com` certificate; the `test` IPv4 and IPv6 links both connect. The access
  log recorded the control host's real public IPv4 and IPv6 as sources, so bridge
  networking preserves IPv6 client addresses. Memory: edge about 10 MiB, old
  `reality_core` about 75 MiB. The operator confirmed both links work in a real client.
- 2026-09-15 09:03 JST, `ops@df5e7b9`: `usca` redeployed to publish IPv6 on `[::]:443`
  (container recreated as `bd5ec11bc3e2`; public key, conf.d and 33 users unchanged; old
  `reality_core` unchanged). The node domain has A and AAAA records pointing at usca. The
  test link file now holds one domain link. Through that domain, forced IPv4 and forced
  IPv6 both connect, and an unforced Xray client on a dual-stack host chose IPv6.
- Address-family findings (see the plan §10): which link a client uses only decides the
  client-to-node family. Sites such as ipinfo see the node's own egress: inside the bridge
  container IPv6 is a ULA (`fd00::`) masqueraded to the node's global IPv6, so Go's
  RFC 6724 ordering prefers IPv4 for dual-stack destinations while IPv6-only
  destinations still work; that is why ipinfo shows IPv4 and reports IPv6 as detected.
- `usca` reduced drills, 2026-09-15 10:14-10:16 JST, all as expected, old `reality_core`
  unchanged throughout: applier user add and remove through the API without a restart
  (33 -> 34 -> 33, conf.d restored to hash `d8680296db7e`); container restart kept 33
  users with `0.0.0.0:443` and `[::]:443` listening; `edge-remove.yml` (keys kept) then
  `edge.yml` restored the same public key and conf.d. Current container `ea66b872264a`.
  Bad-config rejection and the manual logrotate run were covered on `dzire` only.
- `[实测]` 2026-09-15 `dzire` XHTTP (plan §6.3), `ops@fb64bc2` then `ops@10f368f`:
  `edge_xhttp_nodes: ["dzire"]` added the `@xray-edge-xhttp` fallback (`xver: 1`) and the
  `vless-xhttp` inbound (33 users each); the plain TLS probe still returns the real
  `www.flipkart.com` certificate. Test files: `test_dzire.txt` (Vision + XHTTP links) and
  `test_dzire.clash.yaml` (Mihomo Vision + XHTTP `stream-one`).
- `[实测]` dzire REALITY handshakes (old multi and new instance alike) took about 5.3 s:
  REALITY resolves the target on every handshake, the first resolver in dzire's host
  `resolv.conf` (`4.2.2.4`) dropped 50-70% of queries, and Go waits 5 s per unanswered
  server. Mihomo's 5 s dial timeout made it fail (Vision 2/5, XHTTP 1/5). `10f368f` pins
  the edge container DNS on dzire to `8.8.8.8`/`1.1.1.1` with `timeout:1 attempts:2`
  (container recreated as `0886271dadee`); handshakes dropped to 0.3 s and Xray and Mihomo
  Vision and XHTTP all connected 5/5 at about 1 s. The dzire host resolver and the old
  multi instance are unchanged and still affected.
- Local compatibility (`tests/edge/client_compat_local.py`, 9/9): Xray-core (v2rayN's core)
  Vision and XHTTP auto/packet-up/stream-up/stream-one, Mihomo v1.19.31 Vision and XHTTP
  stream-one/stream-up/packet-up. v2rayN 7.24.9 parses `type=xhttp` links; Shadowrocket
  2.2.92 lists XHTTP support since 2.2.67 but not REALITY with XHTTP explicitly, so it needs
  a device test.
- `[实测]` 2026-09-15 host DNS survey (12 reachable nodes): only dzire
  has a lossy resolver. netcup, legend, jp10 and kagoya use Tailscale `100.100.100.100`,
  which is also reachable from their `reality_core` container network.
- Roadmap U5 feasibility (client-side IP leak protection, roadmap §7.2): STUN through
  dzire returns dzire's address for Xray and Mihomo over Vision and XHTTP. Test-only Mihomo
  privacy profiles `test_dzire.privacy.clash.yaml` and `test_usca.privacy.clash.yaml` (0600,
  control host, generated outside the repo; the usca one was built from its link because
  usca has not been redeployed since the Clash snippet was added) pass `mihomo -t` and, with
  TUN off, egress and STUN through the node, block IPv6-only targets on dzire instead of
  going direct, and answer DNS with fake-ip.
- The privacy-profile device test and QR import are deferred to the S4 subscription service
  (operator decision 2026-09-15); Clash device testing is not required for now. A merged
  test profile (`test_privacy.clash.yaml`, usca + dzire Vision + dzire XHTTP) exists only on
  the control host. No temporary publishing: a Tailscale one-shot download and a Cloudflare
  quick tunnel were tried and withdrawn with no downloads.
- `[实测]` 2026-09-15 operator device test of dzire XHTTP: the XHTTP link imported by QR
  code works, and ipleak.net showed no leak. dzire's log for `test.dzire` from non-control
  sources (08:00-14:07 container time) had 479 connections, 434 on `vless-xhttp` and 45 on
  Vision, including 185 to ipleak.net and 5 UDP destinations, so the check ran through the
  node.
- `[实测]` 2026-09-15 15:11 JST `usca` XHTTP, `ops@8be3422` (`edge_xhttp_nodes: ["dzire", "usca"]`):
  container restarted in place (`ea66b872264a`), 33 users on both inbounds, fallback with
  `xver: 1`, old `reality_core` unchanged, plain TLS probe still returns `www.costco.com`.
  From the control host Xray (forced IPv4 and IPv6) and Mihomo connected over Vision and
  XHTTP 6/6; usca's log attributed each request to the right inbound and address family.
  Test files: `test_usca.txt` (Vision + XHTTP) and `test_usca.clash.yaml`.
- `[实测]` 2026-09-15 15:51 JST `usca` egress Happy Eyeballs (S8 canary), `ops@1348713`: the
  deploy run was interrupted after the applier step, which had already restarted the
  container in place with `direct` `sockopt` `UseIP` + `happyEyeballs` (tryDelayMs 250,
  prioritizeIPv6 false, interleave 1, maxConcurrentTry 4); remaining playbook tasks are
  idempotent and unchanged. Old `reality_core` unchanged, fallback still `www.costco.com`,
  33 users. Via usca: IPv4-only -> IPv4, IPv6-only -> IPv6, dual-stack 4/4 -> IPv4 (IPv4
  connects well within 250 ms from usca); XHTTP connects. Selecting IPv6 when IPv4 is slow
  is not yet observed.
- `[实测]` 2026-09-15 `legend` (target `shopee.sg`): first `xray_edge` deploy at
  `ops@c19fe80` with Vision and Happy Eyeballs egress (no XHTTP; no vault path yet). Old
  `reality_core` fingerprint unchanged; container running, image `fd502666`, `0.0.0.0:443`
  and `[::]:443`, 30 users (29 ACL + `test`), timer active. The node domain has A and AAAA;
  plain TLS probes over IPv4 and IPv6 return the real `*.shopee.sg` certificate. Via legend:
  IPv4-only -> IPv4, IPv6-only -> IPv6, dual-stack 6/6 -> IPv4. Before deploy, legend's
  own TCP connects to google, cloudflare, youtube, facebook and netflix took 0-3 ms over both
  families with no failures, so any IPv6 advantage is not visible at connect time from the
  node. Test files: `test_legend.txt`, `test_legend.clash.yaml`.
- `[操作者实测]` 2026-09-15: the operator imported the usca and legend test links by QR code
  on a device and reported that both connect. The report does not say which usca link
  (Vision or XHTTP) was tested, and it includes no IPv4-versus-IPv6 comparison for legend.
- `[实测]` 2026-09-15 about 18:00 JST read-only summary of the three nodes: running, restart
  count 0, not OOM-killed, 15-26 MiB; `json-file` `max-size 10m`/`max-file 3`, read-only root
  filesystem, `cap_drop ALL`. Accepted connections: dzire 841, usca 2328, legend 252, almost
  all `test` (no real users migrated). `error.log` holds only startup lines. usca's 13
  `blocked` entries were UDP 1900/5353 device LAN discovery hitting `block-private`. The
  logrotate status files record 2026-09-15; logs are 38-355 KB, so no rotation has run
  yet. Edge log timestamps are UTC+8 (container zone), unlike the hosts (UTC, JST, EDT).
  On legend, `sudo` warns `unable to resolve host legend` (host file issue, not changed).
- S3 close-out status and remaining gaps (last-good recovery, API user modify, SOCKS5
  membership change on a node, first real rotation) are in the roadmap §7 "S3 验收状态".
- Not yet done: forced IPv4-versus-IPv6 comparison on the client-to-legend leg; long-term
  observation of whether Happy Eyeballs ever selects IPv6 for dual-stack destinations; an
  actual size- or daily-triggered rotation of edge logs.
- Compose form ([`plan-edge-compose`](reviews/data-plane-edge/plan-edge-compose-2026-09-15.md),
  `ops@0538f92`): dzire, usca and legend were migrated on 2026-09-15 (records below). On any
  other node `edge.yml` from `0538f92` on deploys the compose form directly; on a node still
  in the S3 form it performs the migration, which needs its own authorization.
- `[实测]` 2026-09-15 20:1x JST `dzire` migrated to the compose form (`edge.yml --limit dzire`,
  rc 0, changed 14). The first run stopped at the drift check with no change: the operator had
  deleted the untracked `users/shuaiqi.yml`, while the old instances on dzire, usca and legend
  still carry `shuaiqi`. Operator decision: `shuaiqi` is no longer needed; remove it only from
  the new instances and leave old instances to S7 (`edge_drift_old_only_users: ["shuaiqi"]`).
  Before/after: old multi fingerprint unchanged (32 containers `62bbb81c`, config `0cf63b0e`);
  `xray_edge` recreated with compose label `xray-edge`, restart 0, healthy, 12.7 MiB; every
  `docker inspect` parameter identical (image, bridge, 443 binding, DNS override, user,
  read-only, tmpfs, cap drop, no-new-privileges, pids, memory and swap, nofile, log limits,
  restart policy, mounts); public key unchanged; users 33 -> 32 on both inbounds, and the only
  config difference from `last-good` is `shuaiqi` removed. `xray_edge_logrotate` runs as
  10000, no network, read-only, 0.5 MiB; a manual rotation run exits 0 and writes its status
  file. The host timer, both units, `/etc/xray-edge`, the host status file and
  `/opt/xray-edge/bin` are gone. From the control host both `test_dzire.txt` links connect
  (Vision and XHTTP, HTTP 200 in about 1.2 s) and the TLS fallback still returns
  `www.flipkart.com`.
- `[实测]` 2026-09-15 about 20:30 JST dzire compose-form drills (plan-edge-compose §6.2 item 5),
  old multi fingerprint unchanged throughout: a temporary synthetic user added to and removed
  from `state/desired.json` went through the applier container as `api` both times (32 -> 33
  -> 32, restart count 0, conf.d back to `5b86737d`); `docker compose restart` kept 32 users
  on both inbounds with 443 listening; `edge-remove.yml` (keys kept) left no containers, only
  `/opt/xray-edge/keys`, nothing listening on 443 and no host residue; `edge.yml` then
  restored the same public key, conf.d and users, with every `docker inspect` parameter as
  before (the tools image was already on the node, so it was not sent again). Both test
  links connected afterwards. `edge-remove.yml` needs `--vault-password-file` like `edge.yml`.
  Next-day rotation check on dzire: see the 2026-09-16 12:34 UTC entry below.
- `[实测]` 2026-09-16 12:34 UTC dzire rotation check (read-only): `access.log` was rotated
  automatically at 00:29:44 UTC, the first hourly `logrotate-loop` tick after the UTC day change
  (container time is UTC, logrotate 3.22.0). `access.log.1.gz` (last write 23:34:16) and
  `access.log.2.gz` (the 2026-09-15 11:30 drill) are compressed. `error.log` was last rotated at
  23:33:29 UTC by a manual `logrotate -f` in an earlier session (sudo journal), not by the loop; it
  is empty now, so `notifempty` skips it. Both containers up 25 h, restart 0, and the loop logged no
  errors in 36 h. `[缺口]` `maxsize 50M` has not triggered (access.log 70 KB), and the 14-file limit
  cannot be observed until 14 daily rotations have run.
- `[实测]` 2026-09-15 about 20:35 JST `usca` migrated to the compose form (`edge.yml --limit usca`,
  rc 0, changed 13). Old `reality_core` container fingerprint unchanged and its
  `/opt/reality/data/reality_core/config.json` still dated 2026-09-14 (a first broader hash also
  covered `/opt/reality/monitor/state/traffic_cache.json`, which the monitor agent rewrites
  every minute, so it changed; the old-config hash is now taken over `/opt/reality/data` only).
  `xray_edge` recreated with compose label, restart 0, healthy, 13.1 MiB (old `reality_core`
  76 MiB, 560 MB available); every `docker inspect` parameter identical, including
  `0.0.0.0:443` and `[::]:443`; public key unchanged; users 33 -> 32, the only config
  difference from `last-good` being `shuaiqi` removed; Happy Eyeballs (`UseIP`) kept.
  `xray_edge_logrotate` confined as on dzire; manual rotation exits 0. Host timer, units,
  `/etc/xray-edge`, status file and `bin/` gone. From the control host: Vision and XHTTP
  through the domain, and Vision forced to IPv4 and to IPv6, all return HTTP 200; ipify
  through the link reports usca's own IPv4 and IPv6; the TLS fallback over IPv4 and IPv6
  still returns `costco.com`.
- `[实测]` 2026-09-15 about 20:50 JST `legend` migrated to the compose form (`edge.yml --limit legend`,
  rc 0, changed 13; root filesystem 72% -> 75% of 4.9 G after loading the tools image). Old
  `reality_core` fingerprint and `/opt/reality/data` hash unchanged (config dated 2026-09-13).
  `xray_edge` recreated with compose label, restart 0, healthy, 10.4 MiB; `docker inspect`
  parameters identical apart from mount order; public key unchanged; users 30 -> 29, the only
  config difference being `shuaiqi` removed; Happy Eyeballs kept; no XHTTP. Logrotate container
  confined, manual run exits 0; host timer, units, `/etc/xray-edge`, status file and `bin/`
  gone. From the control host the Vision link connects through the domain and forced to IPv4
  and IPv6; ipify through it reports legend's own IPv4 and IPv6; the TLS fallback over both
  families returns `*.shopee.sg`. All three canary nodes now run the compose form. Local: `test_compose.py` 6/6,
  `e2e_local.py` 42/42 three times in a row (now including last-good recovery, UUID and
  short id changes, SOCKS5 membership change, compose logrotate). Buildx gives a new image
  ID on every build, so the tools image tag is a hash of its build inputs.

```bash
# Compose form (dzire, usca, legend since 2026-09-15)
ssh dzire "sudo docker compose -f /opt/xray-edge/compose.yaml ps; sudo docker compose -f /opt/xray-edge/compose.yaml run --rm -T --pull never applier verify --root /opt/xray-edge --container xray_edge --image \$(sudo docker inspect -f '{{.Config.Image}}' xray_edge)"
```

- `[实测]` 2026-09-16 S7 preflight of the remaining nodes (read-only): all x86_64 with the Compose
  plugin (dcc lowest at 2.33.1, Docker 28.0.1). 443 and 80 free everywhere except kagoya, where
  authentik's Caddy held both until the operator had it stopped. Memory available: ams 353 MB,
  hk01 260 MB, hk02 257 MB, jp10 254 MB, jp05 233 MB (469 MB total, no swap), netcup 3.6 GB,
  kagoya 563 MB. Disk: **jp05 root 96% with 89 MB free, too little for the tools image**; hk02 13%
  free of 41 G; others fine. Docker daemon IPv6 (`fixed-cidr-v6`) is set on kagoya, hk01, hk02,
  jp10 and netcup, absent on ams, dcc, jp05 and spt, so those four have no IPv6 egress inside
  containers.
- `[实测]` 2026-09-16 08:05 JST `kagoya` got its first `xray_edge` (compose form, `edge.yml --limit
  kagoya`, rc 0, changed 13). The operator stopped authentik's Caddy first (`docker compose stop
  caddy`, restart policy `unless-stopped`, so it stays stopped across reboots), which freed 443 and
  80. Old `reality_core` fingerprint, `/opt/reality/data` hash and its 28 users unchanged. New
  instance: healthy, restart 0, 11.5 MiB, `0.0.0.0:443` and `[::]:443`, 28 users (27 ACL +
  `test`, `shuaiqi` excluded), no XHTTP, no Happy Eyeballs, nothing outside `/opt/xray-edge`.
  Root filesystem 66% of 69 G free. From the control host the link connects over the domain and
  forced IPv4, egress is kagoya's own IPv4, and the TLS fallback returns `edge01.yahoo.co.jp`.
  After the operator added an AAAA record the same link works over IPv6 with no node-side change:
  forced IPv6 returns 200, egress IPv6 equals the node's address, IPv6 fallback probe correct.
- `[实测]` 2026-09-16 08:33 JST forced one rotation on all four nodes
  (`docker compose exec logrotate logrotate -f ...`, operator authorized): each produced
  `access.log.1.gz` and `error.log.1.gz`, truncated `access.log` to 0 (dzire 712 B, usca 752 KB,
  legend 39 KB, kagoya 1.2 KB before), updated the state file and left Xray running with restart
  count 0; a connection through each node's test link then wrote to the same `access.log` again,
  so `copytruncate` works. No automatic rotation had happened before that: the rule is
  `daily` plus `maxsize 50M`, the largest log was 752 KB, and the logrotate container runs in
  **UTC**, so the daily boundary is 00:00 UTC (09:00 JST), not node local time.
- `[实测]` Container time zones: the Xray image carries `Asia/Shanghai`, so access logs are UTC+8;
  the tools image (alpine) has no `TZ` and runs in UTC; hosts differ (dzire UTC, usca and kagoya
  JST, legend EDT). Both images ship tzdata, so a `TZ` environment variable would change either
  (verified locally with `TZ=UTC` and `TZ=Asia/Tokyo`). Not changed; time zone affects only
  server-side log reading and the daily rotation moment, never clients (REALITY and TLS use
  absolute time, monitor stores epochs). It would start to matter if per-day quotas or expiry
  dates were added.
- Operator decisions 2026-09-15/16: old instances and old code are **not deleted during the
  migration**; whether to delete them is decided after all users are stable on the new
  subscription (roadmap §9 item 11). On kagoya the operator chose to stop authentik's Caddy to
  free 443 rather than share the port.

## Subscription Service (S4) State

- `[实测]` 2026-09-16 19:33 JST first deployment on `spt` (operator ran `subs.yml -K`; an earlier
  run at 18:41 stopped before any change because the operator's shell had no SSH agent with the node
  key). Only `test` is published; `subs_public_base_url` is `https://sub.taoziyoyo.com`, and dzire, usca,
  legend and kagoya are `migrated`. Tunnel token and the `test` token are in the vault
  (`vault_subs_tunnel_token`, `vault_subs_tokens`), never printed. `vault_subs_tokens` was removed on
  2026-09-18 after the console imported it (see Management Console State).
  - Containers: `reality_subs` (UID 10001, read-only, `cap_drop ALL`, no-new-privileges, 128 MB, only
    on the `internal: true` network, `/data` read-only, `/db` writable, healthy) and
    `reality_subs_cloudflared` (UID 65532, read-only, `cap_drop ALL`, internal + egress networks). No
    host port; `reality-monitor` and the host `cloudflared` still active.
  - Public (`https://sub.taoziyoyo.com`): `/healthz` 200; the `test` page 200 with four QR codes and the
    page CSP; `v2ray` 8 links (dzire Vision + XHTTP, usca Vision + XHTTP, legend, kagoya, plus the old
    jp05 and jp10 links that `test` is entitled to); `v2ray-full` 10; both Clash profiles 8 proxies and
    pass `mihomo -t`; unknown token, unknown format and `/` all 404.
  - Removing `tokens.json` gave 503 on both subscription and `/healthz`; restoring it served 200 again
    without a restart; `docker compose restart` kept the access log. The access log (10 rows) and
    container logs contain no token.
  - Cloudflare rewrites `Referrer-Policy` to `same-origin` (a zone-level security-header transform);
    the page also sends `<meta name="referrer" content="no-referrer">` and loads nothing external.
  - 2026-09-16 first device test: Shadowrocket scanned the page address and got the HTML page, so it
    reported that it could not fetch servers. The page address now serves known subscription clients
    directly (plan §10, `render.format_for_agent`): Shadowrocket, v2rayN/NG, V2Box, Hiddify, Streisand
    and NekoBox get `v2ray`; Clash, Mihomo and Stash get `clash-split`; browsers and unknown clients get
    the page. Redeployed about 19:57 JST (image `reality-subs:1f00c3809496`); simulated Shadowrocket,
    v2rayN, Clash Verge, FlClash and Safari requests each received the expected format.
  - `[操作者实测]` 2026-09-16 about 20:00 JST: one QR code of the page address imported into Shadowrocket
    and the nodes connect. The access log shows Shadowrocket receiving `v2ray` (over IPv6 and IPv4)
    and iPhone Safari receiving the page. Node access logs since the morning rotation had `test`
    connections on dzire (231), usca (713) and kagoya (24); none on legend.
  - `[操作者实测]` 2026-09-16 ip125.com via dzire: with split routing the domestic probe showed the real
    IP (China direct rule, expected); after switching the client to global mode only dzire's IP was shown
    and the IPv6 check failed, matching dzire's lack of IPv6 egress (no real IPv6 exposed). Client and
    WebRTC/DNS items were not reported.
  - `[操作者实测]` 2026-09-17: with the client (Shadowrocket) in global mode, the WebRTC check on ip125.com listed the
    device's real public IP instead of the node's, with both the Vision and the XHTTP link. On usca the access log has no
    UDP entries during the test (the last ones that day were NTP and DNS earlier, via `vless-xhttp`), so the STUN traffic
    left the device outside the proxy. This contradicts the 2026-09-15 XHTTP result on dzire, which is not to be relied on.
    Every WebRTC row showed the device's real IPv4 address, so IPv4 STUN traffic went out directly; the client's reason
    is not identified. Deferred (operator, 2026-09-17): investigate first and choose a stable, reliable approach before
    deciding how to implement it (roadmap U5, §7.2); it must not switch off WebRTC or other device functions.
  - Not yet done: device checks of legend and of an XHTTP node, Clash import of both modes, the
    privacy-mode ipleak check (IPv4, IPv6, WebRTC, DNS), real-user tokens and notification.

Contract: [`plan-subscription-service`](reviews/subscription-service/plan-subscription-service-2026-09-15.md)
(APPROVED 2026-09-15, D1 option B: compose with its own `cloudflared` tunnel; hostname not chosen yet).

- Implemented locally, not deployed: `subs/` (stdlib HTTP server, renderer, catalog builder,
  SQLite access log), `docker/subs/`, `roles/subs_service`, `subs.yml`, `subs-remove.yml`,
  `group_vars/all/subs.yml` (`subs_enabled_users: ["test"]`, canary nodes `migrated`).
  Vault variables `vault_subs_tokens` and `vault_subs_tunnel_token` do not exist yet.
- Local verification: `tests/subs/test_subs.py` 19/19 (also inside the image); `e2e_local.py`
  22/22 with Xray (Vision, XHTTP) and Mihomo v1.19.31 (privacy and split profiles, TUN off).
  All 584 links in the 332 old per-user files on `spt` parse.
- 2026-09-15 local run of the `subs.yml` aggregation with every node treated as legacy: 13 nodes
  collected, no node contacted. **Correction 2026-09-16:** that run reported `test_jp05.json` and
  `test_jp10.json` as files the ACL does not allow, but the harness playbook lived in a scratch
  directory and `acl.yml` reads users from `{{ playbook_dir }}/users`, so it loaded no users and
  every ACL was empty. The real ACL allows `test` on jp05 and jp10 (`users/test.yml` has
  `hosts: ['jp05', 'jp10']`), so those files are legitimate, not leftovers. The operator's option A
  (the ACL decides; files it does not allow are left out and listed under `ignored`; a node the ACL
  allows without a legacy file still stops the build) was chosen on that wrong premise; the rule is
  kept because it removes nothing the ACL allows. Any local harness for these plays must run from
  the repository root or set `playbook_dir`-independent paths.
- The user page also explains that websites compare the device's system time zone with the exit
  IP's region (2026-09-16 operator request): prefer a node in a nearby time zone for accounts that
  matter, split mode is unaffected because domestic sites stay direct, and changing the device
  time zone is not advised. Per-node region labels were **not** added: that needs operator-maintained
  metadata, since a host's own time zone does not indicate where the machine is (dzire UTC, legend EDT).
- Design details to confirm on devices: both Clash profiles resolve node domains through
  domestic DoH (`223.5.5.5`, `119.29.29.29`) so that a node can be reached before the tunnel is
  up; the split profile makes Mihomo download GeoIP/GeoSite (about 21 MB) from its default
  jsdelivr URLs on first load, which may fail on some networks in China.

## Management Console (P1) State

Contract: [`plan-console-phase1`](reviews/console/plan-console-phase1-2026-09-16.md) (APPROVED 2026-09-17,
D-C1 to D-C5). Roadmap position: P1 in [`roadmap-unified-2026-09-18.md`](reviews/roadmap-unified-2026-09-18.md) §7.0.

- `[代码]` Implemented 2026-09-17 and committed as `ops@b4dd28a` (2026-09-18): `console/` (FastAPI web and
  report services, SQLite), `docker/console/`, `roles/console_service`, `console.yml`, `console-remove.yml`,
  `group_vars/all/console.yml`; node side `docker/edge-tools/edge_reporter.py`, applier metrics listener and report
  token, `reporter` compose service (off unless the node is in `edge_report_nodes`), node registration files written
  by `edge.yml`; `subs.yml` now only deploys the subscription service, whose `data/` is handed to the console.
  D-C5 (old/new user comparison only warns; `edge_drift_old_only_users` removed) is in the same working tree.
- `[实测]` 2026-09-17 local verification: `tests/edge` render and reporter unit tests 33/33, `tests/edge/test_compose.py`
  7/7, `tests/console/test_compose.py` 7/7, `tests/console/test_console.py` 19/19 (also inside the console image),
  `tests/subs/test_subs.py` inside the subs image OK, `tests/test_socks5_gate.py` 7/7,
  `tests/edge/e2e_local.py` 42/42 with the new tools image, `tests/console/e2e_local.py` 29/29 (real Xray node with
  reporter, console web and report, subscription service, real client: issue, fetch, connect, traffic reported,
  Xray restart with reporter self-restart and restart detection, report token rotation with the old token refused,
  hide/show, rotate and revoke, no tokens in logs). Memory: web and report about 17-39 MiB each, reporter 7-15 MiB.
  The registration and user-export templates were rendered with synthetic data and parsed by the console; the
  export carries only `name`, `groups`, `hosts`, `deny_hosts`.
- `[实测]` Xray v26.3.27 `/debug/vars` gives per-user cumulative counters (read only). A restart is detected from
  Xray's `core: Xray ... started` line in `error.log` as well as from falling counters; the first e2e run showed that
  counters alone miss a restart when new traffic exceeds the old totals.
- `[上游]`+`[实测]` 2026-09-17: the v26.3.27 API listener is TCP only (`app/commander/commander.go`
  `net.Listen("tcp", ...)`), and a dokodemo API inbound on a Unix socket did not come up locally, so the reporter can
  still reach `127.0.0.1:10085` inside the shared network namespace. Accepted as a code-level constraint (plan §7).
- `[实测]` 2026-09-17 22:40-22:52 JST deployed from the working tree later committed as `ops@b4dd28a`:
  `edge.yml` on dzire, usca, legend and kagoya wrote their registration files (`/opt/reality-console/registry`);
  `subs.yml` handed `/opt/reality-subs/data` to the console (`10002:10001`, setgid) and `db/` to its group, without
  restarting the subscription service; `console.yml` started `reality_console_web` and `reality_console_report`
  (healthy) and `reality_console_cloudflared`. The first import took the `test` token and showed the four nodes;
  both publishes succeeded (4 nodes, 1 user); the `test` subscription fetched through `sub.taoziyoyo.com` has 6 links
  (dzire and usca with XHTTP, legend, kagoya). `report.taoziyoyo.com`: `/healthz` 200, `/users` 404, `POST /report`
  without a token 401.
- `[实测]` 2026-09-17 23:05-23:35 JST node reporting enabled on dzire, then usca, legend and kagoya
  (`edge_report_nodes`; each Xray restarted once for the metrics listener, `xray_edge` containers not recreated).
  The reporter sends its own User-Agent because Cloudflare answers Python's default one with 403 (error 1010).
  All four report every 5 minutes with 443 listening and no console alerts; `test` traffic from device use appears per
  node.
- `[实测]` 2026-09-18 00:14-00:38 JST drills: with `reality_console_report` stopped for 18 minutes the home page listed all
  four nodes as not reporting; after the restart each node's spooled reports arrived within one cycle (sequence numbers
  contiguous, one reporter instance per node, nothing dropped), traffic totals did not double-count and the alerts
  cleared. Hiding `legend` in the console removed it from the `test` subscription on the device and showing it again
  brought it back (5 then 6 links).
- `[实测]` 2026-09-18 the console on `spt` was redeployed from the working tree (ahead of `ops@b4dd28a`, not committed):
  pages send `Referrer-Policy: same-origin` (header and meta) because browsers send `Origin: null` on form posts
  under `no-referrer`, which the Origin check refuses. The operator then reported that the page actions work from the
  browser.
- `[代码]` 2026-09-18 working tree, not deployed: after “立即重新发布” the home page shows the publish result
  (`/?published=1#publish`).
- `[实测]` 2026-09-18 `vault_subs_tokens` removed from `group_vars/all/vault.yml` (decrypted and re-encrypted in
  memory; the other 27 keys unchanged; Ansible resolves the variable as absent). Subscription tokens now exist only in
  the console database and its daily backups; restore procedure in `docs/operations.md` §14.7.
- `[代码]` 2026-09-18 working tree, not deployed: each publish records whether the content changed
  (`内容与上次相同` / `内容已更新`), and the home page explains that the manual publish only republishes.

## Node Status Page State

Contract: [`plan-node-status-page`](reviews/console/plan-node-status-page-2026-09-18.md) (APPROVED 2026-09-18, D-S1 to D-S6).

- `[代码]` 2026-09-18 working tree, not deployed: `console/status.py` (`status` service: Xray client as a child process,
  one local HTTP proxy port per node and transport, direct check first, events after 3 failures / 2 successes, daily
  known/unknown seconds, `status.json` into the subscription data directory), new tables `probe_result`, `probe_state`,
  `incident`, `status_daily`, `status_meta`; admin page `/status` with event notes; `subs/statuspage.py` and the route
  `/s/<token>/status` (not access-logged); applier `probe` section (a freedom outbound that redirects every
  connection of the probe user to `status_probe_target`, derived from `status_check_url`);
  `group_vars/all/status.yml` (`status_probe_nodes` empty, credentials `vault_status_probe_uuid` /
  `vault_status_probe_short_id` not yet in the vault); console image copies `/usr/bin/xray` from `edge_xray_image`.
- `[实测]` 2026-09-18 local: applier render tests with a `step3-probe` golden case (existing golden files unchanged),
  `tests/console/test_status.py` 27/27, `tests/console/test_console.py` 19/19, `tests/console/test_compose.py` 10/10,
  `tests/subs/test_subs.py` 23/23 (subs image); Ansible rendered the desired state for `dzire` with the probe on and off
  (off: no probe user, `probe.enabled` false; on: user added, registration users unchanged, classify `restart`;
  missing credentials and a name clash fail the assert without printing values); `--syntax-check` passes for
  `console.yml`, `console-remove.yml`, `edge.yml`, `edge-remove.yml`, `subs.yml`. The vault snippet in operations §14.8
  was run against a copy of the vault only.
- `[实测]` 2026-09-18 local end-to-end (`tests/console/e2e_local.py`, 48/48) with a real node, the probe account,
  the status service and the subscription service: probes through Vision and XHTTP, a wrong XHTTP path opens a partial
  event and closes it on recovery, stopping Xray opens an outage timed at the first failed round, hiding the node is
  recorded as maintenance, cutting the status service's network records no data and opens nothing, an event note
  reaches the user status page, and the probe credential reaches only the check address (other hosts: no connection or
  Cloudflare 403). Probe traffic 11.8 KiB per probe (about 0.52 GB per target per 30 days at 60 s); `status` container
  52 MiB.
- `[实测]` 2026-09-18 (roadmap C18, fixed in the working tree, not deployed): through a node, an ordinary user reaching
  `http://127.0.0.1.nip.io:10086/debug/vars` (a domain that resolves to loopback) gets HTTP 200 from the node's own
  metrics listener; the literal IP is refused by `block-private`. Routing uses `domainStrategy: IPIfNonMatch`, which
  resolves only when no rule matched, and the default rule always matches. The Xray API port (10085) is reachable the
  same way in principle. Affects every deployed `xray_edge` node; on the new system only `test` holds a subscription.
  Fix in the working tree: routing `domainStrategy` `IPOnDemand` (S3 contract §10). `[实测]` node e2e 44/44 and console
  e2e 49/49 with the fix: literal IP, a name pointing at loopback and one pointing at the Docker gateway are all
  refused, SOCKS5 routing per user unchanged. Old-system templates still use `IPIfNonMatch` and were not checked.
- `[代码]` 2026-09-18 working tree, not deployed: the console takes its user list from the node registration files as
  well as the exported profiles, so a user is issuable right after `edge.yml` (`console.yml` only refreshes profile
  details). Users without exported profile details show “档案未同步”.
- `[实测]` 2026-09-18 deployed: `edge.yml` on `dzire` first, then the rest (each Xray restarted once); `subs.yml` and
  `console.yml` on `spt`. All four nodes probe normally (6 targets, no alerts, availability 100%); latest probe
  `usca` 141ms, `kagoya` 420ms, `dzire` 927ms, `legend` 880-950ms with one 5535ms round (probably an occasional DNS
  timeout on that node; `dzire` has explicit container DNS for the same reason). Result record:
  [`node-status-page-2026-09-18.changelog.md`](reviews/console/node-status-page-2026-09-18.changelog.md).
- `[未知]` Docker Engine version on `spt` (the status health check avoids `start_interval`, which needs Engine 25+).

## Console Phase 2 State

Contract: [`plan-console-phase2`](reviews/console/plan-console-phase2-2026-09-18.md) (APPROVED 2026-09-18, D-P2-1 to D-P2-9).

- `[代码]` 2026-09-18 working tree, 2a, not deployed: tables `users`, `node_tiers`, `tier_rules`, `settings`; `console/users.py`
  (access = the existing ACL: node tiers, `tier_rules` from `acl_matrix`, `all`, allow, deny wins); web user CRUD, bulk issue
  and export, node tier editing, pending-deploy and expiry alerts; `console.admin import-users` / `node-users`; `console.yml`
  imports once (`roles/console_service/tasks/users_import_doc.yml`); `edge_user_source: console` makes `edge.yml` read each
  node's users from the console; the old/new user comparison and `edge_skip_drift_check` are gone; `console/auth.py`
  (mode `none` only) and `audit_log.actor` (added by migration). Disabled or expired users' tokens are left out of
  `tokens.json`; the web watcher republishes when the day changes.
- `[实测]` 2026-09-18 with the real `users/*.yml`, inventory and `acl_matrix` in a temporary console: 33 users imported,
  no differences against the 4 registered nodes; for all 11 `edge_nodes` the console lists equal the old ACL result
  (UUID and short id included, no duplicates).
- `[实测]` 2026-09-18 found in the pushed `ops@2f7026d`: with `edge_user_source` files, `edge.yml` fails on `jp05` and `jp10`
  because `test` is both in `edge_extra_users` and allowed there by its own `hosts`; a run over all nodes stops at `jp05`
  before changing it. Fixed in the working tree (extras already in the ACL result are not added twice).
- `[实测]` 2026-09-19 00:14-01:00 JST deployed 2a: `console.yml` imported 33 users with no differences; `legacy-remove.yml`
  removed jp05's old instance (container `reality_core`, `/opt/reality`, old monitor agent); `edge.yml` (console source)
  deployed netcup, ams, dcc, jp05, hk01, hk02 and jp10, and left the first four nodes unchanged. All 11 nodes: users
  match the console, 443 listening, reports arriving, status probes succeed; the 7 new nodes are not shown in
  subscriptions yet. Fixed during the rollout (working tree): the node facts (REALITY target check, global IPv6, public
  IPv4) moved from `acl.yml` to `node_facts.yml` because the console source skipped them; the tools image is compared
  by layer digests, because ams and dcc use the classic `overlay2` store whose image IDs differ from the containerd
  store's for the same image. First report of a freshly deployed node gets 401 (the reporter starts before the node is
  registered) and succeeds on the retry five minutes later.
- `[代码]` 2026-09-19, 2b: the node reporter is now the node agent (`docker/edge-tools/edge_reporter.py`
  class `Sync`): every `edge_sync_interval` (60 s) it posts the users running on the node to `POST /sync` on the report
  service and applies the console's list for that node with `xray api inbounduser / rmu / adu` (tools image now carries
  `/usr/local/bin/xray` from `edge_xray_image`); it refuses an empty list or removing more than half the users at once and
  never touches `SYNC_KEEP` (the probe account). The desired state gains `reality.extra_short_ids` (the console's shared
  short id), registration files gain `sync` and `short_ids`; the console builds subscriptions from the users each syncing
  node reports (`users.effective_nodes`), republishes when those change, records `node_sync`, and holds back users whose
  short id the node is not configured for (pending, needs `edge.yml`).
- `[实测]` 2026-09-19 local: `tests/edge/test_agent.py` 6/6, `tests/console/test_sync.py` 5/5, console unit tests
  20 + 35 + 15, edge compose 9/9; console e2e 59/59 with a real node and agent: a user created in the web page was on the
  node 2 s later (sync interval 5 s in the test), connected with the shared short id without an Xray restart, was
  removed when disabled (new connections refused), and was added back by the agent after an Xray restart; the probe
  account was never touched.
- `[实测]` 2026-09-19 2b rollout: `console.yml`, then `edge.yml` on all 11 nodes (15.5 min, `serial: 1`; one Xray
  restart per node for the shared short id). Afterwards all 11 nodes report synced with no pending users, console home
  shows no alerts, and no status probe failed during the rollout. A node's first sync right after enabling gets 409
  (its agent starts before the console sees the new registration); the console shows that error for one more cycle,
  because it records the state the agent reported in its request, and it clears after about a minute.
- `[代码]` 2026-09-19, 2c (deployed 2026-09-18 17:35Z): `console/bot.py` is the Telegram bot (compose service `bot`, only
  when `vault_console_bot_token` is set; admins are `vault_console_bot_admin_ids`). It long-polls with stdlib `urllib`,
  answers private chats only, keeps its offset, username and heartbeat in the `settings` table, and binds accounts
  with one-time codes in `bot_links`. `publish.publish` now takes an `flock` on `/db/publish.lock`: web requests, the
  watcher thread and the bot publish from different threads and processes, and two unserialized publishes could leave
  `tokens.json` and `catalog.json` from different rounds.
- `[实测]` 2026-09-19 local, 2c: `tests/console/test_bot.py` 22/22, compose 13/13, console unit tests 20 + 35 + 15 + 5;
  console e2e 72/72 with the bot container against `tests/console/fake_telegram.py` on the egress network. The bot
  container used about 17 MiB. `spt` reached `api.telegram.org` directly (HTTP 302 in 0.4 s, plan §5 item 5).
- `[实测]` 2026-09-19 live on `spt` (bot deployed 2026-09-18 17:35Z, one administrator): with 50 s long polls the bot
  logged 370 `getUpdates: ConnectionResetError` in 10.5 h (30-40 per hour); the resets landed about 30-35 s into a
  poll. The poll timeout is now 20 s (`fc5fac7`, deployed 04:20Z): 0 resets and no other errors in the next 22 min,
  where the old rate predicts 10 or more. Where the reset comes from (a device on the path or Telegram's side) is
  `[未知]`.
- `[代码]` 2026-09-19 working tree, user migration (plan-user-migration): `users.stage` puts each active user in
  not_issued / waiting / fetched / using (fetched from the subscription service's access log, using = traffic on the
  new nodes in the last 30 days); the home page shows the counts, `/users?stage=` filters, and `POST /users/bulk-bind`
  issues where missing and makes a binding link for each unbound active user (`/users/links`, not stored). The old
  system is not touched by the migration (D-M1).
- `[实测]` 2026-09-19 slow status probes (> 2 s, 3 days): dzire 56, legend 69, netcup 37, others 0-8; mostly 2-4 s,
  not clustered at 5 or 10 s. DNS inside the node namespace: 50-80 ms cold, 2-20 ms warm (dzire, legend, usca).
  TCP connect from spt: dzire ~270 ms, legend ~265, netcup ~170, kagoya ~125, usca and dcc ~30. So the slow probes
  are the long path from spt plus occasional retransmits, not node DNS. legend and usca resolve through Tailscale
  (`100.100.100.100`).
- `[代码]` 2026-09-19 working tree: `edge.yml` runs `serial: [1, "100%"]` (first node alone, then the rest together;
  a failed first batch stops the play by Ansible's default), and the tools image tar is exported with `run_once`.
  The console's form secret is kept in `settings.form_secret`, so pages opened before a restart still post; a
  refused form shows a Chinese page with a same-origin link back. The nodes list shows and hides nodes, one or
  several at once (`POST /nodes/bulk-show`).
- `[代码]` 2026-09-19 working tree: the status page has minute / hour / day views (`?view=`, default hour).
  `status.build_document` adds `hours` (24 clock hours: state by events, checks and successes) and `minutes` (one
  cell per round over the last hour: ok / partial / outage / nodata / maintenance with the failed transports);
  both optional in `subs/statuspage.validate`. `status.latency_hours` feeds the admin page only. With live data
  one build took about 70 ms and `status.json` was about 45 KiB for 11 nodes.
- `[实测]` 2026-09-19: `test02` (created in the console after the 2b rollout, added by the agents) had no traffic
  rows although 7 nodes reported it: `reports.store` only counted users in the registration file. The report
  route now also counts the users a syncing node's agent last reported running. Resetting an address also
  rotates the user's UUID (`users.rotate_credentials`), because imported configurations kept working with the
  old UUID after a reset.
- `[实测]` 2026-09-19 after `9de03e9` was deployed: `test02` traffic was stored from the first report (dzire).
  A reset at 07:48:50Z changed the UUID; within about a minute all six of its nodes (ams, dcc, dzire, kagoya,
  netcup, usca) ran exactly one `test02` entry per inbound with the new UUID (compared by SHA-256 prefix, Vision
  and XHTTP). usca's sync showed HTTP 502 for one cycle during the console restart and recovered by itself.
- `[代码]` 2026-09-19 working tree, sharing signals (plan-sharing-signals): `queries.fetch_sources` counts networks
  (IPv4 /24, IPv6 /48) and client families per user from the subscription access log; the agent sends HMAC hashes
  of each user's online networks (from `statsonlineiplist`, key from `/sync`, changing per UTC day) and
  `console/sharing.py` keeps them per 10-minute slot for 8 days (`online_seen`); places = max(IPv4, IPv6 networks),
  alert at `console_sharing_threshold` (3). `[实测]` `statsUserOnline` was already on in the node config; on
  kagoya `statsonlineiplist` returned `{"ips": {<ip>: <last seen epoch>}, "name": "user>>><email>>>>online"}`.
  Xray keeps an address in that list only while a connection from it is open, and the time is when the latest
  connection opened (`app/stats/online_map.go`); the agent therefore counts every listed address. The agent
  also sends `xray api statssys` (Alloc, Sys, NumGoroutine, Uptime) with each report; on 2026-09-19 usca and
  dzire used 45 and 19 MB (Sys) against the 300m container limit.
- `[代码]` 2026-09-19 working tree, console-managed egress phase 1 ([`plan-egress-console`](reviews/console/plan-egress-console-2026-09-19.md)):
  `console/egress.py` holds a type registry (SOCKS5 only) and a condition registry (users, domains, IP ranges; none =
  whole node); proxies, credentials and assignments exist only as rows (`egress`, `egress_assignment`, `egress_check`,
  `egress_node`). The admin routes are in `console/egress_web.py` (registered by `web_app` with its shared helpers),
  the node side in `docker/edge-tools/edge_egress.py` (the agent passes in its Xray API call, error type and log),
  and the check itself in `console/egress_probe.py` (standard library only), which the tools image copies next to the
  agent; `tests/edge/test_compose.py` checks that every file the tools image copies is in its tag's hash. `/sync` sends a node its own egress (outbounds + assignments, hashed version) only when its agent
  reports `egress_applied` and has not applied that version; `console.admin node-users` adds `proxy_egress`, so
  `edge.yml` writes the same egress into the config with `runtime` true, and egress-only changes then need no restart.
  The agent (`EGRESS_ENABLED` from `edge_egress_runtime`) checks each egress every sync through a temporary
  `xray run -c stdin:` with one 127.0.0.1:21000+ HTTP inbound per egress (2 attempts); a failed egress drops its rules
  (direct) or points them at `blocked`, and is used again after 2 good checks. It keeps switching with the last list
  while the console is unreachable. Order of runtime changes: `rmrules <ours> default`, `rmo` stale, `ado`,
  `adrules -append` (ours + default) — `adrules` without `-append` replaces the whole routing table. The status service
  on spt checks unassigned egress every 10 minutes and new or changed ones every round. `edge_egress_source: files`
  goes back to the `socks5.yml` profiles and turns the agent's egress off.
- `[实测]` 2026-09-19 after deploying `0b354d7`: all 11 agents report `egress_applied` (empty pool), no sync errors;
  Xray restarted only on jp10 (removing `jpntt_isp`). Node clocks against spt: jp05 -73 s and jp10 -72 s with no time
  sync service (`NTPSynchronized=no`); legend has none either but was within 2 s; the other 8 are synced
  (systemd-timesyncd, kagoya chrony). The rendered REALITY settings have no `maxTimeDiff`, so connections are not
  affected; times the nodes stamp themselves (report periods, the Xray restart time) are off by that much. Turning
  on time sync on those hosts is a host change awaiting the operator's decision.
- `[实测]` 2026-09-19 local spike with the pinned Xray: a runtime rule sent one user through a SOCKS outbound while
  another stayed direct; pointing it at `blocked` cut that user off; removing it returned to direct; an Xray restart
  dropped the runtime rules (the agent puts them back at the next sync).
- `[未知]` Whether the old system's configuration has the same loopback-through-domain path.
  Tunnel procedures: [`runbooks/cloudflare-tunnels.md`](runbooks/cloudflare-tunnels.md).
- Rollout order when authorized: `edge.yml` on dzire, usca, legend, kagoya (registers them; no Xray restart while
  `edge_report_nodes` is empty) → `subs.yml` → `console.yml` → add nodes to `edge_report_nodes` one at a time.
  See `docs/operations.md` §14.

## Production State

- Monitor server runs on `spt`.
- `reality-monitor.service` has been deployed and verified active.
- Service binds internally on `127.0.0.1:8000`.
- `/healthz` returned `{"status":"ok","db_ok":true,"journal_mode":"wal"}` during rollout.
- Cloudflare Request Header Transform Rule is configured for `monitor.taoziyoyo.com` and injects `X-Monitor-Tunnel-Secret`.
- Browser access to `https://monitor.taoziyoyo.com/stats/ui` works from the allowed operator IP after fixing the rule to Request Header rather than Response Header.
- Stats API access with Bearer token works locally via `http://127.0.0.1:8000/stats/health`.

## Monitor Agent Rollout

Production monitor agents were rolled out across current production nodes:

```text
dzire, de, ams, dcc, sg, jp05, hk-hn, hk-hn2, jp10, jpntt, spt
```

The final full refresh used this shape:

```bash
./ansible-playbook deploy 'dzire:de:ams:dcc:sg:jp05:hk-hn:hk-hn2:jp10:jpntt:spt' --tags monitor_agent -K
```

Important details:

- Multi-host targets must be passed as one inventory pattern, e.g. `'sg:ams:jp05'`.
- Do not run `./ansible-playbook deploy sg ams jp05 ...`; Ansible treats extra words as playbook paths.
- Agent runs as `reality-monitor-agent`, in the `docker` group.
- Agent cron is installed under `reality-monitor-agent`.
- Agent state lives under `/opt/reality/monitor/state`.
- `agent.log` may not exist when nothing failed; use `traffic_cache.json` and `/stats/health` as primary proof.

## Node Naming State

Last updated: 2026-09-17 JST. Source: `inventory.ini` after `ops@91e171d` and the
test node retirement below, `host_vars/`, and local SSH config resolved
with `ssh -G`. `inventory.ini` is authoritative; re-read it instead of trusting
this list.

`[reality_nodes]` hosts:

```text
dzire, netcup, ams, dcc, legend, jp05, hk01, hk02, jp10, kagoya, usca, spt
```

Tier groups: `[free]` dzire, usca, netcup; `[basic]` jp05, legend, kagoya;
`[normal]` jp10, hk01; `[premium]` ams, dcc, hk02. Feature groups: `[special]` spt.

Name changes since the previous record:

| Previous name | Current inventory name | Notes |
|---|---|---|
| `de` | `netcup` | `1a648c5 Rename the de node to netcup and update user assignments` |
| `sg` | `legend` | `91e171d`; SSH `Host legend`, `host_vars/legend.yml` |
| `hk-hn` | `hk01` | `91e171d`; SSH `Host hk01`, `host_vars/hk01.yml` |
| `hk-hn2` | `hk02` | `91e171d`; SSH `Host hk02`, `host_vars/hk02.yml` |
| `jpntt` | removed | `91e171d`. The `socks5_egress` profile `jpntt_isp` is only a profile name and routes on `jp10` |
| `lej` | `jp05` | Unchanged since the previous record |
| `hkcod12`, `hyu24`, `hyd13`, `hyu22` | removed | Operator retired all four test nodes on 2026-09-14: removed from `[reality_nodes]`, the `[test_nodes]` group, their `host_vars`, `group_vars/test_nodes.yml`, and the monitor task guard keyed on that group. No replacement test node is chosen (roadmap §9) |

Remaining gaps:

1. Monitor records are keyed by `inventory_hostname`. After the next monitor agent
   deploy, renamed nodes report under the new names; history under `hk-hn`,
   `hk-hn2`, and `sg` is not migrated.
2. `[实测]` 2026-09-14 a deploy limited to `hk01:hk02:legend` ran from 22:32 JST.
   Artifact timestamps show it synced node configs at 22:49, regenerated the local
   subscription cache at 22:48–22:50 and updated the Gist at 22:51
   (`SUBSCRIPTIONS.txt`), then hung writing output after its parent session exited;
   it was terminated at about 23:50. Read-only checks afterwards: every
   `reality_*` container on `hk01` (25) and `hk02` (23) and the single container
   on `legend` is running, and on `hk01`/`hk02` each container's `/config.json`
   hash matches the host file. Whether the running Xray processes loaded the
   latest content was not proven; the containers were created 2026-09-14 08 JST,
   after `91e171d`. Other renamed or removed nodes were not deployed by this run.
3. `[实测]` 2026-09-14 the stale `test_hkcod12.json` and `test_hyu24.json` were
   deleted from `/opt/reality/users` on `spt` after that Gist update. The Gist was
   then regenerated at 23:56 JST with `--tags gist --limit spt`
   (`changed=1`, `failed=0`, `SUBSCRIPTIONS.txt` rewritten), so `test`/`test-full`
   are built only from `test_jp05.json` and `test_jp10.json`. The published Gist
   content itself was not read back.
4. The operator confirmed on 2026-09-14 that the four test node servers have
   already been cancelled, so there is nothing left to decommission on them.

### Historical: 2026-08 rename to `de`, `sg`, `jp05`

At that time the canonical names were `de`, `sg`, `jp05` (from `netcup`, `legend`,
`lej`), with SSH through `Host de`. `de` and `sg` were later abandoned as described
above.

Old monitor history for `netcup`, `legend`, and `lej` was deleted from the monitor DB because historical data was not needed:

- `records`: 178419 rows deleted
- `user_ip_hits`: 0 rows deleted
- Backup created on the server:
  `/opt/reality/monitor/db-backups/traffic_monitor.db.before-legacy-node-clean-1782145043`

Validation returned `[]` for old node names:

```bash
curl -sS -H "Authorization: Bearer $STATS_TOKEN" \
  "http://127.0.0.1:8000/stats/health" \
  | /opt/reality/monitor/.venv/bin/python3 -c 'import sys,json; print([x for x in json.load(sys.stdin) if x["node"] in {"lej","legend","netcup"}])'
```

## User / Subscription State

- `users/dave.yml` exists and is tracked in git via `c583214 Add dave user`.
- `dave` was created because the subscription had already been issued to a customer.
- `dave` metadata at rollout time:
  - `groups: [basic]`
  - `hosts: [hk-hn]`
  - expected subscription cache nodes after ACL: `de`, `dzire`, `hk-hn`, `jp05`, `sg`
- Old local subscription caches were cleaned from `/opt/reality/users`, including stale `dave_*` and old-node suffixes such as `*_netcup.json`, `*_lej.json`, `*_legend.json`.
- `--tags users` was then used to refresh Xray config, local subscription JSON, and Gist.

Expected `dave` cache files:

```text
dave_de.json
dave_dzire.json
dave_hk-hn.json
dave_jp05.json
dave_sg.json
```

## Documentation State

The following runbooks were updated with commands from this rollout:

- `docs/operations.md`
  - multi-host inventory pattern usage
  - `users` full refresh shape
  - user/subscription consistency cleanup
  - stale `/opt/reality/users/*.json` cache cleanup
- `docs/features/monitor/operations.md`
  - current monitor production state
  - stats token lookup
  - precise JSON health filtering instead of `grep`
  - old monitor node history deletion
  - monitor agent grey/full rollout commands
  - agent verification commands

## Operational Cautions

- Avoid pasting full Ansible user records publicly; outputs include private keys and tokens.
- `users/*.yml` is source config; `/opt/reality/users/*.json` is generated subscription cache.
- Gist generation reads `/opt/reality/users/*.json`; stale cache files can continue to expose old subscriptions.
- `--tags users` updates node config, containers, local subscription JSON, and Gist.
- `--tags monitor_agent` only updates monitor agent behavior; it is not the right command for normal user/subscription changes.
- When running Python heredocs in shell, code must start at column 1. Leading indentation before `import` causes `IndentationError`.
- This environment cannot provide the user's sudo password. Production DB changes requiring sudo must be run in the user's terminal unless sudo is already non-interactive.

## Quick Verification Commands

Monitor health:

```bash
STATS_TOKEN=$(sudo awk -F= '/^MONITOR_STATS_BEARER_TOKEN=/{print $2}' /opt/reality/monitor/monitor.env)

curl -sS -H "Authorization: Bearer $STATS_TOKEN" \
  "http://127.0.0.1:8000/stats/health"
```

Old node names absent:

```bash
curl -sS -H "Authorization: Bearer $STATS_TOKEN" \
  "http://127.0.0.1:8000/stats/health" \
  | /opt/reality/monitor/.venv/bin/python3 -c 'import sys,json; print([x for x in json.load(sys.stdin) if x["node"] in {"lej","legend","netcup"}])'
```

Subscription cache sanity:

```bash
find /opt/reality/users -maxdepth 1 -type f -name 'dave_*.json' -printf '%f\n' | sort

find /opt/reality/users -maxdepth 1 -type f \( -name '*_netcup.json' -o -name '*_lej.json' -o -name '*_legend.json' \) -print
```
