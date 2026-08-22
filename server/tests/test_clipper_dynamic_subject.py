"""Is there a subject to point a camera at — the question the blind review
turned into a defect.

The thresholds under test are measured, so the tests are written against the
measurements rather than against round numbers: detection dropouts inside good
content topped out at 1.75s across three Jensen windows, and the real face-less
sequences were 7.50s, 8.50s and 12.25s. Anything that would misclassify either
population is a regression, whatever the constants happen to be.
"""

from __future__ import annotations

from services.clipper import dynamic_subject as ds

HOP = 0.25


def _track(pattern: str) -> list[dict]:
    """`F` = a face this sample, `.` = none, one sample per 0.25s."""
    return [{"t": round(i * HOP, 3), "boxes": [[0, 0, 10, 10]] if c == "F" else []}
            for i, c in enumerate(pattern)]


def test_a_blink_does_not_change_the_composition():
    """The longest dropout measured inside content the reviewer approved was
    1.75s — seven samples. Reacting to it would change framing mid-sentence."""
    line = ds.presence_timeline(_track("F" * 20 + "." * 7 + "F" * 20), hop=HOP)
    assert all(line), "a 1.75s dropout flipped the composition"


def test_a_real_face_less_sequence_does_change_it():
    """The shortest real one measured was 7.50s — thirty samples."""
    line = ds.presence_timeline(_track("F" * 20 + "." * 30 + "F" * 20), hop=HOP)
    assert not line[40], "a 7.5s sequence with nobody in it kept a face crop"


def test_leaving_is_faster_than_entering():
    """Asymmetric on purpose: one face reappearing must not split a B-roll
    sequence in two, but a genuine return to the speaker must not lag."""
    line = ds.presence_timeline(_track("F" * 8 + "." * 40 + "F" * 20), hop=HOP)
    back = line.index(True, 20)
    left = line.index(False)
    assert (back - 48) < (left - 8), "returning is not faster than leaving"


def test_a_single_face_does_not_split_a_b_roll_sequence():
    line = ds.presence_timeline(
        _track("F" * 8 + "." * 20 + "F" + "." * 20 + "F" * 8), hop=HOP)
    assert not line[29], "one stray detection ended the sequence"


def test_the_timeline_opens_on_a_subject():
    """`plan_dynamic_edit` forces the first shot onto a face by design — the
    hook of every reference edit is a person. Opening false would fight it."""
    assert ds.presence_timeline(_track("." * 4 + "F" * 20), hop=HOP)[0] is True


def test_an_empty_track_is_not_a_face_less_clip():
    """No track is missing information, not evidence of an empty frame. Failing
    open keeps the behaviour that already works."""
    assert ds.presence_timeline([]) == []
    assert ds.span_has_subject([], 0.0, 5.0, HOP) is True


# ── the span, which is the decision unit ─────────────────────────────────────


def test_the_span_decides_not_the_sample():
    """Deciding per sample would change composition inside a shot, and a shot
    whose framing changes underneath it is not a shot."""
    line = ds.presence_timeline(_track("F" * 20 + "." * 40 + "F" * 20), hop=HOP)
    assert ds.composition_for(line, 0.0, 5.0, HOP) == "crop"
    assert ds.composition_for(line, 6.0, 14.0, HOP) == "fit"


def test_a_span_that_straddles_a_boundary_keeps_the_face():
    """At exactly half either answer is defensible, so it goes to the subject:
    keeping a face is the behaviour that already works."""
    line = [True] * 10 + [False] * 10
    assert ds.composition_for(line, 0.0, 20 * HOP, HOP) == "crop"


def test_the_hop_is_inferred_when_the_caller_does_not_pass_one():
    """Reading the 2s whole-VOD track as though it were the 0.25s production one
    produced a gap analysis that meant nothing. Inferring the step from the
    samples makes that mistake impossible to repeat silently."""
    coarse = [{"t": i * 2.0, "boxes": []} for i in range(10)]
    assert ds._hop_of(coarse) == 2.0
    # Two samples apart at 2s is 4 seconds — past ENTER_S, so it must flip.
    assert not ds.presence_timeline(
        [{"t": 0.0, "boxes": [[0, 0, 5, 5]]}] + coarse)[-1]


def test_a_span_where_every_sample_has_a_face_keeps_the_crop():
    """Hysteresis suppresses flicker; it must not overrule what is plainly on
    screen. Found by looking at a re-render, not at a test: on the Jensen
    diagram clip the span 14.08-15.18s has a face in 4 of 4 samples and came out
    `fit`, because faces returned at 14.0s and the exit lag withholds the flip
    for a second while shots there run about 1.1s.
    """
    track = _track("." * 40 + "F" * 8)
    line = ds.presence_timeline(track, hop=HOP)
    raw = ds.raw_presence(track)
    t0, t1 = 40 * HOP, 44 * HOP          # entirely inside the returned faces

    assert not line[41], "the smoothed state has not flipped back yet"
    assert ds.composition_for(line, t0, t1, HOP) == "fit"
    assert ds.composition_for(line, t0, t1, HOP, raw) == "crop"


def test_one_face_in_a_span_is_still_not_unanimous():
    """The override is for spans with no ambiguity at all. A single detection
    inside a B-roll sequence must not drag the whole shot back to a crop."""
    track = _track("." * 20 + "F" + "." * 19)
    line = ds.presence_timeline(track, hop=HOP)
    assert ds.composition_for(line, 4.0, 8.0, HOP, ds.raw_presence(track)) == "fit"
