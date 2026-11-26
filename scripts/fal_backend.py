"""Thin layer over fal_client with a free MOCK mode (FAL_MOCK=1).

Real mode: upload -> submit (request id logged BEFORE waiting) -> result -> download.
Mock mode: no network, no money; produces local placeholder images/videos/audio so the
whole pipeline (galleries, QA, assembly) can be exercised end to end.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
import uuid
from pathlib import Path

import common as C


# --------------------------------------------------------------------------- uploads
_UPLOADS = C.LEDGER / "uploads.json"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload(path: Path) -> str:
    """Upload a local file to fal storage once; cached by content hash."""
    path = C.abspath(path)
    if C.MOCK:
        return path.resolve().as_uri()
    cache = json.loads(_UPLOADS.read_text("utf-8")) if _UPLOADS.exists() else {}
    key = _sha(path)
    if key in cache:
        return cache[key]
    import fal_client
    url = fal_client.upload_file(path)
    cache[key] = url
    _UPLOADS.parent.mkdir(parents=True, exist_ok=True)
    _UPLOADS.write_text(json.dumps(cache, indent=1), "utf-8")
    return url


# --------------------------------------------------------------------------- jobs
def submit(endpoint: str, arguments: dict) -> str:
    if C.MOCK:
        rid = "mock-" + uuid.uuid4().hex[:12]
        _MOCK_ARGS[rid] = (endpoint, arguments)
        return rid
    import fal_client
    h = fal_client.submit(endpoint, arguments=arguments)
    return h.request_id


def wait(endpoint: str, request_id: str, poll_s: float = 4.0, timeout_s: float = 3600) -> dict:
    if C.MOCK:
        return _mock_result(request_id, endpoint)
    import fal_client
    t0 = time.time()
    last = None
    while True:
        st = fal_client.status(endpoint, request_id, with_logs=False)
        name = type(st).__name__
        if name != last:
            pos = getattr(st, "position", None)
            print(f"    {request_id[:8]} {name}" + (f" (queue {pos})" if pos is not None else ""))
            last = name
        if isinstance(st, fal_client.Completed):
            return fal_client.result(endpoint, request_id)
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"{request_id} still running after {timeout_s}s; run `fal_gen.py resume` later")
        time.sleep(poll_s)


def download(url: str, dest: Path) -> Path:
    dest = C.safe_dest(dest)
    if url.startswith("file:"):
        from urllib.parse import urlparse, unquote
        src = Path(unquote(urlparse(url).path.lstrip("/")))
        if not src.exists():
            src = Path(unquote(urlparse(url).path))
        shutil.copyfile(src, dest)
        return dest
    import requests
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.replace(dest)
    return dest


def output_urls(result: dict) -> list[str]:
    """Pull file URLs from the different fal response shapes."""
    urls = []
    for key in ("images", "videos"):
        for item in result.get(key) or []:
            urls.append(item["url"] if isinstance(item, dict) else item)
    for key in ("video", "audio", "image", "audio_file"):
        v = result.get(key)
        if isinstance(v, dict) and v.get("url"):
            urls.append(v["url"])
        elif isinstance(v, str) and v.startswith(("http", "file:")):
            urls.append(v)
    return urls


# --------------------------------------------------------------------------- mock
_MOCK_ARGS: dict = {}
_MOCK_DIR = C.BUILD / "mock_outputs"


def _mock_result(rid: str, endpoint: str) -> dict:
    endpoint, args = _MOCK_ARGS.get(rid, (endpoint, {}))
    _MOCK_DIR.mkdir(parents=True, exist_ok=True)
    label = (args.get("prompt") or args.get("text") or endpoint)[:60]
    if "video" in endpoint and "upscale" not in endpoint:
        dur = int(str(args.get("duration", "5")).rstrip("s"))
        out = _MOCK_DIR / f"{rid}.mp4"
        src = args.get("image_url") or args.get("start_image_url")
        _mock_video(out, dur, src, label)
        return {"video": {"url": out.resolve().as_uri()}}
    if "upscale" in endpoint:
        out = _MOCK_DIR / f"{rid}.mp4"
        src = args["video_url"]
        from urllib.parse import urlparse, unquote
        shutil.copyfile(Path(unquote(urlparse(src).path.lstrip("/"))), out)
        return {"video": {"url": out.resolve().as_uri()}}
    if "music" in endpoint:
        secs = (args.get("music_length_ms") or 30000) / 1000
        out = _MOCK_DIR / f"{rid}.mp3"
        C.ffmpeg("-f", "lavfi", "-i", f"sine=frequency=220:duration={secs}",
                 "-af", "volume=0.2", out)
        return {"audio": {"url": out.resolve().as_uri()}}
    if "tts" in endpoint or "speech" in endpoint:
        text = args.get("text") or args.get("prompt") or ""
        secs = max(1.0, len(text) / 14.0)  # ~14 chars/s for Russian narration
        out = _MOCK_DIR / f"{rid}.mp3"
        C.ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={secs:.2f}",
                 "-af", "volume=0.3", out)
        return {"audio": {"url": out.resolve().as_uri()}}
    # images
    n = int(args.get("num_images") or 1)
    urls = []
    for i in range(n):
        out = _MOCK_DIR / f"{rid}_{i}.png"
        _mock_image(out, f"MOCK {i+1}/{n}\n{label}")
        urls.append({"url": out.resolve().as_uri()})
    return {"images": urls}


def _mock_image(out: Path, text: str):
    from PIL import Image, ImageDraw, ImageFont
    import random
    rnd = random.Random(text)
    img = Image.new("RGB", (1920, 1080), tuple(rnd.randint(60, 160) for _ in range(3)))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(C.font_path(), 48)
    d.multiline_text((80, 80), text, fill=(255, 255, 255), font=f, spacing=12)
    img.save(out)


def _mock_video(out: Path, dur: int, src_url: str | None, label: str):
    if src_url and src_url.startswith("file:"):
        from urllib.parse import urlparse, unquote
        img = Path(unquote(urlparse(src_url).path.lstrip("/")))
        C.ffmpeg("-loop", "1", "-i", img, "-t", dur, "-vf",
                 "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                 f"zoompan=z='min(zoom+0.0008,1.2)':d={dur*24}:s=1280x720:fps=24",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", out)
    else:
        C.ffmpeg("-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=24:duration={dur}",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", out)


# --------------------------------------------------------------------------- collect a submitted job
def finish_job(rid, endpoint, shot_id, kind, model_name, units, unit, est_usd, outputs):
    try:
        result = wait(endpoint, rid)
    except Exception as e:  # noqa: BLE001 — keep the job resumable, surface the error
        C.job_event(event="error", request_id=rid, error=str(e)[:500])
        raise
    urls = output_urls(result)
    if not urls:
        C.job_event(event="failed", request_id=rid, error="no output urls",
                    result=json.dumps(result)[:800])
        raise RuntimeError(f"{rid}: no files in result: {json.dumps(result)[:300]}")
    saved = []
    for url, dest in zip(urls, outputs):
        saved.append(download(url, C.abspath(dest)))
    extra = result.get("timestamps")
    if extra and saved:
        sidecar = saved[0].with_suffix(".timestamps.json")
        sidecar.write_text(json.dumps(extra, ensure_ascii=False), "utf-8")
    C.job_event(event="completed", request_id=rid, outputs=[C.rel(p) for p in saved],
                seed=result.get("seed"))
    C.add_cost(dict(shot_id=shot_id, kind=kind, model=model_name, endpoint=endpoint,
                    units=f"{units:g}", unit=unit, est_usd=f"{est_usd:.4f}",
                    est_eur=f"{C.usd_to_eur(est_usd):.4f}", request_id=rid,
                    output=";".join(C.rel(p) for p in saved)))
    return saved


def _loggable(args: dict) -> dict:
    """Arguments as logged: long data URIs trimmed; never contains the API key."""
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and v.startswith("data:"):
            v = v[:40] + "..."
        out[k] = v
    return out


def units_for(model_cfg: dict, *, images: int = 0, seconds: float = 0, chars: int = 0,
              minutes: float = 0) -> float:
    u = model_cfg["unit"]
    if u == "image":
        return images
    if u == "second":
        return seconds
    if u == "1k_chars":
        return chars / 1000
    if u == "minute":
        return math.ceil(minutes)
    raise ValueError(u)
