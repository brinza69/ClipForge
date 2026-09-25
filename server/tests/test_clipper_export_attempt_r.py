"""R4b conditions from the independent review (REVIEW.md F1, F2, F3, F6).

F1: a clip-scoped Clipper job (preview, export) that fails or is cancelled no
longer marks its PROJECT failed/cancelled — recovery reads that project status
as "terminal" and would fail another clip's live export (verdict wave2 (a):
"Un preview eșuat/anulat nu trebuie să schimbe starea exportului clipului").
Pipeline and non-Clipper jobs still mark the project, as before.

F2: every attempt burns ITS OWN `.ass` (decided into a per-attempt directory),
and `exports/{clip}.ass` changes only when that attempt publishes, under the
same lock and check as the mp4 and the sidecar.

F3/F6: no hidden per-attempt file stays beside the export — on success, on a
refused publish, and after an in-process cancel whose encode thread finishes
later.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from database import async_session
from models import ClipModel, ProjectModel
from services.clipper import render_record
from services.clipper.ffmpeg_tools import escape_filter_path
from test_clipper_export_atomic_r import _export
from test_clipper_export_identity_r import _job, _queue, _set_job, _submitted, _take
from test_clipper_mutation_atomicity import _setup, _state, api  # noqa: F401
from test_clipper_shared_export import _ass, _decision


async def _project(pid: str) -> tuple[str, str | None]:
    async with async_session() as s:
        p = await s.get(ProjectModel, pid)
        return p.status, p.description


# ---------------------------------------------------------------------------
# F1. The project row is not a side channel from a clip-scoped job to recovery.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("preview_end", ["none", "fail", "cancel"])
async def test_p1_a_preview_ending_does_not_change_how_recovery_treats_the_export(
        api, tmp_path, preview_end):
    """The reviewer's P1, unchanged in substance."""
    pid, cid, export_job = await _submitted(api, tmp_path)
    await _take(_queue(), export_job)          # a backend renders it ...
    if preview_end != "none":
        # ... and a preview of ANOTHER clip of the same project ends badly.
        other = cid + "-p"
        async with async_session() as s:
            s.add(ClipModel(id=other, project_id=pid, title="o", start_time=6.0,
                            end_time=9.0, duration=3.0, status="approved"))
            await s.commit()
        r = await api.post(f"/api/clipper/clips/{other}/regenerate", json={"what": "preview"})
        assert r.status_code == 200, r.text
        preview = r.json()["job_id"]
        if preview_end == "fail":
            await _queue().fail_job(preview, "preview crashed")
        else:
            await _queue().cancel_job(preview)
    # ... then that backend dies; the next startup finds the export's lease expired.
    await _set_job(export_job, lease_expires_at=datetime.utcnow() - timedelta(seconds=1))
    await _queue().recover_stuck_jobs()
    job = await _job(export_job)
    row, _ = await _state(cid)
    assert (job.status, row.status, row.export_job_id) == ("queued", "exporting", export_job), (
        f"preview_end={preview_end}: export job {job.status!r} ({job.error!r}), "
        f"clip {row.status!r}")


@pytest.mark.parametrize("how", ["fail", "cancel"])
async def test_an_export_ending_frees_its_clip_and_leaves_the_project(api, tmp_path, how):
    pid, cid, job = await _submitted(api, tmp_path)
    before = await _project(pid)
    q = _queue()
    await _take(q, job)
    if how == "fail":
        await q.fail_job(job, "render crashed", owner_id=q.worker_id)
    else:
        await q.cancel_job(job, owner_id=q.worker_id)
    assert (await _job(job)).status == ("failed" if how == "fail" else "cancelled")
    assert (await _state(cid))[0].status == "failed"      # R4b: the clip IS released
    assert await _project(pid) == before == ("ready", None)


@pytest.mark.parametrize("job_type", ["clipper_score", "clipper_ingest", "doodle_render"])
@pytest.mark.parametrize("how", ["fail", "cancel"])
async def test_a_pipeline_or_other_job_ending_still_marks_the_project(
        api, tmp_path, how, job_type):
    """Guard: only the two clip-scoped Clipper types changed."""
    pid, _cid, _ = await _setup(tmp_path, "patch")
    q = _queue()
    job = await q.enqueue(project_id=pid, job_type=job_type)
    await _take(q, job)
    if how == "fail":
        await q.fail_job(job, "stage crashed", owner_id=q.worker_id)
        assert await _project(pid) == ("failed", f"[{job_type} failed] stage crashed")
    else:
        await q.cancel_job(job, owner_id=q.worker_id)
        assert await _project(pid) == ("cancelled", None)


# ---------------------------------------------------------------------------
# F2. The reviewer's P4: stubs that do with the caption path exactly what the
#     real code does — write it at decide (`_write_ass` makes the directory),
#     read it at encode.
# ---------------------------------------------------------------------------

_LBL = contextvars.ContextVar("lbl")


class _Stubs:
    def __init__(self):
        self.decide_gate, self.decide_reached = {}, {}
        self.render_gate, self.render_reached = {}, {}

    def gate(self, where, label):
        getattr(self, f"{where}_gate")[label] = asyncio.Event()
        getattr(self, f"{where}_reached")[label] = asyncio.Event()

    async def decide(self, clip, project, out_dir, **_):
        label = _LBL.get()
        if label in self.decide_gate:
            self.decide_reached[label].set()
            await self.decide_gate[label].wait()
        Path(out_dir).mkdir(parents=True, exist_ok=True)   # as _write_ass does
        ass = Path(out_dir) / f"{clip.id}.ass"       # clipper_captions._write_ass
        ass.write_text(f"captions of {label}", encoding="utf-8")
        return {"cfg": {}, "dyn": None, "caption_y": None, "ass_path": str(ass)}

    async def render(self, clip, project, decision, out, *, src, **_):
        label = _LBL.get()
        if label in self.render_gate:
            self.render_reached[label].set()
            await self.render_gate[label].wait()
        burned = Path(decision["ass_path"]).read_text(encoding="utf-8")   # ffmpeg reads it
        out = Path(out)
        out.write_bytes(burned.encode())
        out.with_suffix(".json").write_text(json.dumps({"attempt": label}), encoding="utf-8")
        return {"size": out.stat().st_size, "sidecar": {"review": {"attempt": label}}}


def _paths(monkeypatch, tmp_path):
    from workers import clipper_render_jobs as jobs

    exports = tmp_path / "exports"
    exports.mkdir()
    monkeypatch.setattr(jobs, "_source_path", lambda p: p.video_path)
    monkeypatch.setattr(jobs.storage, "paths", lambda _p: {"exports_dir": exports})
    monkeypatch.setattr(jobs.storage, "export_path", lambda _p, c: exports / f"{c}.mp4")
    return jobs, exports


def _run(label, job_id, pid, cid, queue):
    from workers.clipper_render_jobs import handle_export

    async def go():
        _LBL.set(label)
        await handle_export(job_id, pid, cid, {"origin": "manual"}, queue)
    return asyncio.ensure_future(go())


def _hidden(exports: Path) -> list[str]:
    return sorted(p.name for p in exports.iterdir() if p.name.startswith("."))


async def test_p4_a_superseded_attempt_cannot_change_the_captions_the_new_one_burns(
        api, tmp_path, monkeypatch):
    from workers import clipper_render_output as output

    jobs, exports = _paths(monkeypatch, tmp_path)
    st = _Stubs()
    monkeypatch.setattr(jobs, "_decide_render", st.decide)
    monkeypatch.setattr(output, "render_export", st.render)

    pid, cid, a = await _submitted(api, tmp_path)
    qa = _queue()
    await _take(qa, a)
    st.gate("decide", "A")
    task_a = _run("A", a, pid, cid, qa)
    await asyncio.wait_for(st.decide_reached["A"].wait(), 10)
    await _queue().cancel_job(a)                     # from the OTHER backend
    b = (await _export(api, cid)).json()["job_id"]
    qb = _queue()
    await _take(qb, b)
    st.gate("render", "B")
    task_b = _run("B", b, pid, cid, qb)
    await asyncio.wait_for(st.render_reached["B"].wait(), 10)   # B decided: ass = B
    st.decide_gate["A"].set()                        # A's slow decide lands now
    with pytest.raises(RuntimeError, match="current export"):
        await task_a                                 # A's publish IS refused ...
    st.render_gate["B"].set()
    await task_b
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("exported", b)
    burned = (exports / f"{cid}.mp4").read_text(encoding="utf-8")
    assert burned == "captions of B", f"the published current attempt burned: {burned!r}"
    # ... and the file beside the export is the one that was burned.
    assert (exports / f"{cid}.ass").read_text(encoding="utf-8") == "captions of B"
    assert _hidden(exports) == []


# ---------------------------------------------------------------------------
# The REAL `render_export` and sidecar writer; only the encoder is replaced, by
# one that does with its paths what `render_dynamic_clip` does: the sendcmd
# script at work_dir/{out.stem}.cmd.txt, the render_record from an argv whose
# filter names `ass_path`, then the file at `out`. It can be held on a
# threading gate, because the real one runs in `asyncio.to_thread`.
# ---------------------------------------------------------------------------

class _Encoder:
    def __init__(self):
        self.gate: threading.Event | None = None
        self.reached, self.returned = threading.Event(), threading.Event()

    def __call__(self, src, plan, out, *, start, work_dir, ass_path=None, **_):
        cmd_path = Path(work_dir) / f"{Path(out).stem}.cmd.txt"
        cmd_path.write_text("0.0 [v] crop w 100;\n", encoding="utf-8")
        graph = (f"[0:v]null[v];[v]subtitles=filename='{escape_filter_path(ass_path)}'[o]"
                 if ass_path else "[0:v]null[o]")
        record = render_record.record(["ffmpeg", "-i", src, "-filter_complex", graph, out],
                                      ass_path=ass_path)
        self.reached.set()
        if self.gate is not None:
            assert self.gate.wait(10)
        burned = Path(ass_path).read_bytes() if ass_path else b"no captions"
        Path(out).write_bytes(b"mp4 burning " + burned)
        self.returned.set()
        return {"path": out, "size": Path(out).stat().st_size, "sendcmd": cmd_path,
                "render_record": record}


@pytest.fixture
def encoder(monkeypatch, tmp_path):
    from services.clipper import dynamic_render
    from services.clipper import render as static_render

    jobs, exports = _paths(monkeypatch, tmp_path)
    enc = _Encoder()
    enc.exports, enc.burn = exports, True

    async def decide(clip, _project, out_dir, **_):
        ass = Path(out_dir) / f"{clip.id}.ass"
        if enc.burn:
            _ass(ass)                                  # mkdir + a real pysubs2 file
        return _decision(dynamic=True, burn=enc.burn, ass_path=str(ass) if enc.burn else None)

    async def review(*_a, **_k):
        return {"verdict": "UNDECIDED", "findings": []}

    monkeypatch.setattr(jobs, "_decide_render", decide)
    monkeypatch.setattr(jobs, "_review", review)
    monkeypatch.setattr(static_render, "_has_audio", lambda _src: False)
    monkeypatch.setattr(dynamic_render, "render_dynamic_clip", enc)
    return enc


async def test_the_published_captions_are_the_bytes_the_sidecar_names(api, tmp_path, encoder):
    pid, cid, job = await _submitted(api, tmp_path)
    q = _queue()
    await _take(q, job)
    await _run("A", job, pid, cid, q)
    exports = encoder.exports
    published = exports / f"{cid}.ass"
    side = json.loads((exports / f"{cid}.json").read_text(encoding="utf-8"))
    rec = side["render_record"]
    assert rec["caption_filter"] is True and rec["ass_refused"] is None
    assert rec["ass_sha256"] == hashlib.sha256(published.read_bytes()).hexdigest()
    # The record names the file that exists, not the attempt's scratch copy.
    assert Path(rec["ass_path"]).resolve() == published.resolve()
    assert Path(rec["ass_path_offered"]).resolve() == published.resolve()
    assert rec["offered_matches_used"] is True
    assert (exports / f"{cid}.mp4").read_bytes() == b"mp4 burning " + published.read_bytes()
    assert sorted(p.name for p in exports.iterdir()) == [f"{cid}.ass", f"{cid}.json",
                                                        f"{cid}.mp4"]


@pytest.mark.parametrize("burn", [True, False])
async def test_a_refused_publish_leaves_every_previous_file_and_no_scratch(
        api, tmp_path, encoder, burn):
    """F2 + F3 on the refusal path: A is cancelled from the other backend while
    its encode runs; its finished encode publishes nothing, and nothing of it
    stays beside the export."""
    encoder.burn = burn
    pid, cid, a = await _submitted(api, tmp_path)
    exports = encoder.exports
    previous = {f"{cid}{s}": f"previous {s}".encode() for s in (".mp4", ".json", ".ass")}
    for name, data in previous.items():
        (exports / name).write_bytes(data)
    q = _queue()
    await _take(q, a)
    encoder.gate = threading.Event()
    task = _run("A", a, pid, cid, q)
    assert await asyncio.to_thread(encoder.reached.wait, 10)
    await _queue().cancel_job(a)                     # from the OTHER backend
    encoder.gate.set()
    with pytest.raises(RuntimeError, match="current export"):
        await task
    assert {p.name: p.read_bytes() for p in exports.iterdir()} == previous


@pytest.mark.parametrize("burn", [True, False])
async def test_a_dynamic_export_leaves_no_hidden_file_beside_it(api, tmp_path, encoder, burn):
    """F3 on the success path. Without captions the `.ass` beside the export is
    left exactly as it was — today's behaviour, kept on purpose (see result)."""
    encoder.burn = burn
    pid, cid, job = await _submitted(api, tmp_path)
    exports = encoder.exports
    (exports / f"{cid}.ass").write_bytes(b"an older export's captions")
    q = _queue()
    await _take(q, job)
    await _run("A", job, pid, cid, q)
    assert _hidden(exports) == []
    assert sorted(p.name for p in exports.iterdir()) == [f"{cid}.ass", f"{cid}.json",
                                                        f"{cid}.mp4"]
    stale = (exports / f"{cid}.ass").read_bytes() == b"an older export's captions"
    assert stale is not burn


async def test_an_in_process_cancel_leaves_no_staged_file_when_the_encode_ends_later(
        api, tmp_path, encoder):
    """F6: `task.cancel()` stops the coroutine, not the encoder thread. The
    staged mp4 that thread finishes after `finally` ran must not stay behind."""
    encoder.burn = False                             # ffmpeg has read any .ass by then
    pid, cid, job = await _submitted(api, tmp_path)
    q = _queue()
    await _take(q, job)
    encoder.gate = threading.Event()
    task = _run("A", job, pid, cid, q)
    assert await asyncio.to_thread(encoder.reached.wait, 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    encoder.gate.set()
    assert await asyncio.to_thread(encoder.returned.wait, 10)
    deadline = time.monotonic() + 3
    while _hidden(encoder.exports) and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert _hidden(encoder.exports) == []
    assert not (encoder.exports / f"{cid}.mp4").exists()
