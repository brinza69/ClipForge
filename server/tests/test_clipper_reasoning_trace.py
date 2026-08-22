"""The two artefacts that make a later change provable rather than asserted.

Every number checked here is one the Reasoning v2 audit had to reconstruct by
hand from `candidates.json`, months after the runs. The sharpest was that 12 of
the top 20 clips on a four-hour source had never been judged at all — a fact
that was true on disk and recorded nowhere.

A trace must never be able to break the run it describes, so the failure tests
matter as much as the happy path.
"""

from __future__ import annotations

from services.clipper import reasoning_trace as rt


def _trace(**kw):
    kw.setdefault("mode", "story_v1")
    return rt.RunTrace("p1", **kw)


# ── Fingerprints ─────────────────────────────────────────────────────────────


def test_key_order_is_not_a_change():
    """Two dicts that differ only in insertion order are the same input. A
    fingerprint that disagreed would report a change on every rerun, and so
    would report nothing at all."""
    assert rt.fingerprint({"a": 1, "b": 2}) == rt.fingerprint({"b": 2, "a": 1})


def test_a_different_value_is_a_change():
    assert rt.fingerprint({"a": 1}) != rt.fingerprint({"a": 2})


def test_the_same_run_twice_fingerprints_the_same():
    a, b = _trace(settings_snapshot={"clip_count": 8}), _trace(settings_snapshot={"clip_count": 8})
    for t in (a, b):
        t.note_versions(anchor_prompt="v3")
        t.note_chunk("anchors", 0, chars=100, produced=4)
    assert a.as_dict()["input_fingerprint"] == b.as_dict()["input_fingerprint"]
    assert a.as_dict()["chunk_plan_fingerprint"] == b.as_dict()["chunk_plan_fingerprint"]


def test_changing_a_setting_changes_the_fingerprint():
    a = _trace(settings_snapshot={"clip_count": 8})
    b = _trace(settings_snapshot={"clip_count": 9})
    assert a.as_dict()["settings_fingerprint"] != b.as_dict()["settings_fingerprint"]


# ── What the models did ──────────────────────────────────────────────────────


def test_a_fallback_is_visible_only_because_the_failure_was_recorded():
    t = _trace()
    t.note_attempt("anchors", "ollama", ok=False, error="connection refused",
                   request="anchors#0")
    t.note_attempt("anchors", "openai", ok=True, request="anchors#0")
    assert t.fallbacks == [{"stage": "anchors", "request": "anchors#0",
                            "after": ["ollama"], "used": "openai"}]
    assert t.outcome() == "fallback"


def test_a_failure_in_one_chunk_is_not_a_fallback_for_another():
    """The first version grouped by STAGE, so chunk 0 failing over and chunk 5
    succeeding on its first engine were the same three rows - and it reported a
    fallback that never happened."""
    t = _trace()
    t.note_attempt("anchors", "ollama", ok=False, request="anchors#0")
    t.note_attempt("anchors", "openai", ok=True, request="anchors#0")
    t.note_attempt("anchors", "ollama", ok=True, request="anchors#1")
    assert [f["request"] for f in t.fallbacks] == ["anchors#0"]


def test_every_provider_failing_is_not_a_complete_run():
    """`_ask` swallows its own failures by design, so nothing reaches the
    worker and `errors` stays empty. The first version called that `complete`:
    a run in which no model answered at all reported success."""
    t = _trace()
    t.note_attempt("anchors", "ollama", ok=False, request="anchors#0")
    t.note_attempt("anchors", "openai", ok=False, request="anchors#0")
    assert t.exhausted == [{"stage": "anchors", "request": "anchors#0",
                            "tried": ["ollama", "openai"]}]
    assert t.outcome() == "failed_non_blocking"


def test_some_chunks_answering_and_some_not_is_partial():
    t = _trace()
    t.note_attempt("anchors", "ollama", ok=True, request="anchors#0")
    t.note_attempt("anchors", "ollama", ok=False, request="anchors#1")
    t.note_count("anchors", 6)
    assert t.outcome() == "partial"


def test_a_first_choice_success_is_not_a_fallback():
    t = _trace()
    t.note_attempt("anchors", "ollama", ok=True, request="anchors#0")
    assert t.fallbacks == []
    assert t.outcome() == "complete"


def test_a_call_that_never_answered_is_not_a_fallback_either():
    t = _trace()
    t.note_attempt("anchors", "ollama", ok=False, request="anchors#0")
    t.note_attempt("anchors", "openai", ok=False, request="anchors#0")
    assert t.fallbacks == []


def test_failures_in_one_stage_do_not_become_a_fallback_in_another():
    t = _trace()
    t.note_attempt("anchors", "ollama", ok=False, request="anchors#0")
    t.note_attempt("judge", "openai", ok=True, request="judge#0")
    assert t.fallbacks == []


def test_an_error_without_anchors_is_non_blocking_not_partial():
    t = _trace()
    t.note_error("propose", RuntimeError("no engine"))
    assert t.outcome() == "failed_non_blocking"


def test_an_error_after_some_anchors_is_partial():
    t = _trace()
    t.note_count("anchors", 12)
    t.note_error("judge", "timeout")
    assert t.outcome() == "partial"


def test_the_snapshot_records_what_ran_not_what_the_defaults_say():
    """Two story runs on this rig were quoted as evidence and later turned out
    not to have come through the API at all. Nothing in the artefacts said so."""
    t = _trace(settings_snapshot={"clip_count": 8, "llm_select": True},
               launched_by="script")
    out = t.as_dict()
    assert out["settings_snapshot"] == {"clip_count": 8, "llm_select": True}
    assert out["launched_by"] == "script"


def test_the_untimed_chunk_is_recorded_as_untimed():
    """The chunker splits on characters and does not know where it landed in
    the source. Recording None is the honest answer; inventing a span would be
    precision the chunker does not have."""
    t = _trace()
    t.note_chunk("nominate", 0, chars=120_000, produced=12)
    chunk = t.as_dict()["chunks"][0]
    assert chunk["t_start"] is None and chunk["t_end"] is None
    assert chunk["chars"] == 120_000


def test_the_trace_never_raises_on_junk():
    t = _trace()
    t.note_count("anchors", "not a number")
    t.note_attempt("s", "e", ok=True, error=None, request="s#0")
    t.note_error("s", "x" * 5000)
    assert isinstance(t.as_dict(), dict)
    assert "anchors" not in t.counts


# ── The selection trace ──────────────────────────────────────────────────────


def _cand(start, end, **kw):
    return {"start": start, "end": end, **kw}


def test_a_candidate_the_judge_never_saw_says_so():
    """The asymmetry that had to be recomputed by hand. `llm_score` of None is
    "never evaluated"; 0.0 is "evaluated and left out", and they are not the
    same claim."""
    out = rt.build_selection_trace(
        [_cand(0, 30),
         _cand(60, 90, llm_score=0.0),
         _cand(120, 150, llm_score=88.0, llm_rank=1)],
        mode="story_v1")
    by_id = {e["variant_id"]: e["judge_status"] for e in out["entries"]}
    assert by_id["0.00-30.00"] == rt.NOT_EVALUATED
    assert by_id["60.00-90.00"] == rt.NOT_SELECTED_IN_JUDGED_POOL
    assert by_id["120.00-150.00"] == rt.SELECTED
    assert out["totals"]["not_evaluated"] == 1


def test_a_ranked_candidate_penalised_to_zero_is_still_ranked():
    """`apply_ranking` gives last place 0.0 and then subtracts a fixed penalty
    per reject reason, clamped at zero. Reading the status off the SCORE made a
    candidate the judge ranked and criticised indistinguishable from one it
    never looked at."""
    out = rt.build_selection_trace(
        [_cand(0, 30, llm_score=0.0, llm_rank=80,
               llm_verdict={"reject_reasons": ["no_payoff", "fans_only"]})],
        mode="story_v1")
    assert out["entries"][0]["judge_status"] == rt.SELECTED
    assert out["entries"][0]["reject_reasons"] == ["no_payoff", "fans_only"]


def test_two_candidates_on_the_same_span_get_different_ids():
    """A heuristic window and a story variant can refine onto the same rounded
    span. Colliding ids would make the structure fingerprint agree between two
    runs that do not."""
    out = rt.build_selection_trace(
        [_cand(0, 30), _cand(0.001, 30.004)], mode="story_v1")
    ids = [e["variant_id"] for e in out["entries"]]
    assert len(set(ids)) == 2, ids


def test_story_candidates_that_reached_the_judge_are_counted():
    """1 of 38 on the four-hour source, and no artefact recorded it."""
    cands = [_cand(0, 30, story={"archetypes": ["FAIL"]}),
             _cand(60, 90, story={"archetypes": ["FUNNY"]}, llm_score=70.0,
                   llm_rank=2),
             _cand(120, 150)]
    out = rt.build_selection_trace(cands, mode="story_v1")
    assert out["totals"]["story_candidates"] == 2
    assert out["totals"]["story_judged"] == 1


def test_the_two_score_scales_are_kept_apart():
    out = rt.build_selection_trace(
        [_cand(0, 30, overall=58.0, llm_score=0.0, llm_rank=40)], mode="story_v1")
    scores = out["entries"][0]["scores"]
    assert scores["overall"] == 58.0 and scores["llm_score"] == 0.0
    assert scores["llm_rank"] == 40


def test_the_variant_id_survives_a_reshuffle():
    """Identified by span, not list position: position moves whenever anything
    upstream changes, which would make every rerun look like a total
    reshuffle."""
    a = rt.build_selection_trace([_cand(0, 30), _cand(60, 90)], mode="legacy")
    b = rt.build_selection_trace([_cand(60, 90), _cand(0, 30)], mode="legacy")
    assert a["structure_fingerprint"] == b["structure_fingerprint"]


def test_an_empty_field_still_produces_a_valid_artefact():
    out = rt.build_selection_trace([], mode="legacy")
    assert out["totals"]["candidates"] == 0
    assert out["trace_version"] == rt.SELECTION_TRACE_VERSION


def test_every_status_is_from_the_closed_list():
    out = rt.build_selection_trace(
        [_cand(0, 30), _cand(1, 31, llm_score=0.0), _cand(2, 32, llm_score=9.0)],
        mode="legacy")
    assert all(e["judge_status"] in rt.JUDGE_STATUSES for e in out["entries"])


# ── Both artefacts are registered ────────────────────────────────────────────


def test_storage_knows_where_to_put_them():
    """An artefact the storage layer does not know about raises on write, and
    it would do so at the END of a scoring run."""
    from services.clipper import storage

    assert "reasoning_run" in storage.ARTIFACT_NAMES
    assert "selection_trace" in storage.ARTIFACT_NAMES


def test_the_whole_write_path_round_trips(tmp_path, monkeypatch):
    """Serialise → disk → parse, through the real storage layer.

    The registry check above only proves the names are allowed. This proves the
    dicts survive `json.dumps` with the compact separators storage uses, which
    is where a stray non-serialisable value would surface — at the end of a
    scoring run, after everything expensive had already been paid for.
    """
    from config import settings
    from services.clipper import storage
    from workers.clipper_build import _write_traces

    monkeypatch.setattr(type(settings), "clipper_dir",
                        property(lambda _self: tmp_path))
    storage.ensure_dirs("p1")

    t = _trace(settings_snapshot={"clip_count": 8}, launched_by="test")
    t.note_attempt("anchors", "ollama", ok=False, error="refused",
                   request="anchors#0")
    t.note_attempt("anchors", "openai", ok=True, request="anchors#0")
    t.note_chunk("anchors", 0, chars=900, t_start=0.0, t_end=1200.0, produced=4)
    t.note_count("anchors", 4)

    ranked = [_cand(0, 30, story={"archetypes": ["FAIL"]}, rank_position=1,
                    overall=71.0, llm_score=88.0, llm_rank=1),
              _cand(60, 90, overall=58.0)]
    _write_traces("p1", t, ranked, "story_v1")

    run = storage.read_artifact("p1", "reasoning_run")
    sel = storage.read_artifact("p1", "selection_trace")
    assert run["outcome"] == "fallback"
    assert run["launched_by"] == "test"
    assert run["chunks"][0]["t_end"] == 1200.0
    assert sel["totals"] == {"candidates": 2, "judged": 1, "not_evaluated": 1,
                             "winners": 1, "story_candidates": 1,
                             "story_judged": 1, "pool_rounds": 0,
                             "eliminated": 0}
    # Both artefacts carry the SAME run id. Written independently, they can
    # otherwise be from different runs and be compared as if they were one.
    assert run["run_id"] == sel["run_id"] == t.run_id


def test_the_two_artefacts_agree_on_how_many_pool_rounds_ran(tmp_path,
                                                             monkeypatch):
    """`pool_rounds` must come from the run, not from the parameter default.

    On `gateslice4h` the two artefacts disagreed: `reasoning_run` said 1 round,
    `selection_trace` said 0 — because `_write_traces` never passed it and 0 is
    the default. 0 is not a neutral value here: it is the one that means the
    judged pool was accepted on the first pass, so the trace was asserting the
    opposite of what the run did, in the artefact an audit reads first.
    """
    from config import settings
    from services.clipper import storage
    from workers.clipper_build import _write_traces

    monkeypatch.setattr(type(settings), "clipper_dir",
                        property(lambda _self: tmp_path))
    storage.ensure_dirs("p1")

    t = _trace()
    t.note_count("pool_rounds", 2)
    _write_traces("p1", t, [_cand(0, 30, rank_position=1)], "story_v1")

    run = storage.read_artifact("p1", "reasoning_run")
    sel = storage.read_artifact("p1", "selection_trace")
    assert run["counts"]["pool_rounds"] == 2
    assert sel["totals"]["pool_rounds"] == 2


def test_a_candidate_dropped_before_the_board_still_appears(tmp_path, monkeypatch):
    """Built from the SURVIVORS, this artefact could say how the winners ranked
    and could not say why a moment is missing — which is the question it exists
    for."""
    out = rt.build_selection_trace(
        [_cand(0, 30), _cand(60, 90), _cand(120, 150)],
        mode="story_v1",
        eliminated={1: rt.ELIMINATED_DEDUPED,
                    2: rt.ELIMINATED_ALREADY_EXPORTED})
    reasons = {e["variant_id"]: e["eliminated_by"] for e in out["entries"]}
    assert reasons["0.00-30.00"] is None
    assert reasons["60.00-90.00"] == rt.ELIMINATED_DEDUPED
    assert reasons["120.00-150.00"] == rt.ELIMINATED_ALREADY_EXPORTED
    assert out["totals"]["eliminated"] == 2


def test_an_unparseable_answer_is_not_a_healthy_run():
    """`note_attempt(ok=True)` only means a provider returned a non-empty
    string. Models return prose and half-fenced blocks, and without recording
    the parse a run where every answer was garbage reported `complete`."""
    t = _trace()
    t.note_attempt("anchors", "ollama", ok=True, request="anchors#0")
    t.note_result("anchors", "anchors#0", parsed=False)
    assert t.unusable == [{"stage": "anchors", "request": "anchors#0",
                           "parsed": False}]
    assert t.outcome() == "failed_non_blocking"


def test_a_parsed_answer_leaves_the_run_complete():
    t = _trace()
    t.note_attempt("anchors", "ollama", ok=True, request="anchors#0")
    t.note_result("anchors", "anchors#0", parsed=True)
    assert t.outcome() == "complete"


def test_the_disambiguator_does_not_depend_on_list_order():
    """An ordinal suffix means "#2" swaps between two runs that merely
    enumerated the same two candidates the other way round."""
    a = _cand(0, 30, text="first one")
    b = _cand(0.001, 30.004, text="second one")
    ids_ab = [e["variant_id"] for e in
              rt.build_selection_trace([a, b], mode="x")["entries"]]
    ids_ba = [e["variant_id"] for e in
              rt.build_selection_trace([b, a], mode="x")["entries"]]
    assert len(set(ids_ab)) == 2
    assert set(ids_ab) == set(ids_ba)


def test_a_broken_trace_cannot_fail_the_run(tmp_path, monkeypatch, caplog):
    """The first thing anyone does with observability that breaks the thing it
    observes is turn it off — and then the next audit reconstructs everything
    by hand again."""
    from config import settings
    from workers.clipper_build import _write_traces

    monkeypatch.setattr(type(settings), "clipper_dir",
                        property(lambda _self: tmp_path))

    class Exploding:
        counts: dict = {}

        def as_dict(self):
            raise RuntimeError("trace is broken")

    _write_traces("p1", Exploding(), [], "legacy")  # must not raise


def test_an_unchunked_run_has_no_chunk_fingerprint():
    """Found by the first rerun of the gate corpus, not by a test.

    A run that reused cached anchors never chunks anything, and the digest of
    an empty list is the SAME on every project — so two unrelated sources both
    reported `4f53cda18c2baa0c` and a comparison read it as a match. A
    fingerprint that agrees across unrelated inputs measures nothing.
    """
    empty, chunked = _trace(), _trace()
    chunked.note_chunk("anchors", 0, chars=100, produced=3)
    assert empty.as_dict()["chunk_plan_fingerprint"] is None
    assert chunked.as_dict()["chunk_plan_fingerprint"] is not None


def test_two_judging_rounds_add_up_instead_of_the_second_erasing_the_first():
    """`note_count` ASSIGNS. `_judge_pool` runs once per round.

    So a run that judged two pools recorded only the second one, and a second
    round that came back with an empty pool would have reported zero moments
    judged for a run that judged plenty — the trace understating the work in
    exactly the runs that did the most of it. Rounds exclude what earlier rounds
    already took, so adding them counts distinct moments and never double-counts.

    The field census is the other case and must NOT accumulate: it describes the
    whole field, is identical every round, and summing it would report twice as
    many story moments as exist.
    """
    from workers import clipper_judging

    # Distinct VOCABULARY, not distinct numbers. Six lines that share six of
    # seven words are one moment as far as `dedupe._group` is concerned, and it
    # is right — text similarity is one of the three axes it compares on. The
    # first version of this fixture collapsed all six into one group.
    import random
    words = ("anvil beacon coral drip ember flint glow harbor ingot jungle kelp "
             "lava mossy nether orb portal quartz slime torch vine").split()
    cands = [{"start": i * 100.0, "end": i * 100.0 + 30.0, "overall": 90.0 - i,
              "text": " ".join(random.Random(i).sample(words, 8)),
              "story": {"payoff_t": i * 100.0 + 20.0, "validity": "valid",
                        "grounding": {"payoff": True}}}
             for i in range(6)]

    trace = _trace()
    first = clipper_judging._judge_pool(cands, 1000.0, trace)
    taken = {g["moment_id"] for g in first["selected_groups"]}
    clipper_judging._judge_pool(cands, 1000.0, trace, exclude=taken,
                                groups=first["all_groups"])

    assert trace.counts["judge_pool_moments"] == len(first["pool"])
    assert trace.counts["story_groups"] == 6


def test_a_source_of_unknown_length_says_so_instead_of_reporting_a_flat_shape():
    """Four zeros is the flattest shape there is, and a reader comparing
    sources would take it as "evenly spread". Same failure as `pool_rounds`
    defaulting to 0: a placeholder that reads as a measurement.
    """
    from workers import clipper_judging

    trace = _trace()
    clipper_judging._note_spread(trace, None)

    stage = next(s for s in trace.stages if s["name"] == "story_spread")
    assert stage["status"] == "unavailable"
    assert not any(k.startswith("story_q") for k in trace.counts)
