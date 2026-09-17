"""Report service: the only console surface reachable from the Internet (through the console's own tunnel).

POST /report   one node report, authenticated by the node's report token (plan-console-phase1 §3.3-3.4)
GET  /healthz  liveness only

No admin route exists here. Responses never say which part of a token was wrong.
"""
import json
import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config, db, registry as reg, reports


def create_app(settings):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    registry = reg.Registry(settings.registry_dir)
    db.init(settings.db_path)

    def reply(status, **body):
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})

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
            stored = reports.store(conn, node, doc)
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
