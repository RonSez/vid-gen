"""End-to-end pipeline test in MOCK mode: zero network to fal, zero cost.

  python scripts/selftest.py [--keep]

Builds a throwaway copy of the project with a 2-chapter demo film (every shot type), then runs
stills -> pick -> video -> QA -> voices -> narration -> music -> maps/titles/ken burns ->
rough cut -> where -> final + subtitles + FCPXML, plus budget-guard and resume checks.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PY = sys.executable

DEMO = """
film:
  title: "Demo"
  fps: 24
  transition_s: 0.5
  default_transition: dissolve
  chapter_fade_s: 0.5
  narration_lead_s: 0.3
  narration_tail_s: 0.4
  music: [{file: assets/audio/music/main_v1.mp3, start_s: 0, volume: 0.3}]
scenes:
  - id: sc_001
    chapter: 0
    narration_ru: "В начале двадцатого века эта земля была бедной. Но сюда приезжали молодые женщины."
    narration_en: "At the start of the twentieth century this land was poor. But young women came here."
  - id: sc_002
    chapter: 1
    narration_ru: "Ханна Майзель приехала учиться и учить."
    narration_en: "Hannah Meisel came to learn and to teach."
shots:
  - {id: shot_001, scene: sc_001, type: title_card, duration_s: 3, title: {text: "Три женщины", subtitle: "Одна земля"}}
  - {id: shot_002, scene: sc_001, type: ai_video, duration_s: 4, model_tier: standard, look: past,
     description: "Wide shot of rocky hills at dawn, a dirt road, no people", people: false}
  - {id: shot_003, scene: sc_001, type: map, duration_s: 4,
     map: {bounds: [34.2, 36.0, 31.0, 33.3], places: [jaffa, jerusalem, kinneret_farm], route: [jaffa, kinneret_farm], title: "1910"}}
  - {id: shot_004, scene: sc_002, type: archival_photo, duration_s: 4, source: assets/archival/test_photo.jpg,
     kenburns: {start: [1.0, 0.5, 0.5], end: [1.2, 0.4, 0.4]}, transition: cut}
  - {id: shot_005, scene: sc_002, type: ai_video, duration_s: 5, model_tier: hero, look: past_dramatic,
     description: "Young women in long skirts hoeing a field by the lake, seen from behind", refs_from: [shot_002]}
  - {id: shot_006, scene: sc_002, type: present_day, duration_s: 3, model_tier: draft,
     description: "Students walking into a modern agricultural college"}
"""

FAILS = []


def run(args, env, expect=0, name=None):
    name = name or " ".join(args[:3])
    p = subprocess.run([PY, *args], cwd=env["PIPELINE_ROOT"], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ok = p.returncode == expect
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (exit {p.returncode}, wanted {expect})"))
    if not ok:
        print("   " + (p.stdout + p.stderr).strip().replace("\n", "\n   ")[-2500:])
        FAILS.append(name)
    return p.stdout + p.stderr


def check(cond, name):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    root = Path(tempfile.mkdtemp(prefix="pipeline_selftest_"))
    shutil.copytree(REPO / "project", root / "project")
    shutil.copytree(REPO / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    for d in ("shots", "assets/archival", "review", "ledger", "output", "build"):
        (root / d).mkdir(parents=True, exist_ok=True)
    if (REPO / "assets/graphics/basemap").exists():
        shutil.copytree(REPO / "assets/graphics/basemap", root / "assets/graphics/basemap")
    (root / "shots/shots.yaml").write_text(DEMO, "utf-8")
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1200, 1600), (120, 100, 80))
    ImageDraw.Draw(img).rectangle([200, 300, 1000, 1300], outline=(240, 230, 210), width=12)
    img.save(root / "assets/archival/test_photo.jpg")

    env = dict(os.environ, PIPELINE_ROOT=str(root), FAL_MOCK="1", PYTHONIOENCODING="utf-8")
    env.pop("FAL_KEY", None)
    S = "scripts/"
    print(f"selftest root: {root}\n")

    run([S + "shots.py", "validate"], env)
    # dry run with NO mock and NO key must still work and spend nothing
    real = dict(env)
    real.pop("FAL_MOCK")
    out = run([S + "fal_gen.py", "stills", "--chapter", "0", "--dry-run"], real, name="dry-run without key")
    check("dry run — nothing submitted" in out, "dry-run prints estimate only")
    run([S + "fal_gen.py", "stills", "--chapter", "0", "--yes"], real, expect=1, name="real mode refuses without FAL_KEY")

    run([S + "fal_gen.py", "stills", "--shots", "2,6", "--n", "3"], env, name="stills (nano banana, mock)")
    run([S + "shots.py", "pick", "2", "B"], env)
    run([S + "shots.py", "pick", "6", "A"], env)
    run([S + "fal_gen.py", "stills", "--shots", "5", "--n", "2", "--model", "still_draft"], env,
        name="stills with refs_from (flux, 2 calls)")
    check(len(list((root / "assets/refs/shot_005").glob("*.png"))) == 2, "shot_005 has 2 stills")
    run([S + "shots.py", "pick", "5", "B"], env)
    run([S + "shots.py", "note", "5", "warmer light"], env)
    run([S + "gallery.py", "refs", "--chapter", "1"], env)
    run([S + "fal_gen.py", "video", "--chapter", "0"], env, name="video ch0 (kling, mock)")
    run([S + "fal_gen.py", "video", "--chapter", "1"], env, name="video ch1 (veo hero + draft, mock)")
    jobs = [json.loads(l) for l in (root / "ledger/jobs.jsonl").read_text("utf-8").splitlines()]
    vids = [j for j in jobs if j.get("event") == "submitted" and j["kind"] == "video"]
    check(all(j["arguments"].get("generate_audio") is False for j in vids), "video audio always off")
    k = [j for j in vids if "kling" in j["endpoint"]][0]
    check(k["arguments"]["duration"] == "4" and "start_image_url" in k["arguments"], "kling args shape")
    v = [j for j in vids if j["endpoint"] == "fal-ai/veo3.1/image-to-video"][0]
    check(v["arguments"]["duration"] == "6s" and v["units"] == 6, "veo duration rounded up 5->6s")
    run([S + "qa_frames.py", "--chapter", "1"], env)
    check((root / "review/qa/shot_005_v1/sheet.jpg").exists(), "QA contact sheet")
    run([S + "shots.py", "pick-clip", "2"], env)
    run([S + "shots.py", "pick-clip", "5", "v1"], env)
    run([S + "gallery.py", "clips", "--chapter", "1"], env)
    run([S + "tts.py", "compare"], env, name="voice compare (4 candidates)")
    run([S + "gallery.py", "voices"], env)
    run([S + "tts.py", "scenes", "--all"], env, name="narration per scene")
    run([S + "fal_gen.py", "music", "--prompt", "calm strings", "--seconds", "40"], env)
    out = run([S + "assemble.py", "rough"], env, name="rough cut")
    rc = root / "review/roughcut_v1.mp4"
    check(rc.exists(), "roughcut_v1.mp4 exists")
    out = run([S + "assemble.py", "where", "0:05"], env)
    check("shot_00" in out, "where 0:05 maps to a shot")
    run([S + "assemble.py", "final", "--burn", "en"], env, name="final with burned EN subs")
    fin = root / "output/final_1080p_v1.mp4"
    if fin.exists():
        sys.path.insert(0, str(HERE))
        os.environ["PIPELINE_ROOT"] = str(root)
        import common as C  # noqa: E402
        info = C.probe_video(fin)
        check(info["width"] == 1920 and info["height"] == 1080 and round(info["fps"]) == 24, f"final 1080p24 {info}")
        check((root / "output/final_1080p_v1.en.srt").exists(), "EN subtitles written")
        check((root / "output/final_1080p_v1.fcpxml").exists(), "FCPXML written")
        lufs = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(fin), "-af", "ebur128", "-f", "null", "-"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
        line = [l for l in lufs.splitlines() if l.strip().startswith("I:")]
        val = float(line[-1].split()[1]) if line else 0
        check(-17 < val < -15, f"loudness ≈ -16 LUFS (got {val})")
    else:
        check(False, "final exists")
    run([S + "gallery.py", "index"], env)
    out = run([S + "budget.py", "status"], env)
    check("mock jobs not counted" in out and "Spent: €0.00" in out, "mock spend not counted")
    run([S + "budget.py", "film"], env)

    # resume: a submitted-but-unfinished job is collected without resubmitting
    with open(root / "ledger/jobs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "submitted", "request_id": "mock-resume1", "endpoint": "fal-ai/flux-2-pro",
                            "shot_id": "shot_006", "kind": "still", "model": "still_draft", "units": 1,
                            "unit": "image", "est_usd": 0.045, "outputs": ["assets/refs/shot_006/shot_006_v9_1.png"],
                            "mock": True}) + "\n")
    run([S + "fal_gen.py", "resume"], env)
    check((root / "assets/refs/shot_006/shot_006_v9_1.png").exists(), "resume collected pending job")
    out = run([S + "fal_gen.py", "resume"], env)
    check("no unfinished jobs" in out, "nothing pending after resume")

    # budget guard (real mode, no submit): fake €490 spent, a €20 batch must be refused
    with open(root / "ledger/costs.csv", "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["2026-01-01", "x", "video", "m", "e", "1", "second", f"{490/0.893:.2f}", "490",
                                "", "fake", "", "0"])
    code = "import budget; budget.guard(%s, yes=True)"
    for usd, expect, nm in [(22.4, 1, "guard: hard cap refuses"), ]:
        p = subprocess.run([PY, "-c", code % usd], cwd=root / "scripts", env=real, capture_output=True, text=True)
        check(p.returncode == expect, nm)
    (root / "ledger/costs.csv").write_text((root / "ledger/costs.csv").read_text("utf-8").replace(f"{490/0.893:.2f}", "356"), "utf-8")
    p = subprocess.run([PY, "-c", code % 5], cwd=root / "scripts", env=real, capture_output=True, text=True)
    check(p.returncode == 3, "guard: 80% warning stops (needs --past-warning)")
    (root / "ledger/costs.csv").write_text((root / "ledger/costs.csv").read_text("utf-8").replace(",356,", ",10,"), "utf-8")
    p = subprocess.run([PY, "-c", "import budget; budget.guard(5, yes=False)"], cwd=root / "scripts", env=real,
                       capture_output=True, text=True)
    check(p.returncode == 2, "guard: batch over €2 needs --yes")

    print(f"\n{'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
    if a.keep or FAILS:
        print(f"kept: {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
