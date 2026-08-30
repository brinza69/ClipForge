"""Does the SOURCE already carry burned-in subtitles — Batch R6.

The defect this exists for is in the baseline: 15 of 15 go ghost exports shipped
with TWO caption systems, because the source already had text burned into it and
nothing in the pipeline could tell. R0 left the hook — `captions_duplicate_
declared` has been `unavailable` since, because reporting 0 there would turn "we
never asked" into "we checked and it is clean".

THREE VALUES, AND `present` IS THE EXPENSIVE ONE. §R6 is explicit about which
way round this goes, and an earlier version of this docstring had it backwards:

    present  the source already has captions  ->  DISABLE ClipForge's layer
    absent   it does not                      ->  KEEP ClipForge's layer
    unknown  nobody could tell                ->  change nothing

So a wrong `present` ships a clip with NO captions at all, which is why it is
the only state that needs both bars cleared. `absent` and `unknown` both leave
today's behaviour alone; the difference between them is what a later batch is
allowed to act on, not what happens now.

TWO APPROACHES FAILED FIRST, and they are written down in
`docs/clipper-caption-detection.md` so the next agent does not run them again.
Both were brightness heuristics over horizontal bands; the second one scored a
source WITHOUT burned captions above the one with them. What works is a real
text detector, and the only reason it is affordable is that `easyocr`'s CRAFT
weights are already cached on this machine — 5 seconds per source on the GPU.

WHAT SEPARATES A SUBTITLE FROM A HUD LABEL, measured on the four labelled
pilots rather than reasoned about. Three properties together, and no single one
of them is enough:

- **One band.** A subtitle has a fixed position; the text on `pilot2c8a`
  (moistcr1tikal, Just Chatting) is scattered over eight bands, one to three
  frames each.
- **Persistent.** It is there in most samples; go ghost's band carries text in 9
  of 14.
- **WIDE.** A line of dialogue spans a good fraction of the frame. This is the
  one that separates a subtitle from a label: `pilot6b38` (Jensen Huang) has
  text in the same band in 13 of 14 frames — a watermark or a lower third — and
  its widest box is 0.08 of the frame against go ghost's 0.45.

THE THRESHOLDS BELOW WERE CHOSEN WITH THE ANSWER VISIBLE, on four sources. That
is not a calibration and this file does not pretend otherwise. They are named
constants so a real one has one place to change, and the margin between the
classes is reported on every call so the next person can see how much room there
was.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["PRESENT", "ABSENT", "UNKNOWN", "STATES", "REASONS",
           "DISABLES_OWN_CAPTIONS", "disables_own_captions", "detect",
           "classify"]

PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"
STATES: tuple[str, ...] = (PRESENT, ABSENT, UNKNOWN)

#: THE DIRECTION, as a fact in code rather than a sentence in a docstring.
#: An earlier version of this module described it backwards in prose while the
#: logic was right, and prose cannot fail a test run — the next batch would have
#: read the paragraph, disabled captions on every source WITHOUT them, and left
#: the duplicates exactly where the defect already is. Whatever consumes this
#: signal reads the constant.
DISABLES_OWN_CAPTIONS = PRESENT


def disables_own_captions(state: str) -> bool:
    """Whether this verdict may switch ClipForge's own caption layer off.

    Only `present` may. `absent` keeps the layer — the source has no text of its
    own, so ours is the only one — and `unknown` changes nothing, which is the
    same behaviour by a different route.
    """
    return state == DISABLES_OWN_CAPTIONS


#: Why an answer could not be given. `absent` is never one of these outcomes.
NO_DETECTOR = "no_text_detector_installed"
NO_VIDEO = "video_unreadable"
TOO_FEW = "too_few_frames_sampled"
AMBIGUOUS = "evidence_between_the_thresholds"
#: Frames were read and the model threw on all of them. Empty bands are
#: indistinguishable from a source with no text, so this has to be its own
#: answer rather than a quiet `absent`.
DETECTOR_FAILED = "detector_failed_on_every_frame"
REASONS: tuple[str, ...] = (NO_DETECTOR, NO_VIDEO, TOO_FEW, AMBIGUOUS,
                            DETECTOR_FAILED)

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
            # `download_enabled=False` because "optional if offline" has to mean
            # it, and the default would fetch weights from the network on a
            # machine that has none. `recognizer=False` because the question is
            # whether text is THERE, not what it says — loading the recognition
            # model would cost seconds per source for nothing.
            return easyocr.Reader(["en"], gpu=gpu, verbose=False,
                                  download_enabled=False, recognizer=False)
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


def _bands(frames: Sequence[Any], reader) -> tuple[list[dict], int]:
    """`(per-band evidence, frames the detector actually got through)`.

    One count per frame per band, not one per box: a caption broken into three
    boxes by the detector is still one caption, and counting the boxes would
    make a busy frame look like a caption track.

    The second return value is the whole point of the tuple. A model that
    throws on every frame leaves the bands empty, and empty bands are
    indistinguishable from a source with no text in them — so the denominator
    has to be what was ANALYSED, never what was sampled.
    """
    hits = [0] * BANDS
    widest = [0.0] * BANDS
    analysed = 0
    for frame in frames:
        found = _frame_bands(frame, reader)
        if found is None:
            # The frame is a failure, WHOLE. Not a frame with fewer boxes.
            continue
        analysed += 1
        for band, width_frac in found.items():
            hits[band] += 1
            widest[band] = max(widest[band], width_frac)
    return ([{"band": i, "frames": hits[i], "widest": round(widest[i], 3)}
             for i in range(BANDS)], analysed)


def _frame_bands(frame: Any, reader) -> dict[int, float] | None:
    """`{band: widest line}` for one frame, or None if it could not be read.

    ATOMIC, and that is the whole point of it being its own function. Only the
    `detect()` CALL used to be inside the `try`; the parsing was outside, so a
    malformed box — one CRAFT returned with three coordinates instead of four —
    raised `IndexError` straight out of a module whose contract is "optional
    signal, never raises". A frame contributes everything or nothing: a partial
    frame is a measurement of the model's failure, not of the source.
    """
    import math

    try:
        height, width = int(frame.shape[0]), int(frame.shape[1])
        boxes = reader.detect(frame, text_threshold=0.7, low_text=0.4)[0][0]
        out: dict[int, float] = {}
        for box in boxes or []:
            x0, x1, y0, y1 = (float(box[0]), float(box[1]),
                              float(box[2]), float(box[3]))
            if not all(math.isfinite(v) for v in (x0, x1, y0, y1)):
                return None
            band = min(BANDS - 1,
                       max(0, int(((y0 + y1) / 2.0 / max(1, height)) * BANDS)))
            out[band] = max(out.get(band, 0.0), (x1 - x0) / max(1, width))
        return out
    except Exception:
        return None


def _verdict_for(band: dict, analysed: int) -> str:
    """What ONE band is on its own: present, absent, or in between."""
    share = band["frames"] / float(max(1, analysed))
    if share >= BAND_SHARE_MIN and band["widest"] >= WIDTH_MIN:
        return PRESENT
    if share <= BAND_SHARE_MAYBE or band["widest"] <= WIDTH_MAYBE:
        # Either it is almost never there, or what is there is too narrow to be
        # a line of dialogue.
        return ABSENT
    return UNKNOWN


def classify(bands: Sequence[dict], analysed: int) -> tuple[str, str | None, dict | None]:
    """`(state, why_unknown, the band that decided)` from the per-band evidence.

    EVERY BAND IS EXAMINED, and the order matters. An earlier version took the
    band with the most frames and judged only that one, which a HUD label wins
    every time — measured on the two real pieces of evidence combined, a
    watermark at 14/14 and 0.08 wide beat the actual caption track at 9/14 and
    0.45, and the source came back `absent` without the caption band ever being
    looked at.

    So: any band that clears both bars makes it `present`; failing that, any
    band in the ambiguous zone makes it `unknown`; `absent` needs EVERY band to
    be clearly negative. That order is not symmetry — it follows from `present`
    being the state that would suppress ClipForge's own captions, so it must be
    reachable on the evidence of one band while `absent` needs all of them.

    `analysed` is the number of frames the detector actually got through, which
    is not the number sampled: a model that throws on every frame produced no
    evidence at all, and reading that as "looked and found nothing" is the same
    mistake in a different coat.
    """
    if analysed < SAMPLES_MIN:
        return UNKNOWN, TOO_FEW, None
    rated = [(b, _verdict_for(b, analysed)) for b in bands or []]
    for state in (PRESENT, UNKNOWN):
        hits = [b for b, v in rated if v == state]
        if hits:
            best = max(hits, key=lambda b: (b["frames"], b["widest"]))
            band = {**best, "share": round(best["frames"] / float(analysed), 3)}
            return state, (AMBIGUOUS if state == UNKNOWN else None), band
    best = (max(bands, key=lambda b: (b["frames"], b["widest"])) if bands
            else {"band": -1, "frames": 0, "widest": 0.0})
    band = {**best, "share": round(best["frames"] / float(analysed), 3)}
    return ABSENT, None, band



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
        # Sampled, got through, and thrown on. `sampled` is what was read off
        # disk; `analysed` is the denominator every share is taken over.
        "analysed": 0,
        "failed": 0,
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
    bands, analysed = _bands(frames, engine)
    out["analysed"] = analysed
    out["failed"] = len(frames) - analysed
    out["bands"] = [b for b in bands if b["frames"]]
    state, why, band = classify(out["bands"], analysed)
    if state == UNKNOWN and why == TOO_FEW and analysed < len(frames):
        # Sampled but not analysed: the model, not the material.
        why = DETECTOR_FAILED if analysed == 0 else TOO_FEW
    out["state"], out["why_unknown"], out["band"] = state, why, band
    return out
