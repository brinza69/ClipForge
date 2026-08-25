"""
ClipForge — AI Stream Clipper: the 9:16 layout engine.

Turns "here is a candidate window, here is where the facecam / gameplay / chat /
HUD sit" into a concrete plan for a 1080x1920 frame. The geometry and the fused
ffmpeg filtergraph live in layout_geom.py and are re-exported here, so callers
keep using layout.plan_layout / layout.build_filtergraph.

One encode, always: the gaming split is a vstack of two crops of the SAME
input, never a second pass over an intermediate file (same rationale as
remix_pipeline._stage_match_and_caption).

Coordinate spaces — three of them, and mixing them up silently ruins a crop:

  * egions rects are in SAMPLED-FRAME coords (regions["frame_width"]/
    ["frame_height"]), because content_type downscales frames to 480px wide.
  * aces boxes are in PROXY coords (signals.face_presence).
  * everything this module EMITS is in SOURCE coords (src_w x src_h), except
    safe_zones, which is in OUTPUT coords so captions.py can use it directly.

Two hard rules the rest of the pipeline depends on:

  * never emit a layout with an empty face band — an unusable webcam falls back
    to a fullscreen layout and says why in warnings;
  * every emitted rect is even and inside the source.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from services.clipper.content_type import median_rect, rect_centre
from services.clipper.ffmpeg_tools import even
from services.clipper.layout_geom import (
    DEADZONE_PX,
    FACE_PCT_MAX,
    FACE_PCT_MIN,
    LAYOUTS,
    MIN_FACECAM_W,
    MIN_LANE,
    MIN_WEBCAM_CONF,
    MIN_WEBCAM_TRACK,
    OUT_H,
    OUT_W,
    _aspect,
    _bands,
    _clamp_rect,
    _fit_aspect,
    _fit_inside,
    _map_rect,
    _mk,
    _NEEDS_FACE,
    _num,
    _pip_box,
    _safe_zones,
    _scale_rect,
    _STACKED,
    _TRACKING,
    build_filtergraph,
)

logger = logging.getLogger("clipforge.clipper.layout")

__all__ = ["LAYOUTS", "plan_layout", "smooth_keyframes", "build_filtergraph"]


# --------------------------------------------------------------------------
# input normalisation
# --------------------------------------------------------------------------

def _content_type(explicit: Any, cand: Any, regions: Any) -> str:
    """What kind of source this is, for picking a layout.

    `explicit` wins. Sniffing it out of the candidate dict was the only route
    until now, and NEITHER pipeline caller put it there — the clip row carried
    the profile, the candidate dict did not, and neither did regions.json. So
    this always answered "unknown", `gaming` was always False, and the whole
    gaming branch below was unreachable in production: every gaming export came
    out as a tracking crop with no face band. The dict lookup stays as a
    fallback for callers that do carry it, tests included.
    """
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    for source in (cand, regions):
        if isinstance(source, dict):
            for key in ("content_type", "profile"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip().lower()
    return "unknown"


def _proxy_dims(cand: Any, regions: Any, src_w: int, src_h: int) -> tuple[int, int]:
    """Pixel size the `faces` boxes were measured in.

    ingest samples the analysis frames FROM the proxy, so the regions frame size
    is the same space in practice — it is the fallback when the candidate does
    not carry the proxy dimensions itself.
    """
    for source in (cand, cand.get("signals") if isinstance(cand, dict) else None, regions):
        if not isinstance(source, dict):
            continue
        w = int(_num(source.get("proxy_width") or source.get("frame_width")))
        h = int(_num(source.get("proxy_height") or source.get("frame_height")))
        if w >= 2 and h >= 2:
            return w, h
    return src_w, src_h


def _face_samples(faces: Any, sx: float, sy: float,
                  src_w: int, src_h: int) -> list[tuple[float, list[dict]]]:
    """[(t, [rect, ...])] in SOURCE coords, one entry per usable sample."""
    out: list[tuple[float, list[dict]]] = []
    for sample in faces or []:
        if not isinstance(sample, dict):
            continue
        rects: list[dict] = []
        for box in sample.get("boxes") or []:
            if not isinstance(box, (list, tuple)) or len(box) < 4:
                continue
            rect = _clamp_rect(
                {"x": _num(box[0]) * sx, "y": _num(box[1]) * sy,
                 "w": _num(box[2]) * sx, "h": _num(box[3]) * sy}, src_w, src_h)
            if rect:
                rects.append(rect)
        out.append((_num(sample.get("t")), rects))
    return out


def _webcam_track(webcam: dict, samples: Sequence[tuple[float, list[dict]]]) -> float:
    """Share of face-bearing samples whose face sits inside the webcam rect.

    A webcam rect that the faces keep leaving is a UI panel, not a facecam.
    Returns 1.0 when there is nothing to judge with — absence of face samples is
    not evidence against the rect (detect_regions already required faces in it).
    """
    bearing = [rects for _, rects in samples if rects]
    if len(bearing) < 3:
        return 1.0
    hits = 0
    for rects in bearing:
        for rect in rects:
            cx, cy = rect_centre(rect)
            if (webcam["x"] <= cx <= webcam["x"] + webcam["w"]
                    and webcam["y"] <= cy <= webcam["y"] + webcam["h"]):
                hits += 1
                break
    return hits / float(len(bearing))


def _face_in(webcam: dict | None,
             samples: Sequence[tuple[float, list[dict]]]) -> dict | None:
    """Median face box sitting inside `webcam` — where the head actually is.

    Not the biggest face in the frame: on a co-stream that is often the OTHER
    streamer, and anchoring this facecam's crop on him would frame the wrong
    person's shoulder.
    """
    if not webcam:
        return None
    inside = []
    for _, rects in samples:
        for rect in rects:
            cx, cy = rect_centre(rect)
            if (webcam["x"] <= cx <= webcam["x"] + webcam["w"]
                    and webcam["y"] <= cy <= webcam["y"] + webcam["h"]):
                inside.append(rect)
    return median_rect(inside) if inside else None


def _subject_rect(samples: Sequence[tuple[float, list[dict]]]) -> dict | None:
    """Median of the biggest face per sample — the person to keep in frame."""
    biggest = [max(rects, key=lambda r: r["w"] * r["h"]) for _, rects in samples if rects]
    return median_rect(biggest) if biggest else None


def _columns(samples: Sequence[tuple[float, list[dict]]],
             src_w: int) -> tuple[dict, dict] | None:
    """Two well-separated subject clusters for split_screen, left then right."""
    boxes = [r for _, rects in samples for r in rects]
    if len(boxes) < 4:
        return None
    centres = sorted(rect_centre(r)[0] for r in boxes)
    mid = centres[len(centres) // 2]
    left = [r for r in boxes if rect_centre(r)[0] < mid]
    right = [r for r in boxes if rect_centre(r)[0] >= mid]
    if len(left) < 2 or len(right) < 2:
        return None
    lo, hi = median_rect(left), median_rect(right)
    if lo is None or hi is None:
        return None
    if rect_centre(hi)[0] - rect_centre(lo)[0] < 0.15 * src_w:
        return None  # one person wobbling, not two people
    return lo, hi


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------

def _fallback(gaming: bool) -> str:
    return "fullscreen_game" if gaming else "fullscreen_crop"


def _game_crop(gameplay: dict | None, avoid: Sequence[dict | None],
               aspect: float, src_w: int, src_h: int) -> dict[str, int] | None:
    """Aspect-fit the gameplay area, then slide it off whatever is redundant.

    The facecam is already in its own band and chat is excluded on request, so
    both are wasted pixels inside the gameplay crop — but only slide when the
    frame actually has slack, never at the cost of re-centring the action.
    """
    base = gameplay or _mk(0, 0, src_w, src_h)
    crop = _fit_aspect(base, aspect, src_w, src_h)
    if crop is None:
        return None
    blockers = [r for r in avoid if isinstance(r, dict)]
    if not blockers or crop["w"] >= even(src_w):
        return crop

    def overlap(x: int) -> float:
        total = 0.0
        for rect in blockers:
            iw = min(x + crop["w"], rect["x"] + rect["w"]) - max(x, rect["x"])
            ih = min(crop["y"] + crop["h"], rect["y"] + rect["h"]) - max(crop["y"], rect["y"])
            total += max(0, iw) * max(0, ih)
        return total

    limit = even(src_w) - crop["w"]
    options = {crop["x"], 0, limit}
    for rect in blockers:
        options.update({even(rect["x"] + rect["w"]), even(rect["x"] - crop["w"])})
    best = min((max(0, min(x, limit)) for x in options),
               key=lambda x: (overlap(x), abs(x - crop["x"])))
    crop["x"] = best
    return crop


def _keyframes(samples: Sequence[tuple[float, list[dict]]], fallback: dict,
               aspect: float, src_w: int, src_h: int,
               t0: float, t1: float) -> list[dict]:
    """One crop rect per face sample inside the candidate window, subject-centred."""
    out: list[dict] = []
    last = fallback
    for t, rects in samples:
        if t1 > t0 and not (t0 - 0.25 <= t <= t1 + 0.25):
            continue
        if rects:
            biggest = max(rects, key=lambda r: r["w"] * r["h"])
            # Frame the head with room to breathe rather than cropping to the jaw.
            padded = {"x": biggest["x"] - biggest["w"] * 0.5,
                      "y": biggest["y"] - biggest["h"] * 0.9,
                      "w": biggest["w"] * 2.0, "h": biggest["h"] * 3.0}
            rect = _fit_aspect(padded, aspect, src_w, src_h) or last
        else:
            rect = last
        last = rect
        out.append({"t": round(max(0.0, t - t0), 3), "rect": dict(rect)})
    return out


def plan_layout(cand: dict, regions: dict, faces: list[dict],
                src_w: int, src_h: int, *, mode: str = "auto",
                face_pct: float = 0.35,
                include_chat: bool = False,
                content_type: str | None = None) -> dict:
    """Plan one 9:16 frame.

    Returns {'layout','face_rect','game_rect','chat_rect','keyframes','warnings',
    'face_pct','safe_zones'}. Rects are SOURCE coords; `safe_zones` is OUTPUT
    (1080x1920) coords; keyframe `t` is CLIP-RELATIVE seconds (t - cand.start),
    because the renderer seeks with -ss before -i.

    `include_chat=True` lets the gameplay crop keep the chat strip and reports
    its rect so captions dodge it; False slides the crop off chat instead.

    `content_type` is what the classifier decided about the SOURCE. Pass it:
    without it a gaming source cannot get a gaming layout, and the caller that
    forgets will not see an error, only a clip with no facecam in it.
    """
    cand = cand if isinstance(cand, dict) else {}
    regions = regions if isinstance(regions, dict) else {}
    warnings: list[str] = []
    src_w, src_h = int(_num(src_w)), int(_num(src_h))

    want_pct = _num(face_pct, 0.35)
    face_pct = min(FACE_PCT_MAX, max(FACE_PCT_MIN, want_pct))
    if abs(face_pct - want_pct) > 1e-6:
        warnings.append(
            f"Face band {want_pct:.0%} is outside the usable range; using {face_pct:.0%}.")

    if src_w < 2 or src_h < 2:
        warnings.append("Source dimensions are unknown; falling back to a plain centre crop.")
        return _plan("fullscreen_crop", None, None, None, [], warnings, face_pct,
                     _safe_zones("fullscreen_crop", None, None, None, [], face_pct))

    # --- normalise every input into source coords ----------------------------
    fw = int(_num(regions.get("frame_width")))
    fh = int(_num(regions.get("frame_height")))
    rsx = src_w / float(fw) if fw >= 2 else 1.0
    rsy = src_h / float(fh) if fh >= 2 else 1.0
    webcam = _clamp_rect(_scale_rect(regions.get("webcam"), rsx, rsy), src_w, src_h)
    # Every facecam, not just the best one. A co-stream has one per person and
    # a gameplay crop that only slides off the first frames the second streamer
    # as though he were the game.
    webcams = [r for r in (_clamp_rect(_scale_rect(w, rsx, rsy), src_w, src_h)
                           for w in regions.get("webcams") or []) if r] or (
        [webcam] if webcam else [])
    gameplay = _clamp_rect(_scale_rect(regions.get("gameplay"), rsx, rsy), src_w, src_h)
    chat = _clamp_rect(_scale_rect(regions.get("chat"), rsx, rsy), src_w, src_h)
    hud = [r for r in (_clamp_rect(_scale_rect(h, rsx, rsy), src_w, src_h)
                       for h in regions.get("hud") or []) if r]

    pw, ph = _proxy_dims(cand, regions, src_w, src_h)
    samples = _face_samples(faces, src_w / float(pw), src_h / float(ph), src_w, src_h)

    confidence = regions.get("confidence") if isinstance(regions.get("confidence"), dict) else {}
    conf = _num(confidence.get("webcam"))
    track = _webcam_track(webcam, samples) if webcam else 0.0
    strict_webcam = bool(webcam) and conf >= MIN_WEBCAM_CONF and track >= MIN_WEBCAM_TRACK
    if webcam and webcam["w"] < MIN_FACECAM_W:
        warnings.append(
            f"Facecam is {webcam['w']}px wide in the source; it will look soft at 1080p.")

    content = _content_type(content_type, cand, regions)
    gaming = content == "gaming"

    # --- pick the layout -----------------------------------------------------
    want = str(mode or "auto").strip().lower()
    if want not in LAYOUTS and want != "auto":
        warnings.append(f"Unknown layout '{mode}'; choosing one automatically.")
        want = "auto"

    columns = _columns(samples, src_w)
    subject = _subject_rect(samples)

    if want == "auto":
        if gaming:
            layout = "face_top_game_bottom" if strict_webcam else "fullscreen_game"
            if webcam and not strict_webcam:
                warnings.append(
                    "The facecam detection was too weak or too unsteady to build a "
                    "split layout; using the full-screen gameplay crop instead.")
        elif columns and content in ("podcast", "interview"):
            layout = "split_screen"
        elif subject:
            layout = "talking_head"
        else:
            layout = "fullscreen_crop"
    else:
        layout = want
        if layout in _NEEDS_FACE and not webcam:
            layout = _fallback(gaming)
            warnings.append(
                f"No facecam was found, so '{want}' would leave an empty face band; "
                f"using '{layout}' instead.")
        elif layout in _NEEDS_FACE and not strict_webcam:
            warnings.append(
                "Keeping your layout, but the facecam detection is weak — check the "
                "preview before exporting.")
        elif layout == "split_screen" and not columns:
            layout = "talking_head" if subject else _fallback(gaming)
            warnings.append(
                f"Only one subject was detected, so '{want}' would show the same person "
                f"twice; using '{layout}' instead.")
        elif layout == "talking_head" and not subject:
            layout = "fullscreen_crop"
            warnings.append(
                f"No face was detected, so '{want}' has nothing to track; "
                f"using '{layout}' instead.")

    # --- build the rects for that layout ------------------------------------
    band_h, rest_h = _bands(face_pct, OUT_H)
    avoid: list[dict | None] = [] if include_chat else [chat]
    chat_rect = chat if (include_chat and chat) else None
    face_rect = game_rect = None
    keyframes: list[dict] = []
    t0, t1 = _num(cand.get("start")), _num(cand.get("end"))

    if layout in _STACKED:
        face_rect = _fit_inside(webcam, OUT_W / float(band_h), src_w, src_h,
                                anchor=_face_in(webcam, samples))
        game_rect = _game_crop(gameplay, avoid + list(webcams),
                               OUT_W / float(rest_h), src_w, src_h)
    elif layout == "pip":
        face_rect = webcam
        game_rect = _game_crop(gameplay, avoid, OUT_W / float(OUT_H), src_w, src_h)
    elif layout == "split_screen" and columns:
        half = OUT_W / 2.0
        face_rect = _fit_aspect(columns[0], half / OUT_H, src_w, src_h)
        game_rect = _fit_aspect(columns[1], half / OUT_H, src_w, src_h)
    elif layout in _TRACKING:
        aspect = OUT_W / float(OUT_H)
        base = (_fit_aspect(subject, aspect, src_w, src_h) if subject
                else _fit_aspect(gameplay or _mk(0, 0, src_w, src_h), aspect, src_w, src_h))
        keyframes = _keyframes(samples, base, aspect, src_w, src_h, t0, t1) if base else []
        keyframes = [{"t": k["t"], "rect": _clamp_rect(k["rect"], src_w, src_h) or base}
                     for k in smooth_keyframes(keyframes, max_pan_px_per_s=src_w * 0.12,
                                               max_zoom_per_s=0.10, ema=0.3)]
        game_rect = keyframes[0]["rect"] if keyframes else base
    else:  # fullscreen_game
        game_rect = _game_crop(gameplay, avoid, OUT_W / float(OUT_H), src_w, src_h)

    if layout in _NEEDS_FACE and not face_rect:
        # Belt and braces: an aspect fit can only fail on a degenerate source,
        # and an empty face band must never reach the renderer.
        layout = _fallback(gaming)
        warnings.append("The facecam rectangle was unusable; fell back to a full-screen crop.")
        game_rect = _game_crop(gameplay, avoid, OUT_W / float(OUT_H), src_w, src_h)
        face_rect = None

    if layout == "split_screen" and columns:
        subjects: tuple[dict, ...] = columns
    elif subject and layout in _TRACKING:
        subjects = (subject,)
    else:
        subjects = ()

    zones = _safe_zones(layout, face_rect, game_rect, chat, hud, face_pct, subjects)
    return _plan(layout, face_rect, game_rect, chat_rect, keyframes, warnings,
                 face_pct, zones, src_w, src_h)


def _plan(layout: str, face_rect: dict | None, game_rect: dict | None,
          chat_rect: dict | None, keyframes: list[dict], warnings: list[str],
          face_pct: float, zones: dict, src_w: int = 0, src_h: int = 0) -> dict:
    seen: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.append(w)
    return {"layout": layout, "face_rect": face_rect, "game_rect": game_rect,
            "chat_rect": chat_rect, "keyframes": keyframes, "warnings": seen,
            "face_pct": round(face_pct, 4), "safe_zones": zones,
            # The frame these crops were measured in. A plan is only valid for
            # it: a 854x480 plan FITS inside a 1920x1080 frame, so a bounds
            # check cannot tell they disagree, and the export silently crops
            # the top-left corner as the facecam. Verified on a real export.
            "src_w": int(src_w), "src_h": int(src_h)}



# --------------------------------------------------------------------------
# temporal smoothing
# --------------------------------------------------------------------------

def smooth_keyframes(kfs: list[dict], *, max_pan_px_per_s: float,
                     max_zoom_per_s: float, ema: float) -> list[dict]:
    """Damp a per-sample crop track into something a viewer can watch.

    Three stages, in order: an EMA on centre and size, a hard cap on how far the
    camera may pan or zoom per second, and a deadzone that suppresses movement
    below DEADZONE_PX entirely — detector noise of one or two pixels must not
    show up as a permanently drifting frame.

    `ema` is the weight of the NEW sample: 1.0 is no smoothing, 0.0 freezes the
    camera on the first rect.
    """
    usable = [(k, k.get("rect")) for k in (kfs or []) if isinstance(k, dict)]
    usable = [(k, r) for k, r in usable
              if isinstance(r, dict) and _num(r.get("w")) >= 2 and _num(r.get("h")) >= 2]
    if not usable:
        return []

    alpha = min(1.0, max(0.0, _num(ema, 0.3)))
    pan_rate = max(0.0, _num(max_pan_px_per_s))
    zoom_rate = max(0.0, _num(max_zoom_per_s))

    first = usable[0][1]
    cx, cy = rect_centre(first)
    w, h = float(first["w"]), float(first["h"])
    out_cx, out_cy, out_w, out_h = cx, cy, w, h
    prev_t = _num(usable[0][0].get("t"))
    out: list[dict] = []

    for kf, rect in usable:
        t = _num(kf.get("t"))
        dt = max(1e-3, t - prev_t)
        prev_t = t

        tx, ty = rect_centre(rect)
        cx += alpha * (tx - cx)
        cy += alpha * (ty - cy)
        w += alpha * (float(rect["w"]) - w)
        h += alpha * (float(rect["h"]) - h)

        pan = pan_rate * dt
        nx = min(out_cx + pan, max(out_cx - pan, cx))
        ny = min(out_cy + pan, max(out_cy - pan, cy))
        grow = 1.0 + zoom_rate * dt
        nw = min(out_w * grow, max(out_w / grow, w))
        nh = min(out_h * grow, max(out_h / grow, h))

        if (abs(nx - out_cx) < DEADZONE_PX and abs(ny - out_cy) < DEADZONE_PX
                and abs(nw - out_w) < DEADZONE_PX and abs(nh - out_h) < DEADZONE_PX):
            nx, ny, nw, nh = out_cx, out_cy, out_w, out_h
        out_cx, out_cy, out_w, out_h = nx, ny, nw, nh

        out.append({"t": round(t, 3),
                    "rect": _mk(even(out_cx - out_w / 2.0), even(out_cy - out_h / 2.0),
                                max(2, even(out_w)), max(2, even(out_h)))})
    return out


