"""Batch R7: the one correction, and the four conditions it has to meet.

§R7's gate is "at most one correction; the rerun produces a stable result". The
tests are the four conditions: actionable, only one, verified before it is
offered, and stable when run again on its own output.
"""

from __future__ import annotations

from services.clipper import bounded_correction as bc
from services.clipper import publish_preflight as pf

KEEP_OUT = [{"x": 0, "y": 1200, "w": 1080, "h": 600, "kind": "face"}]


def _failing(severity=pf.REVISABLE, why="the_caption_sits_on_a_face"):
    return {pf.CAPTIONS: pf.check(pf.FAIL, why=why, severity=severity)}


def _clean():
    return {name: pf.check(pf.PASS) for name in pf.CHECKS}


# --- it must be actionable ---------------------------------------------------


def test_a_reject_is_never_corrected():
    """A `reject` is by definition a finding nothing can act on, and proposing a
    move for one spends the single allowance on a fix that fixes nothing."""
    got = bc.propose(_failing(severity=pf.REJECTABLE), position="bottom",
                     keep_out=KEEP_OUT)
    assert got["kind"] is None and got["why"] == bc.NOT_ACTIONABLE


def test_a_failure_the_caption_cannot_move_away_from_is_left_alone():
    results = _clean()
    results[pf.BOUNDARY] = pf.check(pf.FAIL, why="end_mid_sentence",
                                    severity=pf.REVISABLE)
    got = bc.propose(results, position="bottom", keep_out=KEEP_OUT)
    assert got["kind"] is None and got["why"] == bc.NOT_THE_CAPTION


def test_nothing_failing_means_nothing_to_correct():
    got = bc.propose(_clean(), position="bottom", keep_out=KEEP_OUT)
    assert got["kind"] is None and got["why"] == bc.NOT_THE_CAPTION


# --- it must be the only one -------------------------------------------------


def test_a_second_correction_is_refused():
    """§R7 allows one. A second is not a smaller version of the rule."""
    got = bc.propose(_failing(), position="bottom", keep_out=KEEP_OUT,
                     already=["caption moved to 0.31"])
    assert got["kind"] is None and got["why"] == bc.ALREADY_CORRECTED
    assert got["already"] == ["caption moved to 0.31"]


# --- it must be verified before it is offered --------------------------------


def test_a_move_that_does_not_fix_it_is_not_offered():
    """A correction that is not tested before it is applied is a guess with a
    commit message — and the single allowance is spent either way."""
    got = bc.propose(_failing(), position="bottom", keep_out=KEEP_OUT,
                     still_lands_on_a_face=lambda y: True)
    assert got["kind"] is None and got["why"] == bc.NO_BETTER_PLACE
    assert "would_have_been" in got, "and it says what it withheld"


def test_a_verifier_that_throws_withholds_the_proposal():
    """An answer nobody could obtain is not a yes."""
    def boom(_y):
        raise RuntimeError("no evidence")

    got = bc.propose(_failing(), position="bottom", keep_out=KEEP_OUT,
                     still_lands_on_a_face=boom)
    assert got["kind"] is None and got["why"] == bc.NO_BETTER_PLACE


def test_a_move_that_fixes_it_is_offered_with_its_reason():
    got = bc.propose(_failing(), position="bottom", keep_out=KEEP_OUT,
                     still_lands_on_a_face=lambda y: False)
    assert got["kind"] == bc.MOVE_CAPTION
    assert 0.0 <= got["y_pct"] <= 1.0
    assert got["because"] == "the_caption_sits_on_a_face"
    assert got["resolved_against"] == 1


# --- it must be stable -------------------------------------------------------


def test_proposing_again_on_the_corrected_state_returns_nothing():
    """A proposer that keeps proposing cannot converge, which is exactly what
    "the rerun produces a stable result" forbids."""
    first = bc.propose(_failing(), position="bottom", keep_out=KEEP_OUT,
                       still_lands_on_a_face=lambda y: False)
    assert first["kind"] == bc.MOVE_CAPTION

    # The correction was applied, so the caption check now passes and the
    # correction is on the record. Both routes have to stop.
    assert bc.propose(_clean(), position="bottom",
                      keep_out=KEEP_OUT)["kind"] is None
    again = bc.propose(_failing(), position="bottom", keep_out=KEEP_OUT,
                       already=[bc.MOVE_CAPTION],
                       still_lands_on_a_face=lambda y: False)
    assert again["kind"] is None and again["why"] == bc.ALREADY_CORRECTED


def test_the_same_inputs_give_the_same_proposal():
    """`resolve_position` is deterministic, and the gate asks for a stable
    rerun rather than a stable-looking one."""
    runs = [bc.propose(_failing(), position="bottom", keep_out=KEEP_OUT,
                       still_lands_on_a_face=lambda y: False)
            for _ in range(3)]
    assert len({r["y_pct"] for r in runs}) == 1


# --- the geometry it will not invent -----------------------------------------


def test_no_geometry_means_no_proposal():
    for kw in ({"out_h": 0}, {"out_h": "1920"}, {"keep_out": None}):
        got = bc.propose(_failing(), position="bottom",
                         **{"keep_out": KEEP_OUT, **kw})
        assert got["kind"] is None and got["why"] == bc.NO_GEOMETRY, kw


def test_the_keep_outs_come_from_the_caller_and_are_counted():
    """At render time the position is resolved against the stored safe zones
    PLUS `panels_to_keep_out(panels, shots)`, and `panels` never reaches the
    sidecar — so a proposer that dug them out itself would resolve against a
    smaller set than the renderer had."""
    got = bc.propose(_failing(), position="bottom",
                     keep_out=KEEP_OUT + [{"x": 0, "y": 0, "w": 200, "h": 200}],
                     still_lands_on_a_face=lambda y: False)
    assert got["resolved_against"] == 2


def test_the_kinds_list_is_closed():
    assert bc.KINDS == (bc.MOVE_CAPTION,)
