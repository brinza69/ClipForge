"""
ClipForge — AI Stream Clipper: finding the facecam.

Split out of `content_type.py` on 2026-08-18, when that file passed 830 lines
against the repo's 500 limit. The seam is where the work is: this is the one
question in region detection that took four sessions and sixteen approaches,
and it carries more measured reasoning than the rest of the module together.

Read the constants before changing any of them. Every number here has a
scoreboard behind it — `scripts/score_facecam.py`, 9/9 against
`docs/source-labels.md`, with the rects pinned in
`docs/refs/facecam-golden.json` because a count alone hid a 46-row error for
months.

The short version of what was learned: an inset is not found by its border,
because two of the eleven labelled sources have no border at all. It is found
by asking whether the rectangle holds a SECOND CAMERA — see
`content_geom.scene_independence`.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

import numpy as np

from services.clipper.content_geom import (
    _WEBCAM_AREA,
    _WEBCAM_ASPECT,
    aspect_ok,
    clamp01,
    corner_proximity,
    make_rect,
    median_rect,
    rect_area_frac,
    rect_centre,
    scene_independence,
    snap_rect,
)

logger = logging.getLogger("clipforge.clipper.content_type")

# A facecam that a Haar cascade sees in a THIRD of frames is doing well: the
# streamer looks away, leans out, gets covered by an alert. Measured on the
# co-stream's 40 sampled frames, the two real facecams landed 14 and 13 hits;
# the busiest false position landed 1. The old gate wanted half the frames,
# which neither real facecam could ever have cleared.
#
# 0.15 -> 0.10 on 2026-08-17, and the reason is the recurring one. The rate is
# hits over the frames sampled from the WHOLE range, so it measures how often
# the streamer faced his camera across that span — not whether there is an
# inset. On the 12-minute Minecraft slice the co-streamer's facecam clears the
# bar at 0.33; on the 4-hour source of the SAME STREAM, same camera, same
# layout, it reads 0.12 and was dropped. That is the one this file blamed on
# the borderless case, and it is not: it is a bar fitted to a 12-minute sample
# failing on a 4-hour one.
#
# Swept against the labels: 7/9 at 0.15 and 0.13, then 8/9 flat from 0.12 all
# the way to 0.05 — a plateau wide enough that the exact value does not matter,
# because the geometry gates below do the rejecting and this one was only ever
# costing recall. 0.10 leaves margin under the 0.12 true positive without
# sitting on the edge. `IRL World Cup` (3 h, fullscreen, the set's clean
# negative) and Kai Cenat both still report zero.
_FACECAM_MIN_HITS = 3
_FACECAM_MIN_RATE = 0.10
# Faces whose centres sit this close, as a frame fraction, are one person.
_FACECAM_TOL = 0.12
# Where the inset's edge is looked for, as multiples of the median face box.
# Below the inner bound is the face itself; past the outer one is the game.
# On the co-stream the two insets measure 5.3x and 4.5x the face width, so the
# outer bound has to be generous — a facecam frames a head with a lot of room.
_FACECAM_INNER, _FACECAM_OUTER = 0.75, 4.0
# ...but never further than this share of the FRAME. The outer bound is a
# multiple of the median face box and that box wobbles between 22 and 62 px
# across eighths of one source, so on a small box a 4x reach searches rows
# 0-144 of a 270-row frame — and inside a window that wide the strongest
# gradient is whatever the busiest thing in the picture is. Measured on Jynxzi,
# it was the stream's own top chrome.
_FACECAM_REACH_CAP = 0.30
# An inset sits against an edge of the frame. Of nine features measured over 68
# labelled candidates this one separates best by a wide margin — 93% with a
# single threshold, against 73% for the next — and it is the largest weight a
# logistic fit gives. A learned classifier over all nine scored WORSE than this
# feature alone under leave-one-source-out validation (84% against 91%), which
# is why there is a constant here and not a model.
#
# Sensitivity, on the source scoreboard: 7/9 from 0.44 to 0.52 and 5/9 from
# 0.55. 0.48 is the middle of that plateau. The cliff is moistcr1tikal, whose
# inset sits at the left edge at mid-height rather than in a corner — the case
# this file already records as penalised by corner_proximity. **A plateau
# 0.08 wide on nine sources is thin. Do not nudge this without re-running
# scripts/score_facecam.py.**
_FACECAM_CORNER_MIN = 0.48
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
# Below this the rect's own picture varies less over time than the frame around
# it, which is what a separate camera does and what a window onto the same
# scene cannot. NOT FITTED — 1.0 is the natural boundary of the ratio, and that
# is the point: see `content_geom.scene_independence` for the measured
# separation and for what leave-one-source-out says about fitting it instead.
_FACECAM_INDEPENDENCE_MAX = 1.0
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
               limit: int, forward: bool, at_frame: bool = False) -> int:
    """The inset's edge on one axis: the strongest step between the face and
    the game, or the frame border when the facecam runs into it.

    `at_frame` says the caller already knows this direction points at a frame
    border the facecam is plausibly flush to. It exists because
    `_FACECAM_REACH_CAP` truncates the window and the border test below needs
    the window to REACH the limit — so the cap silently switched the branch
    off. See `_snap_inset`.
    """
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
    if touches_frame or at_frame:
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

    inner_x, inner_y = half_w * _FACECAM_INNER * 2, half_h * _FACECAM_INNER * 2
    reach_x, reach_y = half_w * _FACECAM_OUTER * 2, half_h * _FACECAM_OUTER * 2
    outer_x = min(reach_x, fw * _FACECAM_REACH_CAP)
    outer_y = min(reach_y, fh * _FACECAM_REACH_CAP)

    # WHICH EDGES THIS FACECAM IS FLUSH TO, and only those. `_snap_edge` can
    # conclude "the inset runs into the frame, there is nothing drawn there"
    # only when its window reaches the limit, and _FACECAM_REACH_CAP truncates
    # the window — so the cap turned that branch off for every facecam whose
    # face centre sits further than 0.30 of the frame from the edge it is
    # against. Both bottom-left sources are exactly that: the uncapped reach
    # would have crossed the frame bottom (428 and 416 rows of 270) and the
    # capped one stops at 245 and 260, so their bottom edge came back 25 and 46
    # px short on the proxy — 100 to 184 px on a 1080p source, the streamer's
    # torso sliced out of the face band.
    #
    # Restoring it in ALL FOUR directions is wrong and was measured: the
    # uncapped reach is 4x the face box, so on a 66px face it is +-264 rows of
    # a 270-row frame and every direction qualifies. `go ghost` came back as
    # 480x270 — the whole picture — which is the runaway the cap was added to
    # stop. An inset sits in a CORNER, the seed's own position says which one,
    # and that is the only pair of directions where the answer can be the
    # frame. The dominance test still has to agree.
    left, top = cx < fw / 2.0, cy < fh / 2.0
    x0 = _snap_edge(col_prof, cx, inner_x, outer_x, fw, False,
                    left and cx - reach_x <= 1)
    x1 = _snap_edge(col_prof, cx, inner_x, outer_x, fw, True,
                    not left and cx + reach_x >= fw - 1)
    y0 = _snap_edge(row_prof, cy, inner_y, outer_y, fh, False,
                    top and cy - reach_y <= 1)
    y1 = _snap_edge(row_prof, cy, inner_y, outer_y, fh, True,
                    not top and cy + reach_y >= fh - 1)

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
    stack = np.stack([g.astype(np.float32) for g in grays]) if grays else np.zeros(0)

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
        # An inset is against an edge. This is the gate the last three sessions
        # were looking for, and it was already in the file — as 25% of the score
        # below, where it could be outvoted. Capping the reach above made the
        # rects plausible enough that the area gate stopped rejecting phantoms
        # by accident, so something has to reject them on purpose.
        if corner_proximity(snapped, fw, fh) < _FACECAM_CORNER_MIN:
            continue
        # IS THERE AN INSET HERE AT ALL — the gate this file spent three
        # sessions looking for, and the one every other rule was standing in
        # for by accident. It asks whether the rect holds a SECOND CAMERA
        # rather than a piece of the same picture, which is a question the
        # border cannot answer on a keyed facecam because there is no border.
        # Full reasoning and the 14-rect separation in `scene_independence`.
        if scene_independence(stack, snapped) >= _FACECAM_INDEPENDENCE_MAX:
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



# A layout is only a layout if it lasts. Cutting the stream into stretches buys
# the second facecam on a source whose camera changes, and it costs this: a
# stretch where the streamer reacts to a video containing a webcam gets that
# video reported as an inset.
#
# Measured on Jynxzi's 60-80 minute stretch, and it is the closest call in the
# whole corpus — `240x136@240,0` passes `scene_independence` at 0.96 against a
# 1.0 bar, where every real inset in the set sits at 0.89 or below. Tightening
# the bar to 0.90 would drop it and put the boundary one hundredth above his
# REAL camera at 0.89, which is the zero-margin fit that constant was chosen to
# avoid. The measure is not misbehaving either: a video of somebody else's
# webcam genuinely is a second camera. The definition is what disagrees.
#
# So this uses the property the detector already rests on — AN INSET IS ALWAYS
# IN THE SAME PLACE — across stretches instead of across frames:
#
#   Jynxzi        x~0 y~120 in 7 stretches of 11, the phantom in 1
#   Minecraft 4h  x~0 y~0   in 10 of 12, the second facecam in 5
#   EARLY STREAM  one position in 9 of 13, wandering by 26 px
#
# Only a position seen in a SINGLE stretch is dropped, and only when another
# has established itself in `_LAYOUT_MIN_DOMINANT`. Both halves matter: 1
# against 5 would take Minecraft's real second camera, and a source whose only
# facecam is brief has nothing to be outvoted by — Kai Cenat's inset exists for
# four minutes of 112 and is in the corpus as exactly that case.
_LAYOUT_MIN_DOMINANT = 3


def _drop_transient_webcams(ranges: list[dict[str, Any]]) -> None:
    """Remove one-off facecams from a source that has a settled layout."""
    groups: list[dict[str, Any]] = []
    for i, blob in enumerate(ranges):
        fw = blob.get("frame_width") or 0
        fh = blob.get("frame_height") or 0
        for cam in blob.get("webcams") or []:
            cx, cy = rect_centre(cam)
            for g in groups:
                gx = sum(c[0] for c in g["centres"]) / len(g["centres"])
                gy = sum(c[1] for c in g["centres"]) / len(g["centres"])
                if (abs(cx - gx) <= _FACECAM_TOL * fw
                        and abs(cy - gy) <= _FACECAM_TOL * fh):
                    g["centres"].append((cx, cy))
                    g["where"].add(i)
                    break
            else:
                groups.append({"centres": [(cx, cy)], "where": {i}})

    if not groups:
        return
    if max(len(g["where"]) for g in groups) < _LAYOUT_MIN_DOMINANT:
        return

    doomed = [g for g in groups if len(g["where"]) == 1]
    for g in doomed:
        i = next(iter(g["where"]))
        blob = ranges[i]
        cx, cy = g["centres"][0]
        fw = blob.get("frame_width") or 0
        fh = blob.get("frame_height") or 0
        kept = [c for c in (blob.get("webcams") or [])
                if abs(rect_centre(c)[0] - cx) > _FACECAM_TOL * fw
                or abs(rect_centre(c)[1] - cy) > _FACECAM_TOL * fh]
        if len(kept) == len(blob.get("webcams") or []):
            continue
        blob["webcams"] = kept
        blob["webcam"] = kept[0] if kept else None
        if not kept:
            blob.setdefault("confidence", {})["webcam"] = 0.0
