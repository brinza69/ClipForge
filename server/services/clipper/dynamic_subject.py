"""
ClipForge — AI Stream Clipper: is there a subject to point a camera at, second
by second.

WHY THIS EXISTS. `dynamic_edit`'s grammar is a two-camera switch written for a
stream VOD, where both cameras are already in the frame: the facecam and the
gameplay. It was applied unchanged to an interview, an IRL vlog and a Just
Chatting stream, and the blind review measured what that costs — 44 of 58 clips
carried a technical problem, and the reviewer named the mechanism exactly:

    "când apare altceva decât fețele lor pe ecran se vede prost fiindcă e tăiat"

On material with no second camera, a sequence with no subject has nothing to
point at, and a 9:16 window on it produces a wall of texture. An "8 million
pixels" slide came out an unreadable strip of grid. The whole frame is small but
legible, which a three-way visual test settled: for that sequence the full frame
wins, and for a speaking shot it clearly loses.

So the question this module answers is narrow: FOR EACH SPAN, IS ANYONE THERE.

THE THRESHOLDS ARE MEASURED, NOT CHOSEN. Face presence at the resolution
production actually uses — `dynamic_window` samples every 0.25s, not the 2s of
the whole-VOD `faces.json`, and reading the wrong one produced a gap analysis
that meant nothing. On three Jensen windows:

    detection dropouts inside good content   at most 1.75s  (7 samples)
    real face-less sequences                 7.50s, 8.50s, 12.25s

Nothing lies between 1.75s and 7.50s, so the two populations separate with room
on both sides. `ENTER_S` sits at 3.0s: 1.7x the longest dropout observed, and
under half the shortest real sequence.

ASYMMETRIC ON PURPOSE. Leaving takes 1.0s rather than 3.0s, because a single
face reappearing mid-way through a B-roll sequence should not split it in two,
while a genuine return to the speaker must not lag visibly.

WHAT IT DOES NOT SOLVE, and the plan should not pretend otherwise: a Just
Chatting stream reacting to a video. The detector reports faces there and they
are real — they are just the faces IN the video being watched, not the creator's.
Raw presence sees a subject and holds the crop. That needs a stable-track signal
and is a separate piece of work.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

__all__ = ["ENTER_S", "LEAVE_S", "presence_timeline", "span_has_subject"]

#: How long the frame must go without a face before the shot gives up on
#: pointing at one. See the measurements above.
ENTER_S = 3.0

#: ...and how long a face must be back before it counts again.
LEAVE_S = 1.0

#: What fraction of a span must have a subject for the span to be framed on one.
#: A span that straddles a boundary is mostly one thing or mostly the other; at
#: exactly half either answer is defensible, and this makes it the subject,
#: because keeping a face is the behaviour that already works.
SPAN_SUBJECT_SHARE = 0.5


def _hop_of(samples: Sequence[dict], default: float = 0.25) -> float:
    times = [float(s.get("t") or 0.0) for s in samples if isinstance(s, dict)]
    gaps = sorted(round(b - a, 4) for a, b in zip(times, times[1:]) if b > a)
    return gaps[len(gaps) // 2] if gaps else default


def presence_timeline(face_track: Iterable[dict], *, hop: float | None = None,
                      enter_s: float = ENTER_S, leave_s: float = LEAVE_S
                      ) -> list[bool]:
    """One flag per sample: is a subject worth framing on screen right now.

    Hysteresis, not a threshold on each sample. A raw per-sample answer flips on
    every blink of the detector, and every flip is a composition change the
    viewer sees as a jump cut with no cut.
    """
    samples = [s for s in (face_track or ()) if isinstance(s, dict)]
    if not samples:
        return []
    hop = float(hop or _hop_of(samples))
    enter = max(1, int(round(float(enter_s) / hop)))
    leave = max(1, int(round(float(leave_s) / hop)))

    raw = [bool(s.get("boxes")) for s in samples]
    out: list[bool] = []
    # Starts TRUE: the first shot of every clip opens on a face by design
    # (`plan_dynamic_edit` forces it), so starting false would fight that.
    state = True
    run = 0
    for present in raw:
        if present == state:
            run = 0
        else:
            run += 1
            if (state and run >= enter) or (not state and run >= leave):
                state = present
                run = 0
        out.append(state)
    return out


def _slice(values: Sequence[Any], t0: float, t1: float, hop: float) -> list:
    lo = max(0, int(round(float(t0) / hop)))
    hi = min(len(values), max(lo + 1, int(round(float(t1) / hop))))
    return list(values[lo:hi])


def span_has_subject(timeline: Sequence[bool], t0: float, t1: float,
                     hop: float, *, share: float = SPAN_SUBJECT_SHARE,
                     raw: Sequence[bool] | None = None) -> bool:
    """Whether one shot's span should be framed on a subject.

    The unit is the SPAN, not the sample. Deciding per sample would change
    composition inside a shot, and a shot whose framing changes underneath it is
    not a shot.

    UNANIMOUS RAW EVIDENCE WINS. Hysteresis exists to suppress flicker, not to
    overrule what is plainly on screen — and left to itself it did overrule it.
    Found by looking at a re-render rather than at a test: on the Jensen diagram
    clip the span 14.08-15.18s has a face in 4 of 4 samples and still came out
    `fit`, because faces returned at 14.0s and `LEAVE_S` withholds the flip for
    a second. Shots here run about 1.1s, so the confirmation lag can swallow a
    whole shot that was never ambiguous.
    """
    if not timeline:
        return True
    window = _slice(timeline, t0, t1, hop)
    if not window:
        return True
    if raw:
        seen = _slice(raw, t0, t1, hop)
        if seen and all(seen):
            return True
    return (sum(window) / len(window)) >= float(share)


def composition_for(timeline: Sequence[bool], t0: float, t1: float,
                    hop: float, raw: Sequence[bool] | None = None) -> str:
    """`crop` when there is someone to point at, `fit` when there is not."""
    return ("crop" if span_has_subject(timeline, t0, t1, hop, raw=raw)
            else "fit")


def raw_presence(face_track: Iterable[dict]) -> list[bool]:
    """Unsmoothed per-sample presence, for the unanimity check above."""
    return [bool(s.get("boxes")) for s in (face_track or ())
            if isinstance(s, dict)]
