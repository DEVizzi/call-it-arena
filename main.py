#!/usr/bin/env python3
"""
Call It Arena — Main Orchestrator (`main.py`).

Usage:
  python main.py --format marble_league --dry-run
  python main.py --slot A --dry-run
  python main.py --slot B
  python main.py --weekly-analytics
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv

load_dotenv()

from common.analytics import run_weekly_analytics_and_rebalance
from common.audio import ensure_audio_assets
from common.branding import (
    BASE_DIR,
    OUTPUT_DIR,
    STATE_DIR,
    ensure_flag_assets,
    generate_channel_branding_assets,
)
from common.comments import fetch_recent_comments_and_pick_wildcard
from common.seo import generate_seo_package, redact_secrets
from common.upload_instagram import upload_to_instagram_reels
from common.upload_youtube import authenticate_youtube, upload_to_youtube_shorts
from common.validate import ValidationResult, validate_video_package
from generators.bucket_drop import generate_bucket_drop_video
from generators.data_guess import generate_data_guess_video
from generators.downhill_derby import generate_downhill_derby_video
from generators.flag_quiz import generate_flag_quiz_video
from generators.guess_the_year import generate_guess_the_year_video
from generators.marble_arenas import ARENA_VARIANTS
from generators.marble_league import generate_marble_league_video
from generators.sorting_race import generate_sorting_race_video
from generators.space_guess import generate_space_guess_video
from generators.tower_quake import generate_tower_quake_video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("call_it_arena")

POSTED_STATE_FILE = STATE_DIR / "posted.json"
SCHEDULE_FILE = BASE_DIR / "schedule.yaml"

ROTATING_GUESS_FORMATS = [
    "flag_quiz",
    "data_guess",
    "space_guess",
    "sorting_race",
    "guess_the_year",
    "bucket_drop",
    "tower_quake",
    "downhill_derby",
]

ALL_FORMATS = ["marble_league"] + ROTATING_GUESS_FORMATS


def load_posted_state(path: Path = POSTED_STATE_FILE) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "posts": [],
        "retry_queue": [],
        "recent_title_patterns": [],
        "used_seeds": [],
        "used_quiz_items": [],
        "youtube_quota": {"date": "", "units_used": 0, "daily_limit": 10000},
        "playlist_ids": {},
        "weekly_analytics_history": [],
    }


def save_posted_state(state: Dict[str, Any], path: Path = POSTED_STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["recent_title_patterns"] = list(state.get("recent_title_patterns", []))[-20:]
    state["used_seeds"] = list(dict.fromkeys(state.get("used_seeds", [])))[-500:]
    state["used_quiz_items"] = list(dict.fromkeys(state.get("used_quiz_items", [])))[-200:]
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_schedule_config(path: Path = SCHEDULE_FILE) -> Dict[str, Any]:
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def resolve_slot_and_format(
    requested_slot: str,
    requested_format: str,
    schedule_cfg: Dict[str, Any],
    seed_str: str,
) -> Tuple[str, str, bool]:
    """
    Determines `(slot_id, format_name, is_weekly_recap)` based on CLI args, UTC time, and `schedule.yaml`.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if requested_slot in ("A", "B"):
        slot_id = requested_slot
    else:
        # 02:07 UTC -> Slot A, 14:07 UTC -> Slot B
        slot_id = "A" if now_utc.hour < 10 else "B"

    is_sunday = now_utc.weekday() == 6
    is_weekly_recap = is_sunday and slot_id == "B"

    if requested_format and requested_format != "auto" and requested_format in ALL_FORMATS:
        return slot_id, requested_format, is_weekly_recap

    if slot_id == "B":
        return slot_id, "marble_league", is_weekly_recap

    # Slot A: weighted selection across Formats 2–6 from schedule.yaml
    weights_cfg = schedule_cfg.get("format_weights", {})
    rng = random.Random(int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:10], 16))
    fmt_names = list(ROTATING_GUESS_FORMATS)
    fmt_weights = [max(0.01, float(weights_cfg.get(f, 0.10))) for f in fmt_names]
    chosen_fmt = rng.choices(fmt_names, weights=fmt_weights, k=1)[0]
    return slot_id, chosen_fmt, False


def build_unique_seed(slot_id: str, used_seeds: List[str], custom_seed: Optional[str] = None) -> str:
    if custom_seed:
        return custom_seed
    date_part = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    base_seed = f"{date_part}-{slot_id}"
    if base_seed not in used_seeds:
        return base_seed
    suffix = 2
    while f"{base_seed}-{suffix}" in used_seeds:
        suffix += 1
    return f"{base_seed}-{suffix}"


def invoke_generator(
    format_name: str,
    seed: str,
    output_dir: Path,
    posted_state: Dict[str, Any],
    wildcard: Optional[Dict[str, str]] = None,
    weekly_recap: bool = False,
    allow_network: bool = True,
    update_league_state: bool = True,
    arena_variant: Optional[str] = None,
) -> Dict[str, Any]:
    used_items = posted_state.get("used_quiz_items", [])
    if format_name == "marble_league":
        return generate_marble_league_video(
            seed=seed,
            output_dir=output_dir,
            wildcard=wildcard,
            weekly_recap=weekly_recap,
            update_state=update_league_state,
            variant_override=arena_variant,
        )
    if format_name == "flag_quiz":
        return generate_flag_quiz_video(
            seed=seed,
            output_dir=output_dir,
            used_items=used_items,
            wildcard=wildcard,
        )
    if format_name == "data_guess":
        return generate_data_guess_video(
            seed=seed,
            output_dir=output_dir,
            used_items=used_items,
            wildcard=wildcard,
        )
    if format_name == "space_guess":
        return generate_space_guess_video(
            seed=seed,
            output_dir=output_dir,
            allow_network=allow_network,
            wildcard=wildcard,
        )
    if format_name == "sorting_race":
        return generate_sorting_race_video(
            seed=seed,
            output_dir=output_dir,
            wildcard=wildcard,
        )
    if format_name == "guess_the_year":
        return generate_guess_the_year_video(
            seed=seed,
            output_dir=output_dir,
            allow_network=allow_network,
            wildcard=wildcard,
        )
    if format_name == "bucket_drop":
        return generate_bucket_drop_video(seed=seed, output_dir=output_dir, wildcard=wildcard)
    if format_name == "tower_quake":
        return generate_tower_quake_video(seed=seed, output_dir=output_dir, wildcard=wildcard)
    if format_name == "downhill_derby":
        return generate_downhill_derby_video(seed=seed, output_dir=output_dir, wildcard=wildcard)
    raise ValueError(f"Unsupported format: {format_name}")


def process_retry_queue(posted_state: Dict[str, Any], schedule_cfg: Dict[str, Any]) -> None:
    """
    Retries any platform uploads that failed in a previous run (`state/posted.json -> retry_queue`).
    """
    queue = list(posted_state.get("retry_queue", []))
    if not queue:
        return

    remaining_queue: List[Dict[str, Any]] = []
    playlists_cfg = schedule_cfg.get("playlists", {})

    for item in queue:
        platform = item.get("platform")
        fmt = item.get("format", "marble_league")
        seed = f"{item.get('seed', 'retry')}-R"
        logger.info("Retrying queued %s upload for format=%s (seed=%s)...", platform, fmt, seed)
        try:
            gen_meta = invoke_generator(
                format_name=fmt,
                seed=seed,
                output_dir=OUTPUT_DIR,
                posted_state=posted_state,
                allow_network=True,
                update_league_state=False,
            )
            val = validate_video_package(Path(gen_meta["video_path"]), gen_meta)
            if not val.ok:
                raise RuntimeError(f"Retry validation failed: {val.errors}")

            seo_pkg = item.get("seo_package") or generate_seo_package(
                format_name=fmt,
                seed=seed,
                context=gen_meta,
                recent_patterns=posted_state.get("recent_title_patterns", []),
            )
            if platform == "youtube":
                upload_to_youtube_shorts(
                    video_path=Path(gen_meta["video_path"]),
                    yt_meta=seo_pkg["youtube"],
                    posted_state=posted_state,
                    playlist_title=playlists_cfg.get(fmt, "Call It Arena"),
                    first_comment=seo_pkg.get("first_comment", ""),
                )
            elif platform == "instagram":
                upload_to_instagram_reels(
                    video_path=Path(gen_meta["video_path"]),
                    ig_meta=seo_pkg["instagram"],
                    first_comment=seo_pkg.get("first_comment", ""),
                )
            logger.info("Successfully completed queued retry for %s (%s)", platform, seed)
        except Exception as e:
            item["attempts"] = int(item.get("attempts", 1)) + 1
            item["last_error"] = redact_secrets(str(e))
            logger.warning("Queued retry for %s failed (attempt %d): %s", platform, item["attempts"], item["last_error"])
            if item["attempts"] < 3:
                remaining_queue.append(item)

    posted_state["retry_queue"] = remaining_queue


def run_pipeline(
    slot: str = "auto",
    format_name: str = "auto",
    custom_seed: Optional[str] = None,
    dry_run: bool = False,
    weekly_analytics: bool = False,
    arena_variant: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes the end-to-end Call It Arena pipeline:
    1. Ensures brand, flag, and CC0 audio assets exist.
    2. Reads recent YT/IG comments to select the Wildcard country.
    3. Generates the video and validates it (`validate_video_package`).
    4. Falls back automatically to a backup format if validation fails.
    5. Generates SEO metadata, 5-title scoring, and 4-language localizations.
    6. Uploads independently to YouTube Shorts and Instagram Reels (unless `dry_run=True`),
       queuing any failed platform in `state/posted.json` for the next run.

    Dry runs never mutate `state/league.json` or `state/posted.json`, so they are safe to run while a
    live league season is in progress.
    """
    ensure_flag_assets()
    ensure_audio_assets()
    generate_channel_branding_assets()

    schedule_cfg = load_schedule_config()
    posted_state = load_posted_state()

    if weekly_analytics:
        report_path = run_weekly_analytics_and_rebalance(allow_network=not dry_run)
        logger.info("Weekly analytics report written to: %s", report_path)
        if format_name == "none":
            return {"report_path": str(report_path)}

    # 1. Process any pending platform retries first when live
    if not dry_run:
        process_retry_queue(posted_state, schedule_cfg)

    # 2. Resolve slot, format, and unique seed
    slot_id_prelim = slot if slot in ("A", "B") else ("A" if datetime.datetime.now(datetime.timezone.utc).hour < 10 else "B")
    seed = build_unique_seed(slot_id_prelim, posted_state.get("used_seeds", []), custom_seed=custom_seed)
    slot_id, chosen_format, is_weekly_recap = resolve_slot_and_format(slot, format_name, schedule_cfg, seed)
    if custom_seed is None:
        seed = build_unique_seed(slot_id, posted_state.get("used_seeds", []))

    logger.info("Starting Call It Arena run | Slot=%s | Format=%s | Seed=%s | DryRun=%s", slot_id, chosen_format, seed, dry_run)

    # 3. Read recent comments on last 3 videos to extract Wildcard country request
    yt_service = None
    if not dry_run:
        try:
            yt_service = authenticate_youtube()
        except Exception:
            yt_service = None

    wildcard = fetch_recent_comments_and_pick_wildcard(
        recent_posts=posted_state.get("posts", []),
        youtube_service=yt_service,
        allow_network=not dry_run,
    )

    # 4. Generate video + Run Pre-Upload Validation Gate (with backup format fallback)
    backup_chain = [chosen_format] + [f for f in ("flag_quiz", "marble_league") if f != chosen_format]
    gen_meta: Optional[Dict[str, Any]] = None
    validation: Optional[ValidationResult] = None

    for attempt_fmt in backup_chain:
        try:
            gen_meta = invoke_generator(
                format_name=attempt_fmt,
                seed=seed,
                output_dir=OUTPUT_DIR,
                posted_state=posted_state,
                wildcard=wildcard,
                weekly_recap=is_weekly_recap and attempt_fmt == "marble_league",
                allow_network=True,
                update_league_state=not dry_run,
                arena_variant=arena_variant,
            )
            video_path = Path(gen_meta["video_path"])
            validation = validate_video_package(video_path, gen_meta)
            if validation.ok:
                chosen_format = attempt_fmt
                logger.info(
                    "Validation PASSED for %s: %.2fs, %dx%d, %.2f MB, winner=%s",
                    video_path.name,
                    validation.duration_sec,
                    validation.width,
                    validation.height,
                    validation.file_size_mb,
                    validation.winner,
                )
                break
            logger.error("Validation failed for %s: %s — falling back!", attempt_fmt, validation.errors)
        except Exception as e:
            logger.error("Generator %s raised exception (%s) — falling back!", attempt_fmt, redact_secrets(str(e)))

    if not gen_meta or not validation or not validation.ok:
        raise RuntimeError(f"All generators failed validation! Last errors: {validation.errors if validation else 'Unknown'}")

    # 5. Build SEO & Localization Package
    category_id = str(schedule_cfg.get("brand", {}).get("youtube_category_id", "24"))
    seo_pkg = generate_seo_package(
        format_name=chosen_format,
        seed=seed,
        context=gen_meta,
        recent_patterns=posted_state.get("recent_title_patterns", []),
        category_id=category_id,
        allow_network=True,
    )

    # Record pattern, seed, and quiz item in state
    posted_state.setdefault("recent_title_patterns", []).append(seo_pkg["pattern_id"])
    posted_state.setdefault("used_seeds", []).append(seed)
    if gen_meta.get("quiz_item_id"):
        posted_state.setdefault("used_quiz_items", []).append(str(gen_meta["quiz_item_id"]))

    yt_video_id, yt_url = "", ""
    ig_media_id, ig_url = "", ""
    video_path = Path(gen_meta["video_path"])

    # 6. Upload independently to YouTube Shorts & Instagram Reels when not dry_run
    if not dry_run:
        playlists_cfg = schedule_cfg.get("playlists", {})
        playlist_title = playlists_cfg.get(chosen_format, "Call It Arena")
        if chosen_format == "marble_league" and gen_meta.get("season"):
            playlist_title = f"Marble League Season {gen_meta['season']}"

        # Platform 1: YouTube Shorts
        try:
            yt_video_id, yt_url = upload_to_youtube_shorts(
                video_path=video_path,
                yt_meta=seo_pkg["youtube"],
                posted_state=posted_state,
                playlist_title=playlist_title,
                first_comment=seo_pkg.get("first_comment", ""),
            )
        except Exception as e:
            err_msg = redact_secrets(str(e))
            logger.error("YouTube upload failed (queuing for next run): %s", err_msg)
            posted_state.setdefault("retry_queue", []).append(
                {
                    "platform": "youtube",
                    "format": chosen_format,
                    "seed": seed,
                    "seo_package": seo_pkg,
                    "attempts": 1,
                    "last_error": err_msg,
                }
            )

        # Platform 2: Instagram Reels
        try:
            ig_media_id, ig_url = upload_to_instagram_reels(
                video_path=video_path,
                ig_meta=seo_pkg["instagram"],
                first_comment=seo_pkg.get("first_comment", ""),
            )
        except Exception as e:
            err_msg = redact_secrets(str(e))
            logger.error("Instagram upload failed (queuing for next run): %s", err_msg)
            posted_state.setdefault("retry_queue", []).append(
                {
                    "platform": "instagram",
                    "format": chosen_format,
                    "seed": seed,
                    "seo_package": seo_pkg,
                    "attempts": 1,
                    "last_error": err_msg,
                }
            )

    post_record = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "slot": slot_id,
        "format": chosen_format,
        "seed": seed,
        "winner": validation.winner,
        "duration_sec": validation.duration_sec,
        "file_size_mb": validation.file_size_mb,
        "youtube_title": seo_pkg["youtube"]["title"],
        "youtube_video_id": yt_video_id,
        "youtube_url": yt_url,
        "instagram_media_id": ig_media_id,
        "instagram_url": ig_url,
        "dry_run": dry_run,
    }
    if not dry_run:
        posted_state.setdefault("posts", []).append(post_record)

    if not dry_run:
        save_posted_state(posted_state)

    if is_weekly_recap and not dry_run:
        run_weekly_analytics_and_rebalance(allow_network=True)

    return {
        "video_path": str(video_path),
        "validation": validation.__dict__,
        "seo": seo_pkg,
        "post_record": post_record,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Call It Arena — Automated Shorts & Reels Engine")
    parser.add_argument("--slot", choices=["auto", "A", "B"], default="auto", help="Posting slot (A or B)")
    parser.add_argument(
        "--format",
        choices=["auto", "none"] + ALL_FORMATS,
        default="auto",
        help="Video format generator to run",
    )
    parser.add_argument("--seed", type=str, default=None, help="Explicit deterministic seed (e.g. 20261115-B)")
    parser.add_argument("--dry-run", action="store_true", help="Generate and validate MP4 locally without uploading")
    parser.add_argument("--weekly-analytics", action="store_true", help="Run Sunday weekly analytics & rebalance")
    parser.add_argument(
        "--arena",
        choices=ARENA_VARIANTS,
        default=None,
        help="Force a specific Marble League arena (testing); default = automatic no-repeat rotation",
    )
    args = parser.parse_args()

    result = run_pipeline(
        slot=args.slot,
        format_name=args.format,
        custom_seed=args.seed,
        dry_run=args.dry_run,
        weekly_analytics=args.weekly_analytics,
        arena_variant=args.arena,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
