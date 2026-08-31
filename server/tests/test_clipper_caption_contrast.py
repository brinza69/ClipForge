"""Batch R6: what the caption palette guarantees against any backdrop.

The point of these is that the answer is ARITHMETIC. A white glyph inside a
black outline separates from any backdrop by one of its two colours, so there is
a floor, and no frame sample can find a number below it. What the tests guard is
that the floor is computed over the real range and that an unreadable colour is
refused rather than defaulted — defaulting to black would turn "nobody knows
what colour this is" into the most favourable answer available.
"""

from __future__ import annotations

from services.clipper import caption_contrast as cc

WHITE, BLACK = "#FFFFFF", "#000000"


def test_the_white_on_black_floor_is_the_number_in_the_docstring():
    """`sqrt(21) = 4.58`, at relative luminance 0.179. If this moves, the
    module's central claim moved with it and the docstring is lying."""
    import math

    ratio, at = cc.floor(WHITE, BLACK)
    assert round(ratio, 4) == round(math.sqrt(21.0), 4) == 4.5826
    assert round(at, 4) == 0.1791


def test_the_closed_form_is_the_answer_a_sweep_would_find():
    """SOLVED, NOT SWEPT — and this is the check that the solution is right. The
    first version swept 256 greys and its comment claimed the closed form only
    held when the two colours were the extremes; it holds for every pair, and
    the sweep was the approximation."""
    pairs = [(WHITE, BLACK), ("#FFD700", BLACK), ("#FF3366", "#1A0033"),
             ("#FF6B35", BLACK), ("#00D4FF", BLACK), (BLACK, WHITE),
             ("#808080", "#7F7F7F")]
    for fill, outline in pairs:
        ratio, _at = cc.floor(fill, outline)
        lf, lo = cc.luminance(fill), cc.luminance(outline)
        swept = min(
            max((max(lf, x) + 0.05) / (min(lf, x) + 0.05),
                (max(lo, x) + 0.05) / (min(lo, x) + 0.05))
            for x in (i / 20000.0 for i in range(20001)))
        # The closed form is the INFIMUM, so it can only be at or below what a
        # discrete sweep finds — and the gap is the sweep's own resolution.
        assert ratio <= swept + 1e-9, (fill, outline, ratio, swept)
        assert swept - ratio < 1e-3, (fill, outline, ratio, swept)


def test_the_eight_bit_sweep_overstated_every_floor():
    """Quantising the backdrop to 8-bit greys erred in the direction that
    flatters. A backdrop pixel is 8-bit per CHANNEL, but a coloured pixel's
    luminance is a weighted sum of three of them and lands anywhere between."""
    ratio, _at = cc.floor(WHITE, BLACK)
    greys = min(max(cc.contrast(WHITE, f"#{v:02X}{v:02X}{v:02X}"),
                    cc.contrast(BLACK, f"#{v:02X}{v:02X}{v:02X}"))
                for v in range(256))
    assert greys > ratio, "the sweep was the approximation"
    assert round(greys - ratio, 3) == 0.025


def test_no_backdrop_beats_the_floor():
    """Stated as the property rather than as the number: the floor is a floor."""
    ratio, _at = cc.floor(WHITE, BLACK)
    for level in range(0, 256, 7):
        grey = f"#{level:02X}{level:02X}{level:02X}"
        separation = max(cc.contrast(WHITE, grey), cc.contrast(BLACK, grey))
        assert separation >= ratio - 1e-9, level


def test_the_two_presets_that_cannot_clear_the_large_text_bar():
    """The finding, and it is in the palette rather than on the letterbox. No
    frame sample would have named it."""
    neon = cc.verdict({"text_color": WHITE, "highlight_color": "#FF3366",
                       "outline_color": "#1A0033", "outline_width": 5})
    viral = cc.verdict({"text_color": WHITE, "highlight_color": "#FF6B35",
                        "outline_color": BLACK, "outline_width": 5})
    assert neon["highlight"]["floor"] == 2.33
    assert viral["highlight"]["floor"] == 2.72
    assert neon["highlight"]["clears_large_text"] is False
    assert viral["highlight"]["clears_large_text"] is False
    # ...while both FILLS are fine, which is why looking only at the body text
    # would have called these two clean.
    assert neon["fill"]["clears_large_text"] is True
    assert viral["fill"]["clears_large_text"] is True


def test_the_shipping_preset_clears_both_bars_on_its_fill():
    told = cc.verdict({"text_color": WHITE, "highlight_color": "#FFD700",
                       "outline_color": BLACK, "outline_width": 5})
    assert told["fill"]["floor"] == 4.58
    assert told["fill"]["clears_normal_text"] is True
    # The gold highlight clears the large-text bar and misses the normal one.
    assert told["highlight"]["floor"] == 3.87
    assert told["highlight"]["clears_large_text"] is True
    assert told["highlight"]["clears_normal_text"] is False


# --- what it refuses ---------------------------------------------------------


def test_a_colour_nobody_can_read_is_refused_not_defaulted():
    """Defaulting to black would turn "nobody knows what colour this is" into
    the most favourable possible answer: black is one end of the luminance range
    and contrasts maximally with a white fill."""
    for bad in ("red", "#GGGGGG", "#FFF", "", 7, None, ["#FFFFFF"]):
        assert cc.luminance(bad) is None, repr(bad)
        assert cc.contrast(bad, WHITE) is None, repr(bad)
        assert cc.floor(bad, BLACK) is None, repr(bad)

    told = cc.verdict({"text_color": "puce", "outline_color": BLACK,
                       "outline_width": 5})
    assert cc.BAD_COLOUR in told["refused"]
    assert told["fill"] is None


def test_a_missing_colour_is_not_the_same_refusal_as_an_unreadable_one():
    assert cc.verdict({"outline_color": BLACK,
                       "outline_width": 5})["refused"] == [cc.NO_FILL]
    assert cc.verdict({"text_color": WHITE,
                       "outline_width": 5})["refused"] == [cc.NO_OUTLINE]
    assert cc.verdict(None)["refused"] == [cc.NO_STYLE]
    assert cc.verdict("bold_impact")["refused"] == [cc.NO_STYLE]


def test_an_outline_that_is_not_drawn_is_not_an_outline():
    """Every number in this module assumes two colours are on screen. A width of
    zero leaves one, and a bare fill's worst backdrop is its own colour at 1.0 —
    invisible. The audit was checking the colours without ever asking whether
    the outline is drawn."""
    assert cc.verdict({"text_color": WHITE, "outline_color": BLACK,
                       "outline_width": 0})["refused"] == [cc.NO_OUTLINE_DRAWN]
    assert cc.verdict({"text_color": WHITE, "outline_color": BLACK,
                       "outline_width": -2})["refused"] == [cc.NO_OUTLINE_DRAWN]
    for bad in (None, "5", True, [5]):
        told = cc.verdict({"text_color": WHITE, "outline_color": BLACK,
                           "outline_width": bad})
        assert told["refused"] == [cc.NO_OUTLINE_WIDTH], repr(bad)


def test_the_alpha_convention_is_read_from_the_codebase_not_guessed():
    """`hex_to_ass_color` turns `#RRGGBBAA` into `&HAABBGGRR` and
    `_rgba_from_hex` hands the same byte to `pysubs2.Color`, and both default a
    6-digit colour to `a = 0`. That is ASS transparency: 00 is opaque. So an
    8-digit colour whose alpha is 00 is exactly as solid as the 6-digit form."""
    assert cc.luminance("#FFFFFF00") == cc.luminance(WHITE)
    assert cc.luminance("#00000000") == cc.luminance(BLACK)
    told = cc.verdict({"text_color": "#FFFFFF00", "outline_color": "#00000000",
                       "outline_width": 5})
    assert told["refused"] == [] and told["fill"]["floor"] == 4.58


def test_a_translucent_colour_is_refused_for_a_reason_rather_than_out_of_doubt():
    """A translucent glyph composites WITH the backdrop, so its effective colour
    is a function of the thing it is being compared against — and the whole
    floor argument assumes two fixed colours. The shadow is the one place a
    translucent colour appears, and the shadow is never read here."""
    assert cc.luminance("#000000B0") is None, "31% opaque is not black"
    told = cc.verdict({"text_color": "#FFFFFFB0", "outline_color": BLACK,
                       "outline_width": 5})
    assert told["refused"] == [cc.NOT_OPAQUE]
    outlined = cc.verdict({"text_color": WHITE, "outline_color": "#000000FF",
                           "outline_width": 5})
    assert outlined["refused"] == [cc.NOT_OPAQUE], "FF is invisible in ASS"


def test_a_style_with_no_highlight_reports_none_rather_than_a_pass():
    """Every word is painted in the fill, so there is nothing else to check —
    and `None` says that, where a `clears_large_text: true` would claim a
    measurement of a colour that does not exist."""
    told = cc.verdict({"text_color": WHITE, "outline_color": BLACK,
                       "outline_width": 5})
    assert told["highlight"] is None
    assert told["refused"] == []


def test_an_unreadable_highlight_is_refused_rather_than_skipped():
    """It is the colour the eye is drawn to."""
    told = cc.verdict({"text_color": WHITE, "outline_color": BLACK,
                       "outline_width": 5, "highlight_color": "gold"})
    assert told["refused"] == [cc.BAD_COLOUR]


# --- the arithmetic ----------------------------------------------------------


def test_the_ratio_is_symmetric_and_bounded():
    assert cc.contrast(WHITE, BLACK) == 21.0
    assert cc.contrast(BLACK, WHITE) == 21.0
    assert cc.contrast(WHITE, WHITE) == 1.0


def test_the_verdict_never_claims_to_be_calibrated_or_applied():
    told = cc.verdict({"text_color": WHITE, "outline_color": BLACK,
                       "outline_width": 5})
    assert told["calibrated"] is False
    assert told["applied"] is False
