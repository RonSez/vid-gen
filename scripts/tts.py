"""Russian narration via fal text-to-speech, one audio file per scene.

  python scripts/tts.py compare [--text "..."] [--candidates eleven_v3,minimax_28_hd]
  python scripts/tts.py scenes --scenes sc_001          # Dima approves this sample first
  python scripts/tts.py scenes --chapter 1 [--yes]
  python scripts/tts.py scenes --all [--yes]

The chosen voice = `models.tts` in project/models.yaml (set `voice` there after the compare).
Each scene's audio path + duration are written back to shots.yaml; assembly times shots to it.
"""
from __future__ import annotations

import argparse
import re

import common as C
import fal_gen as G

SAMPLE = ("В начале двадцатого века эта земля была бедной и каменистой. "
          "Но сюда приезжали молодые женщины, которые верили, что знание и труд "
          "могут изменить их судьбу — и судьбу целого народа.")

MAX_CHARS = 2500


def chunks(text: str, limit=MAX_CHARS):
    sents = re.split(r"(?<=[.!?…])\s+", text.strip())
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) + 1 > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def cmd_compare(a):
    cands = C.models_cfg()["tts_candidates"]
    names = a.candidates.split(",") if a.candidates else list(cands)
    text = a.text or SAMPLE
    jobs = []
    d = C.AUDIO / "narration" / "_compare"
    for n in names:
        c = cands[n]
        v = C.next_version(d, n)
        usd = len(text) / 1000 * float(c["usd_per_1k"])
        jobs.append(G.Job(endpoint=c["endpoint"], arguments={c["text_arg"]: text, **dict(c.get("args") or {})},
                          shot_id=f"voice:{n}", kind="tts", model_name=n, units=len(text) / 1000,
                          unit="1k_chars", est_usd=usd, outputs=[d / f"{n}_v{v}.mp3"]))
    G.execute(jobs, a, what="voice comparison")


def cmd_scenes(a):
    shots = C.Shots()
    m = C.model("tts")
    if a.all:
        targets = list(shots.scenes)
    elif a.scenes:
        targets = [shots.scene(s.strip()) for s in a.scenes.split(",")]
    elif a.chapter is not None:
        targets = [s for s in shots.scenes if int(s.get("chapter", 0)) == a.chapter]
    else:
        C.die("pick --scenes, --chapter or --all")
    jobs, groups = [], {}
    d = C.AUDIO / "narration"
    for sc in targets:
        text = str(sc.get("narration_ru") or "").strip()
        if not text:
            continue
        v = C.next_version(d, sc["id"])
        parts = chunks(text)
        groups[sc["id"]] = (v, len(parts))
        for i, part in enumerate(parts, 1):
            usd = len(part) / 1000 * float(m["usd"])
            out = d / f"{sc['id']}_v{v}_part{i}.mp3"
            jobs.append(G.Job(endpoint=m["endpoint"], arguments={m.get("text_arg", "text"): part,
                              **dict(m["defaults"])}, shot_id=sc["id"], kind="tts", model_name="tts",
                              units=len(part) / 1000, unit="1k_chars", est_usd=usd, outputs=[out]))
    saved = G.execute(jobs, a, what="narration")
    if not saved:
        return
    for scid, (v, n) in groups.items():
        parts = [d / f"{scid}_v{v}_part{i}.mp3" for i in range(1, n + 1)]
        if not all(p.exists() for p in parts):
            print(f"  {scid}: some parts missing, not joined")
            continue
        final = C.safe_dest(d / f"{scid}_v{v}.mp3")
        if n == 1:
            parts[0].replace(final)
            ts = parts[0].with_suffix(".timestamps.json")
            if ts.exists():
                ts.replace(final.with_suffix(".timestamps.json"))
        else:
            lst = C.BUILD / f"concat_{scid}.txt"
            lst.parent.mkdir(parents=True, exist_ok=True)
            lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), "utf-8")
            C.ffmpeg("-f", "concat", "-safe", "0", "-i", lst, "-c:a", "libmp3lame", "-q:a", "2", final)
        sc = shots.scene(scid)
        sc["audio"] = C.rel(final)
        sc["audio_duration_s"] = round(C.probe_duration(final), 2)
        print(f"  {scid}: {sc['audio']} ({sc['audio_duration_s']}s)")
    shots.save()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true")
    common.add_argument("--yes", action="store_true")
    common.add_argument("--past-warning", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", parents=[common])
    c.add_argument("--text")
    c.add_argument("--candidates")
    s = sub.add_parser("scenes", parents=[common])
    s.add_argument("--scenes")
    s.add_argument("--chapter", type=int)
    s.add_argument("--all", action="store_true")
    a = ap.parse_args()
    cmd_compare(a) if a.cmd == "compare" else cmd_scenes(a)


if __name__ == "__main__":
    main()
