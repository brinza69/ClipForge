"""The delivered clock: what a trim does to the report.

Split from `test_clipper_edit_quality.py` at 500 lines. The seam is the one the
module itself draws — `drop_spans` change the EDIT, not just the timeline, and
these are the rules for what may still be measured once seconds have been cut
out of a clip.
"""

from __future__ import annotations

from services.clipper import edit_quality as eq
from tests.test_clipper_edit_quality import _crop_fit_fit_crop, _shot, _sidecar


# --- the delivered clock -----------------------------------------------------


def test_the_seconds_the_render_removed_come_off_the_clock():
    """The clock half is exact arithmetic, so it is measured."""
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 4.0, "crop"), _shot(4.0, 10.0, "crop")],
        duration=10.0, drop_spans=[(1.0, 3.0)]))
    assert report["removed_s"] == 2.0
    assert report["duration_s"] == 8.0
    assert report["duration_clock"] == "delivered"


def test_a_trim_reconstructs_the_delivered_shot_sequence():
    """The middle shot is absent from the delivered video. The two surviving
    pieces meet because of a source-time jump, not a normal planner cut."""
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 2.0, "crop"), _shot(2.0, 4.0, "fit"), _shot(4.0, 8.0, "crop")],
        duration=8.0, drop_spans=[(2.0, 4.0)]))
    assert report["defects"] == []
    assert report["duration_s"] == 6.0
    assert report["shots"] == 2
    assert report["shots_per_minute"] == 20.0
    assert report["min_shot_s"] == 2.0
    assert report["composition"] == {"crop": 2}
    assert report["equivalent_cuts"] == 0
    assert report["trim_jumps"] == 1
    assert report["non_contiguous_boundaries"] == 0


def test_a_mid_shot_trim_is_not_counted_as_an_equivalent_cut():
    """Both pieces retain `fit`, but the image jumps forward in source time.
    Calling that boundary `equivalent` would hide the edit R8 exists to name."""
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0, "fit")], duration=10.0,
        drop_spans=[(4.0, 6.0)]))
    assert report["shots"] == 2
    assert report["composition"] == {"fit": 2}
    assert report["equivalent_cuts"] == 0
    assert report["trim_jumps"] == 1


def test_normal_boundaries_survive_beside_a_trim_jump():
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 2.0, "crop"), _shot(2.0, 8.0, "fit")],
        duration=8.0, drop_spans=[(5.0, 6.0)]))
    assert report["shots"] == 3
    assert report["trim_jumps"] == 1
    assert report["equivalent_cuts"] == 0
    assert report["non_contiguous_boundaries"] == 0


def test_an_explicit_empty_trim_preserves_every_shot_metric():
    shots = [_shot(0.0, 3.0, "crop"), _shot(3.0, 10.0, "fit")]
    plain = eq.clip_report(_sidecar(shots, duration=10.0))
    declared = eq.clip_report(_sidecar(shots, duration=10.0, drop_spans=[]))
    for key in ("shots", "shots_per_minute", "min_shot_s", "composition",
                "equivalent_cuts", "non_contiguous_boundaries"):
        assert declared[key] == plain[key], key
    assert plain["trim_jumps"] == eq.UNAVAILABLE
    assert declared["trim_jumps"] == 0


def test_word_times_are_remapped_before_the_tail_is_measured():
    """Untrimmed word times against a trimmed duration reported a tail that is
    exactly the removed seconds too long."""
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)], duration=10.0, drop_spans=[(2.0, 4.0)],
        words=[{"word": "a", "start": 0.0, "end": 0.5},
               {"word": "b", "start": 7.0, "end": 9.98}]))
    assert report["duration_s"] == 8.0
    assert abs(report["tail_s"] - 0.02) < 1e-9


def test_an_export_that_never_declared_its_trim_is_measured_on_the_window():
    """Absent `drop_spans` is not "nothing was removed" — the 58 pilot exports
    predate the key. The clock is named so the two are not read as the same."""
    report = eq.clip_report(_crop_fit_fit_crop())
    assert report["removed_s"] == eq.UNAVAILABLE
    assert report["duration_clock"] == "window"
    assert report["duration_s"] == 10.0
    assert report["trim_jumps"] == eq.UNAVAILABLE


def test_a_corrupt_trim_claim_is_a_finding_not_a_missing_one():
    """The export DID declare something; it just cannot be read. Falling back to
    the window said "never declared", which is the one answer that is certainly
    wrong — and this test used to assert only the fallback, so it pinned the bug
    instead of catching it."""
    report = eq.clip_report(_sidecar([_shot(0.0, 10.0)], drop_spans=[[1.0]]))
    assert "malformed_drop_spans" in report["defects"]
    assert report["removed_s"] == eq.UNAVAILABLE
    assert report["duration_clock"] == "window"
    assert "empty_dynamic_plan" not in report["defects"]
    for key in ("shots", "shots_per_minute", "min_shot_s", "composition",
                "equivalent_cuts", "trim_jumps", "non_contiguous_boundaries",
                "lead_in_s", "tail_s"):
        assert report[key] == eq.UNAVAILABLE, key


def test_a_backwards_trim_span_is_corrupt_too():
    report = eq.clip_report(_sidecar([_shot(0.0, 10.0)], drop_spans=[[4.0, 1.0]]))
    assert "malformed_drop_spans" in report["defects"]


def test_overlapping_or_unsorted_trim_spans_are_corrupt():
    for spans in ([(2.0, 5.0), (4.0, 6.0)], [(5.0, 6.0), (2.0, 3.0)]):
        report = eq.clip_report(_sidecar(
            [_shot(0.0, 10.0)], duration=10.0, drop_spans=spans))
        assert "malformed_drop_spans" in report["defects"], spans


def test_a_trim_span_outside_the_window_is_corrupt():
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)], duration=10.0, drop_spans=[(8.0, 12.0)]))
    assert "malformed_drop_spans" in report["defects"]


def test_a_trim_cannot_remove_the_entire_declared_window():
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)], duration=10.0, drop_spans=[(0.0, 10.0)]))
    assert "malformed_drop_spans" in report["defects"]
    assert report["duration_clock"] == "window"


def test_trim_does_not_hide_an_invalid_composition_it_removed():
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 2.0, "crop"), _shot(2.0, 4.0, "banana"),
         _shot(4.0, 8.0, "crop")],
        duration=8.0, drop_spans=[(2.0, 4.0)]))
    assert "invalid_composition" in report["defects"]
    assert report["composition"] == {"crop": 2}


def test_an_absent_trim_claim_is_not_a_defect():
    """The 58 pilot exports predate the key. Absence is not corruption."""
    report = eq.clip_report(_sidecar([_shot(0.0, 10.0)]))
    assert report["defects"] == []
    assert report["duration_clock"] == "window"
