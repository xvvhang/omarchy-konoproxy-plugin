"""Profile editing is transactional and does not expose stored source URLs."""

import unittest
from unittest.mock import patch

import test_backend as fixtures
import backend as b


class ProfileEditorTests(unittest.TestCase):
    setUp = fixtures.ManagerTests.setUp
    imported = fixtures.ManagerTests.imported

    def test_blank_source_keeps_saved_subscription(self):
        with patch.object(b, "download", return_value=fixtures.PROFILE):
            b.dispatch({"action": "import", "name": "Original", "url": "https://example.com/private-token"})
        original = b.state()["profiles"][0]
        with patch.object(b, "download") as download:
            b.dispatch({"action": "editProfile", "id": original["id"], "name": "Renamed", "url": "", "path": ""})
        current = b.state()["profiles"][0]
        self.assertEqual(current["name"], "Renamed")
        self.assertEqual(current["url"], original["url"])
        self.assertEqual(current["updated"], original["updated"])
        download.assert_not_called()
        self.assertNotIn("url", b.status()["profiles"][0])

    def test_replacing_source_preserves_identity_and_custom_rules(self):
        original = self.imported(True)
        b.dispatch({"action": "rules", "text": "DOMAIN-SUFFIX,example.com,DIRECT"})
        with patch.object(b, "download", return_value=fixtures.PROFILE):
            b.dispatch({"action": "editProfile", "id": original["id"], "name": "Subscription", "url": "https://example.com/new"})
        current = b.state()["profiles"][0]
        self.assertEqual(current["id"], original["id"])
        self.assertEqual(current["kind"], "subscription")
        self.assertEqual(current["rules"], ["DOMAIN-SUFFIX,example.com,DIRECT"])
        self.assertEqual(b.state()["active"], original["id"])

    def test_local_replacement_does_not_activate_inactive_profile(self):
        original = self.imported()
        b.dispatch({"action": "editProfile", "id": original["id"], "name": "Local", "path": str(self.input)})
        self.assertEqual(b.state()["active"], "")
        self.assertEqual(b.state()["profiles"][0]["kind"], "file")
        self.assertFalse((b.DATA / "runtime.yaml").exists())

    def test_invalid_replacement_does_not_change_name_source_or_runtime(self):
        original = self.imported(True)
        before = {path: path.read_text() for path in [b.DATA / "state.json", b.DATA / "runtime.yaml", b.source_path(original["id"])]}
        with patch.object(b, "download", return_value="invalid configuration"), \
                patch.object(b, "validate", side_effect=b.UserError("Invalid configuration.")):
            result = b.response({"action": "editProfile", "id": original["id"], "name": "Changed", "url": "https://example.com/bad"})
        self.assertFalse(result["ok"])
        for path, text in before.items():
            self.assertEqual(path.read_text(), text)

    def test_failed_persistence_rolls_back_source_and_runtime(self):
        original = self.imported(True)
        before = {path: path.read_text() for path in [b.DATA / "state.json", b.DATA / "runtime.yaml", b.source_path(original["id"])]}
        replacement = fixtures.PROFILE.replace("not-a-real-password", "replacement-password")
        with patch.object(b, "download", return_value=replacement), \
                patch.object(b, "save", side_effect=OSError("Storage unavailable")):
            with self.assertRaises(OSError):
                b.dispatch({"action": "editProfile", "id": original["id"], "name": "Changed", "url": "https://example.com/new"})
        for path, text in before.items():
            self.assertEqual(path.read_text(), text)

    def test_invalid_names_and_ambiguous_sources_rejected(self):
        original = self.imported()
        for values in ({"name": ""}, {"name": "x" * 101}, {"name": "Valid", "url": "https://example.com/new", "path": str(self.input)}):
            with self.subTest(values=values):
                self.assertFalse(b.response(dict(action="editProfile", id=original["id"], **values))["ok"])
        self.assertEqual(b.state()["profiles"][0]["name"], original["name"])
