"""
Unit tests for narrative threads.

The need they exist for, stated as a failure: a stream that spends an hour on
one boss can hand back a board that is entirely that boss, with every clip in
a different ten-minute bucket and a different archetype. Timeline and kind
both say "these are different"; only the thread says they are not.

Pure — threads are lexical chaining over atoms, no model and no I/O.
"""

from __future__ import annotations

from services.clipper import dedupe, threads as threads_mod


def _atom(i, start, text, end=None):
    return {"i": i, "start": float(start), "end": float(end or start + 5.0),
            "text": text,
            "audio": {"energy": 0.3, "peaks": 0, "laughter": 0.0},
            "visual": {"motion": 0.3, "scene_change": False, "ui": 0.0},
            "semantic": {"kind": "speech", "cues": 0, "words": len(text.split()),
                         "importance": 0.2}}


# ── chaining ─────────────────────────────────────────────────────────────────


def test_atoms_about_the_same_thing_become_one_thread():
    out = threads_mod.build([
        _atom(0, 0, "the warden is going to spawn in this deep dark cave"),
        _atom(1, 20, "warden spawning again in the deep dark, careful"),
        _atom(2, 40, "deep dark warden almost got me there honestly"),
    ])
    assert len(out) == 1
    assert out[0]["size"] == 3
    assert "warden" in out[0]["keywords"]


def test_a_single_atom_is_not_an_arc():
    """Measured on the real stream, chaining produced 43 "threads" of which 30
    were singletons — as a diversity axis that is one bucket per moment."""
    out = threads_mod.build([
        _atom(0, 0, "the warden spawns in the deep dark cave below"),
        _atom(1, 200, "anyway lets craft some golden apples for later"),
    ])
    assert out == [], "two unrelated moments are two moments, not two arcs"


def test_atoms_about_different_things_do_not_merge():
    out = threads_mod.build([
        _atom(0, 0, "the warden spawns in the deep dark cave below"),
        _atom(1, 20, "the warden deep dark cave is right down there"),
        _atom(2, 200, "anyway lets craft some golden apples for later"),
        _atom(3, 220, "golden apples crafting needs more gold ingots"),
    ])
    assert len(out) == 2
    assert out[0]["size"] == 2 and out[1]["size"] == 2


def test_one_shared_word_is_coincidence_not_a_thread():
    """A stream that says "diamond" all night would otherwise be one arc."""
    out = threads_mod.build([
        _atom(0, 0, "diamond armour is what we need for the warden fight"),
        _atom(1, 20, "diamond is also good for making a pickaxe apparently"),
    ])
    assert out == [], "one shared word chained two unrelated atoms"


def test_a_thread_closes_after_a_long_silence():
    """Two arcs about the same subject an hour apart are two arcs — a thread
    must not span the stretch of stream between them."""
    gap = threads_mod.THREAD_GAP_S + 60
    out = threads_mod.build([
        _atom(0, 0, "warden deep dark cave spawning down here"),
        _atom(1, 20, "warden deep dark cave still spawning down here"),
        _atom(2, gap, "warden deep dark cave spawning down here again"),
        _atom(3, gap + 20, "warden deep dark cave once more down here"),
    ])
    assert len(out) == 2
    assert out[0]["end"] < out[1]["start"]


def test_a_long_thread_does_not_swallow_the_stream():
    """Its vocabulary is what it has been about RECENTLY, or it accumulates
    half the dictionary and matches everything."""
    atoms = [_atom(i, i * 20, f"warden deep dark cave chapter number {i}")
             for i in range(8)]
    atoms += [_atom(8, 200, "completely separate topic about baking bread now"),
              _atom(9, 220, "baking bread and separate topic continues here")]
    out = threads_mod.build(atoms)
    assert len(out) >= 2
    assert out[-1]["size"] == 2


def test_an_empty_stream_has_no_threads():
    assert threads_mod.build([]) == []
    assert threads_mod.build(None) == []


def test_a_window_is_placed_in_the_thread_it_overlaps_most():
    arcs = threads_mod.build([
        _atom(0, 0, "warden deep dark cave spawning right here now"),
        _atom(1, 20, "warden deep dark cave is still spawning here"),
        _atom(2, 400, "baking bread in a furnace is a separate topic"),
        _atom(3, 420, "bread and furnace baking continues over here"),
    ])
    assert threads_mod.thread_at(arcs, 5.0, 25.0) == arcs[0]["id"]
    assert threads_mod.thread_at(arcs, 405.0, 425.0) == arcs[1]["id"]
    assert threads_mod.thread_at(arcs, 9000.0, 9100.0) is None


# ── the consumer ─────────────────────────────────────────────────────────────


def _cut(start, score, thread, payoff=None, kinds=("FUNNY",)):
    return {"start": start, "end": start + 30.0, "overall": score,
            "text": f"words at {start:.0f} unique {start:.0f}",
            "story": {"payoff_t": payoff if payoff is not None else start + 28.0,
                      "archetypes": list(kinds), "thread_id": thread}}


def test_the_board_does_not_fill_with_one_arc():
    """Every clip in a different ten-minute bucket and a different archetype,
    and all of them the same boss fight. Timeline and kind both say these are
    different; only the thread says they are not."""
    same_arc = [_cut(i * 400.0, 90.0 - i, "thread_000",
                     kinds=[k]) for i, k in
                enumerate(["RAGE", "CLUTCH", "FAIL", "SHOCK"])]
    other = _cut(2000.0, 84.0, "thread_007", kinds=["STORY"])
    out = dedupe.deduplicate(same_arc + [other], overlap_threshold=0.5,
                             text_threshold=0.9, target_count=3)
    top3 = sorted((c for c in out if not c["is_alternative"]),
                  key=lambda c: c["rank_position"])[:3]
    arcs = {c["story"]["thread_id"] for c in top3}
    assert "thread_007" in arcs, f"the only other arc never made the board: {arcs}"


def test_a_candidate_with_no_thread_still_ranks():
    plain = [{"start": 0.0, "end": 30.0, "overall": 80.0, "text": "a"},
             {"start": 400.0, "end": 430.0, "overall": 70.0, "text": "b"}]
    out = dedupe.deduplicate(plain, overlap_threshold=0.5, text_threshold=0.9,
                             target_count=2)
    assert sum(1 for c in out if not c["is_alternative"]) == 2


# ── the edges that something reads ───────────────────────────────────────────


def test_a_named_callback_becomes_a_setup_edge():
    out = threads_mod.edges(
        [], [], [{"payoff_t": 5000.0,
                  "callback_to": {"t": 3500.0, "text": "he predicted a choke"}}])
    assert out == [{"kind": "SETUP_FOR", "from_t": 3500.0, "to_t": 5000.0,
                    "why": "he predicted a choke"}]


def test_two_payoffs_in_one_arc_become_a_same_story_edge():
    arcs = threads_mod.build([
        _atom(0, 0, "warden deep dark cave spawning right here now"),
        _atom(1, 20, "warden deep dark cave is still spawning here"),
    ])
    out = threads_mod.edges(arcs, [], [{"payoff_t": 5.0}, {"payoff_t": 22.0}])
    assert [e["kind"] for e in out] == ["SAME_STORY"]
    assert out[0]["from_t"] == 5.0 and out[0]["to_t"] == 22.0


def test_no_relations_are_invented():
    """CAUSES, ESCALATES, CONTRADICTS and the rest would need a model per pair
    or a guess, and nothing queries them."""
    out = threads_mod.edges([], [], [{"payoff_t": 10.0}, {"payoff_t": 900.0}])
    assert out == []
