"""Read-only TUN ownership and split-Tailscale coexistence checks."""

import json
import os
import subprocess

TAILSCALE_BYPASS = ["100.64.0.0/10", "fd7a:115c:a1e0::/48"]


class NetworkConflict(Exception):
    pass


def rule_priority(uid):
    # Tailscale's 5200-5500 rules must precede capture, which must precede main.
    return 12000 + uid % 1000


def inspect_json(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise ValueError()
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise NetworkConflict("Network routing could not be inspected. No interfaces were changed.") from None


def collect():
    links = inspect_json(["/usr/bin/ip", "-details", "-json", "link", "show"])
    routes, rules = [], []
    for family in ("-4", "-6"):
        routes.extend(inspect_json(["/usr/bin/ip", family, "-json", "route", "show", "table", "all"]))
        rules.extend(inspect_json(["/usr/bin/ip", family, "-json", "rule", "show"]))
    return links, routes, rules


def is_default(route):
    return route.get("dst", "default") in ("default", "0.0.0.0/0", "::/0", "0.0.0.0/1", "128.0.0.0/1", "::/1", "8000::/1")


def table_id(value):
    return {"main": "254", "default": "253", "local": "255"}.get(str(value), str(value))


def validate(links, routes, rules, uid, controller=None, tailscale=None):
    own = f"kp{uid}"
    table = str(200000 + uid)
    priority = rule_priority(uid)
    tunnels = {link["ifname"] for link in links
               if link.get("linkinfo", {}).get("info_kind") in ("tun", "wireguard")}
    others = tunnels - {own, "tailscale0"}
    if others:
        raise NetworkConflict("Other tunnel interfaces are present: " + ", ".join(sorted(others)) + ". Review them before enabling TUN; none were stopped.")
    tun = (controller or {}).get("tun", {})
    confirmed = own in tunnels and tun.get("enable") is True and tun.get("device") == own
    if own in tunnels and not confirmed:
        raise NetworkConflict(f"Managed interface {own} exists but is not confirmed by this controller. Restart the managed core to recover; no interface was deleted.")
    owned_rules = [rule for rule in rules if table_id(rule.get("table")) == table]
    if owned_rules and not confirmed:
        raise NetworkConflict("Stale managed routing rules are present. Restart the managed core before enabling TUN.")
    if not confirmed and any(priority <= rule.get("priority", -1) <= priority + 10 for rule in rules):
        raise NetworkConflict("The managed TUN rule-priority range is already in use. No routing rules were changed.")
    if "tailscale0" not in tunnels:
        return {"tailscaleSplit": False, "managedActive": confirmed}
    if not tailscale or tailscale.get("WantRunning") is not True:
        raise NetworkConflict("Cannot verify tailscale0 ownership. Check Tailscale before enabling TUN.")
    if tailscale.get("ExitNodeID") or tailscale.get("ExitNodeIP"):
        raise NetworkConflict("Tailscale exit-node routing is configured on tailscale0. Disable the exit node or use System proxy; Tailscale was not modified.")
    tail_routes = [route for route in routes if route.get("dev") == "tailscale0"]
    if any(is_default(route) for route in tail_routes):
        raise NetworkConflict("Tailscale default routing is active on tailscale0. Use System proxy or review the exit-node settings.")
    tail_tables = {table_id(route.get("table", "main")) for route in tail_routes} - {"254", "255"}
    if not tail_tables:
        raise NetworkConflict("No split-routing table could be verified for tailscale0. Review Tailscale before enabling TUN.")
    for tail_table in tail_tables:
        if not any(table_id(rule.get("table")) == tail_table and rule.get("priority", priority) < priority for rule in rules):
            raise NetworkConflict("Tailscale split routes would not precede managed capture. Review policy routing before enabling TUN.")
    return {"tailscaleSplit": True, "managedActive": confirmed}


def check(manager):
    links, routes, rules = collect()
    tunnels = {link.get("ifname") for link in links if link.get("linkinfo", {}).get("info_kind") in ("tun", "wireguard")}
    controller = None
    if f"kp{os.getuid()}" in tunnels:
        try:
            controller = manager.api(manager.state(), "/configs")
        except manager.UserError:
            raise NetworkConflict("The managed TUN owner could not be confirmed because its controller is unavailable.") from None
    prefs = inspect_json(["/usr/bin/tailscale", "debug", "prefs"]) if "tailscale0" in tunnels else None
    return validate(links, routes, rules, os.getuid(), controller, prefs)
