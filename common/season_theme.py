"""
Call It Arena — Seasonal Visual Theme Engine (`common/season_theme.py`).

Guarantees:
- Season 1 (`season <= 1`) uses `CLASSIC_S1_THEME` (`is_classic=True`), keeping the running Season 1
  visuals 100% pixel-identical.
- Season 2+ (`season >= 2`) cycles through curated, broadcast-grade Championship Editions
  (`Solar Gold`, `Cyber Emerald`, `Arctic Hyperdrive`, `Royal Synthwave`, `Crimson Inferno`) so every
  season has an unmistakable, ultra-polished stadium look & feel across arena backdrops, neon-core
  obstacles, tapered comet trails, HUD scorebugs, and reveal/standings cards.
"""

import math
import re
from dataclasses import dataclass, replace
from typing import Dict, Tuple

import numpy as np
from PIL import Image, ImageDraw


def strip_flag_emojis(text: str) -> str:
    """Removes Unicode regional-indicator flag codepoints that render as tofu boxes in TTF fonts."""
    cleaned = re.sub(r"[\U0001F1E6-\U0001F1FF]+", "", text or "")
    return re.sub(r"  +", " ", cleaned).strip()

from .branding import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_PALETTE,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_COLOR,
    PANEL_BG_COLOR,
    PANEL_BORDER_COLOR,
    TEXT_MUTED,
    TEXT_WHITE,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    get_font,
)

Color = Tuple[int, int, int]

# Default obstacle colors used in `generators/marble_arenas.py`
_ORIG_PURPLE: Color = (170, 92, 255)
_ORIG_CYAN: Color = (0, 208, 222)
_ORIG_PINK: Color = (255, 92, 196)
_ORIG_LIME: Color = (152, 228, 64)
_ORIG_STEEL: Color = (120, 132, 170)


@dataclass(frozen=True)
class SeasonTheme:
    season: int
    edition_key: str
    edition_name: str
    is_classic: bool
    bg_outer: Color
    bg_center: Color
    court_line: Color
    court_accent: Color
    panel_bg: Color
    panel_bg_alt: Color
    panel_win_row: Color
    panel_border: Color
    primary_accent: Color
    secondary_accent: Color
    win_accent: Color
    text_muted: Color
    marble_palette: Tuple[Color, ...]
    obstacle_remap: Dict[Color, Color]
    court_pattern: str  # "classic" | "radar_ticks" | "hex_grid" | "orbital_arcs" | "diamond_grid" | "octagon_target"


def blend_rgb(c1: Color, c2: Color, t: float) -> Color:
    """Linearly interpolates between `c1` and `c2` (`t` clamped to `[0.0, 1.0]`)."""
    f = max(0.0, min(1.0, float(t)))
    return (
        int(round(c1[0] + (c2[0] - c1[0]) * f)),
        int(round(c1[1] + (c2[1] - c1[1]) * f)),
        int(round(c1[2] + (c2[2] - c1[2]) * f)),
    )


def contrasting_text_color(bg: Color, dark: Color = (10, 12, 20), light: Color = TEXT_WHITE) -> Color:
    """Returns crisp dark or white text guaranteeing high perceived luminance contrast on `bg`."""
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return dark if lum >= 152.0 else light


CLASSIC_S1_THEME = SeasonTheme(
    season=1,
    edition_key="classic",
    edition_name="CLASSIC ARENA",
    is_classic=True,
    bg_outer=BG_COLOR,
    bg_center=BG_COLOR,
    court_line=(24, 27, 46),
    court_accent=(36, 40, 66),
    panel_bg=PANEL_BG_COLOR,
    panel_bg_alt=(26, 30, 52),
    panel_win_row=(38, 52, 88),
    panel_border=PANEL_BORDER_COLOR,
    primary_accent=ACCENT_YELLOW,
    secondary_accent=ACCENT_RED,
    win_accent=ACCENT_GREEN,
    text_muted=TEXT_MUTED,
    marble_palette=tuple(ACCENT_PALETTE),
    obstacle_remap={},
    court_pattern="classic",
)

# Five hand-crafted Championship Editions for Season 2+ (cycles cleanly via `(season - 2) % 5`)
_CURATED_THEMES: Tuple[SeasonTheme, ...] = (
    # Season 2 (and S7, S12...): SOLAR GOLD — Championship Gold, Warm Obsidian & Crimson-Coral
    SeasonTheme(
        season=2,
        edition_key="solar_gold",
        edition_name="SOLAR GOLD",
        is_classic=False,
        bg_outer=(10, 9, 15),
        bg_center=(34, 25, 16),
        court_line=(46, 35, 22),
        court_accent=(84, 62, 30),
        panel_bg=(22, 18, 28),
        panel_bg_alt=(30, 25, 38),
        panel_win_row=(56, 44, 22),
        panel_border=(78, 64, 42),
        primary_accent=(255, 196, 46),
        secondary_accent=(255, 70, 86),
        win_accent=(26, 214, 132),
        text_muted=(188, 178, 162),
        marble_palette=(
            (255, 198, 48),
            (255, 76, 92),
            (46, 216, 238),
            (255, 134, 42),
            (42, 224, 146),
            (218, 132, 255),
        ),
        obstacle_remap={
            ACCENT_RED: (255, 74, 88),
            ACCENT_BLUE: (48, 212, 238),
            ACCENT_GREEN: (38, 218, 140),
            ACCENT_YELLOW: (255, 198, 46),
            _ORIG_PURPLE: (255, 132, 44),
            _ORIG_CYAN: (64, 224, 242),
            _ORIG_PINK: (255, 96, 132),
            _ORIG_LIME: (255, 216, 84),
            _ORIG_STEEL: (168, 152, 132),
        },
        court_pattern="radar_ticks",
    ),
    # Season 3 (and S8, S13...): CYBER EMERALD — Deep Matrix Jade, Neon Mint & Electric Acid-Gold
    SeasonTheme(
        season=3,
        edition_key="cyber_emerald",
        edition_name="CYBER EMERALD",
        is_classic=False,
        bg_outer=(6, 14, 13),
        bg_center=(14, 38, 34),
        court_line=(20, 50, 44),
        court_accent=(32, 84, 72),
        panel_bg=(12, 26, 25),
        panel_bg_alt=(18, 36, 35),
        panel_win_row=(20, 62, 50),
        panel_border=(40, 86, 76),
        primary_accent=(20, 236, 162),
        secondary_accent=(0, 194, 216),
        win_accent=(188, 248, 64),
        text_muted=(154, 192, 184),
        marble_palette=(
            (20, 236, 162),
            (0, 218, 246),
            (206, 250, 72),
            (255, 86, 146),
            (255, 188, 52),
            (138, 116, 255),
        ),
        obstacle_remap={
            ACCENT_RED: (255, 82, 138),
            ACCENT_BLUE: (0, 218, 246),
            ACCENT_GREEN: (20, 236, 162),
            ACCENT_YELLOW: (210, 250, 72),
            _ORIG_PURPLE: (132, 112, 255),
            _ORIG_CYAN: (36, 242, 224),
            _ORIG_PINK: (255, 104, 168),
            _ORIG_LIME: (172, 248, 84),
            _ORIG_STEEL: (112, 158, 150),
        },
        court_pattern="hex_grid",
    ),
    # Season 4 (and S9, S14...): ARCTIC HYPERDRIVE — Glacier Cyan, Midnight Sapphire & Titanium
    SeasonTheme(
        season=4,
        edition_key="arctic_hyperdrive",
        edition_name="ARCTIC HYPERDRIVE",
        is_classic=False,
        bg_outer=(7, 11, 22),
        bg_center=(16, 32, 60),
        court_line=(24, 44, 80),
        court_accent=(42, 78, 136),
        panel_bg=(14, 22, 42),
        panel_bg_alt=(20, 31, 56),
        panel_win_row=(24, 52, 92),
        panel_border=(48, 82, 134),
        primary_accent=(56, 218, 255),
        secondary_accent=(68, 126, 255),
        win_accent=(34, 224, 152),
        text_muted=(158, 182, 214),
        marble_palette=(
            (56, 218, 255),
            (82, 138, 255),
            (255, 208, 68),
            (255, 84, 118),
            (38, 228, 164),
            (182, 116, 255),
        ),
        obstacle_remap={
            ACCENT_RED: (255, 84, 118),
            ACCENT_BLUE: (68, 148, 255),
            ACCENT_GREEN: (38, 228, 164),
            ACCENT_YELLOW: (255, 208, 68),
            _ORIG_PURPLE: (174, 112, 255),
            _ORIG_CYAN: (56, 224, 255),
            _ORIG_PINK: (255, 112, 188),
            _ORIG_LIME: (132, 240, 178),
            _ORIG_STEEL: (136, 164, 204),
        },
        court_pattern="orbital_arcs",
    ),
    # Season 5 (and S10, S15...): ROYAL SYNTHWAVE — Cosmic Plum, Neon Orchid & Sunset Gold
    SeasonTheme(
        season=5,
        edition_key="royal_synthwave",
        edition_name="ROYAL SYNTHWAVE",
        is_classic=False,
        bg_outer=(12, 7, 22),
        bg_center=(36, 16, 56),
        court_line=(50, 24, 78),
        court_accent=(92, 44, 134),
        panel_bg=(24, 14, 40),
        panel_bg_alt=(33, 20, 54),
        panel_win_row=(58, 28, 84),
        panel_border=(94, 52, 132),
        primary_accent=(248, 88, 198),
        secondary_accent=(168, 88, 255),
        win_accent=(255, 204, 58),
        text_muted=(192, 168, 214),
        marble_palette=(
            (248, 88, 198),
            (44, 226, 255),
            (255, 202, 58),
            (178, 96, 255),
            (46, 232, 158),
            (255, 118, 76),
        ),
        obstacle_remap={
            ACCENT_RED: (255, 82, 146),
            ACCENT_BLUE: (44, 226, 255),
            ACCENT_GREEN: (46, 232, 158),
            ACCENT_YELLOW: (255, 202, 58),
            _ORIG_PURPLE: (182, 96, 255),
            _ORIG_CYAN: (58, 234, 255),
            _ORIG_PINK: (255, 96, 208),
            _ORIG_LIME: (255, 164, 58),
            _ORIG_STEEL: (164, 142, 196),
        },
        court_pattern="diamond_grid",
    ),
    # Season 6 (and S11, S16...): CRIMSON INFERNO — Volcanic Obsidian, Magma Red & Forged Gold
    SeasonTheme(
        season=6,
        edition_key="crimson_inferno",
        edition_name="CRIMSON INFERNO",
        is_classic=False,
        bg_outer=(14, 8, 10),
        bg_center=(42, 15, 20),
        court_line=(58, 22, 28),
        court_accent=(106, 38, 46),
        panel_bg=(26, 14, 18),
        panel_bg_alt=(36, 20, 25),
        panel_win_row=(64, 26, 30),
        panel_border=(98, 44, 50),
        primary_accent=(255, 86, 62),
        secondary_accent=(255, 188, 46),
        win_accent=(32, 220, 138),
        text_muted=(198, 168, 172),
        marble_palette=(
            (255, 86, 62),
            (255, 196, 48),
            (224, 232, 246),
            (255, 138, 38),
            (38, 222, 146),
            (72, 198, 255),
        ),
        obstacle_remap={
            ACCENT_RED: (255, 76, 58),
            ACCENT_BLUE: (76, 198, 255),
            ACCENT_GREEN: (38, 222, 146),
            ACCENT_YELLOW: (255, 196, 48),
            _ORIG_PURPLE: (255, 128, 42),
            _ORIG_CYAN: (196, 214, 236),
            _ORIG_PINK: (255, 98, 118),
            _ORIG_LIME: (255, 216, 82),
            _ORIG_STEEL: (168, 144, 148),
        },
        court_pattern="octagon_target",
    ),
)


def get_season_theme(season: int) -> SeasonTheme:
    """
    Returns `CLASSIC_S1_THEME` for Season 1 (`season <= 1`), and deterministically selects
    the curated Championship Edition for Season `N >= 2` (`(N - 2) % 5`).
    """
    s_num = int(season)
    if s_num <= 1:
        return CLASSIC_S1_THEME
    template = _CURATED_THEMES[(s_num - 2) % len(_CURATED_THEMES)]
    return replace(template, season=s_num)


def draw_beveled_panel(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    radius: int,
    fill: Color,
    outer_border: Color,
    outer_width: int = 4,
    inner_border: Color = (60, 64, 88),
    top_accent: Color = None,
) -> None:
    """Draws a broadcast-grade double-beveled rounded card with optional top light bar."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill, outline=outer_border, width=outer_width)
    inset = outer_width + 3
    if (x1 - x0) > inset * 4 and (y1 - y0) > inset * 4:
        draw.rounded_rectangle(
            [x0 + inset, y0 + inset, x1 - inset, y1 - inset],
            radius=max(6, radius - inset),
            outline=inner_border,
            width=1,
        )
    if top_accent is not None and (x1 - x0) > radius * 2 + 24:
        bar_pad = radius + 6
        draw.rounded_rectangle(
            [x0 + bar_pad, y0 + 2, x1 - bar_pad, y0 + 8],
            radius=3,
            fill=top_accent,
        )


def render_radial_backdrop(
    theme: SeasonTheme,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
    focus_xy: Tuple[float, float] = (540.0, 920.0),
) -> Image.Image:
    """
    Fast NumPy radial stadium spotlight backdrop (`~8ms` once per video).
    Smoothly transitions from `theme.bg_center` at `focus_xy` to `theme.bg_outer` at edges,
    with a micro 1-LSB dither to prevent H.264 dark-gradient banding.
    """
    if theme.is_classic:
        return Image.new("RGB", (width, height), theme.bg_outer)

    ys = np.linspace(0.0, float(height - 1), height, dtype=np.float32)[:, None]
    xs = np.linspace(0.0, float(width - 1), width, dtype=np.float32)[None, :]
    dx = (xs - float(focus_xy[0])) / (width * 0.62)
    dy = (ys - float(focus_xy[1])) / (height * 0.44)
    dist = np.clip(np.sqrt(dx * dx + dy * dy), 0.0, 1.0)
    # Smoothstep curve for natural stadium spotlight falloff
    falloff = dist * dist * (3.0 - 2.0 * dist)

    c_in = np.array(theme.bg_center, dtype=np.float32)[None, None, :]
    c_out = np.array(theme.bg_outer, dtype=np.float32)[None, None, :]
    rgb = c_in + (c_out - c_in) * falloff[:, :, None]

    # Deterministic spatial Bayer-like micro-dither (+/-0.45 LSB) to eliminate dark MP4 banding
    checker = (((np.arange(height)[:, None] * 3 + np.arange(width)[None, :] * 5) % 4) - 1.5) * 0.3
    rgb = np.clip(rgb + checker[:, :, None], 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(rgb)


def _draw_corner_brackets(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    arm: int,
    color: Color,
    width: int = 3,
) -> None:
    """Broadcast viewfinder corner brackets around the active arena playfield."""
    for cx, cy, sx, sy in (
        (x0, y0, 1, 1),
        (x1, y0, -1, 1),
        (x0, y1, 1, -1),
        (x1, y1, -1, -1),
    ):
        draw.line([(cx, cy), (cx + sx * arm, cy)], fill=color, width=width)
        draw.line([(cx, cy), (cx, cy + sy * arm)], fill=color, width=width)


def _draw_themed_finish_strip(
    draw: ImageDraw.ImageDraw,
    theme: SeasonTheme,
    x0: int,
    x1: int,
    y: int,
    cell: int = 18,
) -> None:
    """Themed racing finish line with crisp accent rails and edition label."""
    rail_col = theme.primary_accent
    draw.line([(x0, y - cell - 3), (x1, y - cell - 3)], fill=rail_col, width=3)
    for row in range(2):
        for i, x in enumerate(range(x0, x1, cell)):
            col = TEXT_WHITE if (i + row) % 2 == 0 else theme.bg_outer
            draw.rectangle([x, y - cell + row * cell, min(x1, x + cell), y + row * cell], fill=col)
    draw.line([(x0, y + cell + 2), (x1, y + cell + 2)], fill=rail_col, width=3)
    fin_txt = f"FINISH LINE  •  {theme.edition_name}"
    fin_font = get_font(23, bold=True)
    fb = draw.textbbox((0, 0), fin_txt, font=fin_font)
    draw.text(
        ((x0 + x1 - (fb[2] - fb[0])) // 2, y + cell + 9),
        fin_txt,
        font=fin_font,
        fill=theme.primary_accent,
    )


def render_seasonal_arena_backdrop(
    theme: SeasonTheme,
    mode: str,
    center: Tuple[float, float],
    finish_y: float,
) -> Image.Image:
    """
    Pre-renders the complete static 1080x1920 seasonal arena court layer once per video.
    """
    img = render_radial_backdrop(theme, focus_xy=center)
    draw = ImageDraw.Draw(img)
    cx, cy = float(center[0]), float(center[1])

    pat = theme.court_pattern
    if mode == "escape":
        _draw_corner_brackets(draw, 48, 362, VIDEO_WIDTH - 48, 1365, arm=28, color=theme.court_accent, width=3)
        if pat == "radar_ticks":
            for r in (155, 285, 415, 515):
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=theme.court_line, width=2)
            for deg in range(0, 360, 15):
                rad = math.radians(deg)
                r_in = 492 if deg % 45 == 0 else 504
                col = theme.court_accent if deg % 45 == 0 else theme.court_line
                draw.line(
                    [
                        (cx + math.cos(rad) * r_in, cy + math.sin(rad) * r_in),
                        (cx + math.cos(rad) * 522, cy + math.sin(rad) * 522),
                    ],
                    fill=col,
                    width=3 if deg % 45 == 0 else 2,
                )
        elif pat == "hex_grid":
            for r in (170, 310, 450, 520):
                verts = [
                    (
                        cx + math.cos(math.radians(30 + i * 60)) * r,
                        cy + math.sin(math.radians(30 + i * 60)) * r,
                    )
                    for i in range(6)
                ]
                draw.polygon(verts, outline=theme.court_accent if r == 450 else theme.court_line, width=2)
            for i in range(6):
                rad = math.radians(30 + i * 60)
                draw.line(
                    [(cx, cy), (cx + math.cos(rad) * 520, cy + math.sin(rad) * 520)],
                    fill=theme.court_line,
                    width=2,
                )
        elif pat == "orbital_arcs":
            for r in (160, 290, 420, 515):
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=theme.court_line, width=2)
            for start_deg in (15, 105, 195, 285):
                r_arc = 470
                draw.arc(
                    [cx - r_arc, cy - r_arc, cx + r_arc, cy + r_arc],
                    start=start_deg,
                    end=start_deg + 55,
                    fill=theme.court_accent,
                    width=4,
                )
        elif pat == "diamond_grid":
            for step in range(-480, 520, 120):
                draw.line([(cx - 470, cy + step), (cx + 470, cy + step - 240)], fill=theme.court_line, width=1)
                draw.line([(cx - 470, cy + step - 240), (cx + 470, cy + step)], fill=theme.court_line, width=1)
            for r in (200, 360, 505):
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=theme.court_accent if r == 505 else theme.court_line, width=2)
        else:  # octagon_target
            for r in (175, 325, 475, 520):
                verts = [
                    (
                        cx + math.cos(math.radians(22.5 + i * 45)) * r,
                        cy + math.sin(math.radians(22.5 + i * 45)) * r,
                    )
                    for i in range(8)
                ]
                draw.polygon(verts, outline=theme.court_accent if r == 475 else theme.court_line, width=2)
            draw.line([(cx - 520, cy), (cx + 520, cy)], fill=theme.court_line, width=2)
            draw.line([(cx, cy - 520), (cx, cy + 520)], fill=theme.court_line, width=2)
    else:
        # Finish-line drop track court styling
        lane_step = 96
        for x in range(96, VIDEO_WIDTH - 64, lane_step):
            draw.line([(x, 376), (x, int(finish_y))], fill=theme.court_line, width=2)
        for y_chevron in range(460, int(finish_y) - 60, 170):
            draw.line([(74, y_chevron), (VIDEO_WIDTH - 74, y_chevron)], fill=theme.court_line, width=1)
            # Subtle downward speed chevrons in center lane
            draw.line([(cx - 38, y_chevron - 14), (cx, y_chevron + 10), (cx + 38, y_chevron - 14)],
                      fill=theme.court_accent, width=3)
        _draw_themed_finish_strip(draw, theme, 72, VIDEO_WIDTH - 72, int(finish_y) + 8, cell=18)

    return img
