1,基于 geoip/geosite 的分流/广告拦截（需要对应数据文件）
2,入站 sniffing + 域名路由（配合上面的分流）
3,policy.levels 按用户等级设置连接数/空闲时间/流量统计
3,dns 使用 DoH/DoT + 规则化解析，减少 DNS 泄漏