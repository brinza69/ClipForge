"""OW1 readers (codex-verdict-next-24 §1 (1), (3), (4), (5)): one generation per operation, an explicit
refusal for a generation that does not verify, and `/retry` + the settings rescore bound to it.

Helpers in ow1_fixtures.py. Scoring goes through the real JobQueue + handle_score.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from services.clipper import analysis_generation as ag
from services.clipper import attempt_stop, scene_address, storage, vocal_bursts
from tests import ow1_fixtures as fx
from tests.ow1_fixtures import retire_ow1_jobs  # noqa: F401  (autouse)
from workers import clipper_build, clipper_finalize, clipper_render_plan


def _rewrite_manifest(pid: str, gen: str, change) -> None:
    path = ag.gen_dir(pid, gen) / ag.MANIFEST
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


# ── (4) the manifest: exactly the files, the absences, no fallback ────────────────────────────────

def test_a_sealed_generation_verifies_and_lists_exactly_its_files():
    ctx = fx.seed_generation("ow1man", "ow1man-a1", frames=3)
    assert ag.MANIFEST not in ctx.manifest["files"], "the manifest never lists its own hash"
    assert sorted(ctx.manifest["files"]) == sorted(
        ["signals.json", "faces.json", "regions.json", "frames_pts.json",
         "frames/frame_00000.jpg", "frames/frame_00001.jpg", "frames/frame_00002.jpg"])
    assert (ctx.manifest["absent"], ctx.manifest["regions_by_segment"]) == (
        ["regions_by_segment.json"], "none")
    assert ctx.read("regions_by_segment") is None and ctx.current
    assert ctx.frames() == [str(ctx.dir / f"frames/frame_{i:05d}.jpg") for i in range(3)]


@pytest.mark.parametrize("damage", [
    "no_manifest", "hash_mismatch", "file_missing", "frames_pts_unlisted", "frame_unlisted",
    "lists_itself", "rbs_undeclared", "foreign_file", "foreign_file_hashed", "other_gen_id"])
def test_a_damaged_generation_is_unavailable_never_partial(damage):
    pid, gen = f"ow1dm_{damage}", f"ow1dm_{damage}-a1"
    fx.seed_generation(pid, gen)
    gdir = ag.gen_dir(pid, gen)
    {"no_manifest": lambda: (gdir / ag.MANIFEST).unlink(),
     "hash_mismatch": lambda: (gdir / "faces.json").write_text("{}"),
     "file_missing": lambda: (gdir / "frames" / "frame_00001.jpg").unlink(),
     "frames_pts_unlisted": lambda: _rewrite_manifest(pid, gen, lambda d: d["files"].pop("frames_pts.json")),
     "frame_unlisted": lambda: _rewrite_manifest(
         pid, gen, lambda d: d["files"].pop("frames/frame_00000.jpg")),
     "lists_itself": lambda: _rewrite_manifest(
         pid, gen, lambda d: d["files"].__setitem__(ag.MANIFEST, "0" * 64)),
     "rbs_undeclared": lambda: _rewrite_manifest(pid, gen, lambda d: d.__setitem__("absent", [])),
     "foreign_file": lambda: _rewrite_manifest(
         pid, gen, lambda d: d["files"].__setitem__("segments.json", "0" * 64)),
     "foreign_file_hashed": lambda: ((gdir / "segments.json").write_text("[]"), _rewrite_manifest(
         pid, gen, lambda d: d["files"].__setitem__("segments.json", ag.sha256_file(gdir / "segments.json")))),
     "other_gen_id": lambda: _rewrite_manifest(pid, gen, lambda d: d.__setitem__("gen_id", "x-a1")),
     }[damage]()
    ctx = ag.open_context(pid, gen)
    assert ctx.state == ag.UNAVAILABLE and ctx.reason and not ctx.current
    for read in (lambda: ctx.read("signals"), ctx.frames, ctx.require):
        with pytest.raises(ag.GenerationUnavailable):
            read()


def test_a_file_changed_after_the_check_is_refused_at_the_read():
    ctx = fx.seed_generation("ow1late", "ow1late-a1")
    (ctx.dir / "signals.json").write_text('{"scenes": []}')
    with pytest.raises(ag.GenerationUnavailable, match="does not match its sha256"):
        ctx.read("signals")


async def test_no_consumer_turns_an_unavailable_generation_into_empty_inputs(client, monkeypatch):
    pid, gen = "ow1nofb", "ow1nofb-a1"
    await fx.make_project(pid, status="failed", generation=gen)
    fx.seed_generation(pid, gen)
    (ag.gen_dir(pid, gen) / "signals.json").write_text("{}")
    storage.write_artifact(pid, "signals", {"scenes": [9.9]})     # a flat file must NOT stand in
    r = await client.get(f"/api/clipper/projects/{pid}/artifacts/signals")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "analysis_unavailable"
    project = await fx.project(pid)
    with pytest.raises(ag.GenerationUnavailable):
        await clipper_render_plan._decide_render(object(), project, None)
    fx.Fakes(monkeypatch)
    await fx.add_job(pid, "ow1nofbs", job_type="clipper_score", metadata={"generation": gen})
    task = await fx.start(fx.queue(), "ow1nofbs")
    await fx.settle(task)
    row = await fx.job("ow1nofbs")
    assert row.status == "failed" and "unavailable" in row.error
    assert await fx.clip_count(pid) == 0 and not storage.paths(pid)["candidates"].exists()


# ── (3) /retry and (Q2) the settings rescore ──────────────────────────────────────────────────────

async def _retry(client, pid: str, *, generation=None, seed=None, transcript=True, flat=False):
    await fx.make_project(pid, status="failed", generation=generation)
    if seed:
        seed()
    if flat:                                                        # a legacy analysis on disk
        storage.write_artifact(pid, "signals", {"scenes": [1.0]})
        storage.write_artifact(pid, "candidates", [{"start": 1.0, "end": 9.0}])
    if not transcript:
        fx.sql("DELETE FROM transcripts WHERE project_id=?", pid)
    await fx.add_job(pid, f"{pid}s", job_type="clipper_score", status="failed")
    r = await client.post(f"/api/clipper/projects/{pid}/retry")
    assert r.status_code == 200, r.text
    job = await fx.job(r.json()["job_id"])
    return r.json()["resumed_at"], json.loads(job.metadata_json or "{}")


async def test_retry_resumes_scoring_on_a_verified_generation_without_candidates(client):
    got = await _retry(client, "ow1rtok", generation="ow1rtok-a1",
                       seed=lambda: fx.seed_generation("ow1rtok", "ow1rtok-a1"))
    assert got == ("clipper_score", {"stage": "resume", "generation": "ow1rtok-a1"})
    assert not storage.paths("ow1rtok")["candidates"].exists()


async def test_retry_never_scores_a_legacy_analysis_because_files_exist(client):
    assert (await _retry(client, "ow1rtleg", flat=True))[0] == "clipper_analyze"


async def test_retry_reanalyses_a_generation_that_does_not_verify(client):
    def broken():
        fx.seed_generation("ow1rtbad", "ow1rtbad-a1")
        (ag.gen_dir("ow1rtbad", "ow1rtbad-a1") / "regions.json").write_text("{}")
    got = await _retry(client, "ow1rtbad", generation="ow1rtbad-a1", seed=broken, flat=True)
    assert got[0] == "clipper_analyze" and "generation" not in got[1]


async def test_retry_reanalyses_a_generation_written_by_other_analysis_code(client):
    def old():
        fx.seed_generation("ow1rtver", "ow1rtver-a1")
        _rewrite_manifest("ow1rtver", "ow1rtver-a1", lambda d: d.__setitem__("analysis_version", "0"))
    assert (await _retry(client, "ow1rtver", generation="ow1rtver-a1", seed=old))[0] == "clipper_analyze"


async def test_retry_without_a_transcript_transcribes(client):
    got = await _retry(client, "ow1rtnot", generation="ow1rtnot-a1", transcript=False,
                       seed=lambda: fx.seed_generation("ow1rtnot", "ow1rtnot-a1"))
    assert got[0] == "clipper_transcribe"


@pytest.mark.parametrize("state", ["current", "legacy"])
async def test_a_settings_rescore_reads_a_generation_or_rebuilds_one(client, state):
    pid = f"ow1pt{state[:3]}"
    gen = f"{pid}-a1" if state == "current" else None
    await fx.make_project(pid, status="ready", generation=gen)
    if gen:
        fx.seed_generation(pid, gen)
    else:
        storage.write_artifact(pid, "candidates", [{"start": 1.0, "end": 9.0}])
    r = await client.patch(f"/api/clipper/projects/{pid}/settings",
                           json={"content_type_override": "podcast"})
    assert r.status_code == 200, r.text
    job = await fx.job(r.json()["rescore_job_id"])
    meta = json.loads(job.metadata_json)
    if gen:
        assert (job.type, meta["generation"]) == ("clipper_score", gen)
    else:
        assert job.type == "clipper_analyze" and "generation" not in meta


# ── (1) one generation per scoring operation, and the board only while it is current ─────────────

async def _score(monkeypatch, pid: str, *, flip_at: str | None):
    """handle_score pinned to G1 through the queue; the pointer moves to G2 at `flip_at`."""
    await fx.make_project(pid, status="scoring", generation=f"{pid}-a1")
    fx.seed_generation(pid, f"{pid}-a1")
    fx.seed_generation(pid, f"{pid}-a2")
    fx.Fakes(monkeypatch)
    reads: list[tuple] = []
    real_read = ag.Context.read

    def read(self, name):
        reads.append((self.generation, name))
        if flip_at == "first_read" and len(reads) == 1:
            fx.sql("UPDATE projects SET analysis_generation=? WHERE id=?", f"{pid}-a2", pid)
        return real_read(self, name)
    monkeypatch.setattr(ag.Context, "read", read)
    real_write = clipper_finalize._write_clips

    async def write(*a, **k):
        if flip_at == "before_board":
            fx.sql("UPDATE projects SET analysis_generation=? WHERE id=?", f"{pid}-a2", pid)
        return await real_write(*a, **k)
    monkeypatch.setattr(clipper_build, "_write_clips", write)
    real_traces = clipper_build._write_traces

    def traces(*a, **k):
        if flip_at == "after_board":
            fx.sql("UPDATE projects SET analysis_generation=? WHERE id=?", f"{pid}-a2", pid)
        return real_traces(*a, **k)
    monkeypatch.setattr(clipper_build, "_write_traces", traces)
    await fx.add_job(pid, f"{pid}s", job_type="clipper_score",
                     metadata={"launched_by": "pipeline", "generation": f"{pid}-a1"})
    if flip_at == "before_start":              # G2 published before this G1 score even began
        await fx.asql("UPDATE projects SET analysis_generation=? WHERE id=?", f"{pid}-a2", pid)
    q = fx.queue()
    # The flips below are blocking writes from hooks ON the loop: with the heartbeat parked no
    # aiosqlite transaction of this loop is in flight to wait on (see fx.asql).
    q.HEARTBEAT_SECONDS = 60
    task = await fx.start(q, f"{pid}s")
    await fx.settle(task)
    cands = storage.read_artifact(pid, "candidates") or []
    return reads, cands, await fx.job(f"{pid}s")


async def test_a_score_on_a_current_generation_publishes_its_board(monkeypatch):
    reads, cands, job = await _score(monkeypatch, "ow1sc0", flip_at=None)
    assert job.status == "done" and await fx.clip_count("ow1sc0") > 0
    assert {g for g, _ in reads} == {"ow1sc0-a1"} and cands
    assert {c["analysis_generation"] for c in cands} == {"ow1sc0-a1"}
    assert (await fx.project("ow1sc0")).status == "ready"


@pytest.mark.parametrize("flip_at", ["before_start", "first_read", "before_board"])
async def test_a_pointer_change_mid_score_never_mixes_or_publishes_a_late_board(monkeypatch, flip_at):
    pid = f"ow1sc_{flip_at}"
    reads, cands, job = await _score(monkeypatch, pid, flip_at=flip_at)
    assert {g for g, _ in reads} == {f"{pid}-a1"}, "every read came from the pinned generation"
    assert {c["analysis_generation"] for c in cands} == {f"{pid}-a1"}, "stamped from what was read"
    assert job.status == "cancelled" and await fx.clip_count(pid) == 0
    assert (await fx.project(pid)).analysis_generation == f"{pid}-a2"


# ── (5) a reader of G1 while G2 becomes current ───────────────────────────────────────────────────

async def test_a_g1_reader_keeps_reading_g1_after_g2_is_published(monkeypatch):
    pid = "ow1keep"
    fx.Fakes(monkeypatch)
    await fx.make_project(pid, status="ready", generation="ow1keep-a1")
    pinned = fx.seed_generation(pid, "ow1keep-a1")
    before = pinned.read("signals")
    await fx.add_job(pid, "ow1keepj", day=2)
    await fx.settle(await fx.start(fx.queue(), "ow1keepj"))
    assert (await fx.project(pid)).analysis_generation == ag.gen_id_for("ow1keepj", 1)
    assert pinned.read("signals") == before and pinned.frames()
    assert ag.open_context(pid, "ow1keep-a1").state == ag.VERIFIED, "no GC of a published generation"


# ── the cooperative stop, unit level (the handler-level bound is in test_clipper_ow1_races) ───────

def test_the_3b_row_loop_stops_before_the_next_ffprobe():
    stop, calls = threading.Event(), []

    def addresser(row, ctx):
        calls.append(row["id"])
        if len(calls) == 2:
            stop.set()
        return {"state": "interval"}
    rows = [{"id": i, "t": float(i), "legacy_index": i, "legacy_excluded": False, "proxy_pts": i,
             "time_base": "1/10", "slot": i, "scene_score": 0.5} for i in range(6)]
    scene = {"state": "ok", "rows": rows, "times": [float(i) for i in range(6)], "time_base": "1/10"}
    ident = {"stream": {"time_base": "1/10", "nb_frames": 60}}
    before = {"state": "recorded", "identity_check": {"proxy": "cache_reuse", "source": "cache_reuse"},
              "clock": {"state": "validated"}, "domain": {"state": "ok"},
              "proxy_identity": ident, "source_identity": ident}
    assert scene_address.project_state(before, "1/10")[0] == "ok", "the rows reach the addresser"
    with pytest.raises(attempt_stop.Stopped):
        scene_address.assemble(scene, before, lambda: before, addresser, {}, stop)
    assert calls == [0, 1]


def test_vocal_bursts_and_build_signals_raise_on_a_stop(tmp_path, monkeypatch):
    import wave

    import numpy as np

    from services.clipper import signals

    wav = tmp_path / "s.wav"
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1), f.setsampwidth(2), f.setframerate(16000)
        f.writeframes((np.sin(np.arange(16000 * 3) / 5) * 9000).astype("<i2").tobytes())
    stop = threading.Event()
    stop.set()
    with pytest.raises(attempt_stop.Stopped):
        vocal_bursts.vocal_burst_timeline(wav, [], stop=stop)
    monkeypatch.setattr(scene_address, "scene_pass", lambda *a, **k: pytest.fail("ran past the stop"))
    with pytest.raises(attempt_stop.Stopped):
        signals.build_signals("ow1bs", str(tmp_path / "none.mp4"), str(wav), 3.0, stop=stop)


async def test_cleanup_waits_for_the_last_thread_and_a_late_body_never_runs():
    threads, gate, ran, cleaned = attempt_stop.Threads(), threading.Event(), [], []
    live = asyncio.get_running_loop().run_in_executor(None, threads.wrap(lambda: gate.wait(5)))
    await asyncio.sleep(0.05)
    assert threads.close(lambda: cleaned.append(1)) is False and cleaned == []
    gate.set()
    await live
    assert cleaned == [1]
    with pytest.raises(attempt_stop.Stopped):
        threads.wrap(lambda: ran.append(1))()
    assert ran == []


async def test_a_board_written_before_the_pointer_moved_does_not_mark_the_new_analysis_ready(monkeypatch):
    reads, cands, job = await _score(monkeypatch, "ow1scA", flip_at="after_board")
    assert job.status == "done" and await fx.clip_count("ow1scA") > 0     # G1 was current then
    row = await fx.project("ow1scA")
    assert row.analysis_generation == "ow1scA-a2" and row.status == "scoring", "G2's own score decides"
