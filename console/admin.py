"""One-off console commands run by console.yml (docker compose run --rm web python -m console.admin ...).

import-initial FILE   On a console that has never issued a token or shown a node, import
                      {"tokens": {user: token}, "shown": [node, ...]} and publish once, so existing
                      subscription addresses and node lists stay the same. Does nothing on a used console.
"""
import argparse
import json
import sys

from subs import catalog as cat

from . import config, db, publish as pub, registry as reg


def import_initial(settings, path):
    with open(path) as fh:
        doc = json.load(fh)
    tokens = doc.get("tokens") or {}
    shown = doc.get("shown") or []
    for user, token in tokens.items():
        if not (isinstance(user, str) and reg.NAME_RE.match(user) and isinstance(token, str) and cat.TOKEN_RE.match(token)):
            raise SystemExit(f"invalid token entry for {user!r}")
    if not all(isinstance(n, str) and reg.NAME_RE.match(n) for n in shown):
        raise SystemExit("invalid node name in shown")
    db.init(settings.db_path)
    with db.connect(settings.db_path) as conn:
        if db.tokens(conn) or conn.execute("SELECT 1 FROM node_display LIMIT 1").fetchone():
            print(json.dumps({"imported": False, "reason": "console already initialized"}))
            return 0
        now = db.now()
        with db.transaction(conn):
            for user, token in sorted(tokens.items()):
                conn.execute("INSERT INTO tokens (user, token, issued_at) VALUES (?, ?, ?)", (user, token, now))
            for node in shown:
                db.set_shown(conn, node, True)
        db.audit(conn, "import", "initial", f"{len(tokens)} token(s), shown {sorted(shown)}")
        ok, detail = pub.publish(settings, conn, reg.Registry(settings.registry_dir), "首次导入")
        db.audit(conn, "publish", "catalog", detail)
    print(json.dumps({"imported": True, "users": sorted(tokens), "shown": sorted(shown),
                      "published": ok, "detail": detail}, ensure_ascii=False))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import-initial")
    p.add_argument("file")
    args = parser.parse_args(argv)
    settings = config.from_env()
    if args.command == "import-initial":
        return import_initial(settings, args.file)
    return 2


if __name__ == "__main__":
    sys.exit(main())
