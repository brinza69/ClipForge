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

    white #FFFFFF inside black #000000   4.58:1, at relative luminance 0.179

AND THE FLOOR HAS A CLOSED FORM: it is the SQUARE ROOT of the contrast between
the glyph's own two colours. `sqrt(21) = 4.58` for white in black. The worst
backdrop is the geometric mean of the two, `sqrt((Lhi+0.05)(Llo+0.05)) - 0.05`,
where neither colour is doing much and both are doing the same amount.

The first version swept 256 greys instead, with a comment claiming the closed
form only held when the two colours were the extremes. That was wrong — it holds
for any pair, `Neon Pop`'s dark-purple outline included — and the sweep was
wrong in the direction that flatters: quantising the backdrop to 8-bit greys
overstated every floor, white in black by 0.025. A backdrop pixel is 8-bit per
CHANNEL, but a coloured pixel's luminance is a weighted sum of three of them and
lands anywhere in between, so the bound has to be continuous.

AND THE FLOOR IS WHERE THE REAL FINDING IS. Run it over the seven shipping
presets and the fills are all fine — 4.39 to 4.58 — but the HIGHLIGHT colour,
the one word the karaoke animation paints, is a different palette:

    Classic White / Boxed White   4.58      Karaoke Yellow  4.07
    Bold Impact                   3.87      Clean Minimal   3.44
    Viral Gradient                2.72      Neon Pop        2.33

`Neon Pop` and `Viral Gradient` cannot GUARANTEE 3.0:1 for their highlighted
word. That is not a letterbox problem and no frame sample would have named it:
it is in the palette.

AND THE PRECISE READING OF THAT SENTENCE MATTERS, because the loose one says
something much stronger and false. A floor of 2.33 means: there EXISTS a
backdrop luminance at which the separation falls to 2.33. On most real backdrops
`Neon Pop` reads perfectly well. What it cannot do is promise.

The honest one-line form of any verdict here is: "the minimum separation this
palette can guarantee, assuming a uniform backdrop, an opaque fill and a visible
outline." Not "the rendered contrast passes".

WHAT THIS IS NOT.

It is a LUMINANCE floor. WCAG contrast ignores hue, so two colours that differ
only in hue score 1:1 here and are perfectly distinguishable on screen. The
number is a lower bound on separation, not a measurement of legibility, and not
a measurement of any frame that was actually rendered.

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

import math
from typing import Any

__all__ = ["LARGE_TEXT_MIN", "NORMAL_TEXT_MIN", "REFUSALS", "KNOWN_SHORTFALLS",
           "palette_key", "accepted_shortfall", "luminance", "contrast",
           "floor", "verdict"]

#: THE TWO PALETTES THAT CANNOT REACH THE BAR AND ARE SHIPPING ANYWAY, decided
#: by the project owner on 31 August 2026 after the measurement below. Neither
#: has ever been used: all 99 stored exports with a style are `Bold Impact`,
#: whose highlight floor is 3.87.
#:
#: The reason for accepting rather than fixing is in the arithmetic. The floor is
#: `sqrt(contrast(fill, outline))`, so reaching 3.0 needs the glyph's two colours
#: to differ by 9:1. `Viral Gradient` is at 7.41 and a small lightening to
#: `#FF9364` would clear it — but `Neon Pop` is at 5.42, and even a pure black
#: outline only lifts it to 2.43; its pink would have to go to about `#FF9DBB`,
#: which is a different preset. A saturated mid-luminance colour cannot both stay
#: saturated and clear a luminance bar.
#:
#: KEYED ON THE PALETTE, NOT ON THE NAME ALONE. An exception by name would let
#: somebody repaint `Neon Pop` into something worse and keep the pass. The key is
#: the name plus the three colours the floor is computed from — fill, highlight,
#: outline — plus the outline width. NOT `shadow_color`, which nothing here
#: reads. Change any of them and the key stops matching, the exception stops
#: applying, and the gate fires.
#:
#: AND THE NAME COMES FROM THE STYLE, so the same palette matches wherever it is
#: found. The first version took the name from the CALLER, which passes a preset
#: id for the shipping presets and `project/clip` for a stored export — so a
#: preset in the exception list matched in one population and not in the other,
#: and the first real use of an accepted preset would have made the presets pass
#: and the exports fail on the same palette.
KNOWN_SHORTFALLS: dict[tuple, str] = {
    ("Neon Pop", "#FFFFFF", "#FF3366", "#1A0033", "5"):
        "highlight floor 2.33; the palette cannot reach 3.0 without ceasing to "
        "be this palette. Accepted 2026-08-31, never used in any stored export.",
    ("Viral Gradient", "#FFFFFF", "#FF6B35", "#000000", "5"):
        "highlight floor 2.72; `#FF9364` would reach 3.10 if it is ever wanted. "
        "Accepted 2026-08-31, never used in any stored export.",
}


def palette_key(style: Any) -> tuple:
    """What makes two styles the same VERDICT: the style's own name and the
    three colours the floor is computed from, plus the outline width.

    THE NAME COMES FROM THE STYLE, not from whatever the caller happens to be
    calling it. The audit labels a stored export `project/clip (x99)` and a
    preset by its id, and a key built from those matches the same palette in one
    population and not in the other.

    The width is in the key because the floor depends on the outline being drawn
    at all, so a stored `None` and a stored `5` are different palettes even with
    identical colours.
    """
    got = style if isinstance(style, dict) else {}
    return (str(got.get("name")), str(got.get("text_color")),
            str(got.get("highlight_color")), str(got.get("outline_color")),
            str(got.get("outline_width")))


def accepted_shortfall(style: Any) -> str | None:
    """Why this exact palette is allowed to miss the bar, or None."""
    return KNOWN_SHORTFALLS.get(palette_key(style))


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
#: The whole floor argument rests on there BEING an outline. A style that names
#: an outline colour and a width of zero has a bare fill, whose worst case is a
#: backdrop of its own colour — 1.0, invisible — and the audit was checking the
#: colours without ever asking whether the outline is drawn.
NO_OUTLINE_WIDTH = "style_has_no_outline_width"
NO_OUTLINE_DRAWN = "outline_width_is_zero"
#: A fill or outline that is not fully opaque. THE CONVENTION IS SETTLED IN THIS
#: CODEBASE and does not need guessing: `captioner_presets.hex_to_ass_color`
#: turns `#RRGGBBAA` into `&HAABBGGRR` and `caption_overlays._rgba_from_hex`
#: hands the same byte to `pysubs2.Color` — and both default a 6-digit colour to
#: `a = 0`. That is ASS transparency: 00 is opaque, FF is invisible. So
#: `#000000B0` is about 31% opaque, which is what a soft shadow should be.
#:
#: It is still refused, but for a reason rather than out of doubt: a translucent
#: glyph composites WITH the backdrop, so its effective colour is a function of
#: the thing it is being compared against, and the whole floor argument assumes
#: two fixed colours. No shipping preset uses one for a fill or an outline; the
#: shadow does, and the shadow is never read here.
NOT_OPAQUE = "colour_is_not_fully_opaque"
REFUSALS: tuple[str, ...] = (BAD_COLOUR, NO_STYLE, NO_FILL, NO_OUTLINE,
                             NO_OUTLINE_WIDTH, NO_OUTLINE_DRAWN, NOT_OPAQUE)


def _rgb(value: Any) -> tuple[int, int, int] | None:
    """`#RRGGBB` to a triplet, or None if it is not one.

AN ALPHA CHANNEL IS READ, NOT DROPPED AND NOT GUESSED AT. `hex_to_ass_color`
    turns `#RRGGBBAA` into `&HAABBGGRR` and `_rgba_from_hex` hands the same byte
    to `pysubs2.Color`, and both default a 6-digit colour to `a = 0` — ASS
    transparency, where 00 is opaque. An 8-digit colour with `AA` of 00 is
    therefore exactly as solid as the 6-digit form and is read; anything else is
    translucent, and `luminance` returns None for it because a translucent glyph
    has no fixed colour to measure.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip("#")
    if len(text) == 8:
        try:
            if int(text[6:8], 16) != 0:
                return None
        except ValueError:
            return None
        text = text[:6]
    if len(text) != 6:
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


def floor(fill: Any, outline: Any) -> tuple[float, float] | None:
    """`(worst separation, the backdrop luminance that causes it)`.

    The glyph separates from the backdrop by whichever of its two colours is
    further from it, so its separation is `max(contrast(fill), contrast(
    outline))` — and the floor is the smallest that maximum gets over every
    backdrop.

    SOLVED, NOT SWEPT. Each of the two contrast curves is a V with its minimum
    of 1.0 at its own colour's luminance, so their maximum is smallest exactly
    where they cross, between the two. Setting `(L+0.05)/(Llo+0.05)` equal to
    `(Lhi+0.05)/(L+0.05)` gives `L = sqrt((Lhi+0.05)(Llo+0.05)) - 0.05` and a
    value of `sqrt((Lhi+0.05)/(Llo+0.05))` — which is the square root of the
    contrast between the glyph's two colours, and nothing else.

    The first version swept 256 greys, with a comment claiming this form only
    held when the two colours were the extremes. It holds for every pair; the
    sweep was the approximation, and it erred high — white in black came back
    4.6075 against a true 4.5826. The crossing always lies between the two
    luminances, so it is always a backdrop that can exist.
    """
    lf, lo_ = luminance(fill), luminance(outline)
    if lf is None or lo_ is None:
        return None
    hi, low = max(lf, lo_), min(lf, lo_)
    return (math.sqrt((hi + 0.05) / (low + 0.05)),
            math.sqrt((hi + 0.05) * (low + 0.05)) - 0.05)


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
        "outline_width": None,
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
        refused.append(_colour_refusal(outline))
    if fill is None:
        refused.append(NO_FILL)
    elif luminance(fill) is None:
        refused.append(_colour_refusal(fill))

    # AND WHETHER THE OUTLINE IS DRAWN AT ALL. Every number below assumes two
    # colours are on screen; a width of zero leaves one, and a bare fill's worst
    # backdrop is its own colour at 1.0.
    width = style.get("outline_width")
    if width is None:
        refused.append(NO_OUTLINE_WIDTH)
    elif isinstance(width, bool) or not isinstance(width, (int, float)):
        refused.append(NO_OUTLINE_WIDTH)
    elif not math.isfinite(width):
        # `width <= 0` is False for NaN, so a NaN width was clearing the check
        # and taking the outline's benefit with it — the same shape as the NaN
        # keep-out rectangle that clears `_norm_rect`. An infinite width is not
        # a width either.
        refused.append(NO_OUTLINE_WIDTH)
    elif width <= 0:
        refused.append(NO_OUTLINE_DRAWN)
    out["outline_width"] = width if isinstance(width, (int, float)) else None
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
            out["refused"] = [_colour_refusal(highlight)]
            return out
        out["highlight"] = _leg(highlight, outline)
    return out


def _colour_refusal(value: Any) -> str:
    """Why a colour could not be read: not opaque, or simply not a colour."""
    text = value.strip().lstrip("#") if isinstance(value, str) else ""
    if len(text) == 8:
        try:
            if int(text[6:8], 16) != 0:
                return NOT_OPAQUE
        except ValueError:
            return BAD_COLOUR
    return BAD_COLOUR


def _leg(colour: str, outline: str) -> dict:
    """One colour's floor, and which published bars it clears."""
    worst = floor(colour, outline)
    assert worst is not None  # both colours were checked by the caller
    ratio, at = worst
    return {
        "colour": colour,
        "outline": outline,
        "floor": round(ratio, 2),
        "worst_backdrop_luminance": round(at, 4),
        "clears_large_text": ratio >= LARGE_TEXT_MIN,
        "clears_normal_text": ratio >= NORMAL_TEXT_MIN,
    }
