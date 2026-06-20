# 计划：流量监控子系统完整性与安全加固

- **日期**：2026-06-13
- **级别**：架构（跨组件子系统：control/data 分离、RBAC、可观测性、一致性、扩展性、审计）
- **分支**：`fix/monitor-integrity`（叠加在 `feat/single-socks5-egress`，因监控子系统仅存在于该 feat 线，trunk `ops` 不含）
- **基线提交**：`89aefeb Add node decommission workflow`
- **作者身份**：Silent Praxis <sp@saberu.com>（全局 gitconfig，W-R25 通过）

---

## 0. 最佳实践前置检查（W-R18）

- **鉴权真实客户端 IP**：FastAPI/Starlette 官方做法是用 `ProxyHeadersMiddleware` / `uvicorn --proxy-headers --forwarded-allow-ips`，且**仅信任来自可信代理对端的转发头**。当前实现自写 `get_client_ip` 且无条件信任客户端可伪造的 `X-Forwarded-For` → 偏离最佳实践。结论：改为"仅当直连对端∈可信代理网段时才信任转发头",并优先用 `CF-Connecting-IP`。
- **SQLite 并发**：官方推荐高并发读写用 `journal_mode=WAL` + `busy_timeout`。当前为 `delete`（回滚日志，写锁整库）→ 偏离。结论：启用 WAL。
- **异步框架阻塞**：Starlette 官方约定——阻塞 IO 应放线程池（`def` 端点自动入线程池，或 `run_in_threadpool`）。当前 `async def` 内同步调 `sqlite3` → 阻塞事件循环,偏离。结论：DB 端点改同步 `def` 或显式线程池。
- **采集投递可靠性**：通用最佳实践是"投递成功才推进 offset/cursor"。当前无条件推进缓存 → 偏离。结论：失败不推进 + 持久化待发队列。

来源：FastAPI/Starlette 官方文档（proxy headers、threadpool）、SQLite 官方 WAL 文档、Uvicorn 部署文档。方向正确,进入正文。

---

## 1. 目的

修复监控子系统三类已实测确认的问题：(a) **线上活跃的鉴权绕过**导致全量数据/用户 IP 史对公网裸奔；(b) **系统性数据丢失与幻象尖峰**导致统计不可信；(c) **用户-IP 审计功能 68 天全死**。在**不中断线上运行节点**的前提下完成。

## 2. 范围（In-Scope）

| 文件 | 改动性质 |
|---|---|
| `group_vars/all/main.yml` | 改 `monitor.trust_proxy_header`；新增 `monitor.trusted_proxies` |
| `roles/monitor/templates/server.py.j2` | `get_client_ip`/`auth_guard` 加固；`init_db` 启 WAL；DB 端点去阻塞 + 写重试；`/stats/ip_report` 鉴权对齐；health 查询加时间界；新增 `/healthz` |
| `roles/monitor/templates/agent.py.j2` | 失败不推进缓存 + 持久化待发队列；缓存迁出 `/tmp`；去除容器放大；计数器重置不再注入尖峰；正则 `\\s`→`\s`；按模式选 access.log |
| `roles/monitor/tasks/main.yml` | cron 加随机抖动打散整点；新增数据保留 cron |
| `monitor.yml`（根，已腐化）| 删除或改为复用角色 |

## 3. 不做（Out-of-Scope，留后续轮次/单独确认）

- `domain_suffix: "taoziyoyo.de"`（main.yml:4）与既有约束（节点仅 `.com` 可用）冲突 —— 属订阅域名,非监控,单独确认。
- 迁移到时序数据库 / 多写聚合架构（#10 的中长期形态）—— 本轮仅加 agent 持久缓冲缓解 SPOF。
- 把 49 个未合并提交并入 trunk `ops` —— 独立产品决策。
- 仪表板（内嵌 HTML）的重构。

## 4. 验收标准

- **A1**：`curl -H "X-Forwarded-For: 127.0.0.1" https://monitor.taoziyoyo.com/debug/whoami` 返回 **401**（当前为 200）；合法 CF 回源仍 200。
- **A2**：节点重启 / 缓存丢失后**不再**产生等于累计计数的单分钟巨值；投递失败的 delta 在下一周期重试入库（注入失败再恢复，校验无丢行）。
- **A3**：`PRAGMA journal_mode` = `wal`；整点并发上报无 `database is locked`；仪表板在整点不再卡顿。
- **B**：注入一次上报后 `user_ip_hits` 出现新行（当前恒为 0）；`monitor.yml` 不再引用缺失文件。
- **C**：保留 cron 存在；`/healthz` 返回服务与 DB 状态。
- **三道验证门**全绿（见 §8）。

## 5. 现状差距分析（含实测证据，2026-06-13 于 spt 本机取证）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| #4 | `X-Forwarded-For` 鉴权绕过 | `https://monitor.taoziyoyo.com/debug/whoami` + 伪造头 = **200 并回吐白名单**；同 TCP 源切头 401↔200 | 公网任意人读全部流量 + 全部用户 IP 史(去匿名化)。**线上活跃泄露** |
| #1 | 投递失败仍推进缓存 + `/tmp` 缓存 | `hk-hn/frank` 06-11 四条各 **46.3GB 单分钟**(≈185GB 幻象)；缓存在 `/tmp`(重启即丢) | 统计单向少计 + 重启假尖峰,数据不可信 |
| #2 | 无 WAL + 单线程阻塞 | `journal_mode=delete`；服务 `Tasks:1`；DB 300MB/410万行；多节点 cron 全 `minute:*` | 整点惊群锁冲突 → 500 → 配合 #1 永久丢数 |
| #6 | IP 日志路径硬编码 `reality_core` | 6 个 multi 节点无该容器；`user_ip_hits=0` | multi 节点 IP 审计无数据 |
| #7 | `/stats/ip_report` 双重鉴权 | 端点要 `auth_guard`+token,agent 只发 token；节点公网 IP 不在白名单 | IP 上报被 401 静默丢弃 |
| #8 | 正则 `\\s` 被原样渲染 | `agent.py.j2:182-183` | IPv6/email 解析失效 |
| #3 | 容器流量"放大对齐" | `agent.py.j2:143-155`,`max(1.0,…)` 只增不减 | single 模式 per-user 数被人为膨胀 |
| #5 | `monitor.yml` 腐化副本 | 引用的 `monitor/server.py` 等全缺失;无引用;服务名/装法都与角色冲突 | 误运行会失败或部署冲突服务 |
| #9 | 无数据保留 | 300MB/410万行/68天,从未清理;health 查询无时间界 | 单调增长,查询劣化 |
| #11 | 自身无可观测性 | 全局 `except: pass`,无 `/healthz` | 故障静默 |

> #6+#7+#8 三者叠加 = 用户-IP 审计 / 共享检测(`audit.yml`、`/stats/ip_matrix`)**68 天全死**(`user_ip_hits=0`)。

## 6. 逐项改动方案与理由

### 阶段 A — 止血（本轮目标）

**A1 鉴权绕过（#4）**
- **现网取证(2026-06-13)**:`/debug/whoami` 经 CF 域名返回 `cf_connecting_ip` 非空、`raw_client`(=`request.client.host`)= 真实客户端 IP，而应用算出的 `client_ip` = 伪造的 `127.0.0.1`。即 **uvicorn 的 proxy_headers 已把真实 IP 解析正确,bug 仅在应用层又去读了最左 `X-Forwarded-For`**。nginx 确认透传 `CF-Connecting-IP`。
- `server.py.j2`：`get_client_ip` 改为——**优先 `CF-Connecting-IP`,否则用 `request.client.host`(uvicorn 已正确解析);彻底不再读最左 `X-Forwarded-For`**。loopback 放行只看 `request.client.host`。
- `main.yml`：`trust_proxy_header: "X-Forwarded-For"` → `"CF-Connecting-IP"`。
- **白名单不变**:现有 9 个 IP 原样保留;合法用户经 `monitor.taoziyoyo.com`(过 CF)访问体验不变,仅堵掉伪造旁路(已用 `raw_client` 证明可行)。
- 配套 ops 建议（写入 operations.md,非本轮代码）：origin 443 防火墙仅放行 CF 网段,堵直连 origin 的旁路。

**A2 采集可靠性（#1）**
- `agent.py.j2`：缓存从 `/tmp/reality_traffic_cache.json` 迁到 `/opt/reality/monitor/state/traffic_cache.json`（随机器持久）。
- 投递改为**逐用户成功才推进该用户缓存**；失败的 delta 累加到持久 `pending` 队列,下周期合并重试。
- **计数器重置/缓存缺失**：当 `last` 不含该用户(首见)时,**只建立基线、当周期上报 0**,不再把累计计数当 delta 注入 → 根除 46GB 尖峰。

**A3 SQLite 并发（#2）**
- `server.py.j2 init_db`：`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`；统一连接 helper 设 `busy_timeout=5000`。
- 写端点（`/report`、`/stats/ip_report`）改同步 `def`（自动入 Starlette 线程池,不阻塞事件循环）+ 写失败重试 N 次。
- `tasks/main.yml` cron：`job: "sleep $((RANDOM \% 45)); {{ monitor_venv_dir }}/bin/python3 …"` 打散整点惊群。

### 阶段 B — 正确性（下一轮）
- B1(#3) 去容器放大,直接信任 xray uplink/downlink。
- B2(#5) 删除 `monitor.yml`（或改 `import_playbook` 复用角色）。
- B3(#6) agent 按 `REALITY_MODE` 选 access.log；multi 遍历各容器日志。
- B4(#7) `/stats/ip_report` 鉴权对齐为仅校验 `REPORT_TOKEN`。
- B5(#8) 正则 `\\s`→`\s`。

### 阶段 C — 健壮性（下一轮）
- C1(#9) 保留 cron（定期 `DELETE` + 周期 `VACUUM`）；health 查询加时间界。
- C2(#11) `/healthz`（服务+DB 探活）；关键路径 `except` 落日志而非全吞。

## 7. 安全灰度与回滚（硬约束：线上节点不中断）

**所有改动均为 `.j2`/配置,`ansible-playbook` 执行前对线上零影响。** 三段严格隔离,staging 全绿才碰现网：

### 段 1 — 离线实现（零生产影响）
写码 + §8 三道验证(临时端口+临时 DB),全程不触线上。

### 段 1.5 — Staging 同构验证（4 台空闲 VPS,不碰现网）
搭一套与现网同构的隔离环境,用改后分支代码实测本轮 plan 是否完善。
> **详见独立文档**：[`plan-staging-env-2026-06-13.md`](./plan-staging-env-2026-06-13.md)（4-VPS 拓扑、三层隔离、ACL 独占、待用户输入项）。状态：⏸ 已暂停,择期继续。

**VPS 分配**：

| VPS | 角色 | 验证 |
|---|---|---|
| T1 | 测试监控服务端(兼节点) | A1 鉴权、A3 WAL/并发、`/healthz` |
| T2 | 节点 `reality_mode: single` | A2 single 采集、去放大、计数器重置不尖峰 |
| T3 | 节点 `reality_mode: multi` | A2 multi 采集 |
| T4 | 节点(第 4 个 agent) | A3 整点惊群并发压测 |

**隔离铁律(绝不误伤现网)**：
- 独立 `inventory.test.ini`(仅 4 台)、独立 `monitor.server_host=T1`、独立测试域名/Token/Gist;
- 所有命令强制 `-i inventory.test.ini` + `--limit`；测试期不加载生产 inventory。

**关键场景**：
- A1：测试域名挂 CF；`curl -H "X-Forwarded-For:<白名单IP>"` 应 **401**,真实白名单 IP 经 CF 访问应 **200**。
- A2：停 T1 监控→agent 攒 pending 数轮→重启→校验**无丢行**;重启 T2→校验**无巨值单条**。
- A3：4 agent cron 对齐同分钟并发→校验 `journal_mode=wal`、**零 `database is locked`**、压测时仪表板不卡。

### 段 2 — 现网金丝雀灰度（staging 全绿后,需单独授权,逐步）
1. 备份：`cp traffic_monitor.db traffic_monitor.db.bak-<ts>`（300MB）。
2. 先 `#4`（改一行头 + get_client_ip）独立灰度,优先止血线上泄露;复验 XFF=401 且白名单仍可访问。
3. 仅 spt 部署改后 server：`ansible-playbook deploy.yml --tags monitor_server --limit spt`；验证 `journal_mode=wal`、`/healthz` OK、一轮上报入库。
4. agent **金丝雀 1-2 台**→观察 `user_ip_hits` 增长、有无锁错误→余下分批。
5. 回滚：保留旧 `server.py` 与 DB 备份;WAL 可 `PRAGMA journal_mode=delete` 回退;模板 `git revert` 后重部署。

## 8. 验证（3-门循环,每个改动后从门1重跑）

1. **方向**：diff 是否只动监控四文件、是否对应各 # 项。
2. **语法/静态**：`python -c "import ast; ast.parse(render(server.py.j2))"`（渲染后解析）；`bash -n`（cron 片段）；`ansible-playbook deploy.yml --syntax-check`；YAML parse。
3. **功能**：本机渲染 server.py 启一个**临时实例(独立端口+临时 DB)**,跑 `curl` 复验 XFF=401、`/report`→入库、`journal_mode=wal`、并发写无锁错误、缓存缺失不注入尖峰。**不碰线上 8000 与生产 DB。**

> 门2 不替代门3。环境能力声明须现场重探(W-R24)。任一门失败 → 修复后从门1重跑,连续 3 次同门失败则停下上报。

## 9. 成本与轮次

- 规模：**中-大**。`server.py.j2`(1948 行)改动是外科式的(init_db / get_client_ip / auth_guard / 写端点 / 新增 healthz)；`agent.py.j2` 中等。
- 本轮只做**阶段 A**(三项止血)+ 删 `monitor.yml`(B2,零风险先带上)。B/C 余项下一轮。
- 用量风险：server.py.j2 较大,单轮可控;若触限按阶段拆。

## 10. Next Steps

- **可立即做（待批准）**：阶段 A 离线实现 + 段1 三门验证。
- **阻塞**：段2 灰度部署需单独授权(动线上);外部评审(W-R20:安全/破坏性改动合并前需 codex/ultrareview/独立 agent)。
- **可延后**：阶段 B/C；`monitor.yml` 若选"改为复用"而非删除;域名 `.de` 单独确认。
