# Telegram bot：创建、写入 vault、开启、轮换与关闭

管理控制台的 Telegram bot（[`plan-console-phase2`](../reviews/console/plan-console-phase2-2026-09-18.md) §3.5、D-P2-7）。
BotFather 的命令于 2026-09-19 按 Telegram 文档核对（[BotFather](https://core.telegram.org/bots/features#botfather)），
界面改版时以当时为准。

除特别注明外，命令都在 `spt` 的仓库根目录执行。bot token 与管理员 ID 只写进 vault，不写成仓库文件。

## 1. bot 做什么

- 控制台 compose 项目中的 `bot` 服务（容器 `reality_console_bot`）：长轮询 `api.telegram.org`，**不开放任何端口**。
- 只在私聊中回应；群里的消息一律忽略。
- 用户先绑定：管理员在控制台用户页（或 bot 的 `/bind`）生成一次性链接，24 小时内有效，发给本人，对方点开后按“开始”。
- 用户命令：`/sub` 订阅地址与二维码（禁止转发）、`/me` 状态与到期日与本月流量、`/status` 节点状态、`/reset` 重置地址（需确认）、
  `/unbind` 解除绑定、`/id` 查自己的 Telegram ID。
- 管理员命令：`/adduser 名字 [档位…] [到期日]`、`/issue 名字`、`/bind 名字`、`/disable 名字`（需确认）、`/enable 名字`、
  `/user [名字]`。管理员在 bot 里看不到别人的订阅地址。
- 操作都记入控制台“记录”页，执行者显示为 `Telegram <ID>`。

## 2. 创建 bot（Telegram 中，操作者）

1. 在 Telegram 打开 `@BotFather`，发送 `/newbot`。
2. 按提示输入显示名称，再输入用户名：5–32 个字符，只能用拉丁字母、数字和下划线，必须以 `bot` 结尾。
3. BotFather 回复 token（形如 `110201543:AAH…`）。**只复制 token 本身**，下一步写进 vault；不要发给别人，也不要贴进聊天或文档。
4. 发送 `/setjoingroups`，选这个 bot，选 **Disable**：bot 不能被拉进群。

## 3. 写入 vault

```bash
cd ~/workspace/projects/reality-ops
ANSIBLE_LOCAL_TEMP=/tmp/reality-ops-ansible-local EDITOR=vim \
  monitor_venv/bin/python -m ansible vault edit group_vars/all/vault.yml --vault-password-file ~/.vault_pass
```

加两行（第一次还不知道自己的 Telegram ID 时，管理员先写空列表，见 §4）：

```yaml
vault_console_bot_token: "<第 2 节第 3 步的 token>"
vault_console_bot_admin_ids: []
```

核对（不打印值）：

```bash
head -n 1 group_vars/all/vault.yml          # 预期 $ANSIBLE_VAULT;1.1;AES256；看到明文立即停止，不得提交
monitor_venv/bin/python - <<'EOF'
import os, subprocess, yaml   # ansible-vault 出错时这里直接报错，不会误报
out = subprocess.run(["monitor_venv/bin/ansible-vault", "view", "--vault-password-file", os.path.expanduser("~/.vault_pass"),
                      "group_vars/all/vault.yml"], capture_output=True, text=True, check=True).stdout
v = yaml.safe_load(out) or {}
t = v.get("vault_console_bot_token")
print("token:", "missing" if not t else f"present, {len(str(t))} chars", "| admins:", v.get("vault_console_bot_admin_ids"))
EOF
```

清空剪贴板。

## 4. 开启并设置管理员

```bash
PB="./monitor_venv/bin/ansible-playbook -i inventory.ini --vault-password-file $HOME/.vault_pass -K"
$PB console.yml
```

- `console.yml` 把 token 与管理员列表写到 `/opt/reality-console/bot/`（root:10002，0640），启动 `bot` 服务，
  最后核对 bot 已连上 Telegram（最多约 90 秒）。token 错误或连不上时这一步失败，原因见
  `sudo docker logs --tail 20 reality_console_bot`（日志不含 token）。
- 在 Telegram 里给自己的 bot 发 `/id`，记下回复的数字 ID。
- 按 §3 把 ID 写进 `vault_console_bot_admin_ids`（例如 `[123456789]`，多人用逗号分隔），再运行一次 `$PB console.yml`。
  管理员列表变化后 bot 会自动重启；之后在 bot 里发 `/help` 能看到管理员命令。

## 5. 核对（§6.2 第 4 项）

1. 控制台用户页（例如 `test`）→ Telegram → 生成绑定链接，用一个 Telegram 账号打开并按“开始”，回复“已绑定用户 test”。
2. 该账号发 `/sub`：收到地址与二维码，消息不能转发；`/me` 显示本月流量与到期日。
3. 用一个**不是管理员**的账号发 `/disable test`：回复“只有管理员可以使用这个命令。”
4. 控制台首页没有“Telegram bot 超过 5 分钟没有连上 Telegram”的提示。

## 6. 轮换 token

token 泄露或怀疑泄露时：

1. `@BotFather` 发送 `/token`，选这个 bot，得到新 token（旧 token 立即失效，bot 随即连不上 Telegram）。
2. 按 §3 替换 `vault_console_bot_token`，运行 `$PB console.yml`（token 文件变化后 bot 自动重启并通过核对）。

token 泄露的影响：别人能以 bot 的身份收发消息（可能收到用户发给 bot 的命令），但读不到控制台数据库；
已发出的订阅地址不受影响，必要时让用户 `/reset`。

## 7. 关闭

从 vault 删除 `vault_console_bot_token`（可保留管理员列表），运行 `$PB console.yml`：`bot` 服务被移除，token 文件被删除，
未绑定用户的页面不再显示 Telegram 一节。绑定关系保留在数据库中，重新开启后继续有效。

## 8. 排查

| 现象 | 处理 |
|---|---|
| `console.yml` 在“核对 bot 已连上 Telegram”失败 | `sudo docker logs --tail 20 reality_console_bot`：`Unauthorized` 表示 token 不对，按 §3 核对；网络错误表示 `spt` 连不上 `api.telegram.org` |
| 首页提示 bot 超过 5 分钟没有连上 Telegram | 同上看日志；bot 会自动重试，恢复后提示消失 |
| 用户点开链接后提示“无效或已过期” | 链接超过 24 小时、已用过或已作废；在用户页重新生成 |
| 用户提示“这个 Telegram 账号已绑定用户 X” | 一个 Telegram 账号只能绑定一个用户；让对方先 `/unbind`，或管理员在 X 的用户页解除绑定 |
| 用户点确认按钮后提示“已失效” | 确认超过 10 分钟或 bot 重启过；重新发送命令 |
| 管理员发命令提示“只有管理员可以使用” | 用 `/id` 核对 ID 是否写进了 `vault_console_bot_admin_ids`，并已重新运行 `console.yml` |
