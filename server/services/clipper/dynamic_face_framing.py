"""Resolve a disagreement between local detections and the clip's face median.

These are competing framing proposals, not confirmed subject measurements.
Keeping their union costs scale; choosing the median can cost the person.
"""
from __future__ import annotations

import math

from services.clipper.dynamic_cameras import ASPECT, _rect


def face_framing(base: dict, local: tuple[float, float], face: dict,
                 headroom: float, src_w: int, src_h: int, *, anchored: bool,
                 observed_centres: list[tuple[float, float]]
                 ) -> tuple[dict, bool, str | None]:
    """Return (rectangle, needs full fit, reason for conservative framing).

    Re-centring per shot follows THIS WINDOW'S detections. On a fixed inset,
    bad detections can walk off the correct cluster: e8fa6b35ea66 shot 15 was
    face_medium at y=260 while the camera sat at y=34, cy=176. The export was
    Minecraft dirt. Keep that protection when a source-wide anchor exists.

    But a clip median is not that anchor. On pilotee0e/c04e7960179b at output
    3s, the local centre is (2292, 684) and the median is (2024, 588). The old
    guard rejected the former and rendered the curtain between two people.
    Neither proposal has established identity, so retain BOTH framings instead
    of silently certifying one. This is not a claim about face detection recall,
    native captions, the active speaker, or the whole head fitting its box.

    The caller must disable push/snap/shake for a widened shot: shrinking this
    rectangle afterwards would undo the containment used to choose it.
    """
    moved = _rect(0, base["h"], *local, headroom, src_w, src_h)
    if (moved["x"] <= face["cx"] <= moved["x"] + moved["w"]
            and moved["y"] <= face["cy"] <= moved["y"] + moved["h"]):
        return moved, False, None
    if anchored:
        return dict(base), False, None

    # The coordinate-wise median can invent a point between observations. On
    # the Minecraft counterexample its x came from (1640,172), its y from
    # (1350,402): the proposed crop contains NEITHER detection. Widening for
    # that phantom needlessly shrank the webcam in the first rendered probe.
    # No threshold or identity claim: if there ARE local samples, a proposal
    # containing none of them is internally inconsistent. With no local sample,
    # _centre_at uses a nearby one; keep both guesses conservatively and label
    # that weaker basis. Neither an empty track nor a nearby face proves what
    # is present in this shot (the Romanian 5.48..6.895s interval is one).
    if observed_centres and not any(
            moved["x"] <= x <= moved["x"] + moved["w"]
            and moved["y"] <= y <= moved["y"] + moved["h"]
            for x, y in observed_centres):
        return dict(base), False, None
    basis = "" if observed_centres else "_nearby_sample_only"

    # Include the old camera too: that preserves the protection against a local
    # false positive without pretending the clip median is a stationary camera.
    left = min(base["x"], moved["x"])
    top = min(base["y"], moved["y"])
    right = max(base["x"] + base["w"], moved["x"] + moved["w"])
    bottom = max(base["y"] + base["h"], moved["y"] + moved["h"])
    # Four pixels cover even-coordinate rounding and the renderer's 2px anchor
    # clamp. This is geometry slack, not annotation uncertainty or a confidence
    # threshold. When the expanded rectangle cannot fit, use the existing fit.
    h = math.ceil(max(bottom - top + 8, (right - left + 8) / ASPECT) / 2) * 2
    if h <= src_h:
        rect = _rect(0, h, (left + right) / 2, (top + bottom) / 2,
                     0.5, src_w, src_h)
        if (rect["x"] <= left and rect["y"] <= top
                and rect["x"] + rect["w"] >= right
                and rect["y"] + rect["h"] >= bottom):
            return rect, False, "unanchored_face_positions_disagree_widened" + basis
    return dict(base), True, "unanchored_face_positions_disagree_fit" + basis
