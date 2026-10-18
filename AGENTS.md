# Agent instructions

Before changing this repository, read [docs/AI_AGENT_GUIDE.md](docs/AI_AGENT_GUIDE.md) and the relevant implementation/tests.

## Non-negotiable rules

- Keep SOCKS listeners loopback-only; never bind to public/LAN interfaces.
- Never commit or print real proxy URIs, subscription URLs, UUIDs, cookies, tokens, or account credentials.
- Keep TLS certificate verification enabled.
- Preserve child-process/temp-file cleanup and prefer context managers.
- Do not retry arbitrary mutating requests. Preserve and test the narrow Cloudflare edge-block POST retry exception if it remains in the implementation.
- Return ordinary application 403 and all 429 responses unchanged; do not implement proxy rotation to evade account bans or rate limits.
- Subscription downloads currently use direct `urllib.request`, not the proxy being initialized.
- Keep supported URI formats and documentation aligned with `xray_proxy/proxy.py`.
- Add offline tests for behavior changes; do not require a live subscription/provider in the default test suite.
- Run `python -m pip install -e '.[socks,test]'`, `python -m pytest -q`, and `python -m build --wheel` before claiming validation.
- Update README, API docs, and this guide together when public behavior changes.
