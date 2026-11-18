"""
Call It Arena — Format 8: Earthquake Tower Survival (`generators/tower_quake.py`).

"Which tower stands last in the earthquake?"
- Three Pymunk rigid-body block towers (picked from 6 designs: Column, Pillars, Staggered,
  Twin Towers, Spire, Wide Base) stand on a kinematic ground plate.
- The plate shakes horizontally with steadily rising magnitude (live Richter meter).
- A tower "falls" once its highest point drops below 55% of its starting height.
- The decisive moment (2nd tower falling) is time-remapped to land right after the reveal,
  with the final seconds before it shown in slow motion during the 3-2-1 countdown.
- Shake growth is auto-tuned per seed so the battle fills the timeline.
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

GROUND_Y = 1240.0
FLOOR_Y = 1335.0
PLATE_HALF = 470.0
TOWER_X = [235.0, 540.0, 845.0]
TOWER_LABELS = ["A", "B", "C"]
TOWER_COLORS = [ACCENT_RED, ACCENT_BLUE, ACCENT_GREEN]
FALL_RATIO = 0.55
BLOCK_SCALE = 1.3  # visual + physical scale of every tower design
SIM_HZ = 120

TOTAL_DURATION = 24.0
INTRO_SEC = 2.0
COUNTDOWN_START_SEC = 15.5
REVEAL_START_SEC = 18.5
OUTRO_START_SEC = 22.2
TARGET_DECISIVE = (COUNTDOWN_START_SEC - INTRO_SEC) + 0.6 + 0.2 * (REVEAL_START_SEC - COUNTDOWN_START_SEC)

Block = Tuple[float, float, float, float]  # (cx offset, bottom height above ground, width, height)


def _design_column() -> List[Block]:
    return [(0.0, i * 50.0, 80.0, 50.0) for i in range(8)]


def _design_pillars() -> List[Block]:
    out: List[Block] = []
    for lvl in range(4):
        base = lvl * 100.0
        out += [(-50.0, base, 24.0, 76.0), (50.0, base, 24.0, 76.0), (0.0, base + 76.0, 140.0, 24.0)]
    return out


def _design_staggered() -> List[Block]:
    return [((15.0 if i % 2 else -15.0), i * 50.0, 100.0, 50.0) for i in range(8)]


def _design_twin() -> List[Block]:
    out: List[Block] = []
    for i in range(6):
        out += [(-34.0, i * 62.0, 50.0, 62.0), (34.0, i * 62.0, 50.0, 62.0)]
    out.append((0.0, 372.0, 140.0, 24.0))
    return out


def _design_spire() -> List[Block]:
    out: List[Block] = [(-62.0, 0.0, 60.0, 50.0), (0.0, 0.0, 60.0, 50.0), (62.0, 0.0, 60.0, 50.0),
                        (-31.0, 50.0, 60.0, 50.0), (31.0, 50.0, 60.0, 50.0)]
    out += [(0.0, 100.0 + i * 50.0, 52.0, 50.0) for i in range(6)]
    return out


def _design_wide_base() -> List[Block]:
    out: List[Block] = [(-76.0, 0.0, 150.0, 40.0), (76.0, 0.0, 150.0, 40.0)]
    out += [(0.0, 40.0 + i * 50.0, 90.0, 50.0) for i in range(5)]
    out += [(0.0, 290.0 + i * 40.0, 60.0, 40.0) for i in range(3)]
    return out


TOWER_DESIGNS = {
    "Column": _design_column,
    "Pillars": _design_pillars,
    "Staggered": _design_staggered,
    "Twin Towers": _design_twin,
    "Spire": _design_spire,
    "Wide Base": _design_wide_base,
}


def _seed_to_int(seed_str: str) -> int:
    return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:12], 16)


def pick_designs(seed: str) -> List[str]:
    rng = random.Random(_seed_to_int(f"{seed}|designs"))
    names = list(TOWER_DESIGNS.keys())
    rng.shuffle(names)
    return names[:3]


def simulate_quake(seed: str, designs: List[str], growth: float, attempt: int) -> Dict[str, Any]:
    rng = random.Random(_seed_to_int(f"{seed}|quake|{attempt}"))
    space = pymunk.Space()
    space.gravity = (0.0, 900.0)
    space.iterations = 30
    space.damping = 0.999

    floor = pymunk.Segment(space.static_body, (-400.0, FLOOR_Y), (1480.0, FLOOR_Y), 10.0)
    floor.friction = 0.9
    floor.elasticity = 0.1
    space.add(floor)

    freq = rng.uniform(1.5, 2.1)
    omega = 2.0 * math.pi * freq
    max_amp = 70.0
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
        for (ox, base_h, w, h) in TOWER_DESIGNS[name]():
            ox, base_h, w, h = (v * BLOCK_SCALE for v in (ox, base_h, w, h))
            mass = w * h / 900.0
            body = pymunk.Body(mass, pymunk.moment_for_box(mass, (w, h)))
            body.position = (TOWER_X[ti] + ox + rng.uniform(-1.5, 1.5), GROUND_Y - 12.0 - base_h - h / 2.0)
            shape = pymunk.Poly.create_box(body, (w, h), radius=0.5)
            shape.friction = 0.75
            shape.elasticity = 0.05
            shape.collision_type = 1
            space.add(body, shape)
            blocks.append({"body": body, "w": w, "h": h})
            top = max(top, base_h + h)
        towers.append({"name": name, "blocks": blocks, "height0": top, "fell_at": None})

    sfx: List[SfxEvent] = []
    clock = {"t": 0.0}
    attach_impulse_sfx(space, sfx, lambda: clock["t"], cutoff_sec=1e9, min_impulse=220.0, min_frame_gap=3)

    shake_start = 0.8
    dt = 1.0 / SIM_HZ
    records: List[Dict[str, Any]] = []
    t = 0.0
    max_t = 46.0
    falls: List[Tuple[float, int]] = []
    peak_amp = 0.0
    while t < max_t:
        amp = min(max_amp, growth * max(0.0, t - shake_start))
        if len(falls) >= 2:  # the quake subsides once the battle is decided
            amp = peak_amp * max(0.0, 1.0 - (t - falls[1][0]) / 1.2)
        else:
            peak_amp = amp
        records.append(
            {
                "plate_x": float(plate.position.x),
                "amp": amp,
                "blocks": [[(float(b["body"].position.x), float(b["body"].position.y), float(b["body"].angle))
                            for b in tw["blocks"]] for tw in towers],
            }
        )
        clock["t"] = t
        # velocity of x(t) = amp(t)·sin(ωt) (amp grows slowly → derivative dominated by ω term)
        plate.velocity = (amp * omega * math.cos(omega * t), 0.0)
        space.step(dt)
        t += dt
        for ti, tw in enumerate(towers):
            if tw["fell_at"] is not None:
                continue
            highest = min(b["body"].position.y - b["h"] / 2.0 for b in tw["blocks"])
            height_now = GROUND_Y - 12.0 - highest
            if height_now < FALL_RATIO * tw["height0"]:
                tw["fell_at"] = t
                falls.append((t, ti))
                sfx.append(SfxEvent(timestamp_sec=t, kind="reveal_fanfare", gain=0.35, pitch_shift=0.6))
                if len(falls) == 2:
                    max_t = t + 6.0
    return {"towers": towers, "records": records, "falls": falls, "sfx": sfx, "growth": growth, "freq": freq,
            "peak_amp": peak_amp}


def run_tuned_quake(seed: str, designs: List[str]) -> Dict[str, Any]:
    growth = 4.2
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for attempt in range(16):
        sim = simulate_quake(seed, designs, growth, attempt)
        if len(sim["falls"]) >= 2:
            t2 = sim["falls"][1][0]
            err = abs(t2 - TARGET_DECISIVE) / TARGET_DECISIVE
            # the survivor must still be standing for the rest of the video (~5s after the decisive fall)
            if len(sim["falls"]) >= 3 and sim["falls"][2][0] < t2 + 5.2:
                err += 1.0
            if best is None or err < best[0]:
                best = (err, sim)
            if err < 0.12:
                break
            growth = max(0.6, min(30.0, growth * (t2 / TARGET_DECISIVE) ** 1.3))
        else:
            growth = min(30.0, growth * 1.8)
    if best is None:
        raise RuntimeError("Tower quake: fewer than two towers fell in every attempt")
    return best[1]


def generate_tower_quake_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    wildcard: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    designs = pick_designs(seed)
    sim = run_tuned_quake(seed, designs)
    towers = sim["towers"]
    records = sim["records"]
    t_decisive = sim["falls"][1][0]
    fallen = {ti for _, ti in sim["falls"][:2]}
    winner_idx = [i for i in range(3) if i not in fallen][0]
    winner_label = f"Tower {TOWER_LABELS[winner_idx]} ({towers[winner_idx]['name']})"
    remap = TimeRemap(t_decisive, INTRO_SEC, COUNTDOWN_START_SEC, REVEAL_START_SEC, slowmo=0.2, post=0.6)
    fall_video_t = {ti: remap.inverse(ft) for ft, ti in sim["falls"]}

    sfx = [SfxEvent(remap.inverse(e.timestamp_sec), e.kind, e.gain, e.pitch_shift) for e in sim["sfx"]]

    base = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
    bd = ImageDraw.Draw(base)
    for y in range(420, int(GROUND_Y), 80):
        bd.line([(60, y), (VIDEO_WIDTH - 60, y)], fill=(20, 23, 40), width=1)
    bd.rectangle([0, FLOOR_Y, VIDEO_WIDTH, FLOOR_Y + 40], fill=(38, 34, 30))
    bd.line([(0, FLOOR_Y), (VIDEO_WIDTH, FLOOR_Y)], fill=(90, 80, 70), width=4)

    def draw_frame(t_sec: float) -> Image.Image:
        rec = records[sim_index(remap(t_sec), SIM_HZ, len(records))]
        frame = base.copy()
        draw = ImageDraw.Draw(frame)
        px = rec["plate_x"]
        # ground plate with hazard stripes
        draw.rectangle([px - PLATE_HALF, GROUND_Y - 12, px + PLATE_HALF, GROUND_Y + 12], fill=(70, 74, 96))
        for sx in range(int(px - PLATE_HALF), int(px + PLATE_HALF), 40):
            draw.polygon([(sx, GROUND_Y + 12), (sx + 20, GROUND_Y + 12), (sx + 32, GROUND_Y - 12), (sx + 12, GROUND_Y - 12)],
                         fill=(247, 144, 9))
        revealed = t_sec >= REVEAL_START_SEC + 0.9
        for ti, tw in enumerate(towers):
            col = TOWER_COLORS[ti]
            down = ti in fall_video_t and t_sec >= fall_video_t[ti]
            fill = tuple(int(c * 0.45) for c in col) if down else col
            for (bx, by, ba), blk in zip(rec["blocks"][ti], tw["blocks"]):
                hw, hh = blk["w"] / 2.0, blk["h"] / 2.0
                pts = []
                for (lx, ly) in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                    rx, ry = rotate_point((lx, ly), ba)
                    pts.append((bx + rx, by + ry))
                draw.polygon(pts, fill=fill, outline=TEXT_WHITE)
            # tower label badge
            lx = TOWER_X[ti]
            badge_col = ACCENT_GREEN if (revealed and ti == winner_idx) else (PANEL_BG_COLOR if not down else (60, 30, 40))
            draw.rounded_rectangle([lx - 90, 1350, lx + 90, 1378], radius=10, fill=badge_col, outline=col, width=2)
            draw_centered_text(draw, f"{TOWER_LABELS[ti]}: {tw['name'].upper()}", int(lx), 1352, get_font(20, bold=True),
                               fill=TEXT_WHITE)
            if down:
                draw_centered_text(draw, "COLLAPSED", int(lx), 420, get_font(34, title=True), fill=ACCENT_RED)

        # Richter meter
        mag = 2.0 + 6.5 * (rec["amp"] / 70.0)
        peak_mag = 2.0 + 6.5 * (sim["peak_amp"] / 70.0)
        draw.rounded_rectangle([64, 1385, VIDEO_WIDTH - 64, 1495], radius=22,
                               fill=(18, 62, 42) if revealed else PANEL_BG_COLOR,
                               outline=ACCENT_GREEN if revealed else PANEL_BORDER_COLOR, width=3)
        if revealed:
            draw_centered_text(draw, f"TOWER {TOWER_LABELS[winner_idx]} STANDS LAST!", VIDEO_WIDTH // 2, 1394,
                               get_font(64, title=True), fill=TEXT_WHITE)
            draw_centered_text(draw, f"{towers[winner_idx]['name']} outlasted a magnitude {peak_mag:.1f} quake",
                               VIDEO_WIDTH // 2, 1460, get_font(26, bold=True), fill=ACCENT_YELLOW)
        else:
            draw.text((92, 1400), "MAGNITUDE", font=get_font(26, bold=True), fill=ACCENT_YELLOW)
            draw.text((92, 1432), f"{mag:.1f}", font=get_font(48, title=True), fill=TEXT_WHITE)
            bar_x0, bar_x1 = 260, VIDEO_WIDTH - 100
            draw.rounded_rectangle([bar_x0, 1428, bar_x1, 1466], radius=12, fill=(20, 22, 38))
            frac = rec["amp"] / 70.0
            bar_col = ACCENT_GREEN if frac < 0.4 else (ACCENT_YELLOW if frac < 0.75 else ACCENT_RED)
            if frac > 0.01:
                draw.rounded_rectangle([bar_x0, 1428, bar_x0 + (bar_x1 - bar_x0) * frac, 1466], radius=12, fill=bar_col)
            draw.text((bar_x0, 1398), "WHICH TOWER STANDS LAST?  A · B · C", font=get_font(24, bold=True), fill=TEXT_MUTED)
        return frame

    video_path = produce_physics_video(
        file_stem=f"tower_quake_{seed}",
        output_dir=output_dir,
        total_duration=TOTAL_DURATION,
        countdown_start_sec=COUNTDOWN_START_SEC,
        reveal_start_sec=REVEAL_START_SEC,
        outro_start_sec=OUTRO_START_SEC,
        hook_spoken="Earthquake incoming! Which tower is the last one standing: A, B, or C?",
        reveal_spoken=f"Tower {TOWER_LABELS[winner_idx]}, the {towers[winner_idx]['name']}, is the last one standing!",
        sfx_events=sfx,
        draw_frame=draw_frame,
        series_badge="EARTHQUAKE TOWER SURVIVAL",
        hook_title="WHICH TOWER STANDS LAST?",
        subtitle_line=f"{' vs '.join(designs)}  •  Seed: {seed}",
        footer_credit=f"Quake seed: {seed} • Physics: Pymunk 2D",
        wildcard=wildcard,
    )

    return {
        "format": "tower_quake",
        "seed": seed,
        "video_path": video_path,
        "duration_sec": TOTAL_DURATION,
        "winner": winner_label,
        "winner_tower": TOWER_LABELS[winner_idx],
        "designs": designs,
        "series_short": "Tower Quake",
        "topic_short": "Earthquake",
        "reveal_timestamp_sec": REVEAL_START_SEC,
        "comment_question": (
            f"{winner_label} stood last! 🏗️ Did you call it? Which tower design should face the next quake? 👇"
        ),
    }
