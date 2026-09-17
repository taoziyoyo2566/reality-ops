#!/usr/bin/env python3
"""Node status page (plan-node-status-page §6.1 item 1): event logic, rounds, the published document, the page.

Run with the console's dependencies installed, like tests/console/test_console.py:
  python tests/console/test_status.py
All hosts, keys and UUIDs are synthetic; no network access except to local test servers.
"""
import dataclasses
import http.server
import json
import os
import pathlib
import socket
import stat as statmod
import sys
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from console import config, db, registry as reg, status, web_app  # noqa: E402
from subs import statuspage  # noqa: E402
from test_console import HOST, PBK_A, PBK_B, UUIDS, Env, Server  # noqa: E402

PROBE = {"uuid": "44444444-4444-4444-8444-444444444444", "short_id": "d4d4d4d4"}
# 2026-01-02 00:00 in UTC+8 is 2026-01-01 16:00 UTC
T0 = 1767283200


def status_settings(**overrides):
    st = config.status_from_env({"STATUS_ENABLED": "true", "STATUS_SHOW_DAYS": "5"})
    return dataclasses.replace(st, **overrides)


class StatusEnv(Env):
    def __init__(self):
        super().__init__()
        self.register("alpha", ["alice", "test"], PBK_A, xhttp=True, status_probe=True)
        self.register("beta", ["alice", "bob"], PBK_B, status_probe=True)
        with self.conn() as conn:
            db.set_shown(conn, "alpha", True)
            db.set_shown(conn, "beta", True)
            conn.execute("UPDATE node_display SET changed_at = ?", (T0 - 86400,))
        self.st = status_settings()
        self.registry = reg.Registry(self.dirs["registry"])

    def register(self, node, users, pbk, status_probe=False, **kw):
        super().register(node, users, pbk, **kw)
        path = os.path.join(self.dirs["registry"], f"{node}.json")
        with open(path) as fh:
            doc = json.load(fh)
        doc["status_probe"] = status_probe
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def nodes(self):
        return self.registry.current()[0]

    def round(self, at, **results):
        """results: alpha_vision=True/False/None ...; missing targets succeed."""
        nodes = self.nodes()
        values = {}
        for name, transport in status.targets(nodes):
            ok = results.get(f"{name}_{transport}", True)
            values[(name, transport)] = (ok, 50 if ok else None, "" if ok else ("local network" if ok is None else "timeout"))
        with self.conn() as conn:
            with db.transaction(conn):
                status.record_round(conn, self.st, nodes, values, at)

    def incidents(self):
        with self.conn() as conn:
            return [(r["node"], r["kind"], r["started_at"], r["ended_at"], r["transports"])
                    for r in conn.execute("SELECT * FROM incident ORDER BY id")]

    def document(self, now):
        with self.conn() as conn:
            return status.build_document(conn, self.st, self.nodes(), now)


class AdvanceTest(unittest.TestCase):
    def run_seq(self, seq, fails=3, recovers=2):
        state, events = status.new_state(), []
        for i, ok in enumerate(seq):
            state, down, up = status.advance(state, ok, 100 + 60 * i, fails, recovers)
            if down is not None:
                events.append(("down", down))
            if up is not None:
                events.append(("up", up))
        return state, events

    def test_two_failures_do_not_open_an_event(self):
        self.assertEqual(self.run_seq([False, False, True, False, False, True])[1], [])

    def test_third_failure_opens_at_the_first_one_and_two_successes_close_at_the_first(self):
        state, events = self.run_seq([True, False, False, False, False, True, False, True, True])
        self.assertEqual(events, [("down", 160), ("up", 520)])
        self.assertIsNone(state["down_since"])

    def test_unknown_results_change_nothing(self):
        self.assertEqual(self.run_seq([False, None, False, None, False])[1], [("down", 100)])
        state, events = self.run_seq([None, None])
        self.assertEqual((state, events), (status.new_state(), []))


class TargetsAndClientTest(unittest.TestCase):
    def setUp(self):
        self.env = StatusEnv()

    def tearDown(self):
        self.env.close()

    def test_only_probed_nodes_and_their_transports(self):
        self.env.register("gamma", ["bob"], PBK_B)  # not carrying the probe account
        nodes = self.env.nodes()
        self.assertEqual(status.targets(nodes), [("alpha", "vision"), ("alpha", "xhttp"), ("beta", "vision")])
        cfg = status.client_config(nodes, PROBE, 20000)
        self.assertEqual([i["port"] for i in cfg["inbounds"]], [20000, 20001, 20002])
        self.assertTrue(all(i["listen"] == "127.0.0.1" and i["protocol"] == "http" for i in cfg["inbounds"]))
        self.assertEqual([o["protocol"] for o in cfg["outbounds"]], ["blackhole", "vless", "vless", "vless"])
        vision, xhttp = cfg["outbounds"][1], cfg["outbounds"][2]
        self.assertEqual(vision["settings"]["vnext"][0]["users"][0],
                         {"id": PROBE["uuid"], "encryption": "none", "flow": "xtls-rprx-vision"})
        self.assertEqual(vision["streamSettings"]["realitySettings"]["shortId"], PROBE["short_id"])
        self.assertEqual(xhttp["streamSettings"]["xhttpSettings"], {"path": "/xp/synthetic", "mode": "auto"})
        self.assertEqual(xhttp["settings"]["vnext"][0]["users"][0]["flow"], "")
        self.assertEqual([r["outboundTag"] for r in cfg["routing"]["rules"]], ["t0", "t1", "t2"])
        self.assertNotIn(UUIDS["alice"], json.dumps(cfg))

    def test_registration_flag_is_a_boolean(self):
        doc = json.load(open(os.path.join(self.env.dirs["registry"], "alpha.json")))
        doc["status_probe"] = "yes"
        with self.assertRaises(reg.RegistryError):
            reg.parse("alpha", doc)
        del doc["status_probe"]
        self.assertFalse(reg.parse("alpha", doc).status_probe)


class RoundTest(unittest.TestCase):
    def setUp(self):
        self.env = StatusEnv()

    def tearDown(self):
        self.env.close()

    def test_partial_then_outage_then_recovery(self):
        env = self.env
        env.round(T0)
        env.round(T0 + 60, alpha_xhttp=False)
        env.round(T0 + 120, alpha_xhttp=False)
        self.assertEqual(env.incidents(), [])
        env.round(T0 + 180, alpha_xhttp=False, alpha_vision=False)
        self.assertEqual(env.incidents(), [("alpha", "partial", T0 + 60, None, "xhttp")])
        env.round(T0 + 240, alpha_xhttp=False, alpha_vision=False)
        env.round(T0 + 300, alpha_xhttp=False, alpha_vision=False)
        self.assertEqual(env.incidents(), [("alpha", "partial", T0 + 60, T0 + 180, "xhttp"),
                                           ("alpha", "outage", T0 + 180, None, "vision,xhttp")])
        env.round(T0 + 360, alpha_xhttp=False)
        env.round(T0 + 420, alpha_xhttp=False)
        env.round(T0 + 480)
        env.round(T0 + 540)
        self.assertEqual(env.incidents(), [("alpha", "partial", T0 + 60, T0 + 180, "xhttp"),
                                           ("alpha", "outage", T0 + 180, T0 + 360, "vision,xhttp"),
                                           ("alpha", "partial", T0 + 360, T0 + 480, "xhttp")])

    def test_unknown_rounds_neither_fail_nor_count(self):
        env = self.env
        env.round(T0, beta_vision=False)
        for i in range(1, 6):
            env.round(T0 + 60 * i, beta_vision=None, alpha_vision=None, alpha_xhttp=None)
        env.round(T0 + 360, beta_vision=False)
        self.assertEqual(env.incidents(), [])
        with env.conn() as conn:
            daily = dict(conn.execute("SELECT day, known_seconds || '/' || unknown_seconds FROM status_daily "
                                      "WHERE node = 'beta'").fetchall())
            self.assertEqual(daily, {"2026-01-02": "120/300"})
            self.assertEqual(status.last_round(conn), T0 + 360)

    def test_maintenance_closes_an_outage_and_suppresses_failures(self):
        env = self.env
        for i in range(3):
            env.round(T0 + 60 * i, beta_vision=False)
        with env.conn() as conn:
            db.set_shown(conn, "beta", False)
            conn.execute("UPDATE node_display SET changed_at = ? WHERE node = 'beta'", (T0 + 150,))
        for i in range(3, 8):
            env.round(T0 + 60 * i, beta_vision=False)
        self.assertEqual(env.incidents(), [("beta", "outage", T0, T0 + 150, "vision"),
                                           ("beta", "maintenance", T0 + 150, None, "")])
        with env.conn() as conn:
            db.set_shown(conn, "beta", True)
            conn.execute("UPDATE node_display SET changed_at = ? WHERE node = 'beta'", (T0 + 500,))
        env.round(T0 + 540, beta_vision=False)
        env.round(T0 + 600, beta_vision=False)
        self.assertEqual(env.incidents()[1:], [("beta", "maintenance", T0 + 150, T0 + 500, "")])
        env.round(T0 + 660, beta_vision=False)
        self.assertEqual(env.incidents()[2:], [("beta", "outage", T0 + 540, None, "vision")])

    def test_node_hidden_before_the_first_round_is_in_maintenance_from_then(self):
        with self.env.conn() as conn:
            db.set_shown(conn, "beta", False)
            conn.execute("UPDATE node_display SET changed_at = ? WHERE node = 'beta'", (T0 - 30 * 86400,))
        self.env.round(T0)
        self.assertEqual(self.env.incidents(), [("beta", "maintenance", T0, None, "")])

    def test_leaving_the_probe_set_closes_open_events(self):
        env = self.env
        for i in range(3):
            env.round(T0 + 60 * i, beta_vision=False)
        env.register("beta", ["alice", "bob"], PBK_B, status_probe=False)
        os.utime(os.path.join(env.dirs["registry"], "beta.json"), (T0, T0 + 1))
        env.round(T0 + 180)
        self.assertEqual(env.incidents(), [("beta", "outage", T0, T0 + 180, "vision")])

    def test_recovery_and_failure_found_in_the_same_round_are_replayed_in_order(self):
        env = self.env
        vision = [False, False, False, False, False, True, True]
        xhttp = [True, True, True, True, False, False, False]
        for i, (v, x) in enumerate(zip(vision, xhttp)):
            env.round(T0 + 60 * i, alpha_vision=v, alpha_xhttp=x)
        self.assertEqual(env.incidents(), [("alpha", "partial", T0, T0 + 240, "vision"),
                                           ("alpha", "outage", T0 + 240, T0 + 300, "vision,xhttp"),
                                           ("alpha", "partial", T0 + 300, None, "xhttp")])

    def test_a_data_gap_ends_open_events_and_resets_counts(self):
        env = self.env
        for i in range(3):
            env.round(T0 + 60 * i, beta_vision=False)
        for i in range(3, 13):   # ten rounds without data
            env.round(T0 + 60 * i, beta_vision=None, alpha_vision=None, alpha_xhttp=None)
        self.assertEqual(env.incidents(), [("beta", "outage", T0, T0 + 180, "vision")])
        doc = env.document(T0 + 60 * 12)
        beta = next(n for n in doc["nodes"] if n["label"] == "beta [tag]")
        self.assertEqual(beta["days"][-1], ["outage", 3, 0, 0])
        self.assertEqual(beta["availability"], 0.0)       # three known minutes, all failed
        # two failures, the service is down for six hours, then one failure: no event
        env.round(T0 + 60 * 13, beta_vision=False)
        env.round(T0 + 60 * 14, beta_vision=False)
        env.round(T0 + 60 * 14 + 6 * 3600, beta_vision=False)
        self.assertEqual(len(env.incidents()), 1)

    def test_an_open_event_is_not_counted_past_the_last_round(self):
        env = self.env
        for i in range(4):
            env.round(T0 + 60 * i, beta_vision=False)
        doc = env.document(T0 + 180 + 5 * 3600)          # the service stopped after the last round
        beta = next(n for n in doc["nodes"] if n["label"] == "beta [tag]")
        self.assertEqual((beta["state"], beta["days"][-1]), ("nodata", ["outage", 4, 0, 0]))

    def test_a_transport_leaving_while_down_does_not_overlap(self):
        env = self.env
        for i in range(3):
            env.round(T0 + 60 * i, alpha_vision=False, alpha_xhttp=False)
        env.register("alpha", ["alice", "test"], PBK_A, xhttp=False, status_probe=True)
        env.round(T0 + 180, alpha_vision=False)
        self.assertEqual(env.incidents(), [("alpha", "outage", T0, T0 + 180, "vision,xhttp"),
                                           ("alpha", "outage", T0 + 180, None, "vision")])
        with env.conn() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT transport FROM probe_state WHERE node = 'alpha'")],
                             ["vision"])

    def test_a_node_that_rejoins_starts_fresh(self):
        env = self.env
        for i in range(3):
            env.round(T0 + 60 * i, beta_vision=False)
        env.register("beta", ["alice", "bob"], PBK_B, status_probe=False)
        env.round(T0 + 180)
        env.register("beta", ["alice", "bob"], PBK_B, status_probe=True, xhttp=False)
        os.utime(os.path.join(env.dirs["registry"], "beta.json"), (T0, T0 + 2))
        env.round(T0 + 10 * 86400)
        env.round(T0 + 10 * 86400 + 60)
        self.assertEqual(env.incidents(), [("beta", "outage", T0, T0 + 180, "vision")])

    def test_maintenance_starts_when_a_hidden_node_joins(self):
        env = self.env
        env.register("gamma", ["bob"], PBK_B)            # never shown, not probed yet
        env.round(T0)
        env.register("gamma", ["bob"], PBK_B, status_probe=True)
        env.round(T0 + 20 * 86400)
        self.assertIn(("gamma", "maintenance", T0 + 20 * 86400, None, ""), env.incidents())

    def test_purge_keeps_recent_data(self):
        env = self.env
        env.round(T0)
        env.round(T0 + 40 * 86400, beta_vision=False)
        with env.conn() as conn:
            status.purge(conn, env.st, T0 + 40 * 86400)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM probe_result WHERE at = ?", (T0,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM probe_result").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM status_daily").fetchone()[0], 4)


class DocumentTest(unittest.TestCase):
    def setUp(self):
        self.env = StatusEnv()

    def tearDown(self):
        self.env.close()

    def test_days_availability_and_no_connection_details(self):
        env = self.env
        # 2026-01-02 23:50 to 2026-01-03 00:10 (UTC+8): an outage across midnight
        start = T0 + 86400 - 600
        for i in range(0, 60):
            at = start - 1800 + 60 * i
            down = start <= at < start + 1200
            env.round(at, beta_vision=not down)
        doc = env.document(start + 1800)
        statuspage.validate(doc)
        self.assertEqual(doc["days"], ["2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02", "2026-01-03"])
        beta = next(n for n in doc["nodes"] if n["label"] == "beta [tag]")
        self.assertEqual(beta["state"], "ok")
        self.assertEqual(beta["days"][:3], [["nodata", 0, 0, 0]] * 3)
        self.assertEqual(beta["days"][3], ["outage", 10, 0, 0])
        self.assertEqual(beta["days"][4], ["outage", 10, 0, 0])
        self.assertEqual(beta["availability"], round(100 * (3600 - 1200) / 3600, 2))
        self.assertEqual(doc["events"][0]["transports"], ["Vision"])
        self.assertEqual((doc["events"][0]["started_at"], doc["events"][0]["ended_at"]), (start, start + 1200))
        text = json.dumps(doc, ensure_ascii=False)
        for secret in ("example.test", PBK_A, PBK_B, UUIDS["alice"], "/xp/synthetic", "alpha\"", "beta\""):
            self.assertNotIn(secret, text)

    def test_current_state(self):
        env = self.env
        for i in range(3):
            env.round(T0 + 60 * i, alpha_xhttp=False)
        with env.conn() as conn:
            db.set_shown(conn, "beta", False)
        env.round(T0 + 180, alpha_xhttp=False)
        states = {n["label"]: n["state"] for n in env.document(T0 + 180)["nodes"]}
        self.assertEqual(states, {"alpha [tag]": "partial", "beta [tag]": "maintenance"})
        stale = {n["label"]: n["state"] for n in env.document(T0 + 180 + 3600)["nodes"]}
        self.assertEqual(stale, {"alpha [tag]": "nodata", "beta [tag]": "maintenance"})
        env.round(T0 + 240)
        env.round(T0 + 300)
        self.assertEqual(env.document(T0 + 300 + 3600)["nodes"][0]["state"], "nodata")

    def test_alerts(self):
        env = self.env
        with env.conn() as conn:
            self.assertEqual(status.alerts(conn, env.st, env.nodes(), T0),
                             ["状态检测超过 5 分钟没有完成一轮（status 服务），状态页不再更新"])
        for i in range(3):
            env.round(T0 + 60 * i, beta_vision=False)
        with env.conn() as conn:
            self.assertEqual(status.alerts(conn, env.st, env.nodes(), T0 + 180),
                             ["节点 beta 故障（Vision），已持续 3 分钟"])
            self.assertEqual(status.alerts(conn, status_settings(enabled=False), env.nodes(), T0 + 9999), [])


class PageTest(unittest.TestCase):
    def setUp(self):
        self.env = StatusEnv()
        for i in range(3):
            self.env.round(T0 + 60 * i, alpha_xhttp=False)
        with self.env.conn() as conn:
            status.set_note(conn, 1, "  机房 <网络>\n维护  ")
        self.doc = self.env.document(T0 + 180)

    def tearDown(self):
        self.env.close()

    def test_page_shows_events_escaped_and_without_scripts(self):
        text = statuspage.page(self.doc, "2026-01-02", back_href="https://sub.example.test/s/x")
        self.assertIn("alpha [tag]", text)
        self.assertIn("部分异常", text)
        self.assertIn("XHTTP 链接无法连接，其他链接正常", text)
        self.assertIn("00:00 起，仍在持续（已 3 分钟）", text)
        self.assertIn("说明：机房 &lt;网络&gt; 维护", text)
        self.assertIn("2026-01-02 的事件", text)
        self.assertIn('http-equiv="refresh"', text)
        self.assertNotIn("<script", text)
        self.assertEqual(text.count("title='"), 2 * 5)   # one cell per node per day

    def test_recent_days_without_selection_and_missing_data(self):
        text = statuspage.page(self.doc, "not-a-day")
        self.assertIn("最近 7 天", text)
        self.assertIn("暂无状态数据", statuspage.page(None))

    def test_expired_documents_are_marked(self):
        fresh = statuspage.page(self.doc, now=T0 + 180 + 60)
        self.assertNotIn("已过期", fresh)
        old = statuspage.page(self.doc, now=T0 + 180 + 3600)
        self.assertIn("状态数据已过期，当前状态未知", old)
        self.assertNotIn("badge s-partial", old)
        self.assertNotIn("badge s-ok", old)

    def test_validate_rejects_bad_documents(self):
        for mutate in (lambda d: d.update(schema=2), lambda d: d["nodes"][0].update(state="up"),
                       lambda d: d["nodes"][0]["days"].pop(), lambda d: d["events"][0].update(kind="x"),
                       lambda d: d.update(days=["2026-1-1"]), lambda d: d.update(interval=0),
                       lambda d: d.update(nodes=[1]), lambda d: d.update(tz=[]), lambda d: d.update(events={}),
                       lambda d: d["events"][0].update(transports=[1]),
                       lambda d: d["events"][0].update(all_transports="2"),
                       lambda d: d.update(generated_at=True), lambda d: d.update(days=["2026-02-30"] * 5)):
            doc = json.loads(json.dumps(self.doc))
            mutate(doc)
            with self.assertRaises(statuspage.StatusError):
                statuspage.validate(doc)

    def test_note_is_trimmed_and_bounded(self):
        with self.env.conn() as conn:
            self.assertEqual(status.set_note(conn, 1, "x" * 900), (True, "x" * status.NOTE_MAX))
            self.assertEqual(status.set_note(conn, 99, "x")[0], False)


class FakeClient:
    def __init__(self, ready=True):
        self.ready = ready
        self.configs = []
        self.stopped = 0

    def ensure(self, cfg, first_port):
        self.configs.append(cfg)
        return self.ready

    def stop(self):
        self.stopped += 1


class ProberTest(unittest.TestCase):
    def setUp(self):
        self.env = StatusEnv()
        self.probe_file = os.path.join(self.env.dirs["data"], "probe.json")
        with open(self.probe_file, "w") as fh:
            json.dump(PROBE, fh)
        self.st = status_settings(probe_file=self.probe_file)

    def tearDown(self):
        self.env.close()

    def prober(self, direct=True, ready=True, through=True):
        calls = []

        def fetch(url, proxy, timeout, expect):
            calls.append(proxy)
            ok = direct if proxy is None else through
            return (ok, 10, "") if ok else (False, None, "timeout")
        return status.Prober(self.env.settings, self.st, client=FakeClient(ready), fetcher=fetch), calls

    def test_round_writes_results_and_a_readable_document(self):
        prober, calls = self.prober()
        results = prober.run_round(T0)
        self.assertEqual(calls[0], None)   # the direct check comes first; the probes run in parallel
        self.assertEqual(sorted(calls[1:]), ["http://127.0.0.1:20000", "http://127.0.0.1:20001", "http://127.0.0.1:20002"])
        self.assertEqual(set(results.values()), {(True, 10, "")})
        path = os.path.join(self.env.dirs["subs-data"], "status.json")
        self.assertEqual(statmod.S_IMODE(os.stat(path).st_mode), 0o640)
        doc = statuspage.load(path)
        self.assertEqual([n["state"] for n in doc["nodes"]], ["ok", "ok"])

    def test_local_problems_are_no_data(self):
        for at, kw, error in ((T0, {"direct": False}, "local network"), (T0 + 60, {"ready": False}, "local client")):
            prober, calls = self.prober(**kw)
            self.assertEqual(set(prober.run_round(at).values()), {(None, None, error)})
            self.assertEqual(calls, [None])

    def test_no_targets_stops_the_client(self):
        for node in ("alpha", "beta"):
            self.env.register(node, ["alice"], PBK_A, status_probe=False)
        prober, calls = self.prober()
        self.assertEqual(prober.run_round(T0), {})
        self.assertEqual((calls, prober.client.stopped), ([], 1))
        self.assertEqual(statuspage.load(os.path.join(self.env.dirs["subs-data"], "status.json"))["nodes"], [])

    def test_a_round_is_processed_once(self):
        prober, calls = self.prober(through=False)
        self.assertIsNotNone(prober.run_round(T0))
        self.assertIsNone(prober.run_round(T0))          # restarted within the same minute
        self.assertEqual(len(calls), 4)
        with self.env.conn() as conn:
            self.assertEqual(conn.execute("SELECT known_seconds FROM status_daily WHERE node = 'beta'").fetchone()[0], 60)
            self.assertEqual(conn.execute("SELECT fails FROM probe_state WHERE node = 'beta'").fetchone()[0], 1)

    def test_invalid_probe_file_fails_the_round(self):
        with open(self.probe_file, "w") as fh:
            json.dump({"uuid": "x", "short_id": ""}, fh)
        prober, _ = self.prober()
        with self.assertRaises(ValueError):
            prober.run_round(T0)

    def test_healthy_follows_the_last_round(self):
        prober, _ = self.prober()
        prober.run_round(T0)
        self.assertTrue(status.healthy(self.env.settings, self.st, T0 + 60))
        self.assertFalse(status.healthy(self.env.settings, self.st, T0 + 3 * 60 + 1))


class Responder(http.server.BaseHTTPRequestHandler):
    code = 204

    def do_GET(self):
        self.send_response(self.server.code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class FetchTest(unittest.TestCase):
    def serve(self, code):
        server = http.server.HTTPServer(("127.0.0.1", 0), Responder)
        server.code = code
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def free_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def test_results(self):
        ok = self.serve(204)
        self.assertEqual(status.fetch("http://cp.example.test/generate_204", ok, 5, 204)[::2], (True, ""))
        self.assertEqual(status.fetch(ok + "/generate_204", None, 5, 204)[::2], (True, ""))
        self.assertEqual(status.fetch("http://cp.example.test/", self.serve(200), 5, 204)[::2], (False, "http 200"))
        self.assertEqual(status.fetch("http://cp.example.test/", self.serve(302), 5, 204)[::2], (False, "http 302"))
        closed = f"http://127.0.0.1:{self.free_port()}"
        self.assertEqual(status.fetch("http://cp.example.test/", closed, 5, 204), (None, None, "local client"))
        self.assertEqual(status.fetch(closed + "/", None, 5, 204), (False, None, "connect"))

    def test_timeout(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        proxy = f"http://127.0.0.1:{listener.getsockname()[1]}"
        self.assertEqual(status.fetch("http://cp.example.test/", proxy, 1, 204), (False, None, "timeout"))


class AdminPageTest(unittest.TestCase):
    def setUp(self):
        self.env = StatusEnv()
        for i in range(3):
            self.env.round(T0 + 60 * i, beta_vision=False)
        self.app = web_app.create_app(self.env.settings, start_watcher=False, csrf_secret=b"k" * 32,
                                      status_settings=self.env.st)

    def tearDown(self):
        self.env.close()

    def test_status_page_notes_and_alerts(self):
        import hashlib
        import hmac
        import urllib.parse
        csrf = hmac.new(b"k" * 32, b"console-form", hashlib.sha256).hexdigest()
        with Server(self.app) as srv:
            status_code, page, _ = srv.request("GET", "/status?day=2026-01-02")
            self.assertEqual(status_code, 200)
            self.assertIn("beta [tag]", page)
            self.assertIn("各连接方式最近一次检测", page)
            self.assertIn("status 服务没有按时完成检测", page)   # T0 is long ago
            self.assertIn("href='/status?day=", page)
            self.assertIn("<td>Vision</td>", page)
            body = urllib.parse.urlencode({"csrf": csrf, "note": "上游线路中断"}).encode()
            code = srv.request("POST", "/incidents/1/note", body,
                               {"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://" + HOST})[0]
            self.assertEqual(code, 303)
            self.assertEqual(srv.request("POST", "/incidents/1/note", urllib.parse.urlencode({"note": "x"}).encode(),
                                         {"Content-Type": "application/x-www-form-urlencoded"})[0], 403)
            self.assertIn('value="上游线路中断"', srv.request("GET", "/status")[1])
            self.assertIn("上游线路中断", srv.request("GET", "/nodes/beta")[1])
            home = srv.request("GET", "/")[1]
            self.assertIn("节点 beta 故障（Vision）", home)
            self.assertIn("状态检测超过 5 分钟没有完成一轮", home)
            self.assertIn("上游线路中断", srv.request("GET", "/audit")[1])

    def test_disabled(self):
        app = web_app.create_app(self.env.settings, start_watcher=False, csrf_secret=b"k" * 32,
                                 status_settings=status_settings(enabled=False))
        with Server(app) as srv:
            self.assertIn("状态页未启用", srv.request("GET", "/status")[1])
            self.assertNotIn("状态检测超过", srv.request("GET", "/")[1])


if __name__ == "__main__":
    unittest.main()
