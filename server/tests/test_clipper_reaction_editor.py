"""Backend tests for the reaction layout editor (PRP clipper-reaction-editor).
Disposable generated media only. t values in GET calls are CLIP-RELATIVE.
asyncio_mode=auto in pytest.ini — no marks needed.
"""
from __future__ import annotations

import math
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def small_source(tmp_path) -> Path:
    """320×180 h264 MP4, 6 seconds, blue lavfi."""
    from services.clipper.ffmpeg_tools import ffmpeg_bin, run
    p = tmp_path / "source.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=6",
         "-c:v", "libx264", "-preset", "ultrafast", str(p)],
        what="reaction test fixture")
    return p


@pytest.fixture
async def reaction_db(small_source):
    """Project + clip rows in the test DB, source pointing at small_source."""
    from sqlalchemy import delete
    from database import async_session
    from models import ClipModel, ClipStatus, ProjectModel
    pid = "rxtest-" + uuid.uuid4().hex[:8]
    cid = pid + "-c"
    async with async_session() as s:
        await s.execute(delete(ClipModel).where(ClipModel.project_id == pid))
        await s.execute(delete(ProjectModel).where(ProjectModel.id == pid))
        s.add(ProjectModel(id=pid, title="reaction test", source_kind="file",
                           status="ready", video_path=str(small_source),
                           width=320, height=180))
        s.add(ClipModel(id=cid, project_id=pid, title="t",
                        start_time=1.0, end_time=5.0, duration=4.0,
                        status=ClipStatus.candidate.value,
                        caption_plan={"preset_id": "bold_impact",
                                      "position": "bottom", "y_pct": 0.85,
                                      "chunks": []}))
        await s.commit()
    return {"pid": pid, "cid": cid, "src": str(small_source)}


def _sv(src: str, w: int = 320, h: int = 180) -> str:
    from services.clipper.reaction_edit import compute_source_version
    return compute_source_version(src, w, h)


def _face_rect() -> dict:
    # w=84, h=60: |84 - 60*1080/768| = 0.375 ≤ 2 ✓
    return {"x": 0, "y": 0, "w": 84, "h": 60}


def _content_rect() -> dict:
    return {"x": 0, "y": 0, "w": 320, "h": 180}


def _valid_put_body(src: str, start: float = 1.0, end: float = 5.0) -> dict:
    return {
        "content_rect": _content_rect(),
        "face_rect": _face_rect(),
        "source_version": _sv(src),
        "source_start": start,
        "source_end": end,
        "src_w": 320, "src_h": 180,
    }


@pytest.fixture
def valid_binding(small_source):
    """Plan + binding valid against small_source, clip=[1.0,5.0]."""
    from services.clipper.reaction_edit import BINDING_SCHEMA, compute_source_version
    from services.clipper.reaction_layout import plan_reaction_layout
    plan = plan_reaction_layout(_content_rect(), _face_rect(), 320, 180)
    plan["reaction_binding"] = {
        "schema": BINDING_SCHEMA,
        "source_version": compute_source_version(str(small_source), 320, 180),
        "source_start": 1.0, "source_end": 5.0,
        "src_w": 320, "src_h": 180, "by": "human",
    }
    return plan, str(small_source)


# ---------------------------------------------------------------------------
# Unit: reaction_edit.py
# ---------------------------------------------------------------------------

def test_compute_source_version(small_source):
    from services.clipper.reaction_edit import compute_source_version
    v = compute_source_version(str(small_source), 320, 180)
    assert v == compute_source_version(str(small_source), 320, 180) and len(v) == 32
    assert v != compute_source_version(str(small_source), 640, 360)


def test_validate_face_aspect():
    from services.clipper.reaction_edit import validate_face_aspect
    validate_face_aspect(84, 60)   # |84 - 84.375| = 0.375 ≤ 2 ✓
    with pytest.raises(ValueError, match="aspect"):
        validate_face_aspect(100, 100)


def test_validate_binding_ok(valid_binding):
    from services.clipper.reaction_edit import validate_binding
    plan, src = valid_binding
    validate_binding(plan, src, 320, 180, 1.0, 5.0)
    validate_binding(plan, src, 320, 180, 2.0, 4.0)  # subset also valid


def test_validate_binding_expanded_raises(valid_binding):
    from services.clipper.reaction_edit import validate_binding
    plan, src = valid_binding
    with pytest.raises(RuntimeError, match="extends outside"):
        validate_binding(plan, src, 320, 180, 0.5, 6.0)


def test_validate_binding_missing_raises():
    from services.clipper.reaction_edit import validate_binding
    with pytest.raises(RuntimeError, match="no reaction_binding"):
        validate_binding({"game_content_fit": True}, "/fake", 320, 180, 1.0, 5.0)


def test_validate_binding_unknown_schema(valid_binding):
    from services.clipper.reaction_edit import validate_binding
    import copy
    plan, src = valid_binding
    bad = copy.deepcopy(plan)
    bad["reaction_binding"]["schema"] = "old_v0"
    with pytest.raises(RuntimeError, match="unknown reaction binding schema"):
        validate_binding(bad, src, 320, 180, 1.0, 5.0)


def test_validate_binding_wrong_dims(valid_binding):
    from services.clipper.reaction_edit import validate_binding
    plan, src = valid_binding
    with pytest.raises(RuntimeError, match="project source"):
        validate_binding(plan, src, 1920, 1080, 1.0, 5.0)


def test_validate_binding_stale_source(valid_binding, tmp_path):
    from services.clipper.reaction_edit import validate_binding
    plan, _ = valid_binding
    other = tmp_path / "other.mp4"
    other.write_bytes(b"z" * 100)
    with pytest.raises(RuntimeError, match="source file has changed"):
        validate_binding(plan, str(other), 320, 180, 1.0, 5.0)


def test_validate_binding_nan_window(valid_binding):
    """'NaN' string in binding source_start must be rejected."""
    import copy
    from services.clipper.reaction_edit import validate_binding
    plan, src = valid_binding
    bad = copy.deepcopy(plan)
    bad["reaction_binding"]["source_start"] = "NaN"
    with pytest.raises(RuntimeError, match="invalid window"):
        validate_binding(bad, src, 320, 180, 1.0, 5.0)


def test_validate_binding_wrong_layout(valid_binding):
    import copy
    from services.clipper.reaction_edit import validate_binding
    plan, src = valid_binding
    bad = copy.deepcopy(plan)
    bad["layout"] = "fullscreen_crop"
    with pytest.raises(RuntimeError, match="layout must be"):
        validate_binding(bad, src, 320, 180, 1.0, 5.0)


def test_validate_binding_wrong_face_pct(valid_binding):
    import copy
    from services.clipper.reaction_edit import validate_binding
    plan, src = valid_binding
    bad = copy.deepcopy(plan)
    bad["face_pct"] = 0.55
    with pytest.raises(RuntimeError, match="face_pct"):
        validate_binding(bad, src, 320, 180, 1.0, 5.0)


def test_validate_binding_plan_src_mismatch(valid_binding):
    import copy
    from services.clipper.reaction_edit import validate_binding
    plan, src = valid_binding
    bad = copy.deepcopy(plan)
    bad["src_w"] = 1920  # plan disagrees with binding
    with pytest.raises(RuntimeError, match="plan source dimensions"):
        validate_binding(bad, src, 320, 180, 1.0, 5.0)


# ---------------------------------------------------------------------------
# HTTP: GET /clips/{id}/reaction-source (t is CLIP-RELATIVE)
# ---------------------------------------------------------------------------

async def test_get_reaction_source_ok(client, reaction_db):
    cid = reaction_db["cid"]
    r = await client.get(f"/api/clipper/clips/{cid}/reaction-source?t=0.5")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/png")
    decoded_t = float(r.headers["x-source-time"])
    assert math.isfinite(decoded_t) and decoded_t >= 0
    assert r.headers["cache-control"] == "no-store"
    assert int(r.headers["x-source-width"]) == 320
    assert int(r.headers["x-source-height"]) == 180
    assert len(r.headers["x-source-version"]) == 32


async def test_get_reaction_source_time_is_absolute_in_source(client, reaction_db):
    """Decoded time must be in source clock (>= clip_start), not clip-relative."""
    cid = reaction_db["cid"]
    r = await client.get(f"/api/clipper/clips/{cid}/reaction-source?t=0.0")
    assert r.status_code == 200, r.text
    decoded_t = float(r.headers["x-source-time"])
    clip_start = float(r.headers["x-clip-start"])
    assert decoded_t >= clip_start - 0.1


async def test_get_reaction_source_negative_t_fails(client, reaction_db):
    cid = reaction_db["cid"]
    r = await client.get(f"/api/clipper/clips/{cid}/reaction-source?t=-0.1")
    assert r.status_code == 422


async def test_get_reaction_source_t_at_or_past_duration_fails(client, reaction_db):
    """t >= clip_duration must be rejected (clip_duration=4.0)."""
    cid = reaction_db["cid"]
    r = await client.get(f"/api/clipper/clips/{cid}/reaction-source?t=4.0")
    assert r.status_code in (404, 422)


async def test_get_reaction_source_unknown_clip(client):
    r = await client.get("/api/clipper/clips/noexist/reaction-source?t=0.5")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# HTTP: PUT /clips/{id}/reaction-layout
# ---------------------------------------------------------------------------

async def test_put_reaction_layout_success(client, reaction_db):
    cid, src = reaction_db["cid"], reaction_db["src"]
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                         json=_valid_put_body(src))
    assert r.status_code == 200, r.text
    clip = r.json()["clip"]
    plan = clip["layout_plan"]
    assert plan["game_content_fit"] is True
    assert plan["reaction_binding"]["schema"] == "clipper_reaction_binding_v1"
    assert plan["reaction_binding"]["by"] == "human"
    assert clip["export_path"] is None and clip["preview_path"] is None


async def test_put_reaction_layout_odd_rect_rejected(client, reaction_db):
    cid, src = reaction_db["cid"], reaction_db["src"]
    body = _valid_put_body(src)
    body["content_rect"]["w"] = 319  # odd
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=body)
    assert r.status_code == 422


async def test_put_reaction_layout_bad_face_aspect_rejected(client, reaction_db):
    cid, src = reaction_db["cid"], reaction_db["src"]
    body = _valid_put_body(src)
    body["face_rect"] = {"x": 0, "y": 0, "w": 100, "h": 100}
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=body)
    assert r.status_code == 422


async def test_put_reaction_layout_stale_version_no_mutation(client, reaction_db):
    cid, src = reaction_db["cid"], reaction_db["src"]
    body = {**_valid_put_body(src), "source_version": "stale" * 8}
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=body)
    assert r.status_code == 409
    assert (await client.get(f"/api/clipper/clips/{cid}")).json()["layout_plan"] is None


@pytest.mark.parametrize("bad", ["NaN", "Infinity", True, None])
async def test_put_reaction_layout_nonfinite_window_rejected(client, reaction_db, bad):
    cid, src = reaction_db["cid"], reaction_db["src"]
    body = _valid_put_body(src)
    body["source_start"] = bad
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=body)
    assert r.status_code >= 400


async def test_put_reaction_layout_window_mismatch_rejected(client, reaction_db):
    cid, src = reaction_db["cid"], reaction_db["src"]
    body = _valid_put_body(src)
    body["source_start"] = 2.0  # differs from clip.start_time=1.0
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=body)
    assert r.status_code == 422


async def test_put_reaction_layout_exporting_rejected(client, reaction_db):
    from database import async_session
    from models import ClipModel, ClipStatus
    cid, src = reaction_db["cid"], reaction_db["src"]
    async with async_session() as s:
        c = await s.get(ClipModel, cid)
        c.status = ClipStatus.exporting.value
        await s.commit()
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                         json=_valid_put_body(src))
    assert r.status_code == 409


async def test_put_updates_automatic_caption_y(client, reaction_db):
    from database import async_session
    from models import ClipModel
    cid, src = reaction_db["cid"], reaction_db["src"]
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                         json=_valid_put_body(src))
    assert r.status_code == 200
    async with async_session() as s:
        c = await s.get(ClipModel, cid)
    assert c.caption_plan is not None and "y_pct" in c.caption_plan


async def test_put_manual_caption_y_preserved(client, reaction_db):
    from database import async_session
    from models import ClipModel
    cid, src = reaction_db["cid"], reaction_db["src"]
    async with async_session() as s:
        c = await s.get(ClipModel, cid)
        c.caption_plan = {"preset_id": "bold_impact", "position": "bottom",
                          "y_pct": 0.99, "y_pct_manual": True, "chunks": []}
        await s.commit()
    assert (await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                             json=_valid_put_body(src))).status_code == 200
    async with async_session() as s:
        c = await s.get(ClipModel, cid)
    assert c.caption_plan["y_pct"] == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# HTTP: DELETE /clips/{id}/reaction-layout
# ---------------------------------------------------------------------------

async def test_delete_clears_reaction_plan(client, reaction_db):
    cid, src = reaction_db["cid"], reaction_db["src"]
    assert (await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                             json=_valid_put_body(src))).status_code == 200
    r = await client.delete(f"/api/clipper/clips/{cid}/reaction-layout")
    assert r.status_code == 200 and r.json()["clip"]["layout_plan"] is None


async def test_delete_no_op_if_no_reaction(client, reaction_db):
    cid = reaction_db["cid"]
    r = await client.delete(f"/api/clipper/clips/{cid}/reaction-layout")
    assert r.status_code == 200 and r.json()["clip"]["layout_plan"] is None


async def test_delete_clears_unbound_plan(client, reaction_db):
    """DELETE must clear even a plan with game_content_fit but no binding."""
    from database import async_session
    from models import ClipModel
    cid = reaction_db["cid"]
    async with async_session() as s:
        c = await s.get(ClipModel, cid)
        c.layout_plan = {"game_content_fit": True}
        await s.commit()
    r = await client.delete(f"/api/clipper/clips/{cid}/reaction-layout")
    assert r.status_code == 200 and r.json()["clip"]["layout_plan"] is None


# ---------------------------------------------------------------------------
# Worker: _layout_plan
# ---------------------------------------------------------------------------

def _make_clip(layout_plan=None, start=1.0, end=5.0):
    return SimpleNamespace(
        id="rx_ns", project_id="rx_proj",
        start_time=start, end_time=end,
        layout_plan=layout_plan, caption_plan=None,
        transcript_text="", headline_text="",
        content_type=None, content_confidence=None,
        content_type_origin=None, status="candidate",
        duration=end - start, overall_score=None,
        sub_scores=None, score_reason=None,
        selection_run_id=None, ranker_version=None,
    )


def _make_project(src: str, w=320, h=180):
    return SimpleNamespace(
        id="rx_proj", width=w, height=h, video_path=src,
        clipper_settings={}, fps=24.0, source_url=None,
        content_type=None, content_confidence=None,
        content_type_override=None, analysis_version=None,
    )


def test_layout_plan_returns_reaction_plan(valid_binding, small_source):
    from workers.clipper_render_plan import _layout_plan
    plan, _ = valid_binding
    result = _layout_plan(_make_clip(layout_plan=plan), _make_project(str(small_source)))
    assert result["game_content_fit"] is True


def test_layout_plan_raises_unbound(small_source):
    from workers.clipper_render_plan import _layout_plan
    from services.clipper.reaction_layout import plan_reaction_layout
    plan = plan_reaction_layout(_content_rect(), _face_rect(), 320, 180)
    with pytest.raises(RuntimeError, match="unbound"):
        _layout_plan(_make_clip(layout_plan=plan), _make_project(str(small_source)))


def test_layout_plan_raises_expanded_interval(valid_binding, small_source):
    from workers.clipper_render_plan import _layout_plan
    plan, _ = valid_binding
    with pytest.raises(RuntimeError, match="extends outside"):
        _layout_plan(_make_clip(layout_plan=plan, start=0.0, end=6.0),
                     _make_project(str(small_source)))


async def test_decide_render_skips_dynamic_for_reaction(valid_binding, small_source, tmp_path):
    from workers import clipper_render_plan as crp
    plan, _ = valid_binding
    called = []
    async def _fake_dyn(*a, **kw):
        called.append(True)
        return None
    with patch.object(crp, "_dynamic_plan", _fake_dyn), \
         patch.object(crp, "_dead_spans", new_callable=AsyncMock, return_value=[]):
        await crp._decide_render(_make_clip(layout_plan=plan),
                                 _make_project(str(small_source)), tmp_path)
    assert called == []


async def test_decide_render_calls_dynamic_for_normal_clip(small_source, tmp_path):
    from workers import clipper_render_plan as crp
    called = []
    async def _fake_dyn(*a, **kw):
        called.append(True)
        return None
    proj = _make_project(str(small_source))
    proj.clipper_settings = {"dynamic_edit": True}
    with patch.object(crp, "_dynamic_plan", _fake_dyn), \
         patch.object(crp, "_layout_plan", return_value={"layout": "full_frame"}), \
         patch.object(crp, "_dead_spans", new_callable=AsyncMock, return_value=[]):
        await crp._decide_render(_make_clip(), proj, tmp_path)
    assert called


# ---------------------------------------------------------------------------
# Ordinary worker render: _decide_render -> render_export
# ---------------------------------------------------------------------------

async def test_render_export_with_api_binding(client, reaction_db, tmp_path):
    """End-to-end: PUT binding via API → _decide_render → render_export.
    Asserts the sidecar carries the reaction_binding and dyn is None.
    """
    from database import async_session
    from models import ClipModel, ProjectModel
    from workers.clipper_render_output import render_export
    from workers import clipper_render_plan as crp

    cid, pid, src = reaction_db["cid"], reaction_db["pid"], reaction_db["src"]

    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                         json=_valid_put_body(src))
    assert r.status_code == 200, r.text

    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
        project = await s.get(ProjectModel, pid)

    with patch.object(crp, "_dead_spans", new_callable=AsyncMock, return_value=[]):
        decision = await crp._decide_render(clip, project, tmp_path)

    assert decision["dyn"] is None
    assert decision["plan"]["game_content_fit"] is True
    assert "reaction_binding" in decision["plan"]

    out = tmp_path / "out.mp4"
    result = await render_export(clip, project, decision, out, src=src)

    assert out.exists() and out.stat().st_size > 1000
    sidecar = result.get("sidecar") or {}
    lp = sidecar.get("layout_plan") or {}
    assert lp.get("reaction_binding") is not None, f"no binding in sidecar: {lp}"
    assert lp["reaction_binding"]["schema"] == "clipper_reaction_binding_v1"
    assert sidecar.get("render_version") is not None
