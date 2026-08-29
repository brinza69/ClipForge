"""Batch R5a: the end lands off a word, which `_fit` had always claimed.

"Staying off words" was true of the START and never of the END — the end only
met `_snap` when the maximum duration was breached. R5's audit found the
asymmetry by measuring it: over the whole corpus, 261 of 6.762 windows end
strictly inside a word and ZERO begin inside one. That 261/0 IS the diagnosis,
because `_fit` is the one place a start is snapped and an end is not.

The rules above it choose WHICH word to end on and several land off an edge:
`_reaction_end` returns `min(w1, limit)`, so a reaction hitting `REACTION_MAX_S`
mid-word cuts there; `_payoff_time` searches a 0.5s grid; the minimum-duration
branch adds `lo` to a start. `_keep_release` rescues none of them — it returns
early when the gap already exceeds `TAIL_PAD_S`, which is exactly the mid-word
case.

MEASURED AFTER THE FIX, on the same corpus: 261 → 0, none refused for the
minimum, the maximum or the media. The move is NOT small — median 0.10s but p90
0.66s, 120 of 261 over 0.15s, and three over 3s where the transcript carries a
"word" lasting five seconds. `scripts/measure_boundary_snap.py` reproduces it.

A separate test file rather than more of `test_clipper_analysis.py`, which is
already 1.100 lines against a 500-line limit.
"""

from __future__ import annotations

from services.clipper import candidate_boundaries as cb


def _words(*spec: tuple[str, float, float]) -> list[dict]:
    return [{"word": w, "start": t0, "end": t1} for w, t0, t1 in spec]


WORDS = _words(("one", 0.0, 0.4), ("two", 0.5, 0.9), ("three", 1.0, 1.6),
               ("four", 2.0, 2.4), ("five", 2.5, 3.2))


def _straddles(words, t: float) -> bool:
    from services.clipper.boundary_completion import _straddled

    return _straddled(words, t) is not None


def test_the_end_is_pushed_out_of_a_word_it_landed_inside():
    """The whole batch. A cut at 1.3 sits inside "three"; the fix puts it at
    1.6, where the word finishes."""
    assert _straddles(WORDS, 1.3)
    _start, end = cb._fit(0.0, 1.3, WORDS, lo=0.5, hi=10.0, floor=0.0, ceiling=5.0)
    assert end == 1.6
    assert not _straddles(WORDS, end)


def test_pushing_out_beats_pulling_back_where_there_is_room():
    """The corpus asked for it: 260 of the 261 windows are pushed out and one is
    pulled back. Completing the word is what the viewer hears; dropping it is a
    second-best that only helps when there is no room."""
    _start, end = cb._fit(0.0, 2.1, WORDS, lo=0.5, hi=10.0, floor=0.0, ceiling=5.0)
    assert end == 2.4, "the end of `four`, not the start of it"


def test_the_maximum_pulls_the_cut_back_instead_of_breaching_it():
    """`_snap` falls back to the word's START when pushing out would cross the
    limit. The clip loses a word rather than the duration bound."""
    _start, end = cb._fit(0.0, 2.1, WORDS, lo=0.5, hi=2.2, floor=0.0, ceiling=5.0)
    assert end == 2.0
    assert not _straddles(WORDS, end)


def test_a_pull_back_that_would_breach_the_minimum_is_refused():
    """Trading one defect for another is not a repair. A clip under the floor is
    a different defect from a truncated word, and the guard says so by leaving
    the cut where the duration rules put it."""
    start, end = cb._fit(1.9, 2.1, WORDS, lo=0.5, hi=0.35, floor=0.0, ceiling=5.0)
    assert end - start <= 0.5, "the duration rules still own the span"


def test_the_media_end_is_never_crossed():
    _start, end = cb._fit(0.0, 3.0, WORDS, lo=0.5, hi=10.0, floor=0.0, ceiling=3.1)
    assert end <= 3.1


def test_a_cut_already_in_the_clear_is_left_alone():
    """The snap runs last and must be an identity on a window that was already
    right, or every clean boundary in the corpus moves for nothing."""
    for end in (0.45, 0.95, 1.8, 2.45):
        _start, out = cb._fit(0.0, end, WORDS, lo=0.4, hi=10.0, floor=0.0,
                              ceiling=5.0)
        assert out == end, f"a clean cut at {end} was moved to {out}"


def test_the_start_side_still_snaps_the_way_it_always_did():
    """The asymmetry is fixed by adding to the end, not by changing the start —
    zero of 6.762 windows began inside a word, so nothing there was broken."""
    start, _end = cb._fit(0.2, 2.1, WORDS, lo=0.5, hi=10.0, floor=0.0, ceiling=5.0)
    assert start == 0.0
    assert not _straddles(WORDS, start)


def test_the_end_lands_off_a_word_through_the_whole_refinement():
    """`_fit` is the last thing `refine_boundaries` does, so the property has to
    survive every rule above it — including `_reaction_end`, which is where the
    corpus's truncations came from."""
    words = _words(("Watch", 0.0, 0.4), ("this.", 0.5, 0.9),
                   ("Oh", 1.0, 1.3), ("myyyyy", 1.4, 4.9), ("god.", 5.0, 5.4))
    transcript = {"segments": [{"start": 0.0, "end": 5.4, "words": words}]}
    cand = {"start": 0.0, "end": 1.35, "words": words}
    out = cb.refine_boundaries(cand, transcript, {}, min_s=0.5, max_s=10.0)
    assert not _straddles(words, out["end"]), (
        f"the refinement left the cut at {out['end']} inside a word")
