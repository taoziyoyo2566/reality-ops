#!/usr/bin/env python3
"""Node agent user sync (plan-console-phase2 §3.3, §6.1 item 2): plans, guards and a full cycle against a fake
Xray API and a fake console.

Run: python3 -m unittest discover -s tests/edge -p 'test_agent.py'
All values are synthetic.
"""
import importlib.util
import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("edge_reporter", REPO / "docker/edge-tools/edge_reporter.py")
rep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rep)

U = {n: f"{i:08d}-1111-4111-8111-111111111111" for i, n in enumerate(["alice", "bob", "carol", "dave", "erin",
                                                                        "frank", "gina", "hank", "status-probe"])}


def user(name, uuid=None):
    return {"name": name, "uuid": uuid or U[name], "short_id": "a1a1a1a1"}


class FakeXray:
    """The inbounds of one Xray process, driven through the same `xray api` calls the agent makes."""

    def __init__(self, node, inbounds):
        self.node = node
        self.inbounds = {tag: dict(users) for tag, users in inbounds.items()}   # tag -> {email: account}
        self.calls = []
        self.fail = None

    def api(self, cfg, command, *args, stdin=None):
        self.calls.append((command, args, json.loads(stdin) if stdin else None))
        if self.fail == command:
            raise rep.SyncError(f"xray api {command} failed (rc=1)")
        if command == "inbounduser":
            tag = args[0].split("=", 1)[1]
            if tag not in self.inbounds:
                raise rep.SyncError("xray api inbounduser failed (rc=1)")
            return json.dumps({"users": [{"email": e, "account": {"id": a["id"], "flow": a.get("flow", "")}}
                                         for e, a in self.inbounds[tag].items()]})
        if command == "rmu":
            tag = args[0].split("=", 1)[1]
            for email in args[1:]:
                self.inbounds[tag].pop(email, None)
            return ""
        if command == "adu":
            for inbound in json.loads(stdin)["inbounds"]:
                for c in inbound["settings"]["clients"]:
                    self.inbounds[inbound["tag"]][c["email"]] = {"id": c["id"], "flow": c.get("flow", "")}
            return ""
        raise AssertionError(command)

    def names(self, tag):
        return sorted(e.split(".")[0] for e in self.inbounds[tag])


class FakeConsole:
    """/sync: answers with its list, or `unchanged` when the node says it runs that version."""

    def __init__(self, users):
        self.set(users)
        self.requests = []
        self.status = 200

    def set(self, users):
        self.users = users
        self.version = f"{abs(hash(json.dumps(users, sort_keys=True))) % 16 ** 16:016x}"

    def post(self, url, token, doc):
        self.requests.append(doc)
        if self.status != 200:
            return self.status, None
        if doc["applied"] == self.version:
            return 200, {"version": self.version, "unchanged": True}
        return 200, {"version": self.version, "users": self.users}


def emails(node, names, vision=True):
    return {f"{n}.{node}": {"id": U[n], "flow": "xtls-rprx-vision" if vision else ""} for n in names}


class PlanTest(unittest.TestCase):
    def test_plan_adds_removes_and_replaces_changed_uuids(self):
        desired = {"alice": user("alice"), "bob": user("bob", "99999999-1111-4111-8111-111111111111"), "carol": user("carol")}
        running = {"alice": U["alice"], "bob": U["bob"], "dave": U["dave"], "status-probe": U["status-probe"]}
        self.assertEqual(rep.sync_plan(desired, running, {"status-probe"}), (["bob", "dave"], ["bob", "carol"]))

    def test_guards(self):
        running = {n: U[n] for n in ("alice", "bob", "carol", "dave", "erin", "frank", "gina", "hank")}
        with self.assertRaisesRegex(rep.SyncError, "empty user list"):
            rep.check_plan({}, running, sorted(running), [], set())
        desired = {n: user(n) for n in ("alice", "bob", "carol")}
        remove, add = rep.sync_plan(desired, running, set())
        with self.assertRaisesRegex(rep.SyncError, "remove 5 of 8"):
            rep.check_plan(desired, running, remove, add, set())
        desired = {n: user(n) for n in ("alice", "bob", "carol", "dave", "erin")}
        remove, add = rep.sync_plan(desired, running, set())
        rep.check_plan(desired, running, remove, add, set())               # three removals: allowed
        rep.check_plan({}, {}, [], [], set())                              # nothing to run, nothing running
        small = {"alice": U["alice"], "bob": U["bob"]}
        rep.check_plan({"alice": user("alice")}, small, ["bob"], [], set())   # few users: removals allowed


class TickTest(unittest.TestCase):
    def setUp(self):
        self.cfg = rep.config_from_env({"REPORT_URL": "x", "REPORT_NODE": "n1", "SYNC_ENABLED": "true",
                                        "SYNC_URL": "http://console/sync", "SYNC_KEEP": "status-probe",
                                        "XHTTP_ENABLED": "true", "LISTEN_PORT": "443"})
        self.xray = FakeXray("n1", {
            rep.REALITY_TAG: {**emails("n1", ["alice", "bob", "status-probe"]), "zed.other": {"id": "x"}},
            rep.XHTTP_TAG: emails("n1", ["alice", "bob", "status-probe"], vision=False)})
        self.console = FakeConsole([user("alice"), user("carol")])
        self._api, self._post = rep.xray_api, rep.post_json
        rep.xray_api, rep.post_json = self.xray.api, self.console.post
        self.sync = rep.Sync(self.cfg)

    def tearDown(self):
        rep.xray_api, rep.post_json = self._api, self._post

    def test_a_cycle_applies_the_list_and_reports_it(self):
        self.assertTrue(self.sync.tick("tok"))
        for tag in (rep.REALITY_TAG, rep.XHTTP_TAG):
            self.assertEqual(self.xray.names(tag)[:3], ["alice", "carol", "status-probe"], tag)
        self.assertIn("zed.other", self.xray.inbounds[rep.REALITY_TAG])                # other nodes' emails untouched
        vision = self.xray.inbounds[rep.REALITY_TAG]["carol.n1"]
        self.assertEqual((vision["id"], vision["flow"]), (U["carol"], "xtls-rprx-vision"))
        self.assertEqual(self.xray.inbounds[rep.XHTTP_TAG]["carol.n1"]["flow"], "")
        adds = [c for c in self.xray.calls if c[0] == "adu"]
        self.assertEqual(adds[0][2]["inbounds"][0]["port"], 443)
        self.assertEqual(adds[1][2]["inbounds"][0]["listen"], "@xray-edge-xhttp")
        first, second = self.console.requests
        self.assertEqual((first["applied"], first["running"]), (None, ["alice", "bob"]))
        self.assertEqual((second["applied"], second["running"], second["error"]), (self.console.version, ["alice", "carol"], ""))
        # nothing changed: one request, no API writes
        calls = len(self.xray.calls)
        self.assertTrue(self.sync.tick("tok"))
        self.assertEqual(len(self.console.requests), 3)
        self.assertFalse([c for c in self.xray.calls[calls:] if c[0] in ("adu", "rmu")])

    def test_an_xray_restart_is_repaired_from_the_held_list(self):
        self.sync.tick("tok")
        self.xray.inbounds[rep.REALITY_TAG] = emails("n1", ["alice", "bob", "status-probe"])   # back to conf.d
        self.sync.tick("tok")
        self.assertEqual(self.xray.names(rep.REALITY_TAG), ["alice", "carol", "status-probe"])
        self.assertEqual(self.console.requests[-1]["applied"], self.console.version)

    def test_refused_and_failed_syncs_are_reported(self):
        self.console.set([])
        self.assertTrue(self.sync.tick("tok"))
        self.assertEqual(self.xray.names(rep.REALITY_TAG)[:2], ["alice", "bob"])          # nothing removed
        self.sync.tick("tok")
        self.assertIn("empty user list", self.console.requests[-1]["error"])
        self.assertIsNone(self.console.requests[-1]["applied"])
        self.console.status = 503
        self.assertTrue(self.sync.tick("tok"))
        self.assertIn("HTTP 503", self.sync.error)
        self.xray.fail = "inbounduser"
        self.assertFalse(self.sync.tick("tok"))                                     # Xray unreachable: counts as a failure

    def test_without_xhttp_only_the_reality_inbound_is_used(self):
        cfg = dict(self.cfg, xhttp=False)
        self.xray.inbounds.pop(rep.XHTTP_TAG)
        self.assertTrue(rep.Sync(cfg).tick("tok"))
        self.assertEqual(self.xray.names(rep.REALITY_TAG)[:3], ["alice", "carol", "status-probe"])


if __name__ == "__main__":
    unittest.main()
