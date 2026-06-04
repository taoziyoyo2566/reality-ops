# SOCKS5 出口模块实施与排障记录

本文沉淀本轮 `reality_single` SOCKS5 出口改造、jp10 验证、r6s 异常、dcc/jpntt 对照测试中的有效结论。真实密码不写入仓库；文中出现的账号、地址按场景说明使用，提交或共享前应再次脱敏。

## 当前结论

- `reality_single` 已支持基于 `socks5_egress.profiles` 的多 SOCKS5 profile。
- 单实例模式通过给每个用户 inbound 增加稳定 tag，实现按用户精准路由：
  ```json
  "tag": "user-test"
  ```
- 指定用户的 TCP 流量可以按 `inboundTag` 转发到指定 SOCKS5 outbound。
- Xray `socks` outbound 必须使用官方扁平结构：
  ```json
  {
    "protocol": "socks",
    "settings": {
      "address": "SOCKS5_ADDRESS",
      "port": 10808,
      "user": "SOCKS5_USER",
      "pass": "SOCKS5_PASS"
    },
    "tag": "socks5-profile-jpntt_isp"
  }
  ```
- 不要把 inbound SOCKS 的 `servers/users` 结构用于 outbound。该错误曾导致 Xray 配置看似生成成功，但链路行为异常。
- r6s / `jp10_isp` 的 SOCKS5 上游可被 curl 访问，但通过 Xray 路由后出现网页 `ERR_CONNECTION_CLOSED`。用 dcc 和 jpntt SOCKS5 对照后，确认主链路可用，问题集中在 r6s 出口或其上游行为。
- `jpntt` 作为 `jp10` 出口方案已验证可行。`jp10` 主机和 `reality_core` 容器网络都能通过 jpntt SOCKS5 获取 jpntt 出口 IP。

## 相关文件

- `group_vars/all/socks5.yml`：新 SOCKS5 profile 配置入口，非密钥结构。
- `group_vars/all/vault.yml`：SOCKS5 地址、端口、用户名、密码、route users 等密钥/环境变量。
- `roles/reality_single/templates/config.json.j2`：生成 `inboundTag` 路由、SOCKS5 outbound、用户 inbound tag。
- `roles/reality_single/tasks/main.yml`：SOCKS5 profile 前置校验、关闭 config diff、防止密码泄漏。
- `deploy.yml`：增加 `users/*.yml` 的 `hosts` 列表格式前置检查。
- `users/test.yml` / `users/wang.yml`：本轮用于 jp10 SOCKS5 出口验证的用户。
- `JPNTT_SOCKS5_EGRESS.md`：jpntt 作为 jp10 SOCKS5 出口的部署与验证手册。

## 配置模型

`group_vars/all/socks5.yml` 中按 profile 管理 SOCKS5 出口：

```yaml
socks5_egress:
  enabled: true
  profiles:
    jpntt_isp:
      enabled: "{{ vault_socks5_jpntt_enabled | default(false) }}"
      address: "{{ vault_socks5_jpntt_address | default('') }}"
      port: "{{ vault_socks5_jpntt_port | default(1080) }}"
      username: "{{ vault_socks5_jpntt_username | default('') }}"
      password: "{{ vault_socks5_jpntt_password | default('') }}"
      priority: 40
      route:
        hosts: ["jp10"]
        users: "{{ vault_socks5_jpntt_route_users | default([]) }}"
        domains: "{{ vault_socks5_jpntt_route_domains | default([]) }}"
        ips: "{{ vault_socks5_jpntt_route_ips | default([]) }}"
        protocols: "{{ vault_socks5_jpntt_route_protocols | default([]) }}"
        network: "{{ vault_socks5_jpntt_route_network | default('tcp') }}"
```

规则：

- `priority` 数值越小越优先。
- `route.hosts` 限定该 profile 在哪些节点生效。
- `route.users` 是用户级别的精确切流名单。
- `route.network: tcp` 用于避免 UDP/QUIC 等流量进入 SOCKS5 上游导致不确定行为。
- 同一个用户如果命中多个 profile，先生成的规则先生效。

## 当前 profile

- `jpntt_isp`
  - 用途：让 jp10 指定用户通过 jpntt 出口。
  - 优先级：`40`。
  - 默认关闭，由 vault 变量启用。
- `dcc_test`
  - 用途：临时对照测试，复用 dcc SOCKS5 信息。
  - 优先级：`50`。
  - 默认关闭。
- `jp10_isp`
  - 用途：原 r6s / jp10 ISP 出口。
  - 优先级：`100`。
  - 已发现通过 Xray 转发时存在网页 `ERR_CONNECTION_CLOSED`，不建议作为稳定生产出口，除非上游修复。

示例：当前若同时生成以下两条路由：

```json
{
  "inboundTag": ["user-test"],
  "outboundTag": "socks5-profile-jpntt_isp"
}
{
  "inboundTag": ["user-test", "user-wang"],
  "outboundTag": "socks5-profile-jp10_isp"
}
```

`user-test` 会优先命中 `jpntt_isp`；`user-wang` 仍走 `jp10_isp`。

## Vault 与运行命令

本仓库本地 `.vault_pass` 曾出现不能解密 `group_vars/all/vault.yml` 的情况。本轮验证中应使用：

```bash
--vault-password-file ~/.vault_pass
```

目标机需要 sudo/become 时加 `-K`：

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
TMPDIR=/tmp \
monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --tags users,config,deploy \
  --skip-tags monitor \
  --vault-password-file ~/.vault_pass -K
```

说明：

- `--tags users,config,deploy` 会更新远端 config/container，并重新生成控制端本地 `/opt/reality/users/*_jp10.json`。
- `--skip-tags monitor` 会跳过监控和 Gist 更新；因此外部订阅代理/Gist 不一定同步更新。
- 如果需要发布订阅，按 README 中 Gist/订阅更新流程单独执行，或不要跳过相关任务。

## 本轮有效命令清单

以下命令从本轮 history 中提炼并脱敏。优先使用这些版本，避免继续使用排障早期的错误命令。

### 编辑 vault

```bash
EDITOR=vim monitor_venv/bin/ansible-vault edit group_vars/all/vault.yml --vault-password-file ~/.vault_pass
```

不要使用仓库内 `.vault_pass`，本轮验证中它曾无法解密当前 `vault.yml`。

### 语法检查

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
TMPDIR=/tmp \
monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --syntax-check \
  --vault-password-file ~/.vault_pass
```

### jp10 dry-run

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
TMPDIR=/tmp \
monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --tags users,config \
  --check \
  --vault-password-file ~/.vault_pass -K
```

需要看 diff 时才加 `--diff`。当前生成远端 `config.json` 的任务已设置 `diff: false`，但仍应避免在含密配置不确定时默认使用 `--diff`。

### jp10 正式部署

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
TMPDIR=/tmp \
monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --tags users,config,deploy \
  --skip-tags monitor \
  --vault-password-file ~/.vault_pass -K
```

说明：

- 这是本轮最常用、最稳定的 jp10 部署命令。
- `--tags users,config,deploy` 会更新远端 Xray config/container 和本地用户订阅缓存。
- `--skip-tags monitor` 不更新监控，也不保证 Gist/订阅代理发布侧已刷新。

### 临时启用 jpntt_isp profile

推荐把下面变量写入 vault 后使用正式部署命令：

```yaml
vault_socks5_jpntt_enabled: true
vault_socks5_jpntt_address: "100.x.x.x"
vault_socks5_jpntt_port: 10808
vault_socks5_jpntt_username: "SOCKS5_USER"
vault_socks5_jpntt_password: "SOCKS5_PASS"
vault_socks5_jpntt_route_users:
  - test
```

临时 extra-vars 方式也可用，但会把密码写进 shell history，不推荐长期使用：

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
TMPDIR=/tmp \
monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --tags users,config,deploy \
  --skip-tags monitor \
  --vault-password-file ~/.vault_pass -K \
  -e vault_socks5_jpntt_enabled=true \
  -e vault_socks5_jpntt_address=100.x.x.x \
  -e vault_socks5_jpntt_port=10808 \
  -e vault_socks5_jpntt_username=SOCKS5_USER \
  -e vault_socks5_jpntt_password='SOCKS5_PASS' \
  -e '{"vault_socks5_jpntt_route_users":["test"]}'
```

如果已经在 shell history 中留下真实密码，应清理本机 shell history，或至少删除对应条目。

### 检查路由是否切到目标 profile

```bash
ssh jp10 'jq ".routing.rules[] | select(.outboundTag | test(\"socks5-profile\"))" /opt/reality/data/reality_core/config.json'
```

预期 `test` 使用 jpntt 时出现：

```json
{
  "type": "field",
  "inboundTag": ["user-test"],
  "network": "tcp",
  "outboundTag": "socks5-profile-jpntt_isp"
}
```

如果仍显示 `socks5-profile-jp10_isp`，说明 Xray 还没切到 jpntt。

### 检查 SOCKS outbound

```bash
ssh jp10 'jq ".outbounds[] | select(.tag | test(\"socks5-profile\")) | {tag, address: .settings.address, port: .settings.port, has_user: (.settings.user != null), has_pass: (.settings.pass != null)}" /opt/reality/data/reality_core/config.json'
```

### 日志观察

```bash
ssh jp10 'tail -n 0 -f /opt/reality/logs/reality_core/access.log /opt/reality/logs/reality_core/error.log'
```

成功切到 jpntt 时应看到：

```text
[user-test -> socks5-profile-jpntt_isp]
```

如果看到：

```text
[user-test -> socks5-profile-jp10_isp]
```

说明仍在走 r6s。

### SOCKS5 连通性测试

从控制端测试：

```bash
curl -sS --connect-timeout 8 --max-time 20 \
  --proxy socks5h://100.x.x.x:10808 \
  --proxy-user "SOCKS5_USER:SOCKS5_PASS" \
  https://ifconfig.me
```

从 jp10 主机测试：

```bash
ssh jp10 'curl -sS --connect-timeout 8 --max-time 20 --proxy socks5h://100.x.x.x:10808 --proxy-user "SOCKS5_USER:SOCKS5_PASS" https://ifconfig.me'
```

从 `reality_core` 容器网络测试：

```bash
ssh jp10 'docker run --rm --network container:reality_core curlimages/curl:latest -sS --connect-timeout 8 --max-time 20 --proxy socks5h://100.x.x.x:10808 --proxy-user "SOCKS5_USER:SOCKS5_PASS" https://ifconfig.me'
```

以上三条都返回同一个 SOCKS5 出口 IP，才能说明 SOCKS5 上游、jp10 主机网络、jp10 容器网络都正常。

## 不建议继续使用的命令形态

- `--vault-password-file .vault_pass`：本轮曾无法解密当前 vault，应使用 `~/.vault_pass`。
- `--tags users,config,monitor,deploy --skip-tags monitor`：同时包含又跳过 `monitor`，语义混乱；排障时用 `--tags users,config,deploy --skip-tags monitor`。
- 未加 `-K` 的部署命令：jp10 需要 become password 时会失败。
- 把真实 SOCKS5 密码放在 `-e vault_socks5_*_password=...` 中长期使用：会进入 shell history；应写入 vault。
- 旧 jq：`.settings.servers[0]...`。这是错误的 outbound 结构检查方式；当前应检查 `.settings.address/.settings.port/.settings.user/.settings.pass`。
- 只执行 `curl --proxy` 就判断 Xray 已切换：不充分。必须同时看 `routing.rules` 和 `access.log`。

## 订阅与客户端

本地订阅缓存：

```bash
/opt/reality/users/test_jp10.json
```

检查订阅：

```bash
jq -r '.[].subscription' /opt/reality/users/test_jp10.json
```

注意：

- `test_jp10.json` 可能同时包含 IPv4 域名节点和 `_ipv6` 节点。
- 排障时先测非 `_ipv6` 的 IPv4 节点，避免把 IPv6 连通性问题误判成 SOCKS5 问题。
- 修改服务端后，v2rayN 需要刷新订阅或重新导入节点。继续测旧节点会得到过时结果。
- v2rayN 显示 `-1` 不一定等价于不可用，最终以网页访问、出口 IP 和服务端日志为准。

## 用户 hosts 写法问题

`users/test.yml` 曾出现：

```yaml
"hosts": [
  "lej,jp10"
]
```

这是一个字符串，不是两个节点。ACL 逻辑使用：

```jinja2
inventory_hostname in u_hosts
```

所以 `jp10` 不会匹配 `"lej,jp10"`，用户不会进入 `jp10` 的 `reality_instances`。

正确写法：

```yaml
"hosts": [
  "lej",
  "jp10"
]
```

`deploy.yml` 已增加前置检查：如果 `hosts` 的任一条目包含逗号，会直接报错并提示拆成列表。

## 前置校验策略

SOCKS5 profile 校验应阻断不一致配置，而不是为了让 playbook 继续执行而放宽。

当前校验要点：

- `socks5_egress.profiles` 必须是字典。
- profile 名称只能包含字母、数字、下划线、短横线。
- 启用的 profile 必须有合法 `address` 和 `port`。
- `route.hosts` 必须存在于 inventory。
- `route.network` 必须是空、`tcp`、`udp` 或 `tcp,udp`。
- `route.users` 必须是全局存在的用户。
- 如果 `route.hosts` 命中当前节点，`route.users` 必须都已经被当前节点承载，否则停止执行。

阻断原因：避免生成“看似成功、实际只对部分用户生效”的半生效 SOCKS5 路由。

## 隐私与 diff

`roles/reality_single/tasks/main.yml` 中生成远端 `config.json` 的任务已设置：

```yaml
diff: false
```

原因：`config.json` 会包含 SOCKS5 用户名/密码。执行 `--diff` 时不能把这些敏感信息打印到终端或日志。

## 标准验证流程

### 1. 直连测试 SOCKS5 上游

从控制端或目标节点测试：

```bash
curl -sS --connect-timeout 8 --max-time 20 \
  --proxy socks5h://SOCKS5_ADDRESS:10808 \
  --proxy-user "SOCKS5_USER:SOCKS5_PASS" \
  https://ifconfig.me
```

预期返回 SOCKS5 上游出口 IP。

### 2. 从 jp10 主机测试 jpntt SOCKS5

```bash
ssh jp10 'curl -sS --connect-timeout 8 --max-time 20 --proxy socks5h://100.x.x.x:10808 --proxy-user "SOCKS5_USER:SOCKS5_PASS" https://ifconfig.me'
```

### 3. 从 jp10 的 `reality_core` 容器网络测试

```bash
ssh jp10 'docker run --rm --network container:reality_core curlimages/curl:latest -sS --connect-timeout 8 --max-time 20 --proxy socks5h://100.x.x.x:10808 --proxy-user "SOCKS5_USER:SOCKS5_PASS" https://ifconfig.me'
```

也可用 `gstatic generate_204` 验证 TLS/HTTP2：

```bash
ssh jp10 'docker run --rm --network container:reality_core curlimages/curl:latest -vk --connect-timeout 8 --max-time 20 --socks5-hostname "SOCKS5_USER:SOCKS5_PASS@100.x.x.x:10808" https://www.gstatic.com/generate_204'
```

预期包含：

```text
Opened SOCKS connection
HTTP/2 204
```

### 4. 确认 Xray 路由

```bash
ssh jp10 'jq ".routing.rules[] | select(.outboundTag | test(\"socks5-profile\"))" /opt/reality/data/reality_core/config.json'
```

预期看到目标用户命中目标 profile，例如：

```json
{
  "inboundTag": ["user-test"],
  "network": "tcp",
  "outboundTag": "socks5-profile-jpntt_isp"
}
```

### 5. 确认 Xray outbound

```bash
ssh jp10 'jq ".outbounds[] | select(.tag | test(\"socks5-profile\")) | {tag, address: .settings.address, port: .settings.port, has_user: (.settings.user != null), has_pass: (.settings.pass != null)}" /opt/reality/data/reality_core/config.json'
```

### 6. 看 access/error 日志

```bash
ssh jp10 'tail -n 0 -f /opt/reality/logs/reality_core/access.log /opt/reality/logs/reality_core/error.log'
```

客户端访问网页时，预期：

```text
[user-test -> socks5-profile-jpntt_isp]
```

如果仍然显示：

```text
[user-test -> socks5-profile-jp10_isp]
```

说明 Xray 还没有切到 jpntt profile，继续检查 profile 是否启用、route users、priority 和部署是否生效。

## r6s / jp10_isp 排障结论

已验证：

- 从 jp10 主机使用 curl 访问 r6s SOCKS5，可返回 r6s 出口 IP。
- 从 `reality_core` 同网络命名空间使用 curl 访问 r6s SOCKS5，可访问 `www.gstatic.com/generate_204`。
- Xray access log 显示 `user-test -> socks5-profile-jp10_isp`。
- 客户端网页仍出现 `net::ERR_CONNECTION_CLOSED`。
- 切换到 dcc SOCKS5 后可用。
- 切换到 jpntt SOCKS5 后可用。

因此结论是：`VLESS/REALITY -> Xray routing -> socks outbound` 主链路成立；r6s 上游或其出口 IP/策略存在问题。

## dcc_test 对照测试

`dcc_test` profile 用于复用现有 dcc SOCKS5 信息做对照。默认关闭，临时启用命令示例：

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
TMPDIR=/tmp \
monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --tags users,config,deploy \
  --skip-tags monitor \
  --vault-password-file ~/.vault_pass -K \
  -e vault_socks5_dcc_test_enabled=true \
  -e '{"vault_socks5_dcc_test_route_users":["test"]}'
```

验证成功后说明主链路可用，问题更可能在原上游 SOCKS5。

## jpntt 出口方案

详见：

- `JPNTT_SOCKS5_EGRESS.md`

关键现场问题：

- 容器用 `--user 10000:10000`，配置文件必须 `chown 10000:10000`，否则 `permission denied`。
- Docker bridge 网络下，Xray 配置里不能 `listen` 宿主机 Tailscale IP；应在容器内监听 `0.0.0.0`，用 Docker `-p TAILSCALE_IP:10808:10808` 控制宿主机绑定地址。
- `jp10` 侧必须实际生成 `socks5-profile-jpntt_isp` 路由。只验证 `curl --proxy` 成功，不代表 Xray route 已切换。

## Debug 日志

默认 Xray 日志级别是 `warning`。排障时可临时启用 debug：

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
TMPDIR=/tmp \
monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --tags users,config,deploy \
  --skip-tags monitor \
  --vault-password-file ~/.vault_pass -K \
  -e reality_log_level=debug
```

重点查找：

```text
proxy/socks
failed
connection ends
closed
EOF
read/write
```

排障结束恢复默认 `warning`：

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
TMPDIR=/tmp \
monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --tags config,deploy \
  --skip-tags monitor \
  --vault-password-file ~/.vault_pass -K
```

## flow / xtls-rprx-vision 结论

曾尝试对 SOCKS5 用户去掉 `flow=xtls-rprx-vision`，但没有解决 r6s 的 `ERR_CONNECTION_CLOSED`。由于 Vision 对性能和流量特征有价值，该改动已恢复。

当前原则：

- 保持原有 `xtls-rprx-vision`。
- 不因 SOCKS5 route 自动去掉 flow。
- 如果未来某个上游明确要求 no-flow，再单独做可配置开关，而不是全局按 SOCKS5 用户移除。

## 后续建议

- 将 jpntt SOCKS5 server Ansible 化，避免手工 Docker 命令漂移。
- `dcc_test` 在生产稳定后可以移除，或改为通用 `test` profile 机制。
- `reality_multi` 旧 `reality_socks5` 实现目前暂不迁移；后续若需要统一模块，再按新 `socks5_egress` 模型改造。
- 订阅发布流程需要单独梳理：本地 `/opt/reality/users` 更新和 Gist/订阅代理更新不是同一件事。
