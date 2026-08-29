"""Batch R5: is the chosen window a finished thought, and can one move fix it.

The batch exists because nothing answered that question. `refine_boundaries`
MOVES both edges and `extract_features` SCORES them, and a score is a number to
rank by — it cannot say "this clip stops in the middle of a word" out loud.

The hole worth naming is in the score itself, and this file pins the fix rather
than the score: `ends_on_sentence = 1.0 if text.endswith(".!?…")` reads 0.0 for
every clip ever cut from a transcript without punctuation, and 0.0 there does
not mean "ends mid-sentence" — it means nobody could tell. `transcriber` strips
punctuation unless asked not to, so a corpus can arrive that way.

Recorded, applied to nothing. `eligible` decides nothing until the validator has
passed the corpus, because a rule that silently removes moments has to be
measured before it is trusted.
"""

from __future__ import annotations

from services.clipper import boundary_completion as bc


def _words(*spec: tuple[str, float, float]) -> list[dict]:
    return [{"word": w, "start": t0, "end": t1} for w, t0, t1 in spec]


#: Two clean sentences with a real pause between them.
TWO = _words(("Hello", 0.0, 0.4), ("there.", 0.5, 0.9),
             ("Watch", 2.0, 2.3), ("this.", 2.4, 2.8))


def _view(start: float, end: float, words=TWO, **kw) -> dict:
    kw.setdefault("max_s", 30.0)
    return bc.boundary_view({"start": start, "end": end}, words, **kw)


# --- the one defect that is a defect anywhere --------------------------------


def test_a_cut_inside_a_word_is_found_on_both_edges():
    """The gate says zero truncated words, and it is the only check that needs
    neither punctuation nor a language."""
    view = _view(0.2, 2.15)
    assert bc.START_IN_WORD in view["start"]["defects"]
    assert bc.END_IN_WORD in view["end"]["defects"]
    assert view["eligible"] is False


def test_the_straddling_word_is_the_one_the_usual_pass_cannot_see():
    """`_neighbourhood` drops it from all three of its lists — it is neither
    before the cut, after it, nor wholly inside — so the word this check exists
    to find is exactly the one that pass loses."""
    from services.clipper.candidate_terms import _neighbourhood

    inside, before, after = _neighbourhood(TWO, 0.2, 2.15)
    assert all(w["word"] != "Hello" for w in inside)
    assert before is None and (after is None or after["word"] != "Hello")
    assert bc._straddled(TWO, 0.2)["word"] == "Hello"


# --- what cannot be known ----------------------------------------------------


def test_a_transcript_without_punctuation_makes_the_sentence_checks_unavailable():
    """THE hole this batch closes. Without punctuation `ends_on_sentence` is
    0.0 for every clip ever cut, and 0.0 does not mean "ends mid-sentence" — it
    means nobody could tell. Reporting a whole corpus as incomplete because of
    how it was transcribed is a claim, not a measurement."""
    bare = _words(("hello", 0.0, 0.4), ("there", 0.5, 0.9),
                  ("watch", 2.0, 2.3), ("this", 2.4, 2.8))
    view = _view(0.0, 1.4, bare)
    assert bc.NO_PUNCTUATION in view["unknown"]
    assert bc.END_MID_SENTENCE not in view["defects"]
    assert bc.START_MID_SENTENCE not in view["defects"]
    assert view["end"]["status"] == bc.UNAVAILABLE
    # Not eligible and not ineligible: the board must not lose a moment for the
    # way its transcript was made.
    assert view["eligible"] is None
    assert view["ineligible_because"] is None


def test_a_window_with_no_words_is_unavailable_not_perfect():
    view = _view(10.0, 14.0)
    assert bc.NO_WORDS in view["unknown"]
    assert view["eligible"] is None
    assert view["measurements"]["tail_s"] is None


def test_a_measured_defect_is_never_swallowed_by_an_unavailable_one():
    """Unavailable on the sentence axis is not unavailable on every axis. A cut
    inside a word needs neither punctuation nor a language, and letting the
    unknown decide hid a CERTAIN defect behind a measurement nobody could make.
    `None` is right only when nothing known rejects the window."""
    bare = _words(("hello", 0.0, 0.4), ("there", 0.5, 0.9))
    view = _view(0.2, 0.9, bare)
    assert bc.NO_PUNCTUATION in view["unknown"]
    assert view["blocking"] == [bc.START_IN_WORD]
    assert view["eligible"] is False
    assert view["ineligible_because"] == bc.START_IN_WORD


# --- the end -----------------------------------------------------------------


def test_a_window_that_stops_mid_sentence_is_incomplete():
    view = _view(2.0, 2.35)
    assert bc.END_MID_SENTENCE in view["end"]["defects"]
    assert view["end"]["status"] == bc.INCOMPLETE


def test_a_clean_window_is_complete_on_both_edges():
    view = _view(0.0, 1.4)
    assert view["start"]["status"] == view["end"]["status"] == bc.COMPLETE
    assert view["defects"] == [] and view["eligible"] is True
    assert view["technical"] == []


def test_no_air_after_the_last_word_is_the_defect_the_render_audit_counts():
    """22 of the 58 pilot exports ended within 50ms of the last word. The
    threshold is imported from `edit_quality` rather than restated, so the
    selection side and the render side count the same thing."""
    from services.clipper.edit_quality import TAIL_TIGHT_S

    view = _view(0.0, 0.92)
    assert view["measurements"]["tail_s"] <= TAIL_TIGHT_S
    assert bc.CLIPPED_RELEASE in view["end"]["defects"]
    # Reported, never blocking: it is fixed by padding, which `_keep_release`
    # already does, not by refusing the moment.
    assert bc.CLIPPED_RELEASE not in bc.BLOCKING
    assert view["eligible"] is True
    # Two axes, named rather than compressed: this one is about the FILE, and
    # whether it may reach a board is R7's preflight question.
    assert view["technical"] == [bc.CLIPPED_RELEASE]


def test_a_long_silence_at_the_end_is_dead_air_not_a_release():
    late = _words(("Hello", 0.0, 0.4), ("there.", 0.5, 0.9),
                  ("Later.", 8.0, 8.4))
    view = _view(0.0, 2.5, late)
    assert view["measurements"]["tail_s"] == 1.6
    assert bc.DEAD_TAIL in view["end"]["defects"]
    assert bc.CLIPPED_RELEASE not in view["end"]["defects"]


def test_an_orphan_tail_needs_the_silence_that_proves_it():
    """A continuation word with a real pause after it is an orphan — the
    speaker stopped, but not on a finished thought. The same word running
    straight into the next sentence is just an early cut."""
    orphaned = _words(("Hold", 0.0, 0.3), ("on", 0.4, 0.6), ("let's", 0.7, 1.0),
                      ("go.", 4.0, 4.3))
    view = _view(0.0, 1.4, orphaned)
    assert bc.ORPHAN_TAIL in view["end"]["defects"]

    continuous = _words(("Hold", 0.0, 0.3), ("on", 0.4, 0.6), ("let's", 0.7, 1.0),
                        ("go.", 1.1, 1.4))
    assert bc.ORPHAN_TAIL not in _view(0.0, 1.05, continuous)["defects"]


def test_a_window_that_opens_in_the_silence_before_a_sentence_opens_cleanly():
    """A time comparison against the sentence's start called this mid-sentence,
    and it is what `refine_boundaries` produces every time it adds a lead-in or
    pads a start. The question is structural: does the window's first word BEGIN
    a sentence."""
    view = _view(1.6, 2.9)
    assert view["measurements"]["lead_s"] == 0.4, "the window opens in silence"
    assert bc.START_MID_SENTENCE not in view["defects"]
    assert view["start"]["status"] == bc.COMPLETE


def test_a_window_that_opens_on_a_continuation_word_is_noted_not_refused():
    """A hook may legitimately open mid-thought, and the gate asks a HUMAN
    whether the opening is acceptable. Recording it and refusing it are
    different things."""
    view = _view(2.0, 2.8, _words(("Hello", 0.0, 0.4), ("there.", 0.5, 0.9),
                                  ("And", 2.0, 2.3), ("this.", 2.4, 2.8)))
    assert bc.START_ON_CONTINUATION in view["start"]["defects"]
    assert bc.START_ON_CONTINUATION not in bc.BLOCKING


# --- the one bounded repair --------------------------------------------------


def test_the_repair_extends_once_to_the_next_sentence_end():
    view = _view(2.0, 2.35)
    repair = view["repair"]
    assert repair["kind"] == bc.REPAIR_EXTEND
    assert repair["end"] == 2.8
    assert bc.END_MID_SENTENCE in repair["clears"]
    # Repairable in one move, so the moment is not refused.
    assert view["eligible"] is True


def test_the_repair_is_proposed_and_never_applied():
    """§3.5: one bounded correction, and this batch does not even make it. The
    window in the record is the window that was chosen."""
    cand = {"start": 2.0, "end": 2.35}
    before = dict(cand)
    view = bc.boundary_view(cand, TWO, max_s=30.0)
    assert cand == before
    assert view["end_s"] == 2.35
    assert view["applied"] is False


def test_the_repair_stops_at_the_maximum_duration():
    view = _view(2.0, 2.35, max_s=0.5)
    assert view["repair"]["refused"] == bc.WOULD_EXCEED_MAX
    assert view["repair"]["would_be_s"] == 0.8
    assert view["eligible"] is False
    assert view["ineligible_because"] == bc.END_MID_SENTENCE


def test_the_repair_stops_before_the_next_window():
    """An extension that runs into the next candidate has not repaired
    anything; it has moved the problem."""
    view = _view(2.0, 2.35, next_start=2.6)
    assert view["repair"]["refused"] == bc.WOULD_OVERLAP_NEXT
    assert view["repair"]["next_start"] == 2.6


def test_the_repair_reports_what_it_would_not_fix():
    """Measured on the repaired window rather than assumed. An extension that
    reaches a sentence end can still land on an orphan, and claiming otherwise
    would be the nudge-until-it-passes this batch is not allowed to do."""
    words = _words(("Watch", 2.0, 2.3), ("this", 2.4, 2.6), ("and.", 2.7, 2.9),
                   ("Later.", 9.0, 9.4))
    view = _view(2.0, 2.45, words)
    repair = view["repair"]
    assert repair["kind"] == bc.REPAIR_EXTEND and repair["end"] == 2.9
    assert bc.ORPHAN_TAIL in repair["remaining"]
    # An orphan is blocking, so a repair that leaves one does not rescue it.
    assert view["eligible"] is False


def test_a_window_with_nothing_wrong_has_no_repair_to_propose():
    assert _view(0.0, 1.4)["repair"]["refused"] == bc.NOTHING_TO_REPAIR


def test_a_defect_this_repair_does_not_touch_says_so():
    """"Nothing is wrong" and "something is wrong and this move does not fix
    it" are different reports, and only the second is a reason to look further.
    They were the same string."""
    view = _view(0.2, 1.4)
    assert bc.START_IN_WORD in view["defects"]
    assert view["repair"]["refused"] == bc.NOT_REPAIRABLE_HERE


def test_a_truncated_word_is_not_something_an_extension_repairs():
    """The only repair R5 allows is finishing a sentence. A cut inside a word is
    handled upstream by `_snap`, and pretending an extension fixes it would
    report a defect as solved by a move that does not touch it."""
    view = _view(0.2, 2.35)
    assert bc.START_IN_WORD in view["blocking"]
    assert view["eligible"] is False
    assert view["ineligible_because"] == bc.START_IN_WORD


# --- the story path ----------------------------------------------------------


def test_context_the_moment_needs_and_no_longer_contains():
    """`story.py` chose the opening around these facts. A required one before
    the window is setup the clip was cut around and then lost."""
    cand = {"start": 2.0, "end": 2.8,
            "story": {"required_context": [{"t": 0.5}, {"t": 2.1}]}}
    view = bc.boundary_view(cand, TWO, max_s=30.0)
    assert bc.CONTEXT_OUTSIDE in view["start"]["defects"]
    assert view["measurements"]["required_context_at"] == 0.5


def test_a_legacy_candidate_has_no_context_to_lose():
    view = _view(0.0, 1.4)
    assert bc.CONTEXT_OUTSIDE not in view["defects"]
    assert "required_context_at" not in view["measurements"]


# --- the report --------------------------------------------------------------


def test_the_report_states_the_rule_it_used():
    from services.clipper.candidate_terms import TAIL_PAD_S

    view = _view(0.0, 1.4)
    assert view["schema"] == "boundary_view_v1"
    assert view["scope"] == "window_completeness_and_one_bounded_repair"
    assert view["tail_pad_target_s"] == TAIL_PAD_S
    assert view["applied"] is False


def test_every_defect_and_refusal_is_in_a_closed_list():
    """Two lists stop being the same list the first time one of them changes,
    and a report that accumulates prose cannot be counted."""
    assert bc.BLOCKING <= set(bc.DEFECTS)
    assert bc.UNAVAILABLE in bc.REFUSALS
    for start, end in ((0.2, 2.15), (2.0, 2.35), (0.0, 1.4), (10.0, 14.0)):
        view = _view(start, end)
        assert set(view["defects"]) <= set(bc.DEFECTS)
        assert set(view["unknown"]) <= set(bc.UNKNOWNS)
        repair = view["repair"]
        assert repair["kind"] in (None, bc.REPAIR_EXTEND)
        if repair["kind"] is None:
            assert repair["refused"] in bc.REFUSALS


def test_the_score_and_the_verdict_answer_the_sentence_question_alike():
    """`extract_features` had the tolerance as a literal. Named in R5 so the
    number that scores a boundary and the number that judges it cannot drift."""
    import inspect

    from services.clipper import candidates as cand_mod
    from services.clipper.candidate_terms import SENTENCE_EDGE_S

    assert SENTENCE_EDGE_S == 0.35, "the value is unchanged; only its name is new"
    source = inspect.getsource(cand_mod)
    assert "SENTENCE_EDGE_S" in source and "<= 0.35" not in source,         "the literal is back, and the score can now drift from the verdict"
