"""
Call It Arena — Format 5: Side-by-Side Algorithm Sorting Race (`generators/sorting_race.py`).

Features:
- Compares two classic sorting algorithms head-to-head on an identical scrambled dataset
  (e.g., QuickSort vs MergeSort, HeapSort vs ShellSort, RadixSort vs QuickSort, MergeSort vs CombSort).
- Renders dual stacked arenas using headless Pygame (`SDL_VIDEODRIVER=dummy`) / PIL with
  highlighted active comparison bars and pitched `tick` SFX synced to sorting operations.
- Freezes at the 3-2-1 "Lock in your guess!" countdown right before the photo-finish sweep,
  then reveals the winning algorithm + operation count in the final ~5s.
"""

import hashlib
import os
import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
from PIL import Image, ImageDraw

from common.audio import SfxEvent, mix_master_audio
from common.branding import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_COLOR,
    OUTPUT_DIR,
    PANEL_BG_COLOR,
    PANEL_BORDER_COLOR,
    TEXT_MUTED,
    TEXT_WHITE,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    get_font,
)
from common.render import (
    FFmpegFramePipe,
    add_countdown_and_reveal_sfx,
    apply_standard_hud,
    draw_centered_text,
)
from common.tts import build_standard_voice_cues


def _seed_to_int(seed_str: str) -> int:
    return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:12], 16)


def _record_quicksort(arr: List[int]) -> List[Tuple[List[int], int, int]]:
    a = list(arr)
    steps: List[Tuple[List[int], int, int]] = []

    def _qs(lo: int, hi: int) -> None:
        if lo >= hi:
            return
        pivot = a[(lo + hi) // 2]
        i, j = lo, hi
        while i <= j:
            while a[i] < pivot:
                steps.append((list(a), i, hi))
                i += 1
            while a[j] > pivot:
                steps.append((list(a), j, lo))
                j -= 1
            if i <= j:
                a[i], a[j] = a[j], a[i]
                steps.append((list(a), i, j))
                i += 1
                j -= 1
        _qs(lo, j)
        _qs(i, hi)

    _qs(0, len(a) - 1)
    steps.append((list(a), -1, -1))
    return steps


def _record_mergesort(arr: List[int]) -> List[Tuple[List[int], int, int]]:
    a = list(arr)
    steps: List[Tuple[List[int], int, int]] = []

    def _ms(lo: int, hi: int) -> None:
        if hi - lo <= 1:
            return
        mid = (lo + hi) // 2
        _ms(lo, mid)
        _ms(mid, hi)
        merged: List[int] = []
        i, j = lo, mid
        while i < mid and j < hi:
            steps.append((list(a), i, j))
            if a[i] <= a[j]:
                merged.append(a[i])
                i += 1
            else:
                merged.append(a[j])
                j += 1
        while i < mid:
            merged.append(a[i])
            i += 1
        while j < hi:
            merged.append(a[j])
            j += 1
        for k, val in enumerate(merged):
            a[lo + k] = val
            steps.append((list(a), lo + k, mid))

    _ms(0, len(a))
    steps.append((list(a), -1, -1))
    return steps


def _record_heapsort(arr: List[int]) -> List[Tuple[List[int], int, int]]:
    a = list(arr)
    steps: List[Tuple[List[int], int, int]] = []
    n = len(a)

    def _sift_down(start: int, end: int) -> None:
        root = start
        while root * 2 + 1 <= end:
            child = root * 2 + 1
            swap = root
            steps.append((list(a), root, child))
            if a[swap] < a[child]:
                swap = child
            if child + 1 <= end and a[swap] < a[child + 1]:
                swap = child + 1
            if swap == root:
                return
            a[root], a[swap] = a[swap], a[root]
            steps.append((list(a), root, swap))
            root = swap

    for start in range((n - 2) // 2, -1, -1):
        _sift_down(start, n - 1)
    for end in range(n - 1, 0, -1):
        a[end], a[0] = a[0], a[end]
        steps.append((list(a), 0, end))
        _sift_down(0, end - 1)
    steps.append((list(a), -1, -1))
    return steps


def _record_shellsort(arr: List[int]) -> List[Tuple[List[int], int, int]]:
    a = list(arr)
    steps: List[Tuple[List[int], int, int]] = []
    n = len(a)
    gap = n // 2
    while gap > 0:
        for i in range(gap, n):
            temp = a[i]
            j = i
            while j >= gap:
                steps.append((list(a), j, j - gap))
                if a[j - gap] > temp:
                    a[j] = a[j - gap]
                    j -= gap
                    steps.append((list(a), j, i))
                else:
                    break
            a[j] = temp
        gap //= 2
    steps.append((list(a), -1, -1))
    return steps


ALGO_MATCHUPS: List[Tuple[str, Callable, str, Callable]] = [
    ("QuickSort", _record_quicksort, "MergeSort", _record_mergesort),
    ("HeapSort", _record_heapsort, "ShellSort", _record_shellsort),
    ("MergeSort", _record_mergesort, "HeapSort", _record_heapsort),
    ("QuickSort", _record_quicksort, "ShellSort", _record_shellsort),
]


def _render_bars_pygame_surface(
    arr: List[int],
    active_i: int,
    active_j: int,
    width: int,
    height: int,
    base_color: Tuple[int, int, int],
    finished: bool,
) -> Image.Image:
    surf = pygame.Surface((width, height))
    surf.fill((22, 24, 42))
    n = len(arr)
    max_val = max(arr) if arr else 1
    bar_w = max(4, (width - 24) // n)
    x_offset = (width - bar_w * n) // 2

    for idx, val in enumerate(arr):
        bh = int(((val / float(max_val)) * (height - 32)))
        bx = x_offset + idx * bar_w
        by = height - 12 - bh
        if finished:
            col = ACCENT_GREEN
        elif idx in (active_i, active_j):
            col = ACCENT_YELLOW
        else:
            col = base_color
        pygame.draw.rect(surf, col, pygame.Rect(bx, by, max(2, bar_w - 2), bh), border_radius=3)

    raw_bytes = pygame.image.tostring(surf, "RGB")
    return Image.frombytes("RGB", (width, height), raw_bytes)


def generate_sorting_race_video(
    seed: str,
    output_dir: Path = OUTPUT_DIR,
    wildcard: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Renders a 22-second (1080x1920 @ 30fps) Sorting Algorithm Race video with Pygame bars
    and pitched synthesizer ticks synced to comparison steps.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pygame.init()
    rng = random.Random(_seed_to_int(seed))

    algo_a_name, algo_a_fn, algo_b_name, algo_b_fn = ALGO_MATCHUPS[_seed_to_int(seed) % len(ALGO_MATCHUPS)]
    if rng.random() < 0.5:
        algo_a_name, algo_a_fn, algo_b_name, algo_b_fn = algo_b_name, algo_b_fn, algo_a_name, algo_a_fn

    n_bars = 48
    base_array = list(range(10, 10 + n_bars * 2, 2))
    rng.shuffle(base_array)

    steps_a = algo_a_fn(base_array)
    steps_b = algo_b_fn(base_array)

    ops_a = len(steps_a)
    ops_b = len(steps_b)
    if ops_a <= ops_b:
        winner_name = algo_a_name
        loser_name = algo_b_name
        winner_ops, loser_ops = ops_a, ops_b
    else:
        winner_name = algo_b_name
        loser_name = algo_a_name
        winner_ops, loser_ops = ops_b, ops_a

    max_ops = max(ops_a, ops_b)
    total_duration = 22.0
    total_frames = int(total_duration * VIDEO_FPS)
    countdown_start_sec = 14.0
    reveal_start_sec = 17.0
    outro_start_sec = 20.2

    sfx_events: List[SfxEvent] = []
    for t_tick in range(1, int(countdown_start_sec * 4)):
        ts = t_tick * 0.25
        pitch = 0.8 + 0.55 * (ts / countdown_start_sec)
        sfx_events.append(SfxEvent(timestamp_sec=ts, kind="tick", gain=0.45, pitch_shift=pitch))
    add_countdown_and_reveal_sfx(sfx_events, countdown_start_sec, reveal_start_sec)

    voice_cues = build_standard_voice_cues(
        hook_text=f"Which algorithm finishes sorting first: {algo_a_name} or {algo_b_name}?",
        countdown_time=countdown_start_sec,
        reveal_time=reveal_start_sec,
        reveal_text=f"{winner_name} wins in {winner_ops} operations!",
        outro_time=outro_start_sec,
        output_dir=output_dir / "tts_cache",
    )

    audio_wav_path = output_dir / f"sorting_race_{seed}.wav"
    mix_master_audio(
        duration_sec=total_duration,
        output_wav_path=audio_wav_path,
        sfx_events=sfx_events,
        voice_cues=voice_cues,
        bgm_track_name="arena_pulse_cc0.wav",
        bgm_volume=0.15,
    )

    output_mp4_path = output_dir / f"sorting_race_{seed}.mp4"
    wildcard_tuple = (
        (wildcard["country_label"], wildcard["username"])
        if wildcard and wildcard.get("country_label") and wildcard.get("username")
        else None
    )

    cx = VIDEO_WIDTH // 2
    panel_w, panel_h = 920, 390

    with FFmpegFramePipe(output_mp4_path, audio_wav_path) as pipe:
        for f_idx in range(total_frames):
            t_sec = f_idx / float(VIDEO_FPS)
            frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), BG_COLOR)
            draw = ImageDraw.Draw(frame)

            # Freeze at 82% of max_ops during countdown so viewer must guess before the finish!
            if t_sec < countdown_start_sec:
                sim_op = int((t_sec / countdown_start_sec) * (max_ops * 0.82))
            elif t_sec < reveal_start_sec:
                sim_op = int(max_ops * 0.82)
            else:
                finish_prog = min(1.0, (t_sec - reveal_start_sec) / 1.0)
                sim_op = int(max_ops * (0.82 + 0.18 * finish_prog))

            idx_a = min(ops_a - 1, sim_op)
            idx_b = min(ops_b - 1, sim_op)
            arr_a, ai_1, ai_2 = steps_a[idx_a]
            arr_b, bi_1, bi_2 = steps_b[idx_b]
            done_a = (t_sec >= reveal_start_sec) and (idx_a >= ops_a - 1)
            done_b = (t_sec >= reveal_start_sec) and (idx_b >= ops_b - 1)

            # Top Panel: Algorithm A
            top_y = 370
            draw.rounded_rectangle(
                [68, top_y, VIDEO_WIDTH - 68, top_y + 470],
                radius=24,
                fill=PANEL_BG_COLOR,
                outline=ACCENT_GREEN if (done_a and winner_name == algo_a_name) else ACCENT_BLUE,
                width=4,
            )
            draw.text(
                (96, top_y + 18),
                f"A: {algo_a_name.upper()}  •  OPS: {idx_a + 1}",
                font=get_font(34, bold=True),
                fill=ACCENT_YELLOW if winner_name == algo_a_name and t_sec >= reveal_start_sec else TEXT_WHITE,
            )
            surf_a = _render_bars_pygame_surface(arr_a, ai_1, ai_2, panel_w, panel_h, ACCENT_BLUE, done_a)
            frame.paste(surf_a, (80, top_y + 66))

            # Bottom Panel: Algorithm B
            bot_y = 870
            draw.rounded_rectangle(
                [68, bot_y, VIDEO_WIDTH - 68, bot_y + 470],
                radius=24,
                fill=PANEL_BG_COLOR,
                outline=ACCENT_GREEN if (done_b and winner_name == algo_b_name) else ACCENT_RED,
                width=4,
            )
            draw.text(
                (96, bot_y + 18),
                f"B: {algo_b_name.upper()}  •  OPS: {idx_b + 1}",
                font=get_font(34, bold=True),
                fill=ACCENT_YELLOW if winner_name == algo_b_name and t_sec >= reveal_start_sec else TEXT_WHITE,
            )
            surf_b = _render_bars_pygame_surface(arr_b, bi_1, bi_2, panel_w, panel_h, ACCENT_RED, done_b)
            frame.paste(surf_b, (80, bot_y + 66))

            if t_sec >= reveal_start_sec:
                draw.rounded_rectangle(
                    [80, 1370, VIDEO_WIDTH - 80, 1595],
                    radius=24,
                    fill=(18, 62, 42),
                    outline=ACCENT_GREEN,
                    width=4,
                )
                draw_centered_text(
                    draw,
                    f"WINNER: {winner_name.upper()}!",
                    cx,
                    1395,
                    get_font(62, title=True),
                    fill=TEXT_WHITE,
                )
                draw_centered_text(
                    draw,
                    f"{winner_name}: {winner_ops} ops  vs  {loser_name}: {loser_ops} ops",
                    cx,
                    1490,
                    get_font(36, bold=True),
                    fill=ACCENT_YELLOW,
                )

            apply_standard_hud(
                frame=frame,
                t_sec=t_sec,
                total_duration=total_duration,
                series_badge="ALGORITHM SORTING RACE",
                hook_title=f"{algo_a_name.upper()} VS {algo_b_name.upper()}: WHO WINS?",
                subtitle_line=f"{n_bars} Scrambled Elements  •  Seed: {seed}",
                countdown_start_sec=countdown_start_sec,
                reveal_start_sec=reveal_start_sec,
                wildcard_info=wildcard_tuple,
                voice_cues=voice_cues,
                footer_credit=f"Sorting Seed: {seed} • Identical Initial Array",
            )
            pipe.write_frame(frame)

    if audio_wav_path.exists():
        audio_wav_path.unlink()

    return {
        "format": "sorting_race",
        "seed": seed,
        "video_path": output_mp4_path,
        "duration_sec": total_duration,
        "winner": f"{winner_name} ({winner_ops} ops)",
        "algo_a": algo_a_name,
        "algo_b": algo_b_name,
        "series_short": "Sorting Race",
        "reveal_timestamp_sec": reveal_start_sec,
        "comment_question": (
            f"Did you pick {winner_name} over {loser_name}? 💻 "
            "Which two sorting algorithms should battle next? 👇"
        ),
    }
