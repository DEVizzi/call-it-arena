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
        self.assertGreater(sum(s.duration for s in segs), 60.0)
        race_seg = next(s for s in segs if s.name.startswith("race_"))
        for t in (0.0, race_seg.duration * 0.5, race_seg.duration - 0.05):
            img = race_seg.render(t)
            self.assertEqual(img.size, (1920, 1080))
        meta = sr.build_recap_metadata(data, [(0.0, "Cold open"), (12.0, "The 16 nations"), (30.0, "Group Stage")],
                                       100.0)
        self.assertLessEqual(len(meta["title"]), 100)
        self.assertIn("0:00 Cold open", meta["description"])
        after = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
