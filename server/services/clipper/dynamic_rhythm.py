"""WHEN the edit is allowed to cut, and WHY — Batch R4.

Shadow instrumentation, like R3a and R3b: computed, recorded, applied to
nothing. `legacy_dynamic` stays frozen and the delivered plan is untouched.

WHAT THIS IS AGAINST. The delivered planner cuts on a clock. `_cut_times` walks
the window taking the best-SOUNDING boundary between `min_shot_s` and
`max_shot_s`, and when a stretch offers none it cuts at `target_shot_s` anyway;
`_pick_camera` then guarantees the next shot LOOKS different, flipping families
after `max_same_family` shots whatever the signals say. Neither rule is dishonest
about itself — both were measured off the reference edits — but together they
make the pace a property of the renderer rather than of the material. That is
how 29,3 cuts a minute reach a tutorial and a Just Chatting stream alike.

THE RULE, and it is the whole batch: **a cut needs a reason AND a place.**

- A REASON is a declared change in what the viewer needs to see. It comes from
  R3b's treatment segments, from the source cutting itself, or — only where the
  action was actually measured — from an onset inside it.
- A PLACE is a moment where cutting does not land inside a word. That is what
  `_boundaries` already computes, and it is reused rather than reimplemented.

A pause with nothing behind it is a place with no reason, and that is the cut
this batch removes. A treatment change with no pause near it is a reason with no
place, and it is NOT dropped: the frame has become wrong, so it cuts where the
change happened and the report says the cut is unsnapped.

WHAT IS REPORTED RATHER THAN ENFORCED. The §4 bands live in
`dynamic_rhythm_pace`, split out at the 500-line limit. Nothing here adds a cut
to reach a band or drops one to stay inside it. On today's signals
`talking_head` and `conversation` read `below` — see `UNMEASURED`, which names
exactly which of their §4 rules nothing in this repo can yet measure. That is
the finding, not a bug to paper over, and the `below` must not be used as a
product gate until the missing signal exists.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any, Sequence

from services.clipper import dynamic_regimes, dynamic_rhythm_pace, edit_profiles
from services.clipper.candidate_terms import _num
# Through `dynamic_edit`, which re-exports it, and NOT straight from
# `dynamic_cuts`. The two import each other — `dynamic_cuts` needs the shared
# sentence regexes and `dynamic_edit` re-exports the cut functions at the
# bottom — so whichever is imported first has to be the one that defines the
# other's dependency. Reaching for `dynamic_cuts` directly makes this module's
# import order decide whether the planner loads at all.
from services.clipper.dynamic_edit import _boundaries
from services.clipper.edit_profiles import CONSERVATIVE
# The closed lists, split out at the 500-line limit and re-exported so the
# worker, the tests and the sidecar keep one import for "the R4 grammar".
# Same pattern as `dynamic_edit` re-exporting `dynamic_cuts`.
from services.clipper.dynamic_rhythm_vocab import (  # noqa: F401
    ADMITS,
    CONFLICTS,
    CONFLICT_COLLISION,
    CONFLICT_TAIL,
    EVENT_REGIME_CHANGE,
    HOLDS,
    HOLD_MIN_SHOT,
    HOLD_NO_BOUNDARY,
    HOLD_NOT_MEASURED_ACTION,
    HOLD_PROFILE,
    HOLD_SAME_PLACE,
    HOLD_SAME_TREATMENT,
    HOLD_TAIL_MIN_SHOT,
    REASONS,
    REASON_BEAT,
    REASON_SCENE,
    REASON_TREATMENT,
    REQUIRED,
    SNAP_S,
    UNMEASURED,
)

__all__ = ["REASONS", "HOLDS", "CONFLICTS", "ADMITS", "UNMEASURED", "SNAP_S",
           "cut_boundaries", "candidates", "place", "rhythm_view"]

def cut_boundaries(words: Sequence[dict], peaks: Sequence[float],
                   scenes: Sequence[float], clip_start: float, duration: float,
                   style: dict) -> list[tuple[float, float]]:
    """Where a cut would not land inside a word, as `[(t, weight)]`.

    THE planner's function, not a copy of it. What changes in R4 is what the
    list is FOR: `_cut_times` treats it as the answer and takes the best entry
    every `target_shot_s`, and here it only ever decides where an already-earned
    cut lands. A boundary alone never produces one.
    """
    return _boundaries(words, peaks, scenes, clip_start, duration, style)


#: One implementation, in the module that also attributes cuts to partitions.
_covering = dynamic_rhythm_pace.covering


def candidates(*, treatment_segments: Sequence[dict],
               regime_segments: Sequence[dict], scenes: Sequence[float] | None,
               beats: Sequence[float] | None, profile: str, duration: float,
               anchor_known: bool = True) -> tuple[list[dict], list[dict]]:
    """`(reasons to cut, candidates held back)`, both clip-relative.

    Only strictly inside the window: a change at 0.0 is how the clip OPENS, and
    a change at the end has no shot after it to be a boundary between.
    """
    admitted = ADMITS.get(profile) or ADMITS[CONSERVATIVE]
    wanted: list[dict] = []
    held: list[dict] = []

    def inside(t: float) -> bool:
        return 0.0 < t < duration

    def offer(t: float, reason: str) -> None:
        t = round(float(t), 3)
        if not inside(t):
            return
        if reason not in admitted:
            held.append({"t": t, "reason": reason, "hold": HOLD_PROFILE})
        else:
            wanted.append({"t": t, "reason": reason})

    for segment in list(treatment_segments or [])[1:]:
        offer(_num(segment.get("t0")), REASON_TREATMENT)

    # Regime boundaries that deliver the same image. Recorded as held rather
    # than ignored: "we saw this change and chose not to cut on it" is a
    # different statement from "we never looked", and only one of them can be
    # audited at all.
    #
    # NOT COMPARABLE WITH R1's 116, however alike the two counts look. Those
    # came from adjacent LEGACY SHOTS delivering one image; these come from R3b
    # REGIME segments. Different populations, so the numbers cannot be
    # subtracted, and a report that put them side by side would invite exactly
    # that.
    key = dynamic_regimes.visual_key_for
    for a, b in zip(regime_segments or [], list(regime_segments or [])[1:]):
        if a.get("regime") == b.get("regime"):
            continue
        if key(a.get("regime"), anchor_known=anchor_known) == \
                key(b.get("regime"), anchor_known=anchor_known):
            t = round(_num(b.get("t0")), 3)
            if inside(t):
                # `regime_change`, NOT `treatment_change`. What was observed is
                # a change of regime; a treatment change is precisely what this
                # is not, and labelling it as one would put a reason in the
                # report that never existed.
                held.append({"t": t, "reason": EVENT_REGIME_CHANGE,
                             "hold": HOLD_SAME_TREATMENT})

    for t in scenes or []:
        offer(_num(t), REASON_SCENE)

    for t in beats or []:
        t = round(_num(t), 3)
        if not inside(t):
            continue
        # The gate is the MEASUREMENT, not the label. `classify_samples` answers
        # `action` only where the motion series was covered and had spread, so a
        # beat over an unmeasured stretch cannot claim to be inside action —
        # and a beat over a stretch that was measured calm is not one either.
        segment = _covering(regime_segments or [], t)
        if segment is None or segment.get("regime") != "action":
            held.append({"t": t, "reason": REASON_BEAT,
                         "hold": HOLD_NOT_MEASURED_ACTION})
        else:
            offer(t, REASON_BEAT)

    wanted.sort(key=lambda c: (c["t"], c["reason"] not in REQUIRED))
    held.sort(key=lambda c: c["t"])
    return wanted, held


def place(wanted: Sequence[dict], boundaries: Sequence[tuple[float, float]],
          *, duration: float, min_shot_s: float,
          snap_s: float = SNAP_S) -> dict:
    """Turn reasons into cut times, or say why each one did not become one.

    Returns `{cuts, held, required_conflicts, min_shot_violations}`.

    THE ASYMMETRY. An OPTIONAL reason with no boundary within `snap_s`, or one
    that would cut sooner than `min_shot_s` after the last, is dropped: nothing
    is wrong with the frame and a cut here would be rhythm for its own sake. A
    REQUIRED reason is emitted anyway and the violation is RECORDED — holding a
    crop on an emptied anchor to protect a minimum shot length trades a defect
    the viewer sees for a metric nobody does.

    THREE WAYS THE OBVIOUS VERSION LOSES A REQUIRED CHANGE, all found by review:

    - **Two of them snapping to one pause.** A change at 5.0 and another at 5.2
      both land on the pause at 5.1, the second is absorbed as a duplicate, and
      the treatment that existed only between them is never shown — with no cut,
      no hold and no violation to say so. A cut satisfies a change only when it
      sits at that change's OWN moment; otherwise the second stays unsnapped, or
      it is reported as a `required_collision`.
    - **A cut landing behind the one before it.** Refused, and it falls back to
      the reason's own time. Raised in review as a consequence of overlapping
      snap windows; worked through, it cannot happen under TODAY'S selection
      rule — any boundary inside a later reason's window and below the earlier
      placement was also inside the earlier reason's window, so it lost there on
      weight or distance and loses again. The guard stays because the ORDER is
      what the tail walk-back and the partition attribution both assume, and the
      next change to `max(near, ...)` would break it silently.
    - **The runt tail.** A cut 100ms before the end is a flash, and reporting it
      while leaving it in `cuts` presents an unmaterialisable requirement as a
      valid edit. It is removed and recorded as a conflict.
    """
    cuts: list[dict] = []
    held: list[dict] = []
    conflicts: list[dict] = []
    violations: list[dict] = []
    at: dict[float, dict] = {}
    last = 0.0

    # The moments a REQUIRED change asks for, so no cut may be moved across one.
    required_at = sorted({round(float(w["t"]), 3) for w in wanted
                          if str(w["reason"]) in REQUIRED})
    # ROUNDED ONCE, HERE. The bounds below used to filter the raw boundary time
    # while the cut was placed at the rounded one, so a boundary at 5.0499996
    # passed a ceiling of 5.05 and then landed exactly on it — putting the cut
    # on the very change it was forbidden to cross, where the next change merged
    # into it and the treatment between the two vanished again. One resolution
    # for the test and the placement, or the test is not testing the placement.
    places = [(round(_num(bt), 3), _num(bw)) for bt, bw in boundaries or []]

    for want in wanted:
        own, reason = round(float(want["t"]), 3), str(want["reason"])
        required = reason in REQUIRED

        # A SNAP MAY NOT CROSS A REQUIRED CHANGE, IN EITHER DIRECTION. Moving a
        # cut past one keeps the list in order and still loses the treatment:
        # forwards, the shot before the cut shows the framing from BEFORE this
        # change, so the stretch between the two changes is never on screen;
        # backwards, the cut that introduces this change happens before the
        # previous change has begun, so that one is shown early and briefly, or
        # not at all. Both were found by review — the forward case on this
        # file's own fixture (5.000 and 5.050 with a pause at 5.300 kept one cut
        # at 5.300 and called the second change conflictual, while the first
        # change's cut had already passed beyond both states), the backward case
        # by asking the symmetric question. Cut ORDER catches neither: a cut
        # before the previous change but after the previous CUT is in order.
        after = bisect_right(required_at, own)
        ceiling = required_at[after] if after < len(required_at) else float("inf")
        before = bisect_left(required_at, own)
        floor = required_at[before - 1] if before else float("-inf")
        # ...and a required change may not snap INTO the tail zone either. A
        # change at 9.2 that snapped to 9.5 of a 10s clip was dropped by the tail
        # walk-back and reported as unmaterialisable, when its own moment leaves
        # a perfectly legal 0.8s shot. The conflict was manufactured by the snap.
        #
        # INCLUSIVE, unlike the change bounds. `duration - min_shot_s` is the
        # last legal place for a cut — the shot after it is exactly the minimum,
        # which passes — and a strict bound threw away the one boundary the rule
        # allows. The change bounds stay strict because landing ON a required
        # moment is landing on the change itself.
        tail_limit = round(duration - min_shot_s, 3) if required else float("inf")
        near = [(bw, bt) for bt, bw in places
                if abs(bt - own) <= snap_s and floor < bt < ceiling
                and bt <= tail_limit]
        if near:
            # The strongest boundary in the window, and the closest one among
            # equals — so a cut moves as little as the evidence allows.
            weight, placed = max(near, key=lambda b: (b[0], -abs(b[1] - own)))
            placement = "snapped"
        elif required:
            weight, placed, placement = None, own, "unsnapped"
        else:
            held.append({"t": own, "reason": reason, "hold": HOLD_NO_BOUNDARY})
            continue

        existing = at.get(placed)
        # AGAINST THE REQUESTS, not against where the cut landed. `existing["t"]`
        # is where it ended up after snapping, which may be nowhere near the
        # moment anything asked for: a change asked at 5.0 and placed at 5.2 made
        # a second change asked at 5.2 look like the same moment, and the
        # treatment between them was folded away. Exact, because both sides are
        # already rounded to 3dp.
        if existing is not None and any(m == own for _r, m in existing["requests"]):
            # One instant, two reasons for it. Both are kept: a treatment change
            # that also falls on a source cut is better evidence than either.
            if reason not in existing["reasons"]:
                existing["reasons"].append(reason)
            existing["requests"].append([reason, own])
            continue

        if existing is not None or placed <= last:
            if not required:
                held.append({"t": own, "reason": reason,
                             "hold": HOLD_SAME_PLACE if existing else HOLD_MIN_SHOT})
                continue
            # Separate the two rather than let one stand for both — but only
            # where the change's own moment is somewhere a cut can go.
            if own > last and own not in at and 0.0 < own < duration:
                weight, placed, placement = None, own, "unsnapped"
            else:
                conflicts.append({
                    "t": own, "reason": reason, "conflict": CONFLICT_COLLISION,
                    "collides_with": existing["t"] if existing else last})
                continue

        if not 0.0 < placed < duration:
            target = conflicts if required else held
            target.append({"t": own, "reason": reason,
                           **({"conflict": CONFLICT_COLLISION} if required
                              else {"hold": HOLD_NO_BOUNDARY})})
            continue

        short = round(placed - last, 3)
        if short < min_shot_s:
            if not required:
                held.append({"t": own, "reason": reason, "hold": HOLD_MIN_SHOT})
                continue
            violations.append({"t": placed, "reason": reason,
                               "shot_s": short, "min_shot_s": min_shot_s})

        entry = {"t": placed, "reasons": [reason], "placement": placement,
                 # Every request this cut answers, with the moment it was asked
                 # from. A single `asked_at` could not say that two changes were
                 # folded into one cut, which is exactly what has to be visible.
                 "requests": [[reason, own]],
                 "boundary_weight": (None if weight is None else round(weight, 3))}
        cuts.append(entry)
        at[placed] = entry
        last = placed

    # THE TAIL IS NOT A VIOLATION, IT IS A REFUSAL. Removing one cut can leave
    # the next one just as close to the end, so this walks back rather than
    # checking once.
    while cuts and round(duration - cuts[-1]["t"], 3) < min_shot_s:
        dropped = cuts.pop()
        at.pop(dropped["t"], None)
        violations = [v for v in violations if v["t"] != dropped["t"]]
        # EVERY request the dropped cut answered, not just the first. A cut can
        # carry a treatment change and a source cut at once, and routing only
        # `reasons[0]` threw the other provenance away — the report would then
        # show a requirement that no longer appears anywhere.
        tail = round(duration - dropped["t"], 3)
        for reason, moment in dropped["requests"]:
            if reason in REQUIRED:
                conflicts.append({"t": moment, "reason": reason,
                                  "conflict": CONFLICT_TAIL, "cut_at": dropped["t"],
                                  "shot_s": tail, "min_shot_s": min_shot_s})
            else:
                held.append({"t": moment, "reason": reason,
                             "hold": HOLD_TAIL_MIN_SHOT})

    # In time order. The walk-back appends from the end, so two conflicts at 9.3
    # and 9.7 came out reversed — a timeline that reads backwards is not one an
    # auditor can follow.
    conflicts.sort(key=lambda c: c["t"])
    return {"cuts": cuts, "held": held, "required_conflicts": conflicts,
            "min_shot_violations": violations}


def _tally(rows: Sequence[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows or []:
        value = row.get(key)
        for one in (value if isinstance(value, list) else [value]):
            if one:
                counts[one] = counts.get(one, 0) + 1
    return counts


def rhythm_view(*, duration: float, profile: str, mode: str,
                regime_view: dict | None,
                boundaries: Sequence[tuple[float, float]] | None,
                scenes: Sequence[float] | None, beats: Sequence[float] | None,
                style: dict | None = None,
                legacy_shots: Sequence[dict] = (),
                snap_s: float = SNAP_S) -> dict:
    """The whole cut proposal for one clip, auditable on its own terms.

    `regime_view` is None when there was no dynamic plan to describe. That comes
    back as an unavailable view carrying the reason, never as a proposal with
    zero cuts — the difference between "this clip needs no cuts" and "nothing
    decided anything here" is the whole subject of the last three batches.
    """
    merged = dict(style or {})
    min_shot_s = _num(merged.get("min_shot_s"), 0.60)
    profile = profile if profile in ADMITS else CONSERVATIVE
    base: dict[str, Any] = {
        "schema": "rhythm_view_v1",
        "scope": "cut_proposal_from_reasons_and_boundaries",
        "profile": profile,
        "admits": list(ADMITS[profile]),
        "unmeasured_rules": list(UNMEASURED.get(profile, ())),
        "min_shot_s": min_shot_s,
        "snap_s": snap_s,
        # Recorded, applied to nothing — and the availability check lives in
        # `edit_profiles`, so this becomes true by itself on the day the final
        # gate makes the grammar deliverable.
        "applied": edit_profiles.delivers_profile(mode),
        # What the renderer actually shipped, for the comparison this batch is
        # for. `legacy_cuts` is shots minus one on the DELIVERED list.
        #
        # In `base` rather than in the available branch: how fast the delivered
        # edit cut is a fact about the FILE, true whether or not a proposal could
        # be made about it. Withholding it when the proposal is unavailable would
        # hide the baseline on exactly the clips that most need explaining.
        "legacy_cuts": max(0, len(legacy_shots or []) - 1),
        "legacy_cuts_per_min": (
            round(max(0, len(legacy_shots or []) - 1) * 60.0 / duration, 2)
            if duration > 0 else None),
    }
    if not isinstance(regime_view, dict) or duration <= 0:
        return {**base, "available": False,
                "unavailable_because": ("no_regime_view" if not regime_view
                                        else "no_duration")}

    segments = regime_view.get("segments") or []
    anchor_known = bool(regime_view.get("target_anchor_known"))
    # PARTIAL IS NOT A PARTITION. Splitting a clip into "action" and "the rest"
    # needs to know what every second WAS: with a partial series the unmeasured
    # seconds fall silently into `quiet`, and a minute nobody measured comes back
    # as a confident `below`. Individual beats stay gated per segment — a
    # measured action stretch inside a partial track is still measured — but the
    # WHOLE-CLIP partition is refused.
    coverage = regime_view.get("action_coverage")
    variability = regime_view.get("action_variability")
    partition_known = (coverage == dynamic_regimes.COVERAGE_COMPLETE
                       and variability == dynamic_regimes.VARIABILITY_VARIABLE)
    unavailable_because = (
        None if partition_known
        else "action_coverage_partial" if coverage == dynamic_regimes.COVERAGE_PARTIAL
        else "action_not_measured")

    wanted, refused = candidates(
        treatment_segments=regime_view.get("treatment_segments") or [],
        regime_segments=segments, scenes=scenes, beats=beats,
        profile=profile, duration=duration, anchor_known=anchor_known)
    placed = place(wanted, boundaries or [], duration=duration,
                   min_shot_s=min_shot_s, snap_s=snap_s)
    held = sorted(refused + placed["held"], key=lambda c: c["t"])

    return {
        **base,
        "available": True,
        "duration_s": round(float(duration), 3),
        # A measured emptiness and an absent measurement, kept apart on both
        # signals: `[]` is "the source was scanned and cut nowhere", None is
        # "nobody scanned it".
        "scenes_known": scenes is not None,
        "beats_known": beats is not None,
        "boundaries_available": len(boundaries or []),
        "cuts": placed["cuts"],
        "cut_count": len(placed["cuts"]),
        "cut_reasons": _tally(placed["cuts"], "reasons"),
        "held": held,
        "held_reasons": _tally(held, "hold"),
        "min_shot_violations": placed["min_shot_violations"],
        # A REQUIRED change the timeline could not materialise. Its own list, so
        # a frame the report calls wrong cannot disappear into the column of
        # opportunities that were merely declined.
        "required_conflicts": placed["required_conflicts"],
        "required_conflict_kinds": _tally(placed["required_conflicts"], "conflict"),
        "action_partition_known": partition_known,
        "action_partition_unavailable_because": unavailable_because,
        "pace": dynamic_rhythm_pace.pace(
            placed["cuts"], duration=duration, profile=profile,
            regime_segments=segments, partition_known=partition_known,
            unavailable_because=unavailable_because),
    }
