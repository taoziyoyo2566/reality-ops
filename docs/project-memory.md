# Project Memory

Last updated: 2026-08-28 JST

## Xray Image State (Consumer Side)

Last updated: 2026-09-14 JST.

Image build and publication left this repository entirely and now belong to
[`taoziyoyo2566/xray-docker`](https://github.com/taoziyoyo2566/xray-docker).
This repository is only a consumer: it owns no Dockerfile, release pipeline, or
tag contract. The contract and upgrade procedure live in
[`docs/runbooks/xray-image-consumption.md`](runbooks/xray-image-consumption.md).

- The deployment default is the floating `taoziyoyo2566/xray-docker:latest`
  (`group_vars/all/main.yml:3`). No digest is pinned (roadmap C12).
- `[实测]` 2026-09-14 read-only snapshot of `spt`, `dzire`, and `usca`: every
  `reality_*` container runs `taoziyoyo2566/xray-docker:latest` and reports
  Xray `26.3.27` (`d2758a0`), in bridge network mode.
  - `spt`: `reality_core` created 2026-09-12, restart count 0; local image
    `taoziyoyo2566/xray-docker@sha256:fd502666d1a9ca7ea772e2c1638bb83e79bdc12c97cddd5e3755edc7485b3ec7`
    (image created 2026-08-28).
  - `dzire` (multi, 32 containers) and `usca` (single): repository digest was
    not collected.
  - The other nodes were not probed; do not assume they run the new image.
  - This supersedes the 2026-08-29 record that the new reference had not been
    deployed and that `spt` still ran `xray_docker@sha256:433d7302...`
    (Xray `25.12.8`).
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
2. None of the renames or removals has been deployed. Node config, subscriptions,
   and monitor names change only when a deploy runs; removing a host from the
   inventory does not stop its containers or decommission it.
3. `[实测]` 2026-09-14 the stale `test_hkcod12.json` and `test_hyu24.json` were
   deleted from `/opt/reality/users` on `spt`; no cache file for any retired test
   node remains there. The published Gist `test`/`test-full` files still carry the
   retired links until the next Gist generation rewrites them from the remaining
   `test_jp05.json` and `test_jp10.json`.

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
