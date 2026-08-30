"""Batch R6: the caption placement decision, written down.

WHAT THESE TESTS ARE GUARDING, and it is one thing above all others: that this
module EXPLAINS `resolve_position` rather than re-implementing it. Batch R5 paid
for that lesson — `_snapped()` copied `_fit`'s guards "just to report on them",
the copy drifted, and the audit passed clips the worker would have refused.

So the tests below check agreement with the shipping function on cases where it
was NOT consulted for the value under test, and they check that the vocabulary
can fail. A reason that always finds one is a label, not a finding.
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


# --- it explains the decision that ships ------------------------------------


def test_the_chosen_position_is_the_one_the_renderer_burns():
    """Not a second opinion about it. If these two ever disagree, the sidecar is
    describing a caption nobody sees."""
    for position in ("bottom", "top", "center", "hook"):
        for layout in ({}, _zones(face=_px(1000, 500)),
                       _zones(hud=_px(0, 300), face=_px(1400, 400)),
                       _zones(everything=_px(0, OUT_H))):
            _x, y = resolve_position(position, layout, out_w=OUT_W, out_h=OUT_H)
            told = cc.explain(position, layout, out_w=OUT_W, out_h=OUT_H)
            assert told["chosen"] == round(y, 4), (position, layout)


def test_the_preset_comes_from_the_shipping_function_not_a_copy():
    """An empty layout has nothing to avoid, so `resolve_position` returns its
    own clamped base. Recomputing it from `SAFE_TOP` and `CAPTION_BOX_H_PCT`
    would be a second copy of the base rule and a second thing to drift."""
    for position in ("bottom", "top", "center", "hook", "", "nonsense"):
        _x, base = resolve_position(position, {}, out_w=OUT_W, out_h=OUT_H)
        told = cc.explain(position, _zones(face=_px(1000, 500)),
                          out_w=OUT_W, out_h=OUT_H)
        assert told["proposed"] == round(base, 4), position


def test_it_never_claims_to_change_anything():
    told = cc.explain("bottom", _zones(face=_px(1000, 500)))
    assert told["changes_anything"] is False


# --- the reasons -------------------------------------------------------------


def test_no_keep_out_rectangles_reports_no_search():
    """Saying "zero rejected" out of a run that never scanned would read as
    "every other position was considered and beaten"."""
    told = cc.explain("bottom", {}, out_w=OUT_W, out_h=OUT_H)
    assert told["reason"] == cc.NO_RECTS
    assert told["scanned"] == 0 and told["bands"] == []
    assert told["rejected"] == [] and told["moved"] is False


def test_a_clear_preset_is_left_where_it_was_asked_for():
    """`resolve_position` says so in its own comment: the preset position is a
    measurement in its own right."""
    told = cc.explain("bottom", _zones(hud=_px(0, 200)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["reason"] == cc.PRESET_CLEAR
    assert told["moved"] is False
    assert told["coverage_at_chosen"] == 0.0


def test_a_search_that_never_ran_rejected_nothing():
    """`resolve_position` returns before the scan when the preset is clear. The
    other clear runs are real and worth reporting, but calling them "rejected"
    would say they had been considered and beaten."""
    told = cc.explain("bottom", _zones(hud=_px(0, 200)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["reason"] == cc.PRESET_CLEAR
    assert told["searched"] is False
    assert told["rejected"] == []
    assert told["bands"], "the runs are still reported"
    assert all(b["rejected_because"] is None for b in told["bands"])
    # And no band is "taken" either: the delivered position was not picked out
    # of them. Saying otherwise would credit the search with the answer.
    assert not any(b["taken"] for b in told["bands"])


def test_the_spec_clamp_wins_over_a_band_outside_it():
    """The clamp is applied first and width second, and the rejection reason
    has to say which one did the work.

    NO SILENT SKIP. The first version returned early if the geometry did not
    produce `BAND_IN_SPEC`, so it passed on any change that stopped producing
    it — a test that cannot fail is a test that is not run. The layout below is
    one of 135 found by sweeping rect positions and heights for exactly this
    case, and if it stops being one, this fails."""
    told = cc.explain("hook", _zones(mid=_px(400, 300)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["reason"] == cc.BAND_IN_SPEC, told["reason"]
    assert SPEC_BAND_LO <= told["chosen"] <= SPEC_BAND_HI
    losers = [b for b in told["rejected"] if not b["in_spec"]]
    assert losers, "a band outside the clamp existed"
    assert all(b["rejected_because"] == cc.OUT_OF_SPEC for b in losers)


def test_nothing_clear_anywhere_says_so_and_reports_the_coverage():
    """A caption that has to sit on something is a different answer from one
    that found a gap, and the sidecar must not spell them the same."""
    told = cc.explain("bottom", _zones(all=_px(0, OUT_H)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["reason"] == cc.NOTHING_CLEAR
    assert told["clear"] == 0 and told["bands"] == []
    assert told["coverage_at_chosen"] > 0.0


def test_the_vocabulary_can_fail():
    """`UNEXPLAINED` is reachable, and that is the point of it. A reason that
    always finds one is a label applied after the fact."""
    assert cc.UNEXPLAINED in cc.REASONS
    # Drive `_reason` directly with an answer no rule accounts for: a position
    # that is clear, inside the clamp, with clear ground inside the clamp — but
    # not the one that came back.
    assert cc._reason(chosen=0.60, base=0.75, rects_used=1,
                      chosen_covered=0.004, clear=[0.60],
                      clear_in_spec=[0.60]) == cc.UNEXPLAINED


def test_every_reason_is_in_the_closed_list():
    for layout in ({}, _zones(hud=_px(0, 200)), _zones(face=_px(1000, 500)),
                   _zones(all=_px(0, OUT_H)), _zones(hud=_px(0, 60),
                                                     face=_px(0, 1500))):
        told = cc.explain("bottom", layout, out_w=OUT_W, out_h=OUT_H)
        assert told["reason"] in cc.REASONS, layout


# --- the alternatives --------------------------------------------------------


def test_the_bands_are_the_search_s_own_candidates():
    """`_widest_band` picks among runs of consecutive clear positions. Reporting
    anything else would be a parallel candidate set invented for the report."""
    told = cc.explain("bottom", _zones(face=_px(1000, 500)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["bands"], "a clear run exists above the face"
    taken = [b for b in told["bands"] if b["taken"]]
    assert len(taken) == 1, "exactly one band was taken"
    assert taken[0]["centre"] == told["chosen"]
    assert told["rejected"] == [b for b in told["bands"] if not b["taken"]]


def test_the_clamp_is_applied_before_the_width():
    """That is the order the search applies them in, so a band outside the clamp
    loses to the clamp whatever its width — and the rejection reason has to say
    which of the two did the work."""
    wide_outside = {"steps": 30, "in_spec": False}
    narrow_inside = {"steps": 2, "in_spec": True}
    assert cc._why_rejected(wide_outside, any_in_spec=True,
                            widest=2) == cc.OUT_OF_SPEC
    assert cc._why_rejected(narrow_inside, any_in_spec=True,
                            widest=30) == cc.NARROWER
    # With nothing clear inside the clamp, the clamp does no work at all.
    assert cc._why_rejected(wide_outside, any_in_spec=False,
                            widest=30) is None


def test_a_band_as_wide_as_the_winner_is_not_given_a_made_up_reason():
    """`_widest_band` breaks that tie by scan order, which is an implementation
    detail and not a reason. `NARROWER` there would be a guess dressed as a
    finding, so the answer is None."""
    tied = {"steps": 12, "in_spec": True}
    assert cc._why_rejected(tied, any_in_spec=True, widest=12) is None


def test_the_rejection_reasons_on_real_geometry_stay_in_the_vocabulary():
    """A rect over the preset splits the scan into two runs, so the search
    really runs and really rejects one."""
    told = cc.explain("center", _zones(mid=_px(900, 200)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["searched"], "the preset is covered, so the scan ran"
    assert told["rejected"], "the rect splits the scan into two runs"
    for band in told["rejected"]:
        assert band["rejected_because"] in (*cc.REJECTED_BECAUSE, None), band
    assert [b["taken"] for b in told["bands"]].count(True) == 1


def test_a_band_centre_between_grid_points_still_matches_its_band():
    """A run of EVEN length has its centre halfway between two scan positions,
    so matching the chosen position to a band needs a tolerance. Without one, an
    even run reported `taken: false` for every band and the sidecar said the
    winner had been rejected."""
    seen_even = False
    ran = 0
    for top in range(600, 1400, 20):
        told = cc.explain("center", _zones(mid=_px(top, 300)),
                          out_w=OUT_W, out_h=OUT_H)
        if not told["searched"] or not told["bands"]:
            continue
        ran += 1
        taken = [b for b in told["bands"] if b["taken"]]
        assert len(taken) == 1, (top, told["chosen"], told["bands"])
        seen_even = seen_even or any(b["steps"] % 2 == 0 for b in told["bands"])
    assert ran, "at least one of these has to reach the scan"
    assert seen_even, "the even-length case has to actually occur"


def test_the_bottom_preset_is_above_the_last_position_the_scan_can_reach():
    """`bottom` is exactly `hi`, and the grid stops one step short because 59.58
    steps truncate to 59. So a caption left at its preset sits somewhere the
    search could not have put it — which is why an empty `taken` on the preset
    branch is a fact about the search and not a bug in the report."""
    lo, hi = scan_bounds(OUT_H)
    grid = scan_grid(lo, hi)
    _x, preset = resolve_position("bottom", {}, out_w=OUT_W, out_h=OUT_H)
    assert preset == round(hi, 4)
    assert grid[-1] < preset, (grid[-1], preset)

    told = cc.explain("bottom", _zones(hud=_px(0, 200)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["reason"] == cc.PRESET_CLEAR
    assert told["chosen_inside_a_clear_run"] is False


def test_a_position_the_search_did_choose_is_inside_a_clear_run():
    """The other half of the same field: when the scan ran, the answer it
    returned is one of the runs it was choosing between."""
    told = cc.explain("center", _zones(mid=_px(900, 200)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["searched"] is True
    assert told["chosen_inside_a_clear_run"] is True


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


def test_an_entry_that_is_not_a_rectangle_is_counted_too():
    """`_iter_rects` ignores anything without `w` and `h`. Offered and thrown
    away at the other gate is still offered and thrown away."""
    told = cc.explain("bottom", {"safe_zones": {"a": {"x": 0, "y": 1000},
                                                "b": "not a rect"}},
                      out_w=OUT_W, out_h=OUT_H)
    assert told["keep_out"]["offered"] == 2
    assert told["keep_out"]["recognised_as_rectangles"] == 0
    assert told["keep_out"]["unreadable"] == 2


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


# --- the arithmetic it must not invent ---------------------------------------


def test_the_grid_is_the_one_the_search_evaluates():
    """It was rebuilt from `resolve_position`'s ROUNDED return values with
    `int(round(...))` where the search uses `int(...)`. Two small differences,
    one wrong answer: 61 candidates against the search's 60, the extra one at
    0.7542 above a real upper bound of 0.75 — a position that could never have
    been chosen, reported as one that lost."""
    lo, hi = scan_bounds(OUT_H)
    told = cc.explain("bottom", _zones(face=_px(1000, 500)),
                      out_w=OUT_W, out_h=OUT_H)
    assert told["scanned"] == len(scan_grid(lo, hi))
    assert all(b["to"] <= hi + 1e-9 for b in told["bands"]), "nothing above hi"
    assert min(b["from"] for b in told["bands"]) >= round(lo, 4) - 1e-9


def test_no_candidate_sits_outside_the_band_the_search_may_use():
    """The regression, stated as the thing it broke rather than as the arithmetic
    that broke it."""
    lo, hi = scan_bounds(OUT_H)
    for layout in (_zones(face=_px(1000, 500)), _zones(mid=_px(900, 200)),
                   _zones(hud=_px(0, 60), face=_px(0, 1500))):
        told = cc.explain("bottom", layout, out_w=OUT_W, out_h=OUT_H)
        for band in told["bands"]:
            assert lo - 1e-9 <= band["from"] <= band["to"] <= hi + 1e-9, band


def test_a_pathological_output_size_does_not_produce_a_backwards_grid():
    """`resolve_position` guards this with `if lo > hi: lo = hi`, so a report
    that scanned a negative range would be describing a search that never
    happened."""
    told = cc.explain("bottom", _zones(face=_px(10, 20)), out_w=100, out_h=100)
    assert told["scanned"] >= 1
    assert all(math.isfinite(b["centre"]) for b in told["bands"])


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
