"""Admin pages (plan-console-phase1 §3.2). Published only on the host's 127.0.0.1 and reached through SSH.

There is no login in phase 1, so the app protects itself against the two ways a browser on the admin's machine
could be turned against it: other Host headers are refused (DNS rebinding) and every form carries a token bound
to this process (cross-site requests).
"""
import datetime
import hashlib
import hmac
import io
import os
import re
import secrets
import threading
import time
import urllib.parse

import segno
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from subs import statuspage

from . import auth, config, db, publish as pub, queries, registry as reg, status as stat, users as users_mod

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    # 不能用 no-referrer：浏览器会让表单 POST 带 `Origin: null`，被下面的来源检查拒绝。
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
}
WATCH_SECONDS = 30
SYNC_STALE_SECONDS = 300
STATUS_NAMES = {"active": "启用", "disabled": "停用", "expired": "已到期", "unknown": "不在控制台"}


class FormData(dict):
    """Form fields: get() gives the first value, getlist() all of them (checkbox groups)."""

    def __init__(self, parsed):
        super().__init__({k: v[0] for k, v in parsed.items()})
        self._all = parsed

    def getlist(self, key):
        return list(self._all.get(key, []))


def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def make_timefmt(offset_hours, label):
    zone = datetime.timezone(datetime.timedelta(hours=offset_hours))

    def fmt(epoch):
        if not epoch:
            return "—"
        return datetime.datetime.fromtimestamp(epoch, zone).strftime("%Y-%m-%d %H:%M ") + label
    return fmt


def qr_svg(text):
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", xmldecl=False, scale=4, border=2)
    return buf.getvalue().decode()


class Watcher:
    """Republish when registration files change or a new day starts (expiry); take one database backup per day."""

    def __init__(self, settings, registry, st=None):
        self.settings = settings
        self.registry = registry
        self.st = st or config.status_from_env()
        self.published_stamp = None
        self.published_day = None
        self.published_sync = None
        self.last_backup_day = None

    def tick(self):
        _, _, stamp = self.registry.current()
        user_day = users_mod.today(self.st.utc_offset_hours)
        with db.connect(self.settings.db_path) as conn:
            # the users node agents report running (plan-console-phase2 §3.3) decide what subscriptions contain
            synced = conn.execute("SELECT group_concat(node || '=' || coalesce(running, ''), ';') "
                                  "FROM (SELECT node, running FROM node_sync ORDER BY node)").fetchone()[0]
            initialized = bool(db.tokens(conn)) or bool(db.shown_nodes(conn))
            if initialized and (stamp, user_day, synced) != (self.published_stamp, self.published_day, self.published_sync):
                if self.published_stamp is None:
                    reason = "控制台启动"
                elif stamp != self.published_stamp:
                    reason = "注册文件变化"
                elif synced != self.published_sync:
                    reason = "节点用户同步"
                else:
                    reason = "日期变更（到期检查）"
                ok, detail = pub.publish(self.settings, conn, self.registry, reason)
                db.audit(conn, "publish", "catalog", detail, actor="控制台")
                # a failed publish is logged once, not every tick
                self.published_stamp, self.published_day, self.published_sync = stamp, user_day, synced
        day = queries.today()
        if day != self.last_backup_day:
            db.backup(self.settings.db_path, self.settings.backup_dir, self.settings.backup_keep, day)
            self.last_backup_day = day

    def run(self):
        while True:
            try:
                self.tick()
            except Exception as exc:  # the watcher must keep running; the error is visible in the container log
                print(f"console: watcher error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(WATCH_SECONDS)


def create_app(settings, start_watcher=True, csrf_secret=None, status_settings=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=TEMPLATES)
    registry = reg.Registry(settings.registry_dir)
    secret = csrf_secret or secrets.token_bytes(32)
    csrf = hmac.new(secret, b"console-form", hashlib.sha256).hexdigest()
    timefmt = make_timefmt(float(os.environ.get("CONSOLE_UTC_OFFSET_HOURS", "9")),
                           os.environ.get("CONSOLE_TZ_LABEL", "JST"))
    templates.env.filters["bytes"] = human_bytes
    templates.env.filters["time"] = timefmt
    st = status_settings or config.status_from_env()
    templates.env.filters["stime"] = make_timefmt(st.utc_offset_hours, st.tz_label)
    templates.env.globals["status_enabled"] = st.enabled
    templates.env.globals["status_tz"] = st.tz_label
    auth.check_mode(settings.auth_mode)
    db.init(settings.db_path)
    watcher = Watcher(settings, registry, st)
    if start_watcher:
        threading.Thread(target=watcher.run, daemon=True).start()
    app.state.watcher = watcher

    @app.middleware("http")
    async def guard(request: Request, call_next):
        request.state.actor = auth.actor_for(request, settings.auth_mode)
        host = request.headers.get("host", "")
        if request.url.path != "/healthz" and host not in settings.allowed_hosts:
            return PlainTextResponse("host not allowed\n", status_code=400)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and origin not in {f"http://{h}" for h in settings.allowed_hosts}:
                return PlainTextResponse("cross-origin request refused\n", status_code=403)
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    def page(request, template, **context):
        context.update(request=request, csrf=csrf, nav=template.split(".")[0])
        return templates.TemplateResponse(request, template, context)

    async def form(request):
        data = urllib.parse.parse_qs((await request.body()).decode("utf-8", "replace"))
        if not hmac.compare_digest((data.get("csrf") or [""])[0], csrf):
            return None
        return FormData(data)

    def refused():
        return PlainTextResponse("form expired; reload the page and try again\n", status_code=403)

    def back(url):
        return RedirectResponse(url, status_code=303)

    def subscription_url(token):
        return f"{settings.public_base_url}/s/{token}"

    def view_context(conn):
        """Nodes, unreadable registration files, {user: record or None} and whether users are managed here.

        After the import (plan-console-phase2 §3.2) users come from the console database. Before it, the list is
        phase 1's, read-only: the exported profiles plus the users found on nodes (None: on a node, no profile).
        """
        nodes, problems, _ = registry.current()
        if users_mod.imported(conn):
            return users_mod.effective_nodes(conn, nodes), problems, users_mod.load(conn), True
        profiles = queries.load_users(settings.users_file)
        records = {name: None for node in nodes.values() for name in node.users}
        records.update({name: {"name": name, "tiers": p.get("groups") or [users_mod.ALL],
                               "allow_nodes": p.get("hosts", []), "deny_nodes": p.get("deny_hosts", []),
                               "status": "active", "expires_on": None, "note": ""}
                        for name, p in profiles.items()})
        return nodes, problems, records, False

    def today():
        return users_mod.today(st.utc_offset_hours)

    def user_state(record, day):
        if record is None:
            return "unknown"
        if record["status"] != "active":
            return "disabled"
        if record.get("expires_on") and record["expires_on"] < day:
            return "expired"
        return "active"

    def actor(request):
        return request.state.actor.name

    def pending_users(conn, nodes, managed, day):
        """{user: [nodes where the user is not yet as the console says]} (managed users only)."""
        out = {}
        if managed:
            for node, diff in users_mod.differences(conn, nodes, day).items():
                for name in diff["add"] + diff["remove"] + diff["change"]:
                    out.setdefault(name, []).append(node)
        return out

    def sync_state(node, row, now):
        """(label, css class) of a node's user sync."""
        if not node.sync:
            return "未开启", "muted"
        if row is None:
            return "未联系", "warn"
        if now - row["checked_at"] > SYNC_STALE_SECONDS:
            return f"{(now - row['checked_at']) // 60} 分钟未联系", "bad"
        if row["error"]:
            return "失败", "bad"
        if row["applied"] == row["desired"]:
            return "已同步", "ok"
        return "同步中", "warn"

    @app.get("/healthz")
    def healthz():
        return PlainTextResponse("ok\n")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, published: str = ""):
        with db.connect(settings.db_path) as conn:
            nodes, problems, users, managed = view_context(conn)
            month = queries.this_month()
            return page(request, "home.html",
                        alerts=(queries.alerts(settings, conn, nodes, problems, users)
                                + user_alerts(conn, nodes, users, managed)
                                + stat.alerts(conn, st, nodes, db.now())),
                        user_count=len(users), issued=len(db.tokens(conn)),
                        node_count=len(nodes), shown=len(db.shown_nodes(conn) & set(nodes)),
                        month=month, month_total=queries.traffic_by_node(conn, month),
                        today_total=queries.traffic_by_node(conn, queries.today()),
                        last_publish=db.last_publish(conn), just_published=published == "1")

    def user_alerts(conn, nodes, users, managed):
        if not managed:
            return ["用户尚未导入控制台：运行 console.yml 完成导入后才能在网页上新增和编辑用户"]
        out = []
        day = today()
        now = db.now()
        rows = users_mod.sync_rows(conn)
        waiting = {n: set(r["pending"]) for n, r in rows.items() if nodes.get(n) and nodes[n].sync}
        diff = users_mod.differences(conn, nodes, day)

        def summary(items):
            return "、".join(f"{n}（+{len(d['add'])} −{len(d['remove'])}{' 改' + str(len(d['change'])) if d['change'] else ''}）"
                            for n, d in items)
        deploy = [(n, d) for n, d in diff.items() if not nodes[n].sync]
        syncing = [(n, dict(d, add=[u for u in d["add"] if u not in waiting.get(n, set())]))
                   for n, d in diff.items() if nodes[n].sync]
        syncing = [(n, d) for n, d in syncing if any(d.values())]
        if deploy:
            out.append(f"{len(deploy)} 台节点上的用户与控制台不一致，运行 edge.yml 后生效：{summary(deploy)}")
        if syncing:
            out.append(f"{len(syncing)} 台节点正在同步用户（约一分钟内生效）：{summary(syncing)}")
        for name, node in sorted(nodes.items()):
            if not node.sync:
                continue
            row = rows.get(name)
            if row is None or now - row["checked_at"] > SYNC_STALE_SECONDS:
                out.append(f"节点 {name} 的用户同步超过 {SYNC_STALE_SECONDS // 60} 分钟没有联系控制台")
            elif row["error"]:
                out.append(f"节点 {name} 用户同步失败：{row['error']}")
            if row and row["pending"]:
                out.append(f"{len(row['pending'])} 个用户需要运行 edge.yml 后才能加入节点 {name}（节点尚未配置其 short_id）")
        limit = (datetime.date.fromisoformat(day) + datetime.timedelta(days=users_mod.EXPIRY_WARN_DAYS)).isoformat()
        for name, u in sorted(users.items()):
            if u and u["status"] == "active" and u.get("expires_on") and day <= u["expires_on"] <= limit:
                out.append(f"用户 {name} 将于 {u['expires_on']} 到期")
        return out

    @app.get("/users", response_class=HTMLResponse)
    def users_page(request: Request, q: str = "", status: str = "", tier: str = ""):
        day = today()
        with db.connect(settings.db_path) as conn:
            nodes, _, users, managed = view_context(conn)
            tokens = db.token_rows(conn)
            month = queries.this_month()
            traffic = queries.traffic_by_user(conn, month)
            pending = pending_users(conn, nodes, managed, day)
            tier_options = users_mod.known_tiers(conn) if managed else []
            usable = {name: users_mod.user_nodes(conn, u, nodes) for name, u in users.items() if u and managed}
        fetches = queries.last_fetches(settings.subs_access_db)
        rows = []
        for name in sorted(set(users) | set(tokens)):
            record = users.get(name)
            state = user_state(record, day) if name in users else "unknown"
            if q and q.lower() not in (name + " " + ((record or {}).get("note") or "")).lower():
                continue
            if status and state != status:
                continue
            if tier and tier not in (record or {}).get("tiers", []):
                continue
            on_nodes = sorted(n for n, node in nodes.items() if name in node.users)
            rows.append({
                "name": name, "record": record, "state": state, "note": (record or {}).get("note", ""),
                "tiers": (record or {}).get("tiers", []), "expires_on": (record or {}).get("expires_on"),
                "nodes": usable.get(name, on_nodes), "pending": pending.get(name, []),
                "issued": name in tokens,
                "issuable": name not in tokens and (state == "active" or (state == "unknown" and not managed
                                                                          and name in users)),
                "fetch": fetches.get(name), "traffic": traffic.get(name, {"up": 0, "down": 0}),
            })
        return page(request, "users.html", rows=rows, month=month, managed=managed, q=q, status=status, tier=tier,
                    tier_options=tier_options, status_names=STATUS_NAMES)

    @app.get("/users/new", response_class=HTMLResponse)
    def user_new_page(request: Request):
        with db.connect(settings.db_path) as conn:
            nodes, _, _, managed = view_context(conn)
            if not managed:
                return back("/users")
            return page(request, "user_new.html", tier_options=users_mod.known_tiers(conn), node_names=sorted(nodes),
                        error="", values={"name": "", "tiers": [users_mod.ALL], "allow_nodes": [], "deny_nodes": [],
                                          "expires_on": "", "note": ""})

    @app.post("/users/new", response_class=HTMLResponse)
    async def user_create(request: Request):
        data = await form(request)
        if data is None:
            return refused()
        name = (data.get("name") or "").strip()
        with db.connect(settings.db_path) as conn:
            nodes, _, _, managed = view_context(conn)
            if not managed:
                return back("/users")
            try:
                fields = users_mod.clean_fields(data.getlist("tiers"), data.getlist("allow_nodes"),
                                                data.getlist("deny_nodes"), data.get("expires_on"), data.get("note"),
                                                known_nodes=nodes)
                with db.transaction(conn):
                    users_mod.create(conn, name, fields)
                    db.audit(conn, "user-create", name, describe(fields), actor=actor(request))
            except users_mod.UserError as exc:
                return page(request, "user_new.html", tier_options=users_mod.known_tiers(conn),
                            node_names=sorted(nodes), error=str(exc),
                            values={"name": name, "tiers": data.getlist("tiers"), "allow_nodes": data.getlist("allow_nodes"),
                                    "deny_nodes": data.getlist("deny_nodes"), "expires_on": data.get("expires_on") or "",
                                    "note": data.get("note") or ""})
        return back(f"/users/{name}")

    @app.get("/users/export", response_class=HTMLResponse)
    def users_export_page(request: Request, names: str = ""):
        return page(request, "users_export.html", lines=export_lines(names), names=names)

    @app.get("/users/export.txt")
    def users_export_text(names: str = ""):
        text = "".join(f"{name}\t{url}\n" for name, url in export_lines(names))
        return PlainTextResponse(text, headers={"Content-Disposition": 'attachment; filename="subscriptions.txt"'})

    def export_lines(names):
        wanted = [n for n in names.split(",") if NAME_RE.match(n)]
        with db.connect(settings.db_path) as conn:
            tokens = db.tokens(conn)
        return [(n, subscription_url(tokens[n])) for n in wanted if n in tokens]

    @app.post("/users/bulk-issue")
    async def users_bulk_issue(request: Request):
        data = await form(request)
        if data is None:
            return refused()
        names = [n for n in data.getlist("names") if NAME_RE.match(n)]
        day = today()
        issued = []
        with db.connect(settings.db_path) as conn:
            _, _, users, _ = view_context(conn)
            existing = db.tokens(conn)
            for name in names:
                if name in users and name not in existing and user_state(users[name], day) in ("active", "unknown"):
                    conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES (?, ?, ?)",
                                 (name, pub.new_token(), db.now()))
                    issued.append(name)
            if issued:
                ok, detail = pub.publish(settings, conn, registry, f"批量发放 {len(issued)} 人")
                db.audit(conn, "bulk-issue", ",".join(issued), detail if ok else f"发布失败：{detail}", actor=actor(request))
        return back("/users/export?names=" + urllib.parse.quote(",".join(names)))

    @app.get("/users/{name}", response_class=HTMLResponse)
    def user_page(request: Request, name: str, error: str = ""):
        if not NAME_RE.match(name):
            return PlainTextResponse("not found\n", status_code=404)
        day = today()
        with db.connect(settings.db_path) as conn:
            nodes, _, users, managed = view_context(conn)
            token = db.token_rows(conn).get(name)
            shown = db.shown_nodes(conn)
            if name not in users and token is None:
                return PlainTextResponse("not found\n", status_code=404)
            record = users.get(name)
            month = queries.this_month()
            per_node = {n: {"up": up, "down": down}
                        for u, n, up, down in queries.traffic_matrix(conn, month) if u == name}
            daily = queries.daily_totals(conn, user=name)
            usable = set(users_mod.user_nodes(conn, record, nodes)) if (managed and record) else None
            tier_options = users_mod.known_tiers(conn) if managed else []
        url = subscription_url(token["token"]) if token else None
        names = sorted(nodes) if usable is not None else sorted(n for n, node in nodes.items() if name in node.users)
        node_rows = [{"name": n, "label": nodes[n].label, "shown": n in shown, "xhttp": nodes[n].xhttp["enabled"],
                      "deployed": name in nodes[n].users, "allowed": usable is None or n in usable,
                      "traffic": per_node.get(n, {"up": 0, "down": 0})}
                     for n in names if usable is None or n in usable or name in nodes[n].users]
        return page(request, "user.html", name=name, record=record, known=name in users, managed=managed,
                    state=user_state(record, day) if name in users else "unknown", token=token, url=url,
                    qr=qr_svg(url) if url else None, node_rows=node_rows, month=month, daily=daily,
                    fetch=queries.last_fetches(settings.subs_access_db).get(name), error=error,
                    tier_options=tier_options, node_names=sorted(nodes))

    def describe(fields):
        parts = [f"档位 {','.join(fields['tiers'])}"]
        if fields["allow_nodes"]:
            parts.append(f"允许 {','.join(fields['allow_nodes'])}")
        if fields["deny_nodes"]:
            parts.append(f"禁止 {','.join(fields['deny_nodes'])}")
        if fields["expires_on"]:
            parts.append(f"到期 {fields['expires_on']}")
        return "；".join(parts)

    @app.post("/users/{name}/edit")
    async def user_edit(request: Request, name: str):
        data = await form(request)
        if data is None:
            return refused()
        with db.connect(settings.db_path) as conn:
            nodes, _, _, managed = view_context(conn)
            if not managed or not NAME_RE.match(name):
                return back("/users")
            try:
                fields = users_mod.clean_fields(data.getlist("tiers"), data.getlist("allow_nodes"),
                                                data.getlist("deny_nodes"), data.get("expires_on"), data.get("note"),
                                                known_nodes=nodes)
                with db.transaction(conn):
                    users_mod.update(conn, name, fields)
                    db.audit(conn, "user-edit", name, describe(fields), actor=actor(request))
            except users_mod.UserError as exc:
                return back(f"/users/{name}?error=" + urllib.parse.quote(str(exc)) + "#edit")
            # expiry decides whether the subscription is served
            ok, detail = pub.publish(settings, conn, registry, f"修改 {name}")
            if not ok:
                db.audit(conn, "publish", "catalog", f"发布失败：{detail}", actor=actor(request))
        return back(f"/users/{name}")

    @app.post("/users/{name}/status")
    async def user_status(request: Request, name: str):
        data = await form(request)
        if data is None:
            return refused()
        status = data.get("status")
        with db.connect(settings.db_path) as conn:
            _, _, _, managed = view_context(conn)
            if not managed or status not in users_mod.STATUSES or users_mod.get(conn, name) is None:
                return back("/users")
            users_mod.set_status(conn, name, status)
            ok, detail = pub.publish(settings, conn, registry, f"{'启用' if status == 'active' else '停用'} {name}")
            db.audit(conn, "user-" + ("enable" if status == "active" else "disable"), name,
                     detail if ok else f"发布失败：{detail}", actor=actor(request))
        return back(f"/users/{name}")

    @app.post("/users/{name}/delete")
    async def user_delete(request: Request, name: str):
        data = await form(request)
        if data is None:
            return refused()
        with db.connect(settings.db_path) as conn:
            _, _, _, managed = view_context(conn)
            if not managed or users_mod.get(conn, name) is None:
                return back("/users")
            if data.get("confirm") != name:
                return back(f"/users/{name}?error=" + urllib.parse.quote("输入的用户名不一致，未删除") + "#delete")
            with db.transaction(conn):
                users_mod.delete(conn, name)
                db.audit(conn, "user-delete", name, "", actor=actor(request))
            ok, detail = pub.publish(settings, conn, registry, f"删除 {name}")
            if not ok:
                db.audit(conn, "publish", "catalog", f"发布失败：{detail}", actor=actor(request))
        return back("/users")

    async def change_token(request, name, action):
        data = await form(request)
        if data is None:
            return refused()
        if not NAME_RE.match(name):
            return PlainTextResponse("not found\n", status_code=404)
        day = today()
        with db.connect(settings.db_path) as conn:
            _, _, users, _ = view_context(conn)
            existing = db.token_rows(conn).get(name)
            now = db.now()
            if action == "issue":
                if existing or name not in users or user_state(users[name], day) not in ("active", "unknown"):
                    return back(f"/users/{name}")
                conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES (?, ?, ?)", (name, pub.new_token(), now))
            elif action == "rotate":
                if not existing:
                    return back(f"/users/{name}")
                conn.execute("UPDATE tokens SET token = ?, rotated_at = ? WHERE user = ?", (pub.new_token(), now, name))
            elif action == "revoke":
                if data.get("confirm") != name:
                    return back(f"/users/{name}")
                conn.execute("DELETE FROM tokens WHERE user = ?", (name,))
            ok, detail = pub.publish(settings, conn, registry, f"{action} {name}")
            db.audit(conn, action, name, detail if ok else f"发布失败：{detail}", actor=actor(request))
        return back(f"/users/{name}")

    @app.post("/users/{name}/issue")
    async def issue(request: Request, name: str):
        return await change_token(request, name, "issue")

    @app.post("/users/{name}/rotate")
    async def rotate(request: Request, name: str):
        return await change_token(request, name, "rotate")

    @app.post("/users/{name}/revoke")
    async def revoke(request: Request, name: str):
        return await change_token(request, name, "revoke")

    @app.get("/nodes", response_class=HTMLResponse)
    def nodes_page(request: Request):
        with db.connect(settings.db_path) as conn:
            nodes, problems, _, _ = view_context(conn)
            status = queries.node_status(conn)
            shown = db.shown_nodes(conn)
            sync_rows = users_mod.sync_rows(conn)
            day_traffic = queries.traffic_by_node(conn, queries.today())
            month = queries.traffic_by_node(conn, queries.this_month())
        now = db.now()
        rows = []
        for name in sorted(set(nodes) | shown):
            node = nodes.get(name)
            st = status.get(name)
            online = bool(st) and now - st["received_at"] <= settings.offline_minutes * 60
            sync = sync_state(node, sync_rows.get(name), now) if node else ("—", "muted")
            rows.append({"name": name, "node": node, "shown": name in shown, "status": st, "online": online, "sync": sync,
                         "today": day_traffic.get(name, {"up": 0, "down": 0}), "month": month.get(name, {"up": 0, "down": 0})})
        return page(request, "nodes.html", rows=rows, problems=problems)

    @app.get("/nodes/{name}", response_class=HTMLResponse)
    def node_page(request: Request, name: str):
        with db.connect(settings.db_path) as conn:
            nodes, _, _, managed = view_context(conn)
            if name not in nodes:
                return PlainTextResponse("not found\n", status_code=404)
            month = queries.this_month()
            per_user = [(u, up, down) for u, n, up, down in queries.traffic_matrix(conn, month) if n == name]
            incidents = stat.incident_rows(conn, db.now() - st.show_days * 86400, node=name)
            tier_rules = users_mod.rules(conn) if managed else {}
            node_tier = users_mod.node_tiers(conn).get(name, []) if managed else []
            diff = users_mod.differences(conn, {name: nodes[name]}, today()).get(name) if managed else None
            wanted = len(users_mod.node_users(conn, name, today())) if managed else None
            sync_row = users_mod.sync_rows(conn).get(name)
            return page(request, "node.html", name=name, node=nodes[name], shown=name in db.shown_nodes(conn),
                        incidents=incidents, managed=managed, tier_rules=tier_rules, node_tier=node_tier, diff=diff,
                        wanted=wanted, sync_row=sync_row, sync=sync_state(nodes[name], sync_row, db.now()),
                        status=queries.node_status(conn).get(name), per_user=per_user, month=month,
                        daily=queries.daily_totals(conn, node=name), offline_minutes=settings.offline_minutes,
                        now=db.now())

    @app.post("/nodes/{name}/tiers")
    async def node_tiers_set(request: Request, name: str):
        data = await form(request)
        if data is None:
            return refused()
        with db.connect(settings.db_path) as conn:
            nodes, _, _, managed = view_context(conn)
            if not managed or name not in nodes:
                return back("/nodes")
            known = set(users_mod.rules(conn))
            chosen = [t for t in data.getlist("tiers") if t in known]
            users_mod.set_node_tiers(conn, name, chosen)
            db.audit(conn, "node-tiers", name, ",".join(chosen) or "（无）", actor=actor(request))
        return back(f"/nodes/{name}#tiers")

    @app.post("/nodes/{name}/show")
    async def node_show(request: Request, name: str):
        data = await form(request)
        if data is None:
            return refused()
        nodes, _, _ = registry.current()
        shown = data.get("shown") == "1"
        if name not in nodes and shown:
            return back("/nodes")
        with db.connect(settings.db_path) as conn:
            db.set_shown(conn, name, shown)
            ok, detail = pub.publish(settings, conn, registry, f"{'显示' if shown else '隐藏'} {name}")
            db.audit(conn, "show" if shown else "hide", name, detail if ok else f"发布失败：{detail}",
                     actor=actor(request))
        return back(f"/nodes/{name}" if name in nodes else "/nodes")

    @app.get("/status", response_class=HTMLResponse)
    def status_page(request: Request, day: str = ""):
        now = db.now()
        with db.connect(settings.db_path) as conn:
            nodes, _, _, _ = view_context(conn)
            doc = stat.build_document(conn, st, nodes, now)
            states = stat.load_states(conn)
            last = stat.last_round(conn)
            incidents = stat.incident_rows(conn, now - 30 * 86400)
        rows = []
        for name, transport in stat.targets(nodes):
            state = states.get((name, transport)) or {}
            rows.append({**state, "node": name, "transport": stat.TRANSPORT_NAMES[transport]})
        body = statuspage.body(doc, day, lambda d: f"/status?day={d}#events")
        return page(request, "status.html", body=body, status_css=statuspage.CSS, rows=rows, last_round=last,
                    stale=last is None or now - last > 3 * st.interval, incidents=incidents,
                    kinds=statuspage.KINDS, note_max=stat.NOTE_MAX, labels={n: nodes[n].label for n in nodes})

    @app.post("/incidents/{incident_id}/note")
    async def incident_note(request: Request, incident_id: int):
        data = await form(request)
        if data is None:
            return refused()
        with db.connect(settings.db_path) as conn:
            ok, note = stat.set_note(conn, incident_id, data.get("note", ""))
            if ok:
                db.audit(conn, "incident-note", str(incident_id), note, actor=actor(request))
        return back("/status#notes")

    @app.post("/publish")
    async def publish_now(request: Request):
        data = await form(request)
        if data is None:
            return refused()
        with db.connect(settings.db_path) as conn:
            ok, detail = pub.publish(settings, conn, registry, "手动发布")
            db.audit(conn, "publish", "catalog", detail, actor=actor(request))
        return back("/?published=1#publish")

    @app.get("/traffic", response_class=HTMLResponse)
    def traffic_page(request: Request, month: str = ""):
        with db.connect(settings.db_path) as conn:
            months = queries.months(conn)
            month = month if re.match(r"^\d{4}-\d{2}$", month or "") else queries.this_month()
            return page(request, "traffic.html", month=month, months=months,
                        matrix=queries.traffic_matrix(conn, month),
                        by_user=queries.traffic_by_user(conn, month), by_node=queries.traffic_by_node(conn, month),
                        daily=queries.daily_totals(conn))

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request):
        with db.connect(settings.db_path) as conn:
            return page(request, "audit.html", entries=queries.audit_entries(conn),
                        publishes=queries.publish_entries(conn))

    return app


def main():
    settings = config.from_env()
    if not settings.public_base_url:
        raise SystemExit("CONSOLE_PUBLIC_BASE_URL is required")
    uvicorn.run(create_app(settings), host="0.0.0.0", port=int(os.environ.get("CONSOLE_PORT", "8200")),
                access_log=False, proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
