"""The transcribe stage must report WHERE it is, not just that it is running.

A 4-hour source takes ~90 minutes here. For that whole time the stage used to
send the constant string "Transcribing", which is indistinguishable from a hung
job — and session 3's handoff records losing a run to exactly that ambiguity.
The transcriber already emits "Transcribing... 1234.5s / 15826.0s (12%)
[chunk 3/9]"; the pipeline was throwing the message away.

Driven through handle_transcribe with a fake transcriber, so it tests the wiring
rather than restating it.
"""

from __future__ import annotations

import pytest

from workers import clipper_pipeline


class _Queue:
    def __init__(self):
        self.seen: list[tuple[float, str]] = []

    async def update_progress(self, job_id, progress, message=""):
        self.seen.append((progress, message))

    def is_cancelled(self, job_id):
        return False

    async def enqueue(self, **kw):
        return "stub"


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """handle_transcribe with the DB, storage and transcriber replaced."""
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"RIFF")

    class _Project:
        id = "p1"
        duration = 15826.0
        clipper_settings = None

    async def _load_project(_pid):
        return _Project()

    async def _set_status(_pid, _status):
        return None

    async def _fake_transcribe(path, *, duration, is_cancelled, on_progress,
                               language, keep_punctuation):
        # What the real worker sends, at the grain it sends it.
        await on_progress(0.05, "Transcribing... 12.0s / 15826.0s (5%) [chunk 1/9]")
        await on_progress(0.52, "Transcribing... 8229.5s / 15826.0s (52%) [chunk 5/9]")
        # Empty on purpose: handle_transcribe raises on an empty transcript
        # BEFORE it opens a session, so this test never writes to the database.
        # The suite runs against the real clipforge.db — an earlier version of
        # this file left a stray `p1` transcript row in it.
        return {"segments": [], "full_text": "", "word_count": 0, "language": "en"}

    monkeypatch.setattr(clipper_pipeline, "_load_project", _load_project)
    monkeypatch.setattr(clipper_pipeline, "_set_status", _set_status)
    monkeypatch.setattr(clipper_pipeline.storage, "paths",
                        lambda _pid: {"audio": audio})
    monkeypatch.setattr("services.transcriber.transcribe", _fake_transcribe)
    return _Queue()


async def test_the_transcribers_own_message_reaches_the_queue(wired):
    """The detail is the point: seconds done, seconds total, which chunk."""
    with pytest.raises(RuntimeError, match="empty"):
        await clipper_pipeline.handle_transcribe("j1", "p1", None, {}, wired)

    detailed = [m for _p, m in wired.seen if "15826.0s" in m]
    assert detailed, f"no message carried the position; got {wired.seen}"
    assert any("52%" in m for m in detailed), "the later position was not reported"


async def test_progress_advances_and_stays_a_fraction(wired):
    with pytest.raises(RuntimeError, match="empty"):
        await clipper_pipeline.handle_transcribe("j1", "p1", None, {}, wired)

    values = [p for p, _m in wired.seen]
    assert all(0.0 <= p <= 1.0 for p in values), f"not a 0..1 fraction: {values}"
    assert max(values) > min(values), "progress never moved"
