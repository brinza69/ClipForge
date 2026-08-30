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
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["CONFLICTS", "UNAVAILABLE", "COMPOSITIONS", "band_for",
           "overlaps", "placement_view"]

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
UNAVAILABLE: tuple[str, ...] = (NO_FACES, NO_PANELS, NO_TEXT, NO_SHOTS,
                                NO_CAPTION)

#: The two compositions `dynamic_geometry` emits. A `crop` fills the output with
#: a 9:16 window on the source; a `fit` puts the whole frame in the middle and
#: blurs the rest.
COMPOSITIONS: tuple[str, ...] = ("crop", "fit")

#: The caption is treated as a FULL-WIDTH strip, which is what
#: `panels_to_keep_out` already assumes and for the same reason: the crop is 9:16
#: out of 16:9, so horizontal position survives the mapping poorly and the
#: caption is centred and nearly full width anyway. Only the vertical extent is
#: honest, so only the vertical extent is compared.
CAPTION_BAND_PCT = 0.12


def band_for(y_pct: float, height_pct: float = CAPTION_BAND_PCT) -> tuple[float, float]:
    """`(top, bottom)` of the caption strip, as fractions of the output height."""
    half = max(0.0, float(height_pct)) / 2.0
    centre = min(1.0, max(0.0, float(y_pct)))
    return max(0.0, centre - half), min(1.0, centre + half)


def _rows(rect: Any, height: float) -> tuple[float, float] | None:
    """A rectangle's vertical extent as fractions, or None if it is not one."""
    if not isinstance(rect, dict) or height <= 0:
        return None
    try:
        y = float(rect.get("y", rect.get("top", 0.0)))
        h = float(rect.get("h", rect.get("height", 0.0)))
    except (TypeError, ValueError):
        return None
    if h <= 0:
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


def _shot_view(shot: dict, band: tuple[float, float], *, out_h: int,
               faces: Sequence[dict] | None, panels: Sequence[dict] | None,
               text: Sequence[dict] | None) -> dict:
    """What the caption covers in ONE shot. Never averaged with another.

    A `fit` shot puts the whole source frame in the middle of the output and
    blurs the rest, so a caption below the frame sits on the blurred band and
    covers nothing of the source. A `crop` fills the output, so everything the
    caption covers is source.
    """
    composition = str(shot.get("composition") or "")
    found: list[str] = []
    evidence: dict[str, float] = {}

    for name, rects, missing in ((ON_FACE, faces, NO_FACES),
                                 (ON_UI, panels, NO_PANELS),
                                 (ON_SOURCE_TEXT, text, NO_TEXT)):
        if rects is None:
            continue
        share = max((overlaps(band, _rows(r, out_h)) for r in rects), default=0.0)
        evidence[name] = round(share, 3)
        if share > 0:
            found.append(name)

    if composition == "fit":
        frame = _rows(shot.get("frame") or shot.get("fit_rect"), out_h)
        if frame is not None and overlaps(band, frame) < 1.0:
            found.append(ON_LETTERBOX)
            evidence[ON_LETTERBOX] = round(1.0 - overlaps(band, frame), 3)

    return {"index": shot.get("index"), "composition": composition,
            "conflicts": found, "evidence": evidence}


def placement_view(*, y_pct: float | None, shots: Sequence[dict] | None,
                   out_h: int = 1920,
                   faces: Sequence[dict] | None = None,
                   panels: Sequence[dict] | None = None,
                   text: Sequence[dict] | None = None) -> dict:
    """`caption_placement_v1` for one clip. Recorded, applied to nothing.

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
    for rects, missing in ((faces, NO_FACES), (panels, NO_PANELS),
                           (text, NO_TEXT)):
        if rects is None:
            unavailable.append(missing)

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
    views = [_shot_view(s, band, out_h=out_h, faces=faces, panels=panels,
                        text=text)
             for s in shots if isinstance(s, dict)]
    out["shots"] = views
    out["conflicts"] = sorted({c for v in views for c in v["conflicts"]})
    scored = [(max(v["evidence"].values(), default=0.0), v) for v in views]
    if scored:
        worst = max(scored, key=lambda p: p[0])
        # The WORST shot, named, rather than a mean over shots that frame
        # different things. An average across a face crop and a wide game shot
        # is a number about neither.
        out["worst"] = {"share": round(worst[0], 3), **worst[1]}
    return out
