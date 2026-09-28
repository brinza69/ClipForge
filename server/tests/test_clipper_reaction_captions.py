"""Focused tests for services/clipper/reaction_captions.py.

Evidence fixture (PRP clipper-reaction-caption-gap.md):
  content  {x:348,y:128,w:1314,h:798}, reaction {x:8,y:476,w:326,h:232},
  source 1920×1080, face_pct=0.40
  → game band rest_h=1152, fitted fg y=248..904, reaction starts y=1152
  → blur gap 904..1152 = 248px, shared 192px envelope fits.
"""
from __future__ import annotations

import pytest
from services.clipper import reaction_captions as rc
from services.clipper.layout_geom import _bands, _game_fit_box, OUT_H, OUT_W


# ---------------------------------------------------------------------------
# Reference geometry helpers
# ---------------------------------------------------------------------------

def _ref_plan(face_pct: float = 0.40) -> dict:
    """game_top_face_bottom + game_content_fit plan for the PRP evidence case."""
    from services.clipper.layout_geom import _safe_zones
    game_rect = {"x": 348, "y": 128, "w": 1314, "h": 798}
    face_rect = {"x": 8, "y": 476, "w": 326, "h": 232}
    safe = _safe_zones("game_top_face_bottom", face_rect, game_rect, None, [],
                       face_pct, game_content_fit=True)
    return {
        "layout": "game_top_face_bottom",
        "game_content_fit": True,
        "game_rect": game_rect,
        "face_rect": face_rect,
        "face_pct": face_pct,
        "safe_zones": safe,
    }


def _caption_plan(
    *,
    y_pct: float = 0.85,
    position: str = "bottom",
    y_pct_manual: bool = False,
    chunks: list | None = None,
    font_size: float = 72,
    outline_width: float = 5,
    shadow_offset: float = 2.5,
    scale: float = 1.0,
    entry_pop: bool = False,
) -> dict:
    plan: dict = {
        "preset_id": "bold_impact",
        "position": position,
        "y_pct": y_pct,
        "scale": scale,
        "entry_pop": entry_pop,
        "style": {
            "font_size": font_size,
            "outline_width": outline_width,
            "shadow_offset": shadow_offset,
        },
        "chunks": chunks if chunks is not None else [
            {"text": "Hello world", "start": 0.0, "end": 1.0}
        ],
    }
    if y_pct_manual:
        plan["y_pct_manual"] = True
    return plan


def _fg_bounds(face_pct: float = 0.40) -> tuple[int, int]:
    """(fg_top_y, fg_bottom_y) in OUTPUT pixels for the reference plan."""
    game_rect = {"x": 348, "y": 128, "w": 1314, "h": 798}
    _band_h, rest_h = _bands(face_pct, OUT_H)
    fg_w, fg_h, off_x, off_y = _game_fit_box(game_rect, OUT_W, rest_h)
    return off_y, off_y + fg_h


def _reaction_top(face_pct: float = 0.40) -> int:
    """y where the reaction band starts in the reference plan."""
    _band_h, rest_h = _bands(face_pct, OUT_H)
    return rest_h


# ---------------------------------------------------------------------------
# Evidence geometry sanity
# ---------------------------------------------------------------------------

def test_reference_fg_bounds():
    fg_top, fg_bottom = _fg_bounds()
    assert fg_top == 248
    assert fg_bottom == 904


def test_reference_reaction_top():
    assert _reaction_top() == 1152


def test_blur_gap_fits_envelope():
    fg_top, fg_bottom = _fg_bounds()
    gap = _reaction_top() - fg_bottom
    assert gap >= 192, f"gap={gap}px < 192px envelope"


# ---------------------------------------------------------------------------
# _foreground_keep_out
# ---------------------------------------------------------------------------

def test_foreground_keep_out_reference():
    plan = _ref_plan()
    rect = rc._foreground_keep_out(plan)
    assert rect["y"] == 248
    assert rect["y"] + rect["h"] == 904
    assert rect["kind"] == "game_fg"


# ---------------------------------------------------------------------------
# Successful placement resolves into the blur gap
# ---------------------------------------------------------------------------

def test_one_line_caption_clears_foreground_and_reaction():
    plan = _ref_plan()
    cp = _caption_plan(chunks=[{"text": "Hello", "start": 0.0, "end": 1.0}])
    y = rc.resolve_reaction_caption_y(plan, cp)
    assert y is not None
    fg_top, fg_bottom = _fg_bounds()
    reaction_top = _reaction_top()
    assert y > fg_bottom / OUT_H, "caption overlaps fitted foreground"
    assert y < reaction_top / OUT_H, "caption overlaps reaction band"


def test_two_line_caption_clears_foreground_and_reaction():
    plan = _ref_plan()
    cp = _caption_plan(chunks=[{"text": "Hello\nWorld", "start": 0.0, "end": 1.0}])
    y = rc.resolve_reaction_caption_y(plan, cp)
    assert y is not None
    fg_top, fg_bottom = _fg_bounds()
    reaction_top = _reaction_top()
    assert y > fg_bottom / OUT_H
    assert y < reaction_top / OUT_H


# ---------------------------------------------------------------------------
# Bypass conditions — None returned, no placement attempted
# ---------------------------------------------------------------------------

def test_bypass_manual_placement():
    plan = _ref_plan()
    cp = _caption_plan(y_pct=0.99, y_pct_manual=True)
    assert rc.resolve_reaction_caption_y(plan, cp) is None


def test_bypass_empty_chunks():
    plan = _ref_plan()
    cp = _caption_plan(y_pct=0.85, chunks=[])
    assert rc.resolve_reaction_caption_y(plan, cp) is None


def test_bypass_whitespace_only_chunks():
    plan = _ref_plan()
    cp = _caption_plan(y_pct=0.85, chunks=[{"text": "   ", "start": 0.0, "end": 1.0}])
    assert rc.resolve_reaction_caption_y(plan, cp) is None


def test_bypass_non_reaction_layout():
    plan = {
        "layout": "face_top_game_bottom",
        "game_content_fit": False,
        "safe_zones": {"keep_out": []},
    }
    cp = _caption_plan(y_pct=0.75)
    assert rc.resolve_reaction_caption_y(plan, cp) is None


def test_bypass_missing_game_content_fit():
    plan = {"layout": "game_top_face_bottom", "safe_zones": {"keep_out": []}}
    cp = _caption_plan(y_pct=0.75)
    assert rc.resolve_reaction_caption_y(plan, cp) is None


def test_bypass_all_chunks_removed_by_drop_spans():
    """When drop_spans remove every overlay, return None instead of placing."""
    plan = _ref_plan()
    cp = _caption_plan(chunks=[{"text": "Hello", "start": 0.0, "end": 1.0}])
    # Drop the entire clip — every overlay remaps away
    y = rc.resolve_reaction_caption_y(plan, cp, drop_spans=[(0.0, 2.0)])
    assert y is None


# ---------------------------------------------------------------------------
# Style envelope refusals
# ---------------------------------------------------------------------------

def test_refuses_entry_pop():
    plan = _ref_plan()
    cp = _caption_plan(entry_pop=True)
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_scale_above_one():
    plan = _ref_plan()
    cp = _caption_plan(scale=1.2)
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_large_font():
    plan = _ref_plan()
    cp = _caption_plan(font_size=80, scale=1.0)
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_outline_above_limit():
    plan = _ref_plan()
    cp = _caption_plan(outline_width=6)
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_shadow_above_limit():
    plan = _ref_plan()
    cp = _caption_plan(shadow_offset=4)
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_negative_shadow():
    plan = _ref_plan()
    cp = _caption_plan(shadow_offset=-1)
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_nan_font_size():
    import math
    plan = _ref_plan()
    cp = _caption_plan(font_size=float("nan"))
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_inf_outline():
    plan = _ref_plan()
    cp = _caption_plan(outline_width=float("inf"))
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


# ---------------------------------------------------------------------------
# Named preset is checked (merged style, not inline-only)
# ---------------------------------------------------------------------------

def test_named_preset_viral_gradient_refused():
    """viral_gradient resolves to 76px — must fail without any inline overrides."""
    plan = _ref_plan()
    cp = {"preset_id": "viral_gradient",
          "chunks": [{"text": "HELLO", "start": 0.0, "end": 1.0}]}
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


# ---------------------------------------------------------------------------
# Text envelope refusals
# ---------------------------------------------------------------------------

def test_refuses_text_too_long():
    plan = _ref_plan()
    cp = _caption_plan(chunks=[{"text": "A" * 23, "start": 0.0, "end": 1.0}])
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_too_many_lines():
    plan = _ref_plan()
    cp = _caption_plan(chunks=[{"text": "Line1\nLine2\nLine3", "start": 0.0, "end": 1.0}])
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_ass_backslash_control():
    plan = _ref_plan()
    cp = _caption_plan(chunks=[{"text": r"A\NB\NC", "start": 0.0, "end": 1.0}])
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


def test_refuses_ass_brace_control():
    plan = _ref_plan()
    cp = _caption_plan(chunks=[{"text": r"{\fscy500}HELLO", "start": 0.0, "end": 1.0}])
    with pytest.raises(ValueError, match="manual"):
        rc.resolve_reaction_caption_y(plan, cp)


# ---------------------------------------------------------------------------
# Portrait / no-gap case raises rather than falling back
# ---------------------------------------------------------------------------

def test_no_gap_raises_not_fallback():
    """A plan where the game content fills the whole band leaves no gap."""
    from services.clipper.layout_geom import _safe_zones
    game_rect = {"x": 0, "y": 0, "w": 1080, "h": 1080}
    face_rect = {"x": 0, "y": 0, "w": 326, "h": 232}
    face_pct = 0.40
    safe = _safe_zones("game_top_face_bottom", face_rect, game_rect, None, [],
                       face_pct, game_content_fit=True)
    plan = {
        "layout": "game_top_face_bottom",
        "game_content_fit": True,
        "game_rect": game_rect,
        "face_rect": face_rect,
        "face_pct": face_pct,
        "safe_zones": safe,
    }
    cp = _caption_plan()
    with pytest.raises(ValueError, match="No clear caption position"):
        rc.resolve_reaction_caption_y(plan, cp)


# ---------------------------------------------------------------------------
# Safe_zones are not mutated
# ---------------------------------------------------------------------------

def test_stored_safe_zones_not_mutated():
    plan = _ref_plan()
    original_keep_out = list(plan["safe_zones"]["keep_out"])
    rc.resolve_reaction_caption_y(plan, _caption_plan())
    assert plan["safe_zones"]["keep_out"] == original_keep_out


# ---------------------------------------------------------------------------
# Stable placement
# ---------------------------------------------------------------------------

def test_placement_stable_across_calls():
    plan = _ref_plan()
    cp = _caption_plan()
    y1 = rc.resolve_reaction_caption_y(plan, cp)
    y2 = rc.resolve_reaction_caption_y(plan, cp)
    assert y1 == pytest.approx(y2)


def test_placement_in_blur_gap():
    plan = _ref_plan()
    cp = _caption_plan()
    y = rc.resolve_reaction_caption_y(plan, cp)
    assert y is not None
    fg_top, fg_bottom = _fg_bounds()
    reaction_top = _reaction_top()
    assert fg_bottom / OUT_H < y < reaction_top / OUT_H


# ---------------------------------------------------------------------------
# PUT suppression bypass: no-gap layout + unsupported style must not refuse
# (tests the caption_policy guard added to clipper_reaction.PUT)
# ---------------------------------------------------------------------------

async def test_put_suppressed_layer_no_gap_unsupported_style_does_not_refuse(
        tmp_path):
    """PUT with suppressed captions must succeed even if the layout has no gap
    and the caption style is outside the supported automatic-placement envelope."""
    import uuid
    from sqlalchemy import delete
    from database import async_session
    from models import ClipModel, ClipStatus, ProjectModel
    from services.clipper.ffmpeg_tools import ffmpeg_bin, run

    # Minimal video file for source guard
    src = tmp_path / "source.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=2",
         "-c:v", "libx264", "-preset", "ultrafast", str(src)],
        what="suppression test fixture")

    pid = "rxtestsupp-" + uuid.uuid4().hex[:8]
    cid = pid + "-c"
    # Caption plan with entry_pop (unsupported style) — would fail placement
    # if the policy check is missing
    caption_plan = {
        "preset_id": "viral_gradient",
        "entry_pop": True,
        "position": "bottom",
        "y_pct": 0.85,
        "chunks": [{"text": "no gap test", "start": 0.0, "end": 1.0}],
    }
    async with async_session() as s:
        await s.execute(delete(ClipModel).where(ClipModel.project_id == pid))
        await s.execute(delete(ProjectModel).where(ProjectModel.id == pid))
        s.add(ProjectModel(id=pid, title="supptest", source_kind="file",
                           status="ready", video_path=str(src),
                           width=320, height=180,
                           clipper_settings={"source_has_burned_captions": True}))
        s.add(ClipModel(id=cid, project_id=pid, title="t",
                        start_time=0.0, end_time=2.0, duration=2.0,
                        status=ClipStatus.candidate.value,
                        caption_plan=caption_plan))
        await s.commit()

    from services.clipper.reaction_edit import compute_source_version, BINDING_SCHEMA
    from services.clipper.reaction_layout import plan_reaction_layout
    import httpx
    from main import app

    sv = compute_source_version(str(src), 320, 180)
    body = {
        "content_rect": {"x": 0, "y": 0, "w": 100, "h": 180},
        "face_rect": {"x": 0, "y": 0, "w": 84, "h": 60},
        "source_version": sv,
        "source_start": 0.0,
        "source_end": 2.0,
        "src_w": 320, "src_h": 180,
    }
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout",
                             json=body)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"

    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
    await s.aclose()
    # y_pct must be unchanged (suppressed: helper skipped)
    assert clip.caption_plan["y_pct"] == pytest.approx(0.85)

    # cleanup
    async with async_session() as s:
        await s.execute(delete(ClipModel).where(ClipModel.project_id == pid))
        await s.execute(delete(ProjectModel).where(ProjectModel.id == pid))
        await s.commit()
