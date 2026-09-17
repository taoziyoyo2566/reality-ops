# Cloudflare 隧道：新建、写入 token、移动路由、轮换与删除

适用于本项目用 compose 部署、经自带 `cloudflared` 容器对外提供服务的项目（订阅服务、管理控制台）。
Cloudflare 控制台的步骤由操作者执行；界面文字于 2026-09-17 按 Cloudflare 文档核对
（[创建隧道](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)、
[查看与轮换 token](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/remote-tunnel-permissions/)），
控制台改版时以当时界面为准。

除特别注明外，命令都在控制端 `spt`（`mail.taoziyoyo.com`，仓库与订阅服务、管理控制台所在的主机）上、仓库根目录执行。
本文的隧道 token 写进 vault，不写成主机上的文件。

## 1. 现有隧道（2026-09-17 快照，以 Cloudflare 控制台为准）

| 用途 | 公共主机名 → 转发到 | 连接器 | token |
|---|---|---|---|
| 旧 monitor | `monitor.taoziyoyo.com`（`[推论]` 及 `subs.taoziyoyo.com`）→ `127.0.0.1:8000` | `spt` 宿主机 `cloudflared.service`（控制台远程管理） | 不在本仓库 |
| 订阅服务 | `sub.taoziyoyo.com` → `http://subs:8100` | `reality_subs_cloudflared`（compose `reality-subs`） | vault `vault_subs_tunnel_token` |
| 管理控制台上报 | `report.taoziyoyo.com` → `http://report:8201` | `reality_console_cloudflared`（compose `reality-console`） | vault `vault_console_tunnel_token` |

## 2. 原则

- **一个 compose 项目一条隧道。** 连接器只能访问本项目网络里的服务名；把别的项目的服务加进来，连接器解析不到会返回 502，
  强行打通两个项目的网络又会让它们互相阻塞移除与回滚。
- **不要在任何主机上执行控制台给出的安装命令。** 连接器由 compose 里的 `cloudflared` 容器运行，只需要命令里的 token。
- **token 只写进 vault。** 不贴到聊天、issue、命令行参数或运维记录；变量名为 `vault_<项目>_tunnel_token`。
- 不修改宿主机 `cloudflared.service` 与旧 monitor 的路由。

## 3. 新建隧道并添加路由（操作者在 Cloudflare 控制台）

1. **Networking → Tunnels**（Zero Trust 控制台中为 **Networks → Tunnels**）→ **Create a tunnel**，连接器类型选 Cloudflared。
2. 名称用项目名，例如 `reality-console`，然后 **Create Tunnel**（或 **Save tunnel**）。
3. 页面给出安装命令：**不执行**，只复制命令中 `--token` 之后以 `eyJ` 开头的字符串，暂存在剪贴板，下一步写入 vault。
   以后要再次查看：选中该隧道 → **Overview** → **Add a replica**。
4. 选中该隧道 → **Routes** → **Add route** → **Published application**：

   | 字段 | 管理控制台上报 | 订阅服务 |
   |---|---|---|
   | Subdomain / Domain | `report` / `taoziyoyo.com` | `sub` / `taoziyoyo.com` |
   | Path | 留空 | 留空 |
   | Service（类型与 URL） | `HTTP`，`report:8201` | `HTTP`，`subs:8100` |

   同一账户下的域名会自动生成指向该隧道的 DNS 记录。若提示主机名已被占用，说明它还挂在别的隧道或 DNS 记录上，先按 §6 处理。
5. 隧道在对应 compose 项目部署前显示为没有连接器，访问主机名返回错误，属正常。

## 4. 写入 vault

```bash
cd ~/workspace/projects/reality-ops
ANSIBLE_LOCAL_TEMP=/tmp/reality-ops-ansible-local EDITOR=vim \
  monitor_venv/bin/python -m ansible vault edit group_vars/all/vault.yml --vault-password-file ~/.vault_pass
```

编辑器打开的是解密后的内容（保存时自动重新加密）。在文件中加一行（值是 §3 第 3 步复制的字符串，加引号），保存退出：

```yaml
vault_console_tunnel_token: "<token>"
```

核对（不打印值）：

```bash
head -n 1 group_vars/all/vault.yml          # 预期 $ANSIBLE_VAULT;1.1;AES256；看到明文立即停止，不得提交
monitor_venv/bin/ansible-vault view --vault-password-file ~/.vault_pass group_vars/all/vault.yml \
  | monitor_venv/bin/python -c 'import sys, yaml; v = (yaml.safe_load(sys.stdin) or {}).get("vault_console_tunnel_token"); print("missing" if not v else f"present, {len(str(v))} chars")'
```

清空剪贴板。token 以 `eyJ` 开头；复制时不要带上命令里的其他部分或换行。

## 5. 部署与核对

```bash
PB="./monitor_venv/bin/ansible-playbook -i inventory.ini --vault-password-file $HOME/.vault_pass -K"
$PB console.yml        # 或 subs.yml；角色把 token 写入 <项目目录>/secrets/tunnel.env（0600），启动 cloudflared
sudo docker logs --tail 20 reality_console_cloudflared      # 预期出现 Registered tunnel connection，没有反复重连
curl -s -o /dev/null -w '%{http_code}\n' https://report.taoziyoyo.com/healthz   # 预期 200
for h in sub subs monitor; do curl -s -o /dev/null -w "$h %{http_code}\n" https://$h.taoziyoyo.com/healthz; done   # 其他主机名不受影响
```

`report.taoziyoyo.com/report` 不带节点 token 时应返回 401，管理页面路径（如 `/users`）应返回 404。

## 6. 把路由从一条隧道移到另一条

例：`report.taoziyoyo.com` 被加到了订阅服务的隧道上，要移到控制台的隧道。

1. 订阅服务的隧道 → **Routes** → 删除 `report.taoziyoyo.com` 这条路由；`sub.taoziyoyo.com` 不动。
2. 若 **DNS** 中仍有 `report` 指向旧隧道的记录，一并删除。
3. 在新隧道上按 §3 第 4 步添加路由，确认 DNS 记录已指向新隧道。
4. 核对：`sub.taoziyoyo.com/healthz` 仍为 200；
   `sudo docker logs --since 5m reality_subs_cloudflared | grep report` 不再出现新的错误。
   控制台部署前 `report.taoziyoyo.com` 返回错误属正常。

## 7. 轮换 token

1. 隧道 → **Overview** → **Refresh token**。已连接的连接器继续工作，但旧 token 不能再建立新连接。
2. 按 §4 用新值替换 vault 中的变量。
3. 重新部署，并强制重建连接器，使其立即改用新 token：

   ```bash
   $PB console.yml
   sudo docker compose -f /opt/reality-console/compose.yaml up -d --force-recreate cloudflared
   ```

   订阅服务对应 `subs.yml` 与 `/opt/reality-subs/compose.yaml`。
4. 按 §5 核对。token 泄漏时同样按本节处理，并检查 Cloudflare 审计日志。

## 8. 删除隧道

1. 先移除使用它的项目（`console-remove.yml` 或 `subs-remove.yml`），连接器随之停止。
2. Cloudflare：删除该隧道的全部路由与对应 DNS 记录，再删除隧道。
3. 从 vault 删除对应变量（§4 的编辑方式），核对文件仍为密文。

## 9. 故障排查

| 现象 | 先查 / 处理 |
|---|---|
| 主机名返回 502，连接器日志 `lookup <服务名> ... no such host` | 路由加在了别的项目的隧道上（§6），或服务名、端口写错，或该服务没有运行 |
| 主机名返回 530 / 错误 1033 | 隧道没有在线的连接器：`cloudflared` 容器未运行，或 token 错误、已轮换（查容器日志） |
| 连接器反复重连、日志提示认证失败 | token 已被刷新或复制不全：按 §4 重新写入后按 §7 第 3 步重建 |
| 程序发出的请求返回 403，正文 `error code: 1010` | Cloudflare 按浏览器特征拦截了程序默认的 User-Agent（如 `Python-urllib`）；节点上报程序使用自己的 User-Agent `reality-edge-reporter/1`，自写的调用也要设置 User-Agent |
| `console.yml` 在第一步失败，提示 `vault_console_tunnel_token` | 变量未写入或名称不符，按 §4 核对 |
