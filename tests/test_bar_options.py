"""Bar presentation settings never apply proxy configuration or control services."""

import unittest
from unittest.mock import patch

import test_backend as fixtures
import backend as b


class BarOptionTests(unittest.TestCase):
    setUp = fixtures.ManagerTests.setUp
    imported = fixtures.ManagerTests.imported

    def test_existing_installations_default_to_icon_only(self):
        self.assertEqual(b.status()["barDisplay"], {key: False for key in b.BAR_DISPLAY_KEYS})
        self.assertFalse(b.DATA.exists())

    def test_each_option_persists_independently(self):
        for key in b.BAR_DISPLAY_KEYS:
            result = b.dispatch({"action": "barDisplay", "key": key, "value": True})
            self.assertEqual(result, {})
            self.assertTrue(b.status()["barDisplay"][key])
        b.dispatch({"action": "barDisplay", "key": "region", "value": False})
        self.assertEqual(b.bar_display(b.state()), {"speed": True, "region": False})
        self.privileged.assert_not_called()
        self.validate.assert_not_called()

    def test_invalid_options_do_not_persist(self):
        for key, value in (("unknown", True), ("speed", "true"), ("speed", 1), ("mode", None)):
            self.assertFalse(b.response({"action": "barDisplay", "key": key, "value": value})["ok"])
        self.assertFalse((b.DATA / "state.json").exists())

    def test_presentation_changes_do_not_apply_network_configuration(self):
        self.imported(True)
        before = (b.DATA / "runtime.yaml").read_text()
        state = b.state()
        with patch.object(b, "apply") as apply, patch.object(b, "api") as api:
            b.dispatch({"action": "barDisplay", "key": "speed", "value": True})
        apply.assert_not_called()
        api.assert_not_called()
        self.privileged.assert_not_called()
        self.assertEqual((b.DATA / "runtime.yaml").read_text(), before)
        current = b.state()
        current.pop("barDisplay")
        self.assertEqual(current, state)

    def test_closed_panel_speed_polling_does_not_depend_on_visibility(self):
        self.assertEqual(b.poll_interval(False, {}), 30)
        self.assertEqual(b.poll_interval(True, {}), 5)
        self.assertEqual(b.poll_interval(False, {"barDisplay": {"speed": True}}), 2)
        self.assertEqual(b.poll_interval(False, {"barDisplay": {"region": True}}), 30)

    def test_old_region_setting_is_preserved_without_removed_options(self):
        original = {"barDisplay": {"country": True, "speed": True, "capture": True, "mode": True}}
        self.assertEqual(b.bar_display(original), {"speed": True, "region": True})
        original["barDisplay"]["region"] = False
        self.assertFalse(b.bar_display(original)["region"])

    def test_removed_display_options_are_rejected(self):
        for key in ("country", "capture", "mode"):
            self.assertFalse(b.response({"action": "barDisplay", "key": key, "value": True})["ok"])

    def test_malformed_display_state_has_safe_defaults(self):
        for value in (None, [], "invalid", {"speed": 1, "country": "true"}):
            self.assertFalse(any(b.bar_display({"barDisplay": value}).values()))
