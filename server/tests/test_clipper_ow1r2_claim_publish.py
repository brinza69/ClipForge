"""OW1r2 (codex-verdict-next-27 §4 R1, R2): the analysis attempt's identity is its CLAIM, and a
publication whose COMMIT was sent is never deleted.

R1 — `next27-check/ow1-capture-result.json`: a takeover before `capture` made attempt 1 read the row,
take attempt 2's number and publish `...-a2` with a score. Here the takeover happens while attempt 1
is held just before `capture`, on the same queue (its own retry) and on a second queue.
R2 — `next27-check/ow1-commit-result.json`: a `CancelledError` right after a successful COMMIT left
the pointer selected and the score scheduled while `finally` deleted the generation.

Through the real JobQueue + handle_analyze; fakes and helpers in ow1_fixtures.py.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from services.clipper import analysis_generation as ag
from workers import clipper_analysis_publish as pub
from workers import clipper_pipeline
from tests import ow1_fixtures as fx
from tests.ow1_fixtures import retire_ow1_jobs  # noqa: F401  (autouse)


def _hold_capture(monkeypatch) -> dict:
    """Each attempt stops just before `capture`, on its claim's attempt number, until released."""
    gates: dict[int, tuple[asyncio.Event, asyncio.Event]] = {}
    real = pub.capture

    async def held(queue, job_id, project_id):
        claimed = CLAIMED_ATTEMPT.get()
        entered, go = gates.setdefault(claimed.attempt, (asyncio.Event(), asyncio.Event()))
        entered.set()
        await go.wait()
        return await real(queue, job_id, project_id)
    monkeypatch.setattr(pub, "capture", held)
    return gates


def _gate(gates, attempt):
    return gates.setdefault(attempt, (asyncio.Event(), asyncio.Event()))


# ── R1: a takeover before capture ─────────────────────────────────────────────────────────────────

async def test_a_takeover_before_capture_on_the_same_worker_publishes_only_the_new_attempt(monkeypatch):
    pid, jid = "ow1r2same", "ow1r2samej"
    fakes = fx.Fakes(monkeypatch)
    fakes.hold_signals[1] = threading.Event()    # only attempt 2 ever gets to build_signals
    gates = _hold_capture(monkeypatch)
    why = fx.refusals(monkeypatch)
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    q = fx.queue()
    q.HEARTBEAT_SECONDS = 300                  # the fence must not be what stops attempt 1
    t1 = await fx.start(q, jid)
    await _gate(gates, 1)[0].wait()
    # Another backend's recovery gave the row back; THIS queue claims it again as attempt 2.
    await fx.asql("UPDATE jobs SET status='queued', worker_id=NULL, lease_expires_at=NULL WHERE id=?", jid)
    t2 = await fx.start(q, jid)
    assert t2 is not t1
    await _gate(gates, 2)[0].wait()

    _gate(gates, 1)[1].set()                   # attempt 1 reaches capture while attempt 2 owns the row
    await fx.settle(t1)
    assert fx.gen_dirs(pid) == [], "attempt 1 refused before writing anything"
    assert (await fx.project(pid)).analysis_generation is None and await fx.score_jobs(pid) == []
    assert (jid, 1) not in q.__dict__.get("_stop_tokens", {})
    row = await fx.job(jid)
    assert (row.status, row.attempt_count) == ("running", 2), "attempt 1's refusal left attempt 2's row"

    _gate(gates, 2)[1].set()
    await fx.until(fakes.entered.setdefault(1, threading.Event()).is_set, "attempt 2 in build_signals")
    assert set(q.__dict__["_stop_tokens"]) == {(jid, 2)}, "the stop token is the claim's"
    fakes.hold_signals[1].set()
    await fx.settle(t2)
    g2 = ag.gen_id_for(jid, 2)
    assert (await fx.job(jid)).status == "done"
    assert (await fx.project(pid)).analysis_generation == g2 and fx.gen_dirs(pid) == [g2]
    assert fx.verified(pid, g2).manifest["attempt_count"] == 2
    assert await fx.score_jobs(pid) == [{"launched_by": "pipeline", "generation": g2}]
    assert why == []                           # attempt 1 never reached publication


@pytest.mark.parametrize("change", ["attempt", "worker"])
async def test_codex_probe_row_bumped_before_capture_is_refused_before_any_write(monkeypatch, change):
    """`probe_ow1.py capture`, verbatim: the same worker's row says attempt 2, no second task —
    and its twin, the row naming another worker under the same attempt number."""
    pid, jid = f"ow1r2bump{change}", f"ow1r2bump{change}j"
    fx.Fakes(monkeypatch)
    gates = _hold_capture(monkeypatch)
    why = fx.refusals(monkeypatch)
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    q = fx.queue()
    q.HEARTBEAT_SECONDS = 300
    t1 = await fx.start(q, jid)
    await _gate(gates, 1)[0].wait()
    await fx.asql({"attempt": "UPDATE jobs SET attempt_count=2 WHERE id=?",
                   "worker": "UPDATE jobs SET worker_id='other-backend' WHERE id=?"}[change], jid)
    _gate(gates, 1)[1].set()
    await fx.settle(t1)
    assert fx.gen_dirs(pid) == [] and why == [], "refused at capture, before any write"
    assert not ag.gen_dir(pid, ag.gen_id_for(jid, 2)).exists()
    assert (await fx.project(pid)).analysis_generation is None and await fx.score_jobs(pid) == []


async def test_a_takeover_before_capture_by_another_queue_publishes_only_its_attempt(monkeypatch):
    pid, jid = "ow1r2other", "ow1r2otherj"
    fx.Fakes(monkeypatch)
    gates = _hold_capture(monkeypatch)
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    qa, qb = fx.queue(), fx.queue()
    qa.HEARTBEAT_SECONDS = 300
    ta = await fx.start(qa, jid)
    await _gate(gates, 1)[0].wait()
    await fx.asql("UPDATE jobs SET lease_expires_at=? WHERE id=?", fx.lease_past(), jid)
    await qb.recover_stuck_jobs()
    tb = await fx.start(qb, jid)               # attempt 2, on the other queue
    await _gate(gates, 2)[0].wait()
    _gate(gates, 1)[1].set()
    await fx.settle(ta)
    assert fx.gen_dirs(pid) == [], "the stale attempt refused before writing anything"
    row = await fx.job(jid)
    assert (row.status, row.worker_id, row.attempt_count) == ("running", qb.worker_id, 2)
    _gate(gates, 2)[1].set()
    await fx.settle(tb)
    g2 = ag.gen_id_for(jid, 2)
    assert (await fx.project(pid)).analysis_generation == g2 and fx.gen_dirs(pid) == [g2]
    assert await fx.score_jobs(pid) == [{"launched_by": "pipeline", "generation": g2}]


@pytest.mark.parametrize("claim", ["none", "other_job", "other_worker"])
async def test_without_this_jobs_claim_the_handler_refuses_before_any_write(monkeypatch, claim):
    pid, jid = f"ow1r2nc_{claim}", f"ow1r2nc_{claim}j"
    fx.Fakes(monkeypatch)
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    q = fx.queue()
    await fx.asql("UPDATE jobs SET status='running', worker_id=?, attempt_count=1, lease_expires_at=? "
                  "WHERE id=?", q.worker_id, "2999-01-01 00:00:00.000000", jid)
    given = {"none": None, "other_job": ClaimedAttempt("another-job", 1, q.worker_id),
             "other_worker": ClaimedAttempt(jid, 1, "someone-else")}[claim]
    token = CLAIMED_ATTEMPT.set(given)
    try:
        with pytest.raises(pub.PublishRefused, match="no claimed attempt"):
            await clipper_pipeline.handle_analyze(job_id=jid, project_id=pid, clip_id=None,
                                                  metadata={}, queue=q)
    finally:
        CLAIMED_ATTEMPT.reset(token)
    assert fx.gen_dirs(pid) == [] and not q.__dict__.get("_stop_tokens")
    assert (await fx.project(pid)).analysis_generation is None and await fx.score_jobs(pid) == []


# ── R2: what a COMMIT that was sent leaves behind ─────────────────────────────────────────────────

def _around_publication_commit(monkeypatch, job_id: str, after) -> list:
    """Run `after(session)` around the COMMIT of `job_id`'s publication transaction only: the
    session `publish` adds the score job in, never a heartbeat's or a progress write's."""
    real_publish, real_commit, real_add = pub.publish, AsyncSession.commit, pub.add_job
    mine, armed, fired = [], [], []

    async def arming(queue, cap, *a, **k):
        if cap.job_id == job_id:
            mine.append(True)
        return await real_publish(queue, cap, *a, **k)

    async def adding(session, **k):
        if mine:
            mine.clear()
            armed.append(session)
        return await real_add(session, **k)

    async def commit(session):
        if not armed or armed[0] is not session:
            return await real_commit(session)
        armed.clear()
        fired.append(True)
        return await after(session, real_commit)
    monkeypatch.setattr(pub, "publish", arming)
    monkeypatch.setattr(pub, "add_job", adding)
    monkeypatch.setattr(AsyncSession, "commit", commit)
    return fired


async def _kept_and_scored_once(pid: str, gen: str) -> None:
    assert (await fx.project(pid)).analysis_generation == gen
    assert gen in fx.gen_dirs(pid), "a committed generation was deleted"
    ctx = fx.verified(pid, gen)
    assert ctx.read("signals")["vocal_bursts"] == [0.0, 0.5]
    assert await fx.score_jobs(pid) == [{"launched_by": "pipeline", "generation": gen}]


@pytest.mark.parametrize("then", ["cancel", "error"])
async def test_a_cancel_or_error_right_after_a_successful_commit_keeps_the_generation(monkeypatch, caplog,
                                                                                      then):
    pid, jid = f"ow1r2ac{then[:3]}", f"ow1r2ac{then[:3]}j"
    fx.Fakes(monkeypatch)

    async def after(session, real_commit):
        await real_commit(session)             # the publication IS committed...
        raise asyncio.CancelledError() if then == "cancel" else RuntimeError("after the commit")
    fired = _around_publication_commit(monkeypatch, jid, after)
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    await fx.settle(await fx.start(fx.queue(), jid))
    assert fired == [True]
    await _kept_and_scored_once(pid, ag.gen_id_for(jid, 1))   # ...so nothing may delete it
    assert "without a known outcome" in caplog.text           # the caller never saw it succeed
    assert (await fx.job(jid)).status == {"cancel": "cancelled", "error": "failed"}[then]


async def test_the_callers_cancel_during_the_commit_waits_for_it_and_keeps_the_generation(monkeypatch,
                                                                                          caplog):
    pid, jid = "ow1r2caller", "ow1r2callerj"
    fx.Fakes(monkeypatch)
    q = fx.queue()
    seen = {}

    async def after(session, real_commit):
        q._running_jobs[jid].cancel()          # the person's cancel lands mid-COMMIT
        await asyncio.sleep(0)
        seen["handler_done_before_commit"] = seen["task"].done()
        await real_commit(session)
    _around_publication_commit(monkeypatch, jid, after)
    await fx.make_project(pid)
    await fx.add_job(pid, jid, day=1)
    seen["task"] = await fx.start(q, jid)
    await fx.settle(seen["task"])
    assert seen["handler_done_before_commit"] is False, "the handler waited for the protected commit"
    # its `finally` ran only after the commit ENDED, so it knew the outcome: nothing "unknown" kept
    assert "without a known outcome" not in caplog.text
    assert (await fx.job(jid)).status == "cancelled"
    await _kept_and_scored_once(pid, ag.gen_id_for(jid, 1))


@pytest.mark.parametrize("fault", ["error", "cancel"])
async def test_a_fault_proven_before_the_commit_rolls_back_and_removes_the_generation(monkeypatch, fault):
    pid, jid = f"ow1r2rb{fault[:3]}", f"ow1r2rb{fault[:3]}j"
    fx.Fakes(monkeypatch)

    async def boom(session, **kwargs):         # inside the transaction, before its COMMIT
        raise asyncio.CancelledError() if fault == "cancel" else RuntimeError("before the commit")
    monkeypatch.setattr(pub, "add_job", boom)
    await fx.make_project(pid, status="ready", generation="ow1r2prev-a1")
    fx.seed_generation(pid, "ow1r2prev-a1")
    await fx.add_job(pid, jid, day=1)
    await fx.settle(await fx.start(fx.queue(), jid))
    assert (await fx.project(pid)).analysis_generation == "ow1r2prev-a1"
    assert await fx.score_jobs(pid) == []
    await fx.until(lambda: fx.gen_dirs(pid) == ["ow1r2prev-a1"], "the proven-unpublished one removed")


async def test_g1_committed_then_replaced_by_g2_before_its_cleanup_stays_readable(monkeypatch):
    """"Not the current pointer" is not "never published": a reader pinned to G1 still reads it."""
    pid, j1, j2 = "ow1r2g1g2", "ow1r2g1g2a", "ow1r2g1g2b"
    fx.Fakes(monkeypatch)
    q = fx.queue()
    g1, g2 = ag.gen_id_for(j1, 1), ag.gen_id_for(j2, 1)
    pinned = {}

    async def after(session, real_commit):
        await real_commit(session)                               # G1 published...
        pinned["ctx"] = fx.verified(pid, g1)                     # ...a score pins it...
        await fx.add_job(pid, j2, day=2)
        await fx.settle(await fx.start(q, j2))                   # ...G2 replaces it...
        assert (await fx.project(pid)).analysis_generation == g2
        raise asyncio.CancelledError()                           # ...then G1's handler is cut short
    _around_publication_commit(monkeypatch, j1, after)
    await fx.make_project(pid)
    await fx.add_job(pid, j1, day=1)
    await fx.settle(await fx.start(q, j1))
    assert fx.gen_dirs(pid) == sorted([g1, g2])
    assert pinned["ctx"].read("signals")["vocal_bursts"] == [0.0, 0.5], "the G1 reader still reads G1"
    assert fx.verified(pid, g1).manifest == pinned["ctx"].manifest
    assert (await fx.project(pid)).analysis_generation == g2
    assert sorted(m["generation"] for m in await fx.score_jobs(pid)) == sorted([g1, g2])
