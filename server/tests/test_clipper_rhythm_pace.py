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
- **`indeterminate` is an answer, but only for the lower bound.** Below
  `60 / lo` seconds a band's own floor does not expect a first cut yet, so "no
  cuts" and "too few cuts" are the same observation. `above` is different: three
  cuts in four seconds is 45 a minute and the clip's length does not make that
  ambiguous, so the ceiling is checked first.
- **A partial motion track is not a partition.** The unmeasured seconds fall
  silently into `quiet` and a minute nobody measured comes back as a confident
  `below`.
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
    rows = _partitions(view)
    loud, quiet = rows["action"], rows["quiet"]
    assert loud["seconds"] == 30.0 and loud["cuts"] == 7
    assert loud["cuts_per_min"] == 14.0 and loud["verdict"] == "within"
    # The action ending at 30.0 IS a treatment change, so there is a cut there —
    # and it belongs to NEITHER band. It happened because the action stopped, and
    # crediting it to the quiet edit makes quiet material look busier for a
    # reason that has nothing to do with quiet material.
    assert quiet["seconds"] == 30.0 and quiet["cuts"] == 0
    assert quiet["band"] == [5.0, 12.0] and quiet["verdict"] == "below"
    assert rows["transition"]["cuts"] == 1
    assert rows["transition"]["verdict"] == "excluded"
    assert rows["transition"]["band"] is None
    # The three still account for every cut.
    assert (loud["cuts"] + quiet["cuts"] + rows["transition"]["cuts"]
            == view["cut_count"])


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
        assert view["action_partition_known"] is False
        assert len(view["pace"]) == 2, "no transition row without a partition"
        for row in view["pace"]:
            assert row["verdict"] == "unavailable"
            assert row["unavailable_because"] == "action_not_measured"
            # Not zero. Nobody counted seconds into a partition that could not
            # be drawn.
            assert row["seconds"] is None and row["cuts"] is None


def test_a_partial_motion_track_is_not_a_partition_either():
    """The subtler half of the same rule, and the one that looked fine. With a
    partial series every second that is not `action` — INCLUDING the seconds
    nobody measured — falls into `quiet`, so a minute nobody measured comes back
    as a confident `below`. We do not know that."""
    view = _propose(_segments(("safe", 0.0, 60.0)), profile="action",
                    duration=60.0, boundaries=_pauses(10.0), scenes=[], beats=[],
                    coverage="partial")
    assert view["action_partition_known"] is False
    assert view["action_partition_unavailable_because"] == "action_coverage_partial"
    for row in view["pace"]:
        assert row["verdict"] == "unavailable"
        assert row["unavailable_because"] == "action_coverage_partial"


def test_a_beat_inside_a_measured_action_stretch_survives_a_partial_track():
    """Refusing the whole-clip PARTITION is not the same as refusing every
    signal in it. A stretch R3b classified `action` was measured — that is what
    the classification requires — so a beat inside it still earns its cut."""
    view = _propose(_segments(("action", 0.0, 30.0), ("safe", 30.0, 60.0)),
                    profile="action", duration=60.0,
                    boundaries=_pauses(10.0), scenes=[], beats=[10.0],
                    coverage="partial")
    # The beat at 10.0, plus the treatment change where the action ends: a crop
    # on the action and a full frame are not the same picture.
    assert [c["t"] for c in view["cuts"]] == [10.0, 30.0]
    assert view["cut_reasons"] == {rhythm.REASON_BEAT: 1,
                                   rhythm.REASON_TREATMENT: 1}
    assert view["action_partition_known"] is False


def test_a_ceiling_is_provable_in_a_window_too_short_to_prove_a_floor():
    """Three cuts in four seconds of action is 45 a minute against a ceiling of
    28. `indeterminate` there hid a verdict the cuts had already demonstrated —
    only the LOWER bound needs room."""
    beats = [1.0, 2.0, 3.0]
    view = _propose(_segments(("action", 0.0, 4.0)), profile="action",
                    duration=4.0, boundaries=_pauses(*beats), scenes=[],
                    beats=beats)
    row = _partitions(view)["action"]
    assert row["seconds"] == 4.0 and row["cuts"] == 3
    assert row["cuts_per_min"] == 45.0
    assert row["verdict"] == "above"


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
