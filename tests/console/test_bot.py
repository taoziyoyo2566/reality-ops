#!/usr/bin/env python3
"""Console phase 2c (plan-console-phase2 §3.5, §6.1 item 1): the Telegram bot's binding, commands and permissions,
and the user page's Telegram section; the migration progress and bulk binding links (plan-user-migration §3).

Run with the console's dependencies installed, like tests/console/test_console.py:
  python tests/console/test_bot.py
The Bot API is replaced by a recorder; all names, IDs, UUIDs and tokens are synthetic.
"""
import hashlib
import hmac
import json
import os
import pathlib
import sqlite3
import sys
import unittest
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from console import bot as bot_mod, config, db, users, web_app  # noqa: E402
from test_console import Env, Server  # noqa: E402
from test_users import import_doc  # noqa: E402

ADMIN, ALICE, STRANGER = 1001, 2002, 3003


class FakeApi:
    """Records every Bot API call; sendPhoto is recorded as method "sendPhoto" with its caption and parameters."""

    def __init__(self):
        self.calls = []

    def call(self, method, http_timeout=30, **params):
        self.calls.append((method, params))
        if method == "getMe":
            return {"id": 42, "is_bot": True, "username": "example_test_bot"}
        return {"message_id": len(self.calls)}

    def send_photo(self, chat_id, png, caption, **params):
        assert png.startswith(b"\x89PNG"), "the QR code is a PNG"
        self.calls.append(("sendPhoto", dict(params, chat_id=chat_id, caption=caption)))
        return {"message_id": len(self.calls)}

    def clean(self, text):
        return str(text)

    def sent(self, chat_id=None):
        """Texts and captions sent, oldest first."""
        return [p.get("text") or p.get("caption") for m, p in self.calls
                if m in ("sendMessage", "sendPhoto", "editMessageText") and (chat_id is None or p.get("chat_id") == chat_id)]

    def last(self, method):
        return next(p for m, p in reversed(self.calls) if m == method)


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class BotEnv(Env):
    def __init__(self):
        super().__init__()
        with self.conn() as conn:
            users.import_doc(conn, import_doc())
            db.set_shown(conn, "alpha", True)
            db.set_shown(conn, "beta", True)
        self.st = config.status_from_env({"STATUS_ENABLED": "true"})
        self.api = FakeApi()
        self.clock = Clock()
        self.bot = bot_mod.Bot(self.settings, self.st, self.api, [ADMIN], clock=self.clock)
        self.bot.start()
        self.update_id = 0

    def say(self, sender, text, chat_type="private"):
        self.update_id += 1
        self.bot.handle({"update_id": self.update_id, "message": {
            "message_id": self.update_id, "date": int(self.clock()), "text": text,
            "from": {"id": sender, "is_bot": False, "first_name": "x"}, "chat": {"id": sender, "type": chat_type}}})
        return self.api.sent(sender)[-1] if self.api.sent(sender) else None

    def press(self, sender, answer="y", code=None):
        markup = self.api.last("sendMessage")["reply_markup"]
        data = next(b["callback_data"] for b in markup["inline_keyboard"][0] if b["callback_data"].startswith(answer + ":"))
        self.update_id += 1
        self.bot.handle({"update_id": self.update_id, "callback_query": {
            "id": str(self.update_id), "from": {"id": sender, "is_bot": False, "first_name": "x"},
            "data": code or data, "message": {"message_id": 7, "date": int(self.clock()),
                                              "chat": {"id": sender, "type": "private"}}}})
        return self.api.sent(sender)[-1]

    def link(self, name):
        with self.conn() as conn:
            return users.new_bind_link(conn, name)["code"]

    def token(self, name):
        with self.conn() as conn:
            return db.tokens(conn).get(name)

    def issue(self, name):
        with self.conn() as conn:
            db.issue_token(conn, name, "t" * 42 + name[0])


class BindingTest(unittest.TestCase):
    def setUp(self):
        self.env = BotEnv()

    def tearDown(self):
        self.env.close()

    def test_start_sets_menus_and_stores_the_username(self):
        scopes = [p.get("scope") for m, p in self.env.api.calls if m == "setMyCommands"]
        self.assertEqual(scopes, [None, {"type": "chat", "chat_id": ADMIN}])
        with self.env.conn() as conn:
            self.assertEqual(bot_mod.state(conn)["username"], "example_test_bot")

    def test_a_link_binds_once_and_expires(self):
        code = self.env.link("alice")
        self.assertIn("你还没有绑定用户", self.env.say(ALICE, "/sub"))
        self.assertIn("已绑定用户 alice", self.env.say(ALICE, f"/start {code}"))
        with self.env.conn() as conn:
            self.assertEqual(users.get(conn, "alice")["telegram_id"], ALICE)
            self.assertIsNone(users.bind_link(conn, "alice"))
            self.assertIn("telegram-bind", [r["action"] for r in conn.execute("SELECT action FROM audit_log")])
        self.assertIn("你已绑定用户 alice", self.env.say(ALICE, f"/start {code}"))                # opened twice
        self.assertIn("无效或已过期", self.env.say(STRANGER, f"/start {code}"))                  # used up
        old = self.env.link("bob")
        with self.env.conn() as conn:
            conn.execute("UPDATE bot_links SET expires_at = ? WHERE user = 'bob'", (db.now() - 1,))
        self.assertIn("无效或已过期", self.env.say(STRANGER, f"/start {old}"))
        self.assertIn("无效或已过期", self.env.say(STRANGER, "/start ../../etc"))

    def test_one_account_one_user_and_rebinding_moves_the_user(self):
        self.env.say(ALICE, f"/start {self.env.link('alice')}")
        self.assertIn("已绑定用户 alice。先发送 /unbind", self.env.say(ALICE, f"/start {self.env.link('bob')}"))
        self.env.say(STRANGER, f"/start {self.env.link('alice')}")                 # alice moves to a new account
        with self.env.conn() as conn:
            self.assertEqual(users.get(conn, "alice")["telegram_id"], STRANGER)
        self.assertIn("你还没有绑定用户", self.env.say(ALICE, "/me"))

    def test_unbind_needs_confirmation_from_the_same_account(self):
        self.env.say(ALICE, f"/start {self.env.link('alice')}")
        self.env.say(ALICE, "/unbind")
        self.assertEqual(self.env.press(ALICE, "n"), "已取消。")
        self.env.say(ALICE, "/unbind")
        self.assertIn("已解除与用户 alice 的绑定", self.env.press(ALICE))
        with self.env.conn() as conn:
            self.assertIsNone(users.get(conn, "alice")["telegram_id"])

    def test_deleting_a_user_drops_the_link(self):
        code = self.env.link("bob")
        with self.env.conn() as conn:
            users.delete(conn, "bob")
        self.assertIn("无效或已过期", self.env.say(STRANGER, f"/start {code}"))


class UserCommandTest(unittest.TestCase):
    def setUp(self):
        self.env = BotEnv()
        self.env.say(ALICE, f"/start {self.env.link('alice')}")

    def tearDown(self):
        self.env.close()

    def test_sub_sends_the_address_only_to_the_bound_user_protected(self):
        self.assertIn("还没有为你发放", self.env.say(ALICE, "/sub"))
        self.env.issue("alice")
        caption = self.env.say(ALICE, "/sub")
        photo = self.env.api.last("sendPhoto")
        self.assertEqual((photo["chat_id"], photo["protect_content"]), (ALICE, True))
        self.assertIn(f"https://sub.example.test/s/{self.env.token('alice')}", caption)
        self.assertIn("/status", caption)
        self.assertNotIn(self.env.token("alice"), "".join(self.env.api.sent(STRANGER)))

    def test_disabled_or_expired_users_get_no_address(self):
        self.env.issue("alice")
        with self.env.conn() as conn:
            users.set_status(conn, "alice", "disabled")
        self.assertIn("停用", self.env.say(ALICE, "/sub"))
        with self.env.conn() as conn:
            users.set_status(conn, "alice", "active")
            conn.execute("UPDATE users SET expires_on = '2000-01-01' WHERE name = 'alice'")
        self.assertIn("已到期", self.env.say(ALICE, "/sub"))

    def test_reset_rotates_publishes_and_sends_the_new_address(self):
        self.env.issue("alice")
        before = self.env.token("alice")
        self.assertIn("确定重置吗", self.env.say(ALICE, "/reset"))
        self.env.press(ALICE)
        after = self.env.token("alice")
        self.assertNotEqual(before, after)
        self.assertIn(after, self.env.api.sent(ALICE)[-1])
        catalog, tokens = self.env.published()
        self.assertIn("alice", catalog["users"])
        with self.env.conn() as conn:
            row = conn.execute("SELECT actor FROM audit_log WHERE action = 'rotate'").fetchone()
        self.assertEqual(row["actor"], f"Telegram {ALICE}（alice）")

    def test_buttons_expire_and_belong_to_who_asked(self):
        self.env.issue("alice")
        before = self.env.token("alice")
        self.env.say(ALICE, "/reset")
        self.assertIn("已失效", self._press_as(STRANGER))
        self.env.say(ALICE, "/reset")
        self.env.clock.now += bot_mod.CONFIRM_SECONDS
        self.assertIn("已失效", self.env.press(ALICE))
        self.assertIn("已失效", self._press_as(ALICE, "y:forged"))
        self.assertEqual(self.env.token("alice"), before)

    def _press_as(self, sender, data=None):
        markup = self.env.api.last("sendMessage")["reply_markup"]
        data = data or markup["inline_keyboard"][0][0]["callback_data"]
        self.env.update_id += 1
        self.env.bot.handle({"update_id": self.env.update_id, "callback_query": {
            "id": "1", "from": {"id": sender}, "data": data,
            "message": {"message_id": 7, "chat": {"id": sender, "type": "private"}}}})
        return self.env.api.sent(sender)[-1]

    def test_me_and_status(self):
        with self.env.conn() as conn:
            conn.execute("UPDATE users SET expires_on = '2999-01-01' WHERE name = 'alice'")
            conn.execute("INSERT INTO traffic_daily (day, node, user, up, down) VALUES (?, 'alpha', 'alice', 1024, 2048)",
                         (bot_mod.queries.today(),))
        text = self.env.say(ALICE, "/me")
        for part in ("用户：alice", "状态：启用", "到期：2999-01-01", "订阅地址：未发放", "订阅中的节点：2 个", "3.0 KiB"):
            self.assertIn(part, text)
        path = os.path.join(self.env.dirs["registry"], "alpha.json")
        with open(path) as fh:
            doc = json.load(fh)
        with open(path, "w") as fh:
            json.dump(dict(doc, status_probe=True), fh)
        self.assertEqual(self.env.say(ALICE, "/status"), "alpha [tag]：无数据")     # probed, no rounds yet
        self.env.issue("alice")
        self.assertIn(f"详情：https://sub.example.test/s/{self.env.token('alice')}/status", self.env.say(ALICE, "/status"))

    def test_groups_bots_and_floods_are_ignored(self):
        count = len(self.env.api.calls)
        self.env.say(ALICE, "/me", chat_type="group")
        self.env.bot.handle({"update_id": 99, "message": {"text": "/me", "from": {"id": 5, "is_bot": True},
                                                          "chat": {"id": 5, "type": "private"}}})
        self.assertEqual(len(self.env.api.calls), count)
        for _ in range(bot_mod.RATE_LIMIT + 5):
            self.env.say(STRANGER, "/id")
        self.assertEqual(len(self.env.api.sent(STRANGER)), bot_mod.RATE_LIMIT)
        self.env.clock.now += 61
        self.assertIn(str(STRANGER), self.env.say(STRANGER, "/id"))


class AdminCommandTest(unittest.TestCase):
    def setUp(self):
        self.env = BotEnv()

    def tearDown(self):
        self.env.close()

    def test_only_admins_run_admin_commands(self):
        for text in ("/adduser carol", "/issue alice", "/bind alice", "/disable alice", "/enable alice", "/user alice"):
            self.assertEqual(self.env.say(STRANGER, text), "只有管理员可以使用这个命令。", text)
        with self.env.conn() as conn:
            self.assertIsNone(users.get(conn, "carol"))

    def test_adduser_validates_and_creates(self):
        self.assertIn("没有这些档位：gold", self.env.say(ADMIN, "/adduser carol gold"))
        self.assertIn("只能给一个到期日", self.env.say(ADMIN, "/adduser carol 2030-01-01 2031-01-01"))
        self.assertIn("用户名只能用", self.env.say(ADMIN, "/adduser car/ol"))
        self.assertIn("已新增 carol", self.env.say(ADMIN, "/adduser carol premium 2030-01-01"))
        self.assertIn("已存在", self.env.say(ADMIN, "/adduser carol"))
        with self.env.conn() as conn:
            carol = users.get(conn, "carol")
            shared = users.shared_short_id(conn)
            actor = conn.execute("SELECT actor FROM audit_log WHERE action = 'user-create'").fetchone()["actor"]
        self.assertEqual((carol["tiers"], carol["expires_on"], carol["short_id"]), (["premium"], "2030-01-01", shared))
        self.assertEqual(actor, f"Telegram 管理员 {ADMIN}")

    def test_issue_never_shows_the_address_to_the_admin(self):
        self.assertIn("对方还没有绑定", self.env.say(ADMIN, "/issue alice"))
        token = self.env.token("alice")
        self.assertTrue(token)
        self.assertNotIn(token, "".join(self.env.api.sent(ADMIN)))
        self.assertIn("alice", self.env.published()[0]["users"])
        self.assertIn("已经发放过", self.env.say(ADMIN, "/issue alice"))
        self.assertIn("没有用户 nobody", self.env.say(ADMIN, "/issue nobody"))
        text = self.env.say(ADMIN, "/user alice")
        self.assertIn("订阅地址：已发放", text)
        self.assertNotIn(token, text)

    def test_bind_gives_a_deep_link(self):
        text = self.env.say(ADMIN, "/bind bob")
        code = text.split("start=", 1)[1].split()[0]
        self.assertIn("https://t.me/example_test_bot?start=", text)
        self.assertIn("已绑定用户 bob", self.env.say(STRANGER, f"/start {code}"))
        self.assertIn("Telegram：已绑定", self.env.say(ADMIN, "/user bob"))

    def test_disable_asks_then_enable(self):
        self.env.issue("alice")
        self.assertIn("确定吗", self.env.say(ADMIN, "/disable alice"))
        self.assertIn("已停用 alice", self.env.press(ADMIN))
        self.assertNotIn("alice", self.env.published()[0]["users"])
        self.assertIn("已经是停用状态", self.env.say(ADMIN, "/disable alice"))
        self.assertIn("已启用 alice", self.env.say(ADMIN, "/enable alice"))
        self.assertIn("alice", self.env.published()[0]["users"])
        self.assertIn("共 3 人", self.env.say(ADMIN, "/user"))

    def test_an_admin_who_is_also_a_user(self):
        self.assertIn("/adduser", self.env.say(ADMIN, "/help"))              # not bound yet: the admin menu
        self.assertIn("你还没有绑定用户", self.env.say(ADMIN, "/sub"))
        self.env.say(ADMIN, f"/start {self.env.link('test')}")
        text = self.env.say(ADMIN, "/help")
        self.assertIn("/sub", text)
        self.assertIn("/adduser", text)
        self.assertNotIn("/adduser", self.env.say(STRANGER, "/start") or "")


class ServiceTest(unittest.TestCase):
    def test_health_needs_a_heartbeat_from_this_process(self):
        env = BotEnv()
        try:
            with env.conn() as conn:
                now = db.now()
                db.set_setting(conn, "bot_started", now + 5)      # restarted after the last heartbeat
            self.assertFalse(bot_mod.healthy(env.settings, now + 10))
            with env.conn() as conn:
                db.set_setting(conn, "bot_heartbeat", now + 6)
            self.assertTrue(bot_mod.healthy(env.settings, now + 10))
            self.assertFalse(bot_mod.healthy(env.settings, now + 6 + bot_mod.HEALTHY_SECONDS + 1))
        finally:
            env.close()

    def test_errors_never_carry_the_token(self):
        api = bot_mod.Api("http://127.0.0.1:9", "123456:secret-token-value")
        with self.assertRaises(bot_mod.ApiError) as ctx:
            api.call("getMe", http_timeout=2)
        self.assertNotIn("secret-token-value", str(ctx.exception))
        self.assertEqual(api.clean("x 123456:secret-token-value y"), "x *** y")

    def test_config_files(self):
        env = BotEnv()
        try:
            path = os.path.join(env.dirs["db"], "config.json")
            for doc, ok in (({"admins": [1, 2]}, True), ({"admins": ["1"]}, False), ({"admins": [True]}, False), ({}, False)):
                with open(path, "w") as fh:
                    json.dump(doc, fh)
                if ok:
                    self.assertEqual(bot_mod.read_admins(path), [1, 2])
                else:
                    self.assertRaises(SystemExit, bot_mod.read_admins, path)
        finally:
            env.close()


class WebTest(unittest.TestCase):
    def setUp(self):
        self.env = BotEnv()
        self.settings = self.env.settings.__class__(**dict(self.env.settings.__dict__, bot_enabled=True))
        self.app = web_app.create_app(self.settings, start_watcher=False, csrf_secret=b"k" * 32)
        self.csrf = hmac.new(b"k" * 32, b"console-form", hashlib.sha256).hexdigest()

    def tearDown(self):
        self.env.close()

    def post(self, srv, path):
        return srv.request("POST", path, urllib.parse.urlencode({"csrf": self.csrf}).encode(),
                           {"Content-Type": "application/x-www-form-urlencoded"})[0]

    def test_link_unbind_and_alert(self):
        with Server(self.app) as srv:
            self.assertIn("未绑定", srv.request("GET", "/users/alice")[1])
            self.assertEqual(self.post(srv, "/users/alice/telegram/link"), 303)
            page = srv.request("GET", "/users/alice")[1]
            code = page.split("https://t.me/example_test_bot?start=", 1)[1][:32]
            self.assertIn("已绑定用户 alice", self.env.say(ALICE, f"/start {code}"))
            self.assertIn(f"ID {ALICE}", srv.request("GET", "/users/alice")[1])
            self.assertEqual(self.post(srv, "/users/alice/telegram/unbind"), 303)
            self.assertIn("未绑定", srv.request("GET", "/users/alice")[1])
            self.post(srv, "/users/alice/telegram/link")
            self.post(srv, "/users/alice/telegram/link-delete")
            self.assertNotIn("start=", srv.request("GET", "/users/alice")[1])
            self.assertNotIn("Telegram bot 超过", srv.request("GET", "/")[1])
            with self.env.conn() as conn:
                db.set_setting(conn, "bot_heartbeat", db.now() - 600)
            self.assertIn("Telegram bot 超过 5 分钟没有连上 Telegram", srv.request("GET", "/")[1])
        with self.env.conn() as conn:
            actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]
        self.assertEqual([a for a in actions if a.startswith("telegram-")],
                         ["telegram-link", "telegram-bind", "telegram-unbind", "telegram-link", "telegram-link-delete"])

    def test_section_hidden_without_the_bot(self):
        app = web_app.create_app(self.env.settings, start_watcher=False, csrf_secret=b"k" * 32)
        with Server(app) as srv:
            self.assertNotIn('id="telegram"', srv.request("GET", "/users/alice")[1])
            self.assertNotIn("Telegram bot", srv.request("GET", "/")[1])


class MigrationTest(unittest.TestCase):
    """Progress by stage on the home and users pages, and issuing with binding links in one step."""

    def setUp(self):
        self.env = BotEnv()
        self.settings = self.env.settings.__class__(**dict(self.env.settings.__dict__, bot_enabled=True))
        self.app = web_app.create_app(self.settings, start_watcher=False, csrf_secret=b"k" * 32)
        self.csrf = hmac.new(b"k" * 32, b"console-form", hashlib.sha256).hexdigest()

    def tearDown(self):
        self.env.close()

    def post(self, srv, path, **fields):
        body = urllib.parse.urlencode(dict(fields, csrf=self.csrf), doseq=True).encode()
        return srv.request("POST", path, body, {"Content-Type": "application/x-www-form-urlencoded"})

    def fetched(self, *names):
        writer = sqlite3.connect(self.env.settings.subs_access_db)
        try:
            with writer:
                writer.execute("CREATE TABLE IF NOT EXISTS hits (ts INTEGER NOT NULL, user TEXT NOT NULL, "
                               "fmt TEXT NOT NULL, ip TEXT, user_agent TEXT)")
                writer.executemany("INSERT INTO hits VALUES (?, ?, 'v2ray', '', '')", [(db.now(), n) for n in names])
        finally:
            writer.close()

    def test_stages(self):
        self.assertEqual([users.stage(*a) for a in ((False, True, True), (True, False, False), (True, True, False),
                                                     (True, False, True))],
                         ["not_issued", "waiting", "fetched", "using"])

    def test_progress_and_stage_filter(self):
        with self.env.conn() as conn:
            conn.execute("INSERT INTO users (name, uuid, short_id, created_at, updated_at) VALUES "
                         "('dave', '44444444-4444-4444-8444-444444444444', 'a1a1a1a1', 1, 1), "
                         "('erin', '55555555-5555-4555-8555-555555555555', 'a1a1a1a1', 1, 1)")
            for name in ("alice", "bob", "test"):
                db.issue_token(conn, name, name[0] * 43)
            conn.execute("INSERT INTO traffic_daily (day, node, user, up, down) VALUES (?, 'alpha', 'alice', 1, 2)",
                         (bot_mod.queries.today(),))
            users.set_status(conn, "erin", "disabled")
        self.fetched("alice", "bob")
        with Server(self.app) as srv:
            home = srv.request("GET", "/")[1]
            self.assertIn("启用中的 4 人", home)
            for label, count in (("未发放", 1), ("待导入", 1), ("已导入", 1), ("使用中", 1)):
                self.assertIn(f"{label} {count}</a>", home)
            self.assertIn("已绑定 Telegram 0", home)
            self.assertIn("停用或到期的 1 人不计入", home)
            for stage, names in (("using", ["alice"]), ("fetched", ["bob"]), ("waiting", ["test"]),
                                 ("not_issued", ["dave"])):
                page = srv.request("GET", f"/users?stage={stage}")[1]
                shown = [n for n in ("alice", "bob", "test", "dave", "erin") if f'href="/users/{n}"' in page]
                self.assertEqual(shown, names, stage)

    def test_bulk_bind_issues_links_and_skips_bound_users(self):
        self.env.say(ALICE, f"/start {self.env.link('alice')}")
        with Server(self.app) as srv:
            self.assertIn('formaction="/users/bulk-bind"', srv.request("GET", "/users")[1])
            status, _, headers = self.post(srv, "/users/bulk-bind", names=["alice", "bob", "test", "../x"])
            self.assertEqual((status, headers["location"]), (303, "/users/links?names=bob%2Ctest"))
            page = srv.request("GET", "/users/links?names=bob,test")[1]
            codes = dict(line.split("\t") for line in
                         srv.request("GET", "/users/links.txt?names=bob,test")[1].splitlines())
            self.assertEqual(sorted(codes), ["bob", "test"])
            self.assertIn(codes["bob"], page)
            self.assertIn("发给用户的消息", page)
            self.assertIn("已绑定用户 bob", self.env.say(STRANGER, "/start " + codes["bob"].split("start=")[1]))
            self.env.say(STRANGER, "/sub")
            self.assertIn(self.env.token("bob"), self.env.api.last("sendPhoto")["caption"])
            self.assertIn("已绑定 Telegram 2", srv.request("GET", "/")[1])
        self.assertIsNone(self.env.token("alice"))                       # already bound: left alone
        self.assertIn("bob", self.env.published()[0]["users"])
        with self.env.conn() as conn:
            actions = [(r["action"], r["target"]) for r in conn.execute("SELECT action, target FROM audit_log ORDER BY id")]
        self.assertIn(("bulk-issue", "bob,test"), actions)
        self.assertIn(("telegram-link", "bob,test"), actions)

    def test_bulk_bind_needs_the_bot(self):
        with self.env.conn() as conn:
            conn.execute("DELETE FROM settings WHERE key = 'bot_username'")
        with Server(self.app) as srv:
            self.assertNotIn("bulk-bind", srv.request("GET", "/users")[1])
            self.assertEqual(self.post(srv, "/users/bulk-bind", names=["bob"])[2]["location"], "/users")
        self.assertIsNone(self.env.token("bob"))


if __name__ == "__main__":
    unittest.main()
