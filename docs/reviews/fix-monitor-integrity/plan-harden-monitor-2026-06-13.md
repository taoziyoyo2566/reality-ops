# 计划：流量监控子系统完整性与安全加固

- **创建**：2026-06-13　**修订**：2026-06-21（按外部评审 + 现网拓扑实测校准，见 §0.1）
- **级别**：架构（跨组件子系统：control/data 分离、RBAC、可观测性、一致性、扩展性、审计）
- **分支**：`fix/monitor-integrity`（叠加在 `feat/single-socks5-egress`，因监控子系统仅存在于该 feat 线，trunk `ops` 不含）
- **基线提交**：`89aefeb Add node decommission workflow`
- **作者身份**：Silent Praxis（host 全局 gitconfig，W-R25 通过；身份值不入文档，W-R14）

---

## 0. 最佳实践前置检查（W-R18）

- **鉴权真实客户端 IP**：FastAPI/Starlette 官方做法是 `ProxyHeadersMiddleware` / `uvicorn --proxy-headers --forwarded-allow-ips`，且**仅信任来自可信代理对端的转发头**。当前实现自写 `get_client_ip`、读可伪造的 `X-Forwarded-For`、且把 `127.0.0.1` 放进白名单又绑 loopback → 偏离最佳实践。结论见 §0.1（拓扑实测后大幅简化）。
- **SQLite 并发**：官方推荐高并发读写用 `journal_mode=WAL` + `busy_timeout`。当前为 `delete`（回滚日志，写锁整库）→ 偏离。结论：启用 WAL。
- **异步框架阻塞**：Starlette 官方约定——阻塞 IO 应放线程池（同步 `def` 端点自动入线程池，或 `run_in_threadpool`）。当前 `async def` 内同步调 `sqlite3` → 阻塞事件循环，偏离。结论：DB 端点改同步 `def`。
- **采集投递可靠性**：通用最佳实践是"投递**确认成功**才推进 offset/cursor"。当前 `requests.post` 既无条件推进缓存、又**不检查 `status_code`**（`agent.py.j2:160,203` 套 `except: pass`，4xx/5xx 也算成功）→ 偏离。结论：失败不推进 + 持久化待发队列 + 显式校验 `status_code==200`。

来源：FastAPI/Starlette 官方文档（proxy headers、threadpool）、SQLite 官方 WAL 文档、Uvicorn 部署文档、CF Tunnel 文档。方向正确，进入正文。

### 0.1 现网拓扑实测（2026-06-21，于 spt 本机取证）—— 改写 #4 威胁模型

| 事实 | 证据 |
|---|---|
| `monitor.taoziyoyo.com` 经 **CF Tunnel** → `127.0.0.1:8000` | 本机 `cloudflared … tunnel run --token`（ingress 在 CF 仪表盘）；uvicorn 绑 `internal_host:127.0.0.1`（`server.py.j2:1948`、`main.yml:37`） |
| nginx 在跑、监听公网 `0.0.0.0:443/:80`，**但不路由 monitor** | `ss -ltn` + 用户确认 |
| spt 即本机 | `inventory.ini` `spt ansible_connection=local` |
| **loopback 白名单后门——实测 200** | `curl 127.0.0.1:8000/stats/daily`（无任何头）→ **200**；`/debug/whoami` 无头 → `client_ip=raw_client=127.0.0.1`，命中 `ip_allowlist` |

**结论（对评审第 1 条的校准）**：纯 CF Tunnel 下 **8000 仅 loopback、无公网直连源站路径**，评审担心的"外部伪造 `CF-Connecting-IP` 直连源站"前提**不成立** → **无需 nginx realip + 源站防火墙那套重型方案**。真正坐实的暴露是 **loopback 白名单后门**：`127.0.0.1` ∈ `ip_allowlist`（`main.yml:43`）+ 服务绑 loopback + cloudflared/nginx/agent 同在本机 → **spt 上任意本地进程零 token 可读全部流量 + 去匿名化 IP 史**。修法因此从架构级改造降为轻量配置收口（§6 A1）。

---

## 1. 目的

修复监控子系统三类已实测确认的问题：(a) **本地零鉴权读全量数据/IP 史的 loopback 后门**（含线上 `X-Forwarded-For` 伪造旁路）；(b) **系统性数据丢失与幻象尖峰**导致统计不可信；(c) **用户-IP 审计功能 68 天全死**（三个叠加 bug，本轮一并修，否则监控修完审计仍坏）。在**不中断线上运行节点**的前提下完成。

## 2. 范围（In-Scope）

| 文件 | 改动性质 |
|---|---|
| `group_vars/all/main.yml` | `ip_allowlist` 移除 `127.0.0.1`、保留运维真实 IP（D1-B）；`trust_proxy_header` → `CF-Connecting-IP`；新增 `vault_monitor_tunnel_secret`（CF 注入头校验值）|
| `roles/monitor/templates/server.py.j2` | A1 D1-B 闭合（`auth_guard` 加 CF secret 头门控 + 保留白名单，见 §6）；`init_db` 启 WAL；DB 端点去阻塞 + 写重试；`/stats/ip_report` 鉴权降为仅 `REPORT_TOKEN`（B4）；health 查询加时间界；新增 `/healthz`；**顶部 `REPORT/ADMIN/STATS_*TOKEN`(:19-21) 改读 0600 env、不再明文渲染** |
| `roles/monitor/templates/agent.py.j2` | 失败不推进缓存 + 持久化待发队列 + `status_code==200` 才推进；缓存迁出 `/tmp`；去除容器放大；计数器重置不再注入尖峰；正则 `\\s`→`\s`（B5）；按模式选 access.log（B3）；`report_token` 外置读取 |
| `roles/monitor/tasks/main.yml` | server `User=` 专用非 root + `server.py` `0640`；server/agent token + D1-B `TUNNEL_SECRET` 均外置 0600 env；cron `user=reality-monitor-agent`（playbook 新建专用用户，见 §6 D2）+ 随机抖动；新增数据保留 cron |
| `monitor_server.py`、`monitor.yml`（根，已腐化死文件，均 git 跟踪） | `git rm`（彻底删，非 `--cached`）；`monitor_server.py:9` 硬编码 token 须**轮换**（删文件不消除历史泄露，W-R13）|

## 3. 不做（Out-of-Scope，留后续轮次/单独确认）

- `domain_suffix: "taoziyoyo.de"`（main.yml）与既有约束（节点仅 `.com` 可用）冲突 —— 属订阅域名，非监控，单独确认。
- **agent 鉴权设计**：`report_token` 全节点共享同一个（vault `vault_monitor_report_token`）→ 任一节点（本地用户皆 docker 组 = root 等价）被攻破即可伪造全局上报；配合 B4 放大。改 per-node token / mTLS 属鉴权架构，**列后续轮**。
- **消除 agent 的 docker 特权依赖**：暴露 xray API 端口 + 宿主侧读网卡/日志，使节点用户可退出 docker 组（真正最小权限）—— 动 `reality_single/multi` 角色，单独轮次。
- 迁移时序数据库 / 多写聚合（#10 中长期形态）；把 49 个未合并提交并入 trunk；仪表板（内嵌 HTML）重构。

## 4. 验收标准

- **A1（鉴权，D1-B）**：本机 `curl 127.0.0.1:8000/stats/daily`（无 secret 头）= **401**（当前 200），伪造 `-H 'CF-Connecting-IP:<白名单IP>'` 仍 **401**；带 `Authorization: Bearer <stats_token>` = 200；经 CF 域名 + 运维白名单 IP（CF 注入 secret 头）= 200 且**仪表板正常**；经 CF 域名 + 非白名单 IP = 401。`debug/whoami` 不再含 `ip_allowlist`。
- **A2（采集）**：节点重启 / 缓存丢失后**不再**产生等于累计计数的单分钟巨值；投递失败的 delta 进 pending、下周期重试入库（注入失败再恢复，校验无丢行、无重复行）；服务端返回非 200 时缓存**不**推进。
- **A3（并发）**：`PRAGMA journal_mode` = `wal`；整点并发上报无 `database is locked`；仪表板整点不卡。
- **B（IP 审计）**：注入一次上报后 `user_ip_hits` 出现新行（当前恒 0）——三 bug（B3 路径/B4 鉴权/B5 正则）需同时修复才成立；`monitor.yml`/`monitor_server.py` 已从 git 移除。
- **权限**：server 以专用非 root 用户运行；节点脚本不含明文 token（token 在 0600 文件）。
- **C**：保留 cron 存在；`/healthz` 返回服务与 DB 状态。
- **三道验证门**全绿（见 §8）。

## 5. 现状差距分析（含实测证据）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| #4 | loopback 白名单后门 + `X-Forwarded-For` 伪造 | `curl 127.0.0.1:8000/stats/daily` 无头 = **200**；`whoami` 无头 client_ip=127.0.0.1；切 `XFF:127.0.0.1` 经域名亦 200 | 本机任意进程 / 历史公网 XFF 旁路读全部流量 + 用户 IP 史（去匿名化） |
| #1 | 投递失败仍推进缓存 + 不查 status + `/tmp` 缓存 | `hk-hn/frank` 06-11 四条各 **46.3GB 单分钟**（≈185GB 幻象）；缓存在 `/tmp`（重启即丢）；`agent.py.j2:160,203` 不读 `status_code` | 统计单向少计 + 重启假尖峰，数据不可信 |
| #2 | 无 WAL + 单线程阻塞 | `journal_mode=delete`；服务 `Tasks:1`；DB 300MB/410万行；多节点 cron 全 `minute:*` | 整点惊群锁冲突 → 500 → 配合 #1 永久丢数 |
| #6 | IP 日志路径硬编码 `reality_core` | `agent.py.j2:14`；6 个 multi 节点无该容器；`user_ip_hits=0` | multi 节点 IP 审计无数据 |
| #7 | `/stats/ip_report` 双重鉴权 | `server.py.j2:1640` 端点要 `auth_guard`+token，agent 只发 token；节点公网 IP 不在白名单 | IP 上报被 401 静默丢弃 |
| #8 | 正则 `\\s` 被原样渲染 | `agent.py.j2:182-183`（Jinja 不处理反斜杠，`email:\\s*` 匹配不上真实日志的空格） | `user_match` 恒 None → IPv6/email 解析失效 |
| #3 | 容器流量"放大对齐" | `agent.py.j2:143-155`，`max(1.0,…)` 只增不减 | single 模式 per-user 数被人为膨胀 |
| #5 | `monitor.yml`/`monitor_server.py` 腐化副本 | 引用的 `monitor/server.py` 等全缺失；无引用；`monitor_server.py:9` 硬编码 token；均 git 跟踪 | 误运行会失败/冲突；提交进 git 的凭证（W-R13） |
| #9 | 无数据保留 | 300MB/410万行/68天，从未清理；health 查询无时间界 | 单调增长，查询劣化 |
| #11 | 自身无可观测性 | 全局 `except: pass`，无 `/healthz` | 故障静默 |

> **#6+#7+#8 三者叠加、各自独立把 `user_ip_hits` 清零** = 用户-IP 审计 / 共享检测（`audit.yml`、`/stats/ip_matrix`）**68 天全死**。**只修 #6/#7/#8 中任一两项，审计仍为 0** → 故本轮三者必须一并修（评审第 4 条）。

## 6. 逐项改动方案与理由

### 阶段 1 — 安全热修（最小、可独立先合，优先止血）

**A1 鉴权彻底闭合本机绕过（#4，评审二/三轮校准；已定 D1-B CF 共享密钥头 2026-06-21）**

威胁模型 = "本机任意进程零 token 可读"。D1-A 纯 Bearer 会废掉现有仪表板（JS `fetch` 不带 `Authorization`(`server.py.j2:1404/1421/1442`)、export 是 `window.open`(`:1485`)，评审第三轮），故改 **D1-B：CF 边缘注入共享密钥头 + 保留运维 IP 白名单**，正交两层闭合：
- **CF 侧**：对 `monitor.taoziyoyo.com` 用 Cloudflare **Transform Rule（Modify Request Header → Set，覆盖客户端同名头）** 注入 `X-Monitor-Tunnel-Secret: <强随机>`（存 vault；经 CF 才有，客户端自带的同名头被 Set 覆盖）。
- **server `auth_guard`（`server.py.j2:1599`）** 放行条件改为：`hmac.compare_digest(请求头 X-Monitor-Tunnel-Secret, TUNNEL_SECRET)` 成立 **且** `is_ip_allowed(get_client_ip())`（即"确经 CF 边缘" ∧ "是运维白名单 IP"）；否则查 `Bearer`；都不满足 → 401。
- **闭合本机绕过**：本机进程直连 8000 不经 CF → 无 secret 头 → 即便伪造 `CF-Connecting-IP:<白名单IP>` 也进不了白名单分支 → 要 Bearer → 401。secret 在 0600 env，非特权本机进程读不到。
- **挡外部**：外部经 CF 域名虽被 CF 加上 secret 头，但其 `CF-Connecting-IP` 不在白名单 → 仍要 Bearer → 401。
- `main.yml:43`：`ip_allowlist` **移除 `127.0.0.1`**（本机管理改 Bearer），保留运维/CF 真实 IP（`is_ip_allowed`/`IP_ALLOWLIST` 保留）。
- `main.yml:45`：`trust_proxy_header` → `CF-Connecting-IP`；`get_client_ip` 取不到不 fallback `request.client.host`。
- `server.py.j2:1614-1624 debug/whoami`：去 `ip_allowlist` 回吐。
- `uvicorn.run`（`server.py.j2:1948`）：显式 `proxy_headers=True, forwarded_allow_ips="127.0.0.1"` + 钉 uvicorn 版本（W-R24）。
- **仪表板零改动**：浏览器经 CF 域名访问，CF 自动加 secret 头、运维 IP 在白名单 → `fetch`/export 原样 200，无任何 UI 改造。`/report`、`/stats/ip_report` 仍走 `REPORT_TOKEN`。
- **CF 配置依赖**：若 Transform Rule 漏配/未覆盖客户端头，退化为"仅白名单"（本机伪造又能进）→ 验收必须实测"本机无 secret 头 = 401"。

**安全债清理（#5，零运行风险，先带上）**
- `git rm monitor.yml monitor_server.py`（直接删工作区+索引；二者均死文件、非保留型，故**不用** `--cached`，避免留 untracked 后被误加回）。**历史泄露**：`monitor_server.py:9` 的 token 已进 git 历史，删文件**不能**消除 → **必须轮换该 token** 使历史值失效；彻底抹除历史需 `git filter-repo`/BFG 改写（W-R19 破坏性，单独评估）。

**server 降权 + token 外置（权限，评审第 2/3 条）**
- `tasks/main.yml` systemd `User=` 改专用非 root 用户（server 只需 loopback:8000 + 写自有 sqlite，不需任何 root 能力）；db `0600`、数据目录归该用户；可叠 `NoNewPrivileges/ProtectSystem`。
- **server token 外置（评审第 2 条）**：`server.py.j2:19-21` 现明文渲染 `REPORT/ADMIN/STATS_*TOKEN`（世界可读 server.py 含 admin bearer）。改为 server 启动读 `0600` env 文件（属主=专用用户，含 D1-B 的 `TUNNEL_SECRET`），模板不再渲染明文；过渡期至少 `server.py` 收 `0640`（专用用户:root）。

### 阶段 2 — 数据可信度 + IP 审计（同轮）

**A2 采集可靠性（#1）**
- `agent.py.j2`：缓存 `/tmp/reality_traffic_cache.json` → `/opt/reality/monitor/state/traffic_cache.json`（随机器持久）。
- **逐用户成功才推进该用户缓存**；推进的判据是 **`resp.status_code == 200`**，非"无异常"。失败 delta 累加到持久 `pending` 队列，下周期合并重试。
- **pending 实现约束（评审第 5 条）**：状态文件**原子写**（temp + `os.replace`）；**单实例文件锁**（`flock`/`O_EXCL`）防 cron 重叠双计——尤因本轮新增 jitter 拉长运行时间而加剧；pending **上限**（按用户累加结转、非无界追加）+ 保留时长；失败落**日志**（不再 `except: pass`）。
- **计数器重置/缓存缺失**：`last` 不含该用户（首见）时**只建基线、当周期上报 0**，不再把累计计数当 delta → 根除 46GB 尖峰。

**A3 SQLite 并发（#2）**
- `server.py.j2 init_db`：`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`；统一连接 helper 设 `busy_timeout=5000`。
- 写端点（`/report`、`/stats/ip_report`）改同步 `def`（自动入线程池）+ 写失败重试 N 次。
- `tasks/main.yml` cron：`job: "sleep $((RANDOM \% 45)); …"` 打散整点惊群；`user=reality-monitor-agent`。**已定（D2）：playbook 新建专用 `reality-monitor-agent`**——`user` 模块建用户 + 加 `docker` 组 + `file` 设 token/state/log 目录属主（state/pending `0700`、token `0600`）。新建用户任务须在 agent 部署前、对所有节点执行；须确保该用户能 `docker exec` 且拥有 state/pending/token 文件。

**IP 审计（#6/#7/#8 —— 三者一并，从原"下一轮"提进本轮）**
- **B3(#6)**：agent 按 `REALITY_MODE` 选 access.log；multi 遍历各容器日志（不再硬编码 `reality_core`）。
- **B4(#7)**：`/stats/ip_report` 去掉 `Depends(auth_guard)`，鉴权对齐为仅校验 `REPORT_TOKEN`（与 `/report` 一致）。⚠️ 与"共享 token"结构性问题相关（§3 后续轮），本轮先恢复功能。
- **B5(#8)**：正则 `\\s`→`\s`（`agent.py.j2:182-183`）。

**agent secret 外置（权限，卫生项非止血）**
- `report_token` 从脚本明文渲染改为读 `0600` 文件（属主=各节点 docker 组运行用户）；脚本本身 0755 无妨。**注**：节点本地用户普遍 docker 组 = root 等价，该用户读 token 冗余无所谓；外置的实际收益是避免明文 token 散落进每台节点世界可读文件（备份/快照/误读）→ 故定为低成本卫生项，非高危。

### 阶段 3 — 健壮性（本轮体量允许则带，否则紧邻下一轮）
- **B1(#3)** 去容器放大，直接信任 xray uplink/downlink。
- **C1(#9)** 保留 cron（定期 `DELETE` + 周期 `VACUUM`）；health 查询加时间界。
- **C2(#11)** `/healthz`（服务+DB 探活）；关键路径 `except` 落日志而非全吞（与 A2 失败日志一致）。

## 7. 安全灰度与回滚（硬约束：线上节点不中断）

**所有改动均为 `.j2`/配置，`ansible-playbook` 执行前对线上零影响。** 三段严格隔离，staging 全绿才碰现网：

### 段 1 — 离线实现（零生产影响）
写码 + §8 三道验证（临时端口 + 临时 DB），全程不触线上。

### 段 1.5 — Staging 同构验证（4 台空闲 VPS，不碰现网）
> **详见**：[`plan-staging-env-2026-06-13.md`](./plan-staging-env-2026-06-13.md)（4-VPS 拓扑、隔离层、ACL 独占、**playbook guard**、待用户输入）。状态：🔧 部分恢复，监控验证待本轮代码就绪。

**关键场景**：
- A1：测试监控经 CF Tunnel；本机 `curl 127.0.0.1:8000/stats/daily` 无头应 **401**，带 Bearer 应 200。
- A2：停测试监控→agent 攒 pending 数轮→重启→**无丢行、无重复行**；重启节点→**无巨值单条**。
- A3：4 agent cron 对齐同分钟并发→`journal_mode=wal`、**零 `database is locked`**、压测仪表板不卡。

### 段 2 — 现网金丝雀灰度（staging 全绿后，需单独授权 + 外部评审 W-R20，逐步）
1. 备份：`cp traffic_monitor.db traffic_monitor.db.bak-<ts>`（300MB）。
2. 先合**阶段 1 安全热修**（A1 D1-B + 删旧文件 + server 降权）独立灰度，优先止血；**先在 CF 配好 Transform Rule 注入 secret 头**，再复验本机 `/stats/daily` 无 secret 头=401、经 CF 域名运维白名单=200、仪表板正常。
3. spt 部署改后 server：`ansible-playbook deploy.yml --tags monitor_server --limit spt`；验证 `journal_mode=wal`、`/healthz` OK、一轮上报入库。
4. agent **金丝雀 1-2 台**→观察 `user_ip_hits` 增长、有无锁错误、pending 是否正常回收→余下分批。
5. 回滚：保留旧 `server.py` 与 DB 备份；WAL 可 `PRAGMA journal_mode=delete` 回退；模板 `git revert` 后重部署。

## 8. 验证（3-门循环，每个改动后从门1重跑）

1. **方向**：diff 是否只动范围内文件、是否对应各 # 项。
2. **语法/静态**：`python -c "import ast; ast.parse(render(server.py.j2))"`（渲染后解析）；`bash -n`（cron 片段）；`ansible-playbook deploy.yml --syntax-check`；YAML parse。
3. **功能**：本机渲染 server.py 起**临时实例（独立端口 + 临时 DB）**，`curl` 复验 `/stats/daily` 无头=401 / 带 Bearer=200、whoami 不回吐 allowlist、`/report`→入库、`journal_mode=wal`、并发写无锁错误、缓存缺失不注入尖峰、注入 IP 上报后 `user_ip_hits` 增行。**不碰线上 8000 与生产 DB。**

> 门2 不替代门3。环境能力声明须现场重探（W-R24）。任一门失败 → 修复后从门1重跑，连续 3 次同门失败则停下上报。

## 9. 成本与轮次

- 规模：**大**（范围较 06-13 版扩大：纳入 IP 审计三修、权限收紧、删旧文件）。`server.py.j2`(1948 行) 改动外科式；`agent.py.j2` 改动较多（pending/锁/原子写/正则/日志路径/token 外置）。
- **建议实现按 §6 阶段拆 round**：阶段 1（安全热修，最小、可先合）→ 阶段 2（采集+IP审计）→ 阶段 3（健壮性）。每 round 独立过三门 + changelog。
- 用量风险：单 round 控制在一个阶段内；触限再细拆。**阶段划分（3+ 任务）实现前需用户 sign-off。**

## 10. Next Steps

**已定决策（2026-06-21，第二轮评审后）**
- **D1 = D1-B CF 共享密钥头**（三轮评审改定）：CF 注入 secret 头 + 保留运维 IP 白名单，正交闭合本机绕过；**仪表板零改动**（D1-A 纯 Bearer 会废掉前端）。详见 §6 阶段1 A1。
- **D2 = 新建专用 `reality-monitor-agent`**：playbook 自建用户 + 加 docker 组 + 设目录属主。详见 §6 阶段2。

- **可立即做（待批准）**：阶段 1 离线实现 + 段1 三门验证。
- **阻塞**：段2 灰度需单独授权（动线上）；外部评审（W-R20，本计划已经一次外部评审校准）。
- **可延后（已入 §3）**：agent per-node token / mTLS；消除 docker 依赖；`.de` 域名确认。
- **运维侧（本计划外，建议顺手）**：cloudflared tunnel token 明文暴露在进程命令行（`ps` 可见），轮换并改 credentials-file。
