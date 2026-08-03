"""
ClipForge — AI Stream Clipper: content classification and on-screen regions.

Two questions this module answers about a source, from a handful of sampled
JPEGs plus the Pass A signal timelines:

  * what KIND of video this is (gaming, podcast, talking head, ...) — which
    picks the scoring profile and the default layout;
  * WHERE the facecam, gameplay, chat and HUD sit on screen — the layout engine
    crops around them and the caption planner treats HUD/chat as keep-out.

Everything here is deliberately cheap: Canny edges, an HSV mean, frame
differencing and the Haar cascade that ships with opencv-python. No model
downloads, no per-frame deep inference — nothing expensive may run across a
multi-hour stream.

This file holds only the parts that need a decoded image. The numeric decision
logic lives in content_geom.py and is re-exported here.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from services.clipper.content_geom import (
    CONTENT_TYPES,
    _CHAT_ASPECT_MAX,
    _CORNER_FRAC,
    _EDGE_HI,
    _EDGE_LO,
    _GRID_COLS,
    _GRID_ROWS,
    _HUD_MAX,
    _MAX_FRAMES,
    _TRACK_IOU,
    _WEBCAM_AREA,
    _WEBCAM_ASPECT,
    _WORK_WIDTH,
    aspect_ok,
    build_tracks,
    clamp01,
    classify_features,
    corner_proximity,
    keyword_scores,
    make_rect,
    median_rect,
    rect_area_frac,
    rect_aspect,
    rect_centre,
    rect_iou,
    rect_overlap_frac,
    snap_rect,
    speech_ratio,
    summarize_faces,
    summarize_motion,
    track_stability,
    transcript_text,
)

logger = logging.getLogger("clipforge.clipper.content_type")

_CV: Any = None
_CV_TRIED = False
_CASCADE: Any = None
_CASCADE_TRIED = False


# --------------------------------------------------------------------------
# frame loading (cv2)
# --------------------------------------------------------------------------

def _cv() -> Any:
    global _CV, _CV_TRIED
    if not _CV_TRIED:
        _CV_TRIED = True
        try:
            import cv2
            _CV = cv2
        except ImportError as exc:  # pragma: no cover - opencv is a hard dep
            logger.error("clipper: opencv unavailable (%s); visual analysis off", exc)
    return _CV


def _face_cascade() -> Any:
    global _CASCADE, _CASCADE_TRIED
    if _CASCADE_TRIED:
        return _CASCADE
    _CASCADE_TRIED = True
    cv = _cv()
    if cv is None:
        return None
    try:
        path = Path(cv.data.haarcascades) / "haarcascade_frontalface_default.xml"
        clf = cv.CascadeClassifier(str(path))
        _CASCADE = None if clf.empty() else clf
    except Exception as exc:
        logger.warning("clipper: Haar face cascade unavailable (%s)", exc)
    return _CASCADE


def _pick(items: Sequence[str], limit: int) -> list[str]:
    items = list(items or [])
    if len(items) <= limit:
        return items
    step = len(items) / float(limit)
    return [items[int(i * step)] for i in range(limit)]


def _load_frames(frames: Sequence[str]) -> tuple[list[Any], float, int, int]:
    """(grayscale frames, mean saturation, width, height). Never raises."""
    cv = _cv()
    grays: list[Any] = []
    sats: list[float] = []
    w = h = 0
    if cv is None:
        return grays, 0.0, 0, 0
    for path in _pick(frames, _MAX_FRAMES):
        try:
            img = cv.imread(str(path), cv.IMREAD_COLOR)
        except Exception:
            img = None
        if img is None or img.size == 0:
            logger.warning("clipper: unreadable analysis frame %s — skipped", path)
            continue
        if img.shape[1] > _WORK_WIDTH:
            scale = _WORK_WIDTH / float(img.shape[1])
            img = cv.resize(img, (_WORK_WIDTH, max(2, int(img.shape[0] * scale))))
        if w and (img.shape[1] != w or img.shape[0] != h):
            continue  # a differently-sized frame can't join the diff stack
        h, w = img.shape[0], img.shape[1]
        sats.append(float(np.mean(cv.cvtColor(img, cv.COLOR_BGR2HSV)[:, :, 1])) / 255.0)
        grays.append(cv.cvtColor(img, cv.COLOR_BGR2GRAY))
    return grays, (float(np.mean(sats)) if sats else 0.0), w, h


def _patch_motion(grays: Sequence[Any], w: int, h: int) -> tuple[float, float]:
    """(corner stability, centre motion), both 0..1. 0.08 mean abs diff is a
    fully-changed patch in practice — brightness flicker sits well below it."""
    if len(grays) < 2 or w < 8 or h < 8:
        return 1.0, 0.0
    cw, ch = max(4, int(w * _CORNER_FRAC)), max(4, int(h * _CORNER_FRAC))
    boxes = [(0, 0), (w - cw, 0), (0, h - ch), (w - cw, h - ch)]
    corner_d, centre_d = [], []
    for a, b in zip(grays, grays[1:]):
        d = np.abs(a.astype(np.int16) - b.astype(np.int16))
        corner_d.append(float(np.mean([np.mean(d[y:y + ch, x:x + cw]) for x, y in boxes])) / 255.0)
        centre_d.append(float(np.mean(d[h // 4:h - h // 4, w // 4:w - w // 4])) / 255.0)
    return (clamp01(1.0 - float(np.mean(corner_d)) / 0.08),
            clamp01(float(np.mean(centre_d)) / 0.08))


def frame_features(frames: Sequence[str]) -> dict[str, Any]:
    """Visual half of the feature vector. Empty-ish dict when nothing decodes."""
    grays, saturation, w, h = _load_frames(frames)
    if not grays:
        return {"frame_count": 0, "frame_width": 0, "frame_height": 0}
    cv = _cv()
    edge_d, line_r = [], []
    for gray in grays:
        edges = cv.Canny(gray, _EDGE_LO, _EDGE_HI)
        edge_d.append(float(np.count_nonzero(edges)) / max(1, edges.size))
        lines = cv.HoughLinesP(edges, 1, math.pi / 180, threshold=60,
                               minLineLength=max(12, w // 12), maxLineGap=4)
        line_r.append(clamp01((0 if lines is None else len(lines)) / 40.0))
    corner, centre = _patch_motion(grays, w, h)
    return {
        "frame_count": len(grays), "frame_width": w, "frame_height": h,
        "edge_density": float(np.mean(edge_d)),
        "straight_line_ratio": float(np.mean(line_r)),
        "saturation": saturation,
        "corner_stability": corner,
        "centre_motion": centre,
    }


def detect_content_type(frames: list[str], signals: dict,
                        transcript: dict) -> dict[str, Any]:
    """{'content_type','confidence' 0..1,'evidence':[str,..]}"""
    signals = signals or {}
    features = frame_features(frames or [])
    fw = int(features.get("frame_width") or signals.get("frame_width") or 0)
    fh = int(features.get("frame_height") or signals.get("frame_height") or 0)
    features.update(summarize_motion(signals))
    features.update(summarize_faces(signals, fw, fh))
    features["speech_ratio"] = speech_ratio(signals)
    features.update(keyword_scores(transcript_text(transcript)))
    return classify_features(features)


# --------------------------------------------------------------------------
# region detection (cv2)
# --------------------------------------------------------------------------

def _face_boxes(grays: Sequence[Any]) -> list[list[dict]]:
    clf = _face_cascade()
    if clf is None:
        return [[] for _ in grays]
    out: list[list[dict]] = []
    for gray in grays:
        try:
            side = max(12, gray.shape[1] // 24)
            found = clf.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5,
                                         minSize=(side, side))
        except Exception as exc:
            logger.warning("clipper: face detection failed on a frame (%s)", exc)
            found = []
        out.append([make_rect(*(int(v) for v in box)) for box in (found if found is not None else [])])
    return out


def _webcam_candidates(edges: Sequence[Any], fw: int, fh: int) -> list[list[dict]]:
    cv = _cv()
    min_peri = 4 * 0.06 * min(fw, fh)
    per_frame: list[list[dict]] = []
    for edge in edges:
        found: list[dict] = []
        try:
            contours, _ = cv.findContours(edge, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
        except Exception as exc:
            logger.warning("clipper: contour pass failed on a frame (%s)", exc)
            contours = []
        for contour in contours:
            peri = cv.arcLength(contour, True)
            if peri < min_peri:
                continue
            approx = cv.approxPolyDP(contour, 0.03 * peri, True)
            if len(approx) != 4:
                continue
            rect = make_rect(*cv.boundingRect(approx))
            if not aspect_ok(rect, *_WEBCAM_ASPECT):
                continue
            if not _WEBCAM_AREA[0] <= rect_area_frac(rect, fw, fh) <= _WEBCAM_AREA[1]:
                continue
            found.append(rect)
        per_frame.append(found)
    return per_frame


def _find_webcam(edges: Sequence[Any], faces: Sequence[Sequence[dict]],
                 fw: int, fh: int) -> tuple[dict | None, float]:
    """A rectangular border is not enough — every UI panel is one. The rect only
    counts as a facecam if a face sits inside it in at least half the frames."""
    frames = len(edges)
    tracks = [t for t in build_tracks(_webcam_candidates(edges, fw, fh)) if t]
    best: tuple[float, dict] | None = None
    for track in tracks:
        rep = median_rect(track)
        if rep is None:
            continue
        hits = sum(1 for boxes in faces
                   if any(_inside(rect_centre(b), rep) for b in boxes))
        if frames and hits < math.ceil(frames / 2):
            continue
        coverage = len(track) / float(max(1, frames))
        score = (0.35 * clamp01(coverage) + 0.30 * (hits / float(max(1, frames)))
                 + 0.15 * track_stability(track) + 0.20 * corner_proximity(rep, fw, fh))
        if best is None or score > best[0]:
            best = (score, rep)
    if best is None:
        return None, 0.0
    return snap_rect(best[1], fw, fh), round(clamp01(best[0]), 3)


def _inside(point: tuple[float, float], rect: dict) -> bool:
    x, y = point
    return rect["x"] <= x <= rect["x"] + rect["w"] and rect["y"] <= y <= rect["y"] + rect["h"]


def _band_stats(rect: dict, edges: Sequence[Any], diffs: Sequence[Any]) -> tuple[float, float]:
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    area = float(max(1, w * h))
    ed = float(np.mean([np.count_nonzero(e[y:y + h, x:x + w]) / area for e in edges]))
    mo = (float(np.mean([np.mean(d[y:y + h, x:x + w]) for d in diffs])) / 255.0) if diffs else 0.0
    return ed, mo


def _find_chat(edges: Sequence[Any], diffs: Sequence[Any], fw: int, fh: int,
               webcam: dict | None, mean_edge: float, mean_diff: float) -> tuple[dict | None, float]:
    """Chat is a tall edge-dense strip at a side edge that barely moves. When
    the evidence is thin we return None — a wrong chat rect steals screen area
    from the crop, which is worse than not finding one."""
    best: tuple[float, dict] | None = None
    for frac in (0.16, 0.22, 0.28):
        bw = max(8, int(fw * frac))
        for x in (0, fw - bw):
            rect = make_rect(x, int(fh * 0.05), bw, max(8, int(fh * 0.90)))
            if rect_aspect(rect) >= _CHAT_ASPECT_MAX:
                continue
            if rect_overlap_frac(rect, webcam) > 0.3:
                continue
            ed, mo = _band_stats(rect, edges, diffs)
            if ed < mean_edge * 1.35:
                continue
            if mean_diff > 0 and mo > mean_diff * 0.5:
                continue
            texty = clamp01(ed / max(mean_edge, 1e-6) - 1.0)
            still = 1.0 - clamp01(mo / mean_diff) if mean_diff > 0 else 0.5
            score = 0.6 * texty + 0.4 * still
            if best is None or score > best[0]:
                best = (score, rect)
    if best is None or best[0] < 0.45:
        return None, 0.0
    return snap_rect(best[1], fw, fh), round(clamp01(best[0]), 3)


def _find_hud(edges: Sequence[Any], diffs: Sequence[Any], fw: int, fh: int,
              webcam: dict | None, chat: dict | None,
              mean_edge: float, mean_diff: float) -> tuple[list[dict], float]:
    """Edge-dense, near-static grid cells hugging the frame border. These become
    caption keep-out zones, so over-detecting only costs caption real estate."""
    cw, ch = max(2, fw // _GRID_COLS), max(2, fh // _GRID_ROWS)
    kept: dict[tuple[int, int], float] = {}
    for row in range(_GRID_ROWS):
        for col in range(_GRID_COLS):
            if 0 < col < _GRID_COLS - 1 and 0 < row < _GRID_ROWS - 1:
                continue
            rect = make_rect(col * cw, row * ch, cw, ch)
            if rect_overlap_frac(rect, webcam) > 0.4 or rect_overlap_frac(rect, chat) > 0.4:
                continue
            ed, mo = _band_stats(rect, edges, diffs)
            if ed < mean_edge * 1.5:
                continue
            if mean_diff > 0 and mo > mean_diff * 0.35:
                continue
            kept[(row, col)] = ed
    if not kept:
        return [], 0.0

    merged: list[tuple[float, dict]] = []
    seen: set[tuple[int, int]] = set()
    for cell in list(kept):
        if cell in seen:
            continue
        stack, group = [cell], []
        seen.add(cell)
        while stack:
            r, c = stack.pop()
            group.append((r, c))
            for nb in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if nb in kept and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        rows = [r for r, _ in group]
        cols = [c for _, c in group]
        rect = make_rect(min(cols) * cw, min(rows) * ch,
                         (max(cols) - min(cols) + 1) * cw, (max(rows) - min(rows) + 1) * ch)
        merged.append((float(np.mean([kept[g] for g in group])), rect))

    merged.sort(key=lambda item: item[0], reverse=True)
    top = merged[:_HUD_MAX]
    rects = [r for r in (snap_rect(rect, fw, fh) for _, rect in top) if r]
    strength = clamp01(float(np.mean([s for s, _ in top])) / max(mean_edge * 2.5, 1e-6))
    return rects, round(strength, 3)


def detect_regions(frames: list[str]) -> dict[str, Any]:
    """{'webcam','gameplay','chat','hud','confidence','frame_width','frame_height'}"""
    grays, _sat, fw, fh = _load_frames(frames or [])
    out: dict[str, Any] = {
        "webcam": None, "gameplay": None, "chat": None, "hud": [],
        "confidence": {"webcam": 0.0, "gameplay": 0.0, "chat": 0.0, "hud": 0.0},
        "frame_width": fw, "frame_height": fh,
    }
    if not grays or fw < 16 or fh < 16:
        logger.warning("clipper: no readable frames for region detection")
        return out

    cv = _cv()
    edges = [cv.Canny(g, _EDGE_LO, _EDGE_HI) for g in grays]
    diffs = [np.abs(a.astype(np.int16) - b.astype(np.int16))
             for a, b in zip(grays, grays[1:])]
    mean_edge = float(np.mean([np.count_nonzero(e) / max(1, e.size) for e in edges]))
    mean_diff = (float(np.mean([np.mean(d) for d in diffs])) / 255.0) if diffs else 0.0

    webcam, w_conf = _find_webcam(edges, _face_boxes(grays), fw, fh)
    chat, c_conf = _find_chat(edges, diffs, fw, fh, webcam, mean_edge, mean_diff)
    hud, h_conf = _find_hud(edges, diffs, fw, fh, webcam, chat, mean_edge, mean_diff)

    # Gameplay is whatever is left. A facecam can't be subtracted rectangularly,
    # so only a side chat strip actually narrows the frame.
    gx, gw = 0, fw
    if chat and chat["w"] < fw * 0.45:
        if chat["x"] <= 2:
            gx, gw = chat["x"] + chat["w"], fw - (chat["x"] + chat["w"])
        elif chat["x"] + chat["w"] >= fw - 2:
            gw = chat["x"]
    gameplay = snap_rect(make_rect(gx, 0, max(2, gw), fh), fw, fh)

    out.update({"webcam": webcam, "gameplay": gameplay, "chat": chat, "hud": hud})
    out["confidence"] = {
        "webcam": w_conf, "chat": c_conf, "hud": h_conf,
        # A full-frame fallback is a default, not a detection — say so.
        "gameplay": 0.75 if gw < fw else 0.5,
    }
    return out
