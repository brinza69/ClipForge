"""Read caption timing from encoded pixels, after real pause removal.

An ASS on the delivered clock is insufficient: burning it before select/setpts
used that clock against source frames. Both renderers made that same mistake.
"""
from types import SimpleNamespace
from copy import deepcopy

import cv2
import pytest

from services.clipper import captions, dynamic_render, render
from services.clipper.ffmpeg_tools import ffmpeg_bin, run, video_info
from workers.clipper_captions import _write_ass


@pytest.mark.parametrize("dynamic", [False, True])
@pytest.mark.parametrize("trim", [False, True])
def test_burned_captions_follow_the_delivered_clock(tmp_path, dynamic, trim):
    source = tmp_path / "source.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=320x180:r=30:d=7", "-c:v", "libx264",
         "-preset", "ultrafast", str(source)], what="caption clock fixture")
    candidate = {"start": 1.0, "end": 6.0}
    words = [{"word": label, "start": start, "end": end}
             for label, start, end in [("FIRST", 1.2, 1.6), ("SECOND", 3.5, 3.9),
                                        ("LAST", 5.2, 5.6)]]
    plan = captions.build_caption_plan(
        candidate, {"segments": [{"start": 1.2, "end": 5.6, "words": words}]},
        preset_id="clean_minimal", max_words=1, position="center", layout={})
    drops = [(1.0, 2.0)] if trim else []
    ass = _write_ass(SimpleNamespace(id="clock", caption_plan=plan), tmp_path, drops)
    assert ass is not None
    out = tmp_path / "delivered.mp4"
    kwargs = dict(fps=30, crf=18, preset="ultrafast", out_w=270, out_h=480,
                  drop_spans=drops, has_audio=False)
    if dynamic:
        dyn = {"duration": 5.0, "shots": [
            {"t0": 0.0, "t1": 5.0, "composition": "fit",
             "rect": {"x": 0, "y": 0, "w": 320, "h": 180}}]}
        dynamic_render.render_dynamic_clip(
            str(source), dyn, str(out), start=1.0, work_dir=tmp_path,
            ass_path=ass, src_w=320, src_h=180, **kwargs)
    else:
        cmd = render.build_render_cmd(str(source), candidate, {}, ass, str(out), **kwargs)
        run(cmd, what="static caption clock export")
    assert video_info(str(out))["duration"] == pytest.approx(4.0 if trim else 5.0, abs=.05)
    cap = cv2.VideoCapture(str(out))
    try:
        samples = [(0.4, True), (0.8, False), (2.7, True), (3.3, False), (4.4, True)]
        for original_t, expected in samples:
            t = original_t - (1.0 if trim and original_t >= 2 else 0.0)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            assert ok
            white = int((frame.min(axis=2) > 180).sum())
            assert (white > 30) == expected, (t, expected, white)
    finally:
        cap.release()


def test_word_highlights_move_with_the_line_without_changing_the_stored_plan(tmp_path):
    import pysubs2

    plan = {"preset_id": "clean_minimal", "style": {"highlight_color": "#FFD700"}, "chunks": [
        {"text": "LEFT RIGHT", "start": 2.2, "end": 3.0, "words": [
            {"word": "LEFT", "start": 2.2, "end": 2.5},
            {"word": "RIGHT", "start": 2.5, "end": 3.0}]}]}
    before = deepcopy(plan)
    ass = _write_ass(SimpleNamespace(id="words", caption_plan=plan), tmp_path, [(0.5, 1.5)])
    events = pysubs2.load(ass).events
    assert [(e.start, e.end) for e in events] == [(1200, 1500), (1500, 2000)]
    assert "}LEFT{" in events[0].text
    assert "}RIGHT{" in events[1].text
    assert plan == before
    source, out = tmp_path / "words-source.mp4", tmp_path / "words.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=320x180:r=30:d=4", "-c:v", "libx264",
         "-preset", "ultrafast", str(source)], what="word highlight fixture")
    cmd = render.build_render_cmd(
        str(source), {"start": 0, "end": 3.5}, {}, ass, str(out),
        fps=30, crf=18, preset="ultrafast", out_w=270, out_h=480,
        drop_spans=[(.5, 1.5)], has_audio=False)
    run(cmd, what="word highlight render")
    cap = cv2.VideoCapture(str(out))
    try:
        for at, left in ((1.3, True), (1.7, False)):
            cap.set(cv2.CAP_PROP_POS_MSEC, at * 1000)
            ok, frame = cap.read()
            assert ok
            yellow = (frame[:, :, 2] > 150) & (frame[:, :, 1] > 100) & (frame[:, :, 0] < 80)
            assert yellow.sum() > 20
            xs = yellow.nonzero()[1]
            assert bool(xs.mean() < 135) == left
    finally:
        cap.release()
