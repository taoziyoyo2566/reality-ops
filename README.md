# Reality Ops

通过 Ansible 部署 Reality (Xray) 与流量监控，所有运行文件集中在 `/opt/reality`，便于统一管理与备份。

## 快速上手
1) 安装依赖：`ansible-galaxy collection install community.general community.docker`
2) SSH 准备：`eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519`
3) 配置变量：编辑 `group_vars/all/main.yml`；在 `group_vars/all/vault.yml` 写随机 token 后用 `ansible-vault encrypt group_vars/all/vault.yml`
4) 预演/实跑：
   ```bash
   ansible -i inventory.ini deploy.yml --check --diff           # 预演
   ansible -i inventory.ini deploy.yml --ask-vault-pass         # 实跑
   ```

## 目录/路径
- Playbook：`deploy.yml`（主部署）、`reset.yml`（重置）、`audit.yml`（审计）
- 变量：`group_vars/all/main.yml`、`group_vars/all/vault.yml`
- 用户：`users/*.yml`
- 运行目录：`/opt/reality`；数据 `/opt/reality/data`；日志 `/opt/reality/logs`
- 监控：运行目录 `/opt/reality/monitor`；虚拟环境 `/opt/reality/monitor/.venv`

## 模式与开关
- `reality_mode`: `single` 或 `multi`
  - `single`：容器名 `reality_core`，管理全部用户。
  - `multi`：每用户一个容器 `reality_<user>`，由 docker compose 启动。
- 切换模式时幂等：
  - 从 multi 切回 single：会自动删除旧的 `reality_*`（保留 `reality_core`）和 `data/docker-compose.yml`。
  - 从 single 切到 multi：单实例容器会被移除，compose 重新生成。
- 监控开关：`monitor_enabled`（任意层变量都可覆盖）
  - `true`：部署/更新监控。
  - `false`：停止 `reality-monitor`、删除上报脚本与 cron。

## Inventory
- 组：`reality_nodes`（全部节点），`free`/`normal`/`premium`（分级），`spt`（本机，`ansible_connection=local`）。
- 按需用 `--limit <group|host>` 选择目标。

## 部署常用命令
```bash
ansible -i inventory.ini all -m ping
ansible-playbook -i inventory.ini deploy.yml --check --diff
ansible-playbook -i inventory.ini deploy.yml --limit premium --ask-vault-pass
```

## 监控
- 服务名 `reality-monitor`，数据库 `/opt/reality/data/traffic_monitor.db`
- 手动上报：`sudo /opt/reality/monitor/.venv/bin/python3 /usr/local/bin/traffic_agent.py`
- 日志：`sudo journalctl -u reality-monitor -f`
- 安全/白名单与 Bearer 配置见 `group_vars/all/main.yml`，token 存在 `group_vars/all/vault.yml`
- API 速览：
  - `POST /report`，header `token: <report_token>`，body `node/user/up_delta/down_delta`
  - 报表：`.../stats/ui`、`.../stats/daily`（需白名单或 Bearer）
  - 订阅代理（可选）：`.../subs/<sub_id>?token=<subs_token>`

## 用户脚本 (generate_user.py)
- 生成用户：`python3 generate_user.py add <name> [--port ...]`
- 删除用户：`python3 generate_user.py delete <name>`
- 查看：`python3 generate_user.py list`
- 需 `cryptography`，可用 `--docker` 选项免本地依赖。

## 日常运维
- 查看核心访问日志：`tail -n 500 /opt/reality/logs/reality_core/access.log`
- 重置：`ansible-playbook -i inventory.ini reset.yml --limit premium`
- Docker 容器列表：
  ```bash
  docker ps -a --filter name=reality_ --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}'
  ```

## 多实例流量统计
- 已内置：监控 agent 会根据 `reality_mode` 自动选择统计方式。
  - `single`: 读取 `reality_core` 的 Xray stats API。
  - `multi`: 枚举 `reality_<user>` 容器，读取容器内 `eth0` 的 rx/tx 字节数并上报 delta，等同于用户流量。
- 覆盖：切换模式后重跑 `deploy.yml` 即会同步更新上报逻辑。

## 备注
- `monitor.yml` 为旧版脚本，已弃用。
- 监控虚拟环境缺失 pip 时会自动重建 `/opt/reality/monitor/.venv`。
