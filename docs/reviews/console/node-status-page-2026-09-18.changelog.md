# 节点状态页：结果记录

计划：[`plan-node-status-page-2026-09-18.md`](plan-node-status-page-2026-09-18.md)（2026-09-18 批准与实施）。
路线图：[`roadmap-unified-2026-09-16.md`](../roadmap-unified-2026-09-16.md) §7 的 S6。

## 结果

- **用户状态页**：持有订阅地址的用户在订阅页点“查看所有节点的运行状态与历史”，或直接访问 `/s/<token>/status`，
  看到全部新系统节点的当前状态、近 90 天每日格与可用率，点某一天可看当天每次事件的起止时间、影响的连接方式与管理员说明。
- **管理状态页**：控制台“状态”页显示同样的内容，另有每种连接方式最近一次探测的结果、耗时与错误，并可给事件写说明。
- **判定**：`spt` 上的 `status` 服务每 60 秒用探测账号经每个节点的每种连接方式访问一次 `http://cp.cloudflare.com/generate_204`；
  连续 3 次失败开始事件（时间取第一次失败），连续 2 次成功结束；本机直接访问失败的一轮记为“无数据”，不计入可用率；
  数据空档超过两轮时结束进行中的事件；控制台隐藏节点的期间记为“维护中”。
- **探测账号**：每台新系统节点上的 `status-probe`，全部连接被固定转发到检测地址（freedom `redirect`），不能当作代理使用。
- **路由修正（路线图 C18）**：新数据面路由改为先解析域名再匹配 IP 规则，使指向环回、内网、Docker 网关与云元数据的域名
  不再能访问节点容器内的监听端口。
- **统一管理**：`edge.yml` 一次覆盖 `edge_nodes` 中的全部节点（逐台执行）；探测账号与节点上报的名单都跟随 `edge_nodes`，
  新节点部署即纳入，不再维护第二份名单。
- 已上线：`dzire`、`usca`、`legend`、`kagoya` 全部探测正常（6 个探测目标）。

## 主要改动

- 新增：`console/status.py`、`console/templates/status.html`、`subs/statuspage.py`、`group_vars/all/status.yml`、
  `tests/console/test_status.py`、`tests/edge/golden/step3-probe/`、本文与计划。
- 修改：应用器（`probe` 段与路由 `domainStrategy`）、`roles/xray_edge`（探测账号、登记文件字段）、`edge.yml`（一次覆盖全部节点）、
  `group_vars/all/edge.yml`、`console/`（数据表、状态页、事件说明、首页提醒、发布是否有变化）、`docker/console/`（加入 Xray）、
  `roles/console_service`、`console-remove.yml`、`subs/`（状态页路由与链接）、各 e2e 与单元测试。
- 文档：`docs/operations.md` §14.2、§14.3、§14.5、§14.6、§14.8；S3 与 S4 合同 §10；路线图 §0、§4（C18）、§7。

## 验证

- 本地：应用器与上报单元测试、控制台单元 19 项、状态页单元 35 项、订阅 24 项、compose 10 项 + 7 项全部通过；
  节点端到端 44/44（含 C18 三种访问方式全部被拦、SOCKS5 分流不变）；控制台端到端 49/49（含部分异常、故障、维护、无数据、
  事件说明、探测账号限制）。秘密扫描无 token 与私钥。
- 实测数据：探测流量 11.8–12.3 KiB/次（每目标每 30 天约 0.52–0.54GB）；`status` 容器 52–55MiB。
- 现场（2026-09-18）：`edge.yml` 先 `dzire` 后其余三台，各重启 Xray 一次；控制台状态页 4 台全部正常、可用率 100%、无提醒；
  各目标最近一次探测耗时 `usca` 141ms、`kagoya` 420ms、`dzire` 927ms、`legend` 880–950ms（其中一轮 5535ms）。

## 与计划的差异（已实施）

- 探测账号的限制由“只放行检测地址的域名”改为“全部连接固定转发到检测地址”：按域名放行可被绕过——嗅探到的域名只用于选路由，
  连接仍发往客户端给出的地址（独立代码审查发现）。
- 增加路由 `domainStrategy` 修正（C18），不在原计划范围，记入 S3 合同 §10。
- `status_probe_nodes` 与 `edge_report_nodes` 改为跟随 `edge_nodes`，`edge.yml` 一次覆盖全部节点（操作者要求统一管理）；
  角色中“必须用 `--limit`”的断言随之删除。
- `status` 容器内存上限 128MiB（计划写 64MiB）：含 Xray 子进程，实测 52–55MiB。
- 审查后增加：数据空档结束进行中的事件并重置计数、同一轮内的恢复与失败按时间回放、维护不早于节点加入探测的时间、
  状态页数据过期提示、`status.json` 完整校验、同一分钟不重复处理。

## 遗留

- 计划 §6.3 的退出条件（连续 24 小时无误报）尚未到时间。
- `legend` 出现过一次 5535ms 的探测耗时（其余约 900ms），疑似节点偶发 DNS 超时；若重复出现，按 `dzire` 的做法在
  `edge_container_dns` 为该节点指定 DNS。
- 旧系统（`reality_single`、`reality_multi` 模板，仍为 `IPIfNonMatch`）是否存在同样的 C18 路径未检查。
- 探测流量会计入节点出站统计，不计入用户流量。
