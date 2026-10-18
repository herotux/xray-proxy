# Termux example: check an Instagram route

This example uses only the public API of this repository's `xray-proxy` package. It does not log in to Instagram, read account data, or modify system-wide Android routing.

## Install

From the repository checkout on Termux:

```bash
pkg install python
python -m pip install -e '.[socks]'
```

Install Xray-core separately and confirm that it runs:

```bash
xray version
```

If your binary is at `~/bin/xray`, the script uses that path by default. Otherwise set `XRAY_PATH`.

## Supply nodes

Preferred: save the provider's subscription response locally, with one VLESS/VMess URI per line (plain text or Base64), in a private file:

```bash
mkdir -p ~/.config/xray-proxy
chmod 700 ~/.config/xray-proxy
nano ~/.config/xray-proxy/subscription.txt
chmod 600 ~/.config/xray-proxy/subscription.txt
```

Do not share this file: proxy subscription links are credentials.

Alternatively, provide a subscription URL through the environment (the subscription is downloaded directly, before Xray starts):

```bash
export XRAY_PROXY_SUBSCRIPTION_URL='https://your-provider.example/subscription'
```

Avoid putting a secret subscription URL in shell history. Prefer the private local file when possible.

## Run

From the repository root:

```bash
python examples/instagram_check.py
```

The script checks nodes sequentially against `https://www.instagram.com/robots.txt`, prints the local SOCKS5 endpoint for the first successful check, and keeps that Xray process running until Ctrl-C. It does not print share links.

Optional settings:

```bash
export XRAY_PATH="$HOME/bin/xray"
export XRAY_PROXY_PORT=10808
# If needed, override the default local subscription file:
export XRAY_PROXY_SUBSCRIPTION_FILE="$HOME/.config/xray-proxy/subscription.txt"
```

The endpoint is local to the device: `socks5h://127.0.0.1:10808` by default. It affects only applications explicitly configured to use it; it is not an Android-wide VPN. The public endpoint check is only a basic reachability test, not a guarantee that Instagram login, feeds, uploads, or every app feature will work.

If Instagram returns HTTP 403 or 429, the script stops instead of treating that response as a broken proxy and rotating through nodes. Respect service restrictions and rate limits.
