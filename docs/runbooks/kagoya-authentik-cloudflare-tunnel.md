# kagoya：authentik 改走 Cloudflare 隧道，让出 443

Status: **未执行** — 本文只描述步骤，节点上的改动需要操作者另行授权（2026-09-16 操作者要求先只写文档）。

Created: 2026-09-16 JST

## 1. 为什么

`kagoya` 上同时有两套东西：

- 本项目的新数据面 `xray_edge`（`/opt/xray-edge`，compose 项目 `xray-edge`），必须独占 **443/TCP**；
- 操作者自有的 authentik（`/opt/authentik`，compose 项目 `authentik`），其 Caddy 原本占用 80、443/TCP 与 443/UDP。

2026-09-16 部署新实例前，操作者选择停掉 Caddy（`docker compose stop caddy`）让出端口；authentik 因此暂时没有公网入口。
本文把 authentik 的入口改为 **Cloudflare 隧道**：不再需要宿主机 80/443，两者可长期共存，且 authentik 不再暴露 kagoya 的真实 IP。

## 2. 现状（2026-09-16 只读核对）

- `/opt/authentik/compose.yml`：`postgresql`、`server`、`worker` 三个服务；`server` **把 9000 与 9443 直接发布到 0.0.0.0**
  （`${COMPOSE_PORT_HTTP:-9000}:9000`、`${COMPOSE_PORT_HTTPS:-9443}:9443`），即绕过 Caddy 也能直接访问。
- `/opt/authentik/compose.override.yml`：加了统一 `logging`，并定义 `caddy`（`caddy:2.11.4-alpine`，端口 80、443、443/udp，
  挂载 `/opt/authentik/Caddyfile`、`caddy-data`、`caddy-config`、`/var/log/caddy`）。
- `/opt/authentik/Caddyfile`：单个站点块，`reverse_proxy server:9000`，带 HSTS 与访问日志。
- 容器网络：`authentik_default`；`caddy` 当前状态 `exited`，重启策略 `unless-stopped`（手动停止后重启主机也不会自动起来）。
- Docker 29.7.1，Compose 插件 5.3.1（支持 `!override` 标签）。

## 3. 目标形态

```text
用户 ──HTTPS──> Cloudflare ──隧道──> cloudflared 容器 ──http://server:9000──> authentik server
```

- 宿主机不再监听 80、443；443/TCP 归 `xray_edge`。
- TLS 由 Cloudflare 终止，不再需要 Caddy 申请证书。
- `server` 的 9000/9443 收回到 `127.0.0.1`，不再对公网开放。
- authentik 取客户端 IP 依赖 `X-Forwarded-For`，其默认信任的私有网段覆盖 `172.16.0.0/12`，隧道容器在该网段内，无需额外配置。

## 4. 操作者先在 Cloudflare 完成

1. 新建一条隧道（与 monitor、订阅服务各自的隧道分开），复制 token。
2. 给 authentik 的域名添加 public hostname，Service 填 `http://server:9000`。
   域名在同一 CF 账户下时，DNS 会自动改为隧道的 CNAME，原先指向 kagoya 的 A/AAAA 记录被取代。
3. 如需 HTTP/3 或 WebSocket，Cloudflare 侧默认即可，无需在节点开放任何端口。

## 5. 节点上的改动

### 5.1 写入隧道 token

```bash
sudo install -m 0600 /dev/null /opt/authentik/cloudflared.env
printf 'TUNNEL_TOKEN=%s\n' '<token>' | sudo tee /opt/authentik/cloudflared.env >/dev/null
```

token 不进入仓库、不进入 compose 文件、不出现在命令历史之外的任何位置（用上面的 `tee` 方式时注意 shell 历史）。

### 5.2 修改 `/opt/authentik/compose.override.yml`

先备份：`sudo cp -p /opt/authentik/compose.override.yml /opt/authentik/compose.override.yml.bak-$(date +%F)`

- **删除整个 `caddy:` 段**（它只在 override 中定义，删除后该服务即不存在）。
- **收回 `server` 的宿主机端口**（compose 合并时序列是追加而不是替换，必须用 `!override`）：

```yaml
  server:
    logging: *ak-logging
    ports: !override
      - "127.0.0.1:9000:9000"
      - "127.0.0.1:9443:9443"
```

- **新增 `cloudflared`**：

```yaml
  cloudflared:
    image: cloudflare/cloudflared:2026.9.1@sha256:b269e8abd07a5bf6f3f4be65d5050b2174eca89c56a0241a8ff32a16aec454e4
    command: ["tunnel", "run"]
    env_file:
      - /opt/authentik/cloudflared.env
    depends_on:
      server:
        condition: service_started
    restart: unless-stopped
    read_only: true
    tmpfs: ["/tmp"]
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 64
    mem_limit: "128m"
    logging: *ak-logging
```

镜像 digest 为 2026-09-15 于 Docker Hub 核对的 `2026.9.1`；升级时重新核对并更新 digest。

### 5.3 应用

```bash
cd /opt/authentik
sudo docker compose -f compose.yml -f compose.override.yml config --quiet    # 先校验
sudo docker compose -f compose.yml -f compose.override.yml up -d --remove-orphans
```

`--remove-orphans` 会删除已停止的 `authentik-caddy-1`。

## 6. 验收

1. `sudo docker compose -f compose.yml -f compose.override.yml ps`：`postgresql`、`server`、`worker`、`cloudflared` 运行中，
   没有 `caddy`。
2. `ss -ltn | grep -E ':(80|443)\b'`：只剩 `xray_edge` 的 443（IPv4 与 IPv6 各一个），没有 80。
3. `ss -ltn | grep 9000`：只监听 `127.0.0.1`。
4. 从外部浏览器访问 authentik 域名：能登录，证书由 Cloudflare 签发。
5. `sudo docker logs authentik-cloudflared-1 | tail`：隧道已注册，无反复重连。
6. 本项目不受影响：`sudo docker compose -f /opt/xray-edge/compose.yaml ps` 仍为 `xray`、`logrotate` 运行中；
   从控制端用 `test` 链接连通一次。

## 7. 回滚

- **隧道有问题、想先恢复 authentik**：在 Cloudflare 删除 public hostname 并把域名的 A/AAAA 指回 kagoya，然后恢复
  `compose.override.yml.bak-*` 并 `up -d`。注意此时 443 已被 `xray_edge` 占用，Caddy 会因端口冲突起不来，必须先
  `docker compose -f /opt/xray-edge/compose.yaml stop xray`（这会中断本项目在该节点的服务），因此回滚等于重新选择端口方案。
- **只想去掉隧道**：删除 `cloudflared` 段与 `cloudflared.env`，`up -d --remove-orphans`；authentik 只剩本机 9000/9443。
- Caddy 的 `caddy-data`、`caddy-config` 与 `/var/log/caddy` 保留不删，便于回退。

## 8. 风险与备注

- authentik 的可用性改为依赖 Cloudflare 与这条隧道；隧道 token 泄漏等于可冒充该隧道，泄漏时在 CF 控制台删除隧道即可。
- Cloudflare 终止 TLS，能看到 authentik 的明文流量；这与 monitor、订阅服务的现状一致。
- `worker` 容器挂载了 `/var/run/docker.sock` 且以 root 运行（authentik 官方 compose 的默认），与本文改动无关，但它使该容器
  等同宿主机 root；如无需 authentik 的 Docker 集成功能，可另行评估去掉该挂载。
- 备选方案（本次未采用）：REALITY「自偷」——`xray_edge` 占 443/TCP 并把非代理流量转给本机 Caddy，authentik 域名照常从 443 访问；
  代价是 kagoya 的伪装域名要从 `www.yahoo.co.jp` 换成操作者自有域名，且 authentik 与数据面互相绑定。需要单独设计与评审。
