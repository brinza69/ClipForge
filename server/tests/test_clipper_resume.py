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


# ── cached artifacts have to have been made by this code, for this file ──────
#
# `retry_analysis` resumed from the furthest stage whose artifacts EXISTED, and
# existence was the whole test. Both ways that goes wrong are already written
# down in this repo: session 3 drew conclusions from a `faces.json` produced
# before the detector was fixed, and repointing a project from a 480p cut to
# the 1080p original produced garbage because an 854x480 plan FITS INSIDE a
# 1920x1080 frame. `meta.json` has recorded the version and the source size
# since the clipper shipped and nothing read them.


def _meta(project_id, version, filesize):
    from services.clipper import storage

    storage.ensure_dirs(project_id)
    storage.write_artifact(project_id, "meta", {
        "analysis_version": version,
        "source": {"duration": 12.0, "filesize": filesize},
        "proxy": {"width": 480, "fps": 10},
    })


def test_matching_version_and_size_is_not_stale(tmp_path):
    from services.clipper import ANALYSIS_VERSION, storage

    media = tmp_path / "src.mp4"
    media.write_bytes(bytes(4096))
    _meta("stale-ok", ANALYSIS_VERSION, 4096)
    assert storage.stale_artifacts("stale-ok", media) is None


def test_a_different_analysis_version_is_stale(tmp_path):
    from services.clipper import storage

    media = tmp_path / "src.mp4"
    media.write_bytes(bytes(4096))
    _meta("stale-ver", "0", 4096)
    reason = storage.stale_artifacts("stale-ver", media)
    assert reason and "analysis_version" in reason


def test_a_source_that_changed_size_is_stale(tmp_path):
    """The 480p-to-1080p repoint, which no bounds check can catch."""
    from services.clipper import ANALYSIS_VERSION, storage

    media = tmp_path / "src.mp4"
    media.write_bytes(bytes(999))
    _meta("stale-src", ANALYSIS_VERSION, 4096)
    reason = storage.stale_artifacts("stale-src", media)
    assert reason and "source changed" in reason


def test_no_meta_means_nothing_to_distrust(tmp_path):
    from services.clipper import storage

    storage.ensure_dirs("stale-none")
    assert storage.stale_artifacts("stale-none", tmp_path / "missing.mp4") is None
