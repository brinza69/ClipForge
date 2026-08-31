"""Batch R7: the publish verdict, and the word §22 had no room for.

The property every test here circles is one sentence: APPROVE is reachable only
when all seven checks passed, never when none of them failed. `review.verdict`
has the opposite rule — `verdict([])` is APPROVE — and 12 of the 101 stored
clips reach it with `sampled: 0`, which is 18% of every approval on disk.
"""

from __future__ import annotations

import pytest

from services.clipper import publish_preflight as pf


def _all(state=pf.PASS, **over):
    """Every check answering the same way, so a test can move one of them."""
    base = {name: pf.check(state, why=None if state == pf.PASS else "measured")
            for name in pf.CHECKS}
    base.update(over)
    return base


# --- the rule the batch exists for -------------------------------------------


def test_nothing_checked_is_not_approved():
    """The whole of R7 in one assertion."""
    assert pf.verdict({}) == pf.UNDECIDED
    assert pf.verdict(None) == pf.UNDECIDED
    assert pf.preflight(None)["verdict"] == pf.UNDECIDED
    assert pf.preflight({})["established"] == "0/7"


def test_one_unmeasured_check_is_enough_to_withhold_approval():
    """Six passes and one thing nobody could look at is not a clean bill."""
    for name in pf.CHECKS:
        results = _all(**{name: pf.check(pf.UNAVAILABLE, why="no_detector")})
        assert pf.verdict(results) == pf.UNDECIDED, name


def test_approve_needs_all_seven():
    got = pf.preflight(_all())
    assert got["verdict"] == pf.APPROVE
    assert got["established"] == "7/7"
    assert got["unavailable"] == [] and got["failed"] == []


def test_a_failure_outranks_an_absence():
    """A clip with something wrong AND something unmeasured is REVISE: the
    finding is actionable and the gap does not make it less so."""
    results = _all(**{pf.CAPTIONS: pf.check(pf.FAIL, why="duplicated",
                                            severity=pf.REVISABLE),
                      pf.SUBJECT: pf.check(pf.UNAVAILABLE, why="no_faces")})
    assert pf.verdict(results) == pf.REVISE


def test_a_rejectable_failure_outranks_everything():
    results = _all(**{pf.GEOMETRY: pf.check(pf.FAIL, why="wrong_aspect",
                                            severity=pf.REJECTABLE),
                      pf.CAPTIONS: pf.check(pf.FAIL, why="duplicated",
                                            severity=pf.REVISABLE),
                      pf.BOUNDARY: pf.check(pf.UNAVAILABLE, why="no_words")})
    assert pf.verdict(results) == pf.REJECT


# --- a missing answer is not an answer ---------------------------------------


def test_a_check_nobody_supplied_is_unavailable_not_absent_from_the_report():
    got = pf.preflight({pf.GEOMETRY: pf.check(pf.PASS)})
    assert set(got["checks"]) == set(pf.CHECKS), "all seven are always reported"
    assert got["checks"][pf.SUBJECT]["state"] == pf.UNAVAILABLE
    assert got["checks"][pf.SUBJECT]["why"] == "no_result_supplied"
    assert got["verdict"] == pf.UNDECIDED
    assert got["established"] == "1/7"


def test_a_result_that_is_not_a_result_is_refused_not_believed():
    """Present and unreadable is not the same fact as absent, and neither may
    be read as an answer."""
    for bad in ("pass", 7, None, [], {"state": "fine"},
                {"state": pf.FAIL, "severity": "bad"}):
        got = pf.preflight({pf.GEOMETRY: bad})
        assert got["checks"][pf.GEOMETRY]["state"] == pf.UNAVAILABLE, repr(bad)
        assert got["verdict"] == pf.UNDECIDED, repr(bad)
        if bad is not None:
            assert any(pf.MALFORMED in line for line in got["refused"]), bad


def test_a_check_outside_the_seven_is_refused():
    """A report that quietly grows a check is a report nobody agreed to, and
    the name would ride into a sidecar a later reader treats as the contract."""
    got = pf.preflight({**_all(), "vibes": pf.check(pf.PASS)})
    assert "vibes" not in got["checks"]
    assert any(pf.NOT_IN_THE_LIST in line for line in got["refused"])
    assert got["verdict"] == pf.APPROVE, "the seven still decide it"


# --- a check has to be able to say why ---------------------------------------


def test_a_non_passing_check_must_say_why():
    """A reason that has to be written is a reason somebody had to have."""
    with pytest.raises(ValueError):
        pf.check(pf.UNAVAILABLE)
    with pytest.raises(ValueError):
        pf.check(pf.FAIL, severity=pf.REVISABLE)


def test_a_failure_must_carry_a_severity():
    """REVISE and REJECT are different answers and the check is the only thing
    that knows which one it earned."""
    with pytest.raises(ValueError):
        pf.check(pf.FAIL, why="something")
    with pytest.raises(ValueError):
        pf.check(pf.FAIL, why="something", severity="bad")
    assert pf.check(pf.FAIL, why="x", severity=pf.REJECTABLE)["severity"] == "reject"


def test_a_state_outside_the_three_is_refused_at_the_source():
    with pytest.raises(ValueError):
        pf.check("probably_fine", why="x")


# --- the gate's own rule -----------------------------------------------------


def test_more_than_one_correction_is_itself_a_finding():
    """§R7's gate is "at most one correction". A preflight that keeps
    correcting cannot converge, and the rerun stability the gate asks for is
    exactly what that destroys."""
    one = pf.preflight(_all(), corrections=["caption moved to 0.62"])
    assert one["refused"] == [] and one["verdict"] == pf.APPROVE

    two = pf.preflight(_all(), corrections=["caption moved", "shot dropped"])
    assert any("more_than_one_correction" in line for line in two["refused"])
    assert two["corrections"] == ["caption moved", "shot dropped"]


def test_it_never_claims_to_have_decided_anything():
    assert pf.preflight(_all())["applied"] is False


# --- what the vocabulary is ---------------------------------------------------


def test_the_seven_checks_are_the_ones_the_batch_named():
    assert len(pf.CHECKS) == 7
    assert set(pf.CHECKS) == {
        pf.GEOMETRY, pf.EQUIVALENCE, pf.SUBJECT, pf.FRAME, pf.CAPTIONS,
        pf.BOUNDARY, pf.PROVENANCE}


def test_undecided_is_a_verdict_and_not_a_flag_beside_one():
    """Keeping APPROVE and adding an `established` flag would leave the word
    that carries the decision saying the wrong thing — the shape of
    `changed_without_moving`, computed and wired to nothing."""
    got = pf.preflight(_all(**{pf.FRAME: pf.check(pf.UNAVAILABLE,
                                                  why="no_recogniser")}))
    assert got["verdict"] == pf.UNDECIDED
    assert got["verdict"] != pf.APPROVE
    assert got["established"] == "6/7"
