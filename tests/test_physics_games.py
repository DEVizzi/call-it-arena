"""
Call It Arena — Physics games test suite (`tests/test_physics_games.py`).

Physics-only checks (no video rendering, no state writes), safe to run during a live league season.
"""

import copy
import json

import pytest
from PIL import Image

from common.physics_kit import TimeRemap
from generators.marble_arenas import ARENA_VARIANTS
from generators.marble_league import (
    ARENA_NO_REPEAT_WINDOW,
    LEAGUE_COUNTRY_CODES,
    LEAGUE_STATE_FILE,
    _draw_race_results_card,
    _draw_standings_card,
    choose_arena_variant,
    compute_standings,
    load_league_state,
    run_tuned_race,
    simulate_race,
)


def test_league_has_at_least_ten_distinct_arenas():
    assert len(ARENA_VARIANTS) >= 10
    assert len(set(ARENA_VARIANTS)) == len(ARENA_VARIANTS)
    # the original four arenas must still exist (history references them)
    for legacy in ("single_ring", "double_ring", "spiral_labyrinth", "funnel_obstacles"):
        assert legacy in ARENA_VARIANTS


@pytest.mark.parametrize("variant", ARENA_VARIANTS)
def test_every_arena_builds_and_simulates(variant):
    codes = LEAGUE_COUNTRY_CODES[:12]
    sim = simulate_race("test-seed", variant, codes, 30, 1.0, sim_until_sec=3.0)
    assert len(sim.frames) == 90
    assert set(sim.frames[-1]["marbles"].keys()) == set(codes)
    for x, y, _a in sim.frames[-1]["marbles"].values():
        assert x == x and y == y  # no NaNs


@pytest.mark.parametrize("variant", ARENA_VARIANTS)
def test_every_arena_produces_finishers_after_tuning(variant):
    codes = LEAGUE_COUNTRY_CODES[:10]
    sim = run_tuned_race("20261120-B", variant, codes, 30)
    assert len(sim.finish_order) >= 1, f"{variant} produced no finishers"
    assert len(set(sim.finish_order)) == len(sim.finish_order)


def test_simulation_is_deterministic():
    codes = LEAGUE_COUNTRY_CODES[:10]
    a = simulate_race("det-seed", "plinko_drop", codes, 30, 1.0)
    b = simulate_race("det-seed", "plinko_drop", codes, 30, 1.0)
    assert a.finish_order == b.finish_order


def test_arena_rotation_never_repeats_within_window():
    history = []
    for day in range(1, 41):
        seed = f"202612{day:02d}-B"
        v = choose_arena_variant(seed, history)
        recent = [h["variant"] for h in history[-ARENA_NO_REPEAT_WINDOW:]]
        assert v not in recent, f"{v} repeated within {ARENA_NO_REPEAT_WINDOW} races"
        history.append({"variant": v})
    assert len({h["variant"] for h in history}) == len(ARENA_VARIANTS)


def test_reveal_cards_render_without_touching_state(tmp_path):
    before = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
    state = copy.deepcopy(load_league_state())
    order = list(LEAGUE_COUNTRY_CODES[:12])
    times = {c: 3.0 + i for i, c in enumerate(order[:7])}
    frame = Image.new("RGB", (1080, 1920))
    _draw_race_results_card(frame, order, times, 7, "Plinko Drop", "FINISHED")
    _draw_standings_card(frame, order[0], compute_standings(state), 1, 7, "Group Stage", "Can Brazil survive?", None)
    after = LEAGUE_STATE_FILE.read_text(encoding="utf-8") if LEAGUE_STATE_FILE.exists() else None
    assert before == after


def test_time_remap_places_decisive_moment_after_reveal():
    r = TimeRemap(decisive_sim_t=14.0, intro=2.0, countdown=15.5, reveal=18.5, slowmo=0.2, post=0.6)
    assert r(0.0) == 0.0 and r(1.9) == 0.0
    assert abs(r.inverse(14.0) - 19.1) < 1e-6  # decisive moment lands 0.6s after the reveal
    for vt in (2.5, 10.0, 16.0, 19.0, 22.0):
        assert abs(r.inverse(r(vt)) - vt) < 1e-6


def test_bucket_drop_physics():
    from generators.bucket_drop import N_BUCKETS, run_tuned_drop

    sim = run_tuned_drop("20261118-A")
    assert sim["landed_t"] is not None
    assert 1 <= sim["final_bucket"] <= N_BUCKETS


def test_tower_quake_physics_has_last_standing_tower():
    from generators.tower_quake import pick_designs, run_tuned_quake

    designs = pick_designs("20261118-A")
    assert len(set(designs)) == 3
    sim = run_tuned_quake("20261118-A", designs)
    falls = sim["falls"]
    assert len(falls) >= 2
    assert len({ti for _, ti in falls[:2]}) == 2
    survivors = {0, 1, 2} - {ti for _, ti in falls[:2]}
    assert len(survivors) == 1  # exactly one "last tower standing" once two have fallen


def test_downhill_derby_physics():
    from generators.downhill_derby import build_terrain, pick_cars, run_tuned_derby

    cars = pick_cars("20261118-A")
    assert len(set(cars)) == 4
    sim = run_tuned_derby("20261118-A", cars, build_terrain("20261118-A"))
    assert sim["order"], "no car finished"
    assert sorted(sim["full_order"]) == [0, 1, 2, 3]


def test_new_formats_registered_everywhere():
    import yaml

    import main
    from common.seo import FORMAT_IG_HASHTAG_POOLS, FORMAT_MAIN_KEYWORDS, FORMAT_YT_TAGS, build_title_candidates

    sched = yaml.safe_load(open(main.SCHEDULE_FILE, encoding="utf-8"))
    for fmt in ("bucket_drop", "tower_quake", "downhill_derby"):
        assert fmt in main.ROTATING_GUESS_FORMATS
        assert fmt in sched["format_weights"] and fmt in sched["playlists"]
        assert fmt in FORMAT_MAIN_KEYWORDS and fmt in FORMAT_YT_TAGS and fmt in FORMAT_IG_HASHTAG_POOLS
        titles = build_title_candidates(fmt, {})
        assert len(titles) == 5 and all(len(t) <= 70 for _, t in titles)
