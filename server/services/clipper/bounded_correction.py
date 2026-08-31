"""The one correction R7 allows, and the four conditions it has to meet.

§R7's gate is "at most one correction; the rerun produces a stable result; a
model failure does not block the job but does not erase the warning either".
This is the correction half. It proposes at most one, and only when it can show
that applying it would fix the thing it is correcting.

WHY MOVING THE CAPTION IS THE ONLY ONE ON OFFER. It is the only failure in the
seven whose fix already exists, is deterministic, and can be checked before it
is applied: `resolve_position` is the function the renderer already uses, and
`caption_placement` can be asked whether the proposed position still lands on
the thing that failed. Everything else on the list would need a rule nobody has
written — a duplicate caption layer cannot be moved anywhere, a boundary is a
different window rather than a different render, and a stale fingerprint means
the artefact is not the one the plan describes.

AND IT IS NOT EVERY WAY THE CAPTIONS CHECK CAN FAIL. That check answers three
questions, and only one of them is about position: a palette floor of 2.33 is
the same 2.33 anywhere on the frame, and two caption layers are two wherever
either sits. The proposer read the failing check's NAME, so it offered to move
the caption out from under `Neon Pop`'s colours. It reads the reason now, and
`publish_checks.ON_A_FACE` is the one it acts on.

FOUR CONDITIONS, and a proposal that misses any of them is not made.

    IT MUST BE ACTIONABLE     only a `revise` failure. A `reject` is by
                              definition a finding nothing can act on, and
                              proposing a move for one would be a correction
                              that fixes nothing while spending the single
                              allowance.
    IT MUST BE THE ONLY ONE   §R7 allows one. A second is not a smaller version
                              of the rule.
    IT MUST BE VERIFIED       the proposed position is run back through the
                              same check, and the verifier is REQUIRED rather
                              than optional. A correction that is not tested
                              before it is applied is a guess with a commit
                              message — and the version that defaulted the
                              verifier to absent made the untested path the
                              common one. Only `False` clears it; `None` is a
                              check that could not answer.
    IT MUST BE STABLE         proposing again on the corrected state returns
                              nothing. A proposer that keeps proposing cannot
                              converge, which is exactly what the gate's "rerun
                              produces a stable result" forbids.

THE KEEP-OUTS COME FROM THE CALLER, and that is not a convenience. At render
time `clipper_captions` resolves the position against the stored safe zones PLUS
`panels_to_keep_out(panels, shots)`, and `panels` is never written to the
sidecar — so a proposer that dug them out itself would be resolving against a
smaller set than the renderer had, and would propose a move the renderer would
not have made. Whoever has the panels passes them in.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.clipper import publish_checks as pk
from services.clipper import publish_preflight as pf

__all__ = ["MOVE_CAPTION", "KINDS", "propose"]

#: The only correction there is. A closed list of one is still a closed list,
#: and the name is what a later reader will look for when a second is added.
MOVE_CAPTION = "move_the_caption"
KINDS: tuple[str, ...] = (MOVE_CAPTION,)

#: Why a failure the caption could in principle be moved away from is left
#: alone. Each of these is a proposal that was considered and withheld.
NOT_ACTIONABLE = "the_failure_is_a_reject_and_nothing_can_act_on_it"
ALREADY_CORRECTED = "one_correction_has_already_been_applied"
NO_GEOMETRY = "no_output_height_or_keep_out_set_to_resolve_against"
NO_BETTER_PLACE = "the_resolver_offers_no_position_that_fixes_it"
NOT_THE_CAPTION = "no_failing_check_a_move_could_fix"
#: The captions check failed for a reason a MOVE cannot repair. A palette floor
#: is the same colours wherever the caption sits, and a duplicate layer is not
#: about position at all — but both arrive as a failing `captions` check, and
#: the proposer used to read the check's NAME and offer a move.
NOT_REPAIRABLE_BY_MOVING = "the_caption_failure_is_not_one_a_move_repairs"
#: Nobody supplied the verification, or it did not answer with a bool. A
#: proposal is only as good as the check that it fixes anything.
NOT_VERIFIED = "no_verification_that_the_proposed_position_fixes_it"


def _failing(results: dict[str, Any]) -> list[str]:
    return [name for name in pf.CHECKS
            if isinstance(results.get(name), dict)
            and results[name].get("state") == pf.FAIL]


def propose(results: dict[str, Any], *, position: str, keep_out: Sequence[dict],
            out_h: int = 1920, out_w: int = 1080,
            already: Sequence[str] = (),
            still_lands_on_a_face=None) -> dict:
    """`{"kind": ..., "y_pct": ...}` or `{"kind": None, "why": ...}`.

    `still_lands_on_a_face` is the caller's own verification: given a candidate
    `y_pct`, it answers whether the caption would still sit on the face that
    caused the failure. It is passed in rather than computed because answering
    it needs the per-shot evidence in output pixels, which only the caller has —
    and a proposer that guessed at it would be proposing a fix it had not
    checked.
    """
    from services.clipper.captions import resolve_position

    if already:
        # §R7 allows one. The count is the rule, not a suggestion.
        return {"kind": None, "why": ALREADY_CORRECTED,
                "already": list(already)}

    failing = _failing(results)
    if any(results[name].get("severity") == pf.REJECTABLE for name in failing):
        return {"kind": None, "why": NOT_ACTIONABLE, "failing": failing}
    if pf.CAPTIONS not in failing:
        return {"kind": None, "why": NOT_THE_CAPTION, "failing": failing}

    # THE CHECK'S NAME IS NOT THE DEFECT. `captions` fails for a caption on a
    # face, for a palette whose floor is 2.33, and for two caption layers, and
    # only the first is about WHERE the caption is. Reading "the captions check
    # is failing and it is revisable" as "move it" offered to repair `Neon Pop`
    # by moving the text, which changes nothing about the colours — and spent
    # the single allowance the gate grants doing it.
    why = results[pf.CAPTIONS].get("why")
    if str(why) != pk.ON_A_FACE:
        return {"kind": None, "why": NOT_REPAIRABLE_BY_MOVING,
                "because": why}

    if not isinstance(out_h, int) or out_h <= 0 or keep_out is None:
        return {"kind": None, "why": NO_GEOMETRY}

    # VERIFIED IS NOT OPTIONAL. The verifier defaulted to absent, so the common
    # path — nobody passed one — proposed a move that nothing had checked, which
    # is the state the four conditions exist to forbid. And a verifier that
    # answers `None` is not answering no: only `False` is.
    if not callable(still_lands_on_a_face):
        return {"kind": None, "why": NOT_VERIFIED}

    try:
        _x, y_pct = resolve_position(
            str(position or "bottom"),
            {"safe_zones": {"keep_out": list(keep_out)}},
            out_w=out_w, out_h=out_h)
    except Exception:
        return {"kind": None, "why": NO_GEOMETRY}

    # VERIFIED BEFORE IT IS OFFERED. A correction that is not tested before it
    # is applied is a guess with a commit message — and the single allowance is
    # spent either way, so a proposal that does not fix the failure is worse
    # than none.
    try:
        still = still_lands_on_a_face(y_pct)
    except Exception:
        return {"kind": None, "why": NO_BETTER_PLACE}
    if still is not False:
        # STRICTLY `False`. `None` is a verifier that could not tell, and `0` is
        # not a verdict — `0 == False` is True, so a truth test would have read
        # an unanswered check as an all-clear.
        return {"kind": None,
                "why": NO_BETTER_PLACE if still is True else NOT_VERIFIED,
                "would_have_been": round(float(y_pct), 4)}

    return {"kind": MOVE_CAPTION, "y_pct": round(float(y_pct), 4),
            "because": why, "resolved_against": len(list(keep_out))}
