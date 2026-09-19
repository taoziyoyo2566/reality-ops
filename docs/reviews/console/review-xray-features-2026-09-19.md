# Xray 功能评估：哪些值得在本项目中使用

- 日期：2026-09-19
- 起因：操作者问“Xray 还有哪些好用的功能，推荐在当前项目中实现”
- 结论与去向：运行状态上报已实现（S3 合同 §10）；出口管理另立方案 [`plan-egress-console`](plan-egress-console-2026-09-19.md)；
  25 端口屏蔽操作者决定不做

依据当前版本 v26.3.27 的 `xray help api`，以及官方文档与 issue。

| 功能 | 评估 |
|---|---|
| 在线 IP 统计（`statsUserOnline`、`statsonlineiplist`） | 本文 B 使用 |
| 按来源 IP 屏蔽连接（`xray api sib`） | 可用于紧急屏蔽某个来源；IP 常变，不宜作为日常手段 |
| 运行中增删出口与路由规则（`ado`、`rmo`、`adrules`、`rmrules`） | 可让出口（如 ISP 的 SOCKS5）改由控制台管理、经 agent 在运行中生效，不重启 Xray；配合 observatory 做出口健康检查与失效回退。现有出口在 `edge.yml` 中配置，改动会让该节点 Xray 重启。待操作者决定 |
| Xray 运行状态（`xray api statssys`：内存、协程数、运行时长） | 2026-09-19 操作者同意并已实现：agent 随每次上报带回，节点列表与节点页显示，内存达到 `console_xray_memory_alert_mib`（默认 240 MiB，`edge_memory_limit` 300m 的 80%）时首页提醒 |
| 屏蔽发往 25 端口的出站 | 2026-09-19 操作者决定不做 |
| 屏蔽 BT、禁止访问内网地址 | 已有（`block-bt`、`block-private`） |
| REALITY 回落限速（`limitFallbackUpload/Download`） | 不做：官方文档说明限速本身就是特征，不建议使用 |
| 按用户限速、限设备数 | 官方 Xray 没有；相关提案（XTLS/Xray-core#3667）以 not planned 关闭 |
| REALITY 抗量子签名（`mldsa65Seed`）、VLESS Encryption | 需要目标网站满足条件（证书长度、X25519MLKEM768）与客户端支持；路线图 S8 |
| WireGuard 出站（Cloudflare WARP） | 可让个别被目标网站封禁的节点经 WARP 访问这些网站；需要另外的 WARP 账号，按需再议 |
