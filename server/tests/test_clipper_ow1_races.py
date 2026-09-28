"""OW1 (codex-verdict-next-21 §2, next-24 §1): a cancelled, superseded or stale `clipper_analyze`
attempt never publishes its analysis — not even from an executor thread that ignores the cancel.

Through the real JobQueue + handle_analyze; fakes and helpers in ow1_fixtures.py. The AD3H probe
(B/AD3H-result.md) found the defect this closes: an orphaned `build_signals` thread rewrote the shared
`analysis/signals.json` and `faces.json` minutes after a cancel, and after a lost lease.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from services.clipper import analysis_generation as ag
from services.clipper import storage
from workers import clipper_analysis_publish as pub
from tests import ow1_fixtures as fx
from tests.ow1_fixtures import retire_ow1_jobs  # noqa: F401  (autouse)


def _flat_untouched(pid: str) -> None:
    """No attempt writes into the shared analysis/ any more (AD3H's W1-W7)."""
    p = storage.paths(pid)
    for name in ("signals", "faces", "regions", "regions_by_segment", "meta"):
        assert not p[name].exists(), f"analysis/{name}.json was written"
    assert not any(p["frames_dir"].glob("*.jpg")), "the shared frames/ was written"


async def _published_whole(pid: str, gen: str, frames: int = 8) -> None:
    row = await fx.project(pid)
    assert row.analysis_generation == gen and row.status == "scoring"
    ctx = fx.verified(pid, gen)
    assert sorted(ctx.manifest["files"]) == sorted(
        ["signals.json", "faces.json", "regions.json", "frames_pts.json"]
        + [f"frames/frame_{i:05d}.jpg" for i in range(frames)])
    assert ctx.manifest["absent"] == ["regions_by_segment.json"]
    assert ctx.read("signals")["vocal_bursts"] == [0.0, 0.5]           # the ENRICHED form, once
    assert set(ctx.read("faces")) == {"samples", "times"}               # the final form only
    assert await fx.score_jobs(pid) == [{"launched_by": "pipeline", "generation": gen}]


# ── 1. old thread held, cancel, full retry, the old thread released and ignoring the stop ─────────

@pytest.mark.parametrize("order", ["retry_first", "orphan_first"])
async def test_a_released_orphan_never_touches_the_retry(monkeypatch, order):
    pid = f"ow1rt{order[0]}"
    fakes = fx.Fakes(monkeypatch)
    fakes.hold_signals[1] = threading.Event()
    await fx.make_project(pid)
    await fx.add_job(pid, f"{pid}j1", day=1)
    q = fx.queue()
    t1 = await fx.start(q, f"{pid}j1")
    await fx.until(fakes.entered.setdefault(1, threading.Event()).is_set, "attempt 1 in build_signals")
    g1 = ag.gen_id_for(f"{pid}j1", 1)
    assert fx.gen_dirs(pid) == [g1]

    await q.cancel_job(f"{pid}j1")                                      # the page's Cancel
    assert fakes.stops_seen[1].is_set(), "the cancel reached the attempt's thread"
    await fx.settle(t1)
    assert (await fx.job(f"{pid}j1")).status == "cancelled"
    assert fx.gen_dirs(pid) == [g1], "a live thread's files are not deleted under it"

    if order == "orphan_first":
        fakes.hold_signals[1].set()
        await fx.until(lambda: fx.gen_dirs(pid) == [], "the orphan's own cleanup after its thread")
    await fx.add_job(pid, f"{pid}j2", day=2)                            # /retry's new attempt
    t2 = await fx.start(q, f"{pid}j2")
    await fx.settle(t2)
    g2 = ag.gen_id_for(f"{pid}j2", 1)
    assert (await fx.job(f"{pid}j2")).status == "done"
    before = {n: (ag.gen_dir(pid, g2) / n).read_bytes() for n in fx.verified(pid, g2).manifest["files"]}
    if order == "retry_first":
        fakes.hold_signals[1].set()                                     # released, ignores the stop
        await fx.until(lambda: fx.gen_dirs(pid) == [g2], "the orphan's own cleanup after its thread")

    await _published_whole(pid, g2)
    assert {n: (ag.gen_dir(pid, g2) / n).read_bytes() for n in before} == before
    _flat_untouched(pid)


async def test_a_frame_grab_that_ignores_the_stop_writes_only_into_its_own_generation(monkeypatch):
    pid = "ow1grab"
    fakes = fx.Fakes(monkeypatch)
    fakes.hold_frames = threading.Event()
    await fx.make_project(pid)
    await fx.add_job(pid, "ow1grabj1", day=1)
    q = fx.queue()
    t1 = await fx.start(q, "ow1grabj1")
    await fx.until(fakes.frames_entered.is_set, "the first ffmpeg launch")
    g1 = ag.gen_id_for("ow1grabj1", 1)
    await q.cancel_job("ow1grabj1")
    await fx.settle(t1)
    fakes.hold_frames.set()                    # the held grab now WRITES frame_00000.jpg...
    await fx.until(lambda: fx.gen_dirs(pid) == [], "cleanup once the grab thread ended")
    assert len(fakes.launches) == 1, "...and the stop keeps the loop from launching another"
    assert (await fx.project(pid)).analysis_generation is None and await fx.score_jobs(pid) == []
    assert g1 not in fx.gen_dirs(pid)
    _flat_untouched(pid)


# ── 2. ownership loss or cancel just before publication ───────────────────────────────────────────

def _just_before_publication(monkeypatch, act):
    real = pub.write_generation

    def hooked(*a, **k):
        out = real(*a, **k)
        act()                                  # the generation is complete; the transaction is next
        return out
    monkeypatch.setattr(pub, "write_generation", hooked)


@pytest.mark.parametrize("race", ["db_cancel", "flag_only", "lease_expired", "attempt_bumped",
                                  "second_queue"])
async def test_publication_is_refused_when_the_attempt_is_no_longer_the_owner(monkeypatch, race):
    pid, jid = f"ow1own{race[:3]}", f"ow1o{race[:4]}"
    fakes = fx.Fakes(monkeypatch)
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    actions = {
        # another backend's /cancel: the row, not this process's local flag
        "db_cancel": lambda: fx.sql("UPDATE jobs SET status='cancelled', cancellation_requested=1 "
                                    "WHERE id=?", jid),
        # the cancel flag alone, the row still `running`
        "flag_only": lambda: fx.sql("UPDATE jobs SET cancellation_requested=1 WHERE id=?", jid),
        # the lease ran out before this worker's heartbeat noticed
        "lease_expired": lambda: fx.sql("UPDATE jobs SET lease_expires_at=? WHERE id=?",
                                        fx.lease_past(), jid),
        # the same worker id, but a later attempt of the job owns the row
        "attempt_bumped": lambda: fx.sql("UPDATE jobs SET attempt_count=attempt_count+1 WHERE id=?", jid),
        # a second backend recovered the expired lease and claimed the job (attempt 2)
        "second_queue": lambda: fx.sql(
            "UPDATE jobs SET worker_id='other-backend', attempt_count=attempt_count+1, "
            "lease_expires_at=? WHERE id=?", "2999-01-01 00:00:00.000000", jid),
    }
    _just_before_publication(monkeypatch, actions[race])
    why = fx.refusals(monkeypatch)
    q = fx.queue()
    q.HEARTBEAT_SECONDS = 60                   # the fence must not be what catches it
    task = await fx.start(q, jid)
    await fx.settle(task)
    assert (await fx.project(pid)).analysis_generation is None
    assert await fx.score_jobs(pid) == []
    await fx.until(lambda: fx.gen_dirs(pid) == [], "the refused attempt's own cleanup")
    _flat_untouched(pid)
    assert why == [{"db_cancel": "analysis not published: the job is no longer running",
                    "flag_only": "analysis not published: the job was cancelled",
                    "lease_expired": "analysis not published: the lease expired",
                    "attempt_bumped": "analysis not published: attempt 2 is running, not 1",
                    "second_queue": "analysis not published: another worker owns the job"}[race]]


async def test_two_queues_the_stale_attempt_is_refused_and_the_new_owner_publishes(monkeypatch):
    """A real second JobQueue takes the expired lease and runs attempt 2 while attempt 1 is held."""
    pid, jid = "ow1twoq", "ow1twoqj"
    fakes = fx.Fakes(monkeypatch)
    fakes.hold_signals[1] = threading.Event()
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    why = fx.refusals(monkeypatch)
    qa, qb = fx.queue(), fx.queue()
    qa.HEARTBEAT_SECONDS = 60                  # A never notices: its thread runs on to publication
    ta = await fx.start(qa, jid)
    await fx.until(fakes.entered.setdefault(1, threading.Event()).is_set, "attempt 1 held")
    await fx.asql("UPDATE jobs SET lease_expires_at=? WHERE id=?", fx.lease_past(), jid)
    await qb.recover_stuck_jobs()
    tb = await fx.start(qb, jid)                # attempt 2, on the other queue
    await fx.settle(tb)
    g2 = ag.gen_id_for(jid, 2)
    await _published_whole(pid, g2)
    fakes.hold_signals[1].set()                 # attempt 1 reaches its publication now
    await fx.settle(ta)
    assert ta.done() and (await fx.project(pid)).analysis_generation == g2
    await fx.until(lambda: fx.gen_dirs(pid) == [g2], "attempt 1 cleaned up its own files only")
    await _published_whole(pid, g2)
    # attempt 2 already finished, so the first check that refuses is the status one
    assert why == ["analysis not published: the job is no longer running"]


# ── 3. failure after partial preparation, and inside the transaction ──────────────────────────────

async def test_a_failure_after_partial_preparation_selects_nothing(monkeypatch):
    pid = "ow1fail"
    fakes = fx.Fakes(monkeypatch)
    fakes.content_type_error = RuntimeError("classifier exploded")
    await fx.make_project(pid)
    await fx.add_job(pid, "ow1failj", day=1)
    task = await fx.start(fx.queue(), "ow1failj")
    await fx.settle(task)
    assert (await fx.job("ow1failj")).status == "failed"
    assert (await fx.project(pid)).analysis_generation is None and await fx.score_jobs(pid) == []
    await fx.until(lambda: fx.gen_dirs(pid) == [], "the partial generation is removed")


async def test_a_failure_inside_the_transaction_keeps_the_previous_generation(monkeypatch):
    pid = "ow1txn"
    fakes = fx.Fakes(monkeypatch)
    await fx.make_project(pid, status="ready", generation="ow1old-a1")
    old = fx.seed_generation(pid, "ow1old-a1")

    async def boom(session, **kwargs):
        raise RuntimeError("enqueue failed inside the publish transaction")
    monkeypatch.setattr(pub, "add_job", boom)
    await fx.add_job(pid, "ow1txnj", day=1)
    task = await fx.start(fx.queue(), "ow1txnj")
    await fx.settle(task)
    row = await fx.project(pid)
    assert row.analysis_generation == "ow1old-a1", "the rolled-back pointer did not move"
    assert row.content_type is None, "the project update rolled back with it"
    assert await fx.score_jobs(pid) == []
    await fx.until(lambda: fx.gen_dirs(pid) == ["ow1old-a1"], "only the new, unpublished one removed")
    assert fx.verified(pid, "ow1old-a1").manifest == old.manifest


# ── 4. the normal path ────────────────────────────────────────────────────────────────────────────

async def test_the_normal_path_selects_the_whole_generation_and_schedules_scoring_once(monkeypatch):
    pid = "ow1norm"
    fx.Fakes(monkeypatch)
    await fx.make_project(pid)
    storage.write_artifact(pid, "meta", {"analysis_version": "x", "source": {"filesize": 1}})
    meta = storage.paths(pid)["meta"].read_bytes()
    await fx.add_job(pid, "ow1normj", day=1)
    task = await fx.start(fx.queue(), "ow1normj")
    await fx.settle(task)
    gen = ag.gen_id_for("ow1normj", 1)
    assert (await fx.job("ow1normj")).status == "done"
    await _published_whole(pid, gen)
    doc = fx.verified(pid, gen).manifest
    assert (doc["content_type"], doc["frames_sampled"]) == ({"content_type": "gaming", "confidence": 0.9}, 8)
    assert doc["inputs"]["transcript"]["row_id"] and doc["attempt_count"] == 1
    assert storage.paths(pid)["meta"].read_bytes() == meta, "meta.json stays ingest's"


async def test_the_same_generation_published_twice_schedules_one_score(monkeypatch):
    pid = "ow1twice"
    fx.Fakes(monkeypatch)
    await fx.make_project(pid)
    seen = {}
    real = pub.publish

    async def keep(queue, cap, project_id, gen_id, detected, **k):
        seen.update(queue=queue, cap=cap, gen=gen_id, detected=detected)
        await real(queue, cap, project_id, gen_id, detected, **k)
    monkeypatch.setattr(pub, "publish", keep)
    await fx.add_job(pid, "ow1twicej", day=1)
    q = fx.queue()
    await fx.settle(await fx.start(q, "ow1twicej"))
    assert await fx.score_jobs(pid) == [{"launched_by": "pipeline", "generation": seen["gen"]}]
    # Put the row back as the owner's live attempt: only the pointer can refuse now.
    await fx.asql("UPDATE jobs SET status='running', worker_id=?, lease_expires_at=? WHERE id=?",
           q.worker_id, "2999-01-01 00:00:00.000000", "ow1twicej")
    with pytest.raises(pub.PublishRefused, match="already published"):
        await real(seen["queue"], seen["cap"], pid, seen["gen"], seen["detected"])
    assert await fx.score_jobs(pid) == [{"launched_by": "pipeline", "generation": seen["gen"]}]


# ── 5. the cooperative stop: progress and launches halt within a measured bound ───────────────────

async def test_the_stop_halts_frame_launches_within_one_grab(monkeypatch):
    pid = "ow1stop"
    fakes = fx.Fakes(monkeypatch, frames=40)
    fakes.frame_delay = 0.05                    # one ffmpeg launch = 50 ms here
    await fx.make_project(pid)
    await fx.add_job(pid, "ow1stopj", day=1)
    q = fx.queue()
    task = await fx.start(q, "ow1stopj")
    await fx.until(lambda: len(fakes.launches) >= 3, "frame grabs under way")
    stopped_at = time.monotonic()
    await q.cancel_job("ow1stopj")
    await fx.settle(task)
    await fx.until(lambda: fx.gen_dirs(pid) == [], "the grab thread ended and cleaned up")
    after = [t for t in fakes.launches if t > stopped_at]
    ended = time.monotonic() - stopped_at
    assert len(after) <= 1, f"{len(after)} ffmpeg launches after the stop"
    assert len(fakes.launches) < 40
    assert ended < 1.0, f"the thread ran {ended:.2f}s past the stop"
    assert (await fx.project(pid)).analysis_generation is None


# ── next-24: new upstream during analyze ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("upstream", ["newer_ingest", "newer_analyze", "running_transcribe",
                                      "transcript_edited"])
async def test_new_upstream_during_analyze_refuses_publication(monkeypatch, upstream):
    pid = f"ow1up_{upstream}"
    fakes = fx.Fakes(monkeypatch)
    fakes.hold_signals[1] = threading.Event()
    why = fx.refusals(monkeypatch)
    await fx.make_project(pid)
    await fx.add_job(pid, f"{pid}j", day=1)
    task = await fx.start(fx.queue(), f"{pid}j")
    await fx.until(fakes.entered.setdefault(1, threading.Event()).is_set, "analyze held")
    if upstream == "newer_ingest":             # queued, no analyze job of its own yet
        await fx.add_job(pid, f"{pid}i", job_type="clipper_ingest", day=2)
    elif upstream == "newer_analyze":          # not active: only its creation order supersedes
        await fx.add_job(pid, f"{pid}a", day=2, status="failed")
    elif upstream == "running_transcribe":
        await fx.add_job(pid, f"{pid}t", job_type="clipper_transcribe", day=1, status="running")
        await fx.asql("UPDATE jobs SET created_at='1989-12-31 00:00:00.000000' WHERE id=?", f"{pid}t")
    else:                                       # no job at all: the captured identity catches it
        await fx.asql("UPDATE transcripts SET segments=? WHERE project_id=?", "[]", pid)
    fakes.hold_signals[1].set()
    await fx.settle(task)
    assert (await fx.project(pid)).analysis_generation is None
    assert await fx.score_jobs(pid) == []
    await fx.until(lambda: fx.gen_dirs(pid) == [], "the refused attempt's cleanup")
    expect = {"newer_ingest": f"job {pid}i supersedes", "newer_analyze": f"job {pid}a supersedes",
              "running_transcribe": f"job {pid}t supersedes",
              "transcript_edited": "the inputs this attempt read changed"}[upstream]
    assert len(why) == 1 and expect in why[0], why


async def test_a_stop_is_never_the_whole_file_regions_fallback(monkeypatch):
    from services.clipper import attempt_stop, content_type

    pid = "ow1rbs"
    fx.Fakes(monkeypatch)

    def stopped(*a):
        raise attempt_stop.Stopped("stop")
    monkeypatch.setattr(content_type, "detect_regions_by_range", stopped)
    await fx.make_project(pid)
    await fx.add_job(pid, "ow1rbsj", day=1)
    task = await fx.start(fx.queue(), "ow1rbsj")
    await fx.settle(task)
    assert (await fx.job("ow1rbsj")).status == "failed"
    assert (await fx.project(pid)).analysis_generation is None and await fx.score_jobs(pid) == []
    await asyncio.sleep(0)


# ── the stop is set on EVERY way out, and never by reusing another attempt's directory ────────────

async def test_ownership_loss_sets_the_stop(monkeypatch):
    pid, jid = "ow1lost", "ow1lostj"
    fakes = fx.Fakes(monkeypatch)
    fakes.hold_signals[1] = threading.Event()
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    q = fx.queue()
    task = await fx.start(q, jid)
    await fx.until(fakes.entered.setdefault(1, threading.Event()).is_set, "attempt 1 held")
    await fx.asql("UPDATE jobs SET worker_id='AD3H-TAKEOVER', attempt_count=attempt_count+1 WHERE id=?", jid)
    await fx.until(fakes.stops_seen[1].is_set, "the heartbeat fence reached the thread")
    fakes.hold_signals[1].set()
    await fx.settle(task)
    assert (await fx.project(pid)).analysis_generation is None


async def test_the_handler_sets_its_stop_on_the_way_out(monkeypatch):
    pid, jid = "ow1out", "ow1outj"
    fakes = fx.Fakes(monkeypatch)
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    q = fx.queue()
    await fx.settle(await fx.start(q, jid))
    assert (await fx.job(jid)).status == "done"
    assert fakes.stops_seen[1].is_set(), "a finished attempt's threads are told to stop too"
    assert (jid, 1) not in q.__dict__.get("_stop_tokens", {})


async def test_an_existing_directory_for_this_attempt_is_never_reused(monkeypatch):
    pid, jid = "ow1exist", "ow1existj"
    fx.Fakes(monkeypatch)
    await fx.make_project(pid)
    squatter = ag.gen_dir(pid, ag.gen_id_for(jid, 1))
    squatter.mkdir(parents=True)
    (squatter / "signals.json").write_text('{"someone": "else"}')
    await fx.add_job(pid, jid, day=1)
    await fx.settle(await fx.start(fx.queue(), jid))
    assert (await fx.job(jid)).status == "failed"
    assert (squatter / "signals.json").read_text() == '{"someone": "else"}'
    assert (await fx.project(pid)).analysis_generation is None and await fx.score_jobs(pid) == []
