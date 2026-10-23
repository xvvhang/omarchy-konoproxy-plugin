# Security and privacy

Konoproxy runs with the desktop user's permissions. Its explicitly authorized
installer creates a root-owned Mihomo binary and helper, plus a dedicated
system service with bounded network capabilities. TUN can change host routing
and DNS. Treat the core, subscription content and plugin updates as trusted
software, not as a sandbox for hostile configuration.

## Reporting a vulnerability

Use GitHub private vulnerability reporting when available. If it is unavailable,
open a minimal issue requesting a private reporting channel, without exploit
details or sensitive data. Do not publish an active subscription, controller
secret, proxy credential or private network configuration in a public issue.

For an ordinary bug, use the repository's Issues page and include the plugin,
Omarchy and core versions, reproduction steps, and sanitized error messages.

## Data to exclude from reports

- Subscription URLs, including query strings and account tokens.
- Profile YAML, server addresses, passwords, UUIDs, private keys and auth headers.
- Controller secrets and the private state directory at
  `~/.local/share/konoproxy/`.
- Unredacted controller IPC output, service logs, node names or screenshots
  that identify a subscription or private network.

The repository contains source and synthetic test fixtures, not production
profiles or a bundled proxy binary. The preview is an illustration using fictional
profile and node labels. Never copy runtime data into the checkout to prepare a report.

## External requests

Subscription refreshes contact the configured HTTPS source. Opening the panel
checks nodes through HTTPS Google latency and Cloudflare trace endpoints;
those services see the node exit IP. These checks do not measure bandwidth.
Core setup downloads the pinned release from the official Mihomo GitHub
release, and may install `python-yaml` through the system package manager.

## Safe removal

Use Settings → Uninstall for optional core/service cleanup. Native
`omarchy plugin remove` does not invoke cleanup and can leave the service and
proxying active. Profile data is retained. See the README before removal.
