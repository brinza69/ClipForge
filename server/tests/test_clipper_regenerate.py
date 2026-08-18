"""The editor's rebuild buttons, and what they were reading.

Both regeneration paths took their words from `clip.transcript_segments` — a
column that exists on the model and that NOTHING in the clipper has ever
written. So "rebuild captions" produced an empty plan and "rebuild headline"
gave the model no words, silently, because an empty transcript is a legitimate
state for a clip with no speech.

The canonical source is the project's `transcripts` row, sliced by the same
`_clip_words` the caption plan is built with, so the editor and the scorer cut
at the same places.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from database import async_session
from models import (
    ClipModel, ClipStatus, JobModel, ProjectModel, TranscriptModel,
)
from routers.clipper_clips import _project_transcript

SEGMENTS = [
    {"start": 0.0, "end": 6.0, "text": "one two three",
     "words": [{"word": "one", "start": 0.0, "end": 1.0},
               {"word": "two", "start": 1.2, "end": 2.0},
               {"word": "three", "start": 2.2, "end": 3.0}]},
    {"start": 10.0, "end": 16.0, "text": "four five",
     "words": [{"word": "four", "start": 10.0, "end": 11.0},
               {"word": "five", "start": 11.2, "end": 12.0}]},
]


async def _seed(pid: str, with_transcript: bool = True) -> None:
    async with async_session() as session:
        session.add(ProjectModel(id=pid, title="t", status="ready",
                                 created_at=datetime.utcnow()))
        if with_transcript:
            session.add(TranscriptModel(
                id=f"tr-{pid}", project_id=pid, language="en",
                segments=SEGMENTS, full_text="one two three four five",
                word_count=5))
        await session.commit()


@pytest.mark.asyncio
async def test_the_transcript_comes_from_the_project_not_the_clip_column():
    await _seed("regen-p1")
    async with async_session() as session:
        t = await _project_transcript(session, "regen-p1")
    assert len(t["segments"]) == 2, "the editor is reading an empty transcript again"
    assert t["full_text"] == "one two three four five"


@pytest.mark.asyncio
async def test_a_project_with_no_transcript_is_an_empty_one_not_a_crash():
    await _seed("regen-p2", with_transcript=False)
    async with async_session() as session:
        assert await _project_transcript(session, "regen-p2") == {"segments": []}


@pytest.mark.asyncio
async def test_the_headline_gets_a_list_of_words_for_its_window():
    """`generate_headline` iterates `cand["words"]` and reads `word` off each
    one. The old code handed it a dict, so it was either empty or a crash."""
    from services.clipper.captions import _clip_words

    await _seed("regen-p3")
    async with async_session() as session:
        transcript = await _project_transcript(session, "regen-p3")

    words = _clip_words({"start": 0.0, "end": 6.0}, transcript)
    assert isinstance(words, list) and words, "no words for a window that has them"
    assert all(isinstance(w, dict) and "word" in w for w in words)
    assert [w["word"] for w in words] == ["one", "two", "three"], (
        "the window was not respected")


@pytest.mark.asyncio
async def test_the_column_the_old_code_read_is_still_empty():
    """Not a style point: if something ever starts writing it, the two sources
    can disagree and this test is where that gets noticed."""
    await _seed("regen-p4")
    async with async_session() as session:
        session.add(ClipModel(
            id="regen-c4", project_id="regen-p4", start_time=0.0, end_time=6.0,
            status=ClipStatus.candidate.value, created_at=datetime.utcnow()))
        await session.commit()
        clip = await session.get(ClipModel, "regen-c4")
        assert not clip.transcript_segments


# ── export is refused only where it can only be a mistake ────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["candidate", "approved", "exported", "failed"])
async def test_export_is_allowed_from_the_states_the_product_uses(client, status):
    """`candidate` because the documented flow exports straight off the board,
    `exported`/`failed` because re-rendering after an edit is what the clip
    editor is for."""
    pid, cid = f"exp-{status}", f"expc-{status}"
    await _seed(pid, with_transcript=False)
    async with async_session() as session:
        session.add(ClipModel(id=cid, project_id=pid, start_time=0.0, end_time=5.0,
                              status=status, created_at=datetime.utcnow()))
        await session.commit()

    r = await client.post(f"/api/clipper/clips/{cid}/export")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [("exporting", "already_exporting"),
                                         ("rejected", "not_exportable")])
async def test_export_is_refused_where_it_can_only_be_a_mistake(client, status, code):
    """A second job on a clip already rendering means two writers on one file
    and progress oscillating between them; a rejected clip was rejected."""
    pid, cid = f"bad-{status}", f"badc-{status}"
    await _seed(pid, with_transcript=False)
    async with async_session() as session:
        session.add(ClipModel(id=cid, project_id=pid, start_time=0.0, end_time=5.0,
                              status=status, created_at=datetime.utcnow()))
        await session.commit()

    r = await client.post(f"/api/clipper/clips/{cid}/export")
    assert r.status_code == 409, r.text
    assert code in json.dumps(r.json())
