# 📊 Reality Traffic Monitor 使用指南

> 本文是快速使用指南；权威运维流程见 [`docs/features/monitor/operations.md`](docs/features/monitor/operations.md)。

## 1. 简介
这是一套自建的流量监控系统，采用 C/S 架构：
* **服务端**: 部署在 `spt`，使用 FastAPI + SQLite 存储数据，经 Cloudflare Tunnel 暴露 HTTPS。
* **客户端**: 部署在所有启用监控的节点，每分钟采集 Xray stats / 容器流量 / access.log 并上报。

## 当前状态（2026-06-22）
* `spt` 监控服务端已完成加固金丝雀部署，`reality-monitor.service` 以 `reality-monitor` 用户运行。
* `/healthz` 已验证为 `{"status":"ok","db_ok":true,"journal_mode":"wal"}`。
* `vault_monitor_tunnel_secret` 已配置，Cloudflare **Request Header Transform Rule** 已注入 `X-Monitor-Tunnel-Secret`，`https://monitor.taoziyoyo.com/stats/ui` 可从白名单 IP 免 Bearer 访问。
* `jp10` 已完成第一台 agent 灰度：专用用户/cron/docker 权限正常，`/stats/health` 已恢复 `stale=false`。
* 尚未全量升级 agent；扩面前需部署已修复的 agent 模板（single stats 解析会跳过无 `value` 项），并继续按 [`deploy-checklist`](docs/reviews/fix-monitor-integrity/deploy-checklist-2026-06-21.md) Phase 4/5 分批推进。

## 2. 访问地址
* **可视化仪表盘**: `https://monitor.taoziyoyo.com/stats/ui` （需 D1-B 白名单或 Bearer）
* **JSON 报表**: `https://monitor.taoziyoyo.com/stats/daily` （需 D1-B 白名单或 Bearer）
* **API 文档**: `https://monitor.taoziyoyo.com/docs` （需 D1-B 白名单或 Bearer）

> **注意**: 如果仪表盘返回 401，优先检查 CF **Request Header** Transform Rule 是否注入 `X-Monitor-Tunnel-Secret`，不要配成 Response Header；同时核对该值是否与 vault 一致，以及当前访问 IP 是否在 `monitor.ip_allowlist`。

## 3. 常用操作

### 查看今日流量
访问仪表盘 URL，默认显示今日（24小时）流量。
* **URL 参数**:
    * `hours=72` : 查看过去 3 天的数据。
    * `detail=false` : 不区分节点，只看用户总账。
    * **示例**: `.../stats/daily?hours=72&detail=false`

### 导出报表
* **CSV**: `.../stats/export?hours=24&detail=true&format=csv` （需 D1-B 白名单或 Bearer）
* **JSON**: `.../stats/export?hours=24&detail=true&format=json` （需 D1-B 白名单或 Bearer）

### 趋势与健康接口
* **时序趋势**: `.../stats/timeseries?hours=24&interval=3600` （需 D1-B 白名单或 Bearer）
* **节点健康**: `.../stats/health?hours=24&stale_minutes=10` （需 D1-B 白名单或 Bearer）

### UI 日志面板
仪表盘底部提供 **Client Logs**，会记录浏览器侧错误并保留本地历史（localStorage）。
如遇页面数据不刷新，优先查看日志面板与浏览器控制台。

### 手动测试上报 (在任意节点执行)
如果发现数据没更新，可以在节点上运行：
```bash
sudo /opt/reality/monitor/.venv/bin/python3 /usr/local/bin/traffic_agent.py
```
无报错即为成功。

### 检查服务端日志 (在 spt 执行)
```bash
# 查看 Python 服务日志
sudo journalctl -u reality-monitor -f

# 查看 Cloudflare Tunnel 日志（若本机以 systemd 管理 cloudflared）
sudo journalctl -u cloudflared -f
```

### 数据库维护
数据文件位于: `/opt/reality/monitor/data/traffic_monitor.db`
如果需要清空数据重来：
```bash
sudo systemctl stop reality-monitor
sudo rm /opt/reality/monitor/data/traffic_monitor.db*
sudo systemctl restart reality-monitor
```
如果需要清理过旧数据（保留天数）：
```bash
curl -X POST 'https://monitor.taoziyoyo.com/stats/cleanup?days=90' \
  -H 'Authorization: Bearer <ADMIN_BEARER>'
```

### 订阅访问日志（可选）
如果启用 `monitor.subs_proxy.enabled=true`，并将订阅链接替换为 `https://monitor.taoziyoyo.com/subs/<sub_id>?token=<subs_token>`，服务端会记录 IP / User-Agent / 时间戳 到数据库。
* 查看日志: `https://monitor.taoziyoyo.com/subs/logs?limit=200` （需 D1-B 白名单或 Bearer）

## 4. 故障排查
1.  **Agent 连不上**: 检查 `group_vars/all/main.yml` 中的 `monitor.server_url` 是否正确，以及监控入口端口（通常 443）是否放行。
2.  **仪表盘 401**: 核对 CF Request Header Transform Rule、`vault_monitor_tunnel_secret`、`monitor.ip_allowlist`，或临时带 `Authorization: Bearer <stats_token>` 访问。
