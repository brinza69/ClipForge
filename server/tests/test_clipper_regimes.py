"""Batch R3b: what a stretch of a clip IS, decided sample by sample.

The first version of this module gave one verdict per existing shot by weighting
the signals across it. That is the majority vote over a mixed shot the plan
forbids, and it produced a `reaction` from a creator in one half of a shot and a
different face in the other, who were never on screen together. Most of what is
pinned here is the difference between that and asking at each moment.

Three rules paid for elsewhere and easy to lose here:

- **Co-presence is a sample-level fact.** Two 50% weights are both satisfied by
  two halves that never overlap.
- **`crop` is not a visual key.** A `speaker` stretch and an `action` stretch are
  both crops pointing at different things, and merging them hid a real change.
- **Unknown is not zero.** Without an anchor there is no "away from the anchor",
  so `reaction` and `conversation` are unavailable rather than false.

Recorded, applied to nothing. The delivered plan is what R2 froze.
"""

from __future__ import annotations

import pytest

from services.clipper import dynamic_regimes as regimes
from services.clipper.edit_profiles import REGIMES

HOP = 0.25
STYLE = {"action_pct": 0.60, "speech_ratio_on": 0.30}


def _flags(pattern: str) -> list[bool]:
    return [c == "1" for c in pattern]


def _levels(pattern: str) -> list[float]:
    """`1` is loud, `0` is quiet, on the normalised scale the view uses."""
    return [1.0 if c == "1" else 0.0 for c in pattern]


def _view(creator: str, others: str | None, action: str, speech: str, **kw) -> dict:
    """The whole proposal, over a clip exactly as long as the pattern."""
    kw.setdefault("duration", len(creator) * HOP)
    return regimes.regime_view(
        creator=_flags(creator),
        others=None if others is None else _flags(others),
        # Speech is a SHARE of each interval now, so the threshold applies.
        action=_levels(action), speech=[1.0 if c == "1" else 0.0 for c in speech],
        hop=HOP, style=STYLE, **kw)


def test_the_regime_list_is_the_one_in_edit_profiles():
    """Two closed lists stop being the same list the first time one changes."""
    assert regimes.REGIMES is REGIMES
    assert set(regimes.VISUAL_KEYS) == set(REGIMES)
    assert set(regimes.VISUAL_KEYS.values()) == {"crop_anchor", "crop_action", "fit_full"}
    # NEVER `crop_creator`: that is an identity nobody here established.
    assert "crop_creator" not in set(regimes.VISUAL_KEYS.values())


# --- the vote that is no longer taken ----------------------------------------


def test_a_creator_then_someone_else_is_not_a_reaction():
    """THE bug this rewrite exists for. Weighted across one shot, both signals
    clear 50% and the answer was `reaction` — from two people who were never on
    screen at the same time."""
    view = _view(creator="1111" + "0000",
                 others="0000" + "1111",
                 action="00000000", speech="11111111")
    assert "reaction" not in view["counts"]
    assert [s["regime"] for s in view["segments"]] == ["speaker", "conversation"]
    assert view["segments"][0]["t1"] == view["segments"][1]["t0"] == 1.0


def test_a_creator_and_someone_else_together_is_a_reaction():
    view = _view(creator="1111", others="1111", action="0000", speech="1111")
    assert [s["regime"] for s in view["segments"]] == ["reaction"]
    assert view["segments"][0]["reason"] == "target_and_external_face_together"


def test_someone_else_alone_gets_a_common_frame():
    view = _view(creator="0000", others="1111", action="0000", speech="0000")
    assert [s["regime"] for s in view["segments"]] == ["conversation"]
    assert regimes.VISUAL_KEYS["conversation"] == "fit_full"


def test_the_boundary_falls_where_the_signal_changes_not_where_a_shot_did():
    """Six samples of speaking then six of nothing produce two segments split at
    1.5s, whatever the legacy edit happened to do around it."""
    view = _view(creator="111111" + "000000", others="0" * 12,
                 action="0" * 12, speech="111111" + "000000")
    assert [s["t0"] for s in view["segments"]] == [0.0, 1.5]
    assert [s["regime"] for s in view["segments"]] == ["speaker", "visual_evidence"]


# --- the visual key ----------------------------------------------------------


def test_two_crops_on_different_subjects_are_not_one_stretch():
    """`speaker` and `action` are both crops, and they point at different
    things. Merging on "both are crops" hid a change the viewer sees."""
    segments = [{"t0": 0.0, "t1": 1.0, "regime": "speaker"},
                {"t0": 1.0, "t1": 2.0, "regime": "action"}]
    merged = regimes.merge_by_visual_key(segments)
    assert [m["visual_key"] for m in merged] == ["crop_anchor", "crop_action"]


def test_a_speaker_and_a_reaction_are_one_stretch():
    """Both frame the creator. What separates them is why, not what is shown."""
    segments = [{"t0": 0.0, "t1": 1.0, "regime": "speaker"},
                {"t0": 1.0, "t1": 2.0, "regime": "reaction"}]
    merged = regimes.merge_by_visual_key(segments)
    assert len(merged) == 1
    assert [m["regime"] for m in merged[0]["members"]] == ["speaker", "reaction"]


def test_two_regimes_that_deliver_the_same_frame_do_not_earn_a_cut():
    """The rule R1 paid for, one level up."""
    segments = [{"t0": 0.0, "t1": 1.0, "regime": "conversation"},
                {"t0": 1.0, "t1": 2.0, "regime": "visual_evidence"},
                {"t0": 2.0, "t1": 3.0, "regime": "speaker"}]
    merged = regimes.merge_by_visual_key(segments)
    assert [m["visual_key"] for m in merged] == ["fit_full", "crop_anchor"]
    assert merged[0]["t0"] == 0.0 and merged[0]["t1"] == 2.0


def test_a_gap_between_segments_is_never_merged_across():
    segments = [{"t0": 0.0, "t1": 1.0, "regime": "safe"},
                {"t0": 2.5, "t1": 3.0, "regime": "visual_evidence"}]
    assert len(regimes.merge_by_visual_key(segments)) == 2


def test_the_merge_keeps_every_reason_and_every_piece_of_evidence():
    """A report that claimed its answer could be reconstructed while dropping
    the evidence would be lying about being auditable."""
    view = _view(creator="0000" + "0000", others="1111" + "0000",
                 action="0000" + "0000", speech="0000" + "0000")
    merged = view["treatment_segments"]
    assert len(merged) == 1, "conversation and visual_evidence both show the frame"
    members = merged[0]["members"]
    assert [m["regime"] for m in members] == ["conversation", "visual_evidence"]
    assert all(m["reason"] and m["evidence"] and m["samples"] for m in members)


# --- the evidence ------------------------------------------------------------


def test_the_evidence_is_counted_over_the_run_not_read_off_one_sample():
    """A stretch that is 80% speech and one that is 5% are different stretches,
    and a single representative moment cannot tell them apart."""
    # One regime throughout — `action` does not change the answer while the
    # creator is present and talking — so the run is four samples with a mixed
    # signal inside it.
    view = _view(creator="1111", others="0000", action="1010", speech="1111")
    segment = view["segments"][0]
    assert [s["regime"] for s in view["segments"]] == ["speaker"]
    assert segment["samples"] == 4
    assert segment["evidence"]["action"] == 0.5
    assert segment["evidence"]["target"] == 1.0
    assert segment["evidence"]["speech"] == 1.0


def test_the_legacy_shots_are_recorded_but_decide_nothing():
    shots = [{"index": 0, "t0": 0.0, "t1": 0.5}, {"index": 1, "t0": 0.5, "t1": 1.0}]
    view = _view(creator="1111", others="0000", action="0000", speech="1111",
                 shots=shots)
    assert view["segments"][0]["overlaps_shots"] == [0, 1]
    # One segment over two shots: the shots did not split it.
    assert len(view["segments"]) == 1


# --- what cannot be known ----------------------------------------------------


def test_without_an_anchor_the_two_face_regimes_are_unavailable_not_false():
    view = _view(creator="1111", others=None, action="0000", speech="1111")
    assert view["off_anchor_known"] is False
    assert not {"reaction", "conversation"} & set(view["counts"])
    assert view["segments"][0]["evidence"]["others"] is None


def test_a_flat_motion_series_is_half_not_silent():
    """With no spread there is no evidence either way, and calling it "no
    action" would be a measurement nobody made."""
    values, spread = regimes.normalise_series([3.0, 3.0, 3.0])
    assert values == [0.5, 0.5, 0.5]
    # And the caller is TOLD, because 0.5 sits under a default cut-off of 0.6
    # and would otherwise read as "not action".
    assert spread is False
    assert regimes.normalise_series([]) == ([], False)


def test_an_infinite_motion_sample_is_not_the_loudest_moment():
    """`_num` is the canonical one, which rejects the infinities. A local copy
    that did not was how R2's validation hole reopened."""
    out, _spread = regimes.normalise_series([0.0, float("inf"), 1.0, float("nan")])
    assert all(0.0 <= v <= 1.0 for v in out)
    # The infinity is sanitised to 0 before scaling, so it is not the loudest
    # moment — it is not a moment at all.
    assert out[1] == out[0]

    louder, spread = regimes.normalise_series([0.0, 1.0, 2.0, 3.0])
    assert spread is True and louder[-1] > louder[0]


# --- the report --------------------------------------------------------------


def test_the_report_states_the_rule_it_used():
    view = _view(creator="1111", others="0000", action="0000", speech="1111")
    assert view["schema"] == "regime_view_v2"
    assert view["scope"] == "regime_segments_at_timeline_resolution"
    assert view["sample_hop_s"] == HOP
    assert view["samples"] == 4
    assert view["action_pct"] == 0.60 and view["speech_ratio_on"] == 0.30


def test_the_coarser_count_is_not_called_the_finer_one():
    """Whether a boundary is VISIBLE is decided by `dynamic_geometry.visual_key`
    over the delivered geometry. This is the coarser question of whether the
    treatment changes, and naming it the finer one would claim a proof nobody
    has."""
    view = _view(creator="1111" + "0000", others="0000" + "0000",
                 action="0000" + "0000", speech="1111" + "0000")
    assert "visible_boundaries" not in view
    assert view["treatment_boundaries"] <= view["regime_boundaries"]


def test_the_segments_cover_the_timeline_without_gaps():
    view = _view(creator="1100" + "0011", others="0" * 8,
                 action="0000" + "1111", speech="1100" + "0000")
    assert view["segments"][0]["t0"] == 0.0
    assert view["segments"][-1]["t1"] == round(8 * HOP, 3)
    for a, b in zip(view["segments"], view["segments"][1:]):
        assert a["t1"] == b["t0"]
    assert sum(s["samples"] for s in view["segments"]) == view["samples"]
