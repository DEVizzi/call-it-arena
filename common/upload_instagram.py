"""
Call It Arena — Instagram Graph API Reels Uploader & Release Asset Host (`common/upload_instagram.py`).

Reuses and upgrades the Instagram Graph API auth & upload pattern from `DEVizzi/quotegram`:
1. Supports hosting the MP4 as a public GitHub Release asset (`media-cache` tag) via `gh release`
   and cleaning up release assets older than 7 days (`cleanup_old_release_assets`).
2. Also supports Graph API Resumable direct binary upload (`upload_type="resumable"` to `rupload.facebook.com`,
   exact `quotegram` pattern) so uploads succeed even if release hosting is skipped or unavailable.
3. Polls container status every 12 seconds (10–15s window) up to 5 minutes (`300s`).
4. Publishes as `REELS` with `share_to_feed=true` and `thumb_offset` set to the reveal frame minus 0.5s.
5. Posts the channel's first engagement comment via `/{media-id}/comments`.
"""

import datetime
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from .comments import post_first_comment_instagram
from .seo import redact_secrets

logger = logging.getLogger("call_it_arena")

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
MEDIA_RELEASE_TAG = "media-cache"


def get_instagram_credentials_env() -> Tuple[str, str]:
    """
    Resolves Instagram credentials supporting both `IG_USER_ID` and legacy `quotegram` `IG_BUSINESS_ACCOUNT_ID`.
    """
    ig_user_id = (
        os.getenv("IG_USER_ID")
        or os.getenv("IG_BUSINESS_ACCOUNT_ID")
        or ""
    ).strip()
    ig_access_token = (
        os.getenv("IG_ACCESS_TOKEN")
        or os.getenv("FB_PAGE_ACCESS_TOKEN")
        or ""
    ).strip()
    return ig_user_id, ig_access_token


def host_video_on_github_release(
    video_path: Path,
    repo: Optional[str] = None,
) -> Optional[str]:
    """
    Uploads `video_path` to a rolling public GitHub Release (`media-cache`) and returns
    its public `browser_download_url` so Instagram's Graph API can fetch `video_url`.
    """
    gh_bin = shutil.which("gh")
    if not gh_bin or not video_path.exists():
        return None
    target_repo = (repo or os.getenv("GITHUB_REPOSITORY") or "DEVizzi/call-it-arena").strip()
    try:
        # Ensure release tag exists
        subprocess.run(
            [
                gh_bin,
                "release",
                "view",
                MEDIA_RELEASE_TAG,
                "--repo",
                target_repo,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        subprocess.run(
            [
                gh_bin,
                "release",
                "create",
                MEDIA_RELEASE_TAG,
                "--repo",
                target_repo,
                "--title",
                "Temporary Instagram Reels Media Cache (7-Day Rolling)",
                "--notes",
                "Automated temporary hosting for Instagram Graph API Reels ingestion. Assets older than 7 days are automatically purged.",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        up = subprocess.run(
            [
                gh_bin,
                "release",
                "upload",
                MEDIA_RELEASE_TAG,
                str(video_path),
                "--repo",
                target_repo,
                "--clobber",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if up.returncode == 0:
            url = f"https://github.com/{target_repo}/releases/download/{MEDIA_RELEASE_TAG}/{video_path.name}"
            logger.info("Hosted MP4 on GitHub Release asset: %s", url)
            return url
    except Exception as e:
        logger.debug("GitHub Release asset upload skipped: %s", redact_secrets(str(e)))
    return None


def cleanup_old_release_assets(days: int = 7, repo: Optional[str] = None) -> int:
    """
    Deletes temporary MP4 assets older than `days` (default 7 days) from the `media-cache` release.
    """
    gh_bin = shutil.which("gh")
    if not gh_bin:
        return 0
    target_repo = (repo or os.getenv("GITHUB_REPOSITORY") or "DEVizzi/call-it-arena").strip()
    removed = 0
    try:
        proc = subprocess.run(
            [
                gh_bin,
                "api",
                f"repos/{target_repo}/releases/tags/{MEDIA_RELEASE_TAG}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return 0
        release_data = json.loads(proc.stdout)
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        for asset in release_data.get("assets", []):
            created_str = asset.get("created_at", "")
            asset_id = asset.get("id")
            if not created_str or not asset_id:
                continue
            created_dt = datetime.datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created_dt < cutoff:
                del_proc = subprocess.run(
                    [
                        gh_bin,
                        "api",
                        "--method",
                        "DELETE",
                        f"repos/{target_repo}/releases/assets/{asset_id}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                if del_proc.returncode == 0:
                    removed += 1
    except Exception as e:
        logger.debug("Release asset cleanup check skipped: %s", redact_secrets(str(e)))
    return removed


def poll_ig_container_status(
    container_id: str,
    access_token: str,
    poll_interval_sec: int = 12,
    max_wait_sec: int = 300,
) -> None:
    """
    Polls `GET /{container_id}?fields=status_code,status` every 12 seconds (10–15s window)
    for up to 5 minutes (`300s`) until `status_code == "FINISHED"`.
    """
    elapsed = 0
    endpoint = f"{GRAPH_BASE_URL}/{container_id}"
    while elapsed < max_wait_sec:
        resp = requests.get(
            endpoint,
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=25,
        ).json()
        code = resp.get("status_code")
        logger.info("Instagram container %s status: %s (%ds/%ds)", container_id, code or "IN_PROGRESS", elapsed, max_wait_sec)
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container {container_id} failed: {redact_secrets(str(resp))}")
        time.sleep(poll_interval_sec)
        elapsed += poll_interval_sec
    raise TimeoutError(f"Instagram container {container_id} timed out after {max_wait_sec}s.")


def upload_to_instagram_reels(
    video_path: Path,
    ig_meta: Dict[str, Any],
    first_comment: str = "",
) -> Tuple[str, str]:
    """
    Publishes `video_path` to Instagram Reels:
    1. Attempts public URL container creation via GitHub Release asset (`video_url`),
       falling back automatically to Graph API Resumable binary upload (`upload_type="resumable"`).
    2. Polls container every 12s up to 5 minutes.
    3. Publishes container via `/{IG_USER_ID}/media_publish`.
    4. Posts the channel's first comment and cleans up release assets > 7 days old.
    Returns `(media_id, permalink)`.
    """
    ig_user_id, access_token = get_instagram_credentials_env()
    if not (ig_user_id and access_token):
        raise RuntimeError(
            "Missing Instagram credentials. Set IG_USER_ID (or IG_BUSINESS_ACCOUNT_ID) and IG_ACCESS_TOKEN."
        )

    caption = str(ig_meta.get("caption", ""))
    thumb_offset_ms = int(ig_meta.get("thumb_offset_ms", 15000))
    container_id: Optional[str] = None

    # Method A: Host MP4 on public GitHub Release asset & pass video_url
    public_video_url = host_video_on_github_release(video_path)
    if public_video_url:
        resp = requests.post(
            f"{GRAPH_BASE_URL}/{ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": public_video_url,
                "caption": caption,
                "share_to_feed": "true",
                "thumb_offset": str(thumb_offset_ms),
                "access_token": access_token,
            },
            timeout=30,
        ).json()
        if "id" in resp:
            container_id = str(resp["id"])

    # Method B: Direct Graph API Resumable Upload (from quotegram/post_instagram.py)
    if not container_id:
        init_resp = requests.post(
            f"{GRAPH_BASE_URL}/{ig_user_id}/media",
            data={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption,
                "share_to_feed": "true",
                "thumb_offset": str(thumb_offset_ms),
                "access_token": access_token,
            },
            timeout=30,
        ).json()
        if "id" not in init_resp or "uri" not in init_resp:
            raise RuntimeError(f"Failed to initialize IG Reels container: {redact_secrets(str(init_resp))}")
        container_id = str(init_resp["id"])
        upload_uri = str(init_resp["uri"])
        file_size = video_path.stat().st_size
        headers = {
            "Authorization": f"OAuth {access_token}",
            "offset": "0",
            "file_size": str(file_size),
        }
        with open(video_path, "rb") as vf:
            up_resp = requests.post(upload_uri, headers=headers, data=vf, timeout=300)
        if not up_resp.ok:
            raise RuntimeError(f"IG Resumable video upload failed: {redact_secrets(up_resp.text)}")

    # Poll container status every 12s up to 5 min
    poll_ig_container_status(container_id, access_token, poll_interval_sec=12, max_wait_sec=300)

    # Publish media container
    pub_resp = requests.post(
        f"{GRAPH_BASE_URL}/{ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=30,
    ).json()
    if "id" not in pub_resp:
        raise RuntimeError(f"IG Reels media_publish failed: {redact_secrets(str(pub_resp))}")

    media_id = str(pub_resp["id"])
    permalink = f"https://www.instagram.com/reel/{media_id}/"
    try:
        p_resp = requests.get(
            f"{GRAPH_BASE_URL}/{media_id}",
            params={"fields": "permalink", "access_token": access_token},
            timeout=15,
        ).json()
        if p_resp.get("permalink"):
            permalink = str(p_resp["permalink"])
    except Exception:
        pass

    logger.info("Published Instagram Reel: %s", permalink)

    if first_comment:
        post_first_comment_instagram(media_id, first_comment, access_token=access_token)

    cleanup_old_release_assets(days=7)
    return media_id, permalink
