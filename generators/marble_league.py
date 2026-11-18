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
)
from common.physics_kit import RotatingIconCache, attach_impulse_sfx, draw_checkered_line
from common.render import (
    FFmpegFramePipe,
    add_countdown_and_reveal_sfx,
    apply_standard_hud,
    draw_centered_text,
)
from common.tts import build_standard_voice_cues
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
    Returns all 16 countries sorted by `(not eliminated, points DESC, wins DESC, podiums DESC, code ASC)`.
    """
    items = list(state.get("countries", {}).values())
    items.sort(
        key=lambda c: (
            0 if c.get("eliminated") else 1,
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
) -> RaceSimulation:
    """Runs the full deterministic Pymunk race for `variant` and records per-frame state."""
    rng = random.Random(_seed_to_int(f"{seed}|{variant}"))
    space = pymunk.Space()
    space.damping = 0.996
    arena = build_arena(variant, space, rng, len(race_codes), marble_radius, intensity)
    space.gravity = arena.gravity

    bodies: Dict[str, pymunk.Body] = {}
    for idx, code in enumerate(race_codes):
        mass = 1.2
        body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, marble_radius))
        body.position = arena.spawn_points[idx]
        if arena.initial_velocities:
            body.velocity = arena.initial_velocities[idx]
        else:
            vx0, vx1, vy0, vy1 = arena.spawn_velocity
            body.velocity = (rng.uniform(vx0, vx1), rng.uniform(vy0, vy1))
        if arena.force_field is not None:
            body.velocity_func = arena.force_field
        shape = pymunk.Circle(body, marble_radius)
        shape.elasticity = arena.marble_elasticity
        shape.friction = arena.marble_friction
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
    seed: str, variant: str, race_codes: List[str], marble_radius: float, pacing: RacePacing = REGULAR_PACING
) -> RaceSimulation:
    """
    Physics is chaotic, so instead of a directional search we scan a fixed intensity ladder
    (each physics-only sim takes ~50ms) and keep the best-paced race. Deterministic per seed.
    """
    best: Optional[Tuple[float, RaceSimulation]] = None
    for intensity in INTENSITY_LADDER:
        sim = simulate_race(seed, variant, race_codes, marble_radius, intensity, sim_until_sec=pacing.sim_until)
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
def _card_frame(frame: Image.Image) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle([54, 360, VIDEO_WIDTH - 54, 1615], radius=36, fill=(18, 20, 36),
                           outline=ACCENT_YELLOW, width=5)
    return draw


def _draw_race_results_card(
    frame: Image.Image,
    finish_order: List[str],
    finish_times: Dict[str, float],
    race_num: int,
    arena_label: str,
    finish_word: str,
    points_multiplier: int = 1,
) -> Image.Image:
    """
    Reveal page 1: winner banner + this race's result table (pos, flag, country, time, points)
    + India's finishing position callout.
    """
    draw = _card_frame(frame)
    cx = VIDEO_WIDTH // 2
    winner_code = finish_order[0]
    winner_meta = COUNTRIES_META.get(winner_code, COUNTRIES_META["IN"])

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

    # India callout if India raced but is outside the top 8
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


def _draw_standings_card(
    frame: Image.Image,
    winner_code: str,
    standings: List[Dict[str, Any]],
    season: int,
    race_num: int,
    stage_name: str,
    cliffhanger_text: str,
    eliminated_code: Optional[str],
) -> Image.Image:
    """
    Reveal page 2: season standings so far (top 8 incl. this race) + India spotlight + cliffhanger.
    """
    draw = _card_frame(frame)
    cx = VIDEO_WIDTH // 2
    draw_centered_text(draw, f"SEASON {season} STANDINGS", cx, 390, get_font(58, title=True), fill=ACCENT_YELLOW)
    draw_centered_text(draw, f"AFTER RACE {race_num}  •  {stage_name.upper()}", cx, 456,
                       get_font(30, bold=True), fill=TEXT_MUTED)

    row_font = get_font(32, bold=True)
    row_y = 512
    india_rank, india_pts = 0, 0
    for idx, entry in enumerate(standings):
        if entry["code"] == "IN":
            india_rank, india_pts = idx + 1, int(entry["points"])
            break

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

    # India spotlight
    draw.rounded_rectangle([92, row_y + 6, VIDEO_WIDTH - 92, row_y + 80], radius=18, fill=(48, 34, 24),
                           outline=(255, 153, 51), width=3)
    in_icon = load_circular_flag_icon("IN", diameter=50, border_color=(255, 153, 51), border_width=2)
    frame.paste(in_icon, (116, row_y + 18), in_icon)
    draw_centered_text(draw, f"INDIA SPOTLIGHT: #{india_rank} OVERALL ({india_pts} PTS)", cx + 20, row_y + 26,
                       get_font(32, bold=True), fill=TEXT_WHITE)
    row_y += 96

    if eliminated_code:
        draw_centered_text(draw, f"ELIMINATED: {COUNTRIES_META[eliminated_code]['name'].upper()}", cx, row_y,
                           get_font(30, bold=True), fill=ACCENT_RED)
        row_y += 44

    cliff_y = min(row_y, 1490)
    draw.rounded_rectangle([92, cliff_y, VIDEO_WIDTH - 92, cliff_y + 105], radius=20, fill=(45, 18, 32),
                           outline=ACCENT_RED, width=3)
    draw_centered_text(draw, "NEXT BATTLE CLIFFHANGER:", cx, cliff_y + 14, get_font(26, bold=True), fill=ACCENT_YELLOW)
    draw_centered_text(draw, cliffhanger_text, cx, cliff_y + 50, get_font(32, bold=True), fill=TEXT_WHITE)
    return frame


def _draw_champion_card(frame: Image.Image, standings: List[Dict[str, Any]], season: int, t_in: float) -> Image.Image:
    """
    Grand Final page 2: SEASON CHAMPION ceremony — trophy, champion flag, podium (top 3 by final points),
    final top-8 table and a 'Season N+1 starts tomorrow' teaser. `t_in` animates the confetti.
    """
    draw = ImageDraw.Draw(frame)
    cx = VIDEO_WIDTH // 2
    draw.rounded_rectangle([54, 360, VIDEO_WIDTH - 54, 1615], radius=36, fill=(22, 18, 8), outline=(255, 210, 60), width=6)
    # confetti (deterministic)
    crng = random.Random(season * 7919)
    for i in range(90):
        x0 = crng.uniform(70, VIDEO_WIDTH - 70)
        y0 = 370 + ((crng.uniform(0, 1200) + t_in * crng.uniform(160, 320)) % 1230)
        col = ACCENT_PALETTE[i % len(ACCENT_PALETTE)] if i % 3 else (255, 220, 90)
        draw.rectangle([x0, y0, x0 + 9, y0 + 16], fill=col)
    champ = standings[0]
    draw_centered_text(draw, f"SEASON {season} CHAMPION", cx, 384, get_font(70, title=True), fill=(255, 210, 60))
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
        col = [(255, 210, 60), (200, 205, 215), (205, 127, 50)][rank_idx]
        draw.rectangle([x0, base_y - h, x0 + 180, base_y], fill=col, outline=TEXT_WHITE, width=3)
        draw_centered_text(draw, f"#{rank_idx + 1}", x0 + 90, base_y - h + 12, get_font(52, title=True), fill=BG_COLOR)
        icon = load_circular_flag_icon(entry["code"], diameter=80, border_color=TEXT_WHITE, border_width=3)
        frame.paste(icon, (x0 + 50, base_y - h - 92), icon)
        draw_centered_text(draw, f"{entry['name']} ({entry['points']})", x0 + 90, base_y + 10, get_font(26, bold=True), fill=TEXT_WHITE)
    # rest of top 8
    row_y = 1070
    font_row = get_font(28, bold=True)
    for rank, entry in enumerate(standings[3:8], start=4):
        draw.rounded_rectangle([110, row_y, VIDEO_WIDTH - 110, row_y + 52], radius=12, fill=(40, 34, 20))
        draw.text((132, row_y + 10), f"#{rank}  {entry['name']}", font=font_row, fill=TEXT_WHITE)
        pts = f"{entry['points']} PTS"
        pb = draw.textbbox((0, 0), pts, font=font_row)
        draw.text((VIDEO_WIDTH - 132 - (pb[2] - pb[0]), row_y + 10), pts, font=font_row, fill=(255, 210, 60))
        row_y += 60
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


def generate_marble_league_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    wildcard: Optional[Dict[str, str]] = None,
    weekly_recap: bool = False,
    update_state: bool = True,
    variant_override: Optional[str] = None,
    force_grand_final: bool = False,
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
    if force_grand_final:
        # Preview/testing only: simulate the season finale in memory. Never persisted.
        if update_state:
            raise ValueError("force_grand_final is a preview mode and requires update_state=False")
        race_num = max_races

    # Determine stage across the 4-week (28-race) season
    if race_num <= 14:
        stage_name = "Group Stage"
    elif race_num <= 21:
        stage_name = "Knockout Stage"
    elif race_num < max_races:
        stage_name = "Championship Finals"
    else:
        stage_name = "Monthly Grand Final"

    # Determine active countries
    all_countries = state.get("countries", {})
    active_codes = [c for c in LEAGUE_COUNTRY_CODES if not all_countries.get(c, {}).get("eliminated", False)]
    if len(active_codes) < 6:
        for c in LEAGUE_COUNTRY_CODES:
            all_countries[c]["eliminated"] = False
        active_codes = list(LEAGUE_COUNTRY_CODES)

    # Vary ball count (10 to 16 in Group Stage, or all remaining in Knockouts)
    if stage_name == "Group Stage":
        ball_count = rng.choice([10, 12, 14, 16])
        # Always include India, USA, Brazil, Indonesia + Wildcard if requested
        priority = ["IN", "US", "BR", "ID"]
        if wildcard and wildcard.get("country_code") in active_codes:
            wc_code = wildcard["country_code"]
            if wc_code not in priority:
                priority.insert(0, wc_code)
        remaining = [c for c in active_codes if c not in priority]
        rng.shuffle(remaining)
        race_codes = (priority + remaining)[:ball_count]
    else:
        race_codes = list(active_codes)

    is_grand_final = stage_name == "Monthly Grand Final"
    # Points: x2 in the Championship Finals, x3 in the Grand Final mega race (keeps the title race alive)
    points_multiplier = GRAND_FINAL_POINTS_MULTIPLIER if is_grand_final else (2 if stage_name == "Championship Finals" else 1)

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
    sim = run_tuned_race(seed, variant, race_codes, marble_radius, pacing)
    arena = sim.arena
    sfx_events = list(sim.sfx_events)

    # Final ranking: finishers in finish order, then closest-to-finish
    escaped_order = list(sim.finish_order)
    un_escaped = [c for c in race_codes if c not in escaped_order]
    un_escaped.sort(key=lambda c: sim.final_progress[c], reverse=True)
    full_finish_order = escaped_order + un_escaped
    winner_code = full_finish_order[0]
    podium_codes = full_finish_order[:5]

    # Award points in league state (Top 5: 10, 7, 5, 3, 1)
    for idx, code in enumerate(podium_codes):
        pts = POINTS_TABLE[idx] * points_multiplier
        c_entry = state["countries"][code]
        c_entry["points"] = int(c_entry.get("points", 0)) + pts
        if idx == 0:
            c_entry["wins"] = int(c_entry.get("wins", 0)) + 1
        if idx < 3:
            c_entry["podiums"] = int(c_entry.get("podiums", 0)) + 1

    # In Knockout Stage (Races 15–21), eliminate the last-place active country if > 8 remain
    eliminated_this_race = None
    if stage_name == "Knockout Stage" and len(active_codes) > 8:
        last_code = full_finish_order[-1]
        state["countries"][last_code]["eliminated"] = True
        eliminated_this_race = last_code

    standings = compute_standings(state)
    winner_meta = COUNTRIES_META[winner_code]

    # Build cliffhanger for tomorrow
    danger_candidates = [s for s in standings if not s.get("eliminated") and s["code"] != winner_code]
    cliff_country = danger_candidates[min(3, len(danger_candidates) - 1)] if danger_candidates else standings[-1]
    cliffhanger_text = f"Can {cliff_country['name']} survive Race {race_num + 1}? Tomorrow."
    if race_num + 1 == max_races:
        cliffhanger_text = "TOMORROW: GRAND FINAL MEGA RACE — TRIPLE POINTS!"
    elif race_num + 1 == 22:
        cliffhanger_text = "Championship Finals start tomorrow — DOUBLE POINTS!"

    state["race_number"] = race_num
    state["stage"] = stage_name
    state.setdefault("history", []).append(
        {
            "season": season,
            "race": race_num,
            "seed": seed,
            "variant": variant,
            "winner": winner_code,
            "podium": podium_codes[:3],
            "eliminated": eliminated_this_race,
            "finish_order": full_finish_order,
            "points_multiplier": points_multiplier,
        }
    )

    # Auto-start next season after Race 28 (Monthly Grand Final)
    if race_num >= max_races:
        champ = standings[0]
        state.setdefault("past_champions", []).append(
            {"season": season, "champion": champ["code"], "points": champ["points"]}
        )
        state["season"] = season + 1
        state["race_number"] = 0
        state["stage"] = "Group Stage"
        for c in state["countries"].values():
            c["points"] = 0
            c["wins"] = 0
            c["podiums"] = 0
            c["eliminated"] = False

    if update_state:
        save_league_state(state)

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
    if is_grand_final:
        hook_spoken = f"Season {season} Grand Final! Triple points, one mega gauntlet. Who becomes champion?"
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

    audio_wav_path = output_dir / f"marble_league_{seed}.wav"
    mix_master_audio(
        duration_sec=total_duration,
        output_wav_path=audio_wav_path,
        sfx_events=sfx_events,
        voice_cues=voice_cues,
        bgm_track_name="arena_pulse_cc0.wav",
        bgm_volume=0.15,
    )

    output_mp4_path = output_dir / f"marble_league_{seed}.mp4"
    series_badge = (
        f"WEEKLY RECAP · S{season} R{race_num}"
        if weekly_recap
        else f"SEASON {season} · RACE {race_num} ({stage_name})"
    )
    if is_grand_final:
        series_badge = f"S{season} GRAND FINAL · 3X POINTS"
    elif points_multiplier == 2:
        series_badge = f"S{season} · RACE {race_num} · 2X POINTS"
    hook_title = "GRAND FINAL: MEGA RACE!" if is_grand_final else arena.hook_title
    wildcard_tuple = (
        (wildcard["country_label"], wildcard["username"])
        if wildcard and wildcard.get("country_label") and wildcard.get("username")
        else None
    )

    # Pre-render the static arena layer once; spinning flag icons are pre-rotated.
    base_layer = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
    _draw_arena_background(ImageDraw.Draw(base_layer), arena)
    arena.kit.render_static(base_layer)
    icon_cache: Dict[str, RotatingIconCache] = {}
    for idx, code in enumerate(race_codes):
        border_col = ACCENT_PALETTE[idx % len(ACCENT_PALETTE)]
        icon = load_circular_flag_icon(code, diameter=marble_radius * 2, border_color=border_col, border_width=4)
        icon_cache[code] = RotatingIconCache(icon)
    trails: Dict[str, List[Tuple[float, float]]] = {c: [] for c in race_codes}
    blank = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)

    with FFmpegFramePipe(output_mp4_path, audio_wav_path) as pipe:
        for f_idx in range(total_frames):
            t_sec = f_idx / float(VIDEO_FPS)
            if t_sec < reveal_start_sec:
                f_state = sim.frames[min(f_idx, len(sim.frames) - 1)]
                frame = base_layer.copy()
                draw = ImageDraw.Draw(frame)
                arena.kit.render_dynamic(draw, f_state["kin"])

                for idx, code in enumerate(race_codes):
                    mx, my, ang = f_state["marbles"][code]
                    if -marble_radius <= mx <= VIDEO_WIDTH + marble_radius and 320 <= my <= 1600:
                        trails[code].append((mx, my))
                        trails[code] = trails[code][-6:]
                        t_col = ACCENT_PALETTE[idx % len(ACCENT_PALETTE)]
                        for t_pt in trails[code][:-1]:
                            draw.ellipse([t_pt[0] - 6, t_pt[1] - 6, t_pt[0] + 6, t_pt[1] + 6], fill=t_col)
                        icon = icon_cache[code].get(ang)
                        frame.paste(icon, (int(mx - marble_radius), int(my - marble_radius)), icon)

                # Anonymous live counter — NO country names until the reveal.
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
                frame = _draw_race_results_card(blank.copy(), full_finish_order, sim.finish_times, race_num,
                                                arena.label, arena.finish_word, points_multiplier)
            elif is_grand_final:
                frame = _draw_champion_card(blank.copy(), standings, season, t_sec - standings_page_sec)
            else:
                frame = _draw_standings_card(blank.copy(), winner_code, standings, season, race_num, stage_name,
                                             cliffhanger_text, eliminated_this_race)

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
                footer_credit=f"Race seed: {seed} • Physics: Pymunk 2D",
            )
            pipe.write_frame(frame)

    # Clean up temporary WAV
    if audio_wav_path.exists():
        audio_wav_path.unlink()

    rival_code = full_finish_order[1] if len(full_finish_order) > 1 else "BR"
    rival_meta = COUNTRIES_META[rival_code]

    return {
        "format": "marble_league",
        "seed": seed,
        "video_path": output_mp4_path,
        "duration_sec": total_duration,
        "winner": f"{winner_meta['emoji']} {winner_meta['name']}",
        "winner_code": winner_code,
        "season": season,
        "race_number": race_num,
        "variant": variant,
        "arena_label": arena.label,
        "is_grand_final": is_grand_final,
        "points_multiplier": points_multiplier,
        "season_champion": standings[0]["name"] if is_grand_final else None,
        "finish_order": full_finish_order,
        "series_title": f"Season {season} · Race {race_num}",
        "series_short": f"S{season}·R{race_num}",
        "focus_country": "India" if "IN" in race_codes else winner_meta["name"],
        "focus_emoji": "🇮🇳" if "IN" in race_codes else winner_meta["emoji"],
        "rival_country": rival_meta["name"],
        "rival_emoji": rival_meta["emoji"],
        "reveal_timestamp_sec": reveal_start_sec,
        "cliffhanger": cliffhanger_text,
        "comment_question": (
            f"Who wins Race {race_num + 1} tomorrow? 🇮🇳 India or {rival_meta['emoji']} {rival_meta['name']}? "
            "Comment your country for the Wildcard spot! 👇"
        ),
    }
