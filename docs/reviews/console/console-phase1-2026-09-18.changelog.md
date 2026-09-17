# 管理控制台第一阶段：结果记录

计划：[`plan-console-phase1-2026-09-16.md`](plan-console-phase1-2026-09-16.md)（2026-09-17 批准，2026-09-18 完成）。
路线图：[`roadmap-unified-2026-09-16.md`](../roadmap-unified-2026-09-16.md) §7.0 的 P1。

## 结果

- **管理控制台**（`spt`，compose 项目 `reality-console`）：管理员在页面上发放、重置、吊销订阅地址，查看地址与二维码、
  最后拉取时间、节点状态、错误日志末尾与按用户 / 节点 / 日 / 月的流量，控制节点是否出现在订阅中，查看操作与发布记录。
  管理页面只在宿主机 `127.0.0.1:8200`；上报服务经控制台自己的隧道以 `report.taoziyoyo.com` 对外，没有管理页面路由。
- **订阅数据由控制台发布**（D-C1～D-C3）：`subs.yml` 只部署订阅服务；新订阅只包含控制台中显示的新系统节点；
  `test` 的地址在首次导入后保持不变。
- **节点上报**：`xray-edge` compose 新增 `reporter`，只读 Xray metrics，每 5 分钟上报各用户流量、443 监听与错误日志末尾；
  控制台不可达时暂存并按序补报。`edge.yml` 部署时向控制台登记节点（注册文件），并可轮换节点上报 token。
- **D-C5**：`edge.yml` 对新旧实例用户集合只提示差异，不再中止；删除 `edge_drift_old_only_users`。
- 已上线：dzire、usca、legend、kagoya 全部登记并开启上报。

## 主要改动

- 新增：`console/`、`docker/console/`、`roles/console_service`、`console.yml`、`console-remove.yml`、`group_vars/all/console.yml`、
  `docker/edge-tools/edge_reporter.py`、`tests/console/`、`tests/edge/test_reporter.py`。
- 修改：应用器（metrics 监听、上报 token）、`roles/xray_edge`（注册、`reporter` 服务、D-C5）、工具镜像、`edge-remove.yml`、
  `subs.yml`、`subs-remove.yml`、`roles/subs_service`（数据目录交给控制台）、`group_vars/all/edge.yml`、`group_vars/all/subs.yml`。
- 文档：路线图另立 2026-09-16 版；`docs/operations.md` §14；新增 `docs/runbooks/cloudflare-tunnels.md`；S3、S4 计划 §10 记录变更。

## 验证

- 本地：应用器与上报程序单元测试 33 项、edge compose 7 项、控制台 compose 7 项、控制台单元 19 项（含镜像内）、订阅服务测试、
  SOCKS5 门控 7 项、节点端到端 42/42、控制台端到端 29/29；秘密扫描无 token 与私钥。
- 现场（2026-09-17～18）：控制台导入并发布成功，`test` 订阅经公网取得 6 条链接；`report.taoziyoyo.com` `/healthz` 200、
  管理路径 404、无 token 401；4 台节点上报正常；停止上报服务 18 分钟后首页显示 4 台离线，恢复后暂存上报按序补齐、流量不重复；
  隐藏再显示 legend 后设备刷新订阅结果一致。

## 与计划的差异（已实施）

- 上报程序除“计数变小”外，也以 Xray 日志中的启动记录判断重启：本地端到端发现仅靠计数会漏判。
- 上报程序使用自己的 User-Agent：Cloudflare 对 Python 默认 User-Agent 返回 403（error 1010）。
- 首次开启上报时各节点 Xray 重启一次（新增 metrics 配置），容器未重建。
- 离线与补报演练以停止控制台上报服务的方式进行，使节点实际产生暂存；计划原写停止节点上的 reporter。
- 新增 `edge_rotate_report_token`，经 `edge.yml` 轮换节点上报 token。

## 遗留

- Xray v26.3.27 的 API 只监听 TCP，`reporter` 与 Xray 共用网络命名空间时仍能访问 API；只靠程序不调用来约束（计划 §7）。
- vault 中的 `vault_subs_tokens` 已导入控制台、不再使用，删除需另行授权。
- Shadowrocket 全局模式下 WebRTC 显示真实 IPv4，原因与方案待调查（路线图 §7.2），不属于本阶段。
- 部署早于本次提交（基于 `ops@2d55b3c` 的工作树）；本次提交的内容与节点和控制台上运行的一致。
