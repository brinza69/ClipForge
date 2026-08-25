"""What the stream has been about, up to any point in it. §2.

A per-chunk pass cannot see the stream. These tests cover the two halves of
fixing that: turning threads into stretches, and putting the finished ones in
front of the model — which is the half that matters, because six times now this
codebase has built a structure nothing read.
"""

from __future__ import annotations

import pytest

from services.clipper import episodes, llm_select


def _thread(tid: str, start: float, end: float, *words: str) -> dict:
    return {"id": tid, "start": start, "end": end, "size": 4,
            "keywords": list(words)}


def _atoms(*spans) -> list[dict]:
    """Atoms carrying the words an episode should be labelled by.

    Full shape, not just text and times: `detect_anchors` also runs these
    through `atoms.to_lines`, which reads the signal blocks.
    """
    return [{"i": i, "start": a, "end": b, "text": " ".join([w] * 4),
             "audio": {"energy": 0.3, "peaks": 0, "laughter": 0.0},
             "visual": {"motion": 0.3, "scene_change": False, "ui": 0.0},
             "semantic": {"kind": "speech", "cues": 0, "words": 4,
                          "importance": 0.2}}
            for i, (a, b, w) in enumerate(spans)]


# ── turning arcs into stretches ──────────────────────────────────────────────


def test_the_stream_is_cut_on_the_clock():
    """Not on gaps between arcs. Threads on a dense source overlap constantly,
    so there is no quiet moment to cut at — the first version produced TWO
    episodes for a 4-hour stream, one of them spanning minute 8 to minute 240."""
    out = episodes.build([_thread("t1", 0.0, 3600.0, "boss")])
    assert len(out) == 3, "an hour should be three twenty-minute stretches"
    assert out[0]["start"] == 0.0 and out[0]["end"] == pytest.approx(1200.0)


def test_an_arc_belongs_to_every_stretch_it_runs_through():
    """A forty-minute fight is what two consecutive episodes are both about."""
    out = episodes.build([_thread("long", 0.0, 2400.0, "boss")])
    assert all("long" in e["threads"] for e in out)


def test_a_moment_is_not_a_stretch():
    assert episodes.build([_thread("t", 0.0, 5.0, "x")]) == []


def test_a_short_source_gets_no_fake_structure():
    assert episodes.build([]) == []
    assert episodes.build(None) == []


def test_a_stretch_is_labelled_by_what_makes_it_different():
    """Two rules failed before this one. "The words the most ARCS share"
    returned "don't, it's, right, can't" on the real source — the words every
    arc has identify nothing."""
    arcs = [_thread("t", 0.0, 2400.0, "x")]
    atoms = _atoms((100.0, 200.0, "golem"), (300.0, 400.0, "okay"),
                   (1400.0, 1500.0, "diamond"), (1600.0, 1700.0, "okay"))
    out = episodes.build(arcs, atoms)
    assert out[0]["keywords"] == ["golem"]
    assert out[1]["keywords"] == ["diamond"]
    assert all("okay" not in e["keywords"] for e in out), (
        "a word said in every stretch has no discriminating power")


def test_a_word_said_once_is_not_a_label():
    """A mis-transcription is rare everywhere else by definition."""
    arcs = [_thread("t", 0.0, 2400.0, "x")]
    atoms = [{"start": 100.0, "end": 101.0, "text": "gorblesnatch"},
             {"start": 1300.0, "end": 1301.0, "text": "hello"}]
    assert episodes.build(arcs, atoms)[0]["keywords"] == []


# ── what a prompt is given ───────────────────────────────────────────────────


def test_only_finished_stretches_count_as_background():
    eps = [{"start": 0.0, "end": 600.0}, {"start": 700.0, "end": 1800.0}]
    assert len(episodes.before(eps, 650.0)) == 1
    assert episodes.before(eps, 100.0) == []


def test_the_summary_is_bounded():
    """The point of a summary is that it is shorter than what it summarises."""
    eps = [{"start": i * 100.0, "end": i * 100.0 + 50.0} for i in range(40)]
    assert len(episodes.before(eps, 1e9, limit=8)) == 8


def test_the_most_recent_are_the_ones_kept():
    eps = [{"start": i * 100.0, "end": i * 100.0 + 50.0, "keywords": [f"k{i}"]}
           for i in range(20)]
    kept = episodes.before(eps, 1e9, limit=3)
    assert [e["keywords"][0] for e in kept] == ["k17", "k18", "k19"]


def test_the_lines_are_in_minutes_a_human_can_read():
    body = episodes.to_lines([
        {"start": 0.0, "end": 600.0, "keywords": ["warden", "deep"]}])
    assert "[0-10 min] warden, deep" in body


def test_nothing_to_summarise_produces_nothing():
    assert episodes.to_lines([]) == ""


# ── the consumer ─────────────────────────────────────────────────────────────


def test_the_prompt_carries_the_summary_and_says_not_to_clip_it():
    eps = [{"start": 0.0, "end": 600.0, "keywords": ["warden", "deep", "dark"]}]
    prompt = llm_select.anchor_prompt("[900] hello", 5, None, eps)
    assert "WHAT THE STREAM HAS BEEN ABOUT SO FAR" in prompt
    assert "warden, deep, dark" in prompt
    # The background is not the window; clipping from it would produce a
    # candidate that is not in the transcript the model was handed.
    assert "Do not clip from it" in prompt
    assert prompt.index("SO FAR") < prompt.index("Below is a livestream"), (
        "the summary is context for reading the transcript, so it comes first")


def test_a_prompt_with_no_summary_is_unchanged():
    """Every legacy path passes none, and must read exactly as before."""
    plain = llm_select.anchor_prompt("[0] hi", 5)
    assert not plain.startswith("WHAT THE STREAM")
    assert plain == llm_select.anchor_prompt("[0] hi", 5, None, [])


async def test_detect_anchors_puts_earlier_stretches_in_front_of_the_model(monkeypatch):
    """The wiring, end to end: threads in, summary in front of a LATER chunk.

    Chunking is forced, because that is the only shape where a summary has
    anything to say. A source small enough to fit in one prompt has no "before"
    — and that is the right answer for it, not a gap.
    """
    prompts: list[str] = []

    async def fake(engine, prompt, **kw):
        prompts.append(prompt)
        return "[]"

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    monkeypatch.setattr(llm_select, "chunk_lines",
                        lambda lines: lines.split("\n[1400]")[:1]
                        + ["[1400]" + lines.split("\n[1400]")[1]])

    await llm_select.detect_anchors(
        [{"start": 3000.0, "text": "later"}], 4000.0,
        threads=[_thread("t1", 0.0, 2400.0, "warden")],
        atoms=_atoms((100.0, 200.0, "warden"), (1400.0, 1500.0, "diamond")))

    assert len(prompts) == 2
    assert "WHAT THE STREAM HAS BEEN ABOUT SO FAR" not in prompts[0], (
        "the first chunk has nothing before it")
    assert "WHAT THE STREAM HAS BEEN ABOUT SO FAR" in prompts[1]
    assert "warden" in prompts[1]


async def test_a_stretch_the_chunk_is_still_inside_is_not_called_background(monkeypatch):
    """Describing a running episode as background would tell the model the
    moment it is reading is old news."""
    seen: dict = {}

    async def fake(engine, prompt, **kw):
        seen["prompt"] = prompt
        return "[]"

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    await llm_select.detect_anchors(
        [{"start": 100.0, "text": "in the middle of it"}], 4000.0,
        threads=[_thread("t1", 0.0, 2400.0, "warden")])
    assert "WHAT THE STREAM HAS BEEN ABOUT SO FAR" not in seen.get("prompt", "")
