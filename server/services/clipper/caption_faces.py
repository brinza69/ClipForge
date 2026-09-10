"""Stable caption placement around local face proposals in the rendered crop.

This is a placement heuristic, NOT a quality gate: face_presence uses requested
seek times, can miss faces (or find false ones), and the caption envelope is the
shared approximate two-line box, not a measurement of burned glyphs. No result
here establishes coverage between observations or the absence of source text.
"""
from __future__ import annotations

import math

from services.clipper import evidence_map
from services.clipper.captions_geom import (
    CAPTION_BOX_H_PCT, SPEC_BAND_LO, SPEC_BAND_HI,
    _widest_band, scan_bounds, scan_grid,
)


def place(plan: dict, dyn: dict | None, *, start: float, src_w: int,
          src_h: int, current_y: float, keep_out: list[dict]) -> dict:
    """Return one height plus its limitations; never mutate the supplied plan.

    Unknown geometry leaves the position alone. Missing detections are counted
    separately; they cannot veto avoidance of a positively observed face, but
    neither can they certify the unobserved part of a clip as clear.
    """
    report = {"schema": "clipper_caption_faces_v1", "applied": False,
              "y_pct": current_y, "coverage_complete": False,
              "caption_extent": "approximate_full_width_two_line_band",
              "face_basis": "detector_proposals_at_requested_source_times",
              "mapped_boxes": 0, "empty_samples": 0, "off_frame_boxes": 0,
              "shots_without_mapped_faces": 0}

    def stop(reason):
        return {**report, "reason": reason}

    if plan.get("y_pct_manual"):
        return stop("manual_position")
    if not dyn or not dyn.get("shots"):
        return stop("no_dynamic_observations")
    space = dyn.get("_face_space") or {}
    pw, ph = space.get("width"), space.get("height")
    if (space.get("clock") != "source_requested" or
            not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) and v > 0 for v in (pw, ph))):
        return stop("face_coordinate_space_unknown")
    # The existing envelope was designed for <=72px, two-line captions. Do not
    # promise room for a larger edited preset or an animated size from that box.
    from services.caption_overlays import _resolve_style
    from services.clipper.captions import DEFAULT_PRESET_ID
    style = _resolve_style({"template_id": plan.get("preset_id") or DEFAULT_PRESET_ID,
                            "style": plan.get("style") or {}})
    if (float(plan.get("scale") or 1) > 1 or
            float(style.get("font_size") or 64) > 72 or plan.get("entry_pop")):
        return stop("caption_extent_not_supported")
    faces = []
    samples = dyn.get("_review_faces") or []
    for shot in dyn["shots"]:
        # crop_window maps fixed anchors; the renderer's oscillating shake is
        # not in that map. A moving crop must not acquire a fictitious clear band.
        if shot.get("shake"):
            return stop("moving_crop_not_supported")
        crop = evidence_map.crop_window(shot, src_w=src_w, src_h=src_h,
                                        style=dyn.get("style"))
        if isinstance(crop, str):
            return stop(crop)
        count = len(faces)
        for sample in samples:
            t = sample["t"] - start
            if not shot["t0"] <= t < shot["t1"]:
                continue
            boxes = sample.get("boxes") or []
            report["empty_samples"] += not boxes
            for box in boxes:
                mapped = evidence_map.map_box(box, proxy_w=pw, proxy_h=ph,
                    src_w=src_w, src_h=src_h, crop=crop)
                if mapped == evidence_map.OFF_FRAME:
                    report["off_frame_boxes"] += 1
                elif isinstance(mapped, str):
                    return stop(mapped)
                else:
                    faces.append(mapped)
        report["shots_without_mapped_faces"] += len(faces) == count
    report["mapped_boxes"] = len(faces)
    if not faces:
        return stop("no_mapped_face_observations")

    half = CAPTION_BOX_H_PCT / 2

    def conflicts(y, boxes):
        # Full-width on purpose: no assumed glyph width or horizontal clipping
        # can make a face at the edge disappear from this avoidance decision.
        return sum(y + half > r["y"] / 1920 and
                   y - half < (r["y"] + r["h"]) / 1920 for r in boxes)

    report["face_conflicts_before"] = conflicts(current_y, faces)
    if not report["face_conflicts_before"]:
        return stop("current_position_clear_in_observations")
    lo, hi = scan_bounds(1920)
    # Include the exact bottom preset (0.75), already an allowed position. The
    # shared grid ends at 0.7442; rounding up would exceed the allowed bound.
    grid = sorted(set(scan_grid(lo, hi) + [hi]))
    clear = [y for y in grid if not conflicts(y, faces + keep_out)]
    preferred = [y for y in clear if SPEC_BAND_LO <= y <= SPEC_BAND_HI]
    y = _widest_band(preferred or clear)
    if y is None:
        return stop("no_clear_position_in_observations")
    return {**report, "applied": True, "y_pct": y,
            "face_conflicts_after": conflicts(y, faces),
            "reason": "moved_around_observed_faces"}
