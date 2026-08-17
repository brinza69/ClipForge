"""Pass D: what the clip looks like, checked before it is encoded.

Every other pass judges the MOMENT. These check the CLIP — the thing a viewer
actually receives. The three checks correspond to three failures seen on real
exports, not to three ideas.

The tests that matter most here are the negative ones. Two of the three checks
were WRONG the first time they were run on real data, in ways no unit test would
have caught, and both mistakes are pinned below so they cannot come back:
comparing proxy pixels to source pixels, and judging a co-streamer's face
against a crop that is framing the other person.
"""

from __future__ import annotations

import numpy as np
import pytest

from services.clipper import review


def _shot(t0, t1, camera, rect):
    return {"t0": t0, "t1": t1, "camera": camera, "rect": rect}


FACE_RECT = {"x": 1500, "y": 0, "w": 214, "h": 380}


# ── geometry ─────────────────────────────────────────────────────────────────

def test_a_clip_with_no_captions_has_no_band():
    """Three of the nine reference Shorts carry no captions at all, so this is a
    legitimate clip rather than a broken one."""
    assert review.caption_band({}, 1920) == (0.0, 0.0)
    assert review.caption_band({"chunks": []}, 1920) == (0.0, 0.0)


def test_the_band_sits_where_the_plan_puts_it():
    band = review.caption_band(
        {"chunks": [{"text": "x"}], "y_pct": 0.75, "scale": 1.0,
         "style": {"font_size": 72}}, 1920)
    assert band[0] < 1440 < band[1], "the band must straddle its own centre"
    assert band[1] - band[0] == pytest.approx(72 * review.CAPTION_INK_RATIO)


def test_the_band_maps_back_into_the_crop_it_came_from():
    """The renderer scales the crop to fill the output, so the inverse is one
    factor. A band halfway down the output is halfway down the crop."""
    rect = {"x": 100, "y": 200, "w": 606, "h": 1080}
    x, y, w, h = review.band_in_source((960.0, 1056.0), rect, 1920)
    assert (x, w) == (100, 606)          # spans the crop horizontally
    assert y == 200 + 540                # 960/1920 of 1080
    assert h == pytest.approx(54, abs=1)


def test_a_band_outside_a_degenerate_rect_is_empty():
    assert review.band_in_source((0.0, 0.0), {"h": 1080}, 1920) == (0, 0, 0, 0)


# ── the UI mask ──────────────────────────────────────────────────────────────

def test_flat_mid_grey_reads_as_a_panel_and_colour_does_not():
    grey = np.full((10, 10, 3), 160, dtype=np.uint8)
    assert review.ui_share(grey) == pytest.approx(1.0)

    sky = np.zeros((10, 10, 3), dtype=np.uint8)
    sky[..., 0] = 200                    # saturated blue: spread is wide
    assert review.ui_share(sky) == pytest.approx(0.0)

    dark = np.full((10, 10, 3), 20, dtype=np.uint8)
    assert review.ui_share(dark) == pytest.approx(0.0)


def test_an_empty_patch_is_not_an_error():
    assert review.ui_share(np.zeros((0, 0, 3), dtype=np.uint8)) == 0.0
    assert review.ui_share(None) == 0.0


# ── the checks ───────────────────────────────────────────────────────────────

def test_a_caption_on_a_panel_is_reported_and_one_on_the_game_is_not():
    findings = review.check_caption_occlusion([(1.0, 0.02), (2.0, 0.60)])
    assert [f.kind for f in findings] == ["caption_over_ui"]
    assert findings[0].where == 2.0
    assert findings[0].severity == "revise"


def test_one_blank_frame_revises_and_a_blank_clip_is_rejected():
    """The difference is the whole point of having two severities: a shot that
    opened on a fade is fixable, a clip that is mostly nothing is not."""
    one = review.check_dead_frames([(0.0, 2.0)] + [(float(i), 40.0) for i in range(1, 10)])
    assert [f.kind for f in one] == ["dead_frame"]

    most = review.check_dead_frames([(float(i), 2.0) for i in range(8)]
                                    + [(9.0, 40.0), (10.0, 40.0)])
    assert [f.kind for f in most] == ["dead_clip"]
    assert most[0].severity == "reject"


def test_a_gameplay_shot_is_never_judged_on_its_missing_face():
    """`camera_rects` pushes the gameplay cameras clear of the facecam on
    purpose. Reporting that as a clipped face would file the feature as a bug."""
    faces = [{"t": 5.0, "boxes": [[0, 0, 40, 40]]}]
    shots = [_shot(0.0, 10.0, "game", {"x": 1332, "y": 42, "w": 522, "h": 928})]
    assert review.check_faces_intact(shots, faces, 0.0,
                                    {"cx": 20.0, "cy": 20.0}) == []


def test_the_other_streamer_is_not_a_clipped_face():
    """The mistake this check made on its first real run. The test source is a
    co-stream with a facecam in each top corner; a shot framing the right-hand
    one legitimately excludes the left-hand one, and the first version called
    that '100% of the face is outside the crop' on every clip it saw."""
    shots = [_shot(0.0, 10.0, "face", FACE_RECT)]
    subject = {"cx": 1607.0, "cy": 190.0}          # the right-hand facecam
    other = [{"t": 5.0, "boxes": [[10, 10, 60, 60]]}]     # the left-hand one
    assert review.check_faces_intact(shots, other, 0.0, subject) == []


def test_the_framed_face_leaving_the_crop_is_reported():
    shots = [_shot(0.0, 10.0, "face", FACE_RECT)]
    subject = {"cx": 1607.0, "cy": 190.0}
    # Same cluster, but the box hangs off the right edge of the crop.
    hanging = [{"t": 5.0, "boxes": [[1680, 150, 120, 120]]}]
    findings = review.check_faces_intact(shots, hanging, 0.0, subject)
    assert [f.kind for f in findings] == ["face_clipped"]
    assert findings[0].value >= review.FACE_CLIPPED_SHARE


def test_face_boxes_are_converted_out_of_proxy_pixels():
    """The other first-run mistake. Boxes are measured on the proxy, crop rects
    are emitted in source pixels — `dynamic_cameras` opens its docstring with
    this trap and this check walked straight into it."""
    shots = [_shot(0.0, 10.0, "face", FACE_RECT)]
    proxy_box = [{"t": 5.0, "boxes": [[400, 40, 30, 40]]}]      # x4 -> 1600, 160

    # Converted, the box sits inside the crop and nothing is wrong.
    assert review.check_faces_intact(shots, proxy_box, 0.0,
                                     {"cx": 1660.0, "cy": 240.0}, 4.0, 4.0) == []

    # Left in proxy pixels it lands at x=400 in a crop that starts at x=1500,
    # so the check reports a perfectly framed face as entirely outside. That is
    # what it did on the first real clip it was pointed at.
    forgot = review.check_faces_intact(shots, proxy_box, 0.0,
                                       {"cx": 415.0, "cy": 60.0})
    assert [f.kind for f in forgot] == ["face_clipped"]
    assert forgot[0].value == pytest.approx(1.0)


# ── the verdict ──────────────────────────────────────────────────────────────

def test_the_verdict_is_the_worst_finding():
    assert review.verdict([]) == "APPROVE"
    assert review.verdict([review.Finding("x", "revise", 0, "")]) == "REVISE"
    assert review.verdict([review.Finding("x", "revise", 0, ""),
                           review.Finding("y", "reject", 0, "")]) == "REJECT"


def test_the_verdict_does_not_care_who_produced_the_finding():
    """The seam for the multimodal half: a model-driven reviewer appends to the
    same list and the verdict is taken over all of it, with nothing here
    changing."""
    from_model = review.Finding("payoff_off_screen", "reject", 3.0,
                                "the kill happened outside the crop")
    assert review.verdict([from_model]) == "REJECT"


def test_a_plan_with_no_shots_cannot_block_an_export():
    """A review that fails must never lose a render. It returns the same shape a
    clean pass returns, which cannot block anything."""
    out = review.review_plan("nonexistent.mp4", {"shots": []}, None, clip_start=0.0)
    assert out["verdict"] == "APPROVE"
    assert out["findings"] == []
    assert out["warnings"]


def test_the_verdict_reaches_the_api():
    """The failure this repo has hit five times now, the last one on the day it
    was warned about: the review shipped to the export sidecar and the log, and
    nothing in the API or the UI could read a file on disk."""
    from services.clipper.serialize import clip_to_dict

    class _Clip:
        def __getattr__(self, name):        # every other column is None
            return None
        id = "c1"
        project_id = "p1"
        start_time = 0.0
        end_time = 10.0
        duration = 10.0
        review = {"verdict": "REVISE", "findings": [{"kind": "caption_over_ui"}],
                  "sampled": 12}

    out = clip_to_dict(_Clip())
    assert out["review"]["verdict"] == "REVISE"
    assert out["review"]["findings"][0]["kind"] == "caption_over_ui"


def test_an_unreadable_proxy_is_a_warning_not_a_crash(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    out = review.review_plan(
        broken, {"shots": [_shot(0.0, 5.0, "face", FACE_RECT)], "duration": 5.0},
        None, clip_start=0.0)
    assert out["verdict"] == "APPROVE"
    assert out["warnings"]
