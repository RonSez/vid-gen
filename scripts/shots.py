"""Edit/inspect shots.yaml without hand-editing YAML. Turns Dima's notes into one-liners.

  python scripts/shots.py status                       # counts per status, per chapter
  python scripts/shots.py list [--chapter 1]
  python scripts/shots.py show 14
  python scripts/shots.py pick 14 B                    # Dima: "014: B"  (approve still B)
  python scripts/shots.py pick-clip 14 v2              # approve clip version 2 (default: latest)
  python scripts/shots.py note 22 "warmer light, less modern"   # director's note -> next prompt
  python scripts/shots.py set 14 duration_s=7 model_tier=hero
  python scripts/shots.py validate
"""
from __future__ import annotations

import argparse
from collections import Counter

import common as C
import prompts as P


def cmd_status(shots: C.Shots):
    by_ch = {}
    for s in shots.shots:
        by_ch.setdefault(shots.chapter_of(s), Counter())[s.get("status", "planned")] += 1
    for ch in sorted(by_ch):
        c = by_ch[ch]
        print(f"chapter {ch}: " + "  ".join(f"{k}={c[k]}" for k in C.STATUSES if c[k]))
    total = sum(float(s.get("duration_s", 0)) for s in shots.shots)
    ai = sum(float(s.get("duration_s", 0)) for s in shots.shots if s.get("type") in ("ai_video", "present_day"))
    print(f"{len(shots.shots)} shots, {total/60:.1f} min total, {ai/60:.1f} min AI video")


def cmd_list(shots: C.Shots, chapter):
    for s in shots.select(chapter=chapter):
        print(f"{s['id']}  ch{shots.chapter_of(s)}  {s['type']:14s} {float(s.get('duration_s',0)):4.1f}s "
              f"{s.get('model_tier','') or '':8s} {s.get('status','planned'):12s} {str(s.get('description',''))[:60]}")


def cmd_show(shots: C.Shots, sid):
    s = shots.shot(sid)
    for k, v in s.items():
        print(f"{k}: {v}")
    if s.get("type") in ("ai_video", "present_day"):
        print("\nimage prompt:\n  " + P.image_prompt(s))
        print("video prompt:\n  " + P.video_prompt(s))
        print("refs:", ", ".join(f"{l}={C.rel(f)}" for l, f in C.ref_options(s["id"])) or "none")
        print("clips:", ", ".join(f"{v}={C.rel(f)}" for v, f in C.clip_options(s["id"])) or "none")


def cmd_pick(shots: C.Shots, sid, letter):
    s = shots.shot(sid)
    opts = dict(C.ref_options(s["id"]))
    if letter.upper() not in opts:
        C.die(f"{s['id']} has options {', '.join(opts) or 'none'}")
    s["ref_selected"] = C.rel(opts[letter.upper()])
    if C.STATUSES.index(s.get("status", "planned")) < C.STATUSES.index("ref_approved"):
        s["status"] = "ref_approved"
    shots.save()
    print(f"{s['id']}: still {letter.upper()} approved -> {s['ref_selected']}")


def cmd_pick_clip(shots: C.Shots, sid, version):
    s = shots.shot(sid)
    opts = dict(C.clip_options(s["id"]))
    if not opts:
        C.die(f"{s['id']} has no clips")
    v = version or list(opts)[-1]
    if v not in opts:
        C.die(f"{s['id']} has clips {', '.join(opts)}")
    s["clip_selected"] = C.rel(opts[v])
    s["status"] = "approved"
    shots.save()
    print(f"{s['id']}: clip {v} approved -> {s['clip_selected']}")


def cmd_note(shots: C.Shots, sid, text):
    s = shots.shot(sid)
    s["direction"] = text
    shots.save()
    print(f"{s['id']}: director's note set; next stills/video prompt:\n  {P.image_prompt(s)}")


def _parse(v: str):
    if v.lower() in ("null", "none", ""):
        return None
    for t in (int, float):
        try:
            return t(v)
        except ValueError:
            pass
    return v


def cmd_set(shots: C.Shots, sid, pairs):
    s = shots.shot(sid)
    for p in pairs:
        k, _, v = p.partition("=")
        s[k] = _parse(v)
    shots.save()
    print(f"{s['id']} updated: " + ", ".join(pairs))


def validate(shots: C.Shots) -> list[str]:
    errs = []
    ids = [s["id"] for s in shots.shots]
    for d in {i for i in ids if ids.count(i) > 1}:
        errs.append(f"duplicate shot id {d}")
    scene_ids = {sc["id"] for sc in shots.scenes}
    st = C.style_cfg()
    for s in shots.shots:
        where = s["id"]
        if s.get("scene") not in scene_ids:
            errs.append(f"{where}: unknown scene {s.get('scene')}")
        t = s.get("type")
        if t not in C.TYPES:
            errs.append(f"{where}: bad type {t}")
        if s.get("status", "planned") not in C.STATUSES:
            errs.append(f"{where}: bad status {s.get('status')}")
        if t in ("ai_video", "present_day"):
            if not s.get("description"):
                errs.append(f"{where}: missing description")
            if str(s.get("model_tier", "standard")) not in P.TIER_TO_MODEL:
                errs.append(f"{where}: bad model_tier {s.get('model_tier')}")
            if P.look_name(s) not in st["looks"]:
                errs.append(f"{where}: unknown look {P.look_name(s)}")
            for o in s.get("refs_from") or []:
                if C.normalize_shot_id(str(o)) not in ids:
                    errs.append(f"{where}: refs_from unknown shot {o}")
        if t == "archival_photo" and not s.get("source"):
            errs.append(f"{where}: archival_photo needs source:")
        if t == "map" and not s.get("map"):
            errs.append(f"{where}: map needs map: block")
        if t == "title_card" and not s.get("title"):
            errs.append(f"{where}: title_card needs title: block")
        for key in ("ref_selected", "clip_selected", "source"):
            if s.get(key) and not C.abspath(s[key]).exists():
                errs.append(f"{where}: {key} file missing: {s[key]}")
    return errs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    l = sub.add_parser("list")
    l.add_argument("--chapter", type=int)
    sh = sub.add_parser("show")
    sh.add_argument("id")
    p = sub.add_parser("pick")
    p.add_argument("id")
    p.add_argument("letter")
    pc = sub.add_parser("pick-clip")
    pc.add_argument("id")
    pc.add_argument("version", nargs="?")
    n = sub.add_parser("note")
    n.add_argument("id")
    n.add_argument("text")
    s = sub.add_parser("set")
    s.add_argument("id")
    s.add_argument("pairs", nargs="+")
    sub.add_parser("validate")
    a = ap.parse_args()
    shots = C.Shots()
    if a.cmd == "status":
        cmd_status(shots)
    elif a.cmd == "list":
        cmd_list(shots, a.chapter)
    elif a.cmd == "show":
        cmd_show(shots, a.id)
    elif a.cmd == "pick":
        cmd_pick(shots, a.id, a.letter)
    elif a.cmd == "pick-clip":
        cmd_pick_clip(shots, a.id, a.version)
    elif a.cmd == "note":
        cmd_note(shots, a.id, a.text)
    elif a.cmd == "set":
        cmd_set(shots, a.id, a.pairs)
    elif a.cmd == "validate":
        errs = validate(shots)
        print("\n".join(errs) if errs else "shots.yaml OK")
        raise SystemExit(1 if errs else 0)


if __name__ == "__main__":
    main()
