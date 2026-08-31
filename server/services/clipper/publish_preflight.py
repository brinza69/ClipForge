"""Is this clip fit to publish — Batch R7's verdict, and what it rests on.

§R7's goal is "no technically defective clip enters the board or the export
automatically". The defect it has to fix first is in the verdict itself.

`review.review_plan` has five failure paths — no shots, no opencv, a proxy it
cannot open, a proxy with no width, and every frame read failing — and all five
return `verdict: APPROVE`. Its docstring states the property on purpose: "a
review that fails must not lose an export. It returns a verdict of APPROVE with
a warning, which is the same shape as a clean pass and cannot block anything."

MEASURED on the corpus: 12 of 101 clips come back APPROVE with `sampled: 0`.
Nobody looked at a single frame of them. That is 18% of every approval on disk.

THE FOURTH WORD. §22's vocabulary is APPROVE / REVISE / REJECT, and it has no
way to say "nothing failed and nothing was checked". Two alternatives were
considered and rejected:

- making an unmeasurable check a `revise` finding folds two different facts —
  "there is something to fix" and "nobody could look" — under one word, and a
  reader acting on REVISE cannot tell which they have;
- keeping APPROVE and adding an `established` flag beside it leaves the word
  that carries the decision saying the wrong thing, and puts the fact that
  would invalidate it in a field nothing is obliged to read. That is exactly
  the shape of `changed_without_moving`: computed, printed, wired to nothing.

So UNDECIDED exists, and the rule is that it is never APPROVE. Failure-safe is
preserved by what UNDECIDED does downstream — it does not block a job, the same
as today — not by calling it a pass.

AN EMPTY CHECK LIST IS UNDECIDED, NOT APPROVE. `verdict([])` returning APPROVE
is the same defect one level up, and it is how a preflight that never ran
reports a clean bill.

THE SEVEN CHECKS ARE §R7's OWN LIST, as a closed vocabulary. Any of them may be
`unavailable`, and today most of them are: this module's first value is showing
what a publish decision actually rests on, which turns out to be less than the
list suggests.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["APPROVE", "REVISE", "REJECT", "UNDECIDED", "VERDICTS",
           "PASS", "FAIL", "UNAVAILABLE", "STATES", "CHECKS",
           "SEVERITIES", "check", "verdict", "preflight"]

#: §22's three, plus the one it has no word for.
APPROVE = "APPROVE"
REVISE = "REVISE"
REJECT = "REJECT"
#: Nothing failed, and at least one thing could not be looked at.
UNDECIDED = "UNDECIDED"
VERDICTS: tuple[str, ...] = (APPROVE, REVISE, REJECT, UNDECIDED)

#: What one check can say. `UNAVAILABLE` is not a mild `PASS`.
PASS = "pass"
FAIL = "fail"
UNAVAILABLE = "unavailable"
STATES: tuple[str, ...] = (PASS, FAIL, UNAVAILABLE)

#: How bad a failure is, in §22's terms. `revise` is a finding something can act
#: on — the caption can move — and `reject` is one nothing can.
REVISABLE = "revise"
REJECTABLE = "reject"
SEVERITIES: tuple[str, ...] = (REVISABLE, REJECTABLE)

#: §R7's own list, closed. A check outside it is a refusal rather than an
#: eighth opinion: the list is what the batch promised to answer, and a report
#: that quietly grows one is a report nobody agreed to.
GEOMETRY = "geometry_and_duration"
EQUIVALENCE = "cut_equivalence_and_profile_rhythm"
SUBJECT = "subject_present_when_the_profile_requires_it"
FRAME = "usable_frame_in_fit_and_no_dominant_browser_chrome"
CAPTIONS = "captions_not_duplicated_overlapping_or_unreadable"
BOUNDARY = "boundary_complete"
PROVENANCE = "provenance_complete"
CHECKS: tuple[str, ...] = (GEOMETRY, EQUIVALENCE, SUBJECT, FRAME, CAPTIONS,
                           BOUNDARY, PROVENANCE)

#: A check that was handed something it could not read. Distinct from one whose
#: INPUT was absent: nobody supplying a shot list and somebody supplying the
#: number 7 are different facts, and only the second is anybody's mistake.
MALFORMED = "check_result_is_not_a_record"
NOT_IN_THE_LIST = "check_is_not_one_of_the_seven"


def check(state: str, *, why: str | None = None,
          severity: str | None = None, evidence: Any = None) -> dict:
    """One check's answer, in the shape `preflight` reads.

    `why` is required for anything that is not a `PASS`, because "this failed"
    and "this could not be looked at" are both useless without it — and because
    a reason that has to be written is a reason somebody had to have.
    """
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}, got {state!r}")
    if state == FAIL and severity not in SEVERITIES:
        raise ValueError(f"a failure needs a severity in {SEVERITIES}")
    if state != PASS and not why:
        raise ValueError("a non-passing check has to say why")
    return {"state": state, "why": why, "severity": severity,
            "evidence": evidence}


def _usable(result: Any) -> bool:
    return (isinstance(result, dict) and result.get("state") in STATES
            and (result.get("state") != FAIL
                 or result.get("severity") in SEVERITIES))


def verdict(results: dict[str, Any]) -> str:
    """The clip's verdict over every check, including the ones that could not run.

    THE ORDER IS THE POINT. A rejectable failure outranks everything; then any
    other failure; then anything unmeasured. `APPROVE` is reachable only when
    every one of the seven checks passed — not when none of them failed.
    """
    if not isinstance(results, dict) or not results:
        # A preflight that never ran does not approve anything.
        return UNDECIDED
    states = []
    for name in CHECKS:
        result = results.get(name)
        if not _usable(result):
            # Absent, or present and unreadable. Both mean this check did not
            # produce an answer, and neither may be read as one.
            states.append((UNAVAILABLE, None))
            continue
        states.append((result["state"], result.get("severity")))

    if any(s == FAIL and sev == REJECTABLE for s, sev in states):
        return REJECT
    if any(s == FAIL for s, _sev in states):
        return REVISE
    if any(s == UNAVAILABLE for s, _sev in states):
        return UNDECIDED
    return APPROVE


def preflight(results: dict[str, Any] | None,
              *, corrections: Sequence[str] = ()) -> dict:
    """`publish_preflight_v1`: the verdict, what it rests on, and what it does not.

    `corrections` is what a bounded correction pass has already applied. §R7's
    gate is "at most one correction", so a second one is itself a finding: a
    preflight that keeps correcting is a preflight that cannot converge, and the
    rerun stability the gate asks for is exactly what that destroys.
    """
    given = results if isinstance(results, dict) else {}
    rows: dict[str, dict] = {}
    refused: list[str] = []

    for name, result in given.items():
        if name not in CHECKS:
            # NOT AN EIGHTH OPINION. A report that quietly grows a check is a
            # report nobody agreed to, and the name would ride into a sidecar
            # that a later reader would treat as part of the contract.
            refused.append(f"{name}: {NOT_IN_THE_LIST}")
            continue
        if not _usable(result):
            refused.append(f"{name}: {MALFORMED}")
            continue
        rows[name] = result

    for name in CHECKS:
        rows.setdefault(name, {"state": UNAVAILABLE,
                               "why": "no_result_supplied",
                               "severity": None, "evidence": None})

    got = verdict(rows)
    unavailable = [n for n in CHECKS if rows[n]["state"] == UNAVAILABLE]
    failed = [n for n in CHECKS if rows[n]["state"] == FAIL]

    extra = list(corrections)[1:]
    if extra:
        # The gate's own words: at most one correction. Reported rather than
        # enforced, because this module decides nothing — but a verdict that
        # rests on three corrections is not the same claim as one that rests on
        # none, and the count is the only thing that says which it is.
        refused.append(f"more_than_one_correction: {', '.join(extra)}")

    return {
        "schema": "publish_preflight_v1",
        "scope": "whether_this_clip_is_fit_to_publish_and_what_that_rests_on",
        "verdict": got,
        "checks": rows,
        "failed": failed,
        "unavailable": unavailable,
        # HOW MUCH OF THE DECISION IS MEASURED, as a fraction nobody has to
        # compute themselves. Seven of seven is the only state in which APPROVE
        # is reachable.
        "established": f"{len(CHECKS) - len(unavailable)}/{len(CHECKS)}",
        "corrections": list(corrections),
        "refused": refused,
        # Never true. This says what a publish decision would rest on; it does
        # not make one, and nothing downstream may treat it as having.
        "applied": False,
    }
