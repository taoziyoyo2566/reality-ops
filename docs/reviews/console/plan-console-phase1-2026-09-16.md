# 管理控制台第一阶段：管理员发放订阅、查看节点与流量

Status: **COMPLETE — 2026-09-18，结果见 [Changelog](console-phase1-2026-09-18.changelog.md)。**（2026-09-17 批准，D-C1～D-C5 见 §8）

Created: 2026-09-16 JST

Updated: 2026-09-17 JST

上位计划：[`roadmap-unified-2026-09-16.md`](../roadmap-unified-2026-09-16.md) §2、§7（本阶段为其中的 P1，见 §7.0）。相关合同：
[`plan-edge-compose`](../data-plane-edge/plan-edge-compose-2026-09-15.md)、
[`plan-subscription-service`](../subscription-service/plan-subscription-service-2026-09-15.md)。
进度见路线图 §7“进度总览”中的 P1 一行。

控制台第一阶段不实现路线图 S2 的控制回路：节点上报是单向遥测，改配置仍由管理员执行 Ansible。
本文修改 S4 合同的三项做法（§8 D-C1、D-C2、D-C3），已记录在 S4 合同 §10；D-C5 修改 S3 合同 §3.4，已于 2026-09-17 批准，记录在 S3 合同 §10 并已在工作树实现。

## 1. 目标

操作者 2026-09-16 明确：新系统（`xray_edge`、新订阅服务）独立于旧系统；旧系统继续运行、不修改、暂不删除，不迁移旧用户，没有时间压力。
新系统做成产品形态的管理控制台，分用户、订阅、节点三个模块，以实现功能为核心，不做复杂机制。

第一阶段只给管理员使用：

1. 以用户为单位发放、重置、吊销订阅地址，随时查看地址与二维码；
2. 查看节点情况：在线状态、版本、用户数、日志摘要；
3. 查看流量：按用户、按节点、按日或按月（新做的最基本采集，现有 monitor 不动）；
4. 控制每台节点是否出现在订阅中。

登录（authentik OIDC）、普通用户功能、用户增删改、由控制台触发节点操作，放在后续阶段。

## 2. 批准时现状

- `[代码]` `xray_edge`（compose 形态）在 `dzire`、`usca`、`legend`、`kagoya`；订阅服务在 `spt` 上线，只发放 `test`。
- `[代码]` 订阅数据由 `subs.yml` 生成：逐台节点计算 ACL，对 `migrated` 节点经 SSH 运行应用器 `verify` 取公钥，
  再在控制端构建 `catalog.json` 与 `tokens.json`。token 明文在 vault（`vault_subs_tokens`），开放用户另由 `subs_enabled_users` 列出，
  节点状态由 `subs_node_states` 设置，未列出的节点默认 `legacy`，输出旧系统 `/opt/reality/users` 中的旧链接。
- `[代码]` 用户档案 `users/*.yml` 由新旧系统共用；`edge.yml` 收敛前读取旧实例用户，只提示差异、不中止（D-C5，工作树，未部署）。
- `[代码]` monitor agent 只采集 `reality_core` 与 `reality_*` 容器，不采集 `xray_edge`。
- `[代码]` 每次 playbook 运行都需要操作者的 sudo 密码（`-K`）与 vault 口令文件，控制台无法在不保存节点 root 凭据的情况下自动运行 Ansible。
- `[代码]` `xray_edge` 容器使用 `network_mode: bridge`，Xray API 只在容器内 `127.0.0.1:10085`（含增删用户的 HandlerService）；应用器经 `docker exec` 调用。
- `[上游]` 2026-09-17 核对：Xray v26.3.27 的 `metrics` 配置有 `listen` 字段；main 分支 `app/metrics/metrics.go` 的 `/debug/vars` 输出
  入站、出站与每用户的累计流量计数，注册的路由（`/debug/vars`、`/debug/pprof/*`）都不能修改状态。
- `[实测]` 2026-09-15 `spt`：x86_64，可用内存约 1.1GB，Docker Compose 插件 5.1.1，`community.docker` 5.1.0。
- `[实测]` 2026-09-17 批准时：`report.taoziyoyo.com` 已解析到 Cloudflare，但这条路由（→ `http://report:8201`）配在订阅服务的隧道上，
  由 `reality_subs_cloudflared` 处理，解析不到 `report` 而返回 502；vault 中尚无 `vault_console_tunnel_token`。
  部署前须按 §3.6 改为控制台自己的隧道（§5 第 4 项）。`sub.taoziyoyo.com`、`subs.`、`monitor.` 同时返回 200。

## 3. 设计

### 3.1 组成

```text
管理员浏览器 ──SSH 隧道──> spt 127.0.0.1:8200
                               │
compose 项目 reality-console（spt）
  ├─ web          管理页面；只发布到宿主机 127.0.0.1:8200
  ├─ report       只接收节点上报；只在内部网络
  └─ cloudflared  独立隧道；公共主机名只转发到 report
         ▲
         │ HTTPS + 每节点 token（每 5 分钟）
节点 compose 项目 xray-edge：xray、logrotate、reporter（新增）

edge.yml ──节点注册文件──> spt:/opt/reality-console/registry/
web ──catalog.json / tokens.json──> spt:/opt/reality-subs/data/ ──> 订阅服务（已上线，不改接口）
```

### 3.2 模块与第一阶段功能

| 模块 | 功能 | 数据来源 |
|---|---|---|
| 用户 | 列表：名称、分组、可用的新节点、是否已发放、最后拉取时间；详情：可用节点、订阅地址与二维码、流量 | `users/*.yml` 的非秘密字段（`name`、`groups`、`hosts`、`deny_hosts`，由 `console.yml` 导出）；节点注册文件中的用户集合 |
| 订阅 | 发放、重置、吊销；每次变更后自动重建并发布；发布记录；拉取记录 | 控制台数据库（token）；节点注册；订阅服务访问日志（只读） |
| 节点 | 列表：在线/离线、最后上报、镜像、最近一次 Xray 重启（由计数归零推断）、用户数（注册文件，部署时已核对）、443 监听、今日流量；详情：错误日志末尾、各用户流量；“在订阅中显示”开关 | 节点注册；节点上报 |
| 流量 | 按用户、节点、日、月汇总 | 节点上报 |
| 审计 | 管理员操作记录 | 控制台数据库 |
| 首页提示 | 节点超过 15 分钟未上报；最近一次发布失败 | 以上各模块 |

### 3.3 节点注册（edge.yml → 控制台）

- 应用器在节点本地生成上报 token（与 REALITY 密钥同样处理，存 `/opt/xray-edge/secrets/report-token`，root:10000 0640），
  `verify` 输出其 SHA-256，不输出明文。
- `edge.yml` 在 `verify` 之后，把注册文件写到 `spt:/opt/reality-console/registry/<节点>.json`（控制端即 `spt`）：
  节点名、显示名、endpoint、端口、SNI、公钥、XHTTP 设置、节点上的用户（`name`、`uuid`、`short_id`）、上报 token 哈希、部署时间与镜像。
- 控制台按文件变化重新加载。注册文件是控制台中节点与订阅数据的唯一来源，替代 `subs.yml` 对节点的 SSH 读取。
- 上报 token 可轮换（应用器按参数重新生成，重新部署后注册文件更新哈希）与吊销（删除注册文件中的哈希，或关闭该节点的上报）。
- 与路线图 S2 的关系：token 在节点生成、只有哈希离开节点，符合路线图 §6 第 1 条的原则；到 S2 时只把“经 Ansible 登记哈希”
  换成节点一次性注册，不并存两套节点身份。
- 新旧用户集合只提示差异（D-C5）：旧实例的用户变更由管理员另行对旧节点运行 `deploy.yml`。

### 3.4 节点上报（reporter）

- Xray 基础配置（`00-base.json`）增加 `metrics`，`listen` 为容器内 `127.0.0.1` 的一个端口，不发布到宿主机。
  这是重启类变更，每台节点部署时 Xray 重启一次。
- `xray-edge` compose 新增服务 `reporter`：
  - 使用工具镜像，`network_mode: "service:xray"`，只读取 metrics 的 `/debug/vars`，**不使用 Xray API**（API 可增删用户）；
    工具镜像不含 `xray` 可执行文件与 gRPC 客户端；
  - UID 10000、只读根文件系统、`cap_drop ALL`、`no-new-privileges`、内存上限，不挂载 Docker socket；
  - 只读挂载 `logs/` 与上报 token，可写 `spool/`。
- 每 `edge_report_interval`（默认 300 秒）采集一次：
  - `/debug/vars` 中各用户的累计上下行，与上次读数相减得到增量；某计数变小时视为 Xray 重启，以当前值为增量；
  - 443 是否在监听（读取共享网络命名空间的 `/proc/net/tcp`、`/proc/net/tcp6`）；
  - 错误日志末尾若干行。
- 上报以 HTTPS POST 发到 `edge_report_url`，携带节点 token；每份上报带递增序号。失败时写入 `spool/`（有数量上限）并在下次重发；
  控制台按“节点 + 序号”去重。
- `edge_report_enabled` 按节点开关，关闭时不部署 `reporter`。

### 3.5 订阅发布

- 控制台是 `catalog.json` 与 `tokens.json` 的唯一写入者（D-C1），复用 `subs/catalog.py` 的校验，原子写入 `/opt/reality-subs/data/`；
  订阅服务按文件变化自动重新加载（现有行为）。
- 两边只以数据目录中的这两个文件交接，订阅服务不增加写入接口，也不主动访问控制台（保持 S4 §3.1、§3.4 的设计）。
  目前两者同在 `spt`，由控制台直接写入；订阅服务日后迁到其他主机时，只增加一个文件传输步骤，两个服务都不改。
- catalog 只包含已注册、且在控制台中设为“显示”的节点，状态为 `migrated`；未注册的节点不出现，也不读取旧系统文件（D-C2）。
- 开放用户 = 已发放 token 的用户；用户在某节点的凭据取自该节点的注册文件，用户不在该节点上时不输出。
- token 明文存控制台数据库，管理员随时可查看地址（D-C3）。数据库与备份只归控制台所有（0600），不进 Git；
  vault 在 Git 中的加密副本不再存在，异地备份另行安排。
- `subs.yml` 只保留订阅服务部署（镜像、compose、隧道），删除汇总与构建部分，以及 `subs_enabled_users`、`subs_node_states`。
  vault 中 `test` 的 token 在首次部署时导入控制台，地址不变；验证后从 vault 删除（另行授权）。
- 订阅服务数据目录的属主调整为：控制台可写，订阅服务只读。

### 3.6 部署形态

- compose 项目 `reality-console`，根目录 `/opt/reality-console/`：`compose.yaml`、`data/`（导出的用户字段）、`registry/`、`db/`、`secrets/`（隧道 token）。
- 镜像 `reality-console`：
  - 基础镜像 `python:3.13-slim`，以 digest 固定；依赖（FastAPI、uvicorn、Jinja2、segno、PyYAML）以 `--require-hashes` 固定；
  - 复制 `console/` 与 `subs/`，复用后者的 catalog、链接与二维码代码；
  - 在控制端构建，标签取构建输入的哈希（与 `roles/subs_service` 相同）。
- 同一镜像跑两个服务，共用 SQLite（WAL）：
  - `web`：发布到宿主机 `127.0.0.1:8200`；
  - `report`：监听容器内 8201，只在内部网络，由 `cloudflared` 转发，不含任何管理页面路由。
- `cloudflared` 使用控制台自己的隧道（token 在 vault `vault_console_tunnel_token`），公共主机名 `report.taoziyoyo.com` → `http://report:8201`；
  不使用订阅服务的隧道，避免两个 compose 项目共用网络、互相阻塞移除与回滚。节点的 `edge_report_url` 为 `https://report.taoziyoyo.com/report`。
- 加固：非 root（UID 10002）、`read_only`、`cap_drop ALL`、`no-new-privileges`、pids 与内存上限、`json-file` 日志上界、健康检查。
- 新 playbook `console.yml`、`console-remove.yml`，新角色 `roles/console_service`。数据库每日备份到 `db/backup/`，保留 14 份。
- 管理员访问：`ssh -L 8200:127.0.0.1:8200 spt`，浏览器打开 `http://127.0.0.1:8200`。
- 改配置的操作（部署、升级、增删节点、改用户）第一阶段仍由管理员用 Ansible 命令行执行。第二阶段起由控制台在管理员在场时触发，
  不保存节点 root 凭据；需要无人值守生效的功能出现时，再评估节点拉取期望状态。

### 3.7 数据表

`tokens`（用户、token、发放与重置时间）、`nodes_state`（显示开关、最新上报快照、最后上报时间）、`report_seq`（去重）、
`traffic_daily`（日期、节点、用户、上行、下行）、`publish_log`、`audit_log`。

### 3.8 回滚

| 所处步骤 | 回滚方式 |
|---|---|
| 控制台已部署 | `console-remove.yml`；订阅数据保留最后一次发布内容，订阅服务继续运行 |
| 需要恢复 `subs.yml` 构建 | 从 Git 恢复 `subs.yml` 的汇总构建部分与 vault token 后重新运行 |
| 节点 reporter | 关闭 `edge_report_enabled` 后对该节点重新运行 `edge.yml`；`xray` 服务不受影响 |

## 4. 范围与非目标

- **范围**：
  - 控制台：`console/`（新包）、`docker/console/`、`roles/console_service`、`console.yml`、`console-remove.yml`、`group_vars/all/console.yml`、`tests/console/`；
  - 节点侧：`roles/xray_edge`（上报 token、注册文件、`reporter` 服务）、`roles/xray_edge/files/xray_edge_apply.py`（metrics 配置、上报 token）、
    `docker/edge-tools/`（上报脚本）、`edge-remove.yml`、`group_vars/all/edge.yml`、`tests/edge/`；
  - 订阅：`subs.yml`（删除汇总构建）、`roles/subs_service`（数据目录属主）、`group_vars/all/subs.yml`、`tests/subs/`；
  - 文档：`docs/operations.md`、`docs/project-memory.md`、S4 合同 §10；
  - 上线：`spt` 部署控制台；`dzire`、`usca`、`legend`、`kagoya` 部署 `reporter`。
- **非目标**：旧系统（single/multi、`deploy.yml`、monitor、Gist、旧订阅、`generate_user.py`）；登录与普通用户功能；用户增删改；
  控制台触发节点操作；其余节点部署新实例；流量配额；节点拉取期望状态；通知。

## 5. 前提、未知与关闭方式

1. **v26.3.27 的 `/debug/vars` 是否与 main 分支一致输出每用户计数**，以及键名与结构。关闭：本地 e2e。
   若结果相反：停止并回到本文评估是否改用 Xray API（须接受 §7 所述的权限扩大）。
2. **API 能否改到 `reporter` 访问不到的位置**：`reporter` 与 Xray 共用网络命名空间，仍能连到 `127.0.0.1:10085`，
   不用 API 只是代码层面的约束。若 v26.3.27 的 API 入站与 `xray api -s` 支持容器私有目录中的 Unix socket，则改用它，使隔离成为强制；
   不支持时接受代码层面的约束（§7）。关闭：本地测试。
3. **`network_mode: service:xray` 在 `xray` 使用 bridge 时的行为**，以及 `xray` 重建后 `reporter` 能否恢复（Compose 5.0.2–5.4.0）。
   关闭：本地重建测试；部署后在节点核对。
4. **Cloudflare 新隧道与上报主机名**。主机名已定（D-C4）。关闭：路由移到控制台自己的隧道、token 写入 vault，部署后无 token 请求返回 401。
5. **能否从只读挂载读取订阅服务的 WAL 模式 SQLite**。关闭：本地测试。若结果相反：由订阅服务定期导出拉取记录文件（需改 `subs/`）。
6. **`spt` 内存能否容纳 web、report、cloudflared**。关闭：本地测量；部署前只读检查可用内存。
7. **节点到 Cloudflare 的出站 HTTPS**。关闭：每台部署后确认上报到达。
8. **订阅服务数据目录属主调整时服务是否中断**。关闭：本地演练。

## 6. 验收标准

### 6.1 本地（控制端，无外部写入）

1. **单元测试**：
   - token 发放、重置、吊销；
   - catalog 只含显示中的节点，开放用户即有 token 的用户，内容不含私钥；
   - 上报鉴权：无 token 或错误 token 返回 401，节点 A 的 token 不能以节点 B 的名义上报；
   - 上报去重与流量汇总；计数变小时按 Xray 重启处理，不产生负值；
   - 注册文件的解析，以及对不合规文件的拒绝。
2. **监听隔离**：`report` 服务上不存在管理页面路由；`web` 只发布到 `127.0.0.1`。
3. **本地 e2e**：
   - 本地 Xray + `reporter` → 本地控制台：产生流量后，控制台显示该用户流量；
   - 发放 token 后，本地订阅服务能用该 token 取到订阅，并连通本地 Xray；吊销后返回 404。
4. **部署定义**：`docker compose config` 通过且加固项齐全；`console.yml`、`console-remove.yml`、`edge.yml`、`edge-remove.yml`、`subs.yml` 语法检查通过。
5. **回归与秘密**：`tests/edge`、`tests/subs` 全部通过；秘密扫描无 token 与私钥。

### 6.2 `spt`（另行授权）

1. `reality-console` 运行，只发布 `127.0.0.1:8200`；`/opt/reality-console` 之外没有新增文件、用户或 systemd 单元；
   monitor、宿主机 `cloudflared` 与订阅服务没有中断。
2. `test` 的 token 导入后地址不变，`test` 设备刷新订阅成功。
3. 上报主机名：无 token 返回 401，管理页面路径返回 404。
4. 容器重启后数据保留；数据库备份已生成。

### 6.3 节点（逐台授权：`dzire`、`usca`、`legend`、`kagoya`）

1. 部署后 Xray 因增加 metrics 配置重启一次（容器 ID 不变），之后不再重启；10 分钟内控制台显示该节点在线及其状态。
2. `test` 连接后，下一个上报周期内控制台出现 `test` 的流量，与节点上直接查询的结果量级一致。
3. 停止 `reporter` 15 分钟后首页提示离线；恢复后补报 `spool/` 中的数据，没有重复计数。
4. 在控制台隐藏、再显示一台节点，`test` 刷新订阅后该节点消失、再出现。

### 6.4 退出条件

§6.1–6.3 通过；管理员可以在控制台为真实用户发放地址（实际发放另行授权）。

## 7. 风险

- **数据集中**：控制台数据库保存全部已发放 token 明文，注册文件含节点上用户的 UUID。以只监听本机、非 root 容器、文件权限限制；
  `spt` 失陷即泄漏，与现状（vault 与口令文件在同一台主机）同级。
- **上报主机名公开**：只接受上报，每节点 token，请求大小与频率受限；某节点 token 泄漏，只能伪造该节点的上报数据。
- **管理页面没有登录**：只监听本机，经 SSH 隧道访问；不得发布到公网或隧道。
- **统计丢失**：Xray 重启会丢失上次读数之后的计数（最多一个间隔）；`spool/` 满时丢弃最旧数据，并在上报中标记。
- **`reporter` 与 Xray 共用网络命名空间**：除非 §5 第 2 项能把 API 改到私有 Unix socket，`reporter` 被攻破时仍可能调用 Xray API 增删本节点用户。
  以不带 API 工具、只读根文件系统、无能力位限制；用户集合每次部署由应用器核对。
- **订阅服务与控制台同机**：两者目前都在 `spt`；搬迁订阅服务需增加文件传输步骤（§3.5）。
- **订阅发布方式改变**：控制台接管后，`subs.yml` 不再构建订阅数据；回滚见 §3.8。

## 8. 操作者决定

2026-09-17 全部决定完毕，本文据此批准。

1. **D-C1 控制台成为订阅数据的唯一写入者**：`subs.yml` 只负责部署，`vault_subs_tokens` 迁入控制台（S4 合同变更）。
   **2026-09-17 已决定：同意**；两边以文件交接，保持订阅服务可搬迁（§3.5）。
2. **D-C2 新订阅只含控制台中显示的新实例节点**，不输出旧链接（S4 合同 §3.2 变更；新系统独立）。**2026-09-17 已决定：同意。**
3. **D-C3 token 明文存控制台数据库**，管理员随时可查看地址（S4 合同 §3.3 变更）。**2026-09-17 已决定：同意**；
   数据库与备份权限只归控制台，不进 Git（§3.5）。
4. **D-C4 上报用的公共主机名**。**2026-09-17 已决定：`report.taoziyoyo.com`**，由控制台自己的隧道转发到 `http://report:8201`（§3.6）。
5. **D-C5 新旧用户集合比较改为只提示**（S3 合同 §3.4 变更）。**2026-09-17 已决定：同意**，已记录在 S3 合同 §10，
   并在工作树实现（`roles/xray_edge/tasks/main.yml`，删除 `edge_drift_old_only_users`），尚未部署。

## 9. 授权边界

本文已于 2026-09-17 批准，只授权工作树内实现与 §6.1 本地验证（含本地构建镜像与本地 compose 运行）。以下各自单独授权：
Git 发布；Cloudflare 隧道与主机名（操作者执行）；vault 修改（写入隧道 token、删除 `vault_subs_tokens`）；
`spt` 上控制台部署与订阅服务数据目录调整；每台节点的 `reporter` 部署；向真实用户发放地址。
