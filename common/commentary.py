"""
Call It Arena — Race commentary engine (`common/commentary.py`).

Turns a simulated marble race into commentator lines for:

* **Shorts (spoiler-safe):** extra live lines between the hook and the 3-2-1 countdown. They hype the
  race and describe visible action, but NEVER name the winner or the order before the reveal
  ("One's out! Did you catch who?"). After the reveal they add table context (knockouts, climbs).
* **Long-form season recap (full play-by-play):** named lead changes, near misses, the winning
  escape, points and table movement.

All wording is deterministic per race seed (seeded variety, no back-to-back repeats) and short enough
to fit the burned-in caption box (<= ~46 characters per line).
"""

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .audio import VoiceCue
from .branding import COUNTRIES_META

NUM_WORDS = {
    2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten",
    11: "Eleven", 12: "Twelve", 13: "Thirteen", 14: "Fourteen", 15: "Fifteen", 16: "Sixteen",
}
ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
            9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
            15: "fifteenth", 16: "sixteenth"}
MAX_CAPTION_CHARS = 46


def country_name(code: str) -> str:
    return str(COUNTRIES_META.get(code, {}).get("name", code))


def _rng(seed: str, salt: str) -> random.Random:
    return random.Random(int(hashlib.sha256(f"{seed}|{salt}".encode("utf-8")).hexdigest()[:12], 16))


def _pick(rng: random.Random, options: Sequence[str], **fmt: Any) -> str:
    fitting = [o.format(**fmt) for o in options]
    short = [o for o in fitting if len(o) <= MAX_CAPTION_CHARS] or fitting
    return rng.choice(short)


# --------------------------------------------------------------------------- race analysis
@dataclass
class RaceEvents:
    first_finish: Optional[Tuple[float, str]]
    finishes: List[Tuple[float, str]]
    lead_changes: List[Tuple[float, str]]
    near_misses: List[Tuple[float, str]]
    last_place: Optional[str]
    duration: float
    counts_at: Callable[[float], int] = field(default=lambda t: 0)


def analyze_race(
    frames: List[Dict[str, Any]],
    race_codes: List[str],
    finish_times: Dict[str, float],
    full_finish_order: List[str],
    progress_fn: Callable[[float, float], float],
    finish_threshold: float,
    fps: int = 30,
) -> RaceEvents:
    """Extracts commentary-worthy moments from per-frame marble positions."""
    n = len(frames)
    duration = n / float(fps)
    finishes = sorted(((t, c) for c, t in finish_times.items()), key=lambda x: x[0])

    lead_changes: List[Tuple[float, str]] = []
    near_misses: List[Tuple[float, str]] = []
    announced: Optional[str] = None
    cand: Optional[str] = None
    cand_since = 0.0
    step = max(1, fps // 6)
    near_cooldown: Dict[str, float] = {}
    for i in range(0, n, step):
        t = i / float(fps)
        marbles = frames[i]["marbles"]
        live = [c for c in race_codes if not (c in finish_times and finish_times[c] <= t)]
        if not live:
            break
        prog = {c: progress_fn(marbles[c][0], marbles[c][1]) for c in live}
        leader = max(live, key=lambda c: prog[c])
        if leader != cand:
            cand, cand_since = leader, t
        elif cand != announced and t - cand_since >= 0.75 and t >= 1.5:
            if not lead_changes or t - lead_changes[-1][0] >= 2.0:
                lead_changes.append((cand_since, cand))
                announced = cand
        if finish_threshold > 0:
            for c in live:
                if prog[c] >= finish_threshold and t - near_cooldown.get(c, -99.0) > 3.0:
                    ft = finish_times.get(c)
                    if ft is None or ft > t + 1.6:
                        near_misses.append((t, c))
                        near_cooldown[c] = t

    def counts_at(t: float) -> int:
        return sum(1 for ft, _ in finishes if ft <= t)

    return RaceEvents(
        first_finish=finishes[0] if finishes else None,
        finishes=finishes,
        lead_changes=lead_changes,
        near_misses=near_misses,
        last_place=full_finish_order[-1] if full_finish_order else None,
        duration=duration,
        counts_at=counts_at,
    )


def finish_threshold_for(mode: str, center: Tuple[float, float], escape_radius: float, finish_y: float,
                         marble_radius: float) -> float:
    """Progress value that counts as a 'near miss' zone: inside the ring gap, or just above the finish line."""
    if mode == "line":
        return finish_y - marble_radius * 2.0
    return escape_radius


# --------------------------------------------------------------------------- Shorts (spoiler-safe)
@dataclass
class PlannedLine:
    t: float
    text: str
    window_end: float  # line must finish before this time
    priority: int = 1


def plan_shorts_lines(
    seed: str,
    events: RaceEvents,
    race_codes: List[str],
    stage: str,
    points_multiplier: int,
    is_grand_final: bool,
    boosted_code: Optional[str],
    countdown_start: float,
    winner_code: str,
) -> List[PlannedLine]:
    """Spoiler-safe pre-reveal lines. Never names the winner, never names finishers."""
    rng = _rng(seed, "shorts_commentary")
    n = len(race_codes)
    nw = NUM_WORDS.get(n, str(n))
    lines: List[PlannedLine] = []

    if is_grand_final:
        ctx = _pick(rng, ["Winner takes the crown!", "Everything on the line. Go!",
                          "The title is decided right here!"])
    elif stage == "Knockout Stage":
        ctx = _pick(rng, ["Points never reset. Lowest total is out!", "Every point counts. Lowest total goes home!",
                          "Lowest total in the table is out!"])
    elif points_multiplier >= 2:
        ctx = _pick(rng, ["Double points on the line!", "Finals race. Double points!"])
    elif boosted_code and boosted_code in race_codes:
        ctx = _pick(rng, ["{c} has the crowd boost!", "Crowd boost for {c}. Will it matter?"],
                    c=country_name(boosted_code))
    else:
        ctx = _pick(rng, ["{nw} marbles. One way out!", "{nw} nations, total chaos!",
                          "Here we go, {nwl} on the grid!"], nw=nw, nwl=nw.lower())
    lines.append(PlannedLine(t=2.9, text=ctx, window_end=countdown_start - 0.25, priority=2))

    if events.first_finish is not None and events.first_finish[0] < countdown_start - 1.5:
        t1 = events.first_finish[0]
        first = _pick(rng, ["One's out! Did you catch who?", "Someone's free! Who was it?",
                            "First one gone! Eyes on the gap!"])
        lines.append(PlannedLine(t=t1 + 0.15, text=first, window_end=countdown_start - 0.25, priority=3))

    # A visible near miss by a NON-winner keeps tension without giving anything away.
    t_from = (events.first_finish[0] + 2.4) if events.first_finish else 6.5
    misses = [(t, c) for t, c in events.near_misses if c != winner_code and t >= 3.5]
    later = [(t, c) for t, c in misses if t >= t_from] or misses
    if later:
        t_nm, c_nm = later[0]
        txt = _pick(rng, ["So close for {c}!", "{c} nearly slips out!", "Ooh, {c} rattles the edge!"],
                    c=country_name(c_nm))
        lines.append(PlannedLine(t=t_nm, text=txt, window_end=countdown_start - 0.25, priority=1))
    else:
        late_t = max(t_from, countdown_start - 3.6)
        k = events.counts_at(late_t)
        txt = (_pick(rng, ["{k} out, the rest still trapped!", "{k} gone. Who's next?"], k=NUM_WORDS.get(k, str(k)))
               if k >= 2 else _pick(rng, ["Still all to play for!", "Nobody's safe yet!"]))
        lines.append(PlannedLine(t=late_t, text=txt, window_end=countdown_start - 0.25, priority=1))
    return lines


def plan_shorts_post_reveal_line(
    seed: str,
    stage: str,
    eliminated_code: Optional[str],
    winner_code: str,
    standings: List[Dict[str, Any]],
    is_grand_final: bool,
    t: float,
    window_end: float,
) -> Optional[PlannedLine]:
    """One line of table context over the standings page (safe: the result is already revealed)."""
    rng = _rng(seed, "shorts_post")
    if is_grand_final:
        return None
    if eliminated_code:
        txt = _pick(rng, ["{c} is knocked out!", "Goodbye {c}. Knocked out!"], c=country_name(eliminated_code))
        return PlannedLine(t=t, text=txt, window_end=window_end, priority=3)
    rank = next((i + 1 for i, s in enumerate(standings) if s.get("code") == winner_code), None)
    if rank == 1:
        txt = _pick(rng, ["{c} now tops the table!", "{c} leads the league!"], c=country_name(winner_code))
    elif rank and rank <= 5:
        txt = _pick(rng, ["{c} moves up to {o}!", "{c} now {o} overall!"], c=country_name(winner_code),
                    o=ORDINALS.get(rank, f"#{rank}"))
    elif standings:
        txt = _pick(rng, ["{c} still leads the table!", "{c} stays on top!"], c=country_name(standings[0]["code"]))
    else:
        return None
    return PlannedLine(t=t, text=txt, window_end=window_end, priority=1)


def schedule_lines_into_cues(
    base_cues: List[VoiceCue],
    planned: List[PlannedLine],
    synth: Callable[[str], Tuple[Path, float]],
    gap: float = 0.25,
) -> List[VoiceCue]:
    """Adds planned lines whose synthesized audio fits without overlapping any existing cue."""
    cues = sorted(base_cues, key=lambda c: c.timestamp_sec)
    for line in sorted(planned, key=lambda p: -p.priority):
        try:
            wav, dur = synth(line.text)
        except Exception:
            continue
        busy = [(c.timestamp_sec - gap, c.timestamp_sec + c.duration_sec + gap) for c in cues]
        start = line.t
        for b0, b1 in sorted(busy):
            if start < b1 and start + dur > b0:
                start = b1  # slide later past the occupied cue
        if start + dur > line.window_end or start - line.t > 2.2:
            continue
        if any(start < b1 and start + dur > b0 for b0, b1 in busy):
            continue
        cues.append(VoiceCue(timestamp_sec=round(start, 3), wav_path=wav, text=line.text, duration_sec=dur))
        cues.sort(key=lambda c: c.timestamp_sec)
    return cues


# --------------------------------------------------------------------------- long-form play-by-play
def recap_race_lines(
    seed: str,
    race_num: int,
    stage: str,
    label: str,
    n_racers: int,
    points_multiplier: int,
    events: Optional[RaceEvents],
    full_finish_order: List[str],
    eliminated: Optional[str],
    leader_before: Optional[Tuple[str, int]],
    winner_rank_after: Optional[int],
    leader_after: Optional[Tuple[str, int]],
) -> Dict[str, Any]:
    """Returns {'intro': str, 'action': [(t, str)], 'finish': str, 'result': str, 'table': str|None}."""
    rng = _rng(seed, f"recap_{race_num}")
    winner = full_finish_order[0] if full_finish_order else ""
    w = country_name(winner)
    nw = NUM_WORDS.get(n_racers, str(n_racers))
    if stage == "Monthly Grand Final":
        intro = "The Grand Final. One mega gauntlet. Winner takes the crown."
    elif stage == "Knockout Stage":
        intro = _pick(rng, ["Race {r}, {a}. Lowest total is out.", "Race {r}. {a}. Knockout rules."],
                      r=race_num, a=label)
    elif points_multiplier >= 2:
        intro = _pick(rng, ["Race {r}, {a}. Double points.", "Race {r}. {a}. Finals pressure."], r=race_num, a=label)
    else:
        intro = _pick(rng, ["Race {r}, {a}. {nw} on the grid.", "Race {r}. {a}.", "On to race {r}, {a}."],
                      r=race_num, a=label, nw=nw)
    if leader_before and race_num > 1 and stage != "Monthly Grand Final":
        intro_tail = _pick(rng, [" {c} leads on {p}.", " {c} tops the table."], c=country_name(leader_before[0]),
                           p=leader_before[1])
        if len(intro + intro_tail) <= 70:
            intro += intro_tail

    action: List[Tuple[float, str]] = []
    if events is not None:
        used: set = set()
        for t, c in events.lead_changes:
            if events.first_finish and t >= events.first_finish[0] - 0.8:
                break
            if c in used or t < 1.2:
                continue
            action.append((t, _pick(rng, ["{c} pushing to the front!", "{c} hunting the exit!", "Here comes {c}!"],
                                    c=country_name(c))))
            used.add(c)
            if len(action) >= 2:
                break
        for t, c in events.near_misses:
            if c != winner and (not events.first_finish or t < events.first_finish[0] + 3.0):
                action.append((t, _pick(rng, ["Oh, so close for {c}!", "{c} nearly gets out!"], c=country_name(c))))
                break
        action.sort(key=lambda x: x[0])

    finish = _pick(rng, ["And it's {c}! {c} breaks free!", "{c} is out first! What a move!",
                         "There goes {c}! {c} takes it!"], c=w)
    pts = 10 * points_multiplier
    second = country_name(full_finish_order[1]) if len(full_finish_order) > 1 else ""
    third = country_name(full_finish_order[2]) if len(full_finish_order) > 2 else ""
    result = f"{w} wins race {race_num} for {pts} points."
    if second and third:
        result += f" {second} second, {third} third."
    table = None
    if eliminated:
        table = _pick(rng, ["{c} has the lowest total and is knocked out.", "And {c} is eliminated."],
                      c=country_name(eliminated))
    elif leader_after and winner_rank_after == 1:
        table = f"{w} now leads the league on {leader_after[1]}."
    elif winner_rank_after and winner_rank_after <= 4:
        table = f"That lifts {w} to {ORDINALS.get(winner_rank_after, '')} overall."
    return {"intro": intro, "action": action, "finish": finish, "result": result, "table": table}
