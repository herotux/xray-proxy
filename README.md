# xray-proxy

Reusable Python helpers for running [Xray-core](https://github.com/XTLS/Xray-core) locally and routing `requests` through VLESS/VMess nodes.

## Features

- Parse common `vless://` and `vmess://` share links into Xray outbound configuration.
- Start/stop a local Xray process with a loopback-only SOCKS5 listener.
- Use plain-text or Base64-encoded VLESS/VMess subscriptions.
- Request-level failover, periodic subscription refresh, and persisted node health/latency ranking.
- Retry safe read requests on transport errors and selected gateway errors; ordinary application 403 responses are returned unchanged.
- Optional Xray least-ping subscription balancing.

The Xray executable is a separate dependency and must be installed for your platform. This library does not bundle it.

## Install

Install from a local checkout:

```bash
python -m pip install '.[socks]'
```

Or install directly from GitHub (repository is private, so authenticate Git/pip first):

```bash
python -m pip install 'xray-proxy[socks] @ git+https://github.com/herotux/xray-proxy.git'
```

## Single node

```python
import requests
from xray_proxy import XrayProxy

with XrayProxy("vless://UUID@server.example:443?type=ws&security=tls&host=cdn.example&path=%2F") as proxy:
    session = requests.Session()
    proxy.configure_session(session)
    response = session.get("https://example.com/", timeout=30)
    print(response.status_code)
    session.close()
```

The SOCKS listener binds only to loopback by default (`127.0.0.1:10808`). Use `socks5h://` so DNS lookups go through the proxy.

## Subscription with failover

```python
from xray_proxy import SubscriptionFailoverSession

with SubscriptionFailoverSession(
    "https://provider.example/subscription",
    xray_path="xray",       # or an absolute path
    port=10808,
    timeout=12,
    max_nodes=200,
) as session:
    response = session.get("https://example.com/", timeout=30)
    print(response.status_code)
```

The subscription URL should return plain-text URI lines or a Base64-encoded list of `vless://` / `vmess://` links. Node ranking is stored under `~/.config/xray-proxy/node-ranking.json` by default. You can set `ranking_file` to override it.

## Subscription least-ping balancer

```python
from xray_proxy import SubscriptionProxy
import requests

with SubscriptionProxy("https://provider.example/subscription") as proxy:
    session = requests.Session()
    proxy.configure_session(session)
    response = session.get("https://example.com/", timeout=30)
    print(response.status_code)
    session.close()
```

This mode lets Xray's observatory/balancer choose among nodes. It is different from `SubscriptionFailoverSession`, which starts one node at a time and retries requests according to its retry policy.

The observatory uses an HTTP(S) URL to measure node latency (this is not an ICMP ping). By default, it probes YouTube's lightweight `https://www.youtube.com/generate_204` endpoint, which is blocked on many Iranian networks. Filtering varies by ISP and can change over time.

Choose another probe URL in code:

```python
with SubscriptionProxy(
    "https://provider.example/subscription",
    probe_url="https://example.org/generate_204",
) as proxy:
    ...
```

Or set an environment variable so the application can change it without editing code:

```bash
export XRAY_PROXY_PROBE_URL="https://www.youtube.com/generate_204"
```

An explicit `probe_url=` argument takes precedence over `XRAY_PROXY_PROBE_URL`. The environment variable is read when `SubscriptionProxy` is created.

## Supported URI formats

The parser supports VLESS and VMess, with TCP, WebSocket, gRPC, HTTP, and HTTPUpgrade transports, plus TLS and REALITY options represented by common share-link parameters. Unsupported transports/security values raise `ProxyError`.

## API

- `parse_proxy_uri(uri) -> dict`
- `ProxyError`
- `XrayProxy(uri, ...)`
- `SubscriptionProxy(subscription_url, ...)`
- `SubscriptionFailoverSession(subscription_url, ...)`

Use the context-manager form where possible so child processes, temporary configs, and sessions are cleaned up.

## Tests

```bash
python -m pip install -e '.[test]'
pytest
```

## Security notes

- The local SOCKS listener is deliberately restricted to loopback addresses.
- Temporary Xray config files are created with owner-only permissions where supported and removed when the proxy stops.
- Subscription URLs contain sensitive connection details; avoid logging or committing them.
- This is an unofficial helper library, not affiliated with XTLS/Xray-core.
