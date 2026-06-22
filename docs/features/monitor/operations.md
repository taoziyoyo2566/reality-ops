# 流量监控子系统 — 运维手册（operations）

常驻运维文档。日常操作 / 访问 / 故障排查。**一次性的加固迁移**见 [`deploy-checklist`](../../reviews/fix-monitor-integrity/deploy-checklist-2026-06-21.md)。

## 1. 架构概览

```
浏览器/运维 ──┐
              ├─ CF 边缘(注入 X-Monitor-Tunnel-Secret + CF-Connecting-IP) ─ CF Tunnel(cloudflared) ─→ 127.0.0.1:8000 (uvicorn, spt)
各节点 agent ─┘ (经 monitor.taoziyoyo.com)                                                              │
                                                                                          SQLite(WAL) /opt/reality/monitor/data/traffic_monitor.db
```
- **server**：仅 `spt`（`monitor.server_host`），systemd `reality-monitor.service`，非 root `reality-monitor` 用户，绑 `127.0.0.1:8000`（无公网直听），经 CF Tunnel 暴露。
- **agent**：所有 `[reality_nodes]`，cron 每分钟（`shuf` 抖动），以 `reality-monitor-agent`（docker 组）运行，经 `docker exec` 取 xray stats / 容器网卡 / 日志，上报 `/report`、`/stats/ip_report`。
- **DB**：SQLite WAL，每日保留 cron 清理 `records`、`subscription_logs`、`user_ip_hits`。

## 当前生产状态（2026-06-23）

- `spt` 的 `monitor_server` 金丝雀已执行完成，`reality-monitor.service` 为 active，运行用户为 `reality-monitor`。
- `/healthz` 已返回 `{"status":"ok","db_ok":true,"journal_mode":"wal"}`，说明新 DB 路径和 WAL 可用。
- 本机 Bearer 访问 `/stats/health` 已验证正常。
- `vault_monitor_tunnel_secret` 已配置为 64 字符 secret；Cloudflare 已配置 **Request Header Transform Rule** 注入 `X-Monitor-Tunnel-Secret`，浏览器经 `https://monitor.taoziyoyo.com/stats/ui` 可从白名单 IP 访问。
- 生产 agent 已分批升级并验证：`jp10`、`dzire`、`sg`、`ams`、`jp05`、`dcc`、`hk-hn`、`hk-hn2`、`jpntt` 均已恢复 `stale=false`。
- 剩余暂不批量处理：`DE` / `netcup` inventory 身份不一致，需先统一 canonical host name。

## 2. 组件与文件位置

| 项 | 路径 / 名称 |
|---|---|
| server 脚本 | `/opt/reality/monitor/server.py`（0640，reality-monitor）|
| server 密钥 env | `/opt/reality/monitor/monitor.env`（0600；systemd `EnvironmentFile`）|
| systemd 单元 | `/etc/systemd/system/reality-monitor.service` |
| DB | `/opt/reality/monitor/data/traffic_monitor.db`（+ `-wal`/`-shm`）|
| agent 脚本 | `/usr/local/bin/traffic_agent.py` |
| agent token | `/opt/reality/monitor/agent_token`（0600，reality-monitor-agent）|
| agent 状态/锁/日志 | `/opt/reality/monitor/state/{traffic_cache.json, agent.lock, agent.log}`（0700）|
| 保留 cron | `reality-monitor` 用户，每日 04:17 |
| agent 上报 cron | `reality-monitor-agent` 用户，每分钟 |
| 配置 | `group_vars/all/main.yml` 的 `monitor.*`；密钥在 vault |

## 3. 鉴权模型（D1-B）与访问

| 端点 | 鉴权 |
|---|---|
| `/healthz` | 无鉴权（探活）|
| `/report`、`/stats/ip_report` | 仅 `token` 头 = `REPORT_TOKEN`（agent 上报）|
| `/stats/*`、`/docs`、`/debug/*`、`/subs/logs` | `auth_guard`：(**经 CF**：`X-Monitor-Tunnel-Secret` 匹配 ∧ `CF-Connecting-IP` ∈ `ip_allowlist`) **或** `Authorization: Bearer <admin/stats token>` |

- **运维看仪表板**：从白名单 IP 经 `https://monitor.taoziyoyo.com/stats/ui` 访问，CF 自动注入 secret 头 → 免 token。
- **脚本/CLI/非白名单**：带 `Authorization: Bearer <stats_token>`（只读）或 `<admin_token>`。
- **本机/非经 CF 的请求无 secret 头 → 一律 401**（含本机 `curl 127.0.0.1:8000/stats/*`），这是预期（闭合本机绕过）。
- secret 双处一致：vault `vault_monitor_tunnel_secret` ↔ CF Transform Rule 注入值。**不一致或缺失 → fail-closed（仅 Bearer 可用）。**

**Cloudflare Transform Rule 配置**

必须配置为 **Request Header Transform Rule**，不是 Response Header：

```text
Rule name:
monitor inject tunnel secret

When incoming requests match:
http.host eq "monitor.taoziyoyo.com"

Modify request header:
Set static

Header name:
X-Monitor-Tunnel-Secret

Value:
<vault_monitor_tunnel_secret / MONITOR_TUNNEL_SECRET 的原始值，不加引号>
```

验证：

```bash
curl -s https://monitor.taoziyoyo.com/debug/whoami
```

预期 `tunnel_verified` 为 `true`，且 `client_ip` 是当前运维公网 IP。

## 4. 日常运维

**健康检查**
```bash
curl -s http://127.0.0.1:8000/healthz                 # {"status":"ok","db_ok":true,"journal_mode":"wal"}
systemctl status reality-monitor; journalctl -u reality-monitor -n 50
curl -s -H 'Authorization: Bearer <stats_token>' http://127.0.0.1:8000/stats/health   # 各节点 last_seen / stale
```

**新增运维访问 IP**（白名单渲染进 server.py）
1. 编辑 `group_vars/all/main.yml` → `monitor.ip_allowlist` 增 IP
2. `ansible-playbook deploy.yml --tags monitor_server --limit spt`（重渲染 + 重启）

**轮换 token**
1. `ansible-vault edit ...` 改对应 `vault_monitor_*`
2. server token（report/admin/stats/subs/tunnel）：`--tags monitor_server --limit spt`
3. agent token（report）：`--tags monitor_config --limit <nodes>`（分批）
4. **tunnel secret 还需同步改 CF Transform Rule 注入值**（否则仪表板 fail-closed）

**数据保留 / VACUUM**
- 自动：保留 cron 每日删 `> retention_days`（默认 90）的 records/subscription_logs/user_ip_hits + `wal_checkpoint(TRUNCATE)`
- 调整：改 `monitor.retention_days` → `--tags monitor_server --limit spt`
- 手动 cleanup：`POST /stats/cleanup?days=90` 仅接受 `Authorization: Bearer <admin_token>`，stats token 只读。
- **整库 VACUUM**（缩文件，会整库加锁，**低频手工、择低峰**）：`systemctl stop reality-monitor; sudo -u reality-monitor sqlite3 /opt/reality/monitor/data/traffic_monitor.db 'VACUUM;'; systemctl start reality-monitor`

**增 / 减节点 agent**
- 增：`ansible-playbook deploy.yml --tags monitor_config --limit <node>`（建用户/token/cron/脚本）
- 减/停某节点监控：`monitor_enabled: false`（host_vars/group_vars）→ 部署触发 cleanup（停服务、删脚本、移 cron）

**关停整套监控**：`monitor_enabled: false` → cleanup block 生效。

## 5. 故障排查

| 症状 | 排查 → 处理 |
|---|---|
| 仪表板经 CF 返回 **401** | 先确认 CF 配的是 **Request Header** Transform Rule，不是 Response Header；再核对 vault `tunnel_secret` 与 CF 注入值、运维 IP 是否在 `ip_allowlist`。临时：带 Bearer 访问 |
| 本机 `curl 127.0.0.1:8000/stats/*` 无 token **返回 200** | **回归！** 不该发生（应 401）：检查 `127.0.0.1` 是否被误加回 `ip_allowlist`、或 `auth_guard` 被改 |
| `user_ip_hits` 不增长（IP 审计死） | 查 `agent.log`；确认 `REALITY_MODE` 与日志路径（single=reality_core / multi=各容器）；`docker exec <c> cat /var/log/xray/access.log` 是否有 `email:`+`from`；server `/stats/ip_report` 是否 200 |
| `database is locked` / 整点 500 | `PRAGMA journal_mode` 应为 `wal`；确认 `get_conn` 的 `busy_timeout`；agent cron 抖动是否生效（`shuf`）|
| 单分钟巨值（幻象尖峰） | 应已根治（首见/计数器重置上报 0）。若现：查该节点 agent 版本、`state/traffic_cache.json` 是否损坏（删之即重建基线）|
| 某节点 agent 不上报 | cron 在 `reality-monitor-agent`？（`crontab -u reality-monitor-agent -l`）；用户在 docker 组？token 文件可读？`state` 可写？看 `agent.log`；nologin 用户 cron 是否真执行 |
| `state` 里 pending 持续增大 | server 不可达：查 server 健康 / 网络 / `REPORT_TOKEN` 是否一致（agent token 文件 vs server env）|
| `-wal` 文件巨大 | 保留 cron 是否在跑（`crontab -u reality-monitor -l`）；手工 `sqlite3 DB 'PRAGMA wal_checkpoint(TRUNCATE);'` |
| server 起不来 | `journalctl -u reality-monitor`；常见：`monitor.env` 缺失/token 空 → 鉴权全失败；DB 属主非 reality-monitor → 无法写 |

## 6. 部署 / 升级

- 常规升级（改了模板/配置）：`--check --diff` → 分批 `--tags monitor_server --limit spt`（server）/ `--tags monitor_config --limit <批>`（agent）。**永远带 `--limit`**（4 台测试机在 `[reality_nodes]` 内）。
- 首次加固迁移（root→专用用户、token 外置、D1-B、WAL）：按 [`deploy-checklist`](../../reviews/fix-monitor-integrity/deploy-checklist-2026-06-21.md) 走，含 DB 属主迁移、CF 前置、金丝雀顺序。

## 7. 安全模型备注

D1-B = "CF 注入共享密钥头 ∧ 运维 IP 白名单" 双条件放行 + Bearer 兜底。secret 证明"确经 CF 边缘"（本机/绕 CF 无法伪造，因 CF Transform Rule `Set` 覆盖客户端同名头），白名单证明"是运维"。两者正交，闭合"本机任意进程零 token 读全量数据"的 loopback 后门，同时仪表板零改动。详见 [`plan-harden-monitor`](../../reviews/fix-monitor-integrity/plan-harden-monitor-2026-06-13.md) §6 A1。
