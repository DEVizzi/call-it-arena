"""
Call It Arena — SEO, Virality Scorer, Anti-Repeat Title Engine & 4-Language Localization (`common/seo.py`).

Uses Gemini API free tier (`GEMINI_API_KEY` or `GOOGLE_API_KEY`) with deterministic template fallbacks.
Enforces:
- YouTube Title <= 70 chars, curiosity hook first, emoji + main keyword.
- Generates 5 title candidates, scores each on (hook word, keyword, emoji, length),
  and NEVER repeats the same title pattern 3 posts in a row.
- YouTube Description: 2-line hook, comment question, seed (`Race seed: YYYYMMDD-X`),
  season standings/playlist link, and exactly 3 hashtags (`#shorts` + 2 niche tags).
- 10–15 relevant tags per format.
- Localizations in Hindi (`hi`), Portuguese (`pt`), Spanish (`es`), and Indonesian (`id`).
- Instagram Reels Caption: platform-native hook + question + line breaks + max 5 rotated hashtags.
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("call_it_arena")

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)

CURIOSITY_HOOK_WORDS = {
    "can", "who", "which", "will", "wait", "impossible", "only", "nobody",
    "watch", "guess", "survive", "escape", "beat", "shocking", "final",
}

FORMAT_MAIN_KEYWORDS: Dict[str, str] = {
    "marble_league": "Marble Race",
    "flag_quiz": "Flag Quiz",
    "data_guess": "Data Race",
    "space_guess": "Space Quiz",
    "sorting_race": "Sorting Race",
    "guess_the_year": "History Quiz",
}

FORMAT_YT_NICHE_HASHTAGS: Dict[str, Tuple[str, str]] = {
    "marble_league": ("#marblerace", "#countryballs"),
    "flag_quiz": ("#guesstheflag", "#geographyquiz"),
    "data_guess": ("#datavisualization", "#worldstats"),
    "space_guess": ("#spacequiz", "#astronomy"),
    "sorting_race": ("#sortingalgorithms", "#coding"),
    "guess_the_year": ("#historyquiz", "#guesstheyear"),
}

FORMAT_YT_TAGS: Dict[str, List[str]] = {
    "marble_league": [
        "marble race", "country marble race", "marble league", "countryballs",
        "physics simulation", "satisfying", "elimination race", "call it arena",
        "india marble race", "usa vs india", "algodoo style", "pymunk physics",
    ],
    "flag_quiz": [
        "guess the flag", "flag quiz", "geography quiz", "world flags",
        "country quiz", "trivia shorts", "blurry flag quiz", "call it arena",
        "hard flag quiz", "flag challenge", "educational shorts", "brain teaser",
    ],
    "data_guess": [
        "bar chart race", "data visualization", "country comparison", "world ranking",
        "guess the country", "global statistics", "our world in data", "world bank data",
        "animated stats", "call it arena", "geography trivia", "satisfying data",
    ],
    "space_guess": [
        "space quiz", "nasa apod", "galaxy or nebula", "astronomy trivia",
        "deep space", "hubble telescope", "james webb", "universe quiz",
        "cosmic mystery", "call it arena", "science shorts", "planet quiz",
    ],
    "sorting_race": [
        "sorting algorithms", "sorting race", "quicksort vs mergesort", "algorithm visualization",
        "computer science", "satisfying audio", "coding shorts", "heapsort",
        "radix sort", "data structures", "call it arena", "tech trivia",
    ],
    "guess_the_year": [
        "guess the year", "history quiz", "on this day", "historical trivia",
        "world history", "time travel quiz", "vintage history", "call it arena",
        "history challenge", "trivia game", "educational quiz", "what year",
    ],
}

FORMAT_IG_HASHTAG_POOLS: Dict[str, List[str]] = {
    "marble_league": [
        "#marblerace", "#countryballs", "#physicssimulation", "#satisfyingvideos",
        "#indiareels", "#marblerun", "#callitarena", "#eliminationgame",
    ],
    "flag_quiz": [
        "#flagquiz", "#guesstheflag", "#geographyquiz", "#triviareels",
        "#worldflags", "#countryquiz", "#callitarena", "#brainteaser",
    ],
    "data_guess": [
        "#datavisualization", "#barchartrace", "#worldstats", "#countrycomparison",
        "#geographyfacts", "#globalranking", "#callitarena", "#dataisbeautiful",
    ],
    "space_guess": [
        "#spacequiz", "#nasaapod", "#astronomyreels", "#cosmos",
        "#deepspace", "#universefacts", "#callitarena", "#astrophotography",
    ],
    "sorting_race": [
        "#sortingalgorithms", "#codingreels", "#computerscience", "#algorithmvisualization",
        "#satisfyingaudio", "#programmerhumor", "#callitarena", "#techreels",
    ],
    "guess_the_year": [
        "#historyquiz", "#guesstheyear", "#onthisday", "#historyfacts",
        "#triviachallenge", "#worldhistory", "#callitarena", "#quizreels",
    ],
}


def redact_secrets(text: str) -> str:
    if not text:
        return ""
    out = text
    for env_key in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "IG_ACCESS_TOKEN", "YT_CLIENT_SECRET",
        "YT_REFRESH_TOKEN", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN", "NASA_API_KEY",
    ):
        val = (os.getenv(env_key) or "").strip()
        if val and len(val) > 5:
            out = out.replace(val, "***REDACTED***")
    return out


def get_gemini_api_key() -> str:
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()


def score_title_candidate(title: str, main_keyword: str) -> float:
    """
    Scores a YouTube title candidate according to virality & retention rules:
    - Hard limit: > 70 chars gets penalized heavily / truncated.
    - Curiosity hook word in first 3 words (+30 pts)
    - Main keyword included (+25 pts)
    - Contains emoji (+15 pts)
    - Optimal mobile Shorts length 38–66 chars (+20 pts)
    - Question mark or exclamation (+10 pts)
    """
    clean = title.strip()
    if not clean or len(clean) > 70:
        return -100.0

    score = 0.0
    words = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in clean.split()]
    if any(w in CURIOSITY_HOOK_WORDS for w in words[:3]):
        score += 30.0
    elif any(w in CURIOSITY_HOOK_WORDS for w in words):
        score += 15.0

    if main_keyword.lower() in clean.lower():
        score += 25.0

    if any(ord(ch) > 0x1F000 for ch in clean):
        score += 15.0

    if 38 <= len(clean) <= 66:
        score += 20.0
    elif 28 <= len(clean) < 38 or 67 <= len(clean) <= 70:
        score += 10.0

    if "?" in clean or "!" in clean:
        score += 10.0

    return score


def build_title_candidates(
    format_name: str,
    context: Dict[str, Any],
) -> List[Tuple[str, str]]:
    """
    Returns 5 distinct `(pattern_id, title_text)` candidates, each guaranteed <= 70 chars.
    Each candidate has a distinct `pattern_id` (`p1`..`p5`) so the anti-repeat rule never allows
    the same pattern 3 posts in a row.
    """
    kw = FORMAT_MAIN_KEYWORDS.get(format_name, "Quiz")
    series_tag = str(context.get("series_short", "")).strip()
    focus_country = str(context.get("focus_country", "India")).strip()
    focus_emoji = str(context.get("focus_emoji", "🇮🇳")).strip()
    rival_country = str(context.get("rival_country", "Brazil")).strip()
    rival_emoji = str(context.get("rival_emoji", "🇧🇷")).strip()
    topic_short = str(context.get("topic_short", "This Round")).strip()

    if format_name == "marble_league":
        s_tag = series_tag or "S1·R1"
        raw = [
            ("pattern_escape", f"Can {focus_country} Escape First? {focus_emoji} {kw} {s_tag}"),
            ("pattern_vs", f"Who Survives: {focus_country} {focus_emoji} vs {rival_country} {rival_emoji}? {kw} {s_tag}"),
            ("pattern_lastsec", f"Wait For The Final Bounce! 😱 {focus_emoji} {kw} {s_tag}"),
            ("pattern_nobody", f"Nobody Guessed This Winner! 🏆 {kw} {s_tag}"),
            ("pattern_pick", f"Which Country Wins {s_tag}? {focus_emoji}{rival_emoji} {kw}"),
        ]
    elif format_name == "flag_quiz":
        raw = [
            ("pattern_99fail", f"Can You Name This Flag Before 1s? 🏳️ {kw}"),
            ("pattern_blur", f"Only 3% Spot This Blurry Flag! 🔍 {kw}"),
            ("pattern_speed", f"How Fast Can You Guess This Flag? ⚡ {kw}"),
            ("pattern_lock", f"Lock Your Guess Before Unblur! 🎯 {kw}"),
            ("pattern_geo", f"Which Country Owns This Flag? 🌍 {kw}"),
        ]
    elif format_name == "data_guess":
        raw = [
            ("pattern_lead", f"Which Country Leads in {topic_short}? 📊 {kw}"),
            ("pattern_overtake", f"Wait For The #1 Overtake! 📈 {topic_short} {kw}"),
            ("pattern_can_in", f"Can {focus_country} {focus_emoji} Reach #1 in {topic_short}? {kw}"),
            ("pattern_shock", f"Nobody Expects #1 in {topic_short}! 🌍 {kw}"),
            ("pattern_callit", f"Who Tops The World in {topic_short}? 🏆 {kw}"),
        ]
    elif format_name == "space_guess":
        raw = [
            ("pattern_cosmic", f"Galaxy, Nebula or Planet? 🔭 NASA {kw}"),
            ("pattern_zoom", f"Can You Call It Before Full Zoom? 🌌 {kw}"),
            ("pattern_nasa", f"What Did NASA Capture Here? 🚀 Deep {kw}"),
            ("pattern_99space", f"Only Space Nerds Get This Right! 🪐 {kw}"),
            ("pattern_mystery", f"Wait For The Cosmic Reveal! ✨ NASA {kw}"),
        ]
    elif format_name == "sorting_race":
        algo_a = str(context.get("algo_a", "QuickSort"))
        algo_b = str(context.get("algo_b", "MergeSort"))
        raw = [
            ("pattern_algo_vs", f"Which Finishes First: {algo_a} vs {algo_b}? ⚡ {kw}"),
            ("pattern_algo_win", f"Can {algo_a} Beat {algo_b}? 💻 {kw}"),
            ("pattern_algo_sound", f"Wait For The Final Sweep! 🔊 {algo_a} {kw}"),
            ("pattern_algo_pick", f"Call The Winner: {algo_a} or {algo_b}? 🏆 {kw}"),
            ("pattern_algo_speed", f"Which Algorithm Sorts Fastest? 🚀 {kw}"),
        ]
    else:  # guess_the_year
        raw = [
            ("pattern_year_call", f"Can You Guess The Exact Year? ⏳ {kw}"),
            ("pattern_year_3clues", f"3 Clues, 1 Year: When Did This Happen? 📜 {kw}"),
            ("pattern_year_lock", f"Only History Buffs Call This Year! 🏛️ {kw}"),
            ("pattern_year_wait", f"Which Year Changed History Here? 🌍 {kw}"),
            ("pattern_year_onthisday", f"On This Day: Can You Call The Year? 🎯 {kw}"),
        ]

    # Enforce <= 70 char limit strictly
    trimmed: List[Tuple[str, str]] = []
    for pid, text in raw:
        t = text.strip()
        if len(t) > 70:
            t = t[:69].rstrip()
        trimmed.append((pid, t))
    return trimmed


def select_best_non_repeating_title(
    candidates: List[Tuple[str, str]],
    main_keyword: str,
    recent_patterns: List[str],
) -> Tuple[str, str]:
    """
    Scores all 5 candidates and selects the highest-scoring title that does NOT
    repeat the last 2 title patterns (`recent_patterns[-2:]`), guaranteeing the same
    pattern is never used 3 posts in a row (and ideally not even twice in a row).
    """
    forbidden_streak = None
    if len(recent_patterns) >= 2 and recent_patterns[-1] == recent_patterns[-2]:
        forbidden_streak = recent_patterns[-1]
    last_pattern = recent_patterns[-1] if recent_patterns else None

    scored: List[Tuple[float, str, str]] = []
    for pattern_id, title in candidates:
        if pattern_id == forbidden_streak:
            continue
        base_score = score_title_candidate(title, main_keyword)
        # Small bonus for rotating pattern every single run
        if pattern_id != last_pattern:
            base_score += 5.0
        scored.append((base_score, pattern_id, title))

    if not scored:
        pattern_id, title = candidates[0]
        return pattern_id, title[:70]

    scored.sort(key=lambda x: x[0], reverse=True)
    _, best_pid, best_title = scored[0]
    return best_pid, best_title[:70]


def build_localizations(
    format_name: str,
    seed: str,
    context: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    """
    Builds localized title (<= 70 chars) and description in Hindi (`hi`),
    Portuguese (`pt`), Spanish (`es`), and Indonesian (`id`) for the YouTube `localizations` field.
    """
    s_tag = str(context.get("series_short", "S1")).strip()
    focus_emoji = str(context.get("focus_emoji", "🇮🇳")).strip()

    if format_name == "marble_league":
        return {
            "hi": {
                "title": f"कौन सा देश पहले बाहर निकलेगा? {focus_emoji} मार्बल रेस {s_tag}"[:70],
                "description": (
                    f"रिजल्ट से पहले अपना विजेता कमेंट करें! 👇\n"
                    f"क्या आपका देश इस राउंड में जीत पाएगा?\n"
                    f"Race seed: {seed}\n#shorts #marblerace #countryballs"
                ),
            },
            "pt": {
                "title": f"Qual País Escapa Primeiro? 🇧🇷 Corrida de Bolinhas {s_tag}"[:70],
                "description": (
                    f"Comente seu palpite antes da revelação! 👇\n"
                    f"Será que o Brasil vence esta rodada?\n"
                    f"Race seed: {seed}\n#shorts #marblerace #countryballs"
                ),
            },
            "es": {
                "title": f"¿Qué País Escapa Primero? 🇲🇽 Carrera de Canicas {s_tag}"[:70],
                "description": (
                    f"¡Deja tu predicción antes del final! 👇\n"
                    f"¿Podrá tu país clasificar hoy?\n"
                    f"Race seed: {seed}\n#shorts #marblerace #countryballs"
                ),
            },
            "id": {
                "title": f"Negara Mana Lolos Duluan? 🇮🇩 Balap Kelereng {s_tag}"[:70],
                "description": (
                    f"Tulis tebakanmu di kolom komentar sebelum reveal! 👇\n"
                    f"Bisakah Indonesia menang di babak ini?\n"
                    f"Race seed: {seed}\n#shorts #marblerace #countryballs"
                ),
            },
        }

    return {
        "hi": {
            "title": f"रिवील से पहले सही जवाब बताएं! 🎯 Call It Arena {s_tag}"[:70],
            "description": f"काउंटडाउन खत्म होने से पहले अपना जवाब लॉक करें! 👇\nRace seed: {seed}\n#shorts #quiz #callitarena",
        },
        "pt": {
            "title": f"Acerte Antes da Revelação! 🎯 Desafio Call It Arena"[:70],
            "description": f"Comente sua resposta antes do fim da contagem! 👇\nRace seed: {seed}\n#shorts #quiz #callitarena",
        },
        "es": {
            "title": f"¡Adivina Antes de la Revelación! 🎯 Call It Arena"[:70],
            "description": f"¡Comenta tu respuesta antes de que termine la cuenta regresiva! 👇\nRace seed: {seed}\n#shorts #quiz #callitarena",
        },
        "id": {
            "title": f"Tebak Sebelum Reveal! 🎯 Tantangan Call It Arena"[:70],
            "description": f"Kunci jawabanmu di komentar sebelum hitungan mundur habis! 👇\nRace seed: {seed}\n#shorts #quiz #callitarena",
        },
    }


def _try_gemini_seo_enrichment(
    format_name: str,
    seed: str,
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Optionally queries Gemini free tier (`GEMINI_API_KEY`) for fresh hook lines and candidate titles.
    Strictly validates lengths and falls back cleanly on any network/quota error.
    """
    api_key = get_gemini_api_key()
    if not api_key:
        return None

    kw = FORMAT_MAIN_KEYWORDS.get(format_name, "Quiz")
    prompt = (
        "You are a viral YouTube Shorts & Instagram Reels strategist for the channel 'Call It Arena' "
        "(tagline: 'Call it before the reveal. 👇').\n"
        f"Format: {format_name} | Main keyword: {kw} | Seed: {seed}\n"
        f"Context JSON: {json.dumps(context, ensure_ascii=False, default=str)}\n"
        "Return ONLY valid JSON with keys:\n"
        "- 'titles': list of 5 curiosity-hook titles, each <= 68 chars, containing an emoji and the main keyword.\n"
        "- 'yt_hook_lines': 2-line engaging hook (do NOT reveal the winner).\n"
        "- 'ig_hook': 1 punchy Instagram Reels hook line + 1 question for comments.\n"
        "- 'first_comment': 1 engaging question to post as the channel's first comment.\n"
    )
    try:
        resp = requests.post(
            GEMINI_API_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"},
            },
            timeout=15,
        )
        if not resp.ok:
            return None
        payload = resp.json()
        text = (
            payload.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        return json.loads(text)
    except Exception as e:
        logger.debug("Gemini SEO fallback activated: %s", redact_secrets(str(e)))
        return None


def generate_seo_package(
    format_name: str,
    seed: str,
    context: Dict[str, Any],
    recent_patterns: Optional[List[str]] = None,
    playlist_url: str = "https://www.youtube.com/@callitarena/playlists",
    category_id: str = "24",
    allow_network: bool = True,
) -> Dict[str, Any]:
    """
    Builds the complete, validated YouTube Shorts + Instagram Reels SEO & localization bundle.
    """
    recent = list(recent_patterns or [])
    kw = FORMAT_MAIN_KEYWORDS.get(format_name, "Quiz")
    candidates = build_title_candidates(format_name, context)

    gemini_data = _try_gemini_seo_enrichment(format_name, seed, context) if allow_network else None
    if gemini_data and isinstance(gemini_data.get("titles"), list):
        for idx, t in enumerate(gemini_data["titles"][:3]):
            t_str = str(t).strip()
            if 20 <= len(t_str) <= 70 and kw.lower() in t_str.lower():
                candidates.append((f"gemini_p{idx + 1}", t_str))

    chosen_pattern, yt_title = select_best_non_repeating_title(candidates, kw, recent)

    # YouTube Description: 2-line hook, question, race seed, season standings link, 3 hashtags (#shorts + 2 niche)
    niche_tag_1, niche_tag_2 = FORMAT_YT_NICHE_HASHTAGS.get(format_name, ("#quiz", "#callitarena"))
    hook_2lines = (
        str(gemini_data.get("yt_hook_lines", "")).strip()
        if gemini_data and gemini_data.get("yt_hook_lines")
        else (
            f"Call it before the reveal! 👇 Every bounce is 100% physics-driven.\n"
            f"Lock your pick before the 3-2-1 countdown hits zero!"
        )
    )
    comment_question = str(
        context.get(
            "comment_question",
            "Which country or pick are you backing for the next battle? Drop it below! 👇",
        )
    )
    yt_description = (
        f"{hook_2lines}\n\n"
        f"💬 {comment_question}\n\n"
        f"🎲 Race seed: {seed}\n"
        f"🏆 Season Standings & Playlist: {playlist_url}\n\n"
        f"#shorts {niche_tag_1} {niche_tag_2}"
    )

    # 10-15 YouTube tags
    yt_tags = list(FORMAT_YT_TAGS.get(format_name, FORMAT_YT_TAGS["marble_league"]))[:14]

    # Instagram Caption: platform-specific rewrite, max 5 rotated hashtags
    pool = FORMAT_IG_HASHTAG_POOLS.get(format_name, FORMAT_IG_HASHTAG_POOLS["marble_league"])
    seed_hash = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    rotated_ig_tags = [pool[(seed_hash + i) % len(pool)] for i in range(5)]
    ig_hook = (
        str(gemini_data.get("ig_hook", "")).strip()
        if gemini_data and gemini_data.get("ig_hook")
        else f"Call it before the reveal. 👇 Can you lock in the winner before the 3-2-1 countdown?"
    )
    ig_caption = (
        f"{ig_hook}\n\n"
        f"{comment_question}\n"
        f"Seed: {seed} • Follow @callitarena for 2x daily arena battles!\n\n"
        f"{' '.join(rotated_ig_tags)}"
    )

    # First comment from channel
    first_comment = (
        str(gemini_data.get("first_comment", "")).strip()
        if gemini_data and gemini_data.get("first_comment")
        else comment_question
    )

    reveal_sec = float(context.get("reveal_timestamp_sec", 19.0))
    thumb_offset_ms = max(0, int(round((reveal_sec - 0.5) * 1000)))

    localizations = build_localizations(format_name, seed, context)

    return {
        "pattern_id": chosen_pattern,
        "youtube": {
            "title": yt_title,
            "description": yt_description,
            "tags": yt_tags,
            "categoryId": str(category_id),
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": False,
            "localizations": localizations,
        },
        "instagram": {
            "caption": ig_caption,
            "share_to_feed": True,
            "thumb_offset_ms": thumb_offset_ms,
            "hashtags": rotated_ig_tags,
        },
        "first_comment": first_comment,
    }
