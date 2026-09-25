"""What may change a clip once its export was submitted (R4b, `export_job_id`).

Contract: codex-verdict-wave2.md §R4b (a)/(b). A clip is touched by a job only
when the job is a `clipper_export`, its own transition won, and it is the
clip's CURRENT attempt (`clips.export_job_id`). That covers the render's own
success path, fail, cancel and startup recovery. A preview never moves the
clip. The column does not stop an FFmpeg that is already running, so the old
attempt's file, sidecar and row are checked against the new attempt's — the
publish is refused, not merely the UPDATE.

Historical orphans (claimed before R4b, no job) are released only by a person:
server/scripts/release_stuck_exports.py, tested at the end.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select, update

from database import async_session, engine
from job_queue import JobQueue
from models import ClipModel, JobModel, ProjectModel
from test_clipper_export_atomic_r import _export, _jobs
from test_clipper_mutation_atomicity import Gate, _setup, _state, api, interleave  # noqa: F401

_SERVER = Path(__file__).resolve().parents[1]


def _queue() -> JobQueue:
    q = JobQueue()
    q._cleanup_workspace = AsyncMock()
    return q


async def _take(queue: JobQueue, job_id: str) -> None:
    async with async_session() as s:
        assert await queue._claim(s, job_id)


async def _set_job(job_id: str, **values) -> None:
    async with async_session() as s:
        await s.execute(update(JobModel).where(JobModel.id == job_id).values(**values))
        await s.commit()


async def _set_clip(cid: str, **values) -> None:
    async with async_session() as s:
        await s.execute(update(ClipModel).where(ClipModel.id == cid).values(**values))
        await s.commit()


async def _job(job_id: str) -> JobModel:
    async with async_session() as s:
        return await s.get(JobModel, job_id)


async def _submitted(api, tmp_path, status="candidate"):
    pid, cid, _ = await _setup(tmp_path, "patch", status=status)
    resp = await _export(api, cid)
    assert resp.status_code == 200, resp.text
    return pid, cid, resp.json()["job_id"]


async def _superseded(api, tmp_path):
    """A finished, then B submitted and current; A's row forced back to
    `running` — a state only a pre-R4b writer or a lost race leaves behind."""
    pid, cid, a = await _submitted(api, tmp_path)
    await _set_job(a, status="done")
    await _set_clip(cid, status="exported", export_path="/tmp/a.mp4")
    b = (await _export(api, cid)).json()["job_id"]
    return pid, cid, a, b


# ---------------------------------------------------------------------------
# fail / cancel
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("how", ["fail_running", "fail_queued", "cancel_running",
                                 "cancel_queued"])
async def test_the_current_export_ending_frees_its_clip_and_keeps_its_history(
        api, tmp_path, how):
    _pid, cid, job = await _submitted(api, tmp_path)
    q = _queue()
    owner = None
    if how.endswith("running"):
        await _take(q, job)
        owner = q.worker_id
    if how.startswith("fail"):
        await q.fail_job(job, "render crashed", owner_id=owner)
    else:
        await q.cancel_job(job, owner_id=owner)
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("failed", job)
    again = await _export(api, cid)
    assert again.status_code == 200, again.text
    ended = "failed" if how.startswith("fail") else "cancelled"
    assert sorted(s for _i, s, _t in await _jobs(cid)) == sorted([ended, "queued"])
    assert (await _state(cid))[0].export_job_id == again.json()["job_id"]


@pytest.mark.parametrize("how", ["fail", "cancel"])
async def test_an_export_job_that_is_not_the_current_attempt_leaves_the_clip(
        api, tmp_path, how):
    _pid, cid, a, b = await _superseded(api, tmp_path)
    q = _queue()
    await _set_job(a, status="running", worker_id=q.worker_id,
                   lease_expires_at=datetime.utcnow() + timedelta(seconds=120))
    if how == "fail":
        await q.fail_job(a, "old render crashed", owner_id=q.worker_id)
    else:
        await q.cancel_job(a, owner_id=q.worker_id)
    assert (await _job(a)).status == ("failed" if how == "fail" else "cancelled")
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("exporting", b)
    assert (await _job(b)).status == "queued"


# ---------------------------------------------------------------------------
# startup recovery
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("branch", ["ownerless", "terminal_project", "cap"])
async def test_recovery_frees_the_clip_of_its_own_failed_export(api, tmp_path, branch):
    pid, cid, job = await _submitted(api, tmp_path)
    recovery = _queue()
    if branch == "ownerless":
        await _set_job(job, status="running", worker_id=None, lease_expires_at=None)
    else:
        await _take(_queue(), job)
        await _set_job(job, lease_expires_at=datetime.utcnow() - timedelta(seconds=1))
        if branch == "terminal_project":
            async with async_session() as s:
                await s.execute(update(ProjectModel).where(ProjectModel.id == pid)
                                .values(status="failed"))
                await s.commit()
        else:
            recovery.MAX_RECOVER = 0
    await recovery.recover_stuck_jobs()
    assert (await _job(job)).status == "failed"
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("failed", job)
    assert (await _export(api, cid)).status_code == 200


async def test_a_requeued_export_keeps_its_claim(api, tmp_path):
    _pid, cid, job = await _submitted(api, tmp_path)
    await _take(_queue(), job)
    await _set_job(job, lease_expires_at=datetime.utcnow() - timedelta(seconds=1))
    await _queue().recover_stuck_jobs()
    assert (await _job(job)).status == "queued"
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("exporting", job)


async def test_recovering_an_old_export_job_leaves_a_newer_attempt_alone(api, tmp_path):
    _pid, cid, a, b = await _superseded(api, tmp_path)
    await _set_job(a, status="running", worker_id=None, lease_expires_at=None)
    await _queue().recover_stuck_jobs()
    assert (await _job(a)).status == "failed"
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("exporting", b)


async def test_a_lost_recovery_race_does_not_touch_the_clip(api, tmp_path):
    """Recovery read the job as running; another backend finished it before
    recovery's UPDATE. rowcount 0: neither the job nor its clip moves."""
    _pid, cid, job = await _submitted(api, tmp_path)
    await _set_job(job, status="running", worker_id=None, lease_expires_at=None)
    gate = Gate(lambda s: s.startswith("UPDATE JOBS"))
    await interleave(_queue().recover_stuck_jobs(), gate,
                     lambda: _set_job(job, status="done"))
    assert gate.window
    assert (await _job(job)).status == "done"
    assert (await _state(cid))[0].status == "exporting"


# ---------------------------------------------------------------------------
# previews never move the clip
# ---------------------------------------------------------------------------

async def _preview(api, cid) -> str:
    r = await api.post(f"/api/clipper/clips/{cid}/regenerate", json={"what": "preview"})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


async def _end(job: str, how: str) -> None:
    q = _queue()
    owner = None
    if how != "cancel_queued":
        await _take(q, job)
        owner = q.worker_id
    if how == "fail":
        await q.fail_job(job, "preview crashed", owner_id=owner)
    else:
        await q.cancel_job(job, owner_id=owner)


@pytest.mark.parametrize("status", ["approved", "exported"])
@pytest.mark.parametrize("how", ["fail", "cancel_running", "cancel_queued"])
async def test_a_failed_or_cancelled_preview_leaves_the_clip_and_its_export(
        api, tmp_path, status, how):
    _pid, cid, _ = await _setup(tmp_path, "patch", status=status)
    before, _ = await _state(cid)
    job = await _preview(api, cid)
    await _end(job, how)
    ended = await _job(job)
    assert ended.status == ("failed" if how == "fail" else "cancelled")
    if how == "fail":
        assert ended.error == "preview crashed"
    row, _ = await _state(cid)
    assert (row.status, row.export_path) == (before.status, before.export_path)


@pytest.mark.parametrize("how", ["fail", "cancel_running", "cancel_queued"])
async def test_a_preview_ending_beside_an_active_export_leaves_the_export(api, tmp_path, how):
    _pid, cid, export_job = await _submitted(api, tmp_path)
    await _end(await _preview(api, cid), how)
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("exporting", export_job)
    assert (await _job(export_job)).status == "queued"


# ---------------------------------------------------------------------------
# a render that finishes: only the current, owned attempt publishes
# ---------------------------------------------------------------------------

_LABEL = contextvars.ContextVar("render_label")


class _Renders:
    """`render_export` stand-in: one gate per label, distinct bytes per label."""

    def __init__(self, exports: Path):
        self.exports, self.gates, self.reached, self.fail = exports, {}, {}, set()

    def gate(self, label):
        self.gates[label], self.reached[label] = asyncio.Event(), asyncio.Event()

    async def __call__(self, clip, project, decision, out, *, src, **_):
        label = _LABEL.get()
        if label in self.gates:
            self.reached[label].set()
            await self.gates[label].wait()
        if label in self.fail:
            raise RuntimeError(f"ffmpeg failed for {label}")
        out = Path(out)
        out.write_bytes(f"render of {label}\n".encode() * 200)
        out.with_suffix(".json").write_text(json.dumps({"attempt": label}), encoding="utf-8")
        return {"size": out.stat().st_size, "sidecar": {"review": {"attempt": label}}}


@pytest.fixture
def renders(monkeypatch, tmp_path):
    from workers import clipper_render_jobs as jobs
    from workers import clipper_render_output as output

    exports = tmp_path / "exports"
    exports.mkdir()
    fake = _Renders(exports)

    async def decide(*_a, **_k):
        return {"cfg": {}, "dyn": None, "caption_y": None}

    monkeypatch.setattr(jobs, "_decide_render", decide)
    monkeypatch.setattr(jobs, "_source_path", lambda p: p.video_path)
    monkeypatch.setattr(jobs.storage, "paths", lambda _p: {"exports_dir": exports})
    monkeypatch.setattr(jobs.storage, "export_path", lambda _p, cid: exports / f"{cid}.mp4")
    monkeypatch.setattr(output, "render_export", fake)
    return fake


def _run(label, job_id, pid, cid, queue):
    from workers.clipper_render_jobs import handle_export

    async def run():
        _LABEL.set(label)
        await handle_export(job_id, pid, cid, {"origin": "manual"}, queue)
    return asyncio.ensure_future(run())


async def _published(renders, cid):
    row, _ = await _state(cid)
    final = renders.exports / f"{cid}.mp4"
    return {"status": row.status, "row_path": row.export_path, "review": row.review,
            "file": final.read_bytes().split(b"\n")[0].decode() if final.exists() else None,
            "sidecar": json.loads(final.with_suffix(".json").read_text(encoding="utf-8"))
            if final.with_suffix(".json").exists() else None,
            "dir": sorted(p.name for p in renders.exports.iterdir())}


@pytest.mark.parametrize("order", ["old_finishes_last", "old_finishes_first"])
async def test_an_old_attempt_cannot_publish_over_the_new_one(api, tmp_path, renders, order):
    """A is cancelled from the other backend while its encode runs — the task is
    not in that process, so FFmpeg goes on. B is submitted and rendered. A's
    finished encode must not reach the file, the sidecar or the row."""
    pid, cid, a = await _submitted(api, tmp_path)
    qa = _queue()
    await _take(qa, a)
    renders.gate("A")
    task_a = _run("A", a, pid, cid, qa)
    await asyncio.wait_for(renders.reached["A"].wait(), 10)
    await _queue().cancel_job(a)
    b = (await _export(api, cid)).json()["job_id"]
    qb = _queue()
    await _take(qb, b)
    if order == "old_finishes_first":
        renders.gates["A"].set()
        with pytest.raises(RuntimeError, match="current export"):
            await task_a
        await _run("B", b, pid, cid, qb)
    else:
        await _run("B", b, pid, cid, qb)
        renders.gates["A"].set()
        with pytest.raises(RuntimeError, match="current export"):
            await task_a
    assert await _published(renders, cid) == {
        "status": "exported", "row_path": str(renders.exports / f"{cid}.mp4"),
        "review": {"attempt": "B"}, "file": "render of B", "sidecar": {"attempt": "B"},
        "dir": [f"{cid}.json", f"{cid}.mp4"]}
    assert (await _state(cid))[0].export_job_id == b


async def test_a_stale_owner_cannot_publish(api, tmp_path, renders):
    """Recovery requeued A (expired lease) and another worker took it: same job
    id, new owner. The stale worker's encode finishes last and is discarded."""
    pid, cid, a = await _submitted(api, tmp_path)
    stale = _queue()
    await _take(stale, a)
    renders.gate("stale")
    task_stale = _run("stale", a, pid, cid, stale)
    await asyncio.wait_for(renders.reached["stale"].wait(), 10)
    await _set_job(a, lease_expires_at=datetime.utcnow() - timedelta(seconds=1))
    await _queue().recover_stuck_jobs()
    fresh = _queue()
    await _take(fresh, a)
    await _run("fresh", a, pid, cid, fresh)
    renders.gates["stale"].set()
    with pytest.raises(RuntimeError, match="current export"):
        await task_stale
    got = await _published(renders, cid)
    assert (got["file"], got["sidecar"], got["review"]) == (
        "render of fresh", {"attempt": "fresh"}, {"attempt": "fresh"})
    assert got["dir"] == [f"{cid}.json", f"{cid}.mp4"]


async def test_a_superseded_render_failure_leaves_the_current_attempt(api, tmp_path, renders):
    pid, cid, a = await _submitted(api, tmp_path)
    qa = _queue()
    await _take(qa, a)
    renders.gate("A")
    renders.fail.add("A")
    task_a = _run("A", a, pid, cid, qa)
    await asyncio.wait_for(renders.reached["A"].wait(), 10)
    await _queue().cancel_job(a)
    b = (await _export(api, cid)).json()["job_id"]
    renders.gates["A"].set()
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        await task_a
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("exporting", b)


# ---------------------------------------------------------------------------
# historical orphans: an explicit, person-run release
# ---------------------------------------------------------------------------

def _script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "release_stuck_exports", _SERVER / "scripts" / "release_stuck_exports.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _orphan(tmp_path) -> str:
    _pid, cid, _ = await _setup(tmp_path, "patch")
    await _set_clip(cid, status="exporting")
    return cid


async def _no_live_export_leases() -> None:
    """The suite shares one DB; a lease another test left live would (rightly)
    make the script refuse, so the positive case clears them first."""
    async with async_session() as s:
        await s.execute(update(JobModel).where(JobModel.status == "running",
                                               JobModel.type == "clipper_export")
                        .values(lease_expires_at=datetime.utcnow() - timedelta(seconds=1)))
        await s.commit()


async def test_the_release_script_lists_orphans_with_the_denominator_first(api, tmp_path, capsys):
    orphan = await _orphan(tmp_path)
    _pid, live, _job_id = await _submitted(api, tmp_path)
    assert await _script().run([]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("exporting clips: ")
    assert any(orphan in line for line in lines) and not any(live in line for line in lines)
    assert (await _state(orphan))[0].status == "exporting"


@pytest.mark.parametrize("why", ["no_attestation", "live_lease"])
async def test_the_release_script_refuses_the_whole_run(api, tmp_path, capsys, why):
    orphan = await _orphan(tmp_path)
    argv = ["--release", orphan]
    if why == "live_lease":
        _pid, _cid, job = await _submitted(api, tmp_path)
        await _take(_queue(), job)
        argv.append("--writers-stopped")
    assert await _script().run(argv) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert (await _state(orphan))[0].status == "exporting"


async def test_the_release_script_releases_only_named_orphans(api, tmp_path, capsys):
    named, other = await _orphan(tmp_path), await _orphan(tmp_path)
    _pid, live, _job_id = await _submitted(api, tmp_path)
    await _no_live_export_leases()
    code = await _script().run(["--release", named, live, "no-such-clip",
                                "--writers-stopped"])
    out = capsys.readouterr().out
    assert code == 1, out
    assert out.splitlines()[0] == "named: 3"
    assert f"{named}\treleased" in out
    assert f"{live}\trefused" in out and "no-such-clip\trefused" in out
    assert [(await _state(c))[0].status for c in (named, other, live)] == [
        "failed", "exporting", "exporting"]
