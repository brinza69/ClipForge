"""Which observed text is DIALOGUE — labelled per group, never per clip.

`source_caption_observation` finds text. Whether that text is the source's
subtitle track, a watermark, a diagram label or a piece of interface is a
different question, and `source_captions` separates them only by width, on
thresholds chosen with the answer visible on four sources. So the confirmation
is a person's or an agent's, and this is the shape it takes.

PER GROUP, AND THAT IS THE WHOLE DESIGN. One frame can carry a subtitle AND a
watermark at once — `pilot6b38`'s watermark is in 13 of 14 frames, more
persistent than go ghost's real caption track. A single `dialogue: true` for a
clip, or even for a frame, would validate the wrong boxes along with the right
ones, and every region built afterwards would be sized to hold a logo.

THREE LABELS AND THE DEFAULT IS NOT ONE OF THE OTHER TWO. A box nobody labelled
is `uncertain`. Not dialogue, because that would let unconfirmed text size a
framing; and not non-dialogue, because that would let unconfirmed text be
CROPPED AWAY. Both directions are wrong and the second is the quieter one.

WHAT AN EXCLUSION DOES NOT MEAN. Labelling a group `non_dialogue` removes it
from the caption measurement. It does not establish that the text may be cut
without editorial consequence — `b23c14c41495`'s last shot reports a 1188 px
band that is certainly not a subtitle, and it is certainly also not something to
crop away without looking. Excluded groups stay in the inventory with their
label and their reason, counted on their own row.

AND THE TRANSCRIPT IS NOT THE ARBITER. It can help a person decide, but text
that does not match the transcript is not thereby proved not to be dialogue:
the source's own subtitles may be a translation, a paraphrase, or simply better
than our ASR. Provenance records who decided and on what; it does not record a
comparison as a decision.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["DIALOGUE", "NON_DIALOGUE", "UNCERTAIN", "LABELS", "AGENT", "HUMAN",
           "BY", "ALL", "apply", "dialogue_boxes", "inventory",
           "CONFIRMED", "OTHER_TEXT", "NO_TEXT", "UNEVIDENCED"]

DIALOGUE = "dialogue"
NON_DIALOGUE = "non_dialogue"
UNCERTAIN = "uncertain"
LABELS: tuple[str, ...] = (DIALOGUE, NON_DIALOGUE, UNCERTAIN)

AGENT = "agent"
HUMAN = "human"
BY: tuple[str, ...] = (AGENT, HUMAN)

#: A label may name every box in its sample rather than list indices.
ALL = "all"

#: The four rows an inventory reports, kept apart because collapsing any two of
#: them is how "nobody looked" becomes "there was nothing there".
CONFIRMED = "confirmed_dialogue"
OTHER_TEXT = "other_text_only"
NO_TEXT = "no_text_observed"
UNEVIDENCED = "unevidenced"


def _finite(value: Any) -> float | None:
    try:
        got = float(value)
    except (TypeError, ValueError):
        return None
    return None if got != got else got


def _targets(label: Any) -> tuple[float, Any, str, str] | None:
    """`(at, boxes, label, by)` from one label record, or None if unusable."""
    if not isinstance(label, dict):
        return None
    at = _finite(label.get("at"))
    what = label.get("label")
    by = label.get("by")
    if at is None or what not in LABELS or by not in BY:
        return None
    boxes = label.get("boxes", ALL)
    if boxes != ALL:
        if (not isinstance(boxes, Sequence) or isinstance(boxes, (str, bytes))
                or not boxes
                or not all(isinstance(i, int) and not isinstance(i, bool)
                           and i >= 0 for i in boxes)):
            return None
    return at, boxes, what, by


def apply(observation: Any, labels: Any, *, tolerance: float = 0.001) -> dict:
    """Attach a label to every observed box. Never raises.

    A label points at a sample by its time — `t_decoded` where the observation
    has one, because that is where the frame actually was — and at boxes by
    index, or `"all"`. Matching on the DECODED time rather than the requested
    one matters: `cv2` seeks approximately, and a label written against the
    time somebody asked for would attach to a different frame.

    Every box starts `uncertain` and only an explicit label moves it. A label
    that matches no sample is reported in `unmatched` rather than dropped: a
    labelling pass whose targets have silently missed is indistinguishable from
    one nobody ran.
    """
    out: dict[str, Any] = {"schema": "clipper_caption_labels_v1",
                           "samples": [], "unmatched": [], "counts":
                           {DIALOGUE: 0, NON_DIALOGUE: 0, UNCERTAIN: 0},
                           "why": None}
    rows = (observation or {}).get("samples") if isinstance(observation, dict) else None
    if not isinstance(rows, Sequence):
        out["why"] = "there_is_no_observation_to_label"
        return out

    parsed: list[tuple[float, Any, str, str]] = []
    for raw in (labels if isinstance(labels, Sequence)
                and not isinstance(labels, (str, bytes)) else []):
        got = _targets(raw)
        if got is None:
            # A LABEL NOBODY CAN READ IS NOT AN ABSENT LABEL. Dropping it
            # quietly would leave the boxes it meant to mark as `uncertain`
            # while the labeller believes they were confirmed.
            out["unmatched"].append({"label": raw, "why": "unreadable"})
            continue
        parsed.append(got)

    used = [False] * len(parsed)
    for row in rows:
        if not isinstance(row, dict):
            continue
        boxes = row.get("boxes")
        when = row.get("t_decoded")
        when = _finite(row.get("t_requested") if when is None else when)
        marks: list[dict] = []
        if isinstance(boxes, Sequence) and not isinstance(boxes, (str, bytes)):
            marks = [{"box": i, "label": UNCERTAIN, "by": None}
                     for i in range(len(boxes))]
            for k, (at, want, what, by) in enumerate(parsed):
                if when is None or abs(at - when) > tolerance:
                    continue
                used[k] = True
                targets = (range(len(marks)) if want == ALL
                           else [i for i in want if i < len(marks)])
                if want != ALL and any(i >= len(marks) for i in want):
                    out["unmatched"].append(
                        {"at": at, "why": "box_index_out_of_range"})
                for i in targets:
                    marks[i] = {"box": i, "label": what, "by": by}
        for mark in marks:
            out["counts"][mark["label"]] += 1
        out["samples"].append({"t": when, "marks": marks,
                               "refused": row.get("refused")})
    for k, (at, _w, what, _by) in enumerate(parsed):
        if not used[k]:
            out["unmatched"].append({"at": at, "label": what,
                                     "why": "no_sample_at_this_time"})
    return out


def dialogue_boxes(observation: Any, labelled: Any, t0: Any, t1: Any) -> dict:
    """The CONFIRMED-dialogue boxes inside `[t0, t1)`, and what was left out.

    This is what a region may be built from. `excluded` and `unlabelled` come
    back beside it — never instead of it — because a region built from three
    boxes out of eleven is a different object from one built from eleven, and
    the caller has to be able to see which it was handed.
    """
    out: dict[str, Any] = {"boxes": [], "samples": 0, "dialogue": 0,
                           "excluded": 0, "unlabelled": 0, "why": None}
    lo, hi = _finite(t0), _finite(t1)
    if lo is None or hi is None or hi <= lo:
        out["why"] = "the_interval_is_not_two_ordered_finite_seconds"
        return out
    rows = (observation or {}).get("samples") if isinstance(observation, dict) else None
    marks = (labelled or {}).get("samples") if isinstance(labelled, dict) else None
    if not isinstance(rows, Sequence) or not isinstance(marks, Sequence):
        out["why"] = "there_is_no_labelled_observation_to_read"
        return out
    by_time = {m.get("t"): m for m in marks if isinstance(m, dict)}
    for row in rows:
        if not isinstance(row, dict):
            continue
        when = row.get("t_decoded")
        when = _finite(row.get("t_requested") if when is None else when)
        if when is None or not (lo <= when < hi):
            continue
        boxes = row.get("boxes")
        if not isinstance(boxes, Sequence) or isinstance(boxes, (str, bytes)):
            continue
        out["samples"] += 1
        found = by_time.get(when) or {}
        labels = {m["box"]: m["label"] for m in (found.get("marks") or [])
                  if isinstance(m, dict) and "box" in m}
        for i, box in enumerate(boxes):
            # DEFAULT UNCERTAIN, not dialogue. A box the labeller never reached
            # may not size a framing.
            what = labels.get(i, UNCERTAIN)
            if what == DIALOGUE:
                out["boxes"].append(box)
                out["dialogue"] += 1
            elif what == NON_DIALOGUE:
                out["excluded"] += 1
            else:
                out["unlabelled"] += 1
    return out


def inventory(shots: Any, observation: Any, labelled: Any,
              start: float = 0.0) -> dict:
    """Every shot in one of four rows, with none of them folded together.

    §A's second point: keep all the shots, and report confirmed dialogue, other
    text, no text observed, and unevidenced SEPARATELY. Two frames read is not
    temporal coverage, and a shot with text nobody labelled is not a shot with
    no text.
    """
    out: dict[str, Any] = {"schema": "clipper_caption_inventory_v1",
                           "shots": 0, "rows": [],
                           "counts": {CONFIRMED: 0, OTHER_TEXT: 0,
                                      NO_TEXT: 0, UNEVIDENCED: 0},
                           "why": None}
    if not isinstance(shots, Sequence) or isinstance(shots, (str, bytes)):
        out["why"] = "there_are_no_shots_to_inventory"
        return out
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        t0, t1 = _finite(shot.get("t0")), _finite(shot.get("t1"))
        if t0 is None or t1 is None or t1 <= t0:
            continue
        out["shots"] += 1
        got = dialogue_boxes(observation, labelled, start + t0, start + t1)
        seen = got["dialogue"] + got["excluded"] + got["unlabelled"]
        if not got["samples"]:
            row = UNEVIDENCED
        elif got["dialogue"]:
            row = CONFIRMED
        elif seen:
            # Text was there and none of it is confirmed dialogue. NOT the same
            # as an empty frame, and the difference decides whether a framing
            # may be sized to leave it out.
            row = OTHER_TEXT
        else:
            row = NO_TEXT
        out["counts"][row] += 1
        out["rows"].append({"shot": shot.get("index"), "row": row,
                            "samples": got["samples"],
                            "dialogue": got["dialogue"],
                            "excluded": got["excluded"],
                            "unlabelled": got["unlabelled"]})
    if not out["shots"]:
        out["why"] = "there_are_no_shots_to_inventory"
    return out
