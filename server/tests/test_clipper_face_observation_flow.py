"""Real window decode -> render planning -> caption observation accounting."""
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import pytest

from services.clipper import dynamic_edit, dynamic_window, face_detector
from services.clipper.caption_faces import place
from services.clipper.ffmpeg_tools import ffmpeg_bin, run
from workers import clipper_render_plan as planning


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("fps,unreadable", [(5, 1), (10, 0)])
async def test_observation_state_and_clock_survive_the_render_path(
        tmp_path, monkeypatch, legacy, fps, unreadable):
    proxy = tmp_path / "proxy.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"color=c=black:s=64x36:r={fps}:d=4", "-c:v", "libx264", str(proxy)],
        what="face observation window fixture")
    project = SimpleNamespace(id="test", width=64, height=36, clipper_settings={})
    clip = SimpleNamespace(id="test", start_time=.47, end_time=2.47,
        transcript_text="hello", headline_text="", duration=2, caption_plan=None,
        content_type="talking_head", content_confidence=None, content_type_origin=None)
    monkeypatch.setattr(planning.storage, "paths", lambda _: {"proxy": proxy})
    monkeypatch.setattr(planning.storage, "read_artifact", lambda _, kind:
        {"proxy_width": 64, "proxy_height": 36} if kind == "signals" else {})
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **kw: {
        "style": {"push_amount": 0}, "shots": [
            {"t0": t, "t1": t + 1, "composition": "fit", "anchor": [32, 18],
             "rect": {"x": 0, "y": 0, "w": 64, "h": 36}} for t in (0, 1)]})
    if legacy:
        monkeypatch.setattr(dynamic_window, "face_presence", lambda *a:
                            [{"t": .5, "boxes": [[20, 8, 15, 14]]}])
    # Decode and window extraction stay real; only the detector responses vary.
    responses = [[[20, 8, 15, 14]], cv2.error("detector failed")] + [[]] * 7
    with patch.object(face_detector, "detect_faces", side_effect=responses):
        plan = await planning._dynamic_plan(clip, project, 64, 36)
    samples = plan["_review_faces"]
    assert all(s["clock"] == "source_requested" for s in samples)
    report = place({}, plan, start=.47, src_w=64, src_h=36, current_y=.51, keep_out=[])
    assert report["observation_space"] == {
        "width": 64, "height": 36, "clock": "source_requested",
        "decoded_space": "reencoded_window"}
    if legacy:
        assert "state" not in samples[0] and samples[0]["decoded_space"] is None
        assert report["samples_in_shots"] == report["legacy_unknown_samples"] == 1
        assert report["address_unavailable_samples"] == 1
        assert report["detected_samples"] == report["empty_samples"] == 0
    else:
        assert samples[0]["t"] == .47 and samples[0]["decoded_t"] == 0
        assert samples[0]["frame_index"] == 0
        assert all(s["decoded_space"] == "reencoded_window" for s in samples)
        assert samples[1]["reason"] == "detection_error"
        assert report["samples_in_shots"] == 8
        assert report["detected_samples"] == report["detector_unavailable_samples"] == 1
        # At 5fps this non-grid cut has no frame for the final interior seek.
        # A valid request inside a window is not proof its frame was decoded.
        assert report["empty_samples"] == 6 - unreadable
        assert report["unreadable_samples"] == unreadable
        reasons = {"detection_error": 1}
        if unreadable:
            reasons["frame_not_decoded"] = unreadable
        assert report["observation_reasons"] == reasons
        assert report["address_unavailable_samples"] == unreadable
    assert not report["coverage_complete"]
