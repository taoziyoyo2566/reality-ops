# 新增两份方案文档调查与 Review

- **Review 日期**：2026-07-26（JST）
- **Review 范围**：
  - [`plan-ipv6-dualstack-2026-07-26.md`](ipv6-dualstack/plan-ipv6-dualstack-2026-07-26.md)
  - [`plan-reality-dest-2026-07-26.md`](reality-dest/plan-reality-dest-2026-07-26.md)
- **仓库基线**：`fix/monitor-integrity`，HEAD `bcd51e7`
- **结论性质**：只审查计划和证据，不实施方案、不访问生产节点、不发布 Git。

## 总结结论

两份文档的现场调查价值较高，主要方向也基本成立：

- Xray 的 `sockopt.domainStrategy: UseIP` 配合 `happyEyeballs` 是官方支持的 TCP 双栈方案；
- REALITY 使用 Apple `dest`、非 443 入站端口以及证书记录接近 8192 字节，确实存在上游已明确提示或已复现的风险；
- 443 合并涉及用户路由、密钥隔离和 `spt` 的 nginx 冲突，单独拆分计划是合理的。

但两份计划目前都不应直接进入实施：有若干 P1 级的验证、版本固定和部署行为缺口。最重要的修订是：把“IPv4/IPv6 开关”明确为“偏好”还是“硬选择”，用真实 VLESS 流量验收；同时把 Xray 镜像固定到可复现的版本和 digest，并按 single/multi role 分别处理重启。

## Findings

### P1-1：IPv6 验收命令没有验证被修改的 Xray 出口

`plan-ipv6-dualstack` §4（约 350-360 行）要求在容器网络命名空间内执行 `curl`。这只能证明容器自身能访问 IPv6；它没有经过用户入站、路由和 `freedom` outbound，因此不能证明新增的 `streamSettings.sockopt.happyEyeballs` 被 Xray 使用。`ipv6.icanhazip.com` 还是 IPv6-only 目标，不能比较双栈竞速结果。

改进：用真实客户端通过一个 jp10 的 VLESS/REALITY 入站访问一个可控的双栈 TCP 回显目标，记录远端看到的地址族、连接成功率、首选地址和失败回退；同时保留容器直连作为网络基线。`ipv4`/`ipv6` 两个模式都要测，UDP/QUIC 单独标记为“不覆盖”。

### P1-2：`node_egress_family` 的名字和效果不一致

计划把 `ipv6`/`ipv4` 都实现为 `UseIP + happyEyeballs`，仅通过 `prioritizeIPv6` 调整队列顺序。这是“偏好”，不是“控制出口族”：IPv6 偏好模式仍可能在 IPv6 较慢时选 IPv4，IPv4 偏好模式也可能最终选 IPv6。Xray 官方文档明确说 `happyEyeballs` 会竞速并选择第一个成功地址，`prioritizeIPv6` 只控制排序后的首选族（[官方 Sockopt 文档](https://xtls.github.io/en/config/transports/sockopt.html)）。

这与 §2 的“控制该节点使用 IPv4 或 IPv6”存在语义偏差。应先记录操作决策：

- 如果目标是自适应容灾，将变量改名为 `node_egress_preference`，并把验收指标写成“偏好/回退比例”；
- 如果目标是硬选择，使用 `UseIPv4`/`UseIPv6` 或明确的 `Force*` 策略，并单独定义 IPv6 不可用时是失败还是回退。

另外，必须给变量增加 Ansible `assert`，只允许 `auto|ipv4|ipv6`；未知值不能静默退回 `auto`。

### P1-3：镜像“固定 tag”不足以满足仓库已有的可回滚策略

`plan-ipv6-dualstack` §2.5 建议把 `xray_image` 从 `:latest` 改成版本 tag。仓库实际情况更严格：`scripts/check_pinned_updates.py:43-59` 要求 `xray_image` 使用 digest，而 `group_vars/all/main.yml:3` 仍是 mutable `:latest`；该检查还期待 `xray_image_update_source`，当前仓库没有对应变量，因此检查本身未形成可用闭环。

此外，`docker-build/dockerfile:1-25` 同时使用 `alpine:latest` 和 `releases/latest`。即使 Compose image 使用版本 tag，重建仍可能得到不同的基础镜像或不同 release 内容，不能保证复现和回滚。

改进：Dockerfile 使用显式 `XRAY_VERSION` 下载固定 tag，基础镜像固定 digest；发布后将最终多架构镜像记录为 digest，并在 `xray_image` 使用 digest。更新脚本保留一个带版本 tag 的 `xray_image_update_source`，部署前校验镜像 digest 和 `docker exec ... xray version` 一致。不要把 mutable tag 称为“pinned”。

### P1-4：重启语义按 role 不同，原文的统一表述不准确

`plan-ipv6-dualstack` §5.1 和 `plan-reality-dest` §7.2 将“写入 config 后不会重启”描述为普遍行为，但仓库实现不同：

- single role 在 `roles/reality_single/tasks/main.yml:234-303` 注册 `config_changed`，并把它传给 `docker_container.restart`，配置变化时会自动重启；
- multi role 在 `roles/reality_multi/tasks/main.yml:161-229` 同步 bind-mounted `config.json`，之后执行 Compose `state: present`，配置文件内容变化本身未必触发容器重建，通常需要显式 restart/recreate。

改进：部署步骤按 `reality_mode` 分开写，明确“生成配置、重启/重建、健康检查、再刷新订阅”的顺序，并把实际容器版本和配置 hash 记录进验收证据。任何包含 `users`/`gist` 的完整部署都要显式防止不必要的全局 Gist 重写；`deploy.yml:191-207` 的 post task 会在配置 token 时更新整个 Gist。

### P1-5：`happyEyeballs` 方案需要绑定 Xray 版本和 Freedom 安全策略

Xray 官方文档要求 `happyEyeballs` 只能在 `sockopt.domainStrategy != AsIs` 时生效，并推荐 `UseIP + interleave`；因此计划的 JSON 结构本身是合理的。可是当前官方 Freedom 文档还说明：新版本存在默认 `finalRules` 安全策略，执行规则时可能先把域名解析成 IP，随后 `sockopt` 和 `happyEyeballs` 不再生效（[官方 Freedom 文档](https://xtls.github.io/en/config/outbounds/freedom.html)）。计划 §2.6 已提到 v26.5+ 的 `finalRules`，但没有把它变成升级闸门。

改进：在每个支持的 Xray 版本上分别做 `xray -test` 和真实双栈行为测试；明确当前 25.12.8/目标 26.3.27 是否没有该策略，以及未来升级到 26.5+ 时如何处理。不能把“当前配置可用”外推为“未来版本仍会调用 sockopt”。

### P1-6：现有 `daemon.json` 覆盖风险应成为 rollout 闸门，而不是旁注

计划 §6.1 已正确发现 `roles/reality_single/tasks/main.yml:94-103` 用完整 `copy` 覆盖 `/etc/docker/daemon.json`，可能抹掉 `usca` 的 IPv6 参数或 `sg` 的日志轮转配置。由于 IPv6 计划把 `usca` 放入首批节点，若执行完整部署，这个既有破坏性行为会与本次变更同批发生。

改进：在进入 IPv6 canary 前先将 daemon 配置改为结构化合并，或明确只允许不触发 `system/docker` 标签的受控部署路径；验收中加入部署前后 daemon 配置 diff。否则“未改动的节点保持 byte-identical”并不能覆盖宿主 Docker 配置被改写的风险。

## P2 改进项与事实修正

### P2-1：更新社区 issue 的状态和措辞

`plan-ipv6-dualstack` §2.6 把 #6256 列为“known open issue”，但截至本次核查该 issue 已是 **Closed as not planned**；它仍可作为 v26.3.27 的兼容性风险证据，但不能写成 open。相反，REALITY 证书记录问题 [#6356](https://github.com/XTLS/Xray-core/issues/6356) 仍有复现记录，相关的 17 KiB buffer 修复 PR [XTLS/REALITY #33](https://github.com/XTLS/REALITY/pull/33) 仍是 open，说明 8192 限制在目标版本上仍应按真实行为验证。

### P2-2：`minClientVer` 的风险要写成条件性兼容矩阵

官方提交 [af7eb68](https://github.com/XTLS/Xray-core/commit/af7eb680) 确实把未配置的 REALITY `minClientVer` 默认值改为 `26.3.27`。社区的 [mihomo #2967](https://github.com/MetaCubeX/mihomo/issues/2967)、[Xray #6477](https://github.com/XTLS/Xray-core/issues/6477) 和 [3x-ui #5922](https://github.com/MHSanaei/3x-ui/issues/5922) 都记录了 v26.7.11 与旧 mihomo 客户端的失败现象。

但 v26.7.11 当前仍标为 prerelease，而 v26.3.27 是官方 Latest（[v26.3.27 release](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27)、[v26.7.11 release](https://github.com/XTLS/Xray-core/releases/tag/v26.7.11)）。因此原文“以后会锁死每个 mihomo 用户”应改成：“当采用含该提交的版本、且服务端未显式设置较低 `minClientVer` 时，当前仍声明 1.8.2 的 mihomo 客户端会被拒绝”。升级验收必须列出实际客户端族（尤其 mihomo/Clash.Meta）和允许的最低版本，不能只测 Xray 客户端。

### P2-3：证书大小探测需要“预筛选 + 真实端到端”两层

OpenSSL `-status -msg` 是发现大 Certificate record 的有用保守探测，且 #6356 已用同类方法复现。但 CDN 节点、SCT、OCSP 和 ClientHello 都会改变结果；OpenSSL 的 ClientHello 也不等价于生产客户端/REALITY 的完整握手。

因此 §6 的 probe script 不应只是一次 OpenSSL 数值比较，也不应把“可选 deploy-time assertion”作为唯一防线。建议脚本记录时间、解析到的 IP、SNI、X25519、ALPN、证书记录大小和版本，并在部署前用实际 Xray client/server 做一次端到端验证；阈值应配置化，且保留安全余量。原文已意识到 CDN variance，这里应把它从说明性 caveat 提升为强制验收条件。

### P2-4：全局 default 的副作用需写入验收清单

`plan-reality-dest` D1/D3 的逻辑是自洽的：保留 default 作为 safety net，同时给生产节点显式覆盖；但改全局 Apple default 会连带改变四个 `[test_nodes]` 的继承值。应在结果中明确它们也会从 Apple 变为新的 default，或给测试节点显式值，避免“deferred”被理解成“无变化”。新节点继续继承 shared default 的集中风险也应在新增节点 checklist 中体现。

## 已核实、可以保留的判断

- Xray 官方明确支持 `UseIP`、`happyEyeballs`、`tryDelayMs: 250`、`interleave` 和 `prioritizeIPv6`；且官方说明 `UseIPv4v6` 不适合作为 Happy Eyeballs 的配套策略，计划选择 `UseIP` 是正确方向。
- Xray v26.3.27 官方 release notes 明确警告非 443 端口和 Apple `dest` 可能导致服务器 IP 被封锁；这支持先处理 Apple `dest`、暂缓 443 架构迁移的优先级判断，但它是上游警告，不是本项目被封的实测证明。
- RFC 6724 的默认策略确实把 ULA `fc00::/7` 放在较低 precedence/不同 label；项目现场对 ULA 源地址导致排序变化的测量与此相符。不过 Xray 官方文档同时指出 `AsIs` 通常表现为 IPv6 优先，所以报告应保留“本项目容器现场测量”这一限定，不要写成 Xray 的普遍行为。
- Docker 官方说明 IPv6 bridge 默认启用 `ip6tables`，默认 NAT 模式会配置 masquerade；因此 IPv6 计划中“现有 NAT66 路径可工作”的方向合理，但仍须用节点现场事实证明，不能由 Docker 默认值单独推出生产连通性。

## 建议的实施前顺序

1. 先修订变量语义、合法值校验、Xray/客户端兼容矩阵和镜像 digest 固定策略。
2. 先修复或绕开 `daemon.json` 的整文件覆盖，再对 jp10 做单节点 IPv6 canary；只在真实 VLESS 流量验证通过后扩大到 usca/jpntt。
3. REALITY `dest` 先执行 Apple 替换和 dcc/de 的证书余量改善；候选域名重新探测并做端到端 REALITY 验证，服务器重启和健康检查通过后再刷新订阅。
4. Xray 升级与 IPv6 功能变更分开发布；首选稳定 v26.3.27，固定镜像 digest；不要把 prerelease v26.7.11 当作同一批升级目标。
5. 443 合并继续保持独立计划，直到完成按 `user` 路由、共享 keypair、客户端迁移和 `spt` 443 冲突的完整设计。

## Review 判定

- **IPv6 dual-stack 计划**：方向可继续，但当前为 **Not Ready**；至少 P1-1、P1-2、P1-3、P1-4 关闭前不应实施。
- **REALITY dest 计划**：Apple/证书风险判断有依据，P1/P2 修订后可进入实施准备；当前版本固定、探测脚本和多 role 重启流程仍为 **Not Ready**。
- **443 迁移**：文档把它单独延期是合理决定，本次 review 不要求扩大范围。

## 外部核查来源

- [Xray Sockopt 官方文档](https://xtls.github.io/en/config/transports/sockopt.html)
- [Xray Freedom 官方文档](https://xtls.github.io/en/config/outbounds/freedom.html)
- [Xray v26.3.27 release notes](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27)
- [Xray v26.7.11 prerelease](https://github.com/XTLS/Xray-core/releases/tag/v26.7.11)
- [RFC 6724 默认地址选择](https://www.rfc-editor.org/rfc/rfc6724)
- [Docker IPv6 官方文档](https://docs.docker.com/engine/daemon/ipv6/)
- [Xray #3052：AsIs 与 gai.conf](https://github.com/XTLS/Xray-core/issues/3052)
- [Xray #6356：REALITY 8192 字节限制](https://github.com/XTLS/Xray-core/issues/6356)
- [XTLS/REALITY #33：17 KiB buffer PR](https://github.com/XTLS/REALITY/pull/33)
- [mihomo #2967：`minClientVer` 兼容性](https://github.com/MetaCubeX/mihomo/issues/2967)
- [Xray #6477：v26.7.11 与 mihomo](https://github.com/XTLS/Xray-core/issues/6477)
- [3x-ui #5922：v26.7.11 REALITY 兼容性](https://github.com/MHSanaei/3x-ui/issues/5922)

本报告证据是截至 2026-07-26 的仓库与公开网页快照；生产节点、DNS/CDN、镜像 registry 和社区 issue 状态会变化，后续实施前需重新执行对应核查。
