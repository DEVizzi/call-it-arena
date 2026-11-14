"""
Call It Arena — Automated Comment Reader, Wildcard Request Extractor & First-Comment Poster (`common/comments.py`).

Features:
- Reads comments on the last 3 published videos across YouTube (`commentThreads.list`)
  and Instagram (`/{media-id}/comments`).
- Parses country/topic requests via Gemini free tier with regex/emoji fallback.
- Selects the most-requested country as the "Wildcard" in the next race
  (`Requested by @username` shown on screen) and stores processed comment IDs in `state/processed_comments.json`.
- Posts a first comment after each upload (`commentThreads.insert` on YouTube, `/{media-id}/comments` on Instagram).
  (Skips comment pinning as neither API supports programmatic pinning.)
"""

import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .branding import COUNTRIES_META, STATE_DIR, sanitize_username
from .seo import GEMINI_API_URL, get_gemini_api_key, redact_secrets

logger = logging.getLogger("call_it_arena")

PROCESSED_COMMENTS_FILE = STATE_DIR / "processed_comments.json"
GRAPH_BASE_URL = "https://graph.facebook.com/v21.0"


def load_comments_state(path: Path = PROCESSED_COMMENTS_FILE) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"processed_comment_ids": [], "pending_wildcard": None, "wildcard_history": []}


def save_comments_state(state: Dict[str, Any], path: Path = PROCESSED_COMMENTS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep processed_comment_ids bounded to last 2000 IDs
    ids = list(dict.fromkeys(state.get("processed_comment_ids", [])))[-2000:]
    state["processed_comment_ids"] = ids
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def extract_country_from_text_regex(text: str) -> Optional[str]:
    """
    Deterministic regex + flag emoji matcher that maps comment text to an ISO-2 country code.
    """
    if not text:
        return None
    low = text.lower()
    for code, meta in COUNTRIES_META.items():
        emoji = str(meta.get("emoji", ""))
        name = str(meta.get("name", "")).lower()
        if emoji and emoji in text:
            return code
        if name and re.search(rf"\b{re.escape(name)}\b", low):
            return code

    aliases = {
        "bharat": "IN",
        "hindustan": "IN",
        "america": "US",
        "united states": "US",
        "united kingdom": "GB",
        "england": "GB",
        "britain": "GB",
        "pinoy": "PH",
        "naija": "NG",
        "turkiye": "TR",
        "türkiye": "TR",
    }
    for alias, code in aliases.items():
        if re.search(rf"\b{re.escape(alias)}\b", low):
            return code
    return None


def _parse_requests_with_gemini_or_regex(
    raw_comments: List[Dict[str, str]],
) -> List[Tuple[str, str]]:
    """
    Given a list of `{"id": ..., "author": ..., "text": ...}`, returns a list of
    `(country_code, sanitized_username)` pairs.
    """
    matches: List[Tuple[str, str]] = []
    for item in raw_comments:
        code = extract_country_from_text_regex(item.get("text", ""))
        if code:
            matches.append((code, sanitize_username(item.get("author", ""))))

    if matches or not raw_comments:
        return matches

    # Try Gemini free tier if regex found no explicit country name/emoji
    api_key = get_gemini_api_key()
    if not api_key:
        return matches

    try:
        valid_codes = list(COUNTRIES_META.keys())
        prompt = (
            f"Extract country requests from these viewer comments. Valid country codes: {valid_codes}.\n"
            f"Comments: {json.dumps(raw_comments[:20], ensure_ascii=False)}\n"
            "Return ONLY a JSON array of objects: [{\"code\": \"IN\", \"author\": \"@user\"}]"
        )
        resp = requests.post(
            GEMINI_API_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
            },
            timeout=12,
        )
        if resp.ok:
            txt = (
                resp.json()
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "[]")
            )
            arr = json.loads(txt)
            for row in arr:
                c = str(row.get("code", "")).upper()
                if c in COUNTRIES_META:
                    matches.append((c, sanitize_username(str(row.get("author", "")))))
    except Exception as e:
        logger.debug("Gemini comment parsing skipped: %s", redact_secrets(str(e)))

    return matches


def fetch_recent_comments_and_pick_wildcard(
    recent_posts: List[Dict[str, Any]],
    youtube_service: Optional[Any] = None,
    ig_access_token: Optional[str] = None,
    allow_network: bool = True,
) -> Optional[Dict[str, str]]:
    """
    Reads comments on the last 3 videos across YouTube and Instagram, deduplicates against
    `state/processed_comments.json`, and returns the most-requested Wildcard country:
    `{"country_code": "IN", "country_label": "🇮🇳 India", "username": "@username"}`.
    """
    state = load_comments_state()
    seen_ids = set(state.get("processed_comment_ids", []))
    raw_new_comments: List[Dict[str, str]] = []

    if allow_network:
        for post in (recent_posts or [])[-3:]:
            # 1. YouTube commentThreads.list
            yt_vid = post.get("youtube_video_id")
            if yt_vid and youtube_service is not None:
                try:
                    resp = (
                        youtube_service.commentThreads()
                        .list(
                            part="snippet",
                            videoId=yt_vid,
                            maxResults=30,
                            order="time",
                            textFormat="plainText",
                        )
                        .execute()
                    )
                    for item in resp.get("items", []):
                        cid = f"yt_{item.get('id', '')}"
                        if cid in seen_ids:
                            continue
                        top = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                        raw_new_comments.append(
                            {
                                "id": cid,
                                "author": top.get("authorDisplayName", "@viewer"),
                                "text": top.get("textDisplay", ""),
                            }
                        )
                        seen_ids.add(cid)
                except Exception as e:
                    logger.debug("Could not read YouTube comments for %s: %s", yt_vid, redact_secrets(str(e)))

            # 2. Instagram /{media-id}/comments
            ig_mid = post.get("instagram_media_id")
            token = ig_access_token or (os.getenv("IG_ACCESS_TOKEN") or "").strip()
            if ig_mid and token:
                try:
                    resp = requests.get(
                        f"{GRAPH_BASE_URL}/{ig_mid}/comments",
                        params={"fields": "id,text,username", "access_token": token},
                        timeout=15,
                    )
                    if resp.ok:
                        for item in resp.json().get("data", []):
                            cid = f"ig_{item.get('id', '')}"
                            if cid in seen_ids:
                                continue
                            raw_new_comments.append(
                                {
                                    "id": cid,
                                    "author": item.get("username", "@viewer"),
                                    "text": item.get("text", ""),
                                }
                            )
                            seen_ids.add(cid)
                except Exception as e:
                    logger.debug("Could not read IG comments for %s: %s", ig_mid, redact_secrets(str(e)))

    extracted = _parse_requests_with_gemini_or_regex(raw_new_comments)
    if extracted:
        counts = Counter(code for code, _ in extracted)
        top_code, _ = counts.most_common(1)[0]
        requester = next(user for code, user in extracted if code == top_code)
        meta = COUNTRIES_META[top_code]
        wildcard = {
            "country_code": top_code,
            "country_label": f"{meta['emoji']} {meta['name']}",
            "username": requester,
        }
        state["pending_wildcard"] = wildcard
        state.setdefault("wildcard_history", []).append(wildcard)

    state["processed_comment_ids"] = list(seen_ids)
    save_comments_state(state)
    return state.get("pending_wildcard")


def post_first_comment_youtube(youtube_service: Any, video_id: str, comment_text: str) -> Optional[str]:
    """
    Posts the channel's first engagement comment on a newly uploaded YouTube Short.
    """
    if not youtube_service or not video_id or not comment_text:
        return None
    try:
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": comment_text}},
            }
        }
        resp = youtube_service.commentThreads().insert(part="snippet", body=body).execute()
        return str(resp.get("id", ""))
    except Exception as e:
        logger.warning("Failed to post YouTube first comment on %s: %s", video_id, redact_secrets(str(e)))
        return None


def post_first_comment_instagram(media_id: str, comment_text: str, access_token: Optional[str] = None) -> Optional[str]:
    """
    Posts the channel's first engagement comment on a newly published Instagram Reel.
    """
    token = (access_token or os.getenv("IG_ACCESS_TOKEN") or "").strip()
    if not media_id or not comment_text or not token:
        return None
    try:
        resp = requests.post(
            f"{GRAPH_BASE_URL}/{media_id}/comments",
            data={"message": comment_text, "access_token": token},
            timeout=15,
        )
        if resp.ok and "id" in resp.json():
            return str(resp.json()["id"])
    except Exception as e:
        logger.warning("Failed to post Instagram first comment on %s: %s", media_id, redact_secrets(str(e)))
    return None
