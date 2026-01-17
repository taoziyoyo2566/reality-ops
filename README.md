# Reality Ops

通过 Ansible 部署 Reality (Xray) 与流量监控。所有运行文件、数据与虚拟环境集中在 `/opt/reality`，便于统一管理与备份。

## 目录结构
- `deploy.yml`: 主部署入口 (Reality 单实例 + 监控)
- `reset.yml`: 删除容器 + 清理数据
- `audit.yml`: 统计最近访问日志
- `group_vars/all.yml`: 全局变量
- `users/*.yml`: 用户配置
- `roles/`: 角色定义

## 关键路径 (本地与远端统一)
- 项目根目录: `/opt/reality`
- 数据目录: `/opt/reality/data`
- 日志目录: `/opt/reality/logs`
- 监控运行目录: `/opt/reality/monitor`
- 监控虚拟环境: `/opt/reality/monitor/.venv`

## 前置条件
- 控制机: 已安装 `ansible`，可 SSH 连接远端
- 远端: `sudo` 权限 + `python3`
- Ansible 集合: `community.general`、`community.docker`

安装集合:
```bash
ansible-galaxy collection install community.general community.docker
```

## SSH 连接准备
```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

若非 UID=1000 用户，建议配置免密 sudo:
```bash
sudo visudo
kagoya ALL=(ALL) NOPASSWD: ALL
```

## Inventory 说明
`inventory.ini` 中包含分组:
- `reality_nodes`: 所有 Reality 节点
- `free`、`normal`、`premium`: 分级分组
- `spt`: 本机节点 (使用 `ansible_connection=local`)

## 配置入口
`group_vars/all.yml` 里常用变量:
- `reality_root_dir`: `/opt/reality`
- `reality_data_dir`: `/opt/reality/data`
- `reality_logs_dir`: `/opt/reality/logs`
- `monitor_root_dir`: `/opt/reality/monitor`
- `monitor_venv_dir`: `/opt/reality/monitor/.venv`
- `monitor.server_host`: 监控服务部署的节点名

## 快速上手
1) 安装集合: `ansible-galaxy collection install community.general community.docker`  
2) 准备 SSH: `eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519`  
3) 填写变量: 编辑 `group_vars/all/main.yml`；在 `group_vars/all/vault.yml` 写入随机 token 后加密。  
4) 预演/实跑:  
   ```bash
   ansible -i inventory.ini deploy.yml --check --diff           # 预演；BBR 校验在 check 模式会自动跳过
   ansible -i inventory.ini deploy.yml --ask-vault-pass         # 实跑；或用 --vault-password-file ~/.vault_pass.txt
   ```  
5) 验证: `sudo journalctl -u reality-monitor -f` 或访问 `https://monitor.taoziyoyo.com/stats/ui`。

## 部署
```bash
ansible -i inventory.ini all -m ping
ansible-playbook -i inventory.ini deploy.yml --check --diff
ansible-playbook -i inventory.ini deploy.yml --limit premium
# 若启用了 Vault（group_vars/all/vault.yml 已加密），在任何 playbook 命令后追加：
#   --ask-vault-pass          # 交互输入密码
#   或 --vault-password-file ~/.vault_pass.txt
# 示例：
# ansible-playbook -i inventory.ini deploy.yml --check --diff --ask-vault-pass
```

> `deploy.yml` 默认使用 `roles/reality_single`。如需多实例，切换为 `roles/reality_multi`。

## 监控
- 服务名: `reality-monitor`
- 数据库: `/opt/reality/data/traffic_monitor.db`
- 手动上报:
```bash
sudo /opt/reality/monitor/.venv/bin/python3 /usr/local/bin/traffic_agent.py
```
- 日志:
```bash
sudo journalctl -u reality-monitor -f
```
更多细节见 `MONITOR.md`。

## 常用操作
查看日志:
```bash
tail -n 500 /opt/reality/logs/reality_core/access.log
```

审计日志:
```bash
ansible-playbook -i inventory.ini audit.yml
```

重置容器与数据:
```bash
ansible-playbook -i inventory.ini reset.yml --limit premium
```

## 用户配置脚本 (generate_user.py)
- 依赖: 需要 `cryptography`。可执行 `python3 -m ensurepip --default-pip && python3 -m pip install cryptography`，或用发行版包管理器安装 (`sudo apt install python3-cryptography`)。
- 生成/覆盖: `python3 generate_user.py add <name>`（兼容旧用法 `python3 generate_user.py <name>`）；可用 `--port` 指定端口，`--min-port/--max-port` 控制自动分配范围，`--users-dir` 指定目录（不存在会自动创建）。
- 删除: `python3 generate_user.py delete <name>` 删除对应的 yml/yaml/json。
- 查看: `python3 generate_user.py list` 默认只显示 yml/yaml；如需包含 json 文件可加 `--include-json`（不展开数组）；如需展开 json 数组逐条查看，可加 `--details`（自动包含 json）。
- 用户名只允许字母、数字、下划线、短横线，避免路径注入。
- 无需在本机安装依赖时，可用 Docker:  
  ```bash
  python3 generate_user.py --docker add alice
  python3 generate_user.py --docker list --users-dir /opt/reality/users
  ```  
  默认镜像 `python:3.11-slim`，可用 `--docker-image` 调整。

## 监控安全配置（IP 白名单 + Bearer + Vault）
1) 生成随机 Token（示例命令）  
```bash
openssl rand -hex 32   # 生成上报/管理 token
openssl rand -hex 24   # 生成订阅代理 token（可选）
```  
2) 填写并加密 Vault  
- 编辑 `group_vars/all/vault.yml`，用上一步生成的随机串替换所有 `CHANGEME-...`。  
- 加密：`ansible-vault encrypt group_vars/all/vault.yml`（如需编辑：`ansible-vault edit group_vars/all/vault.yml`）。  
3) 配置白名单与可信头  
- 在 `group_vars/all.yml` 设置 `monitor.ip_allowlist`（如 CF 出口 IP 或内网网段）。  
- 如使用 Cloudflare，保持 `monitor.trust_proxy_header: CF-Connecting-IP`；否则改为 `X-Forwarded-For`。  
4) 可选：订阅访问日志代理  
- 设 `monitor.subs_proxy.enabled=true`，填写 `gist_user`/`gist_id`，并将订阅链接替换为 `https://monitor.taoziyoyo.com/subs/<sub_id>?token=<subs_token>`。  
- 查看日志：`https://monitor.taoziyoyo.com/subs/logs?limit=200`（需 Bearer）。  
5) 部署  
```bash
ansible-playbook -i inventory.ini deploy.yml --limit spt
```  
6) 访问方式  
- 白名单 IP 可直接访问 `https://monitor.taoziyoyo.com/stats/ui` 等。  
- 非白名单需携带 Header：`Authorization: Bearer <admin_or_stats_token>`。  
- 上报仍用 `report_token`，curl 手动上报示例：  
```bash
curl -X POST https://monitor.taoziyoyo.com/report \
  -H "token: 7aa0542d49ea68ae5547e262e8c9632116114e3a1cd55648" \
  -H "Content-Type: application/json" \
  -d '{"node":"netcup","user":"reap","up_delta":1,"down_delta":1}'
```

## API/上报速览
- 上报接口: `POST /report`，Header `token: <report_token>`，JSON 体包含 `node`、`user`、`up_delta`、`down_delta`（字节数）。  
- 手动测试: `sudo /opt/reality/monitor/.venv/bin/python3 /usr/local/bin/traffic_agent.py` 或使用上面的 curl 示例。  
- 报表/仪表盘: `.../stats/ui`、`.../stats/daily`，需白名单或 Bearer。  
- 订阅代理（可选）: 启用 `monitor.subs_proxy.enabled` 后，访问 `.../subs/<sub_id>?token=<subs_token>`。

## 防火墙放行端口
```bash
grep -hEo '"?port"?: ?[0-9]+' users/*.yml users/*.json \
| grep -oE '[0-9]+' \
| sort -u \
| xargs -I{} sudo ufw allow {}/tcp
```

## Docker 容器查看
```bash
docker ps -a \
  --filter name=reality_ \
  --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}'
```

## Admin API（控制机）
- FastAPI 管理接口 `admin_server.py`（供 n8n/Bot 调用），部署在宿主机。
- 先执行 `./scripts/setup_admin_venv.sh` 创建虚拟环境，再按 `ADMIN_API.md` 配置 systemd（记得设置 `ADMIN_TOKEN` 和订阅域名）。
- 详细说明与 systemd 模板见 `ADMIN_API.md` 与 `systemd/reality-admin.service`。

## 备注
- `monitor.yml` 为旧版脚本 (依赖 `monitor/` 目录)，当前不作为主流程使用。
- 监控虚拟环境缺失 pip 时，会自动重建 `/opt/reality/monitor/.venv` 以提高可靠性。
