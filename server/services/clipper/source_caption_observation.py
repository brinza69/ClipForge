"""Where the SOURCE's subtitle lines are, AT A TIME — batch A's first half.

`source_captions` answers a question about a whole file: does this source carry
burned subtitles. To decide per interval whether our own layer should be on, the
question changes shape — what text was on screen DURING this stretch, and where
— and the existing detector cannot answer it, because `_bands` aggregates every
box into ten horizontal bands and throws the times away.

So this keeps what that discards: every line's box, with the time it was seen,
before any aggregation. It reuses the same detector and the same thresholds. No
model change and no recalibration belong in this batch.

WHAT AN OBSERVATION HAS TO CARRY, and each of these is here because leaving it
out makes the record unusable rather than merely thinner:

  * the identity of the source and the dimensions of the image ANALYSED — the
    detector runs on the proxy, so a box means nothing without the frame it was
    measured in;
  * the time on the source's clock, and the time actually decoded rather than
    the time requested, because seeking is approximate;
  * the boxes of ALL the lines, not a band and not the widest one;
  * where the observation came from — a detector, an agent, or a person;
  * and which moments have no evidence, kept apart from the moments that were
    looked at and had no text.

A TEXT BOX IS NOT A SUBTITLE, and this module never says it is. `pilot6b38`'s
watermark is in 13 of 14 frames — MORE persistent than go ghost's real caption
track — and `source_captions` separates them only by width, on thresholds
chosen with the answer visible. So `dialogue` is three-valued and the detector
may never set it True: for the pilot batch a person or an agent confirms that
the boxes are dialogue rather than a watermark, a diagram label or an interface,
and `from_annotations` is how that confirmation enters in the same shape.

AND THE COVERAGE IS PART OF THE ANSWER. An interval nobody sampled is not an
interval with no subtitle in it. `coverage` reports what was looked at, so a
consumer can tell "no text was seen" from "nothing was looked at" — which is the
distinction every other module in this batch exists to protect.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["SCHEMA", "DETECTOR", "AGENT", "HUMAN", "PROVENANCES",
           "NO_DETECTOR", "NO_VIDEO", "FRAME_UNREADABLE", "BAD_TIMES",
           "observe", "from_annotations", "coverage", "disjoint", "correct",
           "CORRECTED"]

SCHEMA = "source_caption_observation_v1"

#: Who saw it. The detector may not assert `dialogue`; the other two may.
DETECTOR = "detector"
AGENT = "agent"
HUMAN = "human"
PROVENANCES: tuple[str, ...] = (DETECTOR, AGENT, HUMAN)

NO_DETECTOR = "no_text_detector_available"
NO_VIDEO = "the_video_could_not_be_opened"
FRAME_UNREADABLE = "the_frame_could_not_be_decoded"
BAD_TIMES = "the_times_asked_for_are_not_finite_seconds"
#: The model was given the frame and raised on it. Distinct from a frame that
#: could not be decoded: one is the material, the other is the detector.
DETECTOR_FAILED = "the_detector_raised_on_this_frame"


def _finite(value: Any) -> float | None:
    try:
        got = float(value)
    except (TypeError, ValueError):
        return None
    if got != got or got in (float("inf"), float("-inf")) or got < 0:
        return None
    return got


def _empty(video: Any, why: str, provenance: str = DETECTOR) -> dict:
    return {"schema": SCHEMA, "video": None if video is None else str(video),
            "image_w": None, "image_h": None, "provenance": provenance,
            "dialogue": None, "samples": [], "read": 0, "refused": 0,
            "refusals": [why] if why else [], "why": why}


def observe(video: Any, at: Sequence[Any], *, reader: Any = None) -> dict:
    """The subtitle-line boxes at each requested time. Never raises.

    `at` is on the video's own clock, in seconds. `reader` is injectable so the
    rule can be exercised without a model and without a file; passing None asks
    `source_captions` for its cached CRAFT detector, and a machine without it
    gets an observation with no samples and `why: no_text_detector_available` —
    never an observation that saw no text.

    THE DECODED TIME IS RECORDED BESIDE THE REQUESTED ONE. `cv2` seeks to the
    nearest keyframe-ish position, so asking for 51.15 s and reporting the boxes
    as being at 51.15 s states a precision the decoder did not deliver. An
    interval decision made on a box placed a second from where it was seen is
    the kind of error nothing downstream can detect.

    AND SO IS THE FRAME INDEX, which is the only thing that identifies a frame.
    Two different requested times can decode to the SAME frame, so a caller
    holding out "different" times has not necessarily held out different
    evidence — it can test a frozen region against the very frames that built
    it and report a clean result. `frame` is that identity; comparing requested
    times, or even decoded times, is comparing the request.
    """
    from services.clipper import source_captions as scap

    times: list[float] = []
    for raw in (at if isinstance(at, Sequence) and not isinstance(at, (str, bytes))
                else []):
        got = _finite(raw)
        if got is None:
            return _empty(video, BAD_TIMES)
        times.append(got)
    if not times:
        return _empty(video, BAD_TIMES)

    engine = scap._reader() if reader is None else reader
    if engine is None:
        return _empty(video, NO_DETECTOR)

    try:
        import cv2
    except Exception:
        return _empty(video, NO_VIDEO)
    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            return _empty(video, NO_VIDEO)
        out = _empty(video, "")
        out["why"] = None
        samples: list[dict] = []
        for want in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, want * 1000.0)
            got_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            # BOTH READ BEFORE `read()`. After it, `POS_FRAMES` points at the
            # NEXT frame, so an index taken afterwards names a frame nobody
            # looked at — and two sets compared on it would look disjoint
            # exactly when they are not.
            index = cap.get(cv2.CAP_PROP_POS_FRAMES)
            ok, frame = cap.read()
            if not ok or frame is None:
                samples.append({"t_requested": round(want, 3), "t_decoded": None,
                                "frame": None, "boxes": None,
                                "refused": FRAME_UNREADABLE})
                continue
            height, width = int(frame.shape[0]), int(frame.shape[1])
            if out["image_w"] is None:
                out["image_w"], out["image_h"] = width, height
            boxes = _boxes(frame, engine, width, height)
            samples.append({
                "t_requested": round(want, 3),
                "t_decoded": (round(float(got_ms) / 1000.0, 3)
                              if _finite(got_ms) is not None else None),
                "frame": (int(index) if _finite(index) is not None else None),
                "boxes": boxes,
                "refused": None if boxes is not None else DETECTOR_FAILED,
            })
        out["samples"] = samples
        out["read"] = sum(1 for s in samples if s["boxes"] is not None)
        out["refused"] = len(samples) - out["read"]
        out["refusals"] = sorted({s["refused"] for s in samples if s["refused"]})
        return out
    finally:
        cap.release()


def _boxes(frame: Any, reader: Any, width: int, height: int
           ) -> list[dict] | None:
    """Every line's box in one frame, as fractions — or None if it failed.

    ATOMIC, and for the reason `source_captions._frame_bands` is: a frame
    contributes everything or nothing. A malformed box that raised halfway
    through used to leave a partial list, which is a measurement of the model's
    failure presented as a measurement of the frame.

    The same sanity bars the band detector applies: finite, positive extent, and
    inside the frame it was found in. A box running from 0 to a million is a
    perfectly finite number and would put a subtitle across the whole width.
    """
    import math

    from services.clipper.source_captions import _BOX_SLACK_PX

    if width < 1 or height < 1:
        return None
    try:
        found = reader.detect(frame, text_threshold=0.7, low_text=0.4)[0][0]
    except Exception:
        return None
    out: list[dict] = []
    for box in found or []:
        try:
            x0, x1, y0, y1 = (float(box[0]), float(box[1]),
                              float(box[2]), float(box[3]))
        except (TypeError, ValueError, IndexError):
            return None
        if not all(math.isfinite(v) for v in (x0, x1, y0, y1)):
            return None
        if not (0.0 <= x0 < x1 <= width + _BOX_SLACK_PX):
            return None
        if not (0.0 <= y0 < y1 <= height + _BOX_SLACK_PX):
            return None
        out.append({"x0": round(x0 / width, 4), "x1": round(x1 / width, 4),
                    "y0": round(y0 / height, 4), "y1": round(y1 / height, 4)})
    return out


def from_annotations(video: Any, samples: Any, *, image_w: Any, image_h: Any,
                     provenance: str = HUMAN, dialogue: Any = None) -> dict:
    """A person's or an agent's observation, in the shape `observe` produces.

    §A accepts verified annotations for the first batch, and they have to enter
    the same record the detector does — otherwise the consumer grows two paths
    and one of them is the one nobody tested.

    `dialogue` is the confirmation the detector may not make: `True` says these
    boxes are dialogue rather than a watermark, a diagram label or an interface.
    Only `True` and `False` are answers; anything else is nobody having said, and
    a string `"true"` from a form is not a verdict.
    """
    if provenance not in (AGENT, HUMAN):
        # The detector route is `observe`. Letting an annotation claim
        # `provenance: detector` would put an unverifiable record under the name
        # of the one thing that can be re-run.
        return _empty(video, "annotations_must_come_from_an_agent_or_a_person",
                      provenance=DETECTOR)
    w, h = _finite(image_w), _finite(image_h)
    if not w or not h:
        return _empty(video, "the_annotated_image_dimensions_are_unknown",
                      provenance=provenance)
    rows: list[dict] = []
    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)):
        return _empty(video, BAD_TIMES, provenance=provenance)
    for raw in samples:
        if not isinstance(raw, dict):
            return _empty(video, BAD_TIMES, provenance=provenance)
        t = _finite(raw.get("t"))
        if t is None:
            return _empty(video, BAD_TIMES, provenance=provenance)
        boxes = _fractions(raw.get("boxes"))
        rows.append({"t_requested": round(t, 3), "t_decoded": round(t, 3),
                     "frame": raw.get("frame"), "boxes": boxes,
                     "refused": None if boxes is not None else FRAME_UNREADABLE})
    out = _empty(video, "", provenance=provenance)
    out["why"] = None
    out["image_w"], out["image_h"] = int(w), int(h)
    out["dialogue"] = dialogue if isinstance(dialogue, bool) else None
    out["samples"] = rows
    out["read"] = sum(1 for r in rows if r["boxes"] is not None)
    out["refused"] = len(rows) - out["read"]
    out["refusals"] = sorted({r["refused"] for r in rows if r["refused"]})
    return out


def _fractions(boxes: Any) -> list[dict] | None:
    if boxes is None:
        return None
    if not isinstance(boxes, Sequence) or isinstance(boxes, (str, bytes)):
        return None
    out: list[dict] = []
    for box in boxes:
        if not isinstance(box, dict):
            return None
        got = {k: _finite(box.get(k)) for k in ("x0", "x1", "y0", "y1")}
        if any(v is None for v in got.values()):
            return None
        if not (got["x0"] < got["x1"] <= 1.0 and got["y0"] < got["y1"] <= 1.0):
            return None
        out.append({k: round(v, 4) for k, v in got.items()})
    return out


def coverage(observation: Any, t0: Any, t1: Any) -> dict:
    """What evidence exists inside `[t0, t1)`, on the same clock as the samples.

    THE POINT OF IT. A caller asking "was there text during this shot" gets
    three different answers out of this: samples that saw text, samples that saw
    none, and no samples at all — and the third is not the second. An interval
    nobody looked at is `unevidenced`, and a decision taken on it is taken on
    nothing.

    Times are matched on `t_decoded` where there is one, because that is where
    the frame actually was; a sample whose decode position is unknown is counted
    as evidence for its requested time, and said so with `assumed_time`.
    """
    lo, hi = _finite(t0), _finite(t1)
    out: dict[str, Any] = {"t0": lo, "t1": hi, "samples": 0, "with_text": 0,
                           "without_text": 0, "refused": 0, "assumed_time": 0,
                           "unevidenced": None, "boxes": []}
    if lo is None or hi is None or hi <= lo:
        out["unevidenced"] = True
        out["why"] = "the_interval_is_not_two_ordered_finite_seconds"
        return out
    rows = (observation or {}).get("samples") if isinstance(observation, dict) else None
    if not isinstance(rows, Sequence):
        out["unevidenced"] = True
        out["why"] = "there_is_no_observation_to_read"
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        when = row.get("t_decoded")
        assumed = when is None
        if assumed:
            when = row.get("t_requested")
        when = _finite(when)
        if when is None or not (lo <= when < hi):
            continue
        out["samples"] += 1
        if assumed:
            out["assumed_time"] += 1
        if row.get("refused"):
            out["refused"] += 1
        elif row.get("boxes"):
            out["with_text"] += 1
            out["boxes"].extend(row["boxes"])
        else:
            out["without_text"] += 1
    # UNEVIDENCED IS ABOUT USABLE EVIDENCE, not about samples. An interval whose
    # only samples are refusals was looked at and not seen, which for every
    # decision downstream is the same as not having been looked at.
    out["unevidenced"] = (out["with_text"] + out["without_text"]) == 0
    out["why"] = None
    return out


def disjoint(build: Any, holdout: Any) -> dict:
    """Did the hold-out set really read frames the construction set did not.

    THE CHECK THAT WAS ASSUMED AND SHOULD NOT HAVE BEEN. Shifting the requested
    times by half a slot does not guarantee different frames: seeking lands on
    whatever the decoder gives, and two different requests can return the same
    one. A hold-out that shares frames with the construction set is testing a
    region against its own inputs — which is exactly the circularity it exists
    to escape, reported as though it had escaped it.

    Compared on the FRAME INDEX, which identifies a frame. Decoded times are a
    weaker proxy and requested times are not evidence at all. A sample with no
    index cannot be shown to be distinct, so it is counted as `unidentified`
    and blocks the clean answer rather than passing quietly.
    """
    out: dict[str, Any] = {"build": 0, "holdout": 0, "shared": 0,
                           "unidentified": 0, "disjoint": None, "why": None}
    rows_a = (build or {}).get("samples") if isinstance(build, dict) else None
    rows_b = (holdout or {}).get("samples") if isinstance(holdout, dict) else None
    if not isinstance(rows_a, Sequence) or not isinstance(rows_b, Sequence):
        out["why"] = "there_are_not_two_observations_to_compare"
        return out
    seen: set[int] = set()
    for row in rows_a:
        if not isinstance(row, dict):
            continue
        out["build"] += 1
        idx = row.get("frame")
        if isinstance(idx, int) and not isinstance(idx, bool):
            seen.add(idx)
        else:
            out["unidentified"] += 1
    for row in rows_b:
        if not isinstance(row, dict):
            continue
        out["holdout"] += 1
        idx = row.get("frame")
        if isinstance(idx, int) and not isinstance(idx, bool):
            if idx in seen:
                out["shared"] += 1
        else:
            out["unidentified"] += 1
    if out["unidentified"]:
        out["why"] = "some_samples_carry_no_frame_index"
        return out
    if not out["build"] or not out["holdout"]:
        out["why"] = "one_of_the_two_sets_is_empty"
        return out
    out["disjoint"] = out["shared"] == 0
    return out


CORRECTED = "corrected"
NOT_A_CORRECTION = "the_correction_is_not_a_record_with_a_time_boxes_and_a_source"


def correct(observation, corrections):
    """Fold a person's or an agent's corrections into a detector observation.

    THE MOMENT THIS EXISTS FOR. At 1161.1 s on `6053a598cf06` a caption is
    plainly on screen and the detector returned no boxes. Left alone, that
    sample is evidence of an EMPTY frame — `coverage` counts it under
    `without_text`, a region is built as though nothing needed keeping there,
    and the mistake is invisible because a miss and a gap look identical.

    A corrected sample carries `provenance: corrected` and the `corrected_by`
    that supplied it, so the record never claims a detector saw what a person
    supplied. The raw boxes are kept in `detector_boxes` rather than
    overwritten: the correction is an addition to the evidence, not a
    replacement of it, and a later pass measuring the detector's recall needs
    what it actually returned.

    A correction may only ADD. It cannot mark a sample as having no text —
    "the detector saw something and I say it is not there" is a `non_dialogue`
    LABEL, which is `caption_labels`' subject, and letting it be spelled here
    too would put one decision in two places under two names.
    """
    out = {**(observation if isinstance(observation, dict) else {}),
           "corrections": 0, "correction_refusals": []}
    rows = out.get("samples")
    if not isinstance(rows, Sequence):
        out["samples"] = []
        out["correction_refusals"] = ["there_is_no_observation_to_correct"]
        return out

    wanted = {}
    for raw in (corrections if isinstance(corrections, Sequence)
                and not isinstance(corrections, (str, bytes)) else []):
        if not isinstance(raw, dict):
            out["correction_refusals"].append(NOT_A_CORRECTION)
            continue
        at = _finite(raw.get("at"))
        boxes = _fractions(raw.get("boxes"))
        by = raw.get("by")
        if at is None or not boxes or by not in (AGENT, HUMAN):
            # An empty list is a refusal here, not a correction: see above —
            # this may only add.
            out["correction_refusals"].append(NOT_A_CORRECTION)
            continue
        wanted.setdefault(at, []).append((boxes, by, raw.get("why")))

    fixed = []
    for row in rows:
        if not isinstance(row, dict):
            fixed.append(row)
            continue
        when = row.get("t_decoded")
        when = _finite(row.get("t_requested") if when is None else when)
        hits = wanted.pop(when, None) if when is not None else None
        if not hits:
            fixed.append(row)
            continue
        added = [b for boxes, _by, _why in hits for b in boxes]
        fixed.append({**row,
                      "detector_boxes": row.get("boxes"),
                      "boxes": list(row.get("boxes") or []) + added,
                      # A frame the detector FAILED on is no longer a refusal
                      # once somebody supplied what is in it.
                      "refused": None,
                      "provenance": CORRECTED,
                      "corrected_by": sorted({by for _b, by, _w in hits}),
                      "corrected_why": [w for _b, _by, w in hits if w]})
        out["corrections"] += 1
    out["samples"] = fixed
    for at in wanted:
        # A correction that matched no sample is reported, for the reason a
        # label that matches nothing is: a pass whose targets silently missed
        # is indistinguishable from one nobody ran.
        out["correction_refusals"].append("no_sample_at_%s" % at)
    out["read"] = sum(1 for s in fixed
                      if isinstance(s, dict) and s.get("boxes") is not None)
    out["refused"] = sum(1 for s in fixed
                         if isinstance(s, dict) and s.get("boxes") is None)
    return out
