"""Telegram bot (plan-console-phase2 §3.5, D-P2-7): self-service for users and a few commands for administrators.

The bot long-polls the Bot API: it opens no port and only connects out to Telegram. It answers in private chats
only. A user binds their Telegram account with a one-time link the administrator makes (user page or /bind); an
address is only ever sent to the bound user, with protect_content (no forwarding or saving), and administrators
never see another user's address here. Administrators are Telegram user IDs from console.yml.

Run: python -m console.bot            (the compose `bot` service)
     python -m console.bot --check    (health: the last poll of Telegram is recent)
"""
import argparse
import datetime
import io
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid

import segno

from subs import statuspage

from . import config, db, publish as pub, queries, registry as reg, status as stat, users as users_mod

POLL_TIMEOUT = 50
HEALTHY_SECONDS = 180
CONFIRM_SECONDS = 600
RATE_LIMIT = 20                      # messages and button presses per sender per minute; the rest is ignored
LIST_MAX = 3500                      # Telegram allows 4096 characters per message
STATE_NAMES = {"active": "启用", "disabled": "停用", "expired": "已到期"}

USER_COMMANDS = [("sub", "订阅地址与二维码"), ("me", "账户状态、到期日与本月流量"), ("status", "节点状态"),
                 ("reset", "重置订阅地址"), ("unbind", "解除绑定"), ("id", "我的 Telegram ID"), ("help", "帮助")]
ADMIN_COMMANDS = [("adduser", "新增用户：/adduser 名字 [档位…] [到期日]"), ("issue", "发放订阅地址：/issue 名字"),
                  ("bind", "生成绑定链接：/bind 名字"), ("disable", "停用：/disable 名字"),
                  ("enable", "启用：/enable 名字"), ("user", "查询：/user [名字]")]
NOT_BOUND = ("你还没有绑定用户。请向管理员索取绑定链接，点开后按“开始”即可。\n你的 Telegram ID：{uid}")


def log(message):
    print(f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ} bot: {message}", flush=True)


class ApiError(Exception):
    pass


class Api:
    """The Bot API. The token is part of every request URL, so errors are built without it."""

    def __init__(self, base, token):
        self._url = f"{base}/bot{token}/"
        self._token = token

    def clean(self, text):
        return str(text).replace(self._token, "***")

    def _post(self, method, data, content_type, timeout):
        request = urllib.request.Request(self._url + method, data=data, headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise ApiError(f"{method}: {type(exc).__name__}: {self.clean(getattr(exc, 'reason', exc))}") from None
        try:
            doc = json.loads(body)
        except ValueError:
            raise ApiError(f"{method}: the response is not JSON") from None
        if not isinstance(doc, dict) or not doc.get("ok"):
            raise ApiError(f"{method}: {self.clean((doc or {}).get('description', 'failed'))[:200]}")
        return doc.get("result")

    def call(self, method, http_timeout=30, **params):
        return self._post(method, json.dumps(params).encode(), "application/json", http_timeout)

    def send_photo(self, chat_id, png, caption, **params):
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in dict(params, chat_id=chat_id, caption=caption).items():
            value = value if isinstance(value, str) else json.dumps(value)
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="subscription.png"\r\n'
                     f'Content-Type: image/png\r\n\r\n'.encode() + png + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return self._post("sendPhoto", b"".join(parts), f"multipart/form-data; boundary={boundary}", 30)


def qr_png(text):
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=8, border=4)
    return buf.getvalue()


def keyboard(*buttons):
    return {"inline_keyboard": [[{"text": text, "callback_data": data} for text, data in buttons]]}


def state(conn):
    """What the bot stored: {"username", "started", "heartbeat"} (None until set).

    `started` is when the running process began; `heartbeat` the last time it reached Telegram (getMe at start,
    then every poll).
    """
    values = {k: db.setting(conn, "bot_" + k) for k in ("username", "started", "heartbeat")}
    return dict(values, **{k: int(values[k]) if values[k] else None for k in ("started", "heartbeat")})


def alerts(conn, now):
    beat = state(conn)["heartbeat"]
    if beat is None or now - beat > 5 * 60:
        return ["Telegram bot 超过 5 分钟没有连上 Telegram（bot 服务），用户暂时不能通过 bot 取地址"]
    return []


class Bot:
    def __init__(self, settings, st, api, admins, clock=time.time):
        self.settings = settings
        self.st = st
        self.api = api
        self.admins = frozenset(admins)
        self.registry = reg.Registry(settings.registry_dir)
        self.clock = clock
        self.username = None
        self.recent = {}      # sender -> recent update times (rate limit)
        self.pending = {}     # button code -> (action, target, sender, created); lost on restart, by design

    # ---- plumbing -------------------------------------------------------------------------------------------

    def start(self):
        """Learn the bot's username and set the command menus; raises ApiError while Telegram is unreachable."""
        me = self.api.call("getMe")
        self.username = me["username"]
        self.api.call("deleteWebhook")        # getUpdates is refused while a webhook is set
        self.api.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in USER_COMMANDS])
        for admin in sorted(self.admins):
            try:
                self.api.call("setMyCommands", scope={"type": "chat", "chat_id": admin},
                              commands=[{"command": c, "description": d[:256]} for c, d in USER_COMMANDS + ADMIN_COMMANDS])
            except ApiError as exc:   # an administrator who never opened the bot has no chat yet
                log(f"admin menu for {admin} not set: {exc}")
        with db.connect(self.settings.db_path) as conn:
            db.set_setting(conn, "bot_username", self.username)
            db.set_setting(conn, "bot_heartbeat", db.now())
        log(f"@{self.username} ready, {len(self.admins)} administrator(s)")

    def send(self, chat_id, text, **params):
        return self.api.call("sendMessage", chat_id=chat_id, text=text, link_preview_options={"is_disabled": True},
                             **params)

    def limited(self, sender):
        now = self.clock()
        if len(self.recent) > 10000:
            self.recent = {k: v for k, v in self.recent.items() if v and now - v[-1] < 60}
        times = [t for t in self.recent.get(sender, []) if now - t < 60] + [now]
        self.recent[sender] = times
        return len(times) > RATE_LIMIT

    def ask(self, chat_id, text, action, target, sender, confirm):
        code = secrets.token_urlsafe(12)
        now = self.clock()
        self.pending = {k: v for k, v in self.pending.items() if now - v[3] < CONFIRM_SECONDS}
        self.pending[code] = (action, target, sender, now)
        self.send(chat_id, text, reply_markup=keyboard((confirm, "y:" + code), ("取消", "n:" + code)))

    def day(self):
        return users_mod.today(self.st.utc_offset_hours)

    def when(self, epoch):
        zone = datetime.timezone(datetime.timedelta(hours=self.st.utc_offset_hours))
        return datetime.datetime.fromtimestamp(epoch, zone).strftime("%Y-%m-%d %H:%M ") + self.st.tz_label

    def user_state(self, user):
        if user["status"] != "active":
            return "disabled"
        return "active" if users_mod.effective(user, self.day()) else "expired"

    def subscription_url(self, token):
        return f"{self.settings.public_base_url}/s/{token}"

    def publish(self, conn, reason, action, target, actor):
        ok, detail = pub.publish(self.settings, conn, self.registry, reason)
        db.audit(conn, action, target, detail if ok else f"发布失败：{detail}", actor=actor)
        return ok, detail

    def node_count(self, conn, name):
        nodes = users_mod.effective_nodes(conn, self.registry.current()[0])
        return sum(1 for n in db.shown_nodes(conn) if n in nodes and name in nodes[n].users)

    # ---- updates --------------------------------------------------------------------------------------------

    def handle(self, update):
        if isinstance(update.get("message"), dict):
            self.on_message(update["message"])
        elif isinstance(update.get("callback_query"), dict):
            self.on_button(update["callback_query"])

    def on_message(self, msg):
        chat, sender = msg.get("chat") or {}, msg.get("from") or {}
        uid = sender.get("id")
        if chat.get("type") != "private" or sender.get("is_bot") or not isinstance(uid, int):
            return
        if self.limited(uid):
            return
        chat_id = chat["id"]
        text = (msg.get("text") or "").strip()
        command, _, rest = text.partition(" ")
        command = command[1:].split("@", 1)[0].lower() if command.startswith("/") else ""
        args = rest.split()
        with db.connect(self.settings.db_path) as conn:
            if command == "start" and args:
                return self.bind(conn, chat_id, uid, args[0])
            if command == "id":
                return self.send(chat_id, f"你的 Telegram ID：{uid}")
            if command in dict(ADMIN_COMMANDS):
                if uid not in self.admins:
                    return self.send(chat_id, "只有管理员可以使用这个命令。")
                return getattr(self, "admin_" + command)(conn, chat_id, uid, args)
            user = users_mod.by_telegram(conn, uid)
            handler = {"sub": self.cmd_sub, "me": self.cmd_me, "status": self.cmd_status, "reset": self.cmd_reset,
                       "unbind": self.cmd_unbind}.get(command)
            if user is None and (handler is not None or uid not in self.admins):
                return self.send(chat_id, NOT_BOUND.format(uid=uid))
            if handler is None:
                return self.send(chat_id, self.help(user, uid))
            return handler(conn, chat_id, user)

    def help(self, user, uid):
        lines = [f"你好，{user['name']}。" if user else "你好。"] + [f"/{c} {d}" for c, d in USER_COMMANDS]
        if uid in self.admins:
            lines += ["", "管理员："] + [f"/{c} {d}" for c, d in ADMIN_COMMANDS]
        return "\n".join(lines)

    def on_button(self, query):
        msg, sender = query.get("message") or {}, query.get("from") or {}
        chat = msg.get("chat") or {}
        try:
            self.api.call("answerCallbackQuery", callback_query_id=query.get("id"))
        except ApiError as exc:
            log(f"answerCallbackQuery: {exc}")
        uid = sender.get("id")
        if chat.get("type") != "private" or not isinstance(uid, int) or self.limited(uid):
            return
        answer, _, code = str(query.get("data") or "").partition(":")
        entry = self.pending.pop(code, None)
        where = {"chat_id": chat["id"], "message_id": msg.get("message_id")}
        if entry is None or entry[2] != uid or self.clock() - entry[3] >= CONFIRM_SECONDS:
            return self.api.call("editMessageText", text="这个确认已失效，请重新发送命令。", **where)
        if answer != "y":
            return self.api.call("editMessageText", text="已取消。", **where)
        action, target = entry[0], entry[1]
        with db.connect(self.settings.db_path) as conn:
            if action == "disable":
                if uid not in self.admins:
                    return self.api.call("editMessageText", text="只有管理员可以停用用户。", **where)
                return self.do_disable(conn, where, uid, target)
            user = users_mod.by_telegram(conn, uid)
            if user is None or user["name"] != target:
                return self.api.call("editMessageText", text="绑定已变化，请重新发送命令。", **where)
            if action == "reset":
                return self.do_reset(conn, where, uid, user)
            if action == "unbind":
                users_mod.unbind(conn, target)
                db.audit(conn, "telegram-unbind", target, f"Telegram {uid}", actor=f"Telegram {uid}（{target}）")
                return self.api.call("editMessageText", text=f"已解除与用户 {target} 的绑定。", **where)

    # ---- users ----------------------------------------------------------------------------------------------

    def bind(self, conn, chat_id, uid, code):
        try:
            name = users_mod.redeem_bind_link(conn, code, uid)
        except users_mod.InvalidLink as exc:
            current = users_mod.by_telegram(conn, uid)   # e.g. the same link opened twice
            return self.send(chat_id, f"你已绑定用户 {current['name']}。发送 /help 查看命令。" if current else str(exc))
        except users_mod.UserError as exc:
            return self.send(chat_id, str(exc))
        db.audit(conn, "telegram-bind", name, f"Telegram {uid}", actor=f"Telegram {uid}（{name}）")
        user = users_mod.get(conn, name)
        self.send(chat_id, f"已绑定用户 {name}。\n\n" + self.help(user, uid))

    def send_address(self, chat_id, token):
        url = self.subscription_url(token)
        caption = (f"{url}\n\n在客户端中导入这个订阅地址（或扫描二维码）。地址只给你本人使用，请不要转发。"
                   "Telegram 对话不是端到端加密；地址如果泄露，发送 /reset 更换。")
        if self.st.enabled:
            caption += f"\n\n节点状态：{url}/status"
        self.api.send_photo(chat_id, qr_png(url), caption, protect_content=True)

    def cmd_sub(self, conn, chat_id, user):
        current = self.user_state(user)
        if current != "active":
            return self.send(chat_id, f"你的账户{STATE_NAMES[current]}，订阅已暂停。请联系管理员。")
        token = db.tokens(conn).get(user["name"])
        if not token:
            return self.send(chat_id, "管理员还没有为你发放订阅地址。")
        self.send_address(chat_id, token)

    def cmd_me(self, conn, chat_id, user):
        name, day = user["name"], self.day()
        month = queries.this_month()
        traffic = queries.traffic_by_user(conn, month).get(name, {"up": 0, "down": 0})
        expires = user.get("expires_on")
        if expires:
            left = (datetime.date.fromisoformat(expires) - datetime.date.fromisoformat(day)).days
            expiry = f"{expires}（{'还有 ' + str(left) + ' 天' if left >= 0 else '已过期'}）"
        else:
            expiry = "不到期"
        lines = [f"用户：{name}", f"状态：{STATE_NAMES[self.user_state(user)]}", f"到期：{expiry}",
                 f"订阅地址：{'已发放' if name in db.tokens(conn) else '未发放'}",
                 f"订阅中的节点：{self.node_count(conn, name)} 个",
                 f"本月流量（{month}，按 UTC）：{queries.human_bytes(traffic['up'] + traffic['down'])}"
                 f"（上传 {queries.human_bytes(traffic['up'])}，下载 {queries.human_bytes(traffic['down'])}）"]
        self.send(chat_id, "\n".join(lines))

    def cmd_status(self, conn, chat_id, user):
        if not self.st.enabled:
            return self.send(chat_id, "节点状态检测没有开启。")
        doc = stat.build_document(conn, self.st, self.registry.current()[0], int(self.clock()))
        lines = [f"{n['label']}：{statuspage.STATES[n['state']]}" for n in doc["nodes"]] or ["暂无状态数据。"]
        token = db.tokens(conn).get(user["name"])
        if token:
            lines += ["", f"详情：{self.subscription_url(token)}/status"]
        self.send(chat_id, "\n".join(lines))

    def cmd_reset(self, conn, chat_id, user):
        if user["name"] not in db.tokens(conn):
            return self.send(chat_id, "你还没有订阅地址，不需要重置。")
        self.ask(chat_id, "重置后旧地址立即失效，所有设备都要重新导入新地址。确定重置吗？",
                 "reset", user["name"], user["telegram_id"], "确认重置")

    def do_reset(self, conn, where, uid, user):
        name = user["name"]
        if name not in db.tokens(conn):
            return self.api.call("editMessageText", text="你还没有订阅地址。", **where)
        db.rotate_token(conn, name, pub.new_token())
        ok, _ = self.publish(conn, f"rotate {name}", "rotate", name, f"Telegram {uid}（{name}）")
        if not ok:
            return self.api.call("editMessageText", text="已重置，但订阅发布失败，新地址暂时不能使用。请联系管理员。", **where)
        self.api.call("editMessageText", text="已重置，旧地址已失效。新地址如下：", **where)
        self.send_address(where["chat_id"], db.tokens(conn)[name])

    def cmd_unbind(self, conn, chat_id, user):
        self.ask(chat_id, f"解除后，这个 Telegram 账号不能再查看用户 {user['name']} 的订阅；重新绑定需要管理员的新链接。确定吗？",
                 "unbind", user["name"], user["telegram_id"], "确认解除")

    # ---- administrators ---------------------------------------------------------------------------------------

    def target(self, conn, chat_id, args, usage):
        """The user named in args[0], or None after telling the administrator why not."""
        if len(args) != 1 or not users_mod.NAME_RE.match(args[0]):
            self.send(chat_id, usage)
            return None
        user = users_mod.get(conn, args[0])
        if user is None:
            self.send(chat_id, f"没有用户 {args[0]}。")
        return user

    def admin_adduser(self, conn, chat_id, uid, args):
        if not args:
            return self.send(chat_id, "用法：/adduser 名字 [档位…] [到期日 YYYY-MM-DD]\n例如：/adduser alice basic 2026-12-31")
        if not users_mod.imported(conn):
            return self.send(chat_id, "用户尚未导入控制台（运行 console.yml），还不能新增。")
        name, rest = args[0], args[1:]
        dates = [a for a in rest if users_mod.DATE_RE.match(a)]
        tiers = [a for a in rest if not users_mod.DATE_RE.match(a)]
        known = users_mod.known_tiers(conn)
        unknown = [t for t in tiers if t not in known]
        if unknown or len(dates) > 1:
            return self.send(chat_id, (f"没有这些档位：{'、'.join(unknown)}。可用档位：{'、'.join(known)}" if unknown
                                       else "只能给一个到期日。"))
        try:
            fields = users_mod.clean_fields(tiers, [], [], dates[0] if dates else "", "")
            with db.transaction(conn):
                users_mod.create(conn, name, fields)
                db.audit(conn, "user-create", name, users_mod.describe(fields), actor=f"Telegram 管理员 {uid}")
        except users_mod.UserError as exc:
            return self.send(chat_id, str(exc))
        self.send(chat_id, f"已新增 {name}（{users_mod.describe(fields)}）。各节点约一分钟内加入该账号。\n"
                           f"下一步：/issue {name} 发放订阅地址，/bind {name} 生成绑定链接发给对方。")

    def admin_issue(self, conn, chat_id, uid, args):
        user = self.target(conn, chat_id, args, "用法：/issue 名字")
        if user is None:
            return
        name = user["name"]
        if name in db.tokens(conn):
            return self.send(chat_id, f"{name} 已经发放过订阅地址。")
        if self.user_state(user) != "active":
            return self.send(chat_id, f"{name} {STATE_NAMES[self.user_state(user)]}，恢复后才能发放。")
        db.issue_token(conn, name, pub.new_token())
        ok, detail = self.publish(conn, f"issue {name}", "issue", name, f"Telegram 管理员 {uid}")
        if not ok:
            return self.send(chat_id, f"已发放，但订阅发布失败：{detail}")
        how = ("对方发送 /sub 即可取得。" if user["telegram_id"] is not None
               else f"对方还没有绑定：用 /bind {name} 生成绑定链接发给对方，或在控制台用户页查看地址。")
        self.send(chat_id, f"已为 {name} 发放订阅地址。{how}")

    def admin_bind(self, conn, chat_id, uid, args):
        user = self.target(conn, chat_id, args, "用法：/bind 名字")
        if user is None:
            return
        link = users_mod.new_bind_link(conn, user["name"])
        db.audit(conn, "telegram-link", user["name"], "生成绑定链接", actor=f"Telegram 管理员 {uid}")
        note = "\n该用户已绑定其他 Telegram 账号；使用这个链接会改绑到新账号。" if user["telegram_id"] is not None else ""
        self.send(chat_id, f"{user['name']} 的绑定链接（{self.when(link['expires_at'])} 前有效，只能使用一次）：\n"
                           f"https://t.me/{self.username}?start={link['code']}\n\n只发给 {user['name']} 本人；"
                           f"对方点开后按“开始”即完成绑定。{note}")

    def admin_disable(self, conn, chat_id, uid, args):
        user = self.target(conn, chat_id, args, "用法：/disable 名字")
        if user is None:
            return
        if user["status"] != "active":
            return self.send(chat_id, f"{user['name']} 已经是停用状态。")
        self.ask(chat_id, f"停用 {user['name']}：订阅地址立即暂停，各节点约一分钟内移除该账号。确定吗？",
                 "disable", user["name"], uid, "确认停用")

    def do_disable(self, conn, where, uid, name):
        if users_mod.get(conn, name) is None:
            return self.api.call("editMessageText", text=f"没有用户 {name}。", **where)
        users_mod.set_status(conn, name, "disabled")
        ok, detail = self.publish(conn, f"停用 {name}", "user-disable", name, f"Telegram 管理员 {uid}")
        self.api.call("editMessageText", text=f"已停用 {name}。" + ("" if ok else f"订阅发布失败：{detail}"), **where)

    def admin_enable(self, conn, chat_id, uid, args):
        user = self.target(conn, chat_id, args, "用法：/enable 名字")
        if user is None:
            return
        if user["status"] == "active":
            return self.send(chat_id, f"{user['name']} 已经是启用状态。")
        users_mod.set_status(conn, user["name"], "active")
        ok, detail = self.publish(conn, f"启用 {user['name']}", "user-enable", user["name"], f"Telegram 管理员 {uid}")
        expired = self.user_state(users_mod.get(conn, user["name"])) == "expired"
        self.send(chat_id, f"已启用 {user['name']}。" + ("但该用户已到期，要在控制台修改到期日后才能使用。" if expired else "")
                  + ("" if ok else f"订阅发布失败：{detail}"))

    def admin_user(self, conn, chat_id, uid, args):
        if not args:
            users = users_mod.load(conn)
            text = f"共 {len(users)} 人：" + "、".join(f"{n}（{STATE_NAMES[self.user_state(u)]}）"
                                                     for n, u in users.items())
            return self.send(chat_id, text if len(text) <= LIST_MAX else text[:LIST_MAX] + "…")
        user = self.target(conn, chat_id, args, "用法：/user [名字]")
        if user is None:
            return
        name = user["name"]
        tokens = db.tokens(conn)
        fetch = queries.last_fetches(self.settings.subs_access_db).get(name) if name in tokens else None
        month = queries.this_month()
        traffic = queries.traffic_by_user(conn, month).get(name, {"up": 0, "down": 0})
        link = users_mod.bind_link(conn, name)
        lines = [f"{name}（{STATE_NAMES[self.user_state(user)]}）",
                 f"档位：{'、'.join(user['tiers'])}"
                 + (f" · 单独允许：{'、'.join(user['allow_nodes'])}" if user["allow_nodes"] else "")
                 + (f" · 单独禁止：{'、'.join(user['deny_nodes'])}" if user["deny_nodes"] else ""),
                 f"到期：{user.get('expires_on') or '不到期'}"]
        if user.get("note"):
            lines.append(f"备注：{user['note']}")
        if name in tokens:
            lines.append("订阅地址：已发放，" + (f"最后拉取 {self.when(fetch['at'])}" if fetch else "还没有拉取记录"))
        else:
            lines.append("订阅地址：未发放")
        lines.append("Telegram：" + ("已绑定" if user["telegram_id"] is not None else "未绑定")
                     + (f"（有未使用的绑定链接，{self.when(link['expires_at'])} 前有效）" if link else ""))
        lines.append(f"订阅中的节点：{self.node_count(conn, name)} 个 · 本月流量："
                     f"{queries.human_bytes(traffic['up'] + traffic['down'])}")
        self.send(chat_id, "\n".join(lines))


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------

def read_token(path):
    with open(path) as fh:
        token = fh.read().strip()
    if not token or any(c.isspace() for c in token):
        raise SystemExit(f"{path}: no bot token")
    return token


def read_admins(path):
    with open(path) as fh:
        doc = json.load(fh)
    admins = doc.get("admins") if isinstance(doc, dict) else None
    if not isinstance(admins, list) or not all(isinstance(a, int) and not isinstance(a, bool) for a in admins):
        raise SystemExit(f"{path}: admins must be a list of Telegram user IDs")
    return admins


def run(settings, st, bs):
    api = Api(bs.api_url, read_token(bs.token_file))
    db.init(settings.db_path)
    bot = Bot(settings, st, api, read_admins(bs.config_file))
    with db.connect(settings.db_path) as conn:
        db.set_setting(conn, "bot_started", db.now())
    backoff = 5
    while True:
        try:
            bot.start()
            break
        except ApiError as exc:
            log(f"cannot start: {exc}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
    with db.connect(settings.db_path) as conn:
        offset = int(db.setting(conn, "bot_offset") or 0)
    backoff = 5
    while True:
        try:
            updates = api.call("getUpdates", http_timeout=POLL_TIMEOUT + 15, offset=offset, timeout=POLL_TIMEOUT,
                               allowed_updates=["message", "callback_query"])
        except ApiError as exc:
            log(str(exc))
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        backoff = 5
        with db.connect(settings.db_path) as conn:
            db.set_setting(conn, "bot_heartbeat", db.now())
        for update in updates or []:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            try:
                bot.handle(update)
            except Exception as exc:  # one bad update must not stop the bot; the message never holds secrets
                log(f"update failed: {type(exc).__name__}: {api.clean(exc)[:200]}")
            # stored per update, so a restart never handles an update twice
            with db.connect(settings.db_path) as conn:
                db.set_setting(conn, "bot_offset", offset)


def healthy(settings, now=None):
    """This process has reached Telegram (a heartbeat from before a restart does not count) and recently."""
    with db.connect(settings.db_path) as conn:
        s = state(conn)
    return (s["heartbeat"] is not None and s["started"] is not None and s["heartbeat"] >= s["started"]
            and (now or db.now()) - s["heartbeat"] <= HEALTHY_SECONDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 0 when the last poll is recent")
    args = parser.parse_args(argv)
    settings = config.from_env()
    if args.check:
        return 0 if healthy(settings) else 1
    if not settings.public_base_url:
        raise SystemExit("CONSOLE_PUBLIC_BASE_URL is required")
    run(settings, config.status_from_env(), config.bot_from_env())
    return 0


if __name__ == "__main__":
    sys.exit(main())
