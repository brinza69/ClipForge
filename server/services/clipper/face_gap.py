"""Short optical-flow continuation of face-detector gaps.

API: ``continue_gaps(path, samples) -> list[dict]``

Seeds on a 'detected' sample with exactly ONE box and a finite FFMPEG address
(frame_index before read, decoded_t after read). Fills only 'empty' samples in
runs immediately following that seed. Raw samples are never mutated; returned
copies carry an added 'motion' key for every empty sample in the run.

Tracking is a bounded heuristic: individual forward/backward error filtering,
float box translation (no accumulated rounding), seed corners held without
reseeding, local appearance check at the translated box. Stops on budget
expiry, support collapse, appearance break, box departure, or non-finite flow.
Budget 2 s is an engineering bound, not a calibrated threshold.

No reseeding from tracked proposals. No identity claims. No source-clock
fabrication: target_decoded_t is the window PTS the codec returned, not
start + PTS.
"""
from __future__ import annotations

import math
import logging
from copy import deepcopy
from typing import Any, Sequence

logger = logging.getLogger("clipforge.clipper.face_gap")

# ── named constants ──────────────────────────────────────────────────────────
BUDGET_S = 2.0           # engineering bound; do not raise to make a clip pass
METHOD_VERSION = "lk_fb_v1"
MIN_CORNERS = 8          # minimum GFTT corners for texture support
MIN_SURVIVORS = 4        # minimum per-step FB survivors to continue
MIN_SPREAD_PX = 2.0      # spatial std on EACH axis; engineering bound
MIN_SUPPORT_FRACTION = 0.5  # surviving ORIGINAL corners; not calibrated
FB_THRESH_PX = 1.5       # per-point max forward-backward error (pixels)
APPEAR_THRESH = 0.30     # mean normalised diff at translated box → stop
LK_WIN = (15, 15)
LK_LEVELS = 3
MASK_INSET = 2           # pixels inset from box edge for GFTT mask
GFTT_QUALITY = 0.01
GFTT_MIN_DIST = 3.0
# Stored detector PTS is rounded to six decimals, not to half a frame.
_DT_TOL = 0.000001


# ── tiny imports ─────────────────────────────────────────────────────────────

def _cv2() -> Any:
    try:
        import cv2  # noqa: PLC0415
        return cv2
    except ImportError:
        return None


def _np() -> Any:
    try:
        import numpy as np  # noqa: PLC0415
        return np
    except ImportError:
        return None


# ── address helpers ───────────────────────────────────────────────────────────

def _is_valid_fi(fi: Any) -> bool:
    return _is_valid_dt(fi) and float(fi).is_integer()


def _is_valid_dt(dt: Any) -> bool:
    return (isinstance(dt, (int, float)) and not isinstance(dt, bool)
            and math.isfinite(dt) and dt >= 0)


# ── motion record constructors ────────────────────────────────────────────────

def _refused(seed_fi: Any, seed_dt: Any, seed_box: Any,
             target_fi: Any, target_dt: Any, reason: str,
             **extra: Any) -> dict:
    return {"state": "refused", "method": METHOD_VERSION, "reason": reason,
            "seed_frame_index": seed_fi, "seed_decoded_t": seed_dt,
            "seed_box": list(seed_box) if isinstance(seed_box, (list, tuple)) else seed_box,
            "target_frame_index": target_fi, "target_decoded_t": target_dt,
            **extra}


def _tracked(seed_fi: int, seed_dt: float, seed_box: list,
             target_fi: int, target_dt: float, age: float,
             box: list, n_survivors: int, fb_errs: list, appear_d: float) -> dict:
    return {"state": "tracked", "method": METHOD_VERSION,
            "seed_frame_index": seed_fi, "seed_decoded_t": seed_dt,
            "seed_box": list(seed_box),
            "target_frame_index": target_fi, "target_decoded_t": target_dt,
            "age_s": round(float(age), 6),
            "box": list(box),
            "quality": {"n_survivors": n_survivors,
                        "fb_err_max": round(float(max(fb_errs)), 4) if fb_errs else None,
                        "appear_delta": round(float(appear_d), 6)}}


# ── optical-flow helpers ──────────────────────────────────────────────────────

def _gftt_mask(grey: Any, box: list, np: Any) -> Any:
    mask = np.zeros(grey.shape, dtype=np.uint8)
    x = int(box[0]) + MASK_INSET
    y = int(box[1]) + MASK_INSET
    x2 = int(box[0]) + int(box[2]) - MASK_INSET
    y2 = int(box[1]) + int(box[3]) - MASK_INSET
    if x2 > x and y2 > y:
        mask[y:y2, x:x2] = 255
    return mask


def _get_corners(cv2: Any, np: Any, grey: Any, box: list) -> Any | None:
    """GFTT on inset box region. Returns Nx1x2 float32 or None."""
    mask = _gftt_mask(grey, box, np)
    if not mask.any():
        return None
    pts = cv2.goodFeaturesToTrack(grey, maxCorners=60, qualityLevel=GFTT_QUALITY,
                                   minDistance=GFTT_MIN_DIST, mask=mask)
    if pts is None or len(pts) < MIN_CORNERS:
        return None
    return pts


def _lk_fb_filter(cv2: Any, np: Any,
                   grey_prev: Any, grey_next: Any,
                   pts: Any) -> tuple[Any | None, Any | None, list]:
    """Individual forward+backward filter. Returns (prev_survivors, next_survivors, errs)."""
    lk = dict(winSize=LK_WIN, maxLevel=LK_LEVELS,
               criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))
    fwd, sf, _ = cv2.calcOpticalFlowPyrLK(grey_prev, grey_next, pts, None, **lk)
    if fwd is None or sf is None:
        return None, None, []
    ok_f = (sf.ravel() == 1)
    if not ok_f.any():
        return None, None, []
    pts_f = fwd[ok_f]
    pts_p = pts[ok_f]
    bwd, sb, _ = cv2.calcOpticalFlowPyrLK(grey_next, grey_prev, pts_f, None, **lk)
    if bwd is None or sb is None:
        return None, None, []
    ok_b = (sb.ravel() == 1)
    pts_f2 = pts_f[ok_b]
    pts_p2 = pts_p[ok_b]
    bwd2 = bwd[ok_b]
    if len(pts_f2) == 0:
        return None, None, []
    # Per-point FB error: max abs deviation in x or y
    fb_err = np.abs(pts_p2 - bwd2).reshape(-1, 2).max(axis=1)
    good = fb_err <= FB_THRESH_PX
    if not good.any():
        return None, None, []
    return pts_p2[good], pts_f2[good], fb_err[good].tolist()


def _appear_delta(grey_tgt: Any, float_box: list, seed_patch: Any,
                   cv2: Any, np: Any) -> float:
    """Mean normalised abs diff between seed_patch and translated patch in target."""
    x = int(round(float_box[0]))
    y = int(round(float_box[1]))
    w = int(float_box[2])
    h = int(float_box[3])
    gh, gw = grey_tgt.shape
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(gw, x + w), min(gh, y + h)
    if x2 <= x1 or y2 <= y1 or seed_patch.size == 0:
        return 1.0
    tgt = grey_tgt[y1:y2, x1:x2]
    sz = (max(4, tgt.shape[1]), max(4, tgt.shape[0]))
    a = cv2.resize(seed_patch, sz).astype(np.float32)
    b = cv2.resize(tgt, sz).astype(np.float32)
    return float(np.abs(a - b).mean() / 255.0)


def _seed_patch(grey: Any, box: list) -> Any:
    x, y, w, h = int(box[0]), int(box[1]), int(box[2]), int(box[3])
    gh, gw = grey.shape
    return grey[max(0, y):min(gh, y + h), max(0, x):min(gw, x + w)].copy()


def _spatial_spread(pts: Any, np: Any) -> float:
    p = pts.reshape(-1, 2)
    return float(min(p[:, 0].std(), p[:, 1].std()))


def _box_in_frame(box: list, fw: int, fh: int) -> bool:
    return (float(box[0]) >= 0 and float(box[1]) >= 0
            and float(box[0]) + float(box[2]) <= fw
            and float(box[1]) + float(box[3]) <= fh)


# ── main API ──────────────────────────────────────────────────────────────────

def _address_valid(row: dict) -> bool:
    return (_is_valid_fi(row.get("frame_index")) and
            _is_valid_dt(row.get("decoded_t")) and
            row.get("address_basis") == "opencv_ffmpeg_metadata" and
            row.get("decoded_space") == "analysed_file")


def _read_exact(cap: Any, cv2: Any, index: int,
                previous_t: float | None = None) -> tuple[Any, float]:
    # Never turn a fractional/unavailable decoder index into an integer match.
    before = cap.get(cv2.CAP_PROP_POS_FRAMES)
    if not _is_valid_fi(before) or before != index:
        raise ValueError("frame_index_mismatch")
    ok, frame = cap.read()
    if not ok or frame is None:
        raise ValueError("frame_unreadable")
    dt = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
    if not _is_valid_dt(dt) or (previous_t is not None and dt <= previous_t):
        raise ValueError("decoded_t_invalid")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), dt


def continue_gaps(path: str, samples: Sequence[dict]) -> list[dict]:
    """Add motion metadata to copies; failures remain visible for every empty row.

    Only clean empty runs following one addressed detection are eligible.
    No point is reseeded, no raw box/state is changed, and one failed step
    ends the entire run. PTS belong to the analysed file, never the source.
    """
    result = deepcopy(list(samples))
    cv2, np = _cv2(), _np()
    if not samples:
        return result
    for row in result:
        if row.get("state") == "empty":
            row["motion"] = _refused(None, None, None, row.get("frame_index"),
                                     row.get("decoded_t"), "no_single_detected_seed")
    if cv2 is None or np is None:
        for row in result:
            if row.get("state") == "empty":
                row["motion"]["reason"] = "tracking_unavailable"
        return result
    cap = cv2.VideoCapture(str(path))
    try:
        try:
            available = cap.isOpened() and cap.getBackendName() == "FFMPEG"
        except cv2.error:
            available = False
        if not available:
            for row in result:
                if row.get("state") == "empty":
                    row["motion"]["reason"] = "tracking_capture_unavailable"
            return result
        i = 0
        while i + 1 < len(samples):
            seed = samples[i]
            if (seed.get("state") != "detected" or
                    not isinstance(seed.get("boxes"), list) or
                    len(seed["boxes"]) != 1 or samples[i+1].get("state") != "empty"):
                i += 1
                continue
            end = i + 1
            while end < len(samples) and samples[end].get("state") == "empty":
                end += 1
            seed_fi, seed_dt = seed.get("frame_index"), seed.get("decoded_t")
            seed_box = seed["boxes"][0]
            stopped = None
            try:
                if not _address_valid(seed):
                    raise ValueError("invalid_seed_address")
                seed_fi = int(seed_fi)
                if (not isinstance(seed_box, (list, tuple)) or len(seed_box) != 4 or
                        not all(_is_valid_dt(v) for v in seed_box) or
                        seed_box[2] <= 0 or seed_box[3] <= 0):
                    raise ValueError("invalid_seed_box")
                if not cap.set(cv2.CAP_PROP_POS_FRAMES, seed_fi):
                    raise ValueError("seed_seek_failed")
                previous, actual_seed_t = _read_exact(cap, cv2, seed_fi)
                if abs(actual_seed_t - seed_dt) > _DT_TOL:
                    raise ValueError("seed_decoded_t_mismatch")
                fh, fw = previous.shape
                if not _box_in_frame(seed_box, fw, fh):
                    raise ValueError("seed_box_outside_frame")
                points = _get_corners(cv2, np, previous, seed_box)
                if points is None:
                    raise ValueError("seed_texture_unavailable")
                initial_count = len(points)
                patch = _seed_patch(previous, seed_box)
                box = [float(v) for v in seed_box]
                current_fi, previous_t = seed_fi, actual_seed_t
            except (ValueError, cv2.error) as exc:
                stopped = str(exc) if isinstance(exc, ValueError) else "opencv_error"
            for j in range(i+1, end):
                target = samples[j]
                tfi, tdt = target.get("frame_index"), target.get("decoded_t")
                try:
                    if stopped:
                        raise ValueError(stopped)
                    if not _address_valid(target) or target.get("boxes") != []:
                        raise ValueError("invalid_target_address_or_state")
                    tfi = int(tfi)
                    if tfi <= current_fi or tdt <= previous_t:
                        raise ValueError("non_monotonic_address")
                    if tdt - actual_seed_t > BUDGET_S + _DT_TOL:
                        raise ValueError("budget_exceeded")
                    for fi in range(current_fi+1, tfi+1):
                        grey, actual_t = _read_exact(cap, cv2, fi, previous_t)
                        if actual_t - actual_seed_t > BUDGET_S + _DT_TOL:
                            raise ValueError("budget_exceeded")
                        before, after, errors = _lk_fb_filter(
                            cv2, np, previous, grey, points)
                        if (after is None or not np.isfinite(after).all() or
                                len(after) < max(MIN_SURVIVORS,
                                                initial_count * MIN_SUPPORT_FRACTION)):
                            raise ValueError("support_collapse")
                        if _spatial_spread(after, np) < MIN_SPREAD_PX:
                            raise ValueError("support_spread_too_low")
                        delta = np.median((after-before).reshape(-1,2), axis=0)
                        moved = [box[0]+float(delta[0]), box[1]+float(delta[1]),
                                 box[2], box[3]]
                        if not _box_in_frame(moved, fw, fh):
                            raise ValueError("box_left_frame")
                        appearance = _appear_delta(grey, moved, patch, cv2, np)
                        if not math.isfinite(appearance) or appearance > APPEAR_THRESH:
                            raise ValueError("appearance_break")
                        points, box, previous, previous_t = after, moved, grey, actual_t
                    if abs(actual_t - tdt) > _DT_TOL:
                        raise ValueError("target_decoded_t_mismatch")
                    motion = _tracked(seed_fi, seed_dt, seed_box, tfi, tdt,
                        actual_t - actual_seed_t, box, len(points), errors, appearance)
                    motion["quality"]["initial_corners"] = initial_count
                    result[j]["motion"] = motion
                    current_fi = tfi
                except (ValueError, cv2.error) as exc:
                    stopped = str(exc) if isinstance(exc, ValueError) else "opencv_error"
                    result[j]["motion"] = _refused(seed_fi, seed_dt, seed_box,
                                                   tfi, tdt, stopped)
            i = end
    finally:
        cap.release()
    return result
