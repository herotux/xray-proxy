"""Reusable Xray-core SOCKS proxy and subscription failover utilities."""

from .proxy import ProxyError, SubscriptionProxy, XrayProxy, parse_proxy_uri
from .failover import SubscriptionFailoverSession

__all__ = [
    "ProxyError",
    "XrayProxy",
    "SubscriptionProxy",
    "SubscriptionFailoverSession",
    "parse_proxy_uri",
]

__version__ = "0.1.0"
