"""Proxy egress on the node (plan-egress-console §3.3-3.4), run by the node agent (edge_reporter.py).

The console sends this node's egress outbounds and assignments with each sync. Every sync the agent checks each egress
through a temporary Xray (egress_probe.py, shared with the console), then applies the assignments at runtime: an
assignment runs through its egress while the egress works; when it fails, the assignment falls back to direct (its
rule is left out) or blocks (its rule points at `blocked`), and it is used again after RECOVER good checks in a row.
Only outbounds and rules tagged `egress-` are touched; nothing is written to the configuration files.

The agent passes in how to call Xray's API, the error that call raises and its log, so this module knows nothing
else about the agent.
"""
import json

import egress_probe

PREFIX = "egress-"
RECOVER = 2                          # good checks in a row before a failed egress is used again
DEFAULT_RULE = {"type": "field", "ruleTag": "default", "network": "tcp,udp", "outboundTag": "direct"}
USER_AGENT = "reality-edge-egress/1"


class Egress:
    """Keeps the console's egress payload, checks it, and applies it at runtime (memory only)."""

    def __init__(self, xray_bin, api, error, log):
        self.xray_bin = xray_bin
        self.api = api                # (command, *args, stdin=None) -> output; raises `error`
        self.api_error = error
        self.log = log
        self.payload = None
        self.version = None
        self.url = ""
        self.health = {}          # outbound tag -> {"ok": bool, "streak": successes since the last failure}
        self.checks = {}          # outbound tag -> last check result
        self.states = {}          # assignment id -> active | direct | blocked
        self.applied = None       # signature of what runs now
        self.error = ""

    def update(self, doc):
        """Take the egress part of a /sync answer."""
        if isinstance(doc.get("egress_check_url"), str):
            self.url = doc["egress_check_url"]
        payload = doc.get("egress")
        if isinstance(payload, dict) and isinstance(doc.get("egress_version"), str):
            self.payload, self.version = payload, doc["egress_version"]

    def report(self):
        checks = {tag[len(PREFIX):]: check for tag, check in self.checks.items() if tag[len(PREFIX):].isdigit()}
        return {"egress_applied": self.version if self.applied is not None and not self.error else None,
                "egress_states": {str(k): v for k, v in self.states.items()}, "egress_checks": checks}

    def check(self):
        outbounds = (self.payload or {}).get("outbounds") or []
        if not outbounds or not self.url:
            return
        for tag, result in egress_probe.probe(self.xray_bin, outbounds, self.url, USER_AGENT).items():
            self.checks[tag] = result
            state = self.health.setdefault(tag, {"ok": True, "streak": RECOVER})
            if not result["ok"]:
                state.update(ok=False, streak=0)
            else:
                state["streak"] += 1
                state["ok"] = state["ok"] or state["streak"] >= RECOVER

    def desired(self):
        """(outbounds, rules, states) for the current health."""
        payload = self.payload or {"outbounds": [], "assignments": []}
        rules, states = [], {}
        for a in payload.get("assignments", []):
            rule = dict(a["rule"], type="field", ruleTag=f"{a['outbound']}-a{a['id']}")
            if self.health.get(a["outbound"], {"ok": True})["ok"]:
                rules.append(dict(rule, outboundTag=a["outbound"]))
                states[a["id"]] = "active"
            elif a["on_failure"] == "block":
                rules.append(dict(rule, outboundTag="blocked"))
                states[a["id"]] = "blocked"
            else:
                states[a["id"]] = "direct"
        return payload.get("outbounds", []), rules, states

    def apply(self):
        """Make the runtime match desired(); returns True when something was changed."""
        if self.payload is None:
            return False
        outbounds, rules, states = self.desired()
        signature = json.dumps([outbounds, rules], sort_keys=True)
        try:
            present_rules = [r.get("ruleTag", "") for r in json.loads(self.api("lsrules") or "{}").get("rules", [])]
            ours = [t for t in present_rules if t.startswith(PREFIX)]
            if signature == self.applied and ours == [r["ruleTag"] for r in rules]:
                self.states, self.error = states, ""
                return False
            present_outbounds = [o.get("tag", "") for o in json.loads(self.api("lso") or "{}").get("outbounds", [])]
            self.api("rmrules", *ours, "default")
            placed = False
            try:
                stale = [t for t in present_outbounds if t.startswith(PREFIX)]
                if stale:
                    self.api("rmo", *stale)
                if outbounds:
                    self.api("ado", "stdin:", stdin=json.dumps({"outbounds": outbounds}))
                placed = True
            finally:
                # the default rule always goes back; without their outbounds the assignments stay out (direct)
                self.api("adrules", "-append", "stdin:",
                         stdin=json.dumps({"routing": {"rules": (rules if placed else []) + [DEFAULT_RULE]}}))
            now = [r.get("ruleTag", "") for r in json.loads(self.api("lsrules") or "{}").get("rules", [])]
            if [t for t in now if t.startswith(PREFIX)] != [r["ruleTag"] for r in rules] or now[-1:] != ["default"]:
                raise self.api_error("egress rules did not apply in order")
        except (self.api_error, ValueError, IndexError) as exc:
            self.applied, self.error = None, str(exc)[:200]
            self.log(f"egress: {self.error}")
            return False
        self.applied, self.states, self.error = signature, states, ""
        self.log(f"egress: {len(outbounds)} outbound(s), {len(rules)} rule(s) (list {self.version})")
        return True
