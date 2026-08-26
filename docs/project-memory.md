# Project Memory

Last updated: 2026-08-26 JST

## Current Branch State

- Active branch: `fix/xray-image-verifier`.
- Current committed HEAD verified on 2026-08-26:
  `46c97f65a6e1755c4fa71bd92757931876418bda` (`Merge pull request #1 from
  taoziyoyo2566/feat/xray-modernization`).
- Branch base verified on 2026-08-26:
  `origin/ops@46c97f65a6e1755c4fa71bd92757931876418bda`.
- `git worktree list --porcelain` reports one worktree, the primary workspace
  `/home/saberu/workspace/projects/reality-ops`, checked out on this branch.
  The temporary isolated worktree has been removed.
- Pull request #1 merged the initial phase-1 image-release implementation into
  `ops`. The current branch tracks `origin/ops` and contains an uncommitted,
  focused remediation for the pushed-image verifier plus its tests, roadmap,
  runbook, evidence, and this memory update.
- No staging, commit, push, pull request, workflow rerun, registry write, or
  deployment was performed for the current remediation.

Before publishing or considering the phase closed, run:

```bash
git status --short --branch
```

Review the complete diff against `origin/ops`; do not infer that the verifier
remediation is published, integrated, or active in GitHub Actions.

## Xray Image Release State

Last verified: 2026-08-26 JST.

- Official Xray latest stable is `v26.3.27`; `v26.7.28` is a prerelease.
- `docker-build/XRAY_VERSION` and
  `docker-build/XRAY_PRERELEASE_VERSION` pin those channels independently.
- Official amd64 and arm64 asset digests matched all four repository SHA256
  entries. Python `zipfile` integrity tests passed for all four archives.
- The downloaded amd64 binaries reported Xray `26.3.27` (`d2758a0`) and
  `26.7.28` (`5ca6f4b`).
- The Dockerfile base pin matches Docker Hub's current top-level digest for
  `alpine:3.24`:
  `sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b`.
- GitHub Actions run
  [`32911966721`](https://github.com/taoziyoyo2566/reality-ops/actions/runs/32911966721)
  built and pushed both candidates before both jobs failed in
  `Verify pushed multi-platform image` with
  `docker: cannot overwrite digest sha256:...`.
- Docker Hub now has candidate tags for both channels, resolving to these
  immutable top-level digests:
  - `v26.3.27` and
    `build-46c97f65a6e1755c4fa71bd92757931876418bda-xray-v26.3.27` resolve to
    `sha256:168290fdc51724f35b60f2b60d4b043816145bf7ac572af538d79897b3cf7a7d`;
  - `v26.7.28` and
    `build-46c97f65a6e1755c4fa71bd92757931876418bda-xray-v26.7.28` resolve to
    `sha256:f4220a4d33e935574cb1f892677885805acc35d84b596d2b23177b0507c7f095`.
- Neither `stable` nor `prerelease` was promoted. The existing `latest` tag
  remains unchanged at
  `sha256:433d7302cddb336cb3b4d06f543798a850991a662cd136b5a6b7fa43274599a3`.
  Read-only Docker Hub and Buildx inspection reported `linux/amd64` and
  `linux/arm64` plus two `unknown/unknown` provenance manifests.
- The build and scheduled-promotion workflows require digest-pinned target
  runtime verification before moving aliases. Stable updates also require the
  current `latest` digest to pass as a rollback candidate. Both workflows use
  the shared `xray-image-alias-update` concurrency group.
- Root cause: the verifier reused the top-level multi-platform index digest for
  sequential `linux/amd64` and `linux/arm64` runs. Docker's local image store
  retained the first platform under that digest and refused to overwrite it
  with the second platform. The remediation resolves and validates each child
  manifest digest from the index, then runs each platform by its own immutable
  digest.
- Focused verifier tests now reproduce the old local-store collision, assert
  exact per-platform child references, and reject malformed child digests.
  Shell syntax checks, the focused test suite, and `git diff --check` passed
  locally.
- A fresh GitHub Actions run with the remediation, real amd64/arm64 runtime
  checks, alias promotion, and deployment selection remain gaps. This user
  still cannot access the local Docker socket, so a real local container test
  cannot run. GitHub CLI authentication is valid for read-only investigation;
  publication and workflow reruns were not authorized. The guidance-required
  `scripts/check-project-memory.sh` is absent from the repository, so this
  memory was updated manually.

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
