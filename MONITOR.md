# 📊 Reality Traffic Monitor 使用指南

## 1. 简介
这是一套自建的流量监控系统，采用 C/S 架构：
* **服务端**: 部署在 `spt`，使用 FastAPI + SQLite 存储数据，Caddy 提供 HTTPS 访问。
* **客户端**: 部署在所有节点，每分钟采集 `reality_core` 容器流量并上报。

## 2. 访问地址
* **可视化仪表盘**: `https://monitor.taoziyoyo.com/stats/ui` （需白名单或 Bearer）
* **JSON 报表**: `https://monitor.taoziyoyo.com/stats/daily` （需白名单或 Bearer）
* **API 文档**: `https://monitor.taoziyoyo.com/docs` （需白名单或 Bearer）

> **注意**: 如果浏览器提示证书风险，请检查域名解析是否正确，并等待几分钟让 Caddy 自动申请证书。

## 3. 常用操作

### 查看今日流量
访问仪表盘 URL，默认显示今日（24小时）流量。
* **URL 参数**:
    * `hours=72` : 查看过去 3 天的数据。
    * `detail=false` : 不区分节点，只看用户总账。
    * **示例**: `.../stats/daily?hours=72&detail=false`

### 导出报表
* **CSV**: `.../stats/export?hours=24&detail=true&format=csv` （需 Bearer）
* **JSON**: `.../stats/export?hours=24&detail=true&format=json` （需 Bearer）

### 趋势与健康接口
* **时序趋势**: `.../stats/timeseries?hours=24&interval=3600` （需 Bearer）
* **节点健康**: `.../stats/health?hours=24&stale_minutes=10` （需 Bearer）

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

# 查看 Caddy 反代日志
sudo journalctl -u caddy -f
```

### 数据库维护
数据文件位于: `/opt/reality/data/traffic_monitor.db`
如果需要清空数据重来：
```bash
rm /opt/reality/data/traffic_monitor.db
sudo systemctl restart reality-monitor
```
如果需要清理过旧数据（保留天数）：
```bash
curl -X POST 'https://monitor.taoziyoyo.com/stats/cleanup?days=90' \
  -H 'Authorization: Bearer <ADMIN_OR_STATS_BEARER>' \
  -H 'token: <REPORT_TOKEN>'
```

### 订阅访问日志（可选）
如果启用 `monitor.subs_proxy.enabled=true`，并将订阅链接替换为 `https://monitor.taoziyoyo.com/subs/<sub_id>?token=<subs_token>`，服务端会记录 IP / User-Agent / 时间戳 到数据库。
* 查看日志: `https://monitor.taoziyoyo.com/subs/logs?limit=200` （需 Bearer）
```

## 4. 故障排查
1.  **Agent 连不上**: 检查 `group_vars/all.yml` 中的 `server_url` 是否正确，以及 spt 的 8443 端口是否放行。
2.  **HTTPS 证书错误**: 确保你的域名已经 A 记录解析到了 spt 的 IP，且 spt 的 80 端口对外开放（Caddy 需要用 80 端口验证）。
