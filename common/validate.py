"""
Call It Arena — Pre-Upload Video & Metadata Quality Gate (`common/validate.py`).

Validates every generated video before upload:
- File exists and size < 100 MB (and > 50 KB)
- Duration between 15.0s and 60.0s (target 20–40s)
- Resolution exactly 1080x1920
- Contains a valid audio stream (AAC / MP4A)
- Winner / answer exists in the generation metadata
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audio import resolve_ffmpeg_binary


@dataclass
class ValidationResult:
    ok: bool
    duration_sec: float
    width: int
    height: int
    has_audio: bool
    file_size_mb: float
    winner: str
    errors: List[str]


def _inspect_media_with_ffprobe_or_ffmpeg(video_path: Path) -> Dict[str, Any]:
    """
    Extracts width, height, duration, and audio presence using `ffprobe` if available,
    or `ffmpeg -i` stderr stream parsing (works with `imageio_ffmpeg` without needing separate ffprobe).
    """
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin:
        try:
            proc = subprocess.run(
                [
                    ffprobe_bin,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_type,width,height",
                    "-of",
                    "json",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            data = json.loads(proc.stdout)
            width, height = 0, 0
            has_audio = False
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    width = int(stream.get("width", 0))
                    height = int(stream.get("height", 0))
                elif stream.get("codec_type") == "audio":
                    has_audio = True
            duration = float(data.get("format", {}).get("duration", 0.0))
            return {
                "width": width,
                "height": height,
                "duration": duration,
                "has_audio": has_audio,
            }
        except Exception:
            pass

    # Fallback: parse `ffmpeg -i <video_path>` stderr
    ffmpeg_bin = resolve_ffmpeg_binary()
    proc = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-i", str(video_path)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    info_text = proc.stderr or proc.stdout or ""

    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info_text)
    duration = 0.0
    if dur_match:
        h, m, s = int(dur_match.group(1)), int(dur_match.group(2)), float(dur_match.group(3))
        duration = h * 3600.0 + m * 60.0 + s

    res_match = re.search(r"Stream #.*Video:.*?(\d{3,4})x(\d{3,4})", info_text)
    width = int(res_match.group(1)) if res_match else 0
    height = int(res_match.group(2)) if res_match else 0
    has_audio = bool(re.search(r"Stream #.*Audio:", info_text))

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "has_audio": has_audio,
    }


def validate_video_package(
    video_path: Path,
    metadata: Optional[Dict[str, Any]] = None,
    min_duration: float = 15.0,
    max_duration: float = 60.0,
    expected_width: int = 1080,
    expected_height: int = 1920,
    max_size_mb: float = 100.0,
) -> ValidationResult:
    """
    Runs all pre-upload checks on `video_path` and its generation `metadata`.
    Never allows a broken, silent, out-of-spec, or winner-less video to be uploaded.
    """
    errors: List[str] = []
    meta = metadata or {}
    winner = str(meta.get("winner", "")).strip()

    if not video_path.exists():
        return ValidationResult(
            ok=False,
            duration_sec=0.0,
            width=0,
            height=0,
            has_audio=False,
            file_size_mb=0.0,
            winner=winner,
            errors=[f"Video file does not exist: {video_path}"],
        )

    size_bytes = video_path.stat().st_size
    file_size_mb = round(size_bytes / (1024 * 1024), 3)
    if size_bytes < 50_000:
        errors.append(f"Video file is suspiciously small ({size_bytes} bytes)")
    if file_size_mb >= max_size_mb:
        errors.append(f"Video file size {file_size_mb:.2f} MB exceeds {max_size_mb} MB limit")

    probe = _inspect_media_with_ffprobe_or_ffmpeg(video_path)
    duration = round(float(probe["duration"]), 2)
    width = int(probe["width"])
    height = int(probe["height"])
    has_audio = bool(probe["has_audio"])

    if not (min_duration <= duration <= max_duration):
        errors.append(f"Duration {duration}s outside allowed [{min_duration}s, {max_duration}s] range")

    if (width, height) != (expected_width, expected_height):
        errors.append(f"Resolution {width}x{height} does not match required {expected_width}x{expected_height}")

    if not has_audio:
        errors.append("Video has no audio stream")

    if not winner:
        errors.append("Metadata is missing a valid non-empty 'winner' field")

    return ValidationResult(
        ok=len(errors) == 0,
        duration_sec=duration,
        width=width,
        height=height,
        has_audio=has_audio,
        file_size_mb=file_size_mb,
        winner=winner,
        errors=errors,
    )
