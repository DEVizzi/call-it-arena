"""
Call It Arena — Shared physics-game video producer (`common/physics_video.py`).

Wraps the standard Call It Arena pipeline (Piper TTS cues → master audio mix → frame streaming with
the unified HUD → FFmpeg MP4) so each physics game only has to supply a `draw_frame(t_sec)` callback.
"""

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image

from .audio import SfxEvent, mix_master_audio
from .branding import VIDEO_FPS
from .render import FFmpegFramePipe, add_countdown_and_reveal_sfx, apply_standard_hud
from .tts import build_standard_voice_cues


def wildcard_tuple(wildcard: Optional[Dict[str, str]]) -> Optional[Tuple[str, str]]:
    if wildcard and wildcard.get("country_label") and wildcard.get("username"):
        return (wildcard["country_label"], wildcard["username"])
    return None


def produce_physics_video(
    file_stem: str,
    output_dir: Path,
    total_duration: float,
    countdown_start_sec: float,
    reveal_start_sec: float,
    outro_start_sec: float,
    hook_spoken: str,
    reveal_spoken: str,
    sfx_events: List[SfxEvent],
    draw_frame: Callable[[float], Image.Image],
    series_badge: str,
    hook_title: str,
    subtitle_line: str,
    footer_credit: str,
    wildcard: Optional[Dict[str, str]] = None,
    bgm_track_name: str = "arena_pulse_cc0.wav",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    events = [e for e in sfx_events if 0.0 <= e.timestamp_sec < total_duration - 0.3]
    add_countdown_and_reveal_sfx(events, countdown_start_sec, reveal_start_sec)

    voice_cues = build_standard_voice_cues(
        hook_text=hook_spoken,
        countdown_time=countdown_start_sec,
        reveal_time=reveal_start_sec,
        reveal_text=reveal_spoken,
        outro_time=outro_start_sec,
        output_dir=output_dir / "tts_cache",
    )
    audio_wav_path = output_dir / f"{file_stem}.wav"
    mix_master_audio(
        duration_sec=total_duration,
        output_wav_path=audio_wav_path,
        sfx_events=events,
        voice_cues=voice_cues,
        bgm_track_name=bgm_track_name,
        bgm_volume=0.15,
    )

    output_mp4_path = output_dir / f"{file_stem}.mp4"
    wc = wildcard_tuple(wildcard)
    total_frames = int(total_duration * VIDEO_FPS)
    with FFmpegFramePipe(output_mp4_path, audio_wav_path) as pipe:
        for f_idx in range(total_frames):
            t_sec = f_idx / float(VIDEO_FPS)
            frame = draw_frame(t_sec)
            apply_standard_hud(
                frame=frame,
                t_sec=t_sec,
                total_duration=total_duration,
                series_badge=series_badge,
                hook_title=hook_title,
                subtitle_line=subtitle_line,
                countdown_start_sec=countdown_start_sec,
                reveal_start_sec=reveal_start_sec,
                wildcard_info=wc,
                voice_cues=voice_cues,
                footer_credit=footer_credit,
            )
            pipe.write_frame(frame)

    if audio_wav_path.exists():
        audio_wav_path.unlink()
    return output_mp4_path
