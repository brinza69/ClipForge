"""What the burned caption actually lands on — Batch R6, second half.

Recorded, applied to nothing. `applied` is false, the export is byte-identical,
and the delivered caption is where `_caption_y` put it. This says what it covers.

WHAT ALREADY EXISTS, so this does not rebuild it. `captions.resolve_position`
has avoided keep-out rectangles since the clipper shipped, and
`panels_to_keep_out` supplies the detected game UI per clip. What no keep-out
has ever carried is the CREATOR'S FACE and the SOURCE'S OWN TEXT — a diagram, a
slide, a lower third — and those are the two the §R6 list names.

THE MISTAKE THIS FILE IS BUILT NOT TO REPEAT. `panels_to_keep_out` skips face
shots, and the comment there says why in the only way that counts: mapping a
panel through a face crop is arithmetic with no referent, the scale factor is
5-8x, and it shipped a report claiming "66% of the caption sits on detected game
UI" for a clip whose captions sit on the streamer's hoodie. A vision model
disagreed and the frames settled it.

So the same care, in the other direction. A face crop makes the face fill the
output; a game shot puts the same face in a small inset. One number for "the
caption is on the face" across a multi-shot edit would be that error again, told
the other way round. This reports PER SHOT, with the shot's own composition
beside it, and refuses to average.

EVIDENCE, NOT SEMANTICS. Codex's condition for this half, and it settles what
each signal is allowed to say: a face box says a face is there, not who it is;
a text box says the source has text there, not that it is a diagram or a
subtitle; browser chrome is a WARNING about publishing, never a verdict about
it. Every signal that is absent stays absent — it never becomes "clear".

THE EVIDENCE IS PER SHOT, IN OUTPUT PIXELS, AND THE CALLER MAPS IT. The first
version took one list of boxes for the whole clip and evaluated it against every
shot, which LOOKS per-shot and is not: the same rectangles produce the same
answer in a face crop and a wide game shot, so the report repeated one number
under N headings. That is the averaging error wearing a different hat.

Mapping source rectangles into the output frame is `captions.panels_to_keep_out`'s
job and it already carries the measurement about when NOT to — so this asks for
the answer rather than recomputing it, and refuses to invent one.

AND THE LETTERBOX COMES FROM `dynamic_geometry.canvas_size`. The first version
read a `frame` key off the shot. No shot has ever carried one: checked against
70 `fit` shots in the stored sidecars, zero have `frame` or `fit_rect`. The
branch was dead against real data and green against fixtures that invented the
key — a test passing on a contract production does not have.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["CONFLICTS", "UNAVAILABLE", "COMPOSITIONS", "band_for",
           "overlaps", "source_band", "placement_view"]

#: What the caption can land on, as a closed list.
ON_FACE = "over_face"
ON_UI = "over_ui_panel"
ON_SOURCE_TEXT = "over_source_text"
#: The letterboxed strip on a `fit` shot. Not a conflict by itself — it is the
#: one place a caption can sit without covering the source at all — but it is
#: only usable if the text stays readable against a blurred copy of the frame,
#: which nothing here measures. Reported so the decision is somebody's.
ON_LETTERBOX = "on_letterbox_band"
CONFLICTS: tuple[str, ...] = (ON_FACE, ON_UI, ON_SOURCE_TEXT, ON_LETTERBOX)

#: Why a check could not be made. Each one keeps its window in the denominator.
NO_FACES = "no_face_track"
NO_PANELS = "no_ui_detection"
NO_TEXT = "no_text_detection"
NO_SHOTS = "no_shot_list"
NO_CAPTION = "no_caption_plan"
#: The caller supplied no per-shot evidence at all. Distinct from a shot whose
#: entry is missing one signal: this is nobody having mapped anything.
NO_EVIDENCE = "no_per_shot_evidence"
UNAVAILABLE: tuple[str, ...] = (NO_FACES, NO_PANELS, NO_TEXT, NO_SHOTS,
                                NO_CAPTION, NO_EVIDENCE)

#: The two compositions `dynamic_geometry` emits. A `crop` fills the output with
#: a 9:16 window on the source; a `fit` puts the whole frame in the middle and
#: blurs the rest.
COMPOSITIONS: tuple[str, ...] = ("crop", "fit")

#: The caption is treated as a FULL-WIDTH strip, which is what
#: `panels_to_keep_out` already assumes and for the same reason: the crop is 9:16
#: out of 16:9, so horizontal position survives the mapping poorly and the
#: caption is centred and nearly full width anyway. Only the vertical extent is
#: honest, so only the vertical extent is compared.
#:
#: THE HEIGHT IS `captions.CAPTION_BOX_H_PCT`, imported rather than restated.
#: The first version put 0.12 here — a second definition of a number the
#: geometry already had at 0.10, chosen as "a conservative guard" — and a
#: conservative guard is exactly what an approximate band must not be when the
#: report says "the caption is covered". `_overlap_area` places the real box
#: with this constant; a report about a different box is a report about a
#: caption nobody burns.
from services.clipper.captions import CAPTION_BOX_H_PCT as CAPTION_BAND_PCT


def band_for(y_pct: float, height_pct: float = CAPTION_BAND_PCT) -> tuple[float, float]:
    """`(top, bottom)` of the caption strip, as fractions of the output height."""
    half = max(0.0, float(height_pct)) / 2.0
    centre = min(1.0, max(0.0, float(y_pct)))
    return max(0.0, centre - half), min(1.0, centre + half)


def _rows(rect: Any, height: float) -> tuple[float, float] | None:
    """A rectangle's vertical extent as fractions, or None if it is not one.

    None means UNREADABLE, and the caller has to treat it as such. It used to
    flow into `overlaps`, which answers 0.0 for None — so a malformed rectangle
    read as "covers nothing" and a shot full of them read as clear. That is the
    oldest mistake in this plan, arriving in a new place: an absence of
    measurement presented as a measurement of absence.
    """
    import math

    if not isinstance(rect, dict) or height <= 0:
        return None
    try:
        y = float(rect.get("y", rect.get("top", 0.0)))
        h = float(rect.get("h", rect.get("height", 0.0)))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(y) and math.isfinite(h)) or h <= 0 or y < 0:
        return None
    return max(0.0, y / height), min(1.0, (y + h) / height)


def overlaps(band: tuple[float, float], rect: tuple[float, float] | None) -> float:
    """How much of the caption band a rectangle covers, 0..1.

    A SHARE OF THE CAPTION, not of the rectangle. "The caption is 60% covered"
    and "the panel covers 3% of its own area" are different sentences, and only
    the first is about whether anybody can read it.
    """
    if rect is None:
        return 0.0
    top = max(band[0], rect[0])
    bottom = min(band[1], rect[1])
    span = band[1] - band[0]
    if span <= 0 or bottom <= top:
        return 0.0
    return min(1.0, (bottom - top) / span)


def source_band(src_w: int, src_h: int) -> tuple[float, float] | None:
    """Where the SOURCE frame sits on a `fit` shot, as fractions of the output.

    From `dynamic_geometry.canvas_size`, which is the function the renderer pads
    with — not from a key on the shot. The first version read `shot["frame"]`,
    and no shot has ever carried one: 70 `fit` shots in the stored sidecars, zero
    with `frame` or `fit_rect`. The branch was dead against real data and green
    against fixtures that invented the key.

    None when the source is already 9:16 or narrower: there is no letterbox, so
    there is no band to sit on.
    """
    from services.clipper.dynamic_geometry import canvas_size

    if src_w <= 0 or src_h <= 0:
        return None
    _canvas_w, canvas_h, offset = canvas_size(int(src_w), int(src_h))
    if canvas_h <= 0 or offset <= 0:
        return None
    return offset / float(canvas_h), (canvas_h - offset) / float(canvas_h)


def _shot_view(shot: dict, band: tuple[float, float], *, out_h: int,
               evidence_for: dict | None,
               frame: tuple[float, float] | None) -> dict:
    """What the caption covers in ONE shot, from THAT shot's own evidence.

    `evidence_for` carries the rectangles as they land in the OUTPUT frame for
    this shot, mapped by the caller. One list reused across every shot would
    give the same answer in a face crop and a wide game shot, which is the
    averaging error told per-shot.

    A `fit` shot puts the whole source frame in the middle of the output and
    blurs the rest, so a caption below the frame sits on the blurred band and
    covers nothing of the source. A `crop` fills the output, so everything the
    caption covers is source.
    """
    composition = str(shot.get("composition") or "")
    found: list[str] = []
    measured: dict[str, float] = {}
    unavailable: list[str] = []

    for name, key, missing in ((ON_FACE, "faces", NO_FACES),
                               (ON_UI, "panels", NO_PANELS),
                               (ON_SOURCE_TEXT, "text", NO_TEXT)):
        rects = (evidence_for or {}).get(key)
        if rects is None:
            unavailable.append(missing)
            continue
        rows = [_rows(r, out_h) for r in rects]
        if any(row is None for row in rows):
            # A rectangle nobody can read makes the whole signal unavailable for
            # this shot. Skipping it and reporting the rest would answer "the
            # caption covers nothing" out of a list that was partly unreadable.
            unavailable.append(missing)
            continue
        share = max((overlaps(band, row) for row in rows), default=0.0)
        measured[name] = round(share, 3)
        if share > 0:
            found.append(name)

    if composition == "fit" and frame is not None:
        outside = 1.0 - overlaps(band, frame)
        if outside > 0:
            found.append(ON_LETTERBOX)
            measured[ON_LETTERBOX] = round(outside, 3)

    return {"index": shot.get("index"), "composition": composition,
            "conflicts": found, "evidence": measured,
            "unavailable": unavailable}


def placement_view(*, y_pct: float | None, shots: Sequence[dict] | None,
                   evidence: Sequence[dict] | None = None,
                   out_h: int = 1920, src_w: int = 0, src_h: int = 0) -> dict:
    """`caption_placement_v1` for one clip. Recorded, applied to nothing.

    `evidence` is ONE ENTRY PER SHOT, aligned by position, each
    `{"faces": [...] | None, "panels": [...] | None, "text": [...] | None}` in
    OUTPUT pixels. A shorter list is not padded and a missing entry is not an
    empty one: the shots it does not cover come back unavailable.

    PER SHOT, and deliberately not summarised into one number. A face crop makes
    the face fill the output while a game shot puts the same face in a small
    inset, so "the caption is on the face" across a multi-shot edit is the same
    error `panels_to_keep_out` already had to be taught not to make. The worst
    shot is named; the average is not offered.
    """
    unavailable: list[str] = []
    if y_pct is None:
        unavailable.append(NO_CAPTION)
    if not shots:
        unavailable.append(NO_SHOTS)
    if evidence is None:
        unavailable.append(NO_EVIDENCE)

    out: dict[str, Any] = {
        "schema": "caption_placement_v1",
        "scope": "what_the_burned_caption_covers_per_shot",
        "y_pct": y_pct,
        "band_pct": CAPTION_BAND_PCT,
        "unavailable": unavailable,
        "shots": [],
        "conflicts": [],
        "worst": None,
        # Never true here. The delivered caption is where `_caption_y` put it,
        # and this batch does not move it.
        "applied": False,
    }
    if y_pct is None or not shots:
        return out

    band = band_for(float(y_pct))
    out["band"] = [round(band[0], 4), round(band[1], 4)]
    frame = source_band(src_w, src_h)
    out["source_band"] = (None if frame is None
                          else [round(frame[0], 4), round(frame[1], 4)])
    rows = [s for s in shots if isinstance(s, dict)]
    views = [_shot_view(s, band, out_h=out_h,
                        evidence_for=(evidence[i] if evidence is not None
                                      and i < len(evidence) else None),
                        frame=frame)
             for i, s in enumerate(rows)]
    out["shots"] = views
    out["conflicts"] = sorted({c for v in views for c in v["conflicts"]})
    out["unavailable"] = sorted(set(unavailable)
                                | {u for v in views for u in v["unavailable"]})
    scored = [(max(v["evidence"].values(), default=0.0), v) for v in views]
    if scored:
        worst = max(scored, key=lambda p: p[0])
        # The WORST shot, named, rather than a mean over shots that frame
        # different things. An average across a face crop and a wide game shot
        # is a number about neither.
        out["worst"] = {"share": round(worst[0], 3), **worst[1]}
    return out
