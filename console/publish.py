"""Build and publish the subscription service's catalog.json and tokens.json (plan-console-phase1 §3.5, D-C1/D-C2).

The console is the only writer. The catalog holds only registered nodes the administrator shows; users are the
ones holding a token. A node that is shown but has no registration file stops the publish, so a missing file
never silently drops a node from everyone's subscription.
"""
import datetime
import json
import os
import secrets

from subs import catalog as cat

from . import db


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


def publish(settings, conn, registry, reason):
    """Write both files and log the result; returns (ok, detail). Never raises for data problems."""
    nodes, problems, _ = registry.current()
    tokens = db.tokens(conn)
    generated_at = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        catalog, token_doc = build(nodes, db.shown_nodes(conn), tokens, generated_at)
        # The service reloads when either file changes and refuses a mismatched pair; a request that lands
        # between the two renames gets one 503 and the next request loads the complete pair.
        _write(settings.subs_data_dir, "tokens.json", token_doc)
        _write(settings.subs_data_dir, "catalog.json", catalog)
    except (PublishError, OSError) as exc:
        detail = f"{reason}: {type(exc).__name__}: {exc}"
        conn.execute("INSERT INTO publish_log (at, ok, detail) VALUES (?, 0, ?)", (db.now(), detail))
        return False, detail
    detail = f"{reason}: {len(catalog['nodes'])} nodes, {len(catalog['users'])} users"
    if problems:
        detail += f", {len(problems)} registration file(s) skipped"
    conn.execute("INSERT INTO publish_log (at, ok, detail) VALUES (?, 1, ?)", (db.now(), detail))
    return True, detail
