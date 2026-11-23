"""Call It Arena - Marble League tournament rules (v2: carry-over points elimination).

Points never reset. After every Knockout-Stage race, the ACTIVE country with the LOWEST CUMULATIVE TOTAL (league points
+ every point scored since) is eliminated - not the country that finished last in that one race. The last
`teams_entering_semifinals` countries play a pure-knockout Semi-Final (top `semifinal_advance` finishers advance) and a
pure-knockout Grand Final (the winner is champion). Cumulative points only seed / order those knockout rounds.

Standings are rebuilt by REPLAYING the immutable per-race log (`state["history"]`), so corrections are safe:
  replay_standings()          single source of truth (spec 6.1 compute_standings)
  get_elimination_candidate() lowest cumulative total among active countries + the tie-break used (spec 6.2)
  record_game()               validate + score + eliminate + append one race to the log (spec 6.3)
  what_if_report()            read-only replay under another rules version (spec 6.4 / 3.3a)
  edit_past_game()            correct a past race's points and recompute forward, flagging changed eliminations (6.5)

History entries carry `rules_version`: races played under the old rule keep "v1_single_game_last_place" (their stored
elimination is history and is never rewritten); new races use "v2_cumulative".
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

RULES_V1 = "v1_single_game_last_place"
RULES_V2 = "v2_cumulative"

POINTS_TABLE = [10, 7, 5, 3, 1]

STAGE_LEAGUE = "Group Stage"
STAGE_ELIM = "Knockout Stage"
STAGE_SEMI = "Semi-Final"
STAGE_FINAL = "Monthly Grand Final"

PHASE_OF_STAGE = {STAGE_LEAGUE: "league", STAGE_ELIM: "elimination", STAGE_SEMI: "semifinal", STAGE_FINAL: "final"}

# Phase boundaries are CONFIG, never hard-coded. 16 countries, 28 races:
#   races 1-14 league (no eliminations) -> races 15-26 one elimination each (16 -> 4)
#   -> race 27 Semi-Final (4 race, top 2 advance) -> race 28 Grand Final (2 race, winner is champion)
DEFAULT_CONFIG: Dict[str, Any] = {
    "league_games": 14,
    "total_games": 28,
    "teams_entering_semifinals": 4,
    "semifinal_advance": 2,
    "eliminations_per_game": 1,
    "tiebreaker_order": ["last_game_points", "league_points", "game_wins", "host_decision"],
    "reset_points_for_knockouts": True,
    "carry_over_points": True,          # False = old v1 behaviour (last finisher of the race is eliminated)
}

OVERLAY_RULE = ("Points never reset. After every game from here on, the team with the lowest total points "
                "across the whole tournament is eliminated.")
SHORT_RULE = "Points never reset. Lowest total is out."


class InvalidGameResult(ValueError):
    pass


def load_config(state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update((state or {}).get("rules_config") or {})
    if state is not None and "max_races_per_season" in state and "total_games" not in (state.get("rules_config") or {}):
        cfg["total_games"] = int(state["max_races_per_season"])
    return cfg


def rules_version(cfg: Dict[str, Any]) -> str:
    return RULES_V2 if cfg.get("carry_over_points", True) else RULES_V1


# ------------------------------------------------------------------------------------------------ stages
def stage_for_race(race_num: int, cfg: Optional[Dict[str, Any]] = None, n_active: Optional[int] = None) -> str:
    cfg = cfg or DEFAULT_CONFIG
    total = int(cfg["total_games"])
    if race_num >= total:
        return STAGE_FINAL
    if race_num <= int(cfg["league_games"]):
        return STAGE_LEAGUE
    if race_num == total - 1:
        return STAGE_SEMI
    if n_active is not None and n_active <= int(cfg["teams_entering_semifinals"]):
        return STAGE_SEMI                       # fewer teams than expected: move to the knockouts automatically
    return STAGE_ELIM


def points_multiplier_for(stage: str) -> int:
    return 1                                    # knockouts are decided by finishing position, never by points


def points_for_order(finish_order: Sequence[str], multiplier: int = 1) -> Dict[str, int]:
    pts = {c: 0 for c in finish_order}
    for i, c in enumerate(list(finish_order)[: len(POINTS_TABLE)]):
        pts[c] = POINTS_TABLE[i] * int(multiplier)
    return pts


def entry_points(entry: Dict[str, Any]) -> Dict[str, int]:
    """Points each country scored in one logged race (stored, or derived from the finish order for old entries)."""
    if isinstance(entry.get("points_by_team"), dict):
        return {str(k): int(v) for k, v in entry["points_by_team"].items()}
    return points_for_order(entry.get("finish_order") or [], int(entry.get("points_multiplier", 1)))


def entry_eliminated(entry: Dict[str, Any]) -> List[str]:
    out = list(entry.get("eliminated_all") or [])
    if not out and entry.get("eliminated"):
        out = [str(entry["eliminated"])]
    return out


# ------------------------------------------------------------------------------------------------ replay
def _blank(codes: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    return {c: {"code": c, "total_points": 0, "league_points": 0, "game_wins": 0, "podiums": 0,
                "last_game_points": 0, "status": "active", "eliminated_in_game": None} for c in codes}


def replay_standings(history: Sequence[Dict[str, Any]], codes: Sequence[str], season: Optional[int] = None,
                     cfg: Optional[Dict[str, Any]] = None, rules: str = "recorded") -> Dict[str, Any]:
    """Replays the race log. rules="recorded" applies each race's STORED elimination (published history);
    rules=RULES_V2 / RULES_V1 recomputes every elimination under that rule (read-only what-if).
    Returns {"teams": {code: team}, "eliminations": [(race, [codes], tiebreak, margin)]}."""
    cfg = cfg or DEFAULT_CONFIG
    teams = _blank(codes)
    elims: List[Tuple[int, List[str], Optional[str], Optional[int]]] = []
    for e in history:
        if season is not None and int(e.get("season", season)) != int(season):
            continue
        race = int(e["race"])
        pts = entry_points(e)
        order = list(e.get("finish_order") or [])
        for c, t in teams.items():
            if t["status"] == "active":
                t["last_game_points"] = 0
        for c, p in pts.items():
            if c in teams:
                teams[c]["total_points"] += int(p)
                teams[c]["last_game_points"] = int(p)
        if order or e.get("podium"):
            pod = list(e.get("podium") or order[:3])
            win = e.get("winner") or (order[0] if order else pod[0])
            teams[win]["game_wins"] += 1
            for c in pod[:3]:
                teams[c]["podiums"] += 1
        snap = e.get("points_after")
        if isinstance(snap, dict):              # authoritative snapshot (early S1 entries only logged the podium)
            for c, v in snap.items():
                if c in teams:
                    teams[c]["total_points"] = int(v)
        if race == int(cfg["league_games"]):
            for t in teams.values():
                t["league_points"] = t["total_points"]
        if rules == "recorded":
            out = [c for c in entry_eliminated(e) if c in teams]
            tb, margin = e.get("tiebreak_used"), e.get("elimination_margin")
        else:
            stage = e.get("stage") or stage_for_race(race, cfg)
            out, tb, margin = _eliminations_for(stage, teams, order, e.get("seed", ""), race, cfg, rules)
        for c in out:
            teams[c]["status"] = "eliminated"
            teams[c]["eliminated_in_game"] = race
        if out:
            elims.append((race, out, tb, margin))
    return {"teams": teams, "eliminations": elims}


def _eliminations_for(stage: str, teams: Dict[str, Dict[str, Any]], order: Sequence[str], seed: str, race: int,
                      cfg: Dict[str, Any], rules: str) -> Tuple[List[str], Optional[str], Optional[int]]:
    active = [c for c, t in teams.items() if t["status"] == "active"]
    raced = [c for c in order if c in active]
    if stage == STAGE_ELIM and len(active) > int(cfg["teams_entering_semifinals"]):
        if rules == RULES_V1:
            return ([raced[-1]] if raced else []), None, None
        cand = get_elimination_candidate(teams, cfg, seed=f"{seed}:{race}")
        return ([cand["code"]] if cand else []), (cand or {}).get("tiebreak_used"), (cand or {}).get("margin")
    if stage == STAGE_SEMI:
        keep = int(cfg["semifinal_advance"])
        return raced[keep:], "semifinal_knockout", None
    if stage == STAGE_FINAL:
        return raced[1:], "final_knockout", None
    return [], None, None


# ------------------------------------------------------------------------------------------------ elimination
def _coin(seed: str, code: str) -> int:
    return int(hashlib.sha256(f"{seed}:{code}".encode()).hexdigest()[:8], 16)


def get_elimination_candidate(teams: Dict[str, Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
                              seed: str = "") -> Optional[Dict[str, Any]]:
    """The ACTIVE team with the LOWEST cumulative total. Ties use cfg['tiebreaker_order'] (the higher value survives);
    'host_decision' is an automatic seeded coin flip, recorded as such."""
    cfg = cfg or DEFAULT_CONFIG
    active = [t for t in teams.values() if t["status"] == "active"]
    if len(active) < 2:
        return None
    order = [k for k in cfg.get("tiebreaker_order", []) if k != "host_decision"]

    def key(t: Dict[str, Any]) -> Tuple:
        return (t["total_points"],) + tuple(t.get(k, 0) for k in order) + (_coin(seed, t["code"]),)

    ranked = sorted(active, key=key)
    low, nxt = ranked[0], ranked[1]
    tiebreak = None
    if low["total_points"] == nxt["total_points"]:
        tiebreak = "host_decision:coin_flip"
        for k in order:
            if low.get(k, 0) != nxt.get(k, 0):
                tiebreak = k
                break
    return {"code": low["code"], "total": low["total_points"], "next_code": nxt["code"],
            "next_total": nxt["total_points"], "margin": nxt["total_points"] - low["total_points"],
            "tiebreak_used": tiebreak}


def danger_zone(teams: Dict[str, Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Before a race: who is currently lowest on total among active teams, and the gap to the next team."""
    return get_elimination_candidate(teams, cfg, seed="danger")


# ------------------------------------------------------------------------------------------------ recording
def record_game(state: Dict[str, Any], race_num: int, stage: str, finish_order: Sequence[str],
                codes: Sequence[str], seed: str = "", cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validates one race, scores it, decides the elimination(s) under the configured rules and returns the log fields
    (`points_by_team`, `eliminated`, `eliminated_all`, `tiebreak_used`, `elimination_margin`, `rules_version`, `phase`).
    The caller appends the entry to state['history'] and mirrors the counters into state['countries']."""
    cfg = cfg or load_config(state)
    if not finish_order or len(set(finish_order)) != len(finish_order):
        raise InvalidGameResult("finish order is empty or has duplicates")
    unknown = [c for c in finish_order if c not in codes]
    if unknown:
        raise InvalidGameResult(f"unknown countries in finish order: {unknown}")
    countries = state.get("countries", {})
    dead = [c for c in finish_order if countries.get(c, {}).get("eliminated")]
    if dead:
        raise InvalidGameResult(f"eliminated countries cannot race: {dead}")
    mult = points_multiplier_for(stage)
    pts = points_for_order(finish_order, mult)
    if any(v < 0 for v in pts.values()):
        raise InvalidGameResult("negative points")
    season = int(state.get("season", 1))
    rv = rules_version(cfg)
    probe = {"season": season, "race": race_num, "finish_order": list(finish_order), "points_by_team": pts,
             "stage": stage, "seed": seed}
    before = replay_standings(state.get("history", []), codes, season, cfg)["teams"]
    teams = replay_standings(list(state.get("history", [])) + [dict(probe, eliminated=None)], codes, season, cfg)["teams"]
    out, tb, margin = _eliminations_for(stage, teams, finish_order, seed, race_num, cfg, rv)
    return {
        "points_by_team": pts,
        "points_multiplier": mult,
        "eliminated": out[0] if out else None,
        "eliminated_all": out,
        "tiebreak_used": tb,
        "elimination_margin": margin,
        "host_override": False,
        "rules_version": rv,
        "phase": PHASE_OF_STAGE.get(stage, "league"),
        "danger_before": (danger_zone(before, cfg) or {}).get("code") if stage == STAGE_ELIM else None,
    }


def what_if_report(history: Sequence[Dict[str, Any]], codes: Sequence[str], season: int,
                   rules: str = RULES_V2, cfg: Optional[Dict[str, Any]] = None) -> str:
    """Read-only: replays the season under `rules` and compares eliminations with the published ones."""
    cfg = cfg or DEFAULT_CONFIG
    rec = replay_standings(history, codes, season, cfg, "recorded")
    alt = replay_standings(history, codes, season, cfg, rules)
    rec_by = {r: out for r, out, _, _ in rec["eliminations"]}
    lines = [f"WHAT-IF: Season {season} replayed under {rules} (stored results are NOT changed)", ""]
    for race, out, tb, margin in alt["eliminations"]:
        was = rec_by.get(race, [])
        flag = "" if was == out else f"   (published: {', '.join(was) or 'nobody'})"
        lines.append(f"Race {race}: {', '.join(out)} out" + (f" by {margin}" if margin is not None else "")
                     + (f" [tie-break: {tb}]" if tb and "knockout" not in tb else "") + flag)
    for race, out in rec_by.items():
        if race not in {r for r, *_ in alt["eliminations"]}:
            lines.append(f"Race {race}: nobody out under {rules}   (published: {', '.join(out)})")
    return "\n".join(lines)


def edit_past_game(state: Dict[str, Any], season: int, race: int, finish_order: Sequence[str],
                   codes: Sequence[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Returns (new_state, warnings): replaces one race's finish order, recomputes points and every v2 elimination from
    that race forward, and warns for each elimination that changes. v1 races keep their stored eliminations."""
    cfg = load_config(state)
    new = copy.deepcopy(state)
    hist = new.get("history", [])
    idx = next((i for i, e in enumerate(hist) if int(e.get("season", season)) == season and int(e["race"]) == race), None)
    if idx is None:
        raise InvalidGameResult(f"race {race} not found")
    warnings: List[str] = []
    hist[idx]["finish_order"] = list(finish_order)
    hist[idx]["points_by_team"] = points_for_order(finish_order, int(hist[idx].get("points_multiplier", 1)))
    for i in range(idx, len(hist)):
        e = hist[i]
        if int(e.get("season", season)) != season or e.get("rules_version") != RULES_V2:
            continue
        teams = replay_standings(hist[:i] + [dict(e, eliminated=None, eliminated_all=[])], codes, season, cfg)["teams"]
        out, tb, margin = _eliminations_for(e.get("stage") or stage_for_race(int(e["race"]), cfg), teams,
                                            e.get("finish_order") or [], e.get("seed", ""), int(e["race"]), cfg, RULES_V2)
        if out != entry_eliminated(e):
            warnings.append(f"Race {e['race']}: elimination changes {entry_eliminated(e)} -> {out}")
        e.update(eliminated=out[0] if out else None, eliminated_all=out, tiebreak_used=tb, elimination_margin=margin)
    sync_countries(new, codes)
    return new, warnings


def sync_countries(state: Dict[str, Any], codes: Sequence[str]) -> None:
    """Rebuilds state['countries'] counters from the log (current season only)."""
    cfg = load_config(state)
    season = int(state.get("season", 1))
    teams = replay_standings(state.get("history", []), codes, season, cfg)["teams"]
    for c in codes:
        t, row = teams[c], state["countries"][c]
        row.update(points=t["total_points"], wins=t["game_wins"], podiums=t["podiums"],
                   eliminated=t["status"] == "eliminated", eliminated_in=t["eliminated_in_game"],
                   league_points=t["league_points"])
