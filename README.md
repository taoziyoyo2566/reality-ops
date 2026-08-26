# Reality Ops

Reality Ops 是一套基于 Ansible 的 Reality (Xray) 节点编排项目，包含三条主线：
- 节点部署：单实例/多实例两种模式自动编排。
- 流量监控：FastAPI + SQLite 服务端 + 节点 Agent 每分钟上报。
- 订阅分发：汇总每个用户的节点订阅并推送到 GitHub Gist。

> 📖 **本文是项目参考**（架构 / 目录 / 配置字段 / 设计说明）。
> 🛠 **所有运维命令、操作步骤、故障排查见 [`docs/operations.md`](docs/operations.md)。**

## 项目梳理（执行链路）
1. 控制端读取 `users/*.yml`，根据 `groups/hosts/deny_hosts` + `acl_matrix` 在每台节点计算授权用户集合（`reality_instances`）。
2. 根据节点 `reality_mode` 进入 `reality_single` 或 `reality_multi` 角色部署 Xray。
3. 在控制端生成 `/opt/reality/users/*_<host>.json` 节点订阅缓存文件。
4. 首台执行主机在 `post_tasks` 调用 `generate_subs_gist.py`，把订阅聚合后更新 Gist。
5. 监控角色按 `monitor_enabled` 统一部署：
- `monitor.server_host` 节点部署 FastAPI 服务（`reality-monitor` systemd）。
- 全部节点部署 `traffic_agent.py` + cron 每分钟上报流量与用户 IP。

## 目录结构
- `deploy.yml`：主部署入口（含 ACL 过滤 + role 调度 + Gist 更新）。
- `reset.yml`：清理容器/数据/本地订阅缓存并可选回写 Gist。
- `decommission.yml`：节点/VPS 退出服务，清理运行态、订阅，并可选清源码引用。
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
- `docs/operations.md`：**运维操作手册（所有命令用法）。**
- `JPNTT_SOCKS5_EGRESS.md`：jpntt 作为 jp10 SOCKS5 出口 IP 的部署与验证资料。
- `SOCKS5_EGRESS_IMPLEMENTATION_NOTES.md`：SOCKS5 出口模块实施、验证、故障和结论记录。

## 运行依赖
- 控制端：`ansible`、`python3`。
- 若存在 `reality_mode: multi` 节点，控制端还需要 `docker compose`（用于本地 `compose config` 校验）。
- Ansible collections：`community.general`、`community.docker`。
- 目标机：Debian/Ubuntu、Docker Engine 可用、支持 sudo/become。
- 启用监控的节点需要 Python 3.10+（依赖由仓库根目录 `requirements.txt` 固定）。
- 可选：`ansible-vault`（推荐，保护 token）。

> 依赖安装、SSH/Vault 初始化、连通性检查等命令见 operations.md §1。

## 配置入口
### 1) 全局配置 `group_vars/all/main.yml`
关键字段：
- `xray_image`：节点镜像（当前默认 `latest`）。
- `domain_suffix` + `server_hash_suffix`：订阅域名拼接。
- `reality_server_names`、`reality_dest`：Reality 握手参数。
- `reality_root_dir` / `reality_data_dir` / `reality_logs_dir`：运行目录。
- `reality_socks5.*`：可选 socks5 落地配置（默认关闭）。
- `monitor.*`：监控地址、鉴权 token、订阅代理配置。
- `acl_matrix`：节点组授权矩阵。

### 2) 密钥配置 `group_vars/all/vault.yml`
建议至少包含：
- `vault_monitor_report_token`
- `vault_monitor_admin_bearer_token`
- `vault_monitor_stats_bearer_token`
- `vault_monitor_tunnel_secret`
- `vault_monitor_subs_token`
- `vault_monitor_gist_user`
- `vault_monitor_gist_id`
- `vault_github_token`
- `vault_dcc_socks5_address` / `vault_dcc_socks5_port` / `vault_dcc_socks5_username` / `vault_dcc_socks5_password`（可选，仅 dcc socks5 落地）

> 加密 / 编辑 vault 的命令见 operations.md §1。

### 3) 主机覆盖 `host_vars/<host>.yml`
常用字段：
- `reality_mode: single|multi`
- `monitor_enabled: true|false`
- `reality_socks5.*`：节点级 socks5 落地。仅 `target_users` 命中的用户走 socks5 出口，其余继续 `direct`，行为不变。

> dcc 启用 socks5 落地的完整流程与验证命令见 operations.md §6。

### 4) 节点清单 `inventory.ini`
- `reality_nodes`：所有部署目标。
- `free/basic/normal/premium/...`：ACL 用分组。
- `spt` 默认配置为 `ansible_connection=local`，用于本机监控服务端场景。

## ACL 规则（用户可见性）
用户文件支持三个 ACL 字段：
- `groups`: 节点组标签列表（按档位**加**节点）。
- `hosts`: 额外允许的具体主机名列表（在 groups 之上**追加**节点）。
- `deny_hosts`: 黑名单主机列表（从命中结果里**减**掉节点）。

匹配逻辑：
- 命中 `groups` 与节点放行标签交集，或命中 `hosts` 任一主机，即下发该用户到当前节点（二者取并集）。
- `deny_hosts` 优先级最高：命中当前节点则剔除，覆盖 `groups` 命中、`hosts` 钉选、以及节点的 `reality_node_users` 独占白名单。
- 历史兼容：缺失 `groups` 字段时，部署逻辑按 `['all']` 处理（即不过滤、落到所有节点）。
- `generate_user.py add` 默认写入 `groups=["free"]`、`hosts=[]`。

`acl_matrix` 档位放行（高档位节点只放高档位用户，`premium` 用户通吃所有档位节点）：

| 节点档位 | 放行的用户标签 |
|---|---|
| `free` | free, cm, basic, normal, premium |
| `basic` | basic, normal, premium |
| `normal` | normal, premium |
| `premium` | premium |

> 临时封禁/解封某用户某节点的操作步骤见 operations.md §4。

## Playbook 与标签
### `deploy.yml` 标签
| tag | 含义 |
|---|---|
| `always` | 预加载用户配置 + ACL 计算 |
| `users` | 用户配置、容器编排、订阅缓存生成（含 `local_file`、`gist`） |
| `system` | sysctl / 包安装 / 基础环境 |
| `docker` | 镜像与容器相关任务 |
| `update_image` | 强制拉取最新镜像 |
| `cleanup` | 清理旧模式残留 |
| `monitor` | 监控服务 / agent |
| `gist` | Gist 推送 |

`--tags users` 是最快路径，但首次初始化建议至少跑一次完整部署，确保依赖齐全。

### `reset.yml`
- 清理 `reality_core` 和全部 `reality_*` 容器、数据/日志目录、compose 文件、本地 `/opt/reality/users/*_<host>.json`。
- 可调变量：`reset_target_hosts`、`reset_prune_hosts`、`reset_subs_only`、`reset_confirm`、`reset_require_confirm`。
- 默认有防误操作确认（需输入 `YES`）；可选触发 Gist 更新（依赖 vault token）。

### `decommission.yml`
- 专门用于节点/VPS 退出服务；`reset.yml` 表示有效节点重置/清空。
- 默认清理运行态和订阅，不改源码配置；远端不可达或已移除时仅清控制端订阅并更新 Gist。
- 可选清源码引用：`dc_prune=true`（改 `inventory.ini`、`users/*.yml` 的 hosts、`host_vars`）。
- `dc_archive=true`（默认）归档 host_vars 到 `host_vars/archived/`；`dc_rm_vars=true` 直接删除。
- `dc_prune=true` 只自动改 JSON 用户文件或简单 flow-list hosts 行；复杂 YAML 用户文件会报错并要求手动处理。

### `audit.yml`
- 从各节点 `{{ reality_logs_dir }}/reality_core/access.log` 抽取用户与源 IP，在本机汇总输出去重后的用户-IP 统计。

> 以上 playbook 的调用命令与参数示例见 operations.md §5–§9。

## 当前策略说明
- 镜像策略保持 `latest`：完整部署会执行镜像拉取；也可以单独执行 `--tags update_image` 强制刷新镜像。
- `taoziyoyo2566/xray_docker` 自动同步 Xray 官方最新 stable 及其后全部 prerelease，
  支持 Intel/AMD 64 位 x86（`linux/amd64`）和 64 位 ARM（`linux/arm64`）；镜像标签、
  拉取方式和不可变发布规则见 [`docker-build/README.md`](docker-build/README.md)。
- SSH Host Key 校验已开启（`host_key_checking=True`），并通过 `StrictHostKeyChecking=accept-new` 保留首次接入体验。
- 监控 Python 依赖固定在仓库根目录 `requirements.txt`，部署时会下发并按该文件安装。

## 监控系统
### 部署行为
- 服务端仅在 `monitor.server_host` 部署：`/opt/reality/monitor/server.py` + `reality-monitor.service`。
- 客户端在所有 `monitor_enabled=true` 节点部署：`/usr/local/bin/traffic_agent.py` + 每分钟 cron。
- 数据库：`{{ monitor_root_dir }}/data/traffic_monitor.db`。

### 鉴权模型
- `/report`：仅检查 `token` header（agent 上报入口）。
- `/stats/*`、`/docs`、`/openapi.json`：D1-B 鉴权 ——（经 CF：CF 注入 `X-Monitor-Tunnel-Secret` ∧ `CF-Connecting-IP`∈`ip_allowlist`）或 `Authorization: Bearer`；本机/绕 CF 一律 401。
- `/stats/ip_report`：仅 `token: REPORT_TOKEN`（同 `/report`，已去 auth_guard）。`/stats/cleanup`：仅 admin Bearer。`/healthz`：无鉴权。

### 常用接口
- `GET /stats/ui`
- `GET /stats/daily?hours=24&detail=true`
- `GET /stats/timeseries?hours=24&interval=3600`
- `GET /stats/health?hours=24&stale_minutes=10`
- `GET /stats/export?hours=24&detail=true&format=csv`
- `GET /stats/ip_matrix?hours=72`
- `GET /subs/logs?limit=200`

> 监控服务端部署、日志查看、agent 手动上报等命令见 operations.md §10、§12。

## 订阅分发（Gist）
- 数据源：控制端本地 `/opt/reality/users/*.json`。
- 脚本：`generate_subs_gist.py`，部署时自动注入环境变量并执行（无需手工 export）。
- 手工执行需提供 `GITHUB_TOKEN`、`GIST_ID`、`GITHUB_USER`、`SUBS_BASE_URL`、`SUBS_TOKEN`，命令见 operations.md §11。

## generate_user.py 说明
功能：
- `add`：创建用户文件并生成 UUID、端口、short_id、X25519 密钥。
- `update`：仅更新 ACL（`groups/hosts/deny_hosts`）。
- `delete`：删除对应用户文件（按 `.yml/.yaml/.json` 顺序查找）。
- `list`：列出用户和端口；`--wide` 额外显示 ACL（`groups/hosts/deny_hosts`）视图。

文件结构示例：
```json
{
  "name": "alice",
  "uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "port": 24567,
  "short_id": "a1b2c3d4e5f60789",
  "private_key": "...",
  "public_key": "...",
  "groups": ["free"],
  "hosts": [],
  "deny_hosts": []
}
```

> 各子命令的命令用法见 operations.md §2。

## 兼容与遗留
- `monitor.yml`、`monitor_server.py` 旧方案文件**已删除**（曾含硬编码 token，部署时轮换）。
- `group_vars/all.yml` 已移除，请统一使用 `group_vars/all/main.yml`。
