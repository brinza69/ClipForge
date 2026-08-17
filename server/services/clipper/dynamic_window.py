"""Per-window signals the dynamic editor needs, and nothing else can supply.

Split out of `scripts/render_dynamic_clip.py` so the pipeline can reach it. The
shot planner needs two things the whole-VOD artifacts do not have:

  - a DENSE face track. `signals.json` samples roughly every 11 seconds, which
    is three boxes inside a 30-second clip — nowhere near enough to anchor
    twenty shots.
  - motion measured INSIDE the gameplay region. The whole-frame signal cannot
    answer "is something happening in the game", because the facecam is in the
    same frame and the streamer moves constantly.

Both are per-candidate, both need the pixels, and neither can be precomputed
for a whole stream at this density. DB-free and importable on its own, like the
rest of `services/clipper/`.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

from services.clipper.dynamic_cameras import action_band
from services.clipper.ffmpeg_tools import ffmpeg_bin, video_info
from services.clipper.signals import face_presence

logger = logging.getLogger("clipforge.clipper.dynamic")

FACE_HOP_S = 0.25
MOTION_COLS = 8

# What a game UI panel looks like and gameplay does not: near-neutral colour at
# middling brightness. Measured on 90 frames of the test slice, the share of the
# band matching this is bimodal with an empty gap — 74 gameplay frames under
# 0.02, 15 menu frames over 0.11, nothing between 0.05 and 0.11.
UI_SPREAD_MAX = 18     # max channel spread for "this pixel is grey"
UI_LUM_MIN, UI_LUM_MAX = 110, 215

# Cutting the window out of the proxy is a fixed cost per clip; past this it is
# the analysis that is broken, not the machine.
_CUT_TIMEOUT_S = 600


def region_motion(window: Path | str, hop: float,
                  band: tuple[float, float, float, float], src_w: int
                  ) -> tuple[list[float], list[float], list[float], list[float]]:
    """(how much is happening, where, and how much there is to look at) per `hop`.

    Two things the whole-frame motion signal in signals.json cannot give us.
    It cannot answer "is something happening in the GAME", because the facecam
    is in the same frame and he moves constantly — hence the band. And it has no
    spatial component at all, so a gameplay camera built on it points at a fixed
    rectangle and spends half the clip framing an empty wall. Splitting the band
    into columns and returning the busiest one turns the second camera into
    something that actually follows the action.

    The focus value is an x centre in SOURCE pixels, ready to hand to
    `dynamic_edit`; `-1.0` means "nothing moved, no opinion".

    The third series is the band's spatial standard deviation — how much DETAIL
    is on screen, independent of whether it moved. Motion alone cannot tell a
    loading screen from a quiet moment of real gameplay: a black screen with a
    spinner ticking over scores as "something is happening" while being the worst
    thing the clip could cut to. Detail separates them, because a dead screen has
    nothing in it whichever frame you look at.

    The fourth is the share of the band that is flat, mid-luminance grey — an
    open inventory or crafting panel. Neither motion nor detail catches those:
    a menu is high-contrast and the mouse keeps moving, so both call it alive,
    and 17% of the tested slice was menu screens. What a game UI panel is that
    gameplay is not is DESATURATED AND FLAT, so count pixels whose channels sit
    within 18 levels of each other at middling brightness.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(window))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 10.0
        step = max(1, int(round(fps * hop)))
        band_x0, band_x1 = src_w * band[0], src_w * band[1]
        col_w = (band_x1 - band_x0) / MOTION_COLS

        totals: list[float] = []
        focus: list[float] = []
        detail: list[float] = []
        ui: list[float] = []
        previous = None
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % step == 0:
                h, w = frame.shape[:2]
                crop = frame[int(h * band[2]):int(h * band[3]),
                             int(w * band[0]):int(w * band[1])]
                colour = cv2.resize(crop, (MOTION_COLS * 12, 54),
                                    interpolation=cv2.INTER_AREA).astype(np.int16)
                lo = colour.min(axis=2)
                spread = colour.max(axis=2) - lo
                lum = colour.mean(axis=2)
                ui.append(float(np.mean((spread < UI_SPREAD_MAX)
                                        & (lum > UI_LUM_MIN) & (lum < UI_LUM_MAX))))
                grey = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
                                  (MOTION_COLS * 12, 54),
                                  interpolation=cv2.INTER_AREA).astype(np.float32)
                # Spatial spread of THIS frame — no previous needed, so the
                # first sample carries a real value instead of a zero.
                detail.append(float(grey.std()))
                if previous is None:
                    totals.append(0.0)
                    focus.append(-1.0)
                else:
                    diff = np.abs(grey - previous)
                    totals.append(float(diff.mean()))
                    per_col = diff.reshape(54, MOTION_COLS, 12).mean(axis=(0, 2))
                    hottest = int(per_col.argmax())
                    focus.append(-1.0 if per_col.max() <= 0.0
                                 else band_x0 + (hottest + 0.5) * col_w)
                previous = grey
            index += 1
        return totals, focus, detail, ui
    finally:
        cap.release()


# The spatial half of the same measure. `region_motion` answers "is a menu on
# screen"; this answers WHERE, which is what a caption has to keep out of.
PANEL_COLS, PANEL_ROWS = 96, 54     # ~20px cells on a 1920-wide source
PANEL_PERSISTENCE = 0.5             # share of frames a cell must be grey in
# Of the frame. Swept against the test slice, and 0.015 was a guess that found
# NOTHING on five real clips — the first version of this detector measured
# nothing at all, which is this codebase's most repeated failure. The persistent
# UI on a real stream is not one big panel: it is a subscriber counter, a
# BOSSES BEATEN box, a timer, a hotbar. At 0.002 (about ten cells) those come
# back, on three different clips, at identical coordinates — which is itself the
# evidence, because a stream's chrome does not move.
PANEL_MIN_AREA = 0.002
PANEL_HOP_S = 0.5


def ui_panels(window: Path | str, src_w: int, src_h: int,
              hop: float = PANEL_HOP_S) -> list[dict[str, int]]:
    """Rectangles of game UI — inventory, crafting, menus — in SOURCE pixels.

    Why this exists at all: `resolve_position` has implemented caption collision
    avoidance since the clipper shipped, and it avoids the rects in `keep_out`,
    which come from HUD detection. On the source where a caption demonstrably
    landed on the Minecraft inventory, `regions.hud` is `[]` — so the avoidance
    was working perfectly against an empty list. This supplies the list.

    PERSISTENCE is the whole design, and it is the same lesson the facecam
    detector learned: what makes a rectangle a UI panel is not that it looked
    grey once but that it IS THERE. A single frame of grey stone reads exactly
    like an inventory; twenty seconds of it in the same cells does not. Cells
    must be grey in half the sampled frames to count.

    Detection is per CLIP, not per project, because a panel opens and closes —
    `regions.json` is measured once for a whole stream and cannot express that.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(window))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 10.0
        step = max(1, int(round(fps * hop)))
        hits = np.zeros((PANEL_ROWS, PANEL_COLS), dtype=np.int32)
        frames = 0
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % step == 0:
                small = cv2.resize(frame, (PANEL_COLS, PANEL_ROWS),
                                   interpolation=cv2.INTER_AREA).astype(np.int16)
                spread = small.max(axis=2) - small.min(axis=2)
                lum = small.mean(axis=2)
                hits += ((spread < UI_SPREAD_MAX)
                         & (lum > UI_LUM_MIN) & (lum < UI_LUM_MAX)).astype(np.int32)
                frames += 1
            index += 1
    finally:
        cap.release()

    if frames == 0:
        return []

    mask = ((hits / frames) >= PANEL_PERSISTENCE).astype(np.uint8)
    if not mask.any():
        return []

    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    cell_w, cell_h = src_w / PANEL_COLS, src_h / PANEL_ROWS
    out: list[dict[str, int]] = []
    for i in range(1, count):                       # 0 is the background
        x, y, w, h, area = (int(stats[i, k]) for k in range(5))
        if area / float(PANEL_COLS * PANEL_ROWS) < PANEL_MIN_AREA:
            continue
        out.append({"x": int(x * cell_w), "y": int(y * cell_h),
                    "w": max(1, int(w * cell_w)), "h": max(1, int(h * cell_h))})
    out.sort(key=lambda r: -(r["w"] * r["h"]))
    return out


def analyse_window(proxy: Path | str, start: float, duration: float,
                   band: tuple[float, float, float, float] | None,
                   src_w: int) -> dict[str, Any]:
    """Everything `plan_dynamic_edit` needs for one candidate.

    Returns `{"faces", "motion", "focus", "detail", "ui", "band", "hop"}`.
    Face times are on the SOURCE clock, because `dynamic_edit` subtracts
    `cand["start"]` itself.

    Pass `band=None` to derive it from the facecam this clip actually has, which
    is the right default: a constant band encodes one streamer's layout.

    Cutting the window out of the proxy first turns hundreds of random seeks on
    a 6-hour file into one sequential read, and both measurements share it.
    """
    work = Path(tempfile.mkdtemp(prefix="dynwin_"))
    try:
        window = work / "window.mp4"
        subprocess.run(
            [ffmpeg_bin(), "-y", "-loglevel", "error",
             "-ss", f"{start:.3f}", "-i", str(proxy), "-t", f"{duration:.3f}",
             "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
             str(window)],
            check=True, capture_output=True, timeout=_CUT_TIMEOUT_S)

        times = [i * FACE_HOP_S for i in range(int(duration / FACE_HOP_S) + 1)]
        samples = face_presence(str(window), times)
        faces = [{"t": float(s.get("t", 0.0)) + start, "boxes": s.get("boxes") or []}
                 for s in samples]
        if band is None:
            info = video_info(str(window))
            band = action_band(samples, int(info.get("width") or 0),
                               int(info.get("height") or 0))
        totals, focus, detail, ui = region_motion(window, FACE_HOP_S, band, src_w)
        info = video_info(str(window))
        src_h = int(src_w * (int(info.get("height") or 0) or 1)
                    / max(1, int(info.get("width") or 0) or 1))
        return {"faces": faces, "motion": totals, "focus": focus,
                "detail": detail, "ui": ui, "band": tuple(band),
                "panels": ui_panels(window, src_w, src_h),
                "hop": FACE_HOP_S}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def face_seen(signals: Sequence[dict] | None) -> int:
    """How many samples of a window's face track actually found a face."""
    return sum(1 for f in (signals or []) if f.get("boxes"))
