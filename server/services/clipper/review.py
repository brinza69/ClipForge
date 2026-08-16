"""
ClipForge — AI Stream Clipper: Pass D, looking at the clip before it ships.

Everything upstream judges the MOMENT. Nothing has ever judged the CLIP. A
perfectly chosen moment still ships badly when the caption lands on the
inventory panel, when the crop cuts the speaker's face in half, or when a shot
falls on a loading screen — and all three have happened on real exports while
every test passed.

Two rules shape this module, and they are the two this codebase keeps
relearning:

  * MEASURE THE PICTURE, NOT THE ARTEFACT. The obvious way to check "is the
    caption on top of the HUD" is to intersect it with `regions.hud`. On the
    source where a caption actually landed on the Minecraft inventory,
    `regions.hud` is `[]` — so that check would have found nothing, on the one
    clip that proves the defect. Findings here come from pixels.
  * IT RUNS BEFORE THE ENCODE. Reviewing the rendered file would be truer to
    the brief's wording, but a finding that arrives after a 12-24s encode can
    only be reported, and one that arrives before it can be fixed. §17 says
    cheap filtering first. The geometry is exact either way: an output-frame
    strip maps back into the source crop by a scale factor.

The verdict vocabulary is §22's: APPROVE / REVISE / REJECT. REVISE means a
finding something can act on — the caption can move — rather than a milder
REJECT.

The seam for the multimodal half is `Finding` and `verdict`: a model-driven
reviewer appends findings of its own and the verdict is taken over all of them.
Nothing here needs to change when that arrives.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from services.clipper.signals import UI_LUM_MAX, UI_LUM_MIN, UI_SPREAD_MAX

logger = logging.getLogger("clipforge.clipper.review")

__all__ = ["REVIEW_VERSION", "Finding", "caption_band", "band_in_source",
           "ui_share", "verdict", "review_plan"]

REVIEW_VERSION = "review_v1"

# How tall the caption's ink actually is, as a multiple of the font size: cap
# height plus the outline on both sides plus a little leading. The caption plan
# carries a font size and a centre, not a box, and the burned result is one line
# that never wraps (`captions.py` chunks to `max_words`), so the band is a
# horizontal strip rather than a rectangle around the glyphs. That is also the
# right granularity for the question being asked — "is the text sitting on a
# menu panel" is about the strip, not about individual letters.
CAPTION_INK_RATIO = 1.30

# A caption is over a UI panel when this much of the strip behind it is the
# flat mid-grey `signals` already uses to find menus. The source measurement is
# bimodal with an empty gap — 74 gameplay frames under 0.02, 15 menu frames over
# 0.11 — and 0.25 sits well inside the menu mode, because a strip that is a
# QUARTER panel is already unreadable over the part that matters.
CAPTION_UI_SHARE = 0.25

# Below this spatial standard deviation the output frame has nothing in it.
# Same constant and same reasoning as `dynamic_cameras.game_flat_below`: measured
# against synthetic content, everything genuinely blank scores under 6.1 and
# anything with a picture in it 33+.
DEAD_FRAME_STD = 8.0

# ...and a clip is only rejected when this share of its sampled frames are dead.
# One dead frame is a shot that opened on a fade; half of them is not a clip.
DEAD_CLIP_SHARE = 0.40

# A face is "cut" when this much of its box falls outside the crop. A sliver of
# ear leaving the frame is framing; a third of the head is a mistake.
FACE_CLIPPED_SHARE = 0.33


class Finding:
    """One thing wrong with the clip, and how bad it is.

    `where` is the clip-relative second it was seen at, so a reviewer can jump
    to it — the panel that displays these is useless without that.
    """

    __slots__ = ("kind", "severity", "where", "detail", "value")

    def __init__(self, kind: str, severity: str, where: float,
                 detail: str, value: float = 0.0) -> None:
        self.kind = kind
        self.severity = severity        # "revise" | "reject"
        self.where = round(float(where), 2)
        self.detail = detail
        self.value = round(float(value), 4)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "severity": self.severity,
                "at": self.where, "detail": self.detail, "value": self.value}

    def __repr__(self) -> str:                                  # pragma: no cover
        return f"<Finding {self.kind} {self.severity} @{self.where}s>"


# ---------------------------------------------------------------------------
# geometry — pure
# ---------------------------------------------------------------------------

def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out


def caption_band(caption_plan: dict, out_h: int) -> tuple[float, float]:
    """(top, bottom) of the caption's ink in OUTPUT pixels.

    Returns an empty band when the clip has no captions, which is a legitimate
    state — three of the nine reference Shorts carry none.
    """
    if not isinstance(caption_plan, dict) or not (caption_plan.get("chunks") or []):
        return (0.0, 0.0)
    size = _f((caption_plan.get("style") or {}).get("font_size"), 72.0)
    ink = max(8.0, size * _f(caption_plan.get("scale"), 1.0) * CAPTION_INK_RATIO)
    centre = _f(caption_plan.get("y_pct"), 0.75) * out_h
    top = max(0.0, centre - ink / 2.0)
    return (top, min(float(out_h), top + ink))


def band_in_source(band: tuple[float, float], rect: dict, out_h: int
                   ) -> tuple[int, int, int, int]:
    """An output-frame strip mapped back into the SOURCE rectangle it was cut from.

    The renderer crops `rect` out of the source and scales it to fill the output,
    so the mapping is one scale factor. Horizontally the strip spans the whole
    crop, because the caption is centred and the question is what sits behind
    the line, not behind a particular word.
    """
    top, bottom = band
    if bottom <= top or out_h <= 0:
        return (0, 0, 0, 0)
    scale = _f(rect.get("h")) / float(out_h)
    y0 = int(_f(rect.get("y")) + top * scale)
    y1 = int(_f(rect.get("y")) + bottom * scale)
    return (int(_f(rect.get("x"))), y0, int(_f(rect.get("w"))), max(1, y1 - y0))


def _overlap(a: dict, b: tuple[int, int, int, int]) -> float:
    """Share of rect `a` covered by box `b`."""
    ax, ay = _f(a.get("x")), _f(a.get("y"))
    aw, ah = _f(a.get("w")), _f(a.get("h"))
    if aw <= 0 or ah <= 0:
        return 0.0
    bx, by, bw, bh = b
    dx = min(ax + aw, bx + bw) - max(ax, bx)
    dy = min(ay + ah, by + bh) - max(ay, by)
    if dx <= 0 or dy <= 0:
        return 0.0
    return (dx * dy) / (aw * ah)


def ui_share(patch: np.ndarray) -> float:
    """Share of a BGR patch that is flat mid-grey — i.e. a game UI panel.

    The same three-line mask `signals.motion_timeline` and
    `dynamic_window.region_motion` both run, imported rather than copied a third
    time. What differs is only where it is pointed: they average it over a whole
    frame or a whole band, this one over the strip under the caption.
    """
    if patch is None or patch.size == 0 or patch.ndim != 3:
        return 0.0
    channels = patch.astype(np.int16)
    spread = channels.max(axis=2) - channels.min(axis=2)
    lum = channels.mean(axis=2)
    return float(np.mean((spread < UI_SPREAD_MAX)
                         & (lum > UI_LUM_MIN) & (lum < UI_LUM_MAX)))


def verdict(findings: Sequence[Finding]) -> str:
    """§22's three-way decision over every finding, whoever produced them."""
    if any(f.severity == "reject" for f in findings):
        return "REJECT"
    if findings:
        return "REVISE"
    return "APPROVE"


# ---------------------------------------------------------------------------
# the checks — pure, one per observed failure
# ---------------------------------------------------------------------------

def _shot_at(shots: Sequence[dict], t: float) -> dict | None:
    for shot in shots or []:
        if _f(shot.get("t0")) <= t < _f(shot.get("t1")):
            return shot
    return shots[-1] if shots else None


def check_caption_occlusion(samples: Iterable[tuple[float, float]]
                            ) -> list[Finding]:
    """A caption sitting on a menu panel, from (time, ui share) pairs.

    This is the defect that shipped: a caption landed over the Minecraft
    inventory on a real export. `resolve_position` does implement collision
    avoidance, but it avoids `keep_out` rects that come from HUD detection, and
    HUD detection returned [] on that source — so the avoidance had nothing to
    avoid and the caption went exactly where the panel was.
    """
    out = []
    for t, share in samples:
        if share >= CAPTION_UI_SHARE:
            out.append(Finding(
                "caption_over_ui", "revise", t,
                f"{share:.0%} of the strip behind the caption is a UI panel",
                share))
    return out


def check_dead_frames(samples: Sequence[tuple[float, float]]) -> list[Finding]:
    """Shots that landed on a loading screen or a fade.

    The planner already refuses to CUT to a dead region, on the average over the
    shot. A shot can still open or close on one, and the average hides it.
    """
    dead = [(t, std) for t, std in samples if std < DEAD_FRAME_STD]
    if not dead:
        return []
    share = len(dead) / max(1, len(samples))
    if share >= DEAD_CLIP_SHARE:
        return [Finding("dead_clip", "reject", dead[0][0],
                        f"{share:.0%} of sampled frames have nothing in them",
                        share)]
    return [Finding("dead_frame", "revise", t,
                    "the frame here is effectively blank", std)
            for t, std in dead]


def check_faces_intact(shots: Sequence[dict], faces: Sequence[dict],
                       clip_start: float, subject: dict | None = None,
                       sx: float = 1.0, sy: float = 1.0) -> list[Finding]:
    """A crop that slices the head of the person the shot is framing.

    Two things this must NOT do, both found by running it on a real clip before
    trusting it:

      * Judge a gameplay camera. It is SUPPOSED to exclude the facecam —
        `camera_rects` pushes it clear on purpose — so a clipped face there is
        the feature working.
      * Judge every detected face. The test source is a CO-STREAM with a facecam
        in each top corner. A shot framing the right-hand one legitimately
        excludes the left-hand one, and the first version of this check reported
        that as "100% of the face is outside the crop" on every clip it saw.
        Only boxes belonging to the cluster the camera is tracking count.

    `sx`/`sy` convert PROXY pixels to SOURCE pixels — face boxes are measured on
    the proxy, crop rects are emitted in source pixels, which is the trap
    `dynamic_cameras` opens its docstring with.
    """
    out = []
    sx = sx if sx > 0 else 1.0
    sy = sy if sy > 0 else 1.0
    if not subject:
        return out
    # Same tolerance `dynamic_cameras._dominant` uses to decide which detections
    # are the facecam and which are posters, avatars and passers-by.
    scx, scy = _f(subject.get("cx")), _f(subject.get("cy"))
    tol_x, tol_y = 1920 * 0.16, 1080 * 0.22

    for sample in faces or []:
        t = _f(sample.get("t")) - clip_start
        shot = _shot_at(shots, t)
        if shot is None or not str(shot.get("camera", "")).startswith("face"):
            continue
        rect = shot.get("rect") or {}
        for box in sample.get("boxes") or []:
            if not isinstance(box, (list, tuple)) or len(box) < 4:
                continue
            face = {"x": _f(box[0]) * sx, "y": _f(box[1]) * sy,
                    "w": _f(box[2]) * sx, "h": _f(box[3]) * sy}
            cx = face["x"] + face["w"] / 2.0
            cy = face["y"] + face["h"] / 2.0
            if abs(cx - scx) > tol_x or abs(cy - scy) > tol_y:
                continue                 # somebody else, or something else
            outside = 1.0 - _overlap(
                face, (int(_f(rect.get("x"))), int(_f(rect.get("y"))),
                       int(_f(rect.get("w"))), int(_f(rect.get("h")))))
            if outside >= FACE_CLIPPED_SHARE:
                out.append(Finding(
                    "face_clipped", "revise", t,
                    f"{outside:.0%} of the framed face is outside the crop",
                    outside))
                break                    # one finding per sample, not per box
    return out


# ---------------------------------------------------------------------------
# the half that decodes
# ---------------------------------------------------------------------------

def review_plan(proxy: Path | str, plan: dict, caption_plan: dict | None,
                *, clip_start: float, faces: Sequence[dict] = (),
                src_w: int = 1920, out_h: int = 1920,
                samples: int = 12) -> dict[str, Any]:
    """Sample the clip's own frames and report what is wrong with it.

    Reads the PROXY, like every other analysis pass — §39. The crop rects are in
    source pixels and the proxy is a scaled copy of the same frame, so one ratio
    converts them.

    Never raises: a review that fails must not lose an export. It returns a
    verdict of APPROVE with a warning, which is the same shape as a clean pass
    and cannot block anything.
    """
    result: dict[str, Any] = {"version": REVIEW_VERSION, "verdict": "APPROVE",
                              "findings": [], "sampled": 0, "warnings": []}
    shots = plan.get("shots") or []
    duration = _f(plan.get("duration"))
    if not shots or duration <= 0:
        result["warnings"].append("no shots to review")
        return result

    try:
        import cv2
    except Exception as exc:                                    # pragma: no cover
        result["warnings"].append(f"opencv unavailable: {exc}")
        return result

    band = caption_band(caption_plan or {}, out_h)
    times = [duration * (i + 0.5) / samples for i in range(max(1, samples))]

    cap = cv2.VideoCapture(str(proxy))
    if not cap.isOpened():
        result["warnings"].append("could not open the proxy")
        return result

    ui_samples: list[tuple[float, float]] = []
    flat_samples: list[tuple[float, float]] = []
    ratio = 1.0        # bound before the try: the `except` path reads it below
    try:
        proxy_w = float(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0.0)
        ratio = (proxy_w / float(src_w)) if proxy_w > 0 and src_w > 0 else 0.0
        if ratio <= 0:
            result["warnings"].append("proxy has no width; skipping pixel checks")
            return result

        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, (clip_start + t) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            shot = _shot_at(shots, t)
            rect = (shot or {}).get("rect") or {}
            x = int(_f(rect.get("x")) * ratio)
            y = int(_f(rect.get("y")) * ratio)
            w = max(1, int(_f(rect.get("w")) * ratio))
            h = max(1, int(_f(rect.get("h")) * ratio))
            crop = frame[y:y + h, x:x + w]
            if crop.size == 0:
                continue
            result["sampled"] += 1

            grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
            flat_samples.append((t, float(grey.std())))

            if band[1] > band[0]:
                bx, by, bw, bh = band_in_source(band, rect, out_h)
                py0 = int(by * ratio) - y
                py1 = py0 + max(1, int(bh * ratio))
                strip = crop[max(0, py0):max(1, py1), :]
                if strip.size:
                    ui_samples.append((t, ui_share(strip)))
    except Exception as exc:                                    # pragma: no cover
        logger.warning("review: sampling failed (%s)", exc, exc_info=True)
        result["warnings"].append(f"sampling failed: {exc}")
    finally:
        cap.release()

    findings = (check_caption_occlusion(ui_samples)
                + check_dead_frames(flat_samples)
                + check_faces_intact(shots, faces, clip_start,
                                     (plan.get("subject") or {}).get("face"),
                                     1.0 / ratio, 1.0 / ratio))
    result["findings"] = [f.as_dict() for f in findings]
    result["verdict"] = verdict(findings)
    return result
