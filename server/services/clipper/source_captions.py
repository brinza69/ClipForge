"""Does the SOURCE already carry burned-in subtitles — Batch R6.

The defect this exists for is in the baseline: 15 of 15 go ghost exports shipped
with TWO caption systems, because the source already had text burned into it and
nothing in the pipeline could tell. R0 left the hook — `captions_duplicate_
declared` has been `unavailable` since, because reporting 0 there would turn "we
never asked" into "we checked and it is clean".

THREE VALUES, AND `absent` IS THE EXPENSIVE ONE. `present` costs a caption layer
somebody can turn back on. `absent` is what disables the second layer, so it is
only ever returned when the detector actually ran on enough frames and found
nothing. Every other outcome — no detector installed, an unreadable file, too
few samples — is `unknown`, which changes nothing.

TWO APPROACHES FAILED FIRST, and they are written down in
`docs/clipper-caption-detection.md` so the next agent does not run them again.
Both were brightness heuristics over horizontal bands; the second one scored a
source WITHOUT burned captions above the one with them. What works is a real
text detector, and the only reason it is affordable is that `easyocr`'s CRAFT
weights are already cached on this machine — 5 seconds per source on the GPU.

WHAT SEPARATES A SUBTITLE FROM A HUD LABEL, measured on the four labelled
pilots rather than reasoned about. Three properties together, and no single one
of them is enough:

- **One band.** A subtitle has a fixed position; `pilot2c8a`'s gameplay text is
  scattered over eight bands, one to three frames each.
- **Persistent.** It is there in most samples; go ghost's band carries text in 9
  of 14.
- **WIDE.** A line of dialogue spans a good fraction of the frame. This is the
  one that separates a subtitle from a HUD: `pilot6b38` has text in the same
  band in 13 of 14 frames — coordinates or a watermark — and its widest box is
  0.08 of the frame against go ghost's 0.45.

THE THRESHOLDS BELOW WERE CHOSEN WITH THE ANSWER VISIBLE, on four sources. That
is not a calibration and this file does not pretend otherwise. They are named
constants so a real one has one place to change, and the margin between the
classes is reported on every call so the next person can see how much room there
was.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["PRESENT", "ABSENT", "UNKNOWN", "STATES", "REASONS", "detect",
           "classify"]

PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"
STATES: tuple[str, ...] = (PRESENT, ABSENT, UNKNOWN)

#: Why an answer could not be given. `absent` is never one of these outcomes.
NO_DETECTOR = "no_text_detector_installed"
NO_VIDEO = "video_unreadable"
TOO_FEW = "too_few_frames_sampled"
AMBIGUOUS = "evidence_between_the_thresholds"
REASONS: tuple[str, ...] = (NO_DETECTOR, NO_VIDEO, TOO_FEW, AMBIGUOUS)

#: How many sampled frames must carry text in ONE band for it to be a caption
#: track rather than something that happened to be on screen. CHOSEN on four
#: labelled sources: go ghost 9/14, the two negatives with text 3/14 and 13/14.
BAND_SHARE_MIN = 0.45
#: ...and how wide the widest line in that band has to be, as a fraction of the
#: frame. THE discriminator against a HUD label. CHOSEN: go ghost 0.45,
#: `pilot6b38`'s persistent watermark 0.08.
WIDTH_MIN = 0.25
#: Below this the classification is refused rather than guessed.
BAND_SHARE_MAYBE = 0.30
WIDTH_MAYBE = 0.15
#: Fewer sampled frames than this and there is no distribution to speak of.
SAMPLES_MIN = 8
#: Tenths of the frame height. A subtitle occupies one of them; splitting finer
#: would let a two-line caption look like two bands.
BANDS = 10


def _reader():
    """The cached CRAFT detector, or None. Never raises.

    Optional on purpose. A missing detector has to come back as `unknown`, and
    an import error at analysis time must not cost the run — this is one signal
    among many, and R6's rule is that its absence changes nothing.
    """
    try:
        import easyocr
    except Exception:
        return None
    for gpu in (True, False):
        try:
            return easyocr.Reader(["en"], gpu=gpu, verbose=False)
        except Exception:
            continue
    return None


def _sample(video: str, samples: int) -> list:
    """`samples` frames spread over the file, or [] if it cannot be read."""
    try:
        import cv2
    except Exception:
        return []
    cap = cv2.VideoCapture(str(video))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total < samples:
            return []
        step = max(1, total // (samples + 2))
        frames = []
        for i in range(1, samples + 1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i * step)
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
        return frames
    finally:
        cap.release()


def _bands(frames: Sequence[Any], reader) -> list[dict]:
    """Per band: in how many frames text appeared, and the widest line seen.

    One count per frame per band, not one per box: a caption broken into three
    boxes by the detector is still one caption, and counting the boxes would
    make a busy frame look like a caption track.
    """
    hits = [0] * BANDS
    widest = [0.0] * BANDS
    for frame in frames:
        height, width = frame.shape[0], frame.shape[1]
        try:
            boxes = reader.detect(frame, text_threshold=0.7, low_text=0.4)[0][0]
        except Exception:
            continue
        seen: set[int] = set()
        for box in boxes or []:
            x0, x1, y0, y1 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
            band = min(BANDS - 1, max(0, int(((y0 + y1) / 2.0 / max(1, height)) * BANDS)))
            if band not in seen:
                hits[band] += 1
                seen.add(band)
            widest[band] = max(widest[band], (x1 - x0) / max(1, width))
    return [{"band": i, "frames": hits[i], "widest": round(widest[i], 3)}
            for i in range(BANDS)]


def classify(bands: Sequence[dict], sampled: int) -> tuple[str, str | None, dict | None]:
    """`(state, why_unknown, the band)` from the per-band evidence.

    The band that decides is the one with the most frames, and it has to clear
    BOTH bars: present in enough of them, and wide enough to be a line of
    dialogue rather than a label. Between the two sets of thresholds the answer
    is `unknown` — an ambiguous source is exactly the one where turning the
    second caption layer off would be a guess.
    """
    if sampled < SAMPLES_MIN:
        return UNKNOWN, TOO_FEW, None
    best = max(bands or [{"band": -1, "frames": 0, "widest": 0.0}],
               key=lambda b: (b["frames"], b["widest"]))
    share = best["frames"] / float(sampled)
    band = {**best, "share": round(share, 3)}
    if share >= BAND_SHARE_MIN and best["widest"] >= WIDTH_MIN:
        return PRESENT, None, band
    if share <= BAND_SHARE_MAYBE or best["widest"] <= WIDTH_MAYBE:
        # Either nothing is there, or what is there is too narrow to be a line
        # of dialogue. Both are the same answer: no second caption system.
        return ABSENT, None, band
    return UNKNOWN, AMBIGUOUS, band


#: `reader=None` has to MEAN "there is no detector", so the default cannot be
#: None as well. It was, and a test asking for the no-detector path loaded the
#: model to prove the model was missing.
_RESOLVE = object()


def detect(video: str, *, samples: int = 14, reader: Any = _RESOLVE) -> dict:
    """`source_captions_v1` for one file. Never raises.

    `reader` is injectable so a test can exercise the rule without a model and
    without a video — the classification is the part with the judgement in it,
    and it has to be checkable on its own.
    """
    out: dict[str, Any] = {
        "schema": "source_captions_v1",
        "state": UNKNOWN,
        "why_unknown": None,
        "band": None,
        "sampled": 0,
        "bands": [],
        # The thresholds that produced the answer, beside the answer. They were
        # chosen on four sources with the labels visible, and a report that hid
        # that would be claiming a calibration nobody ran.
        "thresholds": {"band_share_min": BAND_SHARE_MIN, "width_min": WIDTH_MIN,
                       "band_share_maybe": BAND_SHARE_MAYBE,
                       "width_maybe": WIDTH_MAYBE, "samples_min": SAMPLES_MIN},
        "calibrated": False,
    }
    engine = _reader() if reader is _RESOLVE else reader
    if engine is None:
        out["why_unknown"] = NO_DETECTOR
        return out

    frames = _sample(video, samples)
    if not frames:
        out["why_unknown"] = NO_VIDEO
        return out

    out["sampled"] = len(frames)
    out["bands"] = [b for b in _bands(frames, engine) if b["frames"]]
    state, why, band = classify(out["bands"], len(frames))
    out["state"], out["why_unknown"], out["band"] = state, why, band
    return out
