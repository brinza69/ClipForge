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
