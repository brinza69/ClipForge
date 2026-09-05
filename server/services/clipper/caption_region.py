"""The region that holds the subject AND the source's subtitle, per interval.

BETWEEN THE TWO FRAMINGS THAT WERE ON THE TABLE. A 9:16 crop of go ghost keeps
the speaker at 803 output pixels and cuts the subtitle; the full letterbox keeps
every pixel of the subtitle and drops the speaker to 254. Codex's third option
is a region wider than 9:16 that contains both, letterboxed into the output —
and it has to be established from LOCAL measurement, because the CUMULATIVE band
is 1216 px wide and no argument may presume a real line ever was.

WHY LOCAL CHANGES THE ANSWER, measured on `6053a598cf06` at 51.4 s: the lines
actually on screen span x 0.358..0.673 — 806 source pixels — against the
envelope's 1216. That region, letterboxed, holds the face at 808 output pixels
and the caption band at 100: both within a pixel or two of what the shipping
crop delivers, with the whole subtitle inside. The envelope would have argued
for a framing three times worse than the one the evidence supports.

WHAT IT IS NOT. It is not a proposal to re-render anything. It computes a
rectangle and the delivered sizes that follow from it, so a static comparison
can be put beside the two treatments that were already rendered. Whether the
result reads is the question the video probe asks a person.

TWO RULES IT WILL NOT BEND.

An interval with no subtitle OBSERVATION does not get a region. Not the
subject's own box, not the previous interval's rectangle — `unavailable`,
because a region built to hold text nobody saw is a framing decision resting on
an absence, which is the failure mode every module in this batch is shaped
against.

And the region is per INTERVAL and constant within it. Recomputing it as each
line appears and disappears would resize the picture on every caption, which is
the defect the whole exercise is meant to avoid: `absorb_brief_fit_islands`
exists because brief composition changes read as broken, and a zoom that
breathes with the dialogue is that defect at a higher frequency.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["SCHEMA", "NO_TEXT", "NO_SUBJECT", "NO_GEOMETRY", "TOO_TALL",
           "region_for", "delivered_sizes"]

SCHEMA = "clipper_caption_region_v1"

NO_TEXT = "no_subtitle_was_observed_in_this_interval"
NO_SUBJECT = "no_subject_box_for_this_interval"
NO_GEOMETRY = "source_dimensions_unknown"
#: The union is taller than the frame, which cannot happen from real boxes and
#: means one of the inputs is in the wrong coordinate system. Refused rather
#: than clamped: a clamp would produce a plausible rectangle out of a
#: measurement that is wrong somewhere else.
TOO_TALL = "the_union_does_not_fit_inside_the_source_frame"

#: The output the region is letterboxed into. Same constants the renderer uses.
OUT_W, OUT_H = 1080, 1920


def _finite(value: Any) -> float | None:
    try:
        got = float(value)
    except (TypeError, ValueError):
        return None
    if got != got or got in (float("inf"), float("-inf")):
        return None
    return got


def _union(boxes: Sequence[dict], w: float, h: float
           ) -> tuple[float, float, float, float] | None:
    """The bounding box of fractional boxes, in SOURCE pixels."""
    got = []
    for box in boxes or []:
        if not isinstance(box, dict):
            return None
        vals = [_finite(box.get(k)) for k in ("x0", "x1", "y0", "y1")]
        if any(v is None for v in vals):
            return None
        x0, x1, y0, y1 = vals  # type: ignore[misc]
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            return None
        got.append((x0 * w, x1 * w, y0 * h, y1 * h))
    if not got:
        return None
    return (min(g[0] for g in got), max(g[1] for g in got),
            min(g[2] for g in got), max(g[3] for g in got))


def _subject_box(subject: Any, src_w: float, src_h: float
                 ) -> tuple[float, float, float, float] | None:
    """The speaker's box in SOURCE pixels, from the plan's own averages.

    `dynamic_plan["subject"]["face"]` carries `cx`, `cy` and `w` over the whole
    clip. Height is not recorded; the detector's boxes are square, which is why
    `w` is used for both — an assumption, and it is stated here rather than
    hidden in the arithmetic because a non-square detector would silently make
    every region the wrong height.
    """
    face = (subject or {}).get("face") if isinstance(subject, dict) else None
    if not isinstance(face, dict):
        return None
    cx, cy, w = (_finite(face.get("cx")), _finite(face.get("cy")),
                 _finite(face.get("w")))
    if cx is None or cy is None or w is None or w <= 0:
        return None
    half = w / 2.0
    return (max(0.0, cx - half), min(src_w, cx + half),
            max(0.0, cy - half), min(src_h, cy + half))


def region_for(boxes: Any, subject: Any, src_w: Any, src_h: Any) -> dict:
    """The smallest rectangle holding the observed text and the subject.

    `boxes` are the subtitle lines actually seen in this interval, as fractions
    of the image they were measured in — `source_caption_observation.coverage`
    returns exactly that list. Fractions, because the detector runs on the proxy
    and pixels do not travel.
    """
    out: dict[str, Any] = {"schema": SCHEMA, "why": None, "rect": None,
                           "text_rect": None, "subject_rect": None,
                           "aspect": None, "wider_than_9_16": None}
    sw, sh = _finite(src_w), _finite(src_h)
    if not sw or not sh or sw < 1 or sh < 1:
        out["why"] = NO_GEOMETRY
        return out
    if not isinstance(boxes, Sequence) or isinstance(boxes, (str, bytes)):
        out["why"] = NO_TEXT
        return out
    text = _union(boxes, sw, sh)
    if text is None:
        # NO REGION FROM NO OBSERVATION. Falling back to the subject's own box
        # would produce a framing decision about text nobody saw.
        out["why"] = NO_TEXT
        return out
    face = _subject_box(subject, sw, sh)
    if face is None:
        out["why"] = NO_SUBJECT
        out["text_rect"] = _rect(text)
        return out

    x0, x1 = min(text[0], face[0]), max(text[1], face[1])
    y0, y1 = min(text[2], face[2]), max(text[3], face[3])
    if (y1 - y0) > sh + 1.0 or (x1 - x0) > sw + 1.0:
        out["why"] = TOO_TALL
        return out
    out["text_rect"], out["subject_rect"] = _rect(text), _rect(face)
    out["rect"] = {"x": round(x0, 1), "y": round(y0, 1),
                   "w": round(x1 - x0, 1), "h": round(y1 - y0, 1)}
    out["aspect"] = round((x1 - x0) / max(1e-6, y1 - y0), 4)
    out["wider_than_9_16"] = bool(out["aspect"] > 9.0 / 16.0)
    return out


def _rect(box: tuple[float, float, float, float]) -> dict:
    return {"x": round(box[0], 1), "y": round(box[2], 1),
            "w": round(box[1] - box[0], 1), "h": round(box[3] - box[2], 1)}


def delivered_sizes(rect: Any, *, subject_w: Any, band_h: Any) -> dict:
    """What the region costs, in delivered pixels of the 1080x1920 output.

    A region wider than 9:16 is scaled to the output's WIDTH and letterboxed top
    and bottom; a narrower one is scaled to its HEIGHT and pillarboxed. One
    scale per region, which is what letterboxing means — and the reason it is
    computed here rather than assumed is that the two cases give different
    numbers and the obvious version uses the width scale for both.
    """
    out: dict[str, Any] = {"scale": None, "subject_w_out_px": None,
                           "band_h_out_px": None, "letterbox_bars_px": None,
                           "pillarbox_bars_px": None, "why": None}
    if not isinstance(rect, dict):
        out["why"] = NO_GEOMETRY
        return out
    w, h = _finite(rect.get("w")), _finite(rect.get("h"))
    if not w or not h or w <= 0 or h <= 0:
        out["why"] = NO_GEOMETRY
        return out
    scale = min(OUT_W / w, OUT_H / h)
    out["scale"] = round(scale, 4)
    out["letterbox_bars_px"] = round(max(0.0, (OUT_H - h * scale) / 2.0), 1)
    out["pillarbox_bars_px"] = round(max(0.0, (OUT_W - w * scale) / 2.0), 1)
    sub, band = _finite(subject_w), _finite(band_h)
    # UNMEASURED STAYS None. A region reported with a subject size of 0 reads as
    # "the speaker vanished", which is a much stronger claim than "nobody
    # measured the speaker".
    out["subject_w_out_px"] = None if sub is None else round(sub * scale, 1)
    out["band_h_out_px"] = None if band is None else round(band * scale, 1)
    return out
