from importlib.metadata import version

import xray_proxy


def test_distribution_metadata_version():
    assert version("xray-proxy") == "0.1.0"


def test_package_exports_public_api():
    for name in (
        "ProxyError",
        "XrayProxy",
        "SubscriptionProxy",
        "SubscriptionFailoverSession",
        "parse_proxy_uri",
    ):
        assert hasattr(xray_proxy, name)
