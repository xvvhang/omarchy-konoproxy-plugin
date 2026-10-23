"""Run the shared QML JavaScript model in Node when available."""

from pathlib import Path
import shutil
import subprocess
import unittest


class PanelModelTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is optional for JavaScript model tests")
    def test_panel_session_filtering_and_profile_order(self):
        result = subprocess.run(["node", str(Path(__file__).with_suffix(".js"))],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
