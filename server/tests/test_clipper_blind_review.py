"""The blind review, which is only worth running if it is actually blind.

Every test here guards one of the two things that make the session usable: the
reviewer cannot tell which board asked for a clip, and an answer stays attached
to the clip it was given for.
"""

from __future__ import annotations

import pytest

from services.clipper import blind_review as review


def _rows(n: int = 6) -> list[dict]:
    """Three legacy-only, one shared, two shadow-only."""
    return [
        {"clip_id": "c1", "project_id": "p", "rank_position": 1, "shadow_rank": None},
        {"clip_id": "c2", "project_id": "p", "rank_position": 2, "shadow_rank": None},
        {"clip_id": "c3", "project_id": "p", "rank_position": 3, "shadow_rank": None},
        {"clip_id": "c4", "project_id": "p", "rank_position": 4, "shadow_rank": 1,
         "shadow_run_id": "r1"},
        {"clip_id": "c5", "project_id": "p", "rank_position": None, "shadow_rank": 2,
         "shadow_run_id": "r1"},
        {"clip_id": "c6", "project_id": "p", "rank_position": None, "shadow_rank": 3,
         "shadow_run_id": "r1"},
    ][:n]


def _answer(**kw):
    base = {"worth_exporting": "yes", "self_contained": "yes", "hook": "strong",
            "start_boundary": "correct", "end_boundary": "correct",
            "technical_problem": "no"}
    base.update(kw)
    return base


# ── The blind ────────────────────────────────────────────────────────────────


def test_a_clip_both_boards_picked_is_listed_once():
    """Listed twice, the reviewer watches it twice — and their two answers
    disagree often enough to matter, because the second viewing is not the same
    instrument as the first."""
    items = review.build_items(_rows())
    assert len(items) == 6
    shared = [i for i in items if i["clip_id"] == "c4"][0]
    assert shared["membership"] == review.BOTH


def test_a_candidate_neither_board_picked_is_not_in_the_session():
    assert review.build_items(
        [{"clip_id": "x", "rank_position": None, "shadow_rank": None}]) == []


def test_the_client_view_cannot_reveal_which_board_asked():
    """An allowlist, not a redaction. Returning the row and trusting the page
    not to render `shadow_rank` puts membership in the DOM, one devtools panel
    away, and the reviewer only has to slip once."""
    session = review.create("s1", _rows(), seed=7)
    handle = session["order"][0]
    clip = {"start_time": 10.0, "end_time": 40.0, "duration": 30.0,
            "transcript_text": "hello", "preview_path": "/x.mp4",
            "rank_position": 1, "shadow_rank": 4, "shadow_run_id": "r1",
            "reasoning": {"shadow_rank": 4}}

    out = review.public_item(session, handle, clip)

    leaked = [k for k in out
              if "shadow" in k or "rank" in k or "legacy" in k]
    assert leaked == [], f"membership leaked through {leaked}"
    assert "r1" not in repr(out)


def test_the_item_handle_is_not_the_clip_id():
    """The clip id is how the reviewer's own board is addressed. A review page
    that shows it invites a glance at the board to see whether the clip is
    ranked, which is the blind broken by a URL."""
    session = review.create("s1", _rows(), seed=7)
    handles = set(session["order"])
    assert not handles & {"c1", "c2", "c3", "c4", "c5", "c6"}


def test_two_sessions_give_the_same_clip_different_handles():
    a = review.create("s1", _rows(), seed=7)
    b = review.create("s2", _rows(), seed=7)
    assert set(a["order"]) & set(b["order"]) == set()


# ── The order ────────────────────────────────────────────────────────────────


def test_the_order_is_stored_not_recomputed():
    """A shuffle re-derived on each load is reproducible only while the item
    list, the sort and the shuffle implementation all stay identical — and a
    session that renumbers itself halfway silently reassigns answers to
    different clips."""
    session = review.create("s1", _rows(), seed=7)
    assert sorted(session["order"]) == sorted(
        i["review_item_id"] for i in session["items"])
    assert session["seed"] == 7


def test_the_next_item_follows_the_recorded_order():
    session = review.create("s1", _rows(), seed=7)
    assert review.next_item(session) == session["order"][0]
    review.record(session, session["order"][0], _answer())
    assert review.next_item(session) == session["order"][1]


def test_a_finished_session_has_no_next_item():
    session = review.create("s1", _rows(2), seed=7)
    for handle in list(session["order"]):
        review.record(session, handle, _answer())
    assert review.next_item(session) is None
    assert review.progress(session) == {"answered": 2, "total": 2, "remaining": 0}


def test_an_answer_remembers_where_in_the_session_it_was_given():
    """Order effects are real — the tenth clip is judged by a different reviewer
    than the first — and a position recovered later from `order` is correct only
    while the order has never been rebuilt."""
    session = review.create("s1", _rows(), seed=7)
    stored = review.record(session, session["order"][2], _answer())
    assert stored["position"] == 3


# ── The rubric ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", [q["key"] for q in review.QUESTIONS])
def test_every_question_must_be_answered(key):
    """Optional fields would make missingness correlate with fatigue and with
    how hard the clip is — which is the signal the review exists to measure,
    arriving as a hole in the data."""
    answer = _answer()
    answer.pop(key)
    assert review.validate(answer) == f"missing answer: {key}"


def test_a_value_outside_the_question_is_refused():
    assert "not one of" in review.validate(_answer(hook="amazing"))


def test_a_rejected_clip_needs_a_reason():
    """"It was bad" is not a finding. Which of the reasons it was tells you
    whether to change selection, boundaries or rendering."""
    assert review.validate(_answer(worth_exporting="no")) == (
        "a rejected clip needs at least one reason")
    assert review.validate(
        _answer(worth_exporting="no", reject_reasons=["no_payoff"])) == ""


def test_an_unknown_reason_is_refused():
    assert "unknown reject reasons" in review.validate(
        _answer(worth_exporting="no", reject_reasons=["vibes"]))


def test_an_invalid_answer_is_not_stored():
    session = review.create("s1", _rows(), seed=7)
    with pytest.raises(ValueError):
        review.record(session, session["order"][0], _answer(hook="amazing"))
    assert session["answers"] == {}


def test_an_unknown_handle_is_refused():
    session = review.create("s1", _rows(), seed=7)
    with pytest.raises(KeyError):
        review.record(session, "not-a-handle", _answer())


# ── The result ───────────────────────────────────────────────────────────────


def test_the_tally_scores_each_board_on_the_clips_it_picked():
    session = review.create("s1", _rows(), seed=7)
    verdicts = {"c1": "yes", "c2": "no", "c3": "no", "c4": "yes",
                "c5": "yes", "c6": "yes"}
    by_clip = {i["clip_id"]: i["review_item_id"] for i in session["items"]}
    for clip_id, verdict in verdicts.items():
        extra = {"reject_reasons": ["no_payoff"]} if verdict == "no" else {}
        review.record(session, by_clip[clip_id],
                      _answer(worth_exporting=verdict, **extra))

    out = review.tally(session)

    # legacy picked c1..c4 → 2 yes of 4; shadow picked c4..c6 → 3 yes of 3.
    assert out[review.LEGACY]["reviewed"] == 4
    assert out[review.LEGACY]["precision"] == 0.5
    assert out[review.SHADOW]["reviewed"] == 3
    assert out[review.SHADOW]["precision"] == 1.0


def test_a_shared_clip_counts_for_both_boards():
    """Agreement is not evidence for either side. Dropping it would compare the
    boards only where they argue, inflating whichever one wins the arguments."""
    session = review.create("s1", _rows(), seed=7)
    shared = [i for i in session["items"] if i["clip_id"] == "c4"][0]
    review.record(session, shared["review_item_id"], _answer())

    out = review.tally(session)
    assert out[review.LEGACY]["yes"] == out[review.SHADOW]["yes"] == 1


def test_unsure_is_neither_a_yes_nor_a_no():
    """Folding it into `no` reports the reviewer's hesitation as a defect of the
    clip. Many `unsure` on one board is its own finding."""
    session = review.create("s1", _rows(), seed=7)
    by_clip = {i["clip_id"]: i["review_item_id"] for i in session["items"]}
    review.record(session, by_clip["c1"], _answer(worth_exporting="unsure"))
    review.record(session, by_clip["c2"], _answer(worth_exporting="yes"))

    out = review.tally(session)
    assert out[review.LEGACY]["unsure"] == 1
    assert out[review.LEGACY]["precision"] == 1.0, "unsure is not a failure"


def test_a_board_nobody_reviewed_has_no_precision_rather_than_zero():
    """Zero reads as "everything it picked was bad". None says nobody looked."""
    session = review.create("s1", _rows(), seed=7)
    assert review.tally(session)[review.SHADOW]["precision"] is None


def test_reveal_is_the_only_way_to_learn_membership():
    session = review.create("s1", _rows(), seed=7)
    with pytest.raises(review.ReviewConflict, match="not_complete"):
        review.reveal(session)
    for handle in session["order"]:
        review.record(session, handle, _answer())
    rows = review.reveal(session)
    assert {r["membership"] for r in rows} == {review.LEGACY, review.SHADOW,
                                              review.BOTH}


def test_a_truncated_preview_never_counts_as_ready():
    """The mistake that invalidated the first real session.

    `render_preview` caps at 12 seconds by design — it is a proxy for the
    editor. A review served from it asks "is this worth exporting" about the
    first twelve seconds of a sixty-second clip. Measured on that session: 14 of
    15 answers said the clip ended too early, which was true of the video and
    false of the clip, and all five payoffs sat past the cut.

    So readiness is the EXPORT's, and a clip with only a preview reports not
    ready rather than quietly serving the proxy.
    """
    session = review.create("s1", _rows(), seed=7)
    handle = session["order"][0]

    only_preview = {"start_time": 0.0, "end_time": 60.0, "duration": 60.0,
                    "preview_path": "/p.mp4", "export_path": None}
    assert review.public_item(session, handle, only_preview)["preview_ready"] is False

    exported = {**only_preview, "export_path": "/e.mp4"}
    assert review.public_item(session, handle, exported)["preview_ready"] is True
