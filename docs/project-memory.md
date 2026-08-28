# Project Memory

Last updated: 2026-08-28 JST

## Xray Image Release State

> **2026-08-28: 镜像项目已剥离。** 构建与发布迁往独立仓库
> [`taoziyoyo2566/xray-docker`](https://github.com/taoziyoyo2566/xray-docker)
> （本地 `~/workspace/projects/xray-docker`）。本节以下条目描述的发布机制
> **已不再由本仓库拥有**，保留为历史记录；镜像侧的现状以新仓库为准。
> 本仓库自此只是消费者，部署默认已改为 `taoziyoyo2566/xray-docker:latest`
> （`group_vars/all/main.yml:3`），旧仓库 `taoziyoyo2566/xray_docker` 冻结、不再更新。
> 本仓库侧的剩余去耦动作见
> [`docs/reviews/roadmap-unified-2026-08-27.md`](reviews/roadmap-unified-2026-08-27.md) P2。

Last verified: 2026-08-27 JST.

- Official Xray latest stable is `v26.3.27`; `v26.7.28` is a prerelease.
- The local unpublished sync design no longer pins two releases in repository
  files. `discover-release-window.sh` reads official GitHub Releases, selects
  the newest stable plus every newer non-draft prerelease, validates both
  architecture asset digests, and compares the resulting immutable tags with
  Docker Hub.
- The downloaded amd64 binaries reported Xray `26.3.27` (`d2758a0`) and
  `26.7.28` (`5ca6f4b`).
- The Dockerfile base pin matches Docker Hub's current top-level digest for
  `alpine:3.24`:
  `sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b`.
- Pull request #2 merged as
  `890b16f23c9979edfd53eea97b701c2bdca674da`. Its
  [`Build and Push Xray Images` run](https://github.com/taoziyoyo2566/reality-ops/actions/runs/32914861142)
  completed successfully for both channels.
- Stable runtime verification reported `v26.3.27` on `linux/amd64` and
  `linux/arm64`. At completion of that run, `v26.3.27`, `stable`, and `latest`
  resolved to
  `sha256:5b905e8ff49804690109f74e305611869513a803d5bacf9d1f24d5fa4b1e40ce`.
- Before stable promotion, the previous `latest` digest
  `sha256:433d7302cddb336cb3b4d06f543798a850991a662cd136b5a6b7fa43274599a3`
  passed both architectures and reported Xray `v25.12.8`.
- Prerelease runtime verification reported `v26.7.28` on both architectures.
  `v26.7.28` and `prerelease` still resolve to
  `sha256:53cb9d8730738744a2dbe8c73502e5cd1d8667b14012fbd38a4a38e13495c3f8`.
- The local unpublished tag contract makes every versioned tag
  immutable. Stable `vX.Y.Z` and beta `vX.Y.Z-beta` imply image revision `r0`;
  later image-only changes use `-rN`. `latest` is the only moving tag and never
  points to beta content. Per-release overrides live in
  `docker-build/XRAY_IMAGE_REVISIONS.json`; unlisted releases use `r0`.
- Build publication refuses an existing version tag. The scheduled/manual sync
  builds only missing tags, verifies every candidate by platform child digest,
  and reconciles `latest` only after the entire missing matrix succeeds.
- The first release attempt failed because it reused the top-level digest for
  sequential platform runs. The integrated verifier now resolves each child
  manifest digest; regression tests model the original local-store collision.
- The prior integrated baseline published public `build-*` tags before runtime
  verification. The pending hardening changes push an untagged candidate by
  digest, verify it, and only then create the immutable version tag. GitHub
  prereleases use `vX.Y.Z-beta[-rN]` according to current upstream state.
- Final stable releases are always built from final official assets. A beta
  with the same upstream tag is never relabeled as stable.
- The expanded local lifecycle work adds GitHub release discovery, a dynamic
  missing-release matrix, immutable-tag availability checks, a revision ledger,
  daily synchronization, and a read-only weekly tag audit. The former fixed
  pins, repair workflow, stable checker, channel aliases, and rollback resolver
  are removed. Docker Hub Overview source is `docker-build/README.md`.
- The separate legacy `taoziyoyo2566/dockerhub-test:test` image still exists;
  it has no remaining workflow owner and is a separately reviewed cleanup
  target.
- The lifecycle hardening was merged to `ops` in `b942b61` (PR #4). Merge did
  not trigger registry writes; manual and daily scheduled syncs do.
- Automatic discovery reads the live GitHub Releases API and Docker Hub tags
  API on every run; no checked-in file pins the current upstream version. The
  daily cron is `04:41 UTC` and the weekly read-only audit is Monday
  `05:29 UTC`. Scheduled workflows execute from the repository default branch,
  currently `ops`. GitHub cron is best-effort: runs may be delayed or dropped,
  and GitHub disables scheduled workflows in inactive public repositories
  after 60 days.
- API, asset-digest, build, or verification failure stops the run before
  `latest` reconciliation. Successfully published immutable tags remain and a
  later run only retries missing tags. There is currently no repository-owned
  external paging integration; GitHub Actions run state and notification
  settings are the operational alert path.
- The first manual dynamic sync
  (`https://github.com/taoziyoyo2566/reality-ops/actions/runs/32986819040`)
  completed successfully on 2026-08-27 JST and published all eleven required
  beta tags from `v26.4.13-beta` through `v26.7.28-beta`. A subsequent live
  audit found no missing required tags. `latest` and `v26.3.27` remain at
  `sha256:a5c6e5de23ce9b5f9d1ccbe5562b82557968ec1b3696c31b9d4ea352cfe73098`;
  old `v26.7.28` and `prerelease` at
  `sha256:53cb9d8730738744a2dbe8c73502e5cd1d8667b14012fbd38a4a38e13495c3f8`.
- A manual audit run overlapped that sync and observed a transient final
  missing tag while publication was still running. The follow-up change makes
  synchronization and audit share one concurrency group, warns on known
  legacy-tag debt, and fails only for missing required tags. Old aliases and
  SHA tags still require separately reviewed cleanup.
- The deployment default was `taoziyoyo2566/xray_docker:latest` at the time of
  that release work, with no deployment reference changed and no VPS rollout.
  **Superseded 2026-08-28**: it is now `taoziyoyo2566/xray-docker:latest`; that
  edit is working-tree only and still unpublished and unrolled. Release evidence and runtime verification use immutable
  digests, but deployment is not yet digest-pinned. Under the new contract it
  follows the sole moving tag `latest`.

Detailed evidence:
[`docs/reviews/roadmap-xray-xhttp-ipv6/phase1-image-release-2026-08-26.md`](reviews/roadmap-xray-xhttp-ipv6/phase1-image-release-2026-08-26.md).

Operator runbook:
[`docs/runbooks/xray-image-release.md`](runbooks/xray-image-release.md).

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
