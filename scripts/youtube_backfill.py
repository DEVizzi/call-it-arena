"""Backfill YouTube with Shorts that only reached Instagram (e.g. while the YouTube token was dead).

Downloads the EXACT published Reel (Instagram Graph API media_url) - never re-renders, so the YouTube copy is identical -
uploads it as a YouTube Short (title from state/posted.json, format playlist, first comment), and records the video id.

    python scripts/youtube_backfill.py 20261121-B-2 20261122-A 20261123-B     # oldest first = playlist order
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.seo import FORMAT_YT_TAGS  # noqa: E402
from common.upload_youtube import upload_to_youtube_shorts  # noqa: E402

POSTED = ROOT / "state" / "posted.json"
GRAPH = "https://graph.facebook.com/v21.0"
RULE = ("📜 Points never reset. After every game from here on, the team with the lowest total points across the "
        "whole tournament is eliminated.\n\n")


def _yaml_playlists() -> dict:
    import yaml
    return (yaml.safe_load((ROOT / "schedule.yaml").read_text(encoding="utf-8")) or {}).get("playlists", {})


def _description(post: dict) -> str:
    rule = RULE if post.get("format") == "marble_league" and "R15" in str(post.get("youtube_title", "")) else ""
    q = ("Which country are you backing in the next race? Comment it below! 👇" if post.get("format") == "marble_league"
         else "Did you call it before the reveal? Comment your pick! 👇")
    return ("Call it before the reveal! 👇 Every bounce is 100% physics-driven.\n"
            "Lock your pick before the 3-2-1 countdown hits zero!\n\n"
            f"{rule}💬 {q}\n\n🎲 Race seed: {post['seed']}\n"
            "🏆 Season Standings & Playlist: https://www.youtube.com/@callitarena/playlists\n\n#shorts")


def main(seeds: list[str]) -> int:
    token = (os.getenv("IG_ACCESS_TOKEN") or "").strip()
    if not token:
        print("IG_ACCESS_TOKEN missing")
        return 1
    state = json.loads(POSTED.read_text(encoding="utf-8"))
    playlists = _yaml_playlists()
    failed = 0
    for seed in seeds:
        post = next((p for p in state.get("posts", []) if p.get("seed") == seed and not p.get("superseded")), None)
        if post is None:
            print(f"{seed}: no live post found - skipped")
            failed += 1
            continue
        if post.get("youtube_video_id"):
            print(f"{seed}: already on YouTube ({post['youtube_url']}) - skipped")
            continue
        r = requests.get(f"{GRAPH}/{post['instagram_media_id']}", params={"fields": "media_url",
                                                                          "access_token": token}, timeout=30)
        url = (r.json() or {}).get("media_url") if r.status_code == 200 else None
        if not url:
            print(f"{seed}: could not get the Reel's media_url (HTTP {r.status_code})")
            failed += 1
            continue
        with tempfile.TemporaryDirectory() as td:
            mp4 = Path(td) / f"{seed}.mp4"
            with requests.get(url, stream=True, timeout=120) as dl:
                dl.raise_for_status()
                with mp4.open("wb") as f:
                    for chunk in dl.iter_content(1 << 20):
                        f.write(chunk)
            meta = {"title": post.get("youtube_title") or f"Call It Arena {seed}", "description": _description(post),
                    "tags": list(FORMAT_YT_TAGS.get(post["format"], FORMAT_YT_TAGS["marble_league"]))[:14],
                    "categoryId": "24"}
            vid, vurl = upload_to_youtube_shorts(mp4, meta, state, playlist_title=playlists.get(post["format"], ""),
                                                 first_comment="Who are you backing next? Comment your country! 👇")
        post.update(youtube_video_id=vid, youtube_url=vurl, youtube_backfilled=True)
        state["retry_queue"] = [i for i in state.get("retry_queue", []) if i.get("seed") != seed]
        POSTED.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{seed}: uploaded {vurl}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
