#!/usr/bin/env python3
"""Unprivileged profile manager and newline-delimited JSON bridge for QML."""

import concurrent.futures
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import inventory as inventory_model
import probing as probing_model
import networking

PLUGIN_ID = "konochi.konoproxy"
HERE = Path(__file__).resolve().parent
HOME = Path.home()
DATA = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share")) / "konoproxy"
CORE = Path("/usr/local/lib/konoproxy/mihomo")
HELPER = Path("/usr/local/lib/konoproxy/privileged.py")
UNIT = f"konoproxy-{os.getuid()}.service"
SERVICE_FILE = Path(f"/etc/systemd/system/{UNIT}")
OWNER_FILE = HELPER.parent / "owner.json"
MAX_DOWNLOAD = 8 * 1024 * 1024


class UserError(Exception):
    """An intentionally safe, English-only diagnostic."""


def run(args, timeout=30):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise UserError("The requested command failed or timed out.") from None
    if result.returncode:
        # Do not expose command output: it may include subscription credentials.
        raise UserError("The operation failed. Check dependencies, authorization and service status.")
    return result.stdout.strip()


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def atomic(path, text):
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise UserError("Stored state is unreadable. Restore a backup before continuing.") from None


def save(state):
    atomic(DATA / "state.json", json.dumps(state, indent=2) + "\n")


def state():
    return read_json(DATA / "state.json", {
        "profiles": [], "active": "", "mode": "rule", "tun": False,
        "interval": 24, "mixedPort": 17890, "controllerPort": 17990,
    })


@contextlib.contextmanager
def locked():
    private_dir(DATA)
    with (DATA / "manager.lock").open("a") as stream:
        os.chmod(stream.name, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def yaml_module():
    try:
        import yaml
        return yaml
    except ImportError:
        raise UserError("Python YAML support is missing. Run the core setup to install python-yaml.") from None


def parse_yaml(text):
    yaml = yaml_module()
    try:
        value = yaml.safe_load(text)
        # Bound expanded traversal without first allocating an expanded document.
        pending = [(value, 0, frozenset())]
        visited = 0
        while pending:
            node, depth, ancestors = pending.pop()
            visited += 1
            if visited > 100000 or depth > 64:
                raise ValueError()
            if isinstance(node, (dict, list)):
                if id(node) in ancestors:
                    raise ValueError()
                children = list(node.values()) if isinstance(node, dict) else node
                if isinstance(node, dict) and any(not isinstance(key, str) for key in node):
                    raise ValueError()
                trail = ancestors | {id(node)}
                pending.extend((child, depth + 1, trail) for child in children)
            elif not isinstance(node, (str, int, float, bool, type(None))):
                raise ValueError()
        return value
    except (ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise UserError("Invalid or excessively complex YAML content.") from None


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise UserError("Use an HTTPS subscription or rule-set URL without embedded login credentials.")


def download(url):
    check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "clash.meta/Konoproxy"})
    try:
        with urllib.request.build_opener(SecureRedirect).open(request, timeout=25) as response:
            payload = response.read(MAX_DOWNLOAD + 1)
        if len(payload) > MAX_DOWNLOAD:
            raise UserError("The downloaded file exceeds the 8 MiB limit.")
        return payload.decode("utf-8-sig")
    except (OSError, ValueError, UnicodeError):
        raise UserError("Download failed. Check the URL, network and subscription validity.") from None


def local_text(path):
    file = Path(path).expanduser()
    try:
        if not file.is_file() or file.stat().st_size > MAX_DOWNLOAD:
            raise ValueError()
        return file.read_text(encoding="utf-8-sig")
    except (OSError, ValueError, UnicodeError):
        raise UserError("Cannot read the file, or it exceeds the 8 MiB limit.") from None


def profile(s, identifier):
    for item in s["profiles"]:
        if item["id"] == identifier:
            return item
    raise UserError("The selected profile no longer exists.")


def source_path(identifier):
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise UserError("Invalid profile identifier.")
    return DATA / "profiles" / f"{identifier}.yaml"


def token():
    file = DATA / "controller-secret"
    if not file.exists():
        atomic(file, secrets.token_urlsafe(32))
    return file.read_text().strip()


def api(s, route, method="GET", body=None, timeout=4):
    request = urllib.request.Request(
        f"http://127.0.0.1:{s['controllerPort']}{route}",
        data=None if body is None else json.dumps(body).encode(), method=method,
        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"},
    )
    try:
        # Never send local controller credentials through ambient HTTP proxies.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
            payload = response.read(MAX_DOWNLOAD)
        return json.loads(payload) if payload else {}
    except (OSError, ValueError):
        raise UserError("The Mihomo controller is unavailable or rejected the operation.") from None


def alive(s):
    try:
        return api(s, "/version")
    except UserError:
        return None


def compile_config(s, item, text):
    source = parse_yaml(text)
    if not isinstance(source, dict) or not (source.get("proxies") or source.get("proxy-providers")):
        raise UserError("Import a Mihomo / Clash.Meta YAML profile, not a legacy Base64 subscription.")
    # Never inherit arbitrary listeners, controller credentials, TUN settings,
    # external UI downloads, scripts or filesystem paths from a subscription.
    allowed = ("proxies", "proxy-groups", "proxy-providers", "rules", "rule-providers", "hosts")
    result = {key: source[key] for key in allowed if key in source}
    for kind in ("proxy-providers", "rule-providers"):
        providers = result.get(kind, {})
        if not isinstance(providers, dict):
            raise UserError("Provider definitions must be a mapping.")
        for index, provider in enumerate(providers.values()):
            if not isinstance(provider, dict) or provider.get("type") != "http":
                raise UserError("Only HTTPS-backed providers are supported in managed profiles.")
            check_url(provider.get("url", ""))
            provider["path"] = str(DATA / "providers" / item["id"] / f"{kind}-{index}.yaml")
    rules = item.get("rules", [])
    result["rules"] = rules + result.get("rules", ["MATCH,DIRECT"])
    for index, provider in enumerate(item.get("ruleProviders", [])):
        name = f"konoproxy-{index}"
        result.setdefault("rule-providers", {})[name] = {
            "type": "http", "behavior": provider["behavior"], "format": provider["format"],
            "url": provider["url"], "interval": 86400,
            "path": str(DATA / "providers" / item["id"] / f"custom-{index}.rules"),
        }
        result["rules"].insert(index, f"RULE-SET,{name},{provider['target']}")
    result.update({
        "mixed-port": s["mixedPort"], "allow-lan": False, "bind-address": "127.0.0.1",
        "external-controller": f"127.0.0.1:{s['controllerPort']}", "secret": token(),
        "mode": s["mode"], "log-level": "warning", "ipv6": True,
        "profile": {"store-selected": True, "store-fake-ip": True},
        "tun": {"enable": s["tun"], "device": f"kp{os.getuid()}", "stack": "gvisor",
                "auto-route": True, "auto-detect-interface": True, "strict-route": True,
                "iproute2-table-index": 200000 + os.getuid(),
                "iproute2-rule-index": networking.rule_priority(os.getuid()),
                "route-exclude-address": networking.TAILSCALE_BYPASS,
                "dns-hijack": ["any:53", "tcp://any:53"]},
        "dns": {"enable": True, "ipv6": True, "enhanced-mode": "fake-ip",
                "fake-ip-range": "198.18.0.1/16", "fake-ip-filter": ["*.lan", "localhost", "*.local"],
                "default-nameserver": ["1.1.1.1", "8.8.8.8"],
                "nameserver": ["https://dns.cloudflare.com/dns-query", "https://dns.google/dns-query"]},
    })
    return yaml_module().safe_dump(result, allow_unicode=True, sort_keys=False)


def validate(candidate):
    if not CORE.is_file():
        raise UserError("Install the managed Mihomo core before importing profiles.")
    run([str(CORE), "-t", "-d", str(DATA), "-f", str(candidate)], timeout=60)


def apply(s, item, text, activate=True):
    candidate = DATA / "candidate.yaml"
    atomic(candidate, compile_config(s, item, text))
    try:
        validate(candidate)
        if not activate:
            return
        runtime = DATA / "runtime.yaml"
        previous = runtime.read_text() if runtime.exists() else None
        was_running = bool(alive(s))
        atomic(runtime, candidate.read_text())
        try:
            if was_running:
                api(s, "/configs?force=true", "PUT", {"path": str(runtime)}, timeout=45)
        except UserError:
            if previous is not None:
                atomic(runtime, previous)
                try:
                    api(s, "/configs?force=true", "PUT", {"path": str(runtime)}, timeout=45)
                except UserError:
                    raise UserError("Apply and rollback both failed. Stop the service and retry the previous profile.") from None
            raise
        if previous is not None:
            atomic(DATA / "last-good.yaml", previous)
    finally:
        candidate.unlink(missing_ok=True)


def commit_profile(s, item, text, activate=True, store_source=False):
    paths = [DATA / "runtime.yaml", DATA / "state.json"]
    if store_source:
        paths.append(source_path(item["id"]))
    originals = {path: path.read_text() if path.exists() else None for path in paths}
    try:
        apply(s, item, text, activate=activate)
        if store_source:
            atomic(source_path(item["id"]), text)
        save(s)
    except Exception:
        runtime = DATA / "runtime.yaml"
        runtime_changed = runtime.exists() and runtime.read_text() != originals[runtime]
        try:
            for path, previous in originals.items():
                current = path.read_text() if path.exists() else None
                if current == previous:
                    continue
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic(path, previous)
            if runtime_changed and originals[runtime] is not None and alive(s):
                api(s, "/configs?force=true", "PUT", {"path": str(runtime)}, timeout=45)
        except Exception:
            raise UserError("Configuration recovery was incomplete. Stop the service and restore the retained profile source.") from None
        raise


def privileged(action, *args, bootstrap=False):
    helper = HERE / "privileged.py" if bootstrap else HELPER
    if not helper.is_file():
        raise UserError("The managed service is not installed. Run core setup first.")
    run(["/usr/bin/pkexec", "/usr/bin/python3", "-I", str(helper), action, *args], timeout=240)


def service_control(action):
    if action not in ("start", "stop", "restart"):
        raise UserError("Unsupported service operation.")
    if password_free_control():
        run(["/usr/bin/systemctl", "--no-ask-password", action, UNIT], timeout=60)
    else:
        privileged(action)


def password_free_control():
    owner = read_json(OWNER_FILE, {})
    return owner.get("uid") == os.getuid() and owner.get("passwordFreeControl") is True


def apply_selection(s, item):
    target = item.get("selectedNode")
    if not target:
        return
    inventory = proxy_inventory(s)
    path = inventory_model.select_path(inventory, s["mode"], target)
    if not path:
        raise UserError("This node is not reachable through a selectable policy in the profile.")
    if inventory.get(target, {}).get("type") in ("URLTest", "Fallback"):
        api(s, "/proxies/" + urllib.parse.quote(target, safe=""), "DELETE")
    for group, node in reversed(path):
        api(s, "/proxies/" + urllib.parse.quote(group, safe=""), "PUT", {"name": node})
    item["selectionPending"] = False
    save(s)


PROXY_KEYS = [
    ("org.gnome.system.proxy", "mode", "'manual'"),
    ("org.gnome.system.proxy", "ignore-hosts", "['localhost', '127.0.0.0/8', '::1', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']"),
] + [(f"org.gnome.system.proxy.{protocol}", key, value)
     for protocol in ("http", "https", "socks")
     for key, value in (("host", "'127.0.0.1'"), ("port", "PORT"))]


def system_proxy(s, enabled):
    file = DATA / "system-proxy.json"
    previous = read_json(file, [])
    if enabled:
        if not alive(s):
            raise UserError("Start Mihomo before enabling the system proxy.")
        if previous:
            return
        entries = []
        for schema, key, value in PROXY_KEYS:
            entries.append({"schema": schema, "key": key,
                            "before": run(["gsettings", "get", schema, key]),
                            "ours": str(s["mixedPort"]) if value == "PORT" else value})
        atomic(file, json.dumps(entries))
        try:
            # Change mode last, once all endpoints are ready.
            for entry in entries[1:] + entries[:1]:
                run(["gsettings", "set", entry["schema"], entry["key"], entry["ours"]])
        except UserError:
            system_proxy(s, False)
            raise
    else:
        for entry in previous:
            current = run(["gsettings", "get", entry["schema"], entry["key"]])
            if current == entry["ours"]:
                run(["gsettings", "set", entry["schema"], entry["key"], entry["before"]])
        file.unlink(missing_ok=True)


def proxy_inventory(s):
    proxies = api(s, "/proxies").get("proxies", {})
    try:
        providers = api(s, "/providers/proxies").get("providers", {})
        for name, provider in providers.items():
            for node in provider.get("proxies", []):
                # Static nodes, groups and built-ins take precedence on collision.
                proxies.setdefault(node["name"], dict(node, _konoproxyProvider=name))
    except UserError:
        pass
    return proxies


def latency_route(inventory, name, query):
    provider = inventory.get(name, {}).get("_konoproxyProvider")
    encoded = urllib.parse.quote(name, safe="")
    if provider:
        return "/providers/proxies/" + urllib.parse.quote(provider, safe="") + "/" + encoded + "/healthcheck?" + query
    return "/proxies/" + encoded + "/delay?" + query


def node_metadata(name, info):
    # Protocol comes from configuration/API data. Names do not prove an exit country.
    kind = info.get("type", "Unknown")
    protocol = {
        "ss": "SS", "ssr": "SSR", "shadowsocks": "SS", "shadowsocksr": "SSR", "vmess": "VMess", "vless": "VLESS",
        "trojan": "Trojan", "hysteria2": "HY2", "hysteria": "Hysteria", "tuic": "TUIC",
        "socks5": "SOCKS5", "http": "HTTP", "wireguard": "WireGuard", "anytls": "AnyTLS",
        "selector": "Group", "urltest": "Auto", "fallback": "Fallback", "loadbalance": "Balanced",
        "direct": "Direct", "reject": "Reject", "rejectdrop": "Reject",
    }.get(kind.lower(), kind)
    return {"country": "Unknown", "countryCode": "", "protocol": protocol}


def check_tun_conflicts():
    try:
        return networking.check(sys.modules[__name__])
    except networking.NetworkConflict as error:
        raise UserError(str(error)) from None


def set_capture(s, value):
    if value not in ("off", "system", "tun"):
        raise UserError("Choose Off, System proxy or Tunnel proxy.")
    if value != "off" and (not s["active"] or not alive(s)):
        raise UserError("Start Mihomo and activate a profile before enabling capture.")
    if value == "tun":
        check_tun_conflicts()
    previous = json.loads(json.dumps(s))
    previous_system = (DATA / "system-proxy.json").exists()
    try:
        # Disable the old path before enabling the new one: never both at once.
        system_proxy(s, False)
        target_tun = value == "tun"
        if s["tun"] != target_tun:
            s["tun"] = target_tun
            item = profile(s, s["active"])
            commit_profile(s, item, source_path(item["id"]).read_text())
        if value == "system":
            system_proxy(s, True)
    except Exception:
        try:
            if state()["tun"] != previous["tun"]:
                item = profile(previous, previous["active"])
                commit_profile(previous, item, source_path(item["id"]).read_text())
            system_proxy(previous, previous_system)
        except Exception:
            raise UserError("Capture recovery was incomplete. Stop the service and review proxy settings.") from None
        raise


BAR_DISPLAY_KEYS = ("speed", "region")


def bar_display(s):
    stored = s.get("barDisplay", {})
    if not isinstance(stored, dict):
        stored = {}
    return {"speed": stored.get("speed") is True,
            "region": stored.get("region", stored.get("country")) is True}


def poll_interval(visible, s):
    return 2 if bar_display(s)["speed"] else 5 if visible else 30


def status():
    s = state()
    version = alive(s) if CORE.exists() else None
    live = None
    result = {
        "installed": CORE.is_file() and HELPER.is_file() and SERVICE_FILE.is_file(),
        "running": bool(version), "version": (version or {}).get("version", ""),
        "passwordFreeControl": password_free_control(),
        "barDisplay": bar_display(s),
        "active": s["active"], "mode": s["mode"], "tun": s["tun"],
        "systemProxy": (DATA / "system-proxy.json").exists(), "interval": s["interval"],
        "profiles": [{k: p.get(k) for k in ("id", "name", "updated", "kind", "updateError")} for p in s["profiles"]],
        "groups": [], "upload": 0, "download": 0,
    }
    if s["active"]:
        item = profile(s, s["active"])
        result["rules"] = "\n".join(item.get("rules", []))
        result["ruleProviders"] = [{k: p[k] for k in ("target", "behavior", "format")} for p in item.get("ruleProviders", [])]
    if version:
        try:
            config = api(s, "/configs")
            result["mode"] = config.get("mode", s["mode"])
            result["tun"] = config.get("tun", {}).get("enable", s["tun"])
            proxies = proxy_inventory(s)
            live = proxies
            for name, group in proxies.items():
                if "all" not in group:
                    continue
                nodes = []
                for node in group["all"]:
                    info = proxies.get(node, {})
                    nodes.append({"name": node, **node_metadata(node, info)})
                kind = group.get("type")
                result["groups"].append({"name": name, "type": kind, "now": group.get("now", ""),
                                         "fixed": group.get("fixed", ""),
                                         "selectable": kind in ("Selector", "URLTest", "Fallback"), "nodes": nodes})
            connections = api(s, "/connections")
            result["upload"] = connections.get("uploadTotal", 0)
            result["download"] = connections.get("downloadTotal", 0)
        except UserError:
            result["warning"] = "Some controller data could not be refreshed."
    result["capture"] = "tun" if result["tun"] and version else "system" if result["systemProxy"] else "off"
    result["profileGroups"] = inventory_model.sections(sys.modules[__name__], s, live)
    return result


def dispatch(request):
    action = request.get("action")
    if action == "status":
        return status()
    with locked():
        s = state()
        if action == "setup":
            if request.get("confirmed") is not True:
                raise UserError("Explicit installation consent is required.")
            if SERVICE_FILE.exists():
                raise UserError("The managed service already exists. Use its Start or Restart action.")
            # Reserve our endpoints before installing; do not replace another proxy.
            for port in (s["mixedPort"], s["controllerPort"]):
                with socket.socket() as probe:
                    try:
                        probe.bind(("127.0.0.1", port))
                    except OSError:
                        raise UserError("A managed port is already in use. Stop the conflicting service first.") from None
            token()
            # Reinstallation must not silently reactivate a previously enabled TUN.
            s.update(active="", tun=False)
            atomic(DATA / "runtime.yaml", json.dumps({
                "mixed-port": s["mixedPort"], "bind-address": "127.0.0.1", "allow-lan": False,
                "external-controller": f"127.0.0.1:{s['controllerPort']}", "secret": token(),
                "mode": "direct", "rules": ["MATCH,DIRECT"], "log-level": "warning",
            }))
            save(s)
            privileged("install", str(DATA), "yes" if request.get("autostart") else "no",
                       "yes" if request.get("passwordFreeControl") else "no", bootstrap=True)
        elif action == "authorizeControl":
            if request.get("confirmed") is not True:
                raise UserError("Explicit authorization consent is required.")
            privileged("configure-control", bootstrap=True)
        elif action == "import":
            name = str(request.get("name", "")).strip()
            if not name or len(name) > 100:
                raise UserError("Enter a profile name of 1 to 100 characters.")
            url = str(request.get("url", "")).strip()
            text = download(url) if url else local_text(str(request.get("path", "")))
            item = {"id": uuid.uuid4().hex, "name": name, "kind": "subscription" if url else "file",
                    "url": url, "updated": time.time(), "rules": [], "ruleProviders": []}
            s["profiles"].append(item)
            commit_profile(s, item, text, activate=False, store_source=True)
        elif action in ("activate", "update", "editProfile", "rename", "delete"):
            item = profile(s, request.get("id"))
            if action == "activate":
                s["active"] = item["id"]
                commit_profile(s, item, source_path(item["id"]).read_text())
            elif action == "update":
                if not item.get("url"):
                    raise UserError("This is a local profile. Import the updated file instead.")
                text = download(item["url"])
                item.update(updated=time.time(), updateError=False)
                item.pop("attempted", None)
                commit_profile(s, item, text, activate=item["id"] == s["active"], store_source=True)
            elif action in ("rename", "editProfile"):
                name = str(request.get("name", "")).strip()
                if not name or len(name) > 100:
                    raise UserError("Enter a profile name of 1 to 100 characters.")
                item["name"] = name
                url = str(request.get("url", "")).strip() if action == "editProfile" else ""
                path = str(request.get("path", "")).strip() if action == "editProfile" else ""
                if url and path:
                    raise UserError("Choose either a subscription URL or a local file, not both.")
                if url or path:
                    text = download(url) if url else local_text(path)
                    item.update(kind="subscription" if url else "file", url=url, updated=time.time(), updateError=False)
                    item.pop("attempted", None)
                    commit_profile(s, item, text, activate=item["id"] == s["active"], store_source=True)
                else:
                    save(s)
            else:
                if s["active"] == item["id"]:
                    raise UserError("Activate another profile before deleting the active profile.")
                s["profiles"].remove(item)
                save(s)
                source_path(item["id"]).unlink(missing_ok=True)
        elif action in ("capture", "tun", "systemProxy"):
            value = request.get("value")
            if action != "capture":
                if not isinstance(value, bool):
                    raise UserError("Invalid capture setting.")
                value = ("tun" if action == "tun" else "system") if value else "off"
            set_capture(s, value)
        elif action in ("mode", "rules", "provider", "clearProviders"):
            item = profile(s, s["active"])
            if action == "mode":
                if request.get("value") not in ("rule", "global"):
                    raise UserError("Invalid routing mode.")
                s["mode"] = request["value"]
            elif action == "rules":
                text = local_text(request["path"]) if request.get("path") else str(request.get("text", ""))
                rules = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
                if len(text) > MAX_DOWNLOAD or any("," not in rule for rule in rules):
                    raise UserError("Use one Clash routing rule per line, such as DOMAIN-SUFFIX,example.com,DIRECT.")
                item["rules"] = rules
            elif action == "clearProviders":
                item["ruleProviders"] = []
            else:
                url, target = str(request.get("url", "")).strip(), str(request.get("target", "")).strip()
                check_url(url)
                behavior, fmt = request.get("behavior", "classical"), request.get("format", "yaml")
                if behavior not in ("classical", "domain", "ipcidr") or fmt not in ("yaml", "text", "mrs"):
                    raise UserError("Unsupported rule-set behavior or format.")
                if fmt == "mrs" and behavior == "classical":
                    raise UserError("MRS rule sets require domain or ipcidr behavior.")
                if not target or any(char in target for char in ",\r\n"):
                    raise UserError("Select a valid target policy group.")
                item.setdefault("ruleProviders", []).append({"url": url, "target": target, "behavior": behavior, "format": fmt})
            commit_profile(s, item, source_path(item["id"]).read_text())
        elif action == "select":
            group, node = str(request.get("group", "")), str(request.get("node", ""))
            api(s, "/proxies/" + urllib.parse.quote(group, safe=""), "PUT", {"name": node})
        elif action == "selectNode":
            item = profile(s, request.get("profile"))
            document, nodes, _ = inventory_model.read_profile(sys.modules[__name__], item)
            automatic = [group["name"] for group in (document.get("proxy-groups") or []) if group.get("type") in ("url-test", "fallback")]
            if not any(node["name"] == request.get("name") for node in nodes) and request.get("name") not in automatic:
                raise UserError("The selected node is no longer present in this profile.")
            item.update(selectedNode=request["name"], selectionPending=True)
            changed = s["active"] != item["id"]
            s["active"] = item["id"]
            if changed:
                commit_profile(s, item, source_path(item["id"]).read_text())
            else:
                save(s)
            if alive(s):
                apply_selection(s, item)
        elif action == "restoreSelection":
            if s["active"] and alive(s):
                item = profile(s, s["active"])
                if item.get("selectionPending"):
                    apply_selection(s, item)
        elif action == "resolveRegions":
            if request.get("confirmed") is not True:
                raise UserError("Confirm the external exit-region lookup first.")
            item = profile(s, request.get("profile"))
            _, nodes, _ = inventory_model.read_profile(sys.modules[__name__], item)
            def resolve(node):
                try:
                    return inventory_model.fingerprint(node), inventory_model.probe_region(sys.modules[__name__], node)
                except Exception:
                    return inventory_model.fingerprint(node), ""
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(resolve, nodes))
            resolved = 0
            updates = {}
            for key, code in results:
                if code:
                    resolved += 1
                    updates[key] = {"code": code, "updated": time.time()}
            probing_model.save_regions(sys.modules[__name__], updates)
            return {"message": f"Resolved {resolved} of {len(nodes)} exit regions. Unavailable nodes remain unverified."}
        elif action == "autoSelect":
            api(s, "/proxies/" + urllib.parse.quote(str(request.get("group", "")), safe=""), "DELETE")
        elif action in ("delay", "delayGroup", "delayProfile"):
            name = str(request.get("name", ""))
            profile_id = request.get("profile")
            if profile_id and (profile_id != s["active"] or not alive(s)):
                raise UserError("Start the core and activate this profile before testing latency.")
            query = urllib.parse.urlencode({"url": "https://www.gstatic.com/generate_204", "timeout": 5000})
            inventory = proxy_inventory(s)
            def test(node):
                try:
                    return node, api(s, latency_route(inventory, node, query), timeout=8).get("delay", 0)
                except UserError:
                    return node, 0
            if action == "delay":
                node, delay = test(name)
                key = inventory_model.node_key(profile_id, node) if profile_id else node
                return {"message": "Node latency: " + (str(delay) + " ms" if delay else "unavailable"),
                        "delays": {key: delay}, "latencySession": request.get("latencySession")}
            if action == "delayProfile":
                item = profile(s, profile_id)
                _, nodes, _ = inventory_model.read_profile(sys.modules[__name__], item)
                names = [node["name"] for node in nodes]
            else:
                names = api(s, "/proxies/" + urllib.parse.quote(name, safe="")).get("all", [])
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                delays = {inventory_model.node_key(profile_id, node) if profile_id else node: delay
                          for node, delay in pool.map(test, names)}
            return {"message": "Group latency test completed.", "delays": delays,
                    "latencySession": request.get("latencySession")}
        elif action in ("start", "stop", "restart"):
            if action == "stop":
                system_proxy(s, False)
            service_control(action)
            if action in ("start", "restart") and s["active"]:
                item = profile(s, s["active"])
                if item.get("selectionPending"):
                    for _ in range(30):
                        if alive(s):
                            apply_selection(s, item)
                            break
                        time.sleep(0.1)
        elif action == "barDisplay":
            key, value = request.get("key"), request.get("value")
            if key not in BAR_DISPLAY_KEYS or not isinstance(value, bool):
                raise UserError("Choose a valid bar display option and an on/off value.")
            options = bar_display(s)
            options[key] = value
            s["barDisplay"] = options
            save(s)
        elif action == "interval":
            if request.get("value") not in (0, 6, 12, 24):
                raise UserError("Choose disabled, 6, 12 or 24 hours.")
            s["interval"] = request["value"]
            save(s)
        elif action == "uninstall":
            if request.get("confirmed") is not True:
                raise UserError("Explicit removal consent is required.")
            if request.get("removeCore"):
                system_proxy(s, False)
                if HELPER.is_file():
                    privileged("uninstall")
                elif CORE.exists() or SERVICE_FILE.exists():
                    raise UserError("The managed installation is incomplete. Restore its helper before removal.")
            # No lifecycle hook exists in Omarchy. Always use this explicit path.
            if request.get("removePlugin"):
                # A detached user unit survives the shell unloading this backend.
                run(["systemd-run", "--user", "--collect", "--quiet",
                     "--unit=konoproxy-remove-" + uuid.uuid4().hex,
                     "/usr/bin/omarchy", "plugin", "remove", PLUGIN_ID, "--yes"])
            return {"message": "Removal completed. Profile data has been retained."}
        else:
            raise UserError("Unknown operation.")
    return {}


def scheduled_updates():
    s = state()
    if not s["interval"]:
        return
    now = time.time()
    for item in s["profiles"]:
        if item.get("url") and now - item.get("attempted", item["updated"]) >= s["interval"] * 3600:
            try:
                dispatch({"action": "update", "id": item["id"]})
            except UserError:
                with locked():
                    current = state()
                    p = profile(current, item["id"])
                    p.update(attempted=now, updateError=True)
                    save(current)


def response(request):
    try:
        return {"ok": True, "result": dispatch(request)}
    except UserError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Unexpected operation failure. Existing profile files have been retained."}


def serve():
    output_lock = threading.Lock()
    visible = False
    input_buffer = b""
    last_poll = last_update = 0
    selection_attempt = None
    def emit(value):
        with output_lock:
            print(json.dumps(value), flush=True)
    def work(request):
        emit({"event": "result", "action": request.get("action"), **response(request)})
    with probing_model.PanelProbes(sys.modules[__name__], emit) as probes, concurrent.futures.ThreadPoolExecutor(max_workers=1) as worker:
        pending = None
        while True:
            readable, _, _ = select.select([sys.stdin], [], [], 1)
            if readable:
                chunk = os.read(sys.stdin.fileno(), 65536)
                if not chunk:
                    return
                input_buffer += chunk
                if len(input_buffer) > MAX_DOWNLOAD * 2:
                    input_buffer = b""
                    emit({"event": "result", "ok": False, "error": "The request exceeds the size limit."})
            while b"\n" in input_buffer:
                line, input_buffer = input_buffer.split(b"\n", 1)
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError()
                    if request.get("action") == "visible":
                        visible = request.get("value") is True
                        probes.set_visible(visible, request.get("latencySession"))
                        last_poll = 0
                    elif pending is None or pending.done():
                        pending = worker.submit(work, request)
                    else:
                        emit({"event": "result", "ok": False, "error": "Another operation is still running."})
                except ValueError:
                    emit({"event": "result", "ok": False, "error": "Invalid request."})
            now = time.monotonic()
            idle = pending is None or pending.done()
            if idle and now - last_poll >= poll_interval(visible, state()):
                snapshot = response({"action": "status"})
                emit({"event": "state", **snapshot})
                last_poll = now
                if snapshot.get("ok") and snapshot["result"].get("running"):
                    saved = state()
                    item = profile(saved, saved["active"]) if saved["active"] else {}
                    attempt = (saved["active"], item.get("selectedNode"))
                    if item.get("selectionPending") and attempt != selection_attempt:
                        selection_attempt = attempt
                        pending = worker.submit(work, {"action": "restoreSelection"})
                else:
                    selection_attempt = None
            if (pending is None or pending.done()) and now - last_update >= 60:
                pending = worker.submit(scheduled_updates)
                last_update = now


if __name__ == "__main__":
    os.umask(0o077)
    if sys.argv[1:] == ["serve"]:
        serve()
    else:
        try:
            request = json.loads(sys.stdin.readline())
            print(json.dumps(response(request)))
        except ValueError:
            print(json.dumps({"ok": False, "error": "Invalid request."}))
