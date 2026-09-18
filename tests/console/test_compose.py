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
import urllib.parse

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
    env.filters["urlsplit"] = lambda url, part: getattr(urllib.parse.urlsplit(url), part)
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
    env.filters["combine"] = lambda a, b: dict(a, **b)
    return env


def render(**overrides):
    variables = group_vars("subs", "console", "status")
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
        self.assertEqual(sorted(self.services), ["cloudflared", "report", "status", "web"])
        for name, svc in self.services.items():
            with self.subTest(service=name):
                self.assertTrue(svc["read_only"])
                self.assertEqual(svc["cap_drop"], ["ALL"])
                self.assertEqual(svc["security_opt"], ["no-new-privileges:true"])
                self.assertEqual(svc["restart"], "unless-stopped")
                self.assertEqual(svc["logging"]["driver"], "json-file")
                self.assertNotIn("docker.sock", " ".join(svc.get("volumes", [])))
        for name in ("web", "report", "status"):
            self.assertEqual(self.services[name]["user"], "10002:10002")
            self.assertEqual(self.services[name]["image"], IMAGE)

    def test_status_service(self):
        status = self.services["status"]
        self.assertEqual(status["command"], ["python", "-m", "console.status"])
        self.assertTrue(status["init"])
        self.assertEqual(status["networks"], ["egress"])
        self.assertNotIn("ports", status)
        self.assertEqual(sorted(status["volumes"]), [
            "/opt/reality-console/db:/db", "/opt/reality-console/probe:/run/probe:ro",
            "/opt/reality-console/registry:/registry:ro", "/opt/reality-subs/data:/subs-data"])
        self.assertEqual((status["pids_limit"], status["mem_limit"]), (128, "128m"))
        self.assertEqual(status["healthcheck"]["test"], ["CMD", "python", "-m", "console.status", "--check"])
        env = status["environment"]
        self.assertEqual((env["STATUS_ENABLED"], env["STATUS_INTERVAL"], env["STATUS_FAIL_COUNT"],
                          env["STATUS_RECOVER_COUNT"], env["STATUS_UTC_OFFSET_HOURS"]), ("true", "60", "3", "2", "8"))
        self.assertEqual(env["STATUS_CHECK_URL"], "http://cp.cloudflare.com/generate_204")
        # the admin pages read the same settings
        web_env = self.services["web"]["environment"]
        self.assertEqual({k: v for k, v in web_env.items() if k.startswith("STATUS_")}, env)

    def test_status_can_be_turned_off(self):
        services = yaml.safe_load(render(status_enabled=False))["services"]
        self.assertNotIn("status", services)
        self.assertEqual(services["web"]["environment"]["STATUS_ENABLED"], "false")

    def test_probe_account_matches_on_both_sides(self):
        status = group_vars("status")
        self.assertEqual(status["status_probe_target"], "cp.cloudflare.com:80")
        values = dict(status, status_check_url="http://probe.example.test:8080/x")
        target = yaml.safe_load((REPO / "group_vars/all/status.yml").read_text())["status_probe_target"]
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters["urlsplit"] = lambda url, part: getattr(urllib.parse.urlsplit(url), part)
        self.assertEqual(env.from_string(target).render(**values), "probe.example.test:8080")
        template = (REPO / "roles/xray_edge/templates/desired-state.json.j2").read_text()
        self.assertIn('"target": {{ status_probe_target | to_json }}', template)
        edge = yaml.safe_load((REPO / "group_vars/all/edge.yml").read_text())
        self.assertEqual(edge["edge_status_probe_enabled"], "{{ inventory_hostname in status_probe_nodes }}")
        # the probe account follows the new-system nodes; no second list to keep in sync
        raw = yaml.safe_load((REPO / "group_vars/all/status.yml").read_text())
        self.assertEqual(raw["status_probe_nodes"], "{{ edge_nodes }}")
        playbook = yaml.safe_load((REPO / "edge.yml").read_text())[0]
        self.assertEqual(playbook["tasks"][0]["when"], "inventory_hostname in edge_nodes")
        self.assertEqual((playbook["hosts"], playbook["serial"], playbook["gather_facts"]),
                         ("reality_nodes", 1, False))
        dockerfile = (REPO / "docker/console/Dockerfile").read_text()
        self.assertIn(f"FROM {edge['edge_xray_image']} AS xray", dockerfile)

    def test_bot_only_with_a_token(self):
        self.assertNotIn("bot", self.services)
        self.assertEqual(self.services["web"]["environment"]["CONSOLE_BOT_ENABLED"], "false")
        raw = yaml.safe_load((REPO / "group_vars/all/console.yml").read_text())
        self.assertEqual(raw["console_bot_token"], "{{ vault_console_bot_token | default('') }}")
        self.assertEqual(raw["console_bot_enabled"], "{{ console_bot_token | length > 0 }}")
        self.assertEqual(raw["console_bot_admin_ids"], "{{ vault_console_bot_admin_ids | default([]) }}")

    def test_bot_service(self):
        services = yaml.safe_load(render(console_bot_enabled=True))["services"]
        bot = services["bot"]
        self.assertEqual(bot["command"], ["python", "-m", "console.bot"])
        self.assertEqual((bot["user"], bot["image"], bot["networks"]), ("10002:10002", IMAGE, ["egress"]))
        self.assertNotIn("ports", bot)
        self.assertEqual(sorted(bot["volumes"]), [
            "/opt/reality-console/bot:/run/bot:ro", "/opt/reality-console/db:/db",
            "/opt/reality-console/registry:/registry:ro", "/opt/reality-subs/data:/subs-data",
            "/opt/reality-subs/db:/subs-db:ro"])
        self.assertEqual(bot["healthcheck"]["test"], ["CMD", "python", "-m", "console.bot", "--check"])
        self.assertTrue(bot["read_only"])
        self.assertEqual((bot["cap_drop"], bot["security_opt"]), (["ALL"], ["no-new-privileges:true"]))
        env = bot["environment"]
        self.assertEqual((env["CONSOLE_PUBLIC_BASE_URL"], env["BOT_API_URL"]),
                         ("https://sub.taoziyoyo.com", "https://api.telegram.org"))
        self.assertNotIn("BOT_TOKEN", " ".join(env))                     # the token is a mounted file only
        web_env = services["web"]["environment"]
        self.assertEqual(web_env["CONSOLE_BOT_ENABLED"], "true")
        self.assertEqual({k: v for k, v in env.items() if k.startswith("STATUS_")},
                         {k: v for k, v in web_env.items() if k.startswith("STATUS_")})

    def test_role_writes_bot_files_for_the_container_only(self):
        tasks = yaml.safe_load((REPO / "roles/console_service/tasks/main.yml").read_text())
        files = next(t for t in tasks if t["name"] == "下发 Telegram bot 的 token 与管理员列表")
        self.assertEqual((files["copy"]["owner"], files["copy"]["group"], files["copy"]["mode"]),
                         ("0", "{{ console_uid }}", "0640"))
        self.assertTrue(files["no_log"])
        dirs = next(t for t in tasks if t["name"] == "创建控制台目录")["loop"]
        self.assertIn({"path": "{{ console_root_dir }}/bot", "owner": "0", "group": "{{ console_uid }}", "mode": "0750"},
                      dirs)

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
            text = render(console_root_dir=tmp, console_registry_dir=f"{tmp}/registry", console_bot_enabled=True)
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
        self.assertEqual(sorted(services["bot"]["networks"]), ["egress"])


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
