"""Can the caption be read at all — Batch R6's contrast line.

§R6 says the blurred letterbox band may host captions "only if it passes the
contrast". The corpus says that question is not a corner case: 27 of the 27
clips with a `fit` shot and known geometry have their caption on that band, so
whatever answers it answers for every letterboxed export already shipped.

THE ANSWER IS ARITHMETIC, NOT A FRAME SAMPLE, and that is the whole design.
These captions are white text inside a black outline, and a glyph like that
separates from ANY backdrop by one of its two colours: against a pale backdrop
the outline does the work, against a dark one the fill does. So there is a floor
— the worst backdrop luminance for the pair, where neither is doing much — and
it can be computed once instead of hunted for in frames.

    white #FFFFFF inside black #000000   4.61:1 at grey 117

No backdrop does worse than that, blurred or otherwise. Sampling frames could
only ever find a number above it.

AND THE FLOOR IS WHERE THE REAL FINDING IS. Run it over the seven shipping
presets and the fills are all fine — 4.42 to 4.61 — but the HIGHLIGHT colour,
the one word the karaoke animation paints, is a different palette:

    Classic White / Boxed White   4.61      Karaoke Yellow  4.08
    Bold Impact                   3.88      Clean Minimal   3.45
    Viral Gradient                2.73      Neon Pop        2.34

`Neon Pop` and `Viral Gradient` cannot clear 3.0:1 for their highlighted word
against ANY uniform backdrop. That is not a letterbox problem and no frame
sample would have named it: it is in the palette.

WHAT THIS IS NOT.

It is a LUMINANCE floor. WCAG contrast ignores hue, so two colours that differ
only in hue score 1:1 here and are perfectly distinguishable on screen. The
number is a lower bound on separation, not a measurement of legibility.

It assumes a UNIFORM backdrop under each glyph. A blurred letterbox is about as
uniform as video gets — that is what the blur does — but a `crop` shot over
detailed source is not, and this says nothing about it.

THE THRESHOLDS ARE BORROWED, and `calibrated: false` travels with every verdict.
WCAG's 3.0 for large text and 4.5 for normal text were written for static web
pages read at leisure, not for 72px Impact on a phone for 1.4 seconds. They are
the only published bars available and they are named, not derived — the same
honesty `source_captions` prints beside its own thresholds.
"""

from __future__ import annotations

from typing import Any

__all__ = ["LARGE_TEXT_MIN", "NORMAL_TEXT_MIN", "REFUSALS", "luminance",
           "contrast", "floor", "verdict"]

#: WCAG 2.1 AA. BORROWED, not derived: written for static text on web pages,
#: not for a caption on screen for a second and a half. Named so the next reader
#: can see exactly what is being asserted.
LARGE_TEXT_MIN = 3.0
NORMAL_TEXT_MIN = 4.5

#: A colour that cannot be read is not a black one. Defaulting an unparsable
#: value to #000000 would turn "nobody knows what colour this is" into the most
#: favourable possible answer, since black is one end of the luminance range and
#: contrasts maximally with the white fill.
BAD_COLOUR = "colour_not_a_hex_triplet"
NO_STYLE = "no_caption_style"
NO_FILL = "style_has_no_text_colour"
NO_OUTLINE = "style_has_no_outline_colour"
REFUSALS: tuple[str, ...] = (BAD_COLOUR, NO_STYLE, NO_FILL, NO_OUTLINE)


def _rgb(value: Any) -> tuple[int, int, int] | None:
    """`#RRGGBB` or `#RRGGBBAA` to a triplet, or None if it is not one.

    ALPHA IS DROPPED, DELIBERATELY AND ONLY HERE. The shadow colour carries one
    (`#000000B0`) and the shadow sits BEHIND the outline, so it changes what the
    backdrop looks like and never what the glyph edge is made of. This function
    is asked only about the fill and the outline, both opaque.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip("#")
    if len(text) not in (6, 8):
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def luminance(colour: Any) -> float | None:
    """WCAG relative luminance, 0..1, or None if the colour cannot be read."""
    rgb = _rgb(colour)
    if rgb is None:
        return None
    channels = []
    for raw in rgb:
        v = raw / 255.0
        channels.append(v / 12.92 if v <= 0.04045
                        else ((v + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Any, b: Any) -> float | None:
    """The WCAG ratio between two colours, or None if either cannot be read."""
    la, lb = luminance(a), luminance(b)
    if la is None or lb is None:
        return None
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def floor(fill: Any, outline: Any) -> tuple[float, int] | None:
    """`(worst separation, the grey that causes it)` for an outlined glyph.

    The glyph separates from the backdrop by whichever of its two colours is
    further from it, so its separation is `max(contrast(fill), contrast(
    outline))` — and the floor is the smallest that maximum gets over every
    possible backdrop luminance.

    Swept over all 256 greys rather than solved, because the closed form
    (`(L+0.05)² = 0.0525` for white and black) only holds when the two colours
    are the extremes. A preset with a dark-purple outline like `Neon Pop` is not
    that case, and a formula that is right for one preset and quietly wrong for
    another is worse than a loop nobody will ever notice the cost of.
    """
    if luminance(fill) is None or luminance(outline) is None:
        return None
    worst: tuple[float, int] | None = None
    for level in range(256):
        grey = f"#{level:02X}{level:02X}{level:02X}"
        best = max(contrast(fill, grey) or 0.0, contrast(outline, grey) or 0.0)
        if worst is None or best < worst[0]:
            worst = (best, level)
    return worst


def verdict(style: Any) -> dict:
    """`caption_contrast_v1`: what this style guarantees, whatever is behind it.

    Recorded, applied to nothing. It changes no colour and moves no caption; it
    says what the palette can and cannot promise.
    """
    out: dict[str, Any] = {
        "schema": "caption_contrast_v1",
        "scope": "what_the_palette_guarantees_against_any_uniform_backdrop",
        "large_text_min": LARGE_TEXT_MIN,
        "normal_text_min": NORMAL_TEXT_MIN,
        # The thresholds are WCAG's, written for static web pages. Named, not
        # derived, and this rides with every verdict for the same reason it
        # rides with `source_captions`.
        "calibrated": False,
        "refused": [],
        "fill": None,
        "highlight": None,
        "applied": False,
    }
    if not isinstance(style, dict):
        out["refused"] = [NO_STYLE]
        return out

    outline = style.get("outline_color")
    fill = style.get("text_color")
    refused: list[str] = []
    if outline is None:
        refused.append(NO_OUTLINE)
    elif luminance(outline) is None:
        refused.append(BAD_COLOUR)
    if fill is None:
        refused.append(NO_FILL)
    elif luminance(fill) is None:
        refused.append(BAD_COLOUR)
    if refused:
        out["refused"] = sorted(set(refused))
        return out

    out["fill"] = _leg(fill, outline)
    # THE HIGHLIGHT IS OPTIONAL AND ITS ABSENCE IS NOT A PASS. A style with no
    # highlight colour paints every word in the fill, so there is nothing else
    # to check — but a style whose highlight cannot be read is refused rather
    # than skipped, because that is the colour the eye is drawn to.
    highlight = style.get("highlight_color")
    if highlight is not None:
        if luminance(highlight) is None:
            out["refused"] = [BAD_COLOUR]
            return out
        out["highlight"] = _leg(highlight, outline)
    return out


def _leg(colour: str, outline: str) -> dict:
    """One colour's floor, and which published bars it clears."""
    worst = floor(colour, outline)
    assert worst is not None  # both colours were checked by the caller
    ratio, grey = worst
    return {
        "colour": colour,
        "outline": outline,
        "floor": round(ratio, 2),
        "worst_backdrop_grey": grey,
        "clears_large_text": ratio >= LARGE_TEXT_MIN,
        "clears_normal_text": ratio >= NORMAL_TEXT_MIN,
    }
