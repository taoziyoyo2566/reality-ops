# Reality Ops

通过 Ansible 部署 Reality (Xray) 与流量监控。数据路径已统一为 `/opt/data`，便于集中管理与备份。

## 目录结构
- `deploy.yml`: 主部署入口 (Reality 单实例 + 监控)
- `reset.yml`: 删除容器 + 清理数据
- `audit.yml`: 统计最近访问日志
- `group_vars/all.yml`: 全局变量
- `users/*.yml`: 用户配置
- `roles/`: 角色定义

## 关键路径 (本地与远端统一)
- 数据目录: `/opt/data`
- 监控运行目录: `/opt/monitor`
- 监控虚拟环境: `/opt/monitor/venv`

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
- `reality_data_dir`: `/opt/data`
- `monitor_root_dir`: `/opt/monitor`
- `monitor_venv_dir`: `/opt/monitor/venv`
- `monitor.server_host`: 监控服务部署的节点名

## 部署
```bash
ansible -i inventory.ini all -m ping
ansible-playbook -i inventory.ini deploy.yml --check --diff
ansible-playbook -i inventory.ini deploy.yml --limit premium
```

> `deploy.yml` 默认使用 `roles/reality_single`。如需多实例，切换为 `roles/reality_multi`。

## 监控
- 服务名: `reality-monitor`
- 数据库: `/opt/data/traffic_monitor.db`
- 手动上报:
```bash
sudo /opt/monitor/venv/bin/python3 /usr/local/bin/traffic_agent.py
```
- 日志:
```bash
sudo journalctl -u reality-monitor -f
```
更多细节见 `MONITOR.md`。

## 常用操作
查看日志:
```bash
tail -n 500 /opt/data/reality_core/logs/access.log
```

审计日志:
```bash
ansible-playbook -i inventory.ini audit.yml
```

重置容器与数据:
```bash
ansible-playbook -i inventory.ini reset.yml --limit premium
```

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

## 备注
- `monitor.yml` 为旧版脚本 (依赖 `monitor/` 目录)，当前不作为主流程使用。
- 监控虚拟环境缺失 pip 时，会自动重建 `/opt/monitor/venv` 以提高可靠性。
