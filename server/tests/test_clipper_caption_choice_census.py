"""Batch R6: what the placement EXPLANATION refuses, and what it counts.

Split from `test_clipper_caption_choice.py` at the 500-line limit. That file is
about the decision — the grid, the bands, the reason — and this one is about the
two things around it:

- the keep-out CENSUS, and the finding it carries rather than fixes: a
  non-finite rectangle clears `captions._norm_rect` and then covers the caption
  box at every scan position, so one malformed entry moves the delivered
  caption;
- that the explanation NEVER RAISES. A caption is burned whether or not anybody
  can say why, so an explanation that throws takes the export down to report on
  it.

Fixtures are duplicated from the other file rather than shared, the same trade
`test_clipper_caption_placement_refusals.py` makes: four short builders is
cheaper than a third place to look when a fixture surprises you.
"""

from __future__ import annotations

import math

from services.clipper import caption_choice as cc
from services.clipper.captions import (
    SPEC_BAND_HI,
    SPEC_BAND_LO,
    resolve_position,
    scan_bounds,
    scan_grid,
)

OUT_W, OUT_H = 1080, 1920


def _zones(**rects) -> dict:
    return {"safe_zones": {k: v for k, v in rects.items()}}


def _px(y: int, h: int, x: int = 0, w: int = OUT_W) -> dict:
    return {"x": x, "y": y, "w": w, "h": h}


# --- the finding it carries rather than fixes --------------------------------


def test_an_unreadable_keep_out_is_counted_not_hidden():
    """`_norm_rect` drops a rect with a non-positive width without a word, so a
    malformed keep-out becomes "nothing in the way" and the caption is placed as
    if the frame were clear. That is live behaviour in shipping code and this
    batch does not move delivered captions — so the count travels instead."""
    told = cc.explain("bottom", _zones(bad=_px(1000, 0), worse=_px(1000, -5)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["keep_out"]["offered"] == 2
    assert told["keep_out"]["used"] == 0
    assert told["keep_out"]["unreadable"] == 2
    # And the placement is still the one that ships, unchanged.
    _x, y = resolve_position("bottom", _zones(bad=_px(1000, 0),
                                              worse=_px(1000, -5)),
                             out_w=OUT_W, out_h=OUT_H)
    assert told["chosen"] == round(y, 4)


def test_a_record_without_w_and_h_is_counted_as_lost():
    """`_iter_rects` ignores anything without them. Something shaped like a
    record going past without a word is the suspicious case."""
    told = cc.explain("bottom", {"safe_zones": {"a": {"x": 0, "y": 1000},
                                                "b": {"nope": 1}}},
                      out_w=OUT_W, out_h=OUT_H)
    assert told["keep_out"]["offered"] == 2
    assert told["keep_out"]["recognised_as_rectangles"] == 0
    assert told["keep_out"]["unreadable"] == 2


def test_a_sibling_scalar_is_not_a_lost_rectangle():
    """A real `safe_zones` is four scalars and one list of rectangles. Counting
    the scalars as offered reported four unreadable on every export in the
    corpus — a loud false alarm about numbers that were never rectangles."""
    real = {"safe_zones": {"top": 200, "caption_bottom": 480,
                           "caption_center": 120, "hook_mid_y": 700,
                           "keep_out": [{"x": 264, "y": 440, "w": 696,
                                         "h": 696, "kind": "face"}]}}
    told = cc.explain("bottom", real, out_w=OUT_W, out_h=OUT_H)
    assert told["keep_out"]["not_rectangle_shaped"] == 4
    assert told["keep_out"]["offered"] == 1
    assert told["keep_out"]["used"] == 1
    assert told["keep_out"]["unreadable"] == 0


_NAN_RECT = {"x": 0, "y": float("nan"), "w": OUT_W, "h": float("nan")}


def test_a_non_finite_keep_out_moves_the_delivered_caption():
    """It clears `_norm_rect` — every comparison against a NaN is false — and
    then overlaps the caption box at EVERY scan position, because `min(by1,
    nan)` returns `by1` and `max(by0, nan)` returns `by0`. So one malformed
    entry erases the band search entirely and the caption is burned somewhere
    else. Live behaviour in `captions`; this batch counts it, it does not move
    delivered captions to fix it."""
    told = cc.explain("bottom", {"safe_zones": {"nan": _NAN_RECT}},
                      out_w=OUT_W, out_h=OUT_H)
    assert told["keep_out"]["used"] == 1
    assert told["keep_out"]["not_finite"] == 1
    assert told["keep_out"]["unreadable"] == 0, "it was not dropped"
    assert told["coverage_at_chosen"] > 0.0, "it covers the chosen position"

    # And the measurement in the docstring, executed: the same layout with and
    # without the NaN rect beside a real one.
    clean = cc.explain("bottom", _zones(face=_px(1000, 500)),
                       out_w=OUT_W, out_h=OUT_H)
    spoilt = cc.explain("bottom", {"safe_zones": {"face": _px(1000, 500),
                                                  "nan": _NAN_RECT}},
                        out_w=OUT_W, out_h=OUT_H)
    assert clean["reason"] == cc.BAND_OUTSIDE_SPEC and clean["clear"] > 0
    assert spoilt["reason"] == cc.NOTHING_CLEAR and spoilt["clear"] == 0
    assert spoilt["chosen"] != clean["chosen"], "it moved the caption"


def test_nested_lists_of_rectangles_are_counted_the_way_they_are_walked():
    """`_iter_rects` expands one level of list, so the census does too — any
    other depth would report a denominator the search never saw."""
    told = cc.explain("bottom", {"safe_zones": {"faces": [_px(1000, 200),
                                                          _px(1300, 200)]}},
                      out_w=OUT_W, out_h=OUT_H)
    assert told["keep_out"]["offered"] == 2
    assert told["keep_out"]["used"] == 2


# --- it never raises ---------------------------------------------------------


def test_an_explanation_never_takes_the_export_down_with_it():
    """A caption is burned whether or not anybody can say why. `(layout or
    {}).get` raised on a list, `int(out_w)` on a string, and `_base_y_pct` calls
    `position.strip()` — so a position that is a number raised from inside
    `resolve_position` before this file could say anything."""
    cases = [
        (("bottom", ["not", "a", "layout"]), {}, cc.BAD_LAYOUT),
        (("bottom", "safe_zones"), {}, cc.BAD_LAYOUT),
        ((7, _zones(face=_px(1000, 500))), {}, cc.BAD_POSITION),
        (([], _zones(face=_px(1000, 500))), {}, cc.BAD_POSITION),
        (("bottom", {}), {"out_w": "1080"}, cc.BAD_OUT_SIZE),
        (("bottom", {}), {"out_h": 0}, cc.BAD_OUT_SIZE),
        (("bottom", {}), {"out_h": float("nan")}, cc.BAD_OUT_SIZE),
    ]
    for args, kwargs, expected in cases:
        told = cc.explain(*args, **kwargs)  # must not raise
        assert expected in told["refused"], (args, kwargs, told["refused"])
        assert told["chosen"] is None, "and it claims nothing"
        assert told["reason"] is None
        assert told["bands"] == [] and told["rejected"] == []


def test_a_refusal_carries_every_field_a_caller_reads():
    """A refusal that omits keys is a refusal the caller crashes on, which is
    the failure this exists to prevent, one level up."""
    good = set(cc.explain("bottom", _zones(face=_px(1000, 500)),
                          out_w=OUT_W, out_h=OUT_H))
    bad = set(cc.explain("bottom", ["not a layout"]))
    assert good <= bad, sorted(good - bad)


def test_a_position_that_is_a_name_nobody_defined_is_still_explained():
    """`_base_y_pct` falls through to the bottom preset for any unknown name,
    and for `None` — `(position or "bottom")` is its first move. Both are real
    answers the shipping function gives, so both get explained. Only a position
    that is not a NAME is refused."""
    for position in ("nonsense", "", None):
        told = cc.explain(position, _zones(face=_px(1000, 500)),
                          out_w=OUT_W, out_h=OUT_H)
        assert told["refused"] == [], repr(position)
        assert told["chosen"] is not None, repr(position)
        _x, shipped = resolve_position(position or "bottom", {},
                                       out_w=OUT_W, out_h=OUT_H)
        assert told["proposed"] == round(shipped, 4), repr(position)


def test_the_scalar_keys_are_the_ones_the_layout_engine_emits():
    """`SIBLING_KEYS` is a copy of a schema `layout_geom._safe_zones` owns, and
    a copy that nothing checks is the mistake Batch R5 paid for. This holds it
    against the producer."""
    from services.clipper.layout_geom import _safe_zones

    emitted = set(_safe_zones("fullscreen", None, None, None, [], 0.3))
    assert cc.SIBLING_KEYS == emitted - {"keep_out"}, emitted


def test_a_corrupt_rectangle_container_is_not_furniture():
    """Waving a value through because it is not a dict or a list is how a set
    where the rectangle list belongs produced a clean census."""
    for bad in ({"oops"}, "keep_out", 7, object()):
        told = cc.explain("bottom", {"safe_zones": {"top": 200,
                                                    "keep_out": bad}},
                          out_w=OUT_W, out_h=OUT_H)
        assert told["keep_out"]["not_rectangle_shaped"] == 1, repr(bad)
        assert told["keep_out"]["unreadable"] >= 1, repr(bad)


def test_a_scalar_under_a_key_nobody_defined_is_suspicious():
    told = cc.explain("bottom", {"safe_zones": {"top": 200, "mystery": 42}},
                      out_w=OUT_W, out_h=OUT_H)
    assert told["keep_out"]["not_rectangle_shaped"] == 1, "only `top`"
    assert told["keep_out"]["unreadable"] == 1, "`mystery` is unaccounted for"
