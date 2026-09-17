"""Node registration files written by edge.yml (plan-console-phase1 §3.3).

One file per node, <registry>/<node>.json. A file that fails validation is left out and reported, never guessed at.
"""
import json
import os
import re
import threading
from dataclasses import dataclass, field

from subs import links

SCHEMA = 1
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{1,200}$")


@dataclass(frozen=True)
class Node:
    name: str
    label: str
    endpoint: str
    port: int
    sni: str
    public_key: str
    xhttp: dict
    users: dict            # user name -> {"uuid", "short_id"}
    report_enabled: bool
    report_digest: str
    image: str
    status_probe: bool = False
    mtime: float = field(compare=False, default=0.0)

    def edge(self):
        """The `edge` object of a subscription catalog node."""
        return {"endpoint": self.endpoint, "port": self.port, "sni": self.sni,
                "public_key": self.public_key, "xhttp": dict(self.xhttp)}


class RegistryError(ValueError):
    pass


def _require(cond, msg):
    if not cond:
        raise RegistryError(msg)


def parse(name, doc, mtime=0.0):
    _require(isinstance(doc, dict) and doc.get("schema") == SCHEMA, "unsupported schema")
    _require(doc.get("node") == name and NAME_RE.match(name), "node name does not match the file name")
    _require(isinstance(doc.get("label"), str) and doc["label"], "missing label")
    _require(isinstance(doc.get("endpoint"), str) and links.HOST_RE.match(doc["endpoint"]), "invalid endpoint")
    _require(isinstance(doc.get("port"), int) and 1 <= doc["port"] <= 65535, "invalid port")
    _require(isinstance(doc.get("sni"), str) and doc["sni"], "missing sni")
    _require(isinstance(doc.get("public_key"), str) and links.KEY_RE.match(doc["public_key"]), "invalid public key")
    xhttp = doc.get("xhttp") or {}
    _require(isinstance(xhttp.get("enabled"), bool), "xhttp.enabled must be a boolean")
    if xhttp["enabled"]:
        _require(isinstance(xhttp.get("path"), str) and PATH_RE.match(xhttp["path"]), "invalid xhttp path")
    users = {}
    for user in doc.get("users") or []:
        uname = user.get("name") if isinstance(user, dict) else None
        _require(isinstance(uname, str) and NAME_RE.match(uname), f"invalid user name {uname!r}")
        _require(uname not in users, f"duplicate user {uname}")
        _require(isinstance(user.get("uuid"), str) and links.UUID_RE.match(user["uuid"]), f"user {uname}: invalid uuid")
        _require(isinstance(user.get("short_id"), str) and links.SHORT_ID_RE.match(user["short_id"]),
                 f"user {uname}: invalid short id")
        users[uname] = {"uuid": user["uuid"], "short_id": user["short_id"]}
    report = doc.get("report") or {}
    _require(isinstance(report.get("enabled"), bool), "report.enabled must be a boolean")
    digest = report.get("token_sha256") or ""
    _require(digest == "" or DIGEST_RE.match(digest), "invalid report token digest")
    _require(not report["enabled"] or digest, "report enabled without a token digest")
    _require(isinstance(doc.get("status_probe", False), bool), "status_probe must be a boolean")
    return Node(name=name, label=doc["label"], endpoint=doc["endpoint"], port=doc["port"], sni=doc["sni"],
                public_key=doc["public_key"],
                xhttp={"enabled": xhttp["enabled"], "path": xhttp.get("path", ""), "mode": xhttp.get("mode", "auto")},
                users=users, report_enabled=report["enabled"], report_digest=digest,
                image=str(doc.get("image", "")), status_probe=doc.get("status_probe", False), mtime=mtime)


def _stamp(directory):
    try:
        entries = sorted((e for e in os.scandir(directory) if e.name.endswith(".json") and e.is_file()),
                         key=lambda e: e.name)
    except OSError:
        return ()
    return tuple((e.name, e.inode(), e.stat().st_size, e.stat().st_mtime_ns) for e in entries)


class Registry:
    """Registration files, reloaded when any of them changes."""

    def __init__(self, directory):
        self.directory = directory
        self._lock = threading.Lock()
        self._stamp = None
        self.nodes = {}
        self.problems = []

    def current(self):
        stamp = _stamp(self.directory)
        with self._lock:
            if stamp != self._stamp:
                nodes, problems = {}, []
                for entry_name, *_ in stamp:
                    name = entry_name[:-5]
                    path = os.path.join(self.directory, entry_name)
                    try:
                        with open(path) as fh:
                            doc = json.load(fh)
                        nodes[name] = parse(name, doc, os.path.getmtime(path))
                    except (OSError, ValueError) as exc:
                        problems.append(f"{entry_name}: {exc}")
                self._stamp, self.nodes, self.problems = stamp, nodes, problems
            return self.nodes, self.problems, self._stamp
