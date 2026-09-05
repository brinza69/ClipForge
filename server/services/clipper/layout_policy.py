"""Does this source have a SECOND region worth pointing a camera at.

THE DEFECT THIS EXISTS TO STOP. `dynamic_cameras.camera_rects` builds `game`
and `game_tight` unconditionally, from geometry alone: "everything to the right
of the facecam, minus the chat strip". Nothing asks whether there IS anything
there. On `pilotf81b` — one man talking to camera on a street, no gameplay, no
second source — the planner still cuts to that rectangle, and the rectangle is
the building behind him.

MEASURED ON THE DELIVERED WINDOWS, not on the planner's rectangles. Across the
stored corpus, 377 of 2,016 delivered windows (18.7%) do not contain the clip's
own average face centre — 798 seconds of video — and 372 of those 377 are a
`game` or `game_tight` camera. Re-planned today, `30d7c6d4eae5` spends 6.8 s of
18.1 s framed away from the only person in the source, and `95e4b5b241a8` 12.4 s
of 37.0 s, with `speech` between 0.53 and 1.0 throughout: he is talking, and the
frame is the street.

THE EXISTING GUARD DOES NOT CATCH IT AND CANNOT. `_pick_camera` already refuses
a game camera when the band is not `alive`, and `alive` is motion, detail and
UI — it rejects a black loading screen. A street with lights and passers-by is
not a loading screen. It moves and it has detail, so it is "alive", and it is
still not a second subject.

WHY A POLICY AND NOT A DETECTOR. The obvious discriminators were tried on the
corpus and none of them is a calibration. Face-position spread is BACKWARDS —
`pilotf81b` is the most stable of the four pilots (IQR 0.21 face-widths against
1.47-2.49 for the composites), because a man holding a phone moves less than a
streamer in an inset. Face width as a fraction of the frame does separate them
(19.6% against 6.3-11.0%), and that is a threshold chosen on four sources with
the answer visible — exactly what `source_captions` documents as not being a
calibration, and this repo has paid for that mistake once already.

So the switch is a person's, in the same shape `caption_policy` uses and for the
same reason:

    somebody said so   -> applied
    the numbers say so -> recorded, and the second camera stays available

`False` is the expensive answer here, in the mirror of `caption_policy`: a wrong
`False` costs cutaways on a source that had a real second subject, which is
visible and recoverable. A wrong `True` is what ships today.
"""

from __future__ import annotations

from typing import Any

__all__ = ["TWO_REGIONS", "ONE_REGION", "HUMAN", "AGENT", "DEFAULT",
           "DECIDED_BY", "SETTING", "decide", "off_subject_seconds"]

#: What the planner is allowed to point at.
TWO_REGIONS = "two_regions"
ONE_REGION = "one_region"

HUMAN = "human"
#: An agent that has actually looked at the source, and says so. Accepted here
#: and not in `caption_policy` because the evidence is different in kind: this
#: asks whether the frame is a composite, which a contact sheet settles, where
#: caption suppression turns on legibility nobody can read off a still.
AGENT = "agent"
#: Nobody has answered. NOT a `False`, and naming it keeps the two apart.
DEFAULT = "default"
DECIDED_BY: tuple[str, ...] = (HUMAN, AGENT, DEFAULT)

#: The project setting. Three-valued, for the reason `caption_policy.SETTING`
#: is: `True` and `False` are answers and absent is not one of them.
SETTING = "source_has_a_second_region"

_NOBODY = "nobody_has_said_whether_this_source_has_a_second_region"
_SAID_YES = "a_person_or_an_agent_declared_a_second_region_in_the_source"
_SAID_NO = "a_person_or_an_agent_declared_this_source_a_single_camera"


def decide(setting: Any = None, *, by: Any = None,
           evidence: Any = None) -> dict:
    """`{"regions", "why", "decided_by", "evidence"}`.

    `setting` is the project's three-valued answer. `evidence` is whatever
    measurement was taken — `off_subject_seconds`, a face-width fraction — and
    it is RECORDED, never applied: none of it is calibrated, and a threshold
    chosen on four sources deciding what a renderer frames is the mistake this
    module is shaped against.
    """
    out: dict[str, Any] = {"schema": "clipper_layout_policy_v1",
                           "regions": TWO_REGIONS, "why": _NOBODY,
                           "decided_by": DEFAULT,
                           "evidence": evidence if isinstance(evidence, dict)
                           else None}
    # `isinstance(setting, bool)` and not a truth test: a form posting the
    # string "false" is truthy, and `0 == False` is True, so an integer would
    # pass as a verdict. The same guard `caption_policy` needs, for the same
    # reason.
    if not isinstance(setting, bool):
        return out
    who = by if by in (HUMAN, AGENT) else HUMAN
    out["decided_by"] = who
    if setting:
        out["why"] = _SAID_YES
        return out
    out["regions"] = ONE_REGION
    out["why"] = _SAID_NO
    return out


def off_subject_seconds(plan: Any) -> dict:
    """How long the DELIVERED windows exclude the clip's own subject centre.

    The measurement behind the finding, kept here so a report and a policy
    reason cannot drift apart. It is a SIGNAL and the docstring of `decide`
    says what that means: `dynamic_plan["subject"]["face"]` is a clip-wide
    average, so a shot that excludes it may be pointing somewhere on purpose —
    on a genuine two-source stream, framing the game while the streamer talks is
    the whole grammar. What it may not be is invisible, which is what it was.

    Reads the delivered window through `evidence_map.crop_window`, never
    `shot["rect"]`: 837 of the corpus's 1,965 crop shots differ, and the
    planner's rectangle is not what the viewer receives.
    """
    from services.clipper import evidence_map as em
    from services.clipper.dynamic_geometry import canvas_size

    out: dict[str, Any] = {"seconds": None, "total_s": None, "shots": 0,
                           "off_shots": 0, "refused": 0, "why": None,
                           "speaking_off_s": None}
    if not isinstance(plan, dict):
        out["why"] = "there_is_no_plan_to_read"
        return out
    try:
        sw, sh = int(plan.get("src_w") or 0), int(plan.get("src_h") or 0)
    except (TypeError, ValueError):
        sw = sh = 0
    face = ((plan.get("subject") or {}).get("face") or {}
            if isinstance(plan.get("subject"), dict) else {})
    cx, cy = face.get("cx"), face.get("cy")
    if sw < 1 or sh < 1 or cx is None or cy is None:
        # NOT zero seconds. A clip whose subject nobody located has an unknown
        # amount of off-subject video, and reporting 0 would read as clean.
        out["why"] = "no_source_geometry_or_no_subject_track"
        return out
    _cw, _ch, off_y = canvas_size(sw, sh)
    total = off = speaking_off = 0.0
    shots = [s for s in (plan.get("shots") or []) if isinstance(s, dict)]
    style = plan.get("style") if isinstance(plan.get("style"), dict) else {}
    for shot in shots:
        try:
            dur = float(shot["t1"]) - float(shot["t0"])
        except (KeyError, TypeError, ValueError):
            out["refused"] += 1
            continue
        if dur <= 0:
            out["refused"] += 1
            continue
        total += dur
        out["shots"] += 1
        crop = em.crop_window(shot, src_w=sw, src_h=sh, style=style)
        if isinstance(crop, str):
            # A window nobody could compute is not a window that held the
            # subject. Counted on its own row and kept out of the seconds.
            out["refused"] += 1
            continue
        x, y, w, h = crop
        if x <= float(cx) <= x + w and y <= float(cy) + off_y <= y + h:
            continue
        off += dur
        out["off_shots"] += 1
        try:
            if float(shot.get("speech") or 0.0) > 0.0:
                speaking_off += dur
        except (TypeError, ValueError):
            pass
    out["total_s"] = round(total, 2)
    out["seconds"] = round(off, 2)
    out["speaking_off_s"] = round(speaking_off, 2)
    return out
