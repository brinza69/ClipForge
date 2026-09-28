"""Shared fixtures for the OW1 tests (test_clipper_ow1_*.py): the analysis generation.

Every race test goes through the REAL `JobQueue` and the REAL `handle_analyze`. What is faked is only
the expensive, media-bound work, and each fake can hold its thread on a `threading.Event` the test
releases: `build_signals`, the frame grab (`proxy_provenance.run_showinfo`, one call per ffmpeg launch),
the vocal-burst pass and the three content-type detectors. A race "from another backend" is a direct SQL
write on its own sqlite3 connection, as a second process sharing clipforge.db would make it.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from config import settings
from database import async_session
from job_queue import JobQueue
from models import ClipModel, JobModel, ProjectModel, TranscriptModel
from services.clipper import analysis_generation as ag
from services.clipper import attempt_stop, content_type, proxy_provenance, signals, storage
from services.clipper import vocal_bursts
from workers import clipper_analysis_publish as pub
from workers import clipper_pipeline

GATE_S = 5.0
JPEG = b"\xff\xd8\xff\xe0" + b"frame" * 20 + b"\xff\xd9"


def segments() -> list[dict]:
    out, t = [], 0.5
    for s in range(12):
        words = []
        for i, w in enumerate(("we", "went", "down", "there", f"again{s}.")):
            words.append({"word": w if i else w.capitalize(), "start": round(t, 3), "end": round(t + 0.4, 3)})
            t += 0.5
        t += 1.5
        out.append({"start": words[0]["start"], "end": words[-1]["end"],
                    "text": " ".join(w["word"] for w in words), "words": words})
    return out


async def make_project(pid: str, *, status: str = "transcribed", generation: str | None = None) -> None:
    async with async_session() as session:
        session.add(ProjectModel(
            id=pid, title="ow1", source_type="local", status=status, duration=80.0, width=1920,
            height=1080, video_path=None, analysis_generation=generation,
            clipper_settings={"reasoning_mode": "legacy", "headline_enabled": False,
                              "min_clip_s": 6, "max_clip_s": 20}))
        segs = segments()
        session.add(TranscriptModel(project_id=pid, language="en", segments=segs,
                                    full_text=" ".join(s["text"] for s in segs)))
        await session.commit()
    storage.ensure_dirs(pid)
    p = storage.paths(pid)
    p["proxy"].write_bytes(b"proxy-bytes")
    p["audio"].write_bytes(b"audio-bytes")


async def add_job(pid: str, job_id: str, *, job_type: str = "clipper_analyze", day: int = 1,
                  status: str = "queued", metadata: dict | None = None) -> None:
    when = datetime(1990, 1, day)
    async with async_session() as session:
        session.add(JobModel(id=job_id, project_id=pid, type=job_type, status=status, progress=0.0,
                             created_at=when, updated_at=when,
                             metadata_json=json.dumps(metadata) if metadata else None))
        await session.commit()


def queue() -> JobQueue:
    q = JobQueue()
    q.HEARTBEAT_SECONDS = 0.05
    clipper_pipeline.register_clipper_handlers(q)
    return q


async def start(q: JobQueue, job_id: str) -> asyncio.Task:
    await q._process_next()
    assert job_id in q._running_jobs, f"the queue claimed {job_id}"
    return q._running_jobs[job_id]


async def until(cond, what: str, timeout: float = GATE_S) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        assert loop.time() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.01)


async def settle(*tasks: asyncio.Task) -> None:
    """Every job task ENDS inside its test. One still running when the test's loop closes leaves its
    aiosqlite connection mid-transaction, and the next test meets `database is locked`."""
    _, pending = await asyncio.wait(list(tasks), timeout=GATE_S * 3)
    if pending:
        import io
        out = io.StringIO()
        for t in pending:
            t.print_stack(file=out)
        raise AssertionError("a job task did not end: " + out.getvalue())


async def job(job_id: str) -> JobModel:
    async with async_session() as session:
        return await session.get(JobModel, job_id)


async def project(pid: str) -> ProjectModel:
    async with async_session() as session:
        return await session.get(ProjectModel, pid)


async def score_jobs(pid: str) -> list[dict]:
    async with async_session() as session:
        rows = (await session.execute(select(JobModel).where(
            JobModel.project_id == pid, JobModel.type == "clipper_score"))).scalars().all()
    return [json.loads(r.metadata_json or "{}") for r in rows]


async def clip_count(pid: str) -> int:
    async with async_session() as session:
        return len((await session.execute(
            select(ClipModel).where(ClipModel.project_id == pid))).scalars().all())


def sql(statement: str, *args) -> int:
    """A write from ANOTHER connection, as a second backend would make it."""
    con = sqlite3.connect(str(settings.db_path), timeout=30)
    try:
        cur = con.execute(statement, args)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


async def asql(statement: str, *args) -> int:
    """`sql` from a coroutine. NEVER call `sql` on the event loop while a queue runs: its heartbeat
    holds an aiosqlite write transaction whose COMMIT needs the loop, so a blocking write there waits
    on itself until the 30 s busy timeout (found as a flake in this suite, not in production)."""
    return await asyncio.to_thread(sql, statement, *args)


def gen_dirs(pid: str) -> list[str]:
    root = storage.paths(pid)["analysis_dir"] / "generations"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


class Fakes:
    """The media-bound work, with a gate per `build_signals` call and a launch log for the frame grab.

    `hold_signals[n]` (call n, 1-based) blocks that call until released, and it IGNORES the stop: it
    returns its result however long it was held, as an executor thread that no cancel can reach does.
    """

    def __init__(self, monkeypatch, *, frames: int = 8):
        self.signals_calls = 0
        self.hold_signals: dict[int, threading.Event] = {}
        self.entered: dict[int, threading.Event] = {}
        self.stops_seen: dict[int, threading.Event | None] = {}
        self.launches: list[float] = []
        self.frame_delay = 0.0
        self.hold_frames: threading.Event | None = None
        self.frames_entered = threading.Event()
        self.content_type_error: Exception | None = None
        monkeypatch.setattr(settings, "clipper_max_sampled_frames", frames)
        monkeypatch.setattr(settings, "max_concurrent_jobs", 0)
        monkeypatch.setattr(signals, "build_signals", self.build_signals)
        monkeypatch.setattr(vocal_bursts, "vocal_burst_timeline",
                            lambda wav, words, stop=None: attempt_stop.check(stop) or [0.0, 0.5])
        monkeypatch.setattr(proxy_provenance, "run_showinfo", self.run_showinfo)
        monkeypatch.setattr(content_type, "detect_regions", lambda frames: {"webcam": None, "n": len(frames)})
        monkeypatch.setattr(content_type, "detect_regions_by_range", lambda f, t, r: [])
        monkeypatch.setattr(content_type, "detect_content_type", self.detect_content_type)

    def build_signals(self, project_id, proxy, wav, duration, stop=None):
        self.signals_calls += 1
        n = self.signals_calls
        self.stops_seen[n] = stop
        self.entered.setdefault(n, threading.Event()).set()
        gate = self.hold_signals.get(n)
        if gate is not None:
            gate.wait(GATE_S * 4)                   # deliberately never looks at `stop`
        return {"version": "t", "duration": duration, "scenes": [1.0, 2.0], "call": n,
                "faces": [{"t": 1.0, "boxes": []}], "silence": [], "peaks": [], "speech": []}

    def run_showinfo(self, cmd, timeout=60, what=""):
        self.launches.append(time.monotonic())
        self.frames_entered.set()
        if self.hold_frames is not None:
            self.hold_frames.wait(GATE_S * 4)       # ignores the stop, then WRITES its frame
        if self.frame_delay:
            time.sleep(self.frame_delay)
        Path(cmd[-1]).write_bytes(JPEG)
        return {"pts": 10 * len(self.launches), "time_base": "1/10"}

    def detect_content_type(self, frames, sig, transcript):
        if self.content_type_error is not None:
            raise self.content_type_error
        return {"content_type": "gaming", "confidence": 0.9}


def verified(pid: str, gen: str) -> ag.Context:
    ctx = ag.open_context(pid, gen)
    assert ctx.state == ag.VERIFIED, ctx.reason
    return ctx


def seed_generation(pid: str, gen: str, *, sig: dict | None = None, frames: int = 2) -> ag.Context:
    """A complete generation on disk, written through the production writer."""
    gdir = ag.gen_dir(pid, gen)
    (gdir / "frames").mkdir(parents=True)
    names = []
    for i in range(frames):
        f = gdir / "frames" / f"frame_{i:05d}.jpg"
        f.write_bytes(JPEG + bytes([i]))
        names.append(str(f))
    storage.atomic_write_json(gdir / ag.FRAMES_PTS, {"frames": []})
    from services.clipper import ANALYSIS_VERSION
    header = {"gen_id": gen, "job_id": gen.split("-a")[0], "attempt_count": 1, "worker_id": "seed",
              "analysis_version": str(ANALYSIS_VERSION), "inputs": {},
              "content_type": {"content_type": "gaming"}, "frames_sampled": frames}
    pub.write_generation(gdir, sig or {"scenes": [1.0], "gen": gen, "silence": []},
                         {"webcam": None, "gen": gen}, [],
                         {"samples": [{"gen": gen}], "times": [1.0 * i for i in range(frames)]},
                         header, names)
    return verified(pid, gen)


def refusals(monkeypatch) -> list[str]:
    """Why each publication was refused, as `publish` said it."""
    seen: list[str] = []
    real = pub.publish

    async def recording(*a, **k):
        try:
            return await real(*a, **k)
        except pub.PublishRefused as exc:
            seen.append(str(exc))
            raise
    monkeypatch.setattr(pub, "publish", recording)
    return seen


def lease_past() -> str:
    return (datetime.utcnow() - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S.%f")


@pytest.fixture(autouse=True)
def retire_ow1_jobs():
    """The suite shares ONE clipforge.db. A row these tests leave queued is claimed by the next test's
    `_process_next`: the 1990 `clipper_ingest` of the upstream test, older than anything
    test_job_claim queues, made `test_graceful_stop_requeues_owned_job` time out in the full suite
    (it failed with "No handler registered for job type: clipper_ingest")."""
    yield
    sql("UPDATE jobs SET status='cancelled', worker_id=NULL, lease_expires_at=NULL "
        "WHERE status IN ('queued', 'running') AND project_id LIKE 'ow1%'")
