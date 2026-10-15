from __future__ import annotations

import time

from xray_proxy import SubscriptionFailoverSession


class FakeResponse:
    def __init__(self, status_code, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.closed = False

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.proxies = {}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def close(self):
        pass


class FakeProxy:
    def __init__(self, socks_url="socks5h://127.0.0.1:10808"):
        self.socks_url = socks_url
        self.stopped = False

    def stop(self):
        self.stopped = True


def make_session(tmp_path, responses):
    session = SubscriptionFailoverSession.__new__(SubscriptionFailoverSession)
    session.subscription_url = "https://example.invalid/subscription"
    session.xray_path = "xray"
    session.host = "127.0.0.1"
    session.port = 10808
    session.timeout = 1
    session.max_nodes = 2
    session.ranking_file = str(tmp_path / "ranking.json")
    session.session = FakeSession(responses)
    session.nodes = ["vless://node-a", "vless://node-b"]
    session._current = FakeProxy()
    session._current_index = 0
    session._closed = False
    session._last_refresh = time.monotonic()
    session._activate_next = lambda: setattr(session, "_current_index", 1)
    session._save_result = lambda *args, **kwargs: None
    return session


def test_detects_cloudflare_edge_block():
    response = FakeResponse(
        403,
        {"server": "cloudflare", "cf-ray": "abc-CDG"},
        "<title>Attention Required! | Cloudflare</title>Please enable cookies",
    )
    assert SubscriptionFailoverSession._is_cloudflare_block(response)


def test_does_not_treat_regular_403_as_proxy_failure():
    response = FakeResponse(403, {"server": "nginx"}, "<title>Forbidden</title>")
    assert not SubscriptionFailoverSession._is_cloudflare_block(response)


def test_retries_cloudflare_403_then_returns_success(tmp_path):
    blocked = FakeResponse(
        403,
        {"server": "cloudflare", "cf-ray": "abc-CDG"},
        "<title>Attention Required! | Cloudflare</title>",
    )
    success = FakeResponse(200, {"server": "nginx"}, "<title>Login</title>")
    session = make_session(tmp_path, [blocked, success])

    result = session.request("POST", "https://shahvani.com/login", data={"x": "y"})

    assert result.status_code == 200
    assert len(session.session.calls) == 2
    assert blocked.closed


def test_returns_application_403_without_retry(tmp_path):
    forbidden = FakeResponse(403, {"server": "nginx"}, "Forbidden")
    session = make_session(tmp_path, [forbidden])

    result = session.request("GET", "https://shahvani.com/private")

    assert result.status_code == 403
    assert len(session.session.calls) == 1
