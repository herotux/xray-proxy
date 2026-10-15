import pytest

from xray_proxy import SubscriptionProxy


def test_probe_url_defaults_to_lightweight_youtube_endpoint(monkeypatch):
    monkeypatch.delenv("XRAY_PROXY_PROBE_URL", raising=False)
    proxy = SubscriptionProxy("https://provider.example/sub")
    assert proxy.probe_url == "https://www.youtube.com/generate_204"


def test_probe_url_can_be_set_with_environment(monkeypatch):
    monkeypatch.setenv("XRAY_PROXY_PROBE_URL", "https://example.org/ping")
    proxy = SubscriptionProxy("https://provider.example/sub")
    assert proxy.probe_url == "https://example.org/ping"


def test_explicit_probe_url_overrides_environment(monkeypatch):
    monkeypatch.setenv("XRAY_PROXY_PROBE_URL", "https://env.example/ping")
    proxy = SubscriptionProxy(
        "https://provider.example/sub",
        probe_url="https://custom.example/check",
    )
    assert proxy.probe_url == "https://custom.example/check"


def test_probe_url_must_be_http_or_https():
    with pytest.raises(ValueError, match="HTTP"):
        SubscriptionProxy("https://provider.example/sub", probe_url="ftp://example.org")
