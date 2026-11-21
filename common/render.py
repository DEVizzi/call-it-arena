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
    load_circular_flag_icon,
    sanitize_username,
)
from .season_theme import (
    SeasonTheme,
    blend_rgb,
    contrasting_text_color,
    draw_beveled_panel,
    strip_flag_emojis,
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


def _fit_font_for_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    sizes: Tuple[int, ...],
    title: bool = False,
    bold: bool = True,
):
    for sz in sizes:
        fnt = get_font(sz, title=title, bold=bold)
        bb = draw.textbbox((0, 0), text, font=fnt)
        if (bb[2] - bb[0]) <= max_width:
            return fnt
    return get_font(sizes[-1], title=title, bold=bold)


def _draw_centered_flag_lockup(
    frame: Image.Image,
    draw: ImageDraw.ImageDraw,
    text: str,
    flag_code: Optional[str],
    center_x: int,
    box_y0: int,
    box_y1: int,
    font,
    border_col: Tuple[int, int, int],
) -> None:
    clean = strip_flag_emojis(text)
    tb = draw.textbbox((0, 0), clean, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    mid_y = (box_y0 + box_y1) // 2
    if flag_code:
        diam = 36
        gap = 12
        total_w = diam + gap + tw
        start_x = center_x - total_w // 2
        icon = load_circular_flag_icon(flag_code, diameter=diam, border_color=border_col, border_width=2)
        frame.paste(icon, (start_x, mid_y - diam // 2), icon)
        draw.text((start_x + diam + gap, mid_y - th // 2 - tb[1]), clean, font=font, fill=TEXT_WHITE)
    else:
        draw_centered_text(draw, clean, center_x, mid_y - th // 2 - tb[1], font, fill=TEXT_WHITE)


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
    crowd_boost_banner: Optional[str] = None,
    theme: Optional[SeasonTheme] = None,
    banner_flag_code: Optional[str] = None,
) -> Image.Image:
    """
    Draws the unified Call It Arena HUD onto a 1080x1920 PIL Image:
    - When `theme` is None or `theme.is_classic` (Season 1), renders the exact Season 1 HUD.
    - When `theme` is active (`season >= 2`), renders broadcast-grade double-beveled panels,
      auto-fitted pills/captions, circular flag lockups, and seasonal Championship Edition styling.
    """
    draw = ImageDraw.Draw(frame)
    cx = VIDEO_WIDTH // 2
    use_theme = theme is not None and not theme.is_classic

    font_badge = get_font(34, title=False, bold=True)
    font_hook = get_font(76, title=True)
    font_sub = get_font(36, title=False, bold=True)
    font_caption = get_font(40, title=False, bold=True)
    font_count = get_font(160, title=True)

    own_handles = {
        "callitarena",
        "thecallitarena",
        "callitarenaofficial",
        "playcallitarena",
        "silentink",
    }

    if not use_theme:
        # 1. Classic Season 1 Header Panel (pixel-identical layout & palette, tofu-free text)
        draw.rounded_rectangle(
            [44, 64, VIDEO_WIDTH - 44, 280],
            radius=28,
            fill=PANEL_BG_COLOR,
            outline=PANEL_BORDER_COLOR,
            width=4,
        )
        pill_w = 420
        draw.rounded_rectangle(
            [cx - pill_w // 2, 80, cx + pill_w // 2, 128],
            radius=16,
            fill=ACCENT_RED,
        )
        draw_centered_text(draw, series_badge.upper(), cx, 86, font_badge, fill=TEXT_WHITE)
        draw_centered_text(draw, hook_title.upper(), cx, 140, font_hook, fill=TEXT_WHITE, stroke_width=3)
        if subtitle_line:
            draw_centered_text(draw, subtitle_line, cx, 224, font_sub, fill=ACCENT_YELLOW)

        if crowd_boost_banner:
            draw.rounded_rectangle([76, 294, VIDEO_WIDTH - 76, 348], radius=18, fill=(52, 34, 14), outline=ACCENT_YELLOW, width=3)
            draw_centered_text(draw, strip_flag_emojis(crowd_boost_banner), cx, 303, get_font(30, bold=True), fill=TEXT_WHITE)
        elif wildcard_info:
            wc_country_label, wc_user = wildcard_info
            safe_user = sanitize_username(wc_user)
            if safe_user.lstrip("@").lower().replace("_", "").replace(".", "") not in own_handles:
                wc_text = strip_flag_emojis(f"WILDCARD: {wc_country_label}  •  Requested by {safe_user}")
                draw.rounded_rectangle([90, 294, VIDEO_WIDTH - 90, 348], radius=18, fill=(32, 38, 68), outline=ACCENT_BLUE, width=3)
                draw_centered_text(draw, wc_text, cx, 303, get_font(30, bold=True), fill=TEXT_WHITE)

        if t_sec <= 2.0:
            pulse = int(6 * abs((t_sec * 4.0) % 2.0 - 1.0))
            draw.rounded_rectangle(
                [110 - pulse, 1520 - pulse, VIDEO_WIDTH - 110 + pulse, 1605 + pulse],
                radius=24,
                fill=ACCENT_YELLOW,
                outline=TEXT_WHITE,
                width=4,
            )
            draw_centered_text(draw, "LOCK YOUR PICK IN THE COMMENTS!", cx, 1538, get_font(42, title=False, bold=True), fill=BG_COLOR)

        if countdown_start_sec <= t_sec < min(reveal_start_sec, countdown_start_sec + 3.0):
            rem = int(3 - (t_sec - countdown_start_sec))
            rem = max(1, min(3, rem))
            c_col = ACCENT_YELLOW if rem == 3 else (ACCENT_BLUE if rem == 2 else ACCENT_RED)
            draw.rounded_rectangle([140, 1390, VIDEO_WIDTH - 140, 1615], radius=32, fill=(15, 15, 25), outline=c_col, width=6)
            draw_centered_text(draw, f"{rem}", cx, 1398, font_count, fill=c_col, stroke_width=4)
            draw_centered_text(draw, "LOCK IN YOUR GUESS NOW!", cx, 1552, get_font(40, bold=True), fill=TEXT_WHITE)

        active_caption = None
        for cue in voice_cues or []:
            if cue.timestamp_sec <= t_sec <= (cue.timestamp_sec + cue.duration_sec + 0.25):
                active_caption = cue.text
                break
        if active_caption:
            draw.rounded_rectangle([70, 1635, VIDEO_WIDTH - 70, 1712], radius=18, fill=(10, 10, 18), outline=ACCENT_BLUE, width=2)
            draw_centered_text(draw, active_caption, cx, 1652, font_caption, fill=TEXT_WHITE)

        if t_sec >= max(total_duration - 1.8, reveal_start_sec + 1.5):
            draw.rounded_rectangle([64, 1730, VIDEO_WIDTH - 64, 1835], radius=24, fill=ACCENT_GREEN, outline=TEXT_WHITE, width=4)
            draw_centered_text(draw, "COMMENT YOUR PICK FOR THE NEXT ONE!", cx, 1745, get_font(38, bold=True), fill=BG_COLOR)
            draw_centered_text(draw, f"@callitarena  •  {BRAND_TAGLINE.replace('👇', '').strip()}", cx, 1792, get_font(28, bold=True), fill=BG_COLOR)
        elif footer_credit:
            draw_centered_text(draw, footer_credit, cx, 1850, get_font(24, bold=False), fill=TEXT_MUTED)
        return frame

    # ------------------------------------------------------------------ Season 2+ Broadcast HUD
    assert theme is not None
    # 1. Double-beveled Broadcast Scorebug Header (y = 60..282)
    draw_beveled_panel(
        draw,
        (44, 60, VIDEO_WIDTH - 44, 282),
        radius=28,
        fill=theme.panel_bg,
        outer_border=theme.primary_accent,
        outer_width=4,
        inner_border=theme.panel_border,
        top_accent=theme.primary_accent,
    )

    badge_txt = series_badge.upper()
    font_badge_fit = _fit_font_for_width(draw, badge_txt, VIDEO_WIDTH - 220, (32, 28, 25), bold=True)
    bb = draw.textbbox((0, 0), badge_txt, font=font_badge_fit)
    pill_w = max(440, min(VIDEO_WIDTH - 148, (bb[2] - bb[0]) + 64))
    pill_fill = theme.secondary_accent
    pill_fg = contrasting_text_color(pill_fill)
    draw.rounded_rectangle(
        [cx - pill_w // 2, 80, cx + pill_w // 2, 128],
        radius=16,
        fill=pill_fill,
        outline=blend_rgb(pill_fill, TEXT_WHITE, 0.35),
        width=2,
    )
    badge_h = bb[3] - bb[1]
    draw_centered_text(draw, badge_txt, cx, 104 - badge_h // 2 - bb[1], font_badge_fit, fill=pill_fg)

    hook_txt = hook_title.upper()
    font_hook_fit = _fit_font_for_width(draw, hook_txt, VIDEO_WIDTH - 140, (76, 68, 60, 52), title=True)
    draw_centered_text(draw, hook_txt, cx, 138, font_hook_fit, fill=TEXT_WHITE, stroke_width=3)

    if subtitle_line:
        font_sub_fit = _fit_font_for_width(draw, subtitle_line, VIDEO_WIDTH - 140, (32, 28, 24), bold=True)
        draw_centered_text(draw, subtitle_line, cx, 226, font_sub_fit, fill=theme.primary_accent)

    # Crowd Boost Banner (Season 2+) or Wildcard Pill with real circular flag badge
    if crowd_boost_banner:
        clean_cb = strip_flag_emojis(crowd_boost_banner)
        font_cb = _fit_font_for_width(draw, clean_cb, VIDEO_WIDTH - 256, (29, 26, 23), bold=True)
        draw_beveled_panel(
            draw,
            (72, 293, VIDEO_WIDTH - 72, 349),
            radius=18,
            fill=theme.panel_win_row,
            outer_border=theme.primary_accent,
            outer_width=3,
            inner_border=theme.panel_border,
        )
        _draw_centered_flag_lockup(
            frame, draw, clean_cb, banner_flag_code, cx, 293, 349, font_cb, theme.primary_accent
        )
    elif wildcard_info:
        wc_country_label, wc_user = wildcard_info
        safe_user = sanitize_username(wc_user)
        if safe_user.lstrip("@").lower().replace("_", "").replace(".", "") not in own_handles:
            wc_text = strip_flag_emojis(f"WILDCARD: {wc_country_label}  •  Requested by {safe_user}")
            font_wc = _fit_font_for_width(draw, wc_text, VIDEO_WIDTH - 256, (29, 26, 23), bold=True)
            draw_beveled_panel(
                draw,
                (86, 293, VIDEO_WIDTH - 86, 349),
                radius=18,
                fill=theme.panel_bg_alt,
                outer_border=theme.secondary_accent,
                outer_width=3,
                inner_border=theme.panel_border,
            )
            _draw_centered_flag_lockup(
                frame, draw, wc_text, banner_flag_code, cx, 293, 349, font_wc, theme.secondary_accent
            )

    # 2. First 2 Seconds Hook Callout Banner (0.0s -> 2.0s)
    if t_sec <= 2.0:
        pulse = int(6 * abs((t_sec * 4.0) % 2.0 - 1.0))
        cta_fill = theme.primary_accent
        cta_fg = contrasting_text_color(cta_fill)
        draw.rounded_rectangle(
            [104 - pulse, 1518 - pulse, VIDEO_WIDTH - 104 + pulse, 1606 + pulse],
            radius=24,
            fill=cta_fill,
            outline=TEXT_WHITE,
            width=4,
        )
        draw_centered_text(
            draw,
            "LOCK YOUR PICK IN THE COMMENTS!",
            cx,
            1540,
            get_font(40, title=False, bold=True),
            fill=cta_fg,
        )

    # 3. 3-2-1 Countdown Overlay
    if countdown_start_sec <= t_sec < min(reveal_start_sec, countdown_start_sec + 3.0):
        rem = int(3 - (t_sec - countdown_start_sec))
        rem = max(1, min(3, rem))
        c_col = theme.primary_accent if rem == 3 else (theme.secondary_accent if rem == 2 else ACCENT_RED)
        draw_beveled_panel(
            draw,
            (136, 1388, VIDEO_WIDTH - 136, 1616),
            radius=32,
            fill=theme.bg_outer,
            outer_border=c_col,
            outer_width=6,
            inner_border=theme.panel_border,
            top_accent=c_col,
        )
        draw_centered_text(draw, f"{rem}", cx, 1398, font_count, fill=c_col, stroke_width=4)
        draw_centered_text(
            draw,
            "LOCK IN YOUR GUESS NOW!",
            cx,
            1552,
            get_font(38, bold=True),
            fill=TEXT_WHITE,
        )

    # 4. Burned-In Captions (auto-fitted to prevent clipping)
    active_caption = None
    for cue in voice_cues or []:
        if cue.timestamp_sec <= t_sec <= (cue.timestamp_sec + cue.duration_sec + 0.25):
            active_caption = cue.text
            break

    if active_caption:
        font_cap_fit = _fit_font_for_width(draw, active_caption, VIDEO_WIDTH - 184, (38, 33, 28, 24), bold=True)
        cb = draw.textbbox((0, 0), active_caption, font=font_cap_fit)
        cap_h = cb[3] - cb[1]
        draw.rounded_rectangle(
            [68, 1634, VIDEO_WIDTH - 68, 1714],
            radius=18,
            fill=theme.bg_outer,
            outline=theme.primary_accent,
            width=2,
        )
        draw_centered_text(draw, active_caption, cx, 1674 - cap_h // 2 - cb[1], font_cap_fit, fill=TEXT_WHITE)

    # 5. Final Outro CTA Banner (last 1.8s of video)
    if t_sec >= max(total_duration - 1.8, reveal_start_sec + 1.5):
        out_fill = theme.win_accent
        out_fg = contrasting_text_color(out_fill)
        draw.rounded_rectangle(
            [64, 1728, VIDEO_WIDTH - 64, 1836],
            radius=24,
            fill=out_fill,
            outline=TEXT_WHITE,
            width=4,
        )
        draw_centered_text(
            draw,
            "COMMENT YOUR PICK FOR THE NEXT ONE!",
            cx,
            1744,
            get_font(36, bold=True),
            fill=out_fg,
        )
        draw_centered_text(
            draw,
            f"@callitarena  •  {theme.edition_name} EDITION",
            cx,
            1792,
            get_font(27, bold=True),
            fill=out_fg,
        )
    elif footer_credit:
        draw_centered_text(
            draw,
            footer_credit,
            cx,
            1850,
            get_font(24, bold=False),
            fill=theme.text_muted,
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
