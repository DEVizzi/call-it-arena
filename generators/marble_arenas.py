"""
Call It Arena — Country Marble League Arena Catalogue (`generators/marble_arenas.py`).

14 physically distinct arena variants. Each builder declares its geometry through `ArenaKit`
(so collision + rendering stay in sync) and returns an `ArenaConfig` describing:
- how a marble FINISHES (`escape` = leaves a radius around the centre, `line` = crosses a finish line),
- where marbles spawn, gravity, optional custom force fields (gravity well), and HUD copy.

`intensity` is a single difficulty knob (mostly gravity / force scale). The league generator
re-simulates with a different intensity when a race would be too quick or too slow, so every
variant reliably produces a watchable 4–15s race without hand-tuning per seed.

Escape-style ("ring") arenas:
  single_ring, double_ring, spiral_labyrinth, funnel_obstacles (original 4),
  triple_ring, hexagon_spin, square_crusher, gear_ring, gravity_well
Finish-line ("drop race") arenas:
  plinko_drop, zigzag_ramps, windmill_drop, hourglass_rush, pendulum_gauntlet
"""

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pymunk

from common.branding import ACCENT_BLUE, ACCENT_GREEN, ACCENT_RED, ACCENT_YELLOW, TEXT_WHITE
from common.physics_kit import ArenaKit, STATIC_KEY, oscillating_driver, sliding_gate_driver

Vec = Tuple[float, float]

PURPLE = (170, 92, 255)
CYAN = (0, 208, 222)
PINK = (255, 92, 196)
LIME = (152, 228, 64)
STEEL = (120, 132, 170)

ARENA_CX, ARENA_CY = 540.0, 920.0
WALL_L, WALL_R = 64.0, 1016.0
CEILING_Y = 372.0
GAP_UP = -math.pi / 2  # rotate a body so its gap (local +x) starts pointing up
DROP_FINISH_Y = 1305.0


@dataclass
class ArenaConfig:
    variant: str
    label: str
    hook_title: str
    hook_spoken: str
    mode: str  # "escape" | "line"
    kit: ArenaKit
    spawn_points: List[Vec]
    gravity: Vec
    center: Vec = (ARENA_CX, ARENA_CY)
    escape_radius: float = 0.0
    finish_y: float = DROP_FINISH_Y
    spawn_velocity: Tuple[float, float, float, float] = (-260.0, 260.0, -240.0, 180.0)
    initial_velocities: Optional[List[Vec]] = None
    marble_elasticity: float = 0.93
    marble_friction: float = 0.12
    force_field: Optional[Callable[[pymunk.Body, Vec, float, float], None]] = None
    bg_style: str = "rings"
    finish_word: str = "ESCAPED"


# ---------------------------------------------------------------------------- spawn helpers
def _annulus_spawn(
    rng: random.Random,
    n: int,
    r: float,
    lo: float,
    hi: float,
    avoid: Optional[List[Tuple[float, float, float]]] = None,
    center: Vec = (ARENA_CX, ARENA_CY),
) -> List[Vec]:
    """Non-overlapping random spawn points in the annulus [lo, hi] around `center`."""
    pts: List[Vec] = []
    avoid = avoid or []
    for idx in range(n):
        placed = None
        for _ in range(400):
            ang = (2.0 * math.pi * idx) / n + rng.uniform(-0.6, 0.6)
            dist = rng.uniform(lo, hi)
            p = (center[0] + math.cos(ang) * dist, center[1] + math.sin(ang) * dist)
            if all(math.hypot(p[0] - q[0], p[1] - q[1]) >= 2 * r + 3 for q in pts) and all(
                math.hypot(p[0] - ax, p[1] - ay) >= ar + r + 4 for ax, ay, ar in avoid
            ):
                placed = p
                break
        if placed is None:  # dense fallback: deterministic spiral
            ang = idx * 2.399963
            dist = lo + (hi - lo) * ((idx + 0.5) / n)
            placed = (center[0] + math.cos(ang) * dist, center[1] + math.sin(ang) * dist)
        pts.append(placed)
    return pts


def _grid_spawn(rng: random.Random, n: int, r: float, x0: float, x1: float, rows_y: List[float]) -> List[Vec]:
    """Shuffled grid of start slots (left→right, row by row) with small jitter."""
    spacing = 2 * r + 8
    per_row = max(1, int((x1 - x0) // spacing))
    slots: List[Vec] = []
    for ry in rows_y:
        row_w = per_row * spacing
        start = x0 + ((x1 - x0) - row_w) / 2.0 + spacing / 2.0
        for i in range(per_row):
            slots.append((start + i * spacing + rng.uniform(-3, 3), ry + rng.uniform(-3, 3)))
    slots = slots[:max(n, 1)] if len(slots) >= n else slots
    rng.shuffle(slots)
    while len(slots) < n:  # should not happen with sane rows, but never crash
        slots.append((rng.uniform(x0 + r, x1 - r), rows_y[0] - 2 * r * (len(slots) - n + 2)))
    return slots[:n]


def _ring_params(rng: random.Random, intensity: float = 1.0) -> Tuple[float, float]:
    """Ring spin speed + escape gap; the gap narrows with low intensity to slow escapes down."""
    speed = rng.uniform(1.35, 2.05) * rng.choice([-1.0, 1.0])
    gap = rng.uniform(44.0, 58.0)
    return speed, max(16.0, gap * min(1.6, intensity))


def _ring_g(base: float, intensity: float) -> float:
    """Gravity for escape arenas only mildly follows intensity (gap size does most of the tuning)."""
    return base * (intensity ** 0.35)


def _open_gap(rng: random.Random, lo: float, hi: float, intensity: float) -> float:
    return max(110.0, rng.uniform(lo, hi) * min(1.5, intensity))


def _box_walls(kit: ArenaKit, top: float = CEILING_Y, bottom: float = 1345.0, color=STEEL) -> None:
    kit.seg((WALL_L, top), (WALL_L, bottom), 8.0, color, elasticity=0.6)
    kit.seg((WALL_R, top), (WALL_R, bottom), 8.0, color, elasticity=0.6)
    kit.seg((WALL_L, top), (WALL_R, top), 8.0, color, elasticity=0.6)


# ----------------------------------------------------------------------- ring (escape) arenas
def _single_ring(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    speed, gap = _ring_params(rng, intensity)
    kit.add_kinematic("outer", (ARENA_CX, ARENA_CY), angular_velocity=speed, angle=GAP_UP)
    kit.ring(415.0, gap, ACCENT_BLUE, "outer", thickness=12.0)
    kit.add_kinematic("spinner", (ARENA_CX, ARENA_CY), angular_velocity=-speed * 1.6)
    kit.seg((-135, 0), (135, 0), 10.0, ACCENT_YELLOW, "spinner", elasticity=0.98)
    return ArenaConfig(
        "single_ring", "Single Ring", "WHICH COUNTRY ESCAPES FIRST?",
        "Which country escapes the ring first?", "escape", kit,
        _annulus_spawn(rng, n, r, 165.0, 330.0), (0.0, _ring_g(510.0, intensity)), escape_radius=415.0,
        spawn_velocity=(-170.0, 170.0, -170.0, 120.0),
    )


def _double_ring(space, rng, n, r, intensity, pegs: bool = False) -> ArenaConfig:
    kit = ArenaKit(space)
    speed, gap = _ring_params(rng, intensity)
    kit.add_kinematic("outer", (ARENA_CX, ARENA_CY), angular_velocity=speed, angle=GAP_UP)
    kit.ring(415.0, gap, ACCENT_BLUE, "outer", thickness=12.0)
    kit.add_kinematic("inner", (ARENA_CX, ARENA_CY), angular_velocity=-speed * 1.25)
    kit.ring(255.0, gap + 10.0, ACCENT_RED, "inner", thickness=10.0, phase_offset_deg=180.0)
    avoid: List[Tuple[float, float, float]] = []
    if pegs:
        for i in range(6):
            ang = i * (2.0 * math.pi / 6)
            p = (ARENA_CX + math.cos(ang) * 155.0, ARENA_CY + math.sin(ang) * 155.0)
            kit.circle(p, 14.0, ACCENT_YELLOW)
            avoid.append((p[0], p[1], 14.0))
    name = "spiral_labyrinth" if pegs else "double_ring"
    label = "Spiral Labyrinth" if pegs else "Double Ring"
    return ArenaConfig(
        name, label, "WHICH COUNTRY ESCAPES FIRST?",
        "Which country escapes both rings first?", "escape", kit,
        _annulus_spawn(rng, n, r, 40.0, 205.0, avoid), (0.0, _ring_g(510.0, intensity)), escape_radius=415.0,
    )


def _funnel_obstacles(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    speed, gap = _ring_params(rng, intensity)
    kit.add_kinematic("outer", (ARENA_CX, ARENA_CY), angular_velocity=speed, angle=GAP_UP)
    kit.ring(415.0, gap, ACCENT_BLUE, "outer", thickness=12.0)
    avoid = []
    for i in range(8):
        ang = i * (2.0 * math.pi / 8)
        p = (ARENA_CX + math.cos(ang) * 220.0, ARENA_CY + math.sin(ang) * 220.0)
        kit.circle(p, 14.0, ACCENT_YELLOW)
        avoid.append((p[0], p[1], 14.0))
    kit.add_kinematic("spinner", (ARENA_CX, ARENA_CY), angular_velocity=-speed * 1.6)
    kit.seg((-135, 0), (135, 0), 10.0, ACCENT_YELLOW, "spinner", elasticity=0.98)
    return ArenaConfig(
        "funnel_obstacles", "Funnel Obstacles", "WHICH COUNTRY ESCAPES FIRST?",
        "Which country escapes the obstacle ring first?", "escape", kit,
        _annulus_spawn(rng, n, r, 160.0, 340.0, avoid), (0.0, _ring_g(510.0, intensity)), escape_radius=415.0,
        spawn_velocity=(-170.0, 170.0, -170.0, 120.0),
    )


def _triple_ring(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    speed, gap = _ring_params(rng, intensity)
    for key, rad, g, mult, col, phase in (
        ("outer", 415.0, gap, 1.0, CYAN, 0.0),
        ("middle", 312.0, gap + 6.0, -1.3, PINK, 120.0),
        ("inner", 208.0, gap + 14.0, 1.65, LIME, 240.0),
    ):
        kit.add_kinematic(key, (ARENA_CX, ARENA_CY), angular_velocity=speed * mult, angle=GAP_UP)
        kit.ring(rad, g, col, key, thickness=10.0, phase_offset_deg=phase)
    return ArenaConfig(
        "triple_ring", "Triple Ring Vortex", "WHO BREAKS 3 RINGS FIRST?",
        "Three spinning rings. Which country breaks out first?", "escape", kit,
        _annulus_spawn(rng, n, r, 20.0, 160.0), (0.0, _ring_g(540.0, intensity)), escape_radius=415.0,
    )


def _hexagon_spin(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    speed, _gap = _ring_params(rng, intensity)
    kit.add_kinematic("hex", (ARENA_CX, ARENA_CY), angular_velocity=speed * 0.75, angle=GAP_UP - math.pi / 6)
    kit.polygon_outline(6, 438.0, PURPLE, "hex", thickness=12.0, open_side=0, open_gap=_open_gap(rng, 150.0, 185.0, intensity))
    kit.add_kinematic("tri", (ARENA_CX, ARENA_CY), angular_velocity=-speed * 1.4)
    kit.polygon_outline(3, 128.0, ACCENT_YELLOW, "tri", thickness=10.0)
    return ArenaConfig(
        "hexagon_spin", "Hexagon Spin", "WHO ESCAPES THE HEXAGON?",
        "Which country escapes the spinning hexagon first?", "escape", kit,
        _annulus_spawn(rng, n, r, 175.0, 330.0), (0.0, _ring_g(520.0, intensity)), escape_radius=438.0,
    )


def _square_crusher(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    speed, _gap = _ring_params(rng, intensity)
    half = 320.0
    kit.add_kinematic("square", (ARENA_CX, ARENA_CY), angular_velocity=speed * 0.85, angle=math.pi / 2)
    kit.polygon_outline(4, half * math.sqrt(2.0), ACCENT_GREEN, "square", thickness=12.0,
                        phase_deg=45.0, open_side=1, open_gap=_open_gap(rng, 165.0, 200.0, intensity))
    kit.add_kinematic("cross", (ARENA_CX, ARENA_CY), angular_velocity=-speed * 1.7)
    kit.seg((-150, 0), (150, 0), 10.0, ACCENT_RED, "cross", elasticity=0.98)
    kit.seg((0, -150), (0, 150), 10.0, ACCENT_RED, "cross", elasticity=0.98)
    return ArenaConfig(
        "square_crusher", "Square Crusher", "WHO ESCAPES THE SQUARE?",
        "Which country escapes the square crusher first?", "escape", kit,
        _annulus_spawn(rng, n, r, 192.0, 272.0), (0.0, _ring_g(520.0, intensity)), escape_radius=half * math.sqrt(2.0),
    )


def _gear_ring(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    speed, gap = _ring_params(rng, intensity)
    kit.add_kinematic("gear", (ARENA_CX, ARENA_CY), angular_velocity=speed * 0.6, angle=GAP_UP)
    kit.ring(415.0, gap, ACCENT_YELLOW, "gear", thickness=12.0)
    teeth = 9
    span = 360.0 - gap
    for i in range(teeth):
        a = math.radians(gap / 2.0 + span * (i + 0.5) / teeth)
        kit.seg((math.cos(a) * 412.0, math.sin(a) * 412.0), (math.cos(a) * 362.0, math.sin(a) * 362.0),
                9.0, ACCENT_YELLOW, "gear")
    kit.circle((ARENA_CX, ARENA_CY), 70.0, PINK, elasticity=1.0)
    return ArenaConfig(
        "gear_ring", "Gear Ring", "WHICH COUNTRY ESCAPES THE GEAR?",
        "The gear ring scoops them up. Which country escapes first?", "escape", kit,
        _annulus_spawn(rng, n, r, 110.0, 300.0, [(ARENA_CX, ARENA_CY, 70.0)]),
        (0.0, _ring_g(520.0, intensity)), escape_radius=415.0, spawn_velocity=(-130.0, 130.0, -130.0, 60.0),
    )


def _gravity_well(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    speed, gap = _ring_params(rng, intensity)
    kit.add_kinematic("ring", (ARENA_CX, ARENA_CY), angular_velocity=speed * 0.55, angle=math.pi / 2)
    kit.ring(415.0, gap * 0.62, CYAN, "ring", thickness=12.0, gaps=2)
    kit.circle((ARENA_CX, ARENA_CY), 58.0, PURPLE, elasticity=1.22, outline=PINK)
    pull = 300.0 / max(0.25, intensity)  # higher intensity = weaker pull = faster escapes
    spawn = _annulus_spawn(rng, n, r, 130.0, 320.0, [(ARENA_CX, ARENA_CY, 58.0)])
    spin = rng.choice([-1.0, 1.0])
    vels: List[Vec] = []
    for (x, y) in spawn:
        dx, dy = x - ARENA_CX, y - ARENA_CY
        d = max(1.0, math.hypot(dx, dy))
        v_orb = rng.uniform(150.0, 230.0)  # launch speed independent of pull strength
        tx, ty = -dy / d * spin, dx / d * spin
        vr = rng.uniform(-90.0, 20.0)
        vels.append((tx * v_orb + dx / d * vr, ty * v_orb + dy / d * vr))

    def _field(body: pymunk.Body, gravity: Vec, damping: float, dt: float) -> None:
        dx, dy = ARENA_CX - body.position.x, ARENA_CY - body.position.y
        d = max(40.0, math.hypot(dx, dy))
        pymunk.Body.update_velocity(body, (dx / d * pull, dy / d * pull), damping, dt)

    return ArenaConfig(
        "gravity_well", "Gravity Well", "WHO ESCAPES THE GRAVITY WELL?",
        "Zero gravity, one black hole. Which country escapes its pull first?", "escape", kit,
        spawn, (0.0, 0.0), escape_radius=415.0, initial_velocities=vels, force_field=_field,
    )


# --------------------------------------------------------------------- drop-race (line) arenas
def _start_gate(kit: ArenaKit, y: float, x0: float = WALL_L, x1: float = WALL_R, open_at: float = 2.0) -> None:
    kit.add_kinematic("gate", (0.0, 0.0), driver=sliding_gate_driver(open_at, (1900.0, 0.0), stop_after=0.7))
    kit.seg((x0 + 10, y), (x1 - 10, y), 7.0, ACCENT_RED, "gate", elasticity=0.2, friction=0.6)


def _plinko_drop(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    _box_walls(kit)
    spinners = [(318.0 + rng.uniform(-25, 25), 820.0), (762.0 + rng.uniform(-25, 25), 1060.0)]
    for row, y in enumerate(range(580, 1251, 85)):
        offset = 50.0 if row % 2 else 0.0
        x = 115.0 + offset
        while x <= 965.0:
            if all(math.hypot(x - sx, y - sy) > 120.0 for sx, sy in spinners):
                kit.circle((x, float(y)), 10.0, ACCENT_YELLOW, elasticity=0.55, friction=0.2)
            x += 100.0
    for i, (sx, sy) in enumerate(spinners):
        key = f"spin{i}"
        kit.add_kinematic(key, (sx, sy), angular_velocity=(2.3 if i % 2 == 0 else -2.3) * rng.uniform(0.85, 1.15))
        kit.seg((-92, 0), (92, 0), 9.0, PINK, key, elasticity=0.7)
    _start_gate(kit, 528.0)
    return ArenaConfig(
        "plinko_drop", "Plinko Drop", "WHO DROPS THROUGH FIRST?",
        "Plinko drop! Which country reaches the bottom first?", "line", kit,
        _grid_spawn(rng, n, r, WALL_L + 12, WALL_R - 12, [490.0, 428.0]),
        (0.0, 420.0 * intensity), spawn_velocity=(-40.0, 40.0, 0.0, 0.0),
        marble_elasticity=0.5, marble_friction=0.2, bg_style="lanes", finish_word="FINISHED",
    )


def _zigzag_ramps(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    _box_walls(kit)
    drop = rng.uniform(205.0, 225.0)  # ~15° ramps
    ramp_end_l, ramp_end_r = 820.0, 260.0
    ramp0 = None
    for i in range(3):
        yt = 520.0 + i * 250.0
        if i % 2 == 0:
            a, b = (WALL_L, yt), (ramp_end_l, yt + drop)
        else:
            a, b = (WALL_R, yt), (ramp_end_r, yt + drop)
        kit.seg(a, b, 8.0, ACCENT_BLUE, elasticity=0.3, friction=0.3)
        if ramp0 is None:
            ramp0 = (a, b)
        # bump pegs sitting on the ramp surface make marbles hop and overtake
        for frac in sorted(rng.uniform(0.25, 0.92) for _ in range(3)):
            bx = a[0] + (b[0] - a[0]) * frac
            by = a[1] + (b[1] - a[1]) * frac - 9.0
            kit.circle((bx, by), 9.0, ACCENT_YELLOW, elasticity=0.6, outline=None)
    # vertical start gate holding the pack on the first ramp
    gate_x = 640.0
    (ax, ay), (bx_, by_) = ramp0
    gate_floor = ay + (gate_x - ax) / (bx_ - ax) * (by_ - ay)
    kit.add_kinematic("gate", (0.0, 0.0), driver=sliding_gate_driver(2.0, (0.0, -1600.0), stop_after=0.7))
    kit.seg((gate_x, CEILING_Y + 14), (gate_x, gate_floor - 4.0), 7.0, ACCENT_RED, "gate", elasticity=0.2)
    row1 = ay + (WALL_L + 12 + r - ax) / (bx_ - ax) * (by_ - ay) - r - 10.0
    return ArenaConfig(
        "zigzag_ramps", "Zigzag Ramps", "WHO ROLLS DOWN FIRST?",
        "Three steep ramps, one finish line. Which country rolls down first?", "line", kit,
        _grid_spawn(rng, n, r, WALL_L + 12, gate_x - 14, [row1, row1 - 2 * r - 6]),
        (0.0, 560.0 * intensity), spawn_velocity=(0.0, 30.0, 0.0, 0.0),
        marble_elasticity=0.35, marble_friction=0.3, bg_style="lanes", finish_word="FINISHED",
    )


def _windmill_drop(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    _box_walls(kit)
    layout = [(300.0, 650.0), (780.0, 650.0), (184.0, 870.0), (540.0, 870.0), (896.0, 870.0), (300.0, 1090.0), (780.0, 1090.0)]
    base = rng.uniform(1.6, 2.2) * rng.choice([-1.0, 1.0])
    for i, (x, y) in enumerate(layout):
        key = f"mill{i}"
        kit.add_kinematic(key, (x, y), angular_velocity=base * (1 if i % 2 == 0 else -1))
        col = CYAN if i % 2 == 0 else PINK
        kit.seg((-92, 0), (92, 0), 8.0, col, key, elasticity=0.75)
        kit.seg((0, -92), (0, 92), 8.0, col, key, elasticity=0.75)
    kit.seg((WALL_L, 1180.0), (468.0, 1268.0), 8.0, STEEL, elasticity=0.4)
    kit.seg((WALL_R, 1180.0), (612.0, 1268.0), 8.0, STEEL, elasticity=0.4)
    _start_gate(kit, 520.0)
    return ArenaConfig(
        "windmill_drop", "Windmill Drop", "WHO SURVIVES THE WINDMILLS?",
        "Seven spinning windmills. Which country drops through first?", "line", kit,
        _grid_spawn(rng, n, r, WALL_L + 12, WALL_R - 12, [482.0, 420.0]),
        (0.0, 400.0 * intensity), spawn_velocity=(-30.0, 30.0, 0.0, 0.0),
        marble_elasticity=0.55, marble_friction=0.15, bg_style="lanes", finish_word="FINISHED",
    )


def _hourglass_rush(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    _box_walls(kit)
    neck1 = rng.uniform(118.0, 132.0)
    kit.seg((WALL_L, 600.0), (540.0 - neck1 / 2, 830.0), 8.0, ACCENT_YELLOW, elasticity=0.4, friction=0.05)
    kit.seg((WALL_R, 600.0), (540.0 + neck1 / 2, 830.0), 8.0, ACCENT_YELLOW, elasticity=0.4, friction=0.05)
    kit.add_kinematic("agitator", (540.0, 905.0), angular_velocity=rng.choice([-3.4, 3.4]))
    kit.seg((-70, 0), (70, 0), 8.0, PINK, "agitator", elasticity=0.9)
    for (x, y) in ((250.0, 985.0), (830.0, 985.0), (420.0, 1060.0), (660.0, 1060.0)):
        kit.circle((x, y), 14.0, CYAN, elasticity=0.8)
    neck2 = rng.uniform(128.0, 145.0)
    kit.seg((WALL_L, 1110.0), (540.0 - neck2 / 2, 1265.0), 8.0, ACCENT_YELLOW, elasticity=0.4, friction=0.05)
    kit.seg((WALL_R, 1110.0), (540.0 + neck2 / 2, 1265.0), 8.0, ACCENT_YELLOW, elasticity=0.4, friction=0.05)
    return ArenaConfig(
        "hourglass_rush", "Hourglass Rush", "WHO SQUEEZES THROUGH FIRST?",
        "Two hourglass necks. Which country squeezes through first?", "line", kit,
        _grid_spawn(rng, n, r, WALL_L + 12, WALL_R - 12, [540.0, 478.0, 416.0]),
        (0.0, 480.0 * intensity), spawn_velocity=(-60.0, 60.0, 0.0, 0.0),
        marble_elasticity=0.5, marble_friction=0.05, bg_style="lanes", finish_word="FINISHED",
    )


def _pendulum_gauntlet(space, rng, n, r, intensity) -> ArenaConfig:
    kit = ArenaKit(space)
    _box_walls(kit)
    omega = rng.uniform(1.9, 2.5)
    for i, ly in enumerate((600.0, 820.0, 1040.0, 1260.0)):
        gx = 395.0 if i % 2 == 0 else 685.0
        gw = 128.0
        rise = 62.0 if i == 0 else 105.0  # steeper shelves below the start funnel keep the pace up
        kit.seg((WALL_L, ly - rise), (gx - gw / 2, ly), 8.0, ACCENT_BLUE, elasticity=0.35, friction=0.1)
        kit.seg((WALL_R, ly - rise), (gx + gw / 2, ly), 8.0, ACCENT_BLUE, elasticity=0.35, friction=0.1)
        if i == 0:
            continue  # level 0 is an open start funnel (keeps spawn slots clear)
        key = f"pend{i}"
        phase = rng.uniform(0.0, 2.0 * math.pi)
        amp = 0.78
        kit.add_kinematic(key, (gx, ly - 175.0), angle=amp * math.sin(phase),
                          driver=oscillating_driver(amp, omega, phase))
        kit.seg((0, 0), (0, 125), 5.0, STEEL, key, elasticity=0.6)
        kit.circle((0, 125), 22.0, ACCENT_RED, key, elasticity=0.95)
    return ArenaConfig(
        "pendulum_gauntlet", "Pendulum Gauntlet", "WHO DODGES THE PENDULUMS?",
        "Swinging pendulums guard every gap. Which country gets through first?", "line", kit,
        _grid_spawn(rng, n, r, WALL_L + 12, WALL_R - 12, [470.0, 410.0]),
        (0.0, 680.0 * intensity), spawn_velocity=(-50.0, 50.0, 0.0, 0.0),
        marble_elasticity=0.45, marble_friction=0.2, bg_style="lanes", finish_word="FINISHED",
    )


def _mega_gauntlet(space, rng, n, r, intensity) -> ArenaConfig:
    """
    GRAND FINAL ONLY — three stages in one screen:
      1) break out of a spinning ring (with a centre spinner),  2) survive the windmill field,
      3) squeeze through the hourglass neck to the finish line.
    """
    kit = ArenaKit(space)
    _box_walls(kit)
    rc = (ARENA_CX, 612.0)
    speed, gap = _ring_params(rng, intensity)
    kit.add_kinematic("mega_outer", rc, angular_velocity=speed * 0.9, angle=GAP_UP)
    kit.ring(222.0, gap * 0.85, (255, 210, 60), "mega_outer", thickness=11.0)
    kit.add_kinematic("mega_spinner", rc, angular_velocity=-speed * 1.5)
    kit.seg((-92, 0), (92, 0), 9.0, PINK, "mega_spinner", elasticity=0.95)
    base = rng.uniform(1.7, 2.3) * rng.choice([-1.0, 1.0])
    for i, (x, y) in enumerate([(200.0, 925.0), (540.0, 960.0), (880.0, 925.0), (360.0, 1085.0), (720.0, 1085.0)]):
        key = f"mega_mill{i}"
        kit.add_kinematic(key, (x, y), angular_velocity=base * (1 if i % 2 == 0 else -1))
        col = CYAN if i % 2 == 0 else LIME
        kit.seg((-78, 0), (78, 0), 8.0, col, key, elasticity=0.75)
        kit.seg((0, -78), (0, 78), 8.0, col, key, elasticity=0.75)
    neck = rng.uniform(128.0, 142.0)
    kit.seg((WALL_L, 1170.0), (540.0 - neck / 2, 1268.0), 8.0, (255, 210, 60), elasticity=0.4, friction=0.05)
    kit.seg((WALL_R, 1170.0), (540.0 + neck / 2, 1268.0), 8.0, (255, 210, 60), elasticity=0.4, friction=0.05)
    return ArenaConfig(
        "mega_gauntlet", "Mega Gauntlet", "GRAND FINAL: MEGA RACE!",
        "Ring, windmills, hourglass. Who wins the Grand Final?", "line", kit,
        _annulus_spawn(rng, n, r, 108.0, 178.0, center=rc), (0.0, 470.0 * intensity),
        spawn_velocity=(-150.0, 150.0, -150.0, 90.0),
        marble_elasticity=0.6, marble_friction=0.12, bg_style="lanes", finish_word="FINISHED",
    )


MEGA_VARIANT = "mega_gauntlet"

ARENA_BUILDERS: Dict[str, Callable[..., ArenaConfig]] = {
    "single_ring": _single_ring,
    "double_ring": lambda s, g, n, r, i: _double_ring(s, g, n, r, i, pegs=False),
    "spiral_labyrinth": lambda s, g, n, r, i: _double_ring(s, g, n, r, i, pegs=True),
    "funnel_obstacles": _funnel_obstacles,
    "triple_ring": _triple_ring,
    "hexagon_spin": _hexagon_spin,
    "square_crusher": _square_crusher,
    "gear_ring": _gear_ring,
    "gravity_well": _gravity_well,
    "plinko_drop": _plinko_drop,
    "zigzag_ramps": _zigzag_ramps,
    "windmill_drop": _windmill_drop,
    "hourglass_rush": _hourglass_rush,
    "pendulum_gauntlet": _pendulum_gauntlet,
}

# Daily rotation pool (the Grand Final mega arena is reserved for race 28 only)
ARENA_VARIANTS: List[str] = list(ARENA_BUILDERS.keys())
ARENA_BUILDERS[MEGA_VARIANT] = _mega_gauntlet


def build_arena(variant: str, space: pymunk.Space, rng: random.Random, n: int, marble_radius: float, intensity: float) -> ArenaConfig:
    if variant not in ARENA_BUILDERS:
        raise ValueError(f"Unknown arena variant: {variant}")
    return ARENA_BUILDERS[variant](space, rng, n, marble_radius, intensity)
