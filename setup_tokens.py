#!/usr/bin/env python3
"""
Call It Arena — Interactive Token Setup & GitHub Secrets Sync (`setup_tokens.py`).

Automates setting up and verifying all secrets for `DEVizzi/call-it-arena`:
1. Syncs existing working `GEMINI_API_KEY`, `YT_CLIENT_ID`, and `YT_CLIENT_SECRET` to `.env` & GitHub Secrets.
2. `--youtube`: Opens your browser to authorize your YouTube channel (`Silent Ink` / `Call It Arena`),
   mints a fresh `YT_REFRESH_TOKEN` with `youtube.upload`, `youtube`, and `youtube.force-ssl` scopes,
   verifies the channel title, and pushes `YT_REFRESH_TOKEN` directly to GitHub Secrets.
3. `--instagram <USER_OR_PAGE_TOKEN>`: Inspects your Meta token, exchanges it for a long-lived/permanent
   Page/Instagram token via `FB_APP_ID` + `FB_APP_SECRET`, auto-detects the `IG_USER_ID` for
   `__silentink__` / `callitarena`, and pushes `IG_USER_ID` + `IG_ACCESS_TOKEN` to GitHub Secrets.
4. `--nasa <NASA_API_KEY>`: Verifies your NASA APOD key and pushes `NASA_API_KEY` to GitHub Secrets.
5. `--status`: Checks which secrets are currently configured on `DEVizzi/call-it-arena` and tests local `.env`.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
REPO_NAME = "DEVizzi/call-it-arena"
GRAPH_BASE_URL = "https://graph.facebook.com/v21.0"


def _set_env_var_locally(key: str, value: str) -> None:
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def _push_github_secret(key: str, value: str) -> bool:
    proc = subprocess.run(
        ["gh", "secret", "set", key, "--repo", REPO_NAME],
        input=value,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode == 0:
        print(f"✅ Synced `{key}` to GitHub Secrets ({REPO_NAME})")
        return True
    print(f"❌ Failed to set `{key}` on GitHub Secrets: {proc.stderr}")
    return False


def save_secret_everywhere(key: str, value: str) -> None:
    clean = value.strip().strip('"').strip("'")
    if not clean:
        return
    _set_env_var_locally(key, clean)
    _push_github_secret(key, clean)


def bootstrap_from_existing_env() -> None:
    """
    Imports working `GOOGLE_API_KEY` -> `GEMINI_API_KEY`, `YOUTUBE_CLIENT_ID` -> `YT_CLIENT_ID`,
    `YOUTUBE_CLIENT_SECRET` -> `YT_CLIENT_SECRET`, and `FB_APP_ID` / `FB_APP_SECRET` from
    `/Users/izazhaq/.gemini/jetski/scratch/GoeSerene/.env` if present.
    """
    donor = Path("/Users/izazhaq/.gemini/jetski/scratch/GoeSerene/.env")
    if not donor.exists():
        return
    load_dotenv(donor, override=False)
    mapping = [
        ("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY")),
        ("YT_CLIENT_ID", os.getenv("YOUTUBE_CLIENT_ID")),
        ("YT_CLIENT_SECRET", os.getenv("YOUTUBE_CLIENT_SECRET")),
        ("FB_APP_ID", os.getenv("FB_APP_ID")),
        ("FB_APP_SECRET", os.getenv("FB_APP_SECRET")),
    ]
    for target_key, raw_val in mapping:
        if raw_val:
            clean = raw_val.strip().strip('"').strip("'")
            _set_env_var_locally(target_key, clean)
            if target_key in ("GEMINI_API_KEY", "YT_CLIENT_ID", "YT_CLIENT_SECRET"):
                _push_github_secret(target_key, clean)


def authorize_youtube_interactively() -> None:
    """
    Opens local browser OAuth flow so the user can pick their `Silent Ink` / `Call It Arena`
    YouTube channel and automatically saves `YT_REFRESH_TOKEN` to `.env` and GitHub Secrets.
    """
    load_dotenv(ENV_FILE, override=True)
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from common.upload_youtube import TOKEN_URI, YOUTUBE_SCOPES, get_youtube_credentials_env

    client_id, client_secret, _ = get_youtube_credentials_env()
    if not (client_id and client_secret):
        raise RuntimeError("Missing YT_CLIENT_ID or YT_CLIENT_SECRET in .env")

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }
    print("\n🌐 Opening browser for YouTube OAuth authorization...")
    print("👉 Select your Google account and choose the 'Silent Ink' / 'Call It Arena' YouTube channel!")
    flow = InstalledAppFlow.from_client_config(client_config, YOUTUBE_SCOPES)
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        raise RuntimeError("Google did not return a refresh_token. Ensure prompt='consent' was approved.")

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    ch_resp = yt.channels().list(part="snippet", mine=True).execute()
    items = ch_resp.get("items", [])
    if items:
        ch_title = items[0]["snippet"].get("title", "Unknown")
        ch_handle = items[0]["snippet"].get("customUrl", "")
        print(f"🎉 Connected YouTube Channel: {ch_title} ({ch_handle}) [ID: {items[0]['id']}]")

    save_secret_everywhere("YT_REFRESH_TOKEN", creds.refresh_token)


def configure_instagram_token(raw_token: str, explicit_ig_user_id: Optional[str] = None) -> None:
    """
    Exchanges a short/long-lived Meta User token for a long-lived/permanent Page token (if FB_APP_ID/SECRET exist),
    lists all connected Instagram Business/Creator accounts, selects `__silentink__` / `callitarena`
    (or uses `explicit_ig_user_id`), and syncs `IG_USER_ID` + `IG_ACCESS_TOKEN` to GitHub Secrets.
    """
    load_dotenv(ENV_FILE, override=True)
    token = raw_token.strip().strip('"').strip("'")
    app_id = (os.getenv("FB_APP_ID") or "").strip()
    app_secret = (os.getenv("FB_APP_SECRET") or "").strip()

    # Step 1: Exchange for 60-day token if FB_APP_ID & FB_APP_SECRET are available
    if app_id and app_secret:
        ex_resp = requests.get(
            f"{GRAPH_BASE_URL}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": token,
            },
            timeout=15,
        )
        if ex_resp.ok and "access_token" in ex_resp.json():
            token = ex_resp.json()["access_token"]
            print("🔄 Exchanged token for a 60-day long-lived Meta token.")

    # Step 2: Inspect connected Facebook Pages and their Instagram Business Accounts
    pages_resp = requests.get(
        f"{GRAPH_BASE_URL}/me/accounts",
        params={"fields": "id,name,access_token,instagram_business_account{id,username,name}", "access_token": token},
        timeout=15,
    )
    chosen_ig_id = explicit_ig_user_id
    chosen_token = token

    if pages_resp.ok and "data" in pages_resp.json():
        pages = pages_resp.json()["data"]
        print(f"📋 Found {len(pages)} Facebook Page(s) on this token:")
        for p in pages:
            ig_acc = p.get("instagram_business_account") or {}
            ig_id = ig_acc.get("id", "")
            ig_uname = ig_acc.get("username", "")
            print(f"  - Page '{p.get('name')}' -> Instagram @{ig_uname} (IG_USER_ID: {ig_id})")
            if ig_uname.lower() in ("__silentink__", "callitarena") or (explicit_ig_user_id and ig_id == explicit_ig_user_id):
                chosen_ig_id = ig_id
                if p.get("access_token"):
                    chosen_token = p["access_token"]
                    print(f"✨ Derived permanent Page token for @{ig_uname}!")
        if not chosen_ig_id and len(pages) == 1:
            ig_acc = pages[0].get("instagram_business_account") or {}
            chosen_ig_id = ig_acc.get("id")
            if pages[0].get("access_token"):
                chosen_token = pages[0]["access_token"]

    if not chosen_ig_id:
        raise RuntimeError(
            "Could not auto-detect IG_USER_ID for __silentink__ / callitarena from /me/accounts. "
            "Pass `--ig-user-id <YOUR_IG_USER_ID>` explicitly or ensure your Facebook Page is linked to __silentink__."
        )

    # Verify IG_USER_ID + token
    v_resp = requests.get(
        f"{GRAPH_BASE_URL}/{chosen_ig_id}",
        params={"fields": "id,username,name", "access_token": chosen_token},
        timeout=15,
    )
    if not v_resp.ok:
        raise RuntimeError(f"Instagram Graph API verification failed: {v_resp.text}")
    info = v_resp.json()
    print(f"🎉 Verified Instagram Business Account: @{info.get('username')} (ID: {info.get('id')})")
    save_secret_everywhere("IG_USER_ID", str(info["id"]))
    save_secret_everywhere("IG_ACCESS_TOKEN", chosen_token)


def configure_nasa_key(api_key: str) -> None:
    clean = api_key.strip().strip('"').strip("'")
    r = requests.get("https://api.nasa.gov/planetary/apod", params={"api_key": clean, "count": 1}, timeout=15)
    if not r.ok:
        raise RuntimeError(f"NASA APOD key check failed ({r.status_code}): {r.text[:200]}")
    print("🎉 Verified NASA_API_KEY against api.nasa.gov/planetary/apod!")
    save_secret_everywhere("NASA_API_KEY", clean)


def show_status() -> None:
    load_dotenv(ENV_FILE, override=True)
    print(f"\n=== GitHub Secrets on {REPO_NAME} ===")
    subprocess.run(["gh", "secret", "list", "--repo", REPO_NAME], check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Call It Arena — Token & Secrets Setup")
    parser.add_argument("--bootstrap", action="store_true", help="Sync existing Gemini & YouTube Client ID/Secret")
    parser.add_argument("--youtube", action="store_true", help="Launch browser to authorize YouTube & sync YT_REFRESH_TOKEN")
    parser.add_argument("--instagram", type=str, default=None, help="Configure Instagram token & auto-detect IG_USER_ID")
    parser.add_argument("--ig-user-id", type=str, default=None, help="Explicit IG_USER_ID (if using a Page token directly)")
    parser.add_argument("--nasa", type=str, default=None, help="Verify and sync NASA_API_KEY")
    parser.add_argument("--status", action="store_true", help="Show configured GitHub Secrets")
    args = parser.parse_args()

    if args.bootstrap:
        bootstrap_from_existing_env()
    if args.youtube:
        authorize_youtube_interactively()
    if args.instagram:
        configure_instagram_token(args.instagram, explicit_ig_user_id=args.ig_user_id)
    if args.nasa:
        configure_nasa_key(args.nasa)
    if args.status or not any([args.bootstrap, args.youtube, args.instagram, args.nasa]):
        show_status()
