#!/usr/bin/env python3
"""Node reporter: counter deltas, restart handling, spool and retry (plan-console-phase1 §3.4, §6.1).

Run: python3 -m unittest discover -s tests/edge -p 'test_reporter.py'
All values are synthetic.
"""
import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("edge_reporter", REPO / "docker/edge-tools/edge_reporter.py")
rep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rep)


def metrics(users):
    return {"stats": {"inbound": {"vless-reality": {"uplink": 1, "downlink": 2}},
                      "user": {f"{n}.node1": {"uplink": up, "downlink": down} for n, (up, down) in users.items()}}}


class DeltaTest(unittest.TestCase):
    def test_counters_keep_only_this_nodes_users(self):
        doc = metrics({"alice": (5, 7)})
        doc["stats"]["user"]["bob.other"] = {"uplink": 1, "downlink": 1}
        self.assertEqual(rep.user_counters(doc, "node1"), {"alice": {"up": 5, "down": 7}})
        self.assertEqual(rep.user_counters({}, "node1"), {})

    def test_first_reading_is_only_a_baseline(self):
        self.assertEqual(rep.traffic_delta(None, {"alice": {"up": 5, "down": 7}}), ({}, False))

    def test_growth_and_new_users(self):
        prev = {"alice": {"up": 5, "down": 7}}
        cur = {"alice": {"up": 8, "down": 7}, "bob": {"up": 1, "down": 2}}
        self.assertEqual(rep.traffic_delta(prev, cur),
                         ({"alice": {"up": 3, "down": 0}, "bob": {"up": 1, "down": 2}}, False))

    def test_start_line_marks_a_restart_even_when_counters_grew(self):
        prev = {"alice": {"up": 5, "down": 7}}
        cur = {"alice": {"up": 50, "down": 70}}
        self.assertEqual(rep.traffic_delta(prev, cur, started=True), ({"alice": {"up": 50, "down": 70}}, True))
        self.assertEqual(rep.traffic_delta(prev, cur), ({"alice": {"up": 45, "down": 63}}, False))

    def test_restart_counts_current_values_once(self):
        prev = {"alice": {"up": 500, "down": 700}, "bob": {"up": 9, "down": 9}}
        cur = {"alice": {"up": 4, "down": 6}}
        self.assertEqual(rep.traffic_delta(prev, cur), ({"alice": {"up": 4, "down": 6}}, True))


class NodeFactsTest(unittest.TestCase):
    def test_listening_reads_both_families(self):
        with tempfile.TemporaryDirectory() as d:
            header = "  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            with open(os.path.join(d, "tcp"), "w") as fh:
                fh.write(header + "   0: 0100007F:275D 00000000:0000 0A 00000000:00000000 00:00000000 00000000 10000 0 1\n")
            with open(os.path.join(d, "tcp6"), "w") as fh:
                fh.write(header + "   1: 00000000000000000000000000000000:01BB 00000000000000000000000000000000:0000 0A 0 0 0 0 0 0 1\n")
            self.assertTrue(rep.listening(443, d))
            self.assertFalse(rep.listening(8443, d))
            self.assertTrue(rep.listening(10077, d))

    def test_start_lines_are_found_once_and_after_rotation(self):
        start = "2026/09/17 10:14:04.949415 [Warning] core: Xray 26.3.27 started\n"
        other = "2026/09/17 10:14:05.000000 [Warning] app/dispatcher: something\n"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "error.log")
            with open(path, "w") as fh:
                fh.write(start + other)
            started, pos = rep.log_startups(path, None)          # first cycle: position only
            self.assertFalse(started)
            self.assertEqual(rep.log_startups(path, pos), (False, pos))
            with open(path, "a") as fh:
                fh.write(other + start + "2026/09/17 10:15:00 [Warning] core: Xray 26.3.27 sta")
            started, pos = rep.log_startups(path, pos)
            self.assertTrue(started)
            self.assertFalse(rep.log_startups(path, pos)[0])     # the partial line is not a start yet
            with open(path, "w") as fh:                          # copytruncate
                fh.write(other)
            started, pos = rep.log_startups(path, pos)
            self.assertFalse(started)
            with open(path, "a") as fh:
                fh.write(start)
            self.assertTrue(rep.log_startups(path, pos)[0])
            self.assertEqual(rep.log_startups(os.path.join(d, "missing"), pos), (False, pos))

    def test_error_tail_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "error.log")
            with open(path, "w") as fh:
                for i in range(1000):
                    fh.write(f"line {i:04d} some error text\n")
            tail = rep.error_tail(path)
            self.assertEqual(len(tail), rep.ERROR_TAIL_LINES)
            self.assertEqual(tail[-1], "line 0999 some error text")
            self.assertEqual(rep.error_tail(os.path.join(d, "missing")), [])


class FakeServers:
    """Metrics endpoint and console on localhost; the console can be told to fail."""

    def __init__(self):
        self.users = {}
        self.fail = False
        self.received = []
        servers = self

        class Metrics(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps(metrics(servers.users)).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Console(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if servers.fail:
                    self.send_response(503)
                elif self.headers.get("User-Agent", "").startswith("Python-urllib"):
                    self.send_response(403)  # as Cloudflare answers the default Python User-Agent
                elif self.headers.get("Authorization") != "Bearer tok":
                    self.send_response(401)
                else:
                    seen = [(r.get("instance"), r["seq"]) for r in servers.received]
                    key = (body["instance"], body["seq"])
                    self.send_response(409 if key in seen else 200)
                    if key not in seen:
                        servers.received.append(body)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.metrics = ThreadingHTTPServer(("127.0.0.1", 0), Metrics)
        self.console = ThreadingHTTPServer(("127.0.0.1", 0), Console)
        for s in (self.metrics, self.console):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    def close(self):
        for s in (self.metrics, self.console):
            s.shutdown()
            s.server_close()


class RunTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServers()
        self.tmp = tempfile.TemporaryDirectory()
        token = os.path.join(self.tmp.name, "token")
        with open(token, "w") as fh:
            fh.write("tok\n")
        spool = os.path.join(self.tmp.name, "spool")
        os.mkdir(spool)
        self.cfg = rep.config_from_env({
            "REPORT_URL": f"http://127.0.0.1:{self.srv.console.server_port}/report",
            "REPORT_NODE": "node1",
            "REPORT_TOKEN_FILE": token,
            "METRICS_URL": f"http://127.0.0.1:{self.srv.metrics.server_port}/debug/vars",
            "SPOOL_DIR": spool,
            "SPOOL_MAX": "3",
            "ERROR_LOG": os.path.join(self.tmp.name, "none.log"),
        })
        self.spool = rep.Spool(spool, 3)

    def tearDown(self):
        self.srv.close()
        self.tmp.cleanup()

    def test_reports_in_order_and_retries_after_outage(self):
        self.srv.users = {"alice": (10, 20)}
        rep.run_once(self.cfg, self.spool)                      # baseline
        self.srv.users = {"alice": (15, 30)}
        self.srv.fail = True
        rep.run_once(self.cfg, self.spool)                      # kept in spool
        self.assertEqual(len(self.spool.pending()), 1)
        self.srv.users = {"alice": (16, 30), "bob": (1, 1)}
        self.srv.fail = False
        rep.run_once(self.cfg, self.spool)                      # both sent, in order
        self.assertEqual(self.spool.pending(), [])
        got = self.srv.received
        self.assertEqual([r["seq"] for r in got], [1, 2, 3])
        self.assertRegex(got[0]["instance"], r"^[0-9a-f]{16}$")
        self.assertEqual({r["instance"] for r in got}, {got[0]["instance"]})
        self.assertTrue(got[0]["baseline"])
        self.assertEqual(got[0]["traffic"], {})
        self.assertEqual(got[1]["traffic"], {"alice": {"up": 5, "down": 10}})
        self.assertEqual(got[2]["traffic"], {"alice": {"up": 1, "down": 0}, "bob": {"up": 1, "down": 1}})
        self.assertEqual(got[1]["period"]["from"], got[0]["period"]["to"])

    def test_spool_limit_drops_oldest_and_says_so(self):
        self.srv.fail = True
        for _ in range(5):
            rep.run_once(self.cfg, self.spool)
        self.assertEqual(len(self.spool.pending()), 3)
        self.srv.fail = False
        rep.run_once(self.cfg, self.spool)
        seqs = [r["seq"] for r in self.srv.received]
        self.assertEqual(seqs, [4, 5, 6])
        self.assertEqual(self.srv.received[-1]["dropped_reports"], 1)

    def test_lost_state_starts_a_new_instance(self):
        rep.run_once(self.cfg, self.spool)
        os.remove(self.spool.state_path)
        rep.run_once(self.cfg, self.spool)
        first, second = self.srv.received
        self.assertEqual((first["seq"], second["seq"]), (1, 1))
        self.assertNotEqual(first["instance"], second["instance"])
        self.assertTrue(second["baseline"])

    def test_already_stored_report_is_removed(self):
        self.srv.users = {"alice": (1, 1)}
        rep.run_once(self.cfg, self.spool)
        self.srv.received.append({"instance": self.srv.received[0]["instance"], "seq": 2})
        rep.run_once(self.cfg, self.spool)
        self.assertEqual(self.spool.pending(), [])


if __name__ == "__main__":
    unittest.main()
