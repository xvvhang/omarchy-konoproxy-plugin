import unittest
from unittest.mock import patch

import networking as n


def tunnel(name):
    return {"ifname": name, "linkinfo": {"info_kind": "tun"}}


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.links = [tunnel("tailscale0")]
        self.routes = [{"dev": "tailscale0", "table": 52, "dst": "100.100.0.1/32"}]
        self.rules = [{"table": 52, "priority": 5270}]
        self.prefs = {"WantRunning": True, "ExitNodeID": "", "ExitNodeIP": ""}

    def check(self, controller=None):
        return n.validate(self.links, self.routes, self.rules, 1000, controller, self.prefs)

    def test_split_tailscale_allowed(self):
        self.assertTrue(self.check()["tailscaleSplit"])
        self.assertLess(5500, n.rule_priority(1000))
        self.assertLess(n.rule_priority(1000) + 10, 32766)

    def test_exit_node_rejected(self):
        self.prefs["ExitNodeID"] = "configured"
        with self.assertRaisesRegex(n.NetworkConflict, "exit-node"):
            self.check()

    def test_default_and_half_default_routes_rejected(self):
        for destination in ("default", "::/0", "0.0.0.0/1", "128.0.0.0/1", "::/1", "8000::/1"):
            with self.subTest(destination=destination):
                self.routes[0]["dst"] = destination
                with self.assertRaisesRegex(n.NetworkConflict, "default routing"):
                    self.check()

    def test_unknown_tunnel_rejected(self):
        self.links.append(tunnel("vpn0"))
        with self.assertRaisesRegex(n.NetworkConflict, "vpn0"):
            self.check()

    def test_own_interface_requires_controller_confirmation(self):
        self.links.append(tunnel("kp1000"))
        with self.assertRaisesRegex(n.NetworkConflict, "not confirmed"):
            self.check()
        self.assertTrue(self.check({"tun": {"enable": True, "device": "kp1000"}})["managedActive"])

    def test_stale_managed_rules_rejected(self):
        self.rules.append({"table": 201000, "priority": 211000})
        with self.assertRaisesRegex(n.NetworkConflict, "Stale"):
            self.check()

    def test_priority_collision_rejected(self):
        self.rules.append({"table": 123, "priority": n.rule_priority(1000)})
        with self.assertRaisesRegex(n.NetworkConflict, "already in use"):
            self.check()

    def test_unverified_tailscale_rejected(self):
        self.prefs = None
        with self.assertRaisesRegex(n.NetworkConflict, "ownership"):
            self.check()

    def test_wrong_rule_order_rejected(self):
        self.rules[0]["priority"] = 22000
        with self.assertRaisesRegex(n.NetworkConflict, "precede"):
            self.check()

    def test_inspection_failure_is_closed(self):
        with patch.object(n.subprocess, "run", side_effect=OSError()):
            with self.assertRaises(n.NetworkConflict):
                n.collect()

    def test_no_tunnels_allowed(self):
        self.assertFalse(n.validate([], [], [], 1000)["tailscaleSplit"])
