"""Paid generation jobs on fal.ai, driven by shots.yaml. Every job is estimated, guarded by the
budget, logged (request id saved BEFORE waiting) and saved as a new version — never overwritten.

  python scripts/fal_gen.py stills --chapter 1 [--n 3] [--model still_draft] [--dry-run] [--yes]
  python scripts/fal_gen.py stills --shots 14,15
  python scripts/fal_gen.py video  --chapter 1 [--tier draft|standard|hero] [--yes]
  python scripts/fal_gen.py looks  --description "women hoeing a field by a lake" [--looks past,present]
  python scripts/fal_gen.py music  --prompt "..." --seconds 180 [--name main]
  python scripts/fal_gen.py upscale --shots 14
  python scripts/fal_gen.py raw    --model still --prompt "..."
  python scripts/fal_gen.py resume                  # collect unfinished jobs (no new charge)

Global: --dry-run (no network, just prompts + cost), --yes (Dima approved the estimate),
--past-warning (Dima approved going past 80% of target). FAL_MOCK=1 = free placeholder outputs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import budget
import common as C
import fal_backend as F
import prompts as P

ORDER = {s: i for i, s in enumerate(C.STATUSES)}


def bump_status(shot, new: str):
    if ORDER.get(new, 0) > ORDER.get(shot.get("status", "planned"), 0):
        shot["status"] = new


# --------------------------------------------------------------------------- job planning
class Job(dict):
    """endpoint, arguments, shot_id, kind, model_name, units, unit, est_usd, outputs, after, uploads"""


def _ref_paths(shots: C.Shots, shot) -> list[Path]:
    """Reference images: approved stills of other shots (`refs_from`) + explicit `ref_images`."""
    paths = []
    for other in shot.get("refs_from") or []:
        o = shots.shot(str(other))
        if o.get("ref_selected"):
            paths.append(C.abspath(o["ref_selected"]))
        else:
            print(f"  note: {shot['id']} refs_from {o['id']} has no approved still yet — skipped")
    for p in shot.get("ref_images") or []:
        paths.append(C.abspath(p))
    for p in paths:
        if not p.exists():
            C.die(f"{shot['id']}: reference image missing: {C.rel(p)}")
    return paths


def plan_stills(shots: C.Shots, targets, n: int, model_name: str) -> list[Job]:
    m = C.model(model_name)
    jobs = []
    for shot in targets:
        if shot.get("type") not in ("ai_video", "present_day"):
            continue
        refs = _ref_paths(shots, shot)[: int(m.get("max_refs", 0))]
        endpoint = m["endpoint"] if refs else m["t2i_endpoint"]
        prompt = P.image_prompt(shot)
        v = C.next_version(C.REFS / shot["id"], shot["id"])
        ext = "." + str(m["defaults"].get("output_format", "png")).replace("jpeg", "jpg")
        per_call = int(m.get("max_count") or 1) if m.get("count_arg") else 1
        made = 0
        while made < n:
            k = min(per_call, n - made)
            args = {"prompt": prompt, **dict(m["defaults"])}
            if m.get("count_arg"):
                args[m["count_arg"]] = k
            outs = [C.REFS / shot["id"] / f"{shot['id']}_v{v}_{made + i + 1}{ext}" for i in range(k)]
            jobs.append(Job(endpoint=endpoint, arguments=args, uploads={m["refs_arg"]: refs} if refs else {},
                            shot_id=shot["id"], kind="still", model_name=model_name,
                            units=k, unit="image", est_usd=k * float(m["usd"]), outputs=outs,
                            after=("ref_ready", shot["id"])))
            made += k
    return jobs


def plan_video(shots: C.Shots, targets, tier: str | None) -> list[Job]:
    jobs = []
    for shot in targets:
        if shot.get("type") not in ("ai_video", "present_day"):
            continue
        if not shot.get("ref_selected"):
            print(f"  skip {shot['id']}: no approved still (ref_selected) yet")
            continue
        name = P.video_model_name(shot, tier)
        m = C.model(name)
        dur = P.fit_duration(m, float(shot.get("duration_s", 5)))
        args = {"prompt": P.video_prompt(shot), "duration": m["duration_format"].format(dur),
                "negative_prompt": P.negative(shot), **dict(m["defaults"])}
        uploads = {m["image_arg"]: C.abspath(shot["ref_selected"])}
        if shot.get("end_ref") and m.get("end_image_arg"):
            uploads[m["end_image_arg"]] = C.abspath(shot["end_ref"])
        v = C.next_version(C.VIDEO, shot["id"])
        out = C.VIDEO / f"{shot['id']}_v{v}.mp4"
        jobs.append(Job(endpoint=m["endpoint"], arguments=args, uploads=uploads, shot_id=shot["id"],
                        kind="video", model_name=name, units=dur, unit="second",
                        est_usd=dur * float(m["usd"]), outputs=[out], after=("draft", shot["id"])))
    return jobs


# --------------------------------------------------------------------------- execution
def show(jobs: list[Job]):
    for j in jobs:
        print(f"\n- {j['shot_id']}  [{j['model_name']}] {j['endpoint']}  "
              f"{j['units']:g} {j['unit']}(s)  est ${j['est_usd']:.3f}")
        for k, v in j["arguments"].items():
            s = json.dumps(v, ensure_ascii=False)
            print(f"    {k}: {s if len(s) < 400 else s[:400] + '…'}")
        for k, v in (j.get("uploads") or {}).items():
            vs = v if isinstance(v, list) else [v]
            print(f"    {k}: " + ", ".join(C.rel(p) for p in vs))
        print("    -> " + ", ".join(C.rel(p) for p in j["outputs"]))


def execute(jobs: list[Job], a, shots: C.Shots | None = None, what="batch"):
    if not jobs:
        print("nothing to do")
        return []
    show(jobs)
    total = sum(j["est_usd"] for j in jobs)
    print(f"\n{len(jobs)} job(s), est ${total:.2f} = €{C.usd_to_eur(total):.2f}")
    if a.dry_run:
        print("dry run — nothing submitted")
        return []
    C.require_key()
    budget.guard(total, yes=a.yes, past_warning=a.past_warning, what=what)
    # 1) upload + submit everything (fal runs them in parallel), logging ids first
    submitted = []
    for j in jobs:
        args = dict(j["arguments"])
        for key, paths in (j.get("uploads") or {}).items():
            args[key] = [F.upload(p) for p in paths] if isinstance(paths, list) else F.upload(paths)
        rid = F.submit(j["endpoint"], args)
        C.job_event(event="submitted", request_id=rid, endpoint=j["endpoint"], shot_id=j["shot_id"],
                    kind=j["kind"], model=j["model_name"], units=j["units"], unit=j["unit"],
                    est_usd=j["est_usd"], outputs=[C.rel(p) for p in j["outputs"]],
                    arguments=F._loggable(args), after=list(j.get("after") or []))
        print(f"  submitted {j['shot_id']} -> {rid}")
        submitted.append((j, rid))
    # 2) collect
    saved_all, failed = [], []
    for j, rid in submitted:
        try:
            saved = F.finish_job(rid, j["endpoint"], j["shot_id"], j["kind"], j["model_name"],
                                 j["units"], j["unit"], j["est_usd"], j["outputs"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! {j['shot_id']} {rid}: {e}")
            failed.append(j["shot_id"])
            continue
        saved_all += saved
        print(f"  saved {', '.join(C.rel(p) for p in saved)}")
        if shots and j.get("after"):
            bump_status(shots.shot(j["after"][1]), j["after"][0])
            shots.save()
    budget.status()
    if failed:
        print(f"\nFAILED: {', '.join(failed)} (errors logged; `resume` retries collection, "
              "or rerun the shot to pay for a new attempt)")
    return saved_all


# --------------------------------------------------------------------------- commands
def cmd_resume(a):
    pend = C.pending_jobs()
    if not pend:
        print("no unfinished jobs")
        return
    C.require_key()
    shots = C.Shots()
    for ev in pend:
        print(f"resuming {ev['shot_id']} {ev['request_id']}")
        try:
            F.finish_job(ev["request_id"], ev["endpoint"], ev["shot_id"], ev["kind"], ev["model"],
                         ev["units"], ev["unit"], ev["est_usd"], [C.abspath(p) for p in ev["outputs"]])
        except Exception as e:  # noqa: BLE001
            print(f"  still failing: {e}")
            continue
        after = ev.get("after")
        if after and after[1] in {s["id"] for s in shots.shots}:
            bump_status(shots.shot(after[1]), after[0])
            shots.save()


def cmd_looks(a):
    st = C.style_cfg()
    names = a.looks.split(",") if a.looks else list(st["looks"])
    m = C.model(a.model)
    jobs = []
    for name in names:
        fake = {"id": f"look_{name}", "type": "ai_video", "look": name, "description": a.description}
        v = C.next_version(C.REFS / "_looks", fake["id"])
        args = {"prompt": P.image_prompt(fake), **dict(m["defaults"])}
        if m.get("count_arg"):
            args[m["count_arg"]] = a.n
        outs = [C.REFS / "_looks" / f"{fake['id']}_v{v}_{i + 1}.png" for i in range(a.n)]
        jobs.append(Job(endpoint=m["t2i_endpoint"], arguments=args, shot_id=fake["id"], kind="still",
                        model_name=a.model, units=a.n, unit="image", est_usd=a.n * float(m["usd"]),
                        outputs=outs))
    execute(jobs, a, what="look tests")


def cmd_music(a):
    m = C.model("music")
    if a.seconds > 600:
        C.die("max 10 min per generation; make several parts (--name part1, part2…)")
    v = C.next_version(C.AUDIO / "music", a.name)
    args = {"prompt": a.prompt, "music_length_ms": int(a.seconds * 1000), **dict(m["defaults"])}
    units = F.units_for(m, minutes=a.seconds / 60)
    jobs = [Job(endpoint=m["endpoint"], arguments=args, shot_id=f"music:{a.name}", kind="music",
                model_name="music", units=units, unit="minute", est_usd=units * float(m["usd"]),
                outputs=[C.AUDIO / "music" / f"{a.name}_v{v}.mp3"])]
    execute(jobs, a, what="music")


def cmd_upscale(a, shots: C.Shots):
    m = C.model("upscale")
    jobs = []
    for shot in shots.select(a.shots, a.chapter):
        clip = shot.get("clip_selected")
        if not clip:
            continue
        secs = C.probe_duration(C.abspath(clip))
        out = C.abspath(clip).with_name(C.abspath(clip).stem + "_up.mp4")
        jobs.append(Job(endpoint=m["endpoint"], arguments=dict(m["defaults"]),
                        uploads={"video_url": C.abspath(clip)}, shot_id=shot["id"], kind="upscale",
                        model_name="upscale", units=round(secs, 2), unit="second",
                        est_usd=secs * float(m["usd"]), outputs=[out]))
    saved = execute(jobs, a, shots, what="upscale")
    for p in saved:
        sid = p.name.split("_v")[0]
        shots.shot(sid)["clip_upscaled"] = C.rel(p)
    if saved:
        shots.save()


def cmd_raw(a):
    m = C.model(a.model)
    v = C.next_version(C.REFS / "_raw", "raw")
    args = {"prompt": a.prompt, **dict(m["defaults"])}
    if m["kind"] != "image":
        C.die("raw supports image models only")
    if m.get("count_arg"):
        args[m["count_arg"]] = a.n
    outs = [C.REFS / "_raw" / f"raw_v{v}_{i + 1}.png" for i in range(a.n)]
    execute([Job(endpoint=m["t2i_endpoint"], arguments=args, shot_id="_raw", kind="still",
                 model_name=a.model, units=a.n, unit="image", est_usd=a.n * float(m["usd"]),
                 outputs=outs)], a, what="raw")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true")
    common.add_argument("--yes", action="store_true", help="Dima approved this estimate")
    common.add_argument("--past-warning", action="store_true")
    common.add_argument("--shots", help="comma list: 14,15 or shot_014")
    common.add_argument("--chapter", type=int)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stills", parents=[common])
    s.add_argument("--n", type=int, default=3)
    s.add_argument("--model", default="still")
    v = sub.add_parser("video", parents=[common])
    v.add_argument("--tier", help="override: draft|standard|hero")
    lk = sub.add_parser("looks", parents=[common])
    lk.add_argument("--description", required=True)
    lk.add_argument("--looks")
    lk.add_argument("--n", type=int, default=1)
    lk.add_argument("--model", default="still")
    mu = sub.add_parser("music", parents=[common])
    mu.add_argument("--prompt", required=True)
    mu.add_argument("--seconds", type=float, required=True)
    mu.add_argument("--name", default="main")
    sub.add_parser("upscale", parents=[common])
    r = sub.add_parser("raw", parents=[common])
    r.add_argument("--model", default="still_draft")
    r.add_argument("--prompt", required=True)
    r.add_argument("--n", type=int, default=1)
    sub.add_parser("resume", parents=[common])
    a = ap.parse_args()

    if a.cmd in ("stills", "video"):
        if not (a.shots or a.chapter is not None):
            C.die("pick --shots or --chapter")
        shots = C.Shots()
        targets = shots.select(a.shots, a.chapter)
        jobs = (plan_stills(shots, targets, a.n, a.model) if a.cmd == "stills"
                else plan_video(shots, targets, a.tier))
        execute(jobs, a, shots, what=a.cmd)
    elif a.cmd == "looks":
        cmd_looks(a)
    elif a.cmd == "music":
        cmd_music(a)
    elif a.cmd == "upscale":
        cmd_upscale(a, C.Shots())
    elif a.cmd == "raw":
        cmd_raw(a)
    elif a.cmd == "resume":
        cmd_resume(a)


if __name__ == "__main__":
    sys.exit(main())
