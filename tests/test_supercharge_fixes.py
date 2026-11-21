"""
Call It Arena — Unit tests for live production bug fixes & Season 2+ Crowd Boost (`tests/test_supercharge_fixes.py`).

Written with standard `unittest.TestCase` so it runs cleanly under both:
- `python3 -m unittest` locally on macOS
- `pytest -v` in GitHub Actions CI
Never mutates live `state/` files.
"""

import datetime
import unittest

from common.comments import (
    PROCESSED_COMMENTS_FILE,
    _parse_requests_with_gemini_or_regex,
    fetch_recent_comments_and_pick_wildcard,
    is_own_channel_author,
    select_wildcard_from_matches,
)
from common.seo import (
    build_localizations,
    build_title_candidates,
    select_best_non_repeating_title,
)
from generators.marble_league import (
    LEAGUE_COUNTRY_CODES,
    LEAGUE_STATE_FILE,
    compute_standings,
    load_league_state,
    pick_pre_race_focus_and_rival,
    resolve_crowd_boost,
    simulate_race,
)
from main import infer_auto_slot, resolve_slot_and_format


class SuperchargeFixesTest(unittest.TestCase):
    def test_auto_slot_resilient_to_morning_cron_delay(self) -> None:
        delayed_morning = datetime.datetime(2026, 11, 22, 10, 45, tzinfo=datetime.timezone.utc)
        # Morning cron delayed past 10:00 UTC must still run Slot A if 20261122-A hasn't run yet
        self.assertEqual(infer_auto_slot(delayed_morning, used_seeds=["20261121-B"]), "A")
        # Once 20261122-A has run, subsequent run resolves to Slot B
        self.assertEqual(infer_auto_slot(delayed_morning, used_seeds=["20261122-A"]), "B")

        evening = datetime.datetime(2026, 11, 22, 14, 10, tzinfo=datetime.timezone.utc)
        self.assertEqual(infer_auto_slot(evening, used_seeds=[]), "B")

        slot_id, fmt, _ = resolve_slot_and_format(
            "A", "auto", {"format_weights": {"tower_quake": 1.0}}, "20261122-A", now_utc=delayed_morning
        )
        self.assertEqual(slot_id, "A")
        self.assertNotEqual(fmt, "marble_league")

    def test_own_channel_comments_filtered_from_wildcard(self) -> None:
        self.assertTrue(is_own_channel_author("@CallItArena"))
        self.assertTrue(is_own_channel_author("callitarena"))
        self.assertTrue(is_own_channel_author("@callitarena.official"))
        self.assertFalse(is_own_channel_author("@rahul_arena_fan"))

        raw = [
            {
                "id": "yt_1",
                "author": "@CallItArena",
                "text": "Who wins tomorrow? 🇮🇳 India or 🇺🇸 USA? Comment below!",
            },
            {
                "id": "yt_2",
                "author": "@pinoy_racer",
                "text": "Let's go Philippines 🇵🇭!!",
            },
            {
                "id": "ig_3",
                "author": "aarav_sharma",
                "text": "India 🇮🇳 all the way!",
            },
            {
                "id": "ig_4",
                "author": "rohan_k",
                "text": "Bharat 🇮🇳 wins next!",
            },
        ]
        matches = _parse_requests_with_gemini_or_regex(raw)
        # @CallItArena self-comment must be excluded; 3 real viewer votes remain (PH x1, IN x2)
        self.assertEqual(len(matches), 3)
        self.assertTrue(all(user != "@CallItArena" for _, user in matches))

        wc = select_wildcard_from_matches(matches)
        self.assertIsNotNone(wc)
        assert wc is not None
        # Non-core fan pick (PH) gets the Group Stage Wildcard berth, while IN is tracked as overall Crowd Vote leader
        self.assertEqual(wc["country_code"], "PH")
        self.assertEqual(wc["username"], "@pinoy_racer")
        self.assertEqual(wc["crowd_code"], "IN")
        self.assertEqual(wc["crowd_votes"], 2)

    def test_dry_run_wildcard_clears_stale_self_vote_without_mutating_disk(self) -> None:
        before = PROCESSED_COMMENTS_FILE.read_text(encoding="utf-8") if PROCESSED_COMMENTS_FILE.exists() else None
        wc = fetch_recent_comments_and_pick_wildcard(recent_posts=[], allow_network=False)
        after = PROCESSED_COMMENTS_FILE.read_text(encoding="utf-8") if PROCESSED_COMMENTS_FILE.exists() else None
        self.assertEqual(before, after)
        if wc is not None:
            self.assertFalse(is_own_channel_author(wc.get("username", "")))

    def test_pre_race_rivalry_never_uses_finish_order(self) -> None:
        state = load_league_state()
        pre_standings = compute_standings(state)
        race_codes = ["IN", "US", "BR", "ID", "PH", "BD", "DE", "TR", "NG", "JP"]
        focus_code, rival_code = pick_pre_race_focus_and_rival(
            race_codes,
            pre_standings,
            seed="20261122-B",
            wildcard={"country_code": "PH", "username": "@fan"},
        )
        self.assertEqual(focus_code, "IN")
        self.assertIn(rival_code, race_codes)
        self.assertNotEqual(focus_code, rival_code)

    def test_seo_breaks_two_pattern_ping_pong_and_matches_arena_mode(self) -> None:
        line_ctx = {
            "series_short": "S1·R14",
            "arena_mode": "line",
            "focus_country": "India",
            "focus_emoji": "🇮🇳",
            "rival_country": "Brazil",
            "rival_emoji": "🇧🇷",
        }
        line_candidates = build_title_candidates("marble_league", line_ctx)
        self.assertIn("Finish First", line_candidates[0][1])
        self.assertNotIn("Escape", line_candidates[0][1])
        for _, title in line_candidates:
            self.assertLessEqual(len(title), 70)

        locs = build_localizations("marble_league", "20261122-B", line_ctx)
        self.assertIn("Chega Primeiro", locs["pt"]["title"])
        for lang_meta in locs.values():
            self.assertLessEqual(len(lang_meta["title"]), 70)

        # Simulate 8 consecutive Marble League runs and verify no 2-pattern A-B-A-B lock-in
        recent = []
        for day in range(1, 9):
            pid, _ = select_best_non_repeating_title(
                line_candidates, "Marble Race", recent, seed=f"202611{day + 13:02d}-B"
            )
            if recent:
                self.assertNotEqual(pid, recent[-1], "Repeated exact same title pattern back-to-back")
            recent.append(pid)
        self.assertGreaterEqual(len(set(recent)), 4)

    def test_crowd_boost_strictly_gated_to_season_2_plus(self) -> None:
        before_league = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
        race_codes = list(LEAGUE_COUNTRY_CODES[:10])
        fan_wc = {
            "country_code": "PH",
            "username": "@pinoy_fan",
            "crowd_code": "IN",
            "crowd_label": "🇮🇳 India",
            "crowd_votes": 7,
            "crowd_user": "@aarav",
        }

        # Season 1 MUST NEVER apply Crowd Boost
        s1_code, s1_banner = resolve_crowd_boost(1, race_codes, fan_wc)
        self.assertIsNone(s1_code)
        self.assertIsNone(s1_banner)

        # Season 2 MUST reject channel self-votes
        bot_code, _ = resolve_crowd_boost(2, race_codes, {"crowd_code": "IN", "crowd_user": "@CallItArena"})
        self.assertIsNone(bot_code)

        # Season 2 MUST activate Crowd Boost for real viewer votes
        s2_code, s2_banner = resolve_crowd_boost(2, race_codes, fan_wc)
        self.assertEqual(s2_code, "IN")
        self.assertIsNotNone(s2_banner)
        assert s2_banner is not None
        self.assertIn("CROWD BOOST (+5%)", s2_banner)
        self.assertIn("7 comments", s2_banner)

        # Physics check: Season 1 (boosted_code=None) vs Season 2 (boosted_code="IN") both simulate cleanly
        sim_s1 = simulate_race("s2-check", "single_ring", race_codes, 30, 1.0, sim_until_sec=3.0, boosted_code=None)
        sim_s1_repeat = simulate_race("s2-check", "single_ring", race_codes, 30, 1.0, sim_until_sec=3.0, boosted_code=None)
        sim_s2 = simulate_race("s2-check", "single_ring", race_codes, 30, 1.0, sim_until_sec=3.0, boosted_code="IN")

        self.assertEqual(sim_s1.frames[-1]["marbles"], sim_s1_repeat.frames[-1]["marbles"])
        self.assertNotEqual(sim_s1.frames[-1]["marbles"]["IN"], sim_s2.frames[-1]["marbles"]["IN"])

        after_league = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
        self.assertEqual(before_league, after_league)

    def test_seasonal_themes_distinct_and_s1_classic(self) -> None:
        import copy
        from PIL import Image, ImageDraw
        from common.render import apply_standard_hud
        from common.season_theme import (
            get_season_theme,
            render_radial_backdrop,
            render_seasonal_arena_backdrop,
            strip_flag_emojis,
        )
        from generators.marble_league import (
            _draw_champion_card,
            _draw_race_results_card,
            _draw_standings_card,
        )

        before_league = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None

        # Season 1 must remain strictly classic so running Season 1 visuals never change mid-season
        t1 = get_season_theme(1)
        self.assertTrue(t1.is_classic)
        self.assertEqual(t1.edition_key, "classic")

        # Seasons 2..6 must produce 5 distinct Championship Editions
        editions_2_to_6 = [get_season_theme(s) for s in range(2, 7)]
        self.assertTrue(all(not t.is_classic for t in editions_2_to_6))
        self.assertEqual(len({t.edition_key for t in editions_2_to_6}), 5)
        self.assertEqual(
            [t.edition_key for t in editions_2_to_6],
            ["solar_gold", "cyber_emerald", "arctic_hyperdrive", "royal_synthwave", "crimson_inferno"],
        )

        # Season 7 cycles cleanly back to solar_gold with season=7
        t7 = get_season_theme(7)
        self.assertFalse(t7.is_classic)
        self.assertEqual(t7.edition_key, "solar_gold")
        self.assertEqual(t7.season, 7)

        # Flag emoji stripper prevents BebasNeue tofu boxes
        self.assertEqual(strip_flag_emojis("🇮🇳 India"), "India")
        self.assertEqual(strip_flag_emojis("CROWD BOOST: 🇵🇭 Philippines"), "CROWD BOOST: Philippines")

        # Render escape + line backdrops, 3-pass neon ArenaKit, and all 3 broadcast cards without errors
        t2 = editions_2_to_6[0]
        t3 = editions_2_to_6[1]
        escape_bg = render_seasonal_arena_backdrop(t2, "escape", (540, 860), 1340)
        line_bg = render_seasonal_arena_backdrop(t3, "line", (540, 860), 1340)
        self.assertEqual(escape_bg.size, (1080, 1920))
        self.assertEqual(line_bg.size, (1080, 1920))

        codes = list(LEAGUE_COUNTRY_CODES[:10])
        sim = simulate_race("s2-theme", "double_ring", codes, 30, 1.0, sim_until_sec=0.5)
        sim.arena.kit.render_static(escape_bg, neon_glow=True, glow_bg=t2.bg_center, color_map=t2.obstacle_remap)
        sim.arena.kit.render_dynamic(
            ImageDraw.Draw(escape_bg),
            sim.frames[-1]["kin"],
            neon_glow=True,
            glow_bg=t2.bg_center,
            color_map=t2.obstacle_remap,
        )

        state = copy.deepcopy(load_league_state())
        standings = compute_standings(state)
        card_bg = render_radial_backdrop(t2)
        res = _draw_race_results_card(card_bg.copy(), codes, {c: 5.0 for c in codes[:5]}, 4, "Double Ring", "ESCAPED", 1, theme=t2)
        std = _draw_standings_card(card_bg.copy(), "IN", standings, 2, 16, "Knockout Stage", "Tomorrow!", "CA", theme=t2)
        champ = _draw_champion_card(card_bg.copy(), standings, 2, 1.0, theme=t2)
        apply_standard_hud(
            res,
            t_sec=19.5,
            total_duration=26.0,
            series_badge="SEASON 2 · SOLAR GOLD · R4",
            hook_title="WHO ESCAPES FIRST?",
            subtitle_line="Seed: 20261210-B",
            crowd_boost_banner="CROWD BOOST (+5%): India • 9 comments (@fan)",
            theme=t2,
            banner_flag_code="IN",
        )
        self.assertEqual(std.size, (1080, 1920))
        self.assertEqual(champ.size, (1080, 1920))

        after_league = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
        self.assertEqual(before_league, after_league)


if __name__ == "__main__":
    unittest.main()

