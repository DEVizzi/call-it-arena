"""
Call It Arena — Legacy v1 Marble League physics (`generators/legacy_v1_sim.py`).

Season 1 Races 1–6 were produced by the original v1 engine (commit a3baa6e, 4 ring arenas).
This module is a faithful, physics-identical extraction of that engine's simulation so the
end-of-season long-form recap can replay those races exactly. It never touches league state.

Do NOT "improve" this file: any change to construction order, constants or the RNG call
sequence changes the replayed outcome. Rendering helpers below are drawing-only.
"""

import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pymunk
from PIL import Image, ImageDraw

from common.audio import SfxEvent
from common.branding import ACCENT_BLUE, ACCENT_RED, ACCENT_YELLOW, TEXT_WHITE, VIDEO_FPS, VIDEO_WIDTH

LEGACY_VARIANTS = ("single_ring", "double_ring", "spiral_labyrinth", "funnel_obstacles")
LEGACY_REVEAL_SEC = 18.5
LEGACY_TOTAL_SEC = 24.0
ARENA_CX, ARENA_CY = VIDEO_WIDTH // 2, 920
OUTER_RADIUS = 415.0
INNER_RADIUS = 255.0
SPINNER_LEN = 135.0

Seg = Tuple[Tuple[float, float], Tuple[float, float]]


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
) -> List[Seg]:
    local_segs: List[Seg] = []
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


class LegacyArenaDrawer:
    """Draws the v1 classic ring arena with the same API shape as `ArenaKit` (static + dynamic layers)."""

    def __init__(self, outer_segs: List[Seg], inner_segs: List[Seg], pegs: List[Tuple[float, float, float]],
                 has_spinner: bool) -> None:
        self.outer_segs = outer_segs
        self.inner_segs = inner_segs
        self.pegs = pegs
        self.has_spinner = has_spinner

    def render_static(self, image: Image.Image, offset: Tuple[float, float] = (0.0, 0.0), **_kwargs: Any) -> None:
        draw = ImageDraw.Draw(image)
        ox, oy = offset
        for px, py, pr in self.pegs:
            draw.ellipse([px + ox - pr, py + oy - pr, px + ox + pr, py + oy + pr], fill=ACCENT_YELLOW,
                         outline=TEXT_WHITE, width=3)

    @staticmethod
    def _ring(draw: ImageDraw.ImageDraw, segs: List[Seg], ang: float, col, width: int,
              offset: Tuple[float, float]) -> None:
        ca, sa = math.cos(ang), math.sin(ang)
        cx, cy = ARENA_CX + offset[0], ARENA_CY + offset[1]
        pts = []
        for p1, p2 in segs:
            if not pts:
                pts.append((cx + p1[0] * ca - p1[1] * sa, cy + p1[0] * sa + p1[1] * ca))
            pts.append((cx + p2[0] * ca - p2[1] * sa, cy + p2[0] * sa + p2[1] * ca))
        draw.line(pts, fill=col, width=width, joint="curve")
        for end in (pts[0], pts[-1]):
            r = width / 2.0
            draw.ellipse([end[0] - r, end[1] - r, end[0] + r, end[1] + r], fill=col)

    def render_dynamic(self, draw: ImageDraw.ImageDraw, poses: Dict[str, Tuple[float, float, float]],
                       offset: Tuple[float, float] = (0.0, 0.0), **_kw: Any) -> None:
        self._ring(draw, self.outer_segs, poses["outer"][2], ACCENT_BLUE, 16, offset)
        if self.inner_segs:
            self._ring(draw, self.inner_segs, poses["inner"][2], ACCENT_RED, 13, offset)
        if self.has_spinner:
            sang = poses["spinner"][2]
            sca, ssa = math.cos(sang), math.sin(sang)
            cx, cy = ARENA_CX + offset[0], ARENA_CY + offset[1]
            draw.line(
                [(cx - SPINNER_LEN * sca, cy - SPINNER_LEN * ssa), (cx + SPINNER_LEN * sca, cy + SPINNER_LEN * ssa)],
                fill=ACCENT_YELLOW,
                width=14,
            )


@dataclass
class LegacyRace:
    race_codes: List[str]
    marble_radius: float
    frames: List[Dict[str, Any]]
    finish_order: List[str]
    finish_times: Dict[str, float]
    full_finish_order: List[str]
    drawer: LegacyArenaDrawer
    sfx_events: List[SfxEvent] = field(default_factory=list)


def simulate_legacy_v1(
    seed: str,
    variant: str,
    active_codes: List[str],
    stage_name: str,
    wildcard_code: Optional[str],
) -> LegacyRace:
    """Re-runs the exact v1 race selection + physics for `seed` (deterministic on the CI platform)."""
    rng = random.Random(_seed_to_int(seed))
    if stage_name == "Group Stage":
        ball_count = rng.choice([10, 12, 14, 16])
        priority = ["IN", "US", "BR", "ID"]
        if wildcard_code and wildcard_code in active_codes and wildcard_code not in priority:
            priority.insert(0, wildcard_code)
        remaining = [c for c in active_codes if c not in priority]
        rng.shuffle(remaining)
        race_codes = (priority + remaining)[:ball_count]
    else:
        race_codes = list(active_codes)

    gravity_y = rng.uniform(440.0, 580.0)
    ring_speed_rad = rng.uniform(1.35, 2.05) * rng.choice([-1.0, 1.0])
    gap_deg = rng.uniform(44.0, 58.0)
    marble_radius = 30 if len(race_codes) <= 12 else 27

    space = pymunk.Space()
    space.gravity = (0.0, gravity_y)
    space.damping = 0.996

    outer_ring_body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
    outer_ring_body.position = (ARENA_CX, ARENA_CY)
    outer_ring_body.angular_velocity = ring_speed_rad
    space.add(outer_ring_body)
    outer_segs = _add_ring_segments(space, outer_ring_body, OUTER_RADIUS, gap_deg=gap_deg, thickness=12.0)

    inner_ring_body: Optional[pymunk.Body] = None
    inner_segs: List[Seg] = []
    if variant in ("double_ring", "spiral_labyrinth"):
        inner_ring_body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        inner_ring_body.position = (ARENA_CX, ARENA_CY)
        inner_ring_body.angular_velocity = -ring_speed_rad * 1.25
        space.add(inner_ring_body)
        inner_segs = _add_ring_segments(
            space, inner_ring_body, INNER_RADIUS, gap_deg=gap_deg + 10.0, thickness=10.0, phase_offset_deg=180.0
        )

    obstacle_pegs: List[Tuple[float, float, float]] = []
    if variant in ("spiral_labyrinth", "funnel_obstacles"):
        peg_ring_r = 155.0 if variant == "spiral_labyrinth" else 220.0
        num_pegs = 6 if variant == "spiral_labyrinth" else 8
        for i in range(num_pegs):
            ang = i * (2.0 * math.pi / num_pegs)
            px = ARENA_CX + math.cos(ang) * peg_ring_r
            py = ARENA_CY + math.sin(ang) * peg_ring_r
            peg_shape = pymunk.Circle(space.static_body, 14.0, (px, py))
            peg_shape.elasticity = 0.98
            peg_shape.friction = 0.1
            peg_shape.collision_type = 2
            space.add(peg_shape)
            obstacle_pegs.append((px, py, 14.0))

    spinner_body: Optional[pymunk.Body] = None
    if variant in ("single_ring", "funnel_obstacles"):
        spinner_body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        spinner_body.position = (ARENA_CX, ARENA_CY)
        spinner_body.angular_velocity = -ring_speed_rad * 1.6
        space.add(spinner_body)
        sp_seg = pymunk.Segment(spinner_body, (-SPINNER_LEN, 0), (SPINNER_LEN, 0), 10.0)
        sp_seg.elasticity = 0.98
        sp_seg.collision_type = 2
        space.add(sp_seg)

    bodies: Dict[str, pymunk.Body] = {}
    spawn_r_limit = 160.0 if inner_ring_body else 240.0
    for idx, code in enumerate(race_codes):
        mass = 1.2
        moment = pymunk.moment_for_circle(mass, 0, marble_radius)
        body = pymunk.Body(mass, moment)
        ang = (2.0 * math.pi * idx) / len(race_codes) + rng.uniform(-0.15, 0.15)
        dist = rng.uniform(42.0, spawn_r_limit)
        body.position = (ARENA_CX + math.cos(ang) * dist, ARENA_CY + math.sin(ang) * dist)
        body.velocity = (rng.uniform(-260.0, 260.0), rng.uniform(-240.0, 180.0))
        shape = pymunk.Circle(body, marble_radius)
        shape.elasticity = 0.93
        shape.friction = 0.12
        shape.collision_type = 1
        space.add(body, shape)
        bodies[code] = body

    sfx_events: List[SfxEvent] = []
    state = {"t": 0.0, "last": -3}

    def _on_post_solve(arbiter: pymunk.Arbiter, _space: pymunk.Space, _data: Any) -> None:
        frame_idx = int(state["t"] * VIDEO_FPS)
        if frame_idx - state["last"] < 3 or state["t"] >= LEGACY_REVEAL_SEC:
            return
        impulse = arbiter.total_impulse.length
        if impulse > 140.0:
            state["last"] = frame_idx
            gain = min(0.75, max(0.22, impulse / 650.0))
            pitch = 0.85 + 0.35 * ((frame_idx * 7) % 10) / 10.0
            sfx_events.append(SfxEvent(timestamp_sec=state["t"], kind="boing", gain=gain, pitch_shift=pitch))

    if hasattr(space, "on_collision"):
        space.on_collision(None, None, post_solve=_on_post_solve)
    elif hasattr(space, "add_default_collision_handler"):
        handler = space.add_default_collision_handler()
        handler.post_solve = _on_post_solve

    frames: List[Dict[str, Any]] = []
    finish_order: List[str] = []
    finish_times: Dict[str, float] = {}
    substeps = 4
    dt = 1.0 / (VIDEO_FPS * substeps)
    n_frames = int(LEGACY_REVEAL_SEC * VIDEO_FPS)
    for f_idx in range(n_frames):
        t = f_idx / float(VIDEO_FPS)
        state["t"] = t
        for _ in range(substeps):
            space.step(dt)
        for code, body in bodies.items():
            if code in finish_times:
                continue
            if math.hypot(body.position.x - ARENA_CX, body.position.y - ARENA_CY) > OUTER_RADIUS + marble_radius * 0.9:
                finish_times[code] = t
                finish_order.append(code)
                sfx_events.append(SfxEvent(timestamp_sec=t, kind="tick", gain=0.80, pitch_shift=1.15))
        frames.append(
            {
                "kin": {
                    "outer": (float(ARENA_CX), float(ARENA_CY), float(outer_ring_body.angle)),
                    "inner": (float(ARENA_CX), float(ARENA_CY), float(inner_ring_body.angle) if inner_ring_body else 0.0),
                    "spinner": (float(ARENA_CX), float(ARENA_CY), float(spinner_body.angle) if spinner_body else 0.0),
                },
                "marbles": {c: (float(b.position.x), float(b.position.y), float(b.angle)) for c, b in bodies.items()},
                "finished": len(finish_order),
            }
        )

    last = frames[-1]["marbles"]
    un_escaped = [c for c in race_codes if c not in finish_order]
    un_escaped.sort(key=lambda c: math.hypot(last[c][0] - ARENA_CX, last[c][1] - ARENA_CY), reverse=True)
    drawer = LegacyArenaDrawer(outer_segs, inner_segs, obstacle_pegs, spinner_body is not None)
    return LegacyRace(
        race_codes=race_codes,
        marble_radius=float(marble_radius),
        frames=frames,
        finish_order=finish_order,
        finish_times=finish_times,
        full_finish_order=finish_order + un_escaped,
        drawer=drawer,
        sfx_events=sfx_events,
    )
