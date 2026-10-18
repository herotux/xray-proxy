#!/usr/bin/env python3
"""Check Instagram reachability through the library's concurrent Xray balancer.

SubscriptionProxy lets Xray observatory probe many VLESS/VMess outbounds
concurrently and routes requests through its leastPing balancer. The script
does not launch one Xray process per node and never prints subscription links.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import requests
from xray_proxy import ProxyError, SubscriptionProxy

CHECK_URL = "https://www.instagram.com/robots.txt"
DEFAULT_SUBSCRIPTION_FILE = Path.home() / ".config/xray-proxy/subscription.txt"
DEFAULT_XRAY_PATH = str(Path.home() / "bin/xray")
DEFAULT_PORT = 10808
DEFAULT_PROBE_URL = "https://www.google.com/generate_204"


def load_subscription_url() -> str:
    url = os.environ.get("XRAY_PROXY_SUBSCRIPTION_URL", "").strip()
    if url:
        return url

    path = Path(os.environ.get("XRAY_PROXY_SUBSCRIPTION_FILE", DEFAULT_SUBSCRIPTION_FILE))
    if not path.is_file():
        raise RuntimeError(
            "Subscription URL not configured. Set XRAY_PROXY_SUBSCRIPTION_URL or "
            f"put one HTTPS subscription URL in {path}."
        )
    if path.stat().st_size > 2_000_000:
        raise RuntimeError("Subscription file is larger than 2 MB.")

    lines = [line.strip() for line in path.read_text(
        encoding="utf-8-sig", errors="replace"
    ).splitlines() if line.strip()]
    if len(lines) == 1 and lines[0].lower().startswith(("https://", "http://")):
        return lines[0]
    raise RuntimeError(
        "The current SubscriptionProxy API expects a subscription URL, not a local "
        "file of raw node links. Put the subscription URL in the configured file or "
        "set XRAY_PROXY_SUBSCRIPTION_URL."
    )


def progress_worker(stop_event: threading.Event, started: float) -> None:
    """Show honest live status while Xray probes nodes concurrently."""
    frames = "|/-\\"
    index = 0
    while not stop_event.wait(0.15):
        elapsed = time.monotonic() - started
        frame = frames[index % len(frames)]
        print(
            f"\r{frame} Xray Observatory is probing nodes concurrently"
            f" | elapsed {elapsed:5.1f}s",
            end="",
            flush=True,
        )
        index += 1
    elapsed = time.monotonic() - started
    print(f"\r✓ Balancer startup/warm-up completed in {elapsed:.1f}s" + " " * 24)


def main() -> int:
    subscription_url = load_subscription_url()
    xray_path = os.environ.get("XRAY_PATH", DEFAULT_XRAY_PATH)
    port = int(os.environ.get("XRAY_PROXY_PORT", str(DEFAULT_PORT)))
    probe_url = os.environ.get("XRAY_PROXY_PROBE_URL", DEFAULT_PROBE_URL)
    warmup = float(os.environ.get("XRAY_PROXY_PROBE_WARMUP", "10"))
    interval = os.environ.get("XRAY_PROXY_PROBE_INTERVAL", "5s")

    proxy = SubscriptionProxy(
        subscription_url,
        xray_path=xray_path,
        port=port,
        probe_url=probe_url,
        probe_interval=interval,
        probe_warmup=warmup,
    )
    session = requests.Session()
    started = time.monotonic()
    stop_progress = threading.Event()
    worker = threading.Thread(
        target=progress_worker, args=(stop_progress, started), daemon=True
    )

    try:
        print("Starting Xray's concurrent Observatory / leastPing balancer.")
        print(f"Probe endpoint: {probe_url}")
        print(f"Probe interval: {interval}; warm-up: {warmup:g}s")
        worker.start()
        proxy.start()
        stop_progress.set()
        worker.join()
        session = proxy.configure_session(session)

        # This single request is a real destination check. Observatory probes
        # are concurrent; this request confirms the selected route can reach Instagram.
        check_started = time.monotonic()
        try:
            response = session.get(CHECK_URL, timeout=(10, 20), allow_redirects=True)
        except requests.RequestException as exc:
            print(
                f"Instagram reachability check failed ({type(exc).__name__}). "
                "The balancer started, but no successful Instagram response was confirmed."
            )
            return 2

        elapsed = time.monotonic() - check_started
        if response.status_code in (403, 429):
            print(
                f"Instagram returned HTTP {response.status_code}. "
                "This is a destination restriction/rate limit, not proof that all nodes are broken. "
                "Stopping without automatic rotation."
            )
            return 2
        if not 200 <= response.status_code < 400:
            print(
                f"Instagram returned HTTP {response.status_code}; "
                "the selected route was not confirmed as working."
            )
            return 2

        print("Instagram reachability check passed.")
        print(f"HTTP status: {response.status_code}; response time: {elapsed:.2f}s")
        print(f"Local SOCKS5 endpoint: {proxy.socks_url}")
        print("Xray's leastPing balancer remains active; new connections can use its current best node.")
        print("Keep this process running. Press Ctrl-C to stop Xray.")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopping Xray.")
        return 0
    except (ProxyError, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        stop_progress.set()
        if worker.is_alive():
            worker.join(timeout=1)
        session.close()
        proxy.stop()


if __name__ == "__main__":
    raise SystemExit(main())
