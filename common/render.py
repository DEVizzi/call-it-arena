"""
Call It Arena — Frame-to-FFmpeg Streaming Renderer & Shared Retention HUD Overlays.

Every video follows the signature Call It Arena retention arc:
- 0–2s: Visual + spoken Hook banner
- Main interactive content / physics simulation
- 3-2-1 Countdown ("LOCK IN YOUR GUESS!")
- Final ~5s: Winner Reveal (+ Leaderboard & Cliffhanger for Marble League)
- Final ≤2s: Outro ("Comment your pick for the next one 👇")
- Burned-in captions throughout for muted viewers
"""

import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw

from .audio import SfxEvent, VoiceCue, resolve_ffmpeg_binary
from .branding import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_COLOR,
    BRAND_TAGLINE,
    PANEL_BG_COLOR,
    PANEL_BORDER_COLOR,
    TEXT_MUTED,
    TEXT_WHITE,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    get_font,
    sanitize_username,
)


class FFmpegFramePipe:
    """
    Streams 1080x1920 RGB24 frames directly into FFmpeg stdin and muxes with master WAV audio,
    encoding with `-c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -c:a aac`.
    """

    def __init__(
        self,
        output_mp4_path: Path,
        audio_wav_path: Path,
        width: int = VIDEO_WIDTH,
        height: int = VIDEO_HEIGHT,
        fps: int = VIDEO_FPS,
    ) -> None:
        self.output_mp4_path = output_mp4_path
        self.audio_wav_path = audio_wav_path
        self.width = width
        self.height = height
        self.fps = fps
        self.proc: Optional[subprocess.Popen] = None

    def __enter__(self) -> "FFmpegFramePipe":
        self.output_mp4_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_bin = resolve_ffmpeg_binary()
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{self.width}x{self.height}",
            "-r",
            str(self.fps),
            "-i",
            "-",
            "-i",
            str(self.audio_wav_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(self.output_mp4_path),
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return self

    def write_frame(self, frame: Image.Image) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("FFmpegFramePipe is not open.")
        if frame.mode != "RGB":
            frame = frame.convert("RGB")
        if frame.size != (self.width, self.height):
            frame = frame.resize((self.width, self.height), Image.Resampling.BILINEAR)
        self.proc.stdin.write(frame.tobytes())

    def write_rgb_array(self, arr: np.ndarray) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("FFmpegFramePipe is not open.")
        self.proc.stdin.write(np.ascontiguousarray(arr, dtype=np.uint8).tobytes())

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.proc is not None:
            if self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
            stderr_bytes = self.proc.stderr.read() if self.proc.stderr else b""
            ret = self.proc.wait(timeout=120)
            if exc_type is None and ret != 0:
                err_txt = stderr_bytes.decode("utf-8", errors="replace")[-800:]
                raise RuntimeError(f"FFmpeg encoding failed (exit {ret}): {err_txt}")


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    center_x: int,
    y: int,
    font,
    fill: Tuple[int, int, int] = TEXT_WHITE,
    stroke_fill: Optional[Tuple[int, int, int]] = (0, 0, 0),
    stroke_width: int = 0,
) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text(
        (center_x - w // 2, y),
        text,
        font=font,
        fill=fill,
        stroke_fill=stroke_fill if stroke_width > 0 else None,
        stroke_width=stroke_width,
    )
    return w, h


def apply_standard_hud(
    frame: Image.Image,
    t_sec: float,
    total_duration: float,
    series_badge: str,
    hook_title: str,
    subtitle_line: str = "",
    countdown_start_sec: float = 16.0,
    reveal_start_sec: float = 19.0,
    wildcard_info: Optional[Tuple[str, str]] = None,
    voice_cues: Optional[List[VoiceCue]] = None,
    footer_credit: str = "",
) -> Image.Image:
    """
    Draws the unified Call It Arena HUD onto a 1080x1920 PIL Image:
    - Top header card with series badge (`SEASON 1 · RACE 1`), main question hook, and optional Wildcard banner.
    - 0–2.0s pulsing Hook badge (`CALL IT BEFORE THE REVEAL 👇`).
    - 3-2-1 Countdown banner during `[countdown_start_sec, countdown_start_sec + 3.0)`.
    - Burned-in captions synced to `voice_cues`.
    - Final ≤1.8s Outro CTA (`Comment your pick for the next one 👇`).
    """
    draw = ImageDraw.Draw(frame)
    cx = VIDEO_WIDTH // 2

    font_badge = get_font(34, title=False, bold=True)
    font_hook = get_font(76, title=True)
    font_sub = get_font(36, title=False, bold=True)
    font_caption = get_font(40, title=False, bold=True)
    font_count = get_font(160, title=True)

    # 1. Top Header Panel (safe below top notch: y = 65..285)
    draw.rounded_rectangle(
        [44, 64, VIDEO_WIDTH - 44, 280],
        radius=28,
        fill=PANEL_BG_COLOR,
        outline=PANEL_BORDER_COLOR,
        width=4,
    )
    # Series pill badge
    pill_w = 420
    draw.rounded_rectangle(
        [cx - pill_w // 2, 80, cx + pill_w // 2, 128],
        radius=16,
        fill=ACCENT_RED,
    )
    draw_centered_text(draw, series_badge.upper(), cx, 86, font_badge, fill=TEXT_WHITE)

    # Main Hook Title
    draw_centered_text(draw, hook_title.upper(), cx, 140, font_hook, fill=TEXT_WHITE, stroke_width=3)

    if subtitle_line:
        draw_centered_text(draw, subtitle_line, cx, 224, font_sub, fill=ACCENT_YELLOW)

    # Optional Wildcard Pill (below header)
    if wildcard_info:
        wc_country_label, wc_user = wildcard_info
        safe_user = sanitize_username(wc_user)
        wc_text = f"WILDCARD: {wc_country_label}  •  Requested by {safe_user}"
        draw.rounded_rectangle(
            [90, 294, VIDEO_WIDTH - 90, 348],
            radius=18,
            fill=(32, 38, 68),
            outline=ACCENT_BLUE,
            width=3,
        )
        draw_centered_text(draw, wc_text, cx, 303, get_font(30, bold=True), fill=TEXT_WHITE)

    # 2. First 2 Seconds Hook Callout Banner (0.0s -> 2.0s)
    if t_sec <= 2.0:
        pulse = int(6 * abs((t_sec * 4.0) % 2.0 - 1.0))
        draw.rounded_rectangle(
            [110 - pulse, 1520 - pulse, VIDEO_WIDTH - 110 + pulse, 1605 + pulse],
            radius=24,
            fill=ACCENT_YELLOW,
            outline=TEXT_WHITE,
            width=4,
        )
        draw_centered_text(
            draw,
            "LOCK YOUR PICK IN THE COMMENTS!",
            cx,
            1538,
            get_font(42, title=False, bold=True),
            fill=BG_COLOR,
        )

    # 3. 3-2-1 Countdown Overlay ("Lock in your guess!")
    if countdown_start_sec <= t_sec < min(reveal_start_sec, countdown_start_sec + 3.0):
        rem = int(3 - (t_sec - countdown_start_sec))
        rem = max(1, min(3, rem))
        c_col = ACCENT_YELLOW if rem == 3 else (ACCENT_BLUE if rem == 2 else ACCENT_RED)
        draw.rounded_rectangle(
            [140, 1390, VIDEO_WIDTH - 140, 1615],
            radius=32,
            fill=(15, 15, 25),
            outline=c_col,
            width=6,
        )
        draw_centered_text(draw, f"{rem}", cx, 1398, font_count, fill=c_col, stroke_width=4)
        draw_centered_text(
            draw,
            "LOCK IN YOUR GUESS NOW!",
            cx,
            1552,
            get_font(40, bold=True),
            fill=TEXT_WHITE,
        )

    # 4. Burned-In Captions (lower-third safe zone y=1635..1715)
    active_caption = None
    for cue in voice_cues or []:
        if cue.timestamp_sec <= t_sec <= (cue.timestamp_sec + cue.duration_sec + 0.25):
            active_caption = cue.text
            break

    if active_caption:
        draw.rounded_rectangle(
            [70, 1635, VIDEO_WIDTH - 70, 1712],
            radius=18,
            fill=(10, 10, 18),
            outline=ACCENT_BLUE,
            width=2,
        )
        draw_centered_text(draw, active_caption, cx, 1652, font_caption, fill=TEXT_WHITE)

    # 5. Final Outro CTA Banner (last 1.8s of video)
    if t_sec >= max(total_duration - 1.8, reveal_start_sec + 1.5):
        draw.rounded_rectangle(
            [64, 1730, VIDEO_WIDTH - 64, 1835],
            radius=24,
            fill=ACCENT_GREEN,
            outline=TEXT_WHITE,
            width=4,
        )
        draw_centered_text(
            draw,
            "COMMENT YOUR PICK FOR THE NEXT ONE!",
            cx,
            1745,
            get_font(38, bold=True),
            fill=BG_COLOR,
        )
        draw_centered_text(
            draw,
            f"@callitarena  •  {BRAND_TAGLINE.replace('👇', '').strip()}",
            cx,
            1792,
            get_font(28, bold=True),
            fill=BG_COLOR,
        )
    elif footer_credit:
        draw_centered_text(
            draw,
            footer_credit,
            cx,
            1850,
            get_font(24, bold=False),
            fill=TEXT_MUTED,
        )

    return frame


def add_countdown_and_reveal_sfx(
    sfx_events: List[SfxEvent],
    countdown_start_sec: float,
    reveal_start_sec: float,
) -> None:
    """
    Appends the 3-2-1 countdown beeps and the winner reveal fanfare chord to `sfx_events`.
    """
    for i in range(3):
        sfx_events.append(
            SfxEvent(
                timestamp_sec=countdown_start_sec + i * 1.0,
                kind="countdown_beep",
                gain=0.75,
                pitch_shift=1.0 + i * 0.12,
            )
        )
    sfx_events.append(
        SfxEvent(
            timestamp_sec=reveal_start_sec,
            kind="reveal_fanfare",
            gain=0.85,
            pitch_shift=1.0,
        )
    )
