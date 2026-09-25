"""A per-CLIP answer to "does the source already carry burned captions".

PRP: PRPs/clipper-clip-caption-source-2026-09-24.md. `caption_policy.decide`
already has a project-level suite (test_clipper_caption_policy.py) that this
file does not repeat; it only covers the new `clip_setting` parameter, the new
column, and the new endpoint.

The "dict identical to the pre-change function" cases below are written out
literally rather than computed by calling `decide()` — the whole point is to
pin today's behaviour independently of the function that produces it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from services.clipper import caption_policy as cp
from services.clipper import source_captions as scap

SCHEMA = "clipper_caption_policy_v1"


def _detector(state: str, calibrated: bool = False) -> dict:
    return {"state": state, "calibrated": calibrated}


# ---------------------------------------------------------------------------
# decide(): a clip's answer wins over the project's
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("project_setting", [True, False, None])
def test_clip_true_suppresses_regardless_of_project(project_setting):
    got = cp.decide(project_setting, clip_setting=True)
    assert got["action"] == cp.SUPPRESS
    assert got["decided_by"] == cp.HUMAN
    assert got["scope"] == "clip"
    assert got["why"] == "a_person_declared_this_clip_already_carries_captions"


@pytest.mark.parametrize("project_setting", [True, False, None])
def test_clip_false_burns_regardless_of_project(project_setting):
    got = cp.decide(project_setting, clip_setting=False)
    assert got["action"] == cp.BURN
    assert got["decided_by"] == cp.HUMAN
    assert got["scope"] == "clip"
    assert got["why"] == "a_person_declared_this_clip_carries_none"


# ---------------------------------------------------------------------------
# decide(): clip_setting=None is byte-identical to the pre-change function
# ---------------------------------------------------------------------------

_DETECTOR_PRESENT = _detector(scap.PRESENT)
_DETECTOR_ABSENT = _detector(scap.ABSENT)

_CASES = [
    # (setting, detector, expected)
    (True, None, {"schema": SCHEMA, "detector": None, "detector_calibrated": None,
                  "action": cp.SUPPRESS, "decided_by": cp.HUMAN,
                  "why": "a_person_declared_the_source_already_carries_captions"}),
    (True, _DETECTOR_PRESENT,
     {"schema": SCHEMA, "detector": scap.PRESENT, "detector_calibrated": False,
      "action": cp.SUPPRESS, "decided_by": cp.HUMAN,
      "why": "a_person_declared_the_source_already_carries_captions"}),
    (True, _DETECTOR_ABSENT,
     {"schema": SCHEMA, "detector": scap.ABSENT, "detector_calibrated": False,
      "action": cp.SUPPRESS, "decided_by": cp.HUMAN,
      "why": "a_person_declared_the_source_already_carries_captions"}),
    (False, None, {"schema": SCHEMA, "detector": None, "detector_calibrated": None,
                   "action": cp.BURN, "decided_by": cp.HUMAN,
                   "why": "a_person_declared_the_source_carries_none"}),
    (False, _DETECTOR_PRESENT,
     {"schema": SCHEMA, "detector": scap.PRESENT, "detector_calibrated": False,
      "action": cp.BURN, "decided_by": cp.HUMAN,
      "why": "a_person_declared_the_source_carries_none"}),
    (False, _DETECTOR_ABSENT,
     {"schema": SCHEMA, "detector": scap.ABSENT, "detector_calibrated": False,
      "action": cp.BURN, "decided_by": cp.HUMAN,
      "why": "a_person_declared_the_source_carries_none"}),
    (None, None, {"schema": SCHEMA, "detector": None, "detector_calibrated": None,
                  "action": cp.BURN, "decided_by": cp.DEFAULT,
                  "why": "nobody_has_declared_the_source"}),
    (None, _DETECTOR_PRESENT,
     {"schema": SCHEMA, "detector": scap.PRESENT, "detector_calibrated": False,
      "action": cp.BURN, "decided_by": cp.DEFAULT,
      "why": ("the_detector_says_the_source_has_captions_and_nobody_"
              "has_confirmed_it")}),
    (None, _DETECTOR_ABSENT,
     {"schema": SCHEMA, "detector": scap.ABSENT, "detector_calibrated": False,
      "action": cp.BURN, "decided_by": cp.DEFAULT,
      "why": "nobody_has_declared_the_source"}),
]


@pytest.mark.parametrize("setting,detector,expected", _CASES)
def test_clip_setting_none_matches_the_pre_change_function(setting, detector, expected):
    got = cp.decide(setting, detector, clip_setting=None)
    assert got == expected
    assert "scope" not in got


# ---------------------------------------------------------------------------
# decide(): only a real bool counts as a clip answer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [1, "true", "false", 0, "", [], {}])
def test_clip_setting_that_is_not_a_bool_is_never_a_decision(bad):
    with_bad = cp.decide(None, clip_setting=bad)
    without = cp.decide(None, clip_setting=None)
    assert with_bad == without
    assert "scope" not in with_bad


# ---------------------------------------------------------------------------
# `_decide_render`: a clip's own True suppresses with scope "clip"
# ---------------------------------------------------------------------------

@pytest.fixture
def _bound_plan(tmp_path):
    from services.clipper.reaction_edit import BINDING_SCHEMA, compute_source_version
    from services.clipper.reaction_layout import plan_reaction_layout

    src = tmp_path / "source.mp4"
    src.write_bytes(b"source guard for planning test")
    plan = plan_reaction_layout({"x": 0, "y": 0, "w": 100, "h": 180},
                                {"x": 0, "y": 0, "w": 84, "h": 60}, 320, 180)
    plan["reaction_binding"] = {
        "schema": BINDING_SCHEMA, "source_version": compute_source_version(str(src), 320, 180),
        "source_start": 0., "source_end": 4., "src_w": 320, "src_h": 180, "by": "human"}
    return plan, str(src)


def _render_objects(plan, src, caption_plan, *, clip_setting=None, **settings):
    clip = SimpleNamespace(
        id="caption-source-probe", project_id="probe", start_time=0., end_time=4.,
        duration=4., layout_plan=plan, caption_plan=caption_plan, transcript_text="",
        headline_text="", content_type=None, content_confidence=None,
        content_type_origin=None, status="candidate", overall_score=None,
        sub_scores=None, score_reason=None, selection_run_id=None, ranker_version=None,
        source_has_burned_captions=clip_setting)
    project = SimpleNamespace(
        id="probe", width=320, height=180, video_path=src, fps=24.,
        clipper_settings={"trim_silence": False, "dynamic_edit": False, **settings},
        source_url=None, content_type=None, content_confidence=None,
        content_type_override=None, analysis_version=None)
    return clip, project


async def test_decide_render_honours_a_clip_true_over_a_default_project(
    _bound_plan, tmp_path
):
    from workers.clipper_render_plan import _decide_render

    plan, src = _bound_plan
    caption_plan = {"chunks": [{"text": "irrelevant", "start": 0., "end": 1.}]}
    clip, project = _render_objects(plan, src, caption_plan, clip_setting=True)
    decision = await _decide_render(clip, project, tmp_path)
    assert decision["caption_policy"]["action"] == cp.SUPPRESS
    assert decision["caption_policy"]["scope"] == "clip"
    assert decision["ass_path"] is None


# ---------------------------------------------------------------------------
# PUT /api/clipper/clips/{clip_id}/caption-source
# ---------------------------------------------------------------------------

async def _make_clip(status: str = "candidate", **clip_kwargs) -> tuple[str, str]:
    from database import async_session
    from models import ClipModel, ProjectModel

    pid = "capsrc-" + uuid.uuid4().hex[:8]
    cid = pid + "-c"
    async with async_session() as s:
        s.add(ProjectModel(id=pid, title="caption source", source_kind="file", status="ready"))
        s.add(ClipModel(id=cid, project_id=pid, title="t", start_time=0.0, end_time=4.0,
                        duration=4.0, status=status, **clip_kwargs))
        await s.commit()
    return pid, cid


async def test_put_true_then_false_then_null_each_200_and_stored(client):
    _pid, cid = await _make_clip()
    url = f"/api/clipper/clips/{cid}/caption-source"

    r = await client.put(url, json={"source_has_burned_captions": True})
    assert r.status_code == 200, r.text
    assert r.json()["clip"]["source_has_burned_captions"] is True

    r = await client.put(url, json={"source_has_burned_captions": False})
    assert r.status_code == 200, r.text
    assert r.json()["clip"]["source_has_burned_captions"] is False

    r = await client.put(url, json={"source_has_burned_captions": None})
    assert r.status_code == 200, r.text
    assert r.json()["clip"]["source_has_burned_captions"] is None


async def test_render_is_invalidated_only_on_a_real_change(client):
    from database import async_session
    from models import ClipModel

    _pid, cid = await _make_clip(
        export_path="/tmp/old.mp4", preview_path="/tmp/old_preview.mp4",
        source_has_burned_captions=True,
    )
    url = f"/api/clipper/clips/{cid}/caption-source"

    # Same value: no write, no invalidation.
    r = await client.put(url, json={"source_has_burned_captions": True})
    assert r.status_code == 200, r.text
    body = r.json()["clip"]
    assert body["export_path"] == "/tmp/old.mp4"
    assert body["preview_path"] == "/tmp/old_preview.mp4"

    # Real change: invalidated.
    r = await client.put(url, json={"source_has_burned_captions": False})
    assert r.status_code == 200, r.text
    body = r.json()["clip"]
    assert body["export_path"] is None
    assert body["preview_path"] is None

    async with async_session() as s:
        row = await s.get(ClipModel, cid)
    assert row.source_has_burned_captions is False


@pytest.mark.parametrize("bad_value", [1, "true"])
async def test_put_with_a_non_bool_value_is_422_and_writes_nothing(client, bad_value):
    from database import async_session
    from models import ClipModel

    _pid, cid = await _make_clip()
    url = f"/api/clipper/clips/{cid}/caption-source"
    r = await client.put(url, json={"source_has_burned_captions": bad_value})
    assert r.status_code == 422, r.text
    async with async_session() as s:
        row = await s.get(ClipModel, cid)
    assert row.source_has_burned_captions is None


async def test_put_with_the_key_missing_is_422_and_writes_nothing(client):
    from database import async_session
    from models import ClipModel

    _pid, cid = await _make_clip()
    url = f"/api/clipper/clips/{cid}/caption-source"
    r = await client.put(url, json={})
    assert r.status_code == 422, r.text
    async with async_session() as s:
        row = await s.get(ClipModel, cid)
    assert row.source_has_burned_captions is None


async def test_put_while_exporting_is_409_and_writes_nothing(client):
    from database import async_session
    from models import ClipModel

    _pid, cid = await _make_clip(status="exporting")
    url = f"/api/clipper/clips/{cid}/caption-source"
    r = await client.put(url, json={"source_has_burned_captions": True})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "export_in_progress"
    async with async_session() as s:
        row = await s.get(ClipModel, cid)
    assert row.source_has_burned_captions is None


async def test_put_unknown_clip_is_404(client):
    r = await client.put("/api/clipper/clips/does-not-exist/caption-source",
                         json={"source_has_burned_captions": True})
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# A clip whose own layer is suppressed is no longer refused for a caption gap
# ---------------------------------------------------------------------------

def _caption_plan() -> dict:
    return {"preset_id": "bold_impact", "position": "bottom", "y_pct": 0.75,
            "scale": 1.0, "entry_pop": False,
            "style": {"font_size": 72, "outline_width": 5, "shadow_offset": 2.5},
            "chunks": [{"text": "YOU PLAY FORTNITE", "start": 0.0, "end": 1.0}]}


async def test_reaction_put_on_a_suppressed_clip_ignores_the_caption_gap(client, tmp_path):
    from database import async_session
    from models import ClipModel, ProjectModel
    from services.clipper.reaction_edit import compute_source_version

    src = tmp_path / "source.mp4"
    src.write_bytes(b"source guard for planning test")
    pid, cid = "capsrc-" + uuid.uuid4().hex[:8], None
    cid = pid + "-c"
    async with async_session() as s:
        s.add(ProjectModel(id=pid, title="caption source suppressed", source_kind="file",
                           status="ready", video_path=str(src), width=320, height=180))
        s.add(ClipModel(id=cid, project_id=pid, title="t", start_time=1.0, end_time=5.0,
                        duration=4.0, status="candidate", caption_plan=_caption_plan(),
                        source_has_burned_captions=True))
        await s.commit()

    # The exact near-square box test_clipper_reaction_caption_hint.py proves
    # 422s ("no caption gap") when the layer burns — here the layer is
    # suppressed, so the placement check must never run.
    square = {"x": 0, "y": 0, "w": 180, "h": 176}
    body = {"content_rect": square, "face_rect": {"x": 0, "y": 0, "w": 84, "h": 60},
            "source_version": compute_source_version(str(src), 320, 180),
            "source_start": 1.0, "source_end": 5.0, "src_w": 320, "src_h": 180}
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=body)
    assert r.status_code == 200, r.text


# --- independent review (Opus) ---
# The first migration test only ran init_db() on an empty file, where
# `create_all` builds the column from models.py whatever the migration list
# says, so it could not fail. These two exercise the ALTER on an existing table.

async def _init_scratch(tmp_path, before_second=None):
    """`init_db()` twice on a scratch file; `before_second(engine)` runs between.
    Returns the clip columns after the second run (never the production DB)."""
    import database
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scratch.db'}")
    original, database.engine = database.engine, eng
    try:
        await database.init_db()
        if before_second:
            await before_second(eng)
        await database.init_db()
        async with eng.connect() as conn:
            rows = (await conn.execute(text("PRAGMA table_info(clips)"))).fetchall()
        return {r[1] for r in rows}
    finally:
        database.engine = original
        await eng.dispose()


async def _drop_column(eng):
    from sqlalchemy import text
    async with eng.begin() as conn:
        await conn.execute(text("ALTER TABLE clips DROP COLUMN source_has_burned_captions"))
        rows = (await conn.execute(text("PRAGMA table_info(clips)"))).fetchall()
    assert "source_has_burned_captions" not in {r[1] for r in rows}


async def test_review_alter_path_adds_the_column_to_an_existing_table(tmp_path):
    assert "source_has_burned_captions" in await _init_scratch(tmp_path, _drop_column)


async def test_review_a_failed_alter_is_a_startup_failure(tmp_path):
    """The `_REQUIRED_MIGRATED_COLUMNS` half: an ALTER that fails must not pass."""
    from sqlalchemy import event
    async def drop_and_block(eng):
        await _drop_column(eng)
        def block(conn, cursor, statement, *a):
            if "ADD COLUMN source_has_burned_captions" in statement:
                raise RuntimeError("blocked by test")
        event.listen(eng.sync_engine, "before_cursor_execute", block)
    with pytest.raises(RuntimeError, match="clips.source_has_burned_captions"):
        await _init_scratch(tmp_path, drop_and_block)


async def _events(cid):
    from sqlalchemy import select
    from database import async_session
    from models import ClipFeedbackModel
    async with async_session() as s:
        rows = (await s.execute(select(ClipFeedbackModel)
                                .where(ClipFeedbackModel.clip_id == cid))).scalars().all()
    return [(r.event_type, r.origin, r.payload) for r in rows]


async def test_review_feedback_event_on_change_none_on_same_value(client):
    _pid, cid = await _make_clip()
    url = f"/api/clipper/clips/{cid}/caption-source"
    assert (await client.put(url, json={"source_has_burned_captions": True})).status_code == 200
    expected = [("caption_changed", "manual",
                 {"field": "source_has_burned_captions", "old": None, "new": True})]
    assert await _events(cid) == expected
    assert (await client.put(url, json={"source_has_burned_captions": True})).status_code == 200
    assert await _events(cid) == expected


async def _reaction_clip(tmp_path, clip_value, project_setting=None):
    from database import async_session
    from models import ClipModel, ProjectModel
    from services.clipper.reaction_edit import compute_source_version
    src = tmp_path / "source.mp4"
    src.write_bytes(b"source guard for planning test")
    pid = "capsrc-" + uuid.uuid4().hex[:8]
    cid, cfg = pid + "-c", {"trim_silence": False, "dynamic_edit": False}
    if project_setting is not None:
        cfg[cp.SETTING] = project_setting
    async with async_session() as s:
        s.add(ProjectModel(id=pid, title="review", source_kind="file", status="ready",
                           video_path=str(src), width=320, height=180, clipper_settings=cfg))
        s.add(ClipModel(id=cid, project_id=pid, title="t", start_time=1.0, end_time=5.0,
                        duration=4.0, status="candidate", caption_plan=_caption_plan(),
                        source_has_burned_captions=clip_value))
        await s.commit()
    body = {"content_rect": {"x": 0, "y": 0, "w": 180, "h": 176},
            "face_rect": {"x": 0, "y": 0, "w": 84, "h": 60},
            "source_version": compute_source_version(str(src), 320, 180),
            "source_start": 1.0, "source_end": 5.0, "src_w": 320, "src_h": 180}
    return pid, cid, body


@pytest.mark.parametrize("clip_value,project_setting", [(None, None), (False, True)])
async def test_review_control_same_geometry_burning_is_422(
        client, tmp_path, clip_value, project_setting):
    """Suspicion (c): without this control the suppressed 200 proves nothing.
    (False, True) also shows a clip's False overrides a project's True here."""
    _pid, cid, body = await _reaction_clip(tmp_path, clip_value, project_setting)
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "caption_placement_failed"


async def test_review_turning_the_layer_back_on_over_a_no_gap_layout(client, tmp_path):
    """Finding 2, fixed: this used to be a 200 that every preview and export then
    failed on. Now it is the reaction PUT's own refusal and height, and following
    that height gives a framing the layer can be turned on over and rendered."""
    from database import async_session
    from models import ClipModel, ProjectModel
    from services.clipper.reaction_captions import resolve_reaction_caption_y
    from workers.clipper_render_plan import _decide_render
    pid, cid, body = await _reaction_clip(tmp_path, True)
    layout = f"/api/clipper/clips/{cid}/reaction-layout"
    source = f"/api/clipper/clips/{cid}/caption-source"
    assert (await client.put(layout, json=body)).status_code == 200
    r = await client.put(source, json={"source_has_burned_captions": False})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "caption_placement_failed"
    async with async_session() as s:
        assert (await s.get(ClipModel, cid)).source_has_burned_captions is True
    body["content_rect"] = {**body["content_rect"], "h": detail["max_content_height"]}
    assert (await client.put(layout, json=body)).status_code == 200
    r = await client.put(source, json={"source_has_burned_captions": False})
    assert r.status_code == 200, r.text
    got = r.json()["clip"]
    assert got["caption_plan"]["y_pct"] == resolve_reaction_caption_y(
        got["layout_plan"], _caption_plan(), clip_duration=4.0)
    async with async_session() as s:
        clip, project = await s.get(ClipModel, cid), await s.get(ProjectModel, pid)
        decision = await _decide_render(clip, project, tmp_path)
    assert decision["caption_policy"]["action"] == cp.BURN


async def test_review_a_rescore_keeps_the_declaration(client):
    from sqlalchemy import select
    from database import async_session
    from models import ClipModel
    from workers.clipper_finalize import _write_clips
    pid, cid = await _make_clip(status="exported", export_path="/tmp/x.mp4")
    r = await client.put(f"/api/clipper/clips/{cid}/caption-source",
                         json={"source_has_burned_captions": True})
    assert r.status_code == 200, r.text
    assert r.json()["clip"]["status"] == "approved"      # demoted by invalidate_render
    cand = {"start": 0.0, "end": 4.0, "headline": "same moment", "overall": 1.0}
    await _write_clips(pid, [cand], [cand], "general", "review-run")
    async with async_session() as s:
        rows = (await s.execute(select(ClipModel)
                                .where(ClipModel.project_id == pid))).scalars().all()
    assert rows and all(c.source_has_burned_captions is True for c in rows)
