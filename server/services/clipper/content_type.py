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
    _WEBCAM_AREA,
    _WEBCAM_ASPECT,
    _WORK_WIDTH,
    aspect_ok,
    clamp01,
    classify_features,
    corner_proximity,
    keyword_scores,
    make_rect,
    median_rect,
    rect_area_frac,
    rect_aspect,
    rect_centre,
    rect_overlap_frac,
    snap_rect,
    speech_ratio,
    summarize_faces,
    summarize_motion,
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
    """(corner stability, centre motion), both 0..1, both RELATIVE.

    Each is measured against the same frame pair's own overall change, never
    against a fixed amount of pixel difference.

    The absolute version compared to 0.08, on the grounds that "0.08 mean abs
    diff is a fully-changed patch in practice — brightness flicker sits well
    below it". That is true of ADJACENT video frames, and these are not: the
    frames handed to the classifier are sampled across the whole source, so on
    a 4-hour VOD two consecutive ones are ~36 seconds apart and everything in
    both patches has changed. Measured on five sources, `corner_stability` came
    back 0.000 on four of them and `centre_motion` 1.000 on all five — so
    `hud_signal = corner * centre` was 0 everywhere, and the 1.8-weight gaming
    vote it feeds could never fire. `screen_like` died the same way.

    Fifth time an absolute threshold has measured nothing here, after
    audio_peak_ratio, audio_dynamic_range, game_ui_ratio and the atom marks.
    Same fix every time: compare a thing to its own context.
    """
    if len(grays) < 2 or w < 8 or h < 8:
        return 1.0, 0.0
    cw, ch = max(4, int(w * _CORNER_FRAC)), max(4, int(h * _CORNER_FRAC))
    boxes = [(0, 0), (w - cw, 0), (0, h - ch), (w - cw, h - ch)]
    corner_d, centre_d, overall_d = [], [], []
    for a, b in zip(grays, grays[1:]):
        d = np.abs(a.astype(np.int16) - b.astype(np.int16))
        corner_d.append(float(np.mean([np.mean(d[y:y + ch, x:x + cw]) for x, y in boxes])) / 255.0)
        centre_d.append(float(np.mean(d[h // 4:h - h // 4, w // 4:w - w // 4])) / 255.0)
        overall_d.append(float(np.mean(d)) / 255.0)
    corner = float(np.mean(corner_d))
    centre = float(np.mean(centre_d))
    overall = float(np.mean(overall_d))
    if overall <= 1e-9:
        return 1.0, 0.0  # nothing changed anywhere; no evidence either way
    # Stability: how much LESS the corners change than the frame as a whole.
    # Motion: how much MORE the middle changes than the border — which is the
    # thing the HUD signal was always trying to say.
    return (clamp01(1.0 - corner / overall),
            clamp01(1.0 - corner / max(centre, 1e-9)))


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
    """Per-frame face rects, through the ONE tuned detector in signals.py.

    This used to run its own cascade at its own settings. On the co-stream that
    detector found 0 faces in 40 frames where the tuned one finds 27, which is
    the whole reason regions.json reported no webcam on a source with two.
    """
    from services.clipper.signals import detect_faces  # noqa: PLC0415 — Pass B uses Pass A

    out: list[list[dict]] = []
    for gray in grays:
        try:
            found = detect_faces(gray)
        except Exception as exc:
            logger.warning("clipper: face detection failed on a frame (%s)", exc)
            found = []
        out.append([make_rect(*box) for box in found])
    return out


# A facecam that a Haar cascade sees in a THIRD of frames is doing well: the
# streamer looks away, leans out, gets covered by an alert. Measured on the
# co-stream's 40 sampled frames, the two real facecams landed 14 and 13 hits;
# the busiest false position landed 1. The old gate wanted half the frames,
# which neither real facecam could ever have cleared.
_FACECAM_MIN_HITS = 3
_FACECAM_MIN_RATE = 0.15
# Faces whose centres sit this close, as a frame fraction, are one person.
_FACECAM_TOL = 0.12
# Where the inset's edge is looked for, as multiples of the median face box.
# Below the inner bound is the face itself; past the outer one is the game.
# On the co-stream the two insets measure 5.3x and 4.5x the face width, so the
# outer bound has to be generous — a facecam frames a head with a lot of room.
_FACECAM_INNER, _FACECAM_OUTER = 0.75, 4.0
# Fallback when no border step is found in range: the face box, padded.
_FACECAM_PAD_W, _FACECAM_PAD_H = 2.6, 2.2
# When the search runs into the frame border there may be no edge to find: a
# facecam flush to the corner has no drawn boundary there, so the strongest
# step in range is whatever texture happened to be inside it. The frame border
# therefore wins unless the interior peak is clearly a real boundary.
#
# Measured on both test projects, peak-over-median for edges that touch the
# frame: real boundaries 5.8 and 3.6, interior texture 2.4, 2.4 and 2.3. Real
# boundaries away from the frame run 6.0 to 71. 3.0 sits in the gap with room
# on both sides.
_FACECAM_EDGE_DOMINANCE = 3.0
# Below this a stretch has too few frames for the face cluster to clear its own
# hit-rate gate, and the answer would be "no facecam" for lack of evidence
# rather than for lack of a facecam.
_MIN_RANGE_FRAMES = 12


def _step_profiles(grays: Sequence[Any]) -> tuple[Any, Any]:
    """(|dI/dx|, |dI/dy|) averaged over frames.

    Averaging before thresholding is the point. A composited border is an edge
    at the SAME pixel in every frame, but on a compressed 480p proxy Canny
    flickers by a pixel and per-frame edge maps do not stack — measured, a
    persistence map of the co-stream produced no usable border lines at all.
    The underlying gradient does not flicker: averaged, the left inset's edge
    stands at x=122 with 26.8 against a neighbourhood under 8.
    """
    stack = np.stack([g.astype(np.float32) for g in grays])
    return (np.abs(np.diff(stack, axis=2)).mean(axis=0),
            np.abs(np.diff(stack, axis=1)).mean(axis=0))


def _snap_edge(profile: Any, seed: float, inner: float, outer: float,
               limit: int, forward: bool) -> int:
    """The inset's edge on one axis: the strongest step between the face and
    the game, or the frame border when the facecam runs into it."""
    lo = int(round(seed + inner)) if forward else int(round(seed - inner))
    hi = int(round(seed + outer)) if forward else int(round(seed - outer))
    lo, hi = (lo, min(limit, hi)) if forward else (max(0, hi), lo)
    if hi - lo < 2 or profile.size == 0:
        return max(0, min(limit, lo if forward else hi))
    window = profile[lo:min(hi, profile.shape[0])]
    if window.size == 0:
        return max(0, min(limit, lo if forward else hi))
    at = lo + int(np.argmax(window))

    touches_frame = (hi >= limit - 1) if forward else (lo <= 1)
    if touches_frame:
        median = float(np.median(window))
        dominant = median > 1e-6 and float(window.max()) / median >= _FACECAM_EDGE_DOMINANCE
        if not dominant:
            return limit if forward else 0
    return at


def _snap_inset(seed: dict, gx: Any, gy: Any, fw: int, fh: int) -> dict:
    """Grow the median face box out to the inset's real bounds.

    The face cluster answers WHERE reliably and HOW BIG not at all — measured
    on the co-stream, the insets are 4.5x and 5.3x the face box, and no single
    padding factor covers both plus a full-screen webcam. Given a seed the
    bounds are a well-posed search though: step outward until the picture
    changes, which is exactly what the averaged gradient marks.
    """
    cx, cy = rect_centre(seed)
    half_w, half_h = seed["w"] / 2.0, seed["h"] / 2.0
    rows = slice(max(0, int(cy - half_h)), max(1, int(cy + half_h)))
    cols = slice(max(0, int(cx - half_w)), max(1, int(cx + half_w)))

    col_prof = gx[rows, :].mean(axis=0) if gx.size else np.zeros(0)
    row_prof = gy[:, cols].mean(axis=1) if gy.size else np.zeros(0)

    x0 = _snap_edge(col_prof, cx, half_w * _FACECAM_INNER * 2,
                    half_w * _FACECAM_OUTER * 2, fw, forward=False)
    x1 = _snap_edge(col_prof, cx, half_w * _FACECAM_INNER * 2,
                    half_w * _FACECAM_OUTER * 2, fw, forward=True)
    y0 = _snap_edge(row_prof, cy, half_h * _FACECAM_INNER * 2,
                    half_h * _FACECAM_OUTER * 2, fh, forward=False)
    y1 = _snap_edge(row_prof, cy, half_h * _FACECAM_INNER * 2,
                    half_h * _FACECAM_OUTER * 2, fh, forward=True)

    if x1 - x0 < seed["w"] or y1 - y0 < seed["h"]:
        return make_rect(cx - seed["w"] * _FACECAM_PAD_W / 2.0,
                         cy - seed["h"] * _FACECAM_PAD_H / 2.0,
                         seed["w"] * _FACECAM_PAD_W, seed["h"] * _FACECAM_PAD_H)
    return make_rect(x0, y0, x1 - x0, y1 - y0)


def _face_groups(faces: Sequence[Sequence[dict]], fw: int, fh: int
                 ) -> list[list[tuple[int, dict]]]:
    """Detections grouped by where they sit — one group per person on screen.

    A co-stream has a facecam per person, and taking the median of every
    detection lands between them, on the gameplay.
    """
    groups: list[list[tuple[int, dict]]] = []
    for i, boxes in enumerate(faces):
        for box in boxes:
            cx, cy = rect_centre(box)
            for g in groups:
                centres = [rect_centre(b) for _, b in g]
                gx = sum(c[0] for c in centres) / len(centres)
                gy = sum(c[1] for c in centres) / len(centres)
                if (abs(cx - gx) <= _FACECAM_TOL * fw
                        and abs(cy - gy) <= _FACECAM_TOL * fh):
                    g.append((i, box))
                    break
            else:
                groups.append([(i, box)])
    return groups


def _find_webcams(grays: Sequence[Any], faces: Sequence[Sequence[dict]],
                  fw: int, fh: int) -> tuple[list[dict], list[float]]:
    """Every facecam inset, best first, with a confidence each.

    Built FROM the faces, not confirmed by them. The old version searched for a
    rectangular border contour and then asked whether a face sat inside it, and
    on the co-stream that never fired once: measured over 40 frames it produced
    10 candidate rects, nine of them seen in a single frame, so there was no
    stable rectangle for a face to be inside of, and regions.json reported no
    webcam on a source with two. The faces are the reliable half of that pair —
    27 hits against 1 false positive — so the cluster seeds the search and the
    averaged gradient supplies the bounds.

    What separates a facecam from a texture the cascade likes is that IT IS
    ALWAYS IN THE SAME PLACE. A wandering game camera drags a false positive
    around with it; an inset is pixel-locked.
    """
    frames = max(1, len(faces))
    gx, gy = _step_profiles(grays)

    scored: list[tuple[float, dict]] = []
    for group in _face_groups(faces, fw, fh):
        hits = len({i for i, _ in group})
        rate = hits / float(frames)
        if hits < _FACECAM_MIN_HITS or rate < _FACECAM_MIN_RATE:
            continue
        median = median_rect([b for _, b in group])
        if median is None:
            continue
        snapped = snap_rect(_snap_inset(median, gx, gy, fw, fh), fw, fh)
        if not snapped:
            continue
        # An inset is small and landscape. Without this a wide IRL shot with
        # people in it reports its own right half as a facecam — measured on
        # the gym-camera project, area 0.36 and aspect 0.78, outside both
        # bounds. The check has to sit on the FINAL rect: the fallback padding
        # would sail through it too.
        if not (_WEBCAM_AREA[0] <= rect_area_frac(snapped, fw, fh) <= _WEBCAM_AREA[1]):
            continue
        if not aspect_ok(snapped, *_WEBCAM_ASPECT):
            continue
        # Spread of the cluster's centres, as a share of its own size: a real
        # inset holds still, a false positive drifts with the game camera.
        centres = [rect_centre(b) for _, b in group]
        drift = clamp01(max(
            (max(c[0] for c in centres) - min(c[0] for c in centres)) / max(1.0, snapped["w"]),
            (max(c[1] for c in centres) - min(c[1] for c in centres)) / max(1.0, snapped["h"]),
        ))
        score = (0.45 * clamp01(rate / 0.35) + 0.30 * (1.0 - drift)
                 + 0.25 * corner_proximity(snapped, fw, fh))
        scored.append((round(clamp01(score), 3), snapped))

    scored.sort(key=lambda s: -s[0])
    return [r for _, r in scored], [s for s, _ in scored]


def _find_webcam(grays: Sequence[Any], faces: Sequence[Sequence[dict]],
                 fw: int, fh: int) -> tuple[dict | None, float]:
    rects, confs = _find_webcams(grays, faces, fw, fh)
    return (rects[0], confs[0]) if rects else (None, 0.0)


def _band_stats(rect: dict, edges: Sequence[Any], diffs: Sequence[Any]) -> tuple[float, float]:
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    area = float(max(1, w * h))
    ed = float(np.mean([np.count_nonzero(e[y:y + h, x:x + w]) / area for e in edges]))
    mo = (float(np.mean([np.mean(d[y:y + h, x:x + w]) for d in diffs])) / 255.0) if diffs else 0.0
    return ed, mo


def _overlaps_any(rect: dict, others: Sequence[dict | None], limit: float) -> bool:
    return any(rect_overlap_frac(rect, o) > limit for o in others if o)


def _find_chat(edges: Sequence[Any], diffs: Sequence[Any], fw: int, fh: int,
               webcams: Sequence[dict | None], mean_edge: float,
               mean_diff: float) -> tuple[dict | None, float]:
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
            if _overlaps_any(rect, webcams, 0.3):
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
              webcams: Sequence[dict | None], chat: dict | None,
              mean_edge: float, mean_diff: float) -> tuple[list[dict], float]:
    """Edge-dense, near-static grid cells hugging the frame border. These become
    caption keep-out zones, so over-detecting only costs caption real estate.

    Every facecam is excluded, not just the first: on the co-stream the second
    one landed in regions.json as a HUD rect, which is both wrong and the
    reason nothing downstream knew a second person was on screen.
    """
    cw, ch = max(2, fw // _GRID_COLS), max(2, fh // _GRID_ROWS)
    kept: dict[tuple[int, int], float] = {}
    for row in range(_GRID_ROWS):
        for col in range(_GRID_COLS):
            if 0 < col < _GRID_COLS - 1 and 0 < row < _GRID_ROWS - 1:
                continue
            rect = make_rect(col * cw, row * ch, cw, ch)
            if _overlaps_any(rect, list(webcams) + [chat], 0.4):
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


def detect_regions_by_range(frames: Sequence[str], frame_times: Sequence[float],
                            ranges: Sequence[tuple[float, float]]
                            ) -> list[dict[str, Any]]:
    """`detect_regions` per stretch of the stream, instead of once for the file.

    Layout is detected ONCE for a whole source, and seven of the eleven sources
    labelled by hand change layout part-way through. On slice4h00test the whole
    file returns ONE facecam for a stream that demonstrably has two, because the
    400 sampled frames are spread across four hours and the first 35 minutes are
    a full-frame camera with no game in them. The detection blends two layouts
    and fits neither.

    Nothing about HOW a region is found changes here — the same function runs on
    fewer frames at a time. The same source cut into 12-minute pieces already
    finds both insets, which is what says the failure is the averaging.

    Each entry is a regions blob with `start`/`end` added.
    """
    out: list[dict[str, Any]] = []
    for start, end in ranges or []:
        mine = [f for f, t in zip(frames, frame_times) if start <= t <= end]
        if len(mine) < _MIN_RANGE_FRAMES:
            continue
        blob = detect_regions(mine)
        blob["start"], blob["end"] = round(start, 2), round(end, 2)
        out.append(blob)
    return out


def detect_regions(frames: list[str]) -> dict[str, Any]:
    """{'webcam','webcams','gameplay','chat','hud','confidence','frame_*'}

    `webcams` lists EVERY facecam found, best first; `webcam` is the best one
    and stays for callers that only ever wanted the streamer. A co-stream has
    one per person, and a layout that only knows about the first will frame the
    second as though it were the game.
    """
    grays, _sat, fw, fh = _load_frames(frames or [])
    out: dict[str, Any] = {
        "webcam": None, "webcams": [], "gameplay": None, "chat": None, "hud": [],
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

    webcams, w_confs = _find_webcams(grays, _face_boxes(grays), fw, fh)
    webcam = webcams[0] if webcams else None
    w_conf = w_confs[0] if w_confs else 0.0
    chat, c_conf = _find_chat(edges, diffs, fw, fh, webcams, mean_edge, mean_diff)
    hud, h_conf = _find_hud(edges, diffs, fw, fh, webcams, chat, mean_edge, mean_diff)

    # Gameplay is whatever is left. A facecam can't be subtracted rectangularly,
    # so only a side chat strip actually narrows the frame.
    gx, gw = 0, fw
    if chat and chat["w"] < fw * 0.45:
        if chat["x"] <= 2:
            gx, gw = chat["x"] + chat["w"], fw - (chat["x"] + chat["w"])
        elif chat["x"] + chat["w"] >= fw - 2:
            gw = chat["x"]
    gameplay = snap_rect(make_rect(gx, 0, max(2, gw), fh), fw, fh)

    out.update({"webcam": webcam, "webcams": webcams, "gameplay": gameplay,
                "chat": chat, "hud": hud})
    out["confidence"] = {
        "webcam": w_conf, "webcams": w_confs, "chat": c_conf, "hud": h_conf,
        # A full-frame fallback is a default, not a detection — say so.
        "gameplay": 0.75 if gw < fw else 0.5,
    }
    return out
