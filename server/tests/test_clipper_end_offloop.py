"""EN2 R3 (codex-verdict-next-5 §2): the boundary refinement runs OFF the event loop.

With the audio, `refine_boundaries` reads the VAD over nearly the whole speech.wav — ~150 s for 4 h,
~7 min extrapolated for 12 h (B/EN1-result.md §5). In `handle_score` that ran on the event loop the
heartbeat shares, and the lease is 120 s. Every test here goes through the real JobQueue and the real
`handle_score`: the queue claims the job, runs its heartbeat, and the handler publishes (candidates.json,
clip rows) or does not. What is controlled is only WHERE the refinement blocks — a candidate, or one
phase of the VAD — with a threading.Event the test releases.

Before R3 the loop was blocked, so the test could neither observe a heartbeat nor cancel during the
refinement; each gate below times out instead (GATE_S) and the assertion on it fails.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
import wave
from datetime import datetime

import numpy as np
import pytest
from sqlalchemy import select

from database import async_session
from job_queue import JobQueue
from models import ClipModel, JobModel, ProjectModel, TranscriptModel
from services.clipper import candidates as cand_mod
from services.clipper import end_acoustics as ea
from services.clipper import storage
from workers import clipper_build

GATE_S = 3.0          # how long a blocked refinement waits for the test before giving up
real_refine = cand_mod.refine_boundaries    # the in-loop reference, before any monkeypatch


# ── a small project: 20 sentences over 80 s, one tone burst per word ─────────────────────────────

def _words() -> list[dict]:
    out, t = [], 0.5
    for s in range(20):
        for i, w in enumerate(("we", "went", "down", "there", f"again{s}.")):
            out.append({"word": w if i else w.capitalize(), "start": round(t, 3), "end": round(t + 0.4, 3)})
            t += 0.5
        t += 1.5                                            # a pause after every sentence
    return out


def _transcript() -> list[dict]:
    words = _words()
    return [{"start": words[i]["start"], "end": words[i + 4]["end"],
             "text": " ".join(w["word"] for w in words[i:i + 5]), "words": words[i:i + 5]}
            for i in range(0, len(words), 5)]


def _speech_wav(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(80 * 16000) / 16000
    x = np.zeros_like(t)
    for w in _words():
        # A word's sound runs 150 ms past Whisper; a sentence's last one 450 ms, past the transcript
        # rules' own pad, so the audio step MOVES those ends and the comparison covers a move.
        tail = 0.45 if w["word"].endswith(".") else 0.15
        on = (t >= w["start"]) & (t < w["end"] + tail)
        x[on] = 0.5 * np.sin(2 * np.pi * 200 * t[on])
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes((x * 32767).astype("<i2").tobytes())


def _loud(x) -> list[list[float]]:
    """A deterministic stand-in for ONE Silero phase: 1 ms frames over 0.1."""
    n = len(x) // 16
    on = np.abs(np.asarray(x[:n * 16])).reshape(n, 16).max(axis=1) > 0.1
    edges = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(int), [0]])))
    return [[a * 16 / 16000, b * 16 / 16000] for a, b in zip(edges[::2], edges[1::2])]


async def _project(pid: str) -> None:
    segments = _transcript()
    async with async_session() as session:
        session.add(ProjectModel(
            id=pid, title="r3", source_type="local", status="analyzing", duration=80.0,
            width=1920, height=1080,
            clipper_settings={"reasoning_mode": "legacy", "headline_enabled": False,
                              "min_clip_s": 6, "max_clip_s": 20}))
        session.add(TranscriptModel(project_id=pid, language="en", segments=segments,
                                    full_text=" ".join(s["text"] for s in segments)))
        await session.commit()
    _speech_wav(storage.paths(pid)["audio"])


async def _start(queue: JobQueue, pid: str, job_id: str):
    async with async_session() as session:
        session.add(JobModel(id=job_id, project_id=pid, type="clipper_score", status="queued",
                             progress=0.0, created_at=datetime(1990, 1, 1),
                             updated_at=datetime(1990, 1, 1), metadata_json="{}"))
        await session.commit()
    await queue._process_next()
    assert job_id in queue._running_jobs, "the queue claimed this job"
    return queue._running_jobs[job_id]


async def _job(job_id: str) -> JobModel:
    async with async_session() as session:
        return await session.get(JobModel, job_id)


async def _published(pid: str) -> tuple[bool, int]:
    async with async_session() as session:
        rows = (await session.execute(select(ClipModel).where(ClipModel.project_id == pid))).scalars().all()
    return storage.paths(pid)["candidates"].exists(), len(rows)


async def _until(cond, what: str, timeout: float = GATE_S) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        assert loop.time() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.01)


@pytest.fixture
async def held(monkeypatch):
    """Teardown: release every gate and let every job task end, so a failing test (as they all do on
    the in-loop code) leaves no running handler holding the database for the next one.

    `clipper_score` runs in the light lane, and `_process_next` asks the heavy lane first: in the full
    suite, earlier tests leave `clipper_export` rows queued, and it would fail one of those (no handler)
    instead of claiming this job. Closing the heavy lane for these queues leaves those rows alone."""
    from config import settings

    monkeypatch.setattr(settings, "max_concurrent_jobs", 0)
    gates: list[threading.Event] = []
    tasks: list[asyncio.Task] = []
    yield gates, tasks
    for gate in gates:
        gate.set()
    for task in tasks:
        await asyncio.wait([task], timeout=GATE_S * 5)


def _queue() -> JobQueue:
    queue = JobQueue()
    queue.HEARTBEAT_SECONDS = 0.05
    queue.register_handler("clipper_score", clipper_build.handle_score)
    return queue


class PhaseGate:
    """`ea.vad_spans` that blocks inside the Nth VAD phase until released."""

    def __init__(self, block_at: int | None):
        self.calls = 0
        self.block_at = block_at
        self.entered, self.release = threading.Event(), threading.Event()

    def __call__(self, x):
        self.calls += 1
        if self.calls == self.block_at:
            self.entered.set()
            self.release.wait(GATE_S)
        return _loud(x)


# ── the tests ───────────────────────────────────────────────────────────────────────────────

async def test_the_event_loop_and_the_heartbeat_advance_during_a_blocked_refinement(monkeypatch, held):
    pid, job_id = "r3hb0001", "r3-job-hb-0001"
    await _project(pid)
    real = cand_mod.refine_boundaries
    entered, release, waited = threading.Event(), threading.Event(), []
    held[0].append(release)

    def blocked(cand, *a, **k):
        if not entered.is_set():
            entered.set()
            waited.append(release.wait(GATE_S))
        return real(cand, *a, **k)

    monkeypatch.setattr(cand_mod, "refine_boundaries", blocked)
    monkeypatch.setattr(ea, "vad_spans", _loud)
    queue = _queue()
    task = await _start(queue, pid, job_id)
    held[1].append(task)
    await _until(entered.is_set, "the refinement to start")

    first = (await _job(job_id)).last_heartbeat
    ticks = 0
    beats = {first}
    while len(beats) < 3:                              # two renewals while the refinement is held
        assert not waited, "the refinement gave up waiting: the event loop was blocked"
        ticks += 1
        await asyncio.sleep(0.02)
        beats.add((await _job(job_id)).last_heartbeat)
    release.set()
    await asyncio.wait_for(task, GATE_S * 3)

    assert waited == [True] and ticks >= 2          # this coroutine ran, and the heartbeat twice
    job = await _job(job_id)
    assert job.status == "done", job.error
    assert (await _published(pid))[0], "a normal run publishes candidates.json"


async def test_cancel_stops_the_vad_between_phases_and_nothing_is_published(monkeypatch, held):
    pid, job_id = "r3cx0001", "r3-job-cx-0001"
    await _project(pid)
    gate = PhaseGate(block_at=2)                       # inside the first block, second phase
    monkeypatch.setattr(ea, "vad_spans", gate)
    held[0].append(gate.release)
    queue = _queue()
    task = await _start(queue, pid, job_id)
    held[1].append(task)
    await _until(gate.entered.is_set, "the second VAD phase")

    await queue.cancel_job(job_id)                     # what the API's cancel does
    await asyncio.sleep(0.3)
    assert not task.done(), "the job ended while its refinement thread was still running"
    gate.release.set()
    await asyncio.wait_for(task, GATE_S * 3)
    await asyncio.sleep(0.2)                           # a thread still running would call again

    assert gate.calls == 2, "no phase, block or candidate ran after the stop"
    assert (await _job(job_id)).status == "cancelled"
    assert await _published(pid) == (False, 0)


async def test_lost_ownership_stops_the_work_and_prevents_publication(monkeypatch, held):
    pid, job_id = "r3lo0001", "r3-job-lo-0001"
    await _project(pid)
    gate = PhaseGate(block_at=3)
    monkeypatch.setattr(ea, "vad_spans", gate)
    held[0].append(gate.release)
    queue = _queue()
    task = await _start(queue, pid, job_id)
    held[1].append(task)
    await _until(gate.entered.is_set, "the third VAD phase")

    async with async_session() as session:            # another worker took the lease over
        job = await session.get(JobModel, job_id)
        job.worker_id = "someone-else"
        await session.commit()
    await _until(lambda: job_id in queue._lost_ownership_jobs or task.done(),
                 "the heartbeat to notice")
    await asyncio.sleep(0.3)
    assert not task.done(), "the job ended while its refinement thread was still running"
    gate.release.set()
    await asyncio.wait_for(task, GATE_S * 3)
    await asyncio.sleep(0.2)

    assert gate.calls == 3
    job = await _job(job_id)
    assert job.worker_id == "someone-else" and job.status == "running", "the new owner's row is untouched"
    assert await _published(pid) == (False, 0)


async def test_a_retry_after_a_cancel_runs_normally_with_the_in_loop_results(monkeypatch, held):
    """Same raw candidates, same audio, same (stand-in) VAD: the off-loop run publishes exactly what the
    in-loop `for` produced — refine_boundaries per candidate with one SpeechAudio for the run."""
    pid = "r3rt0001"
    await _project(pid)
    gate = PhaseGate(block_at=1)
    monkeypatch.setattr(ea, "vad_spans", gate)
    held[0].append(gate.release)
    raws = []
    real_generate = cand_mod.generate_candidates
    monkeypatch.setattr(cand_mod, "generate_candidates",
                        lambda *a, **k: raws.append(real_generate(*a, **k)) or copy.deepcopy(raws[-1]))
    queue = _queue()
    task = await _start(queue, pid, "r3-job-rt-0001")
    held[1].append(task)
    await _until(gate.entered.is_set, "the first VAD phase")
    await queue.cancel_job("r3-job-rt-0001")
    gate.release.set()
    await asyncio.wait_for(task, GATE_S * 3)
    assert await _published(pid) == (False, 0)

    gate.block_at = None                               # the retry: nothing held
    async with async_session() as session:
        (await session.get(ProjectModel, pid)).status = "analyzing"
        await session.commit()
    task = await _start(queue, pid, "r3-job-rt-0002")
    held[1].append(task)
    await asyncio.wait_for(task, 60)
    assert (await _job("r3-job-rt-0002")).status == "done"

    transcript = {"language": "en", "segments": _transcript()}
    audio = ea.SpeechAudio(storage.paths(pid)["audio"])
    expected = [real_refine(copy.deepcopy(c), transcript, {}, min_s=6.0, max_s=20.0, atoms=[],
                            audio=audio) for c in raws[-1]]
    got = json.loads(storage.paths(pid)["candidates"].read_text(encoding="utf-8"))
    key = lambda c: (c["start"], c["end"], c["text"], json.dumps(c.get("end_evidence"), sort_keys=True))
    assert len(raws[-1]) > 3 and any((c.get("end_evidence") or {}).get("status") == "moved" for c in expected)
    assert sorted(map(key, got)) == sorted(map(key, expected))



# ── the stop itself: never an `unavailable`, never the per-candidate fallback ─────────────────────

def test_a_stop_inside_the_vad_is_not_an_unavailable_reading(tmp_path, monkeypatch):
    """Stopped mid-block: the read raises, and the block is not cached — a later reader of the same
    SpeechAudio reads the audio, not a remembered failure."""
    stop = [False]

    def phase(x):
        stop[0] = True                                  # the job is cancelled during the first phase
        return _loud(x)

    monkeypatch.setattr(ea, "vad_spans", phase)
    path = tmp_path / "speech.wav"
    _speech_wav(path)
    audio = ea.SpeechAudio(path, stop=lambda: stop[0])
    with pytest.raises(ea.Stopped):
        audio.window(1.0, 4.0)
    stop[0] = False
    monkeypatch.setattr(ea, "vad_spans", _loud)
    win = audio.window(1.0, 4.0)
    assert "unavailable" not in win and win["spans"], win


async def test_a_stop_in_the_last_candidate_is_a_cancel_not_a_fallback(tmp_path, monkeypatch):
    """With nothing after it, no between-candidates check would catch a swallowed stop: the run would
    return the candidate unrefined as if it had failed, and the caller would publish it."""
    from job_queue import JobCancelledError
    from workers.clipper_boundary_pass import refine_off_loop

    class Queue:
        cancelled = False

        def is_cancelled(self, _job_id):
            return self.cancelled

    queue = Queue()

    def phase(x):
        queue.cancelled = True
        return _loud(x)

    monkeypatch.setattr(ea, "vad_spans", phase)
    path = tmp_path / "speech.wav"
    _speech_wav(path)
    raw = [{"start": 0.5, "end": 5.0, "text": "We went down there again0.", "reasons": []}]
    with pytest.raises(JobCancelledError):
        await refine_off_loop(raw, {"segments": _transcript()}, {}, min_s=2.0, max_s=20.0, atoms=[],
                              audio_path=path, queue=queue, job_id="r3-last")


async def test_a_cancel_between_candidates_stops_even_when_no_audio_is_read(tmp_path, monkeypatch):
    """Every block already cached: no read, so no reader check. The loop's own check stops it."""
    from job_queue import JobCancelledError
    from workers.clipper_boundary_pass import refine_off_loop

    class Queue:
        cancelled = False

        def is_cancelled(self, _job_id):
            return self.cancelled

    queue, refined = Queue(), []

    def one(cand, *a, **k):
        refined.append(real_refine(cand, *a, **k))
        queue.cancelled = True                          # cancelled after the first candidate
        return refined[-1]

    monkeypatch.setattr(ea, "vad_spans", _loud)
    monkeypatch.setattr(cand_mod, "refine_boundaries", one)
    path = tmp_path / "speech.wav"
    _speech_wav(path)
    one_window = {"start": 0.5, "end": 5.0, "text": "We went down there again0.", "reasons": []}
    raw = [one_window, dict(one_window)]                # the second reads only what the first cached
    with pytest.raises(JobCancelledError):
        await refine_off_loop(raw, {"segments": _transcript()}, {}, min_s=2.0, max_s=20.0, atoms=[],
                              audio_path=path, queue=queue, job_id="r3-between")
    assert len(refined) == 1
