# Validation and development

## Reproduce the isolated suite

Use Python 3.11+ and Node.js 22+ for the JavaScript model checks. Node is a test
dependency only, not a runtime requirement for the plugin.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -B -m unittest discover -s tests -v
```

Tests use temporary directories, fake subscriptions, mocked administrator
operations and loopback-only listeners. They do not read production profile
data, install/start a system service or modify host routing or desktop proxies.
Optional real-core tests are skipped when `KONOPROXY_TEST_CORE` is unset. Tests
requiring Node are skipped if it is absent; CI explicitly installs Node.

To exercise a separately obtained, verified Mihomo binary:

```sh
KONOPROXY_TEST_CORE=/absolute/path/to/mihomo \
  .venv/bin/python -B -m unittest discover -s tests -v
```

The optional tests start unprivileged child cores with TUN disabled and use
temporary loopback ports. They never download a core automatically.

## Manifest and Qt checks

```sh
omarchy plugin validate .
/usr/lib/qt6/bin/qmllint -I /path/containing/qs \
  Service.qml Widget.qml ProxyPanel.qml CatIcon.qml
```

The import root must expose the installed Omarchy shell as `qs` and include the
Quickshell QML types. Use Qt 6, not the legacy Qt 5 `qmllint`. The shell exposes
some runtime QObject properties that static lint cannot resolve; a run with
`--missing-property disable` is not full static property validation.

## Validation record

Checked on 2026-10-23 on Omarchy Quattro:

- 128 tests passed with the optional real-core tests enabled.
- Manifest validation passed.
- Qt 6 lint passed with the installed shell's import environment and dynamic
  missing-property diagnostics disabled.
- Checked configuration rollback, failed-persistence recovery, profile editing,
  source isolation, desktop-proxy capture rollback and narrow service controls.
- Checked offline node inventories, panel-session cancellation, two-worker
  probe limits, late-result rejection and independent latency/region requests.
- Checked read-only split-Tailscale conflict detection and routing-order guards.
- Checked bar preference compatibility, region-before-speed order, compact
  indicator sizing, cat drawing commands, chest emblems and knob-center math.
- Reloaded the shell without plugin-specific runtime errors. Inspected the cat
  bar icon and its centered emblem on the running desktop.

The GitHub workflow runs Python 3.11 and 3.13 with Node 22. It does not run a
desktop session, install Mihomo, lint against Omarchy or enable host networking.

## Manual checks still required

- Real system-proxy/TUN transitions with the user's active VPNs, IPv4/IPv6,
  DNS, LAN routes, suspend/resume and failure recovery.
- Actual polkit authorization, service installation, boot startup and removal.
- Transition animation during real service/capture changes; automated tests
  check state confirmation and geometry, not every intermediate rendered frame.
- TUN ruffled-cat appearance and badge legibility across themes, scales and
  vertical bars. Drawing-command tests do not replace visual inspection.
- Many-profile scrolling, provider-only subscriptions and degraded external
  region/latency endpoints on representative production subscriptions.

Do not enable TUN, stop another VPN or uninstall a core solely to run a smoke
test without explicit user authorization. Keep real subscription URLs, node
credentials and controller secrets out of fixtures, screenshots and CI logs.
