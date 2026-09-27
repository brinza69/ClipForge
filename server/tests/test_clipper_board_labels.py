"""The board's small labels (A's release notes O2–O4, 27 Sept 2026).

- O2: a rescore keeps a person's clips with the run that created them, rank included, so two cards
  could both say "#1". `from_current_run` says whether a clip belongs to the project's current run
  (the run of its newest clip); legacy clips with no run id all count as current.
- O3: a cancelled export leaves the clip `failed` (R4b), so Export stays available; `last_export`
  is the clip's latest export job, which lets the card say "cancelled" instead of "failed".
- O4: the editor accepted a 517.9 s range. PATCH now refuses a range longer than the project's
  maximum clip length, unless it is no longer than the clip already was (trimming stays allowed).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from database import async_session
from models import ClipModel, JobModel, ProjectModel

T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _project(ident, *, settings=None, duration=1000.0):
    async with async_session() as session:
        session.add(ProjectModel(id=ident, title="labels", source_kind="file", video_path="",
                                 status="ready", processing_mode="clipping", duration=duration,
                                 width=320, height=180, fps=24,
                                 clipper_settings=settings if settings is not None else {"fps": 24}))
        await session.commit()


async def _clip(ident, cid, *, run=None, rank=None, minute=0, start=0.0, end=30.0, status="candidate"):
    async with async_session() as session:
        session.add(ClipModel(id=cid, project_id=ident, title="c", start_time=start, end_time=end,
                              duration=end - start, transcript_text="said words", status=status,
                              selection_run_id=run, rank_position=rank,
                              created_at=T0 + timedelta(minutes=minute)))
        await session.commit()


async def _job(ident, jid, type_, status, minute, *, clip):
    async with async_session() as session:
        session.add(JobModel(id=jid, project_id=ident, clip_id=clip, type=type_, status=status,
                             created_at=T0 + timedelta(minutes=minute),
                             updated_at=T0 + timedelta(minutes=minute),
                             metadata_json=json.dumps({})))
        await session.commit()


async def _cards(client, ident) -> dict:
    r = await client.get(f"/api/clipper/projects/{ident}")
    assert r.status_code == 200, r.text
    return {c["id"]: c for c in r.json()["clips"]}


async def _row(cid):
    async with async_session() as session:
        c = await session.get(ClipModel, cid, populate_existing=True)
        return c.start_time, c.end_time, c.duration


# ── O2: which run a card belongs to ─────────────────────────────────────────

async def test_a_kept_clip_from_an_earlier_run_is_not_current(client):
    ident = "lblrun"
    await _project(ident)
    await _clip(ident, f"{ident}old", run="run-old", rank=1, minute=0, status="exported")
    await _clip(ident, f"{ident}n1", run="run-new", rank=1, minute=10)
    await _clip(ident, f"{ident}n2", run="run-new", rank=2, minute=10)
    cards = await _cards(client, ident)
    assert cards[f"{ident}old"]["from_current_run"] is False
    assert cards[f"{ident}n1"]["from_current_run"] is True
    assert cards[f"{ident}n2"]["from_current_run"] is True
    # Both still carry their rank; the label, not the data, changes.
    assert cards[f"{ident}old"]["rank_position"] == 1 and cards[f"{ident}n1"]["rank_position"] == 1


async def test_one_card_is_judged_against_the_whole_project(client):
    ident = "lblone"
    await _project(ident)
    await _clip(ident, f"{ident}old", run="run-old", rank=1, minute=0)
    await _clip(ident, f"{ident}new", run="run-new", rank=1, minute=5)
    r = await client.get(f"/api/clipper/clips/{ident}old")
    assert r.status_code == 200, r.text
    assert r.json()["from_current_run"] is False


async def test_legacy_clips_without_a_run_all_count_as_current(client):
    ident = "lbllegacy"
    await _project(ident)
    await _clip(ident, f"{ident}a", run=None, rank=1, minute=0)
    await _clip(ident, f"{ident}b", run=None, rank=2, minute=1)
    cards = await _cards(client, ident)
    assert all(c["from_current_run"] is True for c in cards.values())


# ── O3: the latest export attempt on the card ───────────────────────────────

async def test_the_latest_export_attempt_is_on_the_card(client):
    ident = "lblexp"
    await _project(ident)
    for i in range(3):
        await _clip(ident, f"{ident}c{i}", run="r", rank=i + 1, status="failed" if i < 2 else "candidate")
    c0, c1, c2 = (f"{ident}c{i}" for i in range(3))
    await _job(ident, f"{ident}x0", "clipper_export", "cancelled", 3, clip=c0)
    # c1: a cancelled export, then a later failed one — the latest is what the card says.
    await _job(ident, f"{ident}x1", "clipper_export", "cancelled", 1, clip=c1)
    await _job(ident, f"{ident}x2", "clipper_export", "failed", 2, clip=c1)
    await _job(ident, f"{ident}p2", "clipper_preview", "done", 4, clip=c2)
    cards = await _cards(client, ident)
    assert cards[c0]["last_export"]["status"] == "cancelled"
    assert cards[c0]["last_export"]["job_id"] == f"{ident}x0"
    assert cards[c1]["last_export"]["status"] == "failed"
    assert cards[c2]["last_export"] is None            # a preview is not an export
    assert cards[c2]["last_preview"]["status"] == "done"


# ── O4: the range a PATCH may set ───────────────────────────────────────────

async def _patch(client, cid, start, end):
    return await client.patch(f"/api/clipper/clips/{cid}", json={"start_time": start, "end_time": end})


async def test_a_range_longer_than_the_projects_maximum_is_refused(client):
    ident = "lbllong"
    await _project(ident, settings={"fps": 24, "max_clip_s": 60})
    await _clip(ident, f"{ident}c", start=100.0, end=130.0)
    r = await _patch(client, f"{ident}c", 100.0, 617.9)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error"] == "range_too_long"
    assert "60" in r.json()["detail"]["message"]
    assert await _row(f"{ident}c") == (100.0, 130.0, 30.0)       # nothing written


async def test_the_default_maximum_applies_when_the_project_sets_none(client):
    from config import settings
    ident = "lbldefault"
    for tag, cfg in (("absent", {"fps": 24}), ("bool", {"fps": 24, "max_clip_s": True}),
                     ("zero", {"fps": 24, "max_clip_s": 0}), ("text", {"fps": 24, "max_clip_s": "90"})):
        pid = f"{ident}{tag}"
        await _project(pid, settings=cfg)
        await _clip(pid, f"{pid}c", start=0.0, end=30.0)
        over = settings.clipper_max_clip_s + 1.0
        r = await _patch(client, f"{pid}c", 0.0, over)
        assert r.status_code == 400 and r.json()["detail"]["error"] == "range_too_long", (tag, r.text)
        r = await _patch(client, f"{pid}c", 0.0, settings.clipper_max_clip_s)
        assert r.status_code == 200, (tag, r.text)                   # exactly the maximum is allowed


async def test_trimming_a_clip_longer_than_the_maximum_is_allowed(client):
    ident = "lbltrim"
    await _project(ident, settings={"fps": 24, "max_clip_s": 60})
    await _clip(ident, f"{ident}c", start=0.0, end=120.0)            # predates a lower maximum
    r = await _patch(client, f"{ident}c", 0.0, 100.0)
    assert r.status_code == 200, r.text
    assert await _row(f"{ident}c") == (0.0, 100.0, 100.0)
    r = await _patch(client, f"{ident}c", 0.0, 110.0)                # longer than it now is: refused
    assert r.status_code == 400 and r.json()["detail"]["error"] == "range_too_long"


async def test_the_older_range_refusals_are_unchanged(client):
    ident = "lblold"
    await _project(ident, settings={"fps": 24, "max_clip_s": 60}, duration=500.0)
    await _clip(ident, f"{ident}c", start=100.0, end=130.0)
    r = await _patch(client, f"{ident}c", 130.0, 120.0)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_range"
    r = await _patch(client, f"{ident}c", 480.0, 510.0)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "range_past_source"
    assert await _row(f"{ident}c") == (100.0, 130.0, 30.0)


# ── codex-verdict-next-15 R1/R2: one limit, the same endpoints ──────────────

async def test_the_project_carries_the_limit_the_server_applies(client, monkeypatch):
    """R1: the editor gets the server's effective limit, with a default that is not 90."""
    from config import settings
    monkeypatch.setattr(settings, "clipper_max_clip_s", 120.0)
    ident = "lbleff"
    await _project(ident)                                        # no max_clip_s of its own
    await _clip(ident, f"{ident}c", start=0.0, end=30.0)
    r = await client.get(f"/api/clipper/projects/{ident}")
    assert r.json()["max_clip_s_effective"] == 120.0
    r = await _patch(client, f"{ident}c", 0.0, 110.0)            # a local 90 s would have refused it
    assert r.status_code == 200, r.text
    await _project(f"{ident}60", settings={"fps": 24, "max_clip_s": 60})
    r = await client.get(f"/api/clipper/projects/{ident}60")
    assert r.json()["max_clip_s_effective"] == 60.0


async def test_a_clip_already_over_the_maximum_keeps_its_range_and_edits(client):
    """R2: [0, 100.0004] stores duration 100.0 (rounded); the rule reads the endpoints."""
    ident = "lblr2"
    await _project(ident, settings={"fps": 24, "max_clip_s": 90})
    await _clip(ident, f"{ident}c", start=0.0, end=100.0004)
    r = await client.patch(f"/api/clipper/clips/{ident}c", json={"title": "only the title"})
    assert r.status_code == 200, r.text
    assert (await _patch(client, f"{ident}c", 0.0, 100.0004)).status_code == 200    # unchanged range
    assert (await _patch(client, f"{ident}c", 0.0, 100.0)).status_code == 200      # a trim
    r = await _patch(client, f"{ident}c", 0.0, 100.1)                             # longer than it was
    assert r.status_code == 400 and r.json()["detail"]["error"] == "range_too_long"
