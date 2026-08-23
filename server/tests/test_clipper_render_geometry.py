"""Does the RENDER letterbox, measured on pixels FFmpeg actually produced.

This file exists because the test it replaces passed while the product was
broken. `test_the_graph_letterboxes_rather_than_stretching` asserted that the
filtergraph text contained `force_original_aspect_ratio` and `pad`. It did. And
`scale` fixes its output size at configuration time and never recomputes it when
`sendcmd` changes the crop, so a `fit` shot was stretched into 1080x1920 anyway.
Four blind-review sessions later the reviewer wrote the same note on every
source: "aceeași problemă cu imaginea streched".

So nothing here reads the graph. Each test renders a synthetic source through
the real pipeline and measures the frames: a circle stays a circle, or the
render is distorting.

The synthetic source is a white disc on grey. A circle is the right probe
because it is the one shape whose distortion is unmistakable from a single
measurement — width against height — with no reference frame needed.
"""

from __future__ import annotations

import json
import subprocess

import pytest

pytest.importorskip("numpy")
import numpy as np  # noqa: E402

from services.clipper import dynamic_render as dr  # noqa: E402
from services.clipper.ffmpeg_tools import ffmpeg_bin, ffprobe_bin  # noqa: E402

SRC_W, SRC_H = 1920, 1080
R = 300


def _source(path, seconds: float = 2.0) -> str:
    """A white disc on grey, 1920x1080, as long as the plan needs.

    The source has to outlast the plan: a two-second source under a six-second
    shot list renders sixty frames and the transition test measures nothing.
    """
    subprocess.run(
        [ffmpeg_bin(), "-y", "-v", "error",
         "-f", "lavfi", "-i",
         f"color=c=gray:s={SRC_W}x{SRC_H}:d={seconds + 1:.1f}:r=30",
         "-vf", (f"drawbox=x=0:y=0:w={SRC_W}:h={SRC_H}:color=gray:t=fill,"
                 f"geq=lum='if(lt((X-{SRC_W//2})*(X-{SRC_W//2})"
                 f"+(Y-{SRC_H//2})*(Y-{SRC_H//2}),{R*R}),255,110)':"
                 "cb=128:cr=128"),
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)
    return str(path)


def _disc(frame: np.ndarray) -> tuple[int, int]:
    """(width, height) of the bright disc in a greyscale frame."""
    bright = frame > 190
    cols = np.flatnonzero(bright.any(axis=0))
    rows = np.flatnonzero(bright.any(axis=1))
    if not len(cols) or not len(rows):
        return 0, 0
    return int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1)


def _frame(path, at: float) -> np.ndarray:
    out = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-ss", str(at), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True).stdout
    return np.frombuffer(out, np.uint8).reshape(1920, 1080)


def _shot(t0, t1, composition):
    return {"t0": t0, "t1": t1, "composition": composition,
            "rect": ({"x": 0, "y": 0, "w": SRC_W, "h": SRC_H}
                     if composition == "fit"
                     else {"x": 660, "y": 0, "w": 606, "h": 1078}),
            "anchor": [SRC_W / 2, SRC_H / 2], "move": "hold",
            "camera": "fit" if composition == "fit" else "face"}


def _render(tmp_path, *compositions):
    src = _source(tmp_path / "src.mp4", 2.0 * len(compositions))
    plan = {"duration": 2.0 * len(compositions), "style": {}, "hits": [],
            "shots": [_shot(2.0 * i, 2.0 * (i + 1), c)
                      for i, c in enumerate(compositions)]}
    out = tmp_path / "out.mp4"
    dr.render_dynamic_clip(src, plan, str(out), start=0.0, work_dir=str(tmp_path),
                           ass_path=None, src_w=SRC_W, src_h=SRC_H, fps=30,
                           crf=18, preset="ultrafast", has_audio=False,
                           loudness=False)
    return out


def test_a_full_frame_shot_keeps_the_circle_round(tmp_path):
    """The defect, stated as a measurement. Stretching a 16:9 frame into 9:16
    makes the disc 1.78x taller than it is wide; the reviewer called it
    "streched" on all four sources and no graph-reading test could see it."""
    out = _render(tmp_path, "fit")
    w, h = _disc(_frame(out, 1.0))

    assert w > 50 and h > 50, f"no disc found ({w}x{h})"
    assert abs(h / w - 1.0) < 0.10, f"disc is {w}x{h} — aspect {h / w:.2f}"


def test_a_full_frame_shot_shows_the_whole_frame(tmp_path):
    """Round is not enough on its own: a 9:16 crop of the middle would also be
    round. The letterbox has to be there too."""
    frame = _frame(_render(tmp_path, "fit"), 1.0)
    rows = frame.mean(axis=1)
    dark = int((rows < 30).sum())

    expected = 1920 - round(1080 * SRC_H / SRC_W)
    assert abs(dark - expected) < 40, f"{dark}px of bars, expected ~{expected}"


def test_an_ordinary_shot_is_not_letterboxed(tmp_path):
    """The other half of the promise: the fix must not put bars on the shots
    that were already correct."""
    frame = _frame(_render(tmp_path, "crop"), 1.0)
    rows = frame.mean(axis=1)

    assert int((rows < 30).sum()) < 40, "a crop shot picked up letterbox bars"


def test_the_transition_changes_geometry_and_loses_no_frames(tmp_path):
    """crop -> fit -> crop, measured on both sides of both cuts."""
    out = _render(tmp_path, "crop", "fit", "crop")

    probe = json.loads(subprocess.run(
        [ffprobe_bin(), "-v", "error",
         "-count_frames", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_frames,width,height", "-of", "json", str(out)],
        check=True, capture_output=True, text=True).stdout)
    stream = probe["streams"][0]
    assert (stream["width"], stream["height"]) == (1080, 1920)
    assert int(stream["nb_read_frames"]) >= 175, stream["nb_read_frames"]

    bars = [int((_frame(out, t).mean(axis=1) < 30).sum()) for t in (1.0, 3.0, 5.0)]
    assert bars[0] < 40 and bars[2] < 40, f"crop shots letterboxed: {bars}"
    assert bars[1] > 300, f"the fit shot was not letterboxed: {bars}"
