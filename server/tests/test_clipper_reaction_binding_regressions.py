"""Independent editor regressions: nonzero clip clock and refused stale edits."""
from __future__ import annotations

import copy
import io
import uuid

import av
import numpy as np
import pytest
from PIL import Image


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "clock.mkv"
    with av.open(str(path), "w") as output:
        stream = output.add_stream("ffv1", rate=10)
        stream.width, stream.height, stream.pix_fmt = 1280, 720, "bgr0"
        for i in range(20):
            pixels = np.full((720, 1280, 3), (i * 10, 30, 70), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return path


@pytest.fixture
async def stored(source):
    from database import async_session
    from models import ClipModel, ProjectModel
    pid = "react-reg-" + uuid.uuid4().hex[:10]
    async with async_session() as session:
        project = ProjectModel(id=pid, title="test", source_kind="url", status="ready",
                               video_path=str(source), width=1280, height=720, fps=10,
                               clipper_settings={"dynamic_edit": True})
        clip = ClipModel(id=pid + "-c", project_id=pid, title="test", status="candidate",
                         start_time=.4, end_time=1.65, duration=1.25)
        session.add_all([project, clip])
        await session.commit()
    return clip.id, pid


async def _payload(client, cid):
    result = await client.get(f"/api/clipper/clips/{cid}/reaction-source?t=0.15")
    assert result.status_code == 200, result.text
    h = result.headers
    return {
        "content_rect": {"x": 200, "y": 0, "w": 1000, "h": 600},
        "face_rect": {"x": 0, "y": 0, "w": 140, "h": 100},
        "src_w": int(h["x-source-width"]), "src_h": int(h["x-source-height"]),
        "source_version": h["x-source-version"], "source_start": .4, "source_end": 1.65,
    }


async def test_relative_source_clock_and_pixels(client, stored):
    cid, _ = stored
    result = await client.get(f"/api/clipper/clips/{cid}/reaction-source?t=0.15")
    assert result.status_code == 200, result.text
    assert float(result.headers["x-source-time"]) == pytest.approx(.6)
    assert (result.headers["x-source-width"], result.headers["x-source-height"]) == ("1280", "720")
    image = Image.open(io.BytesIO(result.content)).convert("RGB")
    assert image.size == (960, 540)
    assert image.getpixel((200, 200)) == (60, 30, 70)
    # Requested time is in-window, but the next actual frame is not.
    late = await client.get(f"/api/clipper/clips/{cid}/reaction-source?t=1.24")
    assert late.status_code >= 400


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", True, None])
async def test_nonfinite_or_non_numeric_window_cannot_save(client, stored, bad):
    from database import async_session
    from models import ClipModel
    cid, _ = stored
    payload = await _payload(client, cid)
    payload["source_start"] = bad
    result = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=payload)
    assert result.status_code >= 400
    async with async_session() as session:
        clip = await session.get(ClipModel, cid)
        assert clip.layout_plan is None


@pytest.mark.parametrize("mutation", [
    {"game_rect": {"x": 200, "y": 0, "w": 999, "h": 600}},
    {"game_rect": {"x": 200, "y": 0, "w": 1200, "h": 600}},
    {"face_rect": {"x": 0, "y": 0, "w": 140.9, "h": 100}},
    {"face_pct": .55}, {"face_pct": float("nan")},
    {"layout": "fullscreen_crop"}, {"src_w": 1920}, {"src_w": 1280.0},
    {"reaction_binding": {"source_start": "NaN"}},
    {"reaction_binding": {"source_end": "Infinity"}},
    {"safe_zones": {}},
])
async def test_generic_plan_edit_cannot_bypass_binding(client, stored, mutation, tmp_path, monkeypatch):
    from database import async_session
    from models import ClipModel, ProjectModel
    from workers import clipper_render_plan as worker
    cid, pid = stored
    result = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=await _payload(client, cid))
    assert result.status_code == 200, result.text
    async with async_session() as session:
        clip, project = await session.get(ClipModel, cid), await session.get(ProjectModel, pid)
        plan = copy.deepcopy(clip.layout_plan)
        for key, value in mutation.items():
            if key == "reaction_binding":
                plan[key].update(value)
            else:
                plan[key] = value
        clip.layout_plan = plan
        dynamic_calls = []

        async def unexpected(*args, **kwargs):
            dynamic_calls.append(True)

        monkeypatch.setattr(worker, "_dynamic_plan", unexpected)
        with pytest.raises((ValueError, RuntimeError)):
            await worker._decide_render(clip, project, tmp_path)
        assert not dynamic_calls


async def test_clear_unbound_reaction_recovers_from_refused_plan(client, stored):
    from database import async_session
    from models import ClipModel
    cid, _ = stored
    async with async_session() as session:
        clip = await session.get(ClipModel, cid)
        clip.layout_plan = {"game_content_fit": True}
        await session.commit()
    result = await client.delete(f"/api/clipper/clips/{cid}/reaction-layout")
    assert result.status_code == 200, result.text
    assert result.json()["clip"]["layout_plan"] is None


@pytest.mark.parametrize("metadata_width", [None, 1920])
async def test_actual_frame_is_reported_but_wrong_project_dimensions_cannot_save(client, stored, metadata_width):
    from database import async_session
    from models import ClipModel, ProjectModel
    cid, pid = stored
    async with async_session() as session:
        project = await session.get(ProjectModel, pid)
        project.width = metadata_width
        await session.commit()
    payload = await _payload(client, cid)
    assert (payload["src_w"], payload["src_h"]) == (1280, 720)
    result = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=payload)
    assert result.status_code == 422
    async with async_session() as session:
        assert (await session.get(ClipModel, cid)).layout_plan is None
