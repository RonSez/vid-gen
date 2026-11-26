"""Builds the film from shots.yaml with ffmpeg. Free, local.

  python scripts/assemble.py rough              # review/roughcut_vN.mp4 (960x540, shot labels)
  python scripts/assemble.py final [--burn en]  # output/final_1080p_vN.mp4 + subtitles + FCPXML
  python scripts/assemble.py where 4:32         # which shot is at 4:32 in the latest rough cut

Timing: each scene lasts at least as long as its narration (+ lead/tail); shots in the scene are
stretched proportionally to cover it. Missing visuals become placeholders, so a cut always renders.
Audio: narration per scene, music ducked under the voice, optional ambience, loudness -16 LUFS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape, quoteattr

import common as C
import kenburns
import maps
import titles

RES = {"rough": (960, 540, "veryfast", 26), "final": (1920, 1080, "slow", 16)}


# --------------------------------------------------------------------------- timeline
def build_timeline(shots: C.Shots):
    film = shots.film
    lead = float(film.get("narration_lead_s", 0.6))
    tail = float(film.get("narration_tail_s", 0.9))
    T = float(film.get("transition_s", 0.6))
    default_tr = film.get("default_transition", "dissolve")
    t = 0.0
    scenes = []
    for sc in shots.scenes:
        ss = shots.shots_in_scene(sc["id"])
        if not ss:
            continue
        base = sum(float(s.get("duration_s", 5)) for s in ss)
        narr = float(sc.get("audio_duration_s") or 0)
        need = narr + lead + tail if narr else 0
        scale = need / base if need > base else 1.0
        items = []
        for i, s in enumerate(ss):
            nxt = ss[i + 1] if i + 1 < len(ss) else None
            tr = (nxt.get("transition") or default_tr) if nxt else "cut"
            items.append({"shot": s, "dur": round(float(s.get("duration_s", 5)) * scale, 3),
                          "next_transition": tr if tr in ("dissolve", "cut") else "cut"})
        dur = sum(it["dur"] for it in items)
        scenes.append({"scene": sc, "start": t, "dur": dur, "items": items, "T": T, "lead": lead})
        st = t
        for it in items:
            it["start"] = st
            st += it["dur"]
        t += dur
    return scenes, t


def source_for(shot, width, height, rerender=False):
    """(path, kind) for a shot's visual; kind in clip|still|placeholder."""
    t = shot.get("type")
    if t in ("ai_video", "present_day"):
        for key in ("clip_upscaled", "clip_selected"):
            if shot.get(key) and C.abspath(shot[key]).exists():
                return C.abspath(shot[key]), "clip"
        clips = C.clip_options(shot["id"])
        if clips:
            return clips[-1][1], "clip_unapproved"
        if shot.get("ref_selected"):
            return C.abspath(shot["ref_selected"]), "still"
        opts = C.ref_options(shot["id"])
        if opts:
            return opts[0][1], "still"
        return None, "placeholder"
    if t == "archival_photo":
        out = kenburns.out_path(shot)
        if not C.abspath(shot["source"]).exists():
            return None, "placeholder"
        if rerender or not out.exists() or out.stat().st_mtime < C.abspath(shot["source"]).stat().st_mtime:
            kenburns.render(shot)
        return out, "clip"
    if t == "map":
        out = C.GRAPHICS / "maps" / f"{shot['id']}.mp4"
        if rerender or not out.exists():
            try:
                maps.render(shot)
            except SystemExit:
                return None, "placeholder"
        return out, "clip"
    if t == "title_card":
        out = titles.out_path(shot)
        if rerender or not out.exists():
            titles.render(shot)
        return out, "clip"
    return None, "placeholder"


# --------------------------------------------------------------------------- video
def segment(item, mode, film, rerender=False) -> Path:
    W, H, preset, crf = RES[mode]
    fps = int(film.get("fps", 24))
    shot = item["shot"]
    length = item["dur"] + (item["T"] if item["next_transition"] == "dissolve" else 0)
    src, kind = source_for(shot, 1920, 1080, rerender)
    label = mode == "rough"
    grade = film.get("grade_filter") if shot.get("type") in ("ai_video", "present_day") else None
    key = json.dumps([C.rel(src) if src else None, src.stat().st_mtime if src else 0, length, W, H,
                      label, grade, shot.get("trim_start_s", 0), shot["id"], kind], sort_keys=True)
    h = hashlib.sha1(key.encode()).hexdigest()[:10]
    out = C.BUILD / "segments" / mode / f"{shot['id']}_{h}.mp4"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    font = C.ff_escape_path(C.font_path())
    draw = ""
    if label:
        tag = shot["id"] + ("" if kind == "clip" else f" [{kind.upper()}]")
        draw = (f",drawtext=fontfile='{font}':text='{tag}':x=12:y=h-th-12:fontsize={H//28}:"
                f"fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=6")
    if grade:
        fit += f",{grade}"
    if kind == "placeholder" or src is None:
        msg = re.sub(r"[^\w .,-]", " ", str(shot.get("description") or shot.get("type")))[:70]
        C.ffmpeg("-f", "lavfi", "-i", f"color=c=0x1c1915:s={W}x{H}:r={fps}:d={length}",
                 "-vf", f"drawtext=fontfile='{font}':text='{shot['id']} — {msg}':x=(w-tw)/2:y=(h-th)/2:"
                        f"fontsize={H//30}:fontcolor=0xd8cfc2{draw}",
                 "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p", out)
        return out
    if kind == "still":  # slow push-in over a still while the clip doesn't exist yet
        frames = int(round(length * fps))
        C.ffmpeg("-i", src, "-filter_complex",
                 f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
                 f"zoompan=z='1+0.06*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:"
                 f"s={W}x{H}:fps={fps},setsar=1{draw},format=yuv420p",
                 "-frames:v", frames, "-c:v", "libx264", "-preset", preset, "-crf", crf, out)
        return out
    sdur = C.probe_duration(src) - float(shot.get("trim_start_s", 0))
    speed = ""
    if sdur < length:
        ratio = length / sdur
        if ratio <= 1.25:   # gentle slow-motion is invisible on calm documentary shots
            speed = f",setpts=PTS*{ratio:.5f}"
        else:               # otherwise hold the last frame
            speed = f",tpad=stop_mode=clone:stop_duration={length - sdur:.3f}"
    C.ffmpeg("-ss", float(shot.get("trim_start_s", 0)), "-i", src, "-an", "-vf",
             f"{fit}{speed},fps={fps}{draw},format=yuv420p", "-t", f"{length:.3f}",
             "-c:v", "libx264", "-preset", preset, "-crf", crf, out)
    return out


def render_scene(sc, mode, film, rerender, chapter_edges) -> Path:
    W, H, preset, crf = RES[mode]
    T = sc["T"]
    segs = [segment(dict(it, T=T), mode, film, rerender) for it in sc["items"]]
    key = hashlib.sha1(json.dumps([str(s) for s in segs] + [chapter_edges, T]).encode()).hexdigest()[:10]
    out = C.BUILD / "scenes" / mode / f"{sc['scene']['id']}_{key}.mp4"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    fade = float(film.get("chapter_fade_s", 1.0))
    parts, inputs = [], []
    for i, s in enumerate(segs):
        inputs += ["-i", s]
    cur, L = "[0:v]", sc["items"][0]["dur"] + (T if sc["items"][0]["next_transition"] == "dissolve" else 0)
    for i in range(1, len(segs)):
        prev_tr = sc["items"][i - 1]["next_transition"]
        it = sc["items"][i]
        ln = it["dur"] + (T if it["next_transition"] == "dissolve" else 0)
        lab = f"[x{i}]"
        if prev_tr == "dissolve":
            parts.append(f"{cur}[{i}:v]xfade=transition=fade:duration={T}:offset={L - T:.3f}{lab}")
            L = L + ln - T
        else:
            parts.append(f"{cur}[{i}:v]concat=n=2:v=1:a=0{lab}")
            L = L + ln
        cur = lab
    post = []
    if chapter_edges[0]:
        post.append(f"fade=t=in:st=0:d={fade}")
    if chapter_edges[1]:
        post.append(f"fade=t=out:st={sc['dur'] - fade:.3f}:d={fade}")
    post.append(f"trim=duration={sc['dur']:.3f},format=yuv420p")
    parts.append(f"{cur}{','.join(post)}[out]")
    script = out.with_suffix(".filter.txt")
    script.write_text(";\n".join(parts), "utf-8")
    C.ffmpeg(*inputs, "-/filter_complex", script, "-map", "[out]", "-c:v", "libx264",
             "-preset", preset, "-crf", crf, "-r", int(film.get("fps", 24)), out)
    return out


def join(paths, out: Path):
    lst = out.with_suffix(".concat.txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in paths), "utf-8")
    C.ffmpeg("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out)
    return out


# --------------------------------------------------------------------------- audio
def mix_audio(shots: C.Shots, scenes, total: float, out: Path):
    film = shots.film
    inputs, chains, narr, music, amb = [], [], [], [], []

    def add(path):
        inputs.extend(["-i", C.abspath(path)])
        return len(inputs) // 2 - 1

    norm = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    for sc in scenes:
        s = sc["scene"]
        if s.get("audio") and C.abspath(s["audio"]).exists():
            k = add(s["audio"])
            ms = int((sc["start"] + sc["lead"]) * 1000)
            vol = float(film.get("narration_volume", 1.0))
            chains.append(f"[{k}:a]{norm},volume={vol},adelay={ms}|{ms}[n{k}]")
            narr.append(f"[n{k}]")
        a = s.get("ambience")
        if a and C.abspath(a["file"]).exists():
            k = add(a["file"])
            ms = int(sc["start"] * 1000)
            d = sc["dur"]
            chains.append(f"[{k}:a]{norm},aloop=loop=-1:size=2147483647,atrim=0:{d:.3f},"
                          f"volume={float(a.get('volume', 0.12))},afade=t=in:d=1,afade=t=out:st={max(0, d-1):.3f}:d=1,"
                          f"adelay={ms}|{ms}[b{k}]")
            amb.append(f"[b{k}]")
    for mtrack in film.get("music") or []:
        if not C.abspath(mtrack["file"]).exists():
            print(f"  music missing: {mtrack['file']}")
            continue
        k = add(mtrack["file"])
        st = float(mtrack.get("start_s", 0))
        end = float(mtrack.get("end_s", total))
        d = max(0.1, end - st)
        ms = int(st * 1000)
        fi, fo = float(mtrack.get("fade_in_s", 2)), float(mtrack.get("fade_out_s", 3))
        chains.append(f"[{k}:a]{norm},atrim=0:{d:.3f},volume={float(mtrack.get('volume', 0.3))},"
                      f"afade=t=in:d={fi},afade=t=out:st={max(0, d - fo):.3f}:d={fo},adelay={ms}|{ms}[m{k}]")
        music.append(f"[m{k}]")
    if not (narr or music or amb):
        C.ffmpeg("-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-t", f"{total:.3f}", out)
        return out

    def mixed(labels, name):
        if len(labels) == 1:
            chains.append(f"{labels[0]}apad=whole_dur={total:.3f}[{name}]")
        else:
            chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest,"
                          f"apad=whole_dur={total:.3f}[{name}]")
        return f"[{name}]"

    final = []
    N = mixed(narr, "narr") if narr else None
    M = mixed(music, "mus") if music else None
    B = mixed(amb, "amb") if amb else None
    if N and M:
        chains.append(f"{N}asplit=2[nv][nk]")
        chains.append(f"{M}[nk]sidechaincompress=threshold=0.02:ratio=10:attack=30:release=600[duck]")
        final += ["[nv]", "[duck]"]
    else:
        final += [x for x in (N, M) if x]
    if B:
        final.append(B)
    head = f"{''.join(final)}amix=inputs={len(final)}:normalize=0:duration=longest," if len(final) > 1 \
        else f"{final[0]}"
    chains.append(f"{head}atrim=0:{total:.3f}[aout]")
    script = out.with_suffix(".filter.txt")
    script.write_text(";\n".join(chains), "utf-8")
    raw = out.with_name(out.stem + "_raw.wav")
    C.ffmpeg(*inputs, "-/filter_complex", script, "-map", "[aout]", "-c:a", "pcm_f32le", raw)
    # two-pass loudness normalisation to -16 LUFS (single pass undershoots on short/dynamic mixes)
    target = "I=-16:TP=-1.5:LRA=11"
    p = C.subprocess.run(["ffmpeg", "-hide_banner", "-i", str(raw), "-af",
                          f"loudnorm={target}:print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = json.loads(p.stderr[p.stderr.rindex("{"):p.stderr.rindex("}") + 1])
    C.ffmpeg("-i", raw, "-af",
             f"loudnorm={target}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
             f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
             f"linear=true,aresample=48000", "-c:a", "pcm_s16le", out)
    return out


# --------------------------------------------------------------------------- subtitles
def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?…])\s+", str(text or "").strip()) if s]


def _ts(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(scenes, lang: str, out: Path):
    cues = []
    for sc in scenes:
        s = sc["scene"]
        text = s.get(f"narration_{lang}")
        dur = float(s.get("audio_duration_s") or 0)
        if not text:
            continue
        if not dur:
            dur = max(1.0, len(str(s.get("narration_ru") or text)) / 14.0)
        sents = _sentences(text)
        total = sum(len(x) for x in sents) or 1
        t = sc["start"] + sc["lead"]
        for x in sents:
            d = dur * len(x) / total
            cues.append((t, t + d - 0.05, x))
            t += d
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{x}\n\n" for i, (a, b, x) in enumerate(cues, 1)),
                   "utf-8")
    return out if cues else None


# --------------------------------------------------------------------------- FCPXML (DaVinci Resolve)
def _rt(seconds, fps=24):
    return f"{int(round(seconds * fps))}/{fps}s"


def write_fcpxml(scenes, total, film, out: Path, title: str):
    fps = int(film.get("fps", 24))
    res, clips, aid = [], [], 0

    def asset(path, dur, video=True):
        nonlocal aid
        aid += 1
        uri = Path(path).resolve().as_uri()
        attrs = (f'hasVideo="1" format="r1" hasAudio="0"' if video
                 else 'hasVideo="0" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000"')
        res.append(f'<asset id="a{aid}" name={quoteattr(Path(path).stem)} start="0s" duration="{_rt(dur, fps)}" {attrs}>'
                   f'<media-rep kind="original-media" src={quoteattr(uri)}/></asset>')
        return f"a{aid}"

    first = True
    for sc in scenes:
        for it in sc["items"]:
            seg = it.get("segment")
            if not seg:
                continue
            ref = asset(seg, it["dur"] + 1)
            inner = ""
            if first:
                for mt in film.get("music") or []:
                    if C.abspath(mt["file"]).exists():
                        d = C.probe_duration(C.abspath(mt["file"]))
                        mref = asset(C.abspath(mt["file"]), d, video=False)
                        inner += (f'<asset-clip ref="{mref}" lane="-2" offset="{_rt(float(mt.get("start_s", 0)), fps)}" '
                                  f'duration="{_rt(min(d, total), fps)}" start="0s" name="music"/>')
                first = False
            s = sc["scene"]
            if it is sc["items"][0] and s.get("audio") and C.abspath(s["audio"]).exists():
                d = float(s.get("audio_duration_s") or C.probe_duration(C.abspath(s["audio"])))
                nref = asset(C.abspath(s["audio"]), d, video=False)
                inner += (f'<asset-clip ref="{nref}" lane="-1" offset="{_rt(it["start"] + sc["lead"], fps)}" '
                          f'duration="{_rt(d, fps)}" start="0s" name={quoteattr(s["id"] + " narration")}/>')
            clips.append(f'<asset-clip ref="{ref}" offset="{_rt(it["start"], fps)}" name={quoteattr(it["shot"]["id"])} '
                         f'duration="{_rt(it["dur"], fps)}" start="0s">{inner}</asset-clip>')
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">\n<resources>\n'
           f'<format id="r1" name="FFVideoFormat1080p{fps}" frameDuration="1/{fps}s" width="1920" height="1080"/>\n'
           + "\n".join(res) + "\n</resources>\n"
           f'<library><event name={quoteattr(title)}><project name={quoteattr(out.stem)}>'
           f'<sequence format="r1" duration="{_rt(total, fps)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">'
           f"<spine>\n" + "\n".join(clips) + "\n</spine></sequence></project></event></library>\n</fcpxml>\n")
    out.write_text(xml, "utf-8")
    return out


# --------------------------------------------------------------------------- commands
def build(mode: str, burn: str | None, rerender: bool):
    shots = C.Shots()
    film = shots.film
    scenes, total = build_timeline(shots)
    if not scenes:
        C.die("no scenes with shots")
    print(f"timeline: {len(scenes)} scenes, {sum(len(s['items']) for s in scenes)} shots, {total/60:.2f} min")
    # chapter edges -> fade from/to black
    rendered = []
    for i, sc in enumerate(scenes):
        ch = int(sc["scene"].get("chapter", 0))
        prev_ch = int(scenes[i - 1]["scene"].get("chapter", 0)) if i else None
        next_ch = int(scenes[i + 1]["scene"].get("chapter", 0)) if i + 1 < len(scenes) else None
        edges = [prev_ch != ch, next_ch != ch]
        for it in sc["items"]:
            it["T"] = sc["T"]
        print(f"  scene {sc['scene']['id']} ({sc['dur']:.1f}s)")
        rendered.append(render_scene(sc, mode, film, rerender, edges))
        for it in sc["items"]:
            it["segment"] = segment(dict(it, T=sc["T"]), mode, film)
    vid = join(rendered, C.BUILD / f"video_{mode}.mp4")
    wav = mix_audio(shots, scenes, total, C.BUILD / f"mix_{mode}.wav")

    dest_dir = C.REVIEW if mode == "rough" else C.OUTPUT
    stem = "roughcut" if mode == "rough" else "final_1080p"
    v = C.next_version(dest_dir, stem)
    out = dest_dir / f"{stem}_v{v}.mp4"
    subs = {}
    for lang in ("ru", "en"):
        p = write_srt(scenes, lang, dest_dir / f"{stem}_v{v}.{lang}.srt")
        if p:
            subs[lang] = p
    vf = []
    if burn:
        if burn not in subs:
            C.die(f"no narration_{burn} text to burn")
        style = "FontName=Constantia,FontSize=20,Outline=1,Shadow=0,MarginV=36"
        vf = ["-vf", f"subtitles='{C.ff_escape_path(subs[burn])}':force_style='{style}'"]
    W, H, preset, crf = RES[mode]
    if vf:
        C.ffmpeg("-i", vid, "-i", wav, *vf, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", preset,
                 "-crf", crf, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-shortest",
                 "-movflags", "+faststart", out)
    else:
        C.ffmpeg("-i", vid, "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
                 "-shortest", "-movflags", "+faststart", out)
    # timecode map for Dima's notes
    tc = [{"shot": it["shot"]["id"], "scene": sc["scene"]["id"], "start": round(it["start"], 2),
           "end": round(it["start"] + it["dur"], 2)} for sc in scenes for it in sc["items"]]
    out.with_suffix(".timecodes.json").write_text(json.dumps(tc, indent=1), "utf-8")
    fcp = write_fcpxml(scenes, total, film, dest_dir / f"{stem}_v{v}.fcpxml", film.get("title", "film"))
    placeholders = [it["shot"]["id"] for sc in scenes for it in sc["items"]
                    if source_for(it["shot"], 1920, 1080)[1] != "clip"]
    print(f"\nwrote {C.rel(out)}  ({C.probe_duration(out)/60:.2f} min)")
    print(f"      {', '.join(C.rel(p) for p in subs.values())}")
    print(f"      {C.rel(fcp)}  (DaVinci Resolve: File > Import > Timeline)")
    if placeholders:
        print(f"      {len(placeholders)} shots not final yet (still/placeholder/unapproved): "
              + ", ".join(placeholders[:20]) + (" …" if len(placeholders) > 20 else ""))
    return out


def where(stamp: str, version: int | None):
    parts = [float(x) for x in stamp.split(":")]
    t = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    files = sorted(C.REVIEW.glob("roughcut_v*.timecodes.json"), key=lambda p: int(re.search(r"_v(\d+)", p.name).group(1)))
    if version:
        files = [f for f in files if f.name.startswith(f"roughcut_v{version}.")]
    if not files:
        C.die("no rough cut yet")
    tc = json.loads(files[-1].read_text("utf-8"))
    for e in tc:
        if e["start"] <= t < e["end"]:
            print(f"{stamp} in {files[-1].name.split('.')[0]} = {e['shot']} (scene {e['scene']}, "
                  f"{e['start']:.1f}–{e['end']:.1f}s)")
            return e["shot"]
    print(f"{stamp} is past the end")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rough")
    r.add_argument("--rerender", action="store_true", help="re-render maps/titles/ken burns")
    f = sub.add_parser("final")
    f.add_argument("--burn", choices=["ru", "en"])
    f.add_argument("--rerender", action="store_true")
    w = sub.add_parser("where")
    w.add_argument("stamp")
    w.add_argument("--version", type=int)
    a = ap.parse_args()
    if a.cmd == "where":
        where(a.stamp, a.version)
    else:
        build(a.cmd, getattr(a, "burn", None), a.rerender)


if __name__ == "__main__":
    main()
