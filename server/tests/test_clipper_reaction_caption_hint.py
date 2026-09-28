"""NoCaptionGap and the content height that leaves room for captions.

PRP: PRPs/clipper-reaction-caption-hint-2026-09-24.md. The boundary is asked of
the same builder and resolver the save uses, so these tests pin it by running
them, never by quoting an aspect ratio. asyncio_mode=auto in pytest.ini.
"""
from __future__ import annotations

import uuid

import pytest

from services.clipper import reaction_captions as rc
from services.clipper.reaction_layout import plan_reaction_layout

SRC_W, SRC_H = 1920, 1080
FACE = {"x": 0, "y": 478, "w": 838, "h": 596}        # 70ca's saved reaction box
SQUARE = {"x": 760, "y": 0, "w": 1160, "h": 1018}     # 70ca's first, refused, content box


def _caption_plan(font_size: float = 72) -> dict:
    return {"preset_id": "bold_impact", "position": "bottom", "y_pct": 0.75,
            "scale": 1.0, "entry_pop": False,
            "style": {"font_size": font_size, "outline_width": 5, "shadow_offset": 2.5},
            "chunks": [{"text": "YOU PLAY FORTNITE", "start": 0.0, "end": 1.0}]}


def _resolve(content: dict, caption_plan: dict | None = None):
    plan = plan_reaction_layout(content_rect=content, face_rect=FACE,
                                src_w=SRC_W, src_h=SRC_H, face_pct=0.40)
    return rc.resolve_reaction_caption_y(plan, caption_plan or _caption_plan(),
                                         clip_duration=4.0)


def test_a_near_square_box_raises_no_caption_gap_which_is_still_a_value_error():
    with pytest.raises(rc.NoCaptionGap) as err:
        _resolve(SQUARE)
    assert isinstance(err.value, ValueError)


def test_the_suggested_height_is_the_exact_boundary():
    h = rc.caption_ready_height(SQUARE, FACE, SRC_W, SRC_H, _caption_plan(),
                                face_pct=0.40, clip_duration=4.0)
    assert h is not None and h % 2 == 0 and h < SQUARE["h"]
    assert _resolve({**SQUARE, "h": h}) is not None           # a slot at h
    with pytest.raises(rc.NoCaptionGap):
        _resolve({**SQUARE, "h": h + 2})                        # none one step taller


def test_a_style_outside_the_envelope_is_not_reported_as_a_gap():
    with pytest.raises(ValueError) as err:
        _resolve(SQUARE, _caption_plan(font_size=90))
    assert not isinstance(err.value, rc.NoCaptionGap)


# ---------------------------------------------------------------------------
# HTTP: PUT /clips/{id}/reaction-layout
# ---------------------------------------------------------------------------

@pytest.fixture
def small_source(tmp_path):
    from services.clipper.ffmpeg_tools import ffmpeg_bin, run
    p = tmp_path / "source.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=6",
         "-c:v", "libx264", "-preset", "ultrafast", str(p)],
        what="caption hint fixture")
    return p


@pytest.fixture
def make_clip(small_source):
    async def make(caption_plan: dict) -> tuple[str, str]:
        from database import async_session
        from models import ClipModel, ClipStatus, ProjectModel
        pid = "rxhint-" + uuid.uuid4().hex[:8]
        async with async_session() as s:
            s.add(ProjectModel(id=pid, title="caption hint", source_kind="file",
                               status="ready", video_path=str(small_source),
                               width=320, height=180))
            s.add(ClipModel(id=pid + "-c", project_id=pid, title="t",
                            start_time=1.0, end_time=5.0, duration=4.0,
                            status=ClipStatus.candidate.value,
                            caption_plan=caption_plan))
            await s.commit()
        return pid + "-c", str(small_source)
    return make


def _body(src: str, content: dict) -> dict:
    from services.clipper.reaction_edit import compute_source_version
    return {"content_rect": content, "face_rect": {"x": 0, "y": 0, "w": 84, "h": 60},
            "source_version": compute_source_version(src, 320, 180),
            "source_start": 1.0, "source_end": 5.0, "src_w": 320, "src_h": 180}


SMALL_SQUARE = {"x": 0, "y": 0, "w": 180, "h": 176}


async def test_the_refusal_names_a_height_and_that_height_is_accepted(client, make_clip):
    cid, src = await make_clip(_caption_plan())
    url = f"/api/clipper/clips/{cid}/reaction-layout"
    r = await client.put(url, json=_body(src, SMALL_SQUARE))
    assert r.status_code == 422, r.text
    body = r.json()
    detail = body.get("detail", body)
    h = detail["max_content_height"]
    assert isinstance(h, int) and 2 <= h < SMALL_SQUARE["h"]
    assert f"at most {h} px" in detail["message"]
    assert (await client.get(f"/api/clipper/clips/{cid}")).json()["layout_plan"] is None
    r = await client.put(url, json=_body(src, {**SMALL_SQUARE, "h": h}))
    assert r.status_code == 200, r.text


async def test_a_manual_caption_still_bypasses_placement(client, make_clip):
    cid, src = await make_clip({**_caption_plan(), "y_pct_manual": True})
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                         json=_body(src, SMALL_SQUARE))
    assert r.status_code == 200, r.text
