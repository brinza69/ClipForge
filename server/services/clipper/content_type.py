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
    scene_independence,
    snap_rect,
    speech_ratio,
    summarize_faces,
    summarize_motion,
    transcript_text,
)

logger = logging.getLogger("clipforge.clipper.content_type")

# Re-exported: the facecam detector moved to its own module in the 2026-08-18
# split and everything that used it — the pipeline, the scoreboard script, the
# tests — reaches for it through this one.
#
# READING these here is fine. ASSIGNING to them is not: `content_facecam` reads
# its own module-level names, so `content_type._FACECAM_MIN_RATE = 0.15` binds a
# copy and the detector never sees it. Sweeping a constant is an ordinary thing
# to do on this corpus — it was done three times in one day — so patch
# `services.clipper.content_facecam` directly, and the same goes for
# monkeypatching `_snap_inset`.
from services.clipper.content_facecam import (  # noqa: E402,F401
    _FACECAM_CORNER_MIN,
    _FACECAM_EDGE_DOMINANCE,
    _FACECAM_INDEPENDENCE_MAX,
    _FACECAM_INNER,
    _FACECAM_MIN_HITS,
    _FACECAM_MIN_RATE,
    _FACECAM_OUTER,
    _FACECAM_PAD_H,
    _FACECAM_PAD_W,
    _FACECAM_REACH_CAP,
    _FACECAM_TOL,
    _LAYOUT_MIN_DOMINANT,
    _MIN_RANGE_FRAMES,
    _drop_transient_webcams,
    _face_groups,
    _find_webcam,
    _find_webcams,
    _snap_edge,
    _snap_inset,
    _step_profiles,
)

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


def _load_frames(frames: Sequence[str], limit: int = _MAX_FRAMES
                 ) -> tuple[list[Any], float, int, int]:
    """(grayscale frames, mean saturation, width, height). Never raises."""
    cv = _cv()
    grays: list[Any] = []
    sats: list[float] = []
    w = h = 0
    if cv is None:
        return grays, 0.0, 0, 0
    for path in _pick(frames, limit):
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


# How often a cluster has to appear to count as A PERSON IN THE SCENE. It is
# not the facecam bar and must not follow it: the facecam one asks "is this
# rectangle part of the layout", where a long sample legitimately dilutes the
# rate, and this one asks "is somebody there", where it does not.
#
# They were the same constant until 2026-08-17, and lowering the facecam bar
# from 0.15 to 0.10 — correct on its own terms, 8/9 to 9/9 — silently moved
# this one too. Measured on the same artifacts with only the code changed:
# Jynxzi's scene count went 0 to 1 and the source flipped from `gaming` to
# `talking_head`, taking the classifier from 6/11 to 5/11. moistcr1tikal and
# gym moved 1 to 2 and changed their (already wrong) answers.
#
# 0.15 is the value `_scene_faces` was measured at — clusters outside insets,
# 4 of 8 — so it stays here, pinned, with its own name.
_SCENE_MIN_RATE = 0.15


def _scene_faces(frames: Sequence[str]) -> int | None:
    """How many people are in the SCENE, ignoring anyone in a facecam inset.

    Replaces the modal faces-per-frame for the two-people question, and both
    halves of that matter.

    The MODAL count cannot answer it. Measured on the labelled sources it
    returns 1 or 0 everywhere — including 0 on the two Minecraft sources, which
    demonstrably have two faces on screen, because the cascade misses in enough
    frames that zero is the commonest answer. `two_up` needs 2 or 3, so
    `podcast` and `interview` could never win their own vote and never once did
    on any source. Counting stable CLUSTERS instead is robust to per-frame
    misses: it gets both Minecraft sources right, and an edited interview that
    cuts between speakers reads as several clusters rather than one face.

    Ignoring insets is the other half. Two people in facecam insets is a
    co-stream, not two people sitting together, and counting clusters alone
    turned the 12-minute Minecraft slice from `gaming` into `interview`.
    Excluding clusters that sit inside a detected webcam rect fixes that
    without touching the case it was added for.

    Measured end to end on the eight labelled sources: modal 3/8, clusters
    alone 3/8 (one fixed, one broken), clusters outside insets **4/8**.

    None when the frames cannot be read, so the caller keeps the old statistic
    rather than being told there is nobody on screen.
    """
    grays, _sat, fw, fh = _load_frames(frames or [])
    if not grays or fw < 16 or fh < 16:
        return None
    faces = _face_boxes(grays)
    total = max(1, len(faces))
    cams, _confs = _find_webcams(grays, faces, fw, fh)
    count = 0
    for group in _face_groups(faces, fw, fh):
        hits = len({i for i, _ in group})
        if hits < _FACECAM_MIN_HITS or hits / total < _SCENE_MIN_RATE:
            continue
        median = median_rect([b for _, b in group])
        if median and any(rect_overlap_frac(median, cam) > 0.5 for cam in cams):
            continue                      # this one is a facecam, not the scene
        count += 1
    return count


def detect_content_type(frames: list[str], signals: dict,
                        transcript: dict) -> dict[str, Any]:
    """{'content_type','confidence' 0..1,'evidence':[str,..]}"""
    signals = signals or {}
    features = frame_features(frames or [])
    fw = int(features.get("frame_width") or signals.get("frame_width") or 0)
    fh = int(features.get("frame_height") or signals.get("frame_height") or 0)
    features.update(summarize_motion(signals))
    features.update(summarize_faces(signals, fw, fh))
    scene = _scene_faces(frames or [])
    if scene is not None:
        features["face_count"] = float(scene)
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
    _drop_transient_webcams(out)
    return out


# Region detection gets the whole sample; `_MAX_FRAMES = 40` is right for the
# CLASSIFIER, which asks how stable a layout is and can answer that from a
# handful, and wrong here, where the question is whether a particular inset
# exists at all.
#
# Measured on the 4-hour co-stream: 400 frames sampled, 40 looked at, and the
# co-streamer's facecam lands 3 of those 40 — rate 0.075, under any usable bar.
# It is not rare, it is under-sampled. At 40, 100 and 200 frames the source
# returns one facecam; at 400 it returns both. Every labelled negative returns
# zero at every budget, including `IRL World Cup`, three hours of one camera at
# the full 400.
#
# The cost is face detection, measured at 0.052 s a frame on this rig: 21 s
# against 2 s, inside a stage that takes five minutes on a source that long.
# Capped rather than unbounded on purpose — raising
# `clipper_max_sampled_frames` past this should not silently make analysis
# slower, and nothing measured needs more.
_REGION_FRAMES = 400


def detect_regions(frames: list[str]) -> dict[str, Any]:
    """{'webcam','webcams','gameplay','chat','hud','confidence','frame_*'}

    `webcams` lists EVERY facecam found, best first; `webcam` is the best one
    and stays for callers that only ever wanted the streamer. A co-stream has
    one per person, and a layout that only knows about the first will frame the
    second as though it were the game.
    """
    grays, _sat, fw, fh = _load_frames(frames or [], _REGION_FRAMES)
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
