"""
Call It Arena — 10-game Series tracker for the standalone physics games (`common/series.py`).

Each physics format (Downhill Derby, Tower Quake, Plinko Bucket Drop) runs in 10-game series:
- games 1–9 award normal points, game 10 is the SERIES FINALE worth 3x,
- after the finale the champion is recorded in `champions` and a new series starts at 0.
State lives in `state/series.json` (separate from the Country Marble League `state/league.json`).
Generators only write it when `update_state=True` (never in dry runs / previews).
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from .branding import (
    ACCENT_GREEN,
    ACCENT_YELLOW,
    BG_COLOR,
    PANEL_BORDER_COLOR,
    STATE_DIR,
    TEXT_MUTED,
    TEXT_WHITE,
    VIDEO_WIDTH,
    get_font,
)
from .render import draw_centered_text

SERIES_STATE_FILE = STATE_DIR / "series.json"
SERIES_LENGTH = 10
FINALE_MULTIPLIER = 3
GOLD = (255, 210, 60)


def load_series_state(path: Path = SERIES_STATE_FILE) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_series_state(state: Dict[str, Any], path: Path = SERIES_STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _fmt_state(state: Dict[str, Any], fmt: str) -> Dict[str, Any]:
    return state.setdefault(fmt, {"season": 1, "game": 0, "table": {}, "history": [], "champions": []})


def next_game_info(state: Dict[str, Any], fmt: str, force_game: Optional[int] = None) -> Tuple[int, int, bool, int]:
    """Returns `(series_season, game_no, is_finale, points_multiplier)` for the upcoming game."""
    fs = _fmt_state(state, fmt)
    game = int(force_game) if force_game else int(fs.get("game", 0)) + 1
    is_finale = game >= SERIES_LENGTH
    return int(fs.get("season", 1)), game, is_finale, (FINALE_MULTIPLIER if is_finale else 1)


def record_series_game(
    state: Dict[str, Any],
    fmt: str,
    awards: Dict[str, int],
    winner_key: str,
    seed: str,
    all_keys: Optional[List[str]] = None,
    force_game: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Applies one game's result (mutates `state` in memory) and returns a summary:
    `{season, game, is_finale, multiplier, standings: [(key, points, wins)], champion}`.
    The caller decides whether to persist `state`.
    """
    fs = _fmt_state(state, fmt)
    season, game, is_finale, mult = next_game_info(state, fmt, force_game)
    table: Dict[str, Dict[str, int]] = fs.setdefault("table", {})
    for key in all_keys or []:
        table.setdefault(key, {"points": 0, "wins": 0})
    for key, pts in awards.items():
        entry = table.setdefault(key, {"points": 0, "wins": 0})
        entry["points"] = int(entry["points"]) + int(pts) * mult
    table.setdefault(winner_key, {"points": 0, "wins": 0})["wins"] += 1
    fs["game"] = game
    fs.setdefault("history", []).append({"season": season, "game": game, "seed": seed, "winner": winner_key,
                                         "multiplier": mult})
    fs["history"] = fs["history"][-60:]
    standings = sorted(((k, v["points"], v["wins"]) for k, v in table.items()), key=lambda r: (r[1], r[2]), reverse=True)
    champion = None
    if is_finale:
        champion = standings[0][0] if standings else winner_key
        fs.setdefault("champions", []).append({"season": season, "champion": champion,
                                               "points": standings[0][1] if standings else 0})
        fs["season"] = season + 1
        fs["game"] = 0
        fs["table"] = {}
    return {"season": season, "game": game, "is_finale": is_finale, "multiplier": mult,
            "standings": standings, "champion": champion}


def preview_copy(state: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(state)


def draw_series_card(
    frame: Image.Image,
    series_name: str,
    summary: Dict[str, Any],
    unit: str,
    labels: Dict[str, str],
    colors: Dict[str, Tuple[int, int, int]],
    highlight_key: str,
    top: int = 400,
    bottom: int = 1330,
) -> None:
    """Series standings overlay (after the reveal). Finale variant crowns the series champion in gold."""
    draw = ImageDraw.Draw(frame)
    cx = VIDEO_WIDTH // 2
    finale = summary["is_finale"]
    border = GOLD if finale else ACCENT_YELLOW
    draw.rounded_rectangle([54, top, VIDEO_WIDTH - 54, bottom], radius=32, fill=(18, 20, 36), outline=border, width=5)
    if finale:
        champ = summary["champion"]
        draw_centered_text(draw, f"{series_name.upper()} CHAMPION", cx, top + 22, get_font(54, title=True), fill=GOLD)
        draw_centered_text(draw, labels.get(champ, champ).upper(), cx, top + 86, get_font(72, title=True), fill=TEXT_WHITE)
        sub = f"SERIES {summary['season']} FINAL TABLE  •  FINALE WAS 3X"
    else:
        draw_centered_text(draw, f"{series_name.upper()} STANDINGS", cx, top + 26, get_font(54, title=True), fill=ACCENT_YELLOW)
        sub = f"SERIES {summary['season']}  •  AFTER GAME {summary['game']}/{SERIES_LENGTH}  •  FINALE = 3X"
    draw_centered_text(draw, sub, cx, top + (168 if finale else 94), get_font(26, bold=True), fill=TEXT_MUTED)

    rows = summary["standings"][:7]
    max_pts = max([r[1] for r in rows] + [1])
    y = top + (220 if finale else 150)
    row_h = min(92, int((bottom - 30 - y) / max(1, len(rows))))
    font = get_font(32, bold=True)
    for rank, (key, pts, wins) in enumerate(rows, start=1):
        col = colors.get(key, TEXT_WHITE)
        is_hl = key == highlight_key
        fill = (38, 52, 88) if is_hl else (26, 30, 52)
        draw.rounded_rectangle([90, y, VIDEO_WIDTH - 90, y + row_h - 12], radius=16, fill=fill,
                               outline=ACCENT_GREEN if is_hl else PANEL_BORDER_COLOR, width=3)
        bar_w = int((VIDEO_WIDTH - 200) * (pts / max_pts))
        draw.rounded_rectangle([100, y + row_h - 26, 100 + max(8, bar_w), y + row_h - 18], radius=4, fill=col)
        draw.text((110, y + 10), f"#{rank}  {labels.get(key, key)}", font=font, fill=GOLD if (finale and rank == 1) else TEXT_WHITE)
        val = f"{pts} {unit}  ({wins}W)"
        vb = draw.textbbox((0, 0), val, font=font)
        draw.text((VIDEO_WIDTH - 110 - (vb[2] - vb[0]), y + 10), val, font=font, fill=col)
        y += row_h
    if not finale and summary["game"] == SERIES_LENGTH - 1:
        draw_centered_text(draw, "NEXT GAME: SERIES FINALE — TRIPLE POINTS!", cx, bottom - 44, get_font(28, bold=True), fill=GOLD)
