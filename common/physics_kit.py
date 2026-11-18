"""
Call It Arena — Shared Pymunk Arena Toolkit (`common/physics_kit.py`).

A tiny declarative layer on top of Pymunk used by every physics format:
- `ArenaKit` registers static / kinematic geometry (segments, circles, rings, polygons) ONCE and
  remembers how to draw it, so generators never duplicate collision + render code.
- Kinematic bodies may have a `driver(body, t)` callback (oscillating pendulums, sliding gates, ...).
- `ArenaKit.snapshot()` captures kinematic poses per frame; `render_dynamic()` replays them.
- `RotatingIconCache` pre-rotates circular flag icons so marbles visibly spin without per-frame cost.
- `attach_impulse_sfx()` wires a version-agnostic Pymunk post-solve handler that emits `boing` SFX.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import pymunk
from PIL import Image, ImageDraw

from .audio import SfxEvent
from .branding import TEXT_WHITE, VIDEO_FPS

Color = Tuple[int, int, int]
Vec = Tuple[float, float]
Pose = Tuple[float, float, float]

STATIC_KEY = "__static__"


@dataclass
class ShapeSpec:
    kind: str  # "seg" | "circle"
    body_key: str
    data: Tuple[Any, ...]
    color: Color
    width: int = 0
    outline: Optional[Color] = None


@dataclass
class KinematicEntry:
    body: pymunk.Body
    driver: Optional[Callable[[pymunk.Body, float], None]] = None


def rotate_point(p: Vec, angle: float) -> Vec:
    ca, sa = math.cos(angle), math.sin(angle)
    return (p[0] * ca - p[1] * sa, p[0] * sa + p[1] * ca)


class ArenaKit:
    """Declarative static/kinematic geometry registry bound to a `pymunk.Space`."""

    def __init__(self, space: pymunk.Space) -> None:
        self.space = space
        self.specs: List[ShapeSpec] = []
        self.kinematic: Dict[str, KinematicEntry] = {}

    # ------------------------------------------------------------------ bodies
    def add_kinematic(
        self,
        key: str,
        position: Vec,
        angular_velocity: float = 0.0,
        angle: float = 0.0,
        driver: Optional[Callable[[pymunk.Body, float], None]] = None,
    ) -> pymunk.Body:
        body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        body.position = position
        body.angle = angle
        body.angular_velocity = angular_velocity
        self.space.add(body)
        self.kinematic[key] = KinematicEntry(body=body, driver=driver)
        return body

    def _body(self, body_key: str) -> pymunk.Body:
        if body_key == STATIC_KEY:
            return self.space.static_body
        return self.kinematic[body_key].body

    # ------------------------------------------------------------------ shapes
    def seg(
        self,
        a: Vec,
        b: Vec,
        radius: float,
        color: Color,
        body_key: str = STATIC_KEY,
        elasticity: float = 0.94,
        friction: float = 0.15,
        draw_width: Optional[int] = None,
        collision_type: int = 2,
    ) -> pymunk.Segment:
        shape = pymunk.Segment(self._body(body_key), a, b, radius)
        shape.elasticity = elasticity
        shape.friction = friction
        shape.collision_type = collision_type
        self.space.add(shape)
        width = draw_width if draw_width is not None else max(4, int(radius * 1.5))
        self.specs.append(ShapeSpec("seg", body_key, (a, b), color, width))
        return shape

    def circle(
        self,
        center: Vec,
        radius: float,
        color: Color,
        body_key: str = STATIC_KEY,
        elasticity: float = 0.98,
        friction: float = 0.1,
        outline: Optional[Color] = TEXT_WHITE,
        collision_type: int = 2,
    ) -> pymunk.Circle:
        shape = pymunk.Circle(self._body(body_key), radius, center)
        shape.elasticity = elasticity
        shape.friction = friction
        shape.collision_type = collision_type
        self.space.add(shape)
        self.specs.append(ShapeSpec("circle", body_key, (center, radius), color, 3, outline))
        return shape

    def ring(
        self,
        radius: float,
        gap_deg: float,
        color: Color,
        body_key: str,
        thickness: float = 10.0,
        num_segments: int = 44,
        phase_offset_deg: float = 0.0,
        gaps: int = 1,
        draw_width: Optional[int] = None,
        elasticity: float = 0.94,
    ) -> List[Tuple[Vec, Vec]]:
        """Arc segments around the body's local origin leaving `gaps` evenly spaced openings."""
        local: List[Tuple[Vec, Vec]] = []
        per_gap_span = 360.0 / gaps
        arc_deg = per_gap_span - gap_deg
        segs_per_arc = max(4, num_segments // gaps)
        step = arc_deg / float(segs_per_arc)
        for g in range(gaps):
            start = phase_offset_deg + g * per_gap_span + gap_deg / 2.0
            for i in range(segs_per_arc):
                a1 = math.radians(start + i * step)
                a2 = math.radians(start + (i + 1) * step)
                p1 = (math.cos(a1) * radius, math.sin(a1) * radius)
                p2 = (math.cos(a2) * radius, math.sin(a2) * radius)
                self.seg(p1, p2, thickness, color, body_key, elasticity=elasticity,
                         draw_width=draw_width if draw_width is not None else int(thickness * 1.35))
                local.append((p1, p2))
        return local

    def polygon_outline(
        self,
        sides: int,
        radius: float,
        color: Color,
        body_key: str,
        thickness: float = 11.0,
        phase_deg: float = 0.0,
        open_side: Optional[int] = None,
        open_gap: float = 0.0,
    ) -> None:
        """Regular polygon outline; `open_side` gets a centred opening of `open_gap` px."""
        verts = [
            (math.cos(math.radians(phase_deg + i * 360.0 / sides)) * radius,
             math.sin(math.radians(phase_deg + i * 360.0 / sides)) * radius)
            for i in range(sides)
        ]
        for i in range(sides):
            a, b = verts[i], verts[(i + 1) % sides]
            if open_side is not None and i == open_side and open_gap > 0:
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                frac = max(0.0, min(0.48, (length - open_gap) / (2.0 * length)))
                m1 = (a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
                m2 = (b[0] + (a[0] - b[0]) * frac, b[1] + (a[1] - b[1]) * frac)
                if frac > 0.01:
                    self.seg(a, m1, thickness, color, body_key)
                    self.seg(m2, b, thickness, color, body_key)
            else:
                self.seg(a, b, thickness, color, body_key)

    # ---------------------------------------------------------------- timeline
    def drive(self, t: float) -> None:
        for entry in self.kinematic.values():
            if entry.driver is not None:
                entry.driver(entry.body, t)

    def snapshot(self) -> Dict[str, Pose]:
        return {
            k: (float(e.body.position.x), float(e.body.position.y), float(e.body.angle))
            for k, e in self.kinematic.items()
        }

    # --------------------------------------------------------------- rendering
    @staticmethod
    def _xf(p: Vec, pose: Pose) -> Vec:
        rx, ry = rotate_point(p, pose[2])
        return (pose[0] + rx, pose[1] + ry)

    def _draw_spec(self, draw: ImageDraw.ImageDraw, spec: ShapeSpec, pose: Pose) -> None:
        if spec.kind == "seg":
            a = self._xf(spec.data[0], pose)
            b = self._xf(spec.data[1], pose)
            draw.line([a, b], fill=spec.color, width=spec.width)
            cap = spec.width / 2.0
            for p in (a, b):
                draw.ellipse([p[0] - cap, p[1] - cap, p[0] + cap, p[1] + cap], fill=spec.color)
        elif spec.kind == "circle":
            c = self._xf(spec.data[0], pose)
            r = spec.data[1]
            draw.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=spec.color,
                         outline=spec.outline, width=spec.width if spec.outline else 0)

    def render_static(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        for spec in self.specs:
            if spec.body_key == STATIC_KEY:
                self._draw_spec(draw, spec, (0.0, 0.0, 0.0))

    def render_dynamic(self, draw: ImageDraw.ImageDraw, poses: Dict[str, Pose], offset: Vec = (0.0, 0.0)) -> None:
        for spec in self.specs:
            if spec.body_key != STATIC_KEY and spec.body_key in poses:
                px, py, pa = poses[spec.body_key]
                self._draw_spec(draw, spec, (px + offset[0], py + offset[1], pa))


def oscillating_driver(amplitude: float, omega: float, phase: float = 0.0) -> Callable[[pymunk.Body, float], None]:
    """Pendulum-style driver: angle(t) = A·sin(ωt + φ) realised through angular velocity."""

    def _drive(body: pymunk.Body, t: float) -> None:
        body.angular_velocity = amplitude * omega * math.cos(omega * t + phase)

    return _drive


def sliding_gate_driver(open_at: float, speed: Vec, stop_after: float = 0.6) -> Callable[[pymunk.Body, float], None]:
    """Start-gate driver: stationary until `open_at`, then slides with `speed` for `stop_after` seconds."""

    def _drive(body: pymunk.Body, t: float) -> None:
        if open_at <= t < open_at + stop_after:
            body.velocity = speed
        else:
            body.velocity = (0.0, 0.0)

    return _drive


class RotatingIconCache:
    """Pre-rotated RGBA icons (every `step_deg`) so spinning marbles are cheap to draw."""

    def __init__(self, icon: Image.Image, step_deg: int = 10) -> None:
        self.step = step_deg
        self.frames = [
            icon.rotate(-a, resample=Image.Resampling.BICUBIC) for a in range(0, 360, step_deg)
        ]

    def get(self, angle_rad: float) -> Image.Image:
        deg = math.degrees(angle_rad) % 360.0
        return self.frames[int(round(deg / self.step)) % len(self.frames)]


def attach_impulse_sfx(
    space: pymunk.Space,
    sfx_events: List[SfxEvent],
    clock: Callable[[], float],
    cutoff_sec: float,
    min_impulse: float = 140.0,
    min_frame_gap: int = 3,
) -> None:
    """Emits throttled `boing` SFX on strong collisions (supports Pymunk 6.x and 7.x APIs)."""
    last = {"frame": -min_frame_gap}

    def _post_solve(arbiter: pymunk.Arbiter, _space: pymunk.Space, _data: Any) -> None:
        t = clock()
        frame_idx = int(t * VIDEO_FPS)
        if frame_idx - last["frame"] < min_frame_gap or t >= cutoff_sec:
            return
        impulse = arbiter.total_impulse.length
        if impulse > min_impulse:
            last["frame"] = frame_idx
            gain = min(0.75, max(0.22, impulse / 650.0))
            pitch = 0.85 + 0.35 * ((frame_idx * 7) % 10) / 10.0
            sfx_events.append(SfxEvent(timestamp_sec=t, kind="boing", gain=gain, pitch_shift=pitch))

    if hasattr(space, "on_collision"):
        space.on_collision(None, None, post_solve=_post_solve)
    elif hasattr(space, "add_default_collision_handler"):
        handler = space.add_default_collision_handler()
        handler.post_solve = _post_solve


def draw_checkered_line(draw: ImageDraw.ImageDraw, x0: int, x1: int, y: int, cell: int = 18) -> None:
    """Racing-style checkered finish strip (2 rows) centred on `y`."""
    for row in range(2):
        for i, x in enumerate(range(x0, x1, cell)):
            col = TEXT_WHITE if (i + row) % 2 == 0 else (20, 20, 30)
            draw.rectangle([x, y - cell + row * cell, min(x1, x + cell), y + row * cell], fill=col)


@dataclass
class TimeRemap:
    """
    Maps video time → simulation time so the decisive physics moment happens right after the reveal:
      [0, intro)                : sim frozen at t=0 (hook / preview)
      [intro, countdown)        : sim 0 → pre_sim at constant speed `k`
      [countdown, reveal)       : slow motion (`slowmo`x) — viewers lock in their guess
      [reveal, ...)             : real time; decisive moment occurs `post` seconds after reveal
    """

    decisive_sim_t: float
    intro: float
    countdown: float
    reveal: float
    slowmo: float = 0.2
    post: float = 0.6

    @property
    def pre_sim(self) -> float:
        return max(0.1, self.decisive_sim_t - self.post - self.slowmo * (self.reveal - self.countdown))

    @property
    def speed(self) -> float:
        return self.pre_sim / max(0.1, self.countdown - self.intro)

    def __call__(self, video_t: float) -> float:
        if video_t < self.intro:
            return 0.0
        if video_t < self.countdown:
            return (video_t - self.intro) * self.speed
        if video_t < self.reveal:
            return self.pre_sim + (video_t - self.countdown) * self.slowmo
        return self.pre_sim + (self.reveal - self.countdown) * self.slowmo + (video_t - self.reveal)

    def inverse(self, sim_t: float) -> float:
        """Video time at which simulation time `sim_t` is shown (used to place collision SFX)."""
        if sim_t <= 0.0:
            return self.intro
        if sim_t < self.pre_sim:
            return self.intro + sim_t / self.speed
        slow_end = self.pre_sim + self.slowmo * (self.reveal - self.countdown)
        if sim_t < slow_end:
            return self.countdown + (sim_t - self.pre_sim) / self.slowmo
        return self.reveal + (sim_t - slow_end)


def sim_index(sim_t: float, sim_hz: float, n_records: int) -> int:
    """Nearest recorded physics sample for `sim_t`."""
    return max(0, min(n_records - 1, int(round(sim_t * sim_hz))))
