# Reality Ops

Reality Ops 是一套基于 Ansible 的 Reality (Xray) 节点编排项目，包含三条主线：
- 节点部署：单实例/多实例两种模式自动编排。
- 流量监控：FastAPI + SQLite 服务端 + 节点 Agent 每分钟上报。
- 订阅分发：汇总每个用户的节点订阅并推送到 GitHub Gist。

## 项目梳理（执行链路）
1. 控制端读取 `users/*.yml`，根据 `groups/hosts` + `acl_matrix` 在每台节点计算授权用户集合（`reality_instances`）。
2. 根据节点 `reality_mode` 进入 `reality_single` 或 `reality_multi` 角色部署 Xray。
3. 在控制端生成 `/opt/reality/users/*_<host>.json` 节点订阅缓存文件。
4. 首台执行主机在 `post_tasks` 调用 `generate_subs_gist.py`，把订阅聚合后更新 Gist。
5. 监控角色按 `monitor_enabled` 统一部署：
- `monitor.server_host` 节点部署 FastAPI 服务（`reality-monitor` systemd）。
- 全部节点部署 `traffic_agent.py` + cron 每分钟上报流量与用户 IP。

## 目录结构
- `deploy.yml`：主部署入口（含 ACL 过滤 + role 调度 + Gist 更新）。
- `reset.yml`：清理容器/数据/本地订阅缓存并可选回写 Gist。
- `audit.yml`：聚合 `access.log` 做用户-IP 去重审计。
- `roles/reality_single/`：单实例 `reality_core` 容器模式。
- `roles/reality_multi/`：多实例 `reality_<user>` + compose 模式。
- `roles/monitor/`：监控服务端与 agent。
- `group_vars/all/main.yml`：全局非密钥配置。
- `group_vars/all/vault.yml`：密钥（建议全文件 ansible-vault 加密）。
- `host_vars/*.yml`：主机级覆盖（如 `reality_mode`、`monitor_enabled`）。
- `users/*.yml`：用户配置（JSON 结构写入 `.yml` 文件）。
- `generate_user.py`：用户文件生成/更新/删除/列举工具。
- `generate_subs_gist.py`：订阅聚合与 Gist 推送脚本。

## 运行依赖
- 控制端：`ansible`、`python3`。
- Ansible collections：
```bash
ansible-galaxy collection install community.general community.docker
```
- SSH 准备：
```bash
eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519
```
- 目标机：Debian/Ubuntu、Docker Engine 可用、支持 sudo/become。
- 可选：`ansible-vault`（推荐，保护 token）。

## 配置入口
### 1) 全局配置 `group_vars/all/main.yml`
关键字段：
- `xray_image`：节点镜像。
- `domain_suffix` + `server_hash_suffix`：订阅域名拼接。
- `reality_server_names`、`reality_dest`：Reality 握手参数。
- `reality_root_dir` / `reality_data_dir` / `reality_logs_dir`：运行目录。
- `monitor.*`：监控地址、鉴权 token、订阅代理配置。
- `acl_matrix`：节点组授权矩阵。

### 2) 密钥配置 `group_vars/all/vault.yml`
建议至少包含：
- `vault_monitor_report_token`
- `vault_monitor_admin_bearer_token`
- `vault_monitor_stats_bearer_token`
- `vault_monitor_subs_token`
- `vault_monitor_gist_user`
- `vault_monitor_gist_id`
- `vault_github_token`

加密示例：
```bash
ansible-vault encrypt group_vars/all/vault.yml
```

### 3) 主机覆盖 `host_vars/<host>.yml`
常用字段：
- `reality_mode: single|multi`
- `monitor_enabled: true|false`

### 4) 节点清单 `inventory.ini`
- `reality_nodes`：所有部署目标。
- `free/basic/normal/premium/...`：ACL 用分组。
- `spt` 默认配置为 `ansible_connection=local`，用于本机监控服务端场景。

## ACL 规则（用户可见性）
用户文件支持：
- `groups`: 节点组标签列表。
- `hosts`: 允许的具体主机名列表。

匹配逻辑：
- 命中 `groups` 与节点放行标签交集，或命中 `hosts` 任一主机，即下发该用户到当前节点。
- 历史兼容：缺失 `groups` 字段时，部署逻辑按 `['all']` 处理（即不过滤）。
- `generate_user.py add` 默认写入 `groups=["free"]`、`hosts=[]`。

## 快速开始
### 1) 准备用户
```bash
python3 generate_user.py add alice
python3 generate_user.py add bob --groups netflix --hosts ams,dcc
python3 generate_user.py update bob --groups basic --hosts ams
python3 generate_user.py list --wide
```

### 2) 连通性检查
```bash
ansible -i inventory.ini all -m ping
```

### 3) 首次预演
```bash
ansible-playbook -i inventory.ini deploy.yml --check --diff --vault-password-file ~/.vault_pass
```

### 4) 正式部署
```bash
ansible-playbook -i inventory.ini deploy.yml --vault-password-file ~/.vault_pass
```

### 5) 日常仅改用户
```bash
ansible-playbook -i inventory.ini deploy.yml --tags users --vault-password-file ~/.vault_pass
```

### 6) 指定范围灰度
```bash
ansible-playbook -i inventory.ini deploy.yml --limit premium --tags users --vault-password-file ~/.vault_pass
```

## Playbook 与标签
### `deploy.yml`
- `users`：用户配置、容器编排、订阅缓存生成。
- `system`：sysctl/包安装/基础环境。
- `docker`：镜像与容器相关任务。
- `update_image`：强制拉取最新镜像。
- `cleanup`：清理旧模式残留。
- `monitor`：监控服务/agent。
- `gist`：Gist 推送。
- `always`：预加载用户配置与 ACL 计算。

说明：`--tags users` 是最快路径，但首次初始化建议至少跑一次完整部署，确保依赖齐全。

### `reset.yml`
- 清理 `reality_core` 和全部 `reality_*` 容器。
- 清理数据目录、日志目录、compose 文件。
- 清理本地 `/opt/reality/users/*_<host>.json`。
- 可选触发 Gist 更新（依赖 vault token）。

### `audit.yml`
- 从各节点 `{{ reality_logs_dir }}/reality_core/access.log` 抽取用户与源 IP。
- 在本机汇总输出去重后的用户-IP 统计。

## 监控系统
### 部署行为
- 服务端仅在 `monitor.server_host` 部署：`/opt/reality/monitor/server.py` + `reality-monitor.service`。
- 客户端在所有 `monitor_enabled=true` 节点部署：`/usr/local/bin/traffic_agent.py` + 每分钟 cron。
- 数据库：`{{ reality_data_dir }}/traffic_monitor.db`。

### 鉴权模型
- `/report`：仅检查 `token` header（agent 上报入口）。
- `/stats/*`、`/docs`、`/openapi.json`：IP 白名单或 Bearer Token 访问。
- `/stats/cleanup` 与 `/stats/ip_report`：除 Bearer/IP 外，还要求 `token: REPORT_TOKEN`。

### 常用接口
- `GET /stats/ui`
- `GET /stats/daily?hours=24&detail=true`
- `GET /stats/timeseries?hours=24&interval=3600`
- `GET /stats/health?hours=24&stale_minutes=10`
- `GET /stats/export?hours=24&detail=true&format=csv`
- `GET /stats/ip_matrix?hours=72`
- `GET /subs/logs?limit=200`

## 订阅分发（Gist）
- 数据源：控制端本地 `/opt/reality/users/*.json`。
- 脚本：`generate_subs_gist.py`。
- 部署时自动注入环境变量并执行（无需手工 export）。
- 若手工执行，需提供：`GITHUB_TOKEN`、`GIST_ID`、`GITHUB_USER`、`SUBS_BASE_URL`。

手工执行示例：
```bash
GITHUB_TOKEN=... \
GIST_ID=... \
GITHUB_USER=... \
SUBS_BASE_URL=https://subs.example.com \
SUBS_TOKEN=... \
python3 generate_subs_gist.py
```

## generate_user.py 说明
### 功能
- `add`：创建用户文件并生成 UUID、端口、short_id、X25519 密钥。
- `update`：仅更新 ACL（`groups/hosts`）。
- `delete`：删除对应用户文件（按 `.yml/.yaml/.json` 顺序查找）。
- `list`：列出用户和端口；`--wide` 额外显示 ACL 视图。

### 典型命令
```bash
python3 generate_user.py add alice
python3 generate_user.py add alice --force --port 24001
python3 generate_user.py add carol --groups basic,netflix --hosts ams
python3 generate_user.py update carol --groups premium --hosts ""
python3 generate_user.py delete alice
python3 generate_user.py list --wide
python3 generate_user.py --docker add dave
```

### 文件结构示例
```json
{
  "name": "alice",
  "uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "port": 24567,
  "short_id": "a1b2c3d4e5f60789",
  "private_key": "...",
  "public_key": "...",
  "groups": ["free"],
  "hosts": []
}
```

## 常见运维命令
```bash
# 监控服务日志（monitor.server_host）
sudo journalctl -u reality-monitor -f

# 手动触发 agent 上报
sudo /opt/reality/monitor/.venv/bin/python3 /usr/local/bin/traffic_agent.py

# 查看单实例访问日志
tail -n 300 /opt/reality/logs/reality_core/access.log

# 重置某组节点
ansible-playbook -i inventory.ini reset.yml --limit free --vault-password-file ~/.vault_pass

# 全网 IP 审计
ansible-playbook -i inventory.ini audit.yml --vault-password-file ~/.vault_pass
```

## 故障排查
- `--tags users` 失败且提示 `rsync` 缺失：先执行完整部署或在目标机安装 `rsync`。
- 订阅未更新：确认 `vault_github_token` 与 Gist 参数已配置。
- 监控页面 401：检查访问 IP 是否在 `monitor.ip_allowlist` 或 Bearer 是否正确。
- 节点无用户容器：优先检查 ACL（`groups/hosts`、`inventory` 分组、`acl_matrix`）。

## 兼容与遗留
- `monitor.yml`、`monitor_server.py` 为旧方案文件，当前主流程不依赖。
- `group_vars/all.yml` 标记为 deprecated，请使用 `group_vars/all/main.yml`。
