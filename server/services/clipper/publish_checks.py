"""The seven §R7 checks, answered from one clip's artefacts.

`publish_preflight` owns the vocabulary and the verdict. This owns the reading:
it takes a sidecar and whatever else is on disk and produces one `check()` per
item on §R7's list.

THE RULE THIS FILE IS WRITTEN AROUND. Every one of the seven has to be able to
come back `unavailable`, and most of them will. That is not a gap in the module,
it is the answer: a publish decision today rests on less than the list suggests,
and the point of R7 is that the verdict says so instead of approving anyway.

WHAT EACH ONE READS, and where it stops:

    geometry_and_duration     `edit_quality.clip_report` — a duration that is
                              present and unusable is a DEFECT, one that is
                              absent is unknown, and the report already draws
                              that line
    cut_equivalence_...       the same report's `equivalent_cuts`; the profile
                              rhythm needs `dynamic_rhythm_pace`, whose bands
                              are compared and never enforced, so a `below` is
                              not a failure here either
    subject_present_...       `dynamic_regimes` says `creator_unknown` where the
                              face track is shorter than the clip; nothing yet
                              says which profiles REQUIRE a subject, so this is
                              unavailable until that list exists
    usable_frame_in_fit...    `source_chrome` on the rendered export
    captions_...              three sources at once: `source_captions` for a
                              duplicate layer, `caption_placement` for what the
                              caption covers, `caption_contrast` for whether the
                              palette can be read at all
    boundary_complete         `boundary_completion.eligibility`
    provenance_complete       `edit_quality.fingerprint_status`, which
                              RECOMPUTES the digest rather than copying it —
                              copying would let a plan edited after the render
                              carry a stale fingerprint and pass

A CHECK NEVER RAISES. A preflight that dies on one clip reports on the clips
before it and says nothing about the one that killed it, which is worse than any
verdict it could have returned.
"""

from __future__ import annotations

from typing import Any

from services.clipper import publish_preflight as pf

__all__ = ["geometry", "equivalence", "subject", "frame", "captions",
           "boundary", "provenance", "checks_for"]


def _report(sidecar: Any) -> dict | None:
    from services.clipper.edit_quality import clip_report

    try:
        return clip_report(sidecar)
    except Exception:
        return None


def geometry(sidecar: Any) -> dict:
    """Duration and composition, from the report that already draws the line
    between an absent value and a present impossible one."""
    report = _report(sidecar)
    if report is None:
        return pf.check(pf.UNAVAILABLE, why="sidecar_unreadable")
    defects = [d for d in (report.get("defects") or [])]
    if defects:
        # A composition that is present and impossible, or a duration that is,
        # is corruption rather than age — `edit_quality` decides which, and this
        # only carries the answer.
        return pf.check(pf.FAIL, why=",".join(sorted(defects)),
                        severity=pf.REJECTABLE, evidence=defects)
    if report.get("duration_s") == "unavailable":
        return pf.check(pf.UNAVAILABLE, why="no_duration",
                        evidence=report.get("duration_clock"))
    return pf.check(pf.PASS, evidence={"duration_s": report["duration_s"],
                                       "clock": report.get("duration_clock")})


def equivalence(sidecar: Any) -> dict:
    """Cuts that change nothing, and the profile's rhythm.

    Only the first half is answerable from a sidecar. `dynamic_rhythm_pace`
    compares against §4's bands and never enforces them, so a `below` is a
    measurement rather than a defect and does not belong in a publish gate
    until somebody decides it does.
    """
    report = _report(sidecar)
    if report is None:
        return pf.check(pf.UNAVAILABLE, why="sidecar_unreadable")
    cuts = report.get("equivalent_cuts")
    if cuts == "unavailable":
        return pf.check(pf.UNAVAILABLE, why="shot_list_unreadable_or_absent")
    if cuts:
        # A cut between two shots that deliver the same picture is a cut the
        # viewer cannot see. R1 found 116 of them across the pilots.
        return pf.check(pf.FAIL, why=f"{cuts}_cuts_change_nothing",
                        severity=pf.REVISABLE, evidence=cuts)
    return pf.check(pf.PASS, evidence={"equivalent_cuts": 0})


def subject(sidecar: Any) -> dict:
    """Whether the subject is present when the profile requires it.

    UNAVAILABLE, and not because the signal is missing. `dynamic_regimes`
    reports `creator_unknown` where the face track is shorter than the clip, so
    the presence half exists — what does not exist anywhere is the list of which
    profiles REQUIRE a subject. Answering without it would be inventing the
    requirement in the same breath as checking it.
    """
    return pf.check(pf.UNAVAILABLE,
                    why="no_list_of_which_profiles_require_a_subject")


def frame(chrome: Any) -> dict:
    """A usable frame in `fit`, and no dominant browser chrome.

    `source_chrome` answers the second half and only the second half: it is a
    WARNING about publishing, never a verdict, so a `detected` is a revisable
    finding and a `not_detected` is not a pass — its recall is low and
    undemonstrated, and the module says so.
    """
    from services.clipper import source_chrome as sc

    if not isinstance(chrome, dict) or chrome.get("state") not in sc.STATES:
        return pf.check(pf.UNAVAILABLE, why="no_chrome_detection")
    state = chrome["state"]
    if state == sc.DETECTED:
        return pf.check(pf.FAIL, why="player_chrome_in_the_export",
                        severity=pf.REVISABLE,
                        evidence={"frames": chrome.get("frames_with_a_control")})
    if state == sc.UNAVAILABLE:
        return pf.check(pf.UNAVAILABLE,
                        why=chrome.get("why_unavailable") or "undecided")
    # `not_detected` is a weak look, not a clean bill — and the other half of
    # this check, whether a `fit` shot's frame is usable at all, has no source.
    return pf.check(pf.UNAVAILABLE,
                    why="chrome_not_detected_is_not_clean_and_the_fit_frame_"
                        "check_has_no_source")


def captions(placement: Any, contrast: Any, source: Any) -> dict:
    """Duplicated, covered, or unreadable — three sources, one answer.

    A DUPLICATE LAYER IS THE REJECTABLE ONE. 15 of 15 go ghost exports shipped
    with two caption systems, which is the defect this whole plan opened on, and
    it is not something a bounded correction can move.
    """
    from services.clipper import caption_placement as cp
    from services.clipper import source_captions as scap

    reasons: list[str] = []
    if isinstance(source, dict) and source.get("state") == scap.PRESENT:
        return pf.check(pf.FAIL, why="the_source_already_carries_captions",
                        severity=pf.REJECTABLE, evidence=source.get("band"))

    if isinstance(contrast, dict) and not contrast.get("refused"):
        for part in ("fill", "highlight"):
            leg = contrast.get(part)
            if isinstance(leg, dict) and not leg.get("clears_large_text"):
                reasons.append(f"{part}_floor_{leg.get('floor')}")
    else:
        reasons.append("contrast_unavailable")

    if isinstance(placement, dict) and not placement.get("refused"):
        if placement.get("worst_share_complete") is not True:
            # A floor is not a coverage. Naming the worst shot is a claim about
            # all of them, and it is not established while any share is partial.
            reasons.append("placement_not_established")
        elif cp.ON_FACE in (placement.get("lands_on") or []):
            return pf.check(pf.FAIL, why="the_caption_sits_on_a_face",
                            severity=pf.REVISABLE,
                            evidence=placement.get("worst"))
    else:
        reasons.append("placement_unavailable")

    if any(r.endswith("unavailable") or r == "placement_not_established"
           for r in reasons):
        return pf.check(pf.UNAVAILABLE, why=",".join(sorted(reasons)))
    if reasons:
        return pf.check(pf.FAIL, why=",".join(sorted(reasons)),
                        severity=pf.REVISABLE, evidence=reasons)
    return pf.check(pf.PASS)


def boundary(view: Any) -> dict:
    """Is the window a complete thought — `boundary_completion`'s own verdict.

    Its `eligible` is a THREE-VALUED answer: True, False, and None for "the
    transcript could not say". None is unavailable here, not a pass, which is
    the whole reason that field is three-valued.
    """
    if not isinstance(view, dict) or "eligible" not in view:
        return pf.check(pf.UNAVAILABLE, why="no_boundary_view")
    eligible = view.get("eligible")
    if eligible is None:
        return pf.check(pf.UNAVAILABLE,
                        why=str(view.get("why") or "boundary_unavailable"))
    if eligible is False:
        return pf.check(pf.FAIL, why=str(view.get("why") or "incomplete"),
                        severity=pf.REVISABLE, evidence=view.get("defects"))
    return pf.check(pf.PASS, evidence=view.get("defects"))


def provenance(sidecar: Any) -> dict:
    """The stored digest, RECOMPUTED. Copying it would make it decorative: a
    plan edited after the render would carry a stale fingerprint and pass."""
    from services.clipper.edit_quality import (FINGERPRINT_MISMATCH,
                                               FINGERPRINT_VALID,
                                               fingerprint_status)

    if not isinstance(sidecar, dict):
        return pf.check(pf.UNAVAILABLE, why="sidecar_unreadable")
    try:
        status = fingerprint_status(sidecar)
    except Exception:
        return pf.check(pf.UNAVAILABLE, why="fingerprint_unreadable")
    if status == FINGERPRINT_VALID:
        return pf.check(pf.PASS, evidence=status)
    if status == FINGERPRINT_MISMATCH:
        # The artefact is not the one the plan describes. Nothing a correction
        # can move, which is what makes it rejectable.
        return pf.check(pf.FAIL, why="fingerprint_mismatch",
                        severity=pf.REJECTABLE, evidence=status)
    return pf.check(pf.UNAVAILABLE, why="no_stored_fingerprint")


def checks_for(sidecar: Any, *, chrome: Any = None, placement: Any = None,
               contrast: Any = None, source_captions: Any = None,
               boundary_view: Any = None) -> dict[str, dict]:
    """All seven, from whatever is available. Missing inputs stay unavailable."""
    return {
        pf.GEOMETRY: geometry(sidecar),
        pf.EQUIVALENCE: equivalence(sidecar),
        pf.SUBJECT: subject(sidecar),
        pf.FRAME: frame(chrome),
        pf.CAPTIONS: captions(placement, contrast, source_captions),
        pf.BOUNDARY: boundary(boundary_view),
        pf.PROVENANCE: provenance(sidecar),
    }
