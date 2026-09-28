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
production actually uses — `dynamic_window` samples every 0.25s, while the
whole-VOD `faces.json` has a median gap of 6.7s on these pilots, and reading the
wrong one produced a gap analysis that meant nothing. On three Jensen windows:

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

__all__ = ["ENTER_S", "LEAVE_S", "ANCHOR_FACE_WIDTHS", "presence_timeline",
           "span_has_subject", "anchored_track", "creator_presence",
           "off_anchor_presence"]

#: How long the frame must go without a face before the shot gives up on
#: pointing at one. See the measurements above.
ENTER_S = 3.0

#: ...and how long a face must be back before it counts again.
LEAVE_S = 1.0

#: How far from the fixed anchor a detection may sit and still be treated as the
#: same fixed subject, in multiples of the anchor's own width.
#:
#: CHOSEN, not calibrated, and the FLOOR usually decides. The tolerance is
#: `max(anchor width, _CELL) * this`, and on the one pilot with an anchor the
#: width is 25px against a 40px cell — so the effective tolerance there is 40px,
#: not "one face width". Calling it a face width would be wrong on the only
#: source that exercises it. What would recalibrate either number: the
#: anchor-compatible counts `scripts/measure_creator_presence.py` prints.
ANCHOR_FACE_WIDTHS = 1.0

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
                      enter_s: float = ENTER_S, leave_s: float = LEAVE_S,
                      retrospective: bool = False,
                      starts_present: bool = True) -> list[bool]:
    """One flag per sample: is a subject worth framing on screen right now.

    Hysteresis, not a threshold on each sample. A raw per-sample answer flips on
    every blink of the detector, and every flip is a composition change the
    viewer sees as a jump cut with no cut.

    `starts_present` is the opening assumption, and it is only right for the
    question this was written for. "Is anyone there" starts TRUE because the
    first shot of every clip opens on a face by design. "Is someone ELSE there"
    must start FALSE: presuming a second person at the top of a clip invents a
    reaction out of nothing, which is what it did.

    `retrospective` back-dates a CONFIRMED disappearance to where it actually
    began. Waiting `ENTER_S` before believing the subject is gone is what stops
    a blink from splitting a shot — but it also leaves the first three seconds
    of every real absence framed on a subject who is not there, which is
    exactly the wrong three seconds. Only the disappearance is back-dated: the
    return already has `span_has_subject`'s unanimous-raw-evidence rule, and
    back-dating it too would eat into an absence that genuinely happened.
    """
    samples = [s for s in (face_track or ()) if isinstance(s, dict)]
    if not samples:
        return []
    hop = float(hop or _hop_of(samples))
    enter = max(1, int(round(float(enter_s) / hop)))
    leave = max(1, int(round(float(leave_s) / hop)))

    raw = [bool(s.get("boxes")) for s in samples]
    out: list[bool] = []
    # See `starts_present`: TRUE for "is anyone there", FALSE for "is someone
    # else there".
    state = bool(starts_present)
    run = 0
    for present in raw:
        if present == state:
            run = 0
        else:
            run += 1
            if (state and run >= enter) or (not state and run >= leave):
                state = present
                if retrospective and not state:
                    # The run we just confirmed started `run` samples ago.
                    for i in range(len(out) - run + 1, len(out)):
                        out[i] = False
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


def anchored_track(face_track: Iterable[dict], stable: dict | None) -> list[dict]:
    """The same track, with every detection that is not the creator removed.

    The other half of the Just Chatting failure. `stable_track` already found
    the fixed overlay; until now it only moved the CENTRE of the face-cam
    family, so the faces in the video being reacted to still counted as a
    subject and held the crop. On the Moist pilot only 464 of the 1.285 samples
    that carry a face are ANCHOR-COMPATIBLE — they sit on the fixed cluster. The
    rest are not: nobody has labelled those boxes, and geometry is all this
    knows. "Not on the anchor" is the claim; "not the creator" is the inference,
    and the two must not be written as if they were the same sentence.

    BOTH SIDES ARE IN PROXY PIXELS, which is the only reason this comparison is
    meaningful. `stable_track` is computed from the proxy-resolution face track,
    and `dynamic_window`'s dense track carries its boxes through unscaled —
    `dynamic_edit` is where they get multiplied up to source pixels, and it does
    that to the ANCHOR too, separately. Compare one against the other in the
    wrong space and every detection lands outside the tolerance.

    Two rules that decide whether this is safe:

    - **Samples are never dropped, only their boxes.** A sample with no
      compatible face stays in the list with an empty box list, because the
      timeline is indexed by position and compressing it would shift every time
      after the first removal.
    - **No anchor means no filtering.** `stable_track` returns None for three of
      the four pilots, and on those the raw track IS the identity track. This
      must be the same object's content, not a subtly different one: a source
      with one subject is the case that already works.
    """
    samples = [s for s in (face_track or ()) if isinstance(s, dict)]
    if not isinstance(stable, dict) or stable.get("cx") is None:
        return samples

    cx, cy = float(stable["cx"]), float(stable["cy"])
    tolerance = max(float(stable.get("w") or 0.0), float(_CELL)) * ANCHOR_FACE_WIDTHS
    out: list[dict] = []
    for sample in samples:
        kept = []
        for box in sample.get("boxes") or []:
            if not box or len(box) < 4:
                continue
            bx, by = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
            if abs(bx - cx) <= tolerance and abs(by - cy) <= tolerance:
                kept.append(box)
        out.append({**sample, "boxes": kept})
    return out


def creator_presence(face_track: Iterable[dict], stable: dict | None, *,
                     hop: float | None = None) -> list[bool]:
    """Presence of THE CREATOR, second by second, with a retrospective flip.

    `presence_timeline` answers "is anyone there"; this answers "is anyone on
    the fixed anchor there", which on a reaction stream is a different question
    with a different answer for most of the detections. How different, on real
    material, is what `scripts/measure_creator_presence.py` reports — and only
    as anchor compatibility, never as an identity.
    """
    return presence_timeline(anchored_track(face_track, stable), hop=hop,
                             retrospective=True)


def proposed_compositions(shots: Sequence[dict], face_track: Iterable[dict],
                          stable: dict | None, *, hop: float | None = None) -> dict:
    """What each existing shot's composition WOULD be if the creator decided it.

    Recorded, never applied. R3a's whole job is to make the difference
    observable beside every export while the delivered plan stays exactly what
    it was — the same contract R2 established for the edit profile, and the
    reason a shadow is worth having at all.

    Computed over the shots the planner already produced: no regimes, no
    active-speaker, no new boundaries. Those are R3b, and mixing them in here
    would make it impossible to tell which change moved which frame.
    """
    samples = [s for s in (face_track or ()) if isinstance(s, dict)]
    anchored = anchored_track(samples, stable)
    step = float(hop or _hop_of(samples))
    tolerance = (max(float((stable or {}).get("w") or 0.0), float(_CELL))
                 * ANCHOR_FACE_WIDTHS) if stable else None
    creator = presence_timeline(anchored, hop=step, retrospective=True)
    raw = raw_presence(anchored)

    proposed: list[dict] = []
    changed = 0
    for shot in shots or []:
        if not isinstance(shot, dict):
            continue
        t0, t1 = float(shot.get("t0") or 0.0), float(shot.get("t1") or 0.0)
        want = composition_for(creator, t0, t1, step, raw=raw)
        now = str(shot.get("composition") or "crop")
        if want != now:
            changed += 1
        proposed.append({"index": shot.get("index"), "t0": t0, "t1": t1,
                         "composition": want, "delivered": now})

    return {
        # Enough to audit the number without re-deriving how it was produced.
        # A proposal whose rule nobody can reconstruct is a number, not evidence.
        "schema": "creator_view_v1",
        "scope": "composition_only_existing_shots",
        "sample_hop_s": step,
        "enter_s": ENTER_S,
        "leave_s": LEAVE_S,
        "retrospective": True,
        "anchor": stable,
        # Both sides of the comparison live here. `dynamic_edit` scales them to
        # source pixels separately, and comparing across the two spaces would
        # silently reject every detection.
        "anchor_coordinate_space": "proxy",
        "anchor_tolerance_px": tolerance,
        "total_samples": len(samples),
        "face_samples": sum(1 for s in samples if s.get("boxes")),
        # NULL without an anchor, not the face count. With nothing to compare
        # against, compatibility was never tested — that is unknown, and
        # reporting it as 100% would be the same lie in the other direction.
        "compatible_samples": (sum(1 for s in anchored if s.get("boxes"))
                               if stable else None),
        "shots": proposed,
        "changed": changed,
        # Why the answer is what it is, as a closed set rather than prose.
        "reason": "anchored" if stable else "no_anchor",
    }


def off_anchor_presence(face_track: Iterable[dict], stable: dict | None, *,
                        hop: float | None = None) -> list[bool] | None:
    """Presence of a face that is NOT on the anchor, or None when unknowable.

    The other half of what R3a made computable, and the half R3b needs: a
    creator plus someone else on screen is a reaction, and a creator alone is
    not, and before the anchor existed those two were the same signal.

    None WITHOUT AN ANCHOR, never an empty timeline. With nothing to be "off"
    of, no detection can be classified — and returning all-False would claim
    "there is nobody else here", which is exactly the unproven claim this whole
    batch was written to stop making.
    """
    samples = [s for s in (face_track or ()) if isinstance(s, dict)]
    if not isinstance(stable, dict) or stable.get("cx") is None:
        return None

    on_anchor = anchored_track(samples, stable)
    off = [{**s, "boxes": [b for b in (s.get("boxes") or [])
                           if b not in (kept.get("boxes") or [])]}
           for s, kept in zip(samples, on_anchor)]
    # STARTS ABSENT. `presence_timeline`'s opening TRUE exists because the first
    # shot of a clip is framed on a face; nothing makes a SECOND person likely at
    # the top of a clip, and assuming one invented a reaction on a track that
    # contained only the creator.
    return presence_timeline(off, hop=hop, retrospective=True,
                             starts_present=False)
