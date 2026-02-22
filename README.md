# Reality Ops

通过 Ansible 部署 Reality (Xray) 节点、流量监控与订阅分发，所有运行数据集中在 `/opt/reality`，便于统一管理、备份和切换单/多实例。

## 核心功能
- 一键部署 Reality：支持单实例与多实例两种模式，自动完成 BBR/FastOpen 优化、镜像拉取、容器/compose 启停。
- 流量监控内置：FastAPI + SQLite 服务端（`reality-monitor`）+ 每分钟上报的 agent，提供 UI、报表、健康检查和订阅访问日志。
- 订阅分发：自动生成节点订阅文件并更新到 GitHub Gist，可选经监控域名代理并记录访问日志。
- 用户管理脚本：`generate_user.py` 生成/删除/查看用户文件，避免手写端口和密钥。
- 运维工具：`audit.yml` 全网 IP 审计，`reset.yml` 清理节点，`speedlimit.sh` 等辅助脚本。

## 主要组成
- Playbook：`deploy.yml`（主部署）、`reset.yml`（重置）、`audit.yml`（审计）。
- Roles：`reality_single`（单实例）、`reality_multi`（多实例 compose）、`monitor`（监控服务端/agent）。
- 变量：`group_vars/all/main.yml`（公共配置）、`group_vars/all/vault.yml`（token/密钥，需 `ansible-vault` 加密），可用 `host_vars/<host>.yml` 覆盖。
- 用户配置：`users/*.yml`（JSON 结构），每个用户包含 `name/uuid/port/short_id/private_key/public_key`，可选 ACL 字段 `groups/hosts`（默认 `groups=["free"]`、`hosts=[]`）。
- 运行目录：`/opt/reality`；数据 `/opt/reality/data`；日志 `/opt/reality/logs`；监控 `/opt/reality/monitor` 与虚拟环境 `/opt/reality/monitor/.venv`。

## 前置准备
1) 安装依赖  
   `ansible-galaxy collection install community.general community.docker`
2) SSH 准备  
   `eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519`
3) 配置变量  
   - 编辑 `group_vars/all/main.yml`：域名、镜像、监控开关/端口、订阅代理等。  
   - 在 `group_vars/all/vault.yml` 写入随机 `monitor.report_token/admin_bearer_token/stats_bearer_token/subs_token` 等后加密：  
     `ansible-vault encrypt group_vars/all/vault.yml`
4) 定义节点  
   - `inventory.ini` 中 `reality_nodes` 是部署目标；可用 `free/basic/normal/premium`（及 `special/cmi/netflix` 等特性组）配合 `--limit` 选择。  
   - 每台主机可在 `host_vars/<host>.yml` 设置 `reality_mode`（single/multi）与 `monitor_enabled`。
5) 准备用户  
   - 推荐用脚本：`python3 generate_user.py add <name> [--port ...] [--groups ...] [--hosts ...]`；删除：`python3 generate_user.py delete <name>`；查看：`python3 generate_user.py list`。  
   - 文件会写到 `users/`，格式示例：  
     ```json
     {
       "name": "alice",
       "uuid": "xxxx",
       "port": 23456,
       "short_id": "abcd1234efgh5678",
       "private_key": "...",
       "public_key": "...",
       "groups": ["free"],
       "hosts": []
     }
     ```

## 配置示例（复制后改值即可）
- `group_vars/all/main.yml` 核心字段示例：  
  ```yaml
  xray_image: "taoziyoyo2566/xray_docker:latest"
  domain_suffix: "example.com"
  reality_server_names: ["www.apple.com", "images.apple.com"]
  reality_dest: "www.apple.com:443"
  reality_root_dir: "/opt/reality"
  monitor:
    server_host: "spt"
    server_url: "https://monitor.example.com"
    subs_base_url: "https://subs.example.com"
    port: 8000
    ip_allowlist: ["127.0.0.1"]
    subs_proxy:
      enabled: true
      require_token: true
  ```
- `group_vars/all/vault.yml` 机密示例（填随机值后加密）：  
  ```yaml
  vault_monitor_report_token: "report-token"
  vault_monitor_admin_bearer_token: "admin-bearer"
  vault_monitor_stats_bearer_token: "stats-bearer"
  vault_monitor_subs_token: "subs-token"
  vault_monitor_gist_user: "your-gh-user"
  vault_monitor_gist_id: "gist-id"
  vault_github_token: "ghp_xxx"  # 用于推 Gist
  ```
  `ansible-vault encrypt group_vars/all/vault.yml`
- `host_vars/<host>.yml` 示例：  
  ```yaml
  # 单实例开启监控
  reality_mode: single
  monitor_enabled: true
  ```
  ```yaml
  # 多实例且关闭监控
  reality_mode: multi
  monitor_enabled: false
  ```

## 完整使用指南（一步到位）
1) **填变量**  
   - `group_vars/all/main.yml`：域名、镜像、`monitor` 开关/端口、订阅代理。  
   - `group_vars/all/vault.yml`：随机生成 `monitor.report_token/admin_bearer_token/stats_bearer_token/subs_token`，再 `ansible-vault encrypt group_vars/all/vault.yml`。  
   - 每台主机在 `host_vars/<host>.yml` 设定 `reality_mode`（single/multi）、`monitor_enabled`。  
2) **准备用户**  
   - `python3 generate_user.py add <name> [--port ...] [--groups ...] [--hosts ...]` 生成到 `users/`；已有文件用 `--force` 覆盖。  
   - 老用户迁移标签：`python3 generate_user.py update <name> --groups <...> [--hosts ...]`（仅更新 ACL）。  
   - 删除：`python3 generate_user.py delete <name>`；查看：`python3 generate_user.py list`。  
3) **连通性与预演**  
   - 连通测试：`ansible -i inventory.ini all -m ping`  
  - 预演：`ansible-playbook -i inventory.ini deploy.yml --check --diff`（只看会改什么）。  
4) **正式部署**  
  - 全量：`ansible-playbook -i inventory.ini deploy.yml --ask-vault-pass`  
  - 只改用户/实例：`ansible-playbook -i inventory.ini deploy.yml --tags users --ask-vault-pass`（最快）  
   - 指定范围：追加 `--limit <group|host>`。  
   - 切换模式：修改目标主机的 `reality_mode`，再跑同一命令；单/多实例会自动清理旧容器/compose。  
5) **订阅/Gist（可选但常用）**  
   - 确保有环境变量：`GITHUB_TOKEN`、`GIST_ID`、`GITHUB_USER`、`SUBS_BASE_URL`（可用 `monitor.subs_base_url`），可选 `SUBS_TOKEN`。  
   - 部署结束后 `post_tasks` 会在本机执行 `generate_subs_gist.py`：成功会生成 `SUBSCRIPTIONS.txt`，输出每个用户订阅链接（默认走监控/订阅域名代理，可记录访问日志）。  
6) **验证监控**  
   - 服务端：`sudo journalctl -u reality-monitor -f`；数据库 `/opt/reality/data/traffic_monitor.db`。  
   - Agent：`sudo /opt/reality/monitor/.venv/bin/python3 /usr/local/bin/traffic_agent.py`（手动上报）；cron 每分钟运行。  
   - UI/API（需 IP 白名单或 Bearer）：`.../stats/ui`、`.../stats/daily`、`.../stats/timeseries`、`.../stats/health`；订阅日志 `.../subs/logs`。  
7) **日常操作**  
   - 重置节点：`ansible-playbook -i inventory.ini reset.yml --limit <group|host>`。  
   - 全网审计：`ansible-playbook -i inventory.ini audit.yml`，输出用户-IP 去重统计。  
   - 查看日志：`tail -n 500 /opt/reality/logs/reality_core/access.log`；容器状态：`docker ps -a --filter name=reality_ --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}'`。  
8) **故障排查速查**  
   - Agent 上报失败：检查 `group_vars/all/main.yml` 的 `monitor.server_url` 和 token；`journalctl -u reality-monitor` 看服务端；尝试手动上报命令。  
   - 证书/域名：确认域名 A 记录到监控节点，80/443 放通，等待 Caddy 自动签证书。  
   - 订阅 404/403：检查 `monitor.subs_proxy.enabled`、Gist 变量、`SUBS_TOKEN` 与访问 URL。  

## 常见场景操作
- 新增用户并同步：`python3 generate_user.py add bob` → `ansible-playbook -i inventory.ini deploy.yml --tags users --ask-vault-pass`
- 新增受限用户（仅某组可用）：`python3 generate_user.py add bob --groups netflix` → `ansible-playbook -i inventory.ini deploy.yml --tags users --ask-vault-pass`
- 回收某节点权限：编辑 `users/<name>.yml` 的 `groups/hosts` 后，执行 `ansible-playbook -i inventory.ini deploy.yml --limit <host> --tags users --ask-vault-pass`
- 切到多实例：在目标 `host_vars/<host>.yml` 设 `reality_mode: multi` → 跑主部署；回切 single 同理。
- 关闭监控（单台）：该主机 `monitor_enabled: false` → 部署；会停服务/删 agent/cron。
- 只更新订阅/Gist：确保环境变量到位，`ansible-playbook -i inventory.ini deploy.yml --tags users --ask-vault-pass`（post_tasks 会自动跑 Gist）。
- 手动重启监控服务：`sudo systemctl restart reality-monitor`（spt 节点）；查看日志 `journalctl -u reality-monitor -f`。
- 清理节点：`ansible-playbook -i inventory.ini reset.yml --limit <group|host>`
- 审计用户-IP：`ansible-playbook -i inventory.ini audit.yml`

## 使用流程与常用命令
- 连通性：`ansible -i inventory.ini all -m ping`
- 预演（不改动）：`ansible-playbook -i inventory.ini deploy.yml --check --diff`
- 部署：`ansible-playbook -i inventory.ini deploy.yml --ask-vault-pass`
- 仅改用户/实例（最快）：`ansible-playbook -i inventory.ini deploy.yml --tags users --ask-vault-pass`
- 用户变更并清理旧多实例：`ansible-playbook -i inventory.ini deploy.yml --tags users,cleanup --ask-vault-pass`
- 指定分组/主机：在以上命令加 `--limit <group|host>`，例如 `--limit premium`

## 模式与标签
- `reality_mode`:  
  - `single`（默认）：单容器 `reality_core` 管理全部用户。  
  - `multi`：每用户一个容器 `reality_<user>`，由 docker compose 统一启动。
- 幂等切换：  
  - multi → single：自动删除旧的 `reality_*`（保留 `reality_core`）与 `data/docker-compose.yml`。  
  - single → multi：会移除单实例容器，重新生成 compose。
- 监控开关：`monitor_enabled`（任意层变量可覆盖），为 `false` 时停止 `reality-monitor`、删除 agent 与 cron。
- 标签：  
  - `users`（默认快速变更用户/实例）  
  - `system`（BBR/依赖/Docker 镜像）  
  - `cleanup`（多实例残留清理，不在 `users` 中，需显式添加）  
  - `monitor`（监控角色）  
  - `always`（预加载用户配置，保持幂等）

## 节点 ACL（Tag-based）
- 用户可通过 `groups`（节点组）和 `hosts`（节点名）控制可下发范围；二者任一命中即授权。
- 新增用户默认 `groups=["free"]`、`hosts=[]`；老用户若缺失 `groups` 字段，兼容期内仍按 `["all"]` 处理。
- 典型创建命令：`python3 generate_user.py add bob --groups netflix`。
- 回收权限后，建议按目标节点灰度执行：`ansible-playbook -i inventory.ini deploy.yml --limit <host> --tags users --ask-vault-pass`，会同时清理该主机本地旧订阅缓存，避免幽灵订阅。

## 监控与报表
- 服务：Systemd 单元 `reality-monitor`，数据库 `/opt/reality/data/traffic_monitor.db`。
- Agent：`/usr/local/bin/traffic_agent.py` 每分钟上报（cron），自动根据 `reality_mode` 选择统计方式。  
  - single：读取 `reality_core` 的 Xray stats API。  
  - multi：枚举 `reality_<user>` 容器，读取容器内 `eth0` 的 rx/tx 并上报 delta。  
  - 手动触发：`sudo /opt/reality/monitor/.venv/bin/python3 /usr/local/bin/traffic_agent.py`
- 日志：`sudo journalctl -u reality-monitor -f`
- 访问与接口（需 IP 白名单或 Bearer，配置见 `group_vars/all/main.yml`）：  
  - UI：`.../stats/ui`  
  - 报表：`GET .../stats/daily?hours=24&detail=true`，时间序列：`.../stats/timeseries`  
  - 健康：`.../stats/health`  
  - 订阅访问日志（可选）：启用 `monitor.subs_proxy.enabled` 后，订阅链接 `.../subs/<sub_id>?token=<subs_token>`，日志 `.../subs/logs`

## 订阅与 Gist 更新
- 部署完成后 `post_tasks` 会在本机运行 `generate_subs_gist.py`，需要环境变量：`GITHUB_TOKEN`、`GIST_ID`、`GITHUB_USER`、`SUBS_BASE_URL`（可配合 `monitor.subs_base_url`）、可选 `SUBS_TOKEN`。
- 成功后会在项目根生成 `SUBSCRIPTIONS.txt`，包含每个用户的订阅链接（默认经监控/订阅域名代理）。

## 日常运维
- 查看访问日志：`tail -n 500 /opt/reality/logs/reality_core/access.log`
- 重置节点：`ansible-playbook -i inventory.ini reset.yml --limit <group|host>`
- Docker 状态：`docker ps -a --filter name=reality_ --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}'`

## 备注
- `monitor.yml` 为旧版脚本，已弃用。
- 监控虚拟环境缺 pip 时会自动重建 `/opt/reality/monitor/.venv`。


ansible-playbook -i inventory.ini audit.yml --vault-password-file ~/.vault_pass

ansible-playbook -i inventory.ini deploy.yml --tags users --vault-password-file ~/.vault_pass
