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

数据源是控制端本地 `/opt/reality/users/*.json`，部署时由 `post_tasks` 自动调用 `generate_subs_gist.py`。凭据由 Ansible Vault 注入，日常操作不要手工 `export GITHUB_TOKEN=...`，也不要把 token 写进命令行、`.env`、代码、agent 权限配置或运维记录。

### 11.1 凭据边界

| Vault 变量 | 用途 | 是否为 GitHub 凭据 |
|---|---|---|
| `vault_github_token` | 调用 GitHub API 更新 Gist | 是 |
| `vault_monitor_gist_user` | Gist 所有者账号 | 否（标识符） |
| `vault_monitor_gist_id` | 目标 Gist ID | 否（标识符） |
| `vault_monitor_subs_token` | 订阅代理 URL 的访问控制 | 否（但仍是必须保密的业务 token） |

GitHub 支持 classic PAT（常见前缀 `ghp_`）和 fine-grained PAT（常见前缀 `github_pat_`）。本项目只需以 Gist 所有者身份调用 `PATCH /gists/{gist_id}`，优先使用权限更小、可设过期时间的 fine-grained PAT。

### 11.2 创建 fine-grained PAT（GitHub 网页）

1. 使用 `vault_monitor_gist_user` 对应的账号登录 GitHub。
2. 打开 [Settings → Developer settings → Personal access tokens → Fine-grained tokens](https://github.com/settings/personal-access-tokens/new)。
3. `Token name` 填写可识别用途的名称，例如 `reality-ops-gist`。
4. `Expiration` 设置明确的有效期，并在过期前轮换。
5. `Resource owner` 必须选择 `vault_monitor_gist_user` 对应的账号。
6. `Repository access` 保留公共仓库只读的默认范围；Gist 是账号级资源，不需要 `All repositories`。
7. `Account permissions` 只设置 `Gists: Read and write`，不授予 `Contents`、`Administration`、`Workflows` 等无关权限。
8. 点击 `Generate token`，只将新 token 写入 Vault，不要粘贴到 issue、PR、Gist、聊天或 shell 历史。

GitHub 当前的 `Update a gist` 接口对 fine-grained PAT 的最小要求是账号级 `Gists` 写权限，见 [GitHub REST API 文档](https://docs.github.com/en/rest/gists/gists#update-a-gist)。

### 11.3 写入或轮换 Vault 中的 GitHub PAT

使用仓库相对的 Python module 调用，避免依赖可能包含旧绝对路径 shebang 的 venv console script：

```bash
ANSIBLE_LOCAL_TEMP=/tmp/reality-ops-ansible-local \
EDITOR=vim \
monitor_venv/bin/python -m ansible vault edit \
  group_vars/all/vault.yml \
  --vault-password-file ~/.vault_pass
```

只替换 `vault_github_token`，不要同时改动 Gist ID、所有者或其他监控凭据：

```yaml
vault_github_token: "github_pat_<redacted>"
```

`~/.vault_pass` 是控制端用户的仓库外密码文件，不应改成仓库内硬编码绝对路径或纳入 Git。保存后确认 Vault 仍为密文：

```bash
head -n 1 group_vars/all/vault.yml
# 预期：$ANSIBLE_VAULT;1.1;AES256
```

若看到 YAML 明文，立即停止，不得 commit 或 push。

### 11.4 只读验证 PAT

先验证新 token 能否通过 GitHub 认证；下列命令只在进程管道中解密，不打印 token：

```bash
ANSIBLE_LOCAL_TEMP=/tmp/reality-ops-ansible-local \
monitor_venv/bin/python -m ansible vault view \
  group_vars/all/vault.yml \
  --vault-password-file ~/.vault_pass |
monitor_venv/bin/python -c '
import sys
import yaml
import requests

vault = yaml.safe_load(sys.stdin)
response = requests.get(
    "https://api.github.com/user",
    headers={
        "Authorization": "Bearer " + vault["vault_github_token"],
        "Accept": "application/vnd.github+json",
    },
    timeout=20,
)
print("GitHub authentication: HTTP", response.status_code)
'
```

- `HTTP 200`：认证有效，可继续发布。
- `HTTP 401`：token 无效、已过期/撤销或 Vault 中的值未正确保存。
- `HTTP 403`：查权限/账号策略与 GitHub 短期认证限制，不要快速连续重试。

`HTTP 200` 只证明 token 本身有效；Gist 写权限由创建页面的 `Gists: Read and write` 和后续实际发布共同验证。

### 11.5 受控发布与验收

Gist 任务会聚合控制端 `/opt/reality/users/*.json` 的**全部**缓存，不是只发布本次部署的节点。发布前先确认文件数量、时间与应有用户/节点符合预期；旧缓存会继续污染订阅。

发布动作边界：

- 目标：`vault_monitor_gist_id` 对应的整个 Gist。
- 效果：使用全部本地缓存更新 Gist 文件，产生新 Gist revision。
- 排除：使用本地 `spt` 限定时不部署其他节点或容器。
- 风险/恢复：本地缓存错误会发布错误订阅；可从 Gist revision history 核对上一版，修正缓存后重新发布。

确认范围后执行一次：

```bash
./ansible-playbook deploy spt --tags gist
```

`spt` 是本地控制端/默认 `monitor.server_host`，此限定避免为单独更新 Gist 再连接其他节点。成功必须看到：

```text
✅ Gist 更新成功
```

不能只根据 `PLAY RECAP failed=0` 判定成功：当前 `generate_subs_gist.py` 会捕获 GitHub 请求异常并返回 `False`，但未让进程以非零状态退出。同时，当前脚本会在终端输出完整订阅 URL（含 `vault_monitor_subs_token`），只能在私密终端运行，不得粘贴原始输出或上传日志。

可读取 Gist 元数据确认更新时间（`<gist_id>` 替换为配置值）：

```bash
gh api gists/<gist_id> --jq '{updated_at, file_count: (.files | keys | length)}'
```

### 11.6 泄露、失效与轮换

GitHub secret-scanning 中的 `publicly leaked` 表示凭据曾出现在可公开扫描的仓库、Gist、issue、PR 或其他 GitHub 内容中；`inactive` 只表示该凭据已不可用。历史告警中的 secret 不一定与当前 `vault_github_token` 是同一个，必须根据 alert location、token 所有者和当前认证结果分别处理。

发现 GitHub PAT 泄露或 `401` 时：

1. 停止使用旧 token，不再输出或复制其值。
2. 在 GitHub token 设置中撤销旧 token，检查 Security log 中是否有异常使用。
3. 按 §11.2 使用最小权限创建新 token，按 §11.3 只替换 `vault_github_token`。
4. 按 §11.4 只读验证，再按 §11.5 发布并验收。
5. 删除工作区、历史记录、issue/PR/Gist、终端日志和 agent 本地配置中的明文副本；已撤销 token 不会因为删除文本而恢复。
6. secret-scanning alert 核对完成后按实际状态标记 `Revoked`；只在合规要求必须清除 Git 历史原文时，再单独评审 history rewrite 和 force-push 的影响。

`vault_monitor_subs_token` 泄露不会造成 GitHub API `401`，但会让未授权用户访问订阅代理。它泄露时必须单独轮换，同步更新 monitor server 和客户端订阅 URL，不得把它当作 GitHub PAT 处理。

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
| 部署 `failed=0` 但订阅没变 | 查 `/opt/reality/users/<用户>_<节点>.json` 的 mtime 是不是今天；当前 `generate_subs_gist.py` 捕获 GitHub 异常后未以非零状态退出，RECAP 不能证明 Gist 成功。查输出是否有 `Gist 更新成功`；修复后只刷订阅：`./ansible-playbook deploy <节点> --tags local_file,gist -K`（见 §5、§11） |
| `--tags users` 报 `rsync` 缺失 | 先跑完整部署，或在目标机装 `rsync` |
| `--tags users` 不想触发监控/Gist | 追加 `--skip-tags monitor,gist` |
| 本地临时目录不可写（`~/.ansible/tmp`） | 见下方环境变量改用 `/tmp` |
| 用户在 `list` 里看不到 | 用户文件是否合法 JSON（`["spt"]` 而非裸词 `[spt]`） |
| 订阅未更新 | 按 §11.4 验证 `vault_github_token` 是否返回 `HTTP 200`；检查 Gist 所有者/ID 与 `Gists: Read and write` 权限；确认未被 `--skip-tags gist` 跳过 |
| reset 找不到下线节点（如 `sky`） | 别用 `--limit sky`，改 `--limit spt -e "reset_subs_only=true reset_target_hosts=sky reset_confirm=YES"` |
| 监控页 401 | CF 是否配成 **Request Header** Transform Rule（不是 Response Header）；secret 与 vault `tunnel_secret` 是否一致；运维 IP 是否在 `ip_allowlist`；临时用 Bearer |

临时目录不可写时：
```bash
ANSIBLE_LOCAL_TEMP=/tmp/.ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/.ansible-remote \
ANSIBLE_SSH_CONTROL_PATH_DIR=/tmp/.ansible/cp \
ansible-playbook -i inventory.ini deploy.yml --syntax-check
```

---

## 14. 新系统：xray_edge、订阅服务与管理控制台

新系统与旧系统独立运行（路线图 `docs/reviews/roadmap-unified-2026-09-18.md` §2）。以下命令没有 `./ansible-playbook` 简写，都用原生写法；
每台节点、每次部署都需要单独确认。实施合同：`docs/reviews/console/plan-console-phase1-2026-09-16.md`。

```bash
cd ~/workspace/projects/reality-ops
export SSH_AUTH_SOCK=/run/user/1000/openssh_agent        # 连接节点需要已载入节点密钥的 ssh-agent
PB="./monitor_venv/bin/ansible-playbook -i inventory.ini --vault-password-file $HOME/.vault_pass -K"
```

### 14.1 首次上线顺序

```bash
$PB edge.yml                    # 覆盖 edge_nodes 中的全部节点（先一台试点，再其余同时）：更新工具镜像并向控制台登记
$PB subs.yml                    # 订阅服务只负责部署，把数据目录交给控制台写入
$PB console.yml                 # 部署控制台；首次运行导入 console_initial_shown_nodes 并发布一次（§14.7）
```

新节点加入 `edge_nodes` 前，按 [`runbooks/node-time-sync.md`](runbooks/node-time-sync.md) 检查时钟是否已同步。

前提：Cloudflare 上 `report.taoziyoyo.com` 的路由在**控制台自己的隧道**里、指向 `http://report:8201`（不要加在订阅服务的隧道上），
隧道 token 已写入 vault 的 `vault_console_tunnel_token`。新建隧道、写入 token、移动路由、轮换与删除的步骤见
[`runbooks/cloudflare-tunnels.md`](runbooks/cloudflare-tunnels.md)。

### 14.2 管理页面

在自己的电脑上执行（`<spt>` 换成登录 spt 用的 SSH 主机名），窗口保持打开，然后浏览器打开 `http://127.0.0.1:8200`：

```bash
ssh -N -L 8200:127.0.0.1:8200 <spt>
```

- 页面没有登录，只能这样经 SSH 转发访问，不要把这个端口转发给别人。
- 只接受 `group_vars/all/console.yml` 的 `console_allowed_hosts` 中的 Host（默认 `127.0.0.1:8200`、`localhost:8200`）；
  本地用了其他端口时，把 `127.0.0.1:<端口>` 加进去并重新运行 `console.yml`。

| 要做的事 | 在哪里 | 说明 |
|---|---|---|
| 新增用户 | 用户 → 新增用户 | 填用户名、档位、单独允许 / 禁止的节点、到期日、备注；UUID 自动生成。各节点约一分钟内自动加入该账号（§14.10） |
| 修改、停用、启用、删除用户 | 用户详情 | 停用与到期立即暂停订阅地址；各节点约一分钟内自动增删账号，未完成前页面标“待生效” |
| 给用户发订阅地址 | 用户 → 点用户名 → 发放订阅地址 | 页面显示地址与二维码，只发给本人；停用或到期的用户不能发放 |
| 让用户在 Telegram 里自己取地址 | 用户详情 → Telegram → 生成绑定链接 | 链接 24 小时内有效、只能用一次，发给本人；绑定后用户在 bot 里取地址、看流量、重置地址（§14.11） |
| 一批用户迁到新系统 | 用户列表勾选 → 发放并生成 Telegram 绑定链接 | 未发放的先发放；链接清单页附消息示例；进度看首页“迁移进度”（§14.12） |
| 批量发放、导出清单 | 用户列表勾选 → 发放并导出 | 已发放的沿用原地址；清单只在页面上显示和下载，不保存在服务器上 |
| 改节点的档位 | 节点详情 → 档位与用户 | 页面显示保存后会增加 / 移除哪些用户，各节点约一分钟内自动生效 |
| 地址泄露，要换 | 用户详情 → 重置地址（用户也可在 bot 里 `/reset`） | 同时更换地址与连接凭据（UUID）：旧地址立即失效，用旧地址导入的节点约一分钟内无法连接；用户重新导入新地址 |
| 停用某用户的订阅 | 用户详情 → 输入用户名 → 吊销订阅 | 订阅地址返回 404；**节点上的账号不受影响**，已导入的节点仍能用，要停止访问须停用或删除该用户 |
| 看某用户是否在用 | 用户列表 / 用户详情 | 最后拉取时间、各节点本月流量、近 31 天每日流量 |
| 看账号是否被多人共用 | 用户列表“同时在线”与“最后拉取”下的来源 / 用户详情 / 首页提醒 | 近 24 小时同一时刻最多在几处（不同网络）在线，达到 `console_sharing_threshold`（默认 3）时首页提醒；订阅近 30 天被几个网络、几种客户端拉取。只显示数量，不显示 IP（[`plan-sharing-signals`](reviews/console/plan-sharing-signals-2026-09-19.md)） |
| 让某些用户、网站或整台节点经指定代理出口 | 出口 → 添加出口；节点详情 → 出口 → 分配 | 出口失效时按分配回退直连或断开；开启同步的节点约一分钟内生效，不重启 Xray（§14.13） |
| 节点维护时暂时不给用户，或把新节点加入订阅 | 节点列表 → 该行“隐藏 / 显示”，或勾选多台后“隐藏所选 / 显示所选” | 立即重新发布，用户刷新订阅后生效；节点详情页也有同样的按钮 |
| 看节点是否正常 | 首页“需要注意” / 节点列表 / 节点详情 | 在线、443 监听、最后上报、Xray 内存与运行时长、错误日志末尾；Xray 内存达到 `console_xray_memory_alert_mib`（默认 240 MiB）时首页提醒 |
| 看流量 | 流量 | 按月、按用户、按节点；日期按 UTC |
| 查谁在什么时候做了什么 | 记录 | 管理员操作与每次订阅发布的结果 |
| 看节点现在和过去是否正常 | 状态 | 与用户在“订阅地址/status”看到的内容相同，另有每种连接方式最近一次检测的结果；可给事件写说明（§14.8） |
| 手动重新发布订阅 | 首页 → 立即重新发布 | 按当前数据重写订阅服务的两个文件，页面显示结果；内容没变时用户端没有区别。一般不需要（发放、重置、吊销、显示切换、节点登记变化和控制台启动都会自动发布），用于自动发布失败并排除原因后，或订阅服务数据被清空后 |

不在控制台里做的事：

- 用户变化由节点 agent 自动落到节点上；只有页面提示“需要运行 edge.yml”时才运行（§14.10）。
- 部署、升级、增删节点：`edge.yml` / `edge-remove.yml`；节点上报见 §14.3。

### 14.3 节点上报

上报默认跟随新系统节点（`edge_report_nodes` 等于 `edge_nodes`），部署时自动开启；首次开启时该节点 Xray 重启一次（数秒）。

```bash
$PB edge.yml                                                  # 全部节点
$PB edge.yml --limit dzire -e edge_rotate_report_token=true    # 轮换某节点的上报 token
```

只让部分节点上报时，在 `group_vars/all/edge.yml` 把 `edge_report_nodes` 改成明确的列表。

节点侧核对（只读）：

```bash
ssh dzire "sudo docker compose -f /opt/xray-edge/compose.yaml ps; sudo docker logs --tail 20 xray_edge_reporter"
```

### 14.4 移除（回滚）

```bash
$PB console-remove.yml                              # 保留 db/ 与 registry/；-e console_remove_all=true 全部删除
$PB edge-remove.yml --limit dzire                   # 同时删除该节点的登记文件，控制台下次发布时不再包含它
```

控制台移除后订阅服务继续提供最后一次发布的订阅内容；状态页数据一并删除，用户状态页显示“暂无状态数据”。

### 14.5 本地验证

```bash
python3 -m unittest discover -s tests/edge -p 'test_r*.py'               # 应用器与上报程序
monitor_venv/bin/python tests/edge/test_compose.py
monitor_venv/bin/python tests/console/test_compose.py
monitor_venv/bin/python tests/edge/e2e_local.py                         # 节点端到端（Docker、外网）
monitor_venv/bin/python tests/console/e2e_local.py                      # 控制台端到端（Docker、外网）
# 控制台与状态页单元测试需要 docker/console/requirements.txt 的依赖，可在控制台镜像内运行（镜像由上面的 e2e 构建）：
for t in test_console test_status; do
  docker run --rm -v "$PWD":/repo:ro -w /repo --user 10002:10002 -e PYTHONDONTWRITEBYTECODE=1 reality-console:console-e2e python tests/console/$t.py
done
docker run --rm -v "$PWD":/repo:ro -w /repo --user 10001:10001 -e PYTHONDONTWRITEBYTECODE=1 reality-subs:console-e2e python tests/subs/test_subs.py
```

### 14.6 故障排查

| 现象 | 先查 / 处理 |
|---|---|
| 首页“最近一次订阅发布失败：shown nodes without a registration file” | 显示中的节点没有登记文件：对该节点运行 `edge.yml`，或在节点页隐藏它 |
| 节点“已开启上报，但还没有收到上报” | 节点上 `docker logs xray_edge_reporter`；HTTP 401 表示登记的 token 哈希与节点不一致，重新运行 `edge.yml`；`report.taoziyoyo.com` 返回 502 或 530 时按 [`runbooks/cloudflare-tunnels.md`](runbooks/cloudflare-tunnels.md) §9 排查 |
| `console.yml` 提示数据目录属主不是控制台 | 先运行 `subs.yml` |
| 订阅地址返回 503 | 控制台两次发布之间的瞬间会出现一次；持续出现时看控制台首页的发布记录 |
| 状态页所有节点“无数据” | `spt` 本机访问检测地址失败，或 `status` 服务没有运行：`sudo docker logs --tail 30 reality_console_status` |
| 首页“状态检测超过 5 分钟没有完成一轮” | 同上；日志中 `probe credential file is invalid` 或 `No such file` 表示探测凭据未下发，按 §14.8 写入 vault 后运行 `console.yml` |
| 首页“节点 X 用户同步失败：refusing …” | agent 拒绝了一次删除超过一半用户或空名单的变更；确认控制台里的用户无误后运行 `edge.yml` 应用 |
| 首页“节点 X 的用户同步超过 5 分钟没有联系控制台” | 节点上 `sudo docker logs --tail 20 xray_edge_reporter`；HTTP 401 表示登记的 token 与节点不一致，运行 `edge.yml`；刚部署的节点第一次联系会 401，5 分钟内自动重试 |
| 首页“出口 X 在节点 Y 上不可用” | 出口详情页看各检测方的结果与错误：spt 也失败时是代理本身的问题（停用、换账号或换出口）；只有该节点失败时查代理是否限制来源 IP、节点能否连到代理（§14.13） |
| 控制台显示的节点时间（上报周期、Xray 重启时间）与实际差很多 | 节点时钟没有同步：按 [`runbooks/node-time-sync.md`](runbooks/node-time-sync.md) 检查并安装校时服务 |
| 某节点状态一直失败，但用户能连 | 节点页“状态检测”是否为“已加入探测账号”；未加入或登记文件较旧时对该节点运行 `edge.yml`（§14.8） |

### 14.7 首次导入、订阅 token 的保存与恢复

- `console.yml` 只在控制台数据库里还没有发放过订阅、也没有设置过节点显示时导入一次并发布：`console_initial_shown_nodes`，
  以及 vault 中的 `vault_subs_tokens`（存在时）。过程是写临时文件 `/opt/reality-console/import/initial.json` →
  `docker compose run --rm web python -m console.admin import-initial` → 删除临时文件；输出中的“导入结果”一行说明本次是否导入。
- 订阅 token 只保存在控制台数据库 `/opt/reality-console/db/console.sqlite` 中；每日（UTC）备份为
  `db/backup/console-<日期>.sqlite`，保留 14 份。`console-remove.yml` 默认保留数据库与节点登记文件。
- vault 中已没有 `vault_subs_tokens`，不要再加回：控制台里的重置与吊销不会同步到 vault，数据库丢失后按旧 token 导入
  会让已吊销的地址重新生效。
- 数据库为空时控制台不发布，订阅服务继续提供最后一次发布的内容。此时如果直接运行 `console.yml`，只会导入节点并发布，
  所有订阅地址随之失效、需要重新发放，所以**先恢复备份，再运行 `console.yml`**。

在 spt 上恢复备份（控制台启动时会自动发布一次）：

```bash
D=/opt/reality-console/db
sudo ls -l $D/backup/
B=console-YYYY-MM-DD.sqlite                  # 改成要恢复的备份
R=$D/before-restore-$(date -u +%Y%m%dT%H%M%S)   # 出问题的数据库移到这里保留
sudo docker compose -f /opt/reality-console/compose.yaml stop web report
sudo sh -c "mkdir $R && mv $D/console.sqlite* $R/"   # 数据库文件已不存在时 mv 会报错，可以继续
sudo install -o 10002 -g 10002 -m 0600 $D/backup/$B $D/console.sqlite
sudo docker compose -f /opt/reality-console/compose.yaml start web report
```

恢复后在首页确认“订阅发布”成功，并用一个订阅地址确认能取到节点。备份之后到恢复之前的流量记录会丢失；
节点不会重发已被接收的上报，流量不会重复计算。

### 14.8 节点状态页

合同：[`plan-node-status-page`](reviews/console/plan-node-status-page-2026-09-18.md)。用户在订阅页点“查看所有节点的运行状态与历史”
（`https://sub.taoziyoyo.com/s/<token>/status`）；管理员看控制台“状态”。设置在 `group_vars/all/status.yml`。

`spt` 上的 `status` 服务每 60 秒用探测账号经每个节点（Vision；开启 XHTTP 的节点另测 XHTTP）访问一次 `http://cp.cloudflare.com/generate_204`。
探测账号在节点上的全部连接都被固定转发到这个地址，连不到别处。
某种连接方式连续 3 次失败记为事件，开始时间取第一次失败；连续 2 次成功结束。本机直接访问检测地址失败的那一轮记为“无数据”，
无数据不计入可用率；连续无数据超过两轮时，进行中的事件在最后一轮有数据之后一轮结束，空档前的失败也不再累计。
在控制台隐藏节点的期间记为“维护中”。`status` 服务停止超过三轮后，用户看到的状态页显示“状态数据已过期”。
页面有三种视图，在顶部切换：最近 1 小时（每格一次检测；单次失败不算故障）、最近 24 小时（每格一小时，默认）、
最近 90 天（每格一天）。控制台的“状态”页另有每节点每小时的检测耗时；耗时从 `spt` 测得，只在管理页显示。

**首次上线**（每步各自授权）：

1. 生成探测凭据并写入 vault（明文只在内存中；已存在时不修改）：

```bash
monitor_venv/bin/python - <<'EOF'
import os, secrets, uuid, yaml
from ansible.constants import DEFAULT_VAULT_ID_MATCH
from ansible.parsing.vault import VaultLib, VaultSecret
path = "group_vars/all/vault.yml"
secret = VaultSecret(open(os.path.expanduser("~/.vault_pass"), "rb").read().strip())
vault = VaultLib([(DEFAULT_VAULT_ID_MATCH, secret)])
plain = vault.decrypt(open(path, "rb").read()).decode()
if "vault_status_probe_uuid" in (yaml.safe_load(plain) or {}):
    raise SystemExit("已存在，未修改")
plain = plain.rstrip("\n") + (f'\nvault_status_probe_uuid: "{uuid.uuid4()}"'
                              f'\nvault_status_probe_short_id: "{secrets.token_hex(4)}"\n')
tmp = path + ".tmp"
with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as fh:
    fh.write(vault.encrypt(plain, secret))
os.replace(tmp, path)
print("已写入")
EOF
head -n 1 group_vars/all/vault.yml    # 预期 $ANSIBLE_VAULT;1.1;AES256；看到明文立即停止，不得提交
ANSIBLE_LOCAL_TEMP=/tmp/reality-ops-ansible-local monitor_venv/bin/ansible spt -i inventory.ini -c local \
  --vault-password-file ~/.vault_pass -m debug -a "msg={{ (status_probe_uuid | length) ~ ' ' ~ (status_probe_short_id | length) }}"
# 预期 "36 8"
```

2. 部署探测账号（`status_probe_nodes` 默认就是 `edge_nodes`，新节点部署时自动纳入）：

```bash
$PB edge.yml                    # 一次覆盖全部新系统节点（先一台试点，再其余同时），每台 Xray 重启一次；之后确认 test 仍能连接
$PB edge.yml --limit dzire      # 只处理一台时
```

凭据写入 vault 之前 `edge.yml` 会中止（断言提示缺少哪一项）。

3. 更新订阅服务（增加状态页地址）与控制台（启动 `status` 服务）：

```bash
$PB subs.yml                    # 订阅服务镜像更新，容器重建时中断数秒
$PB console.yml                 # 输出“核对状态服务已完成最近一轮检测”为 ok
```

4. 核对：控制台“状态”页中各连接方式最近一次检测为“成功”；用浏览器打开 `test` 的订阅地址，点状态页链接，内容与管理页一致。

```bash
ssh <spt> "sudo docker compose -f /opt/reality-console/compose.yaml ps status; sudo docker logs --tail 20 reality_console_status"
```

**日常**：

- 事件说明：控制台“状态”→“事件说明”，填写后约一分钟出现在用户状态页；每次保存记入“记录”。
- 计划维护：节点页“从订阅中隐藏”，期间显示“维护中”；恢复时点“在订阅中显示”。
- 修改间隔、失败 / 恢复次数或时区：改 `group_vars/all/status.yml` 后运行 `console.yml`。
- 修改检测地址（只能是 `http://`）：改 `status_check_url` 后先对每台探测节点运行 `edge.yml`（转发目标由它推导，Xray 重启一次），
  再运行 `console.yml`。

**关闭 / 回滚**：

- 停用状态页：`status_enabled: false` 后运行 `console.yml`，停止 `status` 服务并删除 `status.json`；历史数据保留在控制台数据库。
- 移除某节点的探测账号：在 `group_vars/all/status.yml` 把 `status_probe_nodes` 改成明确的列表（去掉该节点），
  再 `$PB edge.yml --limit <节点>`（Xray 重启一次）；该节点从状态页消失，历史保留。

### 14.9 在节点上移除旧系统

路线图 2026-09-18：用户迁到新系统后停用旧系统。`legacy-remove.yml` 只移除旧实例，节点保留给新系统，不修改仓库中的
`users/`、inventory 或 host_vars（`decommission.yml` 用于节点整台下线，会改用户档案，不适用于这种情况）。

```bash
$PB legacy-remove.yml -e '{"legacy_remove_nodes": ["jp05"]}' --check -e ansible_become=false   # 先预览（不改动、不用 sudo）
$PB legacy-remove.yml -e '{"legacy_remove_nodes": ["jp05"], "legacy_free_space": true}'
```

- 删除：`reality_*` 容器、`/opt/reality`（旧配置、日志、旧监控）、旧监控 agent（用户 `reality-monitor-agent`、其定时任务、
  `/usr/local/bin/traffic_agent.py`）、`/etc/logrotate.d/reality-xray`。`legacy_free_space` 另清理 journald 日志（保留 50MB）与 apt 缓存。
- 保留：新实例与 Xray 镜像（同一 digest）、`/etc/sysctl.d/99-reality-optimizations.conf`（BBR 等）、`/etc/docker/daemon.json`
  （Docker IPv6，新实例的 IPv6 监听依赖它）、已安装的软件包。
- 影响：该节点上的旧订阅链接随即失效；尚未拿到新订阅地址的用户失去这台节点，其他旧节点不受影响。
- `spt`（控制台与订阅服务所在主机）被拒绝执行。

### 14.10 用户管理：控制台为准

合同：[`plan-console-phase2`](reviews/console/plan-console-phase2-2026-09-18.md)（D-P2-1、D-P2-3、D-P2-8）。用户以控制台数据库为准，
`users/*.yml` 导入后冻结、不再维护，`generate_user.py` 不再用于新系统。

**首次导入**：`console.yml` 在控制台还没有用户时，从 `users/*.yml`（只取 `name`、`uuid`、`short_id`、`groups`、`hosts`、`deny_hosts`）、
inventory 分组（节点档位）与 `acl_matrix`（档位规则）导入；`edge_extra_users` 中的用户换算为单独允许全部 `edge_nodes`。
输出中的“用户导入与比对结果”列出每台已登记节点的差异，`"differences": {}` 表示导入结果与各节点现状完全一致。

```bash
$PB console.yml      # 导入并比对；差异不为空时先核对原因，再运行 edge.yml
$PB edge.yml         # 从控制台读取每台节点的用户（edge_user_source: console）
```

**档位规则**：节点有档位（初始取自 inventory 分组），档位规则规定每个节点档位接受哪些用户档位；用户档位含 `all` 时可用全部节点；
单独允许追加节点，单独禁止优先。只有“启用且未到期”的用户会进入节点与订阅；到期日按北京时间，当天结束后停止。

**日常**：在网页上新增、修改、停用、删除用户后，各节点的 agent 在一分钟内经 Xray API 增删账号，不重启 Xray；
订阅在节点报告已加入后自动更新（控制台约 30 秒内重新发布）。停用、到期与删除对订阅地址立即生效。
新用户统一使用控制台生成的共用 `short_id`（D-P2-5），它在部署时已写入各节点配置，所以不需要再运行 `edge.yml`。
只有下列情况仍要运行 `edge.yml`：节点页或首页提示“需要运行 edge.yml 后才能加入”（节点尚未配置某个 `short_id`）、
agent 拒绝了变更（一次删除超过一半用户或名单为空）、节点未开启同步（`edge_sync_enabled` 为假）。

**节点 agent（用户同步）**：节点上的 `xray_edge_reporter` 每 60 秒把本机运行的用户报给 `https://report.taoziyoyo.com/sync`，
取回控制台为本节点计算的名单（只含本节点的用户）并补齐差异；它不改配置文件、不重启 Xray，也不动探测账号。
Xray 重启后，运行中的用户回到上次部署的配置，agent 在一分钟内补齐。控制台“节点”页的“用户同步”一列显示
已同步 / 同步中 / 失败 / 多久未联系。

**回滚**：在 `group_vars/all/edge.yml` 设 `edge_user_source: "files"` 后运行 `edge.yml`，节点改回按 `users/*.yml` 与 ACL 计算用户
（此后控制台里的修改不再下发到节点）。

### 14.11 Telegram bot

合同：[`plan-console-phase2`](reviews/console/plan-console-phase2-2026-09-18.md) §3.5（D-P2-7）。vault 中有 `vault_console_bot_token`
时 `console.yml` 开启控制台的 `bot` 服务（长轮询、不开放端口），删除即关闭。创建 bot、写入 vault、设置管理员、核对、轮换与关闭的步骤见
[`runbooks/telegram-bot.md`](runbooks/telegram-bot.md)。

- 用户：管理员在用户详情生成绑定链接发给本人；绑定后 `/sub` 取地址与二维码（消息禁止转发）、`/me` 看状态与本月流量、
  `/status` 看节点状态、`/reset` 重置地址。停用或到期的用户取不到地址。一个 Telegram 账号只能绑定一个用户。
- 管理员（`vault_console_bot_admin_ids` 中的 Telegram ID）：`/adduser`、`/issue`、`/bind`、`/disable`、`/enable`、`/user`；
  在 bot 里看不到别人的订阅地址。
- 用户详情的 Telegram 一节显示绑定状态，可解除绑定、作废未使用的链接；bot 超过 5 分钟连不上 Telegram 时首页提示。
- Telegram 对话不是端到端加密；地址泄露时让用户 `/reset` 或在用户详情重置。

### 14.12 用户迁移

做法与决定：[`plan-user-migration`](reviews/console/plan-user-migration-2026-09-19.md)。旧系统不动；迁移以用户在新系统上“使用中”为准。

1. 确认要用的节点都已“在订阅中显示”（节点页）。
2. 用户列表 → 进度筛选“未发放” → 勾选这一批 → **发放并生成 Telegram 绑定链接**。
3. 链接清单页：把每人的链接连同消息示例私下发给本人（Telegram）。清单可下载，服务器上不保存；链接 24 小时内有效。
4. 用户绑定后在 bot 里发 `/sub` 取地址并导入。之后看首页“迁移进度”：
   - “待导入”：还没拉取；
   - “已导入”：拉取过但近 30 天没有流量；
   - “使用中”：已迁过来。
5. 发出 7 天仍“待导入”的，私下提醒；链接过期的，在用户页或用户列表重新生成。
   不用 Telegram 的用户，用“为勾选的用户发放订阅并导出清单”。


### 14.13 出口（代理出口）

合同：[`plan-egress-console`](reviews/console/plan-egress-console-2026-09-19.md)。出口、账号密码和分配都存放在控制台数据库，
代码与部署配置里不写具体出口；第一期支持 SOCKS5。需要节点开启用户同步（§14.10）；未开启同步的节点只在运行 `edge.yml` 时写入出口，
不做检测与切换。

1. **出口 → 添加出口**，或在“批量导入”里每行一个 `socks5://账号:密码@地址:端口#名称`。spt 约一分钟内检测一次
   （检测前显示“等待检测”），出口页显示是否可用、出口 IP、耗时。要马上知道结果，点该行或出口详情页的 **立即检测**：
   可用的几秒内返回，失效的约 10 秒（只试一次，偶然失败可以再点）。
2. **节点详情 → 出口 → 分配**：选出口，再填条件：用户、网站（`geosite:amazon`、`domain:example.com` 等）、IP 段（CIDR 或 `geoip:jp`）。
   都不填表示整台节点；填了多项时必须同时满足。“出口失效时”：
   - **回退直连**：效果与没有这条分配相同，适合一般网站；
   - **断开**：适合必须用固定出口 IP 的网站，宁可断开也不换 IP。
   “网络”默认只有 TCP；选“TCP 和 UDP”时代理本身要支持 UDP。优先级数字小的先匹配，同一个连接只走第一条匹配的分配。
3. 开启同步的节点约一分钟内生效，不重启 Xray。节点页“出口”一节显示每条分配的现状：
   经出口 / 出口失效，已回退直连 / 出口失效，已断开 / 同步中。用户详情“节点”表的“出口”列显示该用户在各节点走哪个出口。

检测与切换：

- 节点 agent 每分钟经分配到本节点的每个出口访问检测地址（`console_egress_check_url`，默认 Cloudflare 的 trace 页面），
  两次尝试都失败即判定失效，按分配回退直连或断开；连续两次成功后切回。从出口失效到切换最长约 80 秒（检测间隔 60 秒，
  加上两次各 10 秒的超时）。节点的检测结果显示在出口页，失效时首页提醒。
- 没有分配到节点的出口由 spt 检测：新加或修改后约一分钟内，之后每 10 分钟；“立即检测”也从 spt 检测。spt 的结果只说明
  spt 能否连到代理；代理限制来源 IP 时 spt 上会显示不可用，以节点的检测为准。
- Xray 重启后，配置文件里是上次运行 `edge.yml` 时的出口，agent 在一分钟内改成当前的出口和状态。

其他：

- 修改出口时密码框留空表示不改。页面、操作记录和日志都不显示密码；节点只收到分配给自己的出口。
- 停用出口：用到它的分配全部按直连处理。删除出口：同时删除它在各节点上的分配。
- 从旧的 `socks5.yml` profile 过渡：用户来源为控制台（`edge_user_source: console`）时，`edge.yml` 不再把 `socks5.yml` 的 profile
  写入新系统节点。需要的出口先在出口页添加并分配，再运行 `edge.yml`；没有添加的（例如已经失效的 `jpntt`）在下次 `edge.yml` 后
  从节点上移除，原先走它的用户改为直连。
- 回滚：在节点页删除分配，约一分钟内恢复直连。
