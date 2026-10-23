"""Isolated tests: no administrator access, production services or proxy changes."""

import json
import os
from pathlib import Path
import stat
import select
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend as b
import privileged as p

PROFILE = """proxies:
  - name: Test SS
    type: ss
    server: 127.0.0.1
    port: 8388
    cipher: aes-128-gcm
    password: not-a-real-password
proxy-groups:
  - name: JMS
    type: select
    proxies: [Test SS, JMS Auto]
  - name: JMS Auto
    type: url-test
    proxies: [Test SS]
    url: https://www.gstatic.com/generate_204
    interval: 300
rules:
  - MATCH,JMS
"""


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="konoproxy-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.core = self.root / "mihomo"
        self.core.touch()
        self.helper = self.root / "privileged.py"
        self.helper.touch()
        for name, value in (("DATA", self.data), ("CORE", self.core), ("HELPER", self.helper), ("SERVICE_FILE", self.root / "test.service"), ("OWNER_FILE", self.root / "owner.json")):
            task = patch.object(b, name, value)
            task.start()
            self.addCleanup(task.stop)
        for name, value in (("validate", None), ("alive", None), ("privileged", None), ("check_tun_conflicts", None)):
            task = patch.object(b, name, return_value=value)
            setattr(self, name, task.start())
            self.addCleanup(task.stop)
        self.input = self.root / "source.yaml"
        self.input.write_text(PROFILE)

    def imported(self, active=False):
        b.dispatch({"action": "import", "name": "JMS", "path": str(self.input)})
        item = b.state()["profiles"][0]
        if active:
            b.dispatch({"action": "activate", "id": item["id"]})
        return item

    def test_empty_status_does_not_install_or_write_data(self):
        self.assertFalse(b.status()["installed"])
        self.privileged.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_install_requires_explicit_consent(self):
        self.assertFalse(b.response({"action": "setup"})["ok"])
        self.privileged.assert_not_called()

    def test_import_does_not_automatically_activate(self):
        item = self.imported()
        self.assertEqual(b.state()["active"], "")
        self.assertEqual(b.source_path(item["id"]).read_text(), PROFILE)
        self.assertFalse((self.data / "runtime.yaml").exists())

    def test_activation_keeps_source_separate(self):
        item = self.imported(True)
        runtime = b.parse_yaml((self.data / "runtime.yaml").read_text())
        self.assertIn("secret", runtime)
        self.assertNotIn("secret", b.parse_yaml(b.source_path(item["id"]).read_text()))
        self.assertEqual(runtime["proxy-groups"][1]["name"], "JMS Auto")

    def test_files_are_private(self):
        item = self.imported(True)
        for file in (self.data / "state.json", self.data / "controller-secret", self.data / "runtime.yaml", b.source_path(item["id"])):
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode), 0o700)

    def test_legacy_subscription_is_rejected(self):
        self.input.write_text("dm1lc3M6Ly9ub3QtYS1yZWFsLW5vZGU=")
        self.assertFalse(b.response({"action": "import", "name": "Old", "path": str(self.input)})["ok"])
        self.assertEqual(b.state()["profiles"], [])

    def test_recursive_yaml_is_rejected(self):
        with self.assertRaises(b.UserError):
            b.parse_yaml("proxies: &loop [*loop]")

    def test_yaml_object_constructor_is_rejected(self):
        with self.assertRaises(b.UserError):
            b.parse_yaml("!!python/object/apply:os.system ['false']")

    def test_exponential_alias_expansion_is_bounded(self):
        text = "a: &a [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]\n"
        for level in range(1, 8):
            previous = chr(ord("a") + level - 1)
            current = chr(ord("a") + level)
            text += current + ": &" + current + " [" + ", ".join(["*" + previous] * 10) + "]\n"
        with self.assertRaises(b.UserError):
            b.parse_yaml(text)

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(b.UserError):
            b.source_path("../../etc/passwd")

    def test_subscription_cannot_change_control_endpoints(self):
        s = b.state()
        config = b.parse_yaml(b.compile_config(s, {"id": "a" * 32}, PROFILE + "\nexternal-controller: 0.0.0.0:9090\nsecret: unsafe\nallow-lan: true\nexternal-ui-url: https://example.com/ui.zip\nlisteners: [{type: socks, port: 1080}]\ntun: {enable: true}\n"))
        self.assertEqual(config["external-controller"], "127.0.0.1:17990")
        self.assertFalse(config["allow-lan"])
        self.assertFalse(config["tun"]["enable"])
        self.assertNotEqual(config["secret"], "unsafe")
        self.assertNotIn("listeners", config)
        self.assertNotIn("external-ui-url", config)

    def test_provider_paths_are_rewritten(self):
        source = PROFILE + "\nproxy-providers:\n  Remote:\n    type: http\n    url: https://example.com/nodes\n    path: /etc/unsafe.yaml\n"
        config = b.parse_yaml(b.compile_config(b.state(), {"id": "a" * 32}, source))
        self.assertTrue(config["proxy-providers"]["Remote"]["path"].startswith(str(self.data)))

    def test_local_provider_is_rejected(self):
        source = PROFILE + "\nproxy-providers:\n  Local: {type: file, path: /etc/passwd}\n"
        with self.assertRaises(b.UserError):
            b.compile_config(b.state(), {"id": "a" * 32}, source)

    def test_http_and_file_subscription_urls_are_rejected(self):
        for value in ("http://example.com/config", "file:///etc/passwd", "https://user:pass@example.com/config"):
            with self.subTest(value=value), self.assertRaises(b.UserError):
                b.check_url(value)

    def test_https_downgrade_redirect_is_rejected(self):
        with self.assertRaises(b.UserError):
            b.SecureRedirect().redirect_request(None, None, 302, "", {}, "http://example.com/config")

    def test_invalid_update_retains_source_and_runtime(self):
        item = self.imported(True)
        s = b.state()
        s["profiles"][0]["url"] = "https://example.com/private-token"
        b.save(s)
        before = (self.data / "runtime.yaml").read_text()
        self.validate.side_effect = b.UserError("Invalid configuration.")
        with patch.object(b, "download", return_value=PROFILE.replace("Test SS", "Changed")):
            result = b.response({"action": "update", "id": item["id"]})
        self.assertFalse(result["ok"])
        self.assertEqual(b.source_path(item["id"]).read_text(), PROFILE)
        self.assertEqual((self.data / "runtime.yaml").read_text(), before)
        self.assertFalse((self.data / "candidate.yaml").exists())

    def test_failed_controller_apply_rolls_back(self):
        item = self.imported(True)
        before = (self.data / "runtime.yaml").read_text()
        self.alive.return_value = {"version": "test"}
        with patch.object(b, "api", side_effect=[b.UserError("Rejected."), {}]) as api:
            result = b.response({"action": "mode", "value": "global"})
        self.assertFalse(result["ok"])
        self.assertEqual(api.call_count, 2)
        self.assertEqual((self.data / "runtime.yaml").read_text(), before)
        self.assertEqual(b.state()["mode"], "rule")

    def test_persistence_failure_after_apply_restores_runtime(self):
        self.imported(True)
        before = (self.data / "runtime.yaml").read_text()
        self.alive.return_value = {"version": "test"}
        with patch.object(b, "api", return_value={}) as api, patch.object(b, "save", side_effect=OSError("Disk full")):
            result = b.response({"action": "mode", "value": "global"})
        self.assertFalse(result["ok"])
        self.assertEqual(api.call_count, 2)
        self.assertEqual((self.data / "runtime.yaml").read_text(), before)
        self.assertEqual(b.state()["mode"], "rule")

    def test_import_persistence_failure_does_not_leave_orphaned_source(self):
        with patch.object(b, "save", side_effect=OSError("Disk full")):
            answer = b.response({"action": "import", "name": "Test", "path": str(self.input)})
        self.assertFalse(answer["ok"])
        self.assertEqual(list((self.data / "profiles").glob("*.yaml")), [])
        self.assertEqual(b.state()["profiles"], [])

    def test_local_rules_survive_subscription_update(self):
        item = self.imported(True)
        b.dispatch({"action": "rules", "text": "DOMAIN-SUFFIX,example.com,DIRECT"})
        s = b.state()
        s["profiles"][0]["url"] = "https://example.com/config"
        b.save(s)
        with patch.object(b, "download", return_value=PROFILE):
            b.dispatch({"action": "update", "id": item["id"]})
        self.assertEqual(b.parse_yaml((self.data / "runtime.yaml").read_text())["rules"][0], "DOMAIN-SUFFIX,example.com,DIRECT")

    def test_invalid_rules_do_not_replace_persisted_rules(self):
        self.imported(True)
        self.validate.side_effect = b.UserError("Invalid rule.")
        result = b.response({"action": "rules", "text": "INVALID,example.com,Missing"})
        self.assertFalse(result["ok"])
        self.assertEqual(b.state()["profiles"][0]["rules"], [])

    def test_rule_provider_is_compiled_before_subscription_rules(self):
        self.imported(True)
        b.dispatch({"action": "provider", "url": "https://example.com/rules", "target": "JMS", "behavior": "domain", "format": "mrs"})
        config = b.parse_yaml((self.data / "runtime.yaml").read_text())
        self.assertEqual(config["rules"][0], "RULE-SET,konoproxy-0,JMS")
        self.assertEqual(config["rule-providers"]["konoproxy-0"]["format"], "mrs")

    def test_classical_mrs_is_rejected(self):
        self.imported(True)
        self.assertFalse(b.response({"action": "provider", "url": "https://example.com/rules", "target": "JMS", "behavior": "classical", "format": "mrs"})["ok"])

    def test_cannot_delete_active_profile(self):
        item = self.imported(True)
        self.assertFalse(b.response({"action": "delete", "id": item["id"]})["ok"])
        self.assertTrue(b.source_path(item["id"]).exists())

    def test_rename_and_delete_inactive_profile(self):
        item = self.imported()
        b.dispatch({"action": "rename", "id": item["id"], "name": "Backup"})
        self.assertEqual(b.state()["profiles"][0]["name"], "Backup")
        b.dispatch({"action": "delete", "id": item["id"]})
        self.assertFalse(b.source_path(item["id"]).exists())

    def test_status_never_returns_subscription_credentials(self):
        self.imported()
        s = b.state()
        s["profiles"][0]["url"] = "https://example.com/super-secret-token"
        b.save(s)
        self.assertNotIn("super-secret-token", json.dumps(b.status()))

    def test_node_names_are_url_encoded(self):
        with patch.object(b, "api", return_value={} ) as api:
            b.dispatch({"action": "select", "group": "JMS / Primary", "node": "Node"})
        self.assertEqual(api.call_args.args[1], "/proxies/JMS%20%2F%20Primary")

    def test_latency_test_uses_http_probe_not_ping(self):
        with patch.object(b, "api", return_value={"delay": 42}) as api:
            answer = b.dispatch({"action": "delay", "name": "Test SS"})
        self.assertIn("42 ms", answer["message"])
        self.assertEqual(answer["delays"], {"Test SS": 42})
        self.assertIn("generate_204", api.call_args.args[1])

    def test_provider_node_uses_its_healthcheck_endpoint(self):
        replies = [
            {"proxies": {}},
            {"providers": {"Remote / Main": {"proxies": [{"name": "Node A"}]}}},
            {"delay": 17},
        ]
        with patch.object(b, "api", side_effect=replies) as api:
            b.dispatch({"action": "delay", "name": "Node A"})
        self.assertTrue(api.call_args.args[1].startswith("/providers/proxies/Remote%20%2F%20Main/Node%20A/healthcheck?"))

    def test_static_node_takes_precedence_over_provider_collision(self):
        replies = [
            {"proxies": {"Node": {"name": "Node", "type": "Shadowsocks"}}},
            {"providers": {"Remote": {"proxies": [{"name": "Node"}]}}},
        ]
        with patch.object(b, "api", side_effect=replies):
            inventory = b.proxy_inventory(b.state())
        self.assertTrue(b.latency_route(inventory, "Node", "query").startswith("/proxies/Node/delay?"))

    def test_uninstall_requires_consent(self):
        self.assertFalse(b.response({"action": "uninstall", "removeCore": True})["ok"])
        self.privileged.assert_not_called()

    def test_keep_core_does_not_stop_or_remove_service(self):
        with patch.object(b, "run") as run:
            b.dispatch({"action": "uninstall", "confirmed": True, "removeCore": False, "removePlugin": True})
        self.privileged.assert_not_called()
        self.assertEqual(run.call_args.args[0][0], "systemd-run")

    def test_uninstall_before_setup_needs_no_privileged_operation(self):
        self.core.unlink()
        self.helper.unlink()
        with patch.object(b, "run") as run:
            answer = b.response({"action": "uninstall", "confirmed": True, "removeCore": True, "removePlugin": True})
        self.assertTrue(answer["ok"])
        self.privileged.assert_not_called()
        run.assert_called_once()

    def test_core_removed_before_plugin(self):
        events = []
        self.privileged.side_effect = lambda *args, **kwargs: events.append("core")
        with patch.object(b, "system_proxy", side_effect=lambda *args: events.append("proxy")), patch.object(b, "run", side_effect=lambda *args, **kwargs: events.append("plugin")):
            b.dispatch({"action": "uninstall", "confirmed": True, "removeCore": True, "removePlugin": True})
        self.assertEqual(events, ["proxy", "core", "plugin"])

    def test_rejected_authorization_keeps_plugin(self):
        self.privileged.side_effect = b.UserError("Authorization denied.")
        with patch.object(b, "run") as run:
            answer = b.response({"action": "uninstall", "confirmed": True, "removeCore": True, "removePlugin": True})
        self.assertFalse(answer["ok"])
        run.assert_not_called()

    def test_proxy_restore_does_not_overwrite_later_external_changes(self):
        b.atomic(self.data / "system-proxy.json", json.dumps([
            {"schema": "org.gnome.system.proxy", "key": "mode", "before": "'none'", "ours": "'manual'"},
        ]))
        with patch.object(b, "run", return_value="'auto'") as run:
            b.system_proxy(b.state(), False)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.data / "system-proxy.json").exists())

    def test_invalid_interval_is_rejected(self):
        self.assertFalse(b.response({"action": "interval", "value": -1})["ok"])

    def test_unknown_errors_do_not_leak_secrets(self):
        with patch.object(b, "dispatch", side_effect=RuntimeError("private-token")):
            self.assertNotIn("private-token", json.dumps(b.response({})))

    def fake_proxy(self, s, enabled):
        path = self.data / "system-proxy.json"
        if enabled:
            b.atomic(path, "[]")
        else:
            path.unlink(missing_ok=True)

    def test_capture_system_to_tun_is_exclusive(self):
        self.imported(True)
        self.alive.return_value = {"version": "test"}
        self.fake_proxy(b.state(), True)
        with patch.object(b, "system_proxy", side_effect=self.fake_proxy) as proxy, patch.object(b, "api", return_value={}):
            b.dispatch({"action": "capture", "value": "tun"})
        self.assertTrue(b.state()["tun"])
        self.assertFalse((self.data / "system-proxy.json").exists())
        self.assertEqual([call.args[1] for call in proxy.call_args_list], [False])

    def test_capture_tun_to_system_disables_tunnel_first(self):
        self.imported(True)
        self.alive.return_value = {"version": "test"}
        with patch.object(b, "api", return_value={}), patch.object(b, "system_proxy", side_effect=self.fake_proxy):
            b.dispatch({"action": "capture", "value": "tun"})
            def proxy(s, enabled):
                if enabled:
                    self.assertFalse(b.state()["tun"])
                self.fake_proxy(s, enabled)
            with patch.object(b, "system_proxy", side_effect=proxy):
                b.dispatch({"action": "capture", "value": "system"})
        self.assertFalse(b.state()["tun"])
        self.assertTrue((self.data / "system-proxy.json").exists())

    def test_capture_off_preserves_proxy_mode(self):
        self.imported(True)
        s = b.state()
        s.update(tun=True, mode="global")
        b.save(s)
        self.fake_proxy(s, True)
        with patch.object(b, "system_proxy", side_effect=self.fake_proxy):
            b.dispatch({"action": "capture", "value": "off"})
        self.assertFalse(b.state()["tun"])
        self.assertEqual(b.state()["mode"], "global")
        self.assertFalse((self.data / "system-proxy.json").exists())
        self.privileged.assert_not_called()

    def test_failed_tun_switch_restores_previous_system_proxy(self):
        self.imported(True)
        self.fake_proxy(b.state(), True)
        self.alive.return_value = {"version": "test"}
        self.validate.side_effect = b.UserError("Invalid candidate.")
        with patch.object(b, "system_proxy", side_effect=self.fake_proxy):
            result = b.response({"action": "capture", "value": "tun"})
        self.assertFalse(result["ok"])
        self.assertFalse(b.state()["tun"])
        self.assertTrue((self.data / "system-proxy.json").exists())

    def test_failed_system_switch_restores_previous_tun(self):
        self.imported(True)
        self.alive.return_value = {"version": "test"}
        with patch.object(b, "api", return_value={}), patch.object(b, "system_proxy", side_effect=self.fake_proxy):
            b.dispatch({"action": "capture", "value": "tun"})
            def proxy(s, enabled):
                if enabled:
                    raise b.UserError("Desktop proxy unavailable.")
                self.fake_proxy(s, enabled)
            with patch.object(b, "system_proxy", side_effect=proxy):
                result = b.response({"action": "capture", "value": "system"})
        self.assertFalse(result["ok"])
        self.assertTrue(b.state()["tun"])
        self.assertTrue(b.parse_yaml((self.data / "runtime.yaml").read_text())["tun"]["enable"])

    def test_capture_requires_running_core_and_profile(self):
        for value in ("tun", "system"):
            self.assertFalse(b.response({"action": "capture", "value": value})["ok"])

    def test_direct_is_no_longer_a_proxy_mode(self):
        self.imported(True)
        self.assertFalse(b.response({"action": "mode", "value": "direct"})["ok"])

    def test_latency_failure_is_a_transient_timeout_result(self):
        with patch.object(b, "proxy_inventory", return_value={}), patch.object(b, "api", side_effect=b.UserError("Timeout")):
            result = b.dispatch({"action": "delay", "name": "Node", "latencySession": 7})
        self.assertEqual(result["delays"], {"Node": 0})
        self.assertEqual(result["latencySession"], 7)
        self.assertFalse((self.data / "state.json").exists())

    def test_group_latency_returns_results_and_session(self):
        def api(s, route, *args, **kwargs):
            return {"delay": 21} if "/delay?" in route else {"all": ["A", "B"]}
        with patch.object(b, "proxy_inventory", return_value={}), patch.object(b, "api", side_effect=api):
            result = b.dispatch({"action": "delayGroup", "name": "Group", "latencySession": 3})
        self.assertEqual(result["delays"], {"A": 21, "B": 21})
        self.assertEqual(result["latencySession"], 3)

    def test_status_does_not_reintroduce_core_latency_history(self):
        self.alive.return_value = {"version": "test"}
        inventory = {"JMS": {"type": "Selector", "now": "JP Tokyo", "all": ["JP Tokyo"]},
                     "JP Tokyo": {"type": "VLESS", "history": [{"delay": 42}]}}
        with patch.object(b, "api", return_value={}), patch.object(b, "proxy_inventory", return_value=inventory):
            node = b.status()["groups"][0]["nodes"][0]
        self.assertEqual(node["country"], "Unknown")
        self.assertEqual(node["protocol"], "VLESS")
        self.assertNotIn("delay", node)


class MetadataTests(unittest.TestCase):
    def test_flags_do_not_prove_an_exit_country(self):
        result = b.node_metadata("\U0001f1fa\U0001f1f8 via Japan", {"type": "Shadowsocks"})
        self.assertEqual(result["countryCode"], "")
        self.assertEqual(result["protocol"], "SS")

    def test_names_are_not_used_as_geolocation(self):
        for name in ("Hong Kong 01", "Los Angeles s1", "\u9999\u6e2f 01", "Tokyo 01"):
            self.assertEqual(b.node_metadata(name, {"type": "VMess"})["countryCode"], "")

    def test_unknown_names_do_not_invent_a_country(self):
        self.assertEqual(b.node_metadata("s1", {"type": "VLESS"})["country"], "Unknown")


class PrivilegedTests(unittest.TestCase):
    def test_root_without_pkexec_identity_is_rejected(self):
        with patch.object(os, "geteuid", return_value=0), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                p.identity()

    def test_helper_rejects_arbitrary_commands(self):
        user = types.SimpleNamespace(pw_uid=1000, pw_gid=1000)
        with patch.object(p, "identity", return_value=user), patch.object(p, "command") as command:
            with self.assertRaises(ValueError):
                p.main(["shell", "id"])
        command.assert_not_called()

    def test_unit_has_bounded_capabilities_and_no_root_core(self):
        unit = p.service_text(types.SimpleNamespace(pw_uid=1000, pw_gid=1000), "/home/test/.local/share/konoproxy", False)
        self.assertIn("User=1000", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW", unit)
        self.assertIn("ExecStopPost=", unit)
        self.assertNotIn("CAP_SYS_ADMIN", unit)

    def test_pinned_manifest_matches_installer(self):
        manifest = json.loads((Path(b.__file__).parent / "release.json").read_text())
        self.assertEqual(manifest["version"], p.VERSION)
        for architecture, (_, digest) in p.ASSETS.items():
            self.assertEqual(manifest["assets"][architecture]["sha256"], digest)

    def test_cleanup_targets_only_owned_table_and_interface(self):
        with patch.object(p, "command", return_value=types.SimpleNamespace(returncode=1)) as command:
            p.cleanup_network(1000)
        calls = [call.args[0] for call in command.call_args_list]
        self.assertIn(["/usr/bin/ip", "link", "delete", "kp1000"], calls)
        self.assertFalse(any("main" in call or "iptables" in call or "nft" in call for call in calls))

    def test_symlink_data_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "actual").mkdir()
            (home / "konoproxy").symlink_to(home / "actual")
            user = types.SimpleNamespace(pw_dir=directory, pw_uid=os.getuid())
            with self.assertRaises(ValueError):
                p.safe_data(str(home / "konoproxy"), user)


class RepositoryTests(unittest.TestCase):
    def test_no_chinese_outside_dedicated_readme(self):
        root = Path(__file__).resolve().parents[1]
        for file in root.rglob("*"):
            if not file.is_file() or file.name == "README.zh-CN.md" or file.suffix == ".png":
                continue
            if {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}.intersection(file.relative_to(root).parts):
                continue
            text = file.read_text()
            self.assertFalse(any("\u4e00" <= character <= "\u9fff" for character in text), str(file))


class BridgeTests(unittest.TestCase):
    def test_burst_requests_are_not_lost_to_stdin_buffering(self):
        with tempfile.TemporaryDirectory(prefix="konoproxy-bridge-") as directory:
            environment = dict(os.environ, XDG_DATA_HOME=directory)
            script = "import backend; from pathlib import Path; backend.CORE = Path('/nonexistent-konoproxy-test-core'); backend.serve()"
            process = subprocess.Popen([sys.executable, "-B", "-c", script],
                                       cwd=Path(b.__file__).parent, env=environment,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                process.stdin.write(b'{"action":"visible","value":true}\n{"action":"interval","value":0}\n')
                process.stdin.flush()
                output = b""
                deadline = time.monotonic() + 5
                found = False
                while time.monotonic() < deadline:
                    readable, _, _ = select.select([process.stdout], [], [], 0.1)
                    if not readable:
                        continue
                    output += os.read(process.stdout.fileno(), 65536)
                    while b"\n" in output:
                        line, output = output.split(b"\n", 1)
                        event = json.loads(line)
                        if event.get("event") == "result" and event.get("ok"):
                            found = True
                    if found:
                        break
                self.assertTrue(found, "The second request must be processed without a third write.")
                stored = json.loads((Path(directory) / "konoproxy/state.json").read_text())
                self.assertEqual(stored["interval"], 0)
            finally:
                process.terminate()
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()


if __name__ == "__main__":
    unittest.main()
