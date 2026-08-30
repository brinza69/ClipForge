"""A caption band, a rectangle, and how much of the first the second covers.

The arithmetic under `caption_placement`, split out at the 500-line limit. It
knows nothing about shots, evidence or reports — give it numbers and it answers
about numbers — which is why the two most expensive mistakes in this batch both
live here as comments rather than as code:

- `_rows` returns None for a rectangle it cannot read, and None is UNREADABLE.
  It used to flow into `overlaps`, which answers 0.0 — an absence of measurement
  presented as a measurement of absence.
- `source_band` returns a PAIR, because its None has two meanings: "measured,
  and there is no letterbox" and "nobody supplied the dimensions".

And `covered` is a union rather than a maximum, which is the whole of §R6's
"how much of the caption can nobody read".
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["CAPTION_BAND_PCT", "band_for", "overlaps", "covered",
           "source_band"]

#: The caption is treated as a FULL-WIDTH strip, which is what
#: `panels_to_keep_out` already assumes and for the same reason: the crop is 9:16
#: out of 16:9, so horizontal position survives the mapping poorly and the
#: caption is centred and nearly full width anyway. Only the vertical extent is
#: honest, so only the vertical extent is compared.
#:
#: THE HEIGHT IS `captions.CAPTION_BOX_H_PCT`, imported rather than restated.
#: The first version put 0.12 here — a second definition of a number the
#: geometry already had at 0.10 — and two numbers for one box is how a report
#: ends up describing a caption nobody burns.
#:
#: It is the CANONICAL COLLISION geometry, not the pixel-perfect ASS rectangle:
#: its own comment calls it a conservative approximation, and libass lays out
#: the real text from the font, the wrap and the line count. So every overlap
#: here is against the box the avoidance logic uses, which is the right thing to
#: agree with and the wrong thing to call exact.
from services.clipper.captions import CAPTION_BOX_H_PCT as CAPTION_BAND_PCT


def _usable_pct(value: Any) -> bool:
    """Whether a fraction of the frame is one: a finite number in 0..1."""
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and 0.0 <= float(value) <= 1.0


def band_for(y_pct: float, height_pct: float = CAPTION_BAND_PCT) -> tuple[float, float]:
    """`(top, bottom)` of the caption strip, as fractions of the output height."""
    half = max(0.0, float(height_pct)) / 2.0
    centre = min(1.0, max(0.0, float(y_pct)))
    return max(0.0, centre - half), min(1.0, centre + half)


def _rows(rect: Any, height: float) -> tuple[float, float] | None:
    """A rectangle's vertical extent as fractions, or None if it is not one.

    None means UNREADABLE, and the caller has to treat it as such. It used to
    flow into `overlaps`, which answers 0.0 for None — so a malformed rectangle
    read as "covers nothing" and a shot full of them read as clear. That is the
    oldest mistake in this plan, arriving in a new place: an absence of
    measurement presented as a measurement of absence.
    """
    import math

    if not isinstance(rect, dict) or height <= 0:
        return None
    try:
        y = float(rect.get("y", rect.get("top", 0.0)))
        h = float(rect.get("h", rect.get("height", 0.0)))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(y) and math.isfinite(h)) or h <= 0 or y < 0:
        return None
    return max(0.0, y / height), min(1.0, (y + h) / height)


def overlaps(band: tuple[float, float], rect: tuple[float, float] | None) -> float:
    """How much of the caption band a rectangle covers, 0..1.

    A SHARE OF THE CAPTION, not of the rectangle. "The caption is 60% covered"
    and "the panel covers 3% of its own area" are different sentences, and only
    the first is about whether anybody can read it.
    """
    if rect is None:
        return 0.0
    top = max(band[0], rect[0])
    bottom = min(band[1], rect[1])
    span = band[1] - band[0]
    if span <= 0 or bottom <= top:
        return 0.0
    return min(1.0, (bottom - top) / span)


def _usable_size(value: Any) -> bool:
    """Whether a pixel dimension is one: a finite, positive real number."""
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def source_band(src_w: int, src_h: int) -> tuple[tuple[float, float] | None, bool]:
    """`(where the source frame sits on a `fit` shot, whether that is known)`.

    From `dynamic_geometry.canvas_size`, which is the function the renderer pads
    with — not from a key on the shot. The first version read `shot["frame"]`,
    and no shot has ever carried one: 161 `fit` shots, all of them in the 58
    sidecars of the pilot corpus, 27 of which contain at least one — and zero
    with `frame` or `fit_rect`. The branch was dead against real data and green
    against fixtures that invented the key.

    TWO ANSWERS FOR None, WHICH IS WHY THERE IS A SECOND RETURN VALUE. `(None,
    True)` is "measured, and there is no letterbox" — a source already 9:16 or
    narrower fills the output. `(None, False)` is "nobody supplied the source
    dimensions", and a `fit` shot with unknown geometry cannot be said to have a
    band or to lack one. Collapsing them read a missing width as an upright
    source and reported zero conflicts with zero unavailable.
    """
    from services.clipper.dynamic_geometry import canvas_size

    # NOT A COMPARISON AGAINST AN UNKNOWN TYPE. `src_w <= 0` on a string raised
    # `TypeError` out of a function that already had a word for this case: its
    # own `(None, False)` means "nobody supplied the dimensions". The code
    # crashed instead of using the answer it had.
    if not (_usable_size(src_w) and _usable_size(src_h)):
        return None, False
    _canvas_w, canvas_h, offset = canvas_size(int(src_w), int(src_h))
    if canvas_h <= 0:
        return None, False
    if offset <= 0:
        return None, True
    return (offset / float(canvas_h), (canvas_h - offset) / float(canvas_h)), True


def covered(band: tuple[float, float],
            rects: Sequence[tuple[float, float] | None]) -> float:
    """How much of the caption band is covered by ANY of the rectangles, 0..1.

    A UNION, not a maximum. Measured on a real counter-example: one shot where a
    face covers 60.4%, and another where a face covers 39.6% and text covers a
    DIFFERENT 39.6% — 79.2% of the caption together. Taking the largest single
    box, then the largest single signal, called the first shot the worse one.
    "How much of the caption can nobody read" is a question about the union of
    what is on top of it.
    """
    spans = sorted((max(band[0], r[0]), min(band[1], r[1]))
                   for r in rects if r is not None
                   and min(band[1], r[1]) > max(band[0], r[0]))
    width = band[1] - band[0]
    if width <= 0 or not spans:
        return 0.0
    total = 0.0
    top, bottom = spans[0]
    for start, end in spans[1:]:
        if start > bottom:
            total += bottom - top
            top, bottom = start, end
        else:
            bottom = max(bottom, end)
    total += bottom - top
    return min(1.0, total / width)
