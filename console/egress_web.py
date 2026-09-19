"""Admin pages for proxy egress (plan-egress-console §3.5): the pool, one egress, and the assignments on a node.

web_app registers these routes and lends them the helpers every admin page shares (Pages); what an egress is and how
it reaches the nodes lives in egress.py. The node and user pages take their egress part from node_context and
user_egress.
"""
import dataclasses
import threading
import urllib.parse

from fastapi import Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

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


def register(app, settings, pages, xray):
    page, form, refused, back, actor = pages.page, pages.form, pages.refused, pages.back, pages.actor
    checking = threading.Lock()      # one check at a time: the temporary Xray listens on fixed local ports

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
            udp = {egress_id: egress.udp_support(conn, egress_id) for egress_id in items}
            uses = {}
            for a in egress.assignments(conn):
                uses.setdefault(a["egress_id"], set()).add(a["node"])
        rows = []
        for egress_id, item in items.items():
            kind = egress.TYPES.get(item["type"])
            rows.append({"id": egress_id, "name": item["name"], "type_label": kind.label if kind else item["type"],
                         "summary": kind.summary(item["config"]) if kind else "—", "labels": item["labels"],
                         "note": item["note"], "enabled": item["enabled"], "check": (found.get(egress_id) or [None])[0],
                         "udp": udp[egress_id], "nodes": sorted(uses.get(egress_id, set()))})
        return page(request, "egress.html", rows=rows, types=egress.TYPES, error=error, notice=notice,
                    schemes=scheme_rows(settings), networks=egress.NETWORKS, on_failure=egress.ON_FAILURE)

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

    def check_now(item):
        with checking:
            return egress.check_one(xray, settings.egress_check_url, item)

    @app.post("/egress/{egress_id}/check")
    async def egress_check(request: Request, egress_id: int):
        data = await form(request)
        if data is None:
            return refused(request)
        with db.connect(settings.db_path) as conn:
            item = egress.get(conn, egress_id)
        if item is None or item["type"] not in egress.TYPES:
            return back("/egress")
        result = await run_in_threadpool(check_now, item)      # up to about 10 s for a dead egress
        with db.connect(settings.db_path) as conn:
            egress.record_check(conn, egress_id, "spt", result)
        path = "/egress" if data.get("back") == "list" else f"/egress/{egress_id}"
        if not result["ok"]:
            return back_with_error(path, f"{item['name']}：不可用（{result['error'] or '检测失败'}）")
        where = result["exit_ip"] + (f"（{result['country']}）" if result["country"] else "")
        udp = {True: "支持", False: "不支持"}.get(result.get("udp"), "未知")
        return back(path + "?notice=" + urllib.parse.quote(
            f"{item['name']}：可用，出口 IP {where or '未知'}，耗时 {result['latency_ms']} ms，UDP {udp}"))

    @app.get("/egress/{egress_id}", response_class=HTMLResponse)
    def egress_item_page(request: Request, egress_id: int, error: str = "", notice: str = ""):
        with db.connect(settings.db_path) as conn:
            item = egress.get(conn, egress_id)
            if item is None or item["type"] not in egress.TYPES:
                return PlainTextResponse("not found\n", status_code=404)
            found = egress.checks(conn).get(egress_id, [])
            uses = [dict(a, what=egress.describe_conditions(a["conditions"]))
                    for a in egress.assignments(conn) if a["egress_id"] == egress_id]
        return page(request, "egress_item.html", item=item, kind=egress.TYPES[item["type"]], checks=found, uses=uses,
                    on_failure=egress.ON_FAILURE, error=error, notice=notice)

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

    async def scheme_form(data):
        conditions = egress.clean_conditions({"domains": egress.split_values(data.get("domains")),
                                              "ips": egress.split_values(data.get("ips"))}, [], kinds=("what",))
        if data.get("network") in egress.NETWORKS and conditions:
            await run_in_threadpool(egress.check_rules, xray, conditions, data.get("network"))
        return (data.get("name"), conditions, data.get("network"), data.get("on_failure"), data.get("note"))

    @app.post("/egress/schemes/new")
    async def scheme_new(request: Request):
        data = await form(request)
        if data is None:
            return refused(request)
        try:
            with db.connect(settings.db_path) as conn:
                with db.transaction(conn):
                    name, conditions, network, on_failure, note = await scheme_form(data)
                    scheme_id = egress.save_scheme(conn, None, name, conditions, network, on_failure, note)
                    db.audit(conn, "egress-scheme-add", egress.schemes(conn)[scheme_id]["name"],
                             egress.describe_conditions(conditions), actor=actor(request))
        except egress.EgressError as exc:
            return back_with_error("/egress#schemes", exc)
        return back("/egress#schemes")

    @app.get("/egress/schemes/{scheme_id}", response_class=HTMLResponse)
    def scheme_page(request: Request, scheme_id: int, error: str = ""):
        with db.connect(settings.db_path) as conn:
            scheme = egress.schemes(conn).get(scheme_id)
            if scheme is None:
                return PlainTextResponse("not found\n", status_code=404)
            items = egress.pool(conn)
            uses = [dict(a, egress_name=(items.get(a["egress_id"]) or {}).get("name", "（已删除）"))
                    for a in egress.assignments(conn) if a.get("scheme_id") == scheme_id]
        return page(request, "egress_scheme.html", scheme=scheme, uses=uses, networks=egress.NETWORKS,
                    on_failure=egress.ON_FAILURE, error=error)

    @app.post("/egress/schemes/{scheme_id}/edit")
    async def scheme_edit(request: Request, scheme_id: int):
        data = await form(request)
        if data is None:
            return refused(request)
        try:
            with db.connect(settings.db_path) as conn:
                with db.transaction(conn):
                    name, conditions, network, on_failure, note = await scheme_form(data)
                    egress.save_scheme(conn, scheme_id, name, conditions, network, on_failure, note)
                    db.audit(conn, "egress-scheme-edit", egress.schemes(conn)[scheme_id]["name"],
                             f"{egress.describe_conditions(conditions)}，{egress.NETWORKS[network]}，"
                             f"失效时{egress.ON_FAILURE[on_failure]}", actor=actor(request))
        except egress.EgressError as exc:
            return back_with_error(f"/egress/schemes/{scheme_id}", exc)
        return back(f"/egress/schemes/{scheme_id}")

    @app.post("/egress/schemes/{scheme_id}/delete")
    async def scheme_delete(request: Request, scheme_id: int):
        data = await form(request)
        if data is None:
            return refused(request)
        with db.connect(settings.db_path) as conn:
            scheme = egress.schemes(conn).get(scheme_id)
            if scheme is None:
                return back("/egress#schemes")
            if data.get("confirm") != scheme["name"]:
                return back_with_error(f"/egress/schemes/{scheme_id}", "输入的名称不一致，未删除")
            try:
                with db.transaction(conn):
                    egress.delete_scheme(conn, scheme_id)
                    db.audit(conn, "egress-scheme-delete", scheme["name"], "", actor=actor(request))
            except egress.EgressError as exc:
                return back_with_error(f"/egress/schemes/{scheme_id}", exc)
        return back("/egress#schemes")

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
                scheme_id = int(data.get("scheme_id") or 0) or None
                raw = {"users": data.getlist("users")}
                if scheme_id is None:
                    raw.update(domains=egress.split_values(data.get("domains")), ips=egress.split_values(data.get("ips")))
                elif data.get("domains", "").strip() or data.get("ips", "").strip():
                    raise egress.EgressError("选了方案时，网站和 IP 段以方案为准，这里不要再填")
                conditions = egress.clean_conditions(raw, known)
                scheme = egress.schemes(conn).get(scheme_id) if scheme_id else None
                if scheme is None and data.get("network") in egress.NETWORKS:        # assign() explains a bad one
                    await run_in_threadpool(egress.check_rules, xray, conditions, data.get("network"))
                with db.transaction(conn):
                    assignment_id = egress.assign(conn, None, egress_id, name, conditions, data.get("on_failure"),
                                                  data.get("network"), data.get("priority") or 100, scheme_id=scheme_id)
                    a = next(x for x in egress.assignments(conn, name) if x["id"] == assignment_id)
                    db.audit(conn, "egress-assign", name,
                             f"{egress.get(conn, egress_id)['name']}：{describe(a)}，{egress.NETWORKS[a['network']]}，"
                             f"失效时{egress.ON_FAILURE[a['on_failure']]}", actor=actor(request))
            except (ValueError, egress.EgressError) as exc:
                return back_with_error(f"/nodes/{name}#egress", exc)
        return back(f"/nodes/{name}#egress")

    async def node_egress_change(request, name, assignment_id, action):
        data = await form(request)
        if data is None:
            return refused(request)
        with db.connect(settings.db_path) as conn:
            a = next((x for x in egress.assignments(conn, name) if x["id"] == assignment_id), None)
            what = (f"#{assignment_id} {(egress.get(conn, a['egress_id']) or {}).get('name', '（已删除的出口）')}：{describe(a)}"
                    if a else f"#{assignment_id}")
            try:
                if action == "toggle":
                    enabled = egress.toggle_assignment(conn, assignment_id, name)
                    db.audit(conn, "egress-assign-" + ("enable" if enabled else "disable"), name, what, actor=actor(request))
                else:
                    egress.unassign(conn, assignment_id, name)
                    db.audit(conn, "egress-unassign", name, what, actor=actor(request))
            except egress.EgressError as exc:
                return back_with_error(f"/nodes/{name}#egress", exc)
        return back(f"/nodes/{name}#egress")

    @app.post("/nodes/{name}/egress/{assignment_id}/toggle")
    async def node_egress_toggle(request: Request, name: str, assignment_id: int):
        return await node_egress_change(request, name, assignment_id, "toggle")

    @app.post("/nodes/{name}/egress/{assignment_id}/delete")
    async def node_egress_delete(request: Request, name: str, assignment_id: int):
        return await node_egress_change(request, name, assignment_id, "delete")


def scheme_rows(settings):
    with db.connect(settings.db_path) as conn:
        uses = {}
        for a in egress.assignments(conn):
            if a.get("scheme_id"):
                uses.setdefault(a["scheme_id"], set()).add(a["node"])
        return [dict(s, what=egress.describe_conditions(s["conditions"]), nodes=sorted(uses.get(i, set())))
                for i, s in egress.schemes(conn).items()]


def describe(a):
    """What an assignment takes, in words, naming its scheme."""
    if a["scheme_missing"]:
        return "（方案已删除）"
    text = egress.describe_conditions(a["conditions"])
    return f"方案“{a['scheme']}”：{text}" if a["scheme"] else text


def node_context(conn, node, day):
    """Rows, choices and node users for the node page's egress section; `node` is the registered node."""
    name = node.name
    known = [u["name"] for u in users_mod.node_users(conn, name, day)]
    syncing = [u["name"] for u in users_mod.node_payload(conn, node, day)[1]]        # what /sync sends the node
    items = egress.pool(conn)
    found = egress.checks(conn)
    state = egress.node_states(conn).get(name, {})
    payload, notes = egress.plan(conn, name, syncing, state.get("schema", egress.AGENT_SCHEMA))
    version, _ = egress.node_payload(conn, name, syncing, state.get("schema", egress.AGENT_SCHEMA))
    current = state.get("applied") == version
    sent = {entry["id"] for entry in payload["assignments"]}
    rows = []
    for a in egress.assignments(conn, name):
        item = items.get(a["egress_id"])
        rows.append(dict(a, egress_name=item["name"] if item else "（已删除）", what=describe(a),
                         note=notes.get(a["id"]) if a["enabled"] else None, sent=a["id"] in sent,
                         check=next((c for c in found.get(a["egress_id"], []) if c["checker"] == name), None),
                         state=(state.get("states") or {}).get(str(a["id"])) if current else None))

    def status(egress_id):
        latest = (found.get(egress_id) or [None])[0]
        if latest is None:
            return "等待检测"
        if not latest["ok"]:
            return "不可用"
        return "可用，不支持 UDP" if egress.udp_support(conn, egress_id) is False else "可用"
    choices = sorted(({"id": i, "name": e["name"], "status": status(i)} for i, e in items.items() if e["enabled"]),
                     key=lambda c: (c["status"] != "可用", c["name"]))
    return {"egress_rows": rows, "egress_choices": choices, "node_user_names": known, "schemes": egress.schemes(conn),
            "networks": egress.NETWORKS, "on_failure": egress.ON_FAILURE, "egress_states": egress.STATES,
            "egress_error": state.get("error", "")}


def user_egress(conn, name):
    """{node: ["<egress>（全部流量 | 部分流量）"]}: the enabled egress a user's traffic may take on each node."""
    out = {}
    items = egress.pool(conn)
    for a in egress.assignments(conn):
        item = items.get(a["egress_id"])
        if not a["enabled"] or a["scheme_missing"] or not item or not item["enabled"]:
            continue
        if "users" in a["conditions"] and name not in a["conditions"]["users"]:
            continue
        part = "全部流量" if set(a["conditions"]) <= {"users"} else (f"方案“{a['scheme']}”" if a["scheme"] else "部分流量")
        out.setdefault(a["node"], []).append(f"{item['name']}（{part}）")
    return out
