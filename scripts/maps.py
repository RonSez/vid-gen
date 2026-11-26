"""Animated maps made with code (accurate geography, Russian labels). Free and local.

  python scripts/maps.py fetch-basemap          # once: Natural Earth coast/lakes/rivers (public domain)
  python scripts/maps.py render --shots 5  |  --chapter 1  |  --all

Shot entry:
  type: map
  duration_s: 8
  map:
    bounds: [34.0, 36.2, 29.5, 33.5]     # lon_min, lon_max, lat_min, lat_max (widened to 16:9)
    places: [jaffa, jerusalem, kinneret]  # ids from project/places.yaml, appear one by one
    route: [jaffa, jerusalem]             # optional line drawn over time
    zoom_to: kinneret                     # optional slow zoom towards a place
    zoom: 2.0                             # how far in by the end
    borders: assets/graphics/borders/x.geojson   # optional period border lines (from sources!)
    title: "Земля Израиля, 1909"
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

import common as C

BASEMAP = C.GRAPHICS / "basemap"
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
LAYERS = {"land": "ne_10m_land.geojson", "lakes": "ne_10m_lakes.geojson",
          "rivers": "ne_10m_rivers_lake_centerlines.geojson"}
REGION = (30.0, 40.0, 27.0, 36.0)  # keep only the Levant to stay small

COL = {"sea": "#b9c8c4", "land": "#e9dfc6", "coast": "#5b4a36", "lake": "#b9c8c4", "river": "#6f8fa0",
       "border": "#8a2f1d", "dot": "#7a2a12", "text": "#2b2117", "route": "#8a2f1d", "title": "#2b2117"}


# --------------------------------------------------------------------------- basemap
def _clip_geom(geom):
    lo0, lo1, la0, la1 = REGION

    def inside(ring):
        return any(lo0 <= x <= lo1 and la0 <= y <= la1 for x, y in ring[:: max(1, len(ring) // 200)] + ring[-1:])

    t, cs = geom["type"], geom["coordinates"]
    if t == "Polygon":
        return [cs] if inside(cs[0]) else []
    if t == "MultiPolygon":
        return [p for p in cs if inside(p[0])]
    if t == "LineString":
        return [cs] if inside(cs) else []
    if t == "MultiLineString":
        return [l for l in cs if inside(l)]
    return []


def fetch_basemap():
    import requests
    BASEMAP.mkdir(parents=True, exist_ok=True)
    for name, fn in LAYERS.items():
        out = BASEMAP / f"{name}.json"
        if out.exists():
            print(f"have {C.rel(out)}")
            continue
        print(f"downloading {fn} ...")
        r = requests.get(NE + fn, timeout=300)
        r.raise_for_status()
        feats = r.json()["features"]
        parts = []
        for f in feats:
            if f.get("geometry"):
                parts += _clip_geom(f["geometry"])
        out.write_text(json.dumps({"layer": name, "parts": parts}), "utf-8")
        print(f"  {C.rel(out)}: {len(parts)} parts")
    (BASEMAP / "SOURCE.txt").write_text("Natural Earth 1:10m (public domain), naturalearthdata.com\n", "utf-8")


def _layer(name):
    p = BASEMAP / f"{name}.json"
    if not p.exists():
        C.die("basemap missing — run: python scripts/maps.py fetch-basemap")
    return json.loads(p.read_text("utf-8"))["parts"]


# --------------------------------------------------------------------------- render
def _places():
    return C.load_yaml(C.PROJECT / "places.yaml")["places"]


def _fit_169(lo0, lo1, la0, la1):
    """Widen bounds so they fill a 16:9 frame (equirectangular with cos(lat) correction)."""
    k = math.cos(math.radians((la0 + la1) / 2))
    w, h = (lo1 - lo0) * k, la1 - la0
    cx, cy = (lo0 + lo1) / 2, (la0 + la1) / 2
    if w / h < 16 / 9:
        w = h * 16 / 9
    else:
        h = w * 9 / 16
    return cx - w / k / 2, cx + w / k / 2, cy - h / 2, cy + h / 2


def _ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def render(shot, width=1920, height=1080, fps=24) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import Polygon

    m = shot["map"]
    places = _places()
    for pid in list(m.get("places") or []) + list(m.get("route") or []) + ([m["zoom_to"]] if m.get("zoom_to") else []):
        if pid not in places:
            C.die(f"{shot['id']}: place '{pid}' not in project/places.yaml")
    lo0, lo1, la0, la1 = _fit_169(*[float(v) for v in m["bounds"]])
    dur = float(shot.get("duration_s", 8))
    n = int(round(dur * fps))
    font = FontProperties(fname=C.font_path())
    bold = FontProperties(fname=C.font_path(bold=True))

    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(COL["sea"])
    fig.patch.set_facecolor(COL["sea"])
    k = math.cos(math.radians((la0 + la1) / 2))
    ax.set_aspect(1 / k)
    for poly in _layer("land"):
        ax.add_patch(Polygon(poly[0], closed=True, fc=COL["land"], ec=COL["coast"], lw=1.2))
    for poly in _layer("lakes"):
        ax.add_patch(Polygon(poly[0], closed=True, fc=COL["lake"], ec=COL["coast"], lw=0.8))
    for line in _layer("rivers"):
        xs, ys = zip(*line)
        ax.plot(xs, ys, color=COL["river"], lw=1.4)
    if m.get("borders"):
        gj = json.loads(C.abspath(m["borders"]).read_text("utf-8"))
        for f in gj.get("features", []):
            g = f["geometry"]
            lines = g["coordinates"] if g["type"] in ("MultiLineString", "Polygon") else [g["coordinates"]]
            for line in lines:
                xs, ys = zip(*line)
                ax.plot(xs, ys, color=COL["border"], lw=2, ls=(0, (6, 4)))
    ax.set_xticks([]), ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)

    # animated artists
    order = list(m.get("places") or [])
    dots, labels = [], []
    for pid in order:
        p = places[pid]
        d, = ax.plot([p["lon"]], [p["lat"]], "o", ms=11, color=COL["dot"], mec="white", mew=1.5, alpha=0)
        t = ax.text(p["lon"] + 0.04, p["lat"] + 0.02, p["ru"], fontproperties=bold, fontsize=26,
                    color=COL["text"], alpha=0, path_effects=_halo())
        dots.append(d), labels.append(t)
    route = [places[r] for r in m.get("route") or []]
    rline, = ax.plot([], [], color=COL["route"], lw=3.5, solid_capstyle="round")
    if m.get("title"):
        fig.text(0.04, 0.92, m["title"], fontproperties=bold, fontsize=40, color=COL["title"],
                 path_effects=_halo())
    fig.text(0.985, 0.015, "Natural Earth", fontproperties=font, fontsize=11, color="#6b6258", ha="right")

    z_to = places[m["zoom_to"]] if m.get("zoom_to") else None
    zmax = float(m.get("zoom", 1.8))
    out = C.GRAPHICS / "maps" / f"{shot['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                             "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
                             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out)],
                            stdin=subprocess.PIPE)
    appear = [0.15 + 0.6 * i / max(1, len(order)) for i in range(len(order))]
    for i in range(n):
        t = i / max(1, n - 1)
        # camera
        if z_to:
            e = _ease(t)
            z = 1 + (zmax - 1) * e
            cx = (lo0 + lo1) / 2 + (z_to["lon"] - (lo0 + lo1) / 2) * e
            cy = (la0 + la1) / 2 + (z_to["lat"] - (la0 + la1) / 2) * e
            hw, hh = (lo1 - lo0) / 2 / z, (la1 - la0) / 2 / z
            ax.set_xlim(cx - hw, cx + hw), ax.set_ylim(cy - hh, cy + hh)
        else:
            ax.set_xlim(lo0, lo1), ax.set_ylim(la0, la1)
        for j, (d, lb) in enumerate(zip(dots, labels)):
            a = _ease((t - appear[j]) / 0.08)
            d.set_alpha(a), lb.set_alpha(a)
        if len(route) > 1:
            prog = _ease((t - 0.2) / 0.6) * (len(route) - 1)
            xs, ys = [route[0]["lon"]], [route[0]["lat"]]
            for s in range(len(route) - 1):
                f = max(0.0, min(1.0, prog - s))
                if f <= 0:
                    break
                a_, b_ = route[s], route[s + 1]
                xs.append(a_["lon"] + (b_["lon"] - a_["lon"]) * f)
                ys.append(a_["lat"] + (b_["lat"] - a_["lat"]) * f)
            rline.set_data(xs, ys)
        fig.canvas.draw()
        proc.stdin.write(fig.canvas.buffer_rgba().tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        C.die(f"ffmpeg failed rendering {shot['id']}")
    plt.close(fig)
    return out


def _halo():
    from matplotlib import patheffects
    return [patheffects.withStroke(linewidth=5, foreground=COL["land"])]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch-basemap")
    r = sub.add_parser("render")
    r.add_argument("--shots")
    r.add_argument("--chapter", type=int)
    r.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.cmd == "fetch-basemap":
        fetch_basemap()
        return
    for s in C.Shots().select(a.shots, a.chapter, {"map"}):
        print(f"{s['id']}: {C.rel(render(s))}")


if __name__ == "__main__":
    main()
