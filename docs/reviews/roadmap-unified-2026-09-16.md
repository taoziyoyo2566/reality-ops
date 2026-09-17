# Reality Ops 统一建设与治理计划（2026-09-16 版）

Status: **DRAFT — 整体重建方向已由操作者选定；2026-09-16 起新系统作为独立产品建设（§2）。**

Updated: 2026-09-17 JST

取代：[`roadmap-unified-2026-08-27.md`](roadmap-unified-2026-08-27.md)（保留为历史版本，最后提交 `f55ac47`）。

## 0. 相对 2026-08-27 版的变化

| 项目 | 08-27 版 | 本版 |
|---|---|---|
| 总体方向 | 新数据面与新订阅建成后，把全部节点与现有用户迁到新系统，再删除旧系统 | 新系统（`xray_edge`、新订阅、管理控制台）作为独立产品建设；旧系统继续运行、不修改；不迁移旧用户；旧系统暂不删除（§2） |
| 近期实施 | S4 之后进入 S7 现网迁移 | S4 之后先做管理控制台第一阶段 P1（§7） |
| 进度查看 | 分散在各阶段说明与 S3 验收表中 | §7 开头新增“进度总览”，每个阶段一行 |
| 阶段结构 | 只有 S0–S8 | 新增控制台 P1–P4 与 S 阶段的对应关系，S1、S2 保留并标明哪个控制台阶段依赖它们（§7.0） |
| S5 进入条件 | 私有迁移映射已确认 | S1、S2、P1 完成，授权词汇已决定 |
| S7 | 现网迁移：切换订阅、迁移用户、使旧 Gist 与旧凭据失效 | 新系统扩展：按需在其余节点部署新实例并加入新订阅 |
| S4 订阅内容 | 未迁移节点输出旧链接，已迁移节点输出 443 链接 | 新订阅只包含新系统节点（D-C2，2026-09-17 决定，随控制台 P1 实施生效；§9 第 13 项） |
| C02–C04 | 随迁移与删除旧系统关闭 | 旧系统上不会因迁移关闭，处理方式待决（§9 第 12 项） |
| S3 验收表 | last-good 恢复、改用户、SOCKS5 成员变更为缺口或仅有单元测试 | 按 2026-09-15 本地 e2e 更新为“本地实测”，节点上仍未演练 |
| 未决决策 | 11 项 | 第 8、11 项更新；新增第 12–14 项 |
| 完成定义 | 全部节点与用户完成迁移，旧凭据失效 | 新系统在选定节点上运行，控制台可以发放、吊销并查看节点与流量（§11） |
| 新旧用户集合比较（S3） | 不一致即中止部署，另设例外名单 | 只提示差异、不中止，删除例外名单（D-C5，2026-09-17 批准，S3 合同 §10） |

## 1. 文档职责

本文是本项目未来工作、当前问题、实施顺序与验收边界的**唯一当前事实源**。本版取代 08-27 版，变化见 §0。

它取代工作区中曾并行存在的路线图、控制面/数据面 ADR、授权模型草案、传输协议调查、
复核报告和审前快照。那些材料包含多轮调查与相互推翻的中间结论，不再作为执行依据；
仍有长期价值的事实、约束和目标已经收敛到本文。已进入 Git 历史的旧资料保持历史原貌。

本文不是以下事实的证明：

- 不是部署记录，不表示任何仓库改动已经下发；
- 不是线上状态注册表，代码路径不等于节点当前行为；
- 不是 Git 发布授权，也不授权节点、Gist、DNS、镜像仓库或 GitHub 变更；
- 不保存真实凭据、完整订阅、私钥值或逐用户访问清单。

## 2. 已选方向与目标

操作者已选择：**以现有项目的功能与运维经验为参照，重建为控制面 / 数据面分离架构。**

**2026-09-16 方向（操作者决定）**：新系统——新数据面 `xray_edge`、新订阅服务与管理控制台——作为**独立产品**建设：

- 旧系统（single/multi、`deploy.yml`、monitor、Gist 与旧订阅）继续运行，不修改（决定 9）；
- 不迁移旧用户：旧订阅与旧实例照常服务，新系统的订阅由管理员逐个发放；
- 旧系统暂不删除，是否及何时删除另行决定（§9 第 11 项）；
- 新订阅只包含新系统节点（D-C2，2026-09-17 决定，随控制台 P1 实施生效；§9 第 13 项）；
- 近期实施由管理控制台承担，见 [`plan-console-phase1`](console/plan-console-phase1-2026-09-16.md)（DRAFT）；
  下述控制面 / 数据面分离是新系统的长期目标，各控制台阶段与之的对应见 §7.0。

目标不变式：

> 任意节点只服务当前有权访问该节点的用户；授权、到期和吊销在定义的时限内持续收敛，
> 而不是只在人工执行 Ansible 时求值一次。

目标结构：

```text
操作者 ──> 控制面：用户、计划、授权、到期、订阅、审计
                  │
                  │ 节点发起的认证签到；取得签名期望状态
                  ▼
              数据面 agent ──> 原子收敛 Xray，并回报已应用版本
                  │
                  └── REALITY 私钥在节点本地生成和保存，只上传公钥

用户 ──> 控制面订阅接口：每用户独立凭据、不可枚举、可吊销
```

Ansible 保留为 bootstrap、操作系统/基础依赖维护和 break-glass 恢复工具；Xray 用户与配置的
日常收敛由节点 agent 负责。agent 自身升级、控制面部署和平台维护的最终所有者必须在实施前
明确，不能用“Day-2 全部离开 Ansible”代替设计。

新系统上线新节点时，禁止把架构切换与 VLESS Encryption、地址族或 REALITY target 变更混成同一波。
单实例、443 与节点级 REALITY 密钥互为前提，视为同一变更单元（§4.1.3 决定 3）。XHTTP 在新
实例内作为独立的第二步验证（决定 4，2026-09-15 修订）。

2026-09-15 起，数据面以**新增的独立实现**落地，现有 single/multi 代码与节点上的旧实例保持不动（决定 7）。
新实现先由新的 Ansible playbook/角色管理，控制面与 agent 随后接管同一配置合同（决定 8）。

## 3. 证据与当前状态

### 3.1 证据标签

| 标签 | 含义 |
|---|---|
| `[代码]` | 当前仓库源码或配置可直接确认；不代表已部署 |
| `[实测]` | 在注明日期、主机和范围内实际观测；是有时效的快照 |
| `[上游]` | 官方文档、release 或实现；实施前须刷新 |
| `[推论]` | 基于证据得出的设计判断，仍需测试 |
| `[缺口]` | 尚未验证；不得写成已通过或当前线上事实 |

### 3.2 仓库与用户模型

- `[实测]` 2026-09-08：审查基线为 `ops@10f83120a1d9219da637046620c97dc67177c29e`。
- `[代码]` 当前 33 份用户档案中，17 份没有 `groups`，14 份含显式 `hosts`，0 份含
  `expire`，33 份含 `private_key`。不在本文列出用户姓名和逐人权限。
- `[代码]` `deploy.yml` 对缺少 `groups` 的用户使用 `['all']`，使其匹配所有普通节点组；
  `hosts` 又以 `match_group or match_host` 绕过档位矩阵。这不是可持续的授权模型。
- `[代码]` monitor agent 仅在 `monitor_enabled` 为真并实际部署后才有每分钟 cron；
  不得据模板声称 inventory 中的节点都在签到。
- `[代码]` 当前 `/report` 是单向遥测写入，agent 只判断 HTTP 状态码，不读取期望状态。
  将其变成控制回路是新协议和高权限行为，不是“复用现有通道”即可自动获得的能力。

### 3.3 节点与控制端快照

- `[实测]` 2026-08-29：18 台节点中，`spt` 本地可达；装入正确 ssh-agent 后另有 4 台
  可达；6 台缺凭据，6 台 TCP 不可达，`hk-hn` 缺 SSH 映射。该结果是历史快照，接管前
  必须重新探测。
- `[实测]` 2026-09-08 当前控制端：`/tmp/reality_build` 仍有 31 份 `0644` 的
  `config.json`，全部含 `privateKey` 字段，父目录为 `0755`。本次只读核查未读取或输出值。
- `[实测]` 2026-09-14 复查：`/tmp/reality_build` 增至 128 份 `config.json`（ams、dcc、dzire、hk01、
  hk02 五台 multi 节点的构建输出），文件 `0644`、目录 `0755`，最新写入为当日 22:49。
- `[实测]` 2026-09-14 只读探测 `spt`（本机）、`dzire`、`usca`（操作者授权，仅输出汇总值）：

  | 节点 | 形态 | 用户数 | Docker | 镜像 / Xray | 443 监听 | 全局 IPv6 |
  |---|---|---|---|---|---|---|
  | `spt` | single，bridge | 25 | 29.3.1 | `xray-docker:latest` / 26.3.27 | 无 | 有 |
  | `dzire` | multi，32 个容器，bridge | 32 | 29.7.2 | `xray-docker:latest` / 26.3.27 | 无 | 无 |
  | `usca` | single，bridge | 32 | 29.6.0 | `xray-docker:latest` / 26.3.27 | 无 | 有 |

  三台都已运行新仓库镜像，与 `docs/project-memory.md`（2026-08-29“尚未部署到任何节点”）不一致，
  以本快照为准；镜像仍是浮动 `latest`，C12 的固定 digest 与持续对账要求不变。
- `[实测]` 2026-09-15 00:00 JST 只读快照，12 台可达节点。仅输出汇总值：

  | 节点 | 形态 | 容器 | Docker | 镜像 ID | 443 | IPv6 | 根分区 | 最大日志 | 专用轮转配置 |
  |---|---|---|---|---|---|---|---|---|---|
  | `dzire` | multi | 32 | 29.7.2 | `fd502666d1a9` | 无 | 无 | 9% | 0.5MB | 有 |
  | `ams` | multi | 25 | 29.1.3 | `32ea3020b8e7`（RepoDigest `fd502666`） | 无 | 有 | 62% | 75.8MB | **无** |
  | `dcc` | multi | 23 | 28.0.1 | `32ea3020b8e7`（RepoDigest `fd502666`） | 无 | 有 | 54% | 39.7MB | 有 |
  | `hk01` | multi | 25 | 29.3.1 | `fd502666d1a9` | 无 | 有 | 30% | 36.4MB | 有 |
  | `hk02` | multi | 23 | 29.3.1 | `fd502666d1a9` | 无 | 有 | 14% | 34.9MB | 有 |
  | `netcup` | single | 1 | 29.7.2 | `fd502666d1a9` | 无 | 有 | 14% | 8.3MB | **无** |
  | `legend` | single | 1 | 29.2.0 | `fd502666d1a9` | 无 | 有 | 75% | 154.8MB | 有（当日才下发，尚未首次轮转） |
  | `jp05` | single | 1 | 29.6.2 | `fd502666d1a9` | 无 | 有 | **96%（2GB 盘）** | 55.2MB | **无** |
  | `jp10` | single | 1 | 29.6.0 | `fd502666d1a9` | 无 | 有 | 74% | 110.7MB | **无** |
  | `kagoya` | single | 1 | 29.7.1 | **`b891c9781882`** | **nginx 占用** | 有 | 66% | 26.9MB | **无** |
  | `usca` | single | 1 | 29.6.0 | `fd502666d1a9` | 无 | 有 | 25% | 85.4MB | **无** |
  | `spt` | single | 1 | 29.3.1 | **`b891c9781882`**（本地 `latest` 已指向 `fd502666`，容器未重建） | 无 | 有 | 64% | **740.0MB** | **无** |

  `[推论]` 经典镜像存储以配置 digest 作为镜像 ID，containerd 镜像存储以 index digest 作为 ID，
  因此 `ams`/`dcc` 的 ID 不同但 RepoDigest 与其余节点一致；两台所用存储类型未直接查询。全部可达节点 Xray 版本为
  26.3.27，容器重启计数为 0。除 `jp05` 设有 `max-size 5m` 外，容器均未设 Docker 日志上界。
- `[实测]` 2026-09-15：`dzire` 宿主机 `resolv.conf` 首个 DNS `4.2.2.4` 丢包 50–70%，REALITY 每次握手解析
  target 时 Go 解析器等待 5 秒，旧 multi 与新实例的新连接握手均约 5.3 秒。新实例已按节点固定容器 DNS
  修复（`group_vars/all/edge.yml` `edge_container_dns`）；**旧实例与宿主机 DNS 未改**，按决定 9
  只观察，如需修复宿主机 DNS 另行评估。其余 11 台可达节点 DNS 无丢包。
- `[缺口]` 容器 json 日志实际大小、monitor agent crontab 需 root 读取，未取得；
  DNS、客户端兼容与订阅行为未刷新。

## 4. 当前待处理问题

以下只保留仍影响当前安全或未来架构的事项。历史上已修复但未部署的行为仍按
“仓库已修 / 线上未知”区分。

| ID | 优先级 | 问题 | 下一步与关闭条件 |
|---|---|---|---|
| C00 | P1 | 数据面形态待重设计：single/multi 双实现；每用户高位端口与每用户私钥不符合上游 443 建议；Xray 配置按用户整段拼接；SOCKS5 存在两套模型 | 见 §4.1。决策已于 2026-09-14 全部作出（§4.1.3）；按“单实例 + 443 + `conf.d` 分层配置”冻结数据面配置合同，S1 schema 以此为准 |
| C01 | P0 | 控制端 `/tmp/reality_build` 有 world-readable 私钥配置残留（§3.3） | `[代码]` 2026-09-14 multi 构建已改为每主机 `tempfile` 私有目录（`0700/0600`），`always` 中删除；本地探针验证成功、失败注入与 `--tags config` 均无残留，尚未在真实 multi 部署中验证。`[实测]` 2026-09-14 旧残留已经授权清理，`/tmp/reality_build` 不存在。剩余：下一次 multi 部署后确认控制端无 `reality_build_*` 残留 |
| C02 | P0 | 33 份用户私钥位于已跟踪档案，仓库历史已暴露 | 2026-09-15 操作者决定不另做全量重签：新架构只保存节点本地私钥，订阅只输出公钥，新系统不含每用户私钥。2026-09-16 起不迁移旧用户、旧系统暂不删除：**旧系统上本项不会因迁移关闭**，已暴露的旧私钥持续有效；处理方式见 §9 第 12 项 |
| C03 | P0 | 订阅链路共享 token、可枚举用户 ID、运行态密钥为空时代码层 fail-open | 新订阅服务直接返回正文；每用户独立不可枚举凭据；缺密钥拒绝；A 凭据不能取 B 内容；凭据可单独轮换与吊销。新系统已按此实现（S4）；旧订阅链路不迁移，**旧系统上本项仍未关闭**（§9 第 12 项） |
| C04 | P0 | Gist ID 已公开，发布失败/跳过可能静默，旧用户文件不会自动删除，token 会进入输出 | 新系统不使用 Gist，也不以任何 ID 作为安全边界。旧 Gist 继续服务旧用户，**旧系统上本项仍未关闭**；若按 §9 第 12 项处置：旧 Gist 失效而非仅轮换，发布失败必须非零退出，日志中不再出现 token |
| C05 | P0 | single/multi 的 Xray 配置权限可暴露 REALITY 私钥；递归赋权扩大暴露面 | 控制端与节点形成 owner/group/mode 合同；容器仍可读；日志目录不受误伤；single/multi canary 起停通过 |
| C06 | P0 | 文件日志与 Docker stdout 缺可靠上界；`maxsize` 仅在 logrotate 被调用时检查；7 台节点未下发轮转配置（§3.3），`spt` 单文件 740MB，`jp05` 根分区 96%（主因不是日志） | 旧系统按决定 9 不修改：2026-09-15 数据显示 `spt` 日志所在分区剩余约 39GB，风险可接受，只观察。新实例的容器定义必须自带文件日志定时轮转（不依赖系统每日 logrotate）与 Docker 日志上界，作为 U1 合同项验收；`jp05` 磁盘另行排查 |
| C07 | P0 | 现有 ACL 默认放行，档位与能力混用，显式 hosts 旁路；到期没有执行者 | 建立 `grade + features + 有时限例外` 的默认拒绝模型（S1）；新系统用户的授权在私有清单确认；沿用现有用户档案时，新授权与现有 ACL 逐项 diff |
| C08 | P1 | 拉取式控制回路缺安全合同 | 明确 enrollment、节点身份、包签名、密钥轮换、抗重放/降级、版本单调性、原子应用、last-good 与审计；完成威胁模型和负向测试 |
| C09 | P1 | “离线继续 last-good”与到期/吊销时限冲突 | 定义期望状态 TTL、节点本地到期执行、最大失联时间和超时后的 fail-safe；不得承诺无条件“≤1 分钟” |
| C10 | P1 | agent 位于 `docker` 组，具备高权限；升级和失陷恢复无所有者 | 最小化权限与可执行动作；定义 agent 更新、回滚、凭据撤销、控制面失陷和节点隔离流程 |
| C11 | P1 | 节点运行态和可达性不完整 | 部署前重新生成带日期的只读快照；未探测节点保持 gap。原 `[test_nodes]` 四台已于 2026-09-14 退役并移出 inventory，`hk-hn` 已改名 `hk01` 并有 SSH 映射；服务器已由操作者确认退掉，控制端订阅缓存已清理，Gist 已于 2026-09-14 23:56 JST 重新生成 |
| C12 | P1 | 镜像消费有漂移风险：`spt`、`kagoya` 仍运行 `b891c9781882`，其余可达节点为 `fd502666`（§3.3）；项目期望镜像与节点实际运行镜像没有持续对账 | 旧系统按决定 9 不修改，继续使用浮动 `latest`。新实例使用独立镜像变量并固定 digest（候选：`v26.3.27` = `sha256:fd502666…`，2026-09-15 于 Docker Hub 核对，amd64/arm64；`community.docker` 5.1.0 已验证可解析 digest 引用），拉取任务设 `force_source: true`。剩余：持续解析项目引用并采集节点实际 Image ID/RepoDigest/Xray 版本；canary 验证 geodata、坏配置、UID、健康检查和回滚；将节点标记为 `current`、`stale`、`wrong_repo`、`wrong_version`、`unreachable` 或 `unknown` |
| C13 | P1 | 订阅 URI/JSON/YAML 多处手工拼接，fragment 未按上游分享标准编码 | 建立单一 canonical node 与 renderer；使用标准序列化；golden 明确允许的编码修复；输出不含秘密 |
| C14 | P1 | 现有 Xray 配置仍缺 DNS/sniffing 等能力，multi stats 与监控模型不一致 | 在新系统中按独立功能阶段处理；先定义合同与测试，禁止与控制面切换同批 |
| C15 | P1 | Apple/iCloud REALITY target 有上游风险；现有 preflight 使换 target 必然改 SNI 和订阅 | 逐节点选择与探测；用并行入口 canary；记录 H2/ALPN、非跳转域名、网络位置、订阅 diff 与回滚 |
| C16 | P1 | 测试基础不足以支撑安全变更 | 建立授权纯函数、schema、renderer、agent 收敛、订阅鉴权、两个 core config check 和失败注入测试 |
| C17 | P1 | 客户端侧真实 IP 泄漏：分流直连后按会话关联、WebRTC/STUN 与 QUIC 走 UDP 绕过系统代理、IPv6 绕过、App 自带网络栈不读系统代理、本地 DNS 暴露地区；节点看不到未到达它的流量 | 由 U5 处理（§7.1）：订阅下发防泄漏客户端配置，节点保证 UDP 与 IPv6 出口能力、DNS 不带用户子网；不可解决项（手机号/SIM/定位/系统地区/浏览器指纹/用户自选分流）在用户说明中明示 |

### 4.1 C00：数据面形态、443 与 Xray 配置结构

调查日期 2026-09-14。上游证据基于 Xray-core main 分支与 v26.3.27（当前唯一非预发布版），
实施前须重新核对。

#### 4.1.1 结论

**1. 合并 single/multi，每节点只运行一个 Xray 进程。**

- `[上游]` `HandlerService` 的 `adu`/`rmu`（入站增删用户）与 `adi`/`rmi`（增删入站）在运行中
  生效，不需重启。Xray 没有配置热重载（`main/run.go` 只处理 SIGINT/SIGTERM）；API 改动只在
  内存，必须同时落盘，否则重启后丢失。
- `[上游]` 用户流量计数器在连接建立时按 email 动态注册（`app/dispatcher/default.go`），API
  新增的用户同样计数；Vision splice 路径会把字节计入用户计数器（`proxy/proxy.go`），但在连接
  结束时一次性计入，按分钟采样会出现锯齿，总量不丢。
- `[上游]` `RemoveUser` 只删除认证表，`RemoveInbound` 只关闭监听；已建立连接不被主动断开，
  要立即踢出只能重启进程。
- `[推论]` 统计口径会变化：Xray 计代理载荷，不含 TLS/TCP 开销与探测流量；multi 现按容器
  `eth0` 字节计（`roles/monitor/templates/agent.py.j2` `get_multi_stats`），迁移后数值偏小属口径差异。
- `[实测]` 2026-09-14，用户数相同（各 32 人）的两台节点对比：

  | 指标 | `usca`（single，1 个容器） | `dzire`（multi，32 个容器） |
  |---|---|---|
  | 容器 cgroup 内存（`docker stats`） | 15.0MiB | 合计 408.7MiB（每个 10.5–15.2MiB） |
  | xray 进程 RSS | 40.2MB | 合计 1230.7MB（每个约 38.5MB） |
  | `containerd-shim` RSS | 1 个，16.5MB | 32 个，合计 501.8MB |
  | `docker-proxy` 进程 | 65（32 用户 × IPv4/IPv6 + API） | 32（无全局 IPv6） |
  | 主机已用内存 | 660MB / 1431MB | 1603MB / 3921MB |

  `[推论]` RSS 含跨进程共享的只读页，合计值偏高，不能直接相加作为节省量；按主机已用内存差估算，
  同等用户数下 multi 多占约 0.9GB，两台主机基线不同，只作量级参考。改为 443 后只需发布一个端口，
  `docker-proxy` 进程也随之从每用户一到两个降为固定少数。
- 代价：失去按容器 `tc` 限速（`speedlimit.sh`；Xray policy 没有按用户限速字段）、按用户内存/
  进程上限与进程级故障隔离。限速已由操作者确认不需要（§4.1.3 决定 1）。

**2. 业务入站统一监听 443。**

- `[上游]` v26.3.27 release note：“非 443 端口、‘偷苹果’极易导致服务器 IP 被封锁”，并对 REALITY
  入站未单独监听 443、SNI 含 apple/icloud 输出启动警告；main 分支已把警告扩展到 microsoft 与
  `.ru`/`.ir`/`.cn`。
- `[代码]` 现状每用户一个高位端口；默认 target 为 apple；`hk01` 用 icloud。
- 连带变化：每节点一个 443 入站，因此必然是单实例；REALITY 私钥改为每节点一把（与 C02、§6
  第 6 条一致），用户只以 UUID 区分；按用户 SOCKS5 路由从 `inboundTag: user-<name>` 改为
  `user` 条件；全部订阅链接变化。

**3. 建议一次定型的设计项（上线仍按 §4.1.3 决定 3 分变更单元）。**

target 迁离 apple/icloud（C15，独立变更单元）；XHTTP 回落入站布局（新实例内第二步，§4.1.3 决定 4）；用户档案去掉 `port`、`private_key`、
`public_key`；email 统一为 `<用户名>.<节点名>`；镜像固定到不低于 v26.3.27 的 digest（C12）；监控改走 API 并启用
`statsUserOnline`（C06、C14）；清理 `alterId`、`expire`，`dest` 改为 `target`。

#### 4.1.2 Xray 配置结构目标

现状问题 `[代码]`：

- single 模板为每个用户复制一整段 inbound（约 30 行，每段含私钥），SOCKS5 路由依赖每用户
  inbound tag；
- multi 为每个用户生成一份完整配置，SOCKS5 仍使用旧 `reality_socks5` 模型（`servers` 数组），
  与 single 的 `socks5_egress` 模型（扁平 `address/port/user/pass`）不一致；
- `alterId` 与 `expire` 不是 Xray VLESS 字段。

设计依据 `[上游]`：

- `xray-docker` entrypoint 在 `/config.json` 不存在时使用 `XRAY_CONFDIR`（默认
  `/etc/xray/conf.d`），并在启动前执行 `-test`；
- `-confdir` 合并规则（`infra/conf/xray.go` `Override`）：`log`、`api`、`policy`、`stats`、
  `routing`、`dns` 等由后读文件**整体覆盖，不合并**；inbound/outbound 同 tag 整体替换，否则
  追加（outbound 默认前插，文件名含 `tail` 才追加到末尾）；
- `xray api adrules` 默认**替换全部**路由规则，`-append` 才追加；`ado`/`rmo` 增删出站；
- 路由 `user` 条件支持精确 email 与 `regexp:`；同一规则内不同条件为 AND，同一条件多值为 OR；
- `network` 接受 `raw`（与 `tcp` 等价）；REALITY 接受 `target`（`dest` 仍兼容）。

目标布局：每节点一个配置目录，只读挂载到 `/etc/xray/conf.d`，不再挂载单文件
`/config.json`。按变更频率拆分：

| 文件 | 内容 | 触发变更 | 生效方式 |
|---|---|---|---|
| `00-base.json` | `log`、`api`（Handler/Stats/Routing/Logger）、`stats`、`policy`（统计开关、`statsUserOnline`） | 很少 | 重启 |
| `10-inbounds.json` | `api` 入站（`127.0.0.1:10085`）；`vless-reality` 443 入站：REALITY 节点参数、`clients` 每用户一行、`sniffing.routeOnly` | 用户增删改 | 落盘 + 按差异 `adu`/`rmu`；REALITY 参数变更需重启 |
| `20-outbounds.json` | `direct`、`blocked`、每个在本节点生效的 `socks5-<profile>` | SOCKS5 profile 增删 | 落盘 + `ado`/`rmo` |
| `30-routing.json` | 全部规则，每条带 `ruleTag`，末条显式兜底到 `direct` | 路由或 SOCKS5 成员变更 | 落盘 + `adrules` 整体替换 |

路由整体覆盖，因此只能存在一个含 `routing` 的文件；末条兜底规则让 outbound 的前插/追加顺序
不再影响默认出口。

示意（占位值，不是可部署配置）：

```jsonc
// 10-inbounds.json
{
  "inbounds": [
    { "tag": "api", "listen": "127.0.0.1", "port": 10085,
      "protocol": "dokodemo-door", "settings": { "address": "127.0.0.1" } },
    {
      "tag": "vless-reality",
      "port": 443,
      "protocol": "vless",
      "settings": {
        "decryption": "none",
        "clients": [
          { "id": "<uuid>", "email": "<user>.<node>", "flow": "xtls-rprx-vision", "level": 0 }
        ]
      },
      "streamSettings": {
        "network": "raw",
        "security": "reality",
        "realitySettings": {
          "target": "<site>:443",
          "serverNames": ["<site>"],
          "privateKey": "<node-local>",
          "shortIds": ["<sid>"]
        }
      },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"], "routeOnly": true }
    }
  ]
}
```

```jsonc
// 30-routing.json
{
  "routing": {
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      { "type": "field", "ruleTag": "api", "inboundTag": ["api"], "outboundTag": "api" },
      { "type": "field", "ruleTag": "block-bt", "protocol": ["bittorrent"], "outboundTag": "blocked" },
      { "type": "field", "ruleTag": "block-private", "ip": ["geoip:private"], "outboundTag": "blocked" },
      { "type": "field", "ruleTag": "socks5-<profile>", "user": ["<user>.<node>"], "domain": ["<domain>"],
        "network": "tcp", "outboundTag": "socks5-<profile>" },
      { "type": "field", "ruleTag": "default", "network": "tcp,udp", "outboundTag": "direct" }
    ]
  }
}
```

SOCKS5 接入规则：

- 保留 `socks5_egress` 的 profile/route 模型与 D12 单一门控：profile 完整、主机命中且至少有一个
  路由条件时，才同时生成 outbound 与规则；multi 旧 `reality_socks5` 迁入该模型；
- 每个 profile 一条规则，按 `priority` 排序；`route.users` 映射为 `user`，`domains`/`ips`/
  `protocols`/`network` 原样映射；同时配置 users 与 domains 时语义为 AND，须在 schema 中写明；
- 成员变更：重渲染 routing 后 `adrules` 整体替换。新增 profile：先 `ado` 再 `adrules`。删除
  profile：先 `adrules` 再 `rmo`；
- 出站统一使用扁平 `address/port/user/pass` 格式；凭据只出现在 `20-outbounds.json`，文件权限按 C05。

其他约束：

- **标识**：沿用当前 single 设计，email 为 `<用户名>.<inventory 节点名>`；multi 的
  `@<domain_suffix>` 后缀随 multi 退役。monitor 已按此格式截取用户名（`agent.py.j2` 按 `@`、`.`
  截断），因此用户名不得包含 `.` 或 `@`，须由 schema 校验；
- **密钥**：REALITY 私钥在节点本地生成（`xray x25519`），只存在于节点的 `10-inbounds.json`，控制端只保存
  公钥；过渡期 Ansible 在远端渲染，不得落入控制端 `/tmp/reality_build`（C01）；
- **应用顺序**：写候选目录，执行 `xray run -test -confdir <候选>`，原子替换文件，调用 API，再用
  `inbounduser`/`lsi` 核对；失败保留 last-good；
- **XHTTP 在新实例内分两步（决定 4，2026-09-15 修订）**：第一步只上 443 RAW + REALITY +
  Vision 并完成验收；第二步在同一新实例新增本地监听的 `vless-xhttp` 入站，使用同一组 clients，
  由 443 入站回落到它（`[推论]` 依据上游讨论 #4113：“REALITY 加回落……同一 path 抵达同一
  XHTTP 入站”），回落条件与真实客户端支持在该步 canary 中验证。两步都通过后才把该节点加入新订阅。
  期望状态与配置渲染按“承载用户的入站列表”同步 clients，不写死单个入站；
- **`[推论]` 体积**：20 个用户时，single 现模板约 700 行；新结构约 140 行，其中每个用户 1 行。

#### 4.1.3 决策

2026-09-14 已决定：

| 编号 | 决定 | 后果 |
|---|---|---|
| 1 | 不需要按用户限速 | 合并单实例不再被限速阻塞；`speedlimit.sh` 随 multi 退役，不设替代方案 |
| 2 | 接受每节点共用一把 REALITY 私钥 | 用户只以 UUID 区分；用户档案与期望状态不再包含每用户 `private_key`/`public_key`；C02 重签按节点密钥执行 |
| 3 | 按并行变更切换（§2 分波约束已于 2026-09-15 按决定 4、7 修订） | 依据见下方“切换方式”；变更单元、步骤与回滚归 §7.1 |
| 4 | ~~XHTTP 只预留~~ 2026-09-15 修订：XHTTP 在新实例内分两步 | 先验收 443 RAW + REALITY + Vision，再在同一新实例加 XHTTP 回落入站；两步通过后才迁移用户（§4.1.2） |
| 5 | 容器保持 bridge 网络 | 不采用 host 网络；依据见下方 |
| 6 | email 沿用 `<用户名>.<节点名>` | 见 §4.1.2“标识” |

2026-09-15 追加决定：

| 编号 | 决定 | 后果 |
|---|---|---|
| 7 | 代码层面也按并行变更：新数据面以新增的独立实现落地，不修改现有 single/multi | 现有角色、共用变量、`deploy.yml` 与节点上的旧实例保持原样，回滚即停用新实例；全部节点完成 contract 后删除旧的两套实现。隔离要求见下方 |
| 8 | 新实例先由新的 Ansible playbook/角色管理 | 数据面不再等待控制面 S2；配置渲染按期望状态合同设计，控制面与 agent 以后复用同一合同接管（§7） |
| 9 | 旧系统在迁移完成前不做止血修改 | 已提交的 C01 修复保留；C06、C12 等在新实例中实现，旧系统只观察；§9 第 9 项关闭 |

2026-09-16 方向调整对上表的影响：决定 4 的“两步通过后才迁移用户”改为“两步通过后才把该节点加入新订阅”；
决定 7 的“全部节点完成 contract 后删除旧的两套实现”不再成立，旧系统暂不删除（§9 第 11 项）；决定 9 不再以迁移完成为期限，
旧系统保留期间都不做修改。

**新实现的隔离要求（决定 7）** `[代码]` 2026-09-15 核对：

- 容器名不得以 `reality_` 开头：single 角色会删除除 `reality_core` 外的全部 `reality_*` 容器
  （`roles/reality_single/tasks/main.yml:62-75`），multi 角色在无授权用户时与 `reset.yml` 会删除全部
  `reality_*` 容器；
- 使用独立的数据/日志目录与独立 compose 项目：multi 的 compose 项目目录为 `/opt/reality/data`，
  并设 `remove_orphans: true`；
- API 不得占用宿主机 `127.0.0.1:10085`（single 的 `reality_core` 已占用）；
- monitor agent 把 `reality_*`（非 `reality_core`）容器当作 multi 用户统计，旧 monitor 不改，
  测试期间新实例流量与在线 IP 直接经 Xray API 查询；
- 订阅脚本按 `<用户>_<节点>.json` 解析缓存文件名；测试期间新链接只对测试用户输出，输出方式单独设计；
- 用户与 ACL 计算目前内联在 `deploy.yml` pre_tasks；新 playbook 复用时需在“临时复制”与
  “抽出共享文件（碰旧文件但不改行为）”之间选择；
- 节点前提：`kagoya` 的 443 被 nginx 占用；`jp05` 内存 469MB，新旧并行的资源占用须预检。

**切换方式（决定 3）**

依据 `[上游]`：Martin Fowler 的 Parallel Change（expand/migrate/contract）先让接口同时支持
新旧两版，逐步迁移使用者，最后移除旧版；Google SRE Workbook《Canarying Releases》建议多项
功能逐项启用，并以更小、自包含的发布单元降低回滚成本。

`[代码]` 用户客户端使用 `SUBS_BASE_URL/<用户名>` 订阅地址（`generate_subs_gist.py`），地址不随
节点端口、公钥或 target 变化。因此节点侧参数变化会随客户端刷新订阅生效，不需要用户手动操作。
只有 C03 的每用户订阅凭据会改变订阅地址，那是唯一需要用户手动操作的切换。

据此确定的变更单元、执行步骤、观察与回滚归 §7.1。

`[缺口]` 客户端实际刷新订阅的频率，以及长期不拉取订阅的用户数量，尚未统计（需读取 `spt` 上
monitor 数据库的 `subscription_logs`，当前账号无权限）。

**容器网络（决定 5）**

- 此前提出评估 host 网络是本轮推论，不是 Xray 官方建议。`[上游]` XTLS 官方镜像
  （`.github/docker/Dockerfile`：distroless nonroot，`-confdir`）没有网络模式要求；社区面板中
  Marzban 与 Remnawave node 的 compose 使用 `network_mode: host`，原因是面板会动态创建入站
  端口；3x-ui 当前 compose 采用端口发布。
- 本设计固定 443，XHTTP 走本地 fallback，不需要动态端口，host 网络的主要理由不成立。
- `[实测]` 2026-09-14 `spt`、`dzire`、`usca`（Docker 29.3.1 / 29.7.2 / 29.6.0，均为 bridge）：
  容器内 `net.ipv4.ip_unprivileged_port_start=0`，UID 10000 加 `cap_drop: ALL` 可在容器内绑定
  443，无需额外能力。`spt` 与 `usca` 最近各 5000 行 access log 中，用户流量来源全部为公网
  IPv4（4938 / 4413 行），`127.x` 来源只出现在 API 入站（monitor 查询），没有被替换成 Docker
  网关地址，bridge 保留了客户端 IPv4 源地址。`dzire` 的 multi 日志对 SSH 账号不可读，未取样。

#### 4.1.4 缺口与关闭条件

- 已补：multi 资源占用、443 占用、Docker 版本与容器内低端口绑定条件，于 2026-09-14 在 `spt`、
  `dzire`、`usca` 实测（见 §3.3、§4.1.1、§4.1.3）。
- 已补（2026-09-15，§3.3）：12 台可达节点的 443 占用与 Docker 版本；`kagoya` 的 443 被 nginx
  占用，U1 预检不通过，需先决定该节点的 443 处置；`dcc` 为 Docker 28.0.1，其余 29.x。
- 已补（2026-09-15，`xray_edge` canary）：bridge 网络下 IPv6 客户端源地址**被保留**——`usca` 新实例
  发布 `[v6]:443`，控制端经 IPv6 与 IPv4 各自连接后，访问日志记录的来源分别等于控制端公网 IPv6 与
  IPv4，无 Docker 网关或 ULA 地址；新旧实例并行资源：`usca`（1 核）新实例约 10MiB、旧 `reality_core`
  约 75MiB，`dzire` 新实例约 15MiB。真实客户端：操作者已在 `dzire` 以 RAW+Vision 链接实测正常；XHTTP 真实设备兼容已于同日补测（§7 S3 验收状态）。
- `[缺口]` 删除用户后旧连接的
  保持行为；客户端订阅刷新频率与长期不拉取订阅的用户数量（需读取 `spt` 上 monitor 数据库，当前
  账号无权限）。以上均未实测。
  Vision 回落 XHTTP 按决定 4 在新实例第二步验证。
- 关闭条件：以 synthetic fixtures 生成 golden 配置并通过
  `xray run -test -confdir`；staging 验证 API 增删改用户、SOCKS5 成员变更、重启后与落盘状态
  一致，以及用户流量与在线 IP 可取。

#### 4.1.5 参考资料

查证于 2026-09-14。Xray-core 源码链接固定到当时 main 的
`c412e77a9b712082ac9ebf27fa793951cb5a7d85`，实施前须按 §8 重新核对。

Xray 官方：

- v26.3.27 release note（非 443 与 apple/icloud 警告）：
  https://github.com/XTLS/Xray-core/releases/tag/v26.3.27
- 引入端口与 SNI 警告的提交：
  https://github.com/XTLS/Xray-core/commit/157e65b34d32363528088c592d4e415d84f01a63
- SNI 警告当前实现：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/infra/conf/transport_security.go
- 用户增删 API 定义：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/app/proxyman/command/command.proto
- `xray api` 子命令（`adu`/`rmu`/`adi`/`rmi`/`ado`/`rmo`/`adrules`/`inbounduser`/`statsonlineiplist` 等）：
  https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/main/commands/all/api
- 用户计数器注册：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/app/dispatcher/default.go
- splice 路径计入用户计数器：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/proxy/proxy.go
- 删除用户与关闭入站不断开已有连接：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/proxy/vless/inbound/inbound.go 、
  https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/proxy/vless/validator.go 、https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/app/proxyman/inbound/worker.go
- 信号处理（无热重载）：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/main/run.go
- `-confdir` 合并规则：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/infra/conf/xray.go
- policy 字段（无按用户限速）：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/infra/conf/policy.go
- 路由 `user` 条件：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/infra/conf/router.go 、https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/app/router/condition.go
- 官方镜像：https://github.com/XTLS/Xray-core/blob/c412e77a9b712082ac9ebf27fa793951cb5a7d85/.github/docker/Dockerfile
- REALITY 说明：https://github.com/XTLS/REALITY/blob/5dabb073f8e86d87dd283420947098cef171aaf1/README.md
- XHTTP 讨论（REALITY 回落到 XHTTP 入站）：https://github.com/XTLS/Xray-core/discussions/4113

镜像与社区：

- 本项目镜像 entrypoint（`XRAY_CONFDIR`）：
  https://github.com/taoziyoyo2566/xray-docker/blob/4cb6f10bfb9c93efe50f265262cee1c17bd9f819/docker-build/entrypoint.sh
- Marzban compose（host 网络）：
  https://github.com/Gozargah/Marzban/blob/7f396db3e703d71a28060bc9ce4a532ec64cb1f4/docker-compose.yml
- Remnawave node compose（host 网络）：
  https://github.com/remnawave/node/blob/44912631321664dbd5822e9bf8d96766ccff7c93/docker-compose-prod.yml
- 3x-ui compose（端口发布）：
  https://github.com/MHSanaei/3x-ui/blob/768bbd2a292edd79bb231e409e1e58efb9eccbd6/docker-compose.yml

变更方法：

- Martin Fowler, Parallel Change：https://martinfowler.com/bliki/ParallelChange.html
- Google SRE Workbook, Canarying Releases：https://sre.google/workbook/canarying-releases/

### 4.2 已确认的历史修复

以下不再进入未来功能计划，但部署状态仍须按节点验证：

- 删除无效 `connLimit`；
- 修复审计日志路径、用户名解析和采样窗口；
- 修复 single SOCKS5 outbound 与 routing 门控不一致，并有 7 场景回归；
- 镜像内部的 geodata 与重复层问题已在独立 `xray-docker` 项目修复，但本项目节点是否已消费
  新镜像仍归 C12。

## 5. 授权模型目标

商业等级与技术能力分开：

```text
user.grade >= node.min_grade
AND user.features 包含 node.requires
AND 当前时间位于授权有效期内
AND 没有优先级更高的显式拒绝
```

约束：

- 无 plan、节点无 `min_grade`、未知 feature 或无效时间必须 fail closed；
- 例外授予必须包含对象、原因、批准者、起止时间和审计记录；
- 从现有 ACL 转换时，不能用一个标量 grade 假装复现现有非阶梯式访问范围；应先生成精确的临时节点 allowlist，
  与操作者确认后再转译为 plan、features 和例外；
- “近 90 天无流量”不能直接等同于可回收。使用情况必须同时验证遥测覆盖、订阅访问、最后成功
  上报和操作者确认；查询必须从完整用户集合左连接流量数据，不能漏掉零记录用户；
- 逐用户档位、使用状态和例外清单属于敏感运营数据，不进入公开版本化文档。

## 6. 目标控制回路合同

在进入节点实现前，至少冻结以下合同：

1. **节点身份**：一次性 enrollment 换取唯一凭据；凭据可轮换、吊销并绑定节点。
2. **期望状态**：节点专属、不可变、带单调版本、签发时间、到期时间、内容 hash 和签名。
3. **抗回退**：节点拒绝旧版本、错误节点包、过期包、无效签名和不受支持 schema。
4. **授权时效**：节点本地执行用户授权到期；控制面失联不允许已到期授权无限延续。
5. **收敛事务**：先验证与构建候选配置，再原子切换；失败保留 last-good 并回报原因。
6. **密钥所有权**：REALITY 私钥由节点生成并持久化；控制面只接收对应公钥和版本。
7. **最小权限**：agent 只能执行已定义的收敛动作；不得把任意控制面响应直接交给 shell。
8. **可观测性**：回报期望版本、已应用版本、最后成功时间、失败类别和健康状态，不回传秘密。
9. **升级与恢复**：明确 agent/control-plane 的升级者、回滚包、break-glass 与失陷密钥轮换。

## 7. 分阶段实施顺序

每阶段独立 review、验证和回滚。DRAFT 状态不授权执行。

**进度总览**（更新于 2026-09-17；某阶段有进展时只改对应一行，证据见各阶段计划与 [`docs/project-memory.md`](../project-memory.md)）：

| 阶段 | 状态 | 说明与下一步 |
|---|---|---|
| S0 止血与事实基线 | 基本完成 | C01 剩最后一次确认（下一次旧 multi 部署后）；按决定 9 旧系统不改，实际搁置 |
| S1 授权模型 | 未开始 | 先由操作者决定 §9 第 1–3 项（档位、能力、到期语义）；P2 之前完成 |
| S2 控制面与 agent | 未开始 | 工作量最大；先决定 §9 第 5–7 项；P3 之前完成 |
| S3 单节点数据面 | 完成 | canary 验收通过；last-good 恢复、改用户、SOCKS5 变更只在本地测过，按大小轮转未触发（见下方 S3 验收表） |
| S4 订阅服务 | 已上线，仅 `test` | 剩 `legend` 与 XHTTP 节点真机、Clash 两种模式导入、隐私模式泄漏检测；Shadowrocket 的 WebRTC 泄漏留作后续（§7.2） |
| P1 控制台第一阶段 | 完成（2026-09-18） | [`plan-console-phase1`](console/plan-console-phase1-2026-09-16.md) 2026-09-17 批准；§6.1 本地验收通过；已部署并接管订阅发布，4 台节点上报正常，现场演练通过；结果见 [Changelog](console/console-phase1-2026-09-18.changelog.md) |
| S5 授权生命周期 | 未开始 | 依赖 S1、S2、P1 |
| S6 遥测与运维 | 未开始 | P1 的节点上报、离线提示与备份是起步部分 |
| S7 新系统扩展 | 4/12 台 | 已部署 `dzire`、`usca`、`legend`、`kagoya`；其余 8 台逐台授权，`jp05` 磁盘不足 |
| S8 功能演进 | 出站竞速试点中 | `usca`、`legend` 已启用；“IPv4 变慢时选 IPv6”尚未观察到 |
| P2–P4 控制台后续阶段 | 未计划 | 依赖关系见 §7.0 |

2026-09-15 按决定 8 调整顺序：S3 数据面新实例先由新的 Ansible 实现落地并验证，不等待 S2；
S2 控制面与 agent 完成后接管同一配置合同。下表进入条件已按此修订。

2026-09-16 按方向调整（§2）：

- S4 之后先做**管理控制台第一阶段 P1**（下表新增行；控制台各阶段见 §7.0）；
- S7 由“现网迁移”改为“新系统扩展”；
- S5 的进入条件改为 S1、S2、P1 完成；S8 的进入条件由“S7 稳定”改为“新系统稳定运行”。

| 阶段 | 目标 | 进入条件 | 退出门槛 |
|---|---|---|---|
| S0 止血与事实基线 | 处理 C01；完成不扩大行为面的日志、秘密输出和本地构建修复；刷新仓库/节点清单 | 对每个主机或外部动作单独授权 | 无控制端明文残留；秘密扫描通过；带日期的节点 gap 清单完成 |
| S1 测试与领域模型 | 建立用户、节点、授权、期望状态 schema 与纯函数；建立 synthetic fixtures | 授权词汇与数据边界已评审；C00 待决项已决定 | 默认拒绝、时间边界、例外、序列化和负向测试通过；fixtures 无真实值 |
| S2 控制面安全合同 | 实现节点注册、身份、签名状态包、版本/TTL 与审计 API | §6 合同冻结 | 单元、集成、重放、降级、过期、错节点、错签名和密钥轮换测试通过 |
| S3 单节点数据面 | 以新增的独立 Ansible 实现（决定 7、8）在一台节点完成本地密钥、`conf.d` 配置与原子收敛；先 443 RAW+Vision，再加 XHTTP | canary 节点已定（`dzire` → `usca`，§9 第 4 项）；实施合同 [`plan-single-instance-443`](data-plane-edge/plan-single-instance-443-2026-09-15.md) 已评审批准 | 配置检查、起停、重启、坏配置、last-good 和回滚通过；与旧实例并行运行且旧实例无任何变化；API 增删改用户、SOCKS5 成员变更、日志轮转与 Docker 日志上界、固定 digest 通过；XHTTP 步骤真实客户端连接通过 |
| S4 订阅服务 | 每用户鉴权后直接渲染 raw VLESS；不依赖 Gist；按 U5 下发防泄漏客户端配置（§7.2）。订阅包含的节点：现行实现按节点状态输出旧链接或 443 链接；按 D-C2（2026-09-17 决定），控制台 P1 实施后只包含新系统节点。2026-09-16 状态：`spt` 上线，仅 `test`，真机导入与连接通过 | canonical node/public key 链路稳定；实施合同 [`plan-subscription-service`](subscription-service/plan-subscription-service-2026-09-15.md) 已批准（Docker Compose 形态） | A 不能取 B；吊销后内容不可取；无公开落点；日志和响应不泄密 |
| P1 管理控制台第一阶段 | 管理员发放、重置、吊销订阅；查看节点状态、日志与流量；控制节点是否出现在订阅中（§7.0） | S4 已上线；[`plan-console-phase1`](console/plan-console-phase1-2026-09-16.md) 已批准（2026-09-17） | 该计划 §6.4 |
| S5 授权生命周期 | 到期、吊销、计划变更持续收敛（新系统） | S1、S2、P1 完成；授权词汇（§9 第 1–3 项）已决定 | 在线 SLA 与离线 TTL 均达标；节点与订阅同时撤销；审计完整 |
| S6 遥测与运维 | 流量/IP/健康并入认证通道；建立项目期望镜像与节点实际运行镜像的持续对账；补升级、备份、恢复与告警 | S3–S5 稳定；镜像状态字段和 Registry 查询来源已冻结 | 监控不成为授权依据；每个节点报告容器镜像引用、Image ID/RepoDigest、Xray 版本、运行状态、重启次数和检查时间；tag 相同但 digest 不同能标记为 `stale`；错误仓库、版本不符、不可达和数据不完整不会误报为 `current`；控制面恢复演练和 agent 回滚通过 |
| S7 新系统扩展（2026-09-16 前为“现网迁移”） | 按需在其余节点部署新实例（与旧实例并行、旧实例不动），加入新订阅 | 目标节点预检通过（443 空闲、Compose 插件、磁盘与内存）；每台节点单独授权 | 目标节点完成新实例验收并出现在新订阅中；不可达节点明确列为 gap |
| S8 功能演进 | Mihomo、IPv4/IPv6、target、metrics、VLESS Encryption（XHTTP 已按决定 4 移入 S3） | 新系统稳定运行；每项独立批准 | 按真实客户端/core 矩阵逐项验收；不得用解析成功代替真实连接 |

**S3 验收状态（2026-09-15 收尾）**：canary 验收完成，S4 设计可以开始；下表 `[缺口]` 项不阻塞 S4，但须在 S7 向其余节点扩展前关闭或由操作者确认接受。
逐项证据见 [`docs/project-memory.md`](../project-memory.md) “xray_edge Canary State”。

| 退出门槛 | 状态 | 依据 |
|---|---|---|
| 配置检查、坏配置 | `[实测]` | `dzire`：无效 SOCKS5 路由被 `xray run -test` 拒绝，`conf.d` 与容器不变；本地 e2e 覆盖且错误输出屏蔽私钥 |
| 起停、重启、回滚 | `[实测]` | `dzire`、`usca`：容器重启后用户一致；`edge-remove.yml`（保留密钥）后重新部署，公钥与 `conf.d` 相同 |
| last-good 自动恢复 | `[实测]` 本地 | 2026-09-15 compose 形态本地 e2e：运行时才失败的变更使 apply 失败，`conf.d` 恢复为 last-good，排除故障后运行 last-good；节点上未演练 |
| 与旧实例并行、旧实例无变化 | `[实测]` | `dzire`（原 multi）、`usca`（原 single）、`legend`（原 single）部署前后旧实例指纹一致 |
| API 增删用户、不重启 | `[实测]` | `dzire`、`usca`：33 → 34 → 33 经 API，`conf.d` 恢复逐字节一致；本地 e2e 验证被删用户连接失败 |
| API 改用户（UUID/short_id 变更） | `[实测]` 本地 | 2026-09-15 本地 e2e：换 UUID 经 API 不重启，旧 UUID 被拒；换 short_id 重启后可连；节点上未演练 |
| SOCKS5 成员变更 | `[实测]` 本地 | 2026-09-15 本地 e2e：SOCKS5 出口在两个用户间切换，出口日志证实；canary 节点无 SOCKS5 路由，节点上未执行 |
| 日志轮转与 Docker 日志上界 | 部分 `[实测]` | 三台 `json-file` `max-size 10m`/`max-file 3`、只读根文件系统、`cap_drop ALL`；轮转服务手动与定时运行成功；`dzire` 2026-09-16 00:29 UTC 首次自动按天轮转并压缩 `[实测]`；`[缺口]` 按大小（50M）轮转未触发，保留 14 份上限需满 14 天后才能观察 |
| 固定 digest | `[实测]` | 三台镜像均为 `fd502666` |
| XHTTP 真实客户端 | `[实测]` | `dzire` XHTTP 设备实测（ipleak 无泄漏）；`usca` 控制端 Xray/Mihomo 6/6；操作者设备导入 `usca`、`legend` 链接可连 |

其他已确认：bridge 网络保留 IPv4/IPv6 客户端源地址；`usca` 1 核下新实例约 10–24MiB；2026-09-15 约 18:00 JST 只读日志汇总三台
重启 0、错误日志仅启动记录，流量全部来自 `test`（尚未向真实用户发放新订阅）。

### 7.0 管理控制台各阶段与 S 阶段的对应

| 控制台阶段 | 内容 | 依赖 | 状态 |
|---|---|---|---|
| P1 第一阶段 | 管理员发放、重置、吊销订阅；查看节点状态、日志与流量（节点单向上报）；控制节点是否出现在订阅中 | S3、S4（已上线）。P1 自带 S6 的起步部分：节点单向上报、离线提示、数据库备份 | 完成（2026-09-18），见 [`plan-console-phase1`](console/plan-console-phase1-2026-09-16.md) |
| P2 第二阶段 | 用户增删改与暂停（管理员手动）；控制台在管理员在场时触发节点操作（部署、升级、重启、新增、下线）与任务记录；按需在其余节点部署新实例（S7） | **S1**：授权模型先定，否则界面上编辑的仍是现有 ACL 规则 | 未计划 |
| P3 第三阶段 | 到期时间一到自动停用、节点离线时限；节点主动拉取签名期望状态 | **S2、S5** | 未计划 |
| P4 第四阶段 | authentik OIDC 登录；普通用户查看自己的订阅与流量 | P1；`kagoya` 上 authentik 恢复公网入口 | 未计划 |

- P1 不实现 S1 的授权模型、S2 的节点身份与签名下发、S5 的到期自动生效。S2 仍是工作量最大的一项。
- 节点管理方式：改配置走推送（管理员在场时触发 Ansible，控制台不保存节点 root 凭据），查看数据走节点上报；
  需要无人值守生效的功能（P3）时，转为节点拉取期望状态。
- 到期自动停用默认放在 P3；如需在 P2 提供，须把 S2 提前（§9 第 14 项）。

### 7.1 数据面切换方式（C00 决定 3）

> 2026-09-16 起，本节的 migrate 与 contract 步骤不执行（不迁移旧用户，旧系统暂不删除，§2）。
> 预检与 expand 仍用于在节点上部署新实例（S7）；其余内容保留为设计记录。

依据见 §4.1.3。节点侧端口、公钥、target 变化随客户端刷新订阅生效；只有 C03 的订阅地址/凭据
切换需要用户手动操作。

**变更单元**

| 单元 | 内容 | 阶段 |
|---|---|---|
| U1 数据面 | 单实例、443、节点级 REALITY 密钥、`conf.d` 配置、API 用户管理、日志轮转与 Docker 日志上界、固定 digest（互为前提，不可拆分） | S3 验证，S7 逐台执行 |
| U4 XHTTP | 同一新实例内 443 fallback 到本地 XHTTP 入站 | S3 在 U1 验收后验证；S7 中按节点需要启用 |
| U2 target | 迁离 apple/icloud 等高风险 target（C15） | 节点部署新实例后单独 canary |
| U3 订阅凭据 | 每用户订阅凭据与新订阅地址（C03） | S4 实现；新系统用户由管理员发放（P1） |
| U5 客户端防泄漏配置 | 订阅按客户端类型下发完整配置（Mihomo profile、v2rayN、Shadowrocket），提供“隐私/分流”两种模式；节点保证 UDP 转发与（有能力节点的）IPv6 出口，DNS 由节点解析且不配置 `clientIp`（C17，§7.2） | S3 canary 做可行性验证；S4 实现；可选自检页随 S6 |

**单节点步骤（U1）**

1. **预检**：443 是否被占用、Docker 版本、磁盘与内存余量、bridge 下 IPv4/IPv6 源地址是否保留；
   任一项失败则该节点停止，记为 gap。
2. **expand**：用新增的独立实现与旧容器并行启动新实例（443，满足 §4.1.3 隔离要求）；旧的每用户
   高位端口继续服务，旧代码与旧实例不做任何修改；两边使用同一 UUID 与 email（`<用户名>.<节点名>`）；
   新实例流量经 Xray API 采集，旧 monitor 不改。新实例先验收 RAW+Vision，再启用 XHTTP 回落。
3. **migrate**：订阅对该节点只输出 443 链接；客户端刷新后迁移；用 `/subs/logs` 找出长期未拉取
   订阅、可能手工导入链接的用户，单独通知。
4. **观察**：新实例各用户有流量，旧实例各用户流量持续为零；观察时长由操作者按节点批准。
5. **contract**：2026-09-15 决定迁移期间不删除旧实例与旧代码；2026-09-16 起不执行本步骤（§2、§9 第 11 项）。
   旧系统保留期间：旧高位端口持续暴露，旧的每用户私钥持续有效（C02），小内存节点长期并行两套实例；
   已从用户档案移除、但仍留在旧实例中的用户（如 `shuaiqi`）继续可用，`edge.yml` 每次部署会提示这些差异（D-C5）。

**回滚**

| 所处步骤 | 回滚方式 |
|---|---|
| expand | 停止并删除新实例；旧实例未受影响 |
| 已加入新订阅 | 在订阅中移除或隐藏该节点并重新发布；新实例可随后删除 |

**canary 顺序**

2026-09-15 修订：新实例不改旧实例（决定 7），canary 直接使用生产节点：`dzire`（原 multi）→ `usca`
（原 single，覆盖 IPv6）→ 其余节点分批。每台节点独立执行与回滚；前一台未完成验收或出现未解释的
异常时，不开始下一批。S3 实施合同见
[`data-plane-edge/plan-single-instance-443-2026-09-15.md`](data-plane-edge/plan-single-instance-443-2026-09-15.md)。

### 7.2 U5：客户端防泄漏配置（C17）

2026-09-15 操作者决定纳入路线图。依据：网站获取设备真实 IP 的常见手段多数发生在流量未到达节点时
（分流直连、UDP/IPv6 绕过、App 不读系统代理、本地 DNS），节点侧无法单独拦截；订阅服务下发的客户端
配置决定这些流量是否经过节点。

合同：

1. **下发完整配置**：按客户端类型输出完整配置而不只是 `vless://` 链接；Mihomo 为完整 profile（TUN 接管、
   `strict-route`、`fake-ip` 远端解析、UDP 与 IPv6 经代理、sniffer），v2rayN 与 Shadowrocket 输出各自可导入的配置或规则。
2. **两种模式由用户选择**：隐私模式（全部经节点，含国内服务）与分流模式（国内服务直连，暴露真实 IP 但降低风控）；
   默认模式与说明文字须在 S4 前由操作者确定。
3. **节点能力**：节点必须转发 UDP（VLESS UDP，含 WebRTC STUN 返回节点地址）；有全局 IPv6 的节点提供 IPv6 出口，
   订阅按节点能力生成 IPv6 规则；DNS 由节点解析，Xray DNS 不配置 `clientIp`。
4. **不可解决项明示**：手机号、SIM、定位、WiFi、系统地区、浏览器指纹、账号历史与用户自选分流不属于网络层，
   在用户说明中写明。
5. **可选自检页**：控制面提供检测页，对比经节点 IP、WebRTC、IPv6 与 DNS 解析器，随 S6 评估。
6. **二维码与深链接导入**（2026-09-15 操作者决定）：每个用户的订阅地址同时提供二维码与深链接——Clash 类客户端为
   `clash://install-config?url=<个人订阅地址>`，v2rayN 与 Shadowrocket 为订阅地址或 `vless://` 链接的二维码；隐私与分流
   模式各自对应独立地址。二维码与深链接包含个人凭据，只在鉴权后的用户页面展示，随 U3 凭据轮换失效。

验收：隐私模式 profile 在真实设备上以 ipleak 类检测逐项确认（IPv4、IPv6、WebRTC、DNS）只出现节点侧地址；
STUN 经节点返回节点公网地址；分流模式下行为与说明一致。未在真实设备执行的项保持 gap。

可行性验证（2026-09-15，canary，仅测试用户与控制端本机，未改线上订阅）：

- `[实测]` STUN 经 SOCKS5 UDP 转发：Xray-core 与 Mihomo v1.19.31 客户端经 `dzire` 的 Vision 与 XHTTP，
  STUN 服务器看到的均为 `dzire` 公网地址；不经代理时为控制端地址。节点 UDP 转发满足合同第 3 项。
- `[实测]` 以测试用户生成 Mihomo 隐私模式 profile（TUN `mixed` + `strict-route`、`fake-ip` + `respect-rules`、
  `ipv6: true`、sniffer、`MATCH,PROXY`），`mihomo -t` 通过。控制端无 root，关闭 TUN、其余不变时：IPv4 出口与
  STUN 均为节点地址；`usca` 可经节点访问仅 IPv6 目标，`dzire`（无 IPv6 出口）访问仅 IPv6 目标失败且不回退直连；
  本地 DNS 返回 fake-ip（`198.18.0.0/16`）。
- `[缺口]` TUN 接管、真实设备上的 ipleak 类检测、v2rayN 与 Shadowrocket 的等价隐私配置均未验证。2026-09-15 操作者决定：
  隐私 profile 的真实设备验证与二维码导入推迟到 S4 订阅服务实现后进行；临时发布方式（Tailscale 一次性下载、
  Cloudflare 快速隧道）不采用，快速隧道尝试被本地权限检查拦截后已撤回，期间无下载。
- `[缺口]` 2026-09-17 Shadowrocket 全局模式下，ip125.com 的 WebRTC 检测对 Vision 与 XHTTP 链接都显示设备的真实 IPv4；
  测试期间节点上没有 STUN 请求，即 STUN 由设备直接发出，客户端原因未定。后续：先调查原因与各客户端可行的做法，
  选定稳定可靠的方案后再决定实现方式；方案不得关闭 WebRTC 或影响设备的其他功能，上线前须在真机逐项实测。
  下一条 09-15 的结论与此矛盾，不作依据。
- `[实测]` 2026-09-15 操作者以二维码导入 `dzire` 的 XHTTP 链接在真实设备上使用，ipleak.net 未显示泄漏；节点日志中该时段
  测试用户 479 条连接里 434 条经 `vless-xhttp`，含 185 条 ipleak.net 与 5 条 UDP，确认检测流量经节点。

## 8. 验证原则

- 本地：格式解析、类型/静态检查、模板渲染、Xray `run -test`、Mihomo `-t`、单元与失败注入；
- 安全：secret scan、日志脱敏、权限、A/B 用户隔离、吊销、过期、重放、错签名和降级；
- staging：新旧实例并行、原 single/multi 两种节点形态、重启/断网/坏配置/回滚、真实客户端连接；
- 发放与扩展：沿用现有用户档案时新授权与现有 ACL 的 diff、节点应用版本、订阅内容核对；
- 任何未执行的网络、Docker、DNS、节点或客户端检查必须记为 gap，不能以静态检查替代；
- 上游 Xray/Mihomo 版本、兼容边界和 digest 在每次实施前重新从官方来源核对，本文不维护“最新”值。

## 9. 未决决策

以下问题会改变实现合同，未决定前不得假设：

1. grade 是商业档位还是路由偏好；最终保留几档；
2. features 词汇与节点 `requires`，以及现有显式 hosts 哪些是有意例外；
3. 到期功能、时区、边界语义和离线最大延续时间；
4. ~~新的测试节点从哪里来~~：2026-09-15 决定新实例 canary 直接用生产节点，第一台 `dzire`（原 multi，
   用户少），第二台 `usca`（原 single，有 IPv6）；原 `[test_nodes]` 四台已于 2026-09-14 退役；
5. 控制面部署形态、备份/恢复目标和高可用需求；
6. enrollment、签名算法、根密钥保管和轮换方式；
7. agent 升级由 Ansible、签名包还是其他机制负责；
8. ~~S7 中 C03 订阅地址/凭据切换是否与节点迁移放在同一通知窗口~~：2026-09-15 决定不同窗、先发地址；
   2026-09-16 起不迁移旧用户，本项不再适用；新订阅是否输出旧链接以第 13 项为准；
9. ~~旧系统在 S7 前允许做哪些止血修复~~：2026-09-15 已决定不做修改（§4.1.3 决定 9）。
10. U5：2026-09-15 决定用户页面**默认分流模式**，隐私模式并列提供；各客户端配置由 S4 订阅服务渲染。是否提供自检页仍随 S6 评估。
11. 是否删除旧 single/multi 实例、旧代码与旧每用户私钥：2026-09-15 决定迁移期间不删除；2026-09-16 操作者表示不急于删除，
    旧系统继续运行且不修改。何时删除待操作者决定。
12. 旧系统的遗留 P0 风险（C02 已暴露的每用户私钥、C03 共享订阅 token 与可枚举 ID、C04 公开 Gist）不再能通过迁移关闭：
    是否接受为长期残余风险，或对旧系统做最小处置（例如轮换共享 token、更换 Gist ID），需操作者决定；决定前按决定 9 不改旧系统。
13. 管理控制台第一阶段的 D-C1～D-C5（[`plan-console-phase1`](console/plan-console-phase1-2026-09-16.md) §8）：2026-09-17 全部决定（D-C2：新订阅只包含新系统节点；D-C4：上报主机名 `report.taoziyoyo.com`），该计划已批准。
14. 到期自动停用放在哪个控制台阶段：默认 P3（需 S2、S5）；如需在 P2 提供，须把 S2 提前（§7.0）。

**S8 功能：出站 IPv4/IPv6 竞速（2026-09-15 操作者决定，先 canary）**

- `[上游]` Xray `sockopt.happyEyeballs`（RFC 8305，仅 TCP，需 `domainStrategy` 非 `AsIs`，官方建议 `UseIP` 配合
  `interleave`）；采用推荐值 `tryDelayMs 250`、`prioritizeIPv6 false`、`interleave 1`、`maxConcurrentTry 4`。默认 `AsIs`
  下 Go 按 RFC 6724 排序，容器 ULA IPv6 使 IPv4 恒排第一、300ms 后才尝试 IPv6。
- 实现：`group_vars/all/edge.yml` `edge_happy_eyeballs_nodes` 按节点开关，期望状态 `egress.happy_eyeballs`，
  `direct` 出站加 `sockopt`；属 `20-outbounds` 变更，应用时重启。canary 节点 `usca`、`legend`（均有 IPv6 出口；操作者认为 `legend` 的 IPv6 质量优于 IPv4）。
- `[实测]` 2026-09-15 节点：`usca`、`legend` 已启用，经节点 IPv4-only、IPv6-only 目标分别走对应地址族，双栈目标 `usca` 4/4、
  `legend` 6/6 选 IPv4；`legend` 部署前自测两族到常见站点连接均 0–3ms，节点侧看不出 IPv6 优势，操作者感知的差异可能在客户端到节点一段。
  `[缺口]` 日志级别 `warning` 不记录出站地址族，“IPv4 变慢时选 IPv6”仍未观察到，需要另定观察方法。
- `[实测]` 本地：`xray run -test` 通过；A/B 在仅 IPv4 与开启 IPv6 的 Docker 网络中，开启后 IPv4-only、IPv6-only、
  双栈目标行为与关闭时一致，双栈在两族均快时选 IPv4。`[缺口]` “IPv4 变慢时选 IPv6”需节点侧长期观察，本地未模拟。
- 风险：同一用户对同一网站的出口地址族可能变化，部分网站会话与 IP 绑定；只对有 IPv6 出口节点有意义。

## 10. 风险与回滚边界

- 新旧系统并存，旧系统暂不删除（§9 第 11 项）；保留期间不得修改旧入口与旧配置（决定 9）；
- 单个 canary 只改变一个轴；架构、image、address、target、transport、encryption 分开；单实例、
  443 与节点级密钥是同一变更单元 U1，XHTTP 是新实例内的独立第二步 U4（§7.1）；
- 新实现不得修改旧 single/multi 代码、共用变量或旧实例；发现必须改动旧部分时停止并单独评审；
- 旧实例保留期间，旧每用户私钥持续有效（§9 第 12 项）；
- 节点应用必须保留 last-good，但 last-good 不能绕过用户到期和状态包 TTL；
- 镜像回滚使用已记录 immutable digest，不使用 `latest` 猜测上一版；
- target 回滚只能回到仍安全可用的并行入口；
- DNS、Gist、节点、镜像仓库、GitHub 和用户通知是独立外部变更，各自单独授权；
- 秘密一旦进入公开历史，删除当前字面量不能恢复私密性，必须轮换并使旧值失效。

## 11. 总体完成定义

本计划只有在以下全部成立后才能标为 `COMPLETE`：

1. C00–C17 均关闭，或有操作者接受的明确残余风险与所有者（2026-09-16 起，C02–C04 在旧系统上的残余风险须按 §9 第 12 项明确）；
2. 控制面和数据面合同、测试、升级与恢复路径完成；
3. 新系统在操作者选定的节点上运行，新系统用户经新订阅使用，管理控制台可以发放、吊销并查看节点与流量；
   旧系统的去留不在本项范围（§9 第 11 项）；
4. 授权、到期、吊销在在线与离线场景中按合同收敛；
5. 原 single/multi 形态节点上的新实例与真实客户端矩阵通过，未执行项仍明确为 gap；
6. 最终变更形成独立 Changelog；Git 发布、部署与外部变更状态分别陈述。

## 12. 授权边界

| 动作 | 本文是否授权 |
|---|---|
| 继续评审和工作树内实现 | 需按具体请求确认；本文 DRAFT 本身不授权 |
| Git stage/commit/push/PR/merge | 否，必须单独授权 |
| 删除控制端 `/tmp/reality_build` 或修改主机配置 | 否，必须先给出具体主机变更简报并授权 |
| 连接节点、部署、重启服务 | 否，必须逐次明确目标与 `--limit` |
| Gist、DNS、镜像仓库、GitHub 等外部写入 | 否，必须单独授权 |
| 读取、输出或迁移真实秘密 | 否；验证应使用不暴露值的方式 |

## 13. 本次资料收敛记录

2026-09-08：将工作区中未提交的两份 ADR、传输协议调查、复核报告和两份审前快照的有效
结论并入本文；移除逐轮纠错日志、重复计划、失效链接和逐用户敏感访问表。旧 P0–P5 与
Phase −1–7 不再是并行执行路线，统一改由本文 S0–S8 管理。未执行 Git 发布、部署、节点或
外部系统变更。

2026-09-16：按操作者方向决定（新系统为独立产品、不迁移旧用户、旧系统暂不删除）另立本版，取代 08-27 版，变化见 §0。
