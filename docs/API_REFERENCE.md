# API reference and operating guide

This document describes the public API implemented by the current `main` branch. The library is small, but its three runtime modes have different routing, balancing, retry, and cleanup semantics. Read those differences before selecting a class.

## Contents

- [Shared requirements](#shared-requirements)
- [`parse_proxy_uri`](#parse_proxy_uri)
- [`XrayProxy`](#xrayproxy)
- [`SubscriptionProxy`](#subscriptionproxy)
- [`SubscriptionFailoverSession`](#subscriptionfailoversession)
- [Ranking data](#ranking-data)
- [Resource lifecycle](#resource-lifecycle)
- [Error reference](#error-reference)
- [Operational checklist](#operational-checklist)

## Shared requirements

- Python 3.10 or newer.
- Xray-core executable installed separately; default executable name is `xray`.
- Install SOCKS support using `python -m pip install '.[socks]'`.
- The proxy listener must be local-only. The classes reject bind hosts other than `127.0.0.1`, `::1`, and `localhost`.
- Default local port: `10808`. If multiple proxy objects may overlap in one process, allocate distinct ports.

The library creates an Xray child process and a temporary JSON configuration. It does not install a VPN profile or modify global routing.

## `parse_proxy_uri`

```python
from xray_proxy import parse_proxy_uri, ProxyError

outbound = parse_proxy_uri("vless://...")
```

Returns an Xray outbound configuration dictionary for a supported VLESS or VMess share URI. Raises `ProxyError` for unsupported schemes, transports, security modes, or required fields; malformed ports may also raise `ValueError` from URL parsing/conversion.

### VLESS parameters

The implementation reads:

- Authority: UUID/user ID, hostname, and port.
- `type`: `tcp`, `ws`, `grpc`, `http`, `httpupgrade`; `h2` maps to `http`.
- `security`: `none`, `tls`, or `reality`.
- `host`, `path`, `serviceName` / `service_name`.
- `sni` / `serverName`, `fp`, `alpn`.
- `pbk` / `publicKey`, `sid` / `shortId`, `spx` / `spiderX`.
- `encryption` and optional `flow`.

REALITY requires a public key. Defaults and aliases are intentionally practical rather than exhaustive. Always validate a complex share link with the Xray version you actually deploy.

### VMess fields

The URI payload is Base64 or URL-safe Base64 encoded JSON. Required fields are `add`, `id`, and a valid integer `port`. Common fields include `aid`, `scy`, `net`, `tls`, `host`, `path`, `sni`, and `alpn`.

VMess parsing currently maps the common `tls` value to TLS or no TLS; it does not model every Xray VMess extension. Subscription parsing ignores unsupported entries rather than trying to infer missing settings.

## `XrayProxy`

Signature:

```python
XrayProxy(
    uri,
    *,
    xray_path="xray",
    host="127.0.0.1",
    port=10808,
    startup_timeout=12.0,
)
```

### Lifecycle

1. `start()` parses the URI, creates a temporary config with owner-only permissions where supported, and starts `xray run -c <config>`.
2. Startup polls for a local TCP listener until the startup deadline.
3. `configure_session(session)` adds the local `socks5h://host:port` endpoint to the session's `http` and `https` proxies.
4. `stop()` terminates the child process (then kills it if necessary), removes the temporary config, and closes the log handle.
5. The preferred usage is `with XrayProxy(...) as proxy:`; the context manager starts and then stops the proxy.

### Properties and methods

- `socks_url`: local SOCKS5 URL, using `socks5h://` to proxy DNS resolution.
- `start() -> XrayProxy`: starts the child process, idempotent while its current child remains alive.
- `configure_session(session)`: updates the session's `http` and `https` proxy mappings and returns the same session.
- `stop() -> None`: releases the child process and temporary resources.
- `__enter__` / `__exit__`: context-manager lifecycle.

This mode does not retry requests or detect a dead remote node after startup. A listening local port means the local Xray process started; it does not guarantee that the remote node is reachable.

## `SubscriptionProxy`

Signature:

```python
SubscriptionProxy(
    subscription_url,
    *,
    xray_path="xray",
    host="127.0.0.1",
    port=10808,
    startup_timeout=12.0,
    probe_url=None,
    probe_interval="5s",
    probe_warmup=10.0,
    ranking_file=None,
)
```

### Subscription loading

- The subscription is downloaded with Python's `urllib.request` before Xray starts. **That download is direct, not routed through the proxy being created.**
- The response read is capped at 2,000,000 bytes.
- Supported content is newline-separated VLESS/VMess URIs or a Base64-encoded list.
- The first 200 supported URI lines are considered. Invalid/unsupported entries are skipped.
- The subscription endpoint should be an HTTPS URL controlled by a trusted provider.

### Balancing

The generated Xray config includes an observatory and a `leastPing` balancer. Xray measures HTTP(S) probe latency for tagged outbounds; it is not an ICMP ping. `probe_url` must start with `http://` or `https://`.

- `probe_url`: explicit URL, if provided; otherwise use `XRAY_PROXY_PROBE_URL`, then default to `https://www.youtube.com/generate_204`.
- `probe_interval`: Xray duration string, default `5s`.
- `probe_warmup`: seconds to wait after the listener first becomes available, default `10.0`.
- `ranking_file`: path used to read existing successful fingerprints for startup ordering. This class does not update the ranking file itself.

The environment variable is read at object construction time; an explicit argument wins. Recurring probes can be visible to the probe endpoint and may create a traffic pattern. Pick a stable, appropriate endpoint and interval.

### Methods

- `socks_url`
- `start() -> SubscriptionProxy`
- `configure_session(session)`
- `stop()`
- context-manager methods

This class does not expose Python-level request retries or automatic subscription refresh after startup. The balancer chooses among outbounds based on Xray observatory state.

## `SubscriptionFailoverSession`

Signature:

```python
SubscriptionFailoverSession(
    subscription_url,
    *,
    xray_path="xray",
    host="127.0.0.1",
    port=10808,
    timeout=12.0,
    ranking_file=None,
    max_nodes=200,
    session=None,
)
```

This object presents a Requests-like interface and manages one active `XrayProxy` at a time.

### Loading and refresh

- Downloads subscription text directly using `urllib.request`; the subscription fetch itself is not routed through Xray.
- Reads at most 2,000,000 bytes and accepts supported VLESS/VMess URI lines or Base64 content.
- `max_nodes` must be 1–200 and caps accepted candidates.
- Sorts known successful nodes before unmeasured/failed nodes, then by recorded latency.
- Refreshes the subscription list after 600 seconds have elapsed when a request next checks the refresh timer. A failed refresh keeps the previous list/connection and postpones another attempt for 600 seconds.
- If the currently active URI remains in a refreshed list, it is moved to the first position and retained as active; otherwise the active process is stopped and the next request selects a node.

### Request methods

The class provides `request`, `get`, `head`, `options`, `post`, `put`, `patch`, and `delete`. Additional attributes are delegated to its underlying `requests.Session` through `__getattr__`.

Every request activates a node if needed, times the response, and saves the result. The retry behavior is intentionally limited:

- Safe methods are exactly `GET`, `HEAD`, and `OPTIONS`.
- For safe methods, transport exceptions and HTTP `500`, `502`, `503`, `504` may trigger another node.
- An ordinary HTTP `403` is returned to the caller.
- A `403` is classified as a Cloudflare edge block only when the headers suggest Cloudflare and the body contains a recognized marker (for example, `Attention Required`, `please enable cookies`, or `sorry, you have been blocked`).
- The implementation can retry a request receiving a recognized Cloudflare edge-block response, including `POST`. This is a deliberate, narrow exception; classification is heuristic. Do not assume a non-idempotent request is universally safe to retry.
- `PUT`, `PATCH`, and `DELETE` are not retried by the ordinary status/transport policy.

A destination's account ban, authorization denial, or rate limit is not a proxy health problem. Never build automatic rotation/retry logic intended to evade a destination's access controls. Stop and report the original response when it represents a restriction.

### Ranking file

Default path: `~/.config/xray-proxy/node-ranking.json`.

Each stored entry includes a SHA-256 URI fingerprint, `ok`, `latency`, and `checked_at`. Raw subscription URIs are not stored. Writes use a temporary sibling file followed by replacement; the directory is created as needed and the final file is chmod'ed to `0600` where supported. Persistence failures are ignored because ranking is an optimization and should not prevent a request.

### Ownership and close

`close()` stops the active Xray process, closes the underlying Requests session, and marks the wrapper closed. This includes a caller-supplied `session=` object. Use a context manager and do not reuse the object after close.

## Ranking data

The two subscription classes share the default ranking path but use it differently:

- `SubscriptionFailoverSession` both reads and writes results.
- `SubscriptionProxy` reads successful fingerprints for stable startup ordering; Xray's live observatory/balancer makes runtime decisions.

The JSON schema is an internal optimization, not a public interchange format. Do not edit it manually unless you know the implementation details. Delete or move it if you want to reset historical preferences; active processes do not need it for correctness.

## Resource lifecycle

- Use context managers for `XrayProxy`, `SubscriptionProxy`, and `SubscriptionFailoverSession`.
- Close any separately created `requests.Session` you own.
- Avoid launching multiple proxies on the same local port simultaneously.
- Do not kill processes by broad name patterns; stop the object that owns the process.
- If the Python process is interrupted abruptly, cleanup handlers may not run. Inspect only the process you started and remove leftover temporary files only after confirming they are no longer in use.

## Error reference

| Error | Meaning / next step |
|---|---|
| `ProxyError: only vless:// and vmess://...` | URI scheme is unsupported; select a supported share link. |
| `ProxyError: ... URI ...` | Required fields or transport/security are invalid/unsupported; inspect the scheme without printing credentials. |
| `ProxyError: Xray did not start` | Check the executable path/version, config compatibility, port availability, and sanitized logs. |
| `ProxyError: local proxy not ready` | Local listener did not appear before the deadline; increase startup timeout only if startup is genuinely slow. |
| Subscription download error | Check URL reachability, TLS, provider availability, and whether the endpoint returned an HTML error page. |
| HTTP 403 | Usually a destination response. Ordinary 403 is not retried; investigate account/access policy instead of rotating blindly. |
| HTTP 429 | Rate limiting; respect Retry-After and destination rules. The library does not classify 429 as a failover status. |

## Operational checklist

Before deploying:

1. Verify the installed Xray binary with `xray version`.
2. Install SOCKS extras.
3. Validate a single URI with `parse_proxy_uri` without printing it.
4. Test one harmless HTTPS GET through the chosen mode.
5. Confirm the local listener binds only to loopback.
6. Choose a probe URL reachable through intended nodes.
7. Ensure share links and subscription URLs are stored outside source control.
8. Review the retry table if any request can mutate remote state.
9. Test cleanup by leaving the context manager normally and confirming the local process exits.
10. Treat destination-level 403/429 responses as access-control/rate-limit signals, not as automatic instructions to rotate proxies.
