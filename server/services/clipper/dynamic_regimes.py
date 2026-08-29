"""What a stretch of a clip IS, decided sample by sample, before anything cuts.

Batch R3b, and shadow instrumentation like R3a: computed and recorded, applied
to nothing. `legacy_dynamic` stays frozen and the delivered plan is untouched.

DECIDED AT THE TIMELINE'S RESOLUTION, NOT THE SHOT'S. The first version of this
module gave one verdict per existing shot by weighting the signals across it,
which is precisely the majority vote over a mixed shot the plan forbids — and it
produced a `reaction` from a creator in the first half of a shot and a different
face in the second, who were never on screen together. Regimes are a property of
the sequence, so they are decided per sample and the boundaries fall where the
signals actually change. The legacy shots are recorded alongside for reference;
they decide nothing here.

WHERE THE NUMBERS COME FROM. Nothing invents a threshold. `action` is the motion
series normalised the way `dynamic_edit` normalises it — against the 10th and
90th percentile — and the cut-offs are the ones the clip's own style already
names, `action_pct` and `speech_ratio_on`. The one difference is deliberate and
declared: the planner takes those percentiles over per-shot means, this takes
them over samples, because a per-shot bound cannot exist at sample resolution.

`edit_profiles.REGIMES` is the ONE list; this imports it rather than restating
it, because two closed lists stop being the same list the first time one changes.

`safe` IS NOT AN ERROR. It is the correct answer when the signals disagree or
there are not enough of them, and the alternative would be an invented crop.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from services.clipper import series
from services.clipper.candidate_terms import _num
from services.clipper.edit_profiles import REGIMES

__all__ = ["REGIMES", "VISUAL_KEYS", "COVERAGES", "VARIABILITIES",
           "visual_key_for", "normalise_series",
           "classify_samples", "segments_from", "merge_by_visual_key",
           "interval_count", "resample", "regime_view"]

#: What each regime asks the frame to deliver.
#:
#: Three values, not two. `crop` alone is not a visual key: a `speaker` stretch
#: and an `action` stretch are both crops and they point at different things, so
#: a boundary between them is a change the viewer sees. Merging on "both are
#: crops" hid exactly that.
#:
#: `speaker` and `reaction` share a key on purpose — both frame the creator, and
#: what separates them is why, not what is on screen.
#: `crop_anchor`, never `crop_creator`. `stable_track` finds a geometrically
#: stable cluster; R3a's own documentation says plainly that nobody labelled
#: those boxes and the measurement demonstrates compatibility with an anchor,
#: not identity. `crop_creator` may exist only after a real identity check or
#: after the human visual gate — until then the name would assert the thing the
#: batch was written to stop asserting.
#:
#: With no anchor at all it is `crop_subject`: whichever face was found.
VISUAL_KEYS: dict[str, str] = {
    "speaker": "crop_anchor",
    "reaction": "crop_anchor",
    "action": "crop_action",
    "conversation": "fit_full",      # a common frame beats two guessed crops
    "visual_evidence": "fit_full",
    "safe": "fit_full",              # when in doubt, show what is actually there
}


def normalise_series(values: Sequence[float]) -> tuple[list[float], bool]:
    """`(scaled series, whether it had any spread)`, from the shared scaler.

    THE planner's implementation, not a copy of it. The population differs on
    purpose — it scales per-shot means, this scales samples — but the same
    arithmetic runs underneath, because a duplicated helper is one edit away
    from being a second rule.

    The spread flag is why this is not just a list: a flat series scales to 0.5
    everywhere, and 0.5 sits under a default cut-off of 0.6, so "no evidence
    either way" would read as "not action" unless the caller can tell them
    apart.
    """
    return series.normalise(values)


def _mean(total: float, n: int) -> float | None:
    """The mean over MEASURED samples, or None when there were none.

    Not `total / max(1, n)`. Dividing by a floor turns "nobody measured this"
    into "measured, and zero", which is the one confusion this whole batch
    exists to stop making.
    """
    return round(total / n, 3) if n else None


def _at(series: Sequence[Any] | None, index: int, default: Any = None) -> Any:
    return series[index] if series and 0 <= index < len(series) else default


#: TWO INDEPENDENT AXES, because a series can be complete and flat, or varied
#: and full of holes, and one word for both hid whichever was worse.
#:
#: How much of the timeline the motion series reaches.
COVERAGE_COMPLETE = "complete"
COVERAGE_PARTIAL = "partial"
COVERAGE_UNAVAILABLE = "unavailable"
COVERAGES = (COVERAGE_COMPLETE, COVERAGE_PARTIAL, COVERAGE_UNAVAILABLE)

#: Whether it has any spread to scale against.
VARIABILITY_VARIABLE = "variable"
VARIABILITY_FLAT = "flat"
VARIABILITY_UNAVAILABLE = "unavailable"
VARIABILITIES = (VARIABILITY_VARIABLE, VARIABILITY_FLAT, VARIABILITY_UNAVAILABLE)



def classify_samples(*, creator: Sequence[bool], others: Sequence[bool] | None,
                     action: Sequence[float] | None, speech: Sequence[float] | None,
                     action_on: float, speech_on: float,
                     coverage: str = COVERAGE_COMPLETE,
                     variability: str = VARIABILITY_VARIABLE,
                     speech_known: bool = True,
                     intervals: int | None = None) -> list[dict]:
    """One verdict per sample: `{regime, reason}`.

    CO-PRESENCE IS A SAMPLE-LEVEL FACT. `reaction` needs the creator and someone
    else on screen AT THE SAME TIME, and that is a question only this resolution
    can answer: two shot-level weights of 50% each are both satisfied by two
    halves that never overlap.

    `others` is None when the source has no anchor. That is unknown, not zero —
    with nothing to be "off" of, no detection can be classified, and the two
    regimes that depend on the distinction are unavailable rather than false.

    THREE KINDS OF SILENCE, and only one of them is evidence. A motion series
    that is missing, flat, or shorter than the timeline cannot demonstrate the
    absence of action, and word times that do not exist cannot demonstrate that
    nobody spoke. Both resolve to `safe`, never to `visual_evidence` — the
    difference between "there is nothing to see here" and "nobody looked".
    """
    out: list[dict] = []
    total = len(creator or []) if intervals is None else int(intervals)
    for i in range(total):
        # PAST THE END OF THE TRACK IS NOT AN EMPTY FRAME. A face timeline
        # shorter than the clip used to be padded with False, which turned 29.75
        # unmeasured seconds into a confident "nobody was there" — and the report
        # still called itself timeline-resolution.
        if i >= len(creator or []):
            out.append({"regime": "safe", "reason": "target_unknown"})
            continue
        present = bool(_at(creator, i, False))
        external = None if others is None else bool(_at(others, i, False))

        # Is the action at this moment MEASURED, or merely absent from a list?
        covered = (action is not None and i < len(action)
                   and action[i] is not None)
        # A FLAT series is not evidence either: every value lands at 0.5, which
        # sits under a default cut-off of 0.6, so "no spread" would read as "no
        # action". Coverage and variability are separate questions and this
        # needs both to say yes.
        action_known = (covered and variability == VARIABILITY_VARIABLE)
        loud = action_known and _num(_at(action, i, 0.0)) >= action_on

        talking = bool(speech_known and _num(_at(speech, i, 0.0)) >= speech_on)

        if present and external:
            regime, reason = "reaction", "target_and_external_face_together"
        elif present and not speech_known:
            regime, reason = "safe", "speech_unknown"
        elif present and talking:
            regime, reason = ("speaker", "anchor_and_speech" if external is not None
                              else "subject_and_speech")
        elif present:
            regime, reason = ("safe", "signals_insufficient" if external is not None
                              else "no_anchor_to_classify_faces")
        elif external:
            regime, reason = "conversation", "faces_without_anchor"
        elif loud:
            regime, reason = "action", "no_subject_with_action"
        elif not action_known:
            # Nobody is on screen and nobody measured the motion here. "Show the
            # whole frame because there is nothing to point at" is a claim; this
            # is not. The reason names what was wrong with THIS bin, not the
            # series average: a hole inside a measured series is `action_partial`,
            # not `action_measured`.
            regime, reason = "safe", ("action_" + (
                COVERAGE_UNAVAILABLE if action is None
                else COVERAGE_PARTIAL if not covered
                else variability))
        else:
            regime, reason = "visual_evidence", "no_subject_no_action"
        out.append({"regime": regime, "reason": reason})
    return out


def segments_from(verdicts: Sequence[dict], *, hop: float,
                  creator: Sequence[bool] = (),
                  others: Sequence[bool] | None = None,
                  action: Sequence[float] | None = (),
                  speech: Sequence[float] | None = (),
                  shots: Sequence[dict] = (),
                  duration: float | None = None) -> list[dict]:
    """Contiguous, gapless runs of one regime, with the evidence behind each.

    The evidence is COUNTED over the run rather than read off its first sample:
    a stretch that is 80% speech and one that is 5% are different stretches, and
    a single representative moment cannot tell them apart.
    """
    out: list[dict] = []
    for i, verdict in enumerate(verdicts or []):
        # The last interval stops at the end of the CLIP, not one hop past it.
        t1 = round((i + 1) * hop, 3)
        if duration is not None:
            t1 = round(min(t1, float(duration)), 3)
        # SEGMENTED BY (regime, reason), not by regime alone. A run that is
        # `safe` because the series is flat and then `safe` because it stopped
        # being measured is one regime and two different facts, and merging on
        # the regime threw the second away before it reached the sidecar.
        if (out and out[-1]["regime"] == verdict["regime"]
                and out[-1]["reason"] == verdict["reason"]):
            run = out[-1]
            run["t1"] = t1
            run["_n"] += 1
        else:
            out.append({"t0": round(i * hop, 3), "t1": t1,
                        "regime": verdict["regime"], "reason": verdict["reason"],
                        "_n": 1, "_creator": 0, "_creator_n": 0,
                        "_others": 0, "_others_n": 0,
                        "_action": 0.0, "_action_n": 0,
                        "_speech": 0.0, "_speech_n": 0})
            run = out[-1]
        # COUNTED ONLY WHERE MEASURED, per axis. Folding an absent sample in as
        # 0.0 let a segment say `reason = creator_unknown` and
        # `evidence.creator = 0.0` in the same breath — and the second reads as
        # a measurement of absence.
        if i < len(creator or []):
            run["_creator"] += int(bool(creator[i]))
            run["_creator_n"] += 1
        if others is not None and i < len(others):
            run["_others"] += int(bool(others[i]))
            run["_others_n"] += 1
        a = _at(action, i, None) if action is not None else None
        if a is not None:
            run["_action"] += _num(a)
            run["_action_n"] += 1
        sp = _at(speech, i, None) if speech is not None else None
        if sp is not None:
            run["_speech"] += _num(sp)
            run["_speech_n"] += 1

    for segment in out:
        n = max(1, segment.pop("_n"))
        segment["samples"] = n
        # How much of the segment each axis was actually measured over, so a
        # mean taken over two samples is not read as one taken over twenty.
        segment["evidence_coverage"] = {
            "target": segment["_creator_n"], "others": segment["_others_n"],
            "action": segment["_action_n"], "speech": segment["_speech_n"],
        }
        segment["evidence"] = {
            # `target`, not `creator`. Without an anchor the signal means "a
            # face"; with one it means "the geometric cluster". Neither proves
            # who it is, and `target_basis` says which of the two it was.
            "target": _mean(segment.pop("_creator"), segment.pop("_creator_n")),
            # None, not 0.0, when there was never an anchor to be off of.
            "others": (None if others is None else
                       _mean(segment.pop("_others"), segment.pop("_others_n"))),
            "action": _mean(segment.pop("_action"), segment.pop("_action_n")),
            "speech": _mean(segment.pop("_speech"), segment.pop("_speech_n")),
        }

        # Which legacy shots this stretch overlaps. REFERENCE ONLY: the shots did
        # not decide the regime, and deriving it from them the other way round is
        # how the first version ended up voting.
        segment["overlaps_shots"] = [
            s.get("index") for s in (shots or [])
            if isinstance(s, dict)
            and _num(s.get("t1")) > segment["t0"] and _num(s.get("t0")) < segment["t1"]
        ]
    return out


def visual_key_for(regime: str, *, anchor_known: bool = True) -> str:
    """The key a regime delivers, weakened when there is no anchor to point at.

    A `speaker` on an anchored source frames the fixed cluster; on a source with
    no anchor it frames whichever face was found. Same picture, different claim
    — and the claim is what a report is for. NEITHER name says "creator": that
    is an identity nobody here established.
    """
    key = VISUAL_KEYS.get(regime, "fit_full")
    if key == "crop_anchor" and not anchor_known:
        return "crop_subject"
    return key


def merge_by_visual_key(segments: Sequence[dict],
                        *, anchor_known: bool = True) -> list[dict]:
    """Adjacent segments that would deliver the same image become one.

    The rule R1 paid for, one level up: a boundary between two regimes that both
    come out `fit_full` is a cut the viewer cannot see, and forcing a physical
    cut on every regime change would put back the 116 invisible cuts R1 removed.

    The regimes are NOT lost, and neither is why each was chosen. Every merged
    stretch keeps its members, because the reason a segment is what it is
    outlives the decision to show it whole — and a report that claimed its answer
    could be reconstructed while dropping the evidence would be lying.
    """
    out: list[dict] = []
    for segment in segments or []:
        key = visual_key_for(segment.get("regime"), anchor_known=anchor_known)
        # `evidence_coverage` travels with `evidence`. Copying the mean without
        # its denominator left the view that will actually be materialised
        # unable to tell a mean over two samples from one over twenty.
        member = {k: segment[k] for k in
                  ("t0", "t1", "regime", "reason", "evidence",
                   "evidence_coverage", "samples")
                  if k in segment}
        if out and out[-1]["visual_key"] == key and out[-1]["t1"] == segment["t0"]:
            out[-1]["t1"] = segment["t1"]
            out[-1]["members"].append(member)
            continue
        out.append({"t0": segment["t0"], "t1": segment["t1"],
                    "visual_key": key, "members": [member]})
    return out


def resample(values: Sequence[float], *, from_hop: float, to_hop: float,
             intervals: int) -> list[float | None]:
    """A motion series onto `to_hop` bins, on the clock it was actually measured.

    TWO THINGS THE OBVIOUS VERSION GETS WRONG, and both were measured rather
    than reasoned about:

    - **Index 0 is a sentinel, not a measurement.** `region_motion` has no
      previous frame to difference against, so it emits `0.0`. Using it as data
      lets a perfectly constant series look like it has dispersion, and a
      dispersion is what the normaliser scales against.
    - **A value describes the interval BEFORE it.** The sample at `t` is the
      difference between the frames at `t - step` and `t`, so value `j` covers
      `[(j-1) * from_hop, j * from_hop)`. Treating it as the interval that
      follows shifted the whole series forward by one step.

    Bins are overlap-weighted, and a bin the measurements do not fully cover is
    `None` rather than a partial average — past the end of the series there is
    nothing to average, and averaging nothing into calm is the mistake this
    batch keeps finding.
    """
    clean = [_num(v) for v in (values or [])]
    if len(clean) < 2 or from_hop <= 0 or to_hop <= 0 or intervals <= 0:
        return [None] * max(0, intervals)

    out: list[float | None] = []
    for i in range(intervals):
        lo, hi = i * to_hop, (i + 1) * to_hop
        total = 0.0
        weight = 0.0
        for j in range(1, len(clean)):           # 0 is the sentinel
            a, b = (j - 1) * from_hop, j * from_hop
            overlap = min(hi, b) - max(lo, a)
            if overlap > 1e-9:
                total += clean[j] * overlap
                weight += overlap
        # Full coverage or nothing: a bin the series only half reaches is not a
        # measurement of that bin.
        out.append(total / weight if weight >= (hi - lo) - 1e-6 else None)
    return out


def interval_count(duration: float, hop: float) -> int:
    """How many intervals a clip of this length has at this sample rate.

    `ceil`, and a sample landing exactly on the end of the clip does NOT open
    another one. The dense track carries a sample at EOF, and treating it as a
    full interval made a 4.0s clip come back 4.25s long — a sidecar describing
    a quarter second of video that does not exist.
    """
    if hop <= 0 or duration <= 0:
        return 0
    return int(math.ceil(round(duration / hop, 6)))


def regime_view(*, creator: Sequence[bool], others: Sequence[bool] | None,
                action: Sequence[float] | None, speech: Sequence[float] | None,
                hop: float, duration: float, style: dict | None = None,
                shots: Sequence[dict] = (),
                coverage: str = COVERAGE_COMPLETE,
                variability: str = VARIABILITY_VARIABLE,
                speech_known: bool = True, motion_hop: float | None = None,
                speech_source: str = "caption_plan") -> dict:
    """The whole proposal for one clip, auditable on its own terms."""
    merged = dict(style or {})
    action_on = _num(merged.get("action_pct"), 0.60)
    speech_on = _num(merged.get("speech_ratio_on"), 0.30)

    # The timeline is the CLIP's, not the track's. Whatever the detector
    # produced, the proposal may not describe a moment the clip does not have.
    limit = interval_count(duration, hop)
    measured_creator = len(creator or [])
    creator = list(creator or [])[:limit]
    others = None if others is None else list(others)[:limit]
    measured_bins = 0 if action is None else sum(1 for v in action if v is not None)
    if measured_bins == 0:
        coverage, variability = COVERAGE_UNAVAILABLE, VARIABILITY_UNAVAILABLE
    elif measured_bins < limit:
        coverage = COVERAGE_PARTIAL

    # Over the CLIP's intervals, not the track's length: a short track leaves
    # `creator_unknown` behind rather than a shorter report that claims to cover
    # everything.
    verdicts = classify_samples(creator=creator, others=others, action=action,
                                speech=speech, action_on=action_on,
                                speech_on=speech_on, coverage=coverage,
                                variability=variability,
                                speech_known=speech_known, intervals=limit)
    segments = segments_from(verdicts, hop=hop, creator=creator, others=others,
                             action=action, speech=speech, shots=shots,
                             duration=duration)
    # An ANCHOR, not an identity. Without one the presence signal means "a
    # face"; with one it means "the fixed cluster". Neither means "the creator",
    # and the report must not upgrade either on its way out.
    anchor_known = others is not None
    grouped = merge_by_visual_key(segments, anchor_known=anchor_known)

    counts: dict[str, int] = {}
    for segment in segments:
        counts[segment["regime"]] = counts.get(segment["regime"], 0) + 1
    return {
        "schema": "regime_view_v2",
        "scope": "regime_segments_at_timeline_resolution",
        "sample_hop_s": hop,
        "samples": len(verdicts),
        # Everything a reader needs to tell what was measured from what was
        # merely absent. A proposal that cannot be audited is a number.
        "duration_s": round(float(duration), 3),
        "intervals": limit,
        "coverage": {
            "target": len(creator),
            "others": (len(others) if others is not None else None),
            # The number of MEASURED bins, not the length of a list padded
            # with holes.
            "action": measured_bins,
            "speech": (len(speech) if speech is not None else 0),
        },
        # Two axes, not one word. A series can be complete and flat, or varied
        # and full of holes, and a single status hid whichever was worse.
        "action_coverage": coverage,
        "action_variability": variability,
        # Whether the target timeline actually covered the clip. A report that
        # names its scope as the timeline must say when it did not have one.
        "target_covered": measured_creator >= limit,
        "motion_hop_s": motion_hop,
        "speech_known": bool(speech_known),
        "speech_source": speech_source if speech_known else None,
        "action_pct": action_on,
        "speech_ratio_on": speech_on,
        # Without an anchor, `reaction` and `conversation` are not available at
        # all — said out loud, so their absence is not read as evidence that
        # neither ever happened.
        "off_anchor_known": others is not None,
        "target_anchor_known": anchor_known,
        "target_basis": "stable_anchor" if anchor_known else "unanchored_face",
        "segments": segments,
        "treatment_segments": grouped,
        "counts": counts,
        # Counted over REGIME changes only. Segments also split when the reason
        # changes, and a different reason for the same regime is not a change of
        # regime — counting it as one would inflate the very number the
        # treatment merge exists to compare against.
        "regime_boundaries": sum(
            1 for a, b in zip(segments, segments[1:]) if a["regime"] != b["regime"]),
        # NOT `visible_boundaries`. Whether a boundary is visible is decided by
        # `dynamic_geometry.visual_key` over the delivered geometry; this is the
        # coarser question of whether the TREATMENT changes, and naming it the
        # finer one would claim a proof nobody has.
        "treatment_boundaries": max(0, len(grouped) - 1),
    }
