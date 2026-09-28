"""Batch S7: where a quote actually is, and what the resolver refuses to guess.

MEASURED, and it is why this module exists. Across six sources and 164 claims,
`ground_claim` binds 64.0% — and of the misses, 23.2% are verbatim somewhere
within ±120s. The words are there and the pointer drifted, and today that is
indistinguishable from an invented sentence because both come back
`grounded: False`.
"""

from __future__ import annotations

from services.clipper import quote_resolver as qr
from services.clipper import story_evidence as se


def _atoms(*spec) -> list[dict]:
    """`(start, text)` pairs into atoms, six seconds apart like the real ones."""
    return [{"i": i, "start": float(start), "end": float(start) + 6.0,
             "text": text} for i, (start, text) in enumerate(spec)]


CORPUS = _atoms(
    (0.0, "hello pre-notification gang"),
    (6.0, "we are going to become super humans"),
    (12.0, "not because we have super powers"),
    (18.0, "you know what I mean"),
    (24.0, "our ability to have a digital twin"),
    (30.0, "you know what I mean"),
)


# --- what it locates ---------------------------------------------------------


def test_a_quote_found_once_is_bound_with_its_time():
    got = qr.resolve("our ability to have a digital twin", CORPUS)
    assert got["state"] == qr.BOUND
    assert got["matched_t"] == 24.0 and got["occurrences"] == 1


def test_the_drift_is_the_whole_point():
    """`ground_claim` looks in the claimed atom plus one either side — about
    eighteen seconds. A claim pointing at 6.0 for words that live at 24.0 is
    outside it, and today the only thing recorded is that it failed."""
    got = qr.resolve("our ability to have a digital twin", CORPUS, claimed_t=6.0)
    assert got["state"] == qr.BOUND
    assert got["drift_s"] == 18.0


def test_a_quote_may_straddle_two_atoms():
    """`ground_claim` joins its atoms before matching, so this has to allow the
    same thing or it would be answering a different question."""
    got = qr.resolve("super humans not because we have", CORPUS)
    assert got["state"] == qr.BOUND and got["matched_t"] == 6.0


def test_a_drift_of_zero_is_not_the_same_as_no_claimed_time():
    exact = qr.resolve("you know what I mean", CORPUS[:4], claimed_t=18.0)
    assert exact["drift_s"] == 0.0
    silent = qr.resolve("you know what I mean", CORPUS[:4])
    assert silent["drift_s"] is None and silent["claimed_t"] is None


# --- what it refuses to guess ------------------------------------------------


def test_a_quote_found_twice_is_ambiguous_and_never_bound():
    """A filler phrase occurs dozens of times in a three-hour stream. Choosing
    the occurrence nearest the claim would MANUFACTURE a grounding the evidence
    does not support."""
    got = qr.resolve("you know what I mean", CORPUS, claimed_t=19.0)
    assert got["state"] == qr.AMBIGUOUS
    assert got["occurrences"] == 2 and got["at"] == [18.0, 30.0]
    assert got["matched_t"] is None and got["drift_s"] is None


def test_absent_is_not_a_claim_about_invention():
    got = qr.resolve("a sentence nobody ever said here", CORPUS)
    assert got["state"] == qr.ABSENT and got["occurrences"] == 0


def test_nothing_to_resolve_is_its_own_state():
    for quote, atoms in (("", CORPUS), ("   ", CORPUS), (None, CORPUS),
                         ("hello", None), ("hello", [])):
        got = qr.resolve(quote, atoms)
        assert got["state"] == qr.UNRESOLVABLE, (quote, atoms is None)
        assert got["why"], "and it says which"


def test_an_atom_whose_time_cannot_be_read_is_not_at_zero():
    """Its words would otherwise be searchable at a position that does not
    exist, and the drift computed against it would be arithmetic on a guess."""
    broken = [{"i": 0, "start": None, "text": "a unique phrase here"},
              {"i": 1, "start": 10.0, "end": 16.0, "text": "something else"}]
    assert qr.resolve("a unique phrase here", broken)["state"] == qr.ABSENT


def test_a_partial_word_never_matches():
    """The same rule `_contains_sequence` enforces: "a bla" is not inside
    "a blast furnace"."""
    atoms = _atoms((0.0, "a blast furnace"))
    assert qr.resolve("a bla", atoms)["state"] == qr.ABSENT


# --- one notion of "the same words" ------------------------------------------


def test_the_resolver_and_what_ships_agree_on_every_quote():
    """A second tokeniser here would drift from the one in `story_evidence` and
    the comparison would quietly stop meaning anything. Whenever the shipped
    matcher says a quote is inside a run of atoms, the resolver must find it
    there too."""
    hay = se.normalise_quote(" ".join(a["text"] for a in CORPUS)).split()
    for quote in ("hello pre-notification gang", "super humans not because",
                  "digital twin", "you know what I mean",
                  "gang we are going", "nowhere at all", "a bla"):
        needle = se.normalise_quote(quote).split()
        ships = se._contains_sequence(hay, needle)
        resolved = qr.resolve(quote, CORPUS)["state"] in (qr.BOUND, qr.AMBIGUOUS)
        assert ships == resolved, quote


def test_normalisation_is_the_shipped_one():
    """Case and end punctuation fold; an interior apostrophe survives. Both
    sides go through the same function, so this is a property of that function
    rather than of the resolver — asserted here because a copy would break it."""
    atoms = _atoms((0.0, "Y'ALL should know."))
    assert qr.resolve("y'all should know", atoms)["state"] == qr.BOUND
    assert qr.resolve("yall should know", atoms)["state"] == qr.ABSENT


def test_the_occurrence_list_is_capped_but_the_count_is_not():
    """"More than a few" and "two" are different findings, so the count stays
    exact while the list stops growing."""
    atoms = _atoms(*[(6.0 * i, "same words again") for i in range(20)])
    got = qr.resolve("same words again", atoms)
    assert got["occurrences"] == 20
    assert len(got["at"]) == qr.MAX_OCCURRENCES


def test_it_applies_to_nothing():
    assert qr.resolve("digital twin", CORPUS)["applied"] is False
