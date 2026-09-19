"""Report service: the only console surface reachable from the Internet (through the console's own tunnel).

POST /report   one node report, authenticated by the node's report token (plan-console-phase1 §3.3-3.4)
POST /sync     the node agent's user sync (plan-console-phase2 §3.3): it says what runs on the node and gets the
               console's user list for that node only, or "unchanged" when it already runs it
GET  /healthz  liveness only

No admin route exists here. Responses never say which part of a token was wrong.
"""
import json
import os
import re

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config, db, egress, registry as reg, reports, sharing, users

VERSION_RE = re.compile(r"^[0-9a-f]{16}$")
MAX_RUNNING = 10000


def validate_sync(doc, node):
    """The agent's sync request: {schema, node, applied, running, error, online?, online_day?}."""
    if not (isinstance(doc, dict) and doc.get("schema") == reports.SCHEMA and doc.get("node") == node.name):
        raise ValueError("unsupported schema or another node")
    applied = doc.get("applied")
    if applied is not None and not (isinstance(applied, str) and VERSION_RE.match(applied)):
        raise ValueError("invalid applied version")
    running = doc.get("running")
    if running is not None and not (isinstance(running, list) and len(running) <= MAX_RUNNING
                                    and all(isinstance(n, str) and users.NAME_RE.match(n) for n in running)):
        raise ValueError("invalid running list")
    error = doc.get("error", "")
    if not isinstance(error, str):
        raise ValueError("invalid error")
    sharing.validate(doc.get("online"), doc.get("online_day"))
    return applied, running, error[:200]


def create_app(settings):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    registry = reg.Registry(settings.registry_dir)
    db.init(settings.db_path)

    def reply(status, **body):
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})

    async def read_body(request):
        # The tunnel may forward the body chunked, so the size is enforced while reading, not from the header.
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > reports.MAX_BODY:
                return None
        return bytes(body)

    @app.post("/sync")
    async def sync(request: Request):
        nodes, _, _ = registry.current()
        node = reports.node_for_token(nodes, request.headers.get("authorization"))
        if node is None:
            return reply(401, error="unauthorized")
        body = await read_body(request)
        if body is None:
            return reply(413, error="body too large")
        try:
            doc = json.loads(body)
            applied, running, error = validate_sync(doc, node)
        except (ValueError, TypeError) as exc:
            return reply(400, error=str(exc)[:200])
        if not node.sync:
            return reply(409, error="user sync is not enabled for this node")
        day = users.today(config.status_from_env().utc_offset_hours)
        with db.connect(settings.db_path) as conn:
            if not users.imported(conn):
                return reply(409, error="the console has no users yet")
            version, payload, pending = users.node_payload(conn, node, day)
            users.record_sync(conn, node.name, version, applied, running, pending, error)
            now = db.now()
            sharing.record(conn, sharing.validate(doc.get("online"), doc.get("online_day")), doc.get("online_day"),
                           {u["name"] for u in payload}, now)
            online = {"online_key": sharing.key_for(conn, sharing.day_of(now)), "online_day": sharing.day_of(now)}
            # proxy egress (plan-egress-console): what this node should run, and what its agent found and applied
            try:
                found, egress_applied, states, schema, egress_error = egress.validate_report(doc)
            except ValueError as exc:       # a bad egress report must not stop the user sync
                found, egress_applied, states, schema = {}, None, {}, 1
                egress_error = f"agent 上报的出口数据无效：{exc}"
            egress_version, egress_payload = egress.node_payload(conn, node.name, [u["name"] for u in payload], schema)
            mine = {int(o["tag"][len(egress.TAG_PREFIX):]) for o in egress_payload["outbounds"]}
            for egress_id, check in found.items():
                if egress_id in mine:
                    egress.record_check(conn, egress_id, node.name, check)
            if "egress_applied" in doc:
                # only an agent that manages egress reports this; others never receive the credentials
                egress.record_node_state(conn, node.name, egress_applied, states, schema, egress_error)
                online.update(egress_version=egress_version, egress_check_url=settings.egress_check_url)
                if egress_applied != egress_version:
                    online["egress"] = egress_payload
        if applied == version:
            return reply(200, version=version, unchanged=True, **online)
        return reply(200, version=version, users=payload, **online)

    @app.get("/healthz")
    def healthz():
        return reply(200, ok=True)

    @app.post("/report")
    async def report(request: Request):
        nodes, _, _ = registry.current()
        node = reports.node_for_token(nodes, request.headers.get("authorization"))
        if node is None:
            return reply(401, error="unauthorized")
        # The tunnel may forward the body chunked, so the size is enforced while reading, not from the header.
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > reports.MAX_BODY:
                return reply(413, error="body too large")
        try:
            doc = reports.validate(json.loads(body), node)
        except (ValueError, TypeError) as exc:
            return reply(400, error=str(exc)[:200])
        with db.connect(settings.db_path) as conn:
            # a syncing node also carries the users its agent added since the last deployment (plan-console-phase2 §3.3)
            carried = set(node.users)
            if node.sync:
                carried |= set((users.sync_rows(conn).get(node.name) or {}).get("running") or [])
            stored = reports.store(conn, node, doc, users=carried)
            if doc["seq"] % 288 == 0:
                db.purge(conn)
        return reply(200 if stored else 409, stored=stored)

    return app


def main():
    settings = config.from_env()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=int(os.environ.get("CONSOLE_PORT", "8201")),
                access_log=False, proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
