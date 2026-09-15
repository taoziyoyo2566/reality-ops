"""Build catalog.json and tokens.json on the control host (plan-subscription-service §3.2, §3.3, §5 item 1).

Input, from subs.yml:
  --collected  JSON {"generated_at": str, "nodes": {node: {"label", "state", "edge", "users": [...]}}}
               where users are the node's ACL result ({"name", "uuid", "short_id"}).
  --tokens     JSON {user: token} in clear text (from vault, written 0600 by Ansible and removed after).
  --enabled    users published in this catalog; each needs a token.
  --legacy-dir the old system's /opt/reality/users (<user>_<node>.json), read only.
Output: catalog.json and tokens.json (digests only) in --out-dir, and a JSON report without secrets on stdout.
Exit 1 with the report when validation fails or legacy files disagree with the ACL.
"""
import argparse
import json
import os
import sys

from . import catalog as cat
from . import links


def legacy_files(legacy_dir):
    """{(user, node): [links]} from the old system's per-user files."""
    found = {}
    for filename in sorted(os.listdir(legacy_dir)):
        if not filename.endswith(".json") or "_" not in filename:
            continue
        user, node = filename[:-5].rsplit("_", 1)
        with open(os.path.join(legacy_dir, filename)) as fh:
            entries = json.load(fh)
        found[(user, node)] = [e["subscription"] for e in entries if isinstance(e, dict) and e.get("subscription")]
    return found


def build(collected, tokens, enabled, legacy):
    nodes, users, problems = {}, {name: {"nodes": {}} for name in enabled}, []
    acl_pairs = set()
    for node_name, node in sorted(collected["nodes"].items()):
        nodes[node_name] = {"label": node["label"], "state": node["state"]}
        if node.get("edge"):
            nodes[node_name]["edge"] = node["edge"]
        for member in node["users"]:
            if member["name"] not in users:
                continue
            acl_pairs.add((member["name"], node_name))
            if node["state"] == "migrated":
                users[member["name"]]["nodes"][node_name] = {"uuid": member["uuid"], "short_id": member["short_id"]}
            elif (member["name"], node_name) in legacy:
                users[member["name"]]["nodes"][node_name] = {"legacy_links": legacy[(member["name"], node_name)]}

    # §5 item 1: legacy files and the ACL must agree for every published user on legacy nodes.
    for user in enabled:
        for node_name, node in nodes.items():
            if node["state"] != "legacy":
                continue
            in_acl = (user, node_name) in acl_pairs
            has_file = (user, node_name) in legacy
            if in_acl and not has_file:
                problems.append(f"{user} on {node_name}: allowed by ACL but no legacy file")
            if has_file and not in_acl:
                problems.append(f"{user} on {node_name}: legacy file but not allowed by ACL")
        for (file_user, file_node) in legacy:
            if file_user == user and file_node not in nodes:
                problems.append(f"{user}: legacy file for unknown node {file_node}")

    catalog = {"schema": cat.SCHEMA, "generated_at": collected["generated_at"], "nodes": nodes, "users": users}
    missing = sorted(set(enabled) - set(tokens))
    if missing:
        raise cat.CatalogError(f"enabled users without a token: {missing}")
    for user in enabled:
        if not isinstance(tokens[user], str) or not cat.TOKEN_RE.match(tokens[user]):
            raise cat.CatalogError(f"token for {user} must be 43 URL-safe base64 characters")
    table = {cat.token_digest(tokens[user]): user for user in enabled}
    if len(table) != len(enabled):
        raise cat.CatalogError("two users share a token")
    token_doc = {"schema": cat.SCHEMA, "tokens": table}
    cat.validate_catalog(catalog)
    cat.validate_tokens(token_doc, catalog)
    return catalog, token_doc, problems


def _write(path, doc):
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--collected", required=True)
    parser.add_argument("--tokens", required=True)
    parser.add_argument("--enabled", required=True, help="comma-separated user names")
    parser.add_argument("--legacy-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    enabled = sorted({u for u in args.enabled.split(",") if u})
    with open(args.collected) as fh:
        collected = json.load(fh)
    with open(args.tokens) as fh:
        tokens = json.load(fh)
    report = {"ok": False, "users": len(enabled)}
    try:
        catalog, token_doc, problems = build(collected, tokens, enabled, legacy_files(args.legacy_dir))
        report.update({"nodes": len(catalog["nodes"]), "problems": problems,
                       "migrated": sorted(n for n, v in catalog["nodes"].items() if v["state"] == "migrated"),
                       "links": sum(len(v["nodes"]) for v in catalog["users"].values())})
        if problems:
            print(json.dumps(report, ensure_ascii=False))
            return 1
        _write(os.path.join(args.out_dir, "catalog.json"), catalog)
        _write(os.path.join(args.out_dir, "tokens.json"), token_doc)
        report["ok"] = True
    except (cat.CatalogError, links.LinkError, KeyError, ValueError) as exc:
        report["error"] = str(exc)
        print(json.dumps(report, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
