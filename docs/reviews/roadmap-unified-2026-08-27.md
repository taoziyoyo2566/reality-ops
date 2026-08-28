# Xray 栈统一 Roadmap

Created: 2026-08-27 JST
Author: 本文由一次仓库调查 + 上游实测合并而成，证据链见 §6。

## 合并来源

本文合并三个来源，取代它们各自单独作为计划依据：

| 来源 | 最后更新 | 轴向 | 处置 |
|---|---|---|---|
| `todo.md` | 2026-06-06 | 配置层功能丰富 | 内容并入 §4 P3；原文件建议清理（`decommission.yml:91` 有引用需同步改） |
| `docs/reviews/roadmap-xray-xhttp-ipv6-2026-08-25.md` | 2026-08-26 复核 | 交付层现代化 | Phase 1/2/3/4 并入 §4 P2/P4；原文件建议加"已被本文取代"横幅 |
| 2026-08-27 实测调查 | 本次 | 缺陷发现 | §2 全部 |

**合并动因**：前两份计划的交集为零。在旧 roadmap 中检索
`geoip|geosite|sniffing|policy|广告|分流` 零命中；唯一的 DNS 命中是
Phase 3 的 A/AAAA 记录，与 `todo.md` 要求的 DoH/DoT 无关。一份管功能不管交付，
一份管交付不管功能，而 §2 的缺陷两份都未覆盖。

---

## 0. 证据约定

本文每条断言标注来源等级。**未标注的推论不得当作事实使用。**

| 标记 | 含义 |
|---|---|
| `[实测]` | 本轮用真实二进制 / 本地 docker build / 活 API 验证过，复现命令见 §6 |
| `[代码]` | 仓库内 `file:line`，可直接核对 |
| `[上游]` | 官方文档或 GitHub，附链接与查证日期 |
| `[缺口]` | **无法验证**。明确标为未知，任何情况下不得当作已通过 |

实测环境：Xray-core `v26.3.27` (`d2758a0`, go1.26.1 linux/amd64)，
Docker 29.7.1，构建平台 `linux/amd64`。

---

## 1. 前提声明（重要）

**仓库代码 ≠ 线上运行代码。** 由操作者于 2026-08-27 声明：最近几次提交
未重新部署到节点。因此：

- 本文 §2 中标 `[代码]` 的条目描述的是**仓库当前状态**，不代表节点行为。
- 本文 §2 中标 `[实测]` 的条目独立于部署状态，均成立。
- 任何关于"节点上正在发生什么"的判断，一律归入 §3 缺口。

---

## 1.5 项目边界（2026-08-28 更新）

镜像构建与发布已迁出本仓库，成为独立项目
[`taoziyoyo2566/xray-docker`](https://github.com/taoziyoyo2566/xray-docker)
（GitHub 公开、MIT、默认分支 `main`、`DOCKERHUB_*` secrets 已配置、CI 全绿；
本地 `~/workspace/projects/xray-docker` 工作树干净且与 `origin/main` 同步）。
历史由 `reality-ops@b0d44cb` path-filter 迁移，镜像那条线的沿革完整保留。

**本仓库自此只是镜像的消费者。** 归属划分：

| 事项 | 归属 |
|---|---|
| Dockerfile、entrypoint、构建/审计脚本、发布流水线、tag 契约、Docker Hub Overview | `xray-docker` |
| `xray_image` 变量、拉取与更新策略、容器运行参数、配置模板、订阅与监控 | 本仓库 |
| tag 语义或镜像行为变更的告知 | `xray-docker` 发布，本仓库消费 |

**已在新仓库完成、但对节点尚未生效的项**（`[实测]` 2026-08-28 读取新仓库 8-27 提交内容）：

| 项 | 状态 |
|---|---|
| D1 geodata → `/usr/local/share/xray` + `ENV XRAY_LOCATION_ASSET` | 新镜像已修 |
| D2 单层 `install`，不再 copy-up 36MB 二进制 | 新镜像已修 |
| entrypoint 支持 `-confdir`、启动前 `run -test` | 已实现 |
| `HEALTHCHECK`（`XRAY_HEALTH_PORT` 未设则不探测） | 已实现 |
| README 同步到 Docker Hub Overview | 已实现 |
| revision tag 方案 → `build-fingerprint.sh` | 已替换，`XRAY_IMAGE_REVISIONS.json` 在新仓库已废弃 |

这些修复**只存在于新镜像里**。节点当前拉的仍是旧仓库镜像，D1/D2 在线上依旧成立，
其关闭以 P2-c 的迁移验收为准——**不得凭"新仓库已修"记为已通过**。

**双发布者状态（`[实测]` 2026-08-28 GitHub API / Docker Hub API 快照；已于 2026-08-29 止血）**：

| | `taoziyoyo2566/xray_docker`（旧） | `taoziyoyo2566/xray-docker`（新） |
|---|---|---|
| 发布者 | 本仓库 workflow，每日 cron，8-27 15:28 最后一次由 schedule 触发；**2026-08-29 已手工停用**（见 P2-a） | 新仓库 workflow |
| `latest` | 2026-08-26 | 2026-08-27 |
| tag 数 | 16（含 `stable` / `stable-previous` 等遗留） | 13 |
| 累计拉取 | 2236 | 990 |
| 谁在消费 | `group_vars/all/main.yml:3` → 全部节点 | 无 |

在停用之前，上游一旦发布新版本，两条流水线会各自构建，并推向两个不同的 Docker Hub
仓库。P2-a 已于 2026-08-29 执行，旧发布者不再产出新 tag；工作流文件本身仍在
`ops` 上，由 P2-b 删除后仓库状态与实际启停才重新一致。

---

## 2. 已验证的缺陷

### D1 — geodata 靠巧合工作，路径不标准

| 项 | 内容 |
|---|---|
| 现象 | 镜像已含 `geoip.dat` / `geosite.dat`，但落在 `/usr/bin/`，且 `XRAY_LOCATION_ASSET` 未设 |
| 证据 | `[实测]` Xray release zip 内含 `geoip.dat` 19,768,301 B、`geosite.dat` 10,491,954 B，另有 `LICENSE`、`README.md` |
| | `[代码]` `docker-build/dockerfile:28` — `unzip "/tmp/${XRAY_ASSET}" -d /usr/bin/` 把全部内容倒进 `/usr/bin` |
| | `[实测]` Xray 按**可执行文件所在目录**解析资产：dat 与 xray 同目录、cwd=`/` → 加载 170624 条域名规则；移走 dat → `failed to open file: geosite.dat: open <exedir>/geosite.dat: no such file or directory` |
| | `[上游]` 官方文档称搜索顺序为 `./`（当前目录）→ `/usr/local/share/xray` → `/usr/share/xray`，**与实测不符**，以实测为准 |
| 影响 | 正面：`todo.md` 第 1 项"需要对应数据文件"的前提**其实已满足**。负面：路径非标准、无法挂载覆盖、数据随上游 release 冻结、`LICENSE`/`README.md` 污染 `/usr/bin` |
| 归属 | P2 |

### D2 — 镜像层重复，体积多 32%

| 项 | 内容 |
|---|---|
| 现象 | 本地构建 154MB，修正后 104MB（amd64，未压缩） |
| 证据 | `[代码]` `docker-build/dockerfile:36` — `chown xray /usr/bin/xray` 位于第二个 `RUN` 层 |
| | `[实测]` `docker history` 显示两层：`66.9MB`（unzip 层）+ `36.6MB`（chown 层）。后者是 36.6MB 二进制的**完整副本**——改属主触发 overlayfs copy-up |
| | `[实测]` 修正版（去 chown、`install` 分别落位、geodata 移到 `/usr/local/share/xray`、不装 LICENSE/README）实测 **104MB**，且在 `--read-only --user 10000:10000 --cap-drop ALL` 下 geodata 正常解析 |
| 补充 | 该 `chown` 本身无必要：`dockerfile:30` 已 `chmod +x`，执行权限与属主无关 |
| 影响 | 18 台节点每次拉取多传 ~50MB（未压缩）/ ~14MB（gzip） |
| 归属 | P0 |

### D3 — `connLimit` 是死配置，且 Xray 根本没有连接数上限能力

| 项 | 内容 |
|---|---|
| 现象 | 配置了连接数限制，但从未生效 |
| 证据 | `[代码]` `roles/reality_multi/templates/config.json.j2:27`、`roles/reality_single/templates/config.json.j2:23`、`group_vars/all/main.yml:19-20` |
| | `[上游]` [policy 文档](https://xtls.github.io/config/policy.html) 的 `LevelPolicyObject` 字段为 `handshake` / `connIdle` / `uplinkOnly` / `downlinkOnly` / `statsUserUplink` / `statsUserDownlink` / `statsUserOnline` / `bufferSize`，**无 `connLimit`**（查证 2026-08-27） |
| | `[实测]` Xray 静默接受任意未知字段：`{"connLimit":6,"totallyBogusField":123}` → `Configuration OK`；加 `XRAY_JSON_STRICT=1` 仍 `Configuration OK` |
| 关键结论 | **Xray 没有"限制连接数"这个能力。** 这不是写错了字段名，是这个需求在 Xray 层无法表达。 |
| 真正的强制点 | `[代码]` `roles/reality_multi/templates/docker-compose.yml.j2:30` `pids_limit: 256`、`:35-39` `ulimits.nofile 4096/8192`、`nproc 512`——容器层已有实际约束 |
| 可观测替代 | `[实测]` `statsUserOnline` 是合法字段（`Configuration OK`），但它只**统计**在线数（20 秒内活跃），不限制 |
| 影响 | `todo.md` 第 3 项"按用户等级设置连接数"需要**重新定义**，不能按原文实现 |
| 归属 | P0（删除死配置）+ P3（重新定义分级策略） |
| 状态 | **P0 部分已修（2026-08-29）**：两个模板与 `group_vars/all/main.yml` 的 `connLimit` 已删除，渲染回归通过（两模板均为合法 JSON，`policy.levels.0` 仅余 `handshake`/`connIdle`/`uplinkOnly`/`downlinkOnly`/`bufferSize`/`statsUser*`）。P3 的分级策略重定义仍未开始 |
| 未闭合 | 线上 18 台节点的 `config.json` 仍带该字段，**要到下次部署才会消失**；因该字段本就被 Xray 静默丢弃，不部署也无行为影响 |

### D4 — 无 `dns` 块，但 `domainStrategy` 是 `IPIfNonMatch`

| 项 | 内容 |
|---|---|
| 证据 | `[代码]` `roles/reality_multi/templates/config.json.j2:39`、`roles/reality_single/templates/config.json.j2:38` 均为 `"domainStrategy": "IPIfNonMatch"` |
| | `[代码]` 两个模板全文均**无 `dns` 块**（`grep '"dns"'` 零命中） |
| 机制 | `IPIfNonMatch` 会对未命中域名规则的请求做本地解析；无 `dns` 块时 Xray 使用容器的系统解析器（`/etc/resolv.conf` → Docker DNS → 宿主机 resolver） |
| 影响 | 对应 `todo.md` 第 4 项。当前解析路径不受控 |
| 前置条件 | DoH 需要 CA 证书 —— `[代码]` `docker-build/dockerfile:7` 已装 `ca-certificates`，镜像侧无需改动 |
| 归属 | P3 |

### D5 — 入站无 `sniffing`

| 项 | 内容 |
|---|---|
| 证据 | `[代码]` 两个模板 `grep -c sniffing` 均为 `0` |
| 影响 | 对应 `todo.md` 第 2 项。客户端已本地解析、只送 IP 的请求无法命中域名路由规则 |
| 归属 | P3（与 D4 同一个 routing 片段，见 §4 P3 的合并约束） |

### D6 — 节点不会自动重拉镜像

| 项 | 内容 |
|---|---|
| 证据 | `[代码]` `roles/reality_multi/tasks/main.yml:111` — `when: (xray_image_info.images \| default([])) \| length == 0`，仅本地无镜像时才 pull |
| | `[代码]` 同文件 `:118` — 无条件 pull 的任务挂在 `update_image` tag 下，默认不执行 |
| | `[代码]` 同文件 `:257` — `docker_compose_v2` 使用 `pull: never` |
| | `[代码]` `roles/reality_single/tasks/main.yml:126`、`:132` 同构 |
| 影响 | 节点长期停留在首次拉取的那个 `latest`。这解释了"仓库≠线上"的**一部分**，但不是全部——完整原因属于 §3 G1 |
| 归属 | P1 |

### D7 — 上游明确警告当前 REALITY 目标

| 项 | 内容 |
|---|---|
| 证据 | `[实测]` 用 `dest: www.apple.com:443` 在 v26.3.27 上跑 `run -test`，输出：<br>`[Warning] infra/conf: REALITY: Choosing apple, icloud, etc. as the target may get your IP blocked by the GFW`<br>`[Warning] infra/conf: REALITY: Listening on non-443 ports may get your IP blocked by the GFW` |
| 实际暴露面 | `[代码]` `group_vars/all/main.yml:6-7` 的 `www.apple.com` 是**默认值**，但 **13/18 台在 `host_vars` 中显式覆盖**。仅以下节点仍吃默认值：`ali`、`hkcod12`、`hyd13`、`hyu22`、`hyu24`（后四台属 `[test_nodes]`） |
| | `[代码]` `host_vars/hk-hn.yml:9-10` 显式使用 `www.icloud.com` —— **同样命中该警告** |
| | 其余覆盖值（yahoo.co.jp / ebay / booking / flipkart / cathaypacific / walmart / costco / shopee 等）不命中 |
| 非 443 警告 | `[代码]` **只有 multi 模式**的 inbound 固定 `8443`（`reality_multi/.../config.json.j2:55`），由 `docker-compose.yml.j2:13` 映射 `item.port:8443`。<br>**single 模式** Xray 直接监听 `item.port`（`reality_single/.../config.json.j2:111`），`[实测]` 本机 26 个用户端口全部为随机高位端口（如 `33693`），该警告对 single 节点**始终成立** |
| 归属 | P5（需拍板）。范围比初版估计的小：只需处理 `hk-hn` + 5 台吃默认值的节点 |

> **初版修正（2026-08-27）**：初版称"项目使用 apple 作为 REALITY 目标"并称"两模板 inbound 固定 8443"，
> 两处均**不准确**。实测线上配置后已按上表更正。

### D8 — Docker Hub tag 现状与排序

| 项 | 内容 |
|---|---|
| 证据 | `[实测]` 2026-08-27 API 快照：`taoziyoyo2566/xray_docker` 共 16 个 tag |
| | `latest` / `stable` / `v26.3.27` 更新于 `2026-08-26T05:01`；11 个 `-beta` 更新于 `2026-08-26T16:07`~`16:16` |
| | 遗留未清理：`stable`、`stable-previous`、一个 40 位 commit-sha tag（`2025-12-24`） |
| | `stable-previous` 的 manifest 含 `unknown/unknown` 平台条目（attestation），而 `v26.3.27` / `latest` **没有**——说明当前 workflow 不产出 attestation |
| 现象 | Docker Hub 默认按 `last_updated` 倒序，因此 `latest`(05:01) 排在全部 beta(16:07+) **下面**；beta 之间也非版本序 |
| 影响 | 页面默认视图对使用者有误导；`latest` 不在顶部 |
| 归属 | P2 |

### D9 — multi 模式配了 Xray stats 但监控不使用

| 项 | 内容 |
|---|---|
| 证据 | `[代码]` `roles/reality_multi/templates/config.json.j2:13-37` 配置了 `api` / `stats` / `policy.levels.statsUserUplink/Downlink` |
| | `[代码]` `roles/monitor/templates/agent.py.j2:127` — `docker exec reality_core xray api statsquery` 只针对 **single 模式**的 `reality_core` 容器 |
| | `[代码]` 同文件 `:97` — multi 模式改读容器网卡字节数 `/sys/class/net/eth0/statistics/*_bytes` |
| 影响 | multi 模式下 Xray 的用户维度统计是**配了但没接**。`todo.md` 第 3 项的"流量统计"在 multi 下走的是另一条链路 |
| 归属 | P3 |

### D10 — `audit.yml` 只对 single 模式有效

| 项 | 内容 |
|---|---|
| 证据 | `[代码]` `audit.yml:10` — `LOG_FILE="{{ reality_logs_dir }}/reality_core/access.log"` 硬编码 single 模式容器名 |
| | `[代码]` multi 模式日志在 `{{ reality_logs_dir }}/{{ item.name }}/`（`docker-compose.yml.j2:21`） |
| 影响 | 在 multi 节点上 audit 静默返回空（`audit.yml` 内有 `if [ -f "$LOG_FILE" ]` 保护，不报错） |
| 归属 | P0 |

### D11 — 线上配置含仓库中已不存在的变量产物（2026-08-29 改判：不成立）

> **改判（`[实测]` 2026-08-29）**：原判的唯一依据是
> `grep -rn "socks5_egress" host_vars/*.yml` 零命中，但这个变量根本不在 `host_vars` ——
> 它定义在 `group_vars/all/socks5.yml:6`，由 vault 的 `vault_socks5_jpntt_*` 驱动，
> 作用于全体主机。**线上那个 outbound 完全可以从仓库复现**，本条不成立，
> P1 部署阻塞撤销。顺这条线索发现的真实缺陷见 D12。

| 项 | 内容 |
|---|---|
| 现象 | 线上 `config.json` 有一个当时认为仓库**无法再生成**的 outbound |
| 原证据 | `[实测]` 线上配置 `outbounds` 标签为 `["direct","blocked","socks5-profile-jpntt_isp"]` |
| | `[代码]` `grep -rn "socks5_egress" host_vars/*.yml` → 零命中 —— **检索范围有误，漏了 `group_vars/`** |
| 改判证据 | `[代码]` `group_vars/all/socks5.yml:6` 定义 `socks5_egress`；`:9` 为 profile `jpntt_isp`；`:17` 的 `route.hosts` 为 `["jp10"]` |
| | `[代码]` `roles/reality_single/tasks/main.yml:137`、`:150` 由 `socks5_egress` 取值 |
| 结论 | 配置可从仓库复现。「重新部署会静默丢失该 outbound」不成立 |
| 归属 | **撤销**。原 P1 阻塞取消；由此发现的 D12 另行归属 |

---

### D12 — SOCKS5 凭据被写入每一台 single 节点

| 项 | 内容 |
|---|---|
| 现象 | 只有 jp10 需要走 jpntt 的 SOCKS5 出口，但该出口的地址、用户名、密码被写进**全部** single 节点的 `config.json` |
| 证据 | `[代码]` `roles/reality_single/templates/config.json.j2:156` —— outbound 的生成条件只有 `profile.enabled && address && port > 0`，**不做主机门控** |
| | `[代码]` 同文件 `:69` —— routing 规则的条件是 `profile_complete and host_matches and has_route_rule`，**做主机门控** |
| | `[代码]` `group_vars/all/socks5.yml:17` 的 `route.hosts: ["jp10"]` 只影响 `:69` 那个块 |
| | `[实测]` 2026-08-29 本机 `spt`：`outbounds` 含 `socks5-profile-jpntt_isp`，而 `routing.rules` 只有 2 条、无对应规则 —— **凭据在，路由不在** |
| 影响 | 凭据扩散面由 1 台放大到全部 single 节点。任一节点上读到 `config.json` 即泄露该 SOCKS5 账号 |
| 归属 | 待定级。修复方向：给 `:156` 的条件补上与 `:69` 相同的 `host_matches` |

---

### D13 — 用户私钥以明文进入公开仓库

| 项 | 内容 |
|---|---|
| 现象 | 每个用户的 Reality `private_key` 以明文提交在公开仓库中 |
| 证据 | `[实测]` 2026-08-29 `gh api repos/taoziyoyo2566/reality-ops --jq .visibility` → `public` |
| | `[实测]` 已跟踪的 `users/*.yml` 共 32 个，**32 个全部含 `private_key`** |
| | `[代码]` `.gitignore:19` 只忽略 `users/*.json`，`.yml` 不在范围内 |
| 影响 | 任何人可从公开仓库取得全部用户私钥。轮换需同时更新客户端订阅，成本高 |
| 决定 | **操作者 2026-08-29 决定：记录待后续处理**，本轮不修复 |
| 归属 | 待定级。与 P3/P5 的用户体系改造相关，需单独规划 |

---

## 3. 明确的缺口（未知，不得当作已通过）

### G1 — 节点实际运行态（已采集 2/18，其余 16 台仍未知）

**2026-08-27 更新**：执行环境本身就是一台生产节点（`single` 模式），已就地实测。
以下为该节点的实证快照；**其余 17 台仍属未知**，G1 未全部关闭。

| 项 | 实测值 |
|---|---|
| 主机 | `hostname=v133-18-145-97-vir`，全局 IPv6 `2406:8c00:0:3469:133:18:145:97` |
| 容器 | `reality_core`（`single` 模式），`Up 3 weeks`，创建于 `2026-07-30 08:59 JST` |
| 镜像 | `taoziyoyo2566/xray_docker:latest` |
| 镜像 digest | `sha256:433d7302cddb336cb3b4d06f543798a850991a662cd136b5a6b7fa43274599a3` |
| **镜像构建时间** | **`2025-12-24T17:13:07Z`（8 个月前）** |
| **运行中 Xray 版本** | **`25.12.8` (`81f8f39`, go1.25.5)** —— 非 `v26.3.27` |
| 配置 | `/opt/reality/data/reality_core/config.json`（`ro` 挂载），生成于 `2026-07-30 08:59` |
| 规模 | 26 个用户 vless inbound + 1 个 api inbound；端口为随机高位，IPv4/IPv6 双栈映射 |
| API | `127.0.0.1:10085` 已映射，monitor 的 `statsquery` 路径可用 |
| geodata | `[实测]` 旧镜像同样自带 `/usr/bin/geoip.dat`(20,477,449) + `geosite.dat`(9,328,396)，`XRAY_LOCATION_ASSET` 未设 → **D1 对当前运行镜像同样成立** |

**该节点对各缺陷的实证确认**：

| 缺陷 | 线上确认 |
|---|---|
| D3 `connLimit` | ✅ 线上 `policy.levels.0` 确实含 `"connLimit": 24` —— 死配置正在生产运行 |
| D4 无 `dns` 块 | ✅ 线上配置顶层键为 `log/api/stats/policy/routing/inbounds/outbounds`，**无 `dns`**；`domainStrategy` 为 `IPIfNonMatch` |
| D5 无 `sniffing` | ✅ 27 个 inbound 中含 `sniffing` 的为 **0** |
| geo 分流未启用 | ✅ `routing.rules` 仅 2 条（`api` → `api`、`bittorrent` → `blocked`），无 geoip/geosite 规则 |
| D6 不重拉镜像 | ✅ Docker Hub 的 `latest` 已于 `2026-08-26` 指向 v26.3.27，本机仍是 2025-12 的镜像 |
| D7 REALITY 目标 | 本机为 `www.yahoo.co.jp:443`，**不命中** apple/icloud 警告；但 26 个端口全非 443，第二条警告成立 |

**同机并存的第三方部署**（此前未在任何文档中记录）：

| 项 | 值 |
|---|---|
| 容器 | `xray_reality`，`Up 3 weeks`，创建于 `2026-07-24 19:07 JST` |
| 镜像 | `wulabing/xray_docker_reality:latest` @ `sha256:a96fed8bb4be…`，208MB，构建于 `2026-07-23` |
| 端口 | **占用 `0.0.0.0:443` 与 `[::]:443`** |
| 备注 | `xray` 不在该镜像 `$PATH` 中，结构与本项目镜像不同。本项目容器**未占用 443** |

**本机（控制端 `spt`）快照**（`[实测]` 2026-08-29）：

| 项 | 实测值 |
|---|---|
| 主机 | `mail.taoziyoyo.com`，`inventory.ini:27` 以 `ansible_connection=local` 声明为 `spt` |
| 容器 | `reality_core`（`single` 模式），Up 5 days，创建于 2026-08-22 |
| 镜像 | `taoziyoyo2566/xray_docker:latest` |
| 镜像 digest | `sha256:433d7302…` —— **与 G1 上一台节点完全相同** |
| 镜像构建时间 | `2025-12-24T17:13:07Z` |
| 运行中 Xray 版本 | **`25.12.8`**（`81f8f39`，go1.25.5） |
| 规模 | 25 个 inbound（24 用户 + 1 api），IPv4/IPv6 双栈映射 |
| outbounds | `["direct","blocked","socks5-profile-jpntt_isp"]` —— 见 D12 |

**该节点对各缺陷的实证确认**：

| 缺陷 | 线上确认 |
|---|---|
| D3 `connLimit` | ✅ `policy.levels.0` 含 `"connLimit": 24` |
| D4 无 `dns` 块 | ✅ 顶层键为 `log/api/stats/policy/routing/inbounds/outbounds`；`domainStrategy` 为 `IPIfNonMatch` |
| D5 无 `sniffing` | ✅ 25 个 inbound 中含 `sniffing` 的为 0 |
| geo 分流未启用 | ✅ `routing.rules` 仅 2 条 |
| D6 不重拉镜像 | ✅ 容器 7 天前才创建，拉到的仍是 8 个月前的镜像 |

**两台不同机器、不同用户规模、同一个镜像 digest、同一组缺陷** —— 漂移是系统性的，
不是个例。

**仍未关闭的部分** —— 其余 17 台需要同样的采集：

| 项 | 为什么必须 |
|---|---|
| 各节点镜像 digest 与 Xray 版本 | 判断漂移分布；本机证实可落后 8 个月 |
| 各节点实际 `config.json` | 判断配置漂移与凭据扩散范围，见 D12 |
| single / multi 分布 | `[代码]` `host_vars` 声明：single 10 台、multi 6 台、未声明 2 台。**声明值需与线上核对** |

`[实测]` 本机 SSH 配置（`~/.ssh/config` → `Include config.d/*`）仅定义 6 个 Host
（`dzire`/`hyd13`/`hyu24`/`netcup`/`r6s`/`rock9`），且无 `~/.vault_pass`，
ansible ad-hoc 报 `Attempting to decrypt but no vault secrets found`。
**从本机无法采集其余节点** —— 需在真正的控制端执行。

> 初版称"无 `~/.ssh/config`"有误，实际存在但只是一行 `Include config.d/*`。

> **更正（`[实测]` 2026-08-29）**：上文「从本机无法采集其余节点」说的是撰写该段时
> 所在的那台机器。**当前执行环境（`mail.taoziyoyo.com`，即 `spt`）就是可用的控制端**：
> `~/.ssh/config` + `config.d/` 共 21 个 Host，覆盖 inventory 18 台中的 16 台
> （未覆盖 `hk-hn` —— ssh 侧为 `hk01`/`hn01`，映射待确认；以及 `spt` 自身，它是 local）；
> `~/.vault_pass` 与仓库 `.vault_pass` 均存在；`monitor_venv/bin/` 下 ansible 可用。
> **G1-b 的采集清单在这台机器上可以执行**，G1 不再是无解的阻塞。

### G1-b — 采集清单（供控制端执行）

| 项 | 命令要素 | 为什么必须 |
|---|---|---|
| 容器实际镜像 digest | `docker inspect --format '{{.Image}}'` | Docker Hub 上存在从 2025-12 到 2026-08-26 的多个 `latest` |
| 容器内 Xray 版本 | `docker exec <c> xray -version` | 判断距 v26.3.27 的差距 |
| 实际挂载的 `config.json` | `cat {{ reality_data_dir }}/<user>/config.json` | **这是"仓库≠线上"的核心** |
| 运行模式 | 容器名是 `reality_core` 还是 `reality_<user>` | 决定 D9/D10 的适用范围 |
| 容器 uptime | `docker ps --format '{{.Status}}'` | 判断上次真实变更时间 |

**在 G1 关闭前，任何涉及部署的动作都不应执行。**

> **2026-08-29 澄清**：这条谨慎与 D11 无关。D11 已改判撤销，但本条依旧成立 ——
> 依据是其余 16 台的运行态仍然未知，不是那个已撤销的 outbound 结论。

### G2 — 本机到节点的连通性未验证

- `[实测]` `ansible` / `ansible-playbook` **不在 PATH**；仓库 wrapper `ansible-playbook:61-62` 回退到 `monitor_venv/bin/ansible-playbook`，该文件存在
- `[实测]` 无 `~/.ssh/config`
- 是否能实际连通节点**未验证**

### G3 — VLESS Encryption 仅做了配置校验

`[实测]` REALITY + `xtls-rprx-vision` + `decryption: mlkem768x25519plus.native.600s.<key>`
组合通过 `run -test` → `Configuration OK`。

**但仅此而已**：未做真实客户端连通测试、未验证客户端兼容性矩阵、未测试性能影响。
在 P5 执行前必须补齐。

### G4 — `imagetools create` 是否保留 index annotations，未验证

> **2026-08-28：随镜像项目移交 `xray-docker`。** 本仓库不再跟踪该缺口。

P2 计划补 index 级 `annotations`，但存在一个**未验证的前提**：

- `[代码]` `.github/workflows/build-image.yml:107` 用 `push-by-digest=true` 推送候选，
  `:130` 再用 `docker buildx imagetools create --tag` 单独打 tag
- 已知：`docker/metadata-action` 的 `labels` 落在**子镜像 config** 上；Docker Hub 与
  `imagetools inspect` 读的是 **index 上的 annotations**，二者自 buildx 0.12 起分离
- **未验证**：`imagetools create` 从一个已是 index 的源创建新 tag 时，是否保留源 index
  上的 annotations。若不保留，则必须在该步显式传 `--annotation`

在 P2 动手前必须先实测这一条，否则可能补了 `annotations:` 输入却在打 tag 时丢失。

---

## 3.5 在途未提交变更（2026-08-28 作废；2026-08-29 改为随交接提交）

工作树中那处未提交的 patch（`.github/workflows/build-image.yml` 的
`max-parallel: 2 → 1`、`docker-build/discover-release-window.sh` 的排序修正）
**已随镜像项目剥离失去归宿**：这两个文件在本仓库即将删除（P2-b），
而新仓库的对应实现已改用 `build-fingerprint.sh` 方案，不再是同一段代码。

**2026-08-29 更正**：最终没有走丢弃路线。该 patch 已随 `300b098` 一并提交，
保留在 `feat/image-handoff` 上，等 P2-b 删除这两个文件时自然消失。
改为提交而非丢弃，省掉了一次「丢弃未提交工作」的授权，代价只是这两行多活几天。

| 项 | 处置 |
|---|---|
| `max-parallel` 与排序 patch | 已随 `300b098` 提交；P2-b 删文件时消失。对应问题由新仓库自行维护 |
| `tests/test_xray_image_workflow.sh:11` 的红测试 | 已在 `feat/image-handoff` 上把断言同步为 `max-parallel: 1`，使主干在 P2-b 之前保持绿 |
| `discover-release-window.sh` 的同秒 tie-break 缺陷 | 移交新仓库；本仓库不再跟踪 |

`[实测]` 2026-08-29 核对新仓库：`build-image.yml:65` 已是 `max-parallel: 1`，
`discover-release-window.sh:180-198` 已含 `reverse` + `sort_by(.published_at)`
与同秒 tie-break 的处理。两处等价实现在新仓库均已存在，本仓库无论保留或删除都无损失。

---

## 4. 统一阶段划分

依赖关系：`P0 → P1 →` (`P2-c` ∥ `P3`) `→ P4 → P5`。
例外：`P2-a`（停掉重复发布者）与 `P2-b`（删除镜像资产）不依赖 P1，可立即执行，且应先于其余一切镜像相关动作。

### P0 — 修正已验证缺陷（不改架构，零风险）

镜像侧的 D1 / D2 与三项发布脚本测试任务已随项目剥离移出（见 §1.5、§3.5），
本仓库剩下的 P0 只剩配置层两项：

| 任务 | 依据 | 验收 |
|---|---|---|
| ~~删除两个模板的 `connLimit` 与 `group_vars/all/main.yml:19-20`~~ **已完成 2026-08-29** | D3 | ✅ 渲染回归通过：两模板渲染为合法 JSON，`policy.levels.0` 无 `connLimit`，其余字段无回归 |
| `audit.yml:10` 支持 multi 模式日志路径 | D10 | 两种模式都能取到日志 |

**授权边界**：仅工作树编辑。提交 / 推送 / PR 需单独授权。

### P1 — 建立运行态真相 ⛔ 后续部署动作的前置

| 任务 | 依据 |
|---|---|
| 只读采集 G1 清单，产出"线上真实状态"快照文档 | G1 |
| 验证本机到节点连通性 | G2 |
| 基于快照，判定 D6 造成的版本漂移范围 | D6 |
| 决定镜像引用策略：跟随 `latest` + 显式 `update_image`，还是 pin digest | D6 |

**授权边界**：连接节点属于外部只读访问，需操作者单独授权。命令须先经审阅。

### P2 — 镜像去耦与迁移（本仓库侧）

原 P2「镜像与发布链路现代化」的镜像内部工作已随项目剥离迁往 `xray-docker`
（GHCR 双发、index annotations 与 provenance/sbom attestation、遗留 tag 清理、
周审计、`-beta` tag 契约等在那边继续，G4 一并移交）。本仓库剩下三段，次序不可颠倒。

#### P2-a 止血：停掉重复发布者（不依赖 P1，应最先做）

| 任务 | 说明 |
|---|---|
| 停用本仓库的 `Sync Xray Release Images` 与 `Audit Xray Image Tags` | **已于 2026-08-29 执行**。二者原为 `active`（§1.5）。不停用，旧镜像名会继续长出新 tag，"哪个是真相源"永远不收敛 |

**执行记录（`[实测]` 2026-08-29）**：

| Workflow | ID | 执行前 | 执行后 |
|---|---|---|---|
| Sync Xray Release Images | `218448596` | `active` | `disabled_manually` |
| Audit Xray Image Tags | `342639627` | `active` | `disabled_manually` |

停用范围仅限本仓库。`Repository quality checks` / `Dependency Graph` /
`Dependabot Updates` 保持 `active`；`taoziyoyo2566/xray-docker` 的 5 个 workflow
全部未动（已复核）。恢复方式：`gh workflow enable <id> --repo taoziyoyo2566/reality-ops`。

这是 GitHub 上的手工状态，而仓库里的 workflow 文件仍然存在 —— 属于有意的临时漂移，
由 P2-b 删除文件后消除。**在 P2-b 合并前，不要仅凭仓库内容判断这两条流水线是否会运行。**

**授权边界**：改动 GitHub workflow 启停属外部写，需单独授权。已按此授权并执行。

#### P2-b 删除镜像资产并补消费者契约（工作树编辑）

| 任务 | 注意 |
|---|---|
| 删 `docker-build/`、`tests/test_xray_image_*.sh`、`tests/test_xray_release_discovery.sh`、两条镜像 workflow、`docs/runbooks/xray-image-release.md` | — |
| 同步改 `.github/workflows/quality.yml:9-31` | `xray-image-inputs` job 的全部内容都是这些脚本与测试，删文件而不改它 = CI 立刻变红 |
| 改 `README.md:143-145` | 现指向 `docker-build/README.md`（将不存在）与旧镜像名 |
| 改 `docs/project-memory.md:38,55,87` | 三处描述发布机制，需改写为消费者视角 |
| 新增一页镜像消费者契约 | `xray_image` 语义、tag 契约（`latest` 会移动；`vX.Y.Z` 会因镜像定义变更而重建改指；要绝对不变必须 pin digest）、升级流程、上游仓库指针 |
| 清理 `todo.md` 并同步 `decommission.yml:91` 的引用 | 其内容已并入本文 P3/P4；该行 `git grep` 扫描清单硬编码了 `todo.md`，删文件必须同时改 |

验收：`bash -n` 与 `quality.yml` 在删除后仍为绿；仓库内 `git grep -n 'docker-build\|xray_docker'`
只剩消费者契约与历史文档中的说明性引用。

#### P2-c 切换镜像名与引用策略

**操作者决定（2026-08-28）**：当前机器不统筹那 18 台 VPS，不需要分批滚动窗口，
直接切到新镜像库。据此 P2-c 不再由 P1 阻塞，滚动分批一项取消。

| 任务 | 依据 | 状态 |
|---|---|---|
| `group_vars/all/main.yml:3` → `taoziyoyo2566/xray-docker:latest` | D6 | 已改（工作树，未提交未部署） |
| `README.md` 与 `JPNTT_SOCKS5_EGRESS.md` 中的镜像名与说明同步 | — | 已改（工作树） |
| `docs/project-memory.md` 「Xray Image Release State」加剥离横幅并更正部署默认值 | — | 已改（工作树） |
| 修 D6：`reality_single/tasks/main.yml:111`、`reality_multi/tasks/main.yml:111` 的 `when: images \| length == 0` 使节点永不重拉 | D6 | 未做 |
| 实际部署一次，确认新镜像在节点上行为符合下表 | — | 未做，需部署授权 |

迁移验收（新旧镜像的行为差异，全部须在节点上实测，不得据新仓库代码推断）：

| 检查 | 期望 |
|---|---|
| `--read-only --cap-drop ALL --user 10000` 下 geodata 可解析 | D1 在线上关闭 |
| 镜像体积 | D2 在线上关闭 |
| 坏配置行为 | 新 entrypoint 启动前 `run -test`，从"静默重启循环"变为"直接退出并打印解析错误"。`restart: always` 下表现为容器停住，切换当天必须盯 |
| `XRAY_HEALTH_PORT` | 决定是否启用；不设则镜像不做探测 |
| UID 与端口 | 部署已是 `user: "10000:10000"`（`reality_single/tasks/main.yml:271` 与 multi 的 compose 模板），与新镜像默认一致，无需改动 |

**旧 Docker Hub 仓库 `xray_docker` 的归宿（2026-08-28 决定）**：冻结，不再更新。
本仓库的引用已全部切走；`latest` 停在 2026-08-26 那版。它已有 2236 次拉取，
可能存在本项目之外的使用者，但不再为其提供更新——这也让 P2-a 的停用动作没有副作用。

### P3 — 配置层功能补齐（原 `todo.md`）

| 任务 | 依据 | 顺序约束 |
|---|---|---|
| 加 `dns` 块 + DoH/DoT | D4 / todo #4 | **必须最先**，否则 `IPIfNonMatch` 一直在裸奔 |
| 入站 `sniffing` + 域名路由 | D5 / todo #2 | 依赖 D1 的 geodata |
| geoip/geosite 分流 + 广告拦截 | todo #1 | 依赖 D1；数据文件已具备 |
| 重新定义"用户分级策略" | D3 / todo #3 | 见下方说明 |
| 决定 multi 模式统计链路：接 Xray stats 还是保留容器字节数 | D9 | |

**关于 todo #3 的重新定义**：原文"按用户等级设置连接数"在 Xray 层不可实现（D3）。
可实现的替代组合是——
Xray 层：按 `acl_matrix`（`group_vars/all/main.yml:77-88` 定义了 free/cm/basic/normal/premium 五档）
给 client 分配不同 `level`，在 `policy.levels` 里区分 `connIdle` / `handshake` / `bufferSize` / `statsUserOnline`；
容器层：按档位差异化 `pids_limit` / `ulimits` / `mem_limit`。

**合并约束**：D4 + D5 + 分流三项共享同一个 `routing` 片段（见 P2 已知约束），
必须一次性设计，不能分三次追加。

### P4 — 传输层与寻址（原 roadmap Phase 2 / 3）

保留原 roadmap 的目标与验收，未作修改：

- XHTTP + REALITY：加一个 `tcp|xhttp` 选择器，默认 `tcp`；TCP 路径保持 REALITY + Vision 语义不变
- IPv4/IPv6 自动选择：`auto|ipv4` 策略，IPv4 为安全回退；`auto` 使用已验证的双栈 FQDN
- `todo.md` 末段的"订阅双栈节点输出模式"（merged / split / both）并入此阶段

### P5 — 协议层演进（需拍板，逐项独立决策）

| 候选 | 状态 | 阻塞 |
|---|---|---|
| VLESS Encryption（ML-KEM-768 后量子） | 配置校验已通过 | G3：缺真实客户端验证；且需改订阅 URL 与 `generate_user.py` |
| 更换 REALITY dest（弃用 apple） | 上游已警告 | D7：涉及全量客户端订阅变更 |
| `xray api adu` / `rmu` 热增删用户 | 能力已确认存在 | 需重构用户变更流程（当前每次改动重建容器） |
| `metrics` + `listen` 替代 `docker exec statsquery` | 能力已确认存在 | 依赖 P1 的模式判定与 D9 决策 |

---

## 5. 上游情报（查证日期 2026-08-27）

| 事项 | 状态 |
|---|---|
| 最新 stable | `v26.3.27`（2026-03-27），最新 prerelease `v26.7.28`（2026-07-28） |
| VLESS Encryption | 2025 年合入，`xray vlessenc` 生成 decryption/encryption 对；格式 `mlkem768x25519plus.<native\|xorpub\|random>.<600s\|1rtt\|0rtt>.<key>` |
| VLESS **without flow** 弃用 | 上游正推动迁移到带 flow。本项目已用 `xtls-rprx-vision`（`config.json.j2` 两模板），**不受影响** |
| `decryption: "none"` | `[实测]` v26.3.27 上仍正常，无弃用警告 |
| release zip 内容 | 含 `xray` + `geoip.dat` + `geosite.dat` + `LICENSE` + `README.md` |
| 可用 CLI | `run` / `api` / `convert` / `tls` / `uuid` / `x25519` / `wg` / `mldsa65` / `mlkem768` / `vlessenc` / `buildMphCache` |
| `xray api` 子命令 | 含 `adu` / `rmu`（运行时增删用户）、`adrules`、`inbounduser`、`statssys` 等 |

来源：
[Releases](https://github.com/XTLS/Xray-core/releases) ·
[policy 配置](https://xtls.github.io/config/policy.html) ·
[metrics 配置](https://xtls.github.io/config/metrics.html) ·
[环境变量](https://xtls.github.io/config/env.html) ·
[PR #5067 VLESS 后量子加密](https://github.com/XTLS/Xray-core/pull/5067) ·
[Discussion #5568 VLESS without flow 弃用](https://github.com/XTLS/Xray-core/discussions/5568)

---

## 6. 复现附录

所有 `[实测]` 结论的复现步骤。执行环境需要 `python3`、`curl`、`docker`。

```bash
# 准备：下载并解包官方 release（本仓库不存放二进制）
W=$(mktemp -d)
curl -sSL -o "$W/xray.zip" \
  https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip
python3 -c "
import zipfile,os
z=zipfile.ZipFile('$W/xray.zip'); z.extractall('$W/bin')
for i in z.infolist(): print(f'{i.file_size:>12,}  {i.filename}')
os.chmod('$W/bin/xray',0o755)"
# 预期：geoip.dat 19,768,301 / geosite.dat 10,491,954 / xray 36,577,406 / LICENSE / README.md   → D1
```

```bash
# D1：资产解析位置 —— 证明是可执行文件目录，不是 cwd
cat > "$W/geo.json" <<'EOF'
{"log":{"loglevel":"debug"},
 "inbounds":[{"port":18449,"listen":"127.0.0.1","protocol":"dokodemo-door",
   "settings":{"address":"127.0.0.1","port":80,"network":"tcp"}}],
 "outbounds":[{"protocol":"freedom","tag":"direct"},{"protocol":"blackhole","tag":"blocked"}],
 "routing":{"rules":[{"type":"field","domain":["geosite:category-ads-all"],"outboundTag":"blocked"}]}}
EOF
(cd / && "$W/bin/xray" run -test -config "$W/geo.json")      # → MphDomainMatcher ... 170624 domain rule(s)
mkdir -p "$W/only" && cp "$W/bin/xray" "$W/only/"
(cd / && "$W/only/xray" run -test -config "$W/geo.json")     # → open <exedir>/geosite.dat: no such file
```

```bash
# D3：未知字段被静默吞掉
cat > "$W/pol.json" <<'EOF'
{"log":{"loglevel":"warning"},
 "policy":{"levels":{"0":{"connLimit":6,"totallyBogusField":123}}},
 "inbounds":[{"port":18445,"listen":"127.0.0.1","protocol":"dokodemo-door",
   "settings":{"address":"127.0.0.1","port":80,"network":"tcp"}}],
 "outbounds":[{"protocol":"freedom"}]}
EOF
"$W/bin/xray" run -test -config "$W/pol.json"                    # → Configuration OK
XRAY_JSON_STRICT=1 "$W/bin/xray" run -test -config "$W/pol.json" # → Configuration OK
```

```bash
# D7：REALITY 目标警告（用仓库真实参数）
cat > "$W/re.json" <<'EOF'
{"log":{"loglevel":"warning"},
 "inbounds":[{"port":8443,"protocol":"vless",
  "settings":{"clients":[{"id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
              "flow":"xtls-rprx-vision","level":0}],"decryption":"none"},
  "streamSettings":{"network":"tcp","security":"reality","realitySettings":{
    "dest":"www.apple.com:443","serverNames":["www.apple.com"],
    "privateKey":"QG7Sq-oNbEy6mBvRzMx6UfLd7hCPGXwLnZmDNIQF5Uc","shortIds":["01ab"]}}}],
 "outbounds":[{"protocol":"freedom"}]}
EOF
"$W/bin/xray" run -test -config "$W/re.json"
# → [Warning] REALITY: Choosing apple, icloud, etc. as the target may get your IP blocked by the GFW
# → [Warning] REALITY: Listening on non-443 ports may get your IP blocked by the GFW
```

```bash
# P2 约束：confdir 合并语义 —— routing 被整体替换
mkdir -p "$W/cd"
echo '{"log":{"loglevel":"warning"},"outbounds":[{"protocol":"freedom","tag":"direct"}],
       "routing":{"domainStrategy":"IPIfNonMatch","rules":[{"tag":"r-bt","type":"field","protocol":["bittorrent"],"outboundTag":"direct"}]}}' > "$W/cd/00.json"
echo '{"routing":{"rules":[{"tag":"r-ads","type":"field","domain":["geosite:category-ads-all"],"outboundTag":"direct"}]}}' > "$W/cd/10.json"
echo '{"inbounds":[{"port":18447,"listen":"127.0.0.1","protocol":"dokodemo-door",
       "settings":{"address":"127.0.0.1","port":80,"network":"tcp"},"tag":"in"}]}' > "$W/cd/90.json"
"$W/bin/xray" run -dump -confdir "$W/cd" | python3 -c "
import sys,json; t=sys.stdin.read(); c=json.loads(t[t.find('{'):])
r=c.get('routing',{})
print('rules:',len(r.get('rules',[])),'domainStrategy:',r.get('domainStrategy'))"
# → rules: 1  domainStrategy: None      （00.json 的 routing 整个丢失）
```

```bash
# D2：镜像层重复（需在 docker-build/ 下执行）
SHA=$(sha256sum "$W/xray.zip" | cut -d' ' -f1)
docker build --platform linux/amd64 -t xray-probe:current \
  --build-arg XRAY_VERSION=v26.3.27 \
  --build-arg XRAY_SHA256_AMD64="$SHA" --build-arg XRAY_SHA256_ARM64="$SHA" \
  -f dockerfile .
# --no-trunc 必须加，否则 CreatedBy 被截断，看不出是哪条指令造成的
docker history xray-probe:current --no-trunc --format '{{.Size}}\t{{.CreatedBy}}' | head -8
# → 36.6MB  RUN ... chown xray /usr/bin/xray ...     ← 二进制副本
# → 66.9MB  RUN ... unzip ...
docker images xray-probe --format '{{.Tag}}\t{{.Size}}'   # → current  154MB
docker rmi xray-probe:current                             # 清理探针镜像
```

```bash
# D8：Docker Hub tag 快照
curl -sS "https://hub.docker.com/v2/repositories/taoziyoyo2566/xray_docker/tags?page_size=100" \
 | python3 -c "
import sys,json; d=json.load(sys.stdin); print('count:',d['count'])
for t in d['results']: print(f\"  {t['name']:<24} {t['last_updated']}\")"
```

---

## 7. 授权边界

沿用 `~/workspace/.agents/rules/` 的安全底线，本文所列任务的边界：

| 类别 | 需要单独授权 |
|---|---|
| 工作树编辑（改模板、配置、测试、文档） | 否 |
| 停用 / 启用 GitHub workflow | **是** |
| Git 暂存 / 提交 / 推送 / PR | **是** |
| 构建并推送镜像、改动 Docker Hub / GHCR tag | **是** |
| 连接节点（含只读采集） | **是** |
| 部署、DNS、Gist 写入 | **是** |

无法执行的检查一律在文档中标为缺口（§3），**不得记为通过**。

---

## 8. 变更记录

| 日期 | 内容 |
|---|---|
| 2026-08-27 | 创建。合并 `todo.md`、`roadmap-xray-xhttp-ipv6-2026-08-25.md` 与本轮实测调查 |
| 2026-08-27 | 补漏：新增 G4（`imagetools create` 是否保留 index annotations 未验证）与 §3.5（在途未提交 patch 的两个未闭合项），P0 相应增加 2 项。初版遗漏了这三条 |
| 2026-08-27 | 就地实测本机（生产 single 节点）：G1 部分关闭并填入实证快照；新增 D11（线上配置含仓库无法再生成的 outbound，升为 P1 阻塞）；**更正 D7 两处失实**（apple 仅为默认值、13/18 台已覆盖；"两模板固定 8443"错误，single 直接监听 `item.port`）；更正初版"无 `~/.ssh/config`"的错误陈述 |
| 2026-08-28 | 镜像项目已剥离为 `taoziyoyo2566/xray-docker`。新增 §1.5 项目边界与双发布者现状；§3.5 在途 patch 作废；P0 移出已在新仓库完成的 D1/D2 与三项测试任务；P2 重写为「镜像去耦与迁移（本仓库侧）」，拆为 P2-a 止血 / P2-b 删除与契约 / P2-c 切名迁移 |
| 2026-08-28 | 操作者决定直接切换镜像库：`group_vars` / `README` / `JPNTT` / `project-memory` 已在工作树改为 `taoziyoyo2566/xray-docker:latest`，旧库冻结。P2-c 解除 P1 阻塞并取消分批滚动 |
| 2026-08-29 | P2-a 已执行：本仓库两条镜像 workflow 手工停用（记录见 P2-a），§1.5 双发布者表相应更正。§3.5 更正：在途 patch 改为随 `300b098` 提交而非丢弃，并同步 `tests/test_xray_image_workflow.sh:11` 的断言，使主干在 P2-b 之前保持绿。交接内容改由 `feat/image-handoff` 合入 —— 原 `fix/xray-image-lifecycle` 名称与内容不符，且与 xray-docker 继承的历史重名 |
| 2026-08-29 | **D11 改判为不成立**：`socks5_egress` 定义在 `group_vars/all/socks5.yml:6`，原判只 grep 了 `host_vars/`，配置可从仓库复现，P1 部署阻塞撤销。顺此发现并新增 **D12**（SOCKS5 凭据被写入每一台 single 节点 —— outbound 不做主机门控，routing 规则做）与 **D13**（32/32 个已跟踪 `users/*.yml` 含明文 `private_key`，仓库为 public；操作者决定记录待后续处理）。G1 补入本机 `spt` 快照（1/18 → 2/18），并更正「从本机无法采集其余节点」—— 当前执行环境就是可用控制端 |
| 2026-08-29 | **P0 第 1 项已修**：删除 `roles/reality_single/templates/config.json.j2` 与 `roles/reality_multi/templates/config.json.j2` 的 `connLimit`（multi 侧同时去掉 `bufferSize` 的尾逗号）以及 `group_vars/all/main.yml` 的 `reality_conn_limit_single` / `reality_conn_limit_multi`。验收用 Jinja2 桩上下文渲染两个模板并 `json.loads`，确认合法 JSON、`connLimit` 消失、其余 policy 字段无回归 |
