"""Builds prompts from a shot entry + project/style.yaml (the style bible rules)."""
from __future__ import annotations

import common as C

TIER_TO_MODEL = {"draft": "video_draft", "standard": "video_standard", "hero": "video_hero"}


def look_name(shot) -> str:
    if shot.get("look"):
        return str(shot["look"])
    return "present" if shot.get("type") == "present_day" else "past"


def _style():
    return C.style_cfg()


def _is_past(look: str) -> bool:
    return not look.startswith("present")


def negative(shot) -> str:
    st = _style()
    parts = [st["negative"]["common"].strip()]
    if _is_past(look_name(shot)):
        parts.append(st["negative"]["past"].strip())
    if shot.get("avoid"):
        parts.append(str(shot["avoid"]))
    return ", ".join(parts)


def image_prompt(shot) -> str:
    if shot.get("prompt_override"):
        return str(shot["prompt_override"])
    st = _style()
    look = look_name(shot)
    if look not in st["looks"]:
        C.die(f"{shot['id']}: unknown look '{look}' (style.yaml has {list(st['looks'])})")
    honesty = ""
    if shot.get("type") in st.get("honesty_applies_to_types", []) and shot.get("people", True):
        honesty = st["honesty_rule"].strip()
    text = st["prompt_template_image"].format(
        description=str(shot["description"]).strip().rstrip("."),
        honesty=honesty,
        look=st["looks"][look]["image"].strip(),
        period=st["period_rules"].strip() if _is_past(look) else "",
        negative=negative(shot),
    )
    if shot.get("direction"):  # Dima's notes, e.g. "warmer light, less modern"
        text = f"{text}. Director's note: {shot['direction']}"
    return _tidy(text)


def video_prompt(shot) -> str:
    if shot.get("video_prompt_override"):
        return str(shot["video_prompt_override"])
    st = _style()
    look = look_name(shot)
    motion = shot.get("motion") or shot["description"]
    text = st["prompt_template_video"].format(
        motion_desc=str(motion).strip().rstrip("."),
        look_motion=st["looks"][look]["video_motion"].strip(),
    )
    if shot.get("direction"):
        text = f"{text} Director's note: {shot['direction']}"
    return _tidy(text)


def video_model_name(shot, override: str | None = None) -> str:
    if override:
        return TIER_TO_MODEL.get(override, override)
    return TIER_TO_MODEL[str(shot.get("model_tier", "standard"))]


def fit_duration(model_cfg: dict, wanted: float) -> int:
    allowed = sorted(int(d) for d in model_cfg["durations"])
    for d in allowed:
        if d >= wanted - 0.01:
            return d
    return allowed[-1]


def _tidy(s: str) -> str:
    while ". ." in s or ".." in s:
        s = s.replace(". .", ".").replace("..", ".")
    return " ".join(s.split())
