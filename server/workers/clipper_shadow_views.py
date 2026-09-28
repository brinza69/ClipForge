"""Everything a render RECORDS about the edit it did not make.

Split out of `clipper_render_plan.py` in Batch R4, when that file passed the
repo's 500-line limit. The seam is the one the last three batches drew for
themselves: `_decide_render` answers WHAT to render, and this answers what to
SAY about it. Nothing here may change a delivered frame — every view is computed
from the finished plan and written beside it, and R2's frozen export is the
contract that makes the comparison worth anything.

Three proposals ride on every render now:

- R3a `creator_view` — the composition each existing shot would get if only the
  anchored subject counted as one.
- R3b `regime_view` — what each stretch IS, decided per sample rather than per
  shot, because a weighted verdict over a mixed shot is a majority vote.
- R4 `rhythm_view` — which of those boundaries would EARN a cut, where it would
  land, and what §4's band says about the result.

THEY SHARE ONE CLOCK, and that is deliberate rather than convenient. The hop
comes from the first and is handed down, so the three cannot drift into
describing different timelines and then be compared as if they described one.
"""

from __future__ import annotations

from services.clipper import dynamic_regimes, dynamic_rhythm, dynamic_subject
from services.clipper.candidate_terms import _num
from workers.clipper_captions import _clip_words

__all__ = ["shadow_views"]


def _speech_share(words, clip_start: float, t0: float, t1: float) -> float:
    """How much of one interval a word covers, on the CLIP's clock.

    A share rather than a flag, so `speech_ratio_on` means something. The
    boolean it replaced made the threshold decorative: any overlap at all read
    as speech, and the setting was read, persisted and passed without ever
    changing an answer.
    """
    span = max(1e-6, t1 - t0)
    # The UNION of the overlaps, not their sum. Two words that overlap each
    # other would otherwise count the same instant twice — clamped to 1.0, so
    # the error hides, and the 58 pilot clips happen to have no overlapping
    # words at all. A measurement that is only right because the data is tidy is
    # not a measurement.
    spans: list[tuple[float, float]] = []
    for word in words or []:
        ws = _num(word.get("start")) - clip_start
        we = _num(word.get("end"), ws) - clip_start
        lo, hi = max(ws, t0), min(we, t1)
        if hi > lo:
            spans.append((lo, hi))
    covered = 0.0
    end = t0
    for lo, hi in sorted(spans):
        lo = max(lo, end)
        if hi > lo:
            covered += hi - lo
            end = hi
    return min(1.0, covered / span)


def shadow_views(clip, dyn: dict | None, *, mode: str, profile: str) -> dict:
    """`{creator_view, regime_view, rhythm_view}` for one finished plan.

    All three are None when there is no dynamic plan to describe. That is the
    absence of a proposal, not a proposal that found nothing — the distinction
    the last three batches are mostly made of.
    """
    if not dyn:
        return {"creator_view": None, "regime_view": None, "rhythm_view": None}

    # R3a, recorded and not applied: what each shot's composition would be if
    # only the CREATOR counted as a subject. The delivered plan is untouched —
    # `legacy_dynamic` stays frozen and the shadow export must stay byte-for-byte
    # what it was, or R2's contract is broken.
    faces = dyn.get("_review_faces") or []
    stable = dyn.get("_stable_track")
    creator_view = dynamic_subject.proposed_compositions(
        dyn.get("shots") or [], faces, stable)
    # R3b, recorded beside it: what each stretch IS, and how many of those
    # boundaries the viewer would actually see. Same hop as the proposal
    # above, taken from it rather than recomputed, so the two describe the
    # same timeline.
    hop = float(creator_view["sample_hop_s"])
    # Speech as the SHARE of each interval covered by a word, so the
    # threshold the style already names actually applies. A boolean per
    # sample ignored `speech_ratio_on` entirely, which meant the setting was
    # read, stored, passed and never used.
    words = _clip_words(clip)
    start = float(clip.start_time or 0.0)
    duration = float(clip.duration or 0.0)
    limit = dynamic_regimes.interval_count(duration, hop)
    # The last interval ends at the CLIP, not one hop past it: a word
    # covering 4.0-4.1s of a 4.1s clip covers all of the final interval, and
    # measuring it against 4.0-4.25 reported 40%.
    speech = [_speech_share(words, start, i * hop, min((i + 1) * hop, duration))
              for i in range(limit)] if words else None
    # RESAMPLED FIRST. The motion series is taken in whole frames, so on a
    # 10 FPS proxy a 0.25s request lands on a 0.2s grid; normalising it and
    # indexing by the face clock read every value from the wrong moment.
    motion_hop = float(dyn.get("_motion_hop") or hop)
    binned = dynamic_regimes.resample(dyn.get("_motion") or [],
                                      from_hop=motion_hop, to_hop=hop,
                                      intervals=limit)
    scaled, had_spread = dynamic_regimes.normalise_series(
        [v for v in binned if v is not None])
    # Put the gaps back where they were: `normalise_series` only sees the
    # measured bins, and an unmeasured one must stay unmeasured.
    measured = iter(scaled)
    motion = [next(measured) if v is not None else None for v in binned]
    # Two independent axes. Coverage is how much of the timeline the series
    # reaches; variability is whether it has any spread to scale against. A
    # single status could not say "complete but flat", and that is a real
    # state — a static screen measured end to end.
    measured_bins = [v for v in motion if v is not None]
    coverage = (dynamic_regimes.COVERAGE_UNAVAILABLE if not measured_bins
                else dynamic_regimes.COVERAGE_PARTIAL
                if len(measured_bins) < len(motion)
                else dynamic_regimes.COVERAGE_COMPLETE)
    variability = (dynamic_regimes.VARIABILITY_UNAVAILABLE if not measured_bins
                   else dynamic_regimes.VARIABILITY_VARIABLE if had_spread
                   else dynamic_regimes.VARIABILITY_FLAT)
    regime_view = dynamic_regimes.regime_view(
        creator=dynamic_subject.creator_presence(faces, stable, hop=hop),
        others=dynamic_subject.off_anchor_presence(faces, stable, hop=hop),
        action=motion or None, speech=speech, hop=hop, duration=duration,
        style=dyn.get("style"), shots=dyn.get("shots") or [],
        coverage=coverage, variability=variability,
        # NO WORDS IS NOT SILENCE. `_clip_words` returns [] when the clip has
        # no caption plan, and reading that as "nobody spoke" turned real
        # speech into `visual_evidence`.
        speech_known=bool(words),
        motion_hop=motion_hop or None)
    # R4, on top of R3b: which of those boundaries would EARN a cut, and
    # where each one would land. A reason without a place and a place
    # without a reason are both recorded as held, so the proposal can be
    # read against the delivered cadence rather than replacing it.
    rhythm = dyn.get("_rhythm") or {}
    rhythm_view = dynamic_rhythm.rhythm_view(
        duration=duration, profile=profile, mode=mode,
        regime_view=regime_view, boundaries=rhythm.get("boundaries"),
        scenes=rhythm.get("scenes"),
        # The thinned onsets the planner already found. None — never [] —
        # when the audio artefact carried no peaks at all: an unmeasured
        # track must not read as a track with no beats in it.
        beats=(dyn.get("hits") or []) if rhythm.get("beats_known") else None,
        style=dyn.get("style"), legacy_shots=dyn.get("shots") or [])

    return {"creator_view": creator_view, "regime_view": regime_view,
            "rhythm_view": rhythm_view}
