"""Build and publish the subscription service's catalog.json and tokens.json (plan-console-phase1 §3.5, D-C1/D-C2).

The console is the only writer. The catalog holds only registered nodes the administrator shows; users are the
ones holding a token. A node that is shown but has no registration file stops the publish, so a missing file
never silently drops a node from everyone's subscription.

The web pages, their watcher and the bot publish from different threads and processes; a lock file next to the
database makes each publish read the data and write both files before the next one starts.
"""
import contextlib
import datetime
import fcntl
import json
import os
import secrets

from subs import catalog as cat

from . import config, db, users as users_mod


class PublishError(ValueError):
    pass


def new_token():
    return secrets.token_urlsafe(32)


def build(nodes, shown, tokens, generated_at):
    """(catalog, token document) from registry nodes, the shown set and {user: token}."""
    missing = sorted(shown - set(nodes))
    if missing:
        raise PublishError(f"shown nodes without a registration file: {missing}")
    published = {name: nodes[name] for name in sorted(shown)}
    catalog = {
        "schema": cat.SCHEMA,
        "generated_at": generated_at,
        "nodes": {name: {"label": n.label, "state": "migrated", "edge": n.edge()} for name, n in published.items()},
        "users": {
            user: {"nodes": {name: dict(n.users[user]) for name, n in published.items() if user in n.users}}
            for user in sorted(tokens)
        },
    }
    token_doc = {"schema": cat.SCHEMA, "tokens": {cat.token_digest(t): u for u, t in tokens.items()}}
    if len(token_doc["tokens"]) != len(tokens):
        raise PublishError("two users share a token")
    try:
        cat.validate_catalog(catalog)
        cat.validate_tokens(token_doc, catalog)
    except cat.CatalogError as exc:
        raise PublishError(str(exc)) from None
    return catalog, token_doc


def _write(directory, name, doc):
    path = os.path.join(directory, name)
    tmp = os.path.join(directory, f".{name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o640)  # the subscription service reads it through the directory's group
    os.replace(tmp, path)


def _unchanged(directory, catalog, token_doc):
    """True when the files already hold this content (the generation time aside)."""
    try:
        with open(os.path.join(directory, "catalog.json")) as fh:
            old_catalog = json.load(fh)
        with open(os.path.join(directory, "tokens.json")) as fh:
            old_tokens = json.load(fh)
    except (OSError, ValueError):
        return False
    strip = lambda doc: {k: v for k, v in doc.items() if k != "generated_at"}
    return strip(old_catalog) == strip(catalog) and old_tokens == token_doc


@contextlib.contextmanager
def _locked(settings):
    fd = os.open(os.path.join(os.path.dirname(settings.db_path), "publish.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def publish(settings, conn, registry, reason):
    """Write both files and log the result; returns (ok, detail). Never raises for data problems."""
    with _locked(settings):
        return _publish(settings, conn, registry, reason)


def _publish(settings, conn, registry, reason):
    nodes, problems, _ = registry.current()
    # the users each node actually runs: what its agent reported, or its last deployment (plan-console-phase2 §3.3)
    nodes = users_mod.effective_nodes(conn, nodes)
    tokens = db.tokens(conn)
    if users_mod.imported(conn):
        # a disabled or expired user's address stops working until the user is active again (plan-console-phase2 §3.1)
        day = users_mod.today(config.status_from_env().utc_offset_hours)
        active = {n for n, u in users_mod.load(conn).items() if users_mod.effective(u, day)}
        tokens = {user: token for user, token in tokens.items() if user in active}
    generated_at = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        catalog, token_doc = build(nodes, db.shown_nodes(conn), tokens, generated_at)
        unchanged = _unchanged(settings.subs_data_dir, catalog, token_doc)
        # The service reloads when either file changes and refuses a mismatched pair; a request that lands
        # between the two renames gets one 503 and the next request loads the complete pair.
        _write(settings.subs_data_dir, "tokens.json", token_doc)
        _write(settings.subs_data_dir, "catalog.json", catalog)
    except (PublishError, OSError) as exc:
        detail = f"{reason}: {type(exc).__name__}: {exc}"
        conn.execute("INSERT INTO publish_log (at, ok, detail) VALUES (?, 0, ?)", (db.now(), detail))
        return False, detail
    detail = (f"{reason}：{len(catalog['nodes'])} 个节点、{len(catalog['users'])} 个用户，"
              f"{'内容与上次相同' if unchanged else '内容已更新'}")
    if problems:
        detail += f"；跳过 {len(problems)} 个无法读取的登记文件"
    conn.execute("INSERT INTO publish_log (at, ok, detail) VALUES (?, 1, ?)", (db.now(), detail))
    return True, detail
