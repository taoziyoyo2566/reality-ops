"""Proxy egress on the node (plan-egress-console §3.3-3.4), run by the node agent (edge_reporter.py).

The console sends this node's egress outbounds and assignments with each sync. Every sync the agent checks each egress
through a temporary Xray (egress_probe.py, shared with the console), then applies the assignments at runtime: an
assignment runs through its egress while the egress works; when it fails, the assignment falls back to direct (its
rule is left out) or blocks (its rule points at `blocked`), and it is used again after RECOVER good checks in a row.
An assignment arrives as a list of rules, each sent through the egress or blocked (UDP to websites whose TCP goes
through the egress, so browsers fall back from QUIC to TCP). An assignment whose rules send UDP through the egress
needs the egress to relay UDP as well; one that sends only TCP ignores UDP.
Only outbounds and rules tagged `egress-` are touched; nothing is written to the configuration files.

The agent passes in how to call Xray's API, the error that call raises and its log, so this module knows nothing
else about the agent.
"""
import json

import egress_probe

PREFIX = "egress-"
SCHEMA = 2                           # what this agent runs: rule lists, blocks for a disabled egress (console AGENT_SCHEMA)
RECOVER = 2                          # good checks in a row before a failed egress is used again
DEFAULT_RULE = {"type": "field", "ruleTag": "default", "network": "tcp,udp", "outboundTag": "direct"}
USER_AGENT = "reality-edge-egress/1"


def rule_tag(outbound, assignment_id, index):
    """The same tags as the console's deploy_rules, so the configuration and the runtime agree."""
    base = f"{outbound}-a{assignment_id}"
    return base if index == 0 else f"{base}-{index + 1}"


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
        self.health = {}          # outbound tag -> {"tcp" | "udp": {"ok": bool, "streak": successes since the last failure}}
        self.checks = {}          # outbound tag -> last check result
        self.states = {}          # assignment id -> active | direct | blocked
        self.applied = None       # signature of what runs now
        self.outbound_state = {}  # outbound tag -> the settings this agent added it with
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
                "egress_states": {str(k): v for k, v in self.states.items()}, "egress_checks": checks,
                "egress_schema": SCHEMA, "egress_error": self.error}

    def check(self):
        outbounds = (self.payload or {}).get("outbounds") or []
        if not outbounds or not self.url:
            return
        for tag, result in egress_probe.probe(self.xray_bin, outbounds, self.url, USER_AGENT).items():
            self.checks[tag] = result
            health = self.health.setdefault(tag, {"tcp": {"ok": True, "streak": RECOVER},
                                                  "udp": {"ok": True, "streak": RECOVER}})
            for kind, ok in (("tcp", result["ok"]), ("udp", result.get("udp"))):
                if ok is None:                        # UDP is not tried while TCP fails
                    continue
                state = health[kind]
                if not ok:
                    state.update(ok=False, streak=0)
                else:
                    state["streak"] += 1
                    state["ok"] = state["ok"] or state["streak"] >= RECOVER

    def usable(self, outbound, needs_udp):
        health = self.health.get(outbound)
        if health is None:
            return True
        return health["tcp"]["ok"] and (not needs_udp or health["udp"]["ok"])

    def desired(self):
        """(outbounds, rules, states, fallback) for the current health; `fallback` is what runs if the outbounds
        cannot be placed: the "block" assignments, blocked, so they never go direct."""
        payload = self.payload or {"outbounds": [], "assignments": []}
        rules, states, fallback = [], {}, []
        for a in payload.get("assignments", []):
            own = a.get("rules") or [dict(a["rule"], action="egress")]      # a console from before rule lists
            needs_udp = any(r.get("action") == "egress" and "udp" in r.get("network", "tcp").split(",") for r in own)
            if not a.get("egress_off") and self.usable(a["outbound"], needs_udp):
                states[a["id"]] = "active"
            elif a["on_failure"] == "block":
                states[a["id"]] = "blocked"
            else:
                states[a["id"]] = "direct"
                continue
            for i, r in enumerate(own):
                through = states[a["id"]] == "active" and r.get("action") == "egress"
                rule = dict({k: v for k, v in r.items() if k != "action"}, type="field",
                            ruleTag=rule_tag(a["outbound"], a["id"], i), outboundTag=a["outbound"] if through else "blocked")
                rules.append(rule)
                if a["on_failure"] == "block":
                    fallback.append(dict(rule, outboundTag="blocked"))
        return payload.get("outbounds", []), rules, states, fallback

    def apply(self):
        """Make the runtime match desired(); returns True when something was changed."""
        if self.payload is None:
            return False
        outbounds, rules, states, fallback = self.desired()
        signature = json.dumps([outbounds, rules], sort_keys=True)
        try:
            present_rules = [r.get("ruleTag", "") for r in json.loads(self.api("lsrules") or "{}").get("rules", [])]
            ours = [t for t in present_rules if t.startswith(PREFIX)]
            if signature == self.applied and ours == [r["ruleTag"] for r in rules]:
                self.states, self.error = states, ""
                return False
            present = {o.get("tag", "") for o in json.loads(self.api("lso") or "{}").get("outbounds", [])}
            wanted = {o["tag"]: json.dumps(o, sort_keys=True) for o in outbounds}
            # outbounds this agent has not added with these settings (all of them after a restart) are replaced
            changed = [t for t in wanted if t not in present or self.outbound_state.get(t) != wanted[t]]
            stale = sorted(t for t in present if t.startswith(PREFIX) and (t not in wanted or t in changed))
            self.api("rmrules", *ours, "default")
            placing, failure = rules, None
            try:
                try:
                    if stale:
                        self.api("rmo", *stale)
                        for t in stale:
                            self.outbound_state.pop(t, None)
                    if changed:
                        self.api("ado", "stdin:", stdin=json.dumps({"outbounds": [o for o in outbounds if o["tag"] in changed]}))
                        self.outbound_state.update({t: wanted[t] for t in changed})
                except self.api_error as exc:
                    placing, failure = fallback, exc     # without their outbounds: "block" assignments stay blocked
                if placing:
                    try:
                        self.api("adrules", "-append", "stdin:", stdin=json.dumps({"routing": {"rules": placing}}))
                    except self.api_error as exc:
                        failure = failure or exc
                        if placing is not fallback and fallback:
                            self.api("adrules", "-append", "stdin:", stdin=json.dumps({"routing": {"rules": fallback}}))
            finally:
                # the default rule always goes back, last
                self.api("adrules", "-append", "stdin:", stdin=json.dumps({"routing": {"rules": [DEFAULT_RULE]}}))
            if failure is not None:
                raise failure
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
