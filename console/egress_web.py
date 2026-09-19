"""Admin pages for proxy egress (plan-egress-console §3.5): the pool, one egress, and the assignments on a node.

web_app registers these routes and lends them the helpers every admin page shares (Pages); what an egress is and how
it reaches the nodes lives in egress.py. The node and user pages take their egress part from node_context and
user_egress.
"""
import dataclasses
import urllib.parse

from fastapi import Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from . import db, egress, users as users_mod


@dataclasses.dataclass(frozen=True)
class Pages:
    """What the egress routes borrow from web_app."""
    page: object                 # (request, template, **context) -> response
    form: object                 # async (request) -> form data, or None without a valid token
    refused: object              # (request) -> response to a stale or cross-site form
    back: object                 # (url) -> redirect
    actor: object                # (request) -> who acts, for the audit log
    view_context: object         # (conn) -> (nodes, problems, users, managed)
    today: object                # () -> the day user rules are evaluated for


def register(app, settings, pages):
    page, form, refused, back, actor = pages.page, pages.form, pages.refused, pages.back, pages.actor

    def back_with_error(path, error):
        return back(path.replace("#", "?error=" + urllib.parse.quote(str(error)) + "#", 1) if "#" in path
                    else path + "?error=" + urllib.parse.quote(str(error)))

    def type_values(data, kind):
        return {f.name: (data.get(f"f_{f.name}") or "").strip() for f in kind.fields}

    @app.get("/egress", response_class=HTMLResponse)
    def egress_page(request: Request, error: str = "", notice: str = ""):
        with db.connect(settings.db_path) as conn:
            items = egress.pool(conn)
            found = egress.checks(conn)
            uses = {}
            for a in egress.assignments(conn):
                uses.setdefault(a["egress_id"], set()).add(a["node"])
        rows = []
        for egress_id, item in items.items():
            kind = egress.TYPES.get(item["type"])
            rows.append({"id": egress_id, "name": item["name"], "type_label": kind.label if kind else item["type"],
                         "summary": kind.summary(item["config"]) if kind else "—", "labels": item["labels"],
                         "note": item["note"], "enabled": item["enabled"], "check": (found.get(egress_id) or [None])[0],
                         "nodes": sorted(uses.get(egress_id, set()))})
        return page(request, "egress.html", rows=rows, types=egress.TYPES, error=error, notice=notice)

    @app.post("/egress/new")
    async def egress_new(request: Request):
        data = await form(request)
        if data is None:
            return refused(request)
        kind = egress.TYPES.get(data.get("type") or "")
        name = (data.get("name") or "").strip()
        try:
            if kind is None:
                raise egress.EgressError("没有这种出口类型")
            with db.connect(settings.db_path) as conn:
                with db.transaction(conn):
                    egress_id = egress.save(conn, None, name, kind.key, type_values(data, kind),
                                            egress.clean_labels(data.get("labels")), data.get("note"))
                    db.audit(conn, "egress-add", name, f"{kind.label} {kind.summary(egress.get(conn, egress_id)['config'])}",
                             actor=actor(request))
        except egress.EgressError as exc:
            return back_with_error("/egress#new", exc)
        return back("/egress")

    @app.post("/egress/import")
    async def egress_import(request: Request):
        data = await form(request)
        if data is None:
            return refused(request)
        try:
            with db.connect(settings.db_path) as conn:
                added, failed = egress.import_links(conn, data.get("links"), egress.clean_labels(data.get("labels")))
                if added:
                    db.audit(conn, "egress-import", ",".join(added), f"导入 {len(added)} 个", actor=actor(request))
        except egress.EgressError as exc:
            return back_with_error("/egress#import", exc)
        notice = f"导入 {len(added)} 个出口" + (
            f"；{len(failed)} 行未导入：" + "；".join(f"第 {n} 行 {e}" for n, e in failed[:5]) if failed else "")
        return back("/egress?notice=" + urllib.parse.quote(notice))

    @app.get("/egress/{egress_id}", response_class=HTMLResponse)
    def egress_item_page(request: Request, egress_id: int, error: str = ""):
        with db.connect(settings.db_path) as conn:
            item = egress.get(conn, egress_id)
            if item is None or item["type"] not in egress.TYPES:
                return PlainTextResponse("not found\n", status_code=404)
            found = egress.checks(conn).get(egress_id, [])
            uses = [dict(a, what=egress.describe_conditions(a["conditions"]))
                    for a in egress.assignments(conn) if a["egress_id"] == egress_id]
        return page(request, "egress_item.html", item=item, kind=egress.TYPES[item["type"]], checks=found, uses=uses,
                    on_failure=egress.ON_FAILURE, error=error)

    @app.post("/egress/{egress_id}/edit")
    async def egress_edit(request: Request, egress_id: int):
        data = await form(request)
        if data is None:
            return refused(request)
        try:
            with db.connect(settings.db_path) as conn:
                item = egress.get(conn, egress_id)
                if item is None:
                    return back("/egress")
                kind = egress.TYPES[item["type"]]
                with db.transaction(conn):
                    egress.save(conn, egress_id, (data.get("name") or "").strip(), item["type"], type_values(data, kind),
                                egress.clean_labels(data.get("labels")), data.get("note"), item["enabled"])
                    db.audit(conn, "egress-edit", item["name"], kind.summary(egress.get(conn, egress_id)["config"]),
                             actor=actor(request))
        except egress.EgressError as exc:
            return back_with_error(f"/egress/{egress_id}", exc)
        return back(f"/egress/{egress_id}")

    @app.post("/egress/{egress_id}/enable")
    async def egress_enable(request: Request, egress_id: int):
        data = await form(request)
        if data is None:
            return refused(request)
        with db.connect(settings.db_path) as conn:
            item = egress.get(conn, egress_id)
            if item is None:
                return back("/egress")
            enabled = data.get("enabled") == "1"
            egress.set_enabled(conn, egress_id, enabled)
            db.audit(conn, "egress-enable" if enabled else "egress-disable", item["name"], "", actor=actor(request))
        return back(f"/egress/{egress_id}")

    @app.post("/egress/{egress_id}/delete")
    async def egress_delete(request: Request, egress_id: int):
        data = await form(request)
        if data is None:
            return refused(request)
        with db.connect(settings.db_path) as conn:
            item = egress.get(conn, egress_id)
            if item is None:
                return back("/egress")
            if data.get("confirm") != item["name"]:
                return back_with_error(f"/egress/{egress_id}", "输入的名称不一致，未删除")
            with db.transaction(conn):
                egress.delete(conn, egress_id)
                db.audit(conn, "egress-delete", item["name"], "", actor=actor(request))
        return back("/egress")

    @app.post("/nodes/{name}/egress/new")
    async def node_egress_new(request: Request, name: str):
        data = await form(request)
        if data is None:
            return refused(request)
        with db.connect(settings.db_path) as conn:
            nodes, _, _, managed = pages.view_context(conn)
            if not managed or name not in nodes:
                return back("/nodes")
            known = [u["name"] for u in users_mod.node_users(conn, name, pages.today())]
            try:
                egress_id = int(data.get("egress_id") or 0)
                conditions = egress.clean_conditions({
                    "users": data.getlist("users"), "domains": egress.split_values(data.get("domains")),
                    "ips": egress.split_values(data.get("ips"))}, known)
                with db.transaction(conn):
                    egress.assign(conn, None, egress_id, name, conditions, data.get("on_failure"), data.get("network"),
                                  data.get("priority") or 100)
                    db.audit(conn, "egress-assign", name,
                             f"{egress.get(conn, egress_id)['name']}：{egress.describe_conditions(conditions)}，"
                             f"失效时{egress.ON_FAILURE[data.get('on_failure')]}", actor=actor(request))
            except (ValueError, egress.EgressError) as exc:
                return back_with_error(f"/nodes/{name}#egress", exc)
        return back(f"/nodes/{name}#egress")

    async def node_egress_change(request, name, assignment_id, action):
        data = await form(request)
        if data is None:
            return refused(request)
        with db.connect(settings.db_path) as conn:
            try:
                if action == "toggle":
                    enabled = egress.toggle_assignment(conn, assignment_id, name)
                    db.audit(conn, "egress-assign-" + ("enable" if enabled else "disable"), name, str(assignment_id),
                             actor=actor(request))
                else:
                    egress.unassign(conn, assignment_id, name)
                    db.audit(conn, "egress-unassign", name, str(assignment_id), actor=actor(request))
            except egress.EgressError as exc:
                return back_with_error(f"/nodes/{name}#egress", exc)
        return back(f"/nodes/{name}#egress")

    @app.post("/nodes/{name}/egress/{assignment_id}/toggle")
    async def node_egress_toggle(request: Request, name: str, assignment_id: int):
        return await node_egress_change(request, name, assignment_id, "toggle")

    @app.post("/nodes/{name}/egress/{assignment_id}/delete")
    async def node_egress_delete(request: Request, name: str, assignment_id: int):
        return await node_egress_change(request, name, assignment_id, "delete")


def node_context(conn, name, day):
    """Rows, choices and node users for the node page's egress section."""
    known = [u["name"] for u in users_mod.node_users(conn, name, day)]
    items = egress.pool(conn)
    found = egress.checks(conn)
    state = egress.node_states(conn).get(name, {})
    version, _ = egress.node_payload(conn, name, known)
    current = state.get("applied") == version
    rows = []
    for a in egress.assignments(conn, name):
        item = items.get(a["egress_id"])
        rows.append(dict(a, egress_name=item["name"] if item else "（已删除）",
                         what=egress.describe_conditions(a["conditions"]),
                         check=next((c for c in found.get(a["egress_id"], []) if c["checker"] == name), None),
                         state=(state.get("states") or {}).get(str(a["id"])) if current else None))

    def status(egress_id):
        latest = (found.get(egress_id) or [None])[0]
        return "未检测" if latest is None else ("可用" if latest["ok"] else "不可用")
    choices = sorted(({"id": i, "name": e["name"], "status": status(i)} for i, e in items.items() if e["enabled"]),
                     key=lambda c: (c["status"] != "可用", c["name"]))
    return {"egress_rows": rows, "egress_choices": choices, "node_user_names": known,
            "networks": egress.NETWORKS, "on_failure": egress.ON_FAILURE, "egress_states": egress.STATES}


def user_egress(conn, name):
    """{node: ["<egress>（全部流量 | 部分流量）"]}: the enabled egress a user's traffic may take on each node."""
    out = {}
    items = egress.pool(conn)
    for a in egress.assignments(conn):
        item = items.get(a["egress_id"])
        if not a["enabled"] or not item or not item["enabled"]:
            continue
        if "users" in a["conditions"] and name not in a["conditions"]["users"]:
            continue
        part = "全部流量" if set(a["conditions"]) <= {"users"} else "部分流量"
        out.setdefault(a["node"], []).append(f"{item['name']}（{part}）")
    return out
