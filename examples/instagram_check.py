#!/usr/bin/env python3
"""Check Instagram reachability through the library's concurrent Xray balancer.

Xray Observatory probes supported VLESS/VMess outbounds concurrently. The
leastPing balancer ranks routes using the configured HTTP(S) probe endpoint.
The script never prints subscription links.
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
# Rank candidates against the actual destination, not Google, so the probe
# better reflects whether the route can reach Instagram.
DEFAULT_PROBE_URL = CHECK_URL


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

    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if line.strip()
    ]
    if len(lines) == 1 and lines[0].lower().startswith(("https://", "http://")):
        return lines[0]
    raise RuntimeError(
        "SubscriptionProxy expects a subscription URL, not a local file of raw node links. "
        f"Put one subscription URL in {path} or set XRAY_PROXY_SUBSCRIPTION_URL."
    )


def progress_worker(stop_event: threading.Event, started: float) -> None:
    """Use a single-line spinner on a TTY and readable status lines in captured output."""
    frames = "|/-\\"
    index = 0
    interactive = sys.stdout.isatty()
    while not stop_event.wait(0.2):
        elapsed = time.monotonic() - started
        frame = frames[index % len(frames)]
        message = (
            f"{frame} Xray Observatory probing concurrently"
            f" | elapsed {elapsed:5.1f}s"
        )
        if interactive:
            sys.stdout.write("\r" + message)
            sys.stdout.flush()
        elif index == 0 or index % 10 == 0:
            print(message, flush=True)
        index += 1

    elapsed = time.monotonic() - started
    if interactive:
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()
    print(f"Balancer startup/warm-up completed in {elapsed:.1f}s.")


def main() -> int:
    subscription_url = load_subscription_url()
    xray_path = os.environ.get("XRAY_PATH", DEFAULT_XRAY_PATH)
    port = int(os.environ.get("XRAY_PROXY_PORT", str(DEFAULT_PORT)))
    probe_url = os.environ.get("XRAY_PROXY_PROBE_URL", DEFAULT_PROBE_URL)
    warmup = float(os.environ.get("XRAY_PROXY_PROBE_WARMUP", "15"))
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
        target=progress_worker,
        args=(stop_progress, started),
        daemon=True,
    )

    try:
        print("Starting Xray concurrent Observatory / leastPing balancer.")
        print(f"Probe endpoint: {probe_url}")
        print(f"Probe interval: {interval}; warm-up: {warmup:g}s")
        print("Note: the spinner reports startup/warm-up, not a per-node percentage.")
        worker.start()
        proxy.start()
        stop_progress.set()
        worker.join()
        proxy.configure_session(session)

        # Observatory probes all configured outbounds concurrently. This GET
        # separately confirms the selected route can reach Instagram.
        check_started = time.monotonic()
        try:
            response = session.get(
                CHECK_URL,
                timeout=(15, 30),
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            print(
                f"Instagram check failed: {type(exc).__name__}: {exc}. "
                "Xray started, but Instagram reachability was not confirmed."
            )
            print("The proxy will be stopped; no automatic rotation or repeated retries.")
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
        print("The leastPing balancer remains active. Keep this process running.")
        print("Press Ctrl-C to stop Xray.")
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
