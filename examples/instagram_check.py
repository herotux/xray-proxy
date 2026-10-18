#!/usr/bin/env python3
"""Find a working Xray SOCKS endpoint for a public Instagram reachability check.

Input is either XRAY_PROXY_SUBSCRIPTION_URL or a private local subscription file.
Share links are never printed. The first working node is kept alive until Ctrl-C.
HTTP 403/429 is treated as a destination restriction/rate limit, not as a bad node.
"""
from __future__ import annotations

import base64
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import requests
from xray_proxy import ProxyError, XrayProxy, parse_proxy_uri

CHECK_URL = "https://www.instagram.com/robots.txt"
DEFAULT_SUBSCRIPTION_FILE = Path.home() / ".config/xray-proxy/subscription.txt"
DEFAULT_XRAY_PATH = str(Path.home() / "bin/xray")
DEFAULT_PORT = 10808
MAX_SUBSCRIPTION_BYTES = 2_000_000
MAX_NODES = 200


def load_subscription() -> list[str]:
    url = os.environ.get("XRAY_PROXY_SUBSCRIPTION_URL", "").strip()
    if url:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "xray-proxy-instagram-check/1.0"},
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(MAX_SUBSCRIPTION_BYTES + 1)
        if len(raw) > MAX_SUBSCRIPTION_BYTES:
            raise RuntimeError("Subscription is larger than 2 MB.")
        source = raw.decode("utf-8-sig", errors="replace")
    else:
        path = Path(os.environ.get("XRAY_PROXY_SUBSCRIPTION_FILE", DEFAULT_SUBSCRIPTION_FILE))
        if not path.is_file():
            raise RuntimeError(
                "Subscription file not found. Set XRAY_PROXY_SUBSCRIPTION_URL or "
                f"create {path} with one VLESS/VMess URI per line."
            )
        if path.stat().st_size > MAX_SUBSCRIPTION_BYTES:
            raise RuntimeError("Subscription file is larger than 2 MB.")
        source = path.read_text(encoding="utf-8-sig", errors="replace")

    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if lines and not any(line.startswith(("vless://", "vmess://")) for line in lines):
        try:
            decoded = base64.b64decode("".join(lines) + "===", validate=False).decode(
                "utf-8-sig", errors="replace"
            )
            if "://" in decoded:
                lines = [line.strip() for line in decoded.splitlines() if line.strip()]
        except (ValueError, UnicodeError):
            pass

    nodes: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if not line.startswith(("vless://", "vmess://")) or line in seen:
            continue
        try:
            parse_proxy_uri(line)
        except (ProxyError, ValueError, TypeError):
            continue
        seen.add(line)
        nodes.append(line)
        if len(nodes) >= MAX_NODES:
            break
    if not nodes:
        raise RuntimeError("No supported VLESS/VMess nodes found in the subscription.")
    return nodes


def main() -> int:
    xray_path = os.environ.get("XRAY_PATH", DEFAULT_XRAY_PATH)
    port = int(os.environ.get("XRAY_PROXY_PORT", str(DEFAULT_PORT)))
    nodes = load_subscription()
    print(f"Loaded {len(nodes)} supported nodes. Checking Instagram public endpoint…")

    for index, uri in enumerate(nodes, start=1):
        session = requests.Session()
        proxy = XrayProxy(uri, xray_path=xray_path, port=port)
        try:
            with proxy:
                proxy.configure_session(session)
                started = time.monotonic()
                try:
                    response = session.get(CHECK_URL, timeout=(10, 20), allow_redirects=True)
                except requests.RequestException as exc:
                    print(f"[{index}/{len(nodes)}] Connection check failed; trying next node ({type(exc).__name__}).")
                    continue

                elapsed = time.monotonic() - started
                if response.status_code in (403, 429):
                    print(
                        f"Instagram returned HTTP {response.status_code}. "
                        "This may be a destination restriction/rate limit; stopping without rotating."
                    )
                    return 2
                if 200 <= response.status_code < 400:
                    print(f"Working node found: {index}/{len(nodes)}")
                    print(f"Check URL: {CHECK_URL}")
                    print(f"HTTP status: {response.status_code}; response time: {elapsed:.2f}s")
                    print(f"Local SOCKS5 endpoint: {proxy.socks_url}")
                    print("The endpoint stays active until you press Ctrl-C.")
                    try:
                        while True:
                            time.sleep(3600)
                    except KeyboardInterrupt:
                        print("\nStopping Xray.")
                        return 0
                print(
                    f"[{index}/{len(nodes)}] Instagram returned HTTP {response.status_code}; "
                    "not treating this response as proof the node is broken."
                )
                return 2
        except (ProxyError, OSError, RuntimeError) as exc:
            print(f"[{index}/{len(nodes)}] Xray could not start ({type(exc).__name__}); trying next node.")
        finally:
            session.close()

    print("No node passed the public endpoint check.")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, urllib.error.URLError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
