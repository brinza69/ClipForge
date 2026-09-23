"""Editorial framing envelope from elected face observations.

_face_samples discards box height; all face cameras use width as their zoom
scale. When a detector returns tall boxes (YuNet: width 64, height 84) the
narrower width under-sizes the crop. This module recovers height for the
samples _dominant elected, derives a separate framing_span (editorial sizing,
not a head-extent measurement), and widens per-shot rects to contain local
observation proposals whose original observed centre already sits inside the
current camera's delivered footprint.

Coordinate space is SOURCE pixels throughout.
"""
from __future__ import annotations

import math
from typing import Sequence

from services.clipper.dynamic_cameras import ASPECT, _f, _rect


def _median_f(values: Sequence[float], default: float = 0.0) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else default


def _footprint(rect: dict, src_w: int, src_h: int
               ) -> tuple[float, float, float, float] | None:
    """DELIVERED source bounds of a crop rect via the renderer's own arithmetic.

    Uses evidence_map.crop_window so containment checks account for renderer
    rounding. Returns (x0, y0, x1, y1) in source pixels (canvas offset
    subtracted), or None when crop_window refuses.
    """
    from services.clipper import evidence_map
    from services.clipper.dynamic_geometry import canvas_size
    shot = {"t0": 0.0, "t1": 1.0, "rect": rect, "move": "hold", "shake": 0.0,
            "anchor": [rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2],
            "composition": "crop"}
    result = evidence_map.crop_window(shot, src_w=src_w, src_h=src_h, style={})
    if isinstance(result, str):
        return None
    x, y, w, h = result
    off_y = canvas_size(src_w, src_h)[2]
    return float(x), float(y) - off_y, float(x + w), float(y + h) - off_y


# ---------------------------------------------------------------------------
# recover heights discarded by _face_samples
# ---------------------------------------------------------------------------

def elected_spans(
    face_track: Sequence[dict],
    elected: Sequence[tuple[float, float, float, float]],
    sx: float,
    sy: float,
    clip_start: float,
) -> list[tuple[float, float, float, float, float]]:
    """(abs_t, src_cx, src_cy, src_w, src_h) for each elected sample.

    Recovers the largest box's height (which _face_samples dropped) by
    matching on the FULL tuple (clip_t, src_cx, src_cy, src_w) — the same
    arithmetic _face_samples uses. No tolerance, no approximation: a nearby
    timestamp or a different face at the same time does not supply the height.
    A box with zero, negative, or non-finite height is excluded from matching;
    the elected sample is dropped rather than counted with a made-up square.

    Output carries ABSOLUTE source time (sample["t"]) so callers compare
    against shot boundaries without floating-point subtraction error.
    """
    if not elected:
        return []

    # Pre-compute valid track entries: (clip_t, src_cx, src_cy, src_w, abs_t, src_h)
    track_entries: list[tuple[float, float, float, float, float, float]] = []
    for sample in face_track or []:
        if not isinstance(sample, dict):
            continue
        boxes = [b for b in (sample.get("boxes") or [])
                 if isinstance(b, (list, tuple)) and len(b) >= 4]
        if not boxes:
            continue
        abs_t = _f(sample.get("t"))
        clip_t = abs_t - clip_start
        box = max(boxes, key=lambda b: _f(b[2]) * _f(b[3]))
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   and math.isfinite(v) for v in [*box[:4], sample.get("t")]):
            continue  # Reject the elected box; do not elect another in its place.
        src_cx = (_f(box[0]) + _f(box[2]) / 2.0) * sx
        src_cy = (_f(box[1]) + _f(box[3]) / 2.0) * sy
        src_w_val = _f(box[2]) * sx
        src_h_val = _f(box[3]) * sy
        # A box with invalid height cannot contribute an extent measurement.
        if not (math.isfinite(src_h_val) and src_h_val > 0):
            continue
        if not (math.isfinite(src_w_val) and src_w_val > 0):
            continue
        track_entries.append((clip_t, src_cx, src_cy, src_w_val,
                               abs_t, src_h_val))

    out = []
    for et, ecx, ecy, ew in elected:
        et, ecx, ecy, ew = float(et), float(ecx), float(ecy), float(ew)
        for clip_t, src_cx, src_cy, src_w_val, abs_t, src_h_val in track_entries:
            if (clip_t == et and src_cx == ecx
                    and src_cy == ecy and src_w_val == ew):
                out.append((abs_t, ecx, ecy, ew, src_h_val))
                break
        # No match → drop; never invent a square fallback
    return out


# ---------------------------------------------------------------------------
# editorial span
# ---------------------------------------------------------------------------

def compute_framing_span(
    spans: Sequence[tuple[float, float, float, float, float]],
    face_w: float,
) -> tuple[float, str]:
    """Median of max(src_w, src_h) over elected spans, floored by face_w.

    Square boxes (Haar 92×92): max(92, 92) = 92 = face_w → no change.
    Tall boxes (YuNet 64×84): max(64, 84) = 84 > 64 → span grows.
    """
    if not spans:
        return float(face_w), "no_elected_observations"
    extents = [max(w, h) for _, _, _, w, h in spans]
    span = max(float(face_w), _median_f(extents))
    return span, "elected_observation_envelope"


# ---------------------------------------------------------------------------
# per-shot proposals and union widening
# ---------------------------------------------------------------------------

def local_proposals(
    spans: Sequence[tuple[float, float, float, float, float]],
    t0: float,
    t1: float,
    mult: float,
    headroom: float,
    framing_span: float,
    src_w: int,
    src_h: int,
    clip_start: float,
    base: dict,
) -> tuple[list[dict], dict]:
    """One rect proposal per elected observation whose centre is inside base.

    Two-stage filter:
    1. Absolute-time interval: `clip_start + t0 <= abs_t < clip_start + t1`.
       Avoids float-subtraction trap; matches caption_faces.place convention.
    2. Centre-inside-base: the ORIGINAL observed centre (cx, cy) must lie inside
       the DELIVERED footprint of `base` (the face_framing result). This excludes
       false-positive detections on game textures that happen to fall inside the
       dominant cluster but outside the camera already chosen. It is NOT an
       identity claim; it is conditional protection of observations the camera
       already contains. Exclusions are counted, not discarded silently.

    A missing base footprint cannot authorize any spatially filtered proposal;
    those observations remain unavailable, rather than silently unfiltered.

    Returns (proposals, scope) where scope = {seen, included, excluded}.
    """
    abs_t0 = clip_start + t0
    abs_t1 = clip_start + t1
    base_fp = _footprint(base, src_w, src_h)
    if base_fp is None:
        seen = sum(abs_t0 <= s[0] < abs_t1 for s in spans)
        return [], {"seen": seen, "included": 0, "excluded": 0,
                    "unavailable": seen, "reason": "base_footprint_unavailable"}

    proposals: list[dict] = []
    seen = included = excluded = 0

    for abs_t, cx, cy, w, h in spans:
        if not (abs_t0 <= abs_t < abs_t1):
            continue
        seen += 1
        # Check original observed centre against base delivered footprint
        if not (base_fp[0] <= cx <= base_fp[2] and
                base_fp[1] <= cy <= base_fp[3]):
            excluded += 1
            continue
        obs_span = max(framing_span, w, h)
        p = _rect(0, obs_span * mult, cx, cy, headroom, src_w, src_h)
        if p.get("w", 0) > 0 and p.get("h", 0) > 0:
            proposals.append(p)
            included += 1
        else:
            excluded += 1

    return proposals, {"seen": seen, "included": included, "excluded": excluded}


def apply_local_envelope(
    face_track,
    spans: list,
    t0: float,
    t1: float,
    mult: float,
    headroom: float,
    framing_span: float,
    src_w: int,
    src_h: int,
    clip_start: float,
    sx: float,
    sy: float,
    rect: dict,
    framing_fit: bool,
    framing_reason: str | None,
) -> tuple[dict, bool, str | None, dict]:
    """Combine raw local proposals + motion proposals, widen base rect, return envelope entry.

    Extracts the 14-line block from dynamic_edit so dynamic_edit stays <=500 lines.
    Preserves all scope fields; motion provenance survives in envelope entry.
    Returns (rect, framing_fit, framing_reason, envelope_entry).
    """
    from services.clipper.face_gap_framing import motion_proposals

    props, scope = local_proposals(spans, t0, t1, mult, headroom,
                                   framing_span, src_w, src_h, clip_start, rect)
    mot_props, mot_scope = motion_proposals(
        face_track, spans, t0, t1, mult, headroom,
        framing_span, src_w, src_h, clip_start, sx, sy, rect)
    all_props = props + mot_props
    envelope_entry = {
        "source_t0": clip_start + t0, "source_t1": clip_start + t1,
        **scope,
        "motion": mot_scope,
    }
    new_rect, env_fit, env_reason = widen_for_envelope(rect, all_props, src_w, src_h)
    if env_reason:
        framing_fit = framing_fit or env_fit
        sep = "+" if framing_reason else ""
        framing_reason = (framing_reason or "") + sep + env_reason
    return new_rect, framing_fit, framing_reason, envelope_entry


def widen_for_envelope(base: dict, proposals: list[dict], src_w: int,
                       src_h: int) -> tuple[dict, bool, str | None]:
    """Retain every delivered footprint, including the original camera.

    Unknown renderer geometry is a refusal to derive a replacement, never a
    reason to switch back to comparing plan rectangles. Full fit is used only
    for a known union that cannot fit the portrait crop.
    """
    if not proposals:
        return dict(base), False, None
    footprints = [_footprint(r, src_w, src_h) for r in [base, *proposals]]
    if any(fp is None for fp in footprints):
        return dict(base), False, "envelope_footprint_unavailable"
    base_fp = footprints[0]
    left = min(fp[0] for fp in footprints)
    top = min(fp[1] for fp in footprints)
    right = max(fp[2] for fp in footprints)
    bottom = max(fp[3] for fp in footprints)
    if (base_fp[0] <= left and base_fp[1] <= top
            and base_fp[2] >= right and base_fp[3] >= bottom):
        return dict(base), False, None
    # Four pixels each side cover even-coordinate rounding and anchor clamps.
    h = math.ceil(max(bottom - top + 8, (right - left + 8) / ASPECT) / 2) * 2
    if h > src_h:
        return dict(base), True, "envelope_union_exceeds_frame_fit"
    rect = _rect(0, h, (left + right) / 2, (top + bottom) / 2, .5, src_w, src_h)
    actual = _footprint(rect, src_w, src_h)
    if actual is None:
        return dict(base), False, "envelope_footprint_unavailable"
    if (actual[0] <= left and actual[1] <= top
            and actual[2] >= right and actual[3] >= bottom):
        return rect, False, "widened_for_local_envelope"
    return dict(base), True, "envelope_union_fit_failed"
