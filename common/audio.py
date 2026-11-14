"""
Call It Arena — Audio Synthesis, CC0 BGM/SFX Asset Generator & Timestamped Audio Mixer.

Features:
- Generates 100% CC0 public-domain background music tracks in `/assets/music/`
  and crisp sound effects (`boing.wav`, `tick.wav`, `countdown_beep.wav`, `reveal_fanfare.wav`)
  in `/assets/sfx/`.
- Mixes background music at 15% volume (`0.15`), TTS voiceover segments, and synced collision/tick
  SFX timestamps (`adelay` / sample-accurate mixer) into a master 44.1kHz stereo WAV track.
"""

import logging
import os
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from .branding import MUSIC_DIR, SFX_DIR, resolve_safe_path

logger = logging.getLogger("call_it_arena")

SAMPLE_RATE = 44100


@dataclass
class SfxEvent:
    timestamp_sec: float
    kind: str  # "boing", "tick", "countdown_beep", "reveal_fanfare"
    gain: float = 0.65
    pitch_shift: float = 1.0


@dataclass
class VoiceCue:
    timestamp_sec: float
    wav_path: Path
    text: str
    duration_sec: float = 2.0


def resolve_ffmpeg_binary() -> str:
    """
    Strictly resolves the verified `ffmpeg` executable path.
    Checks PATH, standard OS locations, and `imageio_ffmpeg`.
    """
    found = shutil.which("ffmpeg")
    if found and os.path.isfile(found) and os.access(found, os.X_OK):
        return found

    for candidate in (
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
        "/Users/izazhaq/.gemini/jetski/scratch/GoeSerene/.venv/bin/ffmpeg",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    raise FileNotFoundError("ffmpeg executable could not be resolved.")


def _write_wav_mono_or_stereo(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    channels = 1 if pcm16.ndim == 1 else pcm16.shape[1]
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return path


def _read_wav_as_float(path: Path) -> Tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_channels > 1:
        arr = arr.reshape(-1, n_channels).mean(axis=1)
    return arr, sr


def ensure_audio_assets() -> None:
    """
    Ensures CC0 SFX and background music WAV files exist in `/assets/sfx/` and `/assets/music/`.
    """
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Boing / Marble collision chime (95 ms)
    boing_path = SFX_DIR / "boing.wav"
    if not boing_path.exists():
        dur = 0.095
        t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
        freq = np.linspace(520.0, 880.0, len(t))
        env = np.exp(-28.0 * t)
        sig = 0.8 * np.sin(2 * np.pi * np.cumsum(freq) / SAMPLE_RATE) * env
        _write_wav_mono_or_stereo(boing_path, sig)

    # 2. Tick / Sorting or Data comparison click (40 ms)
    tick_path = SFX_DIR / "tick.wav"
    if not tick_path.exists():
        dur = 0.04
        t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
        env = np.exp(-65.0 * t)
        sig = 0.7 * (np.sin(2 * np.pi * 1100.0 * t) + 0.4 * np.sin(2 * np.pi * 2200.0 * t)) * env
        _write_wav_mono_or_stereo(tick_path, sig)

    # 3. Countdown Beep (180 ms)
    beep_path = SFX_DIR / "countdown_beep.wav"
    if not beep_path.exists():
        dur = 0.18
        t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
        env = np.exp(-10.0 * t)
        sig = 0.85 * (np.sin(2 * np.pi * 880.0 * t) + 0.3 * np.sin(2 * np.pi * 1760.0 * t)) * env
        _write_wav_mono_or_stereo(beep_path, sig)

    # 4. Reveal Fanfare Chord (650 ms)
    fanfare_path = SFX_DIR / "reveal_fanfare.wav"
    if not fanfare_path.exists():
        dur = 0.65
        t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
        env = np.minimum(1.0, t / 0.02) * np.exp(-3.5 * t)
        # C major bright triad + octave (523.25, 659.25, 783.99, 1046.50 Hz)
        sig = (
            0.35 * np.sin(2 * np.pi * 523.25 * t)
            + 0.30 * np.sin(2 * np.pi * 659.25 * t)
            + 0.30 * np.sin(2 * np.pi * 783.99 * t)
            + 0.25 * np.sin(2 * np.pi * 1046.50 * t)
        ) * env
        _write_wav_mono_or_stereo(fanfare_path, sig)

    # 5. CC0 Background Music Track 1: Arena Pulse (16s seamless loop)
    arena_bgm = MUSIC_DIR / "arena_pulse_cc0.wav"
    if not arena_bgm.exists():
        dur = 16.0
        t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
        # 120 BPM = 2 Hz beat
        beat_env = 0.5 + 0.5 * np.sin(2 * np.pi * 2.0 * t - np.pi / 2) ** 4
        # Bass progression: A2 (110Hz) -> F2 (87.31Hz) -> C3 (130.81Hz) -> G2 (98.0Hz)
        notes = [110.0, 87.31, 130.81, 98.0]
        bass = np.zeros_like(t)
        arp = np.zeros_like(t)
        seg_len = len(t) // 4
        arp_mults = [2.0, 2.5, 3.0, 4.0]
        for idx, base_f in enumerate(notes):
            s0, s1 = idx * seg_len, (idx + 1) * seg_len
            ts = t[s0:s1]
            bass[s0:s1] = 0.45 * np.sin(2 * np.pi * base_f * ts) + 0.2 * np.sin(2 * np.pi * (base_f * 2) * ts)
            # 16th-note arpeggio (8 notes per second)
            step_idx = ((ts * 8.0).astype(int)) % 4
            arp_f = np.choose(step_idx, [base_f * m for m in arp_mults])
            note_phase = (ts * 8.0) % 1.0
            note_env = np.exp(-4.0 * note_phase)
            arp[s0:s1] = 0.25 * np.sin(2 * np.pi * arp_f * ts) * note_env
        sig = (bass * beat_env + arp) * 0.7
        _write_wav_mono_or_stereo(arena_bgm, sig)

    # 6. CC0 Background Music Track 2: Quiz Tension (16s seamless loop)
    quiz_bgm = MUSIC_DIR / "quiz_tension_cc0.wav"
    if not quiz_bgm.exists():
        dur = 16.0
        t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
        pulse = np.exp(-6.0 * ((t * 2.0) % 1.0))
        pad = 0.3 * np.sin(2 * np.pi * 146.83 * t) + 0.25 * np.sin(2 * np.pi * 220.0 * t)
        tick_arp = 0.25 * np.sin(2 * np.pi * 440.0 * t) * pulse
        sig = (pad + tick_arp) * 0.7
        _write_wav_mono_or_stereo(quiz_bgm, sig)


def build_adelay_filter_string(events: List[SfxEvent]) -> str:
    """
    Constructs an FFmpeg `adelay` filter expression for a list of timestamped SFX events.
    Kept for transparency and direct FFmpeg filter graph inspection.
    """
    parts = []
    for idx, ev in enumerate(events[:32]):
        ms = max(0, int(round(ev.timestamp_sec * 1000)))
        parts.append(f"[{idx + 1}:a]adelay={ms}|{ms},volume={ev.gain:.2f}[s{idx}]")
    return ";".join(parts)


def mix_master_audio(
    duration_sec: float,
    output_wav_path: Path,
    sfx_events: Optional[List[SfxEvent]] = None,
    voice_cues: Optional[List[VoiceCue]] = None,
    bgm_track_name: str = "arena_pulse_cc0.wav",
    bgm_volume: float = 0.15,
) -> Path:
    """
    Synthesizes the complete master audio track (`duration_sec` seconds, 44.1kHz):
    - Loops the selected CC0 background music at `bgm_volume` (default 15% = 0.15).
    - Places all `voice_cues` (from Piper TTS) at their exact timestamps, gently ducking BGM.
    - Places all `sfx_events` (boing, tick, countdown_beep, reveal_fanfare) at their exact timestamps.
    """
    ensure_audio_assets()
    total_samples = max(int(SAMPLE_RATE * duration_sec), SAMPLE_RATE)

    # Load and tile background music at 15% volume
    bgm_path = resolve_safe_path(MUSIC_DIR, bgm_track_name)
    if not bgm_path.exists():
        bgm_path = MUSIC_DIR / "arena_pulse_cc0.wav"
    bgm_samples, _ = _read_wav_as_float(bgm_path)
    repeats = (total_samples // len(bgm_samples)) + 2
    master = np.tile(bgm_samples, repeats)[:total_samples] * float(bgm_volume)

    # Preload SFX bank
    sfx_bank = {}
    for kind in ("boing", "tick", "countdown_beep", "reveal_fanfare"):
        p = SFX_DIR / f"{kind}.wav"
        if p.exists():
            sfx_bank[kind], _ = _read_wav_as_float(p)

    # Mix timestamped SFX events
    for ev in sfx_events or []:
        if ev.kind not in sfx_bank:
            continue
        start_idx = int(ev.timestamp_sec * SAMPLE_RATE)
        if start_idx < 0 or start_idx >= total_samples:
            continue
        clip = sfx_bank[ev.kind]
        if abs(ev.pitch_shift - 1.0) > 0.03 and len(clip) > 16:
            new_len = max(16, int(len(clip) / ev.pitch_shift))
            x_old = np.linspace(0, 1, len(clip))
            x_new = np.linspace(0, 1, new_len)
            clip = np.interp(x_new, x_old, clip).astype(np.float32)
        end_idx = min(total_samples, start_idx + len(clip))
        seg_len = end_idx - start_idx
        master[start_idx:end_idx] += clip[:seg_len] * float(ev.gain)

    # Mix voiceover cues
    for cue in voice_cues or []:
        if not cue.wav_path or not cue.wav_path.exists():
            continue
        try:
            vo_samples, vo_sr = _read_wav_as_float(cue.wav_path)
            if vo_sr != SAMPLE_RATE and len(vo_samples) > 16:
                new_len = int(len(vo_samples) * (SAMPLE_RATE / float(vo_sr)))
                vo_samples = np.interp(
                    np.linspace(0, 1, new_len),
                    np.linspace(0, 1, len(vo_samples)),
                    vo_samples,
                ).astype(np.float32)
            start_idx = max(0, int(cue.timestamp_sec * SAMPLE_RATE))
            if start_idx >= total_samples:
                continue
            end_idx = min(total_samples, start_idx + len(vo_samples))
            seg_len = end_idx - start_idx
            # Slight BGM ducking during speech for crisp voice clarity
            master[start_idx:end_idx] *= 0.65
            master[start_idx:end_idx] += vo_samples[:seg_len] * 0.95
        except Exception as e:
            logger.warning("Skipping voice cue %s: %s", cue.wav_path, e)

    # Smooth fade-out on final 0.35s
    fade_len = min(int(0.35 * SAMPLE_RATE), total_samples // 4)
    if fade_len > 0:
        master[-fade_len:] *= np.linspace(1.0, 0.0, fade_len)

    # Stereo widen slightly for rich playback on Shorts/Reels
    stereo = np.column_stack([master, master])
    return _write_wav_mono_or_stereo(output_wav_path, stereo, SAMPLE_RATE)
