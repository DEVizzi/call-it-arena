"""
Call It Arena — Format 7: Plinko Bucket Drop (`generators/bucket_drop.py`).

"Which bucket will the ball land in?"
- One golden ball is dropped down a TALL Pymunk Plinko tower (pegs, bouncy bumpers, spinning
  paddles) with a scrolling camera; 7 numbered buckets wait at the bottom.
- 0–2s intro pans from the buckets up to the drop point so viewers can see their options.
- The fall is time-remapped (`TimeRemap`): the ball reaches the buckets in slow motion during the
  3-2-1 countdown and actually lands just after the reveal — no spoilers before the lock-in.
- Gravity is auto-tuned per seed so the drop fills the timeline at ~real speed.
"""

import hashlib
import math
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pymunk
from PIL import Image, ImageDraw

from common.audio import SfxEvent
from common.branding import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_COLOR,
    OUTPUT_DIR,
    PANEL_BG_COLOR,
    PANEL_BORDER_COLOR,
    TEXT_MUTED,
    TEXT_WHITE,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    get_font,
)
from common.physics_kit import ArenaKit, TimeRemap, attach_impulse_sfx, sim_index
from common.physics_video import produce_physics_video
from common.render import draw_centered_text

WORLD_H = 2750.0
VIEW_TOP = 380
VIEW_H = 960
WALL_L, WALL_R = 64.0, 1016.0
BALL_R = 24.0
N_BUCKETS = 7
DIVIDER_TOP = WORLD_H - 175.0
FLOOR_Y = WORLD_H - 22.0
SIM_HZ = 120
SUBSTEPS_PER_RECORD = 1

TOTAL_DURATION = 24.0
INTRO_SEC = 2.0
COUNTDOWN_START_SEC = 15.5
REVEAL_START_SEC = 18.5
OUTRO_START_SEC = 22.2
TARGET_DECISIVE = (COUNTDOWN_START_SEC - INTRO_SEC) + 0.6 + 0.2 * (REVEAL_START_SEC - COUNTDOWN_START_SEC)

BUCKET_COLORS = [ACCENT_RED, ACCENT_BLUE, ACCENT_GREEN, ACCENT_YELLOW, (170, 92, 255), (0, 208, 222), (255, 92, 196)]


def _seed_to_int(seed_str: str) -> int:
    return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:12], 16)


def _bucket_edges() -> List[float]:
    w = (WALL_R - WALL_L) / N_BUCKETS
    return [WALL_L + i * w for i in range(N_BUCKETS + 1)]


def _bucket_of(x: float) -> int:
    edges = _bucket_edges()
    for i in range(N_BUCKETS):
        if edges[i] <= x < edges[i + 1]:
            return i + 1
    return 1 if x < edges[0] else N_BUCKETS


def _build_board(space: pymunk.Space, rng: random.Random) -> ArenaKit:
    kit = ArenaKit(space)
    steel = (120, 132, 170)
    kit.seg((WALL_L, -200.0), (WALL_L, FLOOR_Y), 8.0, steel, elasticity=0.5)
    kit.seg((WALL_R, -200.0), (WALL_R, FLOOR_Y), 8.0, steel, elasticity=0.5)
    kit.seg((WALL_L, FLOOR_Y), (WALL_R, FLOOR_Y), 10.0, steel, elasticity=0.15, friction=0.9)
    for x in _bucket_edges()[1:-1]:
        kit.seg((x, DIVIDER_TOP), (x, FLOOR_Y), 7.0, TEXT_WHITE, elasticity=0.25)

    features: List[Tuple[float, float, float]] = []
    for i in range(3):  # spinning paddles
        fx, fy = rng.uniform(220.0, 860.0), 640.0 + i * 560.0 + rng.uniform(-80.0, 80.0)
        key = f"spin{i}"
        kit.add_kinematic(key, (fx, fy), angular_velocity=rng.choice([-1.0, 1.0]) * rng.uniform(1.8, 2.8))
        kit.seg((-88, 0), (88, 0), 8.0, (255, 92, 196), key, elasticity=0.7)
        features.append((fx, fy, 125.0))
    for i in range(4):  # bouncy bumpers
        fx, fy = rng.uniform(180.0, 900.0), 900.0 + i * 420.0 + rng.uniform(-60.0, 60.0)
        if all(math.hypot(fx - a, fy - b) > r + 70 for a, b, r in features):
            kit.circle((fx, fy), 26.0, (0, 208, 222), elasticity=1.05, outline=TEXT_WHITE)
            features.append((fx, fy, 70.0))

    row = 0
    y = 360.0
    while y < DIVIDER_TOP - 120.0:
        xs = []
        x = 120.0 + (56.0 if row % 2 else 0.0)
        while x <= 960.0:
            xs.append(x)
            x += 112.0
        # wall-hugging pegs close the straight drop lanes along the walls
        if xs[0] - WALL_L > 80.0:
            xs.insert(0, WALL_L + 22.0)
        if WALL_R - xs[-1] > 80.0:
            xs.append(WALL_R - 22.0)
        for x in xs:
            if all(math.hypot(x - a, y - b) > r for a, b, r in features):
                kit.circle((x, y), 9.0, ACCENT_YELLOW, elasticity=0.5, friction=0.25, outline=None)
        y += 92.0
        row += 1
    return kit


def simulate_drop(seed: str, gravity: float, attempt: int) -> Dict[str, Any]:
    rng = random.Random(_seed_to_int(f"{seed}|bucket|{attempt}"))
    space = pymunk.Space()
    space.gravity = (0.0, gravity)
    space.damping = 0.998
    kit = _build_board(space, rng)

    body = pymunk.Body(1.0, pymunk.moment_for_circle(1.0, 0, BALL_R))
    body.position = (rng.uniform(330.0, 750.0), 150.0)
    body.velocity = (rng.uniform(-35.0, 35.0), 0.0)
    shape = pymunk.Circle(body, BALL_R)
    shape.elasticity = 0.55
    shape.friction = 0.3
    shape.collision_type = 1
    space.add(body, shape)

    sfx: List[SfxEvent] = []
    clock = {"t": 0.0}
    attach_impulse_sfx(space, sfx, lambda: clock["t"], cutoff_sec=1e9, min_impulse=60.0, min_frame_gap=2)

    records: List[Tuple[float, float, float, Dict[str, Tuple[float, float, float]]]] = []
    dt = 1.0 / SIM_HZ
    landed_t: Optional[float] = None
    slow_since: Optional[float] = None
    t = 0.0
    max_t = 48.0
    while t < max_t:
        records.append((float(body.position.x), float(body.position.y), float(body.angle), kit.snapshot()))
        clock["t"] = t
        kit.drive(t)
        space.step(dt)
        t += dt
        if landed_t is None and body.position.y > DIVIDER_TOP + BALL_R:
            landed_t = t
            max_t = t + 6.0
        if landed_t is None:
            if body.velocity.length < 6.0:
                slow_since = slow_since if slow_since is not None else t
                if t - slow_since > 1.5:
                    break  # balanced on a peg — invalid run
            else:
                slow_since = None
    final_bucket = _bucket_of(float(body.position.x))
    bounces = sum(1 for e in sfx if landed_t is None or e.timestamp_sec <= landed_t)
    return {
        "kit": kit,
        "records": records,
        "landed_t": landed_t,
        "final_bucket": final_bucket,
        "sfx": sfx,
        "bounces": bounces,
        "gravity": gravity,
    }


def run_tuned_drop(seed: str) -> Dict[str, Any]:
    gravity = 320.0
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for attempt in range(14):
        sim = simulate_drop(seed, gravity, attempt)
        if sim["landed_t"] is None:
            continue
        err = abs(sim["landed_t"] - TARGET_DECISIVE) / TARGET_DECISIVE
        if best is None or err < best[0]:
            best = (err, sim)
        if err < 0.1:
            break
        gravity = max(80.0, min(1400.0, gravity * (sim["landed_t"] / TARGET_DECISIVE) ** 2))
    if best is None:
        raise RuntimeError("Bucket drop: ball never landed in any attempt")
    return best[1]


def generate_bucket_drop_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    wildcard: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    sim = run_tuned_drop(seed)
    kit: ArenaKit = sim["kit"]
    records = sim["records"]
    remap = TimeRemap(sim["landed_t"], INTRO_SEC, COUNTDOWN_START_SEC, REVEAL_START_SEC, slowmo=0.2, post=0.6)
    winner_bucket = sim["final_bucket"]
    decisive_video_t = remap.inverse(sim["landed_t"])

    sfx = [SfxEvent(remap.inverse(e.timestamp_sec), "tick", min(0.7, e.gain), e.pitch_shift) for e in sim["sfx"]]

    # Static world layer (pegs, walls, buckets)
    world = Image.new("RGB", (VIDEO_WIDTH, int(WORLD_H) + 40), BG_COLOR)
    wd = ImageDraw.Draw(world)
    for x in range(100, VIDEO_WIDTH - 60, 112):
        wd.line([(x, 0), (x, WORLD_H)], fill=(20, 23, 40), width=2)
    edges = _bucket_edges()
    for i in range(N_BUCKETS):
        col = BUCKET_COLORS[i]
        wd.rectangle([edges[i] + 8, DIVIDER_TOP + 10, edges[i + 1] - 8, FLOOR_Y - 10],
                     fill=tuple(int(c * 0.28) for c in col), outline=col, width=3)
        draw_centered_text(wd, str(i + 1), int((edges[i] + edges[i + 1]) / 2), int(DIVIDER_TOP + 40),
                           get_font(78, title=True), fill=col)
    kit.render_static(world)
    wd.rectangle([WALL_L + 10, 60, WALL_R - 10, 66], fill=ACCENT_RED)

    max_cam = WORLD_H - VIEW_H + 30

    def camera_for(video_t: float, ball_y: float) -> float:
        if video_t < INTRO_SEC:
            p = video_t / INTRO_SEC
            ease = p * p * (3 - 2 * p)
            return max_cam * (1.0 - ease)
        return max(0.0, min(max_cam, ball_y - 330.0))

    trail: List[Tuple[float, float]] = []
    cam_state = {"cam": max_cam}

    def draw_frame(t_sec: float) -> Image.Image:
        st = remap(t_sec)
        bx, by, ba, poses = records[sim_index(st, SIM_HZ, len(records))]
        target = camera_for(t_sec, by)
        cam = target if t_sec < INTRO_SEC + 0.05 else cam_state["cam"] + (target - cam_state["cam"]) * 0.35
        cam_state["cam"] = cam
        frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
        view = world.crop((0, int(cam), VIDEO_WIDTH, int(cam) + VIEW_H))
        vd = ImageDraw.Draw(view)
        kit.render_dynamic(vd, poses, offset=(0.0, -cam))

        landed = t_sec >= decisive_video_t + 0.35
        if landed:
            i = winner_bucket - 1
            pulse = 4 + int(3 * abs(math.sin(t_sec * 6)))
            vd.rectangle([edges[i] + 4, DIVIDER_TOP + 4 - cam, edges[i + 1] - 4, FLOOR_Y - 4 - cam],
                         outline=ACCENT_GREEN, width=pulse)

        sy = by - cam
        trail.append((bx, by))
        del trail[:-8]
        for k, (tx, ty) in enumerate(trail[:-1]):
            rr = 5 + k
            vd.ellipse([tx - rr, ty - cam - rr, tx + rr, ty - cam + rr], fill=(120, 90, 20))
        vd.ellipse([bx - BALL_R - 6, sy - BALL_R - 6, bx + BALL_R + 6, sy + BALL_R + 6], outline=(255, 220, 120), width=3)
        vd.ellipse([bx - BALL_R, sy - BALL_R, bx + BALL_R, sy + BALL_R], fill=(255, 196, 40), outline=TEXT_WHITE, width=3)
        hx, hy = bx + math.cos(ba) * BALL_R * 0.55, sy + math.sin(ba) * BALL_R * 0.55
        vd.ellipse([hx - 5, hy - 5, hx + 5, hy + 5], fill=TEXT_WHITE)
        frame.paste(view, (0, VIEW_TOP))

        draw = ImageDraw.Draw(frame)
        # depth meter
        draw.rounded_rectangle([1028, VIEW_TOP + 10, 1048, VIEW_TOP + VIEW_H - 10], radius=8, fill=PANEL_BG_COLOR)
        frac = max(0.0, min(1.0, by / WORLD_H))
        my = VIEW_TOP + 10 + frac * (VIEW_H - 20)
        draw.ellipse([1028, my - 10, 1048, my + 10], fill=(255, 196, 40))

        draw.rounded_rectangle([64, 1385, VIDEO_WIDTH - 64, 1495], radius=22,
                               fill=(18, 62, 42) if landed else PANEL_BG_COLOR,
                               outline=ACCENT_GREEN if landed else PANEL_BORDER_COLOR, width=3)
        if landed:
            draw_centered_text(draw, f"BUCKET {winner_bucket}!", VIDEO_WIDTH // 2, 1392, get_font(70, title=True),
                               fill=TEXT_WHITE)
            draw_centered_text(draw, f"{sim['bounces']} bounces  •  {sim['landed_t']:.1f}s fall", VIDEO_WIDTH // 2,
                               1462, get_font(26, bold=True), fill=ACCENT_YELLOW)
        else:
            draw_centered_text(draw, "PICK A BUCKET: 1 · 2 · 3 · 4 · 5 · 6 · 7", VIDEO_WIDTH // 2, 1402,
                               get_font(36, bold=True), fill=TEXT_WHITE)
            draw_centered_text(draw, f"DEPTH {int(frac * 100)}%  •  COMMENT YOUR NUMBER", VIDEO_WIDTH // 2, 1452,
                               get_font(26, bold=True), fill=TEXT_MUTED)
        return frame

    video_path = produce_physics_video(
        file_stem=f"bucket_drop_{seed}",
        output_dir=output_dir,
        total_duration=TOTAL_DURATION,
        countdown_start_sec=COUNTDOWN_START_SEC,
        reveal_start_sec=REVEAL_START_SEC,
        outro_start_sec=OUTRO_START_SEC,
        hook_spoken="Seven buckets, one golden ball. Which bucket will it land in?",
        reveal_spoken=f"Bucket {winner_bucket}! Did you call it?",
        sfx_events=sfx,
        draw_frame=draw_frame,
        series_badge="PLINKO BUCKET DROP",
        hook_title="WHICH BUCKET WILL IT HIT?",
        subtitle_line=f"7 Buckets  •  1 Ball  •  Seed: {seed}",
        footer_credit=f"Drop seed: {seed} • Physics: Pymunk 2D",
        wildcard=wildcard,
    )

    return {
        "format": "bucket_drop",
        "seed": seed,
        "video_path": video_path,
        "duration_sec": TOTAL_DURATION,
        "winner": f"Bucket {winner_bucket}",
        "winner_bucket": winner_bucket,
        "series_short": "Plinko Drop",
        "topic_short": "Plinko",
        "reveal_timestamp_sec": REVEAL_START_SEC,
        "comment_question": (
            f"It landed in Bucket {winner_bucket}! 🎯 Which bucket do you pick for the next drop? Comment 1–7 👇"
        ),
    }
