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

__all__ = ["REASONS", "HOLDS", "CONFLICTS", "ADMITS", "UNMEASURED", "SNAP_S",
           "cut_boundaries", "candidates", "place", "rhythm_view"]

# --- why a cut is allowed to exist -------------------------------------------

#: R3b says the delivered image has to change here. REQUIRED: holding the old
#: framing past this point is the R3a bug — a crop kept on an anchor nobody is
#: standing on any more.
REASON_TREATMENT = "treatment_change"
#: The source cut. Following a cut the source already made invents nothing.
REASON_SCENE = "source_scene_cut"
#: An audio onset INSIDE a stretch R3b classified as measured action. The only
#: acceleration in the batch, and it is gated on the measurement, not on the
#: label: `classify_samples` only answers `action` when the motion series was
#: both covered and variable there.
REASON_BEAT = "action_beat"

REASONS: tuple[str, ...] = (REASON_TREATMENT, REASON_SCENE, REASON_BEAT)
#: Required reasons cut even without a place, and even against `min_shot_s`.
#: Everything else is an opportunity, and an opportunity that cannot be taken
#: cleanly is not taken.
REQUIRED: frozenset[str] = frozenset({REASON_TREATMENT})

# --- why a candidate did not become a cut ------------------------------------

HOLD_MIN_SHOT = "min_shot"
HOLD_NO_BOUNDARY = "no_boundary"
#: The regime changed and the image would not. R1's rule, one level up: forcing
#: a physical cut on every regime change puts back the 116 invisible cuts.
HOLD_SAME_TREATMENT = "same_treatment"
HOLD_NOT_MEASURED_ACTION = "not_measured_action"
HOLD_PROFILE = "profile_forbids"
#: An optional reason whose place is already taken by a cut at a DIFFERENT
#: moment. Merging it in would credit that cut with a reason it does not have.
HOLD_SAME_PLACE = "same_place"
#: An optional cut close enough to the end that the shot after it is a flash.
HOLD_TAIL_MIN_SHOT = "tail_min_shot"
HOLDS: tuple[str, ...] = (HOLD_MIN_SHOT, HOLD_NO_BOUNDARY, HOLD_SAME_TREATMENT,
                          HOLD_NOT_MEASURED_ACTION, HOLD_PROFILE,
                          HOLD_SAME_PLACE, HOLD_TAIL_MIN_SHOT)

#: A REQUIRED change that could not be honoured at all. Its own list, not a
#: hold: a hold is an opportunity declined, and this is a visual requirement the
#: timeline cannot materialise. Burying the two together would let a frame the
#: report calls wrong disappear into a column of things that were fine.
#:
#: The cut would leave a runt final shot. Reporting the violation and keeping
#: the cut is not enough — a cut at 9.9s of a 10s clip is a 100ms flash, and a
#: requirement impossible to materialise must not be presented as a valid edit.
#: R5 decides whether to extend the window or move the boundary.
CONFLICT_TAIL = "tail_min_shot"
#: Two DISTINCT required changes that would collapse onto one cut. They cannot
#: both be satisfied by it: a treatment that exists only between them is never
#: shown, and the flicker `min_shot_violations` exists to expose becomes
#: invisible instead.
CONFLICT_COLLISION = "required_collision"
CONFLICTS: tuple[str, ...] = (CONFLICT_TAIL, CONFLICT_COLLISION)

#: An OBSERVED event that is deliberately not in `REASONS`, because on its own
#: it never earns a cut. It is what a `same_treatment` hold is a hold OF.
EVENT_REGIME_CHANGE = "regime_change"

#: Two placements this far apart are the same instant, at the 3dp everything
#: here rounds to. Not a tolerance — a rounding equality.
_SAME_MOMENT = 1e-3

#: Which reasons each profile's grammar admits. CHOSEN from §4's rules, and the
#: only difference today is the beat: §4 gives the pace to events for `action`
#: and to nothing else. Every profile takes a source cut, because following the
#: source is never an invention — including `instructional`, where a scene change
#: IS the next step rather than an interruption of one.
ADMITS: dict[str, tuple[str, ...]] = {
    "talking_head":  (REASON_TREATMENT, REASON_SCENE),
    "conversation":  (REASON_TREATMENT, REASON_SCENE),
    "action":        (REASON_TREATMENT, REASON_SCENE, REASON_BEAT),
    "exploration":   (REASON_TREATMENT, REASON_SCENE),
    "instructional": (REASON_TREATMENT, REASON_SCENE),
    CONSERVATIVE:    (REASON_TREATMENT, REASON_SCENE),
}

#: What §4 asks each profile to do that NOTHING in this repo measures yet.
#:
#: Written down rather than approximated. `talking_head` is told to reframe on a
#: clear idea or emotion, and the nearest thing that exists is a keyword regex
#: over the transcript — a word list containing "bro" and "lol" is not an
#: emotion, and promoting it to a cut reason would be the invented signal §3.4
#: forbids for the active speaker. `conversation` is told to switch on a sure
#: active-speaker signal, which §3.4 names explicitly as the thing not to guess.
#:
#: This is why those two profiles read `below` their band. The gap is the
#: measurement that is missing, not a pace that needs padding.
UNMEASURED: dict[str, tuple[str, ...]] = {
    "talking_head":  ("reframe_on_idea_or_emotion",),
    "conversation":  ("active_speaker",),
    "action":        (),
    "exploration":   (),
    "instructional": ("step_boundary",),
    CONSERVATIVE:    (),
}

#: How far a required cut may be MOVED to land on a pause. CHOSEN, not measured
#: — one named constant so the calibration has exactly one thing to change. Past
#: it, moving the cut would describe a different moment instead of the same one
#: said more cleanly, so the cut stays where the change was and says so.
SNAP_S = 0.40


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

    for want in wanted:
        own, reason = round(float(want["t"]), 3), str(want["reason"])
        required = reason in REQUIRED

        near = [(bw, bt) for bt, bw in boundaries or [] if abs(bt - own) <= snap_s]
        if near:
            # The strongest boundary in the window, and the closest one among
            # equals — so a cut moves as little as the evidence allows.
            weight, chosen = max(near, key=lambda b: (b[0], -abs(b[1] - own)))
            placed, placement = round(float(chosen), 3), "snapped"
        elif required:
            weight, placed, placement = None, own, "unsnapped"
        else:
            held.append({"t": own, "reason": reason, "hold": HOLD_NO_BOUNDARY})
            continue

        existing = at.get(placed)
        if existing is not None and abs(existing["t"] - own) <= _SAME_MOMENT:
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
        row = {"t": dropped["t"], "reason": dropped["reasons"][0],
               "shot_s": round(duration - dropped["t"], 3),
               "min_shot_s": min_shot_s}
        if any(r in REQUIRED for r in dropped["reasons"]):
            conflicts.append({**row, "conflict": CONFLICT_TAIL})
        else:
            held.append({"t": row["t"], "reason": row["reason"],
                         "hold": HOLD_TAIL_MIN_SHOT})

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
