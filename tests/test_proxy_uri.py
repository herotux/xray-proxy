from __future__ import annotations

import base64
import json
from urllib.parse import quote

import pytest

from xray_proxy import ProxyError, SubscriptionFailoverSession, SubscriptionProxy, XrayProxy, parse_proxy_uri


def test_parse_vless_tls_websocket():
    config = parse_proxy_uri(
        "vless://11111111-1111-4111-8111-111111111111@example.com:443"
        "?type=ws&security=tls&host=cdn.example.com&path=%2Fedge&sni=cdn.example.com"
    )
    assert config["protocol"] == "vless"
    server = config["settings"]["vnext"][0]
    assert server["address"] == "example.com"
    assert server["port"] == 443
    assert server["users"][0]["id"] == "11111111-1111-4111-8111-111111111111"
    assert config["streamSettings"]["network"] == "ws"
    assert config["streamSettings"]["tlsSettings"]["serverName"] == "cdn.example.com"


def test_parse_vmess_base64():
    payload = {
        "add": "vmess.example.com", "port": "8443",
        "id": "11111111-1111-4111-8111-111111111111",
        "aid": "0", "net": "ws", "tls": "tls", "host": "cdn.example.com",
        "path": "/v2",
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    config = parse_proxy_uri("vmess://" + encoded)
    assert config["protocol"] == "vmess"
    assert config["settings"]["vnext"][0]["port"] == 8443
    assert config["streamSettings"]["wsSettings"]["path"] == "/v2"


@pytest.mark.parametrize("uri", [
    "https://example.com", "vless://example.com:443",
    "vless://uuid@example.com:99999?type=tcp",
])
def test_rejects_invalid_or_unsupported_uri(uri):
    with pytest.raises((ProxyError, ValueError)):
        parse_proxy_uri(uri)


def test_listener_rejects_non_loopback_host():
    with pytest.raises(ValueError):
        XrayProxy("vless://uuid@example.com:443", host="0.0.0.0")


def test_public_api_exports_all_proxy_modes():
    assert XrayProxy is not None
    assert SubscriptionProxy is not None
    assert SubscriptionFailoverSession is not None
