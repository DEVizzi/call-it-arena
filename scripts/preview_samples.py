#!/usr/bin/env python3
"""
Call It Arena — Preview sample renderer (`scripts/preview_samples.py`).

Renders ONE sample video for review WITHOUT uploading anything and WITHOUT touching league state:
- Marble League runs with `update_state=False` (state/league.json is never written).
- No social/YouTube/Instagram code is imported or called.
Outputs `<out>/<name>.mp4`, a frame contact sheet `<out>/<name>_sheet.png`, and `<out>/<name>.json`.

Usage:
  python scripts/preview_samples.py --format marble_league --arena plinko_drop --seed PREVIEW-1 --out preview_out
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from common.audio import resolve_ffmpeg_binary  # noqa: E402
from common.validate import validate_video_package  # noqa: E402


def contact_sheet(video: Path, times, out_png: Path) -> None:
    ffmpeg = resolve_ffmpeg_binary()
    frames = []
    for i, t in enumerate(times):
        png = out_png.parent / f"_f{i}.png"
        subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-ss", str(t), "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=432:-1", str(png)], check=True)
        frames.append(Image.open(png).convert("RGB"))
    w, h = frames[0].size
    sheet = Image.new("RGB", (w * len(frames), h))
    for i, im in enumerate(frames):
        sheet.paste(im, (i * w, 0))
    sheet.save(out_png)
    for i in range(len(times)):
        (out_png.parent / f"_f{i}.png").unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", required=True,
                    choices=["marble_league", "bucket_drop", "tower_quake", "downhill_derby"])
    ap.add_argument("--arena", default="auto")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--out", default="preview_out")
    ap.add_argument("--finale", action="store_true",
                    help="Preview the season/series finale (in memory only; nothing is saved)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.format == "marble_league":
        from generators.marble_league import generate_marble_league_video

        meta = generate_marble_league_video(
            seed=args.seed,
            output_dir=out,
            update_state=False,  # NEVER write state/league.json from a preview
            variant_override=None if args.arena == "auto" else args.arena,
            force_grand_final=args.finale,
        )
        times = [1.0, 14.0, 30.5, 34.0, 38.0] if args.finale else [1.0, 8.0, 16.5, 20.0, 24.5]
        name = "marble_league_GRAND_FINAL" if args.finale else f"marble_league_{meta['variant']}"
    elif args.format == "bucket_drop":
        from generators.bucket_drop import generate_bucket_drop_video

        meta = generate_bucket_drop_video(seed=args.seed, output_dir=out, update_state=False,
                                          force_game=10 if args.finale else None)
        times = [1.0, 8.0, 16.0, 19.8, 22.5]
        name = "bucket_drop_FINALE" if args.finale else "bucket_drop"
    elif args.format == "tower_quake":
        from generators.tower_quake import generate_tower_quake_video

        meta = generate_tower_quake_video(seed=args.seed, output_dir=out, update_state=False,
                                          force_game=10 if args.finale else None,
                                          scenario_override=None if args.arena == "auto" else args.arena)
        times = [1.5, 9.0, 16.0, 19.6, 22.5]
        name = f"tower_defence_{meta['scenario']}" + ("_FINALE" if args.finale else "")
    else:
        from generators.downhill_derby import generate_downhill_derby_video

        meta = generate_downhill_derby_video(seed=args.seed, output_dir=out, update_state=False,
                                             force_game=10 if args.finale else None)
        times = [1.5, 8.0, 16.0, 19.4, 22.5]
        name = "downhill_derby_FINALE" if args.finale else "downhill_derby"

    video = Path(meta["video_path"])
    final_mp4 = out / f"{name}.mp4"
    video.rename(final_mp4)
    val = validate_video_package(final_mp4, meta)
    contact_sheet(final_mp4, times, out / f"{name}_sheet.png")

    summary = {k: (str(v) if isinstance(v, Path) else v) for k, v in meta.items()}
    summary["video_path"] = str(final_mp4)
    summary["validation"] = {"ok": val.ok, "errors": val.errors, "duration_sec": val.duration_sec,
                             "size_mb": val.file_size_mb, "resolution": f"{val.width}x{val.height}",
                             "has_audio": val.has_audio}
    (out / f"{name}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0 if val.ok else 1


if __name__ == "__main__":
    sys.exit(main())
