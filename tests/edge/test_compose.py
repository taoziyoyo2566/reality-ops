#!/usr/bin/env python3
"""compose.yaml.j2 against the S3 container contract (plan-edge-compose §6.1 item 1).

The xray service must keep every runtime parameter of the S3 docker_container task. When
Docker Compose is available the rendered file is also normalized with `docker compose config`
so the check covers how Compose itself reads it.

Run with a Python that has Jinja2 and PyYAML, e.g.: monitor_venv/bin/python tests/edge/test_compose.py
"""
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import jinja2
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "roles/xray_edge/templates/compose.yaml.j2"
GROUP_VARS = yaml.safe_load((REPO / "group_vars/all/edge.yml").read_text())
TOOLS = "reality-edge-tools:0123456789ab"


def render(node, ports, **overrides):
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    env.filters["to_json"] = json.dumps
    # Ansible's bool filter: real booleans pass through, strings follow Ansible's truthy words.
    env.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).strip().lower() in ("yes", "on", "1", "true")
    variables = {k: v for k, v in GROUP_VARS.items() if isinstance(v, (str, int, list, dict))}
    variables.update({
        "ansible_managed": "test", "edge_node_name": node, "edge_published_ports": ports,
        "edge_tools_image": TOOLS, "edge_report_enabled": False,
        "edge_report_url": "https://report.example.test/report",
    })
    variables.update(overrides)
    return env.from_string(TEMPLATE.read_text()).render(**variables)


# S3 docker_container parameters (roles/xray_edge/tasks/main.yml at ops@e45b424), in compose terms.
S3_XRAY = {
    "image": GROUP_VARS["edge_xray_image"],
    "container_name": "xray_edge",
    "restart": "unless-stopped",
    "network_mode": "bridge",
    "user": "10000:10000",
    "volumes": ["/opt/xray-edge/conf.d:/etc/xray/conf.d:ro", "/opt/xray-edge/logs:/var/log/xray"],
    "read_only": True,
    "tmpfs": ["/tmp", "/run"],
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true"],
    "pids_limit": 256,
    "mem_limit": "300m",
    "mem_reservation": "200m",
    "memswap_limit": "1g",
    "ulimits": {"nofile": {"soft": 65535, "hard": 65535}},
    "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
}
DZIRE_PORTS = ["0.0.0.0:443:443"]
USCA_PORTS = ["0.0.0.0:443:443", "[::]:443:443"]


class ComposeTemplateTest(unittest.TestCase):
    def test_xray_service_matches_s3_container(self):
        doc = yaml.safe_load(render("usca", USCA_PORTS))
        xray = doc["services"]["xray"]
        for key, expected in S3_XRAY.items():
            self.assertEqual(xray.get(key), expected, key)
        self.assertEqual(xray["ports"], USCA_PORTS)
        self.assertNotIn("dns", xray)
        self.assertEqual(set(xray) - set(S3_XRAY), {"ports"})

    def test_dns_override_only_for_listed_node(self):
        xray = yaml.safe_load(render("dzire", DZIRE_PORTS))["services"]["xray"]
        self.assertEqual(xray["dns"], ["8.8.8.8", "1.1.1.1"])
        self.assertEqual(xray["dns_opt"], ["timeout:1", "attempts:2"])
        self.assertEqual(xray["ports"], DZIRE_PORTS)

    def test_no_ports_key_without_published_ports(self):
        self.assertNotIn("ports", yaml.safe_load(render("usca", []))["services"]["xray"])

    def test_only_the_applier_reaches_docker(self):
        services = yaml.safe_load(render("usca", USCA_PORTS))["services"]
        for name, service in services.items():
            mounts = " ".join(service.get("volumes", []))
            self.assertEqual("docker.sock" in mounts, name == "applier", name)
        self.assertEqual(services["applier"]["profiles"], ["tools"])
        self.assertIn("/opt/xray-edge:/opt/xray-edge", services["applier"]["volumes"])
        self.assertEqual(services["applier"]["network_mode"], "none")

    def test_logrotate_service_is_confined(self):
        rotate = yaml.safe_load(render("usca", USCA_PORTS))["services"]["logrotate"]
        self.assertEqual(rotate["image"], TOOLS)
        self.assertEqual(rotate["user"], "10000:10000")
        self.assertEqual(rotate["network_mode"], "none")
        self.assertTrue(rotate["read_only"])
        self.assertEqual(rotate["cap_drop"], ["ALL"])
        self.assertEqual(rotate["command"][-1], str(GROUP_VARS["edge_logrotate_interval"]))
        self.assertIn("/opt/xray-edge/rotate/logrotate.conf:/rotate/logrotate.conf:ro", rotate["volumes"])

    def test_reporter_only_when_enabled_and_confined(self):
        self.assertNotIn("reporter", yaml.safe_load(render("usca", USCA_PORTS))["services"])
        services = yaml.safe_load(render("usca", USCA_PORTS, edge_report_enabled=True))["services"]
        reporter = services["reporter"]
        self.assertEqual(reporter["image"], TOOLS)
        self.assertEqual(reporter["network_mode"], "service:xray")
        self.assertEqual(reporter["user"], "10000:10000")
        self.assertTrue(reporter["read_only"])
        self.assertEqual(reporter["cap_drop"], ["ALL"])
        self.assertEqual(sorted(reporter["volumes"]), [
            "/opt/xray-edge/logs:/var/log/xray:ro",
            "/opt/xray-edge/secrets:/run/report:ro",
            "/opt/xray-edge/spool:/spool",
        ])
        env = reporter["environment"]
        self.assertEqual((env["REPORT_NODE"], env["REPORT_URL"], env["REPORT_INTERVAL"]),
                         ("usca", "https://report.example.test/report", str(GROUP_VARS["edge_report_interval"])))
        # the xray service itself is unchanged by enabling the reporter
        self.assertEqual(services["xray"], yaml.safe_load(render("usca", USCA_PORTS))["services"]["xray"])

    @unittest.skipUnless(shutil.which("docker"), "docker not available")
    def test_compose_reads_the_file_as_intended(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp, "compose.yaml")
            path.write_text(render("usca", USCA_PORTS, edge_report_enabled=True))
            proc = subprocess.run(["docker", "compose", "-f", str(path), "--profile", "tools", "config", "--format", "json"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            services = json.loads(proc.stdout)["services"]
        xray = services["xray"]
        self.assertEqual(xray["network_mode"], "bridge")
        self.assertEqual(sorted((p["host_ip"], p["published"], p["target"]) for p in xray["ports"]),
                         [("0.0.0.0", "443", 443), ("::", "443", 443)])
        self.assertEqual(xray["mem_limit"], str(300 * 1024 * 1024))
        self.assertEqual(xray["memswap_limit"], str(1024 ** 3))
        self.assertEqual(xray["ulimits"]["nofile"], {"soft": 65535, "hard": 65535})
        self.assertEqual([v["read_only"] for v in xray["volumes"] if v["target"] == "/etc/xray/conf.d"], [True])
        self.assertEqual(services["applier"]["entrypoint"], ["python3", "/usr/local/bin/xray_edge_apply.py"])
        self.assertEqual(services["reporter"]["network_mode"], "service:xray")


if __name__ == "__main__":
    unittest.main()
