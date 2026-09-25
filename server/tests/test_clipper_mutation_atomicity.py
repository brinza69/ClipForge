"""An accepted edit, the event that protects it, a claim and a rescore, raced.

PRP: PRPs/clipper-master-plan-2026-09-24.md §4 (B1). Every race is forced with an
EXPLICIT barrier between two connections to the suite's temporary SQLite file —
no sleep, no luck. The barrier (`Gate`) is an engine event that stops one
connection just before a named statement and hands control to the competitor.

It only stops where that connection holds NO transaction. A pause inside one
would hold SQLite's write lock, and the competitor would wait on busy_timeout
instead of interleaving — so where the gate finds the statement inside a
transaction, the window does not exist, `gate.window` says so, and the
competitor runs afterwards. The assertions are the same either way: they are the
contract, not a description of one implementation.

The contract, per edit: a 200 means the edit AND a manual event are on the row
after the competitor finished; anything else is an explicit 404/409 with neither.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.util import await_only

from database import async_session, engine
from models import ClipFeedbackModel, ClipModel, ProjectModel

RUN = "atomicrun001"
FIRST_WRITE = lambda s: not s.startswith(("SELECT", "PRAGMA"))  # noqa: E731
EVENT_INSERT = lambda s: s.startswith("INSERT INTO CLIP_FEEDBACK")  # noqa: E731
CLIP_DELETE = lambda s: s.startswith("DELETE FROM CLIPS")  # noqa: E731


class Gate:
    """One-shot: pauses the first statement `match` accepts (upper-cased)."""

    def __init__(self, match):
        self.match, self.window = match, None
        self.reached, self.release = asyncio.Event(), asyncio.Event()

    def __call__(self, conn, cursor, statement, params, context, executemany):
        if self.window is not None or not self.match(statement.lstrip().upper()):
            return
        self.window = not conn.connection.dbapi_connection._connection.in_transaction
        self.reached.set()
        if self.window:
            await_only(self.release.wait())


async def interleave(first, gate: Gate, second):
    """Run `first` to the gate, `second()` to the end, then the rest of `first`."""
    event.listen(engine.sync_engine, "before_cursor_execute", gate)
    try:
        task = asyncio.ensure_future(first)
        reached = asyncio.ensure_future(gate.reached.wait())
        await asyncio.wait({task, reached}, return_when=asyncio.FIRST_COMPLETED)
        if gate.window:
            other = await second()
            gate.release.set()
            return await task, other
        result = await task          # no window: the competitor goes after
        reached.cancel()
        return result, await second()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", gate)


@pytest.fixture
async def api():
    from httpx import ASGITransport, AsyncClient
    from main import app
    # A raw 500 must be observable as a response, not as a test crash.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _body(src) -> dict:
    from services.clipper.reaction_edit import compute_source_version
    return {"content_rect": {"x": 0, "y": 0, "w": 180, "h": 176},
            "face_rect": {"x": 0, "y": 0, "w": 84, "h": 60},
            "source_version": compute_source_version(str(src), 320, 180),
            "source_start": 1.0, "source_end": 5.0, "src_w": 320, "src_h": 180}


# kind -> (clip kwargs, request, "the edit is on the row", event it must leave)
_REACTION = {"layout": "reaction", "game_content_fit": True}
EDITS = {
    "patch": ({}, lambda api, c, b: api.patch(
        f"/api/clipper/clips/{c}", json={"caption_plan": {"preset_id": "clean"}}),
        lambda r: r.caption_plan == {"preset_id": "clean"}, "caption_changed"),
    "caption_source": ({}, lambda api, c, b: api.put(
        f"/api/clipper/clips/{c}/caption-source", json={"source_has_burned_captions": True}),
        lambda r: r.source_has_burned_captions is True, "caption_changed"),
    "reaction_put": ({"source_has_burned_captions": True}, lambda api, c, b: api.put(
        f"/api/clipper/clips/{c}/reaction-layout", json=b),
        lambda r: (r.layout_plan or {}).get("reaction_binding") is not None, "layout_changed"),
    "reaction_delete": ({"layout_plan": _REACTION}, lambda api, c, b: api.delete(
        f"/api/clipper/clips/{c}/reaction-layout"),
        lambda r: r.layout_plan is None, "layout_changed"),
    "regen_captions": ({}, lambda api, c, b: api.post(
        f"/api/clipper/clips/{c}/regenerate", json={"what": "captions"}),
        lambda r: r.caption_plan is not None, "caption_changed"),
    "approve": ({}, lambda api, c, b: api.post(f"/api/clipper/clips/{c}/approve"),
                lambda r: r.status == "approved", "approved"),
}
KINDS = list(EDITS)


async def _setup(tmp_path, kind: str, status: str = "candidate"):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"source guard for the atomicity tests")
    pid = "atom-" + uuid.uuid4().hex[:8]
    cid = pid + "-c"
    extra = {"export_path": "/tmp/x.mp4"} if status == "exported" else {}
    async with async_session() as s:
        s.add(ProjectModel(id=pid, title="atomicity", source_kind="file", status="ready",
                           video_path=str(src), width=320, height=180,
                           clipper_settings={"trim_silence": False}))
        s.add(ClipModel(id=cid, project_id=pid, title="t", start_time=1.0, end_time=5.0,
                        duration=4.0, status=status, selection_run_id="oldrun000001",
                        **extra, **EDITS[kind][0]))
        await s.commit()
    request = EDITS[kind][1]
    return pid, cid, lambda api: request(api, cid, _body(src))


async def _state(cid: str):
    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
        rows = (await s.execute(
            select(ClipFeedbackModel.event_type, ClipFeedbackModel.origin)
            .where(ClipFeedbackModel.clip_id == cid))).all()
    return clip, [tuple(r) for r in rows]


async def _rescore(pid: str) -> None:
    from workers.clipper_finalize import _write_clips
    cand = {"start": 500.0, "end": 505.0, "overall": 50.0, "headline": "fresh"}
    await _write_clips(pid, [cand], [cand], "general", RUN)


async def _claim(cid: str) -> bool:
    """An export taking the clip. Since R4b the claim commits only together with
    its job row, so this is the whole submit; True when it was accepted."""
    from models import _uuid
    from services.clipper.clip_mutations import submit_export
    async with async_session() as s:
        got = await submit_export(s, cid, job_id=_uuid(), origin="manual")
    return got["outcome"] == "queued"


async def _assert_contract(kind: str, cid: str, resp) -> None:
    clip, events = await _state(cid)
    applied, wanted = EDITS[kind][2], EDITS[kind][3]
    if resp.status_code == 200:
        assert clip is not None, f"{kind}: a 200 edit is gone from the board"
        assert applied(clip), f"{kind}: a 200 edit is not on the row"
        assert (wanted, "manual") in events, f"{kind}: a 200 edit has no protecting event"
    else:
        assert resp.status_code in (404, 409), f"{kind}: {resp.status_code} {resp.text}"
        assert events == [], f"{kind}: a refused edit left events {events}"
        assert clip is None or not applied(clip), f"{kind}: a refused edit is on the row"


# ---------------------------------------------------------------------------
# edit -> event: a rescore in the gap between the edit's commit and its event
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
async def test_a_rescore_in_the_event_window_keeps_the_edit(api, tmp_path, kind):
    pid, cid, send = await _setup(tmp_path, kind)
    resp, _ = await interleave(send(api), Gate(EVENT_INSERT), lambda: _rescore(pid))
    await _assert_contract(kind, cid, resp)


# ---------------------------------------------------------------------------
# rescore between the edit's read and its save, in both orders
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
async def test_a_rescore_that_deletes_first_gets_an_explicit_refusal(api, tmp_path, kind):
    pid, cid, send = await _setup(tmp_path, kind)
    gate = Gate(FIRST_WRITE)
    resp, _ = await interleave(send(api), gate, lambda: _rescore(pid))
    assert gate.window, "the edit wrote before the barrier: the race was not forced"
    assert resp.status_code in (404, 409), f"{resp.status_code} {resp.text}"
    await _assert_contract(kind, cid, resp)


@pytest.mark.parametrize("kind", KINDS)
async def test_an_edit_that_lands_before_the_delete_is_kept(api, tmp_path, kind):
    pid, cid, send = await _setup(tmp_path, kind)
    gate = Gate(CLIP_DELETE)
    _, resp = await interleave(_rescore(pid), gate, lambda: send(api))
    assert gate.window, "the rescore deleted before the barrier: the race was not forced"
    assert resp.status_code == 200, resp.text
    await _assert_contract(kind, cid, resp)


# ---------------------------------------------------------------------------
# claim vs edit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["candidate", "exported"])
@pytest.mark.parametrize("kind", KINDS)
async def test_a_claim_between_read_and_write_wins(api, tmp_path, kind, status):
    """The export took the clip first: the edit loses with a 409, and nothing it
    carried — least of all a status — reaches the row the render reads."""
    _pid, cid, send = await _setup(tmp_path, kind, status)
    gate = Gate(FIRST_WRITE)
    resp, claimed = await interleave(send(api), gate, lambda: _claim(cid))
    assert gate.window and claimed is True
    assert resp.status_code == 409, f"{resp.status_code} {resp.text}"
    clip, events = await _state(cid)
    assert clip.status == "exporting", f"a stale write moved the clip to {clip.status}"
    assert not EDITS[kind][2](clip) and events == []


# Not `approve`: its whole effect is the status, which the claim replaces by design.
@pytest.mark.parametrize("kind", [k for k in KINDS if k != "approve"])
async def test_an_edit_before_the_claim_is_what_the_export_reads(api, tmp_path, kind):
    _pid, cid, send = await _setup(tmp_path, kind)
    assert (await send(api)).status_code == 200
    assert await _claim(cid) is True
    clip, _ = await _state(cid)
    assert clip.status == "exporting" and EDITS[kind][2](clip)


# ---------------------------------------------------------------------------
# an active export and a rescore
# ---------------------------------------------------------------------------

async def test_an_exporting_clip_without_feedback_survives_a_rescore(tmp_path):
    pid, cid, _ = await _setup(tmp_path, "patch")
    assert await _claim(cid) is True
    await _rescore(pid)
    clip, events = await _state(cid)
    assert clip is not None and clip.status == "exporting" and events == []
    assert clip.selection_run_id == "oldrun000001"


async def test_a_claim_that_lands_before_the_delete_survives_it(tmp_path):
    pid, cid, _ = await _setup(tmp_path, "patch")
    gate = Gate(CLIP_DELETE)
    _, claimed = await interleave(_rescore(pid), gate, lambda: _claim(cid))
    assert gate.window and claimed is True
    clip, _ = await _state(cid)
    assert clip is not None and clip.status == "exporting"


async def test_an_unattended_render_is_an_active_export_a_rescore_keeps(tmp_path):
    """`_auto_export` used to queue a render and leave the row `candidate`, so the
    next rescore deleted the clip its own export job was rendering."""
    from models import JobModel
    from workers.clipper_finalize import _auto_export

    pid, cid, _ = await _setup(tmp_path, "patch")
    # The job row is written with the claim (R4b), not through `queue.enqueue`.
    assert await _auto_export(pid, {"auto_export": 1}, None) == 1
    async with async_session() as s:
        assert (await s.execute(select(JobModel.clip_id)
                                .where(JobModel.project_id == pid))).scalars().all() == [cid]
    await _rescore(pid)
    clip, _ = await _state(cid)
    assert clip is not None and clip.status == "exporting"


# ---------------------------------------------------------------------------
# the event fails: the edit must not persist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
async def test_a_failed_event_leaves_neither_edit_nor_event(api, tmp_path, kind):
    _pid, cid, send = await _setup(tmp_path, kind)
    before, _ = await _state(cid)

    def refuse(conn, cursor, statement, *a):
        if statement.lstrip().upper().startswith("INSERT INTO CLIP_FEEDBACK"):
            raise RuntimeError("feedback insert refused by the test")

    event.listen(engine.sync_engine, "before_cursor_execute", refuse)
    try:
        resp = await send(api)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", refuse)
    assert resp.status_code >= 500, f"{kind}: {resp.status_code} with no event written"
    clip, events = await _state(cid)
    assert events == [] and not EDITS[kind][2](clip)
    assert (clip.status, clip.layout_plan, clip.caption_plan) == (
        before.status, before.layout_plan, before.caption_plan)


# ---------------------------------------------------------------------------
# no-op: no event, no invalidation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["patch", "caption_source", "reaction_put",
                                  "regen_captions"])
async def test_a_repeat_is_a_no_op(api, tmp_path, kind):
    _pid, cid, send = await _setup(tmp_path, kind)
    assert (await send(api)).status_code == 200
    first, events = await _state(cid)
    assert events == [(EDITS[kind][3], "manual")]
    async with async_session() as s:          # a render made after the edit
        row = await s.get(ClipModel, cid)
        row.export_path = "/tmp/after-edit.mp4"
        await s.commit()
    assert (await send(api)).status_code == 200
    again, events_again = await _state(cid)
    assert events_again == events
    assert again.export_path == "/tmp/after-edit.mp4"


async def test_deleting_a_reaction_that_is_not_there_is_a_no_op(api, tmp_path):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    r = await api.delete(f"/api/clipper/clips/{cid}/reaction-layout")
    assert r.status_code == 200
    assert (await _state(cid))[1] == []


# ---------------------------------------------------------------------------
# regenerate headline: slow model call, then a write on a snapshot
# ---------------------------------------------------------------------------

@pytest.fixture
def headline_barrier(monkeypatch):
    import services.clipper.headline as headline
    gate = {"reached": asyncio.Event(), "release": asyncio.Event(), "text": "new words"}

    async def fake(cand, **_kw):
        gate["reached"].set()
        await gate["release"].wait()
        return {"text": gate["text"], "source": "test"}

    monkeypatch.setattr(headline, "generate_headline", fake)
    return gate


async def test_a_regenerated_headline_is_protected_and_a_repeat_is_not_an_event(
        api, tmp_path, headline_barrier):
    headline_barrier["release"].set()
    _pid, cid, _ = await _setup(tmp_path, "patch")
    url = f"/api/clipper/clips/{cid}/regenerate"
    assert (await api.post(url, json={"what": "headline"})).status_code == 200
    clip, events = await _state(cid)
    assert clip.headline_text == "new words" and events == [("headline_changed", "manual")]
    assert (await api.post(url, json={"what": "headline"})).status_code == 200
    assert (await _state(cid))[1] == events


async def test_a_headline_for_a_window_that_moved_meanwhile_is_refused(
        api, tmp_path, headline_barrier):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    task = asyncio.ensure_future(api.post(f"/api/clipper/clips/{cid}/regenerate",
                                          json={"what": "headline"}))
    await headline_barrier["reached"].wait()
    moved = await api.patch(f"/api/clipper/clips/{cid}", json={"start_time": 2.0})
    assert moved.status_code == 200
    headline_barrier["release"].set()
    resp = await task
    assert resp.status_code == 409, resp.text
    clip, events = await _state(cid)
    assert clip.headline_text is None and clip.start_time == 2.0
    assert events == [("start_changed", "manual")]
