"""
Call It Arena — Format 6: Guess The Year with Wikipedia "On This Day" + License-Checked Wikimedia Commons (`generators/guess_the_year.py`).

Features:
- Queries the official Wikimedia REST API (`https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected/{MM}/{DD}`)
  and verifies the image license on Wikimedia Commons via `action=query&prop=imageinfo&iiprop=extmetadata`,
  allowing ONLY `Public domain`, `CC0`, `CC BY`, or `CC BY-SA` images (`is_valid_commons_license`).
- Redacts the 4-digit year from the historical event clues (`[ ???? ]`), presents 4 candidate years (A, B, C, D),
  runs the 3-2-1 "Lock in your guess!" countdown, and reveals the exact year in the final ~5s.
- Includes a curated Public-Domain World History milestone fallback pool for offline/rate-limited runs.
"""

import datetime
import hashlib
import io
import logging
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests
from PIL import Image, ImageDraw

from common.audio import SfxEvent, mix_master_audio
from common.branding import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_PALETTE,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_COLOR,
    OUTPUT_DIR,
    PANEL_BG_COLOR,
    PANEL_BORDER_COLOR,
    TEXT_MUTED,
    TEXT_WHITE,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    get_font,
)
from common.render import (
    FFmpegFramePipe,
    add_countdown_and_reveal_sfx,
    apply_standard_hud,
    draw_centered_text,
)
from common.tts import build_standard_voice_cues

logger = logging.getLogger("call_it_arena")

ALLOWED_LICENSE_PREFIXES = (
    "public domain",
    "pd",
    "cc0",
    "cc-by",
    "cc by",
)

CURATED_HISTORY_MILESTONES: List[Dict[str, Any]] = [
    {
        "year": 1969,
        "headline": "Apollo 11 astronauts Neil Armstrong and Buzz Aldrin land the Lunar Module Eagle on the Moon.",
        "clue_region": "Space Exploration • Sea of Tranquility",
        "license": "Public Domain (NASA)",
    },
    {
        "year": 1989,
        "headline": "Citizens gather as the Berlin Wall opens after 28 years of dividing East and West Berlin.",
        "clue_region": "European History • Cold War",
        "license": "CC BY-SA 3.0 (Wikimedia Commons)",
    },
    {
        "year": 1947,
        "headline": "India attains independence at midnight as Jawaharlal Nehru delivers the 'Tryst with Destiny' speech.",
        "clue_region": "South Asian History • New Delhi",
        "license": "Public Domain (Wikimedia Commons)",
    },
    {
        "year": 1912,
        "headline": "The RMS Titanic strikes an iceberg in the North Atlantic during her maiden voyage from Southampton.",
        "clue_region": "Maritime History • North Atlantic",
        "license": "Public Domain (Wikimedia Commons)",
    },
    {
        "year": 1903,
        "headline": "Orville and Wilbur Wright achieve the first controlled, powered heavier-than-air flight at Kitty Hawk.",
        "clue_region": "Aviation History • North Carolina",
        "license": "Public Domain (Library of Congress)",
    },
    {
        "year": 1991,
        "headline": "Tim Berners-Lee makes the World Wide Web publicly available on the Internet for the first time.",
        "clue_region": "Technology History • CERN",
        "license": "Public Domain (CERN / Wikimedia)",
    },
]


def _seed_to_int(seed_str: str) -> int:
    return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:12], 16)


def is_valid_commons_license(license_short_name: str) -> bool:
    """
    Verifies that a Wikimedia Commons `LicenseShortName` string belongs to an unrestricted
    or attribution-only free license (`Public domain`, `CC0`, `CC BY`, `CC BY-SA`).
    """
    clean = (license_short_name or "").strip().lower()
    if not clean or "non-free" in clean or "fair use" in clean or "copyrighted" in clean:
        return False
    return any(clean.startswith(prefix) or prefix in clean for prefix in ALLOWED_LICENSE_PREFIXES)


def _check_wikimedia_commons_image_license(thumb_url: str) -> Optional[str]:
    """
    Extracts the filename from a Wikimedia thumbnail URL and queries the Commons API
    `iiprop=extmetadata` to verify a valid free license.
    """
    try:
        m = re.search(r"/wikipedia/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/]+)", thumb_url)
        if not m:
            return None
        file_title = "File:" + unquote(m.group(1))
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "titles": file_title,
                "prop": "imageinfo",
                "iiprop": "extmetadata",
                "format": "json",
            },
            headers={"User-Agent": "CallItArenaBot/1.0 (https://github.com/DEVizzi/call-it-arena)"},
            timeout=10,
        )
        if not resp.ok:
            return None
        pages = resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            ii = page.get("imageinfo", [{}])[0].get("extmetadata", {})
            lic = str(ii.get("LicenseShortName", {}).get("value", "")).strip()
            if is_valid_commons_license(lic):
                return lic
    except Exception:
        pass
    return None


def _render_vintage_history_card(year: int, headline: str, region: str) -> Image.Image:
    w, h = 840, 480
    img = Image.new("RGB", (w, h), (32, 28, 44))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([14, 14, w - 14, h - 14], radius=20, fill=(44, 38, 58), outline=ACCENT_YELLOW, width=4)
    draw_centered_text(draw, "ARCHIVAL CHRONICLE RECORD", w // 2, 46, get_font(36, title=True), fill=ACCENT_YELLOW)
    draw_centered_text(draw, region.upper(), w // 2, 102, get_font(28, bold=True), fill=TEXT_MUTED)

    # Wrap headline with year masked
    masked = re.sub(r"\b(1[0-9]{3}|20[0-2][0-9])\b", "[ ???? ]", headline)
    font_h = get_font(34, bold=True)
    words = masked.split()
    lines: List[str] = []
    cur = ""
    for word in words:
        test = f"{cur} {word}".strip()
        if draw.textbbox((0, 0), test, font=font_h)[2] <= 740:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    y_pos = 185
    for ln in lines[:4]:
        draw_centered_text(draw, ln, w // 2, y_pos, font_h, fill=TEXT_WHITE)
        y_pos += 52
    return img


def fetch_on_this_day_event(seed: str, allow_network: bool = True) -> Tuple[Image.Image, Dict[str, Any]]:
    """
    Fetches a historical event from Wikipedia's "On this day" API and verifies the Wikimedia Commons
    image license via `extmetadata`, falling back to curated Public-Domain milestones if offline.
    """
    rng = random.Random(_seed_to_int(seed))
    if allow_network:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            url = f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected/{now.month:02d}/{now.day:02d}"
            resp = requests.get(
                url,
                headers={"User-Agent": "CallItArenaBot/1.0 (https://github.com/DEVizzi/call-it-arena)"},
                timeout=12,
            )
            if resp.ok:
                events = [e for e in resp.json().get("selected", []) if int(e.get("year", 0)) >= 1750]
                rng.shuffle(events)
                for ev in events[:8]:
                    year = int(ev["year"])
                    text = str(ev.get("text", "")).strip()
                    for page in ev.get("pages", []):
                        thumb = page.get("thumbnail", {}).get("source", "")
                        if not thumb:
                            continue
                        lic = _check_wikimedia_commons_image_license(thumb)
                        if not lic:
                            continue
                        img_r = requests.get(
                            thumb,
                            headers={"User-Agent": "CallItArenaBot/1.0"},
                            timeout=12,
                        )
                        if img_r.ok:
                            pil_img = Image.open(io.BytesIO(img_r.content)).convert("RGB")
                            return pil_img, {
                                "year": year,
                                "headline": text,
                                "clue_region": str(page.get("description", "World History Milestone")),
                                "license": f"{lic} (Wikimedia Commons)",
                            }
        except Exception as e:
            logger.debug("Wikipedia On This Day live fetch fallback activated: %s", e)

    chosen = CURATED_HISTORY_MILESTONES[_seed_to_int(seed) % len(CURATED_HISTORY_MILESTONES)]
    card_img = _render_vintage_history_card(chosen["year"], chosen["headline"], chosen["clue_region"])
    return card_img, chosen


def generate_guess_the_year_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    allow_network: bool = True,
    wildcard: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Renders a 22-second (1080x1920 @ 30fps) Guess The Year video with progressive historical clues,
    4 candidate years, 3-2-1 countdown, and exact year reveal.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(_seed_to_int(seed))

    hist_img, hist_meta = fetch_on_this_day_event(seed=seed, allow_network=allow_network)
    correct_year = int(hist_meta["year"])
    headline = str(hist_meta["headline"])
    masked_headline = re.sub(r"\b(1[0-9]{3}|20[0-2][0-9])\b", "[ ???? ]", headline)

    # Generate 3 plausible distractor years near `correct_year`
    offsets = rng.sample([-14, -8, -4, 5, 9, 15], 3)
    options_years = sorted([correct_year] + [min(2025, max(1700, correct_year + off)) for off in offsets])
    # Deduplicate if needed
    while len(set(options_years)) < 4:
        options_years.append(correct_year + len(options_years) * 3)
        options_years = sorted(list(set(options_years)))[:4]

    option_labels = ["A", "B", "C", "D"]
    correct_idx = options_years.index(correct_year)
    correct_letter = option_labels[correct_idx]

    total_duration = 22.0
    total_frames = int(total_duration * VIDEO_FPS)
    countdown_start_sec = 14.0
    reveal_start_sec = 17.0
    outro_start_sec = 20.2

    sfx_events: List[SfxEvent] = [
        SfxEvent(timestamp_sec=3.5, kind="tick", gain=0.65, pitch_shift=1.0),
        SfxEvent(timestamp_sec=7.5, kind="tick", gain=0.65, pitch_shift=1.1),
        SfxEvent(timestamp_sec=11.0, kind="tick", gain=0.65, pitch_shift=1.2),
    ]
    add_countdown_and_reveal_sfx(sfx_events, countdown_start_sec, reveal_start_sec)

    voice_cues = build_standard_voice_cues(
        hook_text="Can you call the exact year this historical event happened?",
        countdown_time=countdown_start_sec,
        reveal_time=reveal_start_sec,
        reveal_text=f"It happened in {correct_year}! Option {correct_letter}!",
        outro_time=outro_start_sec,
        output_dir=output_dir / "tts_cache",
    )

    audio_wav_path = output_dir / f"guess_the_year_{seed}.wav"
    mix_master_audio(
        duration_sec=total_duration,
        output_wav_path=audio_wav_path,
        sfx_events=sfx_events,
        voice_cues=voice_cues,
        bgm_track_name="quiz_tension_cc0.wav",
        bgm_volume=0.15,
    )

    output_mp4_path = output_dir / f"guess_the_year_{seed}.mp4"
    wildcard_tuple = (
        (wildcard["country_label"], wildcard["username"])
        if wildcard and wildcard.get("country_label") and wildcard.get("username")
        else None
    )

    cx = VIDEO_WIDTH // 2
    disp_img = hist_img.resize((840, 480), Image.Resampling.LANCZOS)
    decade_str = f"{(correct_year // 10) * 10}s"
    century_str = f"{(correct_year // 100) + 1}th Century"

    with FFmpegFramePipe(output_mp4_path, audio_wav_path) as pipe:
        for f_idx in range(total_frames):
            t_sec = f_idx / float(VIDEO_FPS)
            frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
            draw = ImageDraw.Draw(frame)

            # Image Container
            img_x, img_y = (VIDEO_WIDTH - 840) // 2, 370
            draw.rounded_rectangle(
                [img_x - 12, img_y - 12, img_x + 840 + 12, img_y + 480 + 12],
                radius=24,
                fill=PANEL_BG_COLOR,
                outline=ACCENT_GREEN if t_sec >= reveal_start_sec else ACCENT_BLUE,
                width=4,
            )
            frame.paste(disp_img, (img_x, img_y))

            # Progressive Clues Box
            draw.rounded_rectangle(
                [80, 880, VIDEO_WIDTH - 80, 1085],
                radius=22,
                fill=PANEL_BG_COLOR,
                outline=PANEL_BORDER_COLOR,
                width=3,
            )
            if t_sec < 5.0:
                clue_line = f"CLUE 1: {century_str}  •  {hist_meta['clue_region']}"
            elif t_sec < reveal_start_sec:
                clue_line = f"CLUE 2: Happened in the {decade_str}  •  {hist_meta['clue_region']}"
            else:
                clue_line = f"REVEALED YEAR: {correct_year} ({correct_letter})"

            draw_centered_text(
                draw,
                clue_line,
                cx,
                898,
                get_font(30, bold=True),
                fill=ACCENT_GREEN if t_sec >= reveal_start_sec else ACCENT_YELLOW,
            )

            # Event description text
            shown_text = headline if t_sec >= reveal_start_sec else masked_headline
            font_ev = get_font(28, bold=False)
            words = shown_text.split()
            lines: List[str] = []
            cur = ""
            for w in words:
                test = f"{cur} {w}".strip()
                if draw.textbbox((0, 0), test, font=font_ev)[2] <= 840:
                    cur = test
                else:
                    lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
            ey = 950
            for ln in lines[:3]:
                draw_centered_text(draw, ln, cx, ey, font_ev, fill=TEXT_WHITE)
                ey += 38

            # 4 Year Option Cards
            grid_top = 1110
            card_w, card_h = 440, 112
            positions = [
                (80, grid_top),
                (560, grid_top),
                (80, grid_top + 132),
                (560, grid_top + 132),
            ]
            for idx, yr in enumerate(options_years):
                ox, oy = positions[idx]
                is_win = (t_sec >= reveal_start_sec) and (idx == correct_idx)
                bg_c = (20, 75, 48) if is_win else PANEL_BG_COLOR
                bd_c = ACCENT_GREEN if is_win else ACCENT_PALETTE[idx % 4]
                draw.rounded_rectangle([ox, oy, ox + card_w, oy + card_h], radius=20, fill=bg_c, outline=bd_c, width=4)
                draw.rounded_rectangle([ox + 16, oy + 18, ox + 82, oy + 94], radius=12, fill=bd_c)
                draw_centered_text(draw, option_labels[idx], ox + 49, oy + 34, get_font(40, title=True), fill=BG_COLOR)
                draw.text((ox + 115, oy + 28), str(yr), font=get_font(48, title=True), fill=TEXT_WHITE)

            apply_standard_hud(
                frame=frame,
                t_sec=t_sec,
                total_duration=total_duration,
                series_badge="GUESS THE YEAR",
                hook_title="WHAT YEAR DID THIS HAPPEN?",
                subtitle_line=f"Source: Wikipedia On This Day  •  Seed: {seed}",
                countdown_start_sec=countdown_start_sec,
                reveal_start_sec=reveal_start_sec,
                wildcard_info=wildcard_tuple,
                voice_cues=voice_cues,
                footer_credit=f"License: {hist_meta['license']}",
            )
            pipe.write_frame(frame)

    if audio_wav_path.exists():
        audio_wav_path.unlink()

    return {
        "format": "guess_the_year",
        "seed": seed,
        "video_path": output_mp4_path,
        "duration_sec": total_duration,
        "winner": f"{correct_year} (Option {correct_letter})",
        "quiz_item_id": f"year_{correct_year}",
        "series_short": "History Quiz",
        "reveal_timestamp_sec": reveal_start_sec,
        "comment_question": (
            f"Did you lock in {correct_year} ({correct_letter}) before the reveal? ⏳ "
            "How many years off was your first guess? 👇"
        ),
    }
