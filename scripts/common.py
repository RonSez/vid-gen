"""Shared helpers: paths, config, shots.yaml, versioned files, ledger, ffmpeg."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import shutil
import string
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

# --------------------------------------------------------------------------- paths
# PIPELINE_ROOT lets the self-test run against a throwaway copy of the project.
ROOT = Path(os.environ.get("PIPELINE_ROOT") or Path(__file__).resolve().parent.parent)
PROJECT = ROOT / "project"
SHOTS_FILE = Path(os.environ.get("SHOTS_FILE") or ROOT / "shots" / "shots.yaml")
ASSETS = ROOT / "assets"
REFS = ASSETS / "refs"
VIDEO = ASSETS / "video"
AUDIO = ASSETS / "audio"
GRAPHICS = ASSETS / "graphics"
ARCHIVAL = ASSETS / "archival"
REVIEW = ROOT / "review"
LEDGER = ROOT / "ledger"
BUILD = ROOT / "build"
OUTPUT = ROOT / "output"

MOCK = os.environ.get("FAL_MOCK") == "1"

if hasattr(sys.stdout, "reconfigure"):  # Cyrillic on Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def rel(p: Path | str) -> str:
    """Path relative to ROOT with forward slashes (what we store in yaml/ledger)."""
    p = Path(p)
    try:
        return p.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def abspath(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------- env / yaml
def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def require_key():
    load_env()
    if MOCK:
        return
    if not os.environ.get("FAL_KEY"):
        die("FAL_KEY is not set. Put it in .env (see .env.example). Never paste it in chat logs.")


_yaml_rt = YAML()
_yaml_rt.preserve_quotes = True
_yaml_rt.width = 4096
_yaml_rt.indent(mapping=2, sequence=4, offset=2)


def load_yaml(path: Path):
    with open(path, encoding="utf-8") as f:
        return _yaml_rt.load(f)


def save_yaml(data, path: Path):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _yaml_rt.dump(data, f)
    os.replace(tmp, path)


def models_cfg():
    return load_yaml(PROJECT / "models.yaml")


def style_cfg():
    return load_yaml(PROJECT / "style.yaml")


def model(name: str) -> dict:
    cfg = models_cfg()["models"]
    if name not in cfg:
        die(f"unknown model '{name}'. Known: {', '.join(cfg)}")
    m = dict(cfg[name])
    m["name"] = name
    return m


# --------------------------------------------------------------------------- shots.yaml
TYPES = {"ai_video", "present_day", "archival_photo", "map", "title_card"}
STATUSES = ["planned", "ref_ready", "ref_approved", "draft", "approved", "final"]


class Shots:
    """Round-trip editor for shots.yaml (comments are preserved)."""

    def __init__(self, path: Path = SHOTS_FILE):
        self.path = path
        self.data = load_yaml(path)
        self.data.setdefault("scenes", [])
        self.data.setdefault("shots", [])

    @property
    def film(self) -> dict:
        return self.data.get("film", {}) or {}

    @property
    def scenes(self):
        return self.data["scenes"]

    @property
    def shots(self):
        return self.data["shots"]

    def save(self):
        save_yaml(self.data, self.path)

    def shot(self, sid: str):
        sid = normalize_shot_id(sid)
        for s in self.shots:
            if s["id"] == sid:
                return s
        die(f"no shot '{sid}' in {rel(self.path)}")

    def scene(self, scid: str):
        for s in self.scenes:
            if s["id"] == scid:
                return s
        die(f"no scene '{scid}'")

    def scene_of(self, shot):
        return self.scene(shot["scene"])

    def chapter_of(self, shot) -> int:
        return int(shot.get("chapter", self.scene_of(shot).get("chapter", 0)))

    def shots_in_scene(self, scid: str):
        return [s for s in self.shots if s["scene"] == scid]

    def select(self, ids: str | None = None, chapter: int | None = None,
               types: set | None = None, status: str | None = None):
        out = list(self.shots)
        if ids:
            wanted = [normalize_shot_id(i) for i in ids.split(",") if i.strip()]
            out = [self.shot(i) for i in wanted]
        if chapter is not None:
            out = [s for s in out if self.chapter_of(s) == chapter]
        if types:
            out = [s for s in out if s.get("type") in types]
        if status:
            out = [s for s in out if s.get("status") == status]
        return out


def normalize_shot_id(sid: str) -> str:
    sid = sid.strip()
    if re.fullmatch(r"\d+", sid):
        return f"shot_{int(sid):03d}"
    return sid


# --------------------------------------------------------------------------- versioned files
def next_version(directory: Path, stem: str) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    best = 0
    pat = re.compile(rf"^{re.escape(stem)}_v(\d+)")
    for f in directory.iterdir():
        m = pat.match(f.name)
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def safe_dest(path: Path) -> Path:
    """Never overwrite: if the path exists, raise instead of clobbering."""
    if path.exists():
        die(f"refusing to overwrite existing file {rel(path)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def letters(n: int):
    out = []
    for i in range(n):
        s, k = "", i
        while True:
            s = string.ascii_uppercase[k % 26] + s
            k = k // 26 - 1
            if k < 0:
                break
        out.append(s)
    return out


def ref_options(shot_id: str):
    """All reference stills of a shot, labelled A, B, C... in stable order."""
    d = REFS / shot_id
    files = sorted(
        [f for f in d.glob(f"{shot_id}_v*") if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")],
        key=_version_key,
    ) if d.exists() else []
    return list(zip(letters(len(files)), files))


def clip_options(shot_id: str):
    files = sorted(VIDEO.glob(f"{shot_id}_v*.mp4"), key=_version_key) if VIDEO.exists() else []
    return [(re.search(r"_v(\d+)", f.name).group(0)[1:], f) for f in files]


def _version_key(f: Path):
    m = re.search(r"_v(\d+)(?:_(\d+))?", f.name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


# --------------------------------------------------------------------------- ledger
JOBS = LEDGER / "jobs.jsonl"
COSTS = LEDGER / "costs.csv"
COST_FIELDS = ["date", "shot_id", "kind", "model", "endpoint", "units", "unit",
               "est_usd", "est_eur", "actual_usd", "request_id", "output", "mock"]


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def job_event(**ev):
    LEDGER.mkdir(parents=True, exist_ok=True)
    ev.setdefault("time", now())
    ev.setdefault("mock", MOCK)
    with open(JOBS, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def job_events():
    if not JOBS.exists():
        return []
    with open(JOBS, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def pending_jobs():
    """Submitted jobs that never completed/failed — resumable without paying twice."""
    state = {}
    for ev in job_events():
        rid = ev.get("request_id")
        if not rid:
            continue
        if ev["event"] == "submitted":
            state[rid] = ev
        elif ev["event"] in ("completed", "failed", "abandoned"):
            state.pop(rid, None)
    return list(state.values())


def add_cost(row: dict):
    LEDGER.mkdir(parents=True, exist_ok=True)
    new = not COSTS.exists()
    with open(COSTS, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COST_FIELDS)
        if new:
            w.writeheader()
        row = {k: row.get(k, "") for k in COST_FIELDS}
        row["date"] = row["date"] or now()
        row["mock"] = "1" if MOCK else "0"
        w.writerow(row)


def cost_rows(include_mock=False):
    if not COSTS.exists():
        return []
    with open(COSTS, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if include_mock or r.get("mock") != "1"]


def spent_eur(include_mock=False) -> float:
    rate = float(models_cfg()["eur_per_usd"])
    total = 0.0
    for r in cost_rows(include_mock):
        usd = r.get("actual_usd") or r.get("est_usd") or 0
        total += float(usd) * rate
    return total


def usd_to_eur(usd: float) -> float:
    return usd * float(models_cfg()["eur_per_usd"])


# --------------------------------------------------------------------------- ffmpeg
def run(cmd: list, quiet=True):
    cmd = [str(c) for c in cmd]
    if not quiet:
        print("$", " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-25:])
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    return p.stdout


def ffmpeg(*args):
    return run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args])


def probe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", path])
    return float(out.strip())


def probe_video(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,r_frame_rate", "-of", "json", path])
    s = json.loads(out)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {"width": s["width"], "height": s["height"], "fps": float(num) / float(den)}


def has_ffmpeg():
    return shutil.which("ffmpeg") and shutil.which("ffprobe")


def font_path(bold=False) -> str:
    """A font with Cyrillic glyphs (narration language is Russian)."""
    cands = (["constanb.ttf", "georgiab.ttf", "timesbd.ttf", "arialbd.ttf"] if bold
             else ["constan.ttf", "georgia.ttf", "times.ttf", "arial.ttf"])
    for d in [PROJECT / "fonts", Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")]:
        for c in cands + ["DejaVuSerif.ttf"]:
            if (d / c).exists():
                return str(d / c)
    die("no Cyrillic-capable font found; put a .ttf in project/fonts/")


def ff_escape_path(p: Path | str) -> str:
    """Escape a path for use inside an ffmpeg filter argument (Windows-safe)."""
    s = Path(p).as_posix()
    return s.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
