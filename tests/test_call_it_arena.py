"""
Call It Arena — Automated Test Suite (`tests/test_call_it_arena.py`).
"""

from pathlib import Path
import pytest

from common.analytics import run_weekly_analytics_and_rebalance
from common.audio import ensure_audio_assets
from common.branding import (
    BASE_DIR,
    COUNTRIES_META,
    ensure_flag_assets,
    generate_channel_branding_assets,
    resolve_safe_path,
    sanitize_username,
)
from common.comments import extract_country_from_text_regex
from common.seo import (
    build_title_candidates,
    generate_seo_package,
    select_best_non_repeating_title,
)
from generators.guess_the_year import is_valid_commons_license
from generators.marble_league import compute_standings, load_league_state
from generators.space_guess import is_valid_apod_entry


def test_path_traversal_blocked():
    with pytest.raises(ValueError):
        resolve_safe_path(BASE_DIR / "output", "../../etc/passwd")


def test_sanitize_username():
    assert sanitize_username("  @india_fan<script>  ") == "@india_fanscript"
    assert sanitize_username("") == "@arena_fan"


def test_flags_and_branding_assets_generated():
    flags = ensure_flag_assets()
    assert len(flags) >= 16
    assert (BASE_DIR / "assets" / "flags" / "in.png").exists()

    ensure_audio_assets()
    assert (BASE_DIR / "assets" / "sfx" / "boing.wav").exists()
    assert (BASE_DIR / "assets" / "music" / "arena_pulse_cc0.wav").exists()

    pfp, banner = generate_channel_branding_assets()
    assert pfp.exists() and pfp.stat().st_size > 5000
    assert banner.exists() and banner.stat().st_size > 10000


def test_seo_title_length_and_anti_repeat():
    ctx = {
        "series_short": "S1·R14",
        "focus_country": "India",
        "focus_emoji": "🇮🇳",
        "rival_country": "Brazil",
        "rival_emoji": "🇧🇷",
    }
    candidates = build_title_candidates("marble_league", ctx)
    assert len(candidates) == 5
    for _, title in candidates:
        assert len(title) <= 70

    # If a pattern was used twice in a row, the 3rd post MUST NOT repeat it
    first_pid = candidates[0][0]
    chosen_pid, chosen_title = select_best_non_repeating_title(
        candidates, "Marble Race", [first_pid, first_pid]
    )
    assert chosen_pid != first_pid
    assert len(chosen_title) <= 70


def test_seo_package_localizations_and_ig_hashtags():
    pkg = generate_seo_package(
        format_name="marble_league",
        seed="20261115-B",
        context={"series_short": "S1·R1", "reveal_timestamp_sec": 18.5},
        recent_patterns=[],
        allow_network=False,
    )
    yt = pkg["youtube"]
    ig = pkg["instagram"]
    assert len(yt["title"]) <= 70
    assert "Race seed: 20261115-B" in yt["description"]
    assert set(yt["localizations"].keys()) == {"hi", "pt", "es", "id"}
    assert yt["selfDeclaredMadeForKids"] is False
    assert yt["containsSyntheticMedia"] is False
    assert len(ig["hashtags"]) <= 5
    assert ig["thumb_offset_ms"] == 18000


def test_nasa_apod_filter_skips_video_and_copyright():
    assert is_valid_apod_entry({"media_type": "video", "url": "https://youtube.com/watch?v=123"}) is False
    assert is_valid_apod_entry({"media_type": "image", "copyright": "John Doe", "url": "https://apod.nasa.gov/a.jpg"}) is False
    assert is_valid_apod_entry({"media_type": "image", "url": "https://apod.nasa.gov/public.jpg"}) is True


def test_wikimedia_commons_license_filter():
    assert is_valid_commons_license("Public domain") is True
    assert is_valid_commons_license("CC0") is True
    assert is_valid_commons_license("CC BY-SA 4.0") is True
    assert is_valid_commons_license("Fair use") is False
    assert is_valid_commons_license("Copyrighted free use") is False


def test_comment_country_extractor():
    assert extract_country_from_text_regex("Please add 🇮🇳 in the next race!") == "IN"
    assert extract_country_from_text_regex("Can Brazil win tomorrow?") == "BR"
    assert extract_country_from_text_regex("Let's go Pinoy pride!") == "PH"


def test_league_standings_sorting(tmp_path):
    # Use a fresh default season (never the live state/league.json, whose points change daily)
    state = load_league_state(tmp_path / "fresh_league.json")
    state["countries"]["IN"]["points"] = 25
    state["countries"]["BR"]["points"] = 18
    standings = compute_standings(state)
    assert standings[0]["code"] == "IN"
    assert standings[1]["code"] == "BR"
