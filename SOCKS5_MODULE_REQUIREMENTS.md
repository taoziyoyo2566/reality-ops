# SOCKS5 Egress Module Requirements

## 背景

当前项目里已经存在一个局部 SOCKS5 出口实现：

- `host_vars/dcc.yml` 通过 `reality_socks5` 配置一个 SOCKS5 出口。
- `roles/reality_multi/templates/config.json.j2` 只在命中 `target_users` 时生成 `socks5-egress` outbound。
- 这个实现目前偏向 `dcc + lin_isp` 的单点场景，不是一个可复用的功能模块。
- `reality_single` 与 `reality_multi` 的支持方式不一致，后续扩展到 `jp10`、其他节点或多个 SOCKS5 出口时会变复杂。

目标是把 SOCKS5 出口抽象成统一模块：所有 Xray 配置默认都能带上 SOCKS5 出口定义，但只有当路由规则明确指定使用某个 SOCKS5 profile 时，该出口才真正生效。

本阶段不迁移现有 `dcc + lin_isp`。该场景先保持当前 `reality_socks5` 旧实现不动。新模块先以 `jp10` 为目标验证可用性。

## 术语

- `SOCKS5 profile`：一个 SOCKS5 出口配置单元，包含连接信息和路由选择规则。
- `连接信息`：SOCKS5 服务器地址、端口、用户名、密码等。
- `流量选择规则`：哪些用户、哪些节点、哪些 inbound、哪些目标域名/IP/协议应该走这个 SOCKS5 profile。
- `默认注入`：模板默认把已定义的 SOCKS5 outbound 写入 Xray 配置。
- `未启用`：如果没有任何路由规则引用该 SOCKS5 outbound，即使配置存在，也不会有流量走它。

## 核心需求

1. SOCKS5 应作为独立功能模块存在，不再绑定到某个节点或某个用户名。
2. 所有 Xray 配置模板都应该统一支持该模块，包括：
   - `reality_single`
   - `reality_multi`
3. 默认情况下，配置里可以包含 SOCKS5 outbound；但如果没有路由规则指向它，流量仍然走默认 direct。
4. 一个项目中应支持多个 SOCKS5 profile，例如：
   - `jp10_isp`
   - `lin_isp`
   - `hk_residential`
5. 每个 SOCKS5 profile 应同时定义：
   - 连接信息
   - 哪些流量走该 SOCKS5
6. SOCKS5 连接信息应集中存放在一个专门的模板/变量文件中，避免为了每个验证节点都分散写到 `host_vars/<host>.yml`。
7. 敏感字段应允许引用 vault 变量，不建议明文写入仓库。
8. 如果配置了 SOCKS5 profile 但没有指定任何流量选择规则，应视为“配置存在但无效/不生效”，不应影响现有流量。
9. 如果流量选择规则引用了不存在的用户、节点或 profile，部署时应尽早校验并报错。
10. 同一条流量如果命中多个 SOCKS5 profile，需要有明确优先级，避免路由不可预测。

## 建议配置模型

建议新增独立文件：

- `group_vars/all/socks5.yml`

该文件用于声明 SOCKS5 模块的非密钥结构，密钥继续放在 `group_vars/all/vault.yml`。

示例：

```yaml
socks5_egress:
  enabled: true

  profiles:
    jp10_isp:
      enabled: true
      address: "{{ vault_socks5_jp10_isp_address | default('') }}"
      port: "{{ vault_socks5_jp10_isp_port | default(1080) }}"
      username: "{{ vault_socks5_jp10_isp_username | default('') }}"
      password: "{{ vault_socks5_jp10_isp_password | default('') }}"
      route:
        users: []
        hosts: ["jp10"]
        groups: []
        protocols: []
        domains: []
        ips: []
      priority: 100
```

说明：

- `socks5_egress.enabled=false` 时，整个模块不生成 SOCKS5 outbound 和路由。
- `profiles.<name>.enabled=false` 时，该 profile 不参与生成。
- `route.users`：命中特定用户流量。
- `route.hosts`：限制该 profile 只在这些节点生效。
- `route.groups`：命中特定用户组或节点组时生效，具体语义需要实现时明确。
- `route.protocols`：例如 `["bittorrent"]`、`["http"]`，用于 Xray protocol 路由。
- `route.domains`：目标域名规则。
- `route.ips`：目标 IP 或 CIDR 规则。
- `priority`：多个 profile 可能同时命中时，数值越小或越大优先需要统一约定。

## 生成到 Xray 的预期形态

每个启用且连接信息完整的 profile 生成一个 outbound：

```json
{
  "protocol": "socks",
  "settings": {
    "address": "SOCKS5_ADDRESS",
    "port": 1080,
    "user": "SOCKS5_USERNAME",
    "pass": "SOCKS5_PASSWORD"
  },
  "tag": "socks5-profile-jp10_isp"
}
```

注意：这是 Xray `socks` outbound 的扁平配置结构。不要把 SOCKS inbound 的 `servers/users` 结构用于 outbound。

如果没有任何 routing rule 指向 `socks5-profile-jp10_isp`，该 outbound 虽然存在，但不会接管流量。

路由规则按 profile 的 `route` 生成，例如按用户：

```json
{
  "type": "field",
  "inboundTag": ["user-jp10_isp"],
  "outboundTag": "socks5-profile-jp10_isp"
}
```

## 单实例与多实例的差异

### reality_single

单实例中多个用户在同一个 Xray 配置里，所以每个用户 inbound 需要有稳定 tag：

```json
"tag": "user-<username>"
```

这样才能通过 `inboundTag` 把特定用户流量转到指定 SOCKS5 profile。

### reality_multi

多实例中每个用户一个容器和一个配置。可以有两种实现方式：

1. 仍然把所有 SOCKS5 outbound 默认注入每个用户配置，但只有命中的用户配置生成路由规则。
2. 只把当前用户可能用到的 SOCKS5 outbound 注入该用户配置。

为了满足“默认都加到所有配置中”的需求，优先采用第 1 种。

## 校验要求

部署前应校验：

1. `socks5_egress.profiles` 必须是字典。
2. 启用的 profile 必须有合法 `address` 和 `port`。
3. `port` 必须在 `1..65535`。
4. profile 名称只能包含字母、数字、下划线和短横线，便于生成稳定 tag。
5. `route.users` 中的用户如果限定在当前节点，应能在 `reality_instances` 中找到，否则报错。
6. `route.hosts` 中的主机名应存在于 inventory，否则报错或至少警告。
7. 如果同一流量命中多个 profile，应按 `priority` 生成稳定顺序。
8. 如果没有任何 route 字段，profile 只生成 outbound，不生成 routing rule。

## 与现有配置的关系

现有 `dcc + lin_isp` 配置先不迁移。它仍然可以保留：

```yaml
reality_socks5:
  enabled: true
  address: "{{ vault_dcc_socks5_address | default('') }}"
  port: "{{ vault_dcc_socks5_port | default(1080) }}"
  username: "{{ vault_dcc_socks5_username | default('') }}"
  password: "{{ vault_dcc_socks5_password | default('') }}"
  target_users: ["lin_isp"]
```

新模块先新增 `jp10_isp` profile 做验证：

```yaml
socks5_egress:
  enabled: true
  profiles:
    jp10_isp:
      enabled: true
      address: "{{ vault_socks5_jp10_isp_address | default('') }}"
      port: "{{ vault_socks5_jp10_isp_port | default(1080) }}"
      username: "{{ vault_socks5_jp10_isp_username | default('') }}"
      password: "{{ vault_socks5_jp10_isp_password | default('') }}"
      route:
        users: []
        hosts: ["jp10"]
      priority: 100
```

`reality_socks5` 可以作为旧字段继续服务现有场景。新验证实现读取 `socks5_egress`，不要求先把旧配置迁移过来。

## 待确认问题

1. “哪些流量走这个 SOCKS5”是否只需要按用户/节点匹配，还是也需要域名、IP、协议维度？
2. 多个 SOCKS5 同时命中时，优先级是数值越小优先，还是数值越大优先？
3. 是否允许一个用户在不同节点走不同 SOCKS5 profile？
4. 是否需要支持 fallback：SOCKS5 不可用时自动 direct？Xray 静态路由通常不会自动 fallback。
5. SOCKS5 profile 是否应该始终生成 outbound，即使 address 为空？当前建议是不生成，并在启用且被 route 引用时报错。

## 建议实施步骤

1. 新增 `group_vars/all/socks5.yml` 模板，声明 `socks5_egress`。
2. 新增公共 Jinja 片段或宏，统一生成 SOCKS5 outbound 和 route rules。
3. 改造 `reality_single`：
   - 给每个用户 inbound 增加 `tag`。
   - 生成所有启用 profile 的 outbound。
   - 按 route 生成 routing rules。
4. 改造 `reality_multi`：
   - 每个用户配置默认注入所有启用 profile 的 outbound。
   - 只有 route 命中的当前用户配置生成 routing rules。
5. 增加 Ansible assert 校验。
6. 先为 `jp10` 增加 `socks5_egress.profiles.jp10_isp` 并完成部署验证。
7. 验证稳定后，再决定是否把其他节点或旧 `dcc + lin_isp` 场景纳入新模块。

## jp10 验证命令

### 1. 编辑 vault，填入 jp10 SOCKS5 信息

```bash
EDITOR=vim monitor_venv/bin/ansible-vault edit group_vars/all/vault.yml --vault-password-file ~/.vault_pass
```

需要加入或修改：

```yaml
vault_socks5_jp10_isp_enabled: true
vault_socks5_jp10_isp_address: "SOCKS5_IP"
vault_socks5_jp10_isp_port: 1080
vault_socks5_jp10_isp_username: ""
vault_socks5_jp10_isp_password: ""
vault_socks5_jp10_isp_route_users:
  - "TEST_USER"
```

说明：

- `SOCKS5_IP` 替换为实际 SOCKS5 出口 IP 或域名。
- `TEST_USER` 替换为要在 jp10 上测试走 SOCKS5 的用户名。
- 如果 SOCKS5 无认证，`username/password` 保持空字符串。

### 2. 确认测试用户会出现在 jp10 节点

```bash
python3 generate_user.py list --wide
```

如果测试用户是专门为 jp10 SOCKS5 验证创建的用户，且还没有绑定 jp10，可以用：

```bash
python3 generate_user.py update TEST_USER --hosts jp10
```

注意：

- 这条命令只修改本地 `users/TEST_USER.yml`，不会直接重启或修改任何正在运行的节点。
- `--hosts jp10` 会替换该用户原有 hosts。不要直接对已有正式用户执行，除非确认要覆盖 hosts。
- 如果需要保留用户原有 hosts，需要先看 `list --wide` 输出，再把原有 hosts 和 `jp10` 一起写回。

### 3. 语法检查

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
./monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --syntax-check \
  --vault-password-file ~/.vault_pass
```

### 4. 预演 jp10 用户配置

预演可以使用 `--check`。如果附加 `--diff`，需要确认涉及敏感配置的任务已经设置 `diff: false`，避免 SOCKS5 密码被输出到终端。

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
./monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --tags users,config \
  --check --diff \
  --vault-password-file ~/.vault_pass -K
```

### 5. 部署 jp10 用户配置、远端 config 与容器

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

- `--limit jp10` 限定远端部署只发生在 jp10。
- `--tags users,config,deploy` 会更新 jp10 远端 config/container，并重新生成控制端本地 `/opt/reality/users/*_jp10.json`。
- `--skip-tags monitor` 避免触碰监控部署；同时外部 Gist/订阅代理不一定被更新。
- 如果要发布订阅，需要按 README 的 Gist/订阅流程单独执行，或不要跳过相关任务。

如果只想检查远端配置、不更新容器，可以临时使用：

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
ANSIBLE_REMOTE_TEMP=/tmp/ansible-remote \
TMPDIR=/tmp \
monitor_venv/bin/ansible-playbook -i inventory.ini deploy.yml \
  --limit jp10 \
  --tags users,config \
  --check \
  --skip-tags monitor \
  --vault-password-file ~/.vault_pass -K
```

### 6. 在 jp10 上检查生成的 Xray 配置

```bash
ssh jp10 'grep -n "\"tag\": \"socks5-profile-jp10_isp\"" /opt/reality/data/reality_core/config.json'
```

```bash
ssh jp10 'grep -n "\"outboundTag\": \"socks5-profile-jp10_isp\"" /opt/reality/data/reality_core/config.json'
```

预期：

- 第一条能看到 SOCKS5 outbound，说明 profile 已注入配置。
- 第二条只有在 `vault_socks5_jp10_isp_route_users` 等 route 配置命中时才有输出。

### 7. 重启 jp10 单实例容器

通常 Ansible 检测到配置变更会自动重启；如需手动确认：

```bash
ssh jp10 'docker restart reality_core'
```

### 8. 客户端出口验证

用 `TEST_USER` 的 jp10 节点连接后，在客户端执行：

```bash
curl -s https://api.ipify.org
```

输出应为 SOCKS5 出口 IP。

### 9. 服务端辅助观察

在 jp10 上观察是否连向 SOCKS5 服务器：

```bash
ssh jp10 'sudo tcpdump -ni any host SOCKS5_IP and port 1080'
```

## jp10 验证命令影响面审核

这些命令按影响面分为三类：

1. 只改本地文件，不触碰远端节点：
   - `ansible-vault edit group_vars/all/vault.yml`
   - `python3 generate_user.py list --wide`
   - `python3 generate_user.py update TEST_USER --hosts jp10`

2. 只检查或预演，不修改远端节点：
   - `ansible-playbook ... --limit jp10 --syntax-check`
   - `ansible-playbook ... --limit jp10 --check --diff`

3. 会触碰远端，但限定为 jp10：
   - `ansible-playbook ... --limit jp10 --tags users --skip-tags monitor`
   - `ssh jp10 'grep ...'`
   - `ssh jp10 'docker restart reality_core'`
   - `ssh jp10 'sudo tcpdump ...'`

关键保护点：

- 部署命令带 `--limit jp10`，Ansible 只会把 play 限定到 jp10。
- 部署命令带 `--skip-tags monitor`，不会部署监控。
- 部署命令不跳过 `gist`，会更新全局订阅 Gist。这是为了让客户端能通过订阅拿到 jp10 验证节点；它不会部署或重启 jp10 以外的远端节点。
- `ssh` 命令目标都是 `jp10`，不会连接其他节点。
- `curl -s https://api.ipify.org` 在客户端执行，不会修改任何节点。

需要额外注意：

- `generate_user.py update TEST_USER --hosts jp10` 会覆盖该用户原有 hosts。它不影响正在运行的其他节点，但会影响该用户后续部署或订阅生成结果。对正式用户操作前必须先确认原 hosts。
- 不要去掉部署命令里的 `--limit jp10`。
- 如果要通过客户端订阅验证，不要跳过 `gist`；如果只做远端配置文件验证，可以临时加 `--skip-tags gist`。
- 更新 Gist 会使用控制端本地 `/opt/reality/users/*.json` 缓存聚合订阅。正常情况下只刷新 jp10 对应缓存，其他节点使用既有缓存，不会连接其他远端节点。
