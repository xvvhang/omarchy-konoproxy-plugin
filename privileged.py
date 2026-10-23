#!/usr/bin/env python3
"""Explicitly authorized installer and fixed-operation service helper.

The bootstrap copy is run only after installation consent. Subsequent operations
use the root-owned installed copy; no user-supplied commands are accepted.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path("/usr/local/lib/konoproxy")
SYSTEMD = Path("/etc/systemd/system")
POLKIT = Path("/etc/polkit-1/rules.d")
VERSION = "v1.19.33"
ASSETS = {
    "x86_64": ("amd64-compatible", "386508bfb1c72c43b6a0eda73708ecdce82546581b0ebcb16366c53757c622b6"),
    "aarch64": ("arm64", "ba408b317080ee265c46cb923a50018c1b82cbd708551cdd46a1e3e307396187"),
}


def command(args, check=True, timeout=90):
    return subprocess.run(args, check=check, timeout=timeout, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def identity():
    if os.geteuid() != 0:
        raise ValueError("Administrator authorization is required.")
    # Never accept a target UID as an argument or fall back to root.
    uid = int(os.environ.get("PKEXEC_UID", "0"))
    if uid < 1000:
        raise ValueError("Run this helper through pkexec from a regular user account.")
    return pwd.getpwuid(uid)


def safe_data(value, user):
    path = Path(value)
    home = Path(user.pw_dir)
    if not path.is_absolute() or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
        raise ValueError("The data path must be an absolute path without special characters.")
    if path.resolve() != path or not path.is_relative_to(home) or path.name != "konoproxy":
        raise ValueError("The data directory must be a non-symlink konoproxy directory inside the user's home.")
    if not path.is_dir() or path.stat().st_uid != user.pw_uid:
        raise ValueError("The data directory must already exist and belong to the requesting user.")
    if not (path / "runtime.yaml").is_file():
        raise ValueError("Initialize the managed runtime configuration first.")
    return path


def service_text(user, data, autostart):
    # Paths are constrained by safe_data; numeric identities cannot inject unit fields.
    return f"""# Managed by Konoproxy. Do not edit while the plugin is running.
[Unit]
Description=Konoproxy Mihomo for user {user.pw_uid}
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User={user.pw_uid}
Group={user.pw_gid}
WorkingDirectory={data}
ExecStart={ROOT}/mihomo -d {data} -f {data}/runtime.yaml
ExecStopPost=/usr/bin/python3 -I {ROOT}/privileged.py cleanup
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
KillMode=control-group
UMask=0077
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={data}
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK

[Install]
WantedBy=multi-user.target
"""


def root_write(path, content, mode):
    # ROOT and SYSTEMD are administrator-owned directories, not user staging areas.
    fd, temporary = tempfile.mkstemp(prefix=".konoproxy-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def control_rule(user):
    return f"""// Managed by Konoproxy for UID {user.pw_uid}.
polkit.addRule(function(action, subject) {{
    if (action.id === "org.freedesktop.systemd1.manage-units"
        && subject.user === {json.dumps(user.pw_name)} && subject.local && subject.active
        && action.lookup("unit") === "konoproxy-{user.pw_uid}.service"
        && ["start", "stop", "restart"].indexOf(action.lookup("verb")) !== -1) {{
        return polkit.Result.YES;
    }}
}});
"""


def install_control(user):
    path = POLKIT / f"49-konoproxy-{user.pw_uid}.rules"
    if path.exists() and not path.read_text().startswith("// Managed by Konoproxy"):
        raise ValueError("The target authorization rule is not managed by Konoproxy.")
    root_write(path, control_rule(user).encode(), 0o644)


def remove_control(user):
    path = POLKIT / f"49-konoproxy-{user.pw_uid}.rules"
    if path.exists():
        if not path.read_text().startswith("// Managed by Konoproxy"):
            raise ValueError("The authorization rule has been replaced by an administrator.")
        path.unlink()


def configure_control(user, owner):
    paths = [POLKIT / f"49-konoproxy-{user.pw_uid}.rules", ROOT / "privileged.py", ROOT / "owner.json"]
    originals = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        install_control(user)
        root_write(ROOT / "privileged.py", Path(__file__).read_bytes(), 0o644)
        updated = dict(owner, passwordFreeControl=True)
        root_write(ROOT / "owner.json", json.dumps(updated).encode(), 0o644)
    except Exception:
        for path, previous in originals.items():
            current = path.read_bytes() if path.exists() else None
            if current == previous:
                continue
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                root_write(path, previous, 0o644)
        raise


def fetch_core():
    asset = ASSETS.get(platform.machine())
    if not asset:
        raise ValueError("Only x86_64 and aarch64 are supported.")
    architecture, checksum = asset
    name = f"mihomo-linux-{architecture}-{VERSION}.gz"
    url = f"https://github.com/MetaCubeX/mihomo/releases/download/{VERSION}/{name}"
    # Ignore ambient proxy variables for the root downloader.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=60) as response:
        compressed = response.read(64 * 1024 * 1024 + 1)
    if len(compressed) > 64 * 1024 * 1024 or hashlib.sha256(compressed).hexdigest() != checksum:
        raise ValueError("Mihomo checksum verification failed.")
    binary = gzip.decompress(compressed)
    if len(binary) > 160 * 1024 * 1024:
        raise ValueError("The decompressed core exceeds the size limit.")
    return binary


def cleanup_network(uid):
    # These indices and interface names are reserved by the managed compiler.
    # Never flush the host routing table, firewall, DNS configuration or other TUNs.
    table = str(200000 + uid)
    for family in ("-4", "-6"):
        for _ in range(16):
            if command(["/usr/bin/ip", family, "rule", "del", "table", table], check=False).returncode:
                break
        command(["/usr/bin/ip", family, "route", "flush", "table", table], check=False)
    command(["/usr/bin/ip", "link", "delete", f"kp{uid}"], check=False)


def install(user, data, autostart, password_free=False):
    if any(SYSTEMD.glob("konoproxy-*.service")):
        raise ValueError("A managed service already exists. Remove it explicitly before reinstalling.")
    if ROOT.is_symlink():
        raise ValueError("The installation directory must not be a symlink.")
    ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
    if ROOT.stat().st_uid != 0 or ROOT.stat().st_mode & 0o022:
        raise ValueError("The installation directory must be administrator-owned and not writable by other users.")
    if any(ROOT.iterdir()):
        raise ValueError("The installation directory is not empty. Review the existing installation first.")
    # Fetch and verify before touching dependencies or installing the executable.
    binary = fetch_core()
    command(["/usr/bin/pacman", "-S", "--needed", "--noconfirm", "python-yaml"], timeout=180)
    unit = SYSTEMD / f"konoproxy-{user.pw_uid}.service"
    control_written = False
    try:
        root_write(ROOT / "mihomo", binary, 0o755)
        root_write(ROOT / "privileged.py", Path(__file__).read_bytes(), 0o644)
        root_write(ROOT / "owner.json", json.dumps({"uid": user.pw_uid, "data": str(data), "version": VERSION,
                                                    "passwordFreeControl": password_free}).encode(), 0o644)
        root_write(unit, service_text(user, data, autostart).encode(), 0o644)
        if password_free:
            install_control(user)
            control_written = True
        command(["/usr/bin/systemctl", "daemon-reload"])
        if autostart:
            command(["/usr/bin/systemctl", "enable", unit.name])
        command(["/usr/bin/systemctl", "start", unit.name])
        command(["/usr/bin/systemctl", "is-active", "--quiet", unit.name])
    except Exception:
        if control_written:
            remove_control(user)
        command(["/usr/bin/systemctl", "disable", "--now", unit.name], check=False)
        unit.unlink(missing_ok=True)
        command(["/usr/bin/systemctl", "daemon-reload"], check=False)
        for name in ("mihomo", "owner.json", "privileged.py"):
            (ROOT / name).unlink(missing_ok=True)
        raise


def main(args):
    if args == ["cleanup"] and os.geteuid() >= 1000:
        # systemd supplies the same bounded ambient capabilities as the core.
        # Outside the unit, a regular process cannot change network state.
        cleanup_network(os.geteuid())
        return
    user = identity()
    if not args:
        raise ValueError("A fixed operation is required.")
    action = args[0]
    if action == "install":
        if len(args) != 4 or args[2] not in ("yes", "no") or args[3] not in ("yes", "no"):
            raise ValueError("Invalid installation arguments.")
        install(user, safe_data(args[1], user), args[2] == "yes", args[3] == "yes")
        return
    if len(args) != 1 or action not in ("start", "stop", "restart", "uninstall", "configure-control"):
        raise ValueError("Unsupported service operation.")
    owner = json.loads((ROOT / "owner.json").read_text())
    if owner["uid"] != user.pw_uid:
        raise ValueError("Only the owner can manage this installation.")
    unit = f"konoproxy-{user.pw_uid}.service"
    if action == "configure-control":
        if not (SYSTEMD / unit).is_file():
            raise ValueError("The managed unit must exist before authorizing control.")
        configure_control(user, owner)
    elif action == "uninstall":
        command(["/usr/bin/systemctl", "disable", "--now", unit])
        cleanup_network(user.pw_uid)
        remove_control(user)
        (SYSTEMD / unit).unlink(missing_ok=True)
        command(["/usr/bin/systemctl", "daemon-reload"])
        for name in ("mihomo", "owner.json", "privileged.py"):
            (ROOT / name).unlink(missing_ok=True)
        # Leave package-managed python-yaml installed; other software may use it.
    elif action in ("stop", "restart"):
        command(["/usr/bin/systemctl", "stop", unit])
        cleanup_network(user.pw_uid)
        if action == "restart":
            command(["/usr/bin/systemctl", "start", unit])
    else:
        command(["/usr/bin/systemctl", "start", unit])


if __name__ == "__main__":
    os.umask(0o022)
    try:
        main(sys.argv[1:])
    except Exception:
        print("Konoproxy service operation failed. No arbitrary commands were executed.", file=sys.stderr)
        sys.exit(1)
