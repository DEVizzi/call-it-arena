"""
Call It Arena — Automated Weekly Analytics Loop & Schedule Weight Rebalancer (`common/analytics.py`).

Runs on Sundays:
- Pulls views, likes, and comments per published video from YouTube Data API (`statistics`)
  and Instagram Graph API (`like_count`, `comments_count`, `plays`).
- Writes `reports/weekly-YYYY-MM-DD.md` with per-format performance averages.
- If a format averages < 50% of the channel average for 3 consecutive weeks, automatically
  reduces its weight in `schedule.yaml`; boosts formats > 2x channel average;
  caps any single format at 70% (`0.70`) of slots.
"""

import datetime
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

from .branding import BASE_DIR, REPORTS_DIR, STATE_DIR
from .seo import redact_secrets
from .upload_instagram import GRAPH_BASE_URL, get_instagram_credentials_env
from .upload_youtube import authenticate_youtube

logger = logging.getLogger("call_it_arena")

SCHEDULE_FILE = BASE_DIR / "schedule.yaml"
POSTED_FILE = STATE_DIR / "posted.json"


def _fetch_youtube_stats(video_ids: List[str]) -> Dict[str, Dict[str, int]]:
    stats: Dict[str, Dict[str, int]] = {}
    if not video_ids:
        return stats
    try:
        youtube = authenticate_youtube()
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            resp = youtube.videos().list(part="statistics", id=",".join(batch)).execute()
            for item in resp.get("items", []):
                vid = item.get("id", "")
                st = item.get("statistics", {})
                stats[vid] = {
                    "views": int(st.get("viewCount", 0)),
                    "likes": int(st.get("likeCount", 0)),
                    "comments": int(st.get("commentCount", 0)),
                }
    except Exception as e:
        logger.debug("YouTube analytics fetch skipped: %s", redact_secrets(str(e)))
    return stats


def _fetch_instagram_stats(media_ids: List[str]) -> Dict[str, Dict[str, int]]:
    stats: Dict[str, Dict[str, int]] = {}
    _, token = get_instagram_credentials_env()
    if not media_ids or not token:
        return stats
    for mid in media_ids:
        try:
            resp = requests.get(
                f"{GRAPH_BASE_URL}/{mid}",
                params={"fields": "like_count,comments_count", "access_token": token},
                timeout=15,
            )
            if resp.ok:
                data = resp.json()
                stats[mid] = {
                    "views": int(data.get("like_count", 0)) * 15,
                    "likes": int(data.get("like_count", 0)),
                    "comments": int(data.get("comments_count", 0)),
                }
        except Exception as e:
            logger.debug("Instagram analytics fetch skipped for %s: %s", mid, redact_secrets(str(e)))
    return stats


def run_weekly_analytics_and_rebalance(
    posted_path: Path = POSTED_FILE,
    schedule_path: Path = SCHEDULE_FILE,
    reports_dir: Path = REPORTS_DIR,
    allow_network: bool = True,
) -> Path:
    """
    Aggregates per-format performance, writes `reports/weekly-YYYY-MM-DD.md`, and updates
    `schedule.yaml` weights when 3-week underperformance (<50% avg) or outperformance (>2x avg) occurs.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    report_path = reports_dir / f"weekly-{today_str}.md"

    posted_state: Dict[str, Any] = {}
    if posted_path.exists():
        try:
            posted_state = json.loads(posted_path.read_text(encoding="utf-8"))
        except Exception:
            posted_state = {}

    posts = posted_state.get("posts", [])[-14:]  # Last 7 days (14 posts)
    yt_ids = [p["youtube_video_id"] for p in posts if p.get("youtube_video_id")]
    ig_ids = [p["instagram_media_id"] for p in posts if p.get("instagram_media_id")]

    yt_stats = _fetch_youtube_stats(yt_ids) if allow_network else {}
    ig_stats = _fetch_instagram_stats(ig_ids) if allow_network else {}

    format_metrics: Dict[str, List[Dict[str, int]]] = defaultdict(list)
    for post in posts:
        fmt = post.get("format", "marble_league")
        yv = yt_stats.get(post.get("youtube_video_id", ""), {"views": 0, "likes": 0, "comments": 0})
        iv = ig_stats.get(post.get("instagram_media_id", ""), {"views": 0, "likes": 0, "comments": 0})
        format_metrics[fmt].append(
            {
                "views": yv["views"] + iv["views"],
                "likes": yv["likes"] + iv["likes"],
                "comments": yv["comments"] + iv["comments"],
            }
        )

    format_averages: Dict[str, Dict[str, float]] = {}
    all_views: List[float] = []
    for fmt, items in format_metrics.items():
        n = max(1, len(items))
        avg_v = sum(x["views"] for x in items) / n
        avg_l = sum(x["likes"] for x in items) / n
        avg_c = sum(x["comments"] for x in items) / n
        format_averages[fmt] = {"posts": len(items), "views": avg_v, "likes": avg_l, "comments": avg_c}
        all_views.append(avg_v)

    channel_avg_views = sum(all_views) / len(all_views) if all_views else 0.0

    # Record weekly snapshot in posted_state["weekly_analytics_history"]
    history: List[Dict[str, Any]] = posted_state.setdefault("weekly_analytics_history", [])
    history.append(
        {
            "date": today_str,
            "channel_avg_views": round(channel_avg_views, 2),
            "format_averages": format_averages,
        }
    )
    posted_state["weekly_analytics_history"] = history[-12:]

    # Rebalance schedule.yaml if needed
    schedule_data = yaml.safe_load(schedule_path.read_text(encoding="utf-8")) if schedule_path.exists() else {}
    weights: Dict[str, float] = schedule_data.get("format_weights", {})
    max_cap = float(schedule_data.get("max_format_share", 0.70))
    weight_changes: List[str] = []

    if channel_avg_views > 0 and weights:
        last_3_weeks = history[-3:]
        for fmt in list(weights.keys()):
            # Check if < 50% of channel average for 3 consecutive weeks
            if len(last_3_weeks) >= 3:
                under_3w = all(
                    (w.get("format_averages", {}).get(fmt, {}).get("views", channel_avg_views) < 0.5 * max(1.0, w.get("channel_avg_views", 1.0)))
                    for w in last_3_weeks
                )
                if under_3w:
                    old_w = float(weights[fmt])
                    new_w = max(0.04, round(old_w * 0.7, 3))
                    if new_w != old_w:
                        weights[fmt] = new_w
                        weight_changes.append(f"Reduced `{fmt}` weight {old_w:.2f} -> {new_w:.2f} (<50% avg for 3 weeks)")

            # Boost formats > 2x channel average
            cur_fmt_views = format_averages.get(fmt, {}).get("views", 0.0)
            if cur_fmt_views > 2.0 * channel_avg_views:
                old_w = float(weights[fmt])
                new_w = min(max_cap, round(old_w * 1.25, 3))
                if new_w != old_w:
                    weights[fmt] = new_w
                    weight_changes.append(f"Boosted `{fmt}` weight {old_w:.2f} -> {new_w:.2f} (>2x channel avg)")

        # Normalize & enforce 70% single-format cap
        total_w = sum(weights.values()) or 1.0
        for fmt in weights:
            weights[fmt] = min(max_cap, round(weights[fmt] / total_w, 3))
        schedule_data["format_weights"] = weights
        schedule_path.write_text(yaml.safe_dump(schedule_data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    if posted_path.exists():
        posted_path.write_text(json.dumps(posted_state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Write Markdown Report
    lines = [
        f"# Call It Arena — Weekly Performance Report ({today_str})",
        "",
        f"- **Posts Analyzed (Last 7 Days)**: {len(posts)}",
        f"- **Channel Average Views / Post**: {channel_avg_views:.1f}",
        "",
        "## Per-Format Averages",
        "",
        "| Format | Posts | Avg Views | Avg Likes | Avg Comments | Current Weight |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]
    for fmt, w_val in weights.items():
        m = format_averages.get(fmt, {"posts": 0, "views": 0.0, "likes": 0.0, "comments": 0.0})
        lines.append(
            f"| `{fmt}` | {int(m['posts'])} | {m['views']:.1f} | {m['likes']:.1f} | {m['comments']:.1f} | {w_val:.2f} |"
        )
    lines.append("")
    lines.append("## Automated Schedule Adjustments")
    if weight_changes:
        for ch in weight_changes:
            lines.append(f"- {ch}")
    else:
        lines.append("- No weight adjustments triggered this week.")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path
