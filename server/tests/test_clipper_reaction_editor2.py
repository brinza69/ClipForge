"""Reaction editor — resolver propagation, frozen source token, caption end-to-end.

Assumes:
- PUT and _decide_render propagate caption-resolver exceptions (no swallowing).
- _decode_source_frame uses frame.width/height/time_base; source_version
  computed inside decoder and returned as result["source_version"].
asyncio_mode=auto in pytest.ini — no marks needed.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures (independent copies — no cross-file fixture dependency)
# ---------------------------------------------------------------------------

@pytest.fixture
def small_source(tmp_path) -> Path:
    from services.clipper.ffmpeg_tools import ffmpeg_bin, run
    p = tmp_path / "source.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=6",
         "-c:v", "libx264", "-preset", "ultrafast", str(p)],
        what="reaction test fixture")
    return p


def _face_rect() -> dict:
    # w=84, h=60: |84 - 60*1080/768| = 0.375 ≤ 2 ✓
    return {"x": 0, "y": 0, "w": 84, "h": 60}


def _content_rect() -> dict:
    return {"x": 0, "y": 0, "w": 320, "h": 180}


def _sv(src: str, w: int = 320, h: int = 180) -> str:
    from services.clipper.reaction_edit import compute_source_version
    return compute_source_version(src, w, h)


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


@pytest.fixture
async def reaction_db(small_source):
    """Project + clip with a real caption chunk (non-manual y_pct)."""
    from sqlalchemy import delete
    from database import async_session
    from models import ClipModel, ClipStatus, ProjectModel
    pid = "rxtest2-" + uuid.uuid4().hex[:8]
    cid = pid + "-c"
    caption_plan = {
        "preset_id": "bold_impact",
        "position": "bottom",
        "y_pct": 0.85,
        "chunks": [
            {
                "text": "hello world",
                "start": 0.1,
                "end": 1.0,
                "words": [
                    {"word": "hello", "start": 0.1, "end": 0.5},
                    {"word": "world", "start": 0.6, "end": 1.0},
                ],
            }
        ],
    }
    async with async_session() as s:
        await s.execute(delete(ClipModel).where(ClipModel.project_id == pid))
        await s.execute(delete(ProjectModel).where(ProjectModel.id == pid))
        s.add(ProjectModel(id=pid, title="rx2", source_kind="file", status="ready",
                           video_path=str(small_source), width=320, height=180))
        s.add(ClipModel(id=cid, project_id=pid, title="t",
                        start_time=1.0, end_time=5.0, duration=4.0,
                        status=ClipStatus.candidate.value,
                        caption_plan=caption_plan))
        await s.commit()
    return {"pid": pid, "cid": cid, "src": str(small_source)}


def _make_clip(layout_plan=None, caption_plan=None, start=1.0, end=5.0):
    return SimpleNamespace(
        id="rx2_ns", project_id="rx2_proj",
        start_time=start, end_time=end,
        layout_plan=layout_plan,
        caption_plan=caption_plan,
        transcript_text="", headline_text="",
        content_type=None, content_confidence=None,
        content_type_origin=None, status="candidate",
        duration=end - start, overall_score=None,
        sub_scores=None, score_reason=None,
        selection_run_id=None, ranker_version=None,
    )


def _make_project(src: str, w=320, h=180):
    return SimpleNamespace(
        id="rx2_proj", width=w, height=h, video_path=src,
        clipper_settings={}, fps=24.0, source_url=None,
        content_type=None, content_confidence=None,
        content_type_override=None, analysis_version=None,
    )


# ---------------------------------------------------------------------------
# Issue 2: resolver exceptions propagate — PUT refuses without mutation
# ---------------------------------------------------------------------------

async def test_put_caption_resolve_failure_refuses_without_mutation(client, reaction_db):
    """If resolve_position raises during PUT, the response is non-2xx and no
    layout_plan is written — the session.commit() must not have been reached."""
    from database import async_session
    from models import ClipModel
    cid, src = reaction_db["cid"], reaction_db["src"]

    with patch("services.clipper.captions.resolve_position",
               side_effect=RuntimeError("injected caption failure")):
        r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                             json=_valid_put_body(src))

    assert r.status_code >= 400, f"expected error response, got {r.status_code}"
    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
    assert clip.layout_plan is None, "layout_plan must not be saved on resolve failure"


async def test_worker_caption_resolve_failure_raises(valid_binding, small_source, tmp_path):
    """_decide_render must raise (not silently continue) when resolve_position fails
    for a non-manual reaction caption."""
    from workers import clipper_render_plan as crp
    plan, _ = valid_binding
    auto_caption = {"position": "bottom", "y_pct": 0.85, "chunks": []}
    clip = _make_clip(layout_plan=plan, caption_plan=auto_caption)
    proj = _make_project(str(small_source))

    with patch("services.clipper.captions.resolve_position",
               side_effect=RuntimeError("injected resolve failure")), \
         patch.object(crp, "_dead_spans", new_callable=AsyncMock, return_value=[]):
        with pytest.raises(RuntimeError, match="injected resolve failure"):
            await crp._decide_render(clip, proj, tmp_path)


async def test_put_manual_y_bypass_succeeds_even_if_resolver_would_fail(
        client, reaction_db):
    """y_pct_manual=True must skip resolve_position entirely; PUT succeeds."""
    from database import async_session
    from models import ClipModel
    cid, src = reaction_db["cid"], reaction_db["src"]

    # Overwrite caption_plan to manual
    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
        clip.caption_plan = {**clip.caption_plan, "y_pct_manual": True, "y_pct": 0.99}
        await s.commit()

    with patch("services.clipper.captions.resolve_position",
               side_effect=RuntimeError("must not be called")):
        r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                             json=_valid_put_body(src))

    assert r.status_code == 200, r.text
    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
    assert clip.caption_plan["y_pct"] == pytest.approx(0.99), \
        "manual y_pct must be preserved unchanged"


# ---------------------------------------------------------------------------
# Issue 1: frozen source token — file replaced after decode must fail PUT
# ---------------------------------------------------------------------------

async def test_frozen_token_after_source_replacement_rejects_put(client, reaction_db, monkeypatch):
    """Replace after decode but BEFORE the HTTP handler constructs its response.
    Recomputing the guard there would stamp the old pixels with the new file."""
    from routers import clipper_reaction as api
    cid, src = reaction_db["cid"], reaction_db["src"]
    v1_token = _sv(src)
    decode = api._decode_source_frame

    def replace_after_decode(*args):
        result = decode(*args)
        Path(src).write_bytes(b"\x00" * 512)
        return result

    monkeypatch.setattr(api, "_decode_source_frame", replace_after_decode)

    # 1. Capture token while source is at V1
    r = await client.get(f"/api/clipper/clips/{cid}/reaction-source?t=0.5")
    assert r.status_code == 200, r.text
    assert r.headers["x-source-version"] == v1_token
    assert _sv(src) != v1_token

    # 3. PUT with the stale V1 token must be rejected
    body = _valid_put_body(src)
    body["source_version"] = v1_token
    r2 = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=body)
    assert r2.status_code == 409, f"expected 409, got {r2.status_code}: {r2.text}"


# ---------------------------------------------------------------------------
# Issue 4: end-to-end with real captions, ASS y, fingerprint, preview PNG
# ---------------------------------------------------------------------------

async def test_render_export_full_with_caption_and_preview(
        client, reaction_db, tmp_path):
    """Full pipeline: PUT → _decide_render → render_export → preview-frame.

    Verifies:
    - sidecar reaction_binding.schema is correct
    - sidecar v2 fingerprint matches its recorded recipe
    - executed command burned captions; ASS position matches resolved y
    - caption ink in the decoded MP4 stays above the reaction band
    - preview-frame returns image/png at 1080×1920 output dimensions
    """
    from database import async_session
    from models import ClipModel, ProjectModel
    from workers.clipper_render_output import render_export
    from workers import clipper_render_plan as crp

    cid, pid, src = reaction_db["cid"], reaction_db["pid"], reaction_db["src"]

    # PUT reaction layout
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                         json=_valid_put_body(src))
    assert r.status_code == 200, r.text
    assert r.json()["clip"]["layout_plan"]["game_content_fit"] is True

    # Load real DB rows
    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
        project = await s.get(ProjectModel, pid)

    # _decide_render: caption_y should be resolved from reaction plan safe_zones
    with patch.object(crp, "_dead_spans", new_callable=AsyncMock, return_value=[]):
        decision = await crp._decide_render(clip, project, tmp_path)

    assert decision["dyn"] is None
    assert decision["plan"]["game_content_fit"] is True
    assert "reaction_binding" in decision["plan"]

    import math as _math
    assert decision["caption_y"] is not None, \
        "caption_y must be resolved from reaction plan (not left None)"
    assert _math.isfinite(float(decision["caption_y"])), \
        f"caption_y must be finite, got {decision['caption_y']}"

    # render_export
    out = tmp_path / "out.mp4"
    result = await render_export(clip, project, decision, out, src=src)

    assert out.exists() and out.stat().st_size > 1000
    import json
    sidecar = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))

    # Binding schema
    lp = sidecar.get("layout_plan") or {}
    assert lp.get("reaction_binding", {}).get("schema") == "clipper_reaction_binding_v1"

    from services.clipper import render_input
    assert sidecar["fingerprint_schema"] == render_input.FINGERPRINT_SCHEMA_V2
    assert sidecar["input_fingerprint"] == render_input.input_fingerprint(
        sidecar, schema=sidecar["fingerprint_schema"])
    assert sidecar["render_record"]["caption_filter"] is True

    # Caption policy burned
    cp_decision = sidecar.get("caption_policy") or {}
    assert cp_decision.get("action") == "burn", \
        f"caption_policy.action must be 'burn', got {cp_decision.get('action')!r}"

    # The ASS is the actual placement input, not just the stored plan's claim.
    assert sidecar.get("caption_y") is not None, "sidecar must record caption_y"
    assert _math.isfinite(float(sidecar["caption_y"]))
    import re
    ass = Path(decision["ass_path"]).read_text(encoding="utf-8-sig")
    positions = re.findall(r"\\pos\([^,]+,([0-9.]+)\)", ass)
    assert positions and "HELLO" in ass.upper()
    assert all(abs(float(y) - sidecar["caption_y"] * 1920) <= 1 for y in positions)
    import av
    import numpy as np
    with av.open(str(out)) as container:
        frame = next(f for f in container.decode(video=0) if float(f.time) >= .5)
        pixels = frame.to_ndarray(format="rgb24")
    ys, _ = np.where((pixels > 180).all(axis=2))
    assert len(ys) > 100 and int(ys.max()) < 1152

    # Preview frame: must return PNG at export output dimensions (1080×1920)
    pr = await client.get(f"/api/clipper/clips/{cid}/preview-frame?t=0.5")
    assert pr.status_code == 200, pr.text
    assert pr.headers.get("content-type", "").startswith("image/png"), \
        f"preview-frame must return image/png, got {pr.headers.get('content-type')}"
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(pr.content))
    assert img.size == (1080, 1920), \
        f"preview frame must be 1080×1920, got {img.size}"
