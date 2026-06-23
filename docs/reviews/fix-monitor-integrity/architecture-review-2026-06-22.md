# 架构审查：流量监控加固后整体风险

- 日期：2026-06-22
- 分支：`fix/monitor-integrity`
- 基线提交：`c174815`
- 范围：`deploy.yml`、`roles/monitor`、inventory/group_vars/host_vars、监控运维文档，以及本轮未提交改动后的部署边界。

## 总体结论

监控加固方向正确：D1-B 鉴权、token 外置、专用用户、DB 独立目录、WAL、staging guard 都明显优于旧实现。

从项目架构角度看，`spt` 服务端金丝雀已完成，`jp10` 第一台 agent 灰度也已跑通，可以继续进入第二台 agent 灰度；但仍不适合直接全量 agent 扩面。剩余风险主要集中在节点身份一致性、运维入口一致性、agent 分批升级验证。

## 当前处理状态

- 已处理：P0-1 agent 空采样清空 baseline、P0-2 monitor tag 边界、P0-3 `DE` / `netcup` canonical host name 统一为 `de`、P1-1 旧/新 DB 并存 fail closed 与 check-mode 兼容、P1-2 cleanup admin-only、P1-3 `user_ip_hits` retention、P1-4 single stats 解析缺陷。
- 未处理：P2-1 Ansible shortcut 入口统一仍待收敛。
- 已上线：`spt + monitor_server` 已实际执行，`reality-monitor.service` active，`/healthz` 为 ok/wal；Bearer `/stats/health` 正常；CF Request Header Transform Rule 已配通，`/stats/ui` 从白名单 IP 可访问。
- 当前建议：`de` 单节点执行 agent 验证，确认 `/stats/health` fresh 后再视情况做全量复跑；不要直接无 `--limit` 展开。

## Findings

### P0-1：agent 采集失败会清空 baseline，导致流量丢失

- 状态：已修复。空采样时保留旧 baseline，只重试已有 pending，不再把 state 写成空用户集合。
- 位置：`roles/monitor/templates/agent.py.j2:172-220`
- 现象：`current` 如果因 `docker exec`、`xray statsquery`、Docker socket 权限或容器短暂异常变成 `{}`，代码仍执行 `write_state(current, None, new_pending)`。
- 影响：上轮 baseline 被清空；下一轮采集恢复时用户被当作“首见”，增量上报 0，故障窗口内累计流量丢失。
- 本地验证：预置 state `{"users":{"alice":{"up":1000,"down":2000}}}`，令 `get_core_stats/get_multi_stats` 返回 `{}`，执行 `main()` 后 state 变成 `{"users":{},"pending":{}}`。
- 建议：`last` 非空且 `current` 为空时，判定为采集失败，不推进 baseline；保留旧 `last` 和 `pending`，写 agent log 后退出。若确实需要表示“当前无用户”，应有显式节点状态或连续空采集阈值，不能单次空结果直接清空。

### P0-2：Ansible tag 边界失真，`system/users` 会触发监控副作用

- 状态：已修复。monitor 顶层不再继承 `system`，agent 任务已移到 `monitor_agent`/`monitor_config`，`--tags users` 不再包含 monitor agent。
- 位置：`roles/monitor/tasks/main.yml:4-6`
- 现象：monitor 顶层 install block 带 `tags: ['monitor', 'system']`，导致 block 内所有任务继承 `system`。agent 任务还带 `users`。
- 影响：
  - `--tags system` 会包含 monitor server DB 迁移、env 下发、systemd、agent cron 等非基础环境任务。
  - `--tags users` 会触发 monitor agent 专用用户、token、cron、warm-up。
  - 这扩大了日常命令的爆炸半径，尤其本轮加入 DB 自动迁移后，`system` tag 不应触碰数据迁移。
- 证据：`ansible-playbook deploy.yml --list-tasks --tags system --limit spt` 列出了 monitor server/agent 任务；`--tags users` 列出了 monitor agent 任务。
- 建议：去掉 monitor 顶层 block 的 `system` tag；只给 Python 版本检查、apt、venv、requirements 等基础任务打 `system`。agent 任务应统一归入 `monitor_agent`/`monitor_config`，不要挂在通用 `users` 下，除非文档明确日常 `--tags users` 会变更监控。

### P0-3：inventory 主机名不一致，`DE` 和 `netcup` 被当成两个节点身份

- 状态：已修复。canonical host name 统一为 `de`；`host_vars/netcup.yml` 改为 `host_vars/de.yml`；`[free]` 分组改为 `de`；连接使用 SSH config `Host de`。
- 位置：`inventory.ini:4`、`inventory.ini:34-36`、`host_vars/de.yml:1-3`
- 现象：`[reality_nodes]` 中是 `DE`，但 `[free]` 分组和 host_vars 使用 `netcup`。
- 影响：
  - 部署目标是 `DE`，但 `host_vars/netcup.yml` 不会应用到 `DE`。
  - `DE` 不在 `[free]` 组，ACL 档位和用户下发会偏离预期。
  - wrapper 的 `host_in_reality_nodes` 判断也只认 `[reality_nodes]` 第一列，`./ansible-playbook deploy netcup` 与 `deploy DE` 语义不一致。
- 后续处理：本机 SSH config 已补 `Host de`，`inventory.ini` 已去掉旧的 `netcup` 连接 override，连接名也收敛到 `de`。

### P1-1：DB 自动迁移遇到“旧库和新库同时存在”会静默跳过

- 状态：已修复。旧库和新库同时存在时 fail closed；check-mode 只显示迁移计划，不执行停服务/备份/mv。
- 位置：`roles/monitor/tasks/main.yml:112-186`
- 现象：迁移条件是旧库存在且新库不存在。若先前部署已在新目录生成空库，旧库仍留在 `reality_data_dir`，role 不迁移、不 fail。
- 影响：server 使用新空库，历史数据留在旧路径；运维可能误以为迁移成功。
- 建议：增加 guard：
  - 旧库存在且新库存在时 fail，提示人工 reconcile；
  - 或引入显式变量 `monitor_db_migration_confirm=true` 才允许覆盖/合并；
  - checklist 中要求比较旧/新库行数、大小和 `PRAGMA integrity_check`。

### P1-2：`stats_bearer_token` 不是严格只读

- 状态：已修复。新增 admin-only guard，`/stats/cleanup` 只接受 admin bearer，不再接受 stats bearer + report token。
- 位置：`roles/monitor/templates/server.py.j2:1623-1631`、`roles/monitor/templates/server.py.j2:1928-1931`
- 现象：`auth_guard` 接受 admin 和 stats bearer 访问所有 `/stats/*`；`/stats/cleanup` 再检查 `token == REPORT_TOKEN`。
- 影响：`stats token + 任一 agent report token` 可以执行 cleanup 删除数据。report token 分发到所有 agent，暴露面大于 admin token。
- 建议：拆分 `require_admin_auth` 与 `require_stats_auth`。`/stats/cleanup` 只接受 admin bearer；如仍要双因子，第二因子不应是 agent report token。

### P1-3：`user_ip_hits` 没有保留清理

- 状态：已修复。retention cron 和 admin cleanup 均会按 `last_seen` 清理 `user_ip_hits`。
- 位置：`roles/monitor/tasks/main.yml:253-260`
- 现象：retention cron 只清理 `records` 和 `subscription_logs`，不清理 `user_ip_hits`。
- 影响：IP 审计表长期增长；查询按 `last_seen` 过滤但存储不回收，后续会带来磁盘和索引膨胀。
- 建议：同步执行 `DELETE FROM user_ip_hits WHERE last_seen<?`，并保留 WAL checkpoint。

### P1-4：single 模式 stats 解析会被缺少 `value` 的 stat 项拖成空采样

- 状态：已修复。agent 解析 `xray statsquery` 时跳过无 `value` 或 0 值项；同时兼容 `user>>>name.host>>>traffic>>>...` 与 `inbound>>>user-name>>>traffic>>>...` 两类计数，优先使用 user 维度，inbound 只补缺失方向，避免双计。
- 位置：`roles/monitor/templates/agent.py.j2:125-178`
- 现象：`jp10` 灰度中，`xray api statsquery` 返回了缺少 `value` 的 stat 项；旧解析一旦遇到缺失字段或只看单一命名形态，可能导致 `traffic_cache.json` 被写成 `{"users": {}, "pending": {}}`，服务端 `jp10` 长期 `stale=true`。
- 影响：single 节点 agent 看似执行成功（exit 0、无日志），但不产生基线或增量，上报静默中断。
- 建议：扩面前先部署修复后的 agent 模板；每台灰度节点必须检查 `traffic_cache.json` 非空、`/stats/health` 中该节点 `stale=false`。

### P2-1：两个 Ansible 入口能力不一致

- 位置：`ansible-playbook:46-64`、`scripts/ansible_shortcuts.sh:48-104`
- 现象：根目录 wrapper 会自动定位 `monitor_venv/bin/ansible-playbook`，但 shell shortcut 只调用 PATH 中的 `command ansible-playbook`，且不支持 `dc/decommission`。
- 影响：同一个项目存在两套运维入口，容易再次出现“PATH 中没有 ansible”的误判，也会让文档和实际行为分叉。
- 建议：让 `scripts/ansible_shortcuts.sh` 复用根目录 wrapper，或废弃 shortcut，仅保留 `./ansible-playbook`。

## 已验证

- `ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote ./monitor_venv/bin/ansible-playbook deploy.yml --syntax-check` 通过。
- `./monitor_venv/bin/ansible-inventory --graph` 正常显示 `test_nodes`。
- YAML 静态解析通过：`roles/monitor/tasks/main.yml`、`group_vars/all/main.yml`、`group_vars/test_nodes.yml`、`deploy.yml`。
- `server.py.j2` / `agent.py.j2` 最小上下文渲染后 `py_compile` 通过。
- 直接调用验证 D1-B 鉴权、Bearer、`receive_report`、`receive_ip_report`、WAL 入库通过。
- 现网 `spt` 服务端金丝雀已执行：`systemctl status reality-monitor` active，`curl http://127.0.0.1:8000/healthz` 返回 ok/wal。
- 现网 Bearer `/stats/health` 已返回节点健康数据。
- CF 端到端已验证：Request Header Transform Rule 注入 `X-Monitor-Tunnel-Secret` 后，`monitor.taoziyoyo.com` 仪表板恢复访问；误配为 Response Header 时会 401。
- `jp10` agent 灰度已验证：`reality-monitor-agent` 在 docker 组，cron 已迁移；修复 stats 解析后 `traffic_cache.json` 出现用户基线，`/stats/health` 中 `jp10 last_seen_ago_sec=5`、`stale=false`、`report_count=10`。

## 建议修复顺序

1. 观察 `spt` server 金丝雀 24 小时：`/stats/health`、`journalctl -u reality-monitor`、retention cron、DB WAL 文件增长情况。
2. 选择第 2 台节点进入 Phase 4，优先选 multi 模式节点，验证 cron、docker exec、pending、IP 审计。
3. 对 `de` 单节点执行 monitor agent 验证，确认 host_vars 与 `[free]` 档位均按 `de` 生效。
4. 修 P2-1：让 `scripts/ansible_shortcuts.sh` 复用根目录 `./ansible-playbook`，或废弃 shortcut。
5. Phase 4 两台节点稳定后，再按批次进入全量 agent，不要一次性全量。
