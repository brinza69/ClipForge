"""Aggregation: what may and may not be added up across clips.

Split from `test_clipper_edit_quality.py` at 500 lines, following the module
split. Everything here is about the rules that only exist above a single
export — paired rates, and totals that refuse to be lower bounds.
"""

from __future__ import annotations

from services.clipper import edit_quality as eq
from services.clipper import edit_quality_totals as totals
from tests.test_clipper_edit_quality import _crop_fit_fit_crop, _shot, _sidecar


# --- aggregation -------------------------------------------------------------


def test_trim_jumps_are_totalled_only_with_full_coverage():
    trimmed = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0, "fit")], duration=10.0,
        drop_spans=[(4.0, 6.0)]))
    plain = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0, "crop")], duration=10.0, drop_spans=[]))
    complete = totals.project_report("p1", [trimmed, plain])
    assert complete["trim_jumps"] == 1
    assert complete["coverage"]["trim_jumps"] == 2

    incomplete = totals.project_report("p1", [trimmed, eq.clip_report(_sidecar(None))])
    assert incomplete["trim_jumps"] == eq.UNAVAILABLE
    assert incomplete["trim_jumps_known_lower_bound"] == 1


def test_a_clip_with_no_shots_adds_nothing_to_a_tally_counted_in_shots():
    """The composition totals came out twelve above the shot count, because a
    static export contributed one `unavailable` to a tally whose unit is shots."""
    rows = [eq.clip_report(_sidecar([_shot(0.0, 2.0, "fit"), _shot(2.0, 4.0, "fit")],
                                    duration=4.0)),
            eq.clip_report(_sidecar(None))]
    report = totals.project_report("p1", rows)
    assert sum(report["composition"].values()) == report["shots"] == 2
    assert report["coverage"]["composition"] == 1
    assert report["render_versions"] == {eq.UNAVAILABLE: 2}


def test_the_macro_reports_both_rates_because_they_answer_different_questions():
    """Pooled is the corpus rate; the source mean weighs each source equally.
    One long project can carry the pooled figure on its own."""
    long_project = [eq.clip_report(_sidecar([_shot(0.0, 60.0)] * 1, duration=60.0))]
    short_project = [eq.clip_report(_sidecar([_shot(0.0, 6.0)] * 3, duration=6.0))]
    projects = [totals.project_report("long", long_project),
                totals.project_report("short", short_project)]
    macro = totals.macro_report(projects, long_project + short_project)
    assert abs(macro["shots_per_minute_pooled"] - 4 / (66.0 / 60.0)) < 1e-9
    assert abs(macro["shots_per_minute_source_mean"] - (1.0 + 30.0) / 2) < 1e-9
    assert macro["coverage_sources"] == 2
