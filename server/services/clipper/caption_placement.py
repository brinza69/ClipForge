"""What the burned caption actually lands on — Batch R6, second half.

Recorded, applied to nothing. `applied` is false, the export is byte-identical,
and the delivered caption is where `_caption_y` put it. This says what it covers.

WHAT ALREADY EXISTS, so this does not rebuild it. `captions.resolve_position`
has avoided keep-out rectangles since the clipper shipped, and
`panels_to_keep_out` supplies the detected game UI per clip. What no keep-out
has ever carried is A FACE and the SOURCE'S OWN TEXT — a diagram, a slide, a
lower third — and those are the two the §R6 list names. A face box, not the
creator's face: this module's own rule is that a face box says a face is there
and not who it is, and calling it the creator's here contradicted that two
paragraphs before stating it.

THE MISTAKE THIS FILE IS BUILT NOT TO REPEAT. `panels_to_keep_out` skips face
shots, and the comment there says why in the only way that counts: mapping a
panel through a face crop is arithmetic with no referent, the scale factor is
5-8x, and it shipped a report claiming "66% of the caption sits on detected game
UI" for a clip whose captions sit on the streamer's hoodie. A vision model
disagreed and the frames settled it.

SO IT IS NOT THE MAPPER FOR THIS. `panels_to_keep_out` maps UI panels and skips
face shots ON PURPOSE, which means it cannot supply the face evidence or the
source-text evidence this module reports on — those need their own mapper, and
there is no generic one. An earlier version of this docstring pointed at it as
if there were.

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

THREE ANSWERS, NOT TWO. `lands_on` is what was measured, `unavailable` is what
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

from services.clipper.caption_placement_geom import (  # noqa: F401
    CAPTION_BAND_PCT, _rows, _usable_pct, _usable_size, band_for, covered,
    overlaps, source_band)
from services.clipper.caption_placement_vocab import (  # noqa: F401
    BAD_CAPTION_Y,
    BAD_COMPOSITION,
    BAD_EVIDENCE,
    BAD_EVIDENCE_LIST,
    BAD_OUT_H,
    BAD_RECTS,
    BAD_SHOT,
    BAD_SHOTS,
    MORE_EVIDENCE_THAN_SHOTS,
    BAD_SOURCE_SIZE,
    COMPOSITIONS,
    LANDS_ON,
    OCCLUSIONS,
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


__all__ = ["LANDS_ON", "OCCLUSIONS", "UNAVAILABLE", "REFUSALS",
           "COMPOSITIONS", "band_for", "overlaps", "covered", "source_band",
           "placement_view"]


#: The signals `share` is a union of, one per member of `OCCLUSIONS`.
#: `NO_GEOMETRY` and `NO_COMPOSITION` are deliberately NOT here: they are about
#: the letterbox, which is in `LANDS_ON` and not in `OCCLUSIONS`, so they cannot
#: make a coverage incomplete.
_SHARE_SIGNALS: tuple[str, ...] = (NO_FACES, NO_PANELS, NO_TEXT)


def _missing_for_share(unavailable: Sequence[str],
                       refused: Sequence[str]) -> list[str]:
    """Which of `share`'s own inputs were not measured."""
    return ([u for u in unavailable if u in _SHARE_SIGNALS]
            + [r for r in refused if r == BAD_RECTS])


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
    off_source: float | None = None
    if evidence_for is not None and not isinstance(evidence_for, dict):
        # REFUSED, not unmeasured. `(evidence_for or {}).get(key)` turned a
        # string or a number into "every signal missing" and then let a `worst`
        # be computed over the result.
        return {"index": shot.get("index"), "composition": composition,
                "lands_on": [], "evidence": {}, "unavailable": [],
                "refused": [BAD_EVIDENCE], "share": None,
                "share_complete": False, "off_source": None}

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
                "lands_on": [], "evidence": {}, "unavailable": [],
                "refused": [BAD_COMPOSITION], "share": None,
                "share_complete": False, "off_source": None}

    if composition == "fit":
        if not frame_known:
            # A `fit` shot whose source dimensions nobody supplied cannot be
            # said to have a letterbox or to lack one.
            unavailable.append(NO_GEOMETRY)
        elif frame is not None:
            # ITS OWN FIELD, not an entry in `evidence`. `evidence` and `share`
            # are the same thing at two granularities — per signal and unioned —
            # and putting a NON-occlusion in the same dict produced a shot
            # reading `evidence: {on_letterbox_band: 1.0}` beside `share: 0.0`.
            # Both were right and the pair was unreadable.
            off_source = round(1.0 - overlaps(band, frame), 3)
            if off_source > 0:
                found.append(ON_LETTERBOX)

    return {"index": shot.get("index"), "composition": composition,
            "lands_on": found, "evidence": measured,
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
            #
            # ON THE AXIS `share` IS ACTUALLY MADE OF. It was `not unavailable`,
            # which folds in `no_source_dimensions` and
            # `shot_does_not_say_how_it_is_composed` — both about the LETTERBOX,
            # which is not in `share` at all. A `fit` shot whose three occlusion
            # signals were all measured was being called incomplete because
            # nobody said how wide the source was.
            "share_complete": not _missing_for_share(unavailable, refused_here),
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
            "share": (round(covered(band, boxes), 3) if measured else None),
            # HOW MUCH OF THE CAPTION IS OFF THE SOURCE FRAME, on a `fit` shot.
            # None on a `crop`, which has no letterbox, and None when the source
            # dimensions are unknown — never 0.0, which would say the caption is
            # entirely over the picture.
            "off_source": off_source}


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
    # AND SETTING THEM TO None AFTERWARDS PUT THE REFUSAL STRAIGHT BACK INTO
    # `unavailable`, because `if not shots` below reads None as "there were
    # none". That is the same defect as the NaN caption height reporting
    # `no_caption_plan`, committed in the fix for it, three guards later. The
    # flags carry the state instead.
    shots_refused = shots is not None and (isinstance(shots, (str, bytes))
                                           or not isinstance(shots, Sequence))
    if shots_refused:
        refused.append(BAD_SHOTS)
        shots = None
    if evidence is not None and (isinstance(evidence, (str, bytes))
                                 or not isinstance(evidence, Sequence)):
        refused.append(BAD_EVIDENCE_LIST)
        evidence = None
        evidence_refused = True
    else:
        evidence_refused = False
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
    # MORE EVIDENCE THAN SHOTS IS A DISAGREEMENT, not a longer list. The extra
    # entries were sliced off in silence, so a caller whose idea of the edit had
    # more frames than the edit got a clean report about the frames they agreed
    # on. The one direction that IS legitimate is a SHORTER list — the shots it
    # does not cover come back unavailable, which the docstring has always said.
    if (isinstance(evidence, Sequence) and isinstance(shots, Sequence)
            and len(evidence) > len(shots)):
        refused.append(MORE_EVIDENCE_THAN_SHOTS)
    if not shots and not shots_refused:
        unavailable.append(NO_SHOTS)
    if evidence is None and not evidence_refused:
        unavailable.append(NO_EVIDENCE)

    out: dict[str, Any] = {
        "schema": "caption_placement_v1",
        "scope": "what_the_burned_caption_covers_per_shot",
        "y_pct": y_pct,
        "band_pct": CAPTION_BAND_PCT,
        "unavailable": unavailable,
        "refused": refused,
        "shots": [],
        "lands_on": [],
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
            views.append({"index": i, "composition": None, "lands_on": [],
                          "evidence": {}, "unavailable": [],
                          "refused": [BAD_SHOT], "share": None,
                          "share_complete": False, "off_source": None})
            continue
        views.append(_shot_view(
            shot, band, out_h=out_h,
            evidence_for=(evidence[i] if evidence is not None
                          and i < len(evidence) else None),
            frame=frame, frame_known=frame_known))
    out["shots"] = views
    out["refused"] = sorted(set(refused)
                            | {r for v in views for r in v["refused"]})
    out["lands_on"] = sorted({c for v in views for c in v["lands_on"]})
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
    # AND WHETHER THE WORST SHOT IS ESTABLISHED, WHICH IS A QUESTION ABOUT ALL
    # OF THEM. The first version reported the completeness of the WINNER, and a
    # complete 0.5 does not beat an incomplete 0.4 whose unmeasured signals
    # could carry it to 0.9. Every share that took part has to be a measurement
    # before the maximum over them names anybody — and a shot that was refused
    # outright has no share at all, so it cannot be ruled out either.
    out["worst_share_complete"] = (
        None if out["worst"] is None
        else bool(all(v["share_complete"] for v in views
                      if v["share"] is not None)
                  and not [v for v in views if v["share"] is None]))
    return out
