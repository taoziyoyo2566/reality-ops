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
  nodes (`ali` refused SSH authentication): every `reality_*` container reports
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
[`roadmap-unified-2026-08-27.md`](reviews/roadmap-unified-2026-08-27.md).

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
- `[实测]` 2026-09-15 host DNS survey (12 reachable nodes, `ali` not reachable): only dzire
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
  `ops@0538f92`): `usca` and `legend` still run the S3 form (`docker_container`, host logrotate
  timer, `/opt/xray-edge/bin`) until each is migrated with separate authorization. `edge.yml`
  from `0538f92` on performs that migration, so do not run it against a node without that
  authorization.
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
  Not yet done on dzire: the next-day rotation check. Local: `test_compose.py` 6/6,
  `e2e_local.py` 42/42 three times in a row (now including last-good recovery, UUID and
  short id changes, SOCKS5 membership change, compose logrotate). Buildx gives a new image
  ID on every build, so the tools image tag is a hash of its build inputs.

```bash
# S3 form (current on dzire, usca, legend)
ssh dzire "docker inspect -f '{{.State.Running}} {{.RestartCount}} {{.Image}}' xray_edge; systemctl is-active xray-edge-logrotate.timer"
# Compose form (after migration)
ssh dzire "sudo docker compose -f /opt/xray-edge/compose.yaml ps; sudo docker compose -f /opt/xray-edge/compose.yaml run --rm -T --pull never applier verify --root /opt/xray-edge --container xray_edge --image \$(sudo docker inspect -f '{{.Config.Image}}' xray_edge)"
```

## Subscription Service (S4) State

Contract: [`plan-subscription-service`](reviews/subscription-service/plan-subscription-service-2026-09-15.md)
(APPROVED 2026-09-15, D1 option B: compose with its own `cloudflared` tunnel; hostname not chosen yet).

- Implemented locally, not deployed: `subs/` (stdlib HTTP server, renderer, catalog builder,
  SQLite access log), `docker/subs/`, `roles/subs_service`, `subs.yml`, `subs-remove.yml`,
  `group_vars/all/subs.yml` (`subs_enabled_users: ["test"]`, canary nodes `migrated`).
  Vault variables `vault_subs_tokens` and `vault_subs_tunnel_token` do not exist yet.
- Local verification: `tests/subs/test_subs.py` 19/19 (also inside the image); `e2e_local.py`
  22/22 with Xray (Vision, XHTTP) and Mihomo v1.19.31 (privacy and split profiles, TUN off).
  All 584 links in the 332 old per-user files on `spt` parse.
- `[实测]` 2026-09-15 local run of the `subs.yml` aggregation and build with every node treated
  as legacy (no node contacted, synthetic token): 13 nodes collected; the build stopped because
  `/opt/reality/users` holds `test_jp05.json` and `test_jp10.json` while the ACL does not allow
  `test` on jp05 or jp10 (old-system leftovers). The old files have no entries for `ali`.
  Operator decision 2026-09-15 (plan §5 item 1, option A): the ACL decides. The builder now
  leaves such files out and lists them under `ignored` in its report; a node the ACL allows
  without a legacy file still stops the build. The old files are not changed.
- Design details to confirm on devices: both Clash profiles resolve node domains through
  domestic DoH (`223.5.5.5`, `119.29.29.29`) so that a node can be reached before the tunnel is
  up; the split profile makes Mihomo download GeoIP/GeoSite (about 21 MB) from its default
  jsdelivr URLs on first load, which may fail on some networks in China.

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

Last updated: 2026-09-14 JST. Source: `inventory.ini` after `ops@91e171d` and the
test node retirement below, `host_vars/`, and local SSH config resolved
with `ssh -G`. `inventory.ini` is authoritative; re-read it instead of trusting
this list.

`[reality_nodes]` hosts:

```text
dzire, netcup, ams, dcc, legend, jp05, hk01, hk02, jp10, kagoya, usca, spt, ali
```

Tier groups: `[free]` dzire, usca, netcup; `[basic]` jp05, legend, kagoya;
`[normal]` jp10, hk01; `[premium]` ams, dcc, hk02. Feature groups: `[special]` spt;
`[china]` ali.

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
