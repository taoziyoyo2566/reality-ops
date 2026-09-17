#!/usr/bin/env python3
"""roles/console_service compose template and the subscription service's directory hand-over
(plan-console-phase1 §3.5-3.6, §6.1 items 2 and 4).

Run with a Python that has Jinja2 and PyYAML, e.g.: monitor_venv/bin/python tests/console/test_compose.py
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

import jinja2
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "roles/console_service/templates/compose.yaml.j2"
SUBS_TEMPLATE = REPO / "roles/subs_service/templates/compose.yaml.j2"
IMAGE = "reality-console:0123456789ab"


def group_vars(*names):
    """Variables from group_vars/all, with simple self-references resolved (lookups are left out)."""
    raw = {}
    for name in names:
        raw.update(yaml.safe_load((REPO / f"group_vars/all/{name}.yml").read_text()))
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    values = {k: v for k, v in raw.items() if not (isinstance(v, str) and "{{" in v)}
    pending = {k: v for k, v in raw.items() if k not in values}
    for _ in range(5):
        for key, template in list(pending.items()):
            try:
                values[key] = env.from_string(template).render(**values)
                del pending[key]
            except jinja2.exceptions.UndefinedError:
                continue
    return values


def environment():
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).strip().lower() in ("yes", "on", "1", "true")
    return env


def render(**overrides):
    variables = group_vars("subs", "console")
    variables.update({"ansible_managed": "test", "console_image": IMAGE})
    variables.update(overrides)
    return environment().from_string(TEMPLATE.read_text()).render(**variables)


def render_subs(**overrides):
    variables = group_vars("subs", "console")
    variables.update({"ansible_managed": "test", "subs_image": "reality-subs:0123456789ab"})
    variables.update(overrides)
    return environment().from_string(SUBS_TEMPLATE.read_text()).render(**variables)


class ConsoleComposeTest(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(render())
        self.services = self.doc["services"]

    def test_services_and_hardening(self):
        self.assertEqual(sorted(self.services), ["cloudflared", "report", "web"])
        for name, svc in self.services.items():
            with self.subTest(service=name):
                self.assertTrue(svc["read_only"])
                self.assertEqual(svc["cap_drop"], ["ALL"])
                self.assertEqual(svc["security_opt"], ["no-new-privileges:true"])
                self.assertEqual(svc["restart"], "unless-stopped")
                self.assertEqual(svc["logging"]["driver"], "json-file")
                self.assertNotIn("docker.sock", " ".join(svc.get("volumes", [])))
        for name in ("web", "report"):
            self.assertEqual(self.services[name]["user"], "10002:10002")
            self.assertEqual(self.services[name]["image"], IMAGE)

    def test_only_web_is_published_and_only_on_loopback(self):
        self.assertEqual(self.services["web"]["ports"], ["127.0.0.1:8200:8200"])
        self.assertNotIn("ports", self.services["report"])
        self.assertNotIn("ports", self.services["cloudflared"])
        self.assertEqual(self.services["report"]["networks"], ["internal"])
        self.assertEqual(self.services["web"]["networks"], ["admin"])
        self.assertEqual(sorted(self.services["cloudflared"]["networks"]), ["egress", "internal"])
        self.assertTrue(self.doc["networks"]["internal"]["internal"])

    def test_mounts(self):
        web = self.services["web"]["volumes"]
        self.assertIn("/opt/reality-subs/data:/subs-data", web)
        self.assertIn("/opt/reality-subs/db:/subs-db:ro", web)
        self.assertIn("/opt/reality-console/registry:/registry:ro", web)
        self.assertIn("/opt/reality-console/import:/import:ro", web)
        self.assertEqual(sorted(self.services["report"]["volumes"]),
                         ["/opt/reality-console/db:/db", "/opt/reality-console/registry:/registry:ro"])
        self.assertEqual(self.services["cloudflared"]["env_file"], ["/opt/reality-console/secrets/tunnel.env"])
        self.assertEqual(self.services["web"]["environment"]["CONSOLE_PUBLIC_BASE_URL"], "https://sub.taoziyoyo.com")

    def test_report_host_and_url_agree(self):
        edge = yaml.safe_load((REPO / "group_vars/all/edge.yml").read_text())
        console = group_vars("subs", "console")
        self.assertEqual(edge["edge_report_url"], "https://{{ console_report_host }}/report")
        self.assertEqual(console["console_report_host"], "report.taoziyoyo.com")
        self.assertEqual(yaml.safe_load(render())["services"]["report"]["environment"]["CONSOLE_PORT"],
                         str(console["console_report_port"]))

    @unittest.skipUnless(shutil.which("docker"), "docker not available")
    def test_compose_reads_the_file_as_intended(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = render(console_root_dir=tmp, console_registry_dir=f"{tmp}/registry")
            pathlib.Path(tmp, "secrets").mkdir()
            pathlib.Path(tmp, "secrets", "tunnel.env").write_text("TUNNEL_TOKEN=x\n")
            path = pathlib.Path(tmp, "compose.yaml")
            path.write_text(text)
            proc = subprocess.run(["docker", "compose", "-f", str(path), "config", "--format", "json"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            services = json.loads(proc.stdout)["services"]
        web = services["web"]
        self.assertEqual([(p["host_ip"], p["published"], p["target"]) for p in web["ports"]],
                         [("127.0.0.1", "8200", 8200)])
        self.assertEqual(web["mem_limit"], str(128 * 1024 * 1024))
        self.assertEqual(sorted(services["report"]["networks"]), ["internal"])


class SubsHandOverTest(unittest.TestCase):
    def test_role_gives_data_to_console_and_log_group_to_console(self):
        tasks = (REPO / "roles/subs_service/tasks/main.yml").read_text()
        self.assertIn('{ path: "{{ subs_root_dir }}/data", owner: "{{ console_uid }}", group: "10001", mode: "2750" }', tasks)
        self.assertIn('{ path: "{{ subs_root_dir }}/db", owner: "10001", group: "{{ console_uid }}", mode: "0750" }', tasks)
        self.assertNotIn("catalog.json\", \"tokens.json\"", tasks)
        self.assertIsNone(re.search(r"subs_build_dir\b", tasks))

    def test_subs_compose_unchanged_contract(self):
        subs = yaml.safe_load(render_subs())["services"]["subs"]
        self.assertIn("/opt/reality-subs/data:/data:ro", subs["volumes"])
        self.assertEqual(subs["user"], "10001:10001")


if __name__ == "__main__":
    unittest.main()
