"""
Call It Arena — Format 9: Downhill Derby (`generators/downhill_derby.py`).

"Which car reaches the finish first?"
- Four gravity-powered Pymunk cars (picked from 5 designs: Monster Truck, Racer, Tank, Buggy, Mini)
  with real wheel joints roll down a long procedurally generated bumpy hill. No engines — pure physics,
  so wheel size, mass and wheelbase decide who flies, who flips and who wins.
- Cars are "ghosts" (no car-to-car collisions) so every start is fair, and each one is drawn in its own
  coloured lane so they never overlap on screen.
- A 3.5s "Meet the cars" lineup (wheel size, weight, wheelbase) opens the video so viewers can pick.
- Scrolling camera follows the top two cars; a mini track map shows the whole field.
- The first finish is time-remapped to land right after the reveal (slow-mo during the countdown).
- Gravity is auto-tuned per seed so the race fills the timeline.
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
from common.physics_kit import TimeRemap, attach_impulse_sfx, draw_checkered_line, rotate_point, sim_index
from common.physics_video import produce_physics_video
from common.render import draw_centered_text

SIM_HZ = 120
X_START = 0.0
X_FINISH = 4700.0
X_END = 8200.0
VIEW_TOP, VIEW_BOTTOM = 380, 1340
LABELS = ["A", "B", "C", "D"]
CAR_COLORS = [ACCENT_RED, ACCENT_BLUE, ACCENT_GREEN, ACCENT_YELLOW]
LANE_DY = 62.0  # vertical spacing between the visual lanes

TOTAL_DURATION = 24.0
INTRO_SEC = 3.5  # "Meet the cars" lineup before the start
COUNTDOWN_START_SEC = 15.5
REVEAL_START_SEC = 18.5
OUTRO_START_SEC = 22.2
TARGET_DECISIVE = (COUNTDOWN_START_SEC - INTRO_SEC) + 0.6 + 0.2 * (REVEAL_START_SEC - COUNTDOWN_START_SEC)

CAR_DESIGNS: Dict[str, Dict[str, float]] = {
    "Monster Truck": {"w": 120.0, "h": 34.0, "m": 2.0, "r": 34.0, "wm": 0.8, "ax": 48.0, "ay": 24.0},
    "Racer": {"w": 150.0, "h": 22.0, "m": 1.4, "r": 19.0, "wm": 0.4, "ax": 60.0, "ay": 14.0},
    "Tank": {"w": 110.0, "h": 46.0, "m": 4.0, "r": 24.0, "wm": 1.0, "ax": 42.0, "ay": 24.0},
    "Buggy": {"w": 130.0, "h": 28.0, "m": 1.6, "r": 27.0, "wm": 0.6, "ax": 58.0, "ay": 22.0},
    "Mini": {"w": 84.0, "h": 26.0, "m": 1.0, "r": 22.0, "wm": 0.5, "ax": 30.0, "ay": 18.0},
}


def _seed_to_int(seed_str: str) -> int:
    return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:12], 16)


def pick_cars(seed: str) -> List[str]:
    rng = random.Random(_seed_to_int(f"{seed}|cars"))
    names = list(CAR_DESIGNS.keys())
    rng.shuffle(names)
    return names[:4]


def build_terrain(seed: str) -> List[Tuple[float, float]]:
    """Downhill polyline: flat-ish start pad, rolling bumps, a few kickers, flat run-out after the finish."""
    rng = random.Random(_seed_to_int(f"{seed}|terrain"))
    waves = [(rng.uniform(10.0, 18.0), rng.uniform(320.0, 480.0), rng.uniform(0, 6.28)),
             (rng.uniform(3.0, 6.0), rng.uniform(120.0, 170.0), rng.uniform(0, 6.28))]
    kickers = sorted(rng.uniform(1500.0, X_FINISH - 500.0) for _ in range(3))
    slope = rng.uniform(0.2, 0.25)
    pts: List[Tuple[float, float]] = []
    x = -400.0
    y_finish = None
    while x <= X_END:
        if x < 250.0:
            y = 0.06 * (x - 250.0)
        elif x <= X_FINISH + 150.0:
            d = x - 250.0
            ramp_in = min(1.0, d / 900.0)  # bumps ease in once cars have momentum
            y = slope * d + ramp_in * sum(a * math.sin(2 * math.pi * x / wl + ph) for a, wl, ph in waves)
            for k in kickers:
                if k - 120.0 <= x <= k:
                    y -= 40.0 * (x - (k - 120.0)) / 120.0  # short up-ramp → jump
            y_finish = y
        else:
            y = (y_finish or 0.0) - 0.07 * (x - X_FINISH - 150.0)  # gentle uphill run-out brakes the cars
        pts.append((x, y))
        x += 30.0
    return pts


def _terrain_y(pts: List[Tuple[float, float]], x: float) -> float:
    i = max(0, min(len(pts) - 2, int((x - pts[0][0]) // 30.0)))
    (x0, y0), (x1, y1) = pts[i], pts[i + 1]
    f = (x - x0) / (x1 - x0)
    return y0 + (y1 - y0) * f


def simulate_derby(seed: str, cars: List[str], terrain: List[Tuple[float, float]], g: float, attempt: int) -> Dict[str, Any]:
    rng = random.Random(_seed_to_int(f"{seed}|derby|{attempt}"))
    space = pymunk.Space()
    space.gravity = (0.0, g)
    space.iterations = 20
    for (a, b) in zip(terrain[:-1], terrain[1:]):
        seg = pymunk.Segment(space.static_body, a, b, 6.0)
        seg.friction = 1.0
        seg.elasticity = 0.15
        space.add(seg)

    car_filter = pymunk.ShapeFilter(group=1)  # ghost cars: no car-to-car contact
    built: List[Dict[str, Any]] = []
    for ci, name in enumerate(cars):
        d = CAR_DESIGNS[name]
        start_x = 40.0 + rng.uniform(-2.0, 2.0)
        ground = _terrain_y(terrain, start_x)
        cy = ground - d["ay"] - d["r"] - 8.0
        chassis = pymunk.Body(d["m"], pymunk.moment_for_box(d["m"], (d["w"], d["h"])))
        chassis.position = (start_x, cy)
        cs = pymunk.Poly.create_box(chassis, (d["w"], d["h"]), radius=2.0)
        cs.friction = 0.6
        cs.filter = car_filter
        cs.collision_type = 1
        space.add(chassis, cs)
        wheels = []
        for side in (-1.0, 1.0):
            wb = pymunk.Body(d["wm"], pymunk.moment_for_circle(d["wm"], 0, d["r"]))
            wb.position = (start_x + side * d["ax"], cy + d["ay"])
            ws = pymunk.Circle(wb, d["r"])
            ws.friction = 1.3
            ws.elasticity = 0.2
            ws.filter = car_filter
            ws.collision_type = 1
            space.add(wb, ws)
            joint = pymunk.PivotJoint(chassis, wb, wb.position)
            joint.collide_bodies = False
            space.add(joint)
            wheels.append(wb)
        built.append({"name": name, "chassis": chassis, "wheels": wheels, "finish_t": None})

    sfx: List[SfxEvent] = []
    clock = {"t": 0.0}
    attach_impulse_sfx(space, sfx, lambda: clock["t"], cutoff_sec=1e9, min_impulse=320.0, min_frame_gap=4)

    dt = 1.0 / SIM_HZ
    t = 0.0
    max_t = 44.0
    order: List[int] = []
    records: List[List[Tuple[float, ...]]] = []
    while t < max_t:
        records.append([
            (c["chassis"].position.x, c["chassis"].position.y, c["chassis"].angle,
             c["wheels"][0].position.x, c["wheels"][0].position.y, c["wheels"][0].angle,
             c["wheels"][1].position.x, c["wheels"][1].position.y, c["wheels"][1].angle)
            for c in built
        ])
        clock["t"] = t
        space.step(dt)
        t += dt
        for ci, c in enumerate(built):
            if c["finish_t"] is None and c["chassis"].position.x >= X_FINISH:
                c["finish_t"] = t
                order.append(ci)
                sfx.append(SfxEvent(timestamp_sec=t, kind="tick", gain=0.85, pitch_shift=1.2))
                if len(order) == 1:
                    max_t = t + 6.0
    rest = [ci for ci in range(len(built)) if ci not in order]
    rest.sort(key=lambda ci: built[ci]["chassis"].position.x, reverse=True)
    return {"cars": built, "records": records, "order": order, "full_order": order + rest, "sfx": sfx, "g": g}


def run_tuned_derby(seed: str, cars: List[str], terrain: List[Tuple[float, float]]) -> Dict[str, Any]:
    g = 520.0
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for attempt in range(14):
        sim = simulate_derby(seed, cars, terrain, g, attempt)
        if sim["order"]:
            t1 = sim["cars"][sim["order"][0]]["finish_t"]
            err = abs(t1 - TARGET_DECISIVE) / TARGET_DECISIVE
            if best is None or err < best[0]:
                best = (err, sim)
            if err < 0.1:
                break
            g = max(150.0, min(2500.0, g * (t1 / TARGET_DECISIVE) ** 2))
        else:
            g = min(2500.0, g * 1.6)
    if best is None:
        raise RuntimeError("Downhill derby: no car finished in any attempt")
    return best[1]


def generate_downhill_derby_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    wildcard: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    cars = pick_cars(seed)
    terrain = build_terrain(seed)
    sim = run_tuned_derby(seed, cars, terrain)
    records = sim["records"]
    winner_idx = sim["order"][0]
    t1 = sim["cars"][winner_idx]["finish_t"]
    remap = TimeRemap(t1, INTRO_SEC, COUNTDOWN_START_SEC, REVEAL_START_SEC, slowmo=0.2, post=0.6)
    finish_video_t = {ci: remap.inverse(sim["cars"][ci]["finish_t"]) for ci in sim["order"]}
    winner_name = f"Car {LABELS[winner_idx]} ({cars[winner_idx]})"
    sfx = [SfxEvent(remap.inverse(e.timestamp_sec), e.kind, min(0.7, e.gain), e.pitch_shift) for e in sim["sfx"]]

    y_finish = _terrain_y(terrain, X_FINISH)
    cam = {"x": None, "y": None}
    view_cx, view_cy = VIDEO_WIDTH / 2.0, 900.0
    n_cars = len(cars)
    lane_center = (n_cars - 1) / 2.0

    def lane_dy(ci: int) -> float:
        """Each car rides its own visual lane (identical physics hill), stacked so cars never overlap."""
        return -ci * LANE_DY

    def camera_target(rec: List[Tuple[float, ...]]) -> Tuple[float, float]:
        xs = sorted(((r[0], r[1]) for r in rec), reverse=True)[:2]
        return (sum(p[0] for p in xs) / len(xs) + 120.0,
                sum(p[1] for p in xs) / len(xs) - lane_center * LANE_DY)

    def draw_car(vd: ImageDraw.ImageDraw, ci: int, cx: float, cy: float, ca: float,
                 wheels: List[Tuple[float, float, float]], scale: float = 1.0) -> None:
        d = CAR_DESIGNS[cars[ci]]
        col = CAR_COLORS[ci]
        hw, hh = d["w"] / 2.0 * scale, d["h"] / 2.0 * scale
        pts = [(cx + p[0], cy + p[1]) for p in (rotate_point(q, ca) for q in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)))]
        vd.polygon(pts, fill=col, outline=TEXT_WHITE)
        cab = [(cx + p[0], cy + p[1]) for p in (rotate_point(q, ca) for q in (
            (-hw * 0.4, -hh), (hw * 0.25, -hh), (hw * 0.1, -hh - 16 * scale), (-hw * 0.3, -hh - 16 * scale)))]
        vd.polygon(cab, fill=tuple(min(255, int(c * 0.6 + 90)) for c in col), outline=TEXT_WHITE)
        draw_centered_text(vd, LABELS[ci], int(cx), int(cy - 12 * scale), get_font(int(22 * scale), bold=True), fill=BG_COLOR)
        rr = d["r"] * scale
        for wx, wy, wa in wheels:
            vd.ellipse([wx - rr, wy - rr, wx + rr, wy + rr], fill=(30, 30, 36), outline=col, width=4)
            vd.line([(wx, wy), (wx + math.cos(wa) * rr * 0.85, wy + math.sin(wa) * rr * 0.85)], fill=TEXT_WHITE, width=3)

    def car_traits(name: str) -> str:
        d = CAR_DESIGNS[name]
        wheels = "Small" if d["r"] < 22 else ("Medium" if d["r"] < 30 else "Giant")
        total = d["m"] + 2 * d["wm"]
        weight = "Light" if total < 2.4 else ("Medium" if total < 3.5 else "Heavy")
        base = "Short" if d["ax"] < 45 else "Long"
        return f"{wheels} wheels  •  {weight}  •  {base} wheelbase"

    def draw_lineup(frame: Image.Image) -> None:
        """Pre-race 'Meet the cars' card so viewers can pick before the start."""
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle([54, VIEW_TOP + 20, VIDEO_WIDTH - 54, VIEW_BOTTOM - 20], radius=32,
                               fill=(18, 20, 36), outline=ACCENT_YELLOW, width=5)
        draw_centered_text(draw, "MEET THE CARS", VIDEO_WIDTH // 2, VIEW_TOP + 44, get_font(64, title=True), fill=ACCENT_YELLOW)
        draw_centered_text(draw, "No engines. Only gravity, wheels & weight.", VIDEO_WIDTH // 2, VIEW_TOP + 118,
                           get_font(28, bold=True), fill=TEXT_MUTED)
        row_h = 190
        for ci, name in enumerate(cars):
            y0 = VIEW_TOP + 170 + ci * row_h
            col = CAR_COLORS[ci]
            draw.rounded_rectangle([90, y0, VIDEO_WIDTH - 90, y0 + row_h - 20], radius=20, fill=(26, 30, 52), outline=col, width=3)
            d = CAR_DESIGNS[name]
            cx, cy = 230.0, y0 + 95.0
            wy = cy + d["ay"]
            draw_car(draw, ci, cx, cy, 0.0, [(cx - d["ax"], wy, 0.0), (cx + d["ax"], wy, 0.0)])
            draw.text((400, y0 + 30), f"CAR {LABELS[ci]}: {name.upper()}", font=get_font(44, title=True), fill=TEXT_WHITE)
            draw.text((400, y0 + 92), car_traits(name), font=get_font(26, bold=True), fill=col)

    def draw_frame(t_sec: float) -> Image.Image:
        rec = records[sim_index(remap(t_sec), SIM_HZ, len(records))]
        tx, ty = camera_target(rec)
        if cam["x"] is None:
            cam["x"], cam["y"] = tx, ty
        else:
            cam["x"] += (tx - cam["x"]) * 0.25
            cam["y"] += (ty - cam["y"]) * 0.25
        ox, oy = view_cx - cam["x"], view_cy - cam["y"]

        frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
        view = Image.new("RGB", (VIDEO_WIDTH, VIEW_BOTTOM - VIEW_TOP), (18, 20, 36))
        vd = ImageDraw.Draw(view)
        vy = -VIEW_TOP  # world→view y offset adds this
        # parallax mountains
        for k in range(-2, 6):
            mx = (k * 520.0 - cam["x"] * 0.25) % 3120.0 - 520.0
            vd.polygon([(mx, 700), (mx + 260, 300 + (k % 3) * 60), (mx + 520, 700)], fill=(26, 30, 52))
        # terrain: hill body + one coloured lane line per car (back lanes first)
        vis = [(x + ox, y + oy + vy) for x, y in terrain if -60 <= x + ox <= VIDEO_WIDTH + 60]
        if len(vis) >= 2:
            top_lane = [(x, y + lane_dy(n_cars - 1)) for x, y in vis]
            poly = top_lane + [(vis[-1][0], 2000), (vis[0][0], 2000)]
            vd.polygon(poly, fill=(40, 52, 84))
            for ci in reversed(range(n_cars)):
                lane = [(x, y + lane_dy(ci)) for x, y in vis]
                vd.line(lane, fill=tuple(int(c * 0.75) for c in CAR_COLORS[ci]), width=5)
        # distance poles + finish (span all lanes)
        span = lane_dy(n_cars - 1)
        for mark in range(500, int(X_FINISH), 500):
            sx = mark + ox
            if -20 <= sx <= VIDEO_WIDTH + 20:
                gy = _terrain_y(terrain, mark) + oy + vy
                vd.line([(sx, gy), (sx, gy + span - 50)], fill=TEXT_MUTED, width=3)
                vd.text((sx + 6, gy + span - 50), f"{mark // 10}m", font=get_font(22, bold=True), fill=TEXT_MUTED)
        fx = X_FINISH + ox
        if -40 <= fx <= VIDEO_WIDTH + 40:
            gy = y_finish + oy + vy
            vd.line([(fx, gy), (fx, gy + span - 230)], fill=TEXT_WHITE, width=6)
            for row in range(6):
                for col in range(4):
                    c = TEXT_WHITE if (row + col) % 2 == 0 else (20, 20, 30)
                    top = gy + span - 230
                    vd.rectangle([fx + col * 18, top + row * 18, fx + col * 18 + 18, top + 18 + row * 18], fill=c)
        # cars: back lanes first so front lanes overlap naturally
        for ci in reversed(range(n_cars)):
            r = rec[ci]
            dy = lane_dy(ci)
            wheels = [(r[wi] + ox, r[wi + 1] + oy + vy + dy, r[wi + 2]) for wi in (3, 6)]
            draw_car(vd, ci, r[0] + ox, r[1] + oy + vy + dy, r[2], wheels)
        frame.paste(view, (0, VIEW_TOP))
        if t_sec < INTRO_SEC - 0.3:
            draw_lineup(frame)

        draw = ImageDraw.Draw(frame)
        revealed = t_sec >= finish_video_t[winner_idx] + 0.3
        draw.rounded_rectangle([64, 1385, VIDEO_WIDTH - 64, 1495], radius=22,
                               fill=(18, 62, 42) if revealed else PANEL_BG_COLOR,
                               outline=ACCENT_GREEN if revealed else PANEL_BORDER_COLOR, width=3)
        if revealed:
            draw_centered_text(draw, f"CAR {LABELS[winner_idx]} WINS!", VIDEO_WIDTH // 2, 1392, get_font(66, title=True),
                               fill=TEXT_WHITE)
            draw_centered_text(draw, f"The {cars[winner_idx]} crossed the line in {t1:.1f}s", VIDEO_WIDTH // 2, 1460,
                               get_font(26, bold=True), fill=ACCENT_YELLOW)
        else:
            # mini track map
            x0, x1 = 110, VIDEO_WIDTH - 110
            draw.line([(x0, 1450), (x1, 1450)], fill=TEXT_MUTED, width=4)
            draw_checkered_line(draw, x1 - 4, x1 + 14, 1452, cell=9)
            names = "   ".join(f"{LABELS[i]}={cars[i]}" for i in range(len(cars)))
            draw_centered_text(draw, names, VIDEO_WIDTH // 2, 1398, get_font(22, bold=True), fill=TEXT_WHITE)
            for ci in range(len(rec)):
                p = max(0.0, min(1.0, rec[ci][0] / X_FINISH))
                px = x0 + (x1 - x0) * p
                draw.ellipse([px - 14, 1436, px + 14, 1464], fill=CAR_COLORS[ci], outline=TEXT_WHITE, width=2)
                draw_centered_text(draw, LABELS[ci], int(px), 1439, get_font(18, bold=True), fill=BG_COLOR)
        return frame

    video_path = produce_physics_video(
        file_stem=f"downhill_derby_{seed}",
        output_dir=output_dir,
        total_duration=TOTAL_DURATION,
        countdown_start_sec=COUNTDOWN_START_SEC,
        reveal_start_sec=REVEAL_START_SEC,
        outro_start_sec=OUTRO_START_SEC,
        hook_spoken="No engines, just gravity! Which car reaches the finish first?",
        reveal_spoken=f"Car {LABELS[winner_idx]}, the {cars[winner_idx]}, wins the derby!",
        sfx_events=sfx,
        draw_frame=draw_frame,
        series_badge="DOWNHILL DERBY",
        hook_title="WHICH CAR WINS THE DERBY?",
        subtitle_line=f"4 Cars  •  0 Engines  •  Seed: {seed}",
        footer_credit=f"Derby seed: {seed} • Physics: Pymunk 2D",
        wildcard=wildcard,
    )

    return {
        "format": "downhill_derby",
        "seed": seed,
        "video_path": video_path,
        "duration_sec": TOTAL_DURATION,
        "winner": winner_name,
        "winner_car": LABELS[winner_idx],
        "cars": cars,
        "finish_order": [f"{LABELS[i]}:{cars[i]}" for i in sim["full_order"]],
        "series_short": "Downhill Derby",
        "topic_short": "Derby",
        "reveal_timestamp_sec": REVEAL_START_SEC,
        "comment_question": (
            f"{winner_name} took it! 🏁 Which car design do you back in the next derby? 👇"
        ),
    }
