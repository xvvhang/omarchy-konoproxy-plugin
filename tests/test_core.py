"""Optional real-core smoke test. Never enables TUN or changes desktop settings."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend as b
import inventory as inventory_model
from test_backend import PROFILE


@unittest.skipUnless(os.environ.get("KONOPROXY_TEST_CORE"), "Set KONOPROXY_TEST_CORE to a verified Mihomo executable")
class RealCoreTests(unittest.TestCase):
    def test_import_validate_activate_switch_and_bad_rule_rollback(self):
        core = Path(os.environ["KONOPROXY_TEST_CORE"])
        with tempfile.TemporaryDirectory(prefix="konoproxy-core-") as directory:
            with patch.object(b, "DATA", Path(directory)), patch.object(b, "CORE", core):
                s = b.state()
                sockets = []
                for key in ("mixedPort", "controllerPort"):
                    probe = socket.socket()
                    probe.bind(("127.0.0.1", 0))
                    sockets.append(probe)
                    s[key] = probe.getsockname()[1]
                b.save(s)
                source = Path(directory) / "input.yaml"
                document = b.parse_yaml(PROFILE)
                document["proxies"].extend([
                    {"name": "Test VMess", "type": "vmess", "server": "127.0.0.1", "port": 443,
                     "uuid": "00000000-0000-4000-8000-000000000001", "alterId": 0, "cipher": "auto"},
                    {"name": "Test VLESS Reality", "type": "vless", "server": "127.0.0.1", "port": 443,
                     "uuid": "00000000-0000-4000-8000-000000000002", "tls": True, "network": "tcp",
                     "servername": "example.com", "client-fingerprint": "chrome",
                     "reality-opts": {"public-key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "short-id": ""}},
                ])
                source.write_text(b.yaml_module().safe_dump(document))
                for index, node in enumerate(document["proxies"]):
                    candidate = Path(directory) / f"probe-{index}.json"
                    candidate.write_text(json.dumps(inventory_model.probe_config(node, s["mixedPort"])))
                    b.validate(candidate)
                b.dispatch({"action": "import", "name": "Local test", "path": str(source)})
                item = b.state()["profiles"][0]
                b.dispatch({"action": "activate", "id": item["id"]})
                for probe in sockets:
                    probe.close()
                process = subprocess.Popen([str(core), "-d", directory, "-f", str(Path(directory) / "runtime.yaml")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    for _ in range(100):
                        if b.alive(s):
                            break
                        if process.poll() is not None:
                            self.fail("The isolated core stopped during startup.")
                        time.sleep(0.1)
                    self.assertTrue(b.alive(s))
                    b.dispatch({"action": "mode", "value": "global"})
                    self.assertEqual(b.api(s, "/configs")["mode"], "global")
                    b.dispatch({"action": "select", "group": "JMS", "node": "Test SS"})
                    self.assertEqual(b.api(s, "/proxies/JMS")["now"], "Test SS")
                    b.dispatch({"action": "select", "group": "JMS Auto", "node": "Test SS"})
                    self.assertEqual(b.api(s, "/proxies/JMS%20Auto")["fixed"], "Test SS")
                    b.dispatch({"action": "autoSelect", "group": "JMS Auto"})
                    self.assertEqual(b.api(s, "/proxies/JMS%20Auto")["fixed"], "")
                    b.dispatch({"action": "selectNode", "profile": item["id"], "name": "Test VMess"})
                    self.assertEqual(b.api(s, "/proxies/GLOBAL")["now"], "Test VMess")
                    b.dispatch({"action": "selectNode", "profile": item["id"], "name": "JMS Auto"})
                    self.assertEqual(b.api(s, "/proxies/GLOBAL")["now"], "JMS Auto")
                    b.dispatch({"action": "rules", "text": "DOMAIN-SUFFIX,example.com,DIRECT"})
                    before = (Path(directory) / "runtime.yaml").read_text()
                    bad = b.response({"action": "rules", "text": "UNSUPPORTED-RULE,example.com,Missing"})
                    self.assertFalse(bad["ok"])
                    self.assertEqual((Path(directory) / "runtime.yaml").read_text(), before)
                    snapshot = b.status()
                    self.assertTrue(snapshot["running"])
                    self.assertTrue(snapshot["groups"])
                    self.assertFalse(snapshot["tun"])
                    self.assertEqual(len(snapshot["profileGroups"][0]["nodes"]), 3)
                finally:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
