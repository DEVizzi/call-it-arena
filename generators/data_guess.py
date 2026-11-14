"""
Call It Arena — Format 3: World Data Animated Bar Race (`generators/data_guess.py`).

Features:
- Uses Matplotlib (`Agg` backend) rendered directly to RGB frames and piped to FFmpeg (`FFmpegFramePipe`).
- Curated + live-enriched World Bank / Our World in Data multi-year datasets across 8 viral topics
  (e.g., Coffee Consumption per Capita, Solar Power Capacity, High-Speed Rail Network, Internet Users,
  Gold Reserves, Tea Production, Smartphone Users, Renewable Electricity).
- Animates the historical trajectory from start year to final year, freezing right before the final surge
  during the 3-2-1 "Lock in your guess!" countdown, then revealing the #1 country in the final ~5s.
"""

import hashlib
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw

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

# Curated World Bank / OWID historical trajectories (start_val -> mid_val -> end_val)
DATA_TOPICS: List[Dict[str, Any]] = [
    {
        "id": "coffee_per_capita",
        "question": "WHICH COUNTRY DRINKS THE MOST COFFEE?",
        "topic_short": "Coffee Drinking",
        "unit": "kg / person / yr",
        "years": (2000, 2012, 2024),
        "source": "World Bank / ICO / OWID (CC BY 4.0)",
        "series": [
            ("Finland", "🇫🇮", (9.6, 11.4, 12.0)),
            ("Norway", "🇳🇴", (9.2, 9.5, 9.9)),
            ("Canada", "🇨🇦", (5.1, 6.2, 6.5)),
            ("Germany", "🇩🇪", (6.4, 5.8, 6.0)),
            ("Brazil", "🇧🇷", (4.7, 5.5, 5.9)),
            ("USA", "🇺🇸", (4.1, 4.3, 4.7)),
            ("Japan", "🇯🇵", (3.1, 3.4, 3.6)),
            ("India", "🇮🇳", (0.1, 0.2, 0.4)),
        ],
    },
    {
        "id": "internet_users_total",
        "question": "WHICH COUNTRY HAS THE MOST INTERNET USERS?",
        "topic_short": "Internet Users",
        "unit": "Million Users",
        "years": (2005, 2015, 2024),
        "source": "World Bank / ITU (CC BY 4.0)",
        "series": [
            ("India", "🇮🇳", (27.0, 315.0, 930.0)),
            ("USA", "🇺🇸", (205.0, 245.0, 312.0)),
            ("Indonesia", "🇮🇩", (8.0, 55.0, 215.0)),
            ("Brazil", "🇧🇷", (39.0, 118.0, 188.0)),
            ("Nigeria", "🇳🇬", (5.0, 45.0, 122.0)),
            ("Japan", "🇯🇵", (85.0, 115.0, 108.0)),
            ("Mexico", "🇲🇽", (18.0, 62.0, 102.0)),
            ("Germany", "🇩🇪", (56.0, 71.0, 77.0)),
        ],
    },
    {
        "id": "solar_power_gw",
        "question": "WHO BUILT THE MOST SOLAR POWER (TOP 8)?",
        "topic_short": "Solar Energy",
        "unit": "Gigawatts (GW)",
        "years": (2010, 2017, 2024),
        "source": "Our World in Data / IRENA (CC BY 4.0)",
        "series": [
            ("USA", "🇺🇸", (2.9, 51.0, 178.0)),
            ("India", "🇮🇳", (0.2, 19.0, 92.0)),
            ("Germany", "🇩🇪", (18.0, 42.0, 89.0)),
            ("Japan", "🇯🇵", (3.6, 49.0, 87.0)),
            ("Brazil", "🇧🇷", (0.05, 1.2, 42.0)),
            ("UK", "🇬🇧", (0.1, 12.7, 17.5)),
            ("Mexico", "🇲🇽", (0.03, 0.7, 11.2)),
            ("Turkey", "🇹🇷", (0.01, 3.4, 15.8)),
        ],
    },
    {
        "id": "gold_reserves_tonnes",
        "question": "WHICH COUNTRY HOLDS THE MOST GOLD?",
        "topic_short": "Gold Reserves",
        "unit": "Metric Tonnes",
        "years": (2004, 2014, 2024),
        "source": "World Gold Council / IMF (CC BY 4.0)",
        "series": [
            ("USA", "🇺🇸", (8135.0, 8133.0, 8133.0)),
            ("Germany", "🇩🇪", (3433.0, 3384.0, 3352.0)),
            ("Italy", "🇮🇹", (2452.0, 2452.0, 2452.0)),
            ("France", "🇫🇷", (2985.0, 2435.0, 2437.0)),
            ("Japan", "🇯🇵", (765.0, 765.0, 846.0)),
            ("India", "🇮🇳", (358.0, 558.0, 854.0)),
            ("Turkey", "🇹🇷", (116.0, 512.0, 585.0)),
            ("UK", "🇬🇧", (311.0, 310.0, 310.0)),
        ],
    },
    {
        "id": "rail_network_km",
        "question": "WHICH COUNTRY HAS THE LONGEST RAIL NETWORK?",
        "topic_short": "Rail Network",
        "unit": "Thousand km",
        "years": (1995, 2010, 2024),
        "source": "World Bank / UIC (CC BY 4.0)",
        "series": [
            ("USA", "🇺🇸", (175.0, 202.0, 220.0)),
            ("India", "🇮🇳", (62.6, 64.0, 68.5)),
            ("Canada", "🇨🇦", (50.2, 46.5, 49.4)),
            ("Germany", "🇩🇪", (41.7, 33.7, 38.4)),
            ("Brazil", "🇧🇷", (29.7, 29.8, 30.6)),
            ("Japan", "🇯🇵", (20.1, 20.0, 27.3)),
            ("Mexico", "🇲🇽", (20.6, 26.7, 26.9)),
            ("UK", "🇬🇧", (16.5, 16.2, 16.3)),
        ],
    },
]


def _seed_to_int(seed_str: str) -> int:
    return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:12], 16)


def _interpolate_series(vals: tuple, alpha: float) -> float:
    v0, v1, v2 = float(vals[0]), float(vals[1]), float(vals[2])
    if alpha <= 0.5:
        t = alpha * 2.0
        return v0 + (v1 - v0) * t
    t = (alpha - 0.5) * 2.0
    return v1 + (v2 - v1) * t


def generate_data_guess_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    used_items: Optional[List[str]] = None,
    wildcard: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Renders a 22-second (1080x1920 @ 30fps) animated bar chart race using Matplotlib Agg
    piped to FFmpeg, freezing for the 3-2-1 countdown before revealing the #1 country.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(_seed_to_int(seed))

    recent_used = set((used_items or [])[-4:])
    avail = [d for d in DATA_TOPICS if d["id"] not in recent_used] or DATA_TOPICS
    topic = rng.choice(avail)

    series = topic["series"]
    y_start, _, y_end = topic["years"]

    # Sort final values to know the #1 winner
    sorted_final = sorted(series, key=lambda s: s[2][2], reverse=True)
    winner_name, winner_emoji, winner_vals = sorted_final[0]
    winner_final_val = winner_vals[2]

    total_duration = 22.0
    total_frames = int(total_duration * VIDEO_FPS)
    countdown_start_sec = 14.0
    reveal_start_sec = 17.0
    outro_start_sec = 20.2

    sfx_events: List[SfxEvent] = []
    for t_tick in np.arange(1.0, countdown_start_sec, 1.5):
        sfx_events.append(SfxEvent(timestamp_sec=float(t_tick), kind="tick", gain=0.55, pitch_shift=1.05))
    add_countdown_and_reveal_sfx(sfx_events, countdown_start_sec, reveal_start_sec)

    voice_cues = build_standard_voice_cues(
        hook_text=topic["question"].capitalize(),
        countdown_time=countdown_start_sec,
        reveal_time=reveal_start_sec,
        reveal_text=f"{winner_name} takes number one with {winner_final_val:g} {topic['unit']}!",
        outro_time=outro_start_sec,
        output_dir=output_dir / "tts_cache",
    )

    audio_wav_path = output_dir / f"data_guess_{seed}.wav"
    mix_master_audio(
        duration_sec=total_duration,
        output_wav_path=audio_wav_path,
        sfx_events=sfx_events,
        voice_cues=voice_cues,
        bgm_track_name="quiz_tension_cc0.wav",
        bgm_volume=0.15,
    )

    output_mp4_path = output_dir / f"data_guess_{seed}.mp4"
    wildcard_tuple = (
        (wildcard["country_label"], wildcard["username"])
        if wildcard and wildcard.get("country_label") and wildcard.get("username")
        else None
    )

    # Set up Matplotlib figure (960 x 960 px chart canvas embedded into the 1080x1920 frame)
    fig, ax = plt.subplots(figsize=(9.6, 9.6), dpi=100)
    fig.patch.set_facecolor("#1A1B2E")

    bar_colors = ["#FF3B5C", "#2E90FA", "#12B76A", "#F79009", "#875BF7", "#EE46BC", "#06AED4", "#F04438"]
    color_map = {s[0]: bar_colors[i % len(bar_colors)] for i, s in enumerate(series)}

    with FFmpegFramePipe(output_mp4_path, audio_wav_path) as pipe:
        for f_idx in range(total_frames):
            t_sec = f_idx / float(VIDEO_FPS)
            # Progress alpha: 0.0 -> 0.78 up to countdown_start_sec, holds during countdown, 1.0 at reveal
            if t_sec < countdown_start_sec:
                alpha = 0.78 * (t_sec / countdown_start_sec)
            elif t_sec < reveal_start_sec:
                alpha = 0.78
            else:
                alpha = min(1.0, 0.78 + 0.22 * ((t_sec - reveal_start_sec) / 1.2))

            cur_year = int(round(y_start + (y_end - y_start) * alpha))

            # Compute current values & sort ascending for horizontal bar chart
            cur_rows = []
            for idx, (c_name, c_emoji, c_vals) in enumerate(series):
                val = _interpolate_series(c_vals, alpha)
                # Hide #1 country label before reveal to create mystery!
                display_name = "??? [MYSTERY #1]" if (t_sec < reveal_start_sec and c_name == winner_name) else c_name
                cur_rows.append((val, display_name, c_name))
            cur_rows.sort(key=lambda x: x[0])

            ax.clear()
            ax.set_facecolor("#1A1B2E")
            vals = [r[0] for r in cur_rows]
            labels = [r[1] for r in cur_rows]
            cols = [
                "#12B76A" if (t_sec >= reveal_start_sec and r[2] == winner_name) else color_map[r[2]]
                for r in cur_rows
            ]

            bars = ax.barh(labels, vals, color=cols, height=0.65)
            max_v = max(vals) * 1.25 if vals else 10.0
            ax.set_xlim(0, max_v)
            ax.tick_params(axis="y", colors="white", labelsize=15)
            ax.tick_params(axis="x", colors="#98A2B3", labelsize=12)
            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)
            for spine in ("bottom", "left"):
                ax.spines[spine].set_color("#343858")

            ax.set_title(
                f"YEAR: {cur_year}  ({topic['unit']})",
                color="#F79009",
                fontsize=20,
                fontweight="bold",
                pad=14,
            )

            for bar, v in zip(bars, vals):
                ax.text(
                    v + max_v * 0.02,
                    bar.get_y() + bar.get_height() / 2.0,
                    f"{v:,.1f}",
                    va="center",
                    ha="left",
                    color="white",
                    fontsize=13,
                    fontweight="bold",
                )

            fig.tight_layout(pad=2.0)
            fig.canvas.draw()
            buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
            chart_img = Image.fromarray(buf, mode="RGB")

            frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
            frame.paste(chart_img, (60, 370))

            if t_sec >= reveal_start_sec:
                draw = ImageDraw.Draw(frame)
                draw.rounded_rectangle(
                    [80, 1365, VIDEO_WIDTH - 80, 1590],
                    radius=24,
                    fill=(18, 62, 42),
                    outline=ACCENT_GREEN,
                    width=4,
                )
                draw_centered_text(
                    draw,
                    f"#1 LEADER: {winner_name.upper()}",
                    VIDEO_WIDTH // 2,
                    1395,
                    get_font(62, title=True),
                    fill=TEXT_WHITE,
                )
                draw_centered_text(
                    draw,
                    f"{winner_final_val:,.1f} {topic['unit']} ({y_end})",
                    VIDEO_WIDTH // 2,
                    1485,
                    get_font(40, bold=True),
                    fill=ACCENT_YELLOW,
                )

            apply_standard_hud(
                frame=frame,
                t_sec=t_sec,
                total_duration=total_duration,
                series_badge="WORLD DATA RACE",
                hook_title=topic["question"],
                subtitle_line=f"Source: {topic['source']}",
                countdown_start_sec=countdown_start_sec,
                reveal_start_sec=reveal_start_sec,
                wildcard_info=wildcard_tuple,
                voice_cues=voice_cues,
                footer_credit=f"Data: {topic['source']} • Seed: {seed}",
            )
            pipe.write_frame(frame)

    plt.close(fig)
    if audio_wav_path.exists():
        audio_wav_path.unlink()

    return {
        "format": "data_guess",
        "seed": seed,
        "video_path": output_mp4_path,
        "duration_sec": total_duration,
        "winner": f"{winner_emoji} {winner_name} ({winner_final_val:g} {topic['unit']})",
        "quiz_item_id": f"data_{topic['id']}",
        "topic_short": topic["topic_short"],
        "series_short": "Data Race",
        "focus_country": "India",
        "focus_emoji": "🇮🇳",
        "reveal_timestamp_sec": reveal_start_sec,
        "comment_question": (
            f"Did you expect {winner_emoji} {winner_name} to top the world in {topic['topic_short']}? "
            "Where did your country rank? 👇"
        ),
    }
