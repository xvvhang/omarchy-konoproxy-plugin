"""Session refreshes use no real network, services or administrator operations."""

import contextlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_backend as fixtures
import backend as b
import inventory
import probing


class PanelProbeTests(unittest.TestCase):
    setUp = fixtures.ManagerTests.setUp
    imported = fixtures.ManagerTests.imported

    def test_refresh_runs_once_per_open_and_reuses_identical_node_configs(self):
        self.imported(True)
        b.dispatch({"action": "import", "name": "Backup", "path": str(self.input)})
        events = []
        with patch.object(inventory, "probe_node", return_value={"regionCode": "JP", "delay": 42}) as probe:
            with probing.PanelProbes(b, events.append) as runner:
                runner.set_visible(True, 1)
                for future in runner.futures:
                    future.result(timeout=5)
                runner.set_visible(True, 1)
                probe.assert_called_once()
                self.assertEqual(events[-1]["completed"], 1)
                sections = b.status()["profileGroups"]
                self.assertEqual(sections[0]["nodes"][0]["probeId"], sections[1]["nodes"][0]["probeId"])
                self.assertEqual(sections[0]["nodes"][0]["regionCode"], "JP")
                self.assertEqual(list(events[-1]["delays"].values()), [42])
                runner.set_visible(False, 2)
                runner.set_visible(True, 3)
                for future in runner.futures:
                    future.result(timeout=5)
                self.assertEqual(probe.call_count, 2)

    def test_close_discards_in_flight_results_even_if_probe_ignores_cancellation(self):
        self.imported(True)
        entered, release = threading.Event(), threading.Event()
        events = []
        def probe(*args):
            entered.set()
            release.wait(timeout=5)
            return {"regionCode": "US", "delay": 17}
        with patch.object(inventory, "probe_node", side_effect=probe):
            with probing.PanelProbes(b, events.append) as runner:
                try:
                    runner.set_visible(True, 1)
                    self.assertTrue(entered.wait(timeout=3))
                    futures = list(runner.futures)
                    runner.set_visible(False, 2)
                finally:
                    release.set()
                for future in futures:
                    future.result(timeout=5)
        self.assertFalse(any(event["completed"] for event in events))
        self.assertFalse((b.DATA / "regions.json").exists())

    def test_mutations_remain_available_during_background_probe(self):
        self.imported(True)
        entered, release, mutation_done = threading.Event(), threading.Event(), threading.Event()
        def probe(*args):
            entered.set()
            release.wait(timeout=5)
            return {"regionCode": "", "delay": 0}
        with patch.object(inventory, "probe_node", side_effect=probe):
            with probing.PanelProbes(b, lambda event: None) as runner:
                try:
                    runner.set_visible(True, 1)
                    self.assertTrue(entered.wait(timeout=3))
                    def mutate():
                        b.dispatch({"action": "interval", "value": 0})
                        mutation_done.set()
                    thread = threading.Thread(target=mutate, daemon=True)
                    thread.start()
                    self.assertTrue(mutation_done.wait(timeout=3))
                finally:
                    release.set()
                thread.join(timeout=5)
                for future in runner.futures:
                    future.result(timeout=5)

    def test_probe_concurrency_never_exceeds_two(self):
        item = self.imported(True)
        source = b.parse_yaml(fixtures.PROFILE)
        source["proxies"] = [dict(source["proxies"][0], name="Node " + str(index)) for index in range(6)]
        b.atomic(b.source_path(item["id"]), b.yaml_module().safe_dump(source))
        lock, release, two_entered = threading.Lock(), threading.Event(), threading.Event()
        active, maximum = 0, 0
        def probe(*args):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    two_entered.set()
            release.wait(timeout=5)
            with lock:
                active -= 1
            return {"regionCode": "US", "delay": 20}
        with patch.object(inventory, "probe_node", side_effect=probe):
            with probing.PanelProbes(b, lambda event: None) as runner:
                try:
                    runner.set_visible(True, 1)
                    self.assertTrue(two_entered.wait(timeout=3))
                finally:
                    release.set()
                for future in runner.futures:
                    future.result(timeout=5)
        self.assertEqual(maximum, 2)
        self.assertEqual(len(json.loads((b.DATA / "regions.json").read_text())), 6)

    def test_region_failure_does_not_prevent_latency_measurement(self):
        replies = [(204, b"", 21), OSError("Unavailable")]
        with patch.object(inventory, "probe_port", return_value=contextlib.nullcontext(18000)), \
                patch.object(inventory, "probe_request", side_effect=replies):
            result = inventory.probe_node(b, {"name": "Node"})
        self.assertEqual(result, {"regionCode": "", "delay": 21})

    def test_latency_failure_does_not_discard_the_region(self):
        replies = [OSError("Unavailable"), (200, b"loc=GB\n", 18)]
        with patch.object(inventory, "probe_port", return_value=contextlib.nullcontext(18000)), \
                patch.object(inventory, "probe_request", side_effect=replies):
            result = inventory.probe_node(b, {"name": "Node"})
        self.assertEqual(result, {"regionCode": "UK", "delay": 0})

    def test_latency_is_published_before_region_lookup(self):
        published = []
        def request(port, host, path):
            if host == "www.gstatic.com":
                self.assertEqual(published, [])
                return 204, b"", 25
            self.assertEqual(published, [25])
            return 200, b"loc=JP\n", 10
        with patch.object(inventory, "probe_port", return_value=contextlib.nullcontext(18000)), \
                patch.object(inventory, "probe_request", side_effect=request):
            result = inventory.probe_node(b, {"name": "Node"}, on_delay=published.append)
        self.assertEqual(result, {"regionCode": "JP", "delay": 25})

    def test_close_between_requests_prevents_a_second_network_request(self):
        cancel = threading.Event()
        def request(*args):
            cancel.set()
            return 200, b"loc=JP\n", 10
        with patch.object(inventory, "probe_port", return_value=contextlib.nullcontext(18000)), \
                patch.object(inventory, "probe_request", side_effect=request) as request_mock:
            with self.assertRaises(inventory.ProbeCancelled):
                inventory.probe_node(b, {"name": "Node"}, cancel)
        request_mock.assert_called_once()

    def test_uninstalled_core_does_not_start_background_probes(self):
        self.core.unlink()
        events = []
        with patch.object(inventory, "probe_node") as probe:
            with probing.PanelProbes(b, events.append) as runner:
                runner.set_visible(True, 1)
                self.assertEqual(runner.futures, [])
        probe.assert_not_called()
        self.assertEqual(events[-1]["total"], 0)


class PanelContractTests(unittest.TestCase):
    def test_panel_inherits_native_theme_border(self):
        source = (Path(b.__file__).parent / "ProxyPanel.qml").read_text()
        self.assertNotIn("borderSpec:", source)

    def test_tabs_switch_options_and_commands_have_distinct_components(self):
        source = (Path(b.__file__).parent / "ProxyPanel.qml").read_text()
        self.assertIn("Controls.TabBar {", source)
        self.assertIn("Controls.TabButton {", source)
        self.assertIn("ToggleSwitch {", source)
        self.assertIn("component Action: Controls.AbstractButton", source)
        self.assertIn("component Option: Button", source)
        self.assertIn("component RadioChoice: Option", source)
        home = source.split('visible: root.page === "Home"', 1)[1].split('visible: root.page === "Nodes"', 1)[0]
        self.assertNotIn("Card {", home)
        self.assertNotIn('text: "Connection"', home)
        nodes = source.split('visible: root.page === "Nodes"', 1)[1].split('visible: root.page === "Profiles"', 1)[0]
        self.assertNotIn("nodeFilter", nodes)
        self.assertNotIn("Card {", nodes)
        self.assertIn("RotationAnimator", nodes)
        for removed in ("sortToggle", "regionButton", "autoProfile", "groupTest", "testNode"):
            self.assertNotIn(removed, nodes)

    def test_profiles_and_settings_use_sections_and_selection_controls(self):
        source = (Path(b.__file__).parent / "ProxyPanel.qml").read_text()
        profiles = source.split('visible: root.page === "Profiles"', 1)[1].split('visible: root.page === "Rules"', 1)[0]
        self.assertNotIn("Card {", profiles)
        self.assertNotIn('text: "Use profile"', profiles)
        self.assertIn("Accessible.checked: chosen", profiles)
        self.assertIn('root.editProfile ? "editProfile" : "import"', profiles)
        self.assertIn("root.closeProfileForm()", source)
        settings = source.split('visible: root.page === "Settings"', 1)[1]
        self.assertNotIn("Card {", settings)
        self.assertNotRegex(settings, r"\bChoice \{")
        self.assertIn("RadioChoice {", settings)
        self.assertIn('text: "SUBSCRIPTION REFRESH"', settings)
        self.assertIn('text: "BAR CONTENT"', settings)
        self.assertIn('action: "barDisplay"', settings)
        for key in ("speed", "region"):
            self.assertIn('key: "' + key + '"', settings)
        self.assertIn('label: "Node region"', settings)
        self.assertLess(settings.index('key: "region"'), settings.index('key: "speed"'))
        for key in ("country", "capture", "mode"):
            self.assertNotIn('key: "' + key + '"', settings)
        self.assertNotIn('text: "UNINSTALL"', settings)
        self.assertLess(settings.index('text: "Uninstall..."'), settings.index('text: "SUBSCRIPTION REFRESH"'))

    def test_profile_actions_share_the_profile_container(self):
        source = (Path(b.__file__).parent / "ProxyPanel.qml").read_text()
        row = source.split("id: profileRow", 1)[1].split('visible: root.page === "Rules"', 1)[0]
        self.assertIn("implicitHeight: profileBody.implicitHeight", row)
        self.assertIn("id: profileBody", row)
        for action in ('text: "Edit..."', 'text: "Refresh"', 'text: "Delete..."'):
            self.assertIn(action, row)
        selection = row.split("id: profileSelection", 1)[1].split("Flow {", 1)[0]
        self.assertNotIn('text: "Edit..."', selection)

    def test_rules_use_plain_sections_and_an_unfilled_editor(self):
        source = (Path(b.__file__).parent / "ProxyPanel.qml").read_text()
        rules = source.split('visible: root.page === "Rules"', 1)[1].split('visible: root.page === "Settings"', 1)[0]
        self.assertNotIn("Card {", source)
        for title in ("CUSTOM RULES", "IMPORT RULES", "REMOTE RULE SETS"):
            self.assertIn('text: "' + title + '"', rules)
        self.assertIn('color: "transparent"', rules)

    def test_stop_does_not_insert_a_notice_or_change_mode_hint_height(self):
        source = (Path(b.__file__).parent / "ProxyPanel.qml").read_text()
        home = source.split('visible: root.page === "Home"', 1)[1].split('visible: root.page === "Nodes"', 1)[0]
        self.assertNotIn('value: "off"', home)
        self.assertNotIn('Hint { text: root.snapshot.capture', home)
        self.assertNotIn('Hint { text: root.snapshot.mode', home)
        self.assertNotIn('visible: !root.snapshot.running', source)

    def test_transitions_stay_inside_existing_controls(self):
        source = (Path(b.__file__).parent / "ProxyPanel.qml").read_text()
        self.assertIn("component BusyMark: Text", source)
        self.assertIn('pending: root.transition?.kind === "capture"', source)
        self.assertIn('pending: root.transition?.kind === "mode"', source)
        self.assertIn('spinning: root.transition?.kind === "power"', source)
        self.assertIn("x: Model.knobCenter(powerControl.width", source)
        self.assertIn("powerControl.knobSize - Style.space(4)", source)
        self.assertNotIn('opacity: root.transition?.kind === "power"', source)
        service = (Path(b.__file__).parent / "Service.qml").read_text()
        self.assertIn("Model.transitionConfirmed(root.transition, next)", service)
        self.assertIn("confirmationTimeout.restart()", service)
        self.assertIn('interval: 15000', service)

    def test_bar_uses_compact_content_and_native_indicator_sizing(self):
        source = (Path(b.__file__).parent / "Widget.qml").read_text()
        self.assertIn("CatIcon {", source)
        self.assertIn("ruffled: root.iconState.ruffled", source)
        self.assertNotIn("BoltIcon", source)
        self.assertNotIn("GlobeIcon", source)
        self.assertNotIn("speedMetrics", source)
        self.assertIn("openPanelIndicatorWidth: barLabels.length ? barContents.implicitWidth : 0", source)
        self.assertNotIn("openPanelIndicatorWidth: button.implicitWidth", source)


if __name__ == "__main__":
    unittest.main()
