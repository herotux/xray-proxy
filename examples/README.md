# Termux example: check an Instagram route

This example uses the public `SubscriptionProxy` API from this repository. Xray Observatory probes supported VLESS/VMess outbounds concurrently and the `leastPing` balancer selects a route for new connections. The script does not start a separate Xray process for every node and never prints share links.

## Install

From the repository checkout on Termux:

```bash
pkg install python
python -m pip install -e '.[socks]'
```

Install Xray-core separately and confirm it runs:

```bash
~/bin/xray version
```

## Supply the subscription URL

Preferred: put the HTTPS subscription URL in a private environment variable:

```bash
export XRAY_PROXY_SUBSCRIPTION_URL='https://your-provider.example/subscription'
```

Or put exactly one subscription URL in the private local file:

```bash
mkdir -p ~/.config/xray-proxy
chmod 700 ~/.config/xray-proxy
nano ~/.config/xray-proxy/subscription.txt
chmod 600 ~/.config/xray-proxy/subscription.txt
```

Do not commit or share the subscription URL; it may grant access to your proxy subscription. The current `SubscriptionProxy` API accepts a subscription URL, not a local file containing raw node URIs.

## Run

From the repository root:

```bash
git pull --ff-only
python examples/instagram_check.py
```

The live spinner shows that Xray is starting and warming up its concurrent Observatory. It is not a per-node percentage: the current library API does not expose individual probe progress. After warm-up, the script makes one real request to Instagram's public `robots.txt` endpoint and reports its HTTP status and latency.

If successful, it prints the local SOCKS5 endpoint and keeps Xray running until Ctrl-C. The endpoint is local to this device and only affects apps explicitly configured to use it; it is not an Android-wide VPN.

## Optional settings

```bash
export XRAY_PATH="$HOME/bin/xray"
export XRAY_PROXY_PORT=10808
export XRAY_PROXY_PROBE_URL="https://www.google.com/generate_204"
export XRAY_PROXY_PROBE_INTERVAL="5s"
export XRAY_PROXY_PROBE_WARMUP=10
```

Use a probe URL that is reachable from your network through the candidate nodes. Observatory checks are HTTP(S) probes, not ICMP ping. A successful probe does not guarantee Instagram works, so the script separately checks Instagram. If Instagram returns HTTP 403 or 429, the script stops rather than rotating automatically; respect destination restrictions and rate limits.
