# 监控加固 灰度/部署 Checklist

- **针对**：`fix/monitor-integrity` 分支阶段 1/2/3（提交 `5ea94fb`/`d72cc7b`/`a14a668`/`ce383a9`）
- **硬约束**：线上节点不中断；每步 staging/金丝雀全绿才扩面；现网部署需单独授权 + 外部评审（W-R20）
- **关键事实**：
  - server 仅在 `spt`（`monitor.server_host`，`ansible_connection=local`），监听 `127.0.0.1:8000`，经 CF Tunnel 暴露 `monitor.taoziyoyo.com`
  - agent 跑在**所有** `[reality_nodes]`；`[reality_nodes]` 同时含 4 台测试机（hkcod12/hyu24/hyd13/hyu22）→ **任何 deploy 必须 `--limit`，否则打到全部生产**
  - 路径：DB `/opt/reality/monitor/data/traffic_monitor.db`（#1 后已移出共享的 `reality_data_dir`）；env `/opt/reality/monitor/monitor.env`；agent token `/opt/reality/monitor/agent_token`；state `/opt/reality/monitor/state`
  - tags：`monitor_server`（server.py/systemd/env/retention，受 `when spt` 限定）、`monitor_config`（agent.py/cron/token/user）

## 当前状态（2026-06-23 生产）

- [x] `spt` server 金丝雀已实际执行：`reality-monitor.service` active，运行用户已切到 `reality-monitor`。
- [x] `/healthz` 已返回 `{"status":"ok","db_ok":true,"journal_mode":"wal"}`。
- [x] 本机 Bearer 访问 `/stats/health` 正常，说明 `monitor.env` token 与服务端鉴权可用。
- [x] `vault_monitor_tunnel_secret` 已补齐，`MONITOR_TUNNEL_SECRET` 长度为 64。
- [x] CF 已配置 **Request Header Transform Rule** 注入 `X-Monitor-Tunnel-Secret`；曾误配为 Response Header，会导致源站收不到 secret、`/debug/whoami` 仍 401。
- [x] 经 CF 的 `https://monitor.taoziyoyo.com/debug/whoami` / `/stats/ui` 已恢复访问（运维白名单 IP：`45.145.75.134`）。
- [x] `jp10` agent 第一台生产灰度已执行：专用用户在 docker 组、cron 已迁移、`traffic_cache.json` 出现用户基线，`/stats/health` 中 `jp10 stale=false`。
- [x] `jp10` 灰度暴露 single stats 解析缺陷：`xray statsquery` 会返回缺少 `value` 的 stat 项，已在 agent 模板中修复为跳过无值项并兼容 `user>>>...` / `inbound>>>user-*` 两类计数。
- [x] agent 已分批升级并验证：`jp10`、`dzire`、`sg`、`ams`、`jp05`、`dcc`、`hk-hn`、`hk-hn2`、`jpntt` 均已恢复 `stale=false`。
- [x] `jpntt` 灰度暴露 access.log OOM 风险：旧 agent 会整文件 `cat` 后再截尾；已改为容器内 `tail -n 4000` 后再解析。
- [x] warm-up 已改为 root 下 `sudo -n -u reality-monitor-agent -- ...`，避免 Ansible 对 nologin 用户创建 remote tmp 的 warning/延迟。
- [x] `DE` / `netcup` inventory 身份不一致已处理：canonical host name 统一为 `de`，连接使用 SSH config `Host de`。

---

## Phase 0 — 离线预检（不碰线上）

- [ ] `git -C ~/workspace pull` 同步治理文件；确认在 `fix/monitor-integrity`、工作树干净
- [ ] **在有 ansible 的机器**补 Gate-2（本机 BLOCKED）：
  - `ansible-playbook deploy.yml --syntax-check`
  - `ansible-inventory --graph`（确认 test_nodes 与生产档位分组无误）
- [ ] `--check --diff` 试跑（**务必 `--limit spt`**）：`ansible-playbook deploy.yml --tags monitor_server --limit spt --check --diff`
- [ ] 确认 vault 可解密：`ansible-vault view group_vars/all/<vault文件>`（或对应文件）

## Phase 1 — 前置（CF + vault + 轮换）⚠️ 决定 D1-B 是否生效

> **fail-closed 语义**：`MONITOR_TUNNEL_SECRET` 未配或与 CF 注入值不一致时，鉴权退化为"仅 Bearer"——仪表板经 CF 也需带 token。**要让仪表板零改动可用，下面 1.1–1.3 必须在 server 部署前/同步完成且值一致。**

- [ ] **1.1 生成强随机 secret**：`openssl rand -hex 32` → 记为 `S`
- [ ] **1.2 写入 vault**：`ansible-vault edit ...` 设 `vault_monitor_tunnel_secret: "S"`
- [ ] **1.3 CF 仪表盘配 Transform Rule**：对 `monitor.taoziyoyo.com`，**Modify Request Header → Set static**（不是 Response Header；须覆盖客户端同名头）`X-Monitor-Tunnel-Secret = S`
- [ ] **1.4 轮换泄露 token**：
  - cloudflared tunnel token（明文在 `ps`）：CF 仪表盘重建 tunnel token，改 credentials-file 方式
  - 确认 `monitor_server.py:9` 旧硬编码 token（已随文件删除，仍在 git 历史）当前**未被任何 agent/节点使用**；如曾启用则轮换 `vault_monitor_report_token`
- [ ] **1.5** 确认 vault 里 `vault_monitor_report_token / admin_bearer / stats_bearer / subs_token` 均已设（server/agent 改从 env/文件读，缺值会导致全鉴权失败）

## Phase 2 — Staging 验证（4 台测试机，推荐；安全修复尤其建议）

> 现状：`group_vars/test_nodes.yml` `monitor_enabled:false`（监控延后）。要验证监控需临时启用并**指向独立测试监控**，且 plan-staging §4.2 层④ guard 须先实现（防测试 agent 误报生产）。
- [ ] 若跳过完整 staging：至少在 1 台测试机验证 agent（`--tags monitor_config --limit hyu24`），观察 `/opt/reality/monitor/state/agent.log` 无异常、cron 以 `reality-monitor-agent` 落地
- [ ] A1 端到端（若搭了测试监控）：本机 `curl 127.0.0.1:8000/stats/daily` 无 secret 头 = **401**；经 CF 测试域名（CF 注入 secret + 运维白名单 IP）= **200** 且仪表板正常

## Phase 3 — 生产金丝雀：server（spt）先行

- [ ] **3.1 备份 DB**（稳定在线备份，不移动源文件）：
  ```
  install -d -m 0700 /opt/reality/monitor/db-backups
  sqlite3 /opt/reality/data/traffic_monitor.db ".backup '/opt/reality/monitor/db-backups/traffic_monitor.db.bak-$(date +%s)'"
  ```
- [ ] **3.2 备份旧 server.py**：`cp /opt/reality/monitor/server.py /opt/reality/monitor/server.py.bak`
- [ ] **3.3 部署 server**：`ansible-playbook deploy.yml --tags monitor_server --limit spt`
  - （建 reality-monitor 用户 + `/opt/reality/monitor/data` + server.py(新 DB 路径) + env + systemd + retention）
  - role 会在检测到旧库存在且新库不存在时，先停 `reality-monitor`，在新目录生成 `traffic_monitor.db.bak-pre-migrate-<epoch>`，再只迁移运行文件 `traffic_monitor.db` / `-wal` / `-shm`，最后由 handler 重启服务。
- [ ] **3.3b 自动迁移结果确认**（若旧库仍存在且新库为空，停止扩面，先人工 reconcile）：
  ```
  test -f /opt/reality/monitor/data/traffic_monitor.db
  test ! -f /opt/reality/data/traffic_monitor.db
  ls -lh /opt/reality/monitor/data/traffic_monitor.db*
  ```
- [ ] **3.4 部署时验证（本机 BLOCKED 项，现网确认）**：
  - [ ] DB 在新目录且属主对：`stat -c '%U %a' /opt/reality/monitor/data/traffic_monitor.db` → `reality-monitor 6xx`
  - [ ] **xray 未受影响**（#1 关键回归）：`reality_data_dir` 未被 monitor 改动，spt 上 `docker ps` 正常、`docker logs reality_core` 无权限错误
  - [ ] 服务以非 root 跑：`systemctl show reality-monitor -p User` = `reality-monitor`；`systemctl status reality-monitor` active
  - [ ] env 已下发且 0600：`stat -c '%a %U' /opt/reality/monitor/monitor.env` = `600 reality-monitor`
  - [ ] WAL 生效：`sudo -u reality-monitor sqlite3 /opt/reality/monitor/data/traffic_monitor.db 'PRAGMA journal_mode;'` = `wal`（且生成 `-wal/-shm`，reality-monitor 可写）
  - [ ] healthz：`curl -s http://127.0.0.1:8000/healthz` → `{"status":"ok","db_ok":true,"journal_mode":"wal"}`
  - [ ] **A1 鉴权**：`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/stats/daily` = **401**；带 `-H 'Authorization: Bearer <stats_token>'` = **200**
  - [ ] **仪表板经 CF**：浏览器开 `https://monitor.taoziyoyo.com/stats/ui`（运维白名单 IP）→ 正常加载（CF 注入 secret 生效）。若 401 → 检查 Phase 1.2/1.3 的 S 是否一致
  - [ ] retention cron：`crontab -u reality-monitor -l | grep Retention`
- [ ] **3.5 观察一个上报周期**：现有 agent（仍 root，未升级）继续上报 → records 持续写入、无 `database is locked`（`journalctl -u reality-monitor`）

## Phase 4 — 生产金丝雀：agent 1–2 台

- [x] **4.1 选 1 台低风险节点**部署：`jp10` 已完成第一台灰度（命令形态：`./ansible-playbook deploy jp10 --tags monitor_agent -K`）
- [ ] **4.2 部署时验证**：
  - [x] `reality-monitor-agent` 用户存在且在 docker 组：`id reality-monitor-agent`
  - [ ] token 文件：`stat -c '%a %U' /opt/reality/monitor/agent_token` = `600 reality-monitor-agent`
  - [x] cron 迁移：`crontab -u reality-monitor-agent -l | grep -q shuf` 且 `crontab -l`（root）无 "Reality Traffic Report" 残留
  - [x] **cron 真执行**（nologin 用户关键风险）：`/opt/reality/monitor/state/traffic_cache.json` 已出现用户基线
  - [x] **首跑 re-baseline 正常**：`jp10` 修复后服务端 `last_seen_ago_sec` 刷新到秒级，`stale=false`
  - [ ] **IP 审计恢复**：上报一轮后 `user_ip_hits` 出现该节点新行（B3+B4+B5 验证）：`sqlite3 ... "SELECT count(*) FROM user_ip_hits WHERE node='<node1>'"` > 0
  - [ ] pending 正常：制造一次失败（停 server 1 周期再起）→ 校验**无丢行、无重复行**、`state` 里 pending 累计后清空
- [ ] **4.3** 再加 1 台（multi 模式节点优先，验证 B3 多容器日志路径）

## Phase 5 — 全量扩面

- [ ] 分批 `--limit <批次>` 推 agent 到其余生产节点（避免一次性全量）
- [ ] 全量后复核：`/stats/health` 各节点 last_seen 新鲜、`user_ip_hits` 全节点非 0、无锁错误、仪表板正常
- [ ] 关掉测试机监控/确认 test_nodes 未误接（`group_vars/test_nodes.yml` 仍 `monitor_enabled:false`）

---

## 回滚

| 层 | 回滚 |
|---|---|
| server | 恢复 `server.py.bak` + DB `.bak`；`systemctl restart reality-monitor`；如需退 WAL：`sqlite3 DB 'PRAGMA journal_mode=delete;'` |
| 鉴权 | 临时放宽：在 vault 清空 `vault_monitor_tunnel_secret`（fail-closed→纯 Bearer），用 Bearer 访问；或 `git revert` server 模板后重部署 |
| agent | `git revert` agent 模板重部署该节点；旧 root cron 已被移除，回滚需手工恢复或重部署旧版 |
| 全量 | `git revert` 相关提交 → `--check` → 分批重部署 |

## 部署时仍 BLOCKED（本机无法验，必须现网确认）

- `ansible-playbook --syntax-check`（Phase 0）
- nologin 用户（reality-monitor / reality-monitor-agent）的 cron 实际执行（Debian 13 通常可，**必须 Phase 3.4/4.2 实测**）
- `become_user` warm-up、docker exec 采集（single/multi）、生产 DB→WAL 切换 + `-wal/-shm` 属主
- CF Transform Rule → server 端 secret 头端到端

## 关联文档
- 修复主计划：[`plan-harden-monitor-2026-06-13.md`](./plan-harden-monitor-2026-06-13.md)
- 各阶段 changelog：[`round2`](./round2-2026-06-21.changelog.md)（阶段1）·[`round3`](./round3-2026-06-21.changelog.md)（阶段2-server）·[`round4`](./round4-2026-06-21.changelog.md)（阶段2-agent）·[`round5`](./round5-2026-06-21.changelog.md)（阶段3）·[`round7`](./round7-2026-06-22.changelog.md)（jp10 agent 灰度）
- staging 环境：[`plan-staging-env-2026-06-13.md`](./plan-staging-env-2026-06-13.md)
