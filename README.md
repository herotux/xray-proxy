# xray-proxy

**Reusable Python helpers for running Xray-core locally and routing selected `requests` traffic through VLESS/VMess nodes.**

`xray-proxy` starts and manages an external Xray executable. It does not bundle Xray, install a device-wide VPN, or automatically change traffic outside the Python session you configure.

> Unofficial project. Not affiliated with XTLS/Xray-core or any proxy provider.

## Documentation

- [API reference and operating guide](docs/API_REFERENCE.md) — classes, parameters, supported URI formats, lifecycle, retries, ranking, troubleshooting.
- [AI-agent instructions](docs/AI_AGENT_GUIDE.md) — safety invariants and a required workflow for coding agents.
- [Repository agent rules](AGENTS.md) — concise instructions for any agent working in this repository.

## Features

- Parse common `vless://` and `vmess://` share links into Xray outbound configuration.
- Start/stop a local Xray child process with a loopback-only SOCKS5 listener.
- Configure a `requests.Session` to use `socks5h://127.0.0.1:10808` (or a chosen loopback port).
- Load plain-text or Base64 VLESS/VMess subscription responses.
- Choose between Xray's observatory/least-ping balancer and Python-level sequential failover.
- Persist request-level node health/latency rankings in a private local JSON file.
- Retry selected read requests after transport failures and gateway errors; distinguish ordinary application `403` responses from recognizable Cloudflare edge blocks.
- Run tests without contacting a real proxy provider.

## Requirements and installation

- Python 3.10+
- A separately installed Xray-core executable compatible with the host OS/architecture.
- `requests[socks]` when using the SOCKS listener with Requests.

Install from a checkout:

```bash
python -m pip install '.[socks]'
```

Install test dependencies and run tests:

```bash
python -m pip install -e '.[socks,test]'
python -m pytest -q
```

The package does **not** download or install Xray for you. Make sure the executable is available on `PATH` or pass its explicit path as `xray_path`.

## Choose the right mode

| Mode | What it does | Use it when | Important limitation |
|---|---|---|---|
| `XrayProxy` | Runs one supplied VLESS/VMess node and configures a Requests session | You know which node to use and want predictable routing | No automatic node failover |
| `SubscriptionProxy` | Loads supported nodes and lets Xray observatory + `leastPing` choose among them | You want Xray to balance new connections based on HTTP(S) probes | Not Python request-level retry; probe URL must be reachable through nodes |
| `SubscriptionFailoverSession` | Starts one node at a time and rotates after selected failures | You want explicit sequential failover and a persisted health/latency ranking | Retry policy is method/status-specific; it is not a generic retry wrapper |

Do not confuse the two subscription modes: `SubscriptionProxy` delegates node selection to Xray's balancer; `SubscriptionFailoverSession` manages one Xray process at a time and may reissue eligible requests.

## Quick start: one node

Store the full URI privately (for example in an environment secret or a file with owner-only permissions). Never commit real share links.

```python
import requests
from xray_proxy import XrayProxy

uri = "vless://UUID@server.example:443?type=ws&security=tls&host=cdn.example&path=%2F"

session = requests.Session()
try:
    with XrayProxy(uri, xray_path="/usr/local/bin/xray", port=10808) as proxy:
        proxy.configure_session(session)
        response = session.get("https://example.com/", timeout=30)
        response.raise_for_status()
        print(response.status_code)
finally:
    session.close()
```

Use a `with` block so the child process, temporary configuration, and log handle are cleaned up. `configure_session()` updates the session's `http` and `https` proxy entries. Only requests sent through that session are routed through this local proxy; this is not a system-wide VPN.

The local listener is restricted to loopback addresses by design. The default is `127.0.0.1:10808`. The URL uses `socks5h://`, so hostname resolution is delegated through the SOCKS proxy rather than resolved locally.

## Subscription mode: Xray least-ping balancer

```python
import requests
from xray_proxy import SubscriptionProxy

subscription_url = "https://provider.example/subscription"

session = requests.Session()
try:
    with SubscriptionProxy(
        subscription_url,
        xray_path="/usr/local/bin/xray",
        port=10808,
        probe_url="https://example.org/generate_204",
        probe_interval="10s",
        probe_warmup=10,
    ) as proxy:
        proxy.configure_session(session)
        response = session.get("https://example.com/", timeout=30)
        print(response.status_code)
finally:
    session.close()
```

The subscription endpoint must return newline-separated `vless://` / `vmess://` links or a Base64-encoded list of those links. The implementation reads at most 2 MB and considers the first 200 URI lines. Unsupported or malformed entries are skipped; if none remain, startup raises `ProxyError`.

The default probe URL is `https://www.youtube.com/generate_204`. It may be blocked or slow on some networks. Set a reachable HTTP(S) endpoint explicitly or use `XRAY_PROXY_PROBE_URL`:

```bash
export XRAY_PROXY_PROBE_URL="https://example.org/generate_204"
```

An explicit `probe_url=` argument takes precedence over the environment variable. The variable is read when the object is created. `probe_interval` is an Xray duration such as `5s`, `10s`, or `1m`; `probe_warmup` defaults to 10 seconds so the observatory can collect initial samples before the caller sends its first request.

This is HTTP(S)-based observatory probing, not ICMP ping. The configured endpoint receives recurring probes; choose it deliberately. See the [official Xray Observatory documentation](https://xtls.github.io/en/config/observatory.html).

## Subscription mode: sequential request failover

```python
from xray_proxy import SubscriptionFailoverSession

with SubscriptionFailoverSession(
    "https://provider.example/subscription",
    xray_path="/usr/local/bin/xray",
    port=10808,
    timeout=12,
    max_nodes=100,
) as session:
    response = session.get("https://example.com/", timeout=30)
    print(response.status_code)
```

This class provides Requests-style `get`, `head`, `options`, `post`, `put`, `patch`, `delete`, and `request` methods. It loads the subscription at initialization and refreshes it every 10 minutes when a later request checks the refresh timer. If refreshing fails, it keeps the current list/connection and postpones another refresh attempt for 10 minutes.

### Retry policy — read before using

| Situation | `GET` / `HEAD` / `OPTIONS` | `POST` / `PUT` / `PATCH` / `DELETE` |
|---|---|---|
| Connection/transport exception | May retry with another node | Not retried |
| HTTP 500, 502, 503, 504 | May retry with another node | Not retried by status alone |
| Ordinary HTTP 403 from the application | Returned unchanged | Returned unchanged |
| Recognizable Cloudflare edge-block HTTP 403 | May retry | **POST may retry** if the response is classified as an edge block |

**Important:** this implementation has a narrow exception to the general no-replay rule: it can retry a `POST` after a response that looks like a Cloudflare edge block. The intent is that an edge block occurs before the application processes the request, but detection is heuristic and no HTTP response can provide an absolute guarantee about remote state. Do not use this behavior for high-impact or irreversible POST operations unless the caller can safely tolerate the possibility of replay. Other non-idempotent methods are not retried.

An ordinary application-level `403` is not considered a proxy failure. A `403` or `429` can mean the destination is denying access or rate-limiting a user/account; do not respond by automatically rotating nodes or repeatedly retrying to evade a site restriction. Respect the destination's terms and access controls.

## Node ranking

`SubscriptionFailoverSession` reads and updates a JSON ranking file at:

```text
~/.config/xray-proxy/node-ranking.json
```

Override it with `ranking_file=` when constructing either subscription class. For failover sessions, the file stores a SHA-256 fingerprint of each URI, success status, observed latency, and check timestamp; it does not store the raw URI. The directory is created as needed and file permissions are restricted to owner read/write where supported. Ranking is an optimization, not a guarantee that a node will work.

`SubscriptionProxy` reads existing successful fingerprints to give those nodes a stable ordering preference at startup; runtime selection is still delegated to Xray's observatory/balancer. The class itself does not write new latency measurements to the ranking file.

## Supported share-link formats

The parser exports `parse_proxy_uri(uri) -> dict`, producing an Xray outbound object.

| Input | Supported behavior |
|---|---|
| VLESS | Requires UUID, hostname, and valid port; reads common query parameters such as `type`, `security`, `host`, `path`, `sni`, `fp`, `alpn`, `pbk`, `sid`, `spx`, `flow`, `serviceName` |
| VMess | Base64/URL-safe Base64 JSON with `add`, `id`, and valid `port`; supports common `net`, `tls`, `host`, `path`, `sni`, `alpn`, `aid`, and `scy` fields |
| Transport types | `tcp`, `ws`, `grpc`, `http`, `httpupgrade`; `h2` is normalized to `http` |
| VLESS security | `none`, `tls`, `reality` (REALITY requires a public key parameter such as `pbk`) |
| VMess security | This parser maps the common VMess `tls` field to `tls` or `none`; it does not implement every VMess extension |
| Subscription response | Plain-text lines or Base64 text containing supported VLESS/VMess URIs |

This is a practical parser for common share links, not a complete implementation of every Xray configuration field. Unsupported transport/security variants raise `ProxyError` or are skipped while loading subscriptions. Validate unusual links against the installed Xray-core version.

## Errors and troubleshooting

- **`ProxyError: Xray ... stopped`** — verify `xray_path`, run `xray version`, and test the generated node separately. Startup logs may contain Xray configuration diagnostics; do not publish raw logs if they include private endpoints or other sensitive details.
- **Local SOCKS port already in use** — choose another loopback port with `port=`; ensure all objects use the same port.
- **Requests reports missing SOCKS support** — install `python -m pip install 'requests[socks]'` or this package's `.[socks]` extra.
- **Subscription has no usable nodes** — check whether the URL is reachable, returns plain text/Base64 rather than an HTML login/error page, and contains supported URI schemes.
- **Balancer starts but requests fail** — try a reachable `probe_url`, confirm that the nodes are valid, and allow the configured warm-up. A successful local listener only proves Xray started, not that an exit node can reach every destination.
- **HTTP 403 or 429** — distinguish a destination/application restriction from a connection failure. These are HTTP responses, not proof that Xray is broken.
- **Subscription refresh fails** — the current node list remains in use; check provider reachability and credentials separately.

Do not disable TLS verification to "fix" proxy errors. Diagnose DNS, connectivity, the Xray executable, the node, and destination response independently.

## Security model

- SOCKS listeners bind only to loopback (`127.0.0.1`, `::1`, or `localhost`); non-loopback bind addresses are rejected.
- Temporary Xray JSON configuration files are created with owner-only permissions where supported and deleted on stop.
- Subscription URLs and share links are credentials. Do not commit them, print them, paste them into issue trackers, or include them in AI prompts.
- The node-ranking file stores URI fingerprints rather than raw share links.
- The library does not make traffic from unrelated apps use Xray; only configured Requests sessions are affected.
- HTTPS is still protected by normal certificate verification in Requests. Do not set `verify=False` as a routine workaround.
- A proxy can change the egress route; it does not make an account anonymous, guarantee access, or authorize bypassing a service's bans or access controls.

## API summary

- `parse_proxy_uri(uri) -> dict`
- `ProxyError`
- `XrayProxy(uri, xray_path="xray", host="127.0.0.1", port=10808, startup_timeout=12.0)`
- `SubscriptionProxy(subscription_url, ..., probe_url=None, probe_interval="5s", probe_warmup=10.0, ranking_file=None)`
- `SubscriptionFailoverSession(subscription_url, ..., timeout=12.0, ranking_file=None, max_nodes=200, session=None)`

For full signatures and lifecycle details, see [API reference](docs/API_REFERENCE.md).

## Development and tests

```bash
python -m pip install -e '.[socks,test]'
python -m pytest -q
python -m build --wheel
```

The GitHub Actions workflow runs tests on Python 3.10–3.13 and builds a wheel. Unit tests are designed not to require a real proxy server or subscription.

## License and attribution

See the repository's license file. Xray-core is a separate upstream project; this package invokes an installed Xray executable and is not affiliated with its maintainers.
