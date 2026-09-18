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

from . import config, db, publish as pub, queries, registry as reg, status as stat

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
    """Republish when registration files change; take one database backup per day."""

    def __init__(self, settings, registry):
        self.settings = settings
        self.registry = registry
        self.published_stamp = None
        self.last_backup_day = None

    def tick(self):
        _, _, stamp = self.registry.current()
        with db.connect(self.settings.db_path) as conn:
            initialized = bool(db.tokens(conn)) or bool(db.shown_nodes(conn))
            if initialized and stamp != self.published_stamp:
                ok, detail = pub.publish(self.settings, conn, self.registry,
                                         "注册文件变化" if self.published_stamp is not None else "控制台启动")
                db.audit(conn, "publish", "catalog", detail)
                self.published_stamp = stamp  # a failed publish is logged once, not every tick
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
    db.init(settings.db_path)
    watcher = Watcher(settings, registry)
    if start_watcher:
        threading.Thread(target=watcher.run, daemon=True).start()
    app.state.watcher = watcher

    @app.middleware("http")
    async def guard(request: Request, call_next):
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
        return {k: v[0] for k, v in data.items()}

    def refused():
        return PlainTextResponse("form expired; reload the page and try again\n", status_code=403)

    def back(url):
        return RedirectResponse(url, status_code=303)

    def subscription_url(token):
        return f"{settings.public_base_url}/s/{token}"

    def view_context():
        """Nodes, unreadable registration files, and {user: profile or None}.

        A user deployed to a node counts even when the exported profiles are older than the deployment, so a new
        user can be issued an address right after `edge.yml`, without waiting for the next `console.yml` run.
        """
        nodes, problems, _ = registry.current()
        profiles = queries.load_users(settings.users_file)
        users = {name: None for node in nodes.values() for name in node.users}
        users.update(profiles)
        return nodes, problems, users

    @app.get("/healthz")
    def healthz():
        return PlainTextResponse("ok\n")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, published: str = ""):
        with db.connect(settings.db_path) as conn:
            nodes, problems, users = view_context()
            month = queries.this_month()
            return page(request, "home.html",
                        alerts=(queries.alerts(settings, conn, nodes, problems, users)
                                + stat.alerts(conn, st, nodes, db.now())),
                        user_count=len(users), issued=len(db.tokens(conn)),
                        node_count=len(nodes), shown=len(db.shown_nodes(conn) & set(nodes)),
                        month=month, month_total=queries.traffic_by_node(conn, month),
                        today_total=queries.traffic_by_node(conn, queries.today()),
                        last_publish=db.last_publish(conn), just_published=published == "1")

    @app.get("/users", response_class=HTMLResponse)
    def users_page(request: Request):
        with db.connect(settings.db_path) as conn:
            nodes, _, users = view_context()
            tokens = db.token_rows(conn)
            month = queries.this_month()
            traffic = queries.traffic_by_user(conn, month)
        fetches = queries.last_fetches(settings.subs_access_db)
        rows = []
        for name in sorted(set(users) | set(tokens)):
            rows.append({
                "name": name, "profile": users.get(name) is not None,
                "groups": (users.get(name) or {}).get("groups", []),
                "nodes": sorted(n for n, node in nodes.items() if name in node.users),
                "issued": name in tokens, "fetch": fetches.get(name),
                "traffic": traffic.get(name, {"up": 0, "down": 0}),
            })
        return page(request, "users.html", rows=rows, month=month)

    @app.get("/users/{name}", response_class=HTMLResponse)
    def user_page(request: Request, name: str):
        if not NAME_RE.match(name):
            return PlainTextResponse("not found\n", status_code=404)
        with db.connect(settings.db_path) as conn:
            nodes, _, users = view_context()
            token = db.token_rows(conn).get(name)
            shown = db.shown_nodes(conn)
            if name not in users and token is None:
                return PlainTextResponse("not found\n", status_code=404)
            month = queries.this_month()
            per_node = {n: {"up": up, "down": down}
                        for u, n, up, down in queries.traffic_matrix(conn, month) if u == name}
            daily = queries.daily_totals(conn, user=name)
        url = subscription_url(token["token"]) if token else None
        node_rows = [{"name": n, "label": node.label, "shown": n in shown, "xhttp": node.xhttp["enabled"],
                      "traffic": per_node.get(n, {"up": 0, "down": 0})}
                     for n, node in sorted(nodes.items()) if name in node.users]
        return page(request, "user.html", name=name, profile=users.get(name), known=name in users, token=token, url=url,
                    qr=qr_svg(url) if url else None, node_rows=node_rows, month=month, daily=daily,
                    fetch=queries.last_fetches(settings.subs_access_db).get(name))

    async def change_token(request, name, action):
        data = await form(request)
        if data is None:
            return refused()
        if not NAME_RE.match(name):
            return PlainTextResponse("not found\n", status_code=404)
        _, _, users = view_context()
        with db.connect(settings.db_path) as conn:
            existing = db.token_rows(conn).get(name)
            now = db.now()
            if action == "issue":
                if existing or name not in users:
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
            db.audit(conn, action, name, detail if ok else f"发布失败：{detail}")
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
            nodes, problems, _ = view_context()
            status = queries.node_status(conn)
            shown = db.shown_nodes(conn)
            today = queries.traffic_by_node(conn, queries.today())
            month = queries.traffic_by_node(conn, queries.this_month())
        now = db.now()
        rows = []
        for name in sorted(set(nodes) | shown):
            node = nodes.get(name)
            st = status.get(name)
            online = bool(st) and now - st["received_at"] <= settings.offline_minutes * 60
            rows.append({"name": name, "node": node, "shown": name in shown, "status": st, "online": online,
                         "today": today.get(name, {"up": 0, "down": 0}), "month": month.get(name, {"up": 0, "down": 0})})
        return page(request, "nodes.html", rows=rows, problems=problems)

    @app.get("/nodes/{name}", response_class=HTMLResponse)
    def node_page(request: Request, name: str):
        with db.connect(settings.db_path) as conn:
            nodes, _, _ = view_context()
            if name not in nodes:
                return PlainTextResponse("not found\n", status_code=404)
            month = queries.this_month()
            per_user = [(u, up, down) for u, n, up, down in queries.traffic_matrix(conn, month) if n == name]
            incidents = stat.incident_rows(conn, db.now() - st.show_days * 86400, node=name)
            return page(request, "node.html", name=name, node=nodes[name], shown=name in db.shown_nodes(conn),
                        incidents=incidents,
                        status=queries.node_status(conn).get(name), per_user=per_user, month=month,
                        daily=queries.daily_totals(conn, node=name), offline_minutes=settings.offline_minutes,
                        now=db.now())

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
            db.audit(conn, "show" if shown else "hide", name, detail if ok else f"发布失败：{detail}")
        return back(f"/nodes/{name}" if name in nodes else "/nodes")

    @app.get("/status", response_class=HTMLResponse)
    def status_page(request: Request, day: str = ""):
        now = db.now()
        with db.connect(settings.db_path) as conn:
            nodes, _, _ = view_context()
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
                db.audit(conn, "incident-note", str(incident_id), note)
        return back("/status#notes")

    @app.post("/publish")
    async def publish_now(request: Request):
        data = await form(request)
        if data is None:
            return refused()
        with db.connect(settings.db_path) as conn:
            ok, detail = pub.publish(settings, conn, registry, "手动发布")
            db.audit(conn, "publish", "catalog", detail)
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
