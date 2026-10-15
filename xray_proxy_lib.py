"""Python helpers for validating Xray share-link URIs."""
from urllib.parse import urlparse

class ProxyError(ValueError):
    pass

def parse_proxy_uri(uri: str) -> dict:
    parsed = urlparse(uri.strip())
    if parsed.scheme.lower() not in {"vless", "vmess"}:
        raise ProxyError("Only VLESS and VMess share links are supported.")
    return {"scheme": parsed.scheme.lower(), "host": parsed.hostname, "port": parsed.port}
