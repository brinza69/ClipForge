"""One representation of the narrative evidence, and metrics that match the cut.

Two measured defects are reproduced here.

STALENESS. `candidate_proposals` measured context debt and hook latency against
the window it proposed; `refine_boundaries` then moved both ends and copied the
`story` block verbatim. On the four-hour audited source, 20 story candidates
ended with required context outside their own final span and a hook latency that
described a window which no longer existed.

TWO PAYOFFS. The model named one; `extract_features` derived another from audio
peaks and cue words and every payoff feature described THAT one. The gap had a
median of 7.14s and 4.96s on the two audited sources and a maximum of 61.6s — a
clip chosen for one event and scored around another.
"""

from __future__ import annotations

from services.clipper import story_evidence as se


def _atom(i, start, end, text):
    return {"i": i, "start": start, "end": end, "text": text,
            "audio": {"energy": 0.1, "peaks": 0, "laughter": 0.0},
            "visual": {"motion": 0.1, "scene_change": False, "ui": 0.0},
            "semantic": {"kind": "speech", "cues": 0, "words": len(text.split()),
                         "importance": 0.2}}


ATOMS = [
    _atom(0, 0.0, 4.0, "I'm going to win this with one heart, watch."),
    _atom(1, 4.0, 9.0, "Look to your left — it's a blast furnace!"),
    _atom(2, 9.0, 14.0, "Y'all just sat there and lied, bro."),
]


# ── Normalising a quote ──────────────────────────────────────────────────────


def test_punctuation_and_case_do_not_decide_grounding():
    """The clipper transcribes with keep_punctuation=True on purpose, so a raw
    string compare fails for reasons that have nothing to do with grounding.

    Note what survives: `norm_token` strips punctuation from the ENDS of a
    token, so an interior apostrophe stays. That is symmetric — both the quote
    and the atom text go through this — so "Y'ALL" matches "y'all". It does
    leave one real false negative, and the module docstring names it.
    """
    assert se.normalise_quote("Y'ALL just SAT there,") == "y'all just sat there"


def test_normalising_splits_and_folds_per_token():
    """`norm_token` takes ONE word. Handed a sentence it strips only the ends
    and splits nothing."""
    assert se.normalise_quote("Bine, așa!") == "bine asa"
    assert se.normalise_quote("   ") == ""
    assert se.normalise_quote(None) == ""


# ── Grounding one claim ──────────────────────────────────────────────────────


def test_an_exact_quote_in_the_named_atom_is_grounded():
    out = se.ground_claim(
        {"t": 5.0, "quote": "it's a blast furnace", "atom_ids": [1]}, ATOMS)
    assert out["grounded"] is True
    assert out["atom_ids"] == [1]


def test_a_paraphrase_is_marked_not_dropped():
    """The rule the whole module turns on. Models paraphrase, and a first
    version of a text matcher must not double as a recall filter."""
    out = se.ground_claim(
        {"t": 5.0, "quote": "he mentioned a furnace", "atom_ids": [1]}, ATOMS)
    assert out["grounded"] is False
    assert out["why_not"] == "quote is not in those atoms"
    assert out["t"] == 5.0, "the claim itself survives"


def test_an_atom_id_that_does_not_exist_fails():
    out = se.ground_claim({"t": 5.0, "quote": "blast furnace",
                           "atom_ids": [99]}, ATOMS)
    assert out["grounded"] is False and "unknown atom" in out["why_not"]


def test_a_timestamp_outside_the_atoms_it_names_fails():
    """Quoting the right words while pointing somewhere else is not grounding
    — it is a coincidence."""
    out = se.ground_claim({"t": 300.0, "quote": "blast furnace",
                           "atom_ids": [1]}, ATOMS)
    assert out["grounded"] is False
    assert "timestamp outside" in out["why_not"]


def test_an_empty_quote_fails_explicitly():
    """It matches every string by definition, which would make grounding a
    formality rather than a check."""
    for claim in ({"t": 5.0, "quote": "", "atom_ids": [1]},
                  {"t": 5.0, "quote": "   ", "atom_ids": [1]},
                  {"t": 5.0, "atom_ids": [1]}):
        assert se.ground_claim(claim, ATOMS)["grounded"] is False


def test_a_quote_may_straddle_two_atoms():
    out = se.ground_claim(
        {"t": 8.0, "quote": "blast furnace! Y'all just sat there,",
         "atom_ids": [1, 2]}, ATOMS)
    assert out["grounded"] is True


def test_without_atom_ids_it_falls_back_to_the_neighbourhood():
    """What makes grounding work before the prompt is taught to return ids.
    Strictly weaker: it confirms the words are there, not that the model knew
    where they were."""
    out = se.ground_claim({"t": 5.0, "quote": "blast furnace"}, ATOMS)
    assert out["grounded"] is True
    out = se.ground_claim({"t": 5.0, "quote": "never said this"}, ATOMS)
    assert out["grounded"] is False


def test_no_atoms_at_all_is_not_a_pass():
    """The legacy path has no atoms. That is not the claim's fault and it is
    not evidence either."""
    out = se.ground_claim({"t": 5.0, "quote": "blast furnace"}, None)
    assert out["grounded"] is False
    assert out["why_not"] == "no atoms available"


def test_grounding_an_anchor_counts_both_halves():
    anchor = {
        "payoff_t": 10.0, "payoff_quote": "lied, bro", "payoff_atom_ids": [2],
        "required_context": [
            {"t": 5.0, "fact": "chat trolled him", "quote": "blast furnace",
             "atom_ids": [1]},
            {"t": 1.0, "fact": "he was warned", "quote": "not in the transcript",
             "atom_ids": [0]},
        ],
    }
    out = se.ground_anchor(anchor, ATOMS)
    assert out["payoff_grounded"] is True
    assert out["grounding"]["context_total"] == 2
    assert out["grounding"]["context_grounded"] == 1
    assert len(out["required_context"]) == 2, "nothing is dropped"


# ── Measuring against the FINAL span ─────────────────────────────────────────


def _cand(start, end, **story):
    story.setdefault("payoff_t", 10.0)
    return {"start": start, "end": end, "words": [], "story": story}


def test_context_before_the_start_is_not_covered():
    """The defect: 20 candidates on the four-hour source ended up here and
    said nothing about it."""
    cov = se.boundary_coverage({"payoff_t": 10.0,
                                "required_context": [{"t": 2.0}]}, 5.0, 20.0)
    assert cov["context"] is False and cov["context_outside"] == 1
    assert cov["payoff"] is True


def test_a_payoff_outside_the_window_is_invalid():
    cov = se.boundary_coverage({"payoff_t": 40.0}, 5.0, 20.0)
    assert cov["payoff"] is False
    assert se.validate_story_span({}, cov) == se.INVALID


def test_a_reaction_cut_off_by_the_end_is_recorded():
    cov = se.boundary_coverage({"payoff_t": 10.0, "reaction_end": 25.0}, 5.0, 20.0)
    assert cov["reaction"] is False


def test_a_moment_that_needs_no_context_is_covered_by_any_window():
    cov = se.boundary_coverage({"payoff_t": 10.0, "required_context": []}, 5.0, 20.0)
    assert cov["context"] is True


def test_ungrounded_evidence_is_uncertain_not_invalid():
    """`invalid` is reserved for a window that cannot work. Thin evidence is a
    different claim, and the two must not be collapsed."""
    story = {"payoff_t": 10.0,
             "grounding": {"payoff": False, "context_total": 0,
                           "context_grounded": 0}}
    cov = se.boundary_coverage(story, 5.0, 20.0)
    assert se.validate_story_span(story, cov) == se.UNCERTAIN


def test_everything_grounded_and_covered_is_valid():
    story = {"payoff_t": 10.0, "required_context": [{"t": 6.0}],
             "grounding": {"payoff": True, "context_total": 1,
                           "context_grounded": 1}}
    cov = se.boundary_coverage(story, 5.0, 20.0)
    assert se.validate_story_span(story, cov) == se.VALID


def test_remeasure_is_idempotent():
    """What makes it safe to call after every step that can move an edge."""
    cand = _cand(5.0, 20.0, required_context=[{"t": 6.0}], hook_t=7.0)
    once = se.remeasure(dict(cand, story=dict(cand["story"])))["story"]
    twice = se.remeasure(se.remeasure(dict(cand, story=dict(cand["story"]))))["story"]
    assert once == twice


def test_remeasure_follows_a_moved_edge():
    cand = _cand(5.0, 20.0, required_context=[{"t": 6.0}], hook_t=7.0)
    se.remeasure(cand)
    assert cand["story"]["boundary_coverage"]["context"] is True
    cand["start"] = 8.0          # the edge moves, as refinement moves it
    se.remeasure(cand)
    assert cand["story"]["boundary_coverage"]["context"] is False
    assert cand["story"]["measured_span"] == [8.0, 20.0]


def test_stale_metrics_are_detectable():
    """The gate for this batch is that nothing in the corpus answers True."""
    cand = _cand(5.0, 20.0)
    assert se.stale_metrics(cand) is True, "never measured"
    se.remeasure(cand)
    assert se.stale_metrics(cand) is False
    cand["end"] = 30.0
    assert se.stale_metrics(cand) is True


# ── One payoff, not two ──────────────────────────────────────────────────────


def test_the_semantic_payoff_is_what_features_read():
    assert se.semantic_payoff(_cand(5.0, 20.0, payoff_t=12.0)) == 12.0


def test_a_payoff_outside_the_window_is_not_offered_to_features():
    """Using it would put `payoff_position` outside 0..1 and quietly poison
    the score. The coverage failure is already recorded separately."""
    assert se.semantic_payoff(_cand(5.0, 20.0, payoff_t=40.0)) is None


def test_the_legacy_path_has_no_semantic_payoff():
    assert se.semantic_payoff({"start": 0, "end": 10}) is None
    assert se.semantic_payoff({"start": 0, "end": 10, "story": "nonsense"}) is None


def test_features_prefer_the_semantic_payoff_over_the_loudest_moment():
    """The end-to-end version of the central defect. The window holds a loud
    spike at 2s and the moment was chosen for something at 25s; every payoff
    feature must describe the second one."""
    from services.clipper.candidates import extract_features

    words = [{"word": "w", "start": float(t), "end": t + 0.4}
             for t in range(0, 30)]
    transcript = {"segments": [{"start": 0.0, "end": 30.0,
                                "text": " ".join("w" * 1), "words": words}]}
    signals = {"rms": [0.9] * 6 + [0.05] * 54, "rms_hop": 0.5, "peaks": [2.0]}

    cand = {"start": 0.0, "end": 30.0, "words": words,
            "story": {"payoff_t": 25.0, "payoff_strength": 0.9}}
    feats = extract_features(cand, transcript, signals, 60.0)
    assert cand["payoff_source"] == se.SOURCE_SEMANTIC
    assert feats["payoff_position"] > 0.7, "the payoff sits late in the window"

    legacy = {"start": 0.0, "end": 30.0, "words": words}
    extract_features(legacy, transcript, signals, 60.0)
    assert legacy["payoff_source"] == se.SOURCE_MECHANICAL


# ── The refinement path remeasures ───────────────────────────────────────────


def test_refining_a_boundary_remeasures_the_story():
    """`refine_boundaries` returns `dict(cand)`, which copies the story block
    metrics and all, and everything it does can move both edges."""
    from services.clipper.candidate_boundaries import refine_boundaries

    words = [{"word": "w", "start": float(t), "end": t + 0.5}
             for t in range(0, 60)]
    transcript = {"segments": [{"start": 0.0, "end": 60.0, "text": "w",
                                "words": words}]}
    cand = {"start": 10.0, "end": 40.0, "words": words[10:40],
            "story": {"payoff_t": 30.0, "required_context": [{"t": 12.0}],
                      "hook_t": 12.0, "context_debt": 0.0, "hook_latency": 99.0}}
    out = refine_boundaries(cand, transcript, {}, min_s=15.0, max_s=60.0)

    assert not se.stale_metrics(out)
    assert out["story"]["measured_span"] == [out["start"], out["end"]]
    assert out["story"]["hook_latency"] != 99.0, "the stale value was recomputed"
    # The caller's candidate is never touched — the whole contract of the
    # function, and remeasuring in place would have broken it.
    assert cand["story"]["hook_latency"] == 99.0


def test_the_sentence_snap_does_not_move_past_required_context():
    """Found on the corpus, not by a test: one balanced variant was snapped
    from 32.0 to 32.5 and its only required fact ended up half a second outside
    the clip. Landing on a sentence is a presentation fix; dropping the setup
    the moment was cut around is not a fair price for it."""
    from services.clipper.candidate_boundaries import refine_boundaries

    words = [{"word": "w", "start": float(t), "end": t + 0.5}
             for t in range(0, 80)]
    transcript = {"segments": [
        {"start": 0.0, "end": 32.0, "text": "one.", "words": words[0:32]},
        {"start": 32.5, "end": 80.0, "text": "two.", "words": words[32:80]},
    ]}
    cand = {"start": 32.0, "end": 70.0, "words": words[32:70],
            "story": {"payoff_t": 60.0, "required_context": [{"t": 32.0}]}}
    out = refine_boundaries(cand, transcript, {}, min_s=15.0, max_s=90.0)

    assert out["start"] <= 32.0 + 0.001, "the snap crossed the required fact"
    assert out["story"]["boundary_coverage"]["context"] is True


def test_the_guard_binds_only_where_there_is_context_to_protect():
    """It reads `story.required_context`. The legacy path has no anchor, so
    nothing about the boundary code changes for it."""
    from services.clipper.candidate_boundaries import _context_floor

    assert _context_floor({"start": 0, "end": 10}) is None
    assert _context_floor({"story": {"payoff_t": 5.0}}) is None
    assert _context_floor({"story": {"required_context": []}}) is None
    assert _context_floor(
        {"story": {"required_context": [{"t": 9.0}, {"t": 3.0}]}}) == 3.0
    # Junk must not become a floor of 0.0, which would pin every start.
    assert _context_floor({"story": {"required_context": [{"fact": "x"}]}}) is None


def test_a_word_fragment_does_not_count_as_a_quote():
    """The substring version passed this. "a bla" is not a quotation of
    "a blast furnace" — it is a prefix straddling a word boundary, and any
    short string sitting inside a longer word would have passed too."""
    assert se.ground_claim({"t": 5.0, "quote": "a bla", "atom_ids": [1]},
                           ATOMS)["grounded"] is False
    assert se.ground_claim({"t": 5.0, "quote": "a blast furnace",
                            "atom_ids": [1]}, ATOMS)["grounded"] is True


def test_the_locator_is_recorded_so_two_checks_are_never_one_number():
    """The prompt shows `[seconds] text` and never an atom id, so today every
    claim is located by timestamp. Reported together with the id path, the rate
    would claim a check that is not being performed."""
    by_time = se.ground_claim({"t": 5.0, "quote": "blast furnace"}, ATOMS)
    by_ids = se.ground_claim({"t": 5.0, "quote": "blast furnace",
                              "atom_ids": [1]}, ATOMS)
    assert by_time["matched_by"] == se.MATCHED_BY_TIME
    assert by_ids["matched_by"] == se.MATCHED_BY_IDS


def test_the_timestamp_window_is_atoms_not_twenty_seconds():
    """Reaching +/-20s covered four to eight atoms on the real corpus, so a
    quote could match somewhere the model never pointed at."""
    far = [_atom(i, i * 5.0, i * 5.0 + 5.0, f"line {i} filler words here")
           for i in range(12)]
    far[10] = _atom(10, 50.0, 55.0, "the diamonds were stolen")
    # Claimed at atom 2 (t=10s); the quote lives at atom 10 (t=50s).
    out = se.ground_claim({"t": 10.0, "quote": "the diamonds were stolen"}, far)
    assert out["grounded"] is False
    # Claimed where it actually is.
    near = se.ground_claim({"t": 51.0, "quote": "the diamonds were stolen"}, far)
    assert near["grounded"] is True
