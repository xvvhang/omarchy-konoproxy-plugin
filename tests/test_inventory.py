"""Offline inventory, isolated region probes and narrow service-control policy."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import test_backend as fixtures
import backend as b
import inventory as inventory_model
import privileged as p


class InventoryTests(unittest.TestCase):
    setUp = fixtures.ManagerTests.setUp
    imported = fixtures.ManagerTests.imported
    def test_inventory_stays_available_after_core_stops(self):
        item = self.imported(True)
        snapshot = b.status()
        self.assertFalse(snapshot["running"])
        self.assertEqual(snapshot["profileGroups"][0]["id"], item["id"])
        self.assertEqual(snapshot["profileGroups"][0]["nodes"][0]["protocol"], "SS")
        self.assertNotIn("not-a-real-password", json.dumps(snapshot))

    def test_profiles_are_distinct_even_with_identical_node_names(self):
        first = self.imported(True)
        b.dispatch({"action": "import", "name": "Backup", "path": str(self.input)})
        groups = b.status()["profileGroups"]
        self.assertEqual(len(groups), 2)
        self.assertNotEqual(groups[0]["nodes"][0]["key"], groups[1]["nodes"][0]["key"])

    def test_provider_cache_is_read_while_core_is_off(self):
        item = self.imported(True)
        source = fixtures.PROFILE + "\nproxy-providers:\n  Remote: {type: http, url: https://example.com/nodes}\n"
        b.atomic(b.source_path(item["id"]), source)
        cache = b.DATA / "providers" / item["id"] / "proxy-providers-0.yaml"
        b.atomic(cache, "proxies:\n  - {name: Cached node, type: vless, server: example.com}\n")
        nodes = b.status()["profileGroups"][0]["nodes"]
        self.assertEqual([node["name"] for node in nodes], ["Test SS", "Cached node"])

    def test_missing_provider_cache_is_reported_without_inventing_nodes(self):
        item = self.imported(True)
        b.atomic(b.source_path(item["id"]), fixtures.PROFILE + "\nproxy-providers:\n  Remote: {type: http, url: https://example.com/nodes}\n")
        section = b.status()["profileGroups"][0]
        self.assertTrue(section["warning"])
        self.assertEqual(len(section["nodes"]), 1)

    def test_region_is_not_guessed_from_node_name(self):
        self.input.write_text(fixtures.PROFILE.replace("Test SS", "JP Tokyo"))
        self.imported(True)
        self.assertEqual(b.status()["profileGroups"][0]["nodes"][0]["regionCode"], "")

    def test_verified_region_cache_survives_core_stop(self):
        item = self.imported(True)
        _, nodes, _ = inventory_model.read_profile(b, item)
        b.atomic(b.DATA / "regions.json", json.dumps({inventory_model.fingerprint(nodes[0]): {"code": "GB", "updated": 1}}))
        node = b.status()["profileGroups"][0]["nodes"][0]
        self.assertEqual(node["regionCode"], "UK")
        self.assertTrue(node["regionStale"])

    def test_exit_probe_is_explicit_and_cached_without_credentials(self):
        item = self.imported(True)
        with patch.object(inventory_model, "probe_region", return_value="JP") as probe:
            self.assertFalse(b.response({"action": "resolveRegions", "profile": item["id"]})["ok"])
            probe.assert_not_called()
            result = b.dispatch({"action": "resolveRegions", "profile": item["id"], "confirmed": True})
        self.assertIn("1 of 1", result["message"])
        stored = (b.DATA / "regions.json").read_text()
        self.assertNotIn("not-a-real-password", stored)
        self.assertEqual(b.status()["profileGroups"][0]["nodes"][0]["regionCode"], "JP")

    def test_failed_exit_probe_does_not_erase_cached_region(self):
        item = self.imported(True)
        _, nodes, _ = inventory_model.read_profile(b, item)
        b.atomic(b.DATA / "regions.json", json.dumps({inventory_model.fingerprint(nodes[0]): {"code": "US", "updated": 1}}))
        with patch.object(inventory_model, "probe_region", side_effect=OSError("Unavailable")):
            b.dispatch({"action": "resolveRegions", "profile": item["id"], "confirmed": True})
        self.assertEqual(b.status()["profileGroups"][0]["nodes"][0]["regionCode"], "US")

    def test_stopped_core_selection_is_saved_without_starting_it(self):
        item = self.imported(True)
        b.dispatch({"action": "selectNode", "profile": item["id"], "name": "Test SS"})
        self.privileged.assert_not_called()
        self.assertTrue(b.state()["profiles"][0]["selectionPending"])
        self.assertTrue(b.status()["profileGroups"][0]["nodes"][0]["selected"])

    def test_invalid_offline_node_cannot_be_selected(self):
        item = self.imported(True)
        self.assertFalse(b.response({"action": "selectNode", "profile": item["id"], "name": "Missing"})["ok"])

    def test_automatic_policy_can_be_prepared_while_stopped(self):
        item = self.imported(True)
        self.assertEqual(b.status()["profileGroups"][0]["automaticGroup"], "JMS Auto")
        b.dispatch({"action": "selectNode", "profile": item["id"], "name": "JMS Auto"})
        self.assertTrue(b.state()["profiles"][0]["selectionPending"])
        self.assertEqual(b.state()["profiles"][0]["selectedNode"], "JMS Auto")

    def test_cached_live_selection_is_retained_offline(self):
        self.imported(True)
        live = {"GLOBAL": {"type": "Selector", "all": ["Test SS"], "now": "Test SS"}}
        s = b.state()
        s["mode"] = "global"
        inventory_model.sections(b, s, live)
        self.assertTrue(b.status()["profileGroups"][0]["nodes"][0]["selected"])

    def test_control_policy_requires_explicit_consent(self):
        self.assertFalse(b.response({"action": "authorizeControl"})["ok"])
        self.privileged.assert_not_called()

    def test_authorized_control_uses_only_fixed_systemctl_operation(self):
        b.atomic(b.OWNER_FILE, json.dumps({"uid": os.getuid(), "passwordFreeControl": True}))
        with patch.object(b, "run") as run:
            b.service_control("stop")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/systemctl", "--no-ask-password", "stop", b.UNIT])
        self.privileged.assert_not_called()

    def test_legacy_install_keeps_authenticated_control(self):
        b.service_control("start")
        self.privileged.assert_called_once_with("start")

    def test_scoped_latency_keys_do_not_collide_across_profiles(self):
        item = self.imported(True)
        self.alive.return_value = {"version": "test"}
        with patch.object(b, "api", return_value={"delay": 42}):
            result = b.dispatch({"action": "delay", "profile": item["id"], "name": "Test SS"})
        self.assertEqual(result["delays"], {item["id"] + ":Test SS": 42})

    def test_region_probe_observes_exit_location_and_cleans_up(self):
        process = MagicMock()
        process.poll.return_value = None
        connection = MagicMock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b"ip=192.0.2.1\ncolo=NRT\nloc=GB\n"
        config_files = []
        def spawn(args, **kwargs):
            config = Path(args[-1])
            config_files.append(config)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertFalse(json.loads(config.read_text())["tun"]["enable"])
            return process
        with patch.object(inventory_model.subprocess, "Popen", side_effect=spawn), \
                patch.object(inventory_model.socket, "create_connection", return_value=MagicMock()), \
                patch.object(inventory_model.http.client, "HTTPSConnection", return_value=connection) as https:
            code = inventory_model.probe_region(b, {"name": "Probe", "type": "ss"})
        self.assertEqual(code, "UK")
        connection.set_tunnel.assert_called_once_with("www.cloudflare.com", 443)
        self.assertTrue(https.call_args.kwargs["context"].check_hostname)
        process.terminate.assert_called_once()
        connection.close.assert_called_once()
        self.assertFalse(config_files[0].parent.exists())


class RegionProbeTests(unittest.TestCase):
    def test_region_normalization(self):
        self.assertEqual(inventory_model.normalize_region("GB"), "UK")
        self.assertEqual(inventory_model.normalize_region("jp"), "JP")
        for value in ("", "XX", "T1", "United States"):
            self.assertEqual(inventory_model.normalize_region(value), "")

    def test_probe_config_never_enables_tun_or_an_external_controller(self):
        config = inventory_model.probe_config({"name": "Node", "type": "ss"}, 18000)
        self.assertFalse(config["tun"]["enable"])
        self.assertEqual(config["bind-address"], "127.0.0.1")
        self.assertNotIn("external-controller", config)
        self.assertEqual(config["rules"], ["MATCH,Node"])

    def test_chained_node_is_not_silently_probed_as_a_different_route(self):
        with self.assertRaises(ValueError):
            inventory_model.probe_config({"name": "Node", "dialer-proxy": "Other"}, 18000)

    def test_cache_fingerprint_changes_with_server_credentials(self):
        self.assertNotEqual(inventory_model.fingerprint({"name": "A", "password": "one"}),
                            inventory_model.fingerprint({"name": "A", "password": "two"}))

    def test_global_provider_selection_can_follow_a_policy_group(self):
        inventory = {"GLOBAL": {"type": "Selector", "all": ["Provider group"]},
                     "Provider group": {"type": "Selector", "all": ["Provider node"]}}
        self.assertEqual(inventory_model.select_path(inventory, "global", "Provider node"),
                         [("GLOBAL", "Provider group"), ("Provider group", "Provider node")])

    def test_policy_cycles_are_bounded(self):
        inventory = {"GLOBAL": {"type": "Selector", "all": ["Cycle"]},
                     "Cycle": {"type": "Selector", "all": ["GLOBAL"]}}
        self.assertEqual(inventory_model.select_path(inventory, "global", "Missing"), [])


class ControlPolicyTests(unittest.TestCase):
    def test_control_upgrade_installs_only_managed_files_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root, rules = Path(directory) / "root", Path(directory) / "rules"
            root.mkdir()
            rules.mkdir()
            user = types.SimpleNamespace(pw_uid=1000, pw_name="test-user")
            owner = {"uid": 1000, "version": "test"}
            (root / "owner.json").write_text(json.dumps(owner))
            (root / "privileged.py").write_text("old helper")
            with patch.object(p, "ROOT", root), patch.object(p, "POLKIT", rules), patch.object(p, "command") as command:
                p.configure_control(user, owner)
            command.assert_not_called()
            self.assertTrue(json.loads((root / "owner.json").read_text())["passwordFreeControl"])
            self.assertTrue((rules / "49-konoproxy-1000.rules").exists())
            self.assertEqual((root / "privileged.py").stat().st_mode & 0o777, 0o644)

    def test_failed_control_upgrade_rolls_back_rule_and_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root, rules = Path(directory) / "root", Path(directory) / "rules"
            root.mkdir()
            rules.mkdir()
            user = types.SimpleNamespace(pw_uid=1000, pw_name="test-user")
            owner = {"uid": 1000}
            (root / "owner.json").write_text(json.dumps(owner))
            (root / "privileged.py").write_text("old helper")
            original_write = p.root_write
            def write(path, content, mode):
                if path.name == "owner.json":
                    raise OSError("Disk full")
                original_write(path, content, mode)
            with patch.object(p, "ROOT", root), patch.object(p, "POLKIT", rules), patch.object(p, "root_write", side_effect=write):
                with self.assertRaises(OSError):
                    p.configure_control(user, owner)
            self.assertFalse((rules / "49-konoproxy-1000.rules").exists())
            self.assertEqual((root / "privileged.py").read_text(), "old helper")
            self.assertEqual(json.loads((root / "owner.json").read_text()), owner)

    @unittest.skipUnless(shutil.which("node"), "Node is optional for polkit rule evaluation")
    def test_policy_accepts_only_local_active_owner_and_exact_unit_verbs(self):
        user = types.SimpleNamespace(pw_uid=1000, pw_name="test-user")
        script = """
const vm = require('node:vm');
const fs = require('node:fs');
let rule;
vm.runInNewContext(fs.readFileSync(0, 'utf8'), {polkit: {addRule: fn => rule = fn, Result: {YES: 'yes'}}});
function allowed(user, unit, verb, local=true, active=true, id='org.freedesktop.systemd1.manage-units') {
  return rule({id, lookup: key => key === 'unit' ? unit : verb}, {user, local, active}) === 'yes';
}
const assert = require('node:assert/strict');
for (const verb of ['start', 'stop', 'restart']) assert(allowed('test-user', 'konoproxy-1000.service', verb));
for (const verb of ['enable', 'disable', 'reload', 'kill', 'set-property']) assert(!allowed('test-user', 'konoproxy-1000.service', verb));
assert(!allowed('other', 'konoproxy-1000.service', 'start'));
assert(!allowed('test-user', 'sshd.service', 'start'));
assert(!allowed('test-user', 'konoproxy-1001.service', 'start'));
assert(!allowed('test-user', 'konoproxy-1000.service', 'start', false));
assert(!allowed('test-user', 'konoproxy-1000.service', 'start', true, false));
assert(!allowed('test-user', 'konoproxy-1000.service', 'start', true, true, 'org.freedesktop.systemd1.manage-unit-files'));
"""
        result = subprocess.run(["node", "-e", script], input=p.control_rule(user), text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
