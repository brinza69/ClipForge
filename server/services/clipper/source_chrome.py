"""Is a video player's own chrome burned into the picture — Batch R6.

§R6: "browser chrome, controls and search bars become explicit publishing
warnings". A WARNING, never a verdict — that was Codex's condition for this half
of the batch and it is what the vocabulary below is shaped by: a control label
says a control was on screen, not that the clip should not be published.

WHERE IT WAS MEASURED, AND WHERE THE FIRST ATTEMPT LOOKED. Eight uniform frames
of each of the 20 source proxies found nothing, and the conclusion written from
that was "the corpus contains no positive". It was wrong: the v2 human review
already listed browser UI among the v3 renderer's defects. The positive is in
the RENDERED EXPORTS, and eight instants of a multi-hour source never reached
it. A sample that misses the thing it looked for is not evidence the thing is
absent.

THE MEASUREMENT, on rendered exports at one frame per second:

    positives   14 of 14 Moist exports, 5 to 32 frames with a hit each
    negatives   27 exports from 10 other projects, 1,222 frames, 4,468 tokens
                read, and ONE hit — a single `Search` in one frame

So a threshold of two frames separates them completely, with a margin of five
against one.

    >= 1 frame    14/14 positives, 1/27 negatives
    >= 2 frames   14/14 positives, 0/27 negatives
    >= 3 frames   14/14 positives, 0/27 negatives

THE HYPOTHESIS I BROUGHT WAS THE WRONG ONE, and this is why the URL family is
not here. The plan was "a URL is a URL, so it is high precision by
construction". Across both populations the URL patterns produced 39 matches on
the positives and 0 on the negatives — and every one of the 39 was at confidence
0.00 to 0.01, garbage like `NOU:L Do.com catchvechackndt`. Zero real URLs. The
discriminator that works is the one I thought was weak: a control label from a
closed vocabulary, read confidently, in more than one frame.

WHAT `not_detected` DOES NOT MEAN. It does not mean clean. The recogniser reads
this content badly — on `pilotf81b`, the one source known to carry burned-in
captions, it produced eight tokens and not one above confidence 0.5 — so recall
is low and undemonstrated. The states are `detected`, `not_detected` and
`unavailable`, and only the first is a claim.

WHAT IS NOT CALIBRATED. Two frames and a confidence of 0.6 were chosen with the
answer visible, on 14 positives from ONE source and 27 negatives from ten. That
is more than the four sources behind `source_captions`' thresholds and it is
still not a calibration; `calibrated: false` rides with every verdict.

And one thing deliberately NOT used: the single false positive sits at y=0.551,
mid-frame, while every true hit clusters at y<=0.083 or y>=0.911 — the top and
bottom of a player. A position rule would separate them perfectly, and it would
be a threshold chosen on one negative sample. It is written here so the next
agent can test it against a real corpus rather than adopt it from this one.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

__all__ = ["DETECTED", "NOT_DETECTED", "UNAVAILABLE", "STATES", "REASONS",
           "CONTROLS", "FRAMES_MIN", "CONF_MIN", "is_a_warning", "classify",
           "detect"]

DETECTED = "detected"
NOT_DETECTED = "not_detected"
UNAVAILABLE = "unavailable"
STATES: tuple[str, ...] = (DETECTED, NOT_DETECTED, UNAVAILABLE)

#: Only `detected` says anything. A WARNING about publishing, never a verdict
#: about it — the caller decides what to do, and `not_detected` gives it no
#: grounds to decide anything.
def is_a_warning(state: str) -> bool:
    """Whether this verdict may raise a publishing warning. Only one may."""
    return state == DETECTED


#: Why no answer could be given. `not_detected` is never one of these: it is a
#: real outcome of a real look, just a weak one.
NO_DETECTOR = "no_text_recogniser_installed"
NO_VIDEO = "video_unreadable"
TOO_FEW = "too_few_frames_analysed"
DETECTOR_FAILED = "detector_failed_on_every_frame"
#: The denominator and the per-frame counts disagree. `classify([1, 1],
#: analysed=6)` used to answer `detected`, honouring a `SAMPLES_MIN` of 6 over a
#: list of two — a caller could hand it any denominator it liked.
COUNTS_DISAGREE = "frame_counts_do_not_match_the_denominator"
#: The frames were sampled at a cadence the threshold was not measured at. Two
#: frames means 0.1 seconds at one cadence and 60 at another.
WRONG_CADENCE = "sampled_at_a_cadence_the_threshold_was_not_measured_at"
REASONS: tuple[str, ...] = (NO_DETECTOR, NO_VIDEO, TOO_FEW, DETECTOR_FAILED,
                            COUNTS_DISAGREE, WRONG_CADENCE)

#: The closed vocabulary the measurement was made with. Five of these fired on
#: the positives — `watch later` 70 times, `share` 67, `save` 63, `search` 14,
#: `subscribe` 5 — and the rest never fired on either population. They stay
#: because the measurement was made with this list, and removing the silent ones
#: would make the reported precision belong to a list nobody ran.
CONTROLS = re.compile(
    r"^(search|share|save|subscribe|sign in|log in|watch later|"
    r"reload|refresh|bookmarks?|new tab|history|downloads|"
    r"back|forward|home|menu|settings|more|comments?|"
    r"views?|likes?|shorts|explore|library|trending)$", re.I)

#: How many ANALYSED frames must carry a control label. Two, and the separation
#: it buys is 5-against-1 rather than a hair: the weakest positive had five
#: frames with a hit and the strongest negative had one.
FRAMES_MIN = 2
#: Below this a read is not a read. The garbage that killed the URL hypothesis
#: came back at 0.00 to 0.01.
CONF_MIN = 0.6
#: Fewer analysed frames than this and two is not a threshold, it is a coin.
SAMPLES_MIN = 6
#: THE CADENCE THE THRESHOLD WAS MEASURED AT, in seconds. `FRAMES_MIN` counts
#: frames, so it means one thing at one frame per second and something entirely
#: different at one per minute — the measurement was made at 1.0 and the verdict
#: is refused at anything else rather than quietly rescaled.
EVERY_S = 1.0


def classify(hits_per_frame: Sequence[int], analysed: int) -> tuple[str, str | None]:
    """`(state, why not)` from the per-frame hit counts and the DENOMINATOR.

    `analysed` is what the recogniser got through, never what was sampled. A
    model that throws on every frame leaves no hits, and no hits from no looks
    is indistinguishable from no hits from many — which is the mistake
    `source_captions` had to be taught, one module along.

    AND THE TWO HAVE TO AGREE. `classify([1, 1], analysed=6)` answered
    `detected`, honouring a `SAMPLES_MIN` of 6 over a list of two: the
    denominator was whatever the caller said it was. One entry per analysed
    frame, each a non-negative integer, or the verdict is refused.
    """
    if (not isinstance(hits_per_frame, Sequence)
            or isinstance(hits_per_frame, (str, bytes))
            or not isinstance(analysed, int) or isinstance(analysed, bool)):
        return UNAVAILABLE, COUNTS_DISAGREE
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0
           for n in hits_per_frame):
        return UNAVAILABLE, COUNTS_DISAGREE
    if len(hits_per_frame) != analysed:
        return UNAVAILABLE, COUNTS_DISAGREE
    if analysed <= 0:
        return UNAVAILABLE, DETECTOR_FAILED
    if analysed < SAMPLES_MIN:
        return UNAVAILABLE, TOO_FEW
    frames = sum(1 for n in hits_per_frame if n > 0)
    if frames >= FRAMES_MIN:
        return DETECTED, None
    return NOT_DETECTED, None


#: How far past the frame a box may sit before it is the model failing rather
#: than a read touching the edge. The same slack `source_captions` allows.
_BOX_SLACK_PX = 2.0


def _reader():
    """The cached easyocr reader WITH recognition, or None. Never raises.

    Recognition is on here, unlike `source_captions`, because the question is
    what the text SAYS: `Watch later` is a control and a subtitle is not.
    """
    try:
        import easyocr
    except Exception:
        return None
    for gpu in (True, False):
        try:
            return easyocr.Reader(["en"], gpu=gpu, verbose=False,
                                  download_enabled=False)
        except Exception:
            continue
    return None


def _frame_hits(frame: Any, reader) -> list[dict] | None:
    """One frame's control labels, or None if it could not be read.

    ATOMIC, like `source_captions._frame_bands` and for the same reason: a
    partial frame is a measurement of the model's failure, not of the picture.
    """
    import math

    try:
        height, width = int(frame.shape[0]), int(frame.shape[1])
        if height < 1 or width < 1:
            return None
        found = []
        for box, text, conf in reader.readtext(frame, text_threshold=0.7,
                                               low_text=0.4):
            # AN IMPOSSIBLE READ FAILS THE WHOLE FRAME, and the frame is atomic,
            # so it contributes nothing rather than contributing the rest. A
            # confidence of NaN passed `value < CONF_MIN` — every comparison
            # against a NaN is false — and manufactured a `detected` out of a
            # model that had failed; a NaN coordinate reached the report and the
            # JSON; and a box outside the frame was kept as if it had been read.
            value = float(conf)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                return None
            ys = [float(p[1]) for p in box]
            xs = [float(p[0]) for p in box]
            if not all(math.isfinite(v) for v in ys + xs):
                return None
            if not (0.0 <= min(ys) and max(ys) <= height + _BOX_SLACK_PX):
                return None
            if not (0.0 <= min(xs) and max(xs) <= width + _BOX_SLACK_PX):
                return None
            label = str(text).strip()
            if value < CONF_MIN or not CONTROLS.match(label):
                continue
            found.append({"text": label, "conf": round(value, 2),
                          "y": round(min(ys) / height, 3)})
        return found
    except Exception:
        return None


def detect(video: str, *, every_s: float = EVERY_S, reader=None) -> dict:
    """`source_chrome_v1` for one rendered export. A warning, not a verdict."""
    out: dict[str, Any] = {
        "schema": "source_chrome_v1",
        "scope": "whether_a_video_players_own_chrome_is_burned_into_the_export",
        "state": UNAVAILABLE,
        "why_unavailable": None,
        "frames_analysed": 0,
        "frames_sampled": 0,
        "frames_with_a_control": 0,
        "hits": [],
        "frames_min": FRAMES_MIN,
        "conf_min": CONF_MIN,
        # THE CADENCE TRAVELS WITH THE VERDICT, because `FRAMES_MIN` counts
        # frames and a frame is a different amount of video at every cadence.
        "every_s": every_s,
        "measured_at_every_s": EVERY_S,
        # Chosen with the answer visible, on 14 positives from one source and 27
        # negatives from ten. More than `source_captions` had; still not a
        # calibration.
        "calibrated": False,
        # It never disables anything and never blocks a publish. Whatever
        # consumes this decides, and `not_detected` gives it no grounds.
        "applied": False,
    }
    # A DIFFERENT CADENCE IS REFUSED, not rescaled. Two frames is 0.1 seconds of
    # video at one cadence and 60 at another, and the separation behind the
    # threshold was measured at exactly one frame per second.
    if not isinstance(every_s, (int, float)) or isinstance(every_s, bool) \
            or abs(float(every_s) - EVERY_S) > 1e-9:
        out["why_unavailable"] = WRONG_CADENCE
        return out

    engine = reader if reader is not None else _reader()
    if engine is None:
        out["why_unavailable"] = NO_DETECTOR
        return out

    frames = _sample(video, every_s)
    out["frames_sampled"] = len(frames)
    if not frames:
        out["why_unavailable"] = NO_VIDEO
        return out

    per_frame: list[int] = []
    analysed = 0
    for at, frame in frames:
        found = _frame_hits(frame, engine)
        if found is None:
            continue
        analysed += 1
        per_frame.append(len(found))
        for hit in found:
            out["hits"].append({"at": at, **hit})

    out["frames_analysed"] = analysed
    out["frames_with_a_control"] = sum(1 for n in per_frame if n > 0)
    state, why = classify(per_frame, analysed)
    out["state"] = state
    out["why_unavailable"] = why
    return out


def _sample(video: str, every_s: float) -> list[tuple[float, Any]]:
    """`[(t, frame)]` every `every_s` seconds, or [] if it cannot be read."""
    try:
        import cv2
    except Exception:
        return []
    cap = cv2.VideoCapture(str(video))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if fps <= 0 or total <= 0:
            return []
        step = max(1, int(round(fps * max(0.1, every_s))))
        out = []
        for i in range(0, total, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, frame = cap.read()
            if ok:
                out.append((round(i / fps, 2), frame))
        return out
    finally:
        cap.release()
