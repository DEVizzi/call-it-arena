"""YouTube OAuth health check (no secrets are ever printed).

Exchanges YT_REFRESH_TOKEN for an access token and reads the authorised channel. Exits 1 with a clear reason on
failure. The classic failure is `invalid_grant` exactly ~7 days after minting: that means the Google Cloud OAuth
consent screen is still in "Testing" (refresh tokens expire after 7 days) - publish it to "In production" and mint a
new token once with `python setup_tokens.py --youtube`.

    python scripts/check_youtube_token.py            # used by .github/workflows/token-health.yml
"""
from __future__ import annotations

import os
import sys

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


def _env(*names: str) -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return ""


def main() -> int:
    cid = _env("YT_CLIENT_ID", "YOUTUBE_CLIENT_ID")
    secret = _env("YT_CLIENT_SECRET", "YOUTUBE_CLIENT_SECRET")
    refresh = _env("YT_REFRESH_TOKEN", "YOUTUBE_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        print("YT OAUTH: FAIL - YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN secret missing")
        return 1
    r = requests.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret, "refresh_token": refresh,
                                       "grant_type": "refresh_token"}, timeout=20)
    if r.status_code != 200:
        err = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("error", "")
        hint = (" -> refresh token expired/revoked. If this happens ~7 days after minting, the OAuth consent screen "
                "is in 'Testing': publish it to 'In production', then run `python setup_tokens.py --youtube`."
                if err == "invalid_grant" else "")
        print(f"YT OAUTH: FAIL - token endpoint HTTP {r.status_code} {err}{hint}")
        return 1
    access = r.json().get("access_token", "")
    c = requests.get(CHANNELS_URL, params={"part": "snippet", "mine": "true"},
                     headers={"Authorization": f"Bearer {access}"}, timeout=20)
    items = ((c.json() or {}).get("items") or []) if c.status_code == 200 else []
    if not items:
        print(f"YT OAUTH: FAIL - channels.list HTTP {c.status_code} (token works but no channel is authorised)")
        return 1
    print(f"YT OAUTH: OK - channel '{items[0]['snippet']['title']}' ({items[0]['id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
