"""
Call It Arena — YouTube Data API v3 Uploader, Localization, Playlist Manager & Quota Tracker (`common/upload_youtube.py`).

Reuses the working OAuth 2.0 refresh-token pattern from `DEVizzi/quotegram` (`post_instagram.py`),
upgraded with:
- Backward-compatible secret lookup (`YT_CLIENT_ID` / `YOUTUBE_CLIENT_ID`, etc.)
- `localizations` in Hindi (`hi`), Portuguese (`pt`), Spanish (`es`), and Indonesian (`id`)
- `selfDeclaredMadeForKids=False`, `containsSyntheticMedia=False`, configurable `categoryId` (`24` or `20`)
- Auto-adding each uploaded Short to its format playlist (e.g. "Marble League Season 1")
- First comment posting via `common/comments.py`
- Strict YouTube API quota accounting in `state/posted.json` (upload = 1600 units, default limit = 10,000/day)
- One-command local OAuth token refresh helper: `python -m common.upload_youtube --reauth`
"""

import argparse
import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .comments import post_first_comment_youtube
from .seo import redact_secrets

logger = logging.getLogger("call_it_arena")

TOKEN_URI = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

QUOTA_COSTS = {
    "videos.insert": 1600,
    "videos.update": 50,
    "playlists.list": 1,
    "playlists.insert": 50,
    "playlistItems.insert": 50,
    "commentThreads.insert": 50,
    "commentThreads.list": 1,
    "thumbnails.set": 50,
}


def get_youtube_credentials_env() -> Tuple[str, str, str]:
    """
    Resolves YouTube OAuth secrets supporting both new `YT_*` names and legacy `quotegram` `YOUTUBE_*` names.
    Never logs secret values.
    """
    client_id = (os.getenv("YT_CLIENT_ID") or os.getenv("YOUTUBE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("YT_CLIENT_SECRET") or os.getenv("YOUTUBE_CLIENT_SECRET") or "").strip()
    refresh_token = (os.getenv("YT_REFRESH_TOKEN") or os.getenv("YOUTUBE_REFRESH_TOKEN") or "").strip()
    return client_id, client_secret, refresh_token


def authenticate_youtube() -> Any:
    """
    Authenticates with YouTube Data API v3 using OAuth 2.0 refresh token.
    """
    client_id, client_secret, refresh_token = get_youtube_credentials_env()
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "Missing YouTube OAuth secrets. Set YT_CLIENT_ID, YT_CLIENT_SECRET, and YT_REFRESH_TOKEN "
            "in GitHub Secrets (or run `python -m common.upload_youtube --reauth`)."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def record_quota_usage(posted_state: Dict[str, Any], operation: str) -> int:
    """
    Tracks daily YouTube Data API v3 quota usage (resets every UTC day; default limit 10,000 units).
    """
    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    q = posted_state.setdefault(
        "youtube_quota",
        {"date": today_str, "units_used": 0, "daily_limit": 10000},
    )
    if q.get("date") != today_str:
        q["date"] = today_str
        q["units_used"] = 0
    cost = QUOTA_COSTS.get(operation, 1)
    q["units_used"] = int(q.get("units_used", 0)) + cost
    logger.info(
        "YouTube API quota [%s]: +%d units (Total today: %d / %d)",
        operation,
        cost,
        q["units_used"],
        q.get("daily_limit", 10000),
    )
    return q["units_used"]


def has_quota_for_upload(posted_state: Dict[str, Any]) -> bool:
    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    q = posted_state.get("youtube_quota", {})
    if q.get("date") != today_str:
        return True
    used = int(q.get("units_used", 0))
    limit = int(q.get("daily_limit", 10000))
    return (used + QUOTA_COSTS["videos.insert"]) <= limit


def ensure_playlist_and_add_video(
    youtube: Any,
    video_id: str,
    playlist_title: str,
    posted_state: Dict[str, Any],
) -> Optional[str]:
    """
    Finds or creates the format playlist (e.g. "Marble League Season 1") and inserts `video_id`.
    Caches playlist IDs in `state/posted.json` to save API quota.
    """
    if not playlist_title:
        return None
    playlist_map = posted_state.setdefault("playlist_ids", {})
    playlist_id = playlist_map.get(playlist_title)

    try:
        if not playlist_id:
            resp = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
            record_quota_usage(posted_state, "playlists.list")
            for item in resp.get("items", []):
                if item.get("snippet", {}).get("title") == playlist_title:
                    playlist_id = item.get("id")
                    break

        if not playlist_id:
            create_resp = (
                youtube.playlists()
                .insert(
                    part="snippet,status",
                    body={
                        "snippet": {
                            "title": playlist_title,
                            "description": f"{playlist_title} — Official Call It Arena series playlist.",
                        },
                        "status": {"privacyStatus": "public"},
                    },
                )
                .execute()
            )
            record_quota_usage(posted_state, "playlists.insert")
            playlist_id = create_resp.get("id")

        if playlist_id:
            playlist_map[playlist_title] = playlist_id
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
            record_quota_usage(posted_state, "playlistItems.insert")
            return playlist_id
    except Exception as e:
        logger.warning("Playlist auto-add warning for %s: %s", playlist_title, redact_secrets(str(e)))
    return None


def upload_to_youtube_shorts(
    video_path: Path,
    yt_meta: Dict[str, Any],
    posted_state: Dict[str, Any],
    playlist_title: str = "",
    first_comment: str = "",
) -> Tuple[str, str]:
    """
    Uploads `video_path` to YouTube Shorts with localized metadata (`hi`, `pt`, `es`, `id`),
    adds to its format playlist, and posts the channel's first engagement comment.
    Returns `(video_id, video_url)`.
    """
    if not has_quota_for_upload(posted_state):
        raise RuntimeError("YouTube daily API quota remaining is below 1600 units; deferring upload.")

    youtube = authenticate_youtube()
    title = str(yt_meta.get("title", "Call It Arena Short"))[:70]
    description = str(yt_meta.get("description", ""))
    tags: List[str] = list(yt_meta.get("tags", []))[:15]
    category_id = str(yt_meta.get("categoryId", "24"))
    localizations = yt_meta.get("localizations", {})

    body: Dict[str, Any] = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": False,
        },
    }
    if localizations:
        body["localizations"] = localizations

    parts = "snippet,status,localizations" if localizations else "snippet,status"
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=4 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part=parts, body=body, media_body=media)

    response = None
    retries = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logger.info("YouTube upload progress: %d%%", int(status.progress() * 100))
        except Exception as e:
            retries += 1
            if retries > 3:
                raise RuntimeError(f"YouTube upload failed after 3 retries: {redact_secrets(str(e))}")

    record_quota_usage(posted_state, "videos.insert")
    video_id = str(response["id"])
    video_url = f"https://www.youtube.com/shorts/{video_id}"
    logger.info("Uploaded YouTube Short: %s", video_url)

    if playlist_title:
        ensure_playlist_and_add_video(youtube, video_id, playlist_title, posted_state)

    if first_comment:
        cid = post_first_comment_youtube(youtube, video_id, first_comment)
        if cid:
            record_quota_usage(posted_state, "commentThreads.insert")

    return video_id, video_url


def upload_long_video(
    video_path: Path,
    meta: Dict[str, Any],
    posted_state: Dict[str, Any],
    playlist_title: str = "",
    thumbnail_path: Optional[Path] = None,
    first_comment: str = "",
) -> Tuple[str, str]:
    """
    Uploads a 16:9 long-form video (season recap) as a regular YouTube video with chapters in the
    description, a custom thumbnail (when the channel is verified for custom thumbnails), playlist
    placement and a pinned-style first comment. Returns `(video_id, watch_url)`.
    """
    if not has_quota_for_upload(posted_state):
        raise RuntimeError("YouTube daily API quota too low for a long-form upload; deferring.")
    youtube = authenticate_youtube()
    body: Dict[str, Any] = {
        "snippet": {
            "title": str(meta.get("title", "Call It Arena"))[:100],
            "description": str(meta.get("description", ""))[:4900],
            "tags": list(meta.get("tags", []))[:20],
            "categoryId": str(meta.get("categoryId", "24")),
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": False,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    retries = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logger.info("YouTube long-form upload progress: %d%%", int(status.progress() * 100))
        except Exception as e:
            retries += 1
            if retries > 5:
                raise RuntimeError(f"YouTube long-form upload failed after 5 retries: {redact_secrets(str(e))}")
    record_quota_usage(posted_state, "videos.insert")
    video_id = str(response["id"])
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    logger.info("Uploaded long-form video: %s", video_url)

    if thumbnail_path and Path(thumbnail_path).exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/png")
            ).execute()
            record_quota_usage(posted_state, "thumbnails.set")
        except Exception as e:  # requires a verified channel; never fail the upload over it
            logger.warning("Custom thumbnail not applied: %s", redact_secrets(str(e)))
    if playlist_title:
        ensure_playlist_and_add_video(youtube, video_id, playlist_title, posted_state)
    if first_comment:
        cid = post_first_comment_youtube(youtube, video_id, first_comment)
        if cid:
            record_quota_usage(posted_state, "commentThreads.insert")
    return video_id, video_url


def run_interactive_reauth(client_secrets_file: Optional[str] = None) -> None:
    """
    One-time local CLI helper to generate a fresh `YT_REFRESH_TOKEN` when an old token expires.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_id, client_secret, _ = get_youtube_credentials_env()
    if client_secrets_file and os.path.exists(client_secrets_file):
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, YOUTUBE_SCOPES)
    elif client_id and client_secret:
        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": TOKEN_URI,
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, YOUTUBE_SCOPES)
    else:
        raise RuntimeError(
            "Provide --client-secrets path/to/client_secret.json or set YT_CLIENT_ID and YT_CLIENT_SECRET."
        )

    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")
    print("\n=== YouTube OAuth Refresh Token Generated Successfully ===")
    print("Save the following value to GitHub Secrets as YT_REFRESH_TOKEN (never commit to git):")
    print(creds.refresh_token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube OAuth 2.0 Helper for Call It Arena")
    parser.add_argument("--reauth", action="store_true", help="Launch browser to generate a new YT_REFRESH_TOKEN")
    parser.add_argument("--client-secrets", type=str, default=None, help="Optional path to Google client_secret.json")
    args = parser.parse_args()
    if args.reauth:
        run_interactive_reauth(args.client_secrets)
