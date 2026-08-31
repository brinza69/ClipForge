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
    """4.61:1 at grey 117. If this moves, the module's central claim moved with
    it and the docstring is lying."""
    ratio, grey = cc.floor(WHITE, BLACK)
    assert round(ratio, 2) == 4.61
    assert grey == 117


def test_no_backdrop_beats_the_floor():
    """Stated as the property rather than as the number: the floor is a floor."""
    ratio, _grey = cc.floor(WHITE, BLACK)
    for level in range(0, 256, 7):
        grey = f"#{level:02X}{level:02X}{level:02X}"
        separation = max(cc.contrast(WHITE, grey), cc.contrast(BLACK, grey))
        assert separation >= ratio - 1e-9, level


def test_the_two_presets_that_cannot_clear_the_large_text_bar():
    """The finding, and it is in the palette rather than on the letterbox. No
    frame sample would have named it."""
    neon = cc.verdict({"text_color": WHITE, "highlight_color": "#FF3366",
                       "outline_color": "#1A0033"})
    viral = cc.verdict({"text_color": WHITE, "highlight_color": "#FF6B35",
                        "outline_color": BLACK})
    assert neon["highlight"]["floor"] == 2.34
    assert viral["highlight"]["floor"] == 2.73
    assert neon["highlight"]["clears_large_text"] is False
    assert viral["highlight"]["clears_large_text"] is False
    # ...while both FILLS are fine, which is why looking only at the body text
    # would have called these two clean.
    assert neon["fill"]["clears_large_text"] is True
    assert viral["fill"]["clears_large_text"] is True


def test_the_shipping_preset_clears_both_bars_on_its_fill():
    told = cc.verdict({"text_color": WHITE, "highlight_color": "#FFD700",
                       "outline_color": BLACK})
    assert told["fill"]["floor"] == 4.61
    assert told["fill"]["clears_normal_text"] is True
    # The gold highlight clears the large-text bar and misses the normal one.
    assert told["highlight"]["floor"] == 3.88
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

    told = cc.verdict({"text_color": "puce", "outline_color": BLACK})
    assert cc.BAD_COLOUR in told["refused"]
    assert told["fill"] is None


def test_a_missing_colour_is_not_the_same_refusal_as_an_unreadable_one():
    assert cc.verdict({"outline_color": BLACK})["refused"] == [cc.NO_FILL]
    assert cc.verdict({"text_color": WHITE})["refused"] == [cc.NO_OUTLINE]
    assert cc.verdict(None)["refused"] == [cc.NO_STYLE]
    assert cc.verdict("bold_impact")["refused"] == [cc.NO_STYLE]


def test_a_style_with_no_highlight_reports_none_rather_than_a_pass():
    """Every word is painted in the fill, so there is nothing else to check —
    and `None` says that, where a `clears_large_text: true` would claim a
    measurement of a colour that does not exist."""
    told = cc.verdict({"text_color": WHITE, "outline_color": BLACK})
    assert told["highlight"] is None
    assert told["refused"] == []


def test_an_unreadable_highlight_is_refused_rather_than_skipped():
    """It is the colour the eye is drawn to."""
    told = cc.verdict({"text_color": WHITE, "outline_color": BLACK,
                       "highlight_color": "gold"})
    assert told["refused"] == [cc.BAD_COLOUR]


# --- the arithmetic ----------------------------------------------------------


def test_alpha_is_dropped_only_where_it_is_meaningless():
    """The shadow colour carries one (`#000000B0`) and the shadow sits BEHIND
    the outline, so it changes what the backdrop looks like and never what the
    glyph edge is made of. `_rgb` is only ever asked about the fill and the
    outline, both opaque."""
    assert cc.luminance("#000000B0") == cc.luminance("#000000")


def test_the_ratio_is_symmetric_and_bounded():
    assert cc.contrast(WHITE, BLACK) == 21.0
    assert cc.contrast(BLACK, WHITE) == 21.0
    assert cc.contrast(WHITE, WHITE) == 1.0


def test_the_verdict_never_claims_to_be_calibrated_or_applied():
    told = cc.verdict({"text_color": WHITE, "outline_color": BLACK})
    assert told["calibrated"] is False
    assert told["applied"] is False
