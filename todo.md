1,基于 geoip/geosite 的分流/广告拦截（需要对应数据文件）
2,入站 sniffing + 域名路由（配合上面的分流）
3,policy.levels 按用户等级设置连接数/空闲时间/流量统计
3,dns 使用 DoH/DoT + 规则化解析，减少 DNS 泄漏

reset里面删除配置文件太慢了，当前是一个一个的删除，是不是有更快的方式？

节点/VPS 下线流程改进：
- 不建议只靠删除 inventory 行触发清理；删除后 Ansible 会丢失远端目标上下文，通常只能做本地订阅清理。
- 新增 decommission.yml，和 reset.yml 分离。reset 继续表示重置/清空有效节点，decommission 专门表示节点退出服务。
- 支持 decommission_target=saberu decommission_confirm=YES。
- 节点仍可连接时：停止远端容器、清理远端数据、清理控制端 /opt/reality/users/*_<host>.json、更新 Gist。
- 节点不可连接或已删除 inventory 时：只清理控制端订阅缓存并更新 Gist，明确提示远端容器和数据未清理。
- 支持 lifecycle=decommission 标记节点，例如写在 host_vars/<host>.yml 或 inventory host var 中，用于审计/提示。
- decommission 流程应检查并提示残留引用：inventory.ini、host_vars/<host>.yml、users/*.yml 的 hosts ACL。
- 是否自动删除源码配置应做成显式开关，例如 decommission_prune_config=true，默认只提示不修改。
