"""
Call It Arena — Stdlib-only Episodic Season Recap Scheduler (`common/recap_schedule.py`).

Kept dependency-free (Python stdlib only) so `.github/workflows/season_recap.yml` can check
whether a recap milestone is due in <0.1s before setting up Python/pip/ffmpeg, while sharing
the exact same state machine with `generators/season_recap.py` and `main.py`.
"""

import datetime
from typing import Any, Dict, Optional

SAFE_RECAP_KINDS = ("part1", "part2", "part3", "full", "preview")


def pick_next_due_recap(
    league_state: Dict[str, Any],
    posted_state: Dict[str, Any],
    now_utc: Optional[datetime.datetime] = None,
    full_movie_delay_hours: float = 18.0,
) -> Optional[Dict[str, Any]]:
    """
    Determines which 16:9 Marble League recap milestone (if any) is due right now:
      1. Completed seasons (`past_champions`):
         - `s{s}_part3`: Finale episode (`Races (part2_end+1)–28`), due immediately after Race 28.
         - `s{s}_full`: Full 28-race season movie (`Races 1–28`), due >= `full_movie_delay_hours` after `s{s}_part3`.
      2. Active in-progress season (`league_state['season']`):
         - `s{cur}_part1`: Part 1, due once >= 10 races are completed (`1..min(cur_races, 19)` for Season 1 catch-up,
           `1..10` for Season 2+).
         - `s{cur}_part2`: Part 2, due once >= 20 races are completed (`(part1_end+1)..20`).
      Returns `None` on all non-milestone days (no duplicate Sunday recaps).
    """
    now = now_utc or datetime.datetime.now(datetime.timezone.utc)
    done = posted_state.get("season_recaps") or {}
    max_races = int(league_state.get("max_races_per_season", 28))

    # 1. Check completed seasons first (so Race 28 evening triggers Part 3 Finale, then Full Movie next day)
    completed = sorted({int(c["season"]) for c in league_state.get("past_champions", [])})
    for s in completed:
        p3_key = f"s{s}_part3"
        full_key = f"s{s}_full"
        legacy_done = str(s) in done
        if p3_key not in done and not legacy_done:
            p2_end = int((done.get(f"s{s}_part2") or {}).get("end_race", 20))
            start_r = min(max_races, max(1, p2_end + 1))
            return {
                "key": p3_key,
                "season": s,
                "recap_kind": "part3",
                "start_race": start_r,
                "end_race": max_races,
            }
        if full_key not in done and not legacy_done:
            p3_rec = done.get(p3_key) or {}
            p3_ts = p3_rec.get("posted_utc")
            ready = True
            if p3_ts:
                try:
                    dt = datetime.datetime.fromisoformat(str(p3_ts).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    ready = (now - dt).total_seconds() >= full_movie_delay_hours * 3600.0
                except Exception:
                    ready = True
            if ready:
                return {
                    "key": full_key,
                    "season": s,
                    "recap_kind": "full",
                    "start_race": 1,
                    "end_race": max_races,
                }

    # 2. Check active season milestones (Race 10 -> Part 1, Race 20 -> Part 2)
    cur = int(league_state.get("season", 1))
    cur_hist = [int(e["race"]) for e in league_state.get("history", []) if int(e.get("season", 0)) == cur]
    cur_races = max(cur_hist) if cur_hist else 0

    p1_key = f"s{cur}_part1"
    p2_key = f"s{cur}_part2"
    if cur_races >= 10 and p1_key not in done:
        # Season 1 was already past Race 10 when episodic recaps launched, so Part 1 covers all completed races so far
        end_r = min(cur_races, 19) if cur == 1 else 10
        return {
            "key": p1_key,
            "season": cur,
            "recap_kind": "part1",
            "start_race": 1,
            "end_race": end_r,
        }
    if cur_races >= 20 and p1_key in done and p2_key not in done:
        p1_end = int((done.get(p1_key) or {}).get("end_race", 10))
        start_r = min(20, max(1, p1_end + 1))
        return {
            "key": p2_key,
            "season": cur,
            "recap_kind": "part2",
            "start_race": start_r,
            "end_race": 20,
        }

    return None
