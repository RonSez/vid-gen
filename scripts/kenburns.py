"""Slow zoom/pan ("Ken Burns") over archival photos. Free, local, reproducible.

Shot entry:
  type: archival_photo
  source: assets/archival/meisel_1911_kinneret.jpg
  duration_s: 7
  kenburns: {start: [1.0, 0.5, 0.5], end: [1.15, 0.45, 0.35]}   # [zoom, centre_x, centre_y] 0..1
  fit: cover | contain        # contain = whole photo on a blurred backdrop (portrait photos)

  python scripts/kenburns.py --shots 20,21     |  --chapter 1  |  --all
"""
from __future__ import annotations

import argparse
from pathlib import Path

import common as C


def out_path(shot) -> Path:
    return C.VIDEO / "kenburns" / f"{shot['id']}_kb.mp4"


def render(shot, width=1920, height=1080, fps=24) -> Path:
    src = C.abspath(shot["source"])
    if not src.exists():
        C.die(f"{shot['id']}: missing {C.rel(src)}")
    dur = float(shot.get("duration_s", 6))
    kb = shot.get("kenburns") or {}
    z0, x0, y0 = [float(v) for v in kb.get("start", [1.0, 0.5, 0.5])]
    z1, x1, y1 = [float(v) for v in kb.get("end", [1.12, 0.5, 0.45])]
    frames = max(2, int(round(dur * fps)))
    # Work on a 4x canvas so the zoom is smooth (zoompan jitters at native size).
    W, H = width * 4, height * 4
    p = f"(on/{frames - 1})"
    ease = f"(0.5-0.5*cos(PI*{p}))"
    z = f"({z0}+({z1}-{z0})*{ease})"
    cx = f"({x0}+({x1}-{x0})*{ease})"
    cy = f"({y0}+({y1}-{y0})*{ease})"
    if shot.get("fit", "cover") == "contain":
        base = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:2,"
                f"eq=brightness=-0.15[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2")
    else:
        base = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    vf = (f"{base},zoompan=z='{z}':x='max(0,min(iw-iw/zoom,iw*{cx}-iw/zoom/2))':y='max(0,min(ih-ih/zoom,ih*{cy}-ih/zoom/2))':"
          f"d={frames}:s={width}x{height}:fps={fps},format=yuv420p")
    out = out_path(shot)
    out.parent.mkdir(parents=True, exist_ok=True)
    C.ffmpeg("-i", src, "-filter_complex", vf, "-frames:v", frames,
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shots")
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    shots = C.Shots()
    for s in shots.select(a.shots, a.chapter, {"archival_photo"}):
        print(f"{s['id']}: {C.rel(render(s))}")


if __name__ == "__main__":
    main()
