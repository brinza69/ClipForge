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


# ── the stable track ─────────────────────────────────────────────────────────
#
# The second half of the reframing defect, and a different failure from the one
# above. On a Just Chatting stream the detector reports plenty of faces and they
# are real — they are the faces IN the video being reacted to. `_dominant` picks
# the biggest cluster, the biggest cluster is whatever is playing on the
# browser, and the crop follows it. The reviewer saw it immediately: "nu ia
# webcam pe toate video-urile, de acum ia fețele din video-ul la care se uită".
#
# WHAT ACTUALLY DISCRIMINATES, measured on the four pilot sources rather than
# assumed. Neither size nor persistence alone works: the webcam is 5.2% of frame
# width and an interview subject 10.6%, but a vlog's subject is 9.8%, and every
# source has several clusters that persist across the whole VOD.
#
# What separates them is how much TIGHTER the tightest one is than the rest:
#
#     moistcr1tikal   4.1 px   next 12.2   — a fixed webcam overlay
#     Jensen          5.3 px   next  5.4   — two locked-off interview cameras
#     vlog RO        13.8 px   next 13.9   — handheld, nothing is stable
#     go ghost        8.0 px   next 10.0   — one talking head, moderately still
#
# Only the first has a cluster that stands apart. So a stable track is claimed
# only when the best is decisively better than the runner-up, and on the three
# sources where the current framing already works, nothing is claimed and
# nothing changes.

#: How many times tighter than the runner-up the best cluster must be.
DOMINANCE = 2.0

#: A cluster must appear across at least this much of the source's span, and in
#: at least this share of its samples, before it can be called persistent.
MIN_SPAN = 0.5
MIN_SHARE = 0.05

#: Grid used to collect detections into candidate clusters, in proxy pixels.
_CELL = 40


def stable_track(face_track: Iterable[dict], *, dominance: float = DOMINANCE
                 ) -> dict | None:
    """The one fixed subject in this source, or None when there is not one.

    Returns `{"cx", "cy", "w", "spread", "runner_up"}` in the coordinate space
    of the track it was given. None is the common answer and the safe one: it
    means "carry on choosing the way you already do".
    """
    samples = [s for s in (face_track or ()) if isinstance(s, dict)]
    boxes = [(float(s.get("t") or 0.0), b) for s in samples
             for b in (s.get("boxes") or []) if b and len(b) >= 4]
    if len(boxes) < 20:
        return None

    times = [t for t, _ in boxes]
    span_total = max(times) - min(times)
    if span_total <= 0:
        return None

    cells: dict[tuple[int, int], list] = {}
    for t, b in boxes:
        cx, cy = b[0] + b[2] / 2.0, b[1] + b[3] / 2.0
        key = (int(cx // _CELL) * _CELL, int(cy // _CELL) * _CELL)
        cells.setdefault(key, []).append((t, cx, cy, float(b[2])))

    scored = []
    for members in cells.values():
        ts = [m[0] for m in members]
        if (max(ts) - min(ts)) / span_total < MIN_SPAN:
            continue
        if len(members) / len(samples) < MIN_SHARE:
            continue
        scored.append((_spread(members), members))
    if len(scored) < 2:
        # One persistent cluster and nothing to compare it against is not
        # evidence of a fixed overlay — it is a source with one subject, which
        # is the case that already works.
        return None

    scored.sort(key=lambda row: row[0])
    best, runner_up = scored[0][0], scored[1][0]
    if best <= 0 or runner_up < best * float(dominance):
        return None

    members = scored[0][1]
    return {"cx": _mean(m[1] for m in members),
            "cy": _mean(m[2] for m in members),
            "w": _mean(m[3] for m in members),
            "spread": round(best, 2),
            "runner_up": round(runner_up, 2)}


def _spread(members: Sequence[tuple]) -> float:
    """Positional standard deviation of a cluster, in pixels."""
    xs = [m[1] for m in members]
    ys = [m[2] for m in members]
    return (_variance(xs) + _variance(ys)) ** 0.5


def _variance(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return round(sum(vals) / len(vals), 2) if vals else 0.0
