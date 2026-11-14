"""
Call It Arena — Piper TTS (`en_US-lessac-medium`) Voiceover & Burned-In Caption Engine.

Features:
- Downloads and caches the official `en_US-lessac-medium.onnx` + `.onnx.json` model
  in `~/.cache/piper` (cached in GitHub Actions via `actions/cache`).
- Invokes `piper` CLI / `piper-tts` when available, with automatic fallback to OS TTS (`say` / `espeak-ng` + `ffmpeg`)
  or clean synthesized vocal cadence so offline/local runs never fail.
- Returns `VoiceCue` objects with exact start/duration timestamps for burned-in captions.
"""

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import List, Tuple

import numpy as np
import requests

from .audio import SAMPLE_RATE, VoiceCue, _write_wav_mono_or_stereo, resolve_ffmpeg_binary

logger = logging.getLogger("call_it_arena")

PIPER_CACHE_DIR = Path(os.path.expanduser("~/.cache/piper"))
PIPER_MODEL_NAME = "en_US-lessac-medium"
PIPER_ONNX_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx"
)
PIPER_JSON_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
)


def ensure_piper_model(allow_download: bool = True) -> Tuple[Path, Path]:
    """
    Ensures `en_US-lessac-medium.onnx` and `.onnx.json` are cached in `~/.cache/piper`.
    """
    PIPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    onnx_path = PIPER_CACHE_DIR / f"{PIPER_MODEL_NAME}.onnx"
    json_path = PIPER_CACHE_DIR / f"{PIPER_MODEL_NAME}.onnx.json"

    if allow_download and (not onnx_path.exists() or not json_path.exists()):
        try:
            for url, target in ((PIPER_JSON_URL, json_path), (PIPER_ONNX_URL, onnx_path)):
                if not target.exists():
                    resp = requests.get(url, timeout=45, stream=True)
                    if resp.ok:
                        with open(target, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=65536):
                                if chunk:
                                    f.write(chunk)
        except Exception as e:
            logger.warning("Could not download Piper voice model (%s); will use fallback TTS.", e)

    return onnx_path, json_path


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 1.8


def synthesize_phrase(text: str, output_dir: Path) -> Tuple[Path, float]:
    """
    Synthesizes a short spoken line into a 44.1kHz WAV file.
    Priority:
    1. `piper` CLI with cached `en_US-lessac-medium.onnx`
    2. macOS `say` / Linux `espeak-ng` converted to 44.1kHz WAV via `ffmpeg`
    3. Deterministic vocal formant cadence (zero-dependency offline fallback)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    out_wav = output_dir / f"tts_{digest}.wav"
    if out_wav.exists() and out_wav.stat().st_size > 1000:
        return out_wav, _wav_duration(out_wav)

    # 1. Try Piper TTS CLI
    piper_bin = shutil.which("piper")
    if piper_bin:
        onnx_path, json_path = ensure_piper_model(allow_download=True)
        if onnx_path.exists() and json_path.exists():
            try:
                proc = subprocess.run(
                    [piper_bin, "--model", str(onnx_path), "--output_file", str(out_wav)],
                    input=text,
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if proc.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 1000:
                    return out_wav, _wav_duration(out_wav)
            except Exception as e:
                logger.warning("Piper CLI synthesis failed: %s", e)

    # 2. Try macOS `say` or Linux `espeak-ng`
    say_bin = shutil.which("say")
    if say_bin:
        try:
            ffmpeg_bin = resolve_ffmpeg_binary()
            with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp_aiff:
                aiff_path = tmp_aiff.name
            try:
                subprocess.run(
                    [say_bin, "-r", "195", "-o", aiff_path, text],
                    capture_output=True,
                    timeout=15,
                    check=True,
                )
                subprocess.run(
                    [ffmpeg_bin, "-y", "-i", aiff_path, "-ar", str(SAMPLE_RATE), "-ac", "1", str(out_wav)],
                    capture_output=True,
                    timeout=15,
                    check=True,
                )
                if out_wav.exists() and out_wav.stat().st_size > 1000:
                    return out_wav, _wav_duration(out_wav)
            finally:
                if os.path.exists(aiff_path):
                    os.remove(aiff_path)
        except Exception:
            pass

    # 3. Deterministic syllable formant cadence fallback
    words = max(2, len(text.split()))
    dur = max(1.1, min(3.2, words * 0.28))
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    syllable_rate = max(3.5, words / dur)
    env = (np.sin(np.pi * ((t * syllable_rate) % 1.0)) ** 2) * np.exp(-0.4 * t)
    sig = 0.22 * (
        np.sin(2 * np.pi * 190.0 * t)
        + 0.5 * np.sin(2 * np.pi * 580.0 * t)
        + 0.25 * np.sin(2 * np.pi * 1160.0 * t)
    ) * env
    _write_wav_mono_or_stereo(out_wav, sig, SAMPLE_RATE)
    return out_wav, dur


def build_standard_voice_cues(
    hook_text: str,
    countdown_time: float,
    reveal_time: float,
    reveal_text: str,
    outro_time: float,
    output_dir: Path,
) -> List[VoiceCue]:
    """
    Synthesizes the 4 signature Call It Arena spoken cues & burned-in caption timings:
    1. Hook (0.15s): e.g. "Which country escapes the ring first?"
    2. Countdown prompt (countdown_time): "Three, two, one... lock in your guess!"
    3. Reveal (reveal_time): e.g. "India takes the win!"
    4. Outro (outro_time): "Comment your pick for the next one!"
    """
    cues: List[VoiceCue] = []
    schedule_items = [
        (0.15, hook_text),
        (max(2.5, countdown_time), "Three, two, one! Lock in your guess!"),
        (max(4.0, reveal_time), reveal_text),
        (max(5.5, outro_time), "Comment your pick for the next one!"),
    ]
    for ts, phrase in schedule_items:
        wav_path, dur = synthesize_phrase(phrase, output_dir=output_dir)
        cues.append(VoiceCue(timestamp_sec=ts, wav_path=wav_path, text=phrase, duration_sec=dur))
    return cues
