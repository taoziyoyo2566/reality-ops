"""Console commands run by Ansible (docker compose run --rm web python -m console.admin ...).

import-initial FILE   On a console that has never issued a token or shown a node, import
                      {"tokens": {user: token}, "shown": [node, ...]} and publish once, so existing
                      subscription addresses and node lists stay the same. Does nothing on a used console.
import-users FILE     On a console without users, import {"users", "node_tiers", "rules"} built by console.yml from
                      users/*.yml, the inventory groups and acl_matrix (plan-console-phase2 §3.2). Prints how each
                      registered node's deployed users differ from what the console now says (empty: equivalent).
node-users NODE       The users that belong on NODE today, for edge.yml (contains UUIDs). Exit 3 before the import.
"""
import argparse
import json
import sys

from subs import catalog as cat

from . import config, db, publish as pub, registry as reg, users as users_mod


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
        db.audit(conn, "import", "initial", f"{len(tokens)} token(s), shown {sorted(shown)}", actor="console.yml")
        ok, detail = pub.publish(settings, conn, reg.Registry(settings.registry_dir), "首次导入")
        db.audit(conn, "publish", "catalog", detail, actor="console.yml")
    print(json.dumps({"imported": True, "users": sorted(tokens), "shown": sorted(shown),
                      "published": ok, "detail": detail}, ensure_ascii=False))
    return 0 if ok else 1


def import_users(settings, path):
    with open(path) as fh:
        doc = json.load(fh)
    db.init(settings.db_path)
    day = users_mod.today(config.status_from_env().utc_offset_hours)
    nodes = reg.Registry(settings.registry_dir).current()[0]
    with db.connect(settings.db_path) as conn:
        if users_mod.imported(conn):
            result = {"imported": False, "reason": "console already has users"}
        else:
            try:
                count = users_mod.import_doc(conn, doc)
            except users_mod.UserError as exc:
                print(json.dumps({"imported": False, "error": str(exc)}, ensure_ascii=False))
                return 1
            db.audit(conn, "import", "users", f"{count} user(s)", actor="console.yml")
            result = {"imported": True, "users": count}
        result["differences"] = users_mod.differences(conn, nodes, day)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def node_users(settings, node):
    if not reg.NAME_RE.match(node):
        raise SystemExit("invalid node name")
    db.init(settings.db_path)
    day = users_mod.today(config.status_from_env().utc_offset_hours)
    with db.connect(settings.db_path) as conn:
        if not users_mod.imported(conn):
            print(json.dumps({"error": "the console has no users yet; run console.yml to import them"}))
            return 3
        users = users_mod.node_users(conn, node, day)
        short_ids = [users_mod.shared_short_id(conn)]
    print(json.dumps({"node": node, "day": day, "users": users, "short_ids": short_ids}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import-initial")
    p.add_argument("file")
    p = sub.add_parser("import-users")
    p.add_argument("file")
    p = sub.add_parser("node-users")
    p.add_argument("node")
    args = parser.parse_args(argv)
    settings = config.from_env()
    if args.command == "import-initial":
        return import_initial(settings, args.file)
    if args.command == "import-users":
        return import_users(settings, args.file)
    if args.command == "node-users":
        return node_users(settings, args.node)
    return 2


if __name__ == "__main__":
    sys.exit(main())
