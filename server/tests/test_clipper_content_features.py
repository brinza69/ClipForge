"""The classifier's visual features have to VARY, or they decide nothing.

`corner_stability` read 0.000 on four of five real sources and `centre_motion`
1.000 on all five, so `hud_signal = corner * centre` was 0 everywhere and the
1.8-weight gaming vote it fed had never once fired. The cause was an absolute
threshold (0.08 mean abs diff) applied to frames that are minutes apart,
because the classifier is handed frames sampled across the whole source rather
than adjacent ones.

These tests assert the property that was missing, not the numbers: a feature
that cannot separate a static border from a moving one is not a feature.
"""

from __future__ import annotations

import numpy as np
import pytest

from services.clipper.content_type import _patch_motion

W, H = 480, 270


def _frames(n: int, *, corners_move: bool, seed: int = 0) -> list[np.ndarray]:
    """n frames whose middle always changes completely; corners optional.

    A "static" border still drifts a little — a real overlay carries a clock, a
    subscriber count, compression noise. A perfectly frozen border is a
    different case and has its own test below.
    """
    rng = np.random.default_rng(seed)
    border = rng.integers(0, 255, (H, W), dtype=np.uint8)
    out = []
    for _ in range(n):
        f = border.copy()
        if not corners_move:
            f = np.clip(f.astype(np.int16)
                        + rng.integers(-6, 7, (H, W)), 0, 255).astype(np.uint8)
        else:
            f = rng.integers(0, 255, (H, W), dtype=np.uint8)
        f[H // 4:H - H // 4, W // 4:W - W // 4] = rng.integers(
            0, 255, (H - 2 * (H // 4), W - 2 * (W // 4)), dtype=np.uint8)
        out.append(f)
    return out


def test_a_static_border_scores_higher_than_a_moving_one():
    still, _ = _patch_motion(_frames(8, corners_move=False), W, H)
    moving, _ = _patch_motion(_frames(8, corners_move=True), W, H)
    assert still > moving + 0.2, (
        f"corner_stability cannot tell a fixed border from a moving one: "
        f"{still:.3f} vs {moving:.3f}")


def test_neither_feature_saturates_on_completely_different_frames():
    """The real failure: frames sampled 36s apart differ everywhere, and an
    absolute threshold reads 0 and 1 for every source alike."""
    corner, centre = _patch_motion(_frames(8, corners_move=False), W, H)
    assert 0.0 < corner < 1.0, f"corner_stability pinned at {corner}"
    assert 0.0 < centre < 1.0, f"centre_motion pinned at {centre}"


def test_a_frozen_source_reports_stable_corners():
    frozen = [np.full((H, W), 128, dtype=np.uint8) for _ in range(6)]
    corner, centre = _patch_motion(frozen, W, H)
    assert corner == pytest.approx(1.0)
    assert centre == pytest.approx(0.0)


def test_too_few_frames_is_not_an_error():
    assert _patch_motion([], W, H) == (1.0, 0.0)
    assert _patch_motion([np.zeros((H, W), dtype=np.uint8)], W, H) == (1.0, 0.0)


# ── how many people are on screen ────────────────────────────────────────────
#
# `face_count` was the MODAL faces per frame. Measured on the labelled sources
# it returns 1 or 0 everywhere — including 0 on the two Minecraft sources,
# which demonstrably have two faces on screen — so `two_up` never fired and
# `podcast` and `interview` could never win their own vote.


def test_two_people_in_the_scene_let_an_interview_win():
    from services.clipper.content_geom import classify_features

    base = {"frame_count": 20, "speech_ratio": 0.9, "face_stability": 0.8,
            "face_area": 0.2, "motion_mean": 0.1, "kw_interview": 0.2}
    one = classify_features({**base, "face_count": 1})["content_type"]
    two = classify_features({**base, "face_count": 2})["content_type"]
    assert one != two, "the count has to change the answer or it decides nothing"
    assert two in {"interview", "podcast"}


def test_a_modal_count_could_never_have_said_two():
    """The defect itself, stated as a property. `summarize_faces` takes the
    commonest per-frame count, and on a source where the cascade misses often
    the commonest answer is 0 or 1 however many people are there."""
    from services.clipper.content_geom import summarize_faces

    # Two faces in a third of frames, none in the rest — two people, plainly.
    samples = []
    for i in range(30):
        samples.append({"t": float(i),
                        "boxes": [[0, 0, 10, 10], [50, 0, 10, 10]] if i % 3 == 0 else []})
    out = summarize_faces({"faces": samples}, 100, 100)
    assert out["face_count"] == 0.0, (
        "this is the behaviour that made podcast and interview unreachable")


def test_unreadable_frames_leave_the_old_statistic_alone():
    """None rather than zero: the caller must keep the modal count, not be told
    there is nobody on screen."""
    from services.clipper.content_type import _scene_faces

    assert _scene_faces([]) is None
    assert _scene_faces(["nope.jpg"]) is None


# ── how much of a source is somebody talking ─────────────────────────────────


def test_speech_ratio_prefers_the_transcript_over_the_envelope():
    """The envelope is anything above the silence floor, and on a stream with
    game audio under a voice that is nearly everything: measured across all
    eleven labelled sources it ran 0.863-1.000, a range of 0.137. It could not
    tell a Minecraft stream from an interview, which is the only thing the
    classifier wanted it for.

    From the transcript the same eleven run 0.276-0.918 — 4.7x the spread — and
    they separate on the axis that matters: edited talking content sits at
    0.86-0.92 and live streams at 0.28-0.64.
    """
    from services.clipper.content_geom import speech_ratio

    # Envelope says almost everything is speech; the transcript says a third.
    signals = {"duration": 100.0, "speech": [[0.0, 94.0]], "speech_coverage": 0.34}
    assert speech_ratio(signals) == pytest.approx(0.34)


def test_it_falls_back_to_the_envelope_when_there_is_no_transcript():
    """A source analysed before this existed, or one whose transcription
    failed, keeps the old answer rather than reading zero."""
    from services.clipper.content_geom import speech_ratio

    assert speech_ratio({"duration": 100.0, "speech": [[0.0, 90.0]]}) == pytest.approx(0.90)


def test_the_classifier_reads_more_than_frame_features_supplies():
    """A wrong turn worth pinning. `frame_features` returns 8 keys and the
    classifier reads several it does not produce — motion_mean, face_stability,
    face_count_mean — which looks exactly like five dead inputs if you call
    that function on its own.

    It is not: `detect_content_type` fills them from `summarize_motion` and
    `summarize_faces` over the signals. Measured through the real path they
    vary properly across the labelled sources.
    """
    import inspect

    from services.clipper import content_type as ct

    src = inspect.getsource(ct.detect_content_type)
    assert "summarize_motion" in src
    assert "summarize_faces" in src


# ── the hit-rate gate ────────────────────────────────────────────────────────
#
# The same inset, the same stream, two sample lengths: 0.33 over 12 minutes and
# 0.12 over 4 hours, because the rate counts how often a Haar cascade caught the
# co-streamer facing his camera and a long sample averages in every minute he
# looked away. The 0.15 bar therefore dropped a real facecam for being sampled
# for longer, which is the absolute-threshold mistake in a new costume.
#
# This pins the property rather than the constant: a locked-off inset seen in a
# TENTH of frames must survive. The number itself is scored by
# scripts/score_facecam.py against docs/source-labels.md — 8/9, flat from 0.12
# down to 0.05.

def _inset_source(n: int, hits: int) -> tuple[list, list]:
    """`n` frames of noise with a flat 120x68 inset in the top-left corner, and
    a face detection inside it in `hits` of them."""
    from services.clipper.content_geom import make_rect

    rng = np.random.default_rng(7)
    grays, faces = [], []
    for i in range(n):
        f = rng.integers(0, 255, (H, W), dtype=np.uint8)
        f[0:68, 0:120] = 40                    # the inset: flat, hard-edged
        grays.append(f)
        faces.append([make_rect(49, 23, 22, 22)] if i % (n // hits) == 0 else [])
    return grays, faces


def test_an_inset_seen_in_a_tenth_of_frames_is_still_a_facecam():
    from services.clipper import content_type as ct

    grays, faces = _inset_source(100, 12)
    cams, _ = ct._find_webcams(grays, faces, W, H)
    assert len(cams) == 1, (
        f"a cluster at rate 0.12 was dropped: {cams}. That is the 4-hour "
        f"Minecraft source's second facecam, and it is real.")
    assert (cams[0]["x"], cams[0]["y"]) == (0, 0)


def test_the_rate_gate_still_rejects_a_handful_of_stray_detections():
    """Lowering the bar cannot mean removing it — `_FACECAM_MIN_HITS` is what
    stops three lucky frames from becoming a layout."""
    from services.clipper import content_type as ct

    grays, faces = _inset_source(100, 50)
    faces = [boxes if i < 2 else [] for i, boxes in enumerate(faces)]
    assert ct._find_webcams(grays, faces, W, H)[0] == []


# ── a reason shown to a person has to be a reason ────────────────────────────
#
# `vote()` records a term's explanation whenever its weight clears 0.01, which
# is right for the SCORE and far too low for the EXPLANATION. Measured on the
# labelled corpus, `IRL World Cup` came back as gaming with "gaming vocabulary
# in the transcript" at the top of its evidence — kw_gaming 0.017 against a
# kw_sports of 0.157, contributing 0.034 to a winning score near 0.5.
#
# That is not cosmetic: it cost real time on 2026-08-18, because the evidence
# sent the reader to the word lists, which were behaving correctly. The actual
# cause was the `synthetic` term, and it is a much more interesting finding.


def test_a_term_that_barely_contributed_is_not_offered_as_the_reason():
    from services.clipper.content_geom import classify_features

    # Edge-dense and saturated: `synthetic` carries this decision. The gaming
    # vocabulary is present but negligible, exactly as on the real source.
    verdict = classify_features({
        "edge_density": 0.14, "saturation": 0.55, "line_ratio": 0.5,
        "motion_mean": 0.30, "corner_stability": 0.2, "face_stability": 0.1,
        "face_count": 0.0, "face_count_mean": 0.0, "face_area": 0.0,
        "speech_ratio": 0.5, "kw_gaming": 0.017, "kw_sports": 0.0,
    })
    joined = " ".join(verdict["evidence"])
    assert "vocabulary" not in joined, (
        f"a 0.017 keyword score is being reported as the reason: {joined}")


def test_the_term_that_did_decide_is_still_named():
    from services.clipper.content_geom import classify_features

    verdict = classify_features({
        "edge_density": 0.02, "saturation": 0.10, "line_ratio": 0.05,
        "motion_mean": 0.05, "corner_stability": 0.9, "face_stability": 0.9,
        "face_count": 1.0, "face_count_mean": 1.0, "face_area": 0.10,
        "speech_ratio": 0.8, "kw_gaming": 0.9,
    })
    assert verdict["evidence"], "a decision with a dominant term explained nothing"
    assert verdict["evidence"] != ["no single content type stood out"]
