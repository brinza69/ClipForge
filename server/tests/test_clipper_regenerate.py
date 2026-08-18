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


# ── one clip, one export ─────────────────────────────────────────────────────
#
# The endpoint read the status, checked it, wrote it and committed — two
# statements, so two requests could both read `candidate` and both enqueue a
# render. Two jobs writing one file, with progress oscillating between them.
#
# The job queue got exactly this fix on 2026-08-17 and the clip side was left
# read-then-write on the same day, which is how a lesson gets applied in one
# place and not the other.


async def _clip(pid: str, cid: str, status: str = "candidate") -> None:
    await _seed(pid, with_transcript=False)
    async with async_session() as session:
        session.add(ClipModel(id=cid, project_id=pid, start_time=0.0, end_time=5.0,
                              status=status, created_at=datetime.utcnow()))
        await session.commit()


async def _status_of(cid: str) -> str:
    async with async_session() as session:
        return (await session.get(ClipModel, cid)).status


@pytest.mark.asyncio
async def test_only_one_caller_can_claim_a_clip_for_export():
    """The claim, tested where it can actually fail.

    Eight gathered requests through the ASGI transport do NOT discriminate:
    measured, that version passes against the old read-then-write endpoint too,
    because the requests do not interleave enough to catch the race. A test
    that cannot fail against the bug proves nothing about the fix.

    Calling the claim directly with a session each does discriminate, and the
    race is not theoretical: measured on a fresh DB, read-then-write lets FIVE
    of eight callers believe they own the clip, while the conditional UPDATE
    lets exactly one.
    """
    import asyncio

    from routers.clipper_clips import claim_for_export

    await _clip("conc-p", "conc-c")

    async def attempt() -> bool:
        async with async_session() as session:
            return await claim_for_export(session, "conc-c")

    results = await asyncio.gather(*(attempt() for _ in range(8)))
    assert sum(results) == 1, f"{sum(results)} callers thought they had the clip"
    assert await _status_of("conc-c") == "exporting"


@pytest.mark.asyncio
async def test_the_endpoint_queues_one_render_and_refuses_the_rest(client):
    """The behaviour on top of the claim: 200 once, 409 after."""
    await _clip("ep-p", "ep-c")
    first = await client.post("/api/clipper/clips/ep-c/export")
    assert first.status_code == 200, first.text
    second = await client.post("/api/clipper/clips/ep-c/export")
    assert second.status_code == 409
    assert "already_exporting" in second.text


@pytest.mark.asyncio
async def test_status_cannot_be_written_through_the_generic_patch(client):
    """The whitelist used to include `status` as a bare `str`, which let a
    client write any value AND walk past the export guard by setting the state
    the guard wanted to see. A whitelist containing the field the guards read
    is not a whitelist."""
    await _clip("patch-p", "patch-c", status="exporting")

    r = await client.patch("/api/clipper/clips/patch-c",
                           json={"status": "candidate", "title": "still allowed"})
    assert r.status_code == 200, r.text
    assert "status" not in r.json()["changed"]
    assert await _status_of("patch-c") == "exporting"
    assert r.json()["clip"]["title"] == "still allowed"


@pytest.mark.asyncio
async def test_a_rendering_clip_cannot_be_approved_or_rejected(client):
    await _clip("mid-p", "mid-c", status="exporting")
    for action in ("approve", "reject"):
        r = await client.post(f"/api/clipper/clips/mid-c/{action}")
        assert r.status_code == 409, f"{action}: {r.text}"
        assert "illegal_transition" in r.text
    assert await _status_of("mid-c") == "exporting"


@pytest.mark.asyncio
async def test_approving_an_approved_clip_is_a_double_click_not_an_error(client):
    await _clip("dbl-p", "dbl-c", status="approved")
    r = await client.post("/api/clipper/clips/dbl-c/approve")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_an_exported_clip_can_still_be_re_rendered_and_rejected(client):
    """Re-rendering after an edit is what the clip editor is for, so `exported`
    is not terminal — the strict machine an audit would draw is the wrong
    product."""
    await _clip("re-p", "re-c", status="exported")
    assert (await client.post("/api/clipper/clips/re-c/export")).status_code == 200

    await _clip("rj-p", "rj-c", status="exported")
    assert (await client.post("/api/clipper/clips/rj-c/reject")).status_code == 200
