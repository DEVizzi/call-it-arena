"""
Call It Arena — Format 2: Progressive Un-Pixelate / Un-Blur Flag Quiz (`generators/flag_quiz.py`).

Features:
- Selects a target country flag from `/assets/flags/` (rotating through `used_quiz_items` so flags don't repeat).
- Starts heavily pixelated & Gaussian-blurred at t=0s and progressively sharpens across 5 stages.
- Shows 4 multiple-choice country options (A, B, C, D) and 2 progressive geographic clues.
- Triggers the 3-2-1 "Lock in your guess!" countdown before the final crystal-clear reveal.
- Reveals the winning country + a verified cultural/geographic fun fact in the final ~5s.
"""

import hashlib
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter

from common.audio import SfxEvent, mix_master_audio
from common.branding import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_PALETTE,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_COLOR,
    COUNTRIES_META,
    OUTPUT_DIR,
    PANEL_BG_COLOR,
    PANEL_BORDER_COLOR,
    TEXT_MUTED,
    TEXT_WHITE,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    get_font,
    load_flag_image,
)
from common.render import (
    FFmpegFramePipe,
    add_countdown_and_reveal_sfx,
    apply_standard_hud,
    draw_centered_text,
)
from common.tts import build_standard_voice_cues


def _seed_to_int(seed_str: str) -> int:
    return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:12], 16)


def _pixelate_and_blur(img: Image.Image, pixel_grid: int, blur_radius: float) -> Image.Image:
    w, h = img.size
    small_w = max(4, pixel_grid)
    small_h = max(3, int(round(small_w * (h / float(w)))))
    out = img.resize((small_w, small_h), Image.Resampling.BILINEAR).resize((w, h), Image.Resampling.NEAREST)
    if blur_radius > 0.5:
        out = out.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    return out


def generate_flag_quiz_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    used_items: Optional[List[str]] = None,
    wildcard: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Renders a 22-second (1080x1920 @ 30fps) Flag Quiz video with progressive sharpening,
    4 options, 3-2-1 countdown, and fun-fact reveal.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(_seed_to_int(seed))

    all_codes = list(COUNTRIES_META.keys())
    recent_used = set((used_items or [])[-12:])
    available = [c for c in all_codes if c not in recent_used] or all_codes
    target_code = rng.choice(available)
    target_meta = COUNTRIES_META[target_code]

    distractors = [c for c in all_codes if c != target_code]
    rng.shuffle(distractors)
    options_codes = [target_code] + distractors[:3]
    rng.shuffle(options_codes)
    option_labels = ["A", "B", "C", "D"]
    correct_idx = options_codes.index(target_code)
    correct_letter = option_labels[correct_idx]

    total_duration = 22.0
    total_frames = int(total_duration * VIDEO_FPS)
    countdown_start_sec = 14.0
    reveal_start_sec = 17.0
    outro_start_sec = 20.2

    base_flag = load_flag_image(target_code, size=(760, 490)).convert("RGB")

    # Pre-cache progressive sharpness levels to keep rendering ultra-fast
    sharpness_stages = [
        (0.0, 8, 16.0, "RESOLUTION: 5%  •  EXTREME PIXELATION"),
        (3.5, 14, 10.0, "RESOLUTION: 15%  •  SHARPENING..."),
        (7.0, 24, 6.0, f"CLUE 1: Capital starts with '{str(target_meta['capital'])[0]}'"),
        (10.5, 42, 3.0, f"CLUE 2: Capital is {target_meta['capital']}"),
        (14.0, 68, 1.5, "FINAL LOCK-IN BEFORE UNBLUR!"),
    ]
    stage_images = [
        (t0, _pixelate_and_blur(base_flag, p_grid, b_rad), label)
        for (t0, p_grid, b_rad, label) in sharpness_stages
    ]

    sfx_events: List[SfxEvent] = []
    for t0, _, _, _ in sharpness_stages[1:]:
        sfx_events.append(SfxEvent(timestamp_sec=t0, kind="tick", gain=0.70, pitch_shift=1.1))
    add_countdown_and_reveal_sfx(sfx_events, countdown_start_sec, reveal_start_sec)

    voice_cues = build_standard_voice_cues(
        hook_text="Can you guess this blurred flag before the countdown?",
        countdown_time=countdown_start_sec,
        reveal_time=reveal_start_sec,
        reveal_text=f"Answer {correct_letter}: {target_meta['name']}!",
        outro_time=outro_start_sec,
        output_dir=output_dir / "tts_cache",
    )

    audio_wav_path = output_dir / f"flag_quiz_{seed}.wav"
    mix_master_audio(
        duration_sec=total_duration,
        output_wav_path=audio_wav_path,
        sfx_events=sfx_events,
        voice_cues=voice_cues,
        bgm_track_name="quiz_tension_cc0.wav",
        bgm_volume=0.15,
    )

    output_mp4_path = output_dir / f"flag_quiz_{seed}.mp4"
    wildcard_tuple = (
        (wildcard["country_label"], wildcard["username"])
        if wildcard and wildcard.get("country_label") and wildcard.get("username")
        else None
    )

    cx = VIDEO_WIDTH // 2
    flag_x = (VIDEO_WIDTH - 760) // 2
    flag_y = 385

    with FFmpegFramePipe(output_mp4_path, audio_wav_path) as pipe:
        for f_idx in range(total_frames):
            t_sec = f_idx / float(VIDEO_FPS)
            frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
            draw = ImageDraw.Draw(frame)

            # Determine current flag image & stage label
            if t_sec >= reveal_start_sec:
                cur_flag = base_flag
                stage_banner = f"REVEALED: {target_meta['name'].upper()} ({correct_letter})"
                frame_border = ACCENT_GREEN
            else:
                cur_flag = stage_images[0][1]
                stage_banner = stage_images[0][2]
                for st_t, st_img, st_lbl in stage_images:
                    if t_sec >= st_t:
                        cur_flag = st_img
                        stage_banner = st_lbl
                frame_border = ACCENT_BLUE

            # Flag Frame Container
            draw.rounded_rectangle(
                [flag_x - 16, flag_y - 16, flag_x + 760 + 16, flag_y + 490 + 16],
                radius=24,
                fill=PANEL_BG_COLOR,
                outline=frame_border,
                width=5,
            )
            frame.paste(cur_flag, (flag_x, flag_y))

            # Progress / Clue bar under flag
            draw.rounded_rectangle(
                [120, flag_y + 520, VIDEO_WIDTH - 120, flag_y + 584],
                radius=16,
                fill=(24, 28, 48),
                outline=PANEL_BORDER_COLOR,
                width=2,
            )
            draw_centered_text(
                draw,
                stage_banner,
                cx,
                flag_y + 536,
                get_font(30, bold=True),
                fill=ACCENT_YELLOW if t_sec < reveal_start_sec else ACCENT_GREEN,
            )

            # 2x2 Multiple Choice Option Grid
            grid_top = 1005
            card_w, card_h = 440, 120
            positions = [
                (80, grid_top),
                (560, grid_top),
                (80, grid_top + 145),
                (560, grid_top + 145),
            ]
            for idx, opt_code in enumerate(options_codes):
                ox, oy = positions[idx]
                opt_meta = COUNTRIES_META[opt_code]
                is_winner = (t_sec >= reveal_start_sec) and (idx == correct_idx)
                bg_c = (20, 75, 48) if is_winner else PANEL_BG_COLOR
                bd_c = ACCENT_GREEN if is_winner else ACCENT_PALETTE[idx % 4]
                draw.rounded_rectangle(
                    [ox, oy, ox + card_w, oy + card_h],
                    radius=22,
                    fill=bg_c,
                    outline=bd_c,
                    width=4,
                )
                draw.rounded_rectangle(
                    [ox + 18, oy + 22, ox + 86, oy + 98],
                    radius=14,
                    fill=bd_c,
                )
                draw_centered_text(
                    draw,
                    option_labels[idx],
                    ox + 52,
                    oy + 38,
                    get_font(42, title=True),
                    fill=BG_COLOR,
                )
                draw.text(
                    (ox + 108, oy + 40),
                    str(opt_meta["name"]),
                    font=get_font(36, bold=True),
                    fill=TEXT_WHITE,
                )

            # Fun Fact Card on Reveal
            if t_sec >= reveal_start_sec:
                draw.rounded_rectangle(
                    [80, 1300, VIDEO_WIDTH - 80, 1590],
                    radius=24,
                    fill=(22, 28, 48),
                    outline=ACCENT_YELLOW,
                    width=4,
                )
                draw_centered_text(
                    draw,
                    f"DID YOU KNOW? ({target_meta['name'].upper()})",
                    cx,
                    1320,
                    get_font(34, bold=True),
                    fill=ACCENT_YELLOW,
                )
                # Wrap fun fact cleanly
                fact_words = str(target_meta["fact"]).split()
                lines: List[str] = []
                cur_l = ""
                font_fact = get_font(30, bold=False)
                for w in fact_words:
                    test_l = f"{cur_l} {w}".strip()
                    if draw.textbbox((0, 0), test_l, font=font_fact)[2] <= 840:
                        cur_l = test_l
                    else:
                        lines.append(cur_l)
                        cur_l = w
                if cur_l:
                    lines.append(cur_l)
                fy = 1380
                for ln in lines[:4]:
                    draw_centered_text(draw, ln, cx, fy, font_fact, fill=TEXT_WHITE)
                    fy += 44

            apply_standard_hud(
                frame=frame,
                t_sec=t_sec,
                total_duration=total_duration,
                series_badge="FLAG QUIZ CHALLENGE",
                hook_title="GUESS THE FLAG BEFORE UNBLUR!",
                subtitle_line=f"Seed: {seed}  •  Comment A, B, C, or D!",
                countdown_start_sec=countdown_start_sec,
                reveal_start_sec=reveal_start_sec,
                wildcard_info=wildcard_tuple,
                voice_cues=voice_cues,
                footer_credit="Flag Icons: lipis/flag-icons (MIT)",
            )
            pipe.write_frame(frame)

    if audio_wav_path.exists():
        audio_wav_path.unlink()

    return {
        "format": "flag_quiz",
        "seed": seed,
        "video_path": output_mp4_path,
        "duration_sec": total_duration,
        "winner": f"{target_meta['emoji']} {target_meta['name']} ({correct_letter})",
        "quiz_item_id": f"flag_{target_code}",
        "series_short": "Flag Quiz",
        "focus_country": target_meta["name"],
        "focus_emoji": target_meta["emoji"],
        "reveal_timestamp_sec": reveal_start_sec,
        "comment_question": (
            f"At what % sharpness did you spot {target_meta['emoji']} {target_meta['name']}? "
            "Comment your time & pick the next country! 👇"
        ),
    }
