"""Run Xray-core locally for Shahvani requests; no V2RayNG app required.

The Xray executable itself must be installed separately. This module creates a
loopback SOCKS5 inbound and configures a requests.Session to use it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


class ProxyError(RuntimeError):
    """Raised when a proxy URI is unsupported or Xray cannot start."""


def _first(params: dict, key: str, default: str = "") -> str:
    values = params.get(key)
    return str(values[0]) if values else default


def _split_host_port(value: str, default_port: int) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("[") and "]" in value:
        host, _, rest = value[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") else default_port
    elif value.count(":") == 1:
        host, raw_port = value.rsplit(":", 1)
        port = int(raw_port)
    else:
        host, port = value, default_port
    if not host or not 1 <= port <= 65535:
        raise ProxyError("آدرس یا پورت سرور داخل کانفیگ معتبر نیست.")
    return host, port


def _decode_vmess(uri: str) -> dict:
    raw = uri[len("vmess://"):].strip()
    raw += "=" * (-len(raw) % 4)
    try:
        payload = base64.urlsafe_b64decode(raw.encode("ascii"))
        data = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise ProxyError("لینک VMess قابل‌خواندن نیست.") from exc
    if not isinstance(data, dict) or not data.get("add") or not data.get("id"):
        raise ProxyError("اطلاعات ضروری لینک VMess وجود ندارد.")
    try:
        port = int(data.get("port", 0))
    except (TypeError, ValueError) as exc:
        raise ProxyError("پورت VMess معتبر نیست.") from exc
    if not 1 <= port <= 65535:
        raise ProxyError("پورت VMess معتبر نیست.")
    network = str(data.get("net") or "tcp").lower()
    security = "tls" if str(data.get("tls") or "").lower() in ("tls", "true", "1") else "none"
    return {
        "protocol": "vmess",
        "settings": {"vnext": [{
            "address": str(data["add"]),
            "port": port,
            "users": [{
                "id": str(data["id"]),
                "alterId": int(data.get("aid") or 0),
                "security": str(data.get("scy") or "auto"),
            }],
        }]},
        "streamSettings": _stream_settings(
            network=network,
            security=security,
            host=str(data.get("host") or ""),
            path=str(data.get("path") or ""),
            service_name=str(data.get("path") or data.get("serviceName") or ""),
            sni=str(data.get("sni") or data.get("host") or data["add"]),
            fingerprint="",
            alpn=str(data.get("alpn") or ""),
            public_key="",
            short_id="",
            spider_x="",
        ),
    }


def _stream_settings(*, network: str, security: str, host: str, path: str,
                     service_name: str, sni: str, fingerprint: str,
                     alpn: str, public_key: str, short_id: str,
                     spider_x: str) -> dict:
    aliases = {"httpupgrade": "httpupgrade", "h2": "http"}
    network = aliases.get(network, network)
    if network not in {"tcp", "ws", "grpc", "http", "httpupgrade"}:
        raise ProxyError(
            f"انتقال «{network}» فعلاً پشتیبانی نمی‌شود؛ tcp، ws، grpc و http را امتحان کن."
        )
    stream: dict = {"network": network, "security": security}
    if network == "ws":
        ws: dict = {"path": path or "/"}
        if host:
            ws["headers"] = {"Host": host}
        stream["wsSettings"] = ws
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": service_name, "multiMode": False}
    elif network == "httpupgrade":
        httpupgrade: dict = {"path": path or "/"}
        if host:
            httpupgrade["host"] = host
        stream["httpupgradeSettings"] = httpupgrade
    elif network == "http":
        http: dict = {"path": path or "/"}
        if host:
            http["host"] = [host]
        stream["httpSettings"] = http

    names = [item.strip() for item in alpn.split(",") if item.strip()]
    if security == "tls":
        tls: dict = {"serverName": sni}
        if fingerprint:
            tls["fingerprint"] = fingerprint
        if names:
            tls["alpn"] = names
        stream["tlsSettings"] = tls
    elif security == "reality":
        if not public_key:
            raise ProxyError("کانفیگ REALITY است اما پارامتر pbk (کلید عمومی) وجود ندارد.")
        reality: dict = {
            "serverName": sni,
            "fingerprint": fingerprint or "chrome",
            "publicKey": public_key,
            "shortId": short_id,
        }
        if spider_x:
            reality["spiderX"] = spider_x
        stream["realitySettings"] = reality
    elif security != "none":
        raise ProxyError(f"نوع امنیت «{security}» پشتیبانی نمی‌شود.")
    return stream


def parse_proxy_uri(uri: str) -> dict:
    """Convert a common vless:// or vmess:// share link to an Xray config."""
    uri = uri.strip()
    if uri.lower().startswith("vmess://"):
        return _decode_vmess(uri)
    if not uri.lower().startswith("vless://"):
        raise ProxyError("فعلاً فقط لینک‌های vless:// و vmess:// پشتیبانی می‌شوند.")

    parsed = urlparse(uri)
    if not parsed.hostname or not parsed.port or not parsed.username:
        raise ProxyError("لینک VLESS باید UUID، آدرس سرور و پورت داشته باشد.")
    params = parse_qs(parsed.query, keep_blank_values=True)
    network = _first(params, "type", "tcp").lower()
    security = _first(params, "security", "none").lower()
    sni = _first(params, "sni", "") or _first(params, "serverName", "") or parsed.hostname
    user: dict = {"id": unquote(parsed.username), "encryption": _first(params, "encryption", "none")}
    flow = _first(params, "flow")
    if flow:
        user["flow"] = flow

    return {
        "protocol": "vless",
        "settings": {"vnext": [{
            "address": parsed.hostname,
            "port": parsed.port,
            "users": [user],
        }]},
        "streamSettings": _stream_settings(
            network=network,
            security=security,
            host=_first(params, "host", ""),
            path=_first(params, "path", ""),
            service_name=_first(params, "serviceName", "") or _first(params, "service_name", ""),
            sni=sni,
            fingerprint=_first(params, "fp", ""),
            alpn=_first(params, "alpn", ""),
            public_key=_first(params, "pbk", "") or _first(params, "publicKey", ""),
            short_id=_first(params, "sid", "") or _first(params, "shortId", ""),
            spider_x=_first(params, "spx", "") or _first(params, "spiderX", ""),
        ),
    }


class XrayProxy:
    """Manage an Xray child process and attach a requests.Session to its SOCKS5."""

    def __init__(self, uri: str, *, xray_path: str = "xray",
                 host: str = "127.0.0.1", port: int = 10808,
                 startup_timeout: float = 12.0):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("For safety, the SOCKS listener must be loopback-only.")
        if not 1 <= int(port) <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.uri = uri
        self.xray_path = xray_path
        self.host = host
        self.port = int(port)
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen | None = None
        self._config_path: str | None = None
        self._log_handle = None

    @property
    def socks_url(self) -> str:
        return f"socks5h://{self.host}:{self.port}"

    def start(self) -> "XrayProxy":
        if self.process and self.process.poll() is None:
            return self
        outbound = parse_proxy_uri(self.uri)
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "tag": "local-socks",
                "listen": self.host,
                "port": self.port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            }],
            "outbounds": [
                dict(outbound, tag="proxy"),
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "blocked", "protocol": "blackhole"},
            ],
            "routing": {"rules": [{
                "type": "field",
                "inboundTag": ["local-socks"],
                "outboundTag": "proxy",
            }]},
        }
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="xray-proxy-", suffix=".json",
            delete=False,
        )
        try:
            json.dump(config, handle, ensure_ascii=False)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            self._config_path = handle.name
        finally:
            handle.close()

        self._log_handle = tempfile.TemporaryFile(mode="w+b")
        try:
            self.process = subprocess.Popen(
                [self.xray_path, "run", "-c", self._config_path],
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=self._log_handle,
            )
        except OSError as exc:
            self.stop()
            raise ProxyError(
                f"اجرای Xray ناموفق بود؛ مسیر فایل اجرایی را بررسی کن: {self.xray_path}"
            ) from exc

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                message = self._read_log()
                self.stop()
                raise ProxyError("Xray اجرا نشد. گزارش: " + (message or "بدون جزئیات"))
            try:
                with socket.create_connection((self.host, self.port), timeout=0.25):
                    return self
            except OSError:
                time.sleep(0.15)
        self.stop()
        raise ProxyError("Xray اجرا شد اما پراکسی محلی در زمان مقرر آماده نشد.")

    def _read_log(self) -> str:
        if not self._log_handle:
            return ""
        try:
            self._log_handle.flush()
            self._log_handle.seek(0)
            return self._log_handle.read(6000).decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    def configure_session(self, session):
        """Set SOCKS5 routing on a requests.Session (requires requests[socks])."""
        session.proxies.update({"http": self.socks_url, "https": self.socks_url})
        return session

    def stop(self) -> None:
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self._config_path:
            try:
                Path(self._config_path).unlink(missing_ok=True)
            except OSError:
                pass
            self._config_path = None
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

    def __enter__(self) -> "XrayProxy":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


class SubscriptionProxy:
    """Use a base64/plain-text subscription with Xray least-ping balancing.

    Xray's observatory continuously probes supported outbounds and the balancer
    routes new connections through the currently best healthy proxy.
    """
    def __init__(self, subscription_url: str, *, xray_path: str = "xray",
                 host: str = "127.0.0.1", port: int = 10808,
                 startup_timeout: float = 12.0, probe_url: str = "https://shahvani.com/",
                 probe_interval: str = "5s", probe_warmup: float = 10.0,
                 ranking_file: str | None = None):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("For safety, the SOCKS listener must be loopback-only.")
        if not 1 <= int(port) <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.subscription_url = subscription_url
        self.xray_path = xray_path
        self.host = host
        self.port = int(port)
        self.startup_timeout = startup_timeout
        self.probe_url = probe_url
        self.probe_interval = probe_interval
        self.probe_warmup = max(0.0, float(probe_warmup))
        self.ranking_file = ranking_file or str(Path.home() / ".config" / "xray-proxy" / "node-ranking.json")
        self.process = None
        self._config_path = None
        self._log_handle = None
        self.nodes = []

    @property
    def socks_url(self) -> str:
        return f"socks5h://{self.host}:{self.port}"

    def _download_nodes(self) -> list[dict]:
        import urllib.request
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
        # Most subscription providers return base64-encoded URI lines.
        compact = "".join(text.split())
        if compact and "://" not in compact:
            try:
                decoded = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False)
                candidate = decoded.decode("utf-8-sig", errors="strict").strip()
                if "://" in candidate:
                    text = candidate
            except Exception:
                pass
        uris = [line.strip() for line in text.splitlines()
                if line.strip().lower().startswith(("vless://", "vmess://"))]
        if not uris:
            raise ProxyError("در لینک اشتراک، لینک VLESS یا VMess قابل پشتیبانی پیدا نشد.")
        nodes = []
        errors = []
        ranked = self._load_ranking()
        for uri in uris[:200]:
            try:
                outbound = parse_proxy_uri(uri)
                fingerprint = hashlib.sha256(uri.encode("utf-8")).hexdigest()
                outbound["_shahvani_fingerprint"] = fingerprint
                outbound["_shahvani_uri"] = uri
                nodes.append(outbound)
            except Exception as exc:
                errors.append(str(exc))
        if not nodes:
            detail = errors[0] if errors else "فرمت اشتراک ناشناخته است."
            raise ProxyError(f"هیچ کانفیگ قابل استفاده‌ای پیدا نشد: {detail}")
        # Put previously measured, working nodes first. Xray still uses leastPing
        # observations at runtime; this ordering is a stable tie-breaker/cache.
        if ranked:
            order = {fingerprint: position for position, fingerprint in enumerate(ranked)}
            nodes.sort(key=lambda node: order.get(node.get("_shahvani_fingerprint", ""), len(order) + 1))
        for index, node in enumerate(nodes, 1):
            node.pop("_shahvani_fingerprint", None)
            node.pop("_shahvani_uri", None)
            node["tag"] = f"sub-{index}"
        self.nodes = nodes
        return nodes

    def _load_ranking(self) -> list[str]:
        try:
            with open(self.ranking_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            entries = payload.get("nodes", []) if isinstance(payload, dict) else []
            return [
                item["fingerprint"] for item in entries
                if isinstance(item, dict) and item.get("fingerprint") and item.get("ok")
            ]
        except (OSError, ValueError, TypeError):
            return []

    def start(self) -> "SubscriptionProxy":
        if self.process and self.process.poll() is None:
            return self
        nodes = self._download_nodes()
        selector = [node["tag"] for node in nodes]
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "tag": "local-socks", "listen": self.host, "port": self.port,
                "protocol": "socks", "settings": {"auth": "noauth", "udp": False},
            }],
            "outbounds": nodes + [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "blocked", "protocol": "blackhole"},
            ],
            "observatory": {
                "subjectSelector": selector,
                "probeURL": self.probe_url,
                "probeInterval": self.probe_interval,
                "enableConcurrency": True,
            },
            "routing": {
                "rules": [{
                    "type": "field", "inboundTag": ["local-socks"],
                    "balancerTag": "best-available",
                }],
                "balancers": [{
                    "tag": "best-available",
                    "selector": selector,
                    "strategy": {"type": "leastPing"},
                }],
            },
        }
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="xray-proxy-sub-",
            suffix=".json", delete=False,
        )
        try:
            json.dump(config, handle, ensure_ascii=False)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            self._config_path = handle.name
        finally:
            handle.close()
        self._log_handle = tempfile.TemporaryFile(mode="w+b")
        try:
            self.process = subprocess.Popen(
                [self.xray_path, "run", "-c", self._config_path],
                stdin=subprocess.DEVNULL, stdout=self._log_handle,
                stderr=self._log_handle,
            )
        except OSError as exc:
            self.stop()
            raise ProxyError(f"اجرای Xray ناموفق بود: {self.xray_path}") from exc
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                message = self._read_log()
                self.stop()
                raise ProxyError("Xray اجرا نشد. گزارش: " + (message or "بدون جزئیات"))
            try:
                with socket.create_connection((self.host, self.port), timeout=0.25):
                    # Allow Xray's observatory to collect initial latency samples
                    # before the caller sends its first request through the balancer.
                    if self.probe_warmup:
                        time.sleep(self.probe_warmup)
                        if self.process.poll() is not None:
                            message = self._read_log()
                            self.stop()
                            raise ProxyError("Xray پس از شروع متوقف شد. گزارش: " + (message or "بدون جزئیات"))
                    return self
            except OSError:
                time.sleep(0.15)
        self.stop()
        raise ProxyError("پراکسی محلی در زمان مقرر آماده نشد.")

    def _read_log(self) -> str:
        if not self._log_handle:
            return ""
        try:
            self._log_handle.flush()
            self._log_handle.seek(0)
            return self._log_handle.read(6000).decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    def configure_session(self, session):
        session.proxies.update({"http": self.socks_url, "https": self.socks_url})
        return session

    def stop(self) -> None:
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self._config_path:
            try:
                Path(self._config_path).unlink(missing_ok=True)
            except OSError:
                pass
            self._config_path = None
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

    def __enter__(self) -> "SubscriptionProxy":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
