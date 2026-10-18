# AI-agent guide for xray-proxy

This file is intended for coding agents, repository assistants, and autonomous maintenance tools. Read it before inspecting or changing proxy behavior. The source code and tests are authoritative if a sentence here becomes stale; update this guide in the same change when behavior changes.

## Mission and boundaries

`xray-proxy` is a Python wrapper around an external Xray-core executable. It parses supported VLESS/VMess share URIs, runs a local loopback SOCKS listener, configures Requests sessions, and offers two subscription strategies. It is not a system-wide VPN, anonymity guarantee, account-unban tool, or authorization bypass.

Do not claim that a proxy guarantees access, hides all identity, or prevents a destination from identifying an account. Do not recommend proxy rotation to evade an account ban, rate limit, CAPTCHA, or other destination access control. If a destination returns an account-level restriction, stop automated retries and advise the operator to use the destination's official appeal/support path.

## Repository map

- `xray_proxy/__init__.py`: public exports and package version.
- `xray_proxy/proxy.py`: URI parsing, `ProxyError`, `XrayProxy`, `SubscriptionProxy`.
- `xray_proxy/failover.py`: `SubscriptionFailoverSession`, ranking persistence, subscription refresh and request retry rules.
- `tests/test_proxy_uri.py`: parser/public API/loopback tests.
- `tests/test_failover.py`: selected retry classification behavior.
- `README.md`: install and quick start.
- `docs/API_REFERENCE.md`: detailed public behavior and operational notes.
- `pyproject.toml`: package metadata and extras.
- `.github/workflows/tests.yml`: Python matrix, tests, wheel build.

Do not assume a module or test filename exists based on a prior conversation. Check the repository tree or fetch the current file before editing.

## Hard invariants

1. **Loopback-only listener.** Keep the host validation that permits only `127.0.0.1`, `::1`, and `localhost`. Never change the default to `0.0.0.0` or expose the unauthenticated SOCKS listener to a LAN/public network.
2. **No secrets in source control or logs.** Real VLESS/VMess URIs, UUIDs, subscription URLs, tokens, credentials, cookies, and private host details must not appear in docs, tests, commits, examples, or diagnostic output. Use obviously fake domains and placeholder identifiers.
3. **TLS verification remains enabled.** Do not add `verify=False` as a workaround.
4. **Use context managers and clean up.** Changes must preserve child-process termination, temporary-config deletion, and log-handle closure on normal exits and failures.
5. **No blind replay of writes.** Preserve the current retry contract. Safe retry methods are `GET`, `HEAD`, and `OPTIONS`; transport errors and 500/502/503/504 can trigger failover for those methods. Ordinary application 403 and all 429 responses are not failover statuses. The current implementation has a narrow heuristic exception: a recognized Cloudflare edge-block 403 can cause retry even for POST. Treat that exception as a safety-sensitive behavior, document it, and add tests before changing it.
6. **Separate edge blocks from app restrictions.** A normal 403 is returned unchanged. Never reinterpret an account-level ban or rate limit as a dead proxy node.
7. **Do not silently expand protocol support.** Keep docs aligned with the parser's actual transport/security mappings. Unsupported formats should raise `ProxyError` or be skipped by subscription loading, not be guessed into a possibly invalid config.
8. **No network-dependent unit tests by default.** Tests should use fake responses/processes or parser fixtures; live proxy/provider tests must be explicit opt-in tests.
9. **Keep subscription loading behavior explicit.** Subscription URLs are fetched using `urllib.request` directly, before Xray routing exists. Do not claim that the subscription download itself uses the proxy unless the implementation is deliberately changed and tested.
10. **Ranking is advisory.** The failover ranking stores URI fingerprints, status, latency and timestamps—not raw URIs. Preserve this privacy property.

## Current public modes

### `XrayProxy`

Single explicit URI, one Xray child, local SOCKS5, no request retry and no remote-node health guarantee. `configure_session(session)` updates HTTP/HTTPS proxies. The context manager starts/stops the child.

### `SubscriptionProxy`

Downloads a plain-text or Base64 subscription, considers up to 200 supported URI lines, creates multiple Xray outbounds, and delegates runtime choice to Xray's observatory + `leastPing` balancer. Default probe URL is `https://www.youtube.com/generate_204`, overridden by constructor `probe_url`, then `XRAY_PROXY_PROBE_URL`, then the default. Default interval is `5s`; default warm-up is 10 seconds. It reads existing successful fingerprints for startup ordering but does not write ranking measurements.

### `SubscriptionFailoverSession`

Requests-like wrapper with one active Xray child at a time. Loads up to `max_nodes` (1–200), refreshes the list every 600 seconds when a request next checks the timer, and stores a local ranking file. It closes its underlying Requests session on `close()`, including a supplied session object.

## Required workflow for code changes

1. Read `pyproject.toml`, the relevant implementation file, and related tests before proposing a patch.
2. Identify whether the change affects URI parsing, child process lifecycle, local network exposure, subscription fetching, retries, ranking persistence, or probe behavior.
3. Write or update focused unit tests for every behavior change. Include negative cases and regression tests, especially for 403/429 and non-idempotent requests.
4. Keep examples fake and safe. Never use a real subscription URL or user's node URI.
5. Run:
   ```bash
   python -m pip install -e '.[socks,test]'
   python -m pytest -q
   ```
   Then run the wheel build:
   ```bash
   python -m pip install build
   python -m build --wheel
   ```
6. Review `git diff --check` and inspect the full diff for secrets, accidental behavior changes, and stale docs.
7. Update README/API/agent docs when public behavior, defaults, exceptions, or supported formats change.
8. Never claim tests passed unless the test command actually ran and its result is known.

## Troubleshooting workflow

When Xray exits during startup:

1. Verify `xray_path` and `xray version`.
2. Confirm the configured local port is available.
3. Reproduce with a fake URI in tests or a redacted/placeholder URI fixture.
4. Inspect only the relevant sanitized error lines; never paste credentials, raw URIs, subscription content, or full private configuration into issue comments.
5. Distinguish parser/config failure from a valid local listener whose remote node cannot reach a destination.

When a request receives 403/429:

1. Do not automatically switch nodes to bypass an account restriction.
2. Preserve the response unless the existing documented Cloudflare edge-block classifier applies.
3. Report the distinction between transport failure, recognizable edge challenge, and application-level denial.
4. For a confirmed account ban, stop automation and direct the user to the service's official support/appeal process.

## Documentation contract

Any public API or behavior change must update all applicable items:

- README quick start and mode comparison.
- `docs/API_REFERENCE.md` signatures, defaults, retry semantics and errors.
- This guide's hard invariants and workflow.
- Tests and CI assumptions.

If docs and source disagree, do not paper over the discrepancy. Determine intended behavior, add a regression test, update code/docs consistently, and clearly state any compatibility impact in the commit message.
