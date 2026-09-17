# 新数据面：单实例 · 443 · XHTTP（并行新增实现）

Status: **APPROVED；canary 验收完成（2026-09-15，`dzire`、`usca`、`legend`）。剩余缺口见路线图 §7“S3 验收状态”。**

2026-09-15 操作者确认端口、测试用户与 XHTTP path 方案；本文批准只授权工作树内实现与 §6.1 本地验证，各节点动作另行授权（§9）。

Created: 2026-09-15 JST

上位计划：[`roadmap-unified-2026-08-27.md`](../roadmap-unified-2026-08-27.md) §4.1（C00）、§4.1.3
决定 1–9、§7 S3、§7.1。本文只细化 S3 的实施合同，不改写路线图决定；与路线图冲突时以路线图为准并先修订路线图。

## 1. 目标

在不修改现有 single/multi 实现与节点旧实例的前提下，新增一套独立实现，使一台节点在旧实例
继续服务的同时并行运行一个新的 Xray 实例：

1. **第一步（U1）**：单实例、443、REALITY + RAW + Vision，节点本地生成并保存 REALITY 私钥，
   `conf.d` 分层配置，按期望状态收敛，API 增删用户，文件日志与 Docker 日志有上界，镜像固定 digest；
2. **第二步（U4）**：在同一新实例内增加 XHTTP 回落入站，客户端经 443 以 XHTTP 连接。

两步在 canary 上验收通过后，才进入用户迁移（§7.1 migrate，另立授权）。

## 2. 批准时现状

- `[代码]` 基线 `ops@b3571d1`。旧实现：`deploy.yml`（pre_tasks 内联用户加载、校验与 ACL 计算，
  得出 `reality_instances`）→ `roles/reality_single` 或 `roles/reality_multi`。
- `[代码]` 用户档案 34 份，字段 `name`、`uuid`、`port`、`short_id`、`private_key`、`public_key`（34/34），
  `groups`、`hosts`（17）、`deny_hosts`（8）。
- `[代码]` 旧实现会删除的对象（新实现必须避开）：single 删除除 `reality_core` 外全部 `reality_*`
  容器（`roles/reality_single/tasks/main.yml:62-75`）；multi 在无授权用户时删除全部 `reality_*`，
  compose 项目目录 `/opt/reality/data` 且 `remove_orphans: true`；`reset.yml`、`decommission.yml:297`
  删除全部 `reality_*` 容器；single 的 `reality_core` 占用宿主机 `127.0.0.1:10085`。
- `[代码]` monitor agent 把非 `reality_core` 的 `reality_*` 容器视为 multi 用户（`agent.py.j2:85-91`）。
- `[代码]` SOCKS5 profile 当前只路由到 `jp10`（`group_vars/all/socks5.yml`），canary 节点无 SOCKS5 路由。
- `[上游]` 镜像 `taoziyoyo2566/xray-docker` entrypoint 在无 `/config.json` 时使用 `XRAY_CONFDIR`
  （默认 `/etc/xray/conf.d`），启动前执行 `xray run -test`
  （`xray-docker@4cb6f10`）。`v26.3.27` = `sha256:fd502666d1a9ca7ea772e2c1638bb83e79bdc12c97cddd5e3755edc7485b3ec7`
  （2026-09-15 Docker Hub，amd64/arm64）；Xray-core 最新 stable 仍为 v26.3.27（其后 v26.7.x–v26.9.9 均为预发布）。
- `[上游]` 官方示例 `XTLS/Xray-examples@a64a519` 的 `VLESS-XHTTP-Reality/minimal-steal_others` 为
  **XHTTP 直接承载 REALITY 独占 443**；未提供“RAW+Vision 与 XHTTP 共用 443”的官方示例。
- `[实测]` 2026-09-15：`dzire`（multi，32 容器，Debian 13，Docker 29.7.2，2 核，内存 1684/3921MB，
  无全局 IPv6）与 `usca`（single，Debian 13，Docker 29.6.0，1 核，内存 758/1431MB，有 IPv6）的 443
  与 4443 均无监听；两台均有 Python 3.13.5，无 ufw/nft。云厂商安全组未查。

## 3. 设计

### 3.1 组成与隔离

全部为新增文件，不修改任何现有文件（inventory、`group_vars/all/main.yml`、`deploy.yml`、旧角色、monitor）：

| 路径 | 作用 |
|---|---|
| `edge.yml` | 新 playbook；只对 `edge_nodes` 中的主机执行，未列入则断言失败 |
| `group_vars/all/edge.yml` | `edge_nodes`、`edge_extra_users`、`edge_test_users`、`edge_xray_image`（digest）、端口、日志上界、XHTTP 开关等新变量 |
| `roles/xray_edge/tasks/acl.yml` | 用户加载、校验与 ACL 计算（复制自 `deploy.yml` pre_tasks，见 §3.4） |
| `roles/xray_edge/tasks/main.yml` | 目录、镜像、期望状态下发、调用节点应用器、容器、日志轮转 |
| `roles/xray_edge/files/xray_edge_apply.py` | 节点端应用器（仅 Python 标准库），见 §3.2 |
| `roles/xray_edge/templates/desired-state.json.j2` | 期望状态（不含 REALITY 私钥） |
| `edge-remove.yml` | 移除新实例、配置、timer（回滚用；不触碰旧实例） |
| `tests/edge/` | 渲染器单元测试、golden、本地 `xray run -test -confdir` 与本地端到端连通测试 |

节点侧命名与资源：

| 项 | 值 | 避开的冲突 |
|---|---|---|
| 容器名 | `xray_edge` | 不以 `reality_` 开头 |
| 根目录 | `/opt/xray-edge/{conf.d,last-good,state,keys,logs}` | 不在 `/opt/reality` 下，不属于任何 compose 项目 |
| 编排 | `community.docker.docker_container`（单容器，不用 compose） | 不受 `remove_orphans` 影响 |
| 业务端口 | 宿主机 `0.0.0.0:443` 与（有全局 IPv6 时）`[::]:443` → 容器 443（2026-09-15 变更，见 §10） | 旧实例用每用户高位端口 |
| API | 容器内 `127.0.0.1:10085`，**不发布到宿主机**；经 `docker exec xray_edge xray api ... -s 127.0.0.1:10085` 调用 | 不占宿主机 `127.0.0.1:10085` |
| 日志轮转 | `/etc/xray-edge/logrotate.conf` + `xray-edge-logrotate.{service,timer}` | 不改 `/etc/logrotate.d/reality-xray` |
| 网络 | bridge（决定 5） | — |
| 运行参数 | UID `10000:10000`、`read_only`、`cap_drop: ALL`、`no-new-privileges`、`pids_limit`、内存上限，与旧实例同级 | — |

### 3.2 期望状态与节点端应用器

私钥不离开节点，控制端只处理不含私钥的期望状态；应用器就是将来 agent 的收敛核心（决定 8），
届时只把“Ansible 下发期望状态”换成“agent 取得签名期望状态”。

**期望状态 `desired-state.json`（schema v1）**，由 Ansible 渲染后下发到 `/opt/xray-edge/state/desired.json`（root:root 0600）：

```jsonc
{
  "schema": 1,
  "node": "dzire",
  "generated_at": "<ISO8601>",
  "reality": { "target": "www.flipkart.com:443", "server_names": ["www.flipkart.com"] },
  "listen": { "port": 443 },
  "xhttp": { "enabled": false, "path": "<vault_edge_xhttp_paths[node]>", "mode": "auto" },
  "users": [ { "name": "<user>", "uuid": "<uuid>", "short_id": "<sid>" } ],
  "socks5": [ /* 本节点生效 profile：address/port/user/pass/priority/route，按 D12 单一门控 */ ],
  "log": { "level": "warning" }
}
```

**应用器 `xray_edge_apply.py`**（以 root 在节点执行，Ansible 以 `command` 调用并注册结构化结果）：

1. **密钥**：`keys/reality.json`（root:root 0600）不存在时，经 `docker run --rm <digest> x25519` 生成并写入；
   已存在则只读取。输出只含公钥；私钥不进入 stdout、Ansible 变量或日志。
2. **渲染**：纯函数 `render(desired, private_key) -> {filename: json}`，生成 §3.3 的四个文件到候选目录。
3. **校验**：`docker run --rm -v <候选>:/etc/xray/conf.d:ro <digest> run -test -confdir /etc/xray/conf.d`。失败即退出，
   活动目录与运行实例不变。
4. **分类变更**：与活动目录比较。只有 `clients` 列表不同 → `api`；`00-base`、REALITY/端口/XHTTP 参数、
   `20-outbounds`、`30-routing` 变化 → `restart`（SOCKS5 的 `ado`/`rmo`/`adrules` 热更新留到有 SOCKS5 路由的节点再验证，
   本计划先走重启）。
5. **应用**：先把活动目录复制为 `last-good/`，再逐文件同目录 `rename` 替换进 `conf.d/`（目录 bind mount 能看到替换后的文件）；
   `api` 类按差集执行 `rmu` / `adu`，`restart` 类重启容器。
6. **核对**：`inbounduser` 列出的 email 集合必须等于期望用户集合；容器 `Running` 且 `RestartCount` 未增长。
   失败时恢复 `last-good/` 并重启，返回非零。
7. **权限**（C05 合同）：`conf.d/` root:10000 0750，文件 root:10000 0640；`keys/`、`state/`、`last-good/` root:root 0700。

Ansible 侧只取回公钥（非秘密），用于订阅输出（§3.7）。

### 3.3 配置布局

沿用路线图 §4.1.2，按变更频率拆分，全部挂载到 `/etc/xray/conf.d`（只读）：

| 文件 | 内容 |
|---|---|
| `00-base.json` | `log`（access/error 写 `/var/log/xray`）、`api`（Handler/Stats/Logger/Routing）、`stats`、`policy`（用户上下行统计、`statsUserOnline`） |
| `10-inbounds.json` | `api` 入站 `127.0.0.1:10085`；`vless-reality`：443，`network: raw`，`security: reality`（`target`、`serverNames`、节点私钥、全部用户 `shortIds`），`clients` 每用户一行 `{id, email: <user>.<node>, flow: xtls-rprx-vision}`，`sniffing.routeOnly`；第二步增加 `fallbacks` 与 `vless-xhttp` |
| `20-outbounds.json` | `direct`、`blocked`、生效的 `socks5-<profile>` |
| `30-routing.json` | 全部规则带 `ruleTag`：`api`、`block-bt`、`block-private`、SOCKS5（`user` 条件）、末条兜底 `direct` |

**第二步（XHTTP）**：`vless-reality` 增加 `fallbacks: [{ "path": "<xhttp path>", "dest": "@xray-edge-xhttp" }]`
（或等价的本地监听），新增入站 `vless-xhttp`：`listen: "@xray-edge-xhttp"`，`network: xhttp`，
`xhttpSettings.path` 同上，`security: none`，`clients` 与 `vless-reality` 相同但 `flow` 为空。

`[推论]` 该“RAW+Vision 回落到 XHTTP”的写法依据上游讨论 #4113，没有官方示例；具体字段（回落匹配 path 与否、
abstract socket 与 `127.0.0.1:<port>` 的选择、`xver`）必须先通过 §6.1 的本地端到端测试才能进入 canary。
若本地证明共用 443 不可行，停止并回到路线图评审（备选是官方示例的“XHTTP+REALITY 独占 443”，这会改变 Vision 的去留，属于重大偏差）。

### 3.4 用户与 ACL

- `acl.yml` 复制 `deploy.yml` pre_tasks 的用户加载、三项格式校验、dest/serverNames 配套校验与 ACL 计算，
  得出与旧实现相同的 `reality_instances`；文件头注明来源提交与“随旧实现一起删除”。选择复制而不是抽共享文件，
  是为了满足决定 7（不改旧文件）。
- 额外用户：`edge_extra_users`（canary 期间为 `["test"]`）只加入新实例的 `clients`，不改用户档案，也不影响旧实例。
  额外用户必须存在于 `users/`，且不得与 ACL 结果重复。
- 漂移检查：`edge.yml` 在收敛前比较两组用户集合——期望状态中的用户名集合**去掉 `edge_extra_users`** 后与节点旧实例的实际用户集合
  （multi：`reality_*` 容器名；single：`reality_core` 的入站 tag `user-<name>`，经 Xray API 列出，不读配置文件；
  所用 API 子命令在 v26.3.27 上的可用性于 §6.1 确认，不可用时改为只比较容器内配置文件中 email 集合的 hash 摘要）。
  不一致时停止，避免新旧实例授权范围不同。
- 新增校验：用户名不得含 `.`、`@`（email 与 monitor 截取规则）。
- REALITY `target` 与 `serverNames` 沿用该主机现有 `reality_dest` / `reality_server_names`，不在本计划换 target（U2 单独做）。

### 3.5 镜像

`edge_xray_image: "taoziyoyo2566/xray-docker@sha256:fd502666…"`（v26.3.27）。存在性检查与拉取使用 digest 引用，
拉取任务设 `force_source: true`。旧实现的 `xray_image` 不变。

### 3.6 日志

- 文件日志：`/opt/xray-edge/logs/{access,error}.log`，`xray-edge-logrotate.timer` 每小时以独立 state 调用 logrotate，
  `maxsize 50M`、`rotate 14`、`compress`、`copytruncate`、`nocreate`（与旧模板同理：Xray 无重开日志信号）。
- 容器 stdout：`log_driver: json-file`，`max-size 10m`、`max-file 3`。

### 3.7 订阅输出（仅测试用户）

canary 期间不改 `generate_subs_gist.py`、Gist 与 `/opt/reality/users`。`edge.yml` 仅为 `edge_test_users`（canary 期间为 `["test"]`）中的用户
在控制端写 `~/.local/share/reality-ops/edge-subs/<user>_<node>.txt`（目录 0700、文件 0600，不在仓库内），内容：

每个用户只输出以 `node_endpoint` 域名为地址的链接（2026-09-15 变更，见 §10）；域名同时有 A/AAAA 时由客户端选择地址族。

- 第一步：`vless://<uuid>@<node_endpoint>:443?encryption=none&security=reality&type=tcp&sni=<sni>&fp=chrome&pbk=<节点公钥>&sid=<用户 sid>&flow=xtls-rprx-vision#<user>.<node>-edge`
- 第二步追加一条同样以域名为地址的链接：`type=xhttp&path=<path>&mode=auto`，无 `flow`。

链接由操作者手工导入测试客户端。正式迁移时的订阅输出属于 §7.1 migrate，另行设计。

### 3.8 观测

旧 monitor 不改。canary 期间用只读命令核对：`xray api statsquery`（用户上下行）、`statsonlineiplist`、`inbounduser`、
容器状态与重启次数、access log 中的来源地址（仅统计计数）。

### 3.9 回滚与移除

- 任一步失败或异常：`edge-remove.yml -l <node>` 停止并删除 `xray_edge`、`/opt/xray-edge`（密钥可选保留）、timer 与配置；
  旧实例不受影响，无需恢复。
- 迁移完成后删除旧实现属于 S7 contract，不在本计划。

## 4. 范围与非目标

范围：§3 的新增文件；本地测试；两台 canary（第一台 `dzire`，第二台 `usca`）的 expand 与两步验收。

非目标：修改旧实现或共用变量；正式订阅切换、Gist、用户通知；换 REALITY target；SOCKS5 热更新与真实 SOCKS5 路由验证
（canary 节点无 SOCKS5 路由，另在 `jp10` 验证）；monitor 改造；控制面与 agent；删除旧实例。

## 5. 前提、未知与关闭方式

| 项 | 类型 | 关闭方式 | 若结果相反 |
|---|---|---|---|
| RAW+Vision 与 XHTTP 共用 443 的回落写法 | `[推论]` | §6.1 本地端到端测试 | 停止第二步，回路线图评审 |
| `xray api adu/rmu/inbounduser/lsi` 在 v26.3.27 的可用性与以文件增删用户的准确语义 | `[缺口]` | 本地容器实测 | 用户变更改走重启、核对改用配置摘要，并在路线图记录 |
| 节点 443 从公网可达（云安全组） | `[缺口]` | expand 后从控制端探测 TLS 握手 | 停在该节点，由操作者开放端口 |
| bridge 下 IPv6 客户端源地址 | `[缺口]` | `usca` canary 用 IPv6 客户端测试 | 记录为 IPv6 限制，评审是否需要改网络模式 |
| 测试客户端对 XHTTP 的支持 | `[缺口]` | 操作者列出实际使用的客户端，逐个导入测试 | 不支持的客户端保留 RAW+Vision 链接 |
| dzire 无全局 IPv6 | `[实测]` | 由第二台 `usca` 覆盖 | — |

## 6. 验收标准

### 6.1 本地（控制端，无外部写入）

1. 渲染器单元测试：用户增删、空用户、SOCKS5 门控（D12 同门同出）、用户名非法、XHTTP 开关；输出为合法 JSON 且与 golden 一致；fixtures 无真实值。
2. 以 `edge_xray_image` 运行 `xray run -test -confdir`：第一步与第二步配置均通过。
3. 本地端到端：在控制端 Docker 起服务端与客户端两个容器，客户端经 RAW+Vision 与 XHTTP 两条链路通过服务端访问外网 URL 成功；
   用 API 增删一个用户后，被删用户连接失败、新增用户成功，且无需重启。
4. `edge.yml`、`edge-remove.yml` 语法检查通过；`--list-tasks` 不包含任何旧角色任务。
5. 秘密扫描：仓库与 Ansible 输出中无 REALITY 私钥。

### 6.2 canary 第一步（`dzire`，另行授权）

1. 部署前后旧实例对比无变化：32 个 `reality_*` 容器 ID、创建时间、重启次数、配置文件 hash 一致。
2. `xray_edge` 运行，镜像 ID 为固定 digest，`RestartCount=0`，宿主机 443 监听，API 未发布到宿主机。
3. 期望用户集合 = 旧实例用户集合；`inbounduser` 与之相等。
4. 控制端经 443 完成 REALITY 握手；测试用户客户端导入链接后可访问外网，`statsquery` 显示该用户流量。
5. 通过应用器增删一个测试用户，API 生效且不重启；故意下发坏配置时 `-test` 拒绝、运行实例不变。
6. 容器重启后配置与用户一致；timer 已启用，手动触发一次轮转成功；Docker 日志参数正确。
7. `edge-remove.yml` 在 canary 上演练一次并重新 expand，旧实例全程无变化。

### 6.3 canary 第二步（XHTTP）

1. 开启 `xhttp.enabled` 后应用器判定为重启类变更并成功应用；RAW+Vision 链接仍可用。
2. 测试客户端以 XHTTP 链接连通并有流量；未匹配路径的探测按 REALITY 行为回落到 target，不暴露 Xray 特征（以 `curl`/`openssl` 对比开启前后的响应）。

### 6.4 第二台 canary（`usca`）

重复 6.2–6.3，并补充：原 single 节点形态、IPv6 客户端连通与源地址、1 核 / 1.4GB 内存下新旧并行的资源占用。

## 7. 风险

- 新旧实例并行期间，节点同时暴露旧高位端口与 443，封锁面短期扩大；并行窗口应尽量短（路线图 §7.1）。
- 复制的 ACL 逻辑可能与旧实现漂移；§3.4 的集合比较是止损手段，旧逻辑变更时须同步或停止部署新实例。
- 应用器以 root 运行并调用 Docker，权限高；只接受本地期望状态文件，不执行其中任何命令（路线图 §6 第 7 条）。
- `docker run --rm ... -test` 每次应用都会起临时容器，节点需已存在固定 digest 镜像；拉取失败时应停止而不是回退到 tag。

## 8. 待操作者确认

1. ~~业务端口~~：2026-09-15 操作者确认使用 **443**（两台 canary 的 443 均空闲，不影响旧实例）。
2. ~~`edge_test_users`~~：2026-09-15 确认为 `test`。`test` 在 `dzire`/`usca` 上无 ACL 授权，按操作者选择经 `edge_extra_users`
   只加入新实例（§3.4）。
3. ~~XHTTP `path`~~：2026-09-15 确认每节点随机生成，保存在 vault 变量 `vault_edge_xhttp_paths`（按节点名索引）。
4. ~~实际使用的客户端清单~~：2026-09-15 确认为 v2rayN、Shadowrocket 与主流 Clash（Mihomo）客户端；Clash 真实设备测试按操作者决定暂缓。
5. `jp05` 等 469MB 内存节点是否需要在后续迁移前单独评估。

## 9. 授权边界

本文为 DRAFT。批准后仅授权工作树内实现与 §6.1 本地验证。以下各自单独授权：Git 发布；对 `dzire`、`usca` 的每次部署、
移除或重启；从控制端对节点的主动连通测试之外的任何节点变更；订阅、Gist、DNS、云安全组与用户通知。

## 10. 已批准的变更

- **2026-09-15 · IPv6 监听与测试链接地址。** 起因：`usca` canary 实测时，测试链接同时给出域名和 IPv6 地址两条，
  而 `usca` 的节点域名已有指向同一地址的 AAAA。调查（Xray-docs-next `7aa9bea` `sockopt.md`；Go `net/addrselect.go`
  RFC 6724 排序）表明：客户端连接同时有 A/AAAA 的域名时，Xray 内核默认 `AsIs` 使用 Go Happy Eyeballs 选择地址族；
  ipinfo 看到的是节点出站地址，与所用链接无关。同时发现新实例绑定的是 Ansible 选出的第一个公网 IPv6，而
  `jp10`、`kagoya`、`netcup`、`legend` 各有两个公网 IPv6，AAAA 指向另一个时经域名的 IPv6 连接会失败。
  操作者决定：有全局 IPv6 的节点改为发布 `[::]:443`；测试链接只保留域名一条。节点出站地址族策略保持默认，不在本计划调整。
  影响：`usca` 重新部署时容器因端口映射变化而重建；`dzire` 无全局 IPv6，不受影响。
- **2026-09-15 · 第三台 canary `legend` 与出站竞速。** 操作者授权将 `legend`（原 single，有 IPv6，操作者认为 IPv6 质量较好）
  纳入 canary，只开 RAW+Vision；同时在 `usca`、`legend` 启用路线图 S8 的出站 IPv4/IPv6 竞速（`happyEyeballs` 推荐值）。
  竞速属 S8 独立功能，不是本合同的验收项；按 `edge_happy_eyeballs_nodes` 逐节点开关，默认关闭。
- **2026-09-15 · 整体纳入 Docker Compose。** 起因：操作者要求功能不散布在宿主机各处、便于维护与快速部署。批准的修订合同
  [`plan-edge-compose-2026-09-15.md`](plan-edge-compose-2026-09-15.md)：`xray_edge` 改由 compose 管理（运行参数不变），日志轮转改为
  compose 内容器，应用器进入固定版本的工具镜像，删除 `/opt/xray-edge` 之外的新数据面文件。影响：§3.1 组成、§3.6 日志与 §3.9 移除
  的实现方式以修订合同为准；三台 canary 逐台迁移，各自另行授权。
- **2026-09-17 · 新旧用户集合比较改为只提示（D-C5）。** 起因：2026-09-16 操作者决定新系统作为独立产品建设、不迁移旧用户
  （[`roadmap-unified-2026-09-16.md`](../roadmap-unified-2026-09-16.md) §2）。§3.4 的比较原本保证迁移前后用户权限不变，
  不迁移后这一目的不再成立，却使每次用户变更都必须先对旧节点运行 `deploy.yml`，`edge_drift_old_only_users` 的例外也会变成永久。
  操作者批准：`edge.yml` 仍只读取旧实例用户，但只列出差异、不中止部署；节点没有旧实例时跳过比较；删除 `edge_drift_old_only_users`。
  影响：§3.4“不一致时停止”与 §7 中“集合比较是止损手段”改为提示；ACL 副本与 `deploy.yml` 的一致性靠提示发现（按决定 9，`deploy.yml` 不再变更）。
  `shuaiqi` 在旧实例上的访问保留，直到操作者另行对旧节点运行 `deploy.yml`，每次部署都会提示。
