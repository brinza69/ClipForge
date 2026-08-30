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
read a `frame` key off the shot. No shot has ever carried one: 161 `fit` shots,
all of them inside the 58 sidecars of the pilot corpus, 27 of which contain at
least one — and zero with `frame` or `fit_rect`. The branch was dead against
real data and green against fixtures that invented the key — a test passing on a
contract production does not have.

(The first count written here was 70, off a file list sliced to its first 60
entries; the second said "101 sidecars", which is every sidecar on disk rather
than the corpus the figure belongs to. The conclusion held both times and the
denominator did not.)

THREE ANSWERS, NOT TWO. `conflicts` is what was measured, `unavailable` is what
nobody supplied, and `refused` is what somebody supplied wrongly. The first
version had only the first two, so a NaN caption height reported "nobody set
one" and a shot whose evidence was the number 3 reported all three signals as
merely missing — with a `worst` computed over it. A record that could not be
read must not take part in any count, and must not read as a clean one.

AND `share` IS A UNION, NOT A MAXIMUM. The occluded fraction of the caption,
across every box of every signal. Per-signal maxima called a shot with one 60%
face worse than a shot with a face over one half and text over the other — 79%
of the caption unreadable. The letterbox is excluded on purpose: it occludes
nothing of the source. That is NOT a claim that the text there is readable — the
strip is a blurred copy of the frame, not black, and §R6 wants a contrast
measurement before anything is placed on it. Neither an occlusion nor a pass.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.clipper.caption_placement_vocab import (  # noqa: F401
    BAD_CAPTION_Y,
    BAD_COMPOSITION,
    BAD_EVIDENCE,
    BAD_EVIDENCE_LIST,
    BAD_OUT_H,
    BAD_RECTS,
    BAD_SHOT,
    BAD_SHOTS,
    BAD_SOURCE_SIZE,
    COMPOSITIONS,
    CONFLICTS,
    NO_CAPTION,
    NO_COMPOSITION,
    NO_EVIDENCE,
    NO_FACES,
    NO_GEOMETRY,
    NO_PANELS,
    NO_SHOTS,
    NO_TEXT,
    ON_FACE,
    ON_LETTERBOX,
    ON_SOURCE_TEXT,
    ON_UI,
    REFUSALS,
    UNAVAILABLE,
)


__all__ = ["CONFLICTS", "UNAVAILABLE", "REFUSALS", "COMPOSITIONS", "band_for",
           "overlaps", "covered", "source_band", "placement_view"]


#: The caption is treated as a FULL-WIDTH strip, which is what
#: `panels_to_keep_out` already assumes and for the same reason: the crop is 9:16
#: out of 16:9, so horizontal position survives the mapping poorly and the
#: caption is centred and nearly full width anyway. Only the vertical extent is
#: honest, so only the vertical extent is compared.
#:
#: THE HEIGHT IS `captions.CAPTION_BOX_H_PCT`, imported rather than restated.
#: The first version put 0.12 here — a second definition of a number the
#: geometry already had at 0.10 — and two numbers for one box is how a report
#: ends up describing a caption nobody burns.
#:
#: It is the CANONICAL COLLISION geometry, not the pixel-perfect ASS rectangle:
#: its own comment calls it a conservative approximation, and libass lays out
#: the real text from the font, the wrap and the line count. So every overlap
#: here is against the box the avoidance logic uses, which is the right thing to
#: agree with and the wrong thing to call exact.
from services.clipper.captions import CAPTION_BOX_H_PCT as CAPTION_BAND_PCT


def _usable_pct(value: Any) -> bool:
    """Whether a fraction of the frame is one: a finite number in 0..1."""
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and 0.0 <= float(value) <= 1.0


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


def _usable_size(value: Any) -> bool:
    """Whether a pixel dimension is one: a finite, positive real number."""
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def source_band(src_w: int, src_h: int) -> tuple[tuple[float, float] | None, bool]:
    """`(where the source frame sits on a `fit` shot, whether that is known)`.

    From `dynamic_geometry.canvas_size`, which is the function the renderer pads
    with — not from a key on the shot. The first version read `shot["frame"]`,
    and no shot has ever carried one: 161 `fit` shots, all of them in the 58
    sidecars of the pilot corpus, 27 of which contain at least one — and zero
    with `frame` or `fit_rect`. The branch was dead against real data and green
    against fixtures that invented the key.

    TWO ANSWERS FOR None, WHICH IS WHY THERE IS A SECOND RETURN VALUE. `(None,
    True)` is "measured, and there is no letterbox" — a source already 9:16 or
    narrower fills the output. `(None, False)` is "nobody supplied the source
    dimensions", and a `fit` shot with unknown geometry cannot be said to have a
    band or to lack one. Collapsing them read a missing width as an upright
    source and reported zero conflicts with zero unavailable.
    """
    from services.clipper.dynamic_geometry import canvas_size

    # NOT A COMPARISON AGAINST AN UNKNOWN TYPE. `src_w <= 0` on a string raised
    # `TypeError` out of a function that already had a word for this case: its
    # own `(None, False)` means "nobody supplied the dimensions". The code
    # crashed instead of using the answer it had.
    if not (_usable_size(src_w) and _usable_size(src_h)):
        return None, False
    _canvas_w, canvas_h, offset = canvas_size(int(src_w), int(src_h))
    if canvas_h <= 0:
        return None, False
    if offset <= 0:
        return None, True
    return (offset / float(canvas_h), (canvas_h - offset) / float(canvas_h)), True


def covered(band: tuple[float, float],
            rects: Sequence[tuple[float, float] | None]) -> float:
    """How much of the caption band is covered by ANY of the rectangles, 0..1.

    A UNION, not a maximum. Measured on a real counter-example: one shot where a
    face covers 60.4%, and another where a face covers 39.6% and text covers a
    DIFFERENT 39.6% — 79.2% of the caption together. Taking the largest single
    box, then the largest single signal, called the first shot the worse one.
    "How much of the caption can nobody read" is a question about the union of
    what is on top of it.
    """
    spans = sorted((max(band[0], r[0]), min(band[1], r[1]))
                   for r in rects if r is not None
                   and min(band[1], r[1]) > max(band[0], r[0]))
    width = band[1] - band[0]
    if width <= 0 or not spans:
        return 0.0
    total = 0.0
    top, bottom = spans[0]
    for start, end in spans[1:]:
        if start > bottom:
            total += bottom - top
            top, bottom = start, end
        else:
            bottom = max(bottom, end)
    total += bottom - top
    return min(1.0, total / width)


def _shot_view(shot: dict, band: tuple[float, float], *, out_h: int,
               evidence_for: dict | None,
               frame: tuple[float, float] | None,
               frame_known: bool) -> dict:
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
    # COMPARED EXACTLY, and only tested for emptiness after stripping. An
    # earlier version stripped before comparing, which accepted `"fit "` while
    # refusing `"CROP"` — a closed list with a private spelling rule is two
    # rules, and the second one is undocumented.
    composition = str(shot.get("composition") or "")
    found: list[str] = []
    measured: dict[str, float] = {}
    unavailable: list[str] = []
    refused_here: list[str] = []

    boxes: list[tuple[float, float] | None] = []
    if evidence_for is not None and not isinstance(evidence_for, dict):
        # REFUSED, not unmeasured. `(evidence_for or {}).get(key)` turned a
        # string or a number into "every signal missing" and then let a `worst`
        # be computed over the result.
        return {"index": shot.get("index"), "composition": composition,
                "conflicts": [], "evidence": {}, "unavailable": [],
                "refused": [BAD_EVIDENCE], "share": None,
                "share_complete": False}

    for name, key, missing in ((ON_FACE, "faces", NO_FACES),
                               (ON_UI, "panels", NO_PANELS),
                               (ON_SOURCE_TEXT, "text", NO_TEXT)):
        rects = (evidence_for or {}).get(key)
        if rects is None:
            unavailable.append(missing)
            continue
        if isinstance(rects, (str, bytes)) or not isinstance(rects, Sequence):
            # `for r in rects` raised on anything that is not a sequence. A
            # signal that arrived as the wrong shape is REFUSED, not iterated
            # and not quietly filed as missing.
            refused_here.append(BAD_RECTS)
            continue
        rows = [_rows(r, out_h) for r in rects]
        if any(row is None for row in rows):
            # A rectangle nobody can read makes the whole signal unavailable for
            # this shot. Skipping it and reporting the rest would answer "the
            # caption covers nothing" out of a list that was partly unreadable.
            unavailable.append(missing)
            continue
        share = covered(band, rows)
        measured[name] = round(share, 3)
        boxes.extend(rows)
        if share > 0:
            found.append(name)

    if not composition.strip():
        # NOBODY SAID. The face and text signals do not depend on how the shot
        # is composed and stay measured; only the letterbox question loses its
        # answer, so only that becomes unavailable.
        unavailable.append(NO_COMPOSITION)
    elif composition not in COMPOSITIONS:
        # SOMEBODY SAID SOMETHING NOBODY DEFINED, which is not the same thing
        # and is not a `crop`. Falling through to the `crop` branch answered the
        # letterbox question for a composition whose letterbox is undefined.
        return {"index": shot.get("index"), "composition": composition,
                "conflicts": [], "evidence": {}, "unavailable": [],
                "refused": [BAD_COMPOSITION], "share": None,
                "share_complete": False}

    if composition == "fit":
        if not frame_known:
            # A `fit` shot whose source dimensions nobody supplied cannot be
            # said to have a letterbox or to lack one.
            unavailable.append(NO_GEOMETRY)
        elif frame is not None:
            outside = 1.0 - overlaps(band, frame)
            if outside > 0:
                found.append(ON_LETTERBOX)
                measured[ON_LETTERBOX] = round(outside, 3)

    return {"index": shot.get("index"), "composition": composition,
            "conflicts": found, "evidence": measured,
            "unavailable": unavailable,
            "refused": sorted(set(refused_here)),
            # WHETHER EVERY SIGNAL CONTRIBUTED TO IT. `share` is a union over
            # the signals that were MEASURED, so a shot with faces unavailable
            # and text at 0.4 reported 0.4 — which reads as "40% of the caption
            # is covered" when the honest answer is "at least 40%, and nobody
            # looked at the rest". An incomplete union presented as an exact one
            # is the oldest mistake in this plan wearing the newest hat.
            #
            # AND WITH NOTHING MEASURED AT ALL, `share` IS None RATHER THAN 0.0.
            # A union over an empty set is zero as arithmetic and "nobody
            # looked" as a fact, and a shot that measured nothing was being
            # offered as the worst case at 0.0.
            "share_complete": not unavailable and not refused_here,
            # HOW MUCH OF THE CAPTION IS COVERED, as the union ACROSS SIGNALS
            # and not the largest of them. A face over one half of the caption
            # and text over the other is a caption nobody can read, and the
            # per-signal maxima called that shot better than a single 60% face.
            #
            # `ON_LETTERBOX` IS DELIBERATELY NOT IN IT: it occludes nothing
            # of the source, and the old `max(evidence.values())` let "sits low
            # on the padding" outrank "nobody can read it". That is not a claim
            # that the text is legible there — the strip is a blurred copy of
            # the frame and nothing here measures contrast against it.
            "share": (round(covered(band, boxes), 3) if measured else None)}


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
    # A caption height that is not a number in 0..1 is not a caption height. It
    # used to reach `band_for`, which clamps — so a NaN, a string or a 7.0 came
    # back as a band somewhere plausible and every overlap below it was measured
    # against a caption nobody could place.
    refused: list[str] = []
    # THE PUBLIC ENTRY POINT TAKES WHATEVER IT IS GIVEN. Every one of these
    # raised: `enumerate(7)` on a shot list that is an int, `len(evidence)` on
    # one, `int(src_w)` on a string, a division by an `out_h` of `"1920"`. This
    # is a shadow signal whose entire contract is that its absence changes
    # nothing, and a signal that takes the pipeline down with it has broken that
    # contract in the loudest way there is.
    if not _usable_size(out_h):
        refused.append(BAD_OUT_H)
    if shots is not None and (isinstance(shots, (str, bytes))
                              or not isinstance(shots, Sequence)):
        refused.append(BAD_SHOTS)
        shots = None
    if evidence is not None and (isinstance(evidence, (str, bytes))
                                 or not isinstance(evidence, Sequence)):
        refused.append(BAD_EVIDENCE_LIST)
        evidence = None
    if (src_w or src_h) and not (_usable_size(src_w) and _usable_size(src_h)):
        # Not fatal — `source_band` already answers "nobody supplied them" — but
        # a dimension that is not a number is a different fact from one that is
        # missing, and the two must not spell the same.
        refused.append(BAD_SOURCE_SIZE)
    if y_pct is not None and not _usable_pct(y_pct):
        # REFUSED, and not also reported as absent: "nobody set a caption
        # height" out of a record where somebody set a bad one is a different
        # and wrong sentence.
        y_pct = None
        refused.append(BAD_CAPTION_Y)
    elif y_pct is None:
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
        "refused": refused,
        "shots": [],
        "conflicts": [],
        "worst": None,
        # Never true here. The delivered caption is where `_caption_y` put it,
        # and this batch does not move it.
        "applied": False,
    }
    if y_pct is None or not shots or not _usable_size(out_h):
        return out

    band = band_for(float(y_pct))
    out["band"] = [round(band[0], 4), round(band[1], 4)]
    frame, frame_known = source_band(src_w, src_h)
    out["source_band"] = (None if frame is None
                          else [round(frame[0], 4), round(frame[1], 4)])
    out["source_band_known"] = frame_known
    # ALIGNED WITH THE ORIGINAL LIST. Filtering the non-dicts out first and then
    # enumerating the survivors shifted every later shot onto somebody else's
    # evidence — one bad entry and the whole report is about the wrong frames,
    # silently and precisely.
    views = []
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            # A refusal with its own row. Dropping it silently shifted nothing
            # any more, but it still removed an entry from the corpus without
            # saying so.
            views.append({"index": i, "composition": None, "conflicts": [],
                          "evidence": {}, "unavailable": [],
                          "refused": [BAD_SHOT], "share": None,
                          "share_complete": False})
            continue
        views.append(_shot_view(
            shot, band, out_h=out_h,
            evidence_for=(evidence[i] if evidence is not None
                          and i < len(evidence) else None),
            frame=frame, frame_known=frame_known))
    out["shots"] = views
    out["refused"] = sorted(set(refused)
                            | {r for v in views for r in v["refused"]})
    out["conflicts"] = sorted({c for v in views for c in v["conflicts"]})
    out["unavailable"] = sorted(set(unavailable)
                                | {u for v in views for u in v["unavailable"]})
    # A REFUSED SHOT TAKES PART IN NOTHING. A corrupt record with a `share` of
    # zero was being offered as the worst case when it was the only one.
    scored = [(v["share"], v) for v in views if v["share"] is not None]
    if scored:
        worst = max(scored, key=lambda p: p[0])
        # The WORST shot, named, rather than a mean over shots that frame
        # different things. An average across a face crop and a wide game shot
        # is a number about neither.
        out["worst"] = {**worst[1], "share": round(worst[0], 3)}
    # AND WHETHER THE WORST CASE IS A MEASUREMENT OR A FLOOR. A reader who sees
    # a number and no qualifier beside it will treat it as the answer.
    out["worst_share_complete"] = (None if out["worst"] is None
                                   else bool(out["worst"]["share_complete"]))
    return out
