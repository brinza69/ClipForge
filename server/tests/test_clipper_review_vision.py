"""Pass D's second half: the part that can see.

`review.py` checks what a rule can state. This asks what a rule cannot — did
the moment the clip was cut for actually happen on screen. It is the only part
of the pipeline that spends money, so the tests that matter are the ones about
it staying quiet: off by default, silent without a key, and never able to
reject a clip on six still frames.
"""

from __future__ import annotations

from services.clipper import review_vision as rv


def test_it_is_off_by_default_and_not_pointed_at_a_retired_model():
    """`clipper_llm_judge_model` still says gpt-4o, which is no longer on
    OpenAI's pricing page. This one must not inherit that."""
    from config import Settings

    s = Settings()
    assert s.clipper_vision_review is False
    assert s.clipper_vision_model == "gpt-5.6-terra"


def test_a_missing_payoff_is_reported():
    out = rv.findings_from(
        '{"on_screen": false, "readable": true, "problem": "the kill is off screen"}')
    assert [f.kind for f in out] == ["payoff_off_screen"]
    assert out[0].detail == "the kill is off screen"


def test_a_clean_answer_reports_nothing():
    assert rv.findings_from('{"on_screen": true, "readable": true, "problem": ""}') == []


def test_one_complaint_not_two():
    """A clip whose payoff is off screen is also hard to follow. Reporting both
    reads as two problems when there is one."""
    out = rv.findings_from('{"on_screen": false, "readable": false, "problem": "x"}')
    assert len(out) == 1
    assert out[0].kind == "payoff_off_screen"


def test_it_never_rejects():
    """Six still frames and one opinion. A clip deleted on that cannot be
    argued with, so this pass only ever asks for a second look."""
    for answer in ('{"on_screen": false, "readable": false, "problem": "bad"}',
                   '{"on_screen": true, "readable": false, "problem": "bad"}'):
        assert all(f.severity == "revise" for f in rv.findings_from(answer))


def test_junk_is_dropped_rather_than_guessed_at():
    for junk in ("", "sorry, I cannot help", "{not json}", "null", "[1,2]"):
        assert rv.findings_from(junk) == []


def test_json_inside_prose_is_still_read():
    """Models wrap answers in fences and prefaces; §31 asks for a tolerant
    parser, not a strict one."""
    out = rv.findings_from(
        'Here you go:\n```json\n{"on_screen": false, "readable": true, "problem": "p"}\n```')
    assert [f.kind for f in out] == ["payoff_off_screen"]


def test_the_prompt_carries_the_clip_subject():
    """Without it the model can only describe what it sees, and describing is
    not reviewing."""
    p = rv.build_prompt("he wastes seven ingots", 6)
    assert "he wastes seven ingots" in p
    assert "6 images" in p


def test_an_empty_subject_does_not_produce_an_empty_question():
    assert "unspecified moment" in rv.build_prompt("   ", 6)


def test_merging_recomputes_the_verdict_over_both_halves():
    """The seam `review.verdict` was written for: a model-driven reviewer
    appends to the same list and the verdict is taken over all of it."""
    local = {"verdict": "APPROVE", "findings": [], "warnings": [], "sampled": 12}
    vision = {"model": "m", "prompt_version": "vision_v1", "usage": {"prompt_tokens": 1230},
              "findings": [{"kind": "payoff_off_screen", "severity": "revise",
                            "at": 0.0, "detail": "d"}],
              "warnings": []}
    out = rv.merge(local, vision)
    assert out["verdict"] == "REVISE"
    assert len(out["findings"]) == 1
    assert out["vision"]["usage"]["prompt_tokens"] == 1230
    assert out["sampled"] == 12, "the local half's own record must survive"


def test_merging_keeps_a_local_reject_a_reject():
    local = {"verdict": "REJECT", "warnings": [],
             "findings": [{"kind": "dead_clip", "severity": "reject", "at": 0.0, "detail": "d"}]}
    out = rv.merge(local, {"findings": [], "warnings": [], "usage": {}})
    assert out["verdict"] == "REJECT"


def test_an_unreadable_render_is_a_warning_not_a_crash(tmp_path):
    broken = tmp_path / "x.mp4"
    broken.write_bytes(b"nope")
    assert rv.sample_frames(broken) == []


# ── what the model is asked to look for ──────────────────────────────────────


def test_the_story_engine_beats_the_headline_as_the_subject():
    """Set by watching the first real answer. Handed the headline — which is
    extracted FROM the transcript — the model reviewed text against text:
    "captions do not show the stated phrase". The story engine's `why`
    describes the moment as something that HAPPENS, which is what a picture can
    be compared against."""
    from workers.clipper_render_jobs import _what_happens

    class _C:
        reasoning = {"story": {"why": "he mines into lava and loses the diamonds"}}
        headline_text = "Way oh way oh let's go eat a tepian oh"
        title = "t"
        transcript_text = "way oh way oh"

    assert _what_happens(_C()) == "he mines into lava and loses the diamonds"


def test_the_headline_carries_it_when_the_story_engine_is_off():
    """Which is most of the time — llm_select ships off."""
    from workers.clipper_render_jobs import _what_happens

    class _C:
        reasoning = None
        headline_text = "the iron golem saves him"
        title = "t"
        transcript_text = "..."

    assert _what_happens(_C()) == "the iron golem saves him"


def test_a_clip_with_nothing_to_say_about_it_returns_empty():
    from workers.clipper_render_jobs import _what_happens

    class _C:
        reasoning = None
        headline_text = ""
        title = "x"
        transcript_text = None

    assert _what_happens(_C()) == ""
