"""
Call It Arena — Marble League SEASON RECAP long-form video (`generators/season_recap.py`).

A 16:9 (1920x1080, 30fps) broadcast-style "full season" film rendered at the end of every season
(and on demand as a season-so-far preview). Every race is shown with REAL footage replayed from
verified data (`generators/race_replay.py`), narrated by the commentary engine with Piper TTS.

Running order (chapters are written into the YouTube description automatically):
  Cold open (no spoilers) -> Title -> The 16 nations -> Ch.1 Group Stage -> table check ->
  Ch.2 Knockouts -> table check -> Ch.3 Championship Finals -> Ch.4 Grand Final ->
  Champion ceremony -> Season awards -> The title race (animated points race) ->
  Final table -> Next season teaser -> End screen (20s, safe zones for YouTube end-screen cards)

Never mutates league state. Renders only on GitHub Actions (ffmpeg is never invoked locally).
"""

import copy
import hashlib
import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from common.audio import SfxEvent, VoiceCue, mix_master_audio
from common.branding import (
    ACCENT_GREEN,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_COLOR,
    COUNTRIES_META,
    OUTPUT_DIR,
    TEXT_WHITE,
    get_font,
    load_circular_flag_icon,
)
from common.commentary import NUM_WORDS, analyze_race, country_name, finish_threshold_for, recap_race_lines
from common.physics_kit import RotatingIconCache
from common.render import FFmpegFramePipe
from common.season_theme import SeasonTheme, blend_rgb, draw_beveled_panel, get_season_theme
from common.tts import synthesize_phrase
from generators.race_replay import RaceReplay, build_season_replays, points_multiplier_for, stage_for_race

logger = logging.getLogger("call_it_arena")

W, H, FPS = 1920, 1080, 30
CROP_Y0, CROP_Y1 = 340, 1400  # portrait arena region (1080 x 1060)
VIEW_X, VIEW_Y, VIEW_W, VIEW_H = 40, 66, 907, 890
PANEL_X0, PANEL_X1 = 985, 1880
CAPTION_BOX = (300, 978, 1620, 1050)
POINTS = [10, 7, 5, 3, 1]
GOLD = (255, 204, 64)
SILVER = (205, 212, 226)
BRONZE = (214, 138, 72)
MEDALS = [GOLD, SILVER, BRONZE]
STAGE_CHAPTERS = [
    ("Group Stage", "CHAPTER 1", "THE GROUP STAGE", "Races 1-14  •  Top five score 10-7-5-3-1"),
    ("Knockout Stage", "CHAPTER 2", "THE KNOCKOUTS", "Races 15-21  •  Last place is eliminated"),
    ("Championship Finals", "CHAPTER 3", "CHAMPIONSHIP FINALS", "Races 22-27  •  Double points"),
    ("Monthly Grand Final", "CHAPTER 4", "THE GRAND FINAL", "Race 28  •  Triple points  •  Mega gauntlet"),
]


# =========================================================================== small drawing helpers
def ease_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0]


def fit_font(draw: ImageDraw.ImageDraw, text: str, max_w: int, sizes: Sequence[int], title: bool = True):
    for s in sizes:
        f = get_font(s, title=title)
        if _text_w(draw, text, f) <= max_w:
            return f
    return get_font(sizes[-1], title=title)


def text_c(draw: ImageDraw.ImageDraw, text: str, cx: float, y: float, font, fill) -> None:
    b = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (b[2] - b[0]) / 2 - b[0], y - b[1]), text, font=font, fill=fill)


def text_r(draw: ImageDraw.ImageDraw, text: str, rx: float, y: float, font, fill) -> None:
    b = draw.textbbox((0, 0), text, font=font)
    draw.text((rx - (b[2] - b[0]) - b[0], y - b[1]), text, font=font, fill=fill)


def text_l(draw: ImageDraw.ImageDraw, text: str, x: float, y: float, font, fill) -> None:
    b = draw.textbbox((0, 0), text, font=font)
    draw.text((x - b[0], y - b[1]), text, font=font, fill=fill)


_FLAG_CACHE: Dict[Tuple[str, int, Tuple[int, int, int], int], Image.Image] = {}


def flag(code: str, d: int, border=(255, 255, 255), bw: int = 3) -> Image.Image:
    key = (code, int(d), tuple(border), int(bw))
    if key not in _FLAG_CACHE:
        _FLAG_CACHE[key] = load_circular_flag_icon(code, diameter=int(d), border_color=tuple(border), border_width=bw)
    return _FLAG_CACHE[key]


def paste_flag(img: Image.Image, code: str, cx: float, cy: float, d: int, border=(255, 255, 255), bw: int = 3) -> None:
    ic = flag(code, d, border, bw)
    img.paste(ic, (int(cx - d / 2), int(cy - d / 2)), ic)


def wide_backdrop(theme: SeasonTheme) -> Image.Image:
    """Smooth stadium gradient (numpy) with subtle vignette; works for the classic S1 palette too."""
    import numpy as np

    center = blend_rgb(theme.bg_center, theme.panel_bg, 0.55) if theme.is_classic else theme.bg_center
    outer = theme.bg_outer
    ys = np.linspace(-1.0, 1.0, H, dtype=np.float32)[:, None]
    xs = np.linspace(-1.0, 1.0, W, dtype=np.float32)[None, :]
    d = np.clip(np.sqrt((xs * 0.85) ** 2 + (ys * 1.15) ** 2), 0.0, 1.0)
    f = d * d * (3.0 - 2.0 * d)
    c_in = np.array(center, dtype=np.float32)[None, None, :]
    c_out = np.array(outer, dtype=np.float32)[None, None, :]
    rgb = c_in + (c_out - c_in) * f[:, :, None]
    dither = (((np.arange(H)[:, None] * 3 + np.arange(W)[None, :] * 5) % 4) - 1.5) * 0.3
    rgb = np.clip(rgb + dither[:, :, None], 0, 255).astype(np.uint8)
    return Image.fromarray(rgb)


def panel(draw: ImageDraw.ImageDraw, theme: SeasonTheme, box, accent=None, radius: int = 20) -> None:
    draw_beveled_panel(draw, tuple(int(v) for v in box), radius=radius, fill=theme.panel_bg,
                       outer_border=accent or theme.panel_border, outer_width=3, inner_border=theme.panel_border,
                       top_accent=accent)


# =========================================================================== season data model
@dataclass
class SeasonData:
    season: int
    theme: SeasonTheme
    replays: List[RaceReplay]
    max_races: int
    complete: bool
    points_after: List[Dict[str, float]]  # cumulative points after each replay (same index)
    wins: Dict[str, int]
    podiums: Dict[str, int]
    seconds: Dict[str, int]
    eliminated_at: Dict[str, int]
    final_table: List[Dict[str, Any]]
    champion: Optional[str]
    wildcards: Dict[str, int] = field(default_factory=dict)
    awarded: List[Dict[str, float]] = field(default_factory=list)


def _race_order(rep: RaceReplay, entry: Dict[str, Any]) -> Tuple[List[str], bool]:
    """Full finishing order: recorded history first, else the verified replay's order, else the podium."""
    if entry.get("finish_order"):
        return list(entry["finish_order"]), True
    if rep.available and rep.full_finish_order:
        return list(rep.full_finish_order), True
    return list(entry.get("podium") or []), False


def _awarded(rep: RaceReplay, entry: Dict[str, Any]) -> Tuple[Dict[str, float], bool]:
    mult = int(entry.get("points_multiplier") or points_multiplier_for(rep.stage))
    order, full = _race_order(rep, entry)
    pts: Dict[str, float] = {}
    for i, c in enumerate(order[: 5 if full else 3]):
        pts[c] = pts.get(c, 0.0) + POINTS[i] * mult
    return pts, full


def build_season_data(state: Dict[str, Any], season: int, replays: List[RaceReplay]) -> SeasonData:
    from generators.marble_league import LEAGUE_COUNTRY_CODES

    max_races = int(state.get("max_races_per_season", 28))
    entries = {int(e["race"]): e for e in state.get("history", []) if int(e.get("season", 0)) == season}
    codes = list(LEAGUE_COUNTRY_CODES)

    cum: Dict[str, float] = {c: 0.0 for c in codes}
    raw_after: List[Dict[str, float]] = []
    partial_idx: List[int] = []
    for i, rep in enumerate(replays):
        e = entries.get(rep.race, {})
        pts, full = _awarded(rep, e)
        if not full:
            partial_idx.append(i)
        for c, p in pts.items():
            cum[c] = cum.get(c, 0.0) + p
        raw_after.append(dict(cum))

    # Calibrate races whose 4th/5th places were never recorded against the first exact reference.
    ref_i, ref_pts = None, None
    for i, rep in enumerate(replays):
        pa = entries.get(rep.race, {}).get("points_after")
        if pa:
            ref_i, ref_pts = i, {c: float(v) for c, v in pa.items()}
            break
    archive = (state.get("season_archive") or {}).get(str(season))
    if ref_pts is None and archive:
        ref_i, ref_pts = len(replays) - 1, {r["code"]: float(r["points"]) for r in archive["final_standings"]}
    if ref_pts is None and int(state.get("season", 0)) == season and replays:
        ref_i = len(replays) - 1
        ref_pts = {c: float(v.get("points", 0)) for c, v in state.get("countries", {}).items()}
    points_after = [dict(p) for p in raw_after]
    if ref_pts is not None and ref_i is not None:
        missing = [i for i in partial_idx if i <= ref_i]
        if missing:
            for c in codes:
                residual = ref_pts.get(c, 0.0) - raw_after[ref_i].get(c, 0.0)
                if abs(residual) < 1e-6:
                    continue
                share = residual / len(missing)
                for i in range(len(points_after)):
                    k = sum(1 for m in missing if m <= i)
                    points_after[i][c] = points_after[i].get(c, 0.0) + share * k

    wins: Dict[str, int] = {}
    pods: Dict[str, int] = {}
    seconds: Dict[str, int] = {}
    elim: Dict[str, int] = {}
    wild: Dict[str, int] = {}
    tb_after: List[Dict[str, Tuple[int, int]]] = []
    awarded: List[Dict[str, float]] = []
    for rep in replays:
        e = entries.get(rep.race, {})
        order, _full = _race_order(rep, e)
        awarded.append(_awarded(rep, e)[0])
        if order:
            wins[order[0]] = wins.get(order[0], 0) + 1
            for c in order[:3]:
                pods[c] = pods.get(c, 0) + 1
            if len(order) > 1:
                seconds[order[1]] = seconds.get(order[1], 0) + 1
        if e.get("eliminated"):
            elim[str(e["eliminated"])] = rep.race
        wc = e.get("wildcard") or {}
        if wc.get("code"):
            wild[str(wc["code"])] = wild.get(str(wc["code"]), 0) + 1
        tb_after.append({c: (wins.get(c, 0), pods.get(c, 0)) for c in codes})
    _TIEBREAKS.clear()
    for i, pa in enumerate(points_after):
        _TIEBREAKS[id(pa)] = tb_after[i]

    last_pts = points_after[-1] if points_after else {c: 0.0 for c in codes}
    if archive:
        final_table = list(archive["final_standings"])
    else:
        order_final = ranking(last_pts, list(elim.keys()))
        final_table = [
            {"code": c, "name": country_name(c), "points": int(round(last_pts.get(c, 0.0))), "wins": wins.get(c, 0),
             "podiums": pods.get(c, 0), "eliminated": c in elim}
            for c in order_final
        ]
    complete = bool(archive) or (len(replays) >= max_races)
    champion = (archive or {}).get("champion") or (final_table[0]["code"] if complete and final_table else None)
    return SeasonData(season, get_season_theme(season), replays, max_races, complete, points_after, wins, pods,
                      seconds, elim, final_table, champion, wild, awarded)


_TIEBREAKS: Dict[int, Dict[str, Tuple[int, int]]] = {}


def ranking(points: Dict[str, float], eliminated: Sequence[str] = ()) -> List[str]:
    """Same order as the live league table: active first, then points, wins, podiums (India wins exact ties)."""
    tb = _TIEBREAKS.get(id(points), {})
    return sorted(
        points.keys(),
        key=lambda c: (0 if c in eliminated else 1, round(points[c], 6), tb.get(c, (0, 0))[0], tb.get(c, (0, 0))[1],
                       c == "IN"),
        reverse=True,
    )


# =========================================================================== segment framework
@dataclass
class Segment:
    name: str
    duration: float
    render: Callable[[float], Image.Image]
    voice: List[Tuple[float, str]] = field(default_factory=list)  # local times
    sfx: List[SfxEvent] = field(default_factory=list)  # local times
    chapter: Optional[str] = None
    crossfade: bool = True


class VoiceBank:
    """Synthesizes lines once and reports durations so segment timings can fit the narration."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.cache: Dict[str, Tuple[Path, float]] = {}

    def get(self, text: str) -> Tuple[Path, float]:
        if text not in self.cache:
            self.cache[text] = synthesize_phrase(text, self.out_dir)
        return self.cache[text]

    def dur(self, text: str) -> float:
        return self.get(text)[1]


def layout_lines(bank: VoiceBank, lines: List[Tuple[float, str, int]], seg_end: float, gap: float = 0.3
                 ) -> List[Tuple[float, str]]:
    """Places (t, text, priority) lines without overlap; higher priority first; slides later when busy."""
    placed: List[Tuple[float, float, str]] = []
    for t, text, _pr in sorted(lines, key=lambda x: -x[2]):
        d = bank.dur(text)
        start = t
        for a, b, _ in sorted(placed):
            if start < b + gap and start + d > a - gap:
                start = b + gap
        if start + d > seg_end + 0.05 or start - t > 3.0:
            continue
        if any(start < b + gap and start + d > a - gap for a, b, _ in placed):
            continue
        placed.append((start, start + d, text))
    return [(a, txt) for a, _b, txt in sorted(placed)]


# =========================================================================== shared chrome
class Chrome:
    def __init__(self, data: SeasonData) -> None:
        self.data = data
        self.theme = data.theme
        self.bg = wide_backdrop(self.theme)

    def base(self, chapter_tag: str = "") -> Image.Image:
        img = self.bg.copy()
        d = ImageDraw.Draw(img)
        th = self.theme
        d.rounded_rectangle([40, 18, 330, 54], radius=10, fill=th.primary_accent)
        text_c(d, "CALL IT ARENA", 185, 25, get_font(26, title=True), BG_COLOR)
        text_l(d, f"MARBLE LEAGUE • SEASON {self.data.season}"
               + ("" if self.data.complete else " • SO FAR"), 346, 26, get_font(24, title=True), TEXT_WHITE)
        if chapter_tag:
            text_r(d, chapter_tag, W - 40, 26, get_font(24, title=True), th.primary_accent)
        return img

    def caption(self, img: Image.Image, text: Optional[str]) -> None:
        if not text:
            return
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = CAPTION_BOX
        f = fit_font(d, text, x1 - x0 - 60, (40, 36, 32, 28))
        tw = _text_w(d, text, f)
        cx = (x0 + x1) / 2
        bx0, bx1 = cx - tw / 2 - 30, cx + tw / 2 + 30
        d.rounded_rectangle([bx0, y0, bx1, y1], radius=16, fill=(8, 9, 16), outline=self.theme.primary_accent, width=2)
        text_c(d, text, cx, y0 + 18, f, TEXT_WHITE)


# =========================================================================== arena viewport renderer
class ArenaView:
    """Renders a replay frame into the 907x890 viewport (portrait arena crop, scaled)."""

    def __init__(self, rep: RaceReplay, theme: SeasonTheme) -> None:
        from generators.marble_league import _draw_arena_background

        self.rep = rep
        self.theme = theme
        self.neon = not theme.is_classic
        full = None
        if self.neon and rep.arena is not None:
            from common.season_theme import render_seasonal_arena_backdrop

            full = render_seasonal_arena_backdrop(theme, rep.mode, rep.center, rep.finish_y)
            rep.drawer.render_static(full, neon_glow=True, glow_bg=theme.bg_center, color_map=theme.obstacle_remap)
        else:
            full = Image.new("RGB", (1080, 1920), BG_COLOR)
            fd = ImageDraw.Draw(full)
            if rep.arena is not None:
                _draw_arena_background(fd, rep.arena)
            else:
                cx, cy = rep.center
                for r in (160, 290, 420, 520):
                    fd.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(24, 27, 46), width=2)
            rep.drawer.render_static(full)
        self.static = full.crop((0, CROP_Y0, 1080, CROP_Y1))
        palette = theme.marble_palette
        self.colors = {c: (theme.primary_accent if c == rep.boosted_code else palette[i % len(palette)])
                       for i, c in enumerate(rep.race_codes)}
        r = int(rep.marble_radius)
        self.icons = {c: RotatingIconCache(load_circular_flag_icon(c, diameter=r * 2, border_color=self.colors[c],
                                                                   border_width=4)) for c in rep.race_codes}
        self.trails: Dict[str, List[Tuple[float, float]]] = {c: [] for c in rep.race_codes}

    def reset_trails(self) -> None:
        self.trails = {c: [] for c in self.rep.race_codes}

    def render(self, f_idx: int, highlight: Optional[str] = None, highlight_col=ACCENT_RED, trails: bool = True,
               t_anim: float = 0.0) -> Image.Image:
        rep = self.rep
        f_idx = max(0, min(f_idx, len(rep.frames) - 1))
        fr = rep.frames[f_idx]
        img = self.static.copy()
        d = ImageDraw.Draw(img)
        off = (0.0, -float(CROP_Y0))
        if self.neon:
            rep.drawer.render_dynamic(d, fr["kin"], offset=off, neon_glow=True, glow_bg=self.theme.bg_center,
                                      color_map=self.theme.obstacle_remap)
        else:
            rep.drawer.render_dynamic(d, fr["kin"], offset=off)
        r = rep.marble_radius
        for c in rep.race_codes:
            x, y, a = fr["marbles"][c]
            y -= CROP_Y0
            if not (-r <= x <= 1080 + r and -r <= y <= (CROP_Y1 - CROP_Y0) + r):
                continue
            if trails:
                tr = self.trails[c]
                tr.append((x, y))
                del tr[:-8]
                n = len(tr) - 1
                for i, (tx, ty) in enumerate(tr[:-1]):
                    frac = (i + 1) / float(n + 1)
                    rr = 2.0 + 7.0 * frac
                    d.ellipse([tx - rr, ty - rr, tx + rr, ty + rr],
                              fill=blend_rgb(self.theme.bg_center, self.colors[c], 0.25 + 0.7 * frac))
            if highlight == c:
                pulse = 6 + 4 * abs(math.sin(t_anim * 7.0))
                d.ellipse([x - r - pulse, y - r - pulse, x + r + pulse, y + r + pulse], outline=highlight_col, width=6)
            ic = self.icons[c].get(a)
            img.paste(ic, (int(x - r), int(y - r)), ic)
        return img.resize((VIEW_W, VIEW_H), Image.Resampling.BILINEAR)


def clip_window(rep: RaceReplay) -> Tuple[float, float]:
    end = (len(rep.frames) - 1) / float(FPS)
    t1 = rep.first_finish_t
    if rep.stage == "Monthly Grand Final":
        return 0.0, min(end, (t1 if t1 is not None else end - 3.0) + 3.0)
    if t1 is None:
        return max(0.0, end - 9.0), end
    return max(0.0, t1 - 7.0), min(end, t1 + 2.2)


def build_time_map(w0: float, w1: float, t1: Optional[float], slow: float = 0.35) -> List[float]:
    out: List[float] = []
    s = w0
    while s <= w1:
        out.append(s)
        in_slow = t1 is not None and (t1 - 0.6) <= s <= (t1 + 0.5)
        s += (1.0 / FPS) * (slow if in_slow else 1.0)
    return out


# =========================================================================== segment builders
class RecapBuilder:
    def __init__(self, data: SeasonData, bank: VoiceBank) -> None:
        self.data = data
        self.bank = bank
        self.chrome = Chrome(data)
        self.theme = data.theme
        self.season = data.season
        self.n_races = len(data.replays)

    # ---------------------------------------------------------------- cold open
    def cold_open(self) -> Segment:
        reps = [r for r in self.data.replays if r.available and r.frames]
        th = self.theme
        clips: List[Tuple[RaceReplay, float, float]] = []
        gf = next((r for r in reps if r.stage == "Monthly Grand Final"), None)
        if gf:
            clips.append((gf, 0.0, 4.5))
        ko = next((r for r in reversed(reps) if r.stage == "Knockout Stage"), None)
        if ko and ko.first_finish_t:
            clips.append((ko, max(0.0, ko.first_finish_t - 4.3), ko.first_finish_t - 0.4))
        tight = sorted((r for r in reps if len(r.finish_times) >= 2),
                       key=lambda r: sorted(r.finish_times.values())[1] - sorted(r.finish_times.values())[0])
        for r in tight:
            if all(r is not c[0] for c in clips) and r.first_finish_t:
                clips.append((r, max(0.0, r.first_finish_t - 4.0), r.first_finish_t - 0.35))
                break
        for r in reps:
            if len(clips) >= 3:
                break
            if all(r is not c[0] for c in clips):
                clips.append((r, 0.0, 4.0))
        clips = clips[:3]
        slams = (["28 RACES", "16 NATIONS", "ONE CHAMPION"] if self.data.complete
                 else [f"{self.n_races} RACES", "16 NATIONS", "ONE TITLE"])
        views = [ArenaView(r, th) for r, _, _ in clips]
        bounds: List[Tuple[float, float]] = []
        acc = 0.0
        for _r, a, b in clips:
            ln = max(2.5, b - a)
            bounds.append((acc, acc + ln))
            acc += ln
        dur = max(acc, 9.0)
        base = self.chrome.bg.copy()
        n_word = NUM_WORDS.get(self.n_races, str(self.n_races))
        line = (f"Twenty-eight races. Sixteen nations. Only one champion." if self.data.complete
                else f"{n_word} races in. Sixteen nations. One title.")
        line2 = f"This is Marble League, season {self.season}."

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            k = next((i for i, (a, b) in enumerate(bounds) if a <= t < b), len(bounds) - 1)
            if clips:
                rep, a, _b = clips[k]
                local = t - bounds[k][0]
                if local < 1.0 / FPS:
                    views[k].reset_trails()
                fi = int(round((a + local) * FPS))
                vx = (W - VIEW_W) // 2
                img.paste(views[k].render(fi, t_anim=t), (vx, VIEW_Y))
                d.rectangle([vx - 4, VIEW_Y - 4, vx + VIEW_W + 4, VIEW_Y + VIEW_H + 4], outline=th.primary_accent,
                            width=4)
                slam = slams[k % len(slams)]
                p = ease_out((local - 0.12) / 0.32)
                if p > 0:
                    word1, word2 = slam.split(" ")[0], " ".join(slam.split(" ")[1:])
                    left = k % 2 == 0
                    shift = 70 * (1 - p) * (-1 if left else 1)
                    f1 = get_font(int(230 + 40 * (1 - p)), title=True)
                    f2 = fit_font(d, word2, 380, (112, 96, 84))
                    col1 = blend_rgb(th.bg_outer, th.primary_accent, p)
                    col2 = blend_rgb(th.bg_outer, TEXT_WHITE, p)
                    if left:
                        x = 70 + shift
                        text_l(d, word1, x, 300, f1, col1)
                        d.rectangle([x, 560, x + 300 * p, 568], fill=th.primary_accent)
                        text_l(d, word2, x, 590, f2, col2)
                    else:
                        x = W - 70 + shift
                        text_r(d, word1, x, 300, f1, col1)
                        d.rectangle([x - 300 * p, 560, x, 568], fill=th.primary_accent)
                        text_r(d, word2, x, 590, f2, col2)
            self.chrome.caption(img, _active(lines, self.bank, t))
            return img

        lines = layout_lines(self.bank, [(0.4, line, 3), (dur - self.bank.dur(line2) - 0.3, line2, 2)], dur)
        return Segment("cold_open", dur, render, voice=lines, chapter="Cold open", crossfade=False)

    # ---------------------------------------------------------------- title
    def title(self) -> Segment:
        from generators.marble_league import LEAGUE_COUNTRY_CODES

        th = self.theme
        edition = "CLASSIC ARENA" if th.is_classic else f"{th.edition_name} EDITION"
        base = self.chrome.bg.copy()
        codes = list(LEAGUE_COUNTRY_CODES)

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            # 16-flag parade sweeping in along the bottom
            for i, c in enumerate(codes):
                fp = ease_out((t - 0.15 - i * 0.05) / 0.45)
                if fp <= 0:
                    continue
                cx = W / 2 + (i - 7.5) * 104
                cy = 870 + 60 * (1 - fp)
                paste_flag(img, c, cx, cy, int(78 * (0.7 + 0.3 * fp)),
                           border=th.marble_palette[i % len(th.marble_palette)], bw=3)
            p = ease_out(t / 0.8)
            text_c(d, "MARBLE LEAGUE", W / 2, 230 - 40 * (1 - p), get_font(int(190 + 20 * (1 - p)), title=True),
                   blend_rgb(th.bg_outer, TEXT_WHITE, p))
            q = ease_out((t - 0.5) / 0.6)
            if q > 0:
                bar_w = int(980 * q)
                d.rectangle([W / 2 - bar_w / 2, 470, W / 2 + bar_w / 2, 477], fill=th.primary_accent)
                sub = f"SEASON {self.season}  —  " + ("THE FULL SEASON" if self.data.complete else "THE STORY SO FAR")
                text_c(d, sub, W / 2, 505, get_font(84, title=True), blend_rgb(th.bg_outer, th.primary_accent, q))
            r = ease_out((t - 1.1) / 0.6)
            if r > 0:
                ew = _text_w(d, edition, get_font(38, title=True)) + 60
                d.rounded_rectangle([W / 2 - ew / 2, 640, W / 2 + ew / 2, 698], radius=29,
                                    outline=blend_rgb(th.bg_outer, th.text_muted, r), width=2)
                text_c(d, edition, W / 2, 652, get_font(38, title=True), blend_rgb(th.bg_outer, th.text_muted, r))
            return img

        return Segment("title", 4.8, render, sfx=[SfxEvent(0.25, "reveal_fanfare", 0.8, 1.0)])

    # ---------------------------------------------------------------- roster
    def roster(self) -> Segment:
        from generators.marble_league import LEAGUE_COUNTRY_CODES

        th = self.theme
        base = self.chrome.base("THE NATIONS")
        bd = ImageDraw.Draw(base)
        text_c(bd, "THE 16 NATIONS", W / 2, 92, get_font(76, title=True), TEXT_WHITE)
        codes = list(LEAGUE_COUNTRY_CODES)
        line = "Sixteen nations. Group stage, knockouts, finals, and one Grand Final."
        dur = max(8.0, self.bank.dur(line) + 1.8)
        cells = []
        for i, c in enumerate(codes):
            col, row = i % 4, i // 4
            cells.append((c, 300 + col * 440, 270 + row * 175))
        sfx = [SfxEvent(0.5 + i * 0.12, "tick", 0.5, 1.0 + (i % 4) * 0.08) for i in range(len(codes))]

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            for i, (c, cx, cy) in enumerate(cells):
                p = ease_out((t - 0.5 - i * 0.12) / 0.35)
                if p <= 0:
                    continue
                dd = int(96 * (0.6 + 0.4 * p))
                panel(d, th, (cx - 200, cy - 70, cx + 200, cy + 70), radius=18)
                paste_flag(img, c, cx - 120, cy, dd, border=th.marble_palette[i % len(th.marble_palette)], bw=4)
                text_l(d, country_name(c).upper(), cx - 50, cy - 26, fit_font(d, country_name(c).upper(), 230,
                                                                               (52, 44, 38)), TEXT_WHITE)
            return img

        return Segment("roster", dur, render, voice=layout_lines(self.bank, [(0.6, line, 2)], dur), sfx=sfx,
                       chapter="The 16 nations")

    # ---------------------------------------------------------------- chapter card
    def chapter_card(self, idx: int, tag: str, title: str, sub: str) -> Segment:
        th = self.theme
        base = self.chrome.bg.copy()
        spoken = {0: "Chapter one. The group stage.", 1: "Chapter two. The knockouts. Finish last, and you're out.",
                  2: "Chapter three. The championship finals. Double points.",
                  3: "Chapter four. The Grand Final."}[idx]
        dur = max(3.6, self.bank.dur(spoken) + 1.0)

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            p = ease_out(t / 0.5)
            text_c(d, tag, W / 2, 330, get_font(48, title=True), blend_rgb(th.bg_outer, th.primary_accent, p))
            text_c(d, title, W / 2 + 80 * (1 - p), 400, get_font(150, title=True), blend_rgb(th.bg_outer, TEXT_WHITE, p))
            q = ease_out((t - 0.4) / 0.5)
            d.rectangle([W / 2 - 420 * q, 585, W / 2 + 420 * q, 590], fill=th.primary_accent)
            if q > 0:
                text_c(d, sub, W / 2, 615, get_font(44, title=True), blend_rgb(th.bg_outer, th.text_muted, q))
            return img

        return Segment(f"chapter_{idx}", dur, render, voice=layout_lines(self.bank, [(0.3, spoken, 2)], dur),
                       sfx=[SfxEvent(0.05, "countdown_beep", 0.5, 0.8)], chapter=title.title())

    # ---------------------------------------------------------------- race segment
    def race(self, i: int) -> Segment:
        rep = self.data.replays[i]
        th = self.theme
        before = self.data.points_after[i - 1] if i > 0 else {c: 0.0 for c in self.data.points_after[0]}
        after = self.data.points_after[i]
        elim_before = [c for c, r in self.data.eliminated_at.items() if r < rep.race]
        elim_after = [c for c, r in self.data.eliminated_at.items() if r <= rep.race]
        rank_b = ranking(before, elim_before)
        rank_a = ranking(after, elim_after)
        leader_before = (rank_b[0], int(round(before[rank_b[0]]))) if i > 0 else None
        winner = rep.winner
        w_rank_after = rank_a.index(winner) + 1 if winner in rank_a else None

        events = None
        if rep.available:
            thr = finish_threshold_for(rep.mode, rep.center, rep.escape_radius, rep.finish_y, rep.marble_radius)
            events = analyze_race(rep.frames, rep.race_codes, rep.finish_times, rep.full_finish_order,
                                  rep.progress, thr)
        lines = recap_race_lines(rep.seed, rep.race, rep.stage, rep.label or rep.variant.replace("_", " ").title(),
                                 len(rep.race_codes), rep.points_multiplier, events, rep.full_finish_order,
                                 rep.eliminated, leader_before, w_rank_after, (rank_a[0], int(round(after[rank_a[0]]))))
        if not rep.available:
            return self._result_only(i, rep, lines, before, after, rank_b, rank_a, elim_after)

        view = ArenaView(rep, th)
        w0, w1 = clip_window(rep)
        tmap = build_time_map(w0, w1, rep.first_finish_t)
        intro_d = max(2.0, self.bank.dur(lines["intro"]) + 0.5)
        act_d = len(tmap) / float(FPS)
        res_lines = [lines["result"]] + ([lines["table"]] if lines["table"] else [])
        res_d = max(4.2, sum(self.bank.dur(x) for x in res_lines) + 0.4 * len(res_lines) + 0.8)
        dur = intro_d + act_d + res_d
        is_ko = rep.stage == "Knockout Stage" and bool(rep.eliminated)

        def local_for_sim(st: float) -> float:
            for k, s in enumerate(tmap):
                if s >= st:
                    return intro_d + k / float(FPS)
            return intro_d + act_d

        voice: List[Tuple[float, str, int]] = [(0.2, lines["intro"], 3)]
        for st, txt in lines["action"]:
            if w0 <= st <= w1:
                voice.append((local_for_sim(st), txt, 1))
        if rep.first_finish_t is not None:
            voice.append((local_for_sim(rep.first_finish_t) + 0.05, lines["finish"], 4))
        voice.append((intro_d + act_d + 0.3, lines["result"], 3))
        if lines["table"]:
            voice.append((intro_d + act_d + 0.3 + self.bank.dur(lines["result"]) + 0.35, lines["table"], 2))
        placed = layout_lines(self.bank, voice, dur)

        sfx: List[SfxEvent] = []
        for ev in rep.sfx_events:
            if w0 <= ev.timestamp_sec <= w1:
                sfx.append(SfxEvent(local_for_sim(ev.timestamp_sec), ev.kind, ev.gain * 0.55, ev.pitch_shift))
        sfx.append(SfxEvent(intro_d + act_d + 0.05, "reveal_fanfare", 0.55, 1.0))

        mult_txt = {1: "", 2: "  •  DOUBLE POINTS", 3: "  •  TRIPLE POINTS"}.get(rep.points_multiplier, "")
        head = f"RACE {rep.race:02d}  •  {(rep.label or rep.variant).upper()}"
        stage_tag = rep.stage.upper().replace("MONTHLY ", "")
        base = self.chrome.base(stage_tag)
        bd = ImageDraw.Draw(base)
        bd.rectangle([VIEW_X - 4, VIEW_Y - 4, VIEW_X + VIEW_W + 4, VIEW_Y + VIEW_H + 4], outline=th.panel_border, width=3)
        panel(bd, th, (PANEL_X0, VIEW_Y, PANEL_X1, VIEW_Y + 128), accent=th.primary_accent)
        text_l(bd, head, PANEL_X0 + 28, VIEW_Y + 22, fit_font(bd, head, PANEL_X1 - PANEL_X0 - 56, (54, 46, 40)),
               TEXT_WHITE)
        text_l(bd, f"{len(rep.race_codes)} NATIONS{mult_txt}", PANEL_X0 + 28, VIEW_Y + 86, get_font(30, title=True),
               th.primary_accent)
        last_frame_idx = len(rep.frames) - 1
        state = {"ypos": {}}

        def live_order(fidx: int) -> List[str]:
            fr = rep.frames[min(fidx, last_frame_idx)]
            t = fidx / float(FPS)
            fin = sorted([c for c, ft in rep.finish_times.items() if ft <= t], key=lambda c: rep.finish_times[c])
            rest = [c for c in rep.race_codes if c not in fin]
            rest.sort(key=lambda c: rep.progress(fr["marbles"][c][0], fr["marbles"][c][1]), reverse=True)
            return fin + rest

        def draw_board(img: Image.Image, title: str, order: List[str], finished: Sequence[str], dt_anim: float,
                       pts_map: Optional[Dict[str, str]] = None, red: Optional[str] = None) -> None:
            d = ImageDraw.Draw(img)
            y0 = VIEW_Y + 150
            rows = min(12, len(order))
            row_h = 60
            panel(d, th, (PANEL_X0, y0, PANEL_X1, y0 + 62 + rows * row_h + 14))
            text_l(d, title, PANEL_X0 + 28, y0 + 18, get_font(32, title=True), th.text_muted)
            yp = state["ypos"]
            for k, c in enumerate(order[:rows]):
                target = y0 + 62 + k * row_h
                cur = yp.get(c, target)
                cur += (target - cur) * min(1.0, dt_anim * 12.0)
                yp[c] = cur
            for k, c in enumerate(order[:rows]):
                y = yp[c]
                fill = th.panel_win_row if k == 0 else (th.panel_bg_alt if k % 2 else th.panel_bg)
                if red == c:
                    fill = (90, 24, 34)
                d.rounded_rectangle([PANEL_X0 + 16, y, PANEL_X1 - 16, y + row_h - 8], radius=10, fill=fill)
                pos_col = MEDALS[k] if k < 3 else th.text_muted
                text_c(d, f"{k + 1}", PANEL_X0 + 52, y + 9, get_font(36, title=True), pos_col)
                paste_flag(img, c, PANEL_X0 + 112, y + (row_h - 8) / 2, 40, border=self_colors.get(c, TEXT_WHITE), bw=2)
                text_l(d, country_name(c).upper(), PANEL_X0 + 148, y + 9, get_font(36, title=True), TEXT_WHITE)
                if pts_map and c in pts_map:
                    text_r(d, pts_map[c], PANEL_X1 - 36, y + 9, get_font(36, title=True), th.win_accent)
                elif c in finished:
                    text_r(d, "OUT" if rep.mode == "escape" else "FINISHED", PANEL_X1 - 36, y + 13,
                           get_font(28, title=True), th.win_accent)

        self_colors = view.colors

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            if t < intro_d:
                fidx = int(round(w0 * FPS))
                if t < 1.0 / FPS:
                    view.reset_trails()
                    state["ypos"] = {}
                img.paste(view.render(fidx, trails=False), (VIEW_X, VIEW_Y))
                p = ease_out(t / 0.45)
                lx = VIEW_X + 24 - 400 * (1 - p)
                d.rounded_rectangle([lx, VIEW_Y + VIEW_H - 120, lx + 640, VIEW_Y + VIEW_H - 24], radius=14,
                                    fill=th.panel_bg, outline=th.primary_accent, width=3)
                text_l(d, f"RACE {rep.race}", lx + 26, VIEW_Y + VIEW_H - 108, get_font(48, title=True), th.primary_accent)
                text_l(d, (rep.label or "").upper(), lx + 26, VIEW_Y + VIEW_H - 62, get_font(34, title=True), TEXT_WHITE)
                # pre-race table (top 8)
                pts_map = {c: f"{int(round(before.get(c, 0)))}" for c in rank_b}
                draw_board(img, "TABLE BEFORE THE RACE", [c for c in rank_b if c not in elim_before] or rank_b,
                           [], 1.0, pts_map=pts_map)
            elif t < intro_d + act_d:
                k = min(len(tmap) - 1, int((t - intro_d) * FPS))
                fidx = int(round(tmap[k] * FPS))
                img.paste(view.render(fidx, t_anim=t), (VIEW_X, VIEW_Y))
                if t - intro_d < 1.0 / FPS:
                    state["ypos"] = {}
                    state.pop("order", None)
                if k % 10 == 0 or "order" not in state:
                    state["order"] = live_order(fidx)
                order = state["order"]
                fin = [c for c, ft in rep.finish_times.items() if ft <= fidx / float(FPS)]
                draw_board(img, "LIVE ORDER", order, fin, 1.0 / FPS)
                if rep.first_finish_t is not None and abs(tmap[k] - rep.first_finish_t) < 0.5:
                    d.rounded_rectangle([VIEW_X + VIEW_W - 250, VIEW_Y + 20, VIEW_X + VIEW_W - 20, VIEW_Y + 70],
                                        radius=10, fill=th.secondary_accent)
                    text_c(d, "SLOW MOTION", VIEW_X + VIEW_W - 135, VIEW_Y + 30, get_font(30, title=True), TEXT_WHITE)
            else:
                lt = t - intro_d - act_d
                fidx = last_frame_idx if is_ko else int(round(tmap[-1] * FPS))
                img.paste(view.render(fidx, highlight=rep.eliminated if is_ko else None, trails=False, t_anim=t),
                          (VIEW_X, VIEW_Y))
                # podium strip over the viewport
                p = ease_out(lt / 0.45)
                sy = VIEW_Y + VIEW_H - 170 + 200 * (1 - p)
                d.rounded_rectangle([VIEW_X + 20, sy, VIEW_X + VIEW_W - 20, sy + 150], radius=18, fill=th.panel_bg,
                                    outline=th.primary_accent, width=3)
                for k, c in enumerate(rep.full_finish_order[:3]):
                    cx = VIEW_X + 20 + (VIEW_W - 40) * (k + 0.5) / 3
                    paste_flag(img, c, cx - 70, sy + 75, 84, border=MEDALS[k], bw=5)
                    text_l(d, ["1ST", "2ND", "3RD"][k], cx - 18, sy + 30, get_font(34, title=True), MEDALS[k])
                    text_l(d, f"+{POINTS[k] * rep.points_multiplier}", cx - 18, sy + 74, get_font(46, title=True),
                           TEXT_WHITE)
                if is_ko:
                    q = ease_out((lt - 0.6) / 0.4)
                    if q > 0:
                        bw_ = int(600 * q)
                        d.rounded_rectangle([VIEW_X + VIEW_W / 2 - bw_ / 2, VIEW_Y + 40, VIEW_X + VIEW_W / 2 + bw_ / 2,
                                             VIEW_Y + 130], radius=14, fill=(150, 28, 40), outline=TEXT_WHITE, width=3)
                        if q > 0.6:
                            text_c(d, f"KNOCKED OUT: {country_name(rep.eliminated).upper()}", VIEW_X + VIEW_W / 2,
                                   VIEW_Y + 62, get_font(48, title=True), TEXT_WHITE)
                if lt < 1.0 / FPS:
                    state["ypos"] = {c: VIEW_Y + 150 + 62 + rank_b.index(c) * 60 for c in rank_b[:12] if c in rank_b}
                pts_map = {}
                won = self.data.awarded[i] if i < len(self.data.awarded) else {}
                for c in rank_a:
                    gain = int(round(won.get(c, 0)))
                    pts_map[c] = f"{int(round(after.get(c, 0)))}" + (f" (+{gain})" if gain > 0 else "")
                draw_board(img, "SEASON TABLE", rank_a, [], 1.0 / FPS if lt > 0.5 else 0.0, pts_map=pts_map,
                           red=rep.eliminated if is_ko else None)
            self.chrome.caption(img, _active(placed, self.bank, t))
            return img

        return Segment(f"race_{rep.race}", dur, render, voice=placed, sfx=sfx)

    def _result_only(self, i, rep, lines, before, after, rank_b, rank_a, elim_after) -> Segment:
        th = self.theme
        base = self.chrome.base(rep.stage.upper().replace("MONTHLY ", ""))
        txts = [lines["result"]] + ([lines["table"]] if lines["table"] else [])
        dur = max(5.0, sum(self.bank.dur(x) for x in txts) + 1.6)
        voice = layout_lines(self.bank, [(0.4, txts[0], 3)] + ([(0.4 + self.bank.dur(txts[0]) + 0.35, txts[1], 2)]
                                                              if len(txts) > 1 else []), dur)

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            p = ease_out(t / 0.5)
            panel(d, th, (200, 140, W - 200, 900), accent=th.primary_accent)
            text_c(d, f"RACE {rep.race}  •  {(rep.label or '').upper()}", W / 2, 180, get_font(64, title=True),
                   TEXT_WHITE)
            text_c(d, "RESULT", W / 2, 262, get_font(40, title=True), th.primary_accent)
            for k, c in enumerate(rep.full_finish_order[:3]):
                cx = W / 2 + (k - 1) * 420
                hgt = [300, 230, 180][k]
                top = 840 - hgt * p
                d.rounded_rectangle([cx - 150, top, cx + 150, 840], radius=16, fill=MEDALS[k])
                text_c(d, ["1ST", "2ND", "3RD"][k], cx, top + 20, get_font(64, title=True), BG_COLOR)
                paste_flag(img, c, cx, top - 90, 150, border=MEDALS[k], bw=6)
                text_c(d, country_name(c).upper(), cx, top + 100, get_font(44, title=True), BG_COLOR)
            self.chrome.caption(img, _active(voice, self.bank, t))
            return img

        return Segment(f"race_{rep.race}", dur, render, voice=voice,
                       sfx=[SfxEvent(0.3, "reveal_fanfare", 0.55, 1.0)])

    # ---------------------------------------------------------------- standings table
    def table(self, title: str, pts: Dict[str, float], elim: Sequence[str], spoken: str, tag: str) -> Segment:
        th = self.theme
        order = ranking(pts, elim)
        base = self.chrome.base(tag)
        dur = max(7.0, self.bank.dur(spoken) + 2.0)
        voice = layout_lines(self.bank, [(0.5, spoken, 2)], dur)

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            text_c(d, title, W / 2, 80, get_font(70, title=True), TEXT_WHITE)
            for k, c in enumerate(order):
                col, row = k // 8, k % 8
                p = ease_out((t - 0.2 - k * 0.06) / 0.4)
                if p <= 0:
                    continue
                x0 = 120 + col * 860 - 60 * (1 - p)
                y = 190 + row * 96
                out = c in elim
                fill = (60, 22, 30) if out else (th.panel_win_row if k == 0 else th.panel_bg)
                d.rounded_rectangle([x0, y, x0 + 820, y + 82], radius=14, fill=fill, outline=th.panel_border, width=2)
                text_c(d, f"{k + 1}", x0 + 50, y + 18, get_font(46, title=True), MEDALS[k] if k < 3 else th.text_muted)
                paste_flag(img, c, x0 + 130, y + 41, 58, border=TEXT_WHITE, bw=2)
                name = country_name(c).upper() + ("  •  OUT" if out else "")
                text_l(d, name, x0 + 180, y + 18, get_font(46, title=True), th.text_muted if out else TEXT_WHITE)
                text_r(d, f"{int(round(pts.get(c, 0)))} PTS", x0 + 790, y + 18, get_font(46, title=True),
                       th.primary_accent)
            self.chrome.caption(img, _active(voice, self.bank, t))
            return img

        return Segment(f"table_{tag}", dur, render, voice=voice)

    # ---------------------------------------------------------------- champion
    def champion(self) -> Segment:
        th = self.theme
        champ = self.data.champion
        row = next((r for r in self.data.final_table if r["code"] == champ), {"points": 0, "wins": 0})
        base = self.chrome.bg.copy()
        name = country_name(champ)
        spoken = f"{name} are the season {self.season} champions!"
        spoken2 = f"{int(row.get('points', 0))} points and {int(row.get('wins', 0))} race wins."
        dur = 10.0
        rng = random.Random(self.season)
        confetti = [(rng.uniform(0, W), rng.uniform(-H, 0), rng.uniform(140, 320), rng.choice(
            [GOLD, th.primary_accent, th.secondary_accent, TEXT_WHITE, ACCENT_GREEN]), rng.uniform(0, 6.28))
            for _ in range(160)]

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            for x, y, sp, col, ph in confetti:
                yy = (y + sp * t) % (H + 40) - 20
                xx = x + 18 * math.sin(t * 2 + ph)
                d.rectangle([xx, yy, xx + 10, yy + 16], fill=col)
            p = ease_out(t / 0.9)
            dd = int(340 * (0.7 + 0.3 * p))
            # trophy
            cx, cy = W / 2, 420
            d.polygon([(cx - 210, cy - 200), (cx + 210, cy - 200), (cx + 150, cy + 40), (cx - 150, cy + 40)], fill=GOLD)
            d.rectangle([cx - 30, cy + 40, cx + 30, cy + 130], fill=GOLD)
            d.rounded_rectangle([cx - 140, cy + 130, cx + 140, cy + 175], radius=10, fill=(150, 110, 30))
            paste_flag(img, champ, cx, cy - 90, dd, border=GOLD, bw=8)
            q = ease_out((t - 0.7) / 0.6)
            if q > 0:
                text_c(d, f"SEASON {self.season} CHAMPION", W / 2, 640, get_font(72, title=True),
                       blend_rgb(th.bg_outer, GOLD, q))
                text_c(d, name.upper(), W / 2, 720, get_font(150, title=True), blend_rgb(th.bg_outer, TEXT_WHITE, q))
                text_c(d, f"{int(row.get('points', 0))} PTS  •  {int(row.get('wins', 0))} WINS", W / 2, 885,
                       get_font(54, title=True), blend_rgb(th.bg_outer, th.primary_accent, q))
            self.chrome.caption(img, _active(voice, self.bank, t))
            return img

        voice = layout_lines(self.bank, [(1.0, spoken, 3), (1.0 + self.bank.dur(spoken) + 0.4, spoken2, 2)], dur)
        return Segment("champion", dur, render, voice=voice, chapter="Champion",
                       sfx=[SfxEvent(0.2, "reveal_fanfare", 0.95, 1.0), SfxEvent(1.0, "reveal_fanfare", 0.7, 1.12)])

    # ---------------------------------------------------------------- awards
    def awards(self) -> List[Segment]:
        data = self.data
        th = self.theme
        out: List[Tuple[str, str, str, str]] = []

        def top(m: Dict[str, int]) -> Optional[Tuple[str, int]]:
            if not m:
                return None
            c = max(m, key=lambda k: (m[k], data.points_after[-1].get(k, 0) if data.points_after else 0))
            return c, m[c]

        g = top(data.wins)
        if g:
            out.append(("GOLD CAP", "MOST RACE WINS", g[0], f"{g[1]} WINS"))
        cl = top(data.podiums)
        if cl:
            out.append(("CLUTCH CAP", "MOST PODIUMS", cl[0], f"{cl[1]} PODIUMS"))
        if len(data.points_after) >= 6:
            q_i = max(0, len(data.points_after) // 4 - 1)
            r_early = ranking(data.points_after[q_i])
            r_last = ranking(data.points_after[-1], list(data.eliminated_at.keys()))
            climbs = {c: r_early.index(c) - r_last.index(c) for c in r_last if c in r_early}
            best = max(climbs, key=lambda c: climbs[c]) if climbs else None
            if best and climbs[best] > 0:
                out.append(("BIGGEST CLIMBER", "MOST PLACES GAINED", best, f"+{climbs[best]} PLACES"))
        so = top(data.seconds)
        if so and so[1] >= 2:
            out.append(("SO CLOSE AWARD", "MOST SECOND PLACES", so[0], f"{so[1]} TIMES 2ND"))
        wf = top(data.wildcards)
        if wf:
            out.append(("FAN FAVOURITE", "MOST WILDCARD PICKS", wf[0], f"{wf[1]} PICKS"))

        segs: List[Segment] = []
        for n, (award, sub, code, stat) in enumerate(out):
            spoken = f"The {award.title()} goes to {country_name(code)}. {stat.lower()}."
            dur = max(4.2, self.bank.dur(spoken) + 1.2)
            base = self.chrome.base("SEASON AWARDS")
            voice = layout_lines(self.bank, [(0.4, spoken, 2)], dur)

            def render(t: float, award=award, sub=sub, code=code, stat=stat, voice=voice, base=base) -> Image.Image:
                img = base.copy()
                d = ImageDraw.Draw(img)
                p = ease_out(t / 0.5)
                panel(d, th, (360, 150 + 40 * (1 - p), W - 360, 900), accent=GOLD)
                text_c(d, award, W / 2, 200, get_font(96, title=True), GOLD)
                text_c(d, sub, W / 2, 315, get_font(40, title=True), th.text_muted)
                paste_flag(img, code, W / 2, 530, int(220 * (0.8 + 0.2 * p)), border=GOLD, bw=7)
                text_c(d, country_name(code).upper(), W / 2, 670, get_font(96, title=True), TEXT_WHITE)
                text_c(d, stat, W / 2, 790, get_font(56, title=True), th.primary_accent)
                self.chrome.caption(img, _active(voice, self.bank, t))
                return img

            segs.append(Segment(f"award_{n}", dur, render, voice=voice, chapter="Season awards" if n == 0 else None,
                                sfx=[SfxEvent(0.15, "reveal_fanfare", 0.5, 1.2)]))
        return segs

    # ---------------------------------------------------------------- points race
    def points_race(self) -> Segment:
        th = self.theme
        data = self.data
        steps = [{c: 0.0 for c in data.points_after[0]}] + data.points_after
        per = 0.75
        dur = max(10.0, (len(steps) - 1) * per + 3.0)
        base = self.chrome.base("THE TITLE RACE")
        spoken = "Here's how the title race unfolded, race by race."
        voice = layout_lines(self.bank, [(0.4, spoken, 2)], dur)
        top_val = max(max(s.values()) for s in steps) or 1.0
        codes = list(steps[0].keys())
        row_h = 52
        state: Dict[str, Dict[str, float]] = {"y": {}}

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            x = min(len(steps) - 1.0, max(0.0, (t - 1.0) / per))
            k = int(x)
            fr = x - k
            a, b = steps[k], steps[min(k + 1, len(steps) - 1)]
            cur = {c: a.get(c, 0) + (b.get(c, 0) - a.get(c, 0)) * ease_out(fr) for c in codes}
            race_no = min(len(steps) - 1, k + (1 if fr > 0.5 else 0))
            elim = [c for c, r in data.eliminated_at.items() if r <= max(1, race_no)]
            order = ranking(cur, elim)
            text_l(d, "THE TITLE RACE", 60, 80, get_font(64, title=True), TEXT_WHITE)
            text_r(d, f"AFTER RACE {race_no}", W - 60, 86, get_font(56, title=True), th.primary_accent)
            ys = state["y"]
            for idx, c in enumerate(order):
                target = 170 + idx * row_h
                ys[c] = ys.get(c, target) + (target - ys.get(c, target)) * 0.32
            maxw = W - 520
            color_of = {c: th.marble_palette[i % len(th.marble_palette)] for i, c in enumerate(codes)}
            for c in reversed(order):  # leaders drawn last = on top during overtakes
                y = ys[c]
                val = cur[c]
                bw_ = max(8, int(maxw * val / top_val))
                col = th.text_muted if c in elim else color_of[c]
                d.rounded_rectangle([60, y + 4, 338, y + row_h - 8], radius=8, fill=th.panel_bg)
                text_r(d, country_name(c).upper(), 326, y + 6, get_font(36, title=True),
                       th.text_muted if c in elim else TEXT_WHITE)
                d.rounded_rectangle([350, y + 4, 350 + bw_, y + row_h - 8], radius=8, fill=col)
                paste_flag(img, c, 350 + bw_ + 26, y + (row_h - 4) / 2, 40, border=TEXT_WHITE, bw=2)
                text_l(d, f"{int(round(val))}", 350 + bw_ + 56, y + 6, get_font(36, title=True), TEXT_WHITE)
            self.chrome.caption(img, _active(voice, self.bank, t))
            return img

        return Segment("points_race", dur, render, voice=voice, chapter="The title race")

    # ---------------------------------------------------------------- next season + end screen
    def next_season(self) -> Segment:
        nxt = get_season_theme(self.season + 1)
        th = self.theme
        base = Image.new("RGB", (W, H), nxt.bg_outer)
        if not nxt.is_classic:
            base = wide_backdrop(nxt)
        if self.data.complete:
            spoken = f"Season {self.season + 1} starts now, in the brand new {nxt.edition_name.title()} edition."
            head = f"SEASON {self.season + 1} STARTS NOW"
        else:
            spoken = f"The season continues every day in the Shorts. Season {self.season + 1} brings the {nxt.edition_name.title()} edition."
            head = "THE SEASON CONTINUES DAILY"
        spoken2 = "Comment your country to give them the crowd boost."
        dur = max(7.0, self.bank.dur(spoken) + self.bank.dur(spoken2) + 1.6)
        voice = layout_lines(self.bank, [(0.4, spoken, 3), (0.4 + self.bank.dur(spoken) + 0.4, spoken2, 2)], dur)

        teaser: Optional[Image.Image] = None
        try:
            from common.season_theme import render_seasonal_arena_backdrop
            from generators.race_replay import _build_v2_arena

            arena = _build_v2_arena(f"S{self.season + 1}-teaser", "double_ring", 12, 30.0, 1.0)
            full = render_seasonal_arena_backdrop(nxt, arena.mode, arena.center, arena.finish_y)
            arena.kit.render_static(full, neon_glow=True, glow_bg=nxt.bg_center, color_map=nxt.obstacle_remap)
            arena.kit.render_dynamic(ImageDraw.Draw(full), arena.kit.snapshot(), neon_glow=True,
                                     glow_bg=nxt.bg_center, color_map=nxt.obstacle_remap)
            teaser = full.crop((0, CROP_Y0, 1080, CROP_Y1)).resize((640, 628), Image.Resampling.BILINEAR)
        except Exception as e:
            logger.warning("Next-season teaser window unavailable: %s", e)

        def render(t: float) -> Image.Image:
            img = base.copy()
            d = ImageDraw.Draw(img)
            p = ease_out(t / 0.6)
            lx = 110 - 60 * (1 - p)
            text_l(d, head, lx, 250, fit_font(d, head, 1000, (104, 92, 80)), blend_rgb(nxt.bg_outer, TEXT_WHITE, p))
            text_l(d, f"{nxt.edition_name} EDITION", lx, 390, get_font(84, title=True),
                   blend_rgb(nxt.bg_outer, nxt.primary_accent, p))
            for k, col in enumerate(nxt.marble_palette[:6]):
                q = ease_out((t - 0.5 - k * 0.1) / 0.3)
                if q > 0:
                    cx = 150 + k * 110
                    r = 38 * q
                    d.ellipse([cx - r, 580 - r, cx + r, 580 + r], fill=col, outline=TEXT_WHITE, width=3)
            text_l(d, "COMMENT YOUR COUNTRY", lx, 680, get_font(56, title=True), nxt.secondary_accent)
            text_l(d, "THE MOST-REQUESTED NATION GETS THE CROWD BOOST", lx, 750, get_font(38, title=True),
                   blend_rgb(nxt.bg_outer, TEXT_WHITE, 0.8))
            if teaser is not None:
                q = ease_out((t - 0.3) / 0.6)
                wx, wy = 1180 + int(80 * (1 - q)), 170
                d.rectangle([wx - 6, wy - 6, wx + 646, wy + 634], outline=nxt.primary_accent, width=6)
                img.paste(teaser, (wx, wy))
                d.rounded_rectangle([wx + 20, wy + 20, wx + 250, wy + 66], radius=10, fill=nxt.primary_accent)
                text_c(d, f"SEASON {self.season + 1} ARENA", wx + 135, wy + 28, get_font(30, title=True), BG_COLOR)
            self.chrome.caption(img, _active(voice, self.bank, t))
            return img

        return Segment("next_season", dur, render, voice=voice, chapter="Next season")

    def end_screen(self) -> Segment:
        th = self.theme
        base = self.chrome.bg.copy()
        d0 = ImageDraw.Draw(base)
        text_c(d0, "THANKS FOR WATCHING", W / 2, 90, get_font(96, title=True), TEXT_WHITE)
        text_c(d0, "NEW RACES EVERY DAY IN THE SHORTS  •  @CALLITARENA", W / 2, 215, get_font(44, title=True),
               th.primary_accent)
        # Safe zones for YouTube end-screen elements (video tiles left/right, subscribe centre)
        for x0 in (150, 1110):
            d0.rounded_rectangle([x0, 380, x0 + 660, 752], radius=20, outline=th.panel_border, width=3)
        d0.ellipse([W / 2 - 95, 470, W / 2 + 95, 660], outline=th.primary_accent, width=4)
        spoken = "Thanks for watching. Subscribe, and call the next winner in the Shorts."
        dur = 20.0
        voice = layout_lines(self.bank, [(0.5, spoken, 2)], dur)

        def render(t: float) -> Image.Image:
            img = base.copy()
            self.chrome.caption(img, _active(voice, self.bank, t))
            return img

        return Segment("end_screen", dur, render, voice=voice, chapter="End")


def _active(lines: List[Tuple[float, str]], bank: VoiceBank, t: float) -> Optional[str]:
    for st, txt in lines:
        if st <= t <= st + bank.dur(txt) + 0.2:
            return txt
    return None


# =========================================================================== assembly
def plan_segments(data: SeasonData, bank: VoiceBank) -> List[Segment]:
    b = RecapBuilder(data, bank)
    segs: List[Segment] = [b.cold_open(), b.title(), b.roster()]
    by_stage: Dict[str, List[int]] = {}
    for i, r in enumerate(data.replays):
        by_stage.setdefault(r.stage, []).append(i)
    for ci, (stage, tag, title, sub) in enumerate(STAGE_CHAPTERS):
        idxs = by_stage.get(stage, [])
        if not idxs:
            continue
        segs.append(b.chapter_card(ci, tag, title, sub))
        for i in idxs:
            segs.append(b.race(i))
        last = idxs[-1]
        if stage in ("Group Stage", "Knockout Stage") and last < len(data.replays) - 1 or (
                stage != "Monthly Grand Final" and last == len(data.replays) - 1):
            pts = data.points_after[last]
            elim = [c for c, r in data.eliminated_at.items() if r <= data.replays[last].race]
            leader = ranking(pts, elim)[0]
            n_after = data.replays[last].race
            spoken = (f"After {NUM_WORDS.get(n_after, n_after)} races, {country_name(leader)} lead on "
                      f"{int(round(pts[leader]))} points.")
            segs.append(b.table(f"STANDINGS AFTER RACE {n_after}", pts, elim, spoken, "TABLE CHECK"))
    if data.complete and data.champion:
        segs.append(b.champion())
    segs.extend(b.awards())
    if len(data.points_after) >= 3:
        segs.append(b.points_race())
    if data.complete:
        segs.append(b.table("FINAL STANDINGS", data.points_after[-1], list(data.eliminated_at.keys()),
                            f"The final table of season {data.season}.", "FINAL TABLE"))
    segs.append(b.next_season())
    segs.append(b.end_screen())
    return segs


def _fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def build_recap_metadata(data: SeasonData, chapters: List[Tuple[float, str]], total: float) -> Dict[str, Any]:
    champ = country_name(data.champion) if data.champion else None
    if data.complete:
        title = f"Marble League Season {data.season} — The Full Season: {len(data.replays)} Races, 16 Nations 🏆"
    else:
        title = f"Marble League Season {data.season} — The Story So Far ({len(data.replays)} Races) 🌍"
    top5 = data.final_table[:5]
    lines = [
        f"Every race of Marble League Season {data.season} — real physics, real results, one champion."
        if data.complete else f"Every race of Marble League Season {data.season} so far, with full commentary.",
        "",
        "⏱️ Chapters",
    ]
    lines += [f"{_fmt_ts(t)} {name}" for t, name in chapters]
    lines += ["", "🏁 Standings"]
    lines += [f"{i + 1}. {country_name(r['code'])} — {int(r['points'])} pts" for i, r in enumerate(top5)]
    if champ:
        lines += ["", f"🏆 Season {data.season} champion: {champ}"]
    lines += [
        "",
        "Every race is a real Pymunk physics simulation decided by its seed — nothing is scripted.",
        "New races drop every day in our Shorts. Comment your country to get it in the next race! 👇",
        "",
        "#marblerace #marbleleague #callitarena #countryballs #physics",
    ]
    return {
        "title": title[:100],
        "description": "\n".join(lines)[:4900],
        "tags": ["marble race", "marble league", "country marble race", "marble run", "physics simulation",
                 "call it arena", f"marble league season {data.season}", "countryballs", "satisfying"],
        "categoryId": "24",
        "duration_sec": round(total, 2),
    }


def render_thumbnail(data: SeasonData, out_path: Path) -> Path:
    th = data.theme
    img = wide_backdrop(th).resize((1280, 720), Image.Resampling.BILINEAR)
    d = ImageDraw.Draw(img)
    top3 = [r["code"] for r in data.final_table[:3]]
    d.rectangle([0, 0, 1280, 120], fill=th.primary_accent)
    text_c(d, f"MARBLE LEAGUE • SEASON {data.season}", 640, 22, get_font(80, title=True), BG_COLOR)
    for k, c in enumerate(top3):
        cx = [640, 300, 980][k]
        dd = [300, 220, 220][k]
        cy = [360, 410, 410][k]
        paste_flag(img, c, cx, cy, dd, border=MEDALS[k], bw=10)
    head = f"{len(data.replays)} RACES • 1 CHAMPION" if data.complete else f"{len(data.replays)} RACES SO FAR"
    d.rectangle([0, 590, 1280, 720], fill=(8, 9, 16))
    text_c(d, head, 640, 608, get_font(92, title=True), TEXT_WHITE)
    img.save(out_path, "PNG")
    return out_path


def generate_season_recap_video(
    state: Dict[str, Any],
    season: int,
    output_dir: Path = OUTPUT_DIR,
    stills_dir: Optional[Path] = None,
    max_races: Optional[int] = None,
) -> Dict[str, Any]:
    """Renders the season recap MP4 + thumbnail + metadata. Pure read of `state` (never saved)."""
    state = copy.deepcopy(state)
    output_dir.mkdir(parents=True, exist_ok=True)
    replays = build_season_replays(
        state, season,
        progress_cb=lambda r: logger.info("Recap replay S%s R%02d %-18s -> %s (winner %s)", r.season, r.race,
                                          r.variant, r.source, r.winner),
    )
    if max_races:
        replays = replays[:max_races]
    if not replays:
        raise ValueError(f"No races recorded for season {season}")
    data = build_season_data(state, season, replays)
    bank = VoiceBank(output_dir / "tts_cache")
    segments = plan_segments(data, bank)

    chapters: List[Tuple[float, str]] = []
    voice_cues: List[VoiceCue] = []
    sfx: List[SfxEvent] = []
    t0 = 0.0
    starts: List[float] = []
    for seg in segments:
        starts.append(t0)
        if seg.chapter and (not chapters or t0 - chapters[-1][0] >= 10.0):
            chapters.append((t0, seg.chapter))
        for lt, txt in seg.voice:
            wav, dur = bank.get(txt)
            voice_cues.append(VoiceCue(timestamp_sec=t0 + lt, wav_path=wav, text=txt, duration_sec=dur))
        for ev in seg.sfx:
            sfx.append(SfxEvent(t0 + ev.timestamp_sec, ev.kind, ev.gain, ev.pitch_shift))
        t0 += seg.duration
    total = t0
    if chapters:
        chapters[0] = (0.0, chapters[0][1])

    tag = f"s{season}" + ("" if data.complete else f"_sofar_r{len(replays)}")
    audio_path = output_dir / f"season_recap_{tag}.wav"
    mix_master_audio(total, audio_path, sfx_events=sfx, voice_cues=voice_cues, bgm_track_name="arena_pulse_cc0.wav",
                     bgm_volume=0.10)
    video_path = output_dir / f"season_recap_{tag}.mp4"
    still_marks = {round(s + 0.6 * seg.duration, 1): seg.name for s, seg in zip(starts, segments)}
    if stills_dir:
        stills_dir.mkdir(parents=True, exist_ok=True)

    with FFmpegFramePipe(video_path, audio_path, width=W, height=H, fps=FPS) as pipe:
        prev_last: Optional[Image.Image] = None
        for seg, s0 in zip(segments, starts):
            n = max(1, int(round(seg.duration * FPS)))
            frame = None
            for k in range(n):
                lt = k / float(FPS)
                frame = seg.render(lt)
                if seg.crossfade and prev_last is not None and k < 8:
                    frame = Image.blend(prev_last, frame, (k + 1) / 9.0)
                if stills_dir:
                    gt = round(s0 + lt, 1)
                    if gt in still_marks and abs((s0 + lt) - gt) < 0.5 / FPS:
                        frame.save(stills_dir / f"{int(gt):04d}s_{still_marks.pop(gt)}.png")
                pipe.write_frame(frame)
            prev_last = frame
    if audio_path.exists():
        audio_path.unlink()

    thumb = render_thumbnail(data, output_dir / f"season_recap_{tag}_thumb.png")
    meta = build_recap_metadata(data, chapters, total)
    meta.update({
        "video_path": str(video_path),
        "thumbnail_path": str(thumb),
        "season": season,
        "complete": data.complete,
        "races": len(replays),
        "replay_sources": {r.race: r.source for r in replays},
        "chapters": [(_fmt_ts(t), n) for t, n in chapters],
        "champion": data.champion,
        "winner": country_name(data.champion) if data.champion else country_name(data.final_table[0]["code"]),
    })
    return meta
