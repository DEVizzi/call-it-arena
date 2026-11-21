"""
Call It Arena — Season recap, race replays and commentary tests (`tests/test_season_recap.py`).

Pure-Python checks (no ffmpeg, no TTS): safe for local `python3 -m unittest` and CI `pytest -v`.
Never mutates live `state/` files.
"""

import copy
import tempfile
import unittest
from pathlib import Path

from common.audio import VoiceCue
from common.commentary import (
    analyze_race,
    country_name,
    finish_threshold_for,
    plan_shorts_lines,
    plan_shorts_post_reveal_line,
    schedule_lines_into_cues,
)
from generators.marble_league import (
    LEAGUE_COUNTRY_CODES,
    LEAGUE_STATE_FILE,
    _progress,
    compute_standings,
    load_league_state,
    run_tuned_race,
    simulate_race,
)
from generators.race_replay import build_race_replay, load_replay_file, save_replay_file


class _StubBank:
    """VoiceBank stand-in: deterministic durations, no audio synthesis."""

    def __init__(self, *_a):
        self.cache = {}

    def get(self, text):
        return Path("/nonexistent.wav"), max(1.0, 0.065 * len(text))

    def dur(self, text):
        return self.get(text)[1]


def _full_order(sim, codes):
    un = [c for c in codes if c not in sim.finish_order]
    un.sort(key=lambda c: sim.final_progress[c], reverse=True)
    return sim.finish_order + un


class ReplayFileTest(unittest.TestCase):
    def test_replay_file_roundtrip_is_frame_accurate(self) -> None:
        codes = list(LEAGUE_COUNTRY_CODES[:10])
        sim = simulate_race("replay-rt", "windmill_drop", codes, 30, 1.0, sim_until_sec=2.0)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            save_replay_file(9, 3, "replay-rt", "windmill_drop", 1.0, codes, 30, None, "regular", sim,
                             _full_order(sim, codes), base_dir=base)
            entry = {"season": 9, "race": 3, "seed": "replay-rt", "variant": "windmill_drop"}
            rep = load_replay_file(entry, base_dir=base)
            self.assertIsNotNone(rep)
            assert rep is not None
            self.assertEqual(rep.source, "replay_file")
            self.assertEqual(len(rep.frames), len(sim.frames))
            for f_idx in (0, len(sim.frames) // 2, len(sim.frames) - 1):
                for c in codes:
                    x0, y0, _ = sim.frames[f_idx]["marbles"][c]
                    x1, y1, _ = rep.frames[f_idx]["marbles"][c]
                    self.assertLess(abs(x0 - x1), 0.06)
                    self.assertLess(abs(y0 - y1), 0.06)
            self.assertEqual(rep.mode, "line")
            self.assertIsNotNone(rep.drawer)

    def test_reconstruction_never_contradicts_recorded_results(self) -> None:
        before = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
        state = load_league_state()
        for entry in state.get("history", [])[:8]:
            rep = build_race_replay(entry, base_dir=Path(tempfile.gettempdir()) / "no_replays_here")
            if rep.available:
                self.assertEqual(rep.winner, entry["winner"])
                self.assertEqual(rep.full_finish_order[: len(entry["podium"])], entry["podium"])
                if entry.get("finish_order"):
                    self.assertEqual(rep.full_finish_order, entry["finish_order"])
                self.assertGreater(len(rep.frames), 30)
        after = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
        self.assertEqual(before, after)


class CommentaryTest(unittest.TestCase):
    def test_shorts_commentary_never_names_the_winner_before_reveal(self) -> None:
        codes = list(LEAGUE_COUNTRY_CODES[:12])
        for seed, variant in (("cm-1", "hexagon_spin"), ("cm-2", "plinko_drop"), ("cm-3", "double_ring")):
            sim = run_tuned_race(seed, variant, codes, 30)
            arena = sim.arena
            full = _full_order(sim, codes)
            events = analyze_race(sim.frames, codes, sim.finish_times, full, lambda x, y: _progress(arena, x, y),
                                  finish_threshold_for(arena.mode, arena.center, arena.escape_radius,
                                                       arena.finish_y, 30))
            for stage in ("Group Stage", "Knockout Stage", "Championship Finals"):
                lines = plan_shorts_lines(seed, events, codes, stage, 1, False, None, 15.5, full[0])
                self.assertTrue(lines)
                for ln in lines:
                    self.assertNotIn(country_name(full[0]), ln.text, f"winner spoiled: {ln.text}")
                    self.assertLessEqual(len(ln.text), 46)
                    self.assertLessEqual(ln.window_end, 15.5)

    def test_cue_scheduler_never_overlaps_and_respects_windows(self) -> None:
        base = [VoiceCue(0.15, Path("x"), "hook", 3.0), VoiceCue(15.5, Path("x"), "count", 2.4),
                VoiceCue(18.5, Path("x"), "reveal", 3.2), VoiceCue(24.2, Path("x"), "outro", 1.8)]
        codes = list(LEAGUE_COUNTRY_CODES[:10])
        sim = run_tuned_race("cue-1", "single_ring", codes, 30)
        full = _full_order(sim, codes)
        events = analyze_race(sim.frames, codes, sim.finish_times, full, lambda x, y: _progress(sim.arena, x, y),
                              400.0)
        planned = plan_shorts_lines("cue-1", events, codes, "Group Stage", 1, False, None, 15.5, full[0])
        post = plan_shorts_post_reveal_line("cue-1", "Knockout Stage", "CA", full[0], [], False, 21.7, 24.05)
        planned.append(post)
        cues = schedule_lines_into_cues(base, planned, lambda t: (Path("x"), 0.06 * len(t)))
        spans = sorted((c.timestamp_sec, c.timestamp_sec + c.duration_sec) for c in cues)
        for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
            self.assertLessEqual(a1, b0 + 1e-6)
        self.assertGreater(len(cues), len(base))


class SeasonRecapPlanTest(unittest.TestCase):
    def test_recap_table_matches_live_league_and_renders(self) -> None:
        import generators.season_recap as sr
        from generators.race_replay import build_season_replays

        before = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
        state = load_league_state()
        season = int(state.get("season", 1))
        if not any(int(e.get("season", 0)) == season for e in state.get("history", [])):
            self.skipTest("no races yet this season")
        replays = build_season_replays(copy.deepcopy(state), season)
        data = sr.build_season_data(state, season, replays)

        live = compute_standings(state)
        live_pts = {s["code"]: int(s["points"]) for s in live}
        for c, pts in live_pts.items():
            self.assertEqual(int(round(data.points_after[-1][c])), pts, f"points mismatch for {c}")
        self.assertEqual([r["code"] for r in data.final_table], [s["code"] for s in live])

        segs = sr.plan_segments(data, _StubBank())
        names = [s.name for s in segs]
        self.assertEqual(names[0], "cold_open")
        self.assertEqual(names[-1], "end_screen")
        # Verify sped-up starting sequence reaches Race 1 in <16.5s (down from ~28s)
        first_race_i = next(i for i, s in enumerate(segs) if s.name.startswith("race_"))
        intro_dur = sum(s.duration for s in segs[:first_race_i])
        self.assertLess(intro_dur, 16.5)
        self.assertGreater(sum(s.duration for s in segs), 60.0)
        race_seg = segs[first_race_i]
        for t in (0.0, race_seg.duration * 0.5, race_seg.duration - 0.05):
            img = race_seg.render(t)
            self.assertEqual(img.size, (1920, 1080))
        meta = sr.build_recap_metadata(data, [(0.0, "Intro"), (12.0, "The Group Stage")], 100.0)
        self.assertLessEqual(len(meta["title"]), 100)
        self.assertIn("0:00 Intro", meta["description"])

        # Also test an episodic Part 2 slice (start_race=6..end_race=10) preserves cumulative points
        if len(replays) >= 10:
            slice_data = sr.build_season_data(state, season, replays, start_race=6, end_race=10, recap_kind="part2")
            slice_segs = sr.plan_segments(slice_data, _StubBank())
            race_names = [s.name for s in slice_segs if s.name.startswith("race_")]
            self.assertEqual(race_names, ["race_6", "race_7", "race_8", "race_9", "race_10"])
            self.assertIn("table_entering_table", [s.name for s in slice_segs])
            # Render entering table and Part 2 teaser frames cleanly
            for seg_check in slice_segs[:4] + [slice_segs[-2]]:
                im = seg_check.render(seg_check.duration * 0.5)
                self.assertEqual(im.size, (1920, 1080))

        after = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
        self.assertEqual(before, after)


class EpisodicRecapScheduleTest(unittest.TestCase):
    def test_season1_catchup_and_season2_clean_10_10_8_plus_full_lifecycle(self) -> None:
        import datetime as dt
        from common.recap_schedule import pick_next_due_recap

        t0 = dt.datetime(2026, 4, 15, 17, 20, tzinfo=dt.timezone.utc)
        league = {
            "season": 1,
            "max_races_per_season": 28,
            "past_champions": [],
            "history": [{"season": 1, "race": r} for r in range(1, 14)],
        }
        posted: dict = {"season_recaps": {}}

        # 1. Season 1 at Race 13 -> Part 1 due right now covering Races 1..13
        due = pick_next_due_recap(league, posted, now_utc=t0)
        self.assertEqual(due, {"key": "s1_part1", "season": 1, "recap_kind": "part1", "start_race": 1, "end_race": 13})
        posted["season_recaps"]["s1_part1"] = {"end_race": 13, "posted_utc": t0.isoformat()}

        # 2. Races 14..19 (including Sunday!) -> None (no duplicate Sunday recaps)
        league["history"] = [{"season": 1, "race": r} for r in range(1, 20)]
        self.assertIsNone(pick_next_due_recap(league, posted, now_utc=t0 + dt.timedelta(days=4)))

        # 3. Race 20 -> Part 2 due covering Races 14..20
        league["history"] = [{"season": 1, "race": r} for r in range(1, 21)]
        due2 = pick_next_due_recap(league, posted, now_utc=t0 + dt.timedelta(days=7))
        self.assertEqual(due2, {"key": "s1_part2", "season": 1, "recap_kind": "part2", "start_race": 14, "end_race": 20})
        posted["season_recaps"]["s1_part2"] = {"end_race": 20, "posted_utc": (t0 + dt.timedelta(days=7)).isoformat()}

        # 4. Season 1 finishes Race 28 -> league rolls to Season 2 (race 0), Season 1 in past_champions
        t_fin = t0 + dt.timedelta(days=15)
        league["past_champions"] = [{"season": 1, "champion": "JP"}]
        league["season"] = 2
        league["history"].extend([{"season": 1, "race": r} for r in range(21, 29)])
        due3 = pick_next_due_recap(league, posted, now_utc=t_fin)
        self.assertEqual(due3, {"key": "s1_part3", "season": 1, "recap_kind": "part3", "start_race": 21, "end_race": 28})
        posted["season_recaps"]["s1_part3"] = {"end_race": 28, "posted_utc": t_fin.isoformat()}

        # 5. 2 hours after Part 3 -> Full Movie waits for >=18h spacing
        self.assertIsNone(pick_next_due_recap(league, posted, now_utc=t_fin + dt.timedelta(hours=2)))

        # 6. Next evening (~24h later) -> Full 28-race Season 1 Movie due!
        due_full = pick_next_due_recap(league, posted, now_utc=t_fin + dt.timedelta(hours=24))
        self.assertEqual(due_full, {"key": "s1_full", "season": 1, "recap_kind": "full", "start_race": 1, "end_race": 28})
        posted["season_recaps"]["s1_full"] = {"end_race": 28, "posted_utc": (t_fin + dt.timedelta(hours=24)).isoformat()}

        # 7. Season 2 splits cleanly into 1..10, 11..20, 21..28, and 1..28!
        league["history"].extend([{"season": 2, "race": r} for r in range(1, 12)])
        due_s2_p1 = pick_next_due_recap(league, posted, now_utc=t_fin + dt.timedelta(days=11))
        self.assertEqual(due_s2_p1, {"key": "s2_part1", "season": 2, "recap_kind": "part1", "start_race": 1, "end_race": 10})
        posted["season_recaps"]["s2_part1"] = {"end_race": 10, "posted_utc": (t_fin + dt.timedelta(days=11)).isoformat()}

        league["history"].extend([{"season": 2, "race": r} for r in range(12, 21)])
        due_s2_p2 = pick_next_due_recap(league, posted, now_utc=t_fin + dt.timedelta(days=20))
        self.assertEqual(due_s2_p2, {"key": "s2_part2", "season": 2, "recap_kind": "part2", "start_race": 11, "end_race": 20})


if __name__ == "__main__":
    unittest.main()
