"""Title cards and chapter titles (Russian-capable typography), rendered locally.

Shot entry:
  type: title_card
  duration_s: 5
  title: {text: "Глава первая", subtitle: "Ханна Майзель-Шохат", background: assets/refs/.../x.png (optional)}

  python scripts/titles.py --shots 1  |  --chapter 1  |  --all
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import common as C

INK = (238, 230, 216)
BG = (20, 17, 14)


def out_path(shot) -> Path:
    return C.GRAPHICS / "titles" / f"{shot['id']}.mp4"


def _wrap(draw, text, font, max_w):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def card_image(shot, width=1920, height=1080) -> Path:
    t = shot.get("title") or {}
    bg_path = t.get("background")
    if bg_path:
        img = Image.open(C.abspath(bg_path)).convert("RGB").resize((width, height))
        img = img.filter(ImageFilter.GaussianBlur(6))
        img = Image.blend(img, Image.new("RGB", img.size, BG), 0.55)
    else:
        img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(C.font_path(bold=False), int(height * 0.075))
    small = ImageFont.truetype(C.font_path(bold=False), int(height * 0.038))
    lines = _wrap(d, t.get("text", ""), big, width * 0.8)
    sub = _wrap(d, t.get("subtitle", ""), small, width * 0.7) if t.get("subtitle") else []
    lh, sh = int(height * 0.1), int(height * 0.055)
    total = len(lines) * lh + (len(sub) * sh + int(height * 0.04) if sub else 0)
    y = (height - total) / 2
    for ln in lines:
        d.text((width / 2, y), ln, font=big, fill=INK, anchor="ma")
        y += lh
    if sub:
        y += int(height * 0.02)
        d.line([(width * 0.44, y), (width * 0.56, y)], fill=INK, width=2)
        y += int(height * 0.02)
        for ln in sub:
            d.text((width / 2, y), ln, font=small, fill=(INK[0] - 40, INK[1] - 40, INK[2] - 40), anchor="ma")
            y += sh
    out = C.GRAPHICS / "titles" / f"{shot['id']}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def render(shot, width=1920, height=1080, fps=24) -> Path:
    png = card_image(shot, width, height)
    dur = float(shot.get("duration_s", 4))
    fade = min(0.8, dur / 4)
    out = out_path(shot)
    # slight slow push-in keeps a static card alive
    frames = int(round(dur * fps))
    C.ffmpeg("-i", png, "-filter_complex",
             f"scale={width*2}:{height*2},zoompan=z='1+0.03*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
             f"d={frames}:s={width}x{height}:fps={fps},fade=t=in:st=0:d={fade},"
             f"fade=t=out:st={dur - fade}:d={fade},format=yuv420p",
             "-frames:v", frames, "-c:v", "libx264", "-crf", "18", out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shots")
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    for s in C.Shots().select(a.shots, a.chapter, {"title_card"}):
        print(f"{s['id']}: {C.rel(render(s))}")


if __name__ == "__main__":
    main()
