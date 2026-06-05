1,基于 geoip/geosite 的分流/广告拦截（需要对应数据文件）
2,入站 sniffing + 域名路由（配合上面的分流）
3,policy.levels 按用户等级设置连接数/空闲时间/流量统计
3,dns 使用 DoH/DoT + 规则化解析，减少 DNS 泄漏

reset 里面本地订阅缓存仍是逐个删除；远端容器/数据已批量处理，后续可评估本地缓存批量删除是否值得优化。

节点/VPS 下线流程后续改进：
- 可考虑支持 lifecycle=decommission 标记节点，例如写在 host_vars/<host>.yml、inventory host var 或独立 decommissioned_hosts.yml 中，用于审计/提示。

订阅双栈节点输出模式：
- 默认将同一台双栈 VPS 的 IPv4/IPv6 合并为一个订阅节点，节点地址使用同时具备 A/AAAA 记录的域名。
- 新增类似 subscription_dualstack_mode 的配置，建议默认 merged。
- merged：只输出一个双栈域名节点，面向普通用户。
- split：输出 _ipv4 与 _ipv6，便于强制地址族、测速和排障。
- both：同时输出双栈域名节点、_ipv4、_ipv6，面向高级订阅或调试订阅。
- _ipv4 不能继续使用带 AAAA 的普通域名，应使用 IPv4 literal 或 A-only 子域名；_ipv6 可继续使用 [global_ipv6]。
- 实现时同步改 reality_single 和 reality_multi 的本地订阅缓存生成逻辑，并在 deploy.yml 前置检查中识别 global_ipv4。
