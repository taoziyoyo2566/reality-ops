# Project Memory

Last updated: 2026-08-28 JST

## Xray Image State (Consumer Side)

Last updated: 2026-08-29 JST.

Image build and publication left this repository entirely and now belong to
[`taoziyoyo2566/xray-docker`](https://github.com/taoziyoyo2566/xray-docker).
This repository is only a consumer: it owns no Dockerfile, release pipeline, or
tag contract. The contract and upgrade procedure live in
[`docs/runbooks/xray-image-consumption.md`](runbooks/xray-image-consumption.md).

- The deployment default is `taoziyoyo2566/xray-docker:latest`
  (`group_vars/all/main.yml:3`). The reference is committed but has **not been
  deployed to any node**.
- The old repository `taoziyoyo2566/xray_docker` is frozen; its `latest` stopped
  at 2026-08-26. It had roughly 2236 pulls and may have users outside this
  project, but receives no further updates.
- This repository's `Sync Xray Release Images` and `Audit Xray Image Tags` were
  disabled on 2026-08-29 and their files are removed in this change. This
  repository no longer produces any image.
- `[实测]` The node recorded in unified roadmap G1 and this control host `spt`
  both still run `xray_docker@sha256:433d7302...`, built 2025-12-24, reporting
  Xray `25.12.8`. Nodes do not re-pull automatically (roadmap D6), so the image
  switch has no effect until a deployment runs.
- Migration acceptance -- geodata resolution under `--read-only --cap-drop ALL`,
  image size, bad-config behaviour, `XRAY_HEALTH_PORT` -- has not been executed.
  See roadmap P2-c. **Do not record it as passed on the strength of "the new
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

Canonical inventory names are now:

```text
de, sg, jp05
```

Former names:

```text
netcup -> de
legend -> sg
lej -> jp05
```

Inventory now uses canonical host `de` directly; SSH connection resolves through local SSH config `Host de`.

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
