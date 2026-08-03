"""
ClipForge — AI Stream Clipper: layout geometry and the ffmpeg filtergraph.

Split out of layout.py to keep both files under the repo's 500-line limit. The
seam is real: nothing here makes a DECISION. It is rectangle arithmetic, the
output-space keep-out map, and the string emitter that turns a finished plan
into one -filter_complex. layout.py decides WHICH layout to use; this file
expresses it.

Every rect that leaves here is even and non-degenerate, because H.264 with
yuv420p refuses odd crop dimensions outright.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from services.captioner_presets import (
    SAFE_CAPTION_BOTTOM,
    SAFE_CAPTION_CENTER,
    SAFE_HOOK_MID_Y,
    SAFE_TOP,
)
from services.clipper.ffmpeg_tools import even

logger = logging.getLogger("clipforge.clipper.layout")

LAYOUTS = ("face_top_game_bottom", "game_top_face_bottom", "pip",
           "fullscreen_game", "fullscreen_crop", "split_screen", "talking_head")

OUT_W, OUT_H = 1080, 1920

FACE_PCT_MIN, FACE_PCT_MAX = 0.15, 0.6
MIN_FACECAM_W = 220          # source px below which a facecam upscales to mush
MIN_WEBCAM_CONF = 0.5        # detect_regions confidence floor for the auto path
MIN_WEBCAM_TRACK = 0.35      # share of face samples that must land in the webcam
DEADZONE_PX = 2.0            # sub-pixel jitter must produce no movement at all
MIN_LANE = 16                # smallest sane band/column height in output px

_NEEDS_FACE = {"face_top_game_bottom", "game_top_face_bottom", "pip"}
_STACKED = {"face_top_game_bottom", "game_top_face_bottom"}
_TRACKING = {"fullscreen_crop", "talking_head"}



# --------------------------------------------------------------------------
# geometry helpers — all pure, all guarded against degenerate input
# --------------------------------------------------------------------------

def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out


def _mk(x: float, y: float, w: float, h: float) -> dict[str, int]:
    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}


def _clamp_rect(rect: Any, src_w: int, src_h: int) -> dict[str, int] | None:
    """Even, positive, and fully inside the source — or None."""
    if not isinstance(rect, dict) or src_w < 2 or src_h < 2:
        return None
    lim_w, lim_h = even(src_w), even(src_h)
    w = max(2, min(even(_num(rect.get("w"))), lim_w))
    h = max(2, min(even(_num(rect.get("h"))), lim_h))
    x = max(0, min(even(_num(rect.get("x"))), lim_w - w))
    y = max(0, min(even(_num(rect.get("y"))), lim_h - h))
    return _mk(x, y, w, h)


def _scale_rect(rect: Any, sx: float, sy: float) -> dict[str, int] | None:
    if not isinstance(rect, dict):
        return None
    return _mk(_num(rect.get("x")) * sx, _num(rect.get("y")) * sy,
               _num(rect.get("w")) * sx, _num(rect.get("h")) * sy)


def _aspect(rect: Any, default: float = 1.0) -> float:
    h = _num((rect or {}).get("h"))
    w = _num((rect or {}).get("w"))
    return w / h if h > 0 and w > 0 else default


def _fit_aspect(rect: Any, aspect: float, src_w: int, src_h: int) -> dict[str, int] | None:
    """Grow the rect about its centre until w/h == aspect, then clamp into frame.

    Growing rather than shrinking keeps whatever the rect was pointing at fully
    visible; scale= would otherwise stretch it to fill the lane.
    """
    base = _clamp_rect(rect, src_w, src_h)
    if base is None or aspect <= 0:
        return None
    cx, cy = rect_centre(base)
    w = max(float(base["w"]), base["h"] * aspect)
    h = w / aspect
    if w > src_w:
        w, h = float(src_w), src_w / aspect
    if h > src_h:
        h, w = float(src_h), src_h * aspect
    return _clamp_rect({"x": cx - w / 2.0, "y": cy - h / 2.0, "w": w, "h": h},
                       src_w, src_h)


def _map_rect(rect: Any, crop: Any, dst: dict[str, int]) -> dict[str, int] | None:
    """Project a SOURCE rect through `crop` into an output lane, clipped to it."""
    if not isinstance(rect, dict) or not isinstance(crop, dict):
        return None
    if crop.get("w", 0) <= 0 or crop.get("h", 0) <= 0:
        return None
    sx, sy = dst["w"] / float(crop["w"]), dst["h"] / float(crop["h"])
    x0 = dst["x"] + (_num(rect.get("x")) - crop["x"]) * sx
    y0 = dst["y"] + (_num(rect.get("y")) - crop["y"]) * sy
    x1, y1 = x0 + _num(rect.get("w")) * sx, y0 + _num(rect.get("h")) * sy
    x0, x1 = max(x0, dst["x"]), min(x1, dst["x"] + dst["w"])
    y0, y1 = max(y0, dst["y"]), min(y1, dst["y"] + dst["h"])
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return _mk(x0, y0, x1 - x0, y1 - y0)


def _bands(face_pct: float, out_h: int) -> tuple[int, int]:
    """(face band height, gameplay band height) — both even, both >= MIN_LANE."""
    band = max(MIN_LANE, min(even(out_h * face_pct), out_h - MIN_LANE))
    band = even(band)
    return band, out_h - band


def _pip_box(face_rect: Any, face_pct: float, out_w: int, out_h: int) -> dict[str, int]:
    """Where the picture-in-picture facecam sits, in OUTPUT coords.

    Derived, never stored: plan_layout needs it for the caption keep-out and
    build_filtergraph needs it for the overlay, and the two must agree.
    """
    margin = max(2, even(out_w * 0.045))
    pw = max(MIN_LANE, min(even(out_w * face_pct), out_w - 2 * margin))
    ph = max(MIN_LANE, even(pw / max(_aspect(face_rect, 16 / 9.0), 0.2)))
    top = even(out_h * (SAFE_TOP / float(OUT_H)))
    ph = min(ph, max(MIN_LANE, even(out_h - top - margin)))
    return _mk(margin, top, pw, ph)

def _safe_zones(layout: str, face_rect: dict | None, game_rect: dict | None,
                chat: dict | None, hud: Sequence[dict], face_pct: float,
                subjects: Sequence[dict] = ()) -> dict:
    """Caption keep-out geometry in OUTPUT (1080x1920) coords.

    `lane` is where game_rect lands on the canvas — chat and HUD live in the
    source frame, so they can only be projected through that crop.
    """
    band_h, rest_h = _bands(face_pct, OUT_H)
    keep: list[dict] = []

    if layout in _STACKED:
        face_y = 0 if layout == "face_top_game_bottom" else rest_h
        game_y = band_h if layout == "face_top_game_bottom" else 0
        keep.append({**_mk(0, face_y, OUT_W, band_h), "kind": "face"})
        lane = _mk(0, game_y, OUT_W, rest_h)
    elif layout == "pip":
        keep.append({**_pip_box(face_rect, face_pct, OUT_W, OUT_H), "kind": "face"})
        lane = _mk(0, 0, OUT_W, OUT_H)
    elif layout == "split_screen":
        half = max(MIN_LANE, even(OUT_W // 2))
        lane = _mk(half, 0, OUT_W - half, OUT_H)
        for subject, crop, box in zip(subjects,
                                      (face_rect, game_rect),
                                      (_mk(0, 0, half, OUT_H), lane)):
            mapped = _map_rect(subject, crop, box)
            if mapped:
                keep.append({**mapped, "kind": "face"})
    else:
        lane = _mk(0, 0, OUT_W, OUT_H)
        for subject in subjects[:1]:
            mapped = _map_rect(subject, game_rect, lane)
            if mapped:
                keep.append({**mapped, "kind": "face"})

    for rect, kind in [(chat, "chat")] + [(h, "hud") for h in hud or []]:
        mapped = _map_rect(rect, game_rect, lane) if rect else None
        if mapped:
            keep.append({**mapped, "kind": kind})

    return {"top": SAFE_TOP, "caption_bottom": SAFE_CAPTION_BOTTOM,
            "caption_center": SAFE_CAPTION_CENTER, "hook_mid_y": SAFE_HOOK_MID_Y,
            "keep_out": keep}


# --------------------------------------------------------------------------
# the one fused filtergraph
# --------------------------------------------------------------------------

def _lane(rect: Any, w: int, h: int, label: str, *, pad: bool = False) -> str:
    """One [0:v] -> [label] chain. `pad` letterboxes instead of stretching."""
    body: list[str] = []
    if isinstance(rect, dict) and rect.get("w", 0) >= 2 and rect.get("h", 0) >= 2:
        body.append(f"crop={rect['w']}:{rect['h']}:{rect['x']}:{rect['y']}")
    if pad:
        body.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos")
        body.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        body.append(f"scale={w}:{h}:flags=lanczos")
    body.append("setsar=1")
    return "[0:v]" + ",".join(body) + f"[{label}]"


def build_filtergraph(plan: dict, out_w: int = OUT_W, out_h: int = OUT_H) -> str:
    """The whole -filter_complex string for a layout plan, ending in [v].

    Pure: no probing, no I/O. render.py appends the subtitles filter to the [v]
    pad, which is why every branch must produce exactly one and label it [v].
    """
    plan = plan if isinstance(plan, dict) else {}
    out_w = max(MIN_LANE, even(_num(out_w, OUT_W)))
    out_h = max(MIN_LANE, even(_num(out_h, OUT_H)))
    layout = str(plan.get("layout") or "fullscreen_crop")
    face, game = plan.get("face_rect"), plan.get("game_rect")
    face_pct = min(FACE_PCT_MAX, max(FACE_PCT_MIN, _num(plan.get("face_pct"), 0.35)))

    if layout in _STACKED and isinstance(face, dict) and isinstance(game, dict):
        band_h, rest_h = _bands(face_pct, out_h)
        if layout == "face_top_game_bottom":
            top, bot = _lane(face, out_w, band_h, "top"), _lane(game, out_w, rest_h, "bot")
        else:
            top, bot = _lane(game, out_w, rest_h, "top"), _lane(face, out_w, band_h, "bot")
        return f"{top};{bot};[top][bot]vstack=inputs=2[v]"

    if layout == "pip" and isinstance(face, dict):
        box = _pip_box(face, face_pct, out_w, out_h)
        return (f"{_lane(game, out_w, out_h, 'bg', pad=True)};"
                f"{_lane(face, box['w'], box['h'], 'pip')};"
                f"[bg][pip]overlay={box['x']}:{box['y']}[v]")

    if layout == "split_screen" and isinstance(face, dict) and isinstance(game, dict):
        left = max(MIN_LANE, even(out_w // 2))
        return (f"{_lane(face, left, out_h, 'l')};"
                f"{_lane(game, out_w - left, out_h, 'r')};"
                f"[l][r]hstack=inputs=2[v]")

    # fullscreen_game / fullscreen_crop / talking_head — and every degraded plan.
    return _lane(game, out_w, out_h, "v", pad=True)
