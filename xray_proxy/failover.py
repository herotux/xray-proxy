"""Request-level failover and periodic refresh for subscription nodes.

Only safe read methods (GET/HEAD/OPTIONS) may be retried. Non-idempotent
requests are never replayed after being sent.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

import requests

from .proxy import ProxyError, XrayProxy, parse_proxy_uri


class SubscriptionFailoverSession:
    """Requests-compatible session with node failover and 10-minute refresh."""

    SAFE_RETRY_METHODS = {"GET", "HEAD", "OPTIONS"}
    # A 403 can be specific to one exit node. Try another node for safe reads.
    # HTTP 403 is retried only when the response is identifiable as a
    # Cloudflare edge block. An ordinary application-level 403 is not a node
    # failure and must be returned to the caller.
    RETRYABLE_STATUSES = {500, 502, 503, 504}
    REFRESH_INTERVAL = 600

    def __init__(
        self,
        subscription_url: str,
        *,
        xray_path: str = "xray",
        host: str = "127.0.0.1",
        port: int = 10808,
        timeout: float = 12.0,
        ranking_file: str | None = None,
        max_nodes: int = 200,
        session: requests.Session | None = None,
    ):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("For safety, the SOCKS listener must be loopback-only.")
        if not 1 <= int(port) <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if not 1 <= int(max_nodes) <= 200:
            raise ValueError("max_nodes must be between 1 and 200")
        self.subscription_url = subscription_url
        self.xray_path = str(Path(xray_path).expanduser())
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.max_nodes = int(max_nodes)
        self.ranking_file = ranking_file or str(
            Path.home() / ".config" / "xray-proxy" / "node-ranking.json"
        )
        self.session = session or requests.Session()
        self.nodes: list[str] = []
        self._current: XrayProxy | None = None
        self._current_index = -1
        self._closed = False
        self._last_refresh = 0.0
        self._load_nodes()

    @staticmethod
    def _fingerprint(uri: str) -> str:
        return hashlib.sha256(uri.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_cloudflare_block(response: requests.Response) -> bool:
        """Return true only for a 403 that looks like a Cloudflare edge block."""
        if response.status_code != 403:
            return False
        headers = {str(k).lower(): str(v).lower() for k, v in response.headers.items()}
        if "cloudflare" not in headers.get("server", "") and "cf-ray" not in headers:
            return False
        try:
            body = response.text[:12000].lower()
        except Exception:
            body = ""
        return any(marker in body for marker in (
            "attention required", "please enable cookies", "cloudflare",
            "sorry, you have been blocked",
        ))

    def _load_nodes(self) -> None:
        request = urllib.request.Request(
            self.subscription_url,
            headers={"User-Agent": "xray-proxy/0.1.0", "Accept": "*/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(2_000_000)
        except Exception as exc:
            raise ProxyError(f"دریافت لینک اشتراک ناموفق بود: {exc}") from exc

        text = raw.decode("utf-8-sig", errors="replace").strip()
        compact = "".join(text.split())
        if compact and "://" not in compact:
            try:
                decoded = base64.b64decode(
                    compact + "=" * (-len(compact) % 4), validate=False
                ).decode("utf-8-sig", errors="strict").strip()
                if "://" in decoded:
                    text = decoded
            except Exception:
                pass

        uris = [
            line.strip() for line in text.splitlines()
            if line.strip().lower().startswith(("vless://", "vmess://"))
        ][: self.max_nodes]
        valid: list[tuple[str, str]] = []
        for uri in uris:
            try:
                parse_proxy_uri(uri)
                valid.append((self._fingerprint(uri), uri))
            except ProxyError:
                continue
        if not valid:
            raise ProxyError("در اشتراک هیچ کانفیگ VLESS/VMess قابل پشتیبانی پیدا نشد.")

        ranking: dict[str, tuple[float, bool]] = {}
        try:
            with open(self.ranking_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            entries = payload.get("nodes", []) if isinstance(payload, dict) else []
            for item in entries:
                if isinstance(item, dict) and item.get("fingerprint"):
                    latency = item.get("latency")
                    ranking[item["fingerprint"]] = (
                        float(latency) if isinstance(latency, (int, float)) else 1e9,
                        bool(item.get("ok")),
                    )
        except (OSError, ValueError, TypeError):
            pass

        # Prefer previously successful low-latency nodes; unmeasured nodes follow.
        valid.sort(key=lambda pair: (
            0 if ranking.get(pair[0], (1e9, False))[1] else 1,
            ranking.get(pair[0], (1e9, False))[0],
        ))
        self.nodes = [uri for _, uri in valid]
        self._last_refresh = time.monotonic()

    def _save_result(self, uri: str, *, ok: bool, latency: float | None) -> None:
        fingerprint = self._fingerprint(uri)
        entries: dict[str, dict[str, Any]] = {}
        try:
            with open(self.ranking_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            for item in payload.get("nodes", []):
                if isinstance(item, dict) and item.get("fingerprint"):
                    entries[item["fingerprint"]] = item
        except (OSError, ValueError, TypeError):
            pass
        old = entries.get(fingerprint, {})
        entries[fingerprint] = {
            "fingerprint": fingerprint,
            "ok": bool(ok),
            "latency": latency if latency is not None else old.get("latency", 1e9),
            "checked_at": int(time.time()),
        }
        path = Path(self.ranking_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"nodes": sorted(
                    entries.values(),
                    key=lambda item: (
                        not item.get("ok", False),
                        item.get("latency", 1e9),
                    ),
                )[:self.max_nodes]}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            # Ranking is an optimization; a read request should still work.
            pass

    def _refresh_if_due(self) -> None:
        if time.monotonic() - self._last_refresh < self.REFRESH_INTERVAL:
            return
        current_uri = (
            self.nodes[self._current_index]
            if self._current is not None and 0 <= self._current_index < len(self.nodes)
            else None
        )
        try:
            self._load_nodes()
        except ProxyError:
            # Keep the existing list/connection if the subscription is temporarily down.
            self._last_refresh = time.monotonic()
            return
        if current_uri and current_uri in self.nodes:
            self.nodes.remove(current_uri)
            self.nodes.insert(0, current_uri)
            self._current_index = 0
        else:
            self._stop_current()
            self._current_index = -1

    @property
    def current_proxy_label(self) -> str:
        """Human-readable label for the node currently selected for requests."""
        if self._current is None or not (0 <= self._current_index < len(self.nodes)):
            return ""
        uri = self.nodes[self._current_index]
        try:
            from urllib.parse import unquote, urlparse
            parsed = urlparse(uri)
            config = parse_proxy_uri(uri)
            node = config["settings"]["vnext"][0]
            remark = unquote(parsed.fragment).strip()
            label = f"{config.get('protocol', 'proxy').upper()} {node['address']}:{node['port']}"
            return f"{label} ({remark[:32]})" if remark else label
        except Exception:
            return f"نود {self._current_index + 1}"

    def _stop_current(self) -> None:
        if self._current is not None:
            self._current.stop()
            self._current = None

    def _activate_next(self) -> None:
        # Rotate through the list instead of getting stuck permanently at the
        # last index after one full failover cycle.
        self._stop_current()
        errors: list[str] = []
        count = len(self.nodes)
        if not count:
            raise ProxyError("فهرست کانفیگ‌های اشتراک خالی است.")
        start_index = self._current_index
        for offset in range(1, count + 1):
            index = (start_index + offset) % count
            self._current_index = index
            uri = self.nodes[index]
            proxy = XrayProxy(
                uri,
                xray_path=self.xray_path,
                host=self.host,
                port=self.port,
                startup_timeout=self.timeout,
            )
            try:
                proxy.start()
            except Exception as exc:
                self._save_result(uri, ok=False, latency=None)
                errors.append(str(exc).splitlines()[0][:160])
                proxy.stop()
                continue
            self._current = proxy
            self.session.proxies.update({
                "http": proxy.socks_url,
                "https": proxy.socks_url,
            })
            return
        self._current_index = -1
        detail = errors[-1] if errors else "همه کانفیگ‌ها امتحان شده‌اند."
        raise ProxyError(f"هیچ کانفیگی قابل استفاده نیست: {detail}")

    def _ensure_active(self) -> None:
        if self._closed:
            raise RuntimeError("این نشست بسته شده است.")
        self._refresh_if_due()
        if self._current is None:
            self._activate_next()

    def request(self, method: str, url: str, **kwargs: Any):
        method = method.upper()
        retry_safe = method in self.SAFE_RETRY_METHODS
        last_error: Exception | None = None
        # A Cloudflare edge 403 means the request was blocked before reaching
        # Shahvani. It is safe to retry that specific response even for a POST;
        # all other non-idempotent requests are still never replayed.
        attempts = len(self.nodes) if retry_safe or method == "POST" else 1
        for attempt in range(attempts):
            self._ensure_active()
            active_uri = self.nodes[self._current_index]
            started = time.monotonic()
            try:
                response = self.session.request(method, url, **kwargs)
                elapsed = time.monotonic() - started
                cloudflare_block = self._is_cloudflare_block(response)
                retry_status = response.status_code in self.RETRYABLE_STATUSES
                should_retry = (
                    (retry_safe and retry_status)
                    or cloudflare_block
                )
                if should_retry:
                    status = response.status_code
                    response.close()
                    self._save_result(active_uri, ok=False, latency=elapsed)
                    last_error = requests.HTTPError(
                        f"HTTP {status} via subscription node"
                        + (" (Cloudflare edge block)" if cloudflare_block else "")
                    )
                    if attempt + 1 < attempts:
                        self._activate_next()
                        continue
                    self._stop_current()
                    self._current_index = -1
                    raise last_error
                self._save_result(
                    active_uri, ok=response.status_code < 400, latency=elapsed
                )
                return response
            except requests.RequestException as exc:
                last_error = exc
                self._save_result(active_uri, ok=False, latency=None)
                if not retry_safe or attempt + 1 >= attempts:
                    if retry_safe and attempt + 1 >= attempts:
                        self._stop_current()
                        self._current_index = -1
                    raise
                self._activate_next()
        if last_error:
            raise last_error
        raise ProxyError("درخواست پس از امتحان کانفیگ‌ها انجام نشد.")

    def get(self, url: str, **kwargs: Any):
        return self.request("GET", url, **kwargs)

    def head(self, url: str, **kwargs: Any):
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Any):
        return self.request("OPTIONS", url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any):
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any):
        return self.request("DELETE", url, **kwargs)

    def close(self) -> None:
        if not self._closed:
            self._stop_current()
            self.session.close()
            self._closed = True

    def __enter__(self) -> "SubscriptionFailoverSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __getattr__(self, name: str):
        return getattr(self.session, name)
