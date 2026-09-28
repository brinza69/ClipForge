"""EN3Tr R1 (codex-verdict-next-29): the end_tail binding is corroborated by the REAL encode — the window the
renderer branch executed (read off its argv) and the probed output — never by another read of the clip row.

Real ffmpeg, real `render_export`, real output probe; only the planner's decision is a fixture. Codex's case
(next29-check/probe_tail.py): the clip asks for 2 s, the executed plan for 1 s, and the block said `applies`.
"""
from __future__ import annotations

import pytest

from services.clipper import output_identity
from test_clipper_shared_export import _decision, _models, encoded_source  # noqa: F401
from workers import clipper_render_output as output

# A `moved` record whose end is the fixture clip's (0.25 → 2.25).
EV = {"rule": "end_tail_v1", "state": "moved", "target_s": 0.4, "end_in": 1.95, "vocal_end": 1.85,
      "next_speech": 2.7, "requested_end": 2.25, "end_out": 2.25}


async def _export(tmp_path, source, ident, *, ev=EV, dynamic=True, drop=(), plan_s=None, window=None):
    clip, project = _models(source, ident)
    if window is not None:
        clip.start_time, clip.end_time = window
        clip.duration = round(window[1] - window[0], 3)
    clip.reasoning = {"end_tail": ev}
    decision = _decision(dynamic=dynamic)
    decision["drop"] = list(drop)
    if dynamic:
        decision["dyn"]["duration"] = plan_s if plan_s is not None else clip.duration
    decision["render"] = {"out_w": 180, "out_h": 320, "crf": 30, "preset": "ultrafast"}
    result = await output.render_export(clip, project, decision, tmp_path / f"{ident}.mp4", src=str(source))
    return result["sidecar"]


@pytest.mark.parametrize("dynamic", [True, False])
async def test_a_file_that_ends_at_the_recorded_end_applies(tmp_path, encoded_source, dynamic):
    body = await _export(tmp_path, encoded_source[True], f"ok-{dynamic}", dynamic=dynamic)
    got = body["end_tail"]
    assert got["binding"] == "applies", got
    assert got["executed"] == {"start_s": 0.25, "duration_s": 2.0}
    assert got["measured_duration_s"] == body["output_identity"]["duration_s"]
    assert got["delivered_s"] == {"vocal_end": 1.6, "next_speech": None, "end": 2.0}


async def test_codex_case_a_plan_shorter_than_the_clip_is_not_corroborated(tmp_path, encoded_source):
    body = await _export(tmp_path, encoded_source[False], "short", plan_s=1.0)
    assert body["output_identity"]["duration_s"] == pytest.approx(1.0, abs=0.05)
    got = body["end_tail"]
    assert got["binding"] == "not_corroborated" and got["why"] == "executed_window_ends_elsewhere"
    assert got["executed"] == {"start_s": 0.25, "duration_s": 1.0} and "delivered_s" not in got


async def test_a_source_that_ends_before_the_requested_end_is_not_corroborated(tmp_path, encoded_source):
    # The 3 s source ends 0.9 s before the recorded 3.9: the argv asks for 2.4 s and gets about 1.5.
    ev = {**EV, "end_in": 3.6, "vocal_end": 3.5, "next_speech": None, "requested_end": 3.9, "end_out": 3.9}
    body = await _export(tmp_path, encoded_source[True], "past", ev=ev, window=(1.5, 3.9))
    got = body["end_tail"]
    assert got["executed"] == {"start_s": 1.5, "duration_s": 2.4}
    assert got["measured_duration_s"] < 2.0
    assert got["binding"] == "not_corroborated" and got["why"] == "output_duration_differs"


async def test_a_trimmed_file_applies_on_its_own_clock_and_a_dropped_time_is_none(tmp_path, encoded_source):
    ev = {**EV, "vocal_end": 1.25}  # 1.0 s into the clip: inside the dropped (0.75, 1.25)
    body = await _export(tmp_path, encoded_source[True], "trim", ev=ev, drop=[(0.75, 1.25)])
    got = body["end_tail"]
    assert got["binding"] == "applies", got
    assert got["executed"]["duration_s"] == 1.5
    assert got["delivered_s"] == {"vocal_end": None, "next_speech": None, "end": 1.5}


@pytest.mark.parametrize("argv,window", [
    (["ff", "-y", "-ss", "1.500", "-i", "src", "-t", "2.000", "out"], {"ss": 1.5, "t": 2.0}),
    # A treated encode: the patch's own options sit between the two inputs; `-t` is the one after the last.
    (["ff", "-ss", "1.500", "-i", "src", "-t", "9", "-f", "rawvideo", "-i", "patch", "-t", "2.000", "out"],
     {"ss": 1.5, "t": 2.0}),
    (["ff", "-ss", "x", "-i", "src", "out"], {"ss": None, "t": None}),
    (["ff", "out"], {"ss": None, "t": None}),
])
def test_the_render_record_reads_the_window_off_the_argv(argv, window):
    from services.clipper import render_record

    assert render_record.record(argv)["window"] == window


async def test_an_unavailable_output_probe_is_not_corroborated(tmp_path, monkeypatch, encoded_source):
    monkeypatch.setattr(output_identity, "_video_info", lambda _: (_ for _ in ()).throw(RuntimeError("x")))
    body = await _export(tmp_path, encoded_source[True], "noprobe")
    assert body["output_identity"]["refused"] == output_identity.NO_VIDEO
    got = body["end_tail"]
    assert got["binding"] == "not_corroborated" and got["why"] == "output_probe_unavailable"
    assert got["executed"] == {"start_s": 0.25, "duration_s": 2.0} and got["recorded"] == EV
