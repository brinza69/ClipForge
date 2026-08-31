"""Where a detected box lands in the OUTPUT frame — Batch R6's missing mapper.

`caption_placement` reports what the burned caption covers and says the CALLER
maps the evidence. Nothing did, so its three occlusion signals have always come
back `unavailable`. This is that mapper, and it exists because the obvious
version of it is wrong in three separate places.

THE CHAIN, READ OFF THE RENDERER RATHER THAN ASSUMED. Four transforms, and
skipping any of them silently ruins the answer:

    face detector          boxes in PROXY pixels (480x270 on the pilots)
      | scale X and Y SEPARATELY
    source pixels          what `shot["rect"]` is in
      | pad first: y += canvas_offset
    canvas pixels          9:16, what the renderer actually crops
      | the crop window for this shot
    crop-local pixels
      | one scale, because the canvas is already 9:16
    output 1080x1920

WHY THE PAD COMES FIRST, and it is not a style choice — `dynamic_render` says so
in a comment that cost a review round: `scale` fixes its output size when the
filter is configured and does not recompute it when `sendcmd` changes the crop,
so `force_original_aspect_ratio` did nothing for a `fit` shot and the whole 16:9
frame was STRETCHED into 1080x1920. Letterboxing up front makes every crop 9:16,
including the full-frame one. A mapper that crops before padding is describing a
renderer this repo abandoned.

THREE THINGS IT REFUSES RATHER THAN APPROXIMATES.

A SHOT WHOSE CROP MOVES. `shot["rect"]` looks like the crop and is not:
`dynamic_geometry.visual_key` calls the picture "what `build_sendcmd`
schedules", and refuses any shot whose size timeline has more than one point.
Across the 2,126 stored shots every timeline is a single point, so the rectangle
IS the crop today — but 2,024 of them are LABELLED `move: push` or `pull` and
stand still only because `push_amount` is 0.0 in every stored style. Reading the
label instead of the timeline says "95% of shots move", which is the opposite of
the truth and is the reason this module reads the timeline.

MISSING PROXY DIMENSIONS. `dynamic_edit` falls back to `or src_w`, which assumes
the proxy is the source — a scale factor of 1 where the real one is 5.3 on the
pilots. That fallback is live shipping behaviour and this module does not copy
it: a box whose scale nobody can compute is refused, because a face reported at
a fifth of its true size lands somewhere else entirely and reports a clean
caption.

A BOX THAT MISSES THE CROP. It is not in the output at all, which is a different
answer from "it is in the output covering nothing" — the first is absence, the
second is a measurement, and `caption_placement` has an axis for each.

WHAT THIS IS NOT. It is not `panels_to_keep_out`, and must never become it: that
function deliberately SKIPS face shots, because mapping a game panel through a
face crop is arithmetic with no referent at a 5-8x scale factor, and it once
shipped "66% of the caption sits on detected game UI" for a clip whose captions
sat on a hoodie. The difference here is that a FACE mapped through a FACE crop
has a referent — the crop was built around that face — and the transform is the
renderer's own rather than a proportion invented for the report. That is the
whole of the distinction, and it is worth restating whenever this file is
touched.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

__all__ = ["REFUSALS", "map_box", "map_boxes", "shot_evidence"]

#: Why a box could not be placed. Each one is a refusal, not a zero.
NO_PROXY_SIZE = "proxy_dimensions_unknown"
NO_SOURCE_SIZE = "source_dimensions_unknown"
NO_CROP = "shot_has_no_crop_rectangle"
MOVING_CROP = "shot_crop_changes_size_across_the_shot"
#: The shot itself could not be read — no usable `t0`/`t1`, or a size timeline
#: that threw. DISTINCT FROM `MOVING_CROP`, which the first version returned for
#: both: an exception out of `_size_timeline` is a malformed shot, and saying
#: "its crop moves" about it is a specific claim nobody measured.
BAD_SHOT = "shot_cannot_be_read"
BAD_BOX = "box_is_not_four_finite_numbers"
REFUSALS: tuple[str, ...] = (NO_PROXY_SIZE, NO_SOURCE_SIZE, NO_CROP,
                             MOVING_CROP, BAD_SHOT, BAD_BOX)

#: A box that misses the crop window entirely. NOT a refusal and not a zero
#: overlap: the thing is not in the delivered frame, so there is nothing to
#: measure and nothing was measured wrongly.
OFF_FRAME = "off_frame"


def _positive(value: Any) -> float | None:
    """A finite positive number, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def _finite(value: Any) -> float | None:
    """A finite number, or None. Zero and negatives are fine for a timestamp."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _box(raw: Any) -> tuple[float, float, float, float] | None:
    """`[x, y, w, h]` or `{"x":..}` to four finite numbers, or None."""
    if isinstance(raw, dict):
        values = [raw.get("x"), raw.get("y"), raw.get("w"), raw.get("h")]
    elif isinstance(raw, (list, tuple)) and len(raw) == 4:
        values = list(raw)
    else:
        return None
    out = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if not math.isfinite(v):
            return None
        out.append(float(v))
    if out[2] <= 0 or out[3] <= 0:
        return None
    return out[0], out[1], out[2], out[3]


def crop_window(shot: dict, *, src_w: int, src_h: int,
                style: dict | None = None) -> tuple[float, float, float, float] | str:
    """The shot's crop in CANVAS pixels, or a refusal reason.

    Built the way `dynamic_render.build_dynamic_filtergraph` builds it, including
    the `+ off_y` that turns a source-space rectangle into a canvas-space one,
    and including the `fit` branch that takes the whole canvas.
    """
    from services.clipper.dynamic_geometry import (canvas_size, composition_of,
                                                   _size_timeline)

    sw, sh = _positive(src_w), _positive(src_h)
    if sw is None or sh is None:
        return NO_SOURCE_SIZE
    canvas_w, canvas_h, off_y = canvas_size(int(sw), int(sh))

    # THE SHOT'S OWN FIELDS FIRST, so a refusal names the thing that is wrong.
    # The first version ran the timeline first and caught every exception as
    # `MOVING_CROP`, so a shot with no `t0` was reported as one whose crop
    # moves — a specific claim about geometry, made about a record nobody could
    # read.
    fit = composition_of(shot) == "fit"
    rect = None if fit else _box(shot.get("rect"))
    if not fit and rect is None:
        return NO_CROP
    if _finite(shot.get("t0")) is None or _finite(shot.get("t1")) is None:
        return BAD_SHOT

    try:
        moves = len(_size_timeline(shot, style or {}, int(sw), int(sh))) > 1
    except Exception:
        return BAD_SHOT
    if moves:
        return MOVING_CROP

    if fit:
        return 0.0, 0.0, float(canvas_w), float(canvas_h)
    x, y, w, h = rect
    return x, y + off_y, w, h


def map_box(raw: Any, *, proxy_w: int, proxy_h: int, src_w: int, src_h: int,
            crop: tuple[float, float, float, float],
            out_w: int = 1080, out_h: int = 1920) -> dict | str:
    """One detector box, in OUTPUT pixels — or a refusal, or `OFF_FRAME`.

    `proxy_w`/`proxy_h` are required and are NOT allowed to fall back to the
    source dimensions, which is what `dynamic_edit` does. On the pilots the real
    factor is 5.3; assuming 1 puts a face at a fifth of its size, somewhere else
    in the frame, and reports a clean caption.
    """
    box = _box(raw)
    if box is None:
        return BAD_BOX
    pw, ph = _positive(proxy_w), _positive(proxy_h)
    if pw is None or ph is None:
        return NO_PROXY_SIZE
    sw, sh = _positive(src_w), _positive(src_h)
    if sw is None or sh is None:
        return NO_SOURCE_SIZE
    ow, oh = _positive(out_w), _positive(out_h)
    if ow is None or oh is None:
        return NO_SOURCE_SIZE

    from services.clipper.dynamic_geometry import canvas_size

    _cw, _ch, off_y = canvas_size(int(sw), int(sh))

    # 1. proxy -> source, X and Y separately. Assuming one factor because the
    #    proxy "should" preserve the aspect is an assumption about an encoder.
    sx, sy = sw / pw, sh / ph
    x, y, w, h = box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy

    # 2. source -> canvas. The pad is applied before the crop, so everything the
    #    crop sees has already moved down by the offset.
    y += off_y

    # 3. canvas -> crop-local, and the intersection is where a box leaves the
    #    delivered frame.
    cx, cy, cw2, ch2 = crop
    x0, y0 = max(x, cx), max(y, cy)
    x1, y1 = min(x + w, cx + cw2), min(y + h, cy + ch2)
    if x1 <= x0 or y1 <= y0:
        return OFF_FRAME

    # 4. crop-local -> output. One scale per axis; the canvas is already 9:16,
    #    so on a whole-canvas crop these are equal.
    fx, fy = ow / cw2, oh / ch2
    return {"x": round((x0 - cx) * fx, 2), "y": round((y0 - cy) * fy, 2),
            "w": round((x1 - x0) * fx, 2), "h": round((y1 - y0) * fy, 2)}


def map_boxes(boxes: Sequence[Any], **kw) -> tuple[list[dict], list[str], int]:
    """`(mapped, refusals, how many fell off the frame)`.

    THE THREE ARE KEPT APART on purpose. A refusal means the box could not be
    placed and the signal is incomplete; an off-frame box means it is genuinely
    not in the delivered picture; a mapped box is a measurement. Folding the
    first into the third is the mistake this whole batch is about.
    """
    mapped: list[dict] = []
    refused: list[str] = []
    off = 0
    for raw in boxes or []:
        got = map_box(raw, **kw)
        if got == OFF_FRAME:
            off += 1
        elif isinstance(got, str):
            refused.append(got)
        else:
            mapped.append(got)
    return mapped, sorted(set(refused)), off


def shot_evidence(shot: dict, *, boxes_by_signal: dict[str, Sequence[Any] | None],
                  proxy_w: int, proxy_h: int, src_w: int, src_h: int,
                  style: dict | None = None,
                  out_w: int = 1080, out_h: int = 1920) -> dict:
    """One shot's evidence in the shape `caption_placement` expects.

    `None` for a signal travels through as `None` — the caller had no detection
    to offer, and that is `unavailable`, not an empty list. A signal whose boxes
    were all refused becomes `None` too, for the same reason: nothing about it
    was measured. The refusals ride alongside so a caller can say WHY.
    """
    crop = crop_window(shot, src_w=src_w, src_h=src_h, style=style)
    if isinstance(crop, str):
        return {"evidence": {k: None for k in boxes_by_signal},
                "refused": [crop], "off_frame": {}}

    evidence: dict[str, list[dict] | None] = {}
    refused: list[str] = []
    off_frame: dict[str, int] = {}
    for name, boxes in boxes_by_signal.items():
        if boxes is None:
            evidence[name] = None
            continue
        mapped, why, off = map_boxes(
            boxes, proxy_w=proxy_w, proxy_h=proxy_h, src_w=src_w, src_h=src_h,
            crop=crop, out_w=out_w, out_h=out_h)
        off_frame[name] = off
        if why:
            # A PARTLY UNREADABLE SIGNAL IS NOT A SHORTER ONE. Reporting the
            # boxes that mapped would answer "the caption covers this much" out
            # of a list that was partly unplaceable.
            evidence[name] = None
            refused.extend(why)
            continue
        evidence[name] = mapped
    return {"evidence": evidence, "refused": sorted(set(refused)),
            "off_frame": off_frame}
