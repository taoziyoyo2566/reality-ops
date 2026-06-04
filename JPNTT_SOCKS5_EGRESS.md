# jpntt 作为 jp10 SOCKS5 出口方案

目标：在 `jpntt` 上部署一个受控 SOCKS5 服务，让 `jp10` 的指定用户通过 Xray `socks` outbound 使用 `jpntt` 的公网出口 IP。

链路：

```text
客户端 -> jp10 REALITY 入站 -> jp10 Xray socks outbound -> jpntt SOCKS5 服务 -> jpntt 公网出口
```

## 设计原则

- SOCKS5 服务只给 `jp10` 使用，不做公网开放代理。
- 优先只发布到 Tailscale/内网地址，例如宿主机 `100.x.x.x:10808`。
- 必须开启用户名密码认证。
- jpntt 上的 SOCKS5 服务独立于 `reality_core`，便于排障、重启和回滚。
- jp10 侧继续使用 `socks5_egress.profiles` 管理路由，按用户精确切流。

## jpntt 侧操作

以下命令以独立 Xray 容器为例。变量请替换成实际值，不要把真实密码提交到仓库。

### 1. 确认 jpntt 内网地址

在 `jpntt` 上确认 Tailscale/内网地址：

```bash
ip addr
```

假设得到：

```text
100.x.x.x
```

后续 Docker 端口发布绑定这个宿主机地址。注意：如果容器使用 `--network bridge`，Xray 配置里的 `listen` 不能写宿主机 Tailscale IP，因为容器内部没有这个地址；推荐在容器内监听 `0.0.0.0`，再用 Docker `-p 100.x.x.x:10808:10808` 限制宿主机绑定地址。

### 2. 准备目录

```bash
sudo mkdir -p /opt/reality/data/jpntt_socks5
sudo mkdir -p /opt/reality/logs/jpntt_socks5
```

### 3. 写入 Xray SOCKS5 服务端配置

```bash
sudo tee /opt/reality/data/jpntt_socks5/config.json >/dev/null <<'JSON'
{
  "log": {
    "loglevel": "warning",
    "access": "/var/log/xray/access.log",
    "error": "/var/log/xray/error.log"
  },
  "inbounds": [
    {
      "listen": "0.0.0.0",
      "port": 10808,
      "protocol": "socks",
      "settings": {
        "auth": "password",
        "accounts": [
          {
            "user": "CHANGE_ME_USER",
            "pass": "CHANGE_ME_PASS"
          }
        ],
        "udp": false
      },
      "tag": "socks-in"
    }
  ],
  "outbounds": [
    {
      "protocol": "freedom",
      "tag": "direct"
    }
  ]
}
JSON
```

安全检查：

```bash
sudo chown 10000:10000 /opt/reality/data/jpntt_socks5/config.json
sudo chmod 600 /opt/reality/data/jpntt_socks5/config.json
sudo chown -R 10000:10000 /opt/reality/logs/jpntt_socks5
```

这里的 `chown` 必须保留，因为容器使用 `--user 10000:10000` 运行。只执行 `chmod 600` 而文件仍属于 `root:root` 时，Xray 会报 `open /config.json: permission denied`。

### 4. 启动独立容器

使用项目当前 Xray 镜像：

```bash
sudo docker run -d \
  --name jpntt_socks5 \
  --restart always \
  --network bridge \
  --user 10000:10000 \
  -p 100.x.x.x:10808:10808 \
  -v /opt/reality/data/jpntt_socks5/config.json:/config.json:ro \
  -v /opt/reality/logs/jpntt_socks5:/var/log/xray \
  --read-only \
  --tmpfs /tmp \
  --tmpfs /run \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  taoziyoyo2566/xray_docker:latest
```

这里采用 `--network bridge`，所以 Xray 容器内配置使用 `"listen": "0.0.0.0"`；安全边界由 Docker 端口发布控制：

```bash
-p 100.x.x.x:10808:10808
```

这表示只在宿主机的 Tailscale/内网 IP 上发布 `10808`，不会监听公网地址。如果把 Xray 配置写成 `"listen": "100.x.x.x"`，bridge 网络下会启动失败：

```text
bind: cannot assign requested address
```

如果改用 `--network host`，才可以在 Xray 配置里直接写宿主机 Tailscale IP，但 host 网络隔离性更弱，不作为首选。

确认容器运行：

```bash
sudo docker ps --filter name=jpntt_socks5
sudo docker logs --tail 50 jpntt_socks5
```

### 5. 防火墙限制

只允许 `jp10` 访问 `jpntt:10808`。如果使用 Tailscale ACL，建议在 Tailscale 控制台限制。

如果用 `ufw`，示例：

```bash
sudo ufw allow from JP10_TAILSCALE_IP to any port 10808 proto tcp
sudo ufw deny 10808/tcp
```

不要在公网安全组里开放 `10808` 给 `0.0.0.0/0`。

## jp10 侧接入

### 1. Vault 变量

把连接信息写入 `group_vars/all/vault.yml`，用 `ansible-vault edit` 编辑：

```bash
EDITOR=vim ansible-vault edit group_vars/all/vault.yml --vault-password-file ~/.vault_pass
```

建议变量：

```yaml
vault_socks5_jpntt_enabled: true
vault_socks5_jpntt_address: "100.x.x.x"
vault_socks5_jpntt_port: 10808
vault_socks5_jpntt_username: "CHANGE_ME_USER"
vault_socks5_jpntt_password: "CHANGE_ME_PASS"
vault_socks5_jpntt_route_users:
  - test
```

### 2. `group_vars/all/socks5.yml` 增加 profile

示例：

```yaml
socks5_egress:
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

`priority` 数值越小越优先。如果只想让 `test` 走 jpntt，先只配置 `test`，验证后再加 `wang` 或其他用户。

### 3. 部署 jp10

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

## 分层验证流程

按下面顺序验证。每一步都通过后再进入下一步，这样可以快速定位问题在 jpntt SOCKS5、jp10 到 jpntt 网络、jp10 Xray route，还是客户端订阅。

### 1. 检查 jpntt SOCKS5 容器状态

在 `jpntt` 上执行：

```bash
docker ps --filter name=jpntt_socks5
docker logs --tail 50 jpntt_socks5
```

正常情况下容器应为 `Up`，日志不应反复出现：

```text
permission denied
bind: cannot assign requested address
```

常见错误：

- `open /config.json: permission denied`：配置文件属于 `root:root` 且权限是 `600`，需要 `chown 10000:10000`。
- `bind: cannot assign requested address`：bridge 网络下 Xray 配置里写了宿主机 Tailscale IP，应该改成 `"listen": "0.0.0.0"`，并通过 Docker `-p 100.x.x.x:10808:10808` 限制发布地址。

确认监听：

```bash
ss -lntp | grep 10808
```

预期只看到宿主机 Tailscale/内网 IP 的 `10808`，不要暴露到公网 `0.0.0.0:10808`。

### 2. 在 jpntt 本机测试 SOCKS5

默认测试宿主机 Tailscale/内网地址：

```bash
curl -sS --connect-timeout 8 --max-time 20 \
  --proxy socks5h://100.x.x.x:10808 \
  --proxy-user "CHANGE_ME_USER:CHANGE_ME_PASS" \
  https://ifconfig.me
```

如果额外发布了 `127.0.0.1:10808:10808`，也可以测 loopback；只绑定 Tailscale IP 时这条不会通：

```bash
curl -sS --connect-timeout 8 --max-time 20 \
  --proxy socks5h://127.0.0.1:10808 \
  --proxy-user "CHANGE_ME_USER:CHANGE_ME_PASS" \
  https://ifconfig.me
```

预期返回 `jpntt` 的公网 IP。

### 3. 从 jp10 主机直接测试 jpntt SOCKS5

在控制端执行：

```bash
ssh jp10 'curl --proxy socks5h://100.x.x.x:10808 --proxy-user "CHANGE_ME_USER:CHANGE_ME_PASS" https://ifconfig.me'
```

预期返回 `jpntt` 的公网 IP。

如果这里失败，先不要接入 Xray route，优先检查：

- jp10 到 jpntt 的 Tailscale/内网连通性。
- jpntt 防火墙或 Tailscale ACL。
- SOCKS5 用户名密码。

### 4. 从 jp10 的 reality_core 容器网络测试

这一步是为了确认 `reality_core` 容器所在 Docker 网络也能访问 jpntt SOCKS5。对话排障中已经证明：主机能 curl 不等于容器网络一定可用，所以这一步不能省。

```bash
ssh jp10 'docker run --rm --network container:reality_core curlimages/curl:latest -vk --connect-timeout 8 --max-time 20 --socks5-hostname "CHANGE_ME_USER:CHANGE_ME_PASS@100.x.x.x:10808" https://www.gstatic.com/generate_204'
```

正常结果应包含：

```text
Opened SOCKS connection
HTTP/2 204
```

再测一个普通 HTTPS 目标：

```bash
ssh jp10 'docker run --rm --network container:reality_core curlimages/curl:latest -vk --connect-timeout 8 --max-time 20 --socks5-hostname "CHANGE_ME_USER:CHANGE_ME_PASS@100.x.x.x:10808" https://ifconfig.me'
```

预期返回 `jpntt` 的公网 IP。

### 5. 确认 jp10 生成了路由

```bash
ssh jp10 'jq ".routing.rules[] | select(.outboundTag==\"socks5-profile-jpntt_isp\")" /opt/reality/data/reality_core/config.json'
```

预期包含：

```json
"inboundTag": ["user-test"]
```

如果没有生成这条规则，检查：

- `vault_socks5_jpntt_enabled` 是否为 `true`。
- `vault_socks5_jpntt_route_users` 是否包含目标用户，例如 `test`。
- 目标用户是否已经被授权到 `jp10` 的 `hosts` 或 ACL 分组。

### 6. 确认 Xray SOCKS outbound 结构

```bash
ssh jp10 'jq ".outbounds[] | select(.tag==\"socks5-profile-jpntt_isp\") | {tag, protocol, address: .settings.address, port: .settings.port, has_user: (.settings.user != null), has_pass: (.settings.pass != null)}" /opt/reality/data/reality_core/config.json'
```

预期：

```json
{
  "tag": "socks5-profile-jpntt_isp",
  "protocol": "socks",
  "address": "100.x.x.x",
  "port": 10808,
  "has_user": true,
  "has_pass": true
}
```

注意：Xray 的 `socks` outbound 应使用扁平结构：

```json
"settings": {
  "address": "100.x.x.x",
  "port": 10808,
  "user": "CHANGE_ME_USER",
  "pass": "CHANGE_ME_PASS"
}
```

不要使用入站 SOCKS 的 `servers/users` 结构。

### 7. 看 jp10 访问日志

```bash
ssh jp10 'tail -n 0 -f /opt/reality/logs/reality_core/access.log /opt/reality/logs/reality_core/error.log'
```

客户端使用 `test.jp10` 访问网页，预期出现：

```text
[user-test -> socks5-profile-jpntt_isp]
```

如果日志里没有 `user-test`，说明客户端没有打到这个入站，优先检查：

- 是否使用的是 `test.jp10` 而不是 `test.lej`。
- 是否误选了 `_ipv6` 节点。
- 订阅是否刷新。
- 客户端节点里的端口、UUID、public key、short ID、SNI 是否和 `/opt/reality/users/test_jp10.json` 一致。

如果出现：

```text
[user-test -> socks5-profile-jpntt_isp]
```

但网页打不开，再临时打开 debug 日志：

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

复测后查看 `error.log`，重点找：

```text
proxy/socks
failed
connection ends
closed
EOF
read/write
```

排障结束后恢复默认 `warning`：

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

### 8. 客户端最终验证

客户端使用 `test.jp10` 的 IPv4 节点访问：

```text
https://ifconfig.me
```

预期显示 `jpntt` 公网 IP。

如果网页访问正常但 v2rayN 测试显示 `-1`，以浏览器实际访问和服务端日志为准。此前排障中遇到过 `access.log` 已显示命中 SOCKS5，但客户端测速值不能准确代表可用性的情况。

## 回滚

### 1. 停用 jp10 到 jpntt 的 profile

把 vault 中：

```yaml
vault_socks5_jpntt_enabled: false
```

或者把：

```yaml
vault_socks5_jpntt_route_users: []
```

然后重新部署 jp10。

### 2. 停止 jpntt SOCKS5 容器

```bash
sudo docker stop jpntt_socks5
sudo docker rm jpntt_socks5
```

保留配置文件便于后续恢复：

```bash
/opt/reality/data/jpntt_socks5/config.json
/opt/reality/logs/jpntt_socks5/
```

## 后续可 Ansible 化

如果验证稳定，可以新增一个独立 role，例如 `roles/socks5_server/`：

- 只在 `jpntt` 上部署 `jpntt_socks5` 容器。
- 使用 vault 变量生成 `/opt/reality/data/jpntt_socks5/config.json`。
- 默认绑定内网地址。
- 支持 `--tags socks5_server` 单独部署。

这样后续 jpntt/dcc 或其他出口节点都能用同一套 SOCKS5 server 模块管理。
