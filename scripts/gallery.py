"""Static HTML review pages for Dima (open review/index.html in a browser).

  python scripts/gallery.py refs --chapter 1     # stills: pick with "014: B"
  python scripts/gallery.py clips --chapter 1    # generated clips next to their still
  python scripts/gallery.py looks                # Phase 2 look tests
  python scripts/gallery.py voices               # Russian voice comparison
  python scripts/gallery.py index                # landing page with budget + links
"""
from __future__ import annotations

import argparse
import html
import os
from pathlib import Path

import common as C

CSS = """
:root{--bg:#f6f3ee;--fg:#1f1b16;--muted:#6b6258;--card:#fff;--line:#e2dcd2;--accent:#a4551f;--ok:#2f7d4f}
@media (prefers-color-scheme:dark){:root{--bg:#16130f;--fg:#efe9e0;--muted:#a59b8e;--card:#221d18;--line:#3a332b;--accent:#e08a4a;--ok:#5fbf86}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 Georgia,serif}
main{max-width:1400px;margin:0 auto;padding:24px 16px 80px}h1{font-size:28px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 24px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 20px}
.head{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}.id{font:600 18px ui-monospace,Consolas,monospace}
.tag{font:12px ui-monospace,Consolas,monospace;padding:2px 8px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
.narr{margin:8px 0 2px;font-size:17px}.en{color:var(--muted);font-style:italic;margin:0 0 6px}.desc{color:var(--muted);font-size:14px;margin:0 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
figure{margin:0;position:relative}figure img,figure video{width:100%;border-radius:6px;display:block;background:#000}
.badge{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.72);color:#fff;font:700 20px ui-monospace,Consolas,monospace;padding:2px 10px;border-radius:6px}
figure.sel{outline:4px solid var(--ok);outline-offset:2px;border-radius:6px}figcaption{font-size:12px;color:var(--muted);margin-top:4px;word-break:break-all}
a{color:var(--accent)}.how{background:var(--card);border-left:4px solid var(--accent);padding:10px 14px;margin:0 0 24px}
table{border-collapse:collapse}td{padding:4px 12px 4px 0}
"""


def _r(p: Path) -> str:
    return Path(os.path.relpath(C.abspath(p), C.REVIEW)).as_posix()


def _e(s) -> str:
    return html.escape(str(s or ""))


def page(title: str, sub: str, body: str, out: Path):
    C.REVIEW.mkdir(parents=True, exist_ok=True)
    out.write_text(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<style>{CSS}</style></head><body><main><p><a href="index.html">← all review pages</a></p>
<h1>{_e(title)}</h1><p class="sub">{sub}</p>{body}</main></body></html>""", "utf-8")
    print(f"wrote {C.rel(out)}")
    return out


def _shot_head(shots, s):
    sc = shots.scene_of(s)
    narr = s.get("narration") or sc.get("narration_ru") or ""
    en = s.get("narration_en") or (sc.get("narration_en") if not s.get("narration") else "")
    return (f'<div class="head"><span class="id">{_e(s["id"])}</span>'
            f'<span class="tag">{_e(s.get("type"))}</span><span class="tag">{_e(s.get("model_tier",""))}</span>'
            f'<span class="tag">{float(s.get("duration_s",0)):g}s</span><span class="tag">{_e(s.get("status"))}</span></div>'
            f'<p class="narr">{_e(narr)[:600]}</p>'
            + (f'<p class="en">{_e(en)[:600]}</p>' if en else "")
            + f'<p class="desc">{_e(s.get("description"))}'
            + (f' · <b>note:</b> {_e(s["direction"])}' if s.get("direction") else "") + "</p>")


def refs(chapter):
    shots = C.Shots()
    cards = []
    for s in shots.select(chapter=chapter, types={"ai_video", "present_day"}):
        opts = C.ref_options(s["id"])
        sel = s.get("ref_selected")
        figs = "".join(
            f'<figure class="{"sel" if sel == C.rel(f) else ""}"><a href="{_r(f)}" target="_blank">'
            f'<img loading="lazy" src="{_r(f)}" alt="{_e(s["id"])} option {l}"></a>'
            f'<span class="badge">{l}</span><figcaption>{_e(f.name)}</figcaption></figure>'
            for l, f in opts) or '<p class="desc">no stills yet</p>'
        cards.append(f'<section class="card">{_shot_head(shots, s)}<div class="grid">{figs}</div></section>')
    how = ('<div class="how">Reply with one line per shot, e.g. <b>014: B</b> · <b>015: redo — less modern</b> · '
           '<b>016: C, warmer</b>. The green frame shows the current pick.</div>')
    return page(f"Stills — chapter {chapter}", f"{len(cards)} shots · {_spend()}", how + "".join(cards),
                C.REVIEW / f"refs_ch{chapter}.html")


def clips(chapter):
    shots = C.Shots()
    cards = []
    for s in shots.select(chapter=chapter, types={"ai_video", "present_day"}):
        figs = []
        if s.get("ref_selected"):
            figs.append(f'<figure><img loading="lazy" src="{_r(s["ref_selected"])}"><span class="badge">still</span></figure>')
        for v, f in C.clip_options(s["id"]):
            sel = "sel" if s.get("clip_selected") == C.rel(f) else ""
            qa = C.REVIEW / "qa" / f.stem / "sheet.jpg"
            qa_link = f' · <a href="{_r(qa)}" target="_blank">QA frames</a>' if qa.exists() else ""
            figs.append(f'<figure class="{sel}"><video controls preload="metadata" src="{_r(f)}"></video>'
                        f'<span class="badge">{v}</span><figcaption>{_e(f.name)}{qa_link}</figcaption></figure>')
        cards.append(f'<section class="card">{_shot_head(shots, s)}<div class="grid">{"".join(figs)}</div></section>')
    how = ('<div class="how">Watch for motion: smoothness, warping, anything that looks wrong. '
           'Reply e.g. <b>014: v2 ok</b> · <b>015: redo, slower camera</b>.</div>')
    return page(f"Clips — chapter {chapter}", f"{len(cards)} shots · {_spend()}", how + "".join(cards),
                C.REVIEW / f"clips_ch{chapter}.html")


def looks():
    d = C.REFS / "_looks"
    files = sorted(d.glob("*.png")) if d.exists() else []
    groups = {}
    for f in files:
        groups.setdefault(f.name.split("_v")[0].replace("look_", ""), []).append(f)
    body = "".join(
        f'<section class="card"><div class="head"><span class="id">{_e(name)}</span></div><div class="grid">'
        + "".join(f'<figure><a href="{_r(f)}" target="_blank"><img src="{_r(f)}"></a>'
                  f'<figcaption>{_e(f.name)}</figcaption></figure>' for f in fs)
        + "</div></section>" for name, fs in groups.items())
    return page("Look tests", "Pick the look for the film (Phase 2)", body or "<p>none yet</p>",
                C.REVIEW / "looks.html")


def voices():
    d = C.AUDIO / "narration" / "_compare"
    files = sorted(d.glob("*.mp3")) if d.exists() else []
    body = "".join(f'<section class="card"><div class="head"><span class="id">{_e(f.stem)}</span></div>'
                   f'<audio controls preload="none" src="{_r(f)}" style="width:100%"></audio></section>'
                   for f in files)
    return page("Russian voice comparison", "Same paragraph, different voices. Reply with the one you like.",
                body or "<p>none yet</p>", C.REVIEW / "voices.html")


def _spend():
    cfg = C.models_cfg()["budget"]
    return f"spent €{C.spent_eur():.2f} of €{cfg['target_eur']} (cap €{cfg['cap_eur']})"


def index():
    links = sorted(p for p in C.REVIEW.glob("*.html") if p.name != "index.html")
    cuts = sorted(C.REVIEW.glob("roughcut_v*.mp4"))
    body = '<section class="card"><table>' + "".join(
        f'<tr><td><a href="{p.name}">{_e(p.stem)}</a></td></tr>' for p in links) + "</table></section>"
    if cuts:
        last = cuts[-1]
        body += (f'<section class="card"><div class="head"><span class="id">{_e(last.name)}</span></div>'
                 f'<video controls preload="metadata" src="{last.name}" style="width:100%"></video>'
                 f'<p class="desc">Send notes with timestamps, e.g. “4:32 too long”.</p></section>')
    return page("Three Women — review", _spend(), body, C.REVIEW / "index.html")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("refs", "clips"):
        p = sub.add_parser(n)
        p.add_argument("--chapter", type=int, required=True)
    for n in ("looks", "voices", "index"):
        sub.add_parser(n)
    a = ap.parse_args()
    {"refs": lambda: refs(a.chapter), "clips": lambda: clips(a.chapter), "looks": looks,
     "voices": voices, "index": index}[a.cmd]()
    if a.cmd != "index":
        index()


if __name__ == "__main__":
    main()
