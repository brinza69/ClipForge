"""Batch R4: the §4 bands are compared against, never enforced.

Split from `test_clipper_rhythm.py` at the repo's 500-line limit, on the seam
the module already has: that file pins WHEN a cut is allowed to exist, this one
pins what is then said about how many there were.

The rule underneath every case here is one sentence from the plan: the numbers
in §4 are guardrails somebody chose, not results anybody measured. A proposal
padded to reach a band would make the band unfalsifiable — it would agree with
every implementation, including a wrong one. So the pace is reported with its
verdict and the cut list is left exactly as the reasons made it.

Two things that look like bookkeeping and are not:

- **`action` is judged on two bands, not one.** Events set the pace and a lull
  is not an invitation to cut faster, so the clip is split into the seconds R3b
  called `action` and the rest. Without the motion measurement the split cannot
  be made, and folding the two together would quietly judge quiet material
  against the busy band.
- **`indeterminate` is an answer.** Below `60 / lo` seconds a band's own lower
  bound does not expect a single cut yet, so "no cuts" and "too few cuts" are
  the same observation.
"""

from __future__ import annotations

from services.clipper import dynamic_rhythm as rhythm, edit_profiles

from tests.test_clipper_rhythm import _pauses, _propose, _segments


def _partitions(view: dict) -> dict[str, dict]:
    return {row["partition"]: row for row in view["pace"]}


# --- one band, one partition -------------------------------------------------


def test_a_single_band_profile_is_measured_over_the_whole_clip():
    view = _propose(_segments(("speaker", 0.0, 60.0)), duration=60.0,
                    boundaries=_pauses(10.0, 20.0), scenes=[], beats=[])
    pace = view["pace"]
    assert len(pace) == 1 and pace[0]["partition"] == "clip"
    assert pace[0]["band"] == [5.0, 10.0]
    assert pace[0]["seconds"] == 60.0 and pace[0]["cuts"] == 0
    assert pace[0]["verdict"] == "below"


def test_a_clip_shorter_than_one_expected_interval_is_indeterminate():
    """An eight-second slide against a four-a-minute floor. The floor expects
    its first cut at fifteen seconds, so this clip cannot be under it — and
    saying `below` would invent a verdict out of the clip's length."""
    view = _propose(_segments(("visual_evidence", 0.0, 8.0)),
                    profile="instructional", duration=8.0,
                    boundaries=_pauses(2.0, 4.0, 6.0), scenes=[], beats=[])
    assert view["cut_count"] == 0
    assert _partitions(view)["clip"]["verdict"] == "indeterminate"
    assert _partitions(view)["clip"]["cuts_per_min"] == 0.0


def test_being_over_the_band_does_not_remove_a_cut():
    """Twenty-nine measured events in thirty seconds of measured action is 58 a
    minute against a ceiling of 28. Every one of them had a reason and a place,
    so every one of them stays and the verdict says what happened."""
    beats = [float(i) for i in range(1, 30)]
    view = _propose(_segments(("action", 0.0, 30.0)), profile="action",
                    duration=30.0, boundaries=_pauses(*beats), scenes=[],
                    beats=beats)
    assert view["cut_count"] == 29
    assert _partitions(view)["action"]["cuts_per_min"] == 58.0
    assert _partitions(view)["action"]["verdict"] == "above"


# --- two bands, and the measurement they depend on ---------------------------


def test_the_action_profile_is_judged_on_the_busy_and_the_quiet_half_apart():
    """Thirty seconds of measured action with seven events in it, then thirty
    seconds of the creator talking. One number over the whole minute would call
    that 7/min and pass; the halves are two different edits."""
    beats = [2.0, 6.0, 10.0, 14.0, 18.0, 22.0, 26.0]
    view = _propose(_segments(("action", 0.0, 30.0), ("speaker", 30.0, 60.0)),
                    profile="action", duration=60.0,
                    boundaries=_pauses(*beats), scenes=[], beats=beats)
    loud, quiet = _partitions(view)["action"], _partitions(view)["quiet"]
    assert loud["seconds"] == 30.0 and loud["cuts"] == 7
    assert loud["cuts_per_min"] == 14.0 and loud["verdict"] == "within"
    # The action ending at 30.0 IS a treatment change, so there is a cut there —
    # and it belongs to the stretch it OPENS, not to the one it ends. Attributing
    # it backwards would credit the busy band with a cut made because the busy
    # part stopped.
    assert quiet["seconds"] == 30.0 and quiet["cuts"] == 1
    assert quiet["cuts_per_min"] == 2.0
    assert quiet["band"] == [5.0, 12.0] and quiet["verdict"] == "below"


def test_a_short_quiet_half_is_indeterminate_while_the_busy_half_is_not():
    """The two partitions are judged independently, including on whether they
    are long enough to be judged at all."""
    beats = [float(i) for i in range(1, 8)]
    view = _propose(_segments(("action", 0.0, 35.0), ("speaker", 35.0, 40.0)),
                    profile="action", duration=40.0,
                    boundaries=_pauses(*beats), scenes=[], beats=beats)
    assert _partitions(view)["action"]["verdict"] in {"within", "above"}
    quiet = _partitions(view)["quiet"]
    assert quiet["seconds"] == 5.0 and quiet["verdict"] == "indeterminate"


def test_without_the_motion_measurement_both_halves_are_unavailable():
    """A flat motion series is not a quiet clip. With no spread there is nothing
    to call action, the split cannot be made, and pretending it can would judge
    the whole minute against whichever band happened to be first."""
    for axis in ({"variability": "flat"}, {"coverage": "unavailable"},
                 {"variability": "unavailable"}):
        view = _propose(_segments(("safe", 0.0, 60.0)), profile="action",
                        duration=60.0, boundaries=_pauses(10.0), scenes=[],
                        beats=[], **axis)
        assert view["action_measured"] is False
        for row in view["pace"]:
            assert row["verdict"] == "unavailable"
            assert row["unavailable_because"] == "action_not_measured"
            # Not zero. Nobody counted seconds into a partition that could not
            # be drawn.
            assert row["seconds"] is None and row["cuts"] is None


# --- the comparison the batch is for -----------------------------------------


def test_the_delivered_cadence_is_recorded_beside_the_proposal():
    """The proposal is only worth reading against what actually shipped. 1.341
    shots over 45m43s is the baseline this number exists to be compared with."""
    shots = [{"index": i, "t0": i * 2.0, "t1": (i + 1) * 2.0} for i in range(30)]
    view = _propose(_segments(("speaker", 0.0, 60.0)), duration=60.0,
                    boundaries=_pauses(10.0), scenes=[], beats=[],
                    legacy_shots=shots)
    assert view["legacy_cuts"] == 29
    assert view["legacy_cuts_per_min"] == 29.0
    assert view["cut_count"] == 0


def test_the_delivered_cadence_is_recorded_even_when_the_proposal_is_not():
    """A clip with no regime view still shipped an edit, and how fast that edit
    cut is a fact about the file. Withholding it because the proposal is
    unavailable would hide the very baseline the batch is measured against."""
    view = rhythm.rhythm_view(
        duration=60.0, profile="talking_head", mode="legacy_dynamic",
        regime_view=None, boundaries=[], scenes=[], beats=[],
        legacy_shots=[{"index": i} for i in range(30)])
    assert view["available"] is False
    assert view["legacy_cuts"] == 29 and view["legacy_cuts_per_min"] == 29.0


# --- the accessor ------------------------------------------------------------


def test_only_the_action_profile_has_a_quiet_band():
    """`band_for` is THE accessor for a reason: a caller reading
    `cuts_per_min` straight out of `PROFILES` judges a gaming clip's lulls
    against the busy band and never looks wrong enough to be noticed."""
    assert edit_profiles.band_for("action", quiet=True) == (5.0, 12.0)
    for name in edit_profiles.PROFILES:
        if name != "action":
            assert edit_profiles.band_for(name, quiet=True) is None
        assert edit_profiles.band_for(name) is not None


def test_an_unknown_profile_is_read_as_the_conservative_one():
    """The same fallback `resolve` already makes, so a profile name that only
    exists in a stale settings row cannot buy a bolder band."""
    assert (edit_profiles.band_for("nonsense")
            == edit_profiles.band_for(edit_profiles.CONSERVATIVE))
    view = _propose(_segments(("speaker", 0.0, 60.0)), profile="nonsense",
                    duration=60.0, boundaries=_pauses(10.0), scenes=[], beats=[])
    assert view["profile"] == edit_profiles.CONSERVATIVE
