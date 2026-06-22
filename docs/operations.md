# Reality Ops 操作手册（命令用法）

面向日常运维的**使用说明**：只讲"怎么用、各命令怎么敲"。
字段含义 / 配置项原理 / 目录结构见 `README.md`；本文力求把**所有操作命令**收全，运维时只看本文即可。

---

## 0. 调用约定

仓库根目录自带 `./ansible-playbook` 包装脚本，**自动注入 `-i inventory.ini`，并在存在 `~/.vault_pass` 时自动追加 `--vault-password-file ~/.vault_pass`**，把动作名映射到对应 playbook：

| 简写 | 实际 playbook | 位置参数行为 |
|---|---|---|
| `./ansible-playbook deploy <目标>` | `deploy.yml` | `--limit <目标>` |
| `./ansible-playbook audit <目标>` | `audit.yml` | `--limit <目标>` |
| `./ansible-playbook reset <目标>` | `reset.yml` | `-e reset_target_hosts=<目标>`；在 inventory 内自动 `--limit <目标>`，不在则 `--limit spt -e reset_subs_only=true` |
| `./ansible-playbook dc <目标>`（= `decommission`） | `decommission.yml` | `-e dc_target=<目标>`；在 inventory 内自动 `--limit <目标>`，不在则 `--limit spt`（仅本地清理） |

- `<目标>` 可以是单个节点名（`jp10`）或 inventory 组名（`premium`/`free`）。
- `deploy`/`audit` 不带位置参数时作用于全体 `reality_nodes`。
- **`dc`/`decommission` 必须带目标**，否则 wrapper 层直接报错（不会连所有节点）。
- 原生参数透传：`./ansible-playbook -i inventory.ini reset.yml --syntax-check`。
- 不带 `./` 直接敲 `ansible-playbook ...` 需要 PATH 注入（`source scripts/ansible_shortcuts.sh`）。
- 本文后续用 `./ansible-playbook ...` 简写；等价原生写法形如：
  `ansible-playbook -i inventory.ini deploy.yml --limit jp10 --tags users --vault-password-file ~/.vault_pass`。

> **PATH 提示**：本机 ansible 装在 `monitor_venv`，裸 `ansible` / `ansible-playbook` 默认不在 PATH。
> 用本文里的原生 `ansible[-playbook] ...` 命令前，先 `source monitor_venv/bin/activate`（提示符出现 `(monitor_venv)`），
> 或直接用 `monitor_venv/bin/ansible[-playbook] ...`。`./ansible-playbook` 包装器会自动定位真实二进制，无需激活。

---

## 1. 环境准备（首次/换机）

```bash
# 1) 安装 Ansible collections
ansible-galaxy collection install community.general community.docker

# 2) 载入 SSH key
eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519

# 3) 准备 vault 口令文件（仅首次；妥善保管，勿入库）
openssl rand -base64 32 > .vault_pass
chmod 600 .vault_pass

# 4) 连通性检查
ansible -i inventory.ini all -m ping --vault-password-file ~/.vault_pass
ansible -i inventory.ini test_nodes -m ping --vault-password-file ~/.vault_pass   # 仅测试组
```

依赖前提：控制端有 `ansible`、`python3`；若有 `reality_mode: multi` 节点还需 `docker compose`（本地 `compose config` 校验）；目标机为 Debian/Ubuntu、Docker 可用、支持 sudo。

### 密钥文件 `group_vars/all/vault.yml`
```bash
ansible-vault encrypt group_vars/all/vault.yml                                    # 首次加密
EDITOR=vim ansible-vault edit group_vars/all/vault.yml --vault-password-file ~/.vault_pass   # 编辑（勿手改密文）
```

---

## 2. 用户管理：`generate_user.py`

用户配置是 `users/<name>.yml`（JSON 写进 `.yml`）。改完用户后**必须重新部署**相关节点才生效（见 §5）。

### 2.1 新增 `add`
```bash
python3 generate_user.py add alice                              # 自动选端口、默认 groups=["free"]
python3 generate_user.py add bob --port 26000                   # 指定端口
python3 generate_user.py add carol --groups premium --hosts ams,dcc
python3 generate_user.py add dave --groups basic,netflix --hosts ams --deny-hosts jp10
python3 generate_user.py add alice --force                      # 覆盖同名文件（会重建 uuid/密钥）
python3 generate_user.py --docker add eve                       # 容器内执行，免装 cryptography
```
- `--groups`：档位/特性标签，逗号分隔，不传默认 `free`。
- `--hosts`：额外钉到的具体节点，逗号分隔，默认空。
- `--deny-hosts`：黑名单节点，逗号分隔，默认空。
- 端口自动从 `--min-port`(默认 20000)~`--max-port`(默认 60000) 选未占用的。

### 2.2 更新 ACL `update`
只改 ACL 字段，不动 uuid/端口/密钥。至少传 `--groups`/`--hosts`/`--deny-hosts` 之一。**三者均为整体覆盖，非追加。**
```bash
python3 generate_user.py update carol --groups premium          # 改档位
python3 generate_user.py update carol --hosts ams,dcc           # 改 hosts
python3 generate_user.py update carol --hosts ""                # 清空 hosts
python3 generate_user.py update carol --deny-hosts jp10,ams     # 设黑名单（覆盖式）
python3 generate_user.py update carol --deny-hosts ""           # 清空黑名单（解封）
```

### 2.3 删除 `delete`
```bash
python3 generate_user.py delete bob        # 删除 bob.yml/.yaml/.json
```

### 2.4 查看 `list`
```bash
python3 generate_user.py list              # 用户名 + 端口 + 路径
python3 generate_user.py list --wide       # 额外显示 ACL：groups / hosts / deny_hosts
python3 generate_user.py list --include-json   # 同时展示 json 文件
python3 generate_user.py list --details        # 展开 json 数组逐条显示
```

> ⚠️ `list` 用 `json.load` 解析，用户文件必须是合法 JSON：`hosts` 要写 `["spt"]` 而非裸词 `[spt]`，否则该用户被静默跳过、不显示。

---

## 3. ACL 速查：谁能用哪些节点

| 用户字段 | 作用 | 方向 |
|---|---|---|
| `groups` | 命中节点档位放行矩阵（`acl_matrix`）即下发 | **加** |
| `hosts` | 额外钉到指定节点 | **加** |
| `deny_hosts` | 命中则剔除，优先级最高（覆盖 groups/hosts/节点独占白名单） | **减** |

最终某节点是否下发该用户：`(命中 groups 或 命中 hosts) 且 不在 deny_hosts`。

档位放行（高档位节点只放高档位用户；`premium` 用户通吃所有档位节点）：

| 节点档位（inventory 组） | 放行的用户标签 |
|---|---|
| `free` | free, cm, basic, normal, premium |
| `basic` | basic, normal, premium |
| `normal` | normal, premium |
| `premium` | premium |

> 缺失 `groups` 字段的历史用户按 `['all']` 处理（不过滤、落到所有节点）。新用户请显式写 `groups`。

---

## 4. 临时封禁 / 解封某用户某节点

核心场景：某用户在某节点流量超量，临时切断。

```bash
# ① 封禁 zhao 不能用 jp10
python3 generate_user.py update zhao --deny-hosts jp10
./ansible-playbook deploy jp10 --tags users

# ② 解封
python3 generate_user.py update zhao --deny-hosts ""
./ansible-playbook deploy jp10 --tags users
```
- **改完必须对该节点重新部署才生效**；只改文件不部署，旧容器/inbound 照跑。
- 生效后：single 模式重启容器移除 inbound、清理订阅；multi 模式删除该用户容器、清理订阅（磁盘留惰性残目录，不影响隔离）。
- 多节点黑名单：`--deny-hosts jp10,ams`（覆盖式，要把已有的一起带上）。

### 4.1 验证封禁是否真的生效
部署 `PLAY RECAP` 显示 `failed=0` 只代表跑完，不代表生效，务必实测：

```bash
# ① 订阅侧（两种模式都适用）：被封用户该节点的订阅文件应已消失
ls /opt/reality/users/<用户>_<节点>.json        # 提示 No such file = 已移除

# ② 容器侧（仅 multi 模式节点，每用户一个容器）
ssh <节点> 'docker ps | grep reality_<用户> || echo NONE'   # NONE = 容器已删
#   single 模式节点没有 per-user 容器（用户是 reality_core 的 inbound），改查订阅文件①即可

# ③ 节点上实际下发的用户
ssh <节点> 'docker ps --format "{{.Names}}"'
```
> 用 `ansible ... -m shell` 跑这类命令时：(1) 必须带 `--vault-password-file ~/.vault_pass`；
> (2) 命令里别用 `{{ }}`（会被 ansible 当 Jinja 模板报错），要 docker 格式串直接用 `ssh` 更省事。

---

## 5. 部署：`deploy.yml`

```bash
# 完整部署全体节点（首次/装依赖必跑）
./ansible-playbook deploy

# 首次预演不改动（看 diff）
./ansible-playbook -i inventory.ini deploy.yml --check --diff --vault-password-file ~/.vault_pass

# 只更新用户配置（最快路径，日常改用户后用这个）
./ansible-playbook deploy --tags users

# 只部署单节点 / 单档位（灰度）
./ansible-playbook deploy jp10 --tags users
./ansible-playbook deploy premium --tags users

# 多节点显式刷新：目标必须写成一个 inventory pattern，不要把多个主机写成多个位置参数
./ansible-playbook deploy 'dzire:de:ams:dcc:sg:jp05:hk-hn:hk-hn2:jp10:jpntt:spt' --tags users --check --diff -K
./ansible-playbook deploy 'dzire:de:ams:dcc:sg:jp05:hk-hn:hk-hn2:jp10:jpntt:spt' --tags users -K

# 只刷订阅（不碰容器，最快）：清理该节点旧订阅缓存 + 按当前用户重生成 + 推 Gist
# 适用：容器已正确、只是订阅缓存陈旧（解封后、被删/被封用户残留在订阅里）
./ansible-playbook deploy dcc --tags local_file,gist -K

# 跳过监控与 Gist（只动节点）
./ansible-playbook deploy dcc --tags users --skip-tags monitor,gist

# 强制刷新 Xray 镜像
./ansible-playbook deploy --tags update_image
```

### `deploy.yml` 标签一览
| tag | 含义 |
|---|---|
| `always` | 预加载用户配置 + ACL 计算（无条件执行） |
| `users` | 用户配置、容器编排、订阅缓存生成（**含 `local_file` 与 `gist`**） |
| `system` | sysctl / 装包 / 基础环境 |
| `docker` | 镜像与容器相关 |
| `update_image` | 强制拉取最新镜像 |
| `cleanup` | 清理旧模式残留 |
| `monitor` | 监控服务 / agent |
| `gist` | Gist 推送 |

- `--tags users` 是最快路径，但**首次初始化至少完整跑一次**确保依赖齐全。
- 镜像策略为 `latest`：完整部署会拉镜像；也可单独 `--tags update_image` 强刷。

### 5.1 用户 / 订阅一致性收尾

新增用户、修改用户档位/hosts/deny_hosts、节点改名或发现订阅里有旧节点时，按这个顺序收口：

```bash
# 1) 先确认本地订阅缓存里是否有旧节点名或目标用户残留
sudo find /opt/reality/users -maxdepth 1 -type f \( \
  -name '<user>_*.json' -o \
  -name '*_netcup.json' -o \
  -name '*_lej.json' -o \
  -name '*_legend.json' \
\) -print

# 2) 删除明确应重建的缓存；不要删 users/*.yml 源配置
sudo find /opt/reality/users -maxdepth 1 -type f \( \
  -name '<user>_*.json' -o \
  -name '*_netcup.json' -o \
  -name '*_lej.json' -o \
  -name '*_legend.json' \
\) -delete

# 3) 重新部署受影响节点；会更新 Xray 配置、本地订阅 JSON，并推送 Gist
./ansible-playbook deploy '<node1>:<node2>:<node3>' --tags users --check --diff -K
./ansible-playbook deploy '<node1>:<node2>:<node3>' --tags users -K

# 4) 验证目标用户只出现在预期节点
find /opt/reality/users -maxdepth 1 -type f -name '<user>_*.json' -printf '%f\n' | sort

# 5) 验证旧节点名缓存已清空
find /opt/reality/users -maxdepth 1 -type f \( -name '*_netcup.json' -o -name '*_lej.json' -o -name '*_legend.json' \) -print
```

注意：
- Ansible wrapper 的多主机目标使用 inventory pattern，例如 `'sg:ams:jp05'`；`./ansible-playbook deploy sg ams jp05 ...` 会被解析为多个 playbook 参数而报错。
- `users/*.yml` 是源配置，`/opt/reality/users/*.json` 是订阅缓存。Gist 只聚合缓存文件，所以旧缓存会继续污染订阅，必须显式清掉或用对应节点的 `--tags users/local_file,gist` 重建。
- 新用户文件必须纳入 git；未跟踪的 `users/*.yml` 也会被本机 Ansible 读取，但其他控制端/远端仓库不会有这份配置。

---

## 6. 节点级 SOCKS5 落地（以 dcc 为例）

仅让指定用户在某节点走 SOCKS5 出口，不影响其他用户。

```bash
# 1) 在 host_vars/dcc.yml 启用，仅把目标用户加进 target_users：
#    reality_socks5:
#      enabled: true
#      address: "{{ vault_dcc_socks5_address }}"
#      port:    "{{ vault_dcc_socks5_port }}"
#      username:"{{ vault_dcc_socks5_username }}"
#      password:"{{ vault_dcc_socks5_password }}"
#      target_users: ["alice_socks"]

# 2) 建一个仅 dcc 可见的 socks5 用户
python3 generate_user.py add lin_isp --groups socks5_only --hosts dcc

# 3) 部署 dcc（仅用户/容器，跳过监控与 Gist）
./ansible-playbook deploy dcc --tags users --skip-tags monitor,gist
```

验证：
```bash
# 控制端本地构建产物
grep -n '"protocol": "socks"' /tmp/reality_build/dcc/data/lin_isp/config.json
# 目标机落地配置
ssh dcc 'grep -n "\"protocol\": \"socks\"" /opt/reality/data/lin_isp/config.json'
# 仅重载目标用户容器，避免全量抖动
ssh dcc 'docker restart reality_lin_isp'
# 出口验证：连上该节点后看出口 IP
curl -s https://api.ipify.org
# 服务端辅助：观察到 socks5 服务器的连接
ssh dcc 'sudo tcpdump -ni any host <socks5_ip> and port <socks5_port>'
```

---

## 7. 重置：`reset.yml`

清理运行态/订阅，不改源码配置。**带确认**，非交互需 `reset_confirm=YES`。
清理范围：`reality_*` 容器、数据/日志目录、compose 文件、本地 `/opt/reality/users/*_<host>.json`。

```bash
# 重置单节点（在 reality_nodes 内会自动 --limit）
./ansible-playbook reset dcc -e "reset_confirm=YES"

# 重置某组
ansible-playbook -i inventory.ini reset.yml --limit free \
  -e "reset_confirm=YES" --vault-password-file ~/.vault_pass

# 重置多个节点
ansible-playbook -i inventory.ini reset.yml \
  -e "reset_target_hosts=dcc,sky reset_confirm=YES" --vault-password-file ~/.vault_pass

# 仅清订阅 + 刷新 Gist（不动远端容器/数据）
ansible-playbook -i inventory.ini reset.yml --limit spt --tags local_file,gist \
  -e "reset_subs_only=true reset_target_hosts=sky reset_confirm=YES" --vault-password-file ~/.vault_pass

# 显式删除已下线节点的订阅缓存
ansible-playbook -i inventory.ini reset.yml \
  -e "reset_prune_hosts=sky,kagoya reset_confirm=YES" --vault-password-file ~/.vault_pass
```
可调变量：`reset_target_hosts`（本次处理的节点）、`reset_prune_hosts`（强删订阅缓存）、`reset_subs_only=true`（仅订阅+Gist）、`reset_confirm=YES`、`reset_require_confirm=false`（跳过交互确认）。未确认 `YES` 会安全取消。

---

## 8. 节点下线：`decommission.yml` / `dc`

专用于节点/VPS 退出服务（有效节点重置用 §7 `reset`）。**必须指定目标**，**带确认**（非交互 `dc_confirm=YES`）。

```bash
# 基础下线：清远端运行态/日志/数据 + 本地订阅 + 更新 Gist，host_vars 默认归档
./ansible-playbook dc saberu -e "dc_confirm=YES" -K

# 同时清源码引用：删 inventory 行、从 users/*.yml 的 hosts 移除该节点、处理 host_vars
./ansible-playbook dc saberu -e "dc_confirm=YES dc_prune=true" -K

# 连 host_vars/<host>.yml 一起删除（默认是归档到 host_vars/archived/）
./ansible-playbook dc saberu -e "dc_confirm=YES dc_prune=true dc_rm_vars=true" -K
```
- 节点不可达 / 已从 inventory 删除：只清控制端订阅 + 更新 Gist，并提示远端未清理。
- `dc_prune=true` 只自动改 JSON 用户文件 / 简单 flow-list hosts 行；复杂 YAML 用户文件会报错要求手动处理。
- `dc_archive=true`（默认）归档 host_vars；`dc_rm_vars=true` 直接删除。
- `-K`：远端 sudo 清理、或控制端首次修复 `/opt/reality/users` 归属时需要。

---

## 9. 流量/访问审计：`audit.yml`

从各节点 `access.log` 抽取用户 + 源 IP，本机汇总去重输出。
```bash
./ansible-playbook audit              # 全体节点
./ansible-playbook audit dcc          # 单节点
```

---

## 10. 监控系统

### 部署
监控**服务端 + agent 都由 `deploy.yml` 的 `monitor` role 部署**（tag `monitor`），按节点 `monitor_enabled` 开关。
```bash
./ansible-playbook deploy spt --tags monitor_server -K      # 只更服务端（spt = monitor.server_host）
./ansible-playbook deploy <node> --tags monitor_agent -K     # 只更单节点 agent
./ansible-playbook deploy <node> --tags monitor_config -K    # agent 配置/token/cron 批次更新
```
- 服务端仅在 `monitor.server_host`（默认 `spt`）部署：`/opt/reality/monitor/server.py` + `reality-monitor.service`。
- agent 在所有 `monitor_enabled=true` 节点：`/usr/local/bin/traffic_agent.py` + 每分钟 cron；数据库 `{{ monitor_root_dir }}/data/traffic_monitor.db`（已移出共享的 reality_data_dir，避免与 xray 配置目录属主冲突）。
- ⚠️ 旧方案文件 `monitor.yml`/`monitor_server.py` **已删除**（曾含硬编码 token，部署时轮换）。

### 常用接口（D1-B：CF 注入 secret 头 + 白名单，或 Bearer）
```
GET /stats/ui
GET /stats/daily?hours=24&detail=true
GET /stats/timeseries?hours=24&interval=3600
GET /stats/health?hours=24&stale_minutes=10
GET /stats/export?hours=24&detail=true&format=csv
GET /stats/ip_matrix?hours=72
GET /subs/logs?limit=200
```
鉴权(D1-B)：`/report`、`/stats/ip_report` 仅校验 `token` header；`/stats/*`、`/docs` 需 (CF **Request Header** Transform Rule 注入 `X-Monitor-Tunnel-Secret` ∧ `CF-Connecting-IP`∈白名单) 或 `Bearer`，本机/绕 CF 一律 401；`/stats/cleanup` 仅接受 admin Bearer；`/healthz` 无鉴权。

---

## 11. 订阅分发（Gist）

数据源是控制端本地 `/opt/reality/users/*.json`，部署时由 `post_tasks` 自动调用 `generate_subs_gist.py`（自动注入环境变量，无需手工 export）。

手工执行（一般不需要）：
```bash
GITHUB_TOKEN=... GIST_ID=... GITHUB_USER=... \
SUBS_BASE_URL=https://subs.example.com SUBS_TOKEN=... \
python3 generate_subs_gist.py
```

---

## 12. 日常运维命令（节点上）

```bash
# 监控服务端日志（monitor.server_host）
sudo journalctl -u reality-monitor -f

# 手动触发一次 agent 上报
sudo /opt/reality/monitor/.venv/bin/python3 /usr/local/bin/traffic_agent.py

# 查看单实例访问日志
tail -n 300 /opt/reality/logs/reality_core/access.log
```

---

## 13. 故障排查

| 现象 | 先查 / 处理 |
|---|---|
| 节点上没有某用户容器 | ACL：用户 `groups/hosts/deny_hosts`、inventory 分组、`acl_matrix` |
| 改了用户但没生效 | 是否重新部署了该节点（`deploy <节点> --tags users`） |
| 封禁没生效 | `deny_hosts` 拼对节点名 + 是否重新部署该节点（见 §4.1 实测） |
| 部署 `failed=0` 但订阅没变 | 查 `/opt/reality/users/<用户>_<节点>.json` 的 mtime 是不是今天；`failed_when:false` 会把任务报错吞成 `ok`，RECAP 不可信。修复后只刷订阅：`./ansible-playbook deploy <节点> --tags local_file,gist -K`（见 §5） |
| `--tags users` 报 `rsync` 缺失 | 先跑完整部署，或在目标机装 `rsync` |
| `--tags users` 不想触发监控/Gist | 追加 `--skip-tags monitor,gist` |
| 本地临时目录不可写（`~/.ansible/tmp`） | 见下方环境变量改用 `/tmp` |
| 用户在 `list` 里看不到 | 用户文件是否合法 JSON（`["spt"]` 而非裸词 `[spt]`） |
| 订阅未更新 | `vault_github_token` 与 Gist 参数是否配置；是否被 `--skip-tags gist` 跳过 |
| reset 找不到下线节点（如 `sky`） | 别用 `--limit sky`，改 `--limit spt -e "reset_subs_only=true reset_target_hosts=sky reset_confirm=YES"` |
| 监控页 401 | CF 是否配成 **Request Header** Transform Rule（不是 Response Header）；secret 与 vault `tunnel_secret` 是否一致；运维 IP 是否在 `ip_allowlist`；临时用 Bearer |

临时目录不可写时：
```bash
ANSIBLE_LOCAL_TEMP=/tmp/.ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/.ansible-remote \
ANSIBLE_SSH_CONTROL_PATH_DIR=/tmp/.ansible/cp \
ansible-playbook -i inventory.ini deploy.yml --syntax-check
```
