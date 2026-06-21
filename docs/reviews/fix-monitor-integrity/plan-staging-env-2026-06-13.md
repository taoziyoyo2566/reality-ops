# 计划：监控修复的隔离 Staging 环境（4 台 VPS）

- **日期**：2026-06-13
- **级别**：架构（独立测试环境搭建：隔离边界、与生产解耦）
- **状态**：🔧 **部分恢复**（2026-06-19）。隔离层②③ + ACL 独占已落地，监控按用户决定**延后接入**；详见 [`round1-2026-06-19.changelog.md`](./round1-2026-06-19.changelog.md)。
- **设计变更（2026-06-19）**：用户决定**保留生产 `inventory.ini` 并把 4 台测试机直接放进 `[reality_nodes]`**，故放弃隔离层①（独立 `inventory.test.ini`）。隔离改为依赖：层③ ACL 独占（`reality_node_users`，host_vars 闸门）+ 层② 组级配置（`group_vars/test_nodes.yml`：禁 Gist、暂关监控）+ 运行约束 `--limit` 圈定测试机。**代价**：无物理隔离，全量 `ansible-playbook deploy.yml` 会同时打生产+测试，操作上必须始终 `--limit`。
- **分支**：`fix/monitor-integrity`
- **关联**：本环境用于验证 [`plan-harden-monitor-2026-06-13.md`](./plan-harden-monitor-2026-06-13.md) 的阶段 A 修复（§7 段1.5）

---

## 1. 目的

搭一套与生产同构、但**完全独立**的 4 台 VPS staging,达成两个目标：
1. **验证监控修复**（A1 鉴权 / A2 采集 / A3 WAL+并发）在真实环境是否完善；
2. **验证代理节点可用性**——用 `test` 用户真实拨号走流量,确认节点本身能用。

二者在同一条链路上一并验证：
```
部署 4 台测试节点(xray 起来) → test 用户真实连接走流量 → agent 上报 → 测试监控记录 → 仪表板显示
        ↑ 验证“节点能不能用”                                    ↑ 验证“监控修复有没有效”
```

## 2. 现状（截至暂停）

- 用户已在 `inventory.ini` 加入 4 台测试机:**`hkcod12` `hyu24` `hyd13` `hyu22`**(在 `[reality_nodes]` 组,但不在任何 free/basic/normal/premium 档位组)。
- 分支 `fix/monitor-integrity` 已建;主修复计划已写;监控修复代码**尚未实现**。
- 取证已完成（见关联计划 §5）。

## 3. 关键事实（取证结论，决定设计）

- **ACL 默认 'all' 陷阱**：`deploy.yml:66` `u.get('groups', ['all'])` —— 用户不写 groups 默认 `['all']`,匹配所有节点。实测 **29 个用户里 18 个默认 'all'**（ceci/cpp/frank/han/ice/kyler/lin/reap/rsp/sby/starry/yhbb/yinbenhai/ying/yinlu/yuu/zhao/zhi）。不做独占,这 18 个会全落到测试机。
- **`ansible.cfg` 未设 `hash_behaviour=merge`** → 默认 `replace`：host_vars 里写局部 `monitor:` 会**整体替换**丢键。覆盖 monitor 配置必须在 test group_vars 里给**完整** monitor 字典。
- **隐蔽共享通道——Gist**：`deploy.yml` post_task 跑 `generate_subs_gist.py` 推**生产 Gist**（`when vault_github_token|length>0`）。测试环境必须禁掉（test group_vars 置空 `vault_github_token`），否则把测试订阅推到生产 Gist 污染真实用户。
- **monitor 角色未参数化**：服务名 `reality-monitor`、库 `traffic_monitor.db`、端口 `monitor.internal_port` 写死。→ 不能在 spt 上安全并存第二实例；测试监控应放在独立机器（独立服务/库,自然无冲突）。
- **test 用户现状**（`users/test.yml`）：`groups:["test"]`（acl_matrix 无此键→组逻辑不匹配任何节点）、`hosts:["jp05","jp10"]`（仅靠主机点名授权,落在生产 jp05/jp10）。
- **入口为 CF Tunnel（非 nginx，2026-06-21 实测纠正）**：生产 `monitor.taoziyoyo.com` 经 cloudflared → `127.0.0.1:8000`（详见关联计划 §0.1）。测试监控同构（hkcod12 上 cloudflared → loopback:8000）。故 A1 以**本机 loopback** 为准绳验证（`curl 127.0.0.1:8000/...`），不依赖 nginx/XFF 旧路径。

## 4. 设计：三层隔离 + 监控兼节点拓扑

### 4.1 推荐拓扑（监控兼节点,不浪费 VPS,完全隔离生产）

| VPS | 角色 | reality_mode | 说明 |
|---|---|---|---|
| **hkcod12** | 测试监控端 **+** 代理节点 | single | 兼任监控（复刻生产 spt 的双重角色）|
| hyu24 | 纯代理节点 | single | |
| hyd13 | 纯代理节点 | multi | 覆盖多实例 |
| hyu22 | 纯代理节点 | multi | |

> **为何不用 spt 当测试监控**：spt 是要保护的生产监控机;在其上并存测试实例需把角色全参数化(端口/库/服务名/vhost),且有覆盖生产 server.py / 重启生产服务 / 写进 300MB 生产库的风险。用一台测试 VPS 兼任监控(像 spt 一样)最安全、管理手法一致(唯一区别:测试监控经 SSH 管理,spt 是本机 local)。

### 4.2 隔离层（当前 ②③④ 软隔离生效；① 物理隔离为推荐但用户决定未启用）

> **状态**：原设计为"三层物理隔离"，但 round1 用户决定保留生产 `inventory.ini`、**放弃层①**（详见顶部设计变更 + `round1` changelog）。**当前实际 = ②③④ 软隔离 + 运行时 `--limit`，无物理隔离**——故新增层④ guard 补偿。**切勿误以为已物理隔离。**

| 层 | 文件 | 作用 |
|---|---|---|
| ①〔**未启用**〕独立 inventory | `inventory.test.ini`（`[reality_nodes]` 只含这 4 台 + 分组）| 推荐的物理隔离；用户决定保留生产 inventory，故未采用 |
| ② 测试 group_vars | `group_vars/test_nodes.yml` 或随测试 inventory | 完整 `monitor` 字典指向 hkcod12;`vault_github_token: ""` 禁 Gist;独立 token / 测试 Gist |
| ③ ACL 独占 | `deploy.yml` 改 + 4 个 host_vars | 测试机只装 `test` 用户,挡掉 18 个 'all' 用户 |
| ④ 监控目标 guard | `roles/monitor/tasks/main.yml` 新增 assert | **补偿放弃层①的物理隔离**：测试组节点启用监控时硬断言 `monitor.server_url` 不含生产域、`monitor.server_host` 不是 `spt`，且 report/tunnel token 不复用生产 vault 值,禁止测试 agent 误报生产 monitor（评审第 2 条）|

## 5. 待实现改动清单（恢复时执行）

1. **`deploy.yml` ACL 独占**（加法、gated、生产零影响）：在 `reality_instances` 计算处加分支——当节点定义 `reality_node_users` 时,`reality_instances` = `all_raw_users` 中名字∈该列表的用户,**绕过 group/'all' 匹配**;未定义则走原逻辑。建议形式：
   ```jinja
   {%- if reality_node_users is defined -%}
     {%- set valid_users = [] -%}
     {%- for u in all_raw_users | default([]) -%}
       {%- if u.name in reality_node_users -%}{%- set _ = valid_users.append(u) -%}{%- endif -%}
     {%- endfor -%}
     {{ valid_users }}
   {%- else -%}
     ...原有逻辑...
   {%- endif -%}
   ```
2. **4 个 host_vars**（`host_vars/hkcod12.yml` 等）：`reality_mode`（按 §4.1 表）、`monitor_enabled: true`、`reality_node_users: ['test']`。
3. **`inventory.test.ini`**：`[reality_nodes]` 含 4 台 + 分档组(测试用)；按需 `ansible_host=<IP>`。
4. **`group_vars/test_nodes.yml`**（或测试专用 all）：完整 `monitor` 字典（`server_host: hkcod12`、`server_url: https://monitor-test.taoziyoyo.com`、独立 token、`subs_proxy` 按需）；`vault_github_token: ""`。
5. **CF**：加 `monitor-test.taoziyoyo.com` → hkcod12（用户在 CF 侧操作）。
6. （可选，B 轮）`test` 用户订阅/凭证用于真实拨号验证节点。
7. **监控目标 guard（隔离层④，翻开监控前必做）**：在 `roles/monitor/tasks/main.yml` 监控 block 起始加 `assert`——当节点 `in groups['test_nodes']` 且 `monitor_enabled` 时,硬断言 `monitor.server_url` 不含生产域、`monitor.server_host` 不是 `spt`，且 report/tunnel token 不复用生产 vault 值,否则 fail。把"必须 `--limit`"这条操作纪律变成硬失败，补偿放弃层①后的物理隔离缺失。

## 6. 待用户提供的输入（恢复时先问）

1. **4 台 VPS 怎么连**：已配 ssh config 别名（可直连）还是给 `ansible_host=<IP>`?具体地址。
2. 确认 **hkcod12 当测试监控**（或指定另一台）。
3. **`reality_mode` 分配**确认（默认 2 single + 2 multi）。
4. **测试子域名**（默认 `monitor-test.taoziyoyo.com`）。
5. 监控访问方式：CF 子域（推荐,可端到端验 A1）vs 直连 IP:8000。

## 7. 验证场景（环境就绪后）

- **节点可用**：test 用户拿订阅/凭证真实连 4 台节点,流量能通。
- **A1（D1-B）**：本机 `curl 127.0.0.1:8000/stats/daily` 无 secret 头应 **401**（伪造 CF-Connecting-IP 仍 401）;带 `Authorization: Bearer` 应 **200**;经 CF 测试域名（CF 注入 secret 头 + 白名单 IP）应 **200** 且仪表板正常。
- **A2**：停 hkcod12 监控→agent 攒 pending 数轮→重启→**无丢行**;重启某节点→**无巨值单条尖峰**。
- **A3**：4 agent cron 对齐同分钟并发→`journal_mode=wal`、**零 `database is locked`**、压测时仪表板不卡。

## 8. Next Steps（恢复入口）

- **恢复时第一步**：向用户取 §6 的 5 项输入。
- **再按 §5 清单**实现配置（纯 `.j2`/config,不部署、不碰生产),跑语法校验。
- **然后**进入关联计划的"段1 离线实现阶段 A" → 部署到本 staging → §7 验证 → 通过后才论及生产灰度。
- **阻塞**：用户输入未给前不动；生产灰度始终需单独授权 + 外部评审（W-R20）。
