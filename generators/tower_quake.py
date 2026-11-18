"""
Call It Arena — Format 8: TOWER DEFENCE SEASON (`generators/tower_quake.py`, format id `tower_quake`).

"Six towers. One disaster. Which tower stands last?"
- ALL 6 rigid-body tower designs compete in every game (Needle, Temple, Zigzag, Twin Towers, Spire,
  Fortress), on shuffled positions, on one kinematic ground plate.
- A 10-game season where every game is a DIFFERENT disaster, so every design gets scenarios to shine:
    Earthquake (sideways shake)      → wide/low bases shine
    Ground Slam (vertical pounding)  → interlocked/heavy stacks shine
    Tilting Ground (seesaw plate)    → low centre of gravity shines
    Meteor Shower (rain from above)  → slim targets shine (less roof to hit)
    Wrecking Ball (descending sweep) → short towers shine (tall ones get hit first)
    Hurricane (gusting wind)         → narrow, heavy towers shine (less surface)
    Cannon Barrage (side fire)       → slim towers shine (smaller target)
    Bowling Strike (balls along the ground) → pillars/arches shine (balls roll between legs)
    Bomb Blitz (random blasts)       → spread-out designs shine
  The 9 disasters are shuffled once per season (each happens exactly once), and GAME 10 is the
  APOCALYPSE finale (earthquake + meteors + hurricane at once) worth 3x points.
- Disasters escalate until only one tower stands; each collapse is marked "OUT #6 … #2" live.
  The 5th collapse (the moment the last tower standing is decided) is time-remapped to land right
  after the 3-2-1 reveal, and the disaster calms down afterwards so the champion stays upright.
- Points per game by survival rank: 10 / 7 / 5 / 3 / 2 / 1 (finale x3) → season standings card after
  every game, "TOWER DEFENCE CHAMPION" card after the finale. State: `state/series.json`.
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
from common.physics_kit import TimeRemap, attach_impulse_sfx, rotate_point, sim_index
from common.physics_video import produce_physics_video
from common.render import draw_centered_text
from common.series import (
    SERIES_LENGTH,
    draw_series_card,
    load_series_state,
    next_game_info,
    record_series_game,
    save_series_state,
)

SERIES_KEY = "tower_quake"
GROUND_Y = 1240.0
FLOOR_Y = 1335.0
PLATE_HALF = 530.0
TOWER_SLOTS = [110.0, 282.0, 454.0, 626.0, 798.0, 970.0]
TOWER_COLORS = [ACCENT_RED, ACCENT_BLUE, ACCENT_GREEN, ACCENT_YELLOW, (170, 92, 255), (0, 208, 222)]
FALL_RATIO = 0.55
SURVIVAL_POINTS = [10, 7, 5, 3, 2, 1]  # rank 1 (last standing) → rank 6 (first to fall)
SIM_HZ = 120

TOTAL_DURATION = 24.0
INTRO_SEC = 3.5
COUNTDOWN_START_SEC = 15.5
REVEAL_START_SEC = 18.5
OUTRO_START_SEC = 22.2
TARGET_DECISIVE = (COUNTDOWN_START_SEC - INTRO_SEC) + 0.6 + 0.2 * (REVEAL_START_SEC - COUNTDOWN_START_SEC)

Block = Tuple[float, float, float, float]  # (cx offset, bottom height above plate, width, height)


# ------------------------------------------------------------------------------------ towers
def _needle() -> List[Block]:
    return [(0.0, i * 50.0, 70.0, 50.0) for i in range(8)]


def _temple() -> List[Block]:
    out: List[Block] = []
    for lvl in range(4):
        base = lvl * 100.0
        out += [(-50.0, base, 24.0, 76.0), (50.0, base, 24.0, 76.0), (0.0, base + 76.0, 140.0, 24.0)]
    return out


def _zigzag() -> List[Block]:
    return [((14.0 if i % 2 else -14.0), i * 50.0, 90.0, 50.0) for i in range(8)]


def _twins() -> List[Block]:
    out: List[Block] = []
    for i in range(6):
        out += [(-32.0, i * 62.0, 48.0, 62.0), (32.0, i * 62.0, 48.0, 62.0)]
    out.append((0.0, 372.0, 118.0, 24.0))
    return out


def _spire() -> List[Block]:
    # heavy 3-2 pyramid base + short thick column: low centre of gravity, small target
    out: List[Block] = [(-49.0, 0.0, 48.0, 56.0), (0.0, 0.0, 48.0, 56.0), (49.0, 0.0, 48.0, 56.0),
                        (-25.0, 56.0, 48.0, 56.0), (25.0, 56.0, 48.0, 56.0)]
    out += [(0.0, 112.0 + i * 50.0, 52.0, 50.0) for i in range(5)]
    return out


def _fortress() -> List[Block]:
    out: List[Block] = [(-37.0, 0.0, 72.0, 40.0), (37.0, 0.0, 72.0, 40.0)]
    out += [(0.0, 40.0 + i * 50.0, 90.0, 50.0) for i in range(5)]
    out += [(0.0, 290.0 + i * 40.0, 60.0, 40.0) for i in range(3)]
    return out


TOWER_DESIGNS = {
    "Needle": _needle,
    "Temple": _temple,
    "Zigzag": _zigzag,
    "Twin Towers": _twins,
    "Spire": _spire,
    "Fortress": _fortress,
}
DESIGN_NAMES = list(TOWER_DESIGNS.keys())
DESIGN_COLORS = {name: TOWER_COLORS[i] for i, name in enumerate(DESIGN_NAMES)}

# ------------------------------------------------------------------------------------ disasters
SCENARIOS: Dict[str, Dict[str, str]] = {
    "earthquake": {"label": "Earthquake", "meter": "MAGNITUDE", "spoken": "A massive earthquake hits!"},
    "ground_slam": {"label": "Ground Slam", "meter": "SLAM FORCE", "spoken": "The ground starts slamming up and down!"},
    "tilt": {"label": "Tilting Ground", "meter": "TILT ANGLE", "spoken": "The whole ground turns into a seesaw!"},
    "meteor_shower": {"label": "Meteor Shower", "meter": "METEORS", "spoken": "Meteors are raining from the sky!"},
    "wrecking_ball": {"label": "Wrecking Ball", "meter": "BALL DEPTH", "spoken": "A wrecking ball swings lower and lower!"},
    "hurricane": {"label": "Hurricane", "meter": "WIND SPEED", "spoken": "A hurricane is coming!"},
    "cannon_barrage": {"label": "Cannon Barrage", "meter": "FIRE RATE", "spoken": "Cannons open fire from both sides!"},
    "bowling": {"label": "Bowling Strike", "meter": "BALL SPEED", "spoken": "Giant bowling balls roll in!"},
    "bombs": {"label": "Bomb Blitz", "meter": "BLAST POWER", "spoken": "Bombs are going off everywhere!"},
    "apocalypse": {"label": "APOCALYPSE", "meter": "CHAOS", "spoken": "Apocalypse finale!"},
}
REGULAR_SCENARIOS = [k for k in SCENARIOS if k != "apocalypse"]
FINALE_SCENARIO = "apocalypse"


def _seed_to_int(seed_str: str) -> int:
    return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:12], 16)


def scenario_for_game(series_season: int, game: int) -> str:
    """Each regular disaster appears exactly once per 10-game season; game 10 is always the Apocalypse."""
    if game >= SERIES_LENGTH:
        return FINALE_SCENARIO
    order = list(REGULAR_SCENARIOS)
    random.Random(_seed_to_int(f"tower-defence-season-{series_season}")).shuffle(order)
    return order[(game - 1) % len(order)]


def pick_designs(seed: str) -> List[str]:
    """All 6 designs, in a seeded shuffled slot order (left→right) so positions are fair across a season."""
    names = list(DESIGN_NAMES)
    random.Random(_seed_to_int(f"{seed}|slots")).shuffle(names)
    return names


# ------------------------------------------------------------------------------------ simulation
def _ball(space: pymunk.Space, pos, vel, r: float, mass: float, elasticity: float = 0.3) -> pymunk.Body:
    body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, r))
    body.position = pos
    body.velocity = vel
    shape = pymunk.Circle(body, r)
    shape.friction = 0.6
    shape.elasticity = elasticity
    shape.collision_type = 3
    space.add(body, shape)
    return body


def simulate_defence(seed: str, designs: List[str], scenario: str, power: float, attempt: int,
                     max_t: float = 46.0) -> Dict[str, Any]:
    rng = random.Random(_seed_to_int(f"{seed}|defence|{scenario}|{attempt}"))
    space = pymunk.Space()
    space.gravity = (0.0, 900.0)
    space.iterations = 22
    space.damping = 0.999

    floor = pymunk.Segment(space.static_body, (-600.0, FLOOR_Y), (1680.0, FLOOR_Y), 10.0)
    floor.friction = 0.9
    floor.elasticity = 0.1
    space.add(floor)

    plate = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
    plate.position = (540.0, GROUND_Y)
    plate_shape = pymunk.Segment(plate, (-PLATE_HALF, 0.0), (PLATE_HALF, 0.0), 12.0)
    plate_shape.friction = 1.0
    plate_shape.elasticity = 0.05
    space.add(plate, plate_shape)

    towers: List[Dict[str, Any]] = []
    for ti, name in enumerate(designs):
        blocks = []
        top = 0.0
        x0 = TOWER_SLOTS[ti]
        for (ox, base_h, w, h) in TOWER_DESIGNS[name]():
            mass = w * h / 900.0
            body = pymunk.Body(mass, pymunk.moment_for_box(mass, (w, h)))
            body.position = (x0 + ox + rng.uniform(-1.0, 1.0), GROUND_Y - 12.0 - base_h - h / 2.0)
            shape = pymunk.Poly.create_box(body, (w, h), radius=0.5)
            shape.friction = 0.75
            shape.elasticity = 0.05
            shape.collision_type = 1
            space.add(body, shape)
            blocks.append({"body": body, "w": w, "h": h, "area": w * h})
            top = max(top, base_h + h)
        towers.append({"name": name, "x": x0, "blocks": blocks, "height0": top, "fell_at": None})

    sfx: List[SfxEvent] = []
    clock = {"t": 0.0}
    attach_impulse_sfx(space, sfx, lambda: clock["t"], cutoff_sec=1e9, min_impulse=260.0, min_frame_gap=3)

    parts = {"earthquake", "meteor_shower", "hurricane"} if scenario == FINALE_SCENARIO else {scenario}
    combo = 0.75 if scenario == FINALE_SCENARIO else 1.0
    q_omega = 2.0 * math.pi * rng.uniform(1.5, 2.1)
    s_omega = 2.0 * math.pi * rng.uniform(2.4, 3.1)
    t_omega = 2.0 * math.pi * rng.uniform(0.45, 0.7)
    wb_omega = rng.uniform(1.4, 1.8)
    wind_dir = rng.choice([-1.0, 1.0])
    start = 0.8

    wrecker: Optional[pymunk.Body] = None
    if "wrecking_ball" in parts:
        wrecker = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        wrecker.position = (540.0, 640.0)
        ws = pymunk.Circle(wrecker, 46.0)
        ws.friction = 0.4
        ws.elasticity = 0.2
        space.add(wrecker, ws)

    projectiles: List[Tuple[pymunk.Body, float, str, float]] = []
    lifetime = {"meteor": 2.2, "cannon": 3.5, "bowl": 6.0}
    blasts: List[Tuple[float, float, float, float]] = []
    spawn_acc = {"meteor": 0.0, "cannon": 0.0, "bowl": 0.0, "bomb": 0.0}
    side = {"cannon": 1.0, "bowl": 1.0}

    dt = 1.0 / SIM_HZ
    t = 0.0
    falls: List[Tuple[float, int]] = []
    calm_at: Optional[float] = None
    peak_level = 0.0
    records: List[Dict[str, Any]] = []
    cap_t = max_t

    while t < cap_t:
        e = power * max(0.0, t - start)  # escalation
        calm = 1.0 if calm_at is None else max(0.0, 1.0 - (t - calm_at) / 1.2)
        level = min(1.0, e / 30.0) * (calm if calm_at is not None else 1.0)
        if calm_at is None:
            peak_level = level

        # --- plate motion (earthquake / ground slam / tilt)
        vx = vy = av = 0.0
        if "earthquake" in parts:
            amp = min(90.0, 0.9 * e * combo) * calm
            vx = amp * q_omega * math.cos(q_omega * t)
        if "ground_slam" in parts:
            amp = min(45.0, 0.35 * e) * calm
            vy = amp * s_omega * math.cos(s_omega * t)
        if "tilt" in parts:
            amp = min(0.24, 0.006 * e) * calm
            av = amp * t_omega * math.cos(t_omega * t)
        plate.velocity = (vx, vy)
        plate.angular_velocity = av
        if calm_at is not None and calm <= 0.0:
            plate.velocity = ((540.0 - plate.position.x) * 2.0, (GROUND_Y - plate.position.y) * 2.0)
            plate.angular_velocity = -plate.angle * 2.0

        spawning = calm_at is None
        # --- meteors
        if "meteor_shower" in parts and spawning:
            spawn_acc["meteor"] += dt * (0.5 + 0.22 * e * combo)
            while spawn_acc["meteor"] >= 1.0:
                spawn_acc["meteor"] -= 1.0
                r = rng.uniform(12.0, 25.0)
                b = _ball(space, (rng.uniform(60.0, 1020.0), 300.0), (rng.uniform(-80.0, 80.0), rng.uniform(380.0, 650.0)),
                          r, r * r / 55.0, 0.2)
                projectiles.append((b, r, "meteor", t))
        # --- cannons
        if "cannon_barrage" in parts and spawning:
            spawn_acc["cannon"] += dt * (0.35 + 0.11 * e)
            while spawn_acc["cannon"] >= 1.0:
                spawn_acc["cannon"] -= 1.0
                side["cannon"] *= -1.0
                # artillery: lob a shell from off-screen at a random STILL-STANDING tower (middle towers too)
                standing = [tw for tw in towers if tw["fell_at"] is None] or towers
                target = rng.choice(standing)
                sx, sy = (-40.0 if side["cannon"] > 0 else 1120.0), GROUND_Y - rng.uniform(150.0, 300.0)
                tx = target["x"] + rng.uniform(-30.0, 30.0)
                ty = GROUND_Y - 12.0 - target["height0"] * rng.uniform(0.35, 0.95)
                flight = rng.uniform(0.8, 1.1)
                vx = (tx - sx) / flight
                vy = (ty - sy) / flight - 0.5 * 900.0 * flight
                b = _ball(space, (sx, sy), (vx, vy), 16.0, 7.0 + 0.05 * e)
                projectiles.append((b, 16.0, "cannon", t))
        # --- bowling balls
        if "bowling" in parts and spawning:
            spawn_acc["bowl"] += dt * (0.28 + 0.05 * e)
            while spawn_acc["bowl"] >= 1.0:
                spawn_acc["bowl"] -= 1.0
                side["bowl"] *= -1.0
                sx = -60.0 if side["bowl"] > 0 else 1140.0  # rolls in from off-screen onto the plate
                b = _ball(space, (sx, plate.position.y - 12.0 - 40.0), (side["bowl"] * (420.0 + 22.0 * e), 0.0), 38.0, 22.0, 0.1)
                projectiles.append((b, 38.0, "bowl", t))
        # --- bombs
        if "bombs" in parts and spawning:
            spawn_acc["bomb"] += dt / max(0.55, 2.2 - 0.07 * e)
            while spawn_acc["bomb"] >= 1.0:
                spawn_acc["bomb"] -= 1.0
                standing = [tw for tw in towers if tw["fell_at"] is None] or towers
                tw = rng.choice(standing)
                bx = tw["x"] + rng.uniform(-70.0, 70.0)
                by = GROUND_Y - rng.uniform(60.0, 360.0)
                radius = 150.0
                strength = 55.0 + 16.0 * e
                for tw2 in towers:
                    for blk in tw2["blocks"]:
                        bd = blk["body"]
                        dx, dy = bd.position.x - bx, bd.position.y - by
                        d = math.hypot(dx, dy)
                        if d < radius:
                            fall = 1.0 - d / radius
                            j = strength * bd.mass * fall
                            bd.apply_impulse_at_world_point((dx / max(d, 1.0) * j, dy / max(d, 1.0) * j - 0.3 * j), bd.position)
                blasts.append((t, bx, by, radius))
                sfx.append(SfxEvent(timestamp_sec=t, kind="boing", gain=0.8, pitch_shift=0.55))
        # --- hurricane wind
        if "hurricane" in parts:
            gust = (0.55 + 0.45 * abs(math.sin(0.9 * t))) * calm
            for tw in towers:
                base = plate.position.y - 12.0
                for blk in tw["blocks"]:
                    bd = blk["body"]
                    hgt = max(0.0, base - bd.position.y)
                    f = 0.85 * e * combo * gust * (blk["area"] / 900.0) * (1.0 + hgt / 280.0)
                    bd.apply_force_at_world_point((wind_dir * f, 0.0), bd.position)
        # --- wrecking ball (descending sweep)
        if wrecker is not None:
            if calm_at is None:
                lower = GROUND_Y - 70.0
                wy = wrecker.position.y
                vy_w = 9.0 * power if (t > start and wy < lower) else 0.0
                wrecker.velocity = (560.0 * wb_omega * math.cos(wb_omega * t), vy_w)
            else:
                wrecker.velocity = (0.0, -500.0)

        records.append({
            "plate": (float(plate.position.x), float(plate.position.y), float(plate.angle)),
            "blocks": [[(float(b["body"].position.x), float(b["body"].position.y), float(b["body"].angle))
                        for b in tw["blocks"]] for tw in towers],
            "proj": [(float(b.position.x), float(b.position.y), r, kind, t - born) for b, r, kind, born in projectiles],
            "wreck": (float(wrecker.position.x), float(wrecker.position.y)) if wrecker is not None else None,
            "level": level,
        })
        clock["t"] = t
        space.step(dt)
        t += dt

        # projectile cleanup
        keep = []
        for b, r, kind, born in projectiles:
            expired = (t - born) > lifetime[kind]  # meteors burn out so they never bury the towers
            if expired or b.position.y > FLOOR_Y + 220 or not (-400.0 < b.position.x < 1480.0):
                space.remove(b, *b.shapes)
            else:
                keep.append((b, r, kind, born))
        projectiles = keep

        # collapse detection (height relative to the local plate surface under each tower)
        px, py, pa = plate.position.x, plate.position.y, plate.angle
        for ti, tw in enumerate(towers):
            if tw["fell_at"] is not None:
                continue
            local_base = py + (tw["x"] - px) * math.tan(pa) - 12.0
            highest = min(b["body"].position.y - b["h"] / 2.0 for b in tw["blocks"])
            if local_base - highest < FALL_RATIO * tw["height0"]:
                tw["fell_at"] = t
                falls.append((t, ti))
                sfx.append(SfxEvent(timestamp_sec=t, kind="reveal_fanfare", gain=0.3, pitch_shift=0.55))
                if len(falls) == len(towers) - 1 and calm_at is None:
                    calm_at = t
                    cap_t = t + 6.0

    return {"towers": towers, "records": records, "falls": falls, "sfx": sfx, "power": power,
            "blasts": blasts, "peak_level": peak_level, "scenario": scenario}


def run_tuned_defence(seed: str, designs: List[str], scenario: str) -> Dict[str, Any]:
    power = 1.0
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    n = len(designs)
    for attempt in range(10):
        sim = simulate_defence(seed, designs, scenario, power, attempt)
        falls = sim["falls"]
        if len(falls) >= n - 1:
            t_dec = falls[n - 2][0]
            err = abs(t_dec - TARGET_DECISIVE) / TARGET_DECISIVE
            first = falls[0][0]
            if first < 2.0:
                err += 0.3  # someone collapsing instantly is boring
            if len(falls) >= n and falls[n - 1][0] < t_dec + 5.2:
                err += 1.0  # champion must still be standing through the reveal
            if best is None or err < best[0]:
                best = (err, sim)
            if err < 0.12:
                break
            power = max(0.15, min(12.0, power * (t_dec / TARGET_DECISIVE) ** 1.3))
        else:
            power = min(12.0, power * 1.8)
    if best is None:
        if scenario != "earthquake":
            # Safety net: never fail a post — fall back to the always-decisive earthquake.
            return run_tuned_defence(seed, designs, "earthquake")
        raise RuntimeError(f"Tower Defence ({scenario}): could not topple {n - 1} towers in any attempt")
    return best[1]


# Backwards-compatible name used by older callers/tests
def run_tuned_quake(seed: str, designs: List[str], scenario: str = "earthquake") -> Dict[str, Any]:
    return run_tuned_defence(seed, designs, scenario)


# ------------------------------------------------------------------------------------ video
def generate_tower_quake_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    wildcard: Optional[Dict[str, str]] = None,
    update_state: bool = True,
    force_game: Optional[int] = None,
    scenario_override: Optional[str] = None,
) -> Dict[str, Any]:
    if force_game and update_state:
        raise ValueError("force_game is a preview mode and requires update_state=False")
    series_state = load_series_state()
    series_season, game_no, is_finale, _mult = next_game_info(series_state, SERIES_KEY, force_game)
    scenario = scenario_override if scenario_override in SCENARIOS else scenario_for_game(series_season, game_no)
    sc = SCENARIOS[scenario]
    next_scenario = None if is_finale else scenario_for_game(series_season, game_no + 1)

    designs = pick_designs(seed)
    sim = run_tuned_defence(seed, designs, scenario)
    scenario = sim["scenario"]  # may differ if the safety-net fallback kicked in
    sc = SCENARIOS[scenario]
    towers = sim["towers"]
    records = sim["records"]
    n = len(designs)
    falls = sim["falls"]
    t_decisive = falls[n - 2][0]
    fallen_first_n = [ti for _, ti in falls[: n - 1]]
    winner_idx = [i for i in range(n) if i not in fallen_first_n][0]
    survival_order = [winner_idx] + list(reversed(fallen_first_n))  # rank 1..6
    out_rank = {ti: n - k for k, ti in enumerate(fallen_first_n)}  # first to fall = #6
    winner_design = towers[winner_idx]["name"]

    remap = TimeRemap(t_decisive, INTRO_SEC, COUNTDOWN_START_SEC, REVEAL_START_SEC, slowmo=0.2, post=0.6)
    fall_video_t = {ti: remap.inverse(ft) for ft, ti in falls}
    card_from = remap.inverse(t_decisive) + 1.6

    awards = {towers[ti]["name"]: SURVIVAL_POINTS[rank] for rank, ti in enumerate(survival_order)}
    series = record_series_game(series_state, SERIES_KEY, awards, winner_design, seed, all_keys=DESIGN_NAMES,
                                force_game=force_game)
    if update_state:
        save_series_state(series_state)

    sfx = [SfxEvent(remap.inverse(e.timestamp_sec), e.kind, e.gain, e.pitch_shift) for e in sim["sfx"]]
    blasts = sim["blasts"]

    base = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
    bd = ImageDraw.Draw(base)
    for y in range(420, int(GROUND_Y), 80):
        bd.line([(30, y), (VIDEO_WIDTH - 30, y)], fill=(20, 23, 40), width=1)
    bd.rectangle([0, FLOOR_Y, VIDEO_WIDTH, FLOOR_Y + 48], fill=(38, 34, 30))
    bd.line([(0, FLOOR_Y), (VIDEO_WIDTH, FLOOR_Y)], fill=(90, 80, 70), width=4)

    def draw_frame(t_sec: float) -> Image.Image:
        st = remap(t_sec)
        rec = records[sim_index(st, SIM_HZ, len(records))]
        frame = base.copy()
        draw = ImageDraw.Draw(frame)

        # hurricane streaks
        if scenario in ("hurricane", "apocalypse") and rec["level"] > 0.02:
            n_streaks = int(10 + 50 * rec["level"])
            for k in range(n_streaks):
                yy = 420 + (k * 97) % 820
                xx = (k * 233 + st * (500 + 900 * rec["level"])) % 1300 - 110
                draw.line([(xx, yy), (xx + 60 + 80 * rec["level"], yy)], fill=(70, 90, 130), width=2)

        # plate
        px, py, pa = rec["plate"]
        c, s_ = math.cos(pa), math.sin(pa)
        p1 = (px - PLATE_HALF * c, py - PLATE_HALF * s_)
        p2 = (px + PLATE_HALF * c, py + PLATE_HALF * s_)
        draw.line([p1, p2], fill=(70, 74, 96), width=24)
        for k in range(0, int(2 * PLATE_HALF), 44):
            qx, qy = p1[0] + c * k, p1[1] + s_ * k
            draw.line([(qx, qy + 10), (qx + 18 * c, qy + 18 * s_ - 10)], fill=(247, 144, 9), width=6)

        # towers
        for ti, tw in enumerate(towers):
            col = DESIGN_COLORS[tw["name"]]
            down = ti in fall_video_t and t_sec >= fall_video_t[ti]
            fill = tuple(int(v * 0.42) for v in col) if down else col
            for (bx, by, ba), blk in zip(rec["blocks"][ti], tw["blocks"]):
                hw, hh = blk["w"] / 2.0, blk["h"] / 2.0
                pts = []
                for (lx, ly) in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                    rx, ry = rotate_point((lx, ly), ba)
                    pts.append((bx + rx, by + ry))
                draw.polygon(pts, fill=fill, outline=TEXT_WHITE)
            lx = tw["x"]
            is_champ = t_sec >= REVEAL_START_SEC + 0.9 and ti == winner_idx
            badge = ACCENT_GREEN if is_champ else ((60, 30, 40) if down else PANEL_BG_COLOR)
            draw.rounded_rectangle([lx - 82, 1347, lx + 82, 1379], radius=10, fill=badge, outline=col, width=2)
            draw_centered_text(draw, tw["name"].upper(), int(lx), 1351, get_font(20, bold=True), fill=TEXT_WHITE)
            if down:
                draw_centered_text(draw, f"OUT #{out_rank.get(ti, n)}", int(lx), 700, get_font(36, title=True), fill=ACCENT_RED)

        # projectiles / wrecking ball / blasts
        for (qx, qy, r, kind, age) in rec["proj"]:
            if kind == "meteor" and age > 1.8:
                r *= max(0.15, (2.2 - age) / 0.4)  # burning out
            if kind == "meteor":
                draw.polygon([(qx - r * 0.8, qy - r * 0.2), (qx + r * 0.8, qy - r * 0.2), (qx, qy - r * 3.2)], fill=(255, 120, 30))
                draw.ellipse([qx - r, qy - r, qx + r, qy + r], fill=(255, 170, 60), outline=(255, 230, 150), width=2)
            elif kind == "cannon":
                draw.ellipse([qx - r, qy - r, qx + r, qy + r], fill=(30, 30, 36), outline=(200, 200, 210), width=2)
            else:
                draw.ellipse([qx - r, qy - r, qx + r, qy + r], fill=(40, 20, 70), outline=(170, 92, 255), width=4)
                draw.ellipse([qx - 8, qy - 18, qx + 2, qy - 8], fill=TEXT_WHITE)
        if rec["wreck"] is not None:
            wx, wy = rec["wreck"]
            draw.line([(wx, 380), (wx, wy)], fill=(150, 150, 160), width=5)
            draw.ellipse([wx - 46, wy - 46, wx + 46, wy + 46], fill=(60, 60, 70), outline=TEXT_WHITE, width=3)
        for (bt, bx_, by_, br) in blasts:
            age = st - bt
            if 0.0 <= age < 0.35:
                rr = br * (0.3 + 2.0 * age)
                draw.ellipse([bx_ - rr, by_ - rr, bx_ + rr, by_ + rr], outline=(255, 200, 80), width=8)
                if age < 0.12:
                    draw.ellipse([bx_ - 40, by_ - 40, bx_ + 40, by_ + 40], fill=(255, 240, 180))

        # intro: today's disaster banner
        if t_sec < INTRO_SEC - 0.2:
            draw.rounded_rectangle([60, 400, VIDEO_WIDTH - 60, 640], radius=28, fill=(18, 20, 36), outline=ACCENT_YELLOW, width=5)
            tag = "SEASON FINALE · 3X POINTS" if is_finale else f"GAME {game_no}/10 · TODAY'S DISASTER"
            draw_centered_text(draw, tag, VIDEO_WIDTH // 2, 420, get_font(32, bold=True), fill=ACCENT_YELLOW)
            draw_centered_text(draw, sc["label"].upper(), VIDEO_WIDTH // 2, 470, get_font(96, title=True), fill=TEXT_WHITE)
            draw_centered_text(draw, "6 towers enter. Which one stands last?", VIDEO_WIDTH // 2, 588,
                               get_font(28, bold=True), fill=TEXT_MUTED)

        # bottom panel
        revealed = t_sec >= REVEAL_START_SEC + 0.9
        standing = sum(1 for ti in range(n) if not (ti in fall_video_t and t_sec >= fall_video_t[ti]))
        draw.rounded_rectangle([64, 1392, VIDEO_WIDTH - 64, 1500], radius=22,
                               fill=(18, 62, 42) if revealed else PANEL_BG_COLOR,
                               outline=ACCENT_GREEN if revealed else PANEL_BORDER_COLOR, width=3)
        if revealed:
            draw_centered_text(draw, f"{winner_design.upper()} STANDS LAST!", VIDEO_WIDTH // 2, 1398,
                               get_font(64, title=True), fill=TEXT_WHITE)
            draw_centered_text(draw, f"Survived the {sc['label']}  •  +{SURVIVAL_POINTS[0] * series['multiplier']} pts",
                               VIDEO_WIDTH // 2, 1464, get_font(26, bold=True), fill=ACCENT_YELLOW)
        else:
            draw.text((92, 1404), "STILL STANDING", font=get_font(24, bold=True), fill=ACCENT_YELLOW)
            draw.text((92, 1434), f"{standing}/{n}", font=get_font(50, title=True), fill=TEXT_WHITE)
            bar_x0, bar_x1 = 300, VIDEO_WIDTH - 100
            draw.text((bar_x0, 1404), f"{sc['meter']}  •  {sc['label'].upper()}", font=get_font(24, bold=True), fill=TEXT_MUTED)
            draw.rounded_rectangle([bar_x0, 1440, bar_x1, 1476], radius=12, fill=(20, 22, 38))
            lv = rec["level"]
            if lv > 0.01:
                bar_col = ACCENT_GREEN if lv < 0.4 else (ACCENT_YELLOW if lv < 0.75 else ACCENT_RED)
                draw.rounded_rectangle([bar_x0, 1440, bar_x0 + (bar_x1 - bar_x0) * lv, 1476], radius=12, fill=bar_col)

        if t_sec >= card_from:
            footer = (f"NEXT DISASTER: {SCENARIOS[next_scenario]['label'].upper()}" if next_scenario and game_no < SERIES_LENGTH - 1
                      else ("NEXT: APOCALYPSE FINALE — 3X POINTS!" if next_scenario else None))
            draw_series_card(frame, "Tower Defence", series, "PTS", {k: k for k in DESIGN_NAMES}, DESIGN_COLORS,
                             winner_design, footer_text=footer)
        return frame

    video_path = produce_physics_video(
        file_stem=f"tower_quake_{seed}",
        output_dir=output_dir,
        total_duration=TOTAL_DURATION,
        countdown_start_sec=COUNTDOWN_START_SEC,
        reveal_start_sec=REVEAL_START_SEC,
        outro_start_sec=OUTRO_START_SEC,
        hook_spoken=f"{sc['spoken']} Which tower stands last?",
        reveal_spoken=f"The {winner_design} is the last tower standing!",
        sfx_events=sfx,
        draw_frame=draw_frame,
        series_badge=("TOWER DEFENCE FINALE · 3X" if series["is_finale"] else f"TOWER DEFENCE · GAME {series['game']}/10"),
        hook_title=f"{sc['label'].upper()}: WHO STANDS LAST?" if len(sc["label"]) <= 11 else "WHICH TOWER STANDS LAST?",
        subtitle_line=f"6 Towers  •  Disaster: {sc['label']}  •  Seed: {seed}",
        footer_credit=f"Tower seed: {seed} • Physics: Pymunk 2D",
        wildcard=wildcard,
    )

    return {
        "format": "tower_quake",
        "seed": seed,
        "video_path": video_path,
        "duration_sec": TOTAL_DURATION,
        "winner": f"{winner_design} (survived the {sc['label']})",
        "winner_tower": winner_design,
        "scenario": scenario,
        "scenario_label": sc["label"],
        "designs": designs,
        "survival_order": [towers[ti]["name"] for ti in survival_order],
        "series_short": f"Tower Defence {series['game']}/10",
        "series_game": series["game"],
        "series_finale": series["is_finale"],
        "series_champion": series["champion"],
        "topic_short": sc["label"],
        "reveal_timestamp_sec": REVEAL_START_SEC,
        "comment_question": (
            f"The {winner_design} survived the {sc['label']}! 🏗️ Which tower survives the next disaster? 👇"
        ),
    }
