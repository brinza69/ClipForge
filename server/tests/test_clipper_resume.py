"""Model output survives a re-run, and only while it is still valid.

`atoms`, `promises` and `threads` were already checkpointed; the LLM passes
were not, so any failure after them — or any ordinary re-score — paid for them
again. Anchors are the story path's model call and the one worth keeping.

The stamp is the half that matters. A checkpoint without one is WORSE than no
checkpoint: change the prompt version or the reasoning mode, re-score, and the
run silently reuses answers the new configuration would never have produced.
"""

from __future__ import annotations

import pytest

from workers import clipper_build as build


def _stamp(reasoning="story_v1", duration=1000.0):
    return build._anchor_stamp({"reasoning_version": reasoning}, duration)


@pytest.fixture
def store(monkeypatch):
    """storage.read_artifact/write_artifact against a dict."""
    disk: dict[str, object] = {}
    monkeypatch.setattr(build.storage, "read_artifact",
                        lambda _pid, name: disk.get(name))
    monkeypatch.setattr(build.storage, "write_artifact",
                        lambda _pid, name, data: disk.__setitem__(name, data))
    return disk


def test_a_cached_answer_comes_back(store):
    build._cache("p1", "anchors", _stamp(), [{"payoff_t": 12.0}])
    assert build._cached("p1", "anchors", _stamp()) == [{"payoff_t": 12.0}]


def test_nothing_cached_reads_as_nothing(store):
    assert build._cached("p1", "anchors", _stamp()) is None


def test_a_different_prompt_version_invalidates_it(store, monkeypatch):
    build._cache("p1", "anchors", _stamp(), [{"payoff_t": 12.0}])
    monkeypatch.setattr("services.clipper.llm_select.ANCHOR_PROMPT_VERSION",
                        "anchor_v99")
    assert build._cached("p1", "anchors", _stamp()) is None, (
        "a new prompt must not reuse the old prompt's answers")


def test_switching_reasoning_mode_invalidates_it(store):
    build._cache("p1", "anchors", _stamp("story_v1"), [{"payoff_t": 12.0}])
    assert build._cached("p1", "anchors", _stamp("legacy")) is None


def test_a_different_source_length_invalidates_it(store):
    """A re-pointed project is a different stream, and every anchor timestamp
    in the cache belongs to the old one."""
    build._cache("p1", "anchors", _stamp(duration=1000.0), [{"payoff_t": 12.0}])
    assert build._cached("p1", "anchors", _stamp(duration=4000.0)) is None


def test_an_artifact_written_by_an_older_build_is_ignored(store):
    """Before the stamp existed these files were bare lists."""
    store["anchors"] = [{"payoff_t": 12.0}]
    assert build._cached("p1", "anchors", _stamp()) is None


def test_the_stamp_covers_what_changes_the_answer():
    keys = set(_stamp())
    assert {"prompt", "reasoning", "engines", "duration"} <= keys, (
        "the stamp has to name everything that changes what the model is asked")
