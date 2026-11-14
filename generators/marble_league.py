"""
Call It Arena — Format 1 (FLAGSHIP): Country Marble League (`generators/marble_league.py`).

Features:
- 16 high-engagement countries: India, USA, Brazil, Indonesia, Philippines, Pakistan,
  Bangladesh, Nigeria, Mexico, UK, Germany, Japan, Turkey, Egypt, Vietnam, Canada.
- Headless Pygame (`SDL_VIDEODRIVER=dummy`) + Pymunk 2D rigid-body physics simulation.
- 4 Rotating Arena Variants (seeded by `YYYYMMDD-SLOT`):
  1. `single_ring`: Rotating neon ring with an escape gap & central bumper spinner.
  2. `double_ring`: Inner rotating ring escaping into a counter-rotating outer ring.
  3. `spiral_labyrinth`: Dual offset rotating rings with interior deflector pegs.
  4. `funnel_obstacles`: Rotating upper ring dropping into a pinball obstacle funnel.
- Varies ring angular velocity, gap degrees, marble count (8–16), and gravity per seed.
- Logs Pymunk collision impulses to emit synced CC0 `boing` SFX timestamps.
- Updates `state/league.json` across a 4-week season (Group Stage -> Knockouts -> Monthly Grand Final)
  and auto-starts the next season after Race 28.
- Ends every race with a Top 5 Season Standings card + `"🇮🇳 India: #X"` spotlight + tomorrow's cliffhanger.
- Also supports Sunday Slot B `"Weekly Standings + Mega Quiz"` mode (`weekly_recap=True`).
"""

import hashlib
import json
import logging
import math
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
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
from common.render import (
    FFmpegFramePipe,
    add_countdown_and_reveal_sfx,
    apply_standard_hud,
    draw_centered_text,
)
from common.tts import build_standard_voice_cues

logger = logging.getLogger("call_it_arena")

LEAGUE_STATE_FILE = STATE_DIR / "league.json"

LEAGUE_COUNTRY_CODES = [
    "IN", "US", "BR", "ID", "PH", "PK", "BD", "NG",
    "MX", "GB", "DE", "JP", "TR", "EG", "VN", "CA",
]

ARENA_VARIANTS = [
    "single_ring",
    "double_ring",
    "spiral_labyrinth",
    "funnel_obstacles",
]


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


def _add_ring_segments(
    space: pymunk.Space,
    body: pymunk.Body,
    radius: float,
    gap_deg: float,
    thickness: float = 10.0,
    num_segments: int = 44,
    phase_offset_deg: float = 0.0,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """
    Adds static/kinematic arc segments forming a circular ring of `radius` around `(0, 0)` on `body`,
    leaving an open escape gap of `gap_deg` degrees.
    Returns local segment endpoints `(p1, p2)` for rendering.
    """
    local_segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    arc_deg = 360.0 - gap_deg
    step_deg = arc_deg / float(num_segments)
    start_deg = phase_offset_deg + gap_deg / 2.0

    for i in range(num_segments):
        a1 = math.radians(start_deg + i * step_deg)
        a2 = math.radians(start_deg + (i + 1) * step_deg)
        p1 = (math.cos(a1) * radius, math.sin(a1) * radius)
        p2 = (math.cos(a2) * radius, math.sin(a2) * radius)
        seg = pymunk.Segment(body, p1, p2, thickness)
        seg.elasticity = 0.94
        seg.friction = 0.15
        seg.collision_type = 2
        space.add(seg)
        local_segs.append((p1, p2))
    return local_segs


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
    """
    Renders the final ~5s Leaderboard & Reveal Card onto `frame`:
    - Winner Banner with Flag Icon
    - Top 5 League Standings Table
    - Explicit `"🇮🇳 India: #X (Y pts)"` callout badge
    - Tomorrow's Cliffhanger Banner (`"Can 🇧🇷 Brazil survive Round X? Tomorrow."`)
    """
    draw = ImageDraw.Draw(frame)
    cx = VIDEO_WIDTH // 2

    # Darken arena slightly with a sleek translucent card
    card_top, card_bot = 360, 1615
    draw.rounded_rectangle(
        [54, card_top, VIDEO_WIDTH - 54, card_bot],
        radius=36,
        fill=(18, 20, 36),
        outline=ACCENT_YELLOW,
        width=5,
    )

    winner_meta = COUNTRIES_META.get(winner_code, COUNTRIES_META["IN"])
    # Winner header pill
    draw.rounded_rectangle(
        [100, card_top + 26, VIDEO_WIDTH - 100, card_top + 110],
        radius=22,
        fill=ACCENT_GREEN,
    )
    draw_centered_text(
        draw,
        f"WINNER: {winner_meta['name'].upper()} (+10 PTS)",
        cx,
        card_top + 42,
        get_font(58, title=True),
        fill=BG_COLOR,
    )

    # Circular winner flag icon
    win_flag = load_circular_flag_icon(winner_code, diameter=130, border_color=ACCENT_YELLOW, border_width=5)
    frame.paste(win_flag, (cx - 65, card_top + 128), win_flag)

    # Top 5 Standings Header
    draw_centered_text(
        draw,
        f"SEASON {season} STANDINGS  •  {stage_name.upper()}",
        cx,
        card_top + 276,
        get_font(46, title=True),
        fill=ACCENT_YELLOW,
    )

    row_y = card_top + 338
    font_row = get_font(34, bold=True)
    india_rank = 1
    india_pts = 0
    for idx, entry in enumerate(standings):
        if entry["code"] == "IN":
            india_rank = idx + 1
            india_pts = int(entry["points"])
            break

    for rank_idx, entry in enumerate(standings[:5], start=1):
        code = entry["code"]
        is_win = code == winner_code
        row_bg = (38, 52, 88) if is_win else (26, 30, 52)
        row_border = ACCENT_GREEN if is_win else PANEL_BORDER_COLOR
        draw.rounded_rectangle(
            [92, row_y, VIDEO_WIDTH - 92, row_y + 84],
            radius=18,
            fill=row_bg,
            outline=row_border,
            width=3,
        )
        icon = load_circular_flag_icon(code, diameter=58, border_color=TEXT_WHITE, border_width=2)
        frame.paste(icon, (114, row_y + 13), icon)
        draw.text(
            (192, row_y + 22),
            f"#{rank_idx}  {entry['name']}",
            font=font_row,
            fill=TEXT_WHITE,
        )
        pts_text = f"{entry['points']} PTS  ({entry['wins']}W)"
        pb = draw.textbbox((0, 0), pts_text, font=font_row)
        draw.text(
            (VIDEO_WIDTH - 120 - (pb[2] - pb[0]), row_y + 22),
            pts_text,
            font=font_row,
            fill=ACCENT_YELLOW if rank_idx == 1 else TEXT_WHITE,
        )
        row_y += 96

    # Dedicated India Rank Spotlight Bar ("🇮🇳 India: #X")
    draw.rounded_rectangle(
        [92, row_y + 10, VIDEO_WIDTH - 92, row_y + 88],
        radius=18,
        fill=(48, 34, 24),
        outline=(255, 153, 51),
        width=3,
    )
    in_icon = load_circular_flag_icon("IN", diameter=54, border_color=(255, 153, 51), border_width=2)
    frame.paste(in_icon, (116, row_y + 22), in_icon)
    draw_centered_text(
        draw,
        f"INDIA SPOTLIGHT: #{india_rank} OVERALL ({india_pts} PTS)",
        cx + 20,
        row_y + 30,
        get_font(34, bold=True),
        fill=TEXT_WHITE,
    )

    # Cliffhanger Box at bottom of card
    cliff_y = row_y + 108
    draw.rounded_rectangle(
        [92, cliff_y, VIDEO_WIDTH - 92, cliff_y + 105],
        radius=20,
        fill=(45, 18, 32),
        outline=ACCENT_RED,
        width=3,
    )
    draw_centered_text(
        draw,
        "NEXT BATTLE CLIFFHANGER:",
        cx,
        cliff_y + 14,
        get_font(26, bold=True),
        fill=ACCENT_YELLOW,
    )
    draw_centered_text(
        draw,
        cliffhanger_text,
        cx,
        cliff_y + 50,
        get_font(32, bold=True),
        fill=TEXT_WHITE,
    )

    return frame


def generate_marble_league_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    wildcard: Optional[Dict[str, str]] = None,
    weekly_recap: bool = False,
    update_state: bool = True,
) -> Dict[str, Any]:
    """
    Runs a complete Country Marble League physics simulation (or Sunday Weekly Standings + Mega Quiz),
    renders the 1080x1920 30fps MP4 with synced collision SFX + Piper TTS, updates `state/league.json`,
    and returns the generation metadata dictionary.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    state = load_league_state()
    rng = random.Random(_seed_to_int(seed))

    season = int(state.get("season", 1))
    race_num = int(state.get("race_number", 0)) + 1
    max_races = int(state.get("max_races_per_season", 28))

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

    # Select arena variant and physics parameters per seed
    variant = ARENA_VARIANTS[_seed_to_int(seed) % len(ARENA_VARIANTS)]
    gravity_y = rng.uniform(440.0, 580.0)
    ring_speed_rad = rng.uniform(1.35, 2.05) * rng.choice([-1.0, 1.0])
    gap_deg = rng.uniform(44.0, 58.0)
    marble_radius = 30 if len(race_codes) <= 12 else 27

    # Video timing: 24.0 seconds total (720 frames @ 30fps)
    total_duration = 24.0
    total_frames = int(total_duration * VIDEO_FPS)
    countdown_start_sec = 15.5
    reveal_start_sec = 18.5
    outro_start_sec = 22.2

    # Set up Pymunk physics space
    pygame.init()
    space = pymunk.Space()
    space.gravity = (0.0, gravity_y)
    space.damping = 0.996

    arena_cx, arena_cy = VIDEO_WIDTH // 2, 920
    outer_radius = 415.0
    inner_radius = 255.0

    # Kinematic rotating ring bodies
    outer_ring_body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
    outer_ring_body.position = (arena_cx, arena_cy)
    outer_ring_body.angular_velocity = ring_speed_rad
    space.add(outer_ring_body)
    outer_segs = _add_ring_segments(space, outer_ring_body, outer_radius, gap_deg=gap_deg, thickness=12.0)

    inner_ring_body: Optional[pymunk.Body] = None
    inner_segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    if variant in ("double_ring", "spiral_labyrinth"):
        inner_ring_body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        inner_ring_body.position = (arena_cx, arena_cy)
        inner_ring_body.angular_velocity = -ring_speed_rad * 1.25
        space.add(inner_ring_body)
        inner_segs = _add_ring_segments(
            space,
            inner_ring_body,
            inner_radius,
            gap_deg=gap_deg + 10.0,
            thickness=10.0,
            phase_offset_deg=180.0,
        )

    # Static obstacle pegs for spiral_labyrinth / funnel_obstacles
    obstacle_pegs: List[Tuple[float, float, float]] = []
    if variant in ("spiral_labyrinth", "funnel_obstacles"):
        peg_ring_r = 155.0 if variant == "spiral_labyrinth" else 220.0
        num_pegs = 6 if variant == "spiral_labyrinth" else 8
        for i in range(num_pegs):
            ang = i * (2.0 * math.pi / num_pegs)
            px = arena_cx + math.cos(ang) * peg_ring_r
            py = arena_cy + math.sin(ang) * peg_ring_r
            peg_shape = pymunk.Circle(space.static_body, 14.0, (px, py))
            peg_shape.elasticity = 0.98
            peg_shape.friction = 0.1
            peg_shape.collision_type = 2
            space.add(peg_shape)
            obstacle_pegs.append((px, py, 14.0))

    # Central rotating spinner bar in `single_ring` and `funnel_obstacles`
    spinner_body: Optional[pymunk.Body] = None
    spinner_len = 135.0
    if variant in ("single_ring", "funnel_obstacles"):
        spinner_body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        spinner_body.position = (arena_cx, arena_cy)
        spinner_body.angular_velocity = -ring_speed_rad * 1.6
        space.add(spinner_body)
        sp_seg = pymunk.Segment(spinner_body, (-spinner_len, 0), (spinner_len, 0), 10.0)
        sp_seg.elasticity = 0.98
        sp_seg.collision_type = 2
        space.add(sp_seg)

    # Create Marble rigid bodies
    marbles: Dict[str, Dict[str, Any]] = {}
    spawn_r_limit = 160.0 if inner_ring_body else 240.0
    for idx, code in enumerate(race_codes):
        mass = 1.2
        moment = pymunk.moment_for_circle(mass, 0, marble_radius)
        body = pymunk.Body(mass, moment)
        ang = (2.0 * math.pi * idx) / len(race_codes) + rng.uniform(-0.15, 0.15)
        dist = rng.uniform(42.0, spawn_r_limit)
        body.position = (arena_cx + math.cos(ang) * dist, arena_cy + math.sin(ang) * dist)
        body.velocity = (rng.uniform(-260.0, 260.0), rng.uniform(-240.0, 180.0))
        shape = pymunk.Circle(body, marble_radius)
        shape.elasticity = 0.93
        shape.friction = 0.12
        shape.collision_type = 1
        space.add(body, shape)

        border_col = ACCENT_PALETTE[idx % len(ACCENT_PALETTE)]
        icon = load_circular_flag_icon(code, diameter=marble_radius * 2, border_color=border_col, border_width=4)
        marbles[code] = {
            "code": code,
            "body": body,
            "shape": shape,
            "icon": icon,
            "escaped_at": None,
            "escape_rank": None,
            "trail": [],
        }

    # Pre-simulate trajectory & collision events across `total_frames` so we know the exact winner
    # before synthesizing the voiceover & audio track!
    sfx_events: List[SfxEvent] = []
    last_sfx_frame = -3
    current_sim_time = 0.0

    def _on_post_solve(arbiter: pymunk.Arbiter, _space: pymunk.Space, _data: Any) -> None:
        nonlocal last_sfx_frame
        frame_idx = int(current_sim_time * VIDEO_FPS)
        if frame_idx - last_sfx_frame < 3 or current_sim_time >= reveal_start_sec:
            return
        impulse = arbiter.total_impulse.length
        if impulse > 140.0:
            last_sfx_frame = frame_idx
            gain = min(0.75, max(0.22, impulse / 650.0))
            pitch = 0.85 + 0.35 * ((frame_idx * 7) % 10) / 10.0
            sfx_events.append(SfxEvent(timestamp_sec=current_sim_time, kind="boing", gain=gain, pitch_shift=pitch))

    if hasattr(space, "on_collision"):
        space.on_collision(None, None, post_solve=_on_post_solve)
    elif hasattr(space, "add_default_collision_handler"):
        handler = space.add_default_collision_handler()
        handler.post_solve = _on_post_solve

    recorded_frames_state: List[Dict[str, Any]] = []
    escaped_order: List[str] = []
    physics_substeps = 4
    dt = 1.0 / (VIDEO_FPS * physics_substeps)

    for f_idx in range(total_frames):
        current_sim_time = f_idx / float(VIDEO_FPS)
        if current_sim_time < reveal_start_sec:
            for _ in range(physics_substeps):
                space.step(dt)

            # Check which marbles have escaped past `outer_radius + marble_radius * 1.1`
            for code, m in marbles.items():
                pos = m["body"].position
                dx, dy = pos.x - arena_cx, pos.y - arena_cy
                dist = math.hypot(dx, dy)
                if m["escaped_at"] is None and dist > (outer_radius + marble_radius * 0.9):
                    m["escaped_at"] = current_sim_time
                    escaped_order.append(code)
                    m["escape_rank"] = len(escaped_order)
                    sfx_events.append(
                        SfxEvent(
                            timestamp_sec=current_sim_time,
                            kind="tick",
                            gain=0.80,
                            pitch_shift=1.15,
                        )
                    )

        snap_marbles = {}
        for code, m in marbles.items():
            pos = m["body"].position
            dx, dy = pos.x - arena_cx, pos.y - arena_cy
            dist = math.hypot(dx, dy)
            snap_marbles[code] = {
                "x": float(pos.x),
                "y": float(pos.y),
                "dist": float(dist),
                "escaped_at": m["escaped_at"],
                "escape_rank": m["escape_rank"],
            }

        recorded_frames_state.append(
            {
                "outer_angle": float(outer_ring_body.angle),
                "inner_angle": float(inner_ring_body.angle) if inner_ring_body else 0.0,
                "spinner_angle": float(spinner_body.angle) if spinner_body else 0.0,
                "marbles": snap_marbles,
            }
        )

    # Determine final ranking: escaped marbles in escape order, then closest to outer ring escape
    final_frame_marbles = recorded_frames_state[int(reveal_start_sec * VIDEO_FPS) - 1]["marbles"]
    un_escaped = [c for c in race_codes if c not in escaped_order]
    un_escaped.sort(key=lambda c: final_frame_marbles[c]["dist"], reverse=True)
    full_finish_order = escaped_order + un_escaped
    winner_code = full_finish_order[0]
    podium_codes = full_finish_order[:5]

    # Award points in league state (Top 5: 10, 7, 5, 3, 1)
    points_table = [10, 7, 5, 3, 1]
    for idx, code in enumerate(podium_codes):
        pts = points_table[idx]
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

    # Add countdown & reveal fanfare SFX
    add_countdown_and_reveal_sfx(sfx_events, countdown_start_sec, reveal_start_sec)

    # Build TTS voiceover cues & burned-in captions
    hook_spoken = (
        f"Weekly Standings and Mega Race! Which country escapes first?"
        if weekly_recap
        else f"Season {season}, Race {race_num}! Which country escapes the ring first?"
    )
    reveal_spoken = f"{winner_meta['name']} wins Race {race_num} and takes 10 league points!"
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

    # Stream rendered frames directly into FFmpeg
    output_mp4_path = output_dir / f"marble_league_{seed}.mp4"
    series_badge = (
        f"WEEKLY RECAP · S{season} R{race_num}"
        if weekly_recap
        else f"SEASON {season} · RACE {race_num} ({stage_name})"
    )
    wildcard_tuple = (
        (wildcard["country_label"], wildcard["username"])
        if wildcard and wildcard.get("country_label") and wildcard.get("username")
        else None
    )

    trails: Dict[str, List[Tuple[float, float]]] = {c: [] for c in race_codes}

    with FFmpegFramePipe(output_mp4_path, audio_wav_path) as pipe:
        for f_idx, f_state in enumerate(recorded_frames_state):
            t_sec = f_idx / float(VIDEO_FPS)
            frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
            draw = ImageDraw.Draw(frame)

            # Subtle arena background radial rings
            for bg_r in (160, 290, 420, 520):
                draw.ellipse(
                    [arena_cx - bg_r, arena_cy - bg_r, arena_cx + bg_r, arena_cy + bg_r],
                    outline=(24, 27, 46),
                    width=2,
                )

            if t_sec < reveal_start_sec:
                # Draw outer rotating ring segments
                ca, sa = math.cos(f_state["outer_angle"]), math.sin(f_state["outer_angle"])
                for p1, p2 in outer_segs:
                    x1 = arena_cx + p1[0] * ca - p1[1] * sa
                    y1 = arena_cy + p1[0] * sa + p1[1] * ca
                    x2 = arena_cx + p2[0] * ca - p2[1] * sa
                    y2 = arena_cy + p2[0] * sa + p2[1] * ca
                    draw.line([(x1, y1), (x2, y2)], fill=ACCENT_BLUE, width=16)

                # Draw inner rotating ring segments if active
                if inner_segs:
                    ica, isa = math.cos(f_state["inner_angle"]), math.sin(f_state["inner_angle"])
                    for p1, p2 in inner_segs:
                        x1 = arena_cx + p1[0] * ica - p1[1] * isa
                        y1 = arena_cy + p1[0] * isa + p1[1] * ica
                        x2 = arena_cx + p2[0] * ica - p2[1] * isa
                        y2 = arena_cy + p2[0] * isa + p2[1] * ica
                        draw.line([(x1, y1), (x2, y2)], fill=ACCENT_RED, width=13)

                # Draw obstacle pegs
                for px, py, pr in obstacle_pegs:
                    draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=ACCENT_YELLOW, outline=TEXT_WHITE, width=3)

                # Draw central spinner bar
                if spinner_body is not None:
                    sca, ssa = math.cos(f_state["spinner_angle"]), math.sin(f_state["spinner_angle"])
                    sx1, sy1 = arena_cx - spinner_len * sca, arena_cy - spinner_len * ssa
                    sx2, sy2 = arena_cx + spinner_len * sca, arena_cy + spinner_len * ssa
                    draw.line([(sx1, sy1), (sx2, sy2)], fill=ACCENT_YELLOW, width=14)

                # Draw marbles & motion trails
                for idx, code in enumerate(race_codes):
                    m_snap = f_state["marbles"][code]
                    mx, my = m_snap["x"], m_snap["y"]
                    if 0 <= mx <= VIDEO_WIDTH and 320 <= my <= 1600:
                        trails[code].append((mx, my))
                        trails[code] = trails[code][-6:]
                        t_col = ACCENT_PALETTE[idx % len(ACCENT_PALETTE)]
                        for t_pt in trails[code][:-1]:
                            draw.ellipse([t_pt[0] - 6, t_pt[1] - 6, t_pt[0] + 6, t_pt[1] + 6], fill=t_col)
                        icon = marbles[code]["icon"]
                        frame.paste(icon, (int(mx - marble_radius), int(my - marble_radius)), icon)

                # Live Escape / Leader Mini-Bar below arena
                draw.rounded_rectangle(
                    [64, 1385, VIDEO_WIDTH - 64, 1495],
                    radius=22,
                    fill=PANEL_BG_COLOR,
                    outline=PANEL_BORDER_COLOR,
                    width=3,
                )
                live_sorted = sorted(
                    race_codes,
                    key=lambda c: (
                        1 if f_state["marbles"][c]["escaped_at"] is not None else 0,
                        -(f_state["marbles"][c]["escape_rank"] or 99),
                        f_state["marbles"][c]["dist"],
                    ),
                    reverse=True,
                )
                draw.text(
                    (92, 1400),
                    f"LIVE LEADERS ({variant.replace('_', ' ').upper()}):",
                    font=get_font(26, bold=True),
                    fill=ACCENT_YELLOW,
                )
                lx = 92
                for rank_i, l_code in enumerate(live_sorted[:4], start=1):
                    l_name = COUNTRIES_META[l_code]["name"]
                    draw.text(
                        (lx, 1440),
                        f"#{rank_i} {l_name}",
                        font=get_font(30, bold=True),
                        fill=ACCENT_GREEN if rank_i == 1 else TEXT_WHITE,
                    )
                    lx += 235
            else:
                # Final ~5.5s: Leaderboard Card + India Rank + Cliffhanger
                frame = _draw_leaderboard_and_cliffhanger_card(
                    frame=frame,
                    winner_code=winner_code,
                    podium_codes=podium_codes,
                    standings=standings,
                    season=season,
                    race_num=race_num,
                    stage_name=stage_name,
                    cliffhanger_text=cliffhanger_text,
                    seed=seed,
                )

            # Apply standard Call It Arena HUD (header, 0-2s hook, 3-2-1 countdown, captions, outro)
            apply_standard_hud(
                frame=frame,
                t_sec=t_sec,
                total_duration=total_duration,
                series_badge=series_badge,
                hook_title="WHICH COUNTRY ESCAPES FIRST?",
                subtitle_line=f"Seed: {seed}  •  {len(race_codes)} Countries  •  {variant.replace('_', ' ').title()}",
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
