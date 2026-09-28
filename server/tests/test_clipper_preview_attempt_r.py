"""BURST R1 (codex-verdict-next-23 §1, next-24 §3): a preview's caption report is the SELECTED ATTEMPT's.

The render is published to an immutable per-attempt file and one transaction selects it —
`preview_path` AND `preview_record` ({job_id, attempt, worker, caption_plan_state}) together. The attempt
is captured when the handler starts; at publication the job row must still be that attempt's (running,
same worker and attempt, a live lease), and an attempt older than the selected one never replaces it.
A commit that fails, or a step that raises before it, leaves the previous file AND report selected.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from models import ClipModel, JobModel, ProjectModel
from services.clipper import storage
from test_clipper_caption_display_api import WELL_TIMED, _built, _card, _render, _seed
from workers import clipper_preview_publish as pub
from workers import clipper_render_jobs as jobs

T0 = dt.datetime(2026, 9, 27, 12, 0, 0)


def A(job, attempt=1, worker="w1"):
    return {"job_id": job, "attempt": attempt, "worker": worker}


async def _job(ident, cid, name, minutes, *, attempt=1, worker="w1", status="running",
               lease_minutes=10):
    async with async_session() as session:
        session.add(JobModel(id=name, project_id=ident, clip_id=cid, type="clipper_preview",
                             status=status, metadata_json="{}", worker_id=worker,
                             attempt_count=attempt,
                             lease_expires_at=dt.datetime.utcnow() + dt.timedelta(minutes=lease_minutes),
                             created_at=T0 + dt.timedelta(minutes=minutes)))
        await session.commit()


async def _set_job(name, **values):
    async with async_session() as session:
        job = await session.get(JobModel, name)
        for k, v in values.items():
            setattr(job, k, v)
        await session.commit()


async def _publish(ident, cid, att, state, tmp_path, body=b"mp4"):
    """Publish as attempt `att`; returns (cause, the attempt's own published file)."""
    async with async_session() as session:
        clip = await session.get(ClipModel, cid)
        project = await session.get(ProjectModel, ident)
        seen = jobs._preview_inputs(clip, project)
    out = pub.attempt_path(storage.preview_path(ident, cid), att)
    tmp = tmp_path / f"{att['job_id']}-{att['attempt']}.render.mp4"
    tmp.write_bytes(body)
    out.parent.mkdir(parents=True, exist_ok=True)
    return await pub._publish_preview(clip, seen, tmp, out, state, att), out


async def _two(ident):
    plan = _built(WELL_TIMED)
    cid = await _seed(ident, plan, status="approved", exported=False)
    await _job(ident, cid, f"{ident}o", 0)
    await _job(ident, cid, f"{ident}n", 5)
    clean, bad = _render(plan), _render(plan)
    bad["display"] = {**bad["display"], "ass_agrees": False}
    return cid, clean, bad


async def _selected(cid):
    async with async_session() as session:
        row = await session.get(ClipModel, cid)
        return row.preview_path, row.preview_record


async def _both(client, ident, cid):
    one = await client.get(f"/api/clipper/clips/{cid}")
    assert one.status_code == 200, one.text
    view = await _card(client, ident)
    assert one.json()["caption_display"] == view
    return view


# ── ordering between attempts ──────────────────────────────────────────────────

async def test_an_older_job_finishing_last_is_not_selected(client, tmp_path):
    cid, clean, bad = await _two("pa1")
    assert (await _publish("pa1", cid, A("pa1n"), clean, tmp_path, b"new"))[0] is None
    cause, old_file = await _publish("pa1", cid, A("pa1o"), bad, tmp_path, b"old")
    assert cause == "newer_preview" and not old_file.exists()
    path, rec = await _selected(cid)
    assert (rec["job_id"], rec["attempt"]) == ("pa1n", 1)
    assert open(path, "rb").read() == b"new"
    pv = (await _both(client, "pa1", cid))["preview"]
    assert (pv["state"], pv["identity"]) == ("verified", {"job_id": "pa1n", "attempt": 1})
    async with async_session() as session:
        meta = json.loads((await session.get(JobModel, "pa1o")).metadata_json)
    assert meta["discarded"] == "newer_preview"


async def test_the_newer_job_finishing_last_is_selected(client, tmp_path):
    cid, clean, bad = await _two("pa2")
    assert (await _publish("pa2", cid, A("pa2o"), bad, tmp_path, b"old"))[0] is None
    assert (await _publish("pa2", cid, A("pa2n"), clean, tmp_path, b"new"))[0] is None
    path, rec = await _selected(cid)
    assert rec["job_id"] == "pa2n" and open(path, "rb").read() == b"new"
    assert (await _both(client, "pa2", cid))["preview"]["state"] == "verified"


async def test_a_newer_job_that_never_published_leaves_the_file_to_its_publisher(client, tmp_path):
    cid, clean, bad = await _two("pa3")
    assert (await _publish("pa3", cid, A("pa3o"), bad, tmp_path))[0] is None
    await _set_job("pa3n", status="failed")
    view = await _both(client, "pa3", cid)
    assert (view["preview"]["state"], view["preview"]["identity"]) == (
        "unverified", {"job_id": "pa3o", "attempt": 1})


async def test_an_attempt_that_published_then_failed_still_describes_its_file(client, tmp_path):
    cid, clean, bad = await _two("pa4")
    assert (await _publish("pa4", cid, A("pa4n"), clean, tmp_path))[0] is None
    await _set_job("pa4n", status="failed")
    assert (await _both(client, "pa4", cid))["preview"]["state"] == "verified"


# ── the attempt, not the job id ────────────────────────────────────────────────

async def test_attempt_two_published_then_a_late_attempt_one_of_the_same_job_is_refused(client, tmp_path):
    cid, clean, bad = await _two("pa5")
    # recovery gave the job to another worker as attempt 2; it published
    await _set_job("pa5n", attempt_count=2, worker_id="w2")
    assert (await _publish("pa5", cid, A("pa5n", 2, "w2"), clean, tmp_path, b"a2"))[0] is None
    # attempt 1 of the SAME job finishes late
    cause, late = await _publish("pa5", cid, A("pa5n", 1, "w1"), bad, tmp_path, b"a1")
    assert cause == pub.LOST_ATTEMPT and not late.exists()
    path, rec = await _selected(cid)
    assert (rec["attempt"], open(path, "rb").read()) == (2, b"a2")
    assert (await _both(client, "pa5", cid))["preview"]["identity"] == {"job_id": "pa5n", "attempt": 2}


async def test_a_retry_on_the_same_worker_is_told_apart_by_its_attempt(client, tmp_path):
    # the same worker re-ran the job as attempt 2 (next-24 §3): only the attempt tells them apart
    cid, clean, bad = await _two("pa5b")
    await _set_job("pa5bn", attempt_count=2)
    assert (await _publish("pa5b", cid, A("pa5bn", 2, "w1"), clean, tmp_path, b"a2"))[0] is None
    cause, late = await _publish("pa5b", cid, A("pa5bn", 1, "w1"), bad, tmp_path, b"a1")
    assert cause == pub.LOST_ATTEMPT and not late.exists()
    path, rec = await _selected(cid)
    assert (rec["attempt"], open(path, "rb").read()) == (2, b"a2")


@pytest.mark.parametrize("how", ["other_worker", "cancelled", "lease_expired", "no_row"])
async def test_an_attempt_that_no_longer_owns_its_job_selects_nothing(client, tmp_path, how):
    ident = f"pa6{how[:6]}"
    cid, clean, bad = await _two(ident)
    job = f"{ident}n"
    if how == "other_worker":
        await _set_job(job, worker_id="someone-else")
    elif how == "cancelled":
        await _set_job(job, status="cancelled")
    elif how == "lease_expired":
        await _set_job(job, lease_expires_at=dt.datetime.utcnow() - dt.timedelta(seconds=1))
    else:
        job = f"{ident}ghost"
    cause, out = await _publish(ident, cid, A(job), clean, tmp_path)
    assert cause == pub.LOST_ATTEMPT and not out.exists()
    assert await _selected(cid) == (None, None)
    if how != "no_row":            # the row is another attempt's: no discard marker written on it
        async with async_session() as session:
            assert json.loads((await session.get(JobModel, job)).metadata_json) == {}


# ── publication that fails half-way ────────────────────────────────────────────

@pytest.mark.parametrize("where", ["before_commit", "at_commit"])
async def test_a_publication_that_fails_keeps_the_previous_file_and_report(client, tmp_path,
                                                                        monkeypatch, where):
    ident = f"pa7{where[:6]}"
    cid, clean, bad = await _two(ident)
    assert (await _publish(ident, cid, A(f"{ident}o"), clean, tmp_path, b"old"))[0] is None
    before = await _selected(cid)
    if where == "before_commit":       # the file is in place, then a step raises
        def boom(*_a, **_k):
            raise RuntimeError("simulated failure after the rename")
        monkeypatch.setattr(pub, "_caption_warnings", boom)
    else:                              # the commit itself fails
        real = AsyncSession.commit
        armed = {"on": True}

        async def failing_commit(self):
            if armed["on"]:
                armed["on"] = False
                raise RuntimeError("simulated commit failure")
            return await real(self)
        monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(RuntimeError, match="simulated"):
        await _publish(ident, cid, A(f"{ident}n"), bad, tmp_path, b"new")
    monkeypatch.undo()
    assert await _selected(cid) == before                    # still the old file AND its report
    assert open(before[0], "rb").read() == b"old"
    new_file = pub.attempt_path(storage.preview_path(ident, cid), A(f"{ident}n"))
    assert str(new_file) != before[0]                        # the new render is only unselected
    view = await _both(client, ident, cid)
    assert (view["preview"]["state"], view["preview"]["identity"]) == (
        "verified", {"job_id": f"{ident}o", "attempt": 1})


async def test_the_handler_removes_its_unselected_file_and_never_the_selected_one(tmp_path, monkeypatch):
    """Through the REAL `handle_preview` (render stubbed): a step fails after this attempt's file was
    renamed into place and before the commit. Its `finally` must delete THAT file — it is not the
    selected one — and leave the selected file and record untouched."""
    import services.clipper.render as static_render

    ident = "pa8"
    cid, clean, bad = await _two(ident)
    assert (await _publish(ident, cid, A(f"{ident}o"), clean, tmp_path, b"old"))[0] is None
    before = await _selected(cid)

    class _Queue:
        worker_id = "w1"

        async def update_progress(self, *_):
            pass

        def is_cancelled(self, _):
            return False

    async def render(_src, _cand, _plan, _ass, out, **_kw):
        Path(out).write_bytes(b"new")

    def boom(*_a, **_k):
        raise RuntimeError("simulated failure after the rename")

    monkeypatch.setattr(static_render, "render_preview", render)
    monkeypatch.setattr(jobs, "_source_path", lambda _: "unused.mp4")
    monkeypatch.setattr(pub, "_caption_warnings", boom)
    CLAIMED_ATTEMPT.set(ClaimedAttempt(f"{ident}n", 1, "w1"))    # as the queue's claim leaves it (R1c)
    with pytest.raises(RuntimeError, match="simulated"):
        await jobs.handle_preview(f"{ident}n", ident, cid, {}, _Queue())
    mine = pub.attempt_path(storage.preview_path(ident, cid), A(f"{ident}n"))
    assert not mine.exists(), "the unselected per-attempt file was left behind"
    assert await _selected(cid) == before and open(before[0], "rb").read() == b"old"


# ── legacy and invalidation ────────────────────────────────────────────────────

async def test_a_legacy_preview_with_no_record_is_unknown_never_verified(client):
    plan = _built(WELL_TIMED)
    cid = await _seed("pa9", plan, status="approved", exported=False, preview=True)
    async with async_session() as session:
        session.add(JobModel(id="pa9p", project_id="pa9", clip_id=cid, type="clipper_preview",
                             status="done", metadata_json=json.dumps(
                                 {"caption_plan_state": _render(plan)})))
        await session.commit()
    view = await _both(client, "pa9", cid)
    assert (view["preview"]["state"], view["preview"]["identity"]) == ("not_measured", {"job_id": None})
    assert view["level"] == "unknown"


async def test_an_edit_makes_the_selected_preview_stale(client, tmp_path):
    cid, clean, bad = await _two("pa10")
    assert (await _publish("pa10", cid, A("pa10n"), clean, tmp_path))[0] is None
    r = await client.patch(f"/api/clipper/clips/{cid}", json={"start_time": 100.5})
    assert r.status_code == 200, r.text
    pv = (await _both(client, "pa10", cid))["preview"]
    assert (pv["state"], pv["found_state"], pv["stale_reason"]) == ("stale", "verified",
                                                                    "preview_cleared")


def test_a_record_without_a_report_is_never_read_as_one():
    from services.clipper.serialize import caption_display_view

    clip = ClipModel(id="pa11c", project_id="pa11", status="approved", preview_path="p.mp4",
                     preview_record={"job_id": "j", "attempt": 1}, caption_plan=None)
    pv = caption_display_view(clip)["preview"]
    assert (pv["state"], pv["identity"]) == ("not_measured", {"job_id": None})
