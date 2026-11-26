"""Pull frames from generated clips into one contact sheet for Claude's visual QA.

  python scripts/qa_frames.py --shots 14            # latest clip of shot 14
  python scripts/qa_frames.py --chapter 1 --status draft
  python scripts/qa_frames.py --file assets/video/shot_014_v2.mp4

Sheet = the approved still (top-left) + N evenly spaced frames, timestamps burned in.
Check: hands/faces, modern objects, text, warping, mismatch with the still.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import common as C


def sheet(clip: Path, still: Path | None, n: int = 6) -> Path:
    out_dir = C.REVIEW / "qa" / clip.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = C.probe_duration(clip)
    tiles = []
    if still and still.exists():
        t = out_dir / "00_still.jpg"
        C.ffmpeg("-i", still, "-vf", "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2",
                 "-frames:v", 1, t)
        tiles.append(t)
    font = C.ff_escape_path(C.font_path())
    for i in range(n):
        ts = dur * (i + 0.5) / n
        t = out_dir / f"{i+1:02d}_{ts:05.2f}s.jpg"
        C.ffmpeg("-ss", f"{ts:.3f}", "-i", clip, "-frames:v", 1, "-vf",
                 "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,"
                 f"drawtext=fontfile='{font}':text='{ts:.1f}s':x=10:y=10:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.6",
                 t)
        tiles.append(t)
    from PIL import Image
    cols = 4 if len(tiles) > 4 else len(tiles)
    rows = (len(tiles) + cols - 1) // cols
    canvas = Image.new("RGB", (640 * cols, 360 * rows), (0, 0, 0))
    for i, t in enumerate(tiles):
        canvas.paste(Image.open(t), ((i % cols) * 640, (i // cols) * 360))
    out = out_dir / "sheet.jpg"
    canvas.save(out, quality=88)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shots")
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--status")
    ap.add_argument("--file")
    ap.add_argument("--n", type=int, default=6)
    a = ap.parse_args()
    if a.file:
        print(C.rel(sheet(C.abspath(a.file), None, a.n)))
        return
    shots = C.Shots()
    for s in shots.select(a.shots, a.chapter, {"ai_video", "present_day"}, a.status):
        clips = C.clip_options(s["id"])
        if not clips:
            continue
        clip = clips[-1][1]
        still = C.abspath(s["ref_selected"]) if s.get("ref_selected") else None
        print(f"{s['id']}: {C.rel(sheet(clip, still, a.n))}")


if __name__ == "__main__":
    main()
