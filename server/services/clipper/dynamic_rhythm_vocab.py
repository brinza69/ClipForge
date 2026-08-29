"""The closed vocabulary Batch R4 cuts by: reasons, holds, conflicts, profiles.

Split from `dynamic_rhythm.py` at the repo's 500-line limit, on the seam the
batch's own rule draws — a cut needs a REASON and a PLACE, and everything here
answers the first half. No algorithm, only the lists and what each entry means.

They are CLOSED lists on purpose. A report that counts its own categories can be
audited; one that accumulates prose cannot, and the reason a stretch is what it
is has to outlive the decision it produced.
"""

from __future__ import annotations

from services.clipper.edit_profiles import CONSERVATIVE

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
#:
#: AN INTEGRITY GUARD, not a case anyone has produced. With the snap bounded on
#: both sides by the neighbouring required moments, a later change should always
#: find its own moment free — the earlier cut lands strictly before it. That is
#: an argument, not a proof, and it is exactly the kind that stops holding the
#: first time a bound is relaxed. The branch stays so the failure would be
#: REPORTED rather than swallowed: the alternative to an unused constant here is
#: a treatment that disappears in silence. Same call as the ordering guard in
#: `place`.
CONFLICT_COLLISION = "required_collision"
CONFLICTS: tuple[str, ...] = (CONFLICT_TAIL, CONFLICT_COLLISION)

#: An OBSERVED event that is deliberately not in `REASONS`, because on its own
#: it never earns a cut. It is what a `same_treatment` hold is a hold OF.
EVENT_REGIME_CHANGE = "regime_change"


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
