"""Private profile inventory and isolated node probes, independent of the service."""

import contextlib
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import tempfile
import time

REGION_TTL = 7 * 86400


def node_key(profile_id, name):
    return profile_id + ":" + name


def fingerprint(node):
    return hashlib.sha256(json.dumps(node, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize_region(code):
    code = str(code).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", code) or code in ("XX", "ZZ", "T1", "A1", "A2", "EU"):
        return ""
    return "UK" if code == "GB" else code


def read_profile(manager, item):
    document = manager.parse_yaml(manager.local_text(str(manager.source_path(item["id"]))))
    if not isinstance(document, dict):
        raise manager.UserError("The stored profile is not a YAML mapping.")
    nodes = list(document.get("proxies") or [])
    missing = False
    for index, _ in enumerate((document.get("proxy-providers") or {}).values()):
        cache = manager.DATA / "providers" / item["id"] / f"proxy-providers-{index}.yaml"
        if not cache.is_file():
            missing = True
            continue
        provider = manager.parse_yaml(manager.local_text(str(cache)))
        if isinstance(provider, dict):
            nodes.extend(provider.get("proxies") or [])
    unique = {}
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("name"), str) and isinstance(node.get("type"), str):
            unique.setdefault(node["name"], node)
    return document, list(unique.values()), missing


def current_leaf(manager, s, inventory):
    if s["mode"] == "global":
        name = "GLOBAL"
    else:
        name = next((name for name, group in inventory.items()
                     if name != "GLOBAL" and group.get("type") == "Selector"), "")
    visited = set()
    while name and name not in visited:
        visited.add(name)
        entry = inventory.get(name, {})
        if "all" not in entry:
            return name
        name = entry.get("now", "")
    return ""


def sections(manager, s, live=None):
    regions = manager.read_json(manager.DATA / "regions.json", {})
    view_path = manager.DATA / "view-cache.json"
    view = manager.read_json(view_path, {})
    if live is not None and s["active"]:
        selected = current_leaf(manager, s, live)
        if selected and view.get(s["active"]) != selected:
            view[s["active"]] = selected
            manager.atomic(view_path, json.dumps(view))
    result = []
    for item in s["profiles"]:
        section = {"id": item["id"], "name": item["name"], "active": item["id"] == s["active"], "nodes": [], "warning": "", "automaticGroup": ""}
        try:
            document, nodes, missing = read_profile(manager, item)
            section["automaticGroup"] = next((group["name"] for group in (document.get("proxy-groups") or [])
                                               if group.get("type") in ("url-test", "fallback")), "")
            selected = item.get("selectedNode", "") if item.get("selectionPending") else view.get(item["id"], item.get("selectedNode", ""))
            if section["active"] and live is not None:
                selected = current_leaf(manager, s, live)
            for node in nodes:
                probe_id = fingerprint(node)
                region = regions.get(probe_id, {})
                code = normalize_region(region.get("code", ""))
                section["nodes"].append({
                    "key": node_key(item["id"], node["name"]), "name": node["name"],
                    "probeId": probe_id,
                    "protocol": manager.node_metadata(node["name"], node)["protocol"],
                    "regionCode": code, "regionStale": bool(code and time.time() - region.get("updated", 0) > REGION_TTL),
                    "selected": node["name"] == selected,
                })
            if missing:
                section["warning"] = "Some providers have no local cache yet. Start and activate this profile to fetch them."
        except manager.UserError:
            section["warning"] = "The local profile or provider cache could not be read."
        result.append(section)
    return result


def select_path(inventory, mode, target):
    roots = ["GLOBAL"] if mode == "global" else [name for name, group in inventory.items()
             if name != "GLOBAL" and group.get("type") == "Selector"]
    if mode != "global":
        roots += [name for name, group in inventory.items() if group.get("type") in ("URLTest", "Fallback")]
    queue = [(name, [], frozenset()) for name in roots]
    visited = set()
    while queue:
        name, path, seen = queue.pop(0)
        if name in seen or name in visited:
            continue
        visited.add(name)
        group = inventory.get(name, {})
        if group.get("type") not in ("Selector", "URLTest", "Fallback"):
            continue
        if target in group.get("all", []):
            return path + [(name, target)]
        for child in group.get("all", []):
            if child in inventory and "all" in inventory[child]:
                queue.append((child, path + [(name, child)], seen | {name}))
    return []


def probe_config(node, port):
    if node.get("dialer-proxy"):
        raise ValueError("Chained nodes require a dedicated region probe implementation.")
    return {"mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": False,
            "mode": "rule", "log-level": "silent", "ipv6": True,
            "proxies": [node], "rules": ["MATCH," + node["name"]], "tun": {"enable": False}}


class ProbeCancelled(Exception):
    pass


def check_cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ProbeCancelled()


@contextlib.contextmanager
def probe_port(manager, node, cancel=None):
    if not manager.CORE.is_file():
        raise manager.UserError("Install the managed core before resolving node regions.")
    check_cancelled(cancel)
    with tempfile.TemporaryDirectory(prefix="konoproxy-region-") as directory:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        config = Path(directory) / "probe.json"
        manager.atomic(config, json.dumps(probe_config(node, port)))
        process = subprocess.Popen(["/usr/bin/timeout", "--signal=TERM", "--kill-after=3s", "40s",
                                    str(manager.CORE), "-d", directory, "-f", str(config)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            ready = False
            for _ in range(60):
                check_cancelled(cancel)
                if process.poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        ready = True
                        break
                except OSError:
                    time.sleep(0.05)
            if not ready:
                raise manager.UserError("The isolated region probe did not start.")
            check_cancelled(cancel)
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def probe_request(port, host, path):
    # CONNECT + TLS validation, without environment proxies or controller edits.
    connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=10,
                                             context=ssl.create_default_context())
    try:
        connection.set_tunnel(host, 443)
        started = time.monotonic()
        connection.request("GET", path, headers={"User-Agent": "Konoproxy", "Host": host})
        response = connection.getresponse()
        body = response.read(16385)
        elapsed = max(1, round((time.monotonic() - started) * 1000))
        if len(body) > 16384:
            raise ValueError("Probe response exceeds the size limit.")
        return response.status, body, elapsed
    finally:
        connection.close()


def trace_region(status, body):
    if status != 200:
        return ""
    fields = dict(line.split("=", 1) for line in body.decode("ascii").splitlines() if "=" in line)
    return normalize_region(fields.get("loc", ""))


def probe_region(manager, node):
    with probe_port(manager, node) as port:
        status, body, _ = probe_request(port, "www.cloudflare.com", "/cdn-cgi/trace")
        code = trace_region(status, body)
        if not code:
            raise manager.UserError("The exit region was not available.")
        return code


def probe_node(manager, node, cancel=None, on_delay=None):
    result = {"regionCode": "", "delay": 0}
    with probe_port(manager, node, cancel) as port:
        check_cancelled(cancel)
        try:
            status, _, delay = probe_request(port, "www.gstatic.com", "/generate_204")
            if status == 204:
                result["delay"] = delay
        except Exception:
            pass
        check_cancelled(cancel)
        if on_delay is not None:
            on_delay(result["delay"])
        try:
            status, body, _ = probe_request(port, "www.cloudflare.com", "/cdn-cgi/trace")
            result["regionCode"] = trace_region(status, body)
        except Exception:
            pass
        check_cancelled(cancel)
    return result
