"""
Call It Arena — Marble League race replays (`generators/race_replay.py`).

Gives the end-of-season long-form recap frame-accurate footage of every league race:

1. Replay files (preferred): from now on every live race saves its full trajectory
   (`state/replays/s{season}_r{race:02d}.json.gz`, ~100 KB) right after the Short is rendered,
   so recaps never depend on physics determinism or future code changes.
2. Verified re-simulation (fallback for races raced before replay files existed): the race is
   re-simulated from its seed and ONLY accepted if the re-simulated finishing order matches the
   recorded result exactly (current engine for v2 races, `legacy_v1_sim` for Season 1 Races 1–6).
3. Otherwise the race is marked `available=False` and the recap shows a results card instead of
   footage — the recap can never show an outcome that differs from what was actually posted.
"""

import gzip
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from common.audio import SfxEvent
from common.branding import STATE_DIR

logger = logging.getLogger("call_it_arena")

REPLAY_DIR = STATE_DIR / "replays"
REPLAY_FORMAT_VERSION = 1


@dataclass
class RaceReplay:
    season: int
    race: int
    seed: str
    variant: str
    stage: str
    points_multiplier: int
    race_codes: List[str]
    marble_radius: float
    full_finish_order: List[str]
    finish_times: Dict[str, float] = field(default_factory=dict)
    eliminated: Optional[str] = None
    boosted_code: Optional[str] = None
    available: bool = False
    source: str = "unavailable"  # replay_file | resim | legacy_resim | unavailable
    frames: List[Dict[str, Any]] = field(default_factory=list)
    sfx_events: List[SfxEvent] = field(default_factory=list)
    mode: str = "escape"
    label: str = ""
    finish_word: str = "ESCAPED"
    center: Tuple[float, float] = (540.0, 920.0)
    finish_y: float = 1305.0
    escape_radius: float = 0.0
    drawer: Any = None  # ArenaKit-like: render_static(image, ...), render_dynamic(draw, poses, ...)
    arena: Any = None  # ArenaConfig when built by the v2 engine (None for legacy v1)

    @property
    def winner(self) -> str:
        return self.full_finish_order[0] if self.full_finish_order else ""

    @property
    def first_finish_t(self) -> Optional[float]:
        return min(self.finish_times.values()) if self.finish_times else None

    def progress(self, x: float, y: float) -> float:
        """Larger = closer to finishing (same metric the live race uses)."""
        if self.mode == "line":
            return y
        return ((x - self.center[0]) ** 2 + (y - self.center[1]) ** 2) ** 0.5


# --------------------------------------------------------------------------- stage helpers
def stage_for_race(race_num: int, max_races: int = 28) -> str:
    if race_num <= 14:
        return "Group Stage"
    if race_num <= 21:
        return "Knockout Stage"
    if race_num < max_races:
        return "Championship Finals"
    return "Monthly Grand Final"


def points_multiplier_for(stage: str) -> int:
    return 3 if stage == "Monthly Grand Final" else (2 if stage == "Championship Finals" else 1)


# --------------------------------------------------------------------------- replay files
def replay_path(season: int, race: int, base_dir: Path = REPLAY_DIR) -> Path:
    return base_dir / f"s{int(season)}_r{int(race):02d}.json.gz"


def save_replay_file(
    season: int,
    race: int,
    seed: str,
    variant: str,
    intensity: float,
    race_codes: List[str],
    marble_radius: float,
    boosted_code: Optional[str],
    pacing_name: str,
    sim: Any,
    full_finish_order: List[str],
    base_dir: Path = REPLAY_DIR,
) -> Path:
    """Compactly stores a live race (positions x10 / angles x1000 as ints, gzip JSON)."""
    marbles: Dict[str, List[int]] = {c: [] for c in race_codes}
    kin_keys = sorted(sim.frames[0]["kin"].keys()) if sim.frames else []
    kin: Dict[str, List[int]] = {k: [] for k in kin_keys}
    for fr in sim.frames:
        for c in race_codes:
            x, y, a = fr["marbles"][c]
            marbles[c].extend((int(round(x * 10)), int(round(y * 10)), int(round(a * 1000))))
        for k in kin_keys:
            x, y, a = fr["kin"][k]
            kin[k].extend((int(round(x * 10)), int(round(y * 10)), int(round(a * 1000))))
    payload = {
        "v": REPLAY_FORMAT_VERSION,
        "season": int(season),
        "race": int(race),
        "seed": seed,
        "variant": variant,
        "intensity": float(intensity),
        "race_codes": list(race_codes),
        "marble_radius": float(marble_radius),
        "boosted_code": boosted_code,
        "pacing": pacing_name,
        "n_frames": len(sim.frames),
        "finish_order": list(sim.finish_order),
        "finish_times": {c: round(float(t), 4) for c, t in sim.finish_times.items()},
        "full_finish_order": list(full_finish_order),
        "marbles": marbles,
        "kin": kin,
        "sfx": [[round(e.timestamp_sec, 4), e.kind, round(e.gain, 3), round(e.pitch_shift, 3)] for e in sim.sfx_events],
    }
    base_dir.mkdir(parents=True, exist_ok=True)
    out = replay_path(season, race, base_dir)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    return out


def _decode_frames(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    n = int(payload["n_frames"])
    codes = payload["race_codes"]
    frames: List[Dict[str, Any]] = []
    fin_times = payload.get("finish_times", {})
    for i in range(n):
        j = i * 3
        t = i / 30.0
        frames.append(
            {
                "marbles": {
                    c: (payload["marbles"][c][j] / 10.0, payload["marbles"][c][j + 1] / 10.0,
                        payload["marbles"][c][j + 2] / 1000.0)
                    for c in codes
                },
                "kin": {
                    k: (v[j] / 10.0, v[j + 1] / 10.0, v[j + 2] / 1000.0) for k, v in payload.get("kin", {}).items()
                },
                "finished": sum(1 for ft in fin_times.values() if ft <= t + 1e-6),
            }
        )
    return frames


def _build_v2_arena(seed: str, variant: str, n_codes: int, marble_radius: float, intensity: float) -> Any:
    import pymunk

    from generators.marble_arenas import build_arena
    from generators.marble_league import _seed_to_int

    rng = random.Random(_seed_to_int(f"{seed}|{variant}"))
    space = pymunk.Space()
    return build_arena(variant, space, rng, n_codes, marble_radius, intensity)


def _apply_arena(rep: RaceReplay, arena: Any) -> None:
    rep.arena = arena
    rep.drawer = arena.kit
    rep.mode = arena.mode
    rep.label = arena.label
    rep.finish_word = arena.finish_word
    rep.center = (float(arena.center[0]), float(arena.center[1]))
    rep.finish_y = float(arena.finish_y)
    rep.escape_radius = float(arena.escape_radius)


def load_replay_file(entry: Dict[str, Any], max_races: int = 28, base_dir: Path = REPLAY_DIR) -> Optional[RaceReplay]:
    path = replay_path(int(entry["season"]), int(entry["race"]), base_dir)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("seed") != entry.get("seed"):
            return None
        stage = stage_for_race(int(entry["race"]), max_races)
        rep = RaceReplay(
            season=int(entry["season"]),
            race=int(entry["race"]),
            seed=str(entry["seed"]),
            variant=str(payload["variant"]),
            stage=stage,
            points_multiplier=int(entry.get("points_multiplier") or points_multiplier_for(stage)),
            race_codes=list(payload["race_codes"]),
            marble_radius=float(payload["marble_radius"]),
            full_finish_order=list(payload["full_finish_order"]),
            finish_times={c: float(t) for c, t in payload["finish_times"].items()},
            eliminated=entry.get("eliminated"),
            boosted_code=payload.get("boosted_code"),
            available=True,
            source="replay_file",
            frames=_decode_frames(payload),
            sfx_events=[SfxEvent(float(t), str(k), float(g), float(p)) for t, k, g, p in payload.get("sfx", [])],
        )
        _apply_arena(
            rep,
            _build_v2_arena(rep.seed, rep.variant, len(rep.race_codes), rep.marble_radius, float(payload["intensity"])),
        )
        return rep
    except Exception as e:  # corrupted / incompatible file -> fall back to re-simulation
        logger.warning("Replay file %s unusable (%s); falling back to re-simulation.", path.name, e)
        return None


# --------------------------------------------------------------------------- verified re-simulation
def _group_stage_candidates(seed: str, target: Optional[set], league_codes: List[str]) -> List[List[str]]:
    from generators.marble_league import _seed_to_int

    cands: List[List[str]] = []
    for wc in [None] + list(league_codes):
        rng = random.Random(_seed_to_int(seed))
        ball_count = rng.choice([10, 12, 14, 16])
        priority = ["IN", "US", "BR", "ID"]
        if wc and wc not in priority:
            priority.insert(0, wc)
        remaining = [c for c in league_codes if c not in priority]
        rng.shuffle(remaining)
        codes = (priority + remaining)[:ball_count]
        if (target is None or set(codes) == target) and codes not in cands:
            cands.append(codes)
    return cands


def _resim_v2(entry: Dict[str, Any], stage: str, max_races: int) -> Optional[RaceReplay]:
    from generators.marble_league import LEAGUE_COUNTRY_CODES, MEGA_PACING, REGULAR_PACING, run_tuned_race

    recorded: List[str] = list(entry.get("finish_order") or [])
    if not recorded:
        return None
    if entry.get("race_codes"):
        cands = [list(entry["race_codes"])]
    elif stage == "Group Stage":
        cands = _group_stage_candidates(str(entry["seed"]), set(recorded), list(LEAGUE_COUNTRY_CODES))
    else:
        cands = [[c for c in LEAGUE_COUNTRY_CODES if c in set(recorded)]]
    pacing = MEGA_PACING if stage == "Monthly Grand Final" else REGULAR_PACING
    boosted = entry.get("crowd_boost")
    for codes in cands:
        radius = float(entry.get("marble_radius") or (30 if len(codes) <= 12 else 27))
        sim = run_tuned_race(str(entry["seed"]), str(entry["variant"]), codes, radius, pacing, boosted_code=boosted)
        un = [c for c in codes if c not in sim.finish_order]
        un.sort(key=lambda c: sim.final_progress[c], reverse=True)
        if sim.finish_order + un != recorded:
            continue
        rep = RaceReplay(
            season=int(entry["season"]),
            race=int(entry["race"]),
            seed=str(entry["seed"]),
            variant=str(entry["variant"]),
            stage=stage,
            points_multiplier=int(entry.get("points_multiplier") or points_multiplier_for(stage)),
            race_codes=list(codes),
            marble_radius=radius,
            full_finish_order=recorded,
            finish_times=dict(sim.finish_times),
            eliminated=entry.get("eliminated"),
            boosted_code=boosted,
            available=True,
            source="resim",
            frames=sim.frames,
            sfx_events=list(sim.sfx_events),
        )
        _apply_arena(rep, sim.arena)
        return rep
    return None


def _resim_legacy_v1(entry: Dict[str, Any], stage: str) -> Optional[RaceReplay]:
    from generators.legacy_v1_sim import LEGACY_VARIANTS, OUTER_RADIUS, simulate_legacy_v1
    from generators.marble_league import LEAGUE_COUNTRY_CODES

    if str(entry.get("variant")) not in LEGACY_VARIANTS or stage != "Group Stage":
        return None
    podium = list(entry.get("podium") or [])
    tried: set = set()
    for wc in [None] + list(LEAGUE_COUNTRY_CODES):
        try:
            race = simulate_legacy_v1(str(entry["seed"]), str(entry["variant"]), list(LEAGUE_COUNTRY_CODES), stage, wc)
        except Exception as e:
            logger.warning("Legacy v1 re-simulation failed for %s: %s", entry.get("seed"), e)
            return None
        key = tuple(race.race_codes)
        if key in tried:
            continue
        tried.add(key)
        if race.full_finish_order[0] != entry.get("winner") or race.full_finish_order[: len(podium)] != podium:
            continue
        rep = RaceReplay(
            season=int(entry["season"]),
            race=int(entry["race"]),
            seed=str(entry["seed"]),
            variant=str(entry["variant"]),
            stage=stage,
            points_multiplier=1,
            race_codes=race.race_codes,
            marble_radius=race.marble_radius,
            full_finish_order=race.full_finish_order,
            finish_times=race.finish_times,
            eliminated=entry.get("eliminated"),
            available=True,
            source="legacy_resim",
            frames=race.frames,
            sfx_events=race.sfx_events,
            mode="escape",
            label=str(entry["variant"]).replace("_", " ").title(),
            finish_word="ESCAPED",
            center=(540.0, 920.0),
            escape_radius=OUTER_RADIUS,
            drawer=race.drawer,
        )
        return rep
    return None


def build_race_replay(entry: Dict[str, Any], max_races: int = 28, base_dir: Path = REPLAY_DIR) -> RaceReplay:
    """Returns the best available verified replay for one league `history` entry."""
    race_num = int(entry["race"])
    stage = stage_for_race(race_num, max_races)
    rep = load_replay_file(entry, max_races, base_dir)
    if rep is None:
        try:
            rep = _resim_v2(entry, stage, max_races) if entry.get("finish_order") else _resim_legacy_v1(entry, stage)
        except Exception as e:
            logger.warning("Re-simulation failed for S%s R%s: %s", entry.get("season"), race_num, e)
            rep = None
    if rep is not None:
        return rep
    order = list(entry.get("finish_order") or entry.get("podium") or [entry.get("winner", "")])
    return RaceReplay(
        season=int(entry["season"]),
        race=race_num,
        seed=str(entry.get("seed", "")),
        variant=str(entry.get("variant", "")),
        stage=stage,
        points_multiplier=int(entry.get("points_multiplier") or points_multiplier_for(stage)),
        race_codes=list(order),
        marble_radius=30.0,
        full_finish_order=order,
        eliminated=entry.get("eliminated"),
        label=str(entry.get("variant", "")).replace("_", " ").title(),
    )


def build_season_replays(
    state: Dict[str, Any],
    season: int,
    base_dir: Path = REPLAY_DIR,
    progress_cb: Optional[Callable[[RaceReplay], None]] = None,
) -> List[RaceReplay]:
    max_races = int(state.get("max_races_per_season", 28))
    out: List[RaceReplay] = []
    for entry in state.get("history", []):
        if int(entry.get("season", 0)) != int(season):
            continue
        rep = build_race_replay(entry, max_races, base_dir)
        out.append(rep)
        if progress_cb:
            progress_cb(rep)
    out.sort(key=lambda r: r.race)
    return out
