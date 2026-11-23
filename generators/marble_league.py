"""
Call It Arena — Format 1 (FLAGSHIP): Country Marble League (`generators/marble_league.py`).

Features:
- 16 high-engagement countries: India, USA, Brazil, Indonesia, Philippines, Pakistan,
  Bangladesh, Nigeria, Mexico, UK, Germany, Japan, Turkey, Egypt, Vietnam, Canada.
- Headless Pygame (`SDL_VIDEODRIVER=dummy`) + Pymunk 2D rigid-body physics simulation.
- 14 physically distinct arenas (see `generators/marble_arenas.py`):
  9 escape-style arenas (rings, hexagon, square crusher, gear ring, gravity well) and
  5 drop races to a finish line (plinko, zigzag ramps, windmills, hourglass, pendulums).
- Anti-repeat arena rotation: an arena is never reused within the previous 9 league races
  (read from `state/league.json -> history`), so viewers see a fresh game type every day.
- Adaptive physics tuning: each race is pre-simulated and re-run with a scaled intensity if the
  first finisher would arrive too early/late, so every arena yields a watchable race.
- NO country names are shown while the race runs (only an anonymous `ESCAPED 3/12` counter),
  so viewers must lock in their guess. Names appear only at the reveal:
  page 1 = full race result table (position, time, points), page 2 = season standings so far.
- Logs Pymunk collision impulses to emit synced CC0 `boing` SFX timestamps.
- Updates `state/league.json` across a 4-week season (Group Stage -> Knockouts -> Monthly Grand Final)
  and auto-starts the next season after Race 28.
- Also supports Sunday Slot B `"Weekly Standings + Mega Quiz"` mode (`weekly_recap=True`).
"""

import copy
import hashlib
import json
import logging
import math
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pymunk
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
    PANEL_BG_COLOR,
    PANEL_BORDER_COLOR,
    STATE_DIR,
    TEXT_MUTED,
    TEXT_WHITE,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    get_font,
    load_circular_flag_icon,
    sanitize_username,
)
from common.comments import is_own_channel_author
from common.physics_kit import RotatingIconCache, attach_impulse_sfx, draw_checkered_line
from common.render import (
    FFmpegFramePipe,
    _fit_font_for_width,
    add_countdown_and_reveal_sfx,
    apply_standard_hud,
    draw_centered_text,
)
from common.season_theme import (
    SeasonTheme,
    blend_rgb,
    contrasting_text_color,
    draw_beveled_panel,
    get_season_theme,
    render_radial_backdrop,
    render_seasonal_arena_backdrop,
)
from common.commentary import (
    analyze_race,
    finish_threshold_for,
    plan_shorts_lines,
    plan_shorts_post_reveal_line,
    schedule_lines_into_cues,
)
from common import league_rules
from common.tts import build_standard_voice_cues, synthesize_phrase
from generators.marble_arenas import ARENA_VARIANTS, MEGA_VARIANT, ArenaConfig, build_arena

logger = logging.getLogger("call_it_arena")

LEAGUE_STATE_FILE = STATE_DIR / "league.json"

LEAGUE_COUNTRY_CODES = [
    "IN", "US", "BR", "ID", "PH", "PK", "BD", "NG",
    "MX", "GB", "DE", "JP", "TR", "EG", "VN", "CA",
]

# An arena is not reused within this many previous races (14 arenas → ~10-race freshness window).
ARENA_NO_REPEAT_WINDOW = 9

POINTS_TABLE = [10, 7, 5, 3, 1]

# Season 2+ Comment Crowd Physics Boost (strictly inactive during Season 1; keeps radius unchanged for ring gaps)
CROWD_BOOST_MIN_SEASON = 2
CROWD_BOOST_MASS_MULT = 1.08
CROWD_BOOST_SPEED_MULT = 1.06
CROWD_BOOST_ELASTICITY_MULT = 1.03
CROWD_BOOST_FRICTION_MULT = 0.85

# Video timeline (seconds)
TOTAL_DURATION = 26.0
COUNTDOWN_START_SEC = 15.5
REVEAL_START_SEC = 18.5
STANDINGS_PAGE_SEC = 22.2
OUTRO_START_SEC = 24.2

PHYSICS_SUBSTEPS = 4

# Grand Final (race 28) MEGA RACE timeline: longer video, triple points, champion reveal
MEGA_TOTAL_DURATION = 40.0
MEGA_COUNTDOWN_START_SEC = 29.5
MEGA_REVEAL_START_SEC = 32.5
MEGA_STANDINGS_PAGE_SEC = 36.0
MEGA_OUTRO_START_SEC = 38.2
GRAND_FINAL_POINTS_MULTIPLIER = 3


@dataclass
class RacePacing:
    """How long the physics race runs and what counts as good pacing for it."""

    sim_until: float = REVEAL_START_SEC
    t1_lo: float = 4.0
    t1_hi: float = 12.0
    early_cut: float = 9.5


REGULAR_PACING = RacePacing()
MEGA_PACING = RacePacing(sim_until=MEGA_REVEAL_START_SEC, t1_lo=9.0, t1_hi=24.0, early_cut=20.0)


def load_league_state(path: Path = LEAGUE_STATE_FILE) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    countries = {}
    for code in LEAGUE_COUNTRY_CODES:
        meta = COUNTRIES_META[code]
        countries[code] = {
            "code": code,
            "name": meta["name"],
            "emoji": meta["emoji"],
            "points": 0,
            "wins": 0,
            "podiums": 0,
            "eliminated": False,
        }
    return {
        "season": 1,
        "race_number": 0,
        "max_races_per_season": 28,
        "stage": "Group Stage",
        "countries": countries,
        "history": [],
        "past_champions": [],
    }


def save_league_state(state: Dict[str, Any], path: Path = LEAGUE_STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def compute_standings(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns all 16 countries sorted by `(not eliminated, eliminated later, points DESC, wins DESC, podiums DESC)`.
    """
    items = list(state.get("countries", {}).values())
    items.sort(
        key=lambda c: (
            0 if c.get("eliminated") else 1,
            int(c.get("eliminated_in") or 0),      # v2: a later knockout = a better finish
            int(c.get("points", 0)),
            int(c.get("wins", 0)),
            int(c.get("podiums", 0)),
            c.get("code") == "IN",
        ),
        reverse=True,
    )
    return items


def _seed_to_int(seed_str: str) -> int:
    return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:12], 16)


def choose_arena_variant(seed: str, history: List[Dict[str, Any]], window: int = ARENA_NO_REPEAT_WINDOW) -> str:
    """
    Deterministically picks an arena for `seed`, excluding any arena used in the last `window`
    league races so consecutive races always look different.
    """
    recent = {h.get("variant") for h in (history or [])[-window:] if isinstance(h, dict)}
    pool = [v for v in ARENA_VARIANTS if v not in recent] or list(ARENA_VARIANTS)
    return pool[_seed_to_int(seed) % len(pool)]


def pick_pre_race_focus_and_rival(
    race_codes: List[str],
    pre_race_standings: List[Dict[str, Any]],
    seed: str,
    wildcard: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    Deterministically selects `(focus_code, rival_code)` strictly from PRE-RACE standings and
    `race_codes` (never from `full_finish_order`), eliminating P1/P2 title spoilers while
    spotlighting real Season title contenders and viewer Wildcard nations.
    """
    focus_code = (
        "IN"
        if "IN" in race_codes
        else next((s["code"] for s in pre_race_standings if s["code"] in race_codes), race_codes[0])
    )
    rival_pool = [
        s["code"]
        for s in pre_race_standings
        if s["code"] in race_codes and s["code"] != focus_code
    ][:4]
    if wildcard:
        wc = str(wildcard.get("country_code", ""))
        if wc in race_codes and wc != focus_code and wc not in rival_pool:
            rival_pool.append(wc)
    if not rival_pool:
        rival_pool = [c for c in race_codes if c != focus_code] or ["BR"]
    rival_code = rival_pool[_seed_to_int(f"{seed}|rival") % len(rival_pool)]
    return focus_code, rival_code


# --------------------------------------------------------------------------- simulation
@dataclass
class RaceSimulation:
    arena: ArenaConfig
    intensity: float
    frames: List[Dict[str, Any]]
    finish_order: List[str]
    finish_times: Dict[str, float]
    final_progress: Dict[str, float]
    sfx_events: List[SfxEvent] = field(default_factory=list)


def _progress(arena: ArenaConfig, x: float, y: float) -> float:
    """Larger = closer to finishing (used for live counter + ranking non-finishers)."""
    if arena.mode == "line":
        return y
    return math.hypot(x - arena.center[0], y - arena.center[1])


def _is_finished(arena: ArenaConfig, x: float, y: float, marble_radius: float) -> bool:
    if arena.mode == "line":
        return y > arena.finish_y + marble_radius * 0.5
    return math.hypot(x - arena.center[0], y - arena.center[1]) > arena.escape_radius + marble_radius * 0.9


def simulate_race(
    seed: str,
    variant: str,
    race_codes: List[str],
    marble_radius: float,
    intensity: float,
    sim_until_sec: float = REVEAL_START_SEC,
    boosted_code: Optional[str] = None,
) -> RaceSimulation:
    """
    Runs the full deterministic Pymunk race for `variant` and records per-frame state.
    When `boosted_code` is None (all of Season 1), physics is 100% identical for every marble.
    When `boosted_code` is set (Season 2+ Crowd Boost), that marble receives a balanced ~5%
    momentum/restitution surge without altering its radius (so ring gaps remain fair).
    """
    rng = random.Random(_seed_to_int(f"{seed}|{variant}"))
    space = pymunk.Space()
    space.damping = 0.996
    arena = build_arena(variant, space, rng, len(race_codes), marble_radius, intensity)
    space.gravity = arena.gravity

    bodies: Dict[str, pymunk.Body] = {}
    for idx, code in enumerate(race_codes):
        if arena.initial_velocities:
            vx, vy = arena.initial_velocities[idx]
        else:
            vx0, vx1, vy0, vy1 = arena.spawn_velocity
            vx, vy = rng.uniform(vx0, vx1), rng.uniform(vy0, vy1)

        is_boosted = boosted_code is not None and code == boosted_code
        mass = 1.2 * CROWD_BOOST_MASS_MULT if is_boosted else 1.2
        if is_boosted:
            if math.hypot(vx, vy) < 10.0:
                vy += 28.0
            else:
                vx *= CROWD_BOOST_SPEED_MULT
                vy *= CROWD_BOOST_SPEED_MULT

        body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, marble_radius))
        body.position = arena.spawn_points[idx]
        body.velocity = (vx, vy)
        if arena.force_field is not None:
            body.velocity_func = arena.force_field
        shape = pymunk.Circle(body, marble_radius)
        shape.elasticity = (
            min(0.985, arena.marble_elasticity * CROWD_BOOST_ELASTICITY_MULT)
            if is_boosted
            else arena.marble_elasticity
        )
        shape.friction = (
            arena.marble_friction * CROWD_BOOST_FRICTION_MULT
            if is_boosted
            else arena.marble_friction
        )
        shape.collision_type = 1
        space.add(body, shape)
        bodies[code] = body

    sfx_events: List[SfxEvent] = []
    clock = {"t": 0.0}
    attach_impulse_sfx(space, sfx_events, lambda: clock["t"], cutoff_sec=sim_until_sec)

    finish_order: List[str] = []
    finish_times: Dict[str, float] = {}
    frames: List[Dict[str, Any]] = []
    dt = 1.0 / (VIDEO_FPS * PHYSICS_SUBSTEPS)
    n_frames = int(sim_until_sec * VIDEO_FPS)

    for f_idx in range(n_frames):
        t = f_idx / float(VIDEO_FPS)
        clock["t"] = t
        for sub in range(PHYSICS_SUBSTEPS):
            arena.kit.drive(t + sub * dt)
            space.step(dt)
        for code, body in bodies.items():
            if code in finish_times:
                continue
            if _is_finished(arena, body.position.x, body.position.y, marble_radius):
                finish_times[code] = t
                finish_order.append(code)
                if arena.force_field is not None:
                    body.velocity_func = pymunk.Body.update_velocity  # leave the force field
                sfx_events.append(SfxEvent(timestamp_sec=t, kind="tick", gain=0.80, pitch_shift=1.15))
        frames.append(
            {
                "kin": arena.kit.snapshot(),
                "marbles": {
                    c: (float(b.position.x), float(b.position.y), float(b.angle)) for c, b in bodies.items()
                },
                "finished": len(finish_order),
            }
        )

    final_progress = {c: _progress(arena, b.position.x, b.position.y) for c, b in bodies.items()}
    return RaceSimulation(arena, intensity, frames, finish_order, finish_times, final_progress, sfx_events)


def race_quality_penalty(sim: RaceSimulation, n_racers: int, pacing: RacePacing = REGULAR_PACING) -> float:
    """0 = ideal race pacing. Penalises instant finishes, no finishers, and 'everyone gone early'."""
    times = sorted(sim.finish_times.values())
    t1 = times[0] if times else 99.0
    pen = 0.0
    if t1 < pacing.t1_lo:
        pen += (pacing.t1_lo - t1) * 3.0
    if t1 > pacing.t1_hi:
        pen += (t1 - pacing.t1_hi) * 2.0
    want = min(5, n_racers)
    if len(times) < want:
        pen += (want - len(times)) * 2.5
    early = sum(1 for t in times if t < pacing.early_cut)
    if n_racers >= 8 and early >= 0.75 * n_racers:
        pen += 4.0  # whole pack gone long before the countdown = dead air
    return pen


INTENSITY_LADDER = [1.0, 0.7, 1.4, 0.5, 2.0, 0.35, 2.8, 0.25, 4.0, 0.85, 1.2, 0.6, 1.7]


def run_tuned_race(
    seed: str,
    variant: str,
    race_codes: List[str],
    marble_radius: float,
    pacing: RacePacing = REGULAR_PACING,
    boosted_code: Optional[str] = None,
) -> RaceSimulation:
    """
    Physics is chaotic, so instead of a directional search we scan a fixed intensity ladder
    (each physics-only sim takes ~50ms) and keep the best-paced race. Deterministic per seed.
    """
    best: Optional[Tuple[float, RaceSimulation]] = None
    for intensity in INTENSITY_LADDER:
        sim = simulate_race(
            seed,
            variant,
            race_codes,
            marble_radius,
            intensity,
            sim_until_sec=pacing.sim_until,
            boosted_code=boosted_code,
        )
        pen = race_quality_penalty(sim, len(race_codes), pacing)
        if best is None or pen < best[0]:
            best = (pen, sim)
        if pen <= 0.0:
            break
    assert best is not None
    logger.info("Marble race tuned: variant=%s intensity=%.2f penalty=%.2f finishers=%d",
                variant, best[1].intensity, best[0], len(best[1].finish_order))
    return best[1]


# --------------------------------------------------------------------------- reveal cards
def _card_frame(frame: Image.Image, theme: Optional[SeasonTheme] = None) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(frame)
    if theme is None or theme.is_classic:
        draw.rounded_rectangle([54, 360, VIDEO_WIDTH - 54, 1615], radius=36, fill=(18, 20, 36),
                               outline=ACCENT_YELLOW, width=5)
    else:
        draw_beveled_panel(
            draw,
            (54, 360, VIDEO_WIDTH - 54, 1615),
            radius=36,
            fill=theme.panel_bg,
            outer_border=theme.primary_accent,
            outer_width=5,
            inner_border=theme.panel_border,
            top_accent=theme.primary_accent,
        )
    return draw


def _draw_race_results_card(
    frame: Image.Image,
    finish_order: List[str],
    finish_times: Dict[str, float],
    race_num: int,
    arena_label: str,
    finish_word: str,
    points_multiplier: int = 1,
    theme: Optional[SeasonTheme] = None,
) -> Image.Image:
    """
    Reveal page 1: winner banner + this race's result table (pos, flag, country, time, points)
    + India's finishing position callout.
    """
    use_theme = theme is not None and not theme.is_classic
    draw = _card_frame(frame, theme=theme)
    cx = VIDEO_WIDTH // 2
    winner_code = finish_order[0]
    winner_meta = COUNTRIES_META.get(winner_code, COUNTRIES_META["IN"])

    if not use_theme:
        draw.rounded_rectangle([100, 386, VIDEO_WIDTH - 100, 470], radius=22, fill=ACCENT_GREEN)
        draw_centered_text(draw, f"WINNER: {winner_meta['name'].upper()} (+{10 * points_multiplier} PTS)", cx, 402,
                           get_font(58, title=True), fill=BG_COLOR)
        win_flag = load_circular_flag_icon(winner_code, diameter=120, border_color=ACCENT_YELLOW, border_width=5)
        frame.paste(win_flag, (cx - 60, 484), win_flag)

        draw_centered_text(draw, f"RACE {race_num} RESULTS  •  {arena_label.upper()}", cx, 616,
                           get_font(44, title=True), fill=ACCENT_YELLOW)
        hdr_font = get_font(24, bold=True)
        draw.text((112, 672), "POS   COUNTRY", font=hdr_font, fill=TEXT_MUTED)
        draw.text((640, 672), "TIME", font=hdr_font, fill=TEXT_MUTED)
        draw.text((838, 672), "PTS", font=hdr_font, fill=TEXT_MUTED)

        row_font = get_font(32, bold=True)
        row_y = 708
        rows = finish_order[:8]
        for pos, code in enumerate(rows, start=1):
            meta = COUNTRIES_META[code]
            is_win = pos == 1
            is_in = code == "IN"
            fill = (38, 52, 88) if is_win else ((48, 34, 24) if is_in else (26, 30, 52))
            border = ACCENT_GREEN if is_win else ((255, 153, 51) if is_in else PANEL_BORDER_COLOR)
            draw.rounded_rectangle([92, row_y, VIDEO_WIDTH - 92, row_y + 70], radius=16, fill=fill, outline=border, width=3)
            draw.text((112, row_y + 17), f"#{pos}", font=row_font, fill=ACCENT_YELLOW if pos <= 3 else TEXT_WHITE)
            icon = load_circular_flag_icon(code, diameter=50, border_color=TEXT_WHITE, border_width=2)
            frame.paste(icon, (182, row_y + 10), icon)
            draw.text((246, row_y + 17), str(meta["name"]), font=row_font, fill=TEXT_WHITE)
            t_txt = f"{finish_times[code]:.1f}s" if code in finish_times else "DNF"
            draw.text((640, row_y + 17), t_txt, font=row_font, fill=TEXT_WHITE if code in finish_times else TEXT_MUTED)
            pts = (POINTS_TABLE[pos - 1] * points_multiplier) if pos <= len(POINTS_TABLE) else 0
            draw.text((838, row_y + 17), f"+{pts}" if pts else "0", font=row_font,
                      fill=ACCENT_GREEN if pts else TEXT_MUTED)
            row_y += 80

        if "IN" in finish_order and finish_order.index("IN") >= len(rows):
            pos_in = finish_order.index("IN") + 1
            draw.rounded_rectangle([92, row_y + 6, VIDEO_WIDTH - 92, row_y + 70], radius=16, fill=(48, 34, 24),
                                   outline=(255, 153, 51), width=3)
            draw_centered_text(draw, f"INDIA FINISHED #{pos_in} OF {len(finish_order)} THIS RACE", cx, row_y + 22,
                               get_font(30, bold=True), fill=TEXT_WHITE)
        else:
            n_done = len(finish_times)
            draw_centered_text(draw, f"{n_done}/{len(finish_order)} {finish_word.lower()} before the buzzer", cx,
                               row_y + 22, get_font(28, bold=True), fill=TEXT_MUTED)
        return frame

    # ------------------------------------------------------------------ Season 2+ Broadcast Results Card
    assert theme is not None
    win_fill = theme.win_accent
    win_fg = contrasting_text_color(win_fill)
    win_txt = f"WINNER: {winner_meta['name'].upper()} (+{10 * points_multiplier} PTS)"
    font_win = _fit_font_for_width(draw, win_txt, VIDEO_WIDTH - 240, (56, 50, 44), title=True)
    draw.rounded_rectangle(
        [92, 386, VIDEO_WIDTH - 92, 470],
        radius=22,
        fill=win_fill,
        outline=blend_rgb(win_fill, TEXT_WHITE, 0.4),
        width=2,
    )
    draw_centered_text(draw, win_txt, cx, 402, font_win, fill=win_fg)

    # Halo ring around winner flag
    draw.ellipse([cx - 66, 480, cx + 66, 612], outline=blend_rgb(theme.panel_bg, theme.primary_accent, 0.55), width=4)
    win_flag = load_circular_flag_icon(winner_code, diameter=118, border_color=theme.primary_accent, border_width=5)
    frame.paste(win_flag, (cx - 59, 487), win_flag)

    sec_txt = f"RACE {race_num} RESULTS  •  {arena_label.upper()}"
    font_sec = _fit_font_for_width(draw, sec_txt, VIDEO_WIDTH - 190, (44, 38, 34), title=True)
    draw_centered_text(draw, sec_txt, cx, 618, font_sec, fill=theme.primary_accent)

    hdr_font = get_font(23, bold=True)
    draw.text((112, 672), "POS", font=hdr_font, fill=theme.text_muted)
    draw.text((206, 672), "COUNTRY", font=hdr_font, fill=theme.text_muted)
    draw.text((662, 672), "TIME", font=hdr_font, fill=theme.text_muted)
    draw.text((864, 672), "PTS", font=hdr_font, fill=theme.text_muted)

    medal_fills = [(255, 206, 56), (206, 214, 226), (212, 134, 62)]
    row_font = get_font(31, bold=True)
    row_y = 706
    rows = finish_order[:8]
    for pos, code in enumerate(rows, start=1):
        meta = COUNTRIES_META[code]
        is_win = pos == 1
        is_in = code == "IN"
        fill = theme.panel_win_row if is_win else ((48, 34, 24) if is_in else theme.panel_bg_alt)
        border = theme.win_accent if is_win else ((255, 153, 51) if is_in else theme.panel_border)
        draw.rounded_rectangle([92, row_y, VIDEO_WIDTH - 92, row_y + 70], radius=16, fill=fill, outline=border, width=3)

        if pos <= 3:
            m_col = medal_fills[pos - 1]
            m_fg = contrasting_text_color(m_col)
            draw.rounded_rectangle([106, row_y + 14, 174, row_y + 56], radius=12, fill=m_col)
            draw_centered_text(draw, f"#{pos}", 140, row_y + 18, get_font(28, bold=True), fill=m_fg)
        else:
            draw_centered_text(draw, f"#{pos}", 140, row_y + 18, row_font, fill=TEXT_WHITE)

        icon = load_circular_flag_icon(code, diameter=50, border_color=TEXT_WHITE, border_width=2)
        frame.paste(icon, (192, row_y + 10), icon)
        draw.text((258, row_y + 17), str(meta["name"]), font=row_font, fill=TEXT_WHITE)

        t_txt = f"{finish_times[code]:.1f}s" if code in finish_times else "DNF"
        draw.text((662, row_y + 17), t_txt, font=row_font, fill=TEXT_WHITE if code in finish_times else theme.text_muted)

        pts = (POINTS_TABLE[pos - 1] * points_multiplier) if pos <= len(POINTS_TABLE) else 0
        pts_txt = f"+{pts}" if pts else "0"
        pb = draw.textbbox((0, 0), pts_txt, font=row_font)
        draw.text(
            (VIDEO_WIDTH - 122 - (pb[2] - pb[0]), row_y + 17),
            pts_txt,
            font=row_font,
            fill=theme.win_accent if pts else theme.text_muted,
        )
        row_y += 80

    footer_y = 1362
    if "IN" in finish_order and finish_order.index("IN") >= len(rows):
        pos_in = finish_order.index("IN") + 1
        draw.rounded_rectangle([92, footer_y, VIDEO_WIDTH - 92, footer_y + 74], radius=18, fill=(48, 34, 24),
                               outline=(255, 153, 51), width=3)
        draw_centered_text(draw, f"INDIA FINISHED #{pos_in} OF {len(finish_order)} THIS RACE", cx, footer_y + 20,
                           get_font(30, bold=True), fill=TEXT_WHITE)
    else:
        n_done = len(finish_times)
        draw.rounded_rectangle(
            [92, footer_y, VIDEO_WIDTH - 92, footer_y + 74],
            radius=18,
            fill=theme.panel_bg_alt,
            outline=theme.panel_border,
            width=2,
        )
        draw_centered_text(
            draw,
            f"{n_done}/{len(finish_order)} {finish_word.upper()}  •  {theme.edition_name} EDITION",
            cx,
            footer_y + 22,
            get_font(27, bold=True),
            fill=theme.primary_accent,
        )

    # Bottom race progress strip inside card
    prog_y = 1468
    draw.rounded_rectangle([92, prog_y, VIDEO_WIDTH - 92, prog_y + 114], radius=20,
                           fill=theme.bg_outer, outline=theme.panel_border, width=2)
    draw_centered_text(draw, f"SEASON {theme.season} CHAMPIONSHIP PROGRESS  •  RACE {race_num} / 28", cx, prog_y + 18,
                       get_font(25, bold=True), fill=theme.text_muted)
    bar_x0, bar_x1 = 128, VIDEO_WIDTH - 128
    draw.rounded_rectangle([bar_x0, prog_y + 64, bar_x1, prog_y + 88], radius=12, fill=theme.panel_bg_alt)
    fill_w = max(24, int((bar_x1 - bar_x0) * min(1.0, race_num / 28.0)))
    draw.rounded_rectangle([bar_x0, prog_y + 64, bar_x0 + fill_w, prog_y + 88], radius=12, fill=theme.primary_accent)
    return frame


def _draw_standings_card(
    frame: Image.Image,
    winner_code: str,
    standings: List[Dict[str, Any]],
    season: int,
    race_num: int,
    stage_name: str,
    cliffhanger_text: str,
    eliminated_code: Optional[str],
    theme: Optional[SeasonTheme] = None,
    elim_note: str = "",
) -> Image.Image:
    """
    Reveal page 2: season standings so far (top 8 incl. this race) + India spotlight + cliffhanger.
    """
    use_theme = theme is not None and not theme.is_classic
    draw = _card_frame(frame, theme=theme)
    cx = VIDEO_WIDTH // 2

    india_rank, india_pts = 0, 0
    for idx, entry in enumerate(standings):
        if entry["code"] == "IN":
            india_rank, india_pts = idx + 1, int(entry["points"])
            break

    if not use_theme:
        draw_centered_text(draw, f"SEASON {season} STANDINGS", cx, 390, get_font(58, title=True), fill=ACCENT_YELLOW)
        draw_centered_text(draw, f"AFTER RACE {race_num}  •  {stage_name.upper()}", cx, 456,
                           get_font(30, bold=True), fill=TEXT_MUTED)

        row_font = get_font(32, bold=True)
        row_y = 512
        for rank, entry in enumerate(standings[:8], start=1):
            code = entry["code"]
            is_win = code == winner_code
            is_in = code == "IN"
            fill = (38, 52, 88) if is_win else ((48, 34, 24) if is_in else (26, 30, 52))
            border = ACCENT_GREEN if is_win else ((255, 153, 51) if is_in else PANEL_BORDER_COLOR)
            draw.rounded_rectangle([92, row_y, VIDEO_WIDTH - 92, row_y + 70], radius=16, fill=fill, outline=border, width=3)
            draw.text((112, row_y + 17), f"#{rank}", font=row_font, fill=ACCENT_YELLOW if rank == 1 else TEXT_WHITE)
            icon = load_circular_flag_icon(code, diameter=50, border_color=TEXT_WHITE, border_width=2)
            frame.paste(icon, (182, row_y + 10), icon)
            draw.text((246, row_y + 17), str(entry["name"]), font=row_font, fill=TEXT_WHITE)
            pts_text = f"{entry['points']} PTS  ({entry['wins']}W)"
            pb = draw.textbbox((0, 0), pts_text, font=row_font)
            draw.text((VIDEO_WIDTH - 116 - (pb[2] - pb[0]), row_y + 17), pts_text, font=row_font,
                      fill=ACCENT_YELLOW if rank == 1 else TEXT_WHITE)
            row_y += 80

        draw.rounded_rectangle([92, row_y + 6, VIDEO_WIDTH - 92, row_y + 80], radius=18, fill=(48, 34, 24),
                               outline=(255, 153, 51), width=3)
        in_icon = load_circular_flag_icon("IN", diameter=50, border_color=(255, 153, 51), border_width=2)
        frame.paste(in_icon, (116, row_y + 18), in_icon)
        draw_centered_text(draw, f"INDIA SPOTLIGHT: #{india_rank} OVERALL ({india_pts} PTS)", cx + 20, row_y + 26,
                           get_font(32, bold=True), fill=TEXT_WHITE)
        row_y += 96

        if eliminated_code:
            draw_centered_text(draw, f"ELIMINATED: {COUNTRIES_META[eliminated_code]['name'].upper()}{elim_note}", cx, row_y,
                               get_font(30, bold=True), fill=ACCENT_RED)
            row_y += 44

        cliff_y = min(row_y, 1490)
        draw.rounded_rectangle([92, cliff_y, VIDEO_WIDTH - 92, cliff_y + 105], radius=20, fill=(45, 18, 32),
                               outline=ACCENT_RED, width=3)
        draw_centered_text(draw, "NEXT BATTLE CLIFFHANGER:", cx, cliff_y + 14, get_font(26, bold=True), fill=ACCENT_YELLOW)
        draw_centered_text(draw, cliffhanger_text, cx, cliff_y + 50, get_font(32, bold=True), fill=TEXT_WHITE)
        return frame

    # ------------------------------------------------------------------ Season 2+ Broadcast Standings Card
    assert theme is not None
    draw_centered_text(draw, f"SEASON {season} STANDINGS", cx, 384, get_font(58, title=True), fill=theme.primary_accent)
    sub_hdr = f"{theme.edition_name}  •  AFTER RACE {race_num}  •  {stage_name.upper()}"
    font_sh = _fit_font_for_width(draw, sub_hdr, VIDEO_WIDTH - 190, (27, 24, 21), bold=True)
    draw_centered_text(draw, sub_hdr, cx, 448, font_sh, fill=theme.text_muted)

    row_font = get_font(30, bold=True)
    row_y = 492
    for rank, entry in enumerate(standings[:8], start=1):
        code = entry["code"]
        is_win = code == winner_code
        is_in = code == "IN"
        fill = theme.panel_win_row if is_win else ((48, 34, 24) if is_in else theme.panel_bg_alt)
        border = theme.win_accent if is_win else ((255, 153, 51) if is_in else theme.panel_border)
        draw.rounded_rectangle([92, row_y, VIDEO_WIDTH - 92, row_y + 64], radius=15, fill=fill, outline=border, width=3)

        if rank == 1:
            ldr_fg = contrasting_text_color(theme.primary_accent)
            draw.rounded_rectangle([106, row_y + 12, 174, row_y + 52], radius=11, fill=theme.primary_accent)
            draw_centered_text(draw, "#1", 140, row_y + 15, get_font(27, bold=True), fill=ldr_fg)
        else:
            draw_centered_text(draw, f"#{rank}", 140, row_y + 15, row_font, fill=TEXT_WHITE)

        icon = load_circular_flag_icon(code, diameter=46, border_color=TEXT_WHITE, border_width=2)
        frame.paste(icon, (190, row_y + 9), icon)
        draw.text((252, row_y + 15), str(entry["name"]), font=row_font, fill=TEXT_WHITE)

        pts_text = f"{entry['points']} PTS  ({entry['wins']}W)"
        pb = draw.textbbox((0, 0), pts_text, font=row_font)
        draw.text(
            (VIDEO_WIDTH - 116 - (pb[2] - pb[0]), row_y + 15),
            pts_text,
            font=row_font,
            fill=theme.primary_accent if rank == 1 else TEXT_WHITE,
        )
        row_y += 73

    # India spotlight row (y = 1076..1148)
    draw.rounded_rectangle([92, row_y + 4, VIDEO_WIDTH - 92, row_y + 74], radius=18, fill=(48, 34, 24),
                           outline=(255, 153, 51), width=3)
    in_icon = load_circular_flag_icon("IN", diameter=48, border_color=(255, 153, 51), border_width=2)
    frame.paste(in_icon, (114, row_y + 15), in_icon)
    draw_centered_text(draw, f"INDIA SPOTLIGHT: #{india_rank} OVERALL ({india_pts} PTS)", cx + 22, row_y + 22,
                       get_font(30, bold=True), fill=TEXT_WHITE)
    row_y += 88

    # Dual Cap Stat Tiles: GOLD CAP (Most Wins) & CLUTCH CAP (Most Podiums)
    gold_leader = max(standings, key=lambda c: (int(c.get("wins", 0)), int(c.get("points", 0))))
    clutch_leader = max(standings, key=lambda c: (int(c.get("podiums", 0)), int(c.get("points", 0))))
    for tile_x0, tile_x1, cap_title, leader, stat_txt, cap_col in (
        (92, 526, "GOLD CAP  •  MOST WINS", gold_leader, f"{int(gold_leader.get('wins', 0))}W", (255, 202, 52)),
        (554, VIDEO_WIDTH - 92, "CLUTCH CAP  •  PODIUMS", clutch_leader, f"{int(clutch_leader.get('podiums', 0))} POD", theme.secondary_accent),
    ):
        draw.rounded_rectangle([tile_x0, row_y, tile_x1, row_y + 96], radius=16,
                               fill=theme.bg_outer, outline=cap_col, width=2)
        tcx = (tile_x0 + tile_x1) // 2
        draw_centered_text(draw, cap_title, tcx, row_y + 12, get_font(21, bold=True), fill=cap_col)
        val_str = f"{leader['name']}  •  {stat_txt}"
        val_font = get_font(26, bold=True)
        vb = draw.textbbox((0, 0), val_str, font=val_font)
        vw, vh = vb[2] - vb[0], vb[3] - vb[1]
        diam, gap = 38, 12
        sx = tcx - (diam + gap + vw) // 2
        c_icon = load_circular_flag_icon(leader["code"], diameter=diam, border_color=cap_col, border_width=2)
        frame.paste(c_icon, (sx, row_y + 44), c_icon)
        draw.text((sx + diam + gap, row_y + 63 - vh // 2 - vb[1]), val_str, font=val_font, fill=TEXT_WHITE)
    row_y += 110

    if eliminated_code:
        draw.rounded_rectangle([92, row_y, VIDEO_WIDTH - 92, row_y + 56], radius=15,
                               fill=(62, 16, 24), outline=ACCENT_RED, width=3)
        el_str = f"KNOCKED OUT: {COUNTRIES_META[eliminated_code]['name'].upper()}{elim_note}"
        ef = get_font(28, bold=True)
        eb = draw.textbbox((0, 0), el_str, font=ef)
        ew, eh = eb[2] - eb[0], eb[3] - eb[1]
        el_icon = load_circular_flag_icon(eliminated_code, diameter=36, border_color=ACCENT_RED, border_width=2)
        sx = cx - (36 + 12 + ew) // 2
        frame.paste(el_icon, (sx, row_y + 10), el_icon)
        draw.text((sx + 48, row_y + 28 - eh // 2 - eb[1]), el_str, font=ef, fill=TEXT_WHITE)
        row_y += 68

    # Season Progress Strip (fills card rhythm cleanly above Cliffhanger)
    prog_h = 68 if eliminated_code else 92
    draw.rounded_rectangle([92, row_y, VIDEO_WIDTH - 92, row_y + prog_h], radius=16,
                           fill=theme.panel_bg_alt, outline=theme.panel_border, width=2)
    draw_centered_text(draw, f"SEASON {season} ROAD TO FINALE  •  RACE {race_num} OF 28", cx, row_y + 11,
                       get_font(22, bold=True), fill=theme.text_muted)
    bx0, bx1 = 126, VIDEO_WIDTH - 126
    by0 = row_y + (42 if eliminated_code else 52)
    by1 = by0 + (16 if eliminated_code else 22)
    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=9, fill=theme.bg_outer)
    bw = max(22, int((bx1 - bx0) * min(1.0, race_num / 28.0)))
    draw.rounded_rectangle([bx0, by0, bx0 + bw, by1], radius=9, fill=theme.primary_accent)
    row_y += prog_h + 14

    cliff_y = min(max(row_y, 1468), 1482)
    draw_beveled_panel(
        draw,
        (92, cliff_y, VIDEO_WIDTH - 92, 1592),
        radius=20,
        fill=theme.bg_outer,
        outer_border=theme.secondary_accent,
        outer_width=3,
        inner_border=theme.panel_border,
    )
    draw_centered_text(draw, "NEXT BATTLE CLIFFHANGER:", cx, cliff_y + 16, get_font(24, bold=True), fill=theme.primary_accent)
    font_cliff = _fit_font_for_width(draw, cliffhanger_text, VIDEO_WIDTH - 224, (31, 27, 24), bold=True)
    draw_centered_text(draw, cliffhanger_text, cx, cliff_y + 54, font_cliff, fill=TEXT_WHITE)
    return frame


def _draw_champion_card(
    frame: Image.Image,
    standings: List[Dict[str, Any]],
    season: int,
    t_in: float,
    theme: Optional[SeasonTheme] = None,
) -> Image.Image:
    """
    Grand Final page 2: SEASON CHAMPION ceremony — trophy, champion flag, podium (top 3 by final points),
    final top-8 table and a 'Season N+1 starts tomorrow' teaser. `t_in` animates the confetti.
    """
    draw = ImageDraw.Draw(frame)
    cx = VIDEO_WIDTH // 2
    use_theme = theme is not None and not theme.is_classic
    gold = (255, 210, 60)
    card_bg = theme.panel_bg if use_theme and theme else (22, 18, 8)
    row_bg = theme.panel_bg_alt if use_theme and theme else (40, 34, 20)
    conf_palette = list(theme.marble_palette) if use_theme and theme else list(ACCENT_PALETTE)

    if use_theme and theme:
        draw_beveled_panel(
            draw,
            (54, 360, VIDEO_WIDTH - 54, 1615),
            radius=36,
            fill=card_bg,
            outer_border=gold,
            outer_width=6,
            inner_border=theme.primary_accent,
            top_accent=gold,
        )
    else:
        draw.rounded_rectangle([54, 360, VIDEO_WIDTH - 54, 1615], radius=36, fill=card_bg, outline=gold, width=6)

    # confetti (deterministic)
    crng = random.Random(season * 7919)
    for i in range(90):
        x0 = crng.uniform(70, VIDEO_WIDTH - 70)
        y0 = 370 + ((crng.uniform(0, 1200) + t_in * crng.uniform(160, 320)) % 1230)
        col = conf_palette[i % len(conf_palette)] if i % 3 else (255, 220, 90)
        draw.rectangle([x0, y0, x0 + 9, y0 + 16], fill=col)
    champ = standings[0]
    draw_centered_text(draw, f"SEASON {season} CHAMPION", cx, 384, get_font(70, title=True), fill=gold)
    # trophy
    tx, ty = cx, 560
    draw.polygon([(tx - 80, ty - 90), (tx + 80, ty - 90), (tx + 55, ty), (tx - 55, ty)], fill=(255, 196, 40), outline=TEXT_WHITE)
    draw.arc([tx - 125, ty - 95, tx - 45, ty - 15], 90, 270, fill=(255, 196, 40), width=10)
    draw.arc([tx + 45, ty - 95, tx + 125, ty - 15], 270, 90, fill=(255, 196, 40), width=10)
    draw.rectangle([tx - 14, ty, tx + 14, ty + 40], fill=(255, 196, 40))
    draw.rounded_rectangle([tx - 60, ty + 40, tx + 60, ty + 64], radius=6, fill=(160, 110, 20))
    flag = load_circular_flag_icon(champ["code"], diameter=96, border_color=TEXT_WHITE, border_width=4)
    frame.paste(flag, (tx - 48, ty - 96), flag)
    draw_centered_text(draw, f"{champ['name'].upper()}  •  {champ['points']} PTS  •  {champ['wins']} WINS", cx, 650,
                       get_font(40, title=True), fill=TEXT_WHITE)
    # podium 2-1-3
    podium = standings[:3]
    blocks = [(1, cx - 280, 150), (0, cx - 90, 210), (2, cx + 100, 110)]
    base_y = 1000
    for rank_idx, x0, h in blocks:
        if rank_idx >= len(podium):
            continue
        entry = podium[rank_idx]
        col = [gold, (200, 205, 215), (205, 127, 50)][rank_idx]
        draw.rectangle([x0, base_y - h, x0 + 180, base_y], fill=col, outline=TEXT_WHITE, width=3)
        draw_centered_text(draw, f"#{rank_idx + 1}", x0 + 90, base_y - h + 12, get_font(52, title=True), fill=BG_COLOR)
        icon = load_circular_flag_icon(entry["code"], diameter=80, border_color=TEXT_WHITE, border_width=3)
        frame.paste(icon, (x0 + 50, base_y - h - 92), icon)
        draw_centered_text(draw, f"{entry['name']} ({entry['points']})", x0 + 90, base_y + 10, get_font(26, bold=True), fill=TEXT_WHITE)
    # rest of top 8
    row_y = 1070
    font_row = get_font(28, bold=True)
    for rank, entry in enumerate(standings[3:8], start=4):
        draw.rounded_rectangle([110, row_y, VIDEO_WIDTH - 110, row_y + 52], radius=12, fill=row_bg)
        draw.text((132, row_y + 10), f"#{rank}  {entry['name']}", font=font_row, fill=TEXT_WHITE)
        pts = f"{entry['points']} PTS"
        pb = draw.textbbox((0, 0), pts, font=font_row)
        draw.text((VIDEO_WIDTH - 132 - (pb[2] - pb[0]), row_y + 10), pts, font=font_row, fill=gold)
        row_y += 60

    next_theme = get_season_theme(season + 1)
    if use_theme and theme:
        draw_beveled_panel(
            draw,
            (104, 1410, VIDEO_WIDTH - 104, 1558),
            radius=20,
            fill=theme.bg_outer,
            outer_border=gold,
            outer_width=3,
            inner_border=theme.panel_border,
        )
        draw_centered_text(draw, f"NEXT UP  •  SEASON {season + 1} EDITION", cx, 1430,
                           get_font(24, bold=True), fill=gold)
        draw_centered_text(draw, f"{next_theme.edition_name} ARENA STARTS TOMORROW!", cx, 1476,
                           get_font(36, title=True), fill=TEXT_WHITE)
    else:
        draw_centered_text(draw, f"SEASON {season + 1} STARTS TOMORROW — ALL 16 COUNTRIES BACK AT 0", cx, row_y + 16,
                           get_font(28, bold=True), fill=ACCENT_YELLOW)
    return frame


# Backwards-compatible single-card helper (kept for callers/tests that import it).
def _draw_leaderboard_and_cliffhanger_card(
    frame: Image.Image,
    winner_code: str,
    podium_codes: List[str],
    standings: List[Dict[str, Any]],
    season: int,
    race_num: int,
    stage_name: str,
    cliffhanger_text: str,
    seed: str,
) -> Image.Image:
    return _draw_standings_card(frame, winner_code, standings, season, race_num, stage_name, cliffhanger_text, None)


# --------------------------------------------------------------------------- arena drawing
def _draw_arena_background(draw: ImageDraw.ImageDraw, arena: ArenaConfig) -> None:
    if arena.bg_style == "rings":
        cx, cy = arena.center
        for bg_r in (160, 290, 420, 520):
            draw.ellipse([cx - bg_r, cy - bg_r, cx + bg_r, cy + bg_r], outline=(24, 27, 46), width=2)
    else:
        for x in range(100, VIDEO_WIDTH - 60, 90):
            draw.line([(x, 380), (x, 1340)], fill=(22, 25, 42), width=2)
        draw_checkered_line(draw, 72, VIDEO_WIDTH - 72, int(arena.finish_y) + 8, cell=18)
        draw.text((80, int(arena.finish_y) + 30), "FINISH", font=get_font(24, bold=True), fill=TEXT_MUTED)


def resolve_crowd_boost(
    season: int,
    race_codes: List[str],
    wildcard: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns `(boosted_country_code, hud_banner_text)` for Season 2+ (`season >= CROWD_BOOST_MIN_SEASON`)
    when real viewer comments voted for a country in `race_codes`.
    Strictly returns `(None, None)` throughout Season 1 so active season physics & records never change.
    """
    if season < CROWD_BOOST_MIN_SEASON or not wildcard:
        return None, None
    cand_code = str(wildcard.get("crowd_code") or wildcard.get("country_code") or "").upper()
    cand_user = str(wildcard.get("crowd_user") or wildcard.get("username") or "")
    if not cand_code or cand_code not in race_codes or not cand_user or is_own_channel_author(cand_user):
        return None, None
    safe_user = sanitize_username(cand_user)
    votes = int(wildcard.get("crowd_votes") or wildcard.get("wildcard_votes") or 1)
    meta = COUNTRIES_META.get(cand_code, COUNTRIES_META["IN"])
    vote_detail = f"{votes} comments ({safe_user})" if votes > 1 else f"Backed by {safe_user}"
    return cand_code, f"CROWD BOOST (+5%): {meta['name']}  •  {vote_detail}"


def generate_marble_league_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    wildcard: Optional[Dict[str, Any]] = None,
    weekly_recap: bool = False,
    update_state: bool = True,
    variant_override: Optional[str] = None,
    force_grand_final: bool = False,
    season_override: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Runs a complete Country Marble League physics simulation (or Sunday Weekly Standings + Mega Quiz),
    renders the 1080x1920 30fps MP4 with synced collision SFX + Piper TTS, updates `state/league.json`
    (only when `update_state=True`), and returns the generation metadata dictionary.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    state = load_league_state()
    rng = random.Random(_seed_to_int(seed))

    season = int(state.get("season", 1))
    race_num = int(state.get("race_number", 0)) + 1
    max_races = int(state.get("max_races_per_season", 28))
    if season_override is not None:
        if update_state:
            raise ValueError("season_override is a preview mode and requires update_state=False")
        season = int(season_override)
    if force_grand_final:
        # Preview/testing only: simulate the season finale in memory. Never persisted.
        if update_state:
            raise ValueError("force_grand_final is a preview mode and requires update_state=False")
        race_num = max_races

    # Snapshot pre-race standings BEFORE awarding this race's points (prevents title P1/P2 spoilers)
    pre_race_standings = compute_standings(state)

    # Determine active countries + stage (v2 rules: config-driven phases, carry-over points elimination)
    rules_cfg = league_rules.load_config(state)
    all_countries = state.get("countries", {})
    active_codes = [c for c in LEAGUE_COUNTRY_CODES if not all_countries.get(c, {}).get("eliminated", False)]
    if len(active_codes) < 2:
        for c in LEAGUE_COUNTRY_CODES:
            all_countries[c]["eliminated"] = False
        active_codes = list(LEAGUE_COUNTRY_CODES)
    stage_name = league_rules.stage_for_race(race_num, rules_cfg, len(active_codes))
    pre_teams = league_rules.replay_standings(state.get("history", []), LEAGUE_COUNTRY_CODES, season, rules_cfg)["teams"]
    danger_pre = league_rules.danger_zone(pre_teams, rules_cfg) if stage_name == league_rules.STAGE_ELIM else None

    # Vary ball count (10 to 16 in Group Stage, or all remaining in Knockouts)
    if stage_name == "Group Stage":
        ball_count = rng.choice([10, 12, 14, 16])
        # Always include India, USA, Brazil, Indonesia + viewer Wildcard (and Crowd Boost leader in S2+)
        priority = ["IN", "US", "BR", "ID"]
        if season >= CROWD_BOOST_MIN_SEASON and wildcard and wildcard.get("crowd_code") in active_codes:
            cc = str(wildcard["crowd_code"])
            if cc not in priority:
                priority.insert(0, cc)
        if wildcard and wildcard.get("country_code") in active_codes:
            wc_code = str(wildcard["country_code"])
            if wc_code not in priority:
                priority.insert(0, wc_code)
        remaining = [c for c in active_codes if c not in priority]
        rng.shuffle(remaining)
        race_codes = (priority + remaining)[:ball_count]
    else:
        race_codes = list(active_codes)

    boosted_code, crowd_boost_banner = resolve_crowd_boost(season, race_codes, wildcard)
    focus_code, rival_code = pick_pre_race_focus_and_rival(race_codes, pre_race_standings, seed, wildcard)
    focus_meta = COUNTRIES_META[focus_code]
    rival_meta = COUNTRIES_META[rival_code]

    is_grand_final = stage_name == "Monthly Grand Final"
    # v2: points never reset and are never multiplied - the Semi-Final and Grand Final are pure knockouts
    points_multiplier = league_rules.points_multiplier_for(stage_name)

    # Select a fresh arena (never repeats within the last ARENA_NO_REPEAT_WINDOW races);
    # the Grand Final always uses the multi-stage MEGA GAUNTLET.
    if is_grand_final:
        variant = MEGA_VARIANT
    elif variant_override in ARENA_VARIANTS:
        variant = variant_override
    else:
        variant = choose_arena_variant(seed, state.get("history", []))
    marble_radius = 30 if len(race_codes) <= 12 else 27

    if is_grand_final:
        total_duration = MEGA_TOTAL_DURATION
        countdown_start_sec = MEGA_COUNTDOWN_START_SEC
        reveal_start_sec = MEGA_REVEAL_START_SEC
        standings_page_sec = MEGA_STANDINGS_PAGE_SEC
        outro_start_sec = MEGA_OUTRO_START_SEC
        pacing = MEGA_PACING
    else:
        total_duration = TOTAL_DURATION
        countdown_start_sec = COUNTDOWN_START_SEC
        reveal_start_sec = REVEAL_START_SEC
        standings_page_sec = STANDINGS_PAGE_SEC
        outro_start_sec = OUTRO_START_SEC
        pacing = REGULAR_PACING
    total_frames = int(total_duration * VIDEO_FPS)

    pygame.init()
    sim = run_tuned_race(seed, variant, race_codes, marble_radius, pacing, boosted_code=boosted_code)
    arena = sim.arena
    sfx_events = list(sim.sfx_events)

    # Final ranking: finishers in finish order, then closest-to-finish
    escaped_order = list(sim.finish_order)
    un_escaped = [c for c in race_codes if c not in escaped_order]
    un_escaped.sort(key=lambda c: sim.final_progress[c], reverse=True)
    full_finish_order = escaped_order + un_escaped
    winner_code = full_finish_order[0]
    podium_codes = full_finish_order[:5]

    # v2: score + decide the elimination from the replayed log (lowest CUMULATIVE total, not last in this race)
    rec = league_rules.record_game(state, race_num, stage_name, full_finish_order, LEAGUE_COUNTRY_CODES,
                                   seed=seed, cfg=rules_cfg)

    # Award points in league state (Top 5: 10, 7, 5, 3, 1)
    for idx, code in enumerate(podium_codes):
        pts = POINTS_TABLE[idx] * points_multiplier
        c_entry = state["countries"][code]
        c_entry["points"] = int(c_entry.get("points", 0)) + pts
        if idx == 0:
            c_entry["wins"] = int(c_entry.get("wins", 0)) + 1
        if idx < 3:
            c_entry["podiums"] = int(c_entry.get("podiums", 0)) + 1

    eliminated_all = list(rec["eliminated_all"])
    for code in eliminated_all:
        state["countries"][code]["eliminated"] = True
        state["countries"][code]["eliminated_in"] = race_num
    eliminated_this_race = eliminated_all[-1] if stage_name == league_rules.STAGE_SEMI and eliminated_all else (
        eliminated_all[0] if eliminated_all else None)
    elim_margin = rec.get("elimination_margin")
    elim_note = ""
    if stage_name == league_rules.STAGE_ELIM and eliminated_this_race:
        elim_note = " · LOWEST TOTAL" + (f" (BY {elim_margin})" if elim_margin else " (TIE-BREAK)")
    if race_num == int(rules_cfg["league_games"]):
        for c in state["countries"].values():
            c["league_points"] = int(c.get("points", 0))

    # Snapshot (deep copy) so the Grand Final season reset below can never zero the champion card's numbers
    standings = copy.deepcopy(compute_standings(state))
    winner_meta = COUNTRIES_META[winner_code]

    # Build cliffhanger for tomorrow
    danger_candidates = [s for s in standings if not s.get("eliminated") and s["code"] != winner_code]
    cliff_country = danger_candidates[min(3, len(danger_candidates) - 1)] if danger_candidates else standings[-1]
    cliffhanger_text = f"Can {cliff_country['name']} survive Race {race_num + 1}? Tomorrow."
    post_teams = league_rules.replay_standings(
        state.get("history", []) + [{"season": season, "race": race_num, "finish_order": full_finish_order,
                                     "points_by_team": rec["points_by_team"], "eliminated_all": eliminated_all,
                                     "winner": winner_code, "podium": podium_codes[:3]}],
        LEAGUE_COUNTRY_CODES, season, rules_cfg)["teams"]
    next_stage = league_rules.stage_for_race(race_num + 1, rules_cfg,
                                             sum(1 for t in post_teams.values() if t["status"] == "active"))
    if race_num + 1 > max_races:
        pass
    elif next_stage == league_rules.STAGE_FINAL:
        cliffhanger_text = "TOMORROW: THE GRAND FINAL — WINNER TAKES THE CROWN!"
    elif next_stage == league_rules.STAGE_SEMI:
        cliffhanger_text = f"TOMORROW: SEMI-FINAL — TOP {rules_cfg['semifinal_advance']} REACH THE GRAND FINAL!"
    elif next_stage == league_rules.STAGE_ELIM:
        dz = league_rules.danger_zone(post_teams, rules_cfg)
        if dz:
            cliffhanger_text = f"DANGER: {COUNTRIES_META[dz['code']]['name'].upper()}, {dz['margin']} PTS FROM SAFETY!"

    state["race_number"] = race_num
    state["stage"] = stage_name
    history_entry: Dict[str, Any] = {
        "season": season,
        "race": race_num,
        "seed": seed,
        "variant": variant,
        "winner": winner_code,
        "podium": podium_codes[:3],
        "eliminated": eliminated_this_race,
        "finish_order": full_finish_order,
        "points_multiplier": points_multiplier,
        "stage": stage_name,
        "phase": rec["phase"],
        "rules_version": rec["rules_version"],
        "points_by_team": rec["points_by_team"],
        "eliminated_all": eliminated_all,
        "tiebreak_used": rec["tiebreak_used"],
        "elimination_margin": elim_margin,
        "host_override": False,
    }
    if boosted_code:
        history_entry["crowd_boost"] = boosted_code
    # Extra fields (additive) so the end-of-season long-form recap can replay + narrate every race
    history_entry["race_codes"] = list(race_codes)
    history_entry["marble_radius"] = marble_radius
    history_entry["intensity"] = float(sim.intensity)
    history_entry["finish_times"] = {c: round(float(t), 3) for c, t in sim.finish_times.items()}
    history_entry["points_after"] = {s["code"]: int(s.get("points", 0)) for s in standings}
    if wildcard and wildcard.get("country_code") and not is_own_channel_author(str(wildcard.get("username", ""))):
        history_entry["wildcard"] = {
            "code": str(wildcard["country_code"]),
            "user": sanitize_username(str(wildcard.get("username", ""))),
        }
    state.setdefault("history", []).append(history_entry)

    # Auto-start next season after Race 28 (Monthly Grand Final)
    next_season = season
    next_race_num = race_num + 1
    if race_num >= max_races:
        champ = standings[0]
        state.setdefault("past_champions", []).append(
            {"season": season, "champion": champ["code"], "points": champ["points"]}
        )
        # Archive the final table BEFORE the reset (used by the long-form Season Recap video)
        state.setdefault("season_archive", {})[str(season)] = {
            "champion": champ["code"],
            "final_standings": [
                {k: s.get(k) for k in ("code", "name", "points", "wins", "podiums", "eliminated")}
                for s in standings
            ],
        }
        next_season = season + 1
        next_race_num = 1
        state["season"] = next_season
        state["race_number"] = 0
        state["stage"] = "Group Stage"
        for c in state["countries"].values():
            c["points"] = 0
            c["wins"] = 0
            c["podiums"] = 0
            c["eliminated"] = False

    if update_state:
        save_league_state(state)
        try:
            from generators.race_replay import save_replay_file

            save_replay_file(
                season=season,
                race=race_num,
                seed=seed,
                variant=variant,
                intensity=float(sim.intensity),
                race_codes=race_codes,
                marble_radius=marble_radius,
                boosted_code=boosted_code,
                pacing_name="mega" if is_grand_final else "regular",
                sim=sim,
                full_finish_order=full_finish_order,
            )
        except Exception as e:  # never let recap bookkeeping break the live Short
            logger.warning("Could not save race replay file: %s", e)

    # Add countdown & reveal fanfare SFX (+ page-flip tick for the standings page)
    add_countdown_and_reveal_sfx(sfx_events, countdown_start_sec, reveal_start_sec)
    sfx_events.append(SfxEvent(timestamp_sec=standings_page_sec, kind="tick", gain=0.6, pitch_shift=0.9))
    if is_grand_final:
        sfx_events.append(SfxEvent(timestamp_sec=standings_page_sec + 0.1, kind="reveal_fanfare", gain=0.9, pitch_shift=1.12))

    # Build TTS voiceover cues & burned-in captions
    hook_spoken = (
        f"Weekly Standings and Mega Race! {arena.hook_spoken}"
        if weekly_recap
        else f"Season {season}, Race {race_num}! {arena.hook_spoken}"
    )
    reveal_spoken = f"{winner_meta['name']} wins Race {race_num} and takes {10 * points_multiplier} league points!"
    if stage_name == league_rules.STAGE_ELIM and not weekly_recap:
        hook_spoken = f"Season {season}, Race {race_num}! {league_rules.SHORT_RULE}"
    elif stage_name == league_rules.STAGE_SEMI:
        hook_spoken = f"Season {season} Semi-Final! Top {rules_cfg['semifinal_advance']} reach the Grand Final!"
    if is_grand_final:
        hook_spoken = f"Season {season} Grand Final! One mega gauntlet. The winner is champion!"
        reveal_spoken = (f"{winner_meta['name']} wins the Grand Final! {standings[0]['name']} is the "
                         f"Season {season} champion!")
    voice_cues = build_standard_voice_cues(
        hook_text=hook_spoken,
        countdown_time=countdown_start_sec,
        reveal_time=reveal_start_sec,
        reveal_text=reveal_spoken,
        outro_time=outro_start_sec,
        output_dir=output_dir / "tts_cache",
    )

    # Live commentary: spoiler-safe hype lines before the countdown + table context after the reveal
    try:
        events = analyze_race(
            sim.frames,
            race_codes,
            sim.finish_times,
            full_finish_order,
            lambda x, y: _progress(arena, x, y),
            finish_threshold_for(arena.mode, arena.center, arena.escape_radius, arena.finish_y, marble_radius),
        )
        planned = plan_shorts_lines(
            seed=seed,
            events=events,
            race_codes=race_codes,
            stage=stage_name,
            points_multiplier=points_multiplier,
            is_grand_final=is_grand_final,
            boosted_code=boosted_code,
            countdown_start=countdown_start_sec,
            winner_code=winner_code,
        )
        post_line = plan_shorts_post_reveal_line(
            seed=seed,
            stage=stage_name,
            eliminated_code=eliminated_this_race,
            winner_code=winner_code,
            standings=standings,
            is_grand_final=is_grand_final,
            t=standings_page_sec - 0.5,
            window_end=outro_start_sec - 0.15,
        )
        if post_line is not None:
            planned.append(post_line)
        tts_dir = output_dir / "tts_cache"
        voice_cues = schedule_lines_into_cues(voice_cues, planned, lambda txt: synthesize_phrase(txt, tts_dir))
    except Exception as e:
        logger.warning("Live commentary skipped: %s", e)

    audio_wav_path = output_dir / f"marble_league_{seed}.wav"
    mix_master_audio(
        duration_sec=total_duration,
        output_wav_path=audio_wav_path,
        sfx_events=sfx_events,
        voice_cues=voice_cues,
        bgm_track_name="arena_pulse_cc0.wav",
        bgm_volume=0.15,
    )

    theme = get_season_theme(season)
    use_theme = not theme.is_classic

    output_mp4_path = output_dir / f"marble_league_{seed}.mp4"
    if use_theme:
        if is_grand_final:
            series_badge = f"S{season} GRAND FINAL · {theme.edition_name}"
        elif points_multiplier == 2:
            series_badge = f"S{season} · R{race_num} · {theme.edition_name} · 2X"
        elif weekly_recap:
            series_badge = f"WEEKLY RECAP · S{season} R{race_num} · {theme.edition_name}"
        elif stage_name == "Knockout Stage":
            series_badge = f"S{season} · R{race_num} · KNOCKOUT STAGE"
        else:
            series_badge = f"SEASON {season} · {theme.edition_name} · R{race_num}"
    else:
        series_badge = (
            f"WEEKLY RECAP · S{season} R{race_num}"
            if weekly_recap
            else f"SEASON {season} · RACE {race_num} ({stage_name})"
        )
        if is_grand_final:
            series_badge = f"S{season} GRAND FINAL · WINNER TAKES ALL"
        elif points_multiplier == 2:
            series_badge = f"S{season} · RACE {race_num} · 2X POINTS"

    hook_title = "GRAND FINAL: MEGA RACE!" if is_grand_final else arena.hook_title
    wildcard_tuple = (
        (str(wildcard["country_label"]), str(wildcard["username"]))
        if wildcard
        and wildcard.get("country_label")
        and wildcard.get("username")
        and not is_own_channel_author(str(wildcard.get("username", "")))
        else None
    )

    # Pre-render the static arena layer once; spinning flag icons are pre-rotated.
    if use_theme:
        base_layer = render_seasonal_arena_backdrop(theme, arena.mode, arena.center, arena.finish_y)
        arena.kit.render_static(
            base_layer,
            neon_glow=True,
            glow_bg=theme.bg_center,
            color_map=theme.obstacle_remap,
        )
        card_backdrop = render_radial_backdrop(theme)
        palette = theme.marble_palette
    else:
        base_layer = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
        _draw_arena_background(ImageDraw.Draw(base_layer), arena)
        arena.kit.render_static(base_layer)
        card_backdrop = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
        palette = tuple(ACCENT_PALETTE)

    icon_cache: Dict[str, RotatingIconCache] = {}
    for idx, code in enumerate(race_codes):
        border_col = (
            (theme.primary_accent if use_theme else ACCENT_YELLOW)
            if code == boosted_code
            else palette[idx % len(palette)]
        )
        icon = load_circular_flag_icon(code, diameter=marble_radius * 2, border_color=border_col, border_width=4)
        icon_cache[code] = RotatingIconCache(icon)
    trails: Dict[str, List[Tuple[float, float]]] = {c: [] for c in race_codes}

    hud_theme = theme if use_theme else None
    footer_txt = (
        f"Seed: {seed} • {theme.edition_name} Edition • Pymunk 2D"
        if use_theme
        else f"Race seed: {seed} • Physics: Pymunk 2D"
    )

    with FFmpegFramePipe(output_mp4_path, audio_wav_path) as pipe:
        for f_idx in range(total_frames):
            t_sec = f_idx / float(VIDEO_FPS)
            if t_sec < reveal_start_sec:
                f_state = sim.frames[min(f_idx, len(sim.frames) - 1)]
                frame = base_layer.copy()
                draw = ImageDraw.Draw(frame)
                arena.kit.render_dynamic(
                    draw,
                    f_state["kin"],
                    neon_glow=use_theme,
                    glow_bg=theme.bg_center,
                    color_map=theme.obstacle_remap if use_theme else None,
                )

                for idx, code in enumerate(race_codes):
                    mx, my, ang = f_state["marbles"][code]
                    if -marble_radius <= mx <= VIDEO_WIDTH + marble_radius and 320 <= my <= 1600:
                        trails[code].append((mx, my))
                        max_trail = 8 if use_theme else 6
                        trails[code] = trails[code][-max_trail:]
                        t_col = (
                            (theme.primary_accent if use_theme else ACCENT_YELLOW)
                            if code == boosted_code
                            else palette[idx % len(palette)]
                        )
                        pts_trail = trails[code][:-1]
                        if use_theme and pts_trail:
                            n_tr = len(pts_trail)
                            for ti, t_pt in enumerate(pts_trail):
                                frac = (ti + 1) / float(n_tr + 1)
                                tr_r = 2.0 + 7.5 * frac
                                tr_col = blend_rgb(theme.bg_center, t_col, 0.22 + 0.72 * frac)
                                draw.ellipse(
                                    [t_pt[0] - tr_r, t_pt[1] - tr_r, t_pt[0] + tr_r, t_pt[1] + tr_r],
                                    fill=tr_col,
                                )
                        else:
                            for t_pt in pts_trail:
                                draw.ellipse([t_pt[0] - 6, t_pt[1] - 6, t_pt[0] + 6, t_pt[1] + 6], fill=t_col)

                        if code == boosted_code:
                            aura_col = theme.primary_accent if use_theme else ACCENT_YELLOW
                            aura_r = marble_radius + 5 + int(2 * abs(math.sin(t_sec * 6.0)))
                            draw.ellipse(
                                [mx - aura_r, my - aura_r, mx + aura_r, my + aura_r],
                                outline=aura_col,
                                width=3,
                            )
                        icon = icon_cache[code].get(ang)
                        frame.paste(icon, (int(mx - marble_radius), int(my - marble_radius)), icon)

                # Anonymous live counter — NO country names until the reveal.
                if use_theme:
                    draw_beveled_panel(
                        draw,
                        (60, 1380, VIDEO_WIDTH - 60, 1498),
                        radius=22,
                        fill=theme.panel_bg,
                        outer_border=theme.primary_accent,
                        outer_width=3,
                        inner_border=theme.panel_border,
                    )
                    draw.text(
                        (90, 1394),
                        f"ARENA: {arena.label.upper()}",
                        font=get_font(25, bold=True),
                        fill=theme.primary_accent,
                    )
                    ed_tag = f"{theme.edition_name}"
                    eb = draw.textbbox((0, 0), ed_tag, font=get_font(22, bold=True))
                    draw.text(
                        (VIDEO_WIDTH - 90 - (eb[2] - eb[0]), 1396),
                        ed_tag,
                        font=get_font(22, bold=True),
                        fill=theme.secondary_accent,
                    )
                    draw.text(
                        (90, 1430),
                        f"{arena.finish_word}: {f_state['finished']}/{len(race_codes)}",
                        font=get_font(34, bold=True),
                        fill=TEXT_WHITE,
                    )
                    hidden = (
                        f"{points_multiplier}X POINTS • NAMES HIDDEN"
                        if points_multiplier > 1
                        else "NAMES HIDDEN UNTIL REVEAL"
                    )
                    hb = draw.textbbox((0, 0), hidden, font=get_font(23, bold=True))
                    draw.text(
                        (VIDEO_WIDTH - 90 - (hb[2] - hb[0]), 1438),
                        hidden,
                        font=get_font(23, bold=True),
                        fill=theme.text_muted,
                    )
                    # Live escape progress bar along dock bottom
                    bar_x0, bar_x1 = 90, VIDEO_WIDTH - 90
                    draw.rounded_rectangle([bar_x0, 1476, bar_x1, 1486], radius=5, fill=theme.bg_outer)
                    if f_state["finished"] > 0:
                        prog_w = max(14, int((bar_x1 - bar_x0) * (f_state["finished"] / float(len(race_codes)))))
                        draw.rounded_rectangle(
                            [bar_x0, 1476, bar_x0 + prog_w, 1486],
                            radius=5,
                            fill=theme.primary_accent,
                        )
                else:
                    draw.rounded_rectangle([64, 1385, VIDEO_WIDTH - 64, 1495], radius=22, fill=PANEL_BG_COLOR,
                                           outline=PANEL_BORDER_COLOR, width=3)
                    draw.text((92, 1400), f"ARENA: {arena.label.upper()}", font=get_font(26, bold=True), fill=ACCENT_YELLOW)
                    draw.text((92, 1440), f"{arena.finish_word}: {f_state['finished']}/{len(race_codes)}",
                              font=get_font(34, bold=True), fill=TEXT_WHITE)
                    hidden = f"{points_multiplier}X POINTS • NAMES HIDDEN" if points_multiplier > 1 else "RESULTS HIDDEN UNTIL REVEAL"
                    hb = draw.textbbox((0, 0), hidden, font=get_font(24, bold=True))
                    draw.text((VIDEO_WIDTH - 92 - (hb[2] - hb[0]), 1448), hidden, font=get_font(24, bold=True),
                              fill=TEXT_MUTED)
            elif t_sec < standings_page_sec:
                frame = _draw_race_results_card(card_backdrop.copy(), full_finish_order, sim.finish_times, race_num,
                                                arena.label, arena.finish_word, points_multiplier, theme=hud_theme)
            elif is_grand_final:
                frame = _draw_champion_card(card_backdrop.copy(), standings, season, t_sec - standings_page_sec,
                                            theme=hud_theme)
            else:
                frame = _draw_standings_card(card_backdrop.copy(), winner_code, standings, season, race_num, stage_name,
                                             cliffhanger_text, eliminated_this_race, theme=hud_theme,
                                             elim_note=elim_note)

            hud_flag_code = boosted_code or (
                str(wildcard["country_code"]).upper()
                if wildcard and wildcard.get("country_code") and wildcard_tuple
                else None
            )
            apply_standard_hud(
                frame=frame,
                t_sec=t_sec,
                total_duration=total_duration,
                series_badge=series_badge,
                hook_title=hook_title,
                subtitle_line=f"Seed: {seed}  •  {len(race_codes)} Countries  •  {arena.label}",
                countdown_start_sec=countdown_start_sec,
                reveal_start_sec=reveal_start_sec,
                wildcard_info=wildcard_tuple,
                voice_cues=voice_cues,
                footer_credit=footer_txt,
                crowd_boost_banner=crowd_boost_banner,
                theme=hud_theme,
                banner_flag_code=hud_flag_code,
            )
            pipe.write_frame(frame)

    # Clean up temporary WAV
    if audio_wav_path.exists():
        audio_wav_path.unlink()

    next_race_label = (
        f"Season {next_season} Race 1" if next_season != season else f"Race {next_race_num}"
    )
    comment_cta = (
        "Comment your country to give them tomorrow's +5% CROWD BOOST! 🔥👇"
        if next_season >= CROWD_BOOST_MIN_SEASON
        else "Comment your country for the Wildcard spot! 👇"
    )

    return {
        "format": "marble_league",
        "seed": seed,
        "video_path": output_mp4_path,
        "duration_sec": total_duration,
        "winner": f"{winner_meta['emoji']} {winner_meta['name']}",
        "winner_code": winner_code,
        "season": season,
        "edition_key": theme.edition_key,
        "edition_name": theme.edition_name,
        "race_number": race_num,
        "stage_name": stage_name,
        "variant": variant,
        "arena_mode": arena.mode,
        "arena_label": arena.label,
        "crowd_boost_code": boosted_code,
        "is_grand_final": is_grand_final,
        "points_multiplier": points_multiplier,
        "season_champion": standings[0]["name"] if is_grand_final else None,
        "finish_order": full_finish_order,
        "series_title": f"Season {season} · Race {race_num}",
        "series_short": f"S{season}·R{race_num}",
        "focus_country": focus_meta["name"],
        "focus_emoji": focus_meta["emoji"],
        "rival_country": rival_meta["name"],
        "rival_emoji": rival_meta["emoji"],
        "reveal_timestamp_sec": reveal_start_sec,
        "cliffhanger": cliffhanger_text,
        "comment_question": (
            f"Who wins {next_race_label} tomorrow? {focus_meta['emoji']} {focus_meta['name']} or "
            f"{rival_meta['emoji']} {rival_meta['name']}? {comment_cta}"
        ),
    }
