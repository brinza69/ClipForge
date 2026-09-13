"""
Tests for reaction_layout.py and the game_content_fit extension in layout_geom.py.

Every numerical assertion uses independently derived expected values — not the
function's own output run again on slightly different inputs. The ffmpeg render
test at the bottom decodes actual pixels to verify full foreground preservation
and proportionality. A separate small-canvas encode catches blur-radius limits.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from services.clipper.reaction_layout import plan_reaction_layout
from services.clipper.layout_geom import (
    OUT_H,
    OUT_W,
    _bands,
    _game_fit_box,
    build_filtergraph,
)
from services.clipper.render import build_render_cmd


# ── helpers ───────────────────────────────────────────────────────────────────

def _small_plan(**kw):
    """A minimal valid plan: 200×100 game on a 400×400 source, face below it."""
    defaults = dict(
        content_rect={"x": 0, "y": 0, "w": 200, "h": 100},
        face_rect={"x": 0, "y": 200, "w": 200, "h": 100},
        src_w=400, src_h=400, face_pct=0.40,
    )
    defaults.update(kw)
    return plan_reaction_layout(**defaults)


def _plain_plan():
    return {
        "layout": "game_top_face_bottom",
        "game_rect": {"x": 0, "y": 0, "w": 200, "h": 100},
        "face_rect": {"x": 0, "y": 100, "w": 200, "h": 100},
        "face_pct": 0.40,
    }


# ── Bounds refusals ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("content,face,match", [
    ([0, 0, 100, 100], {"x": 0, "y": 0, "w": 100, "h": 100}, "content_rect"),
    ({"x": 0, "y": 0, "w": 100, "h": 100}, None, "face_rect"),
    ({"x": 0, "y": 0, "w": 100, "h": 100}, "bad", "face_rect"),
])
def test_refuses_invalid_rect_type(content, face, match):
    with pytest.raises(ValueError, match=match):
        plan_reaction_layout(content, face, 400, 400)


@pytest.mark.parametrize("patch,match", [
    ({"w": 100.0}, "content_rect.w"),   # float field
    ({"w": True}, "content_rect"),      # bool field (subclass of int)
    ({"w": 101}, "even"),               # odd width
    ({"h": 101}, "even"),               # odd height
    ({"x": 1}, "even"),                 # odd x origin
    ({"y": 1}, "even"),                 # odd y origin
    ({"w": 0}, "positive"),             # zero width
])
def test_refuses_invalid_content_rect_value(patch, match):
    base = {"x": 0, "y": 0, "w": 100, "h": 100}
    with pytest.raises(ValueError, match=match):
        plan_reaction_layout(
            {**base, **patch},
            {"x": 0, "y": 0, "w": 100, "h": 100},
            400, 400,
        )


@pytest.mark.parametrize("patch,match", [
    ({"x": 1}, "even"),   # odd x on face rect
    ({"y": 1}, "even"),   # odd y on face rect
])
def test_refuses_odd_origin_on_face_rect(patch, match):
    face_base = {"x": 0, "y": 200, "w": 100, "h": 100}
    with pytest.raises(ValueError, match=match):
        plan_reaction_layout(
            {"x": 0, "y": 0, "w": 100, "h": 100},
            {**face_base, **patch},
            400, 400,
        )


def test_refuses_rect_out_of_source():
    with pytest.raises(ValueError, match="outside source"):
        plan_reaction_layout(
            {"x": 100, "y": 0, "w": 200, "h": 100},
            {"x": 0, "y": 0, "w": 100, "h": 100},
            200, 400,
        )


def test_refuses_odd_src_width():
    with pytest.raises(ValueError, match="even"):
        plan_reaction_layout(
            {"x": 0, "y": 0, "w": 100, "h": 100},
            {"x": 0, "y": 0, "w": 100, "h": 100},
            201, 400,
        )


@pytest.mark.parametrize("fp", [0.8, 0.05])
def test_refuses_face_pct_out_of_range(fp):
    with pytest.raises(ValueError, match="face_pct"):
        plan_reaction_layout(
            {"x": 0, "y": 0, "w": 100, "h": 100},
            {"x": 0, "y": 0, "w": 100, "h": 100},
            400, 400, face_pct=fp,
        )


# ── Plan fields ───────────────────────────────────────────────────────────────

def test_plan_basic_fields():
    plan = _small_plan()
    assert plan["layout"] == "game_top_face_bottom"
    assert plan["game_content_fit"] is True
    assert plan["src_w"] == 400
    assert plan["src_h"] == 400
    assert "caller" in plan["note"].lower()
    assert "keep_out" in plan["safe_zones"]


def test_rects_preserved_exactly():
    content = {"x": 0, "y": 0, "w": 200, "h": 100}
    face = {"x": 0, "y": 200, "w": 200, "h": 100}
    plan = plan_reaction_layout(content, face, 400, 400)
    assert plan["game_rect"] == content
    assert plan["face_rect"] == face


# ── Foreground geometry (_game_fit_box) ───────────────────────────────────────
# Expected values derived independently; never from running the function again.

@pytest.mark.parametrize("game,out_w,band_h,expect", [
    # game 1314×798 (ar≈1.647), band 1080×1152 (ar≈0.938) → width binds
    # fg_w=1080, fg_h=even(1080*798/1314)=656, off_x=0, off_y=even((1152-656)/2)=248
    ({"x": 348, "y": 128, "w": 1314, "h": 798}, 1080, 1152, (1080, 656, 0, 248)),
    # game 100×200 (ar=0.5), band 540×576 (ar=0.938) → height binds
    # fg_h=576, fg_w=even(576*100/200)=288, off_y=0, off_x=even((540-288)/2)=126
    ({"x": 0, "y": 0, "w": 100, "h": 200}, 540, 576, (288, 576, 126, 0)),
    # game 200×200 (ar=1.0), band 540×576 (ar≈0.938) → width binds
    # fg_w=540, fg_h=even(540)=540, off_x=0, off_y=even((576-540)/2)=18
    ({"x": 0, "y": 0, "w": 200, "h": 200}, 540, 576, (540, 540, 0, 18)),
])
def test_fit_box_geometry(game, out_w, band_h, expect):
    assert _game_fit_box(game, out_w, band_h) == expect


# ── HUD mapping (numerical) ───────────────────────────────────────────────────
# src 400×400, game_rect {x:100,y:100,w:200,h:100}, face_pct=0.40
#   face_band=768, game_band=1152; width binds → fg_w=1080, fg_h=540
#   off_x=0, off_y=306; lane={x:0,y:306,w:1080,h:540}
#
# HUD {x:150,y:120,w:50,h:30}:
#   sx=1080/200=5.4, sy=540/100=5.4
#   x0=0+(150-100)*5.4=270, y0=306+(120-100)*5.4=414
#   w=50*5.4=270, h=30*5.4=162

def test_hud_mapping_numerical():
    plan = plan_reaction_layout(
        {"x": 100, "y": 100, "w": 200, "h": 100},
        {"x": 0, "y": 200, "w": 200, "h": 100},
        400, 400, face_pct=0.40,
        hud=[{"x": 150, "y": 120, "w": 50, "h": 30}],
    )
    hud_entry = next(
        (k for k in plan["safe_zones"]["keep_out"] if k.get("kind") == "hud"), None
    )
    assert hud_entry is not None, "HUD keep-out missing"
    assert hud_entry == {"x": 270, "y": 414, "w": 270, "h": 162, "kind": "hud"}


def test_face_keepout_covers_full_band_width():
    # Face keep-out must be conservative: full output width regardless of fit.
    plan = _small_plan()
    face_entry = next(
        (k for k in plan["safe_zones"]["keep_out"] if k.get("kind") == "face"), None
    )
    assert face_entry is not None
    band_h, _ = _bands(0.40, OUT_H)
    assert face_entry["w"] == OUT_W
    assert face_entry["h"] == band_h


# ── Filtergraph format ────────────────────────────────────────────────────────

def test_filtergraph_with_fit_uses_overlay_and_blur():
    graph = build_filtergraph({**_plain_plan(), "game_content_fit": True})
    assert "overlay=" in graph and "boxblur" in graph and graph.endswith("[v]")


def test_filtergraph_without_fit_uses_plain_scale():
    graph = build_filtergraph(_plain_plan())
    assert "overlay=" not in graph and "boxblur" not in graph and graph.endswith("[v]")


def test_filtergraph_fit_differs_from_plain():
    plan = _plain_plan()
    assert build_filtergraph({**plan, "game_content_fit": True}) != build_filtergraph(plan)


def test_filtergraph_false_is_same_as_absent():
    plan = _plain_plan()
    assert build_filtergraph(plan) == build_filtergraph({**plan, "game_content_fit": False})


@pytest.mark.parametrize("bad_val", ["yes", 1])
def test_game_content_fit_invalid_type_raises(bad_val):
    with pytest.raises(ValueError, match="game_content_fit"):
        build_filtergraph({**_plain_plan(), "game_content_fit": bad_val})


def test_game_content_fit_unsupported_layout_raises():
    plan = {
        "layout": "face_top_game_bottom",
        "game_rect": {"x": 0, "y": 0, "w": 200, "h": 100},
        "face_rect": {"x": 0, "y": 100, "w": 200, "h": 50},
        "face_pct": 0.35, "game_content_fit": True,
    }
    with pytest.raises(ValueError, match="game_content_fit"):
        build_filtergraph(plan)


@pytest.mark.parametrize("game,face", [
    ({"x": 0, "y": 0, "w": 1920, "h": 1080}, None),
    (None, {"x": 0, "y": 0, "w": 200, "h": 100}),
])
def test_game_content_fit_refuses_missing_rect(game, face):
    # Enabled fit mode must refuse rather than fall through to the fullscreen path.
    plan = {"layout": "game_top_face_bottom", "game_rect": game, "face_rect": face,
            "face_pct": 0.35, "game_content_fit": True}
    with pytest.raises(ValueError, match="game_content_fit"):
        build_filtergraph(plan)


@pytest.mark.parametrize("key,value", [("w", 0), ("x", 1), ("y", True), ("h", None), ("x", 400)])
def test_fit_refuses_malformed_stored_rect(key, value):
    plan = _small_plan()
    plan["face_rect"][key] = value
    with pytest.raises(ValueError, match="game_content_fit"):
        build_filtergraph(plan)


def test_blur_radius_bounded_for_small_canvas():
    # 16×32 band: min(16,32)//4=4; ffmpeg errors on radius 20 for such a canvas.
    graph = build_filtergraph({**_plain_plan(), "game_content_fit": True},
                               out_w=16, out_h=32)
    m = re.search(r"boxblur=(\d+)", graph)
    assert m is not None, "boxblur not found in filtergraph"
    assert int(m.group(1)) <= 4, f"blur radius {m.group(1)} too large for 16×32 canvas"
    _skip_if_no_ffmpeg()
    subprocess.run([_ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i",
                    "color=s=400x400:d=0.1", "-filter_complex", graph,
                    "-map", "[v]", "-frames:v", "1", "-f", "null", "-"],
                   check=True, capture_output=True, timeout=20)


# ── Render command acceptance and fingerprint sensitivity ─────────────────────

def test_render_cmd_accepts_reaction_plan():
    plan = _small_plan()
    cmd = build_render_cmd(
        "src.mp4", {"start": 0.0, "end": 5.0}, plan, None, "out.mp4",
        fps=60, crf=20, preset="medium",
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay=" in graph and "vstack=inputs=2[v]" in graph


def test_game_content_fit_changes_render_filtergraph():
    plan = _plain_plan()
    c1 = build_render_cmd("s.mp4", {"start": 0.0, "end": 5.0}, plan,
                          None, "o.mp4", fps=60, crf=20, preset="medium")
    c2 = build_render_cmd("s.mp4", {"start": 0.0, "end": 5.0},
                          {**plan, "game_content_fit": True},
                          None, "o.mp4", fps=60, crf=20, preset="medium")
    g1 = c1[c1.index("-filter_complex") + 1]
    g2 = c2[c2.index("-filter_complex") + 1]
    assert g1 != g2


def test_v2_fingerprint_sensitive_to_game_content_fit():
    """Changing game_content_fit changes the v2 input fingerprint."""
    from services.clipper.render_input import FINGERPRINT_SCHEMA_V2, input_fingerprint

    def _sc(fit):
        p = dict(_plain_plan())
        if fit:
            p["game_content_fit"] = True
        return {
            "source": {"path": "s.mp4", "url": None, "size_bytes": 100},
            "source_start": 0.0, "source_end": 5.0,
            "layout_plan": p, "dynamic_plan": None,
            "caption_plan": None, "caption_y": None, "drop_spans": None,
            "render_version": "v_test",
            "render": {"fps": 30, "crf": 20, "preset": "medium",
                       "watermark": "", "out_w": 1080, "out_h": 1920},
            "caption_identity": None,
            "fingerprint_schema": FINGERPRINT_SCHEMA_V2,
        }

    fp1 = input_fingerprint(_sc(False), schema=FINGERPRINT_SCHEMA_V2)
    fp2 = input_fingerprint(_sc(True), schema=FINGERPRINT_SCHEMA_V2)
    assert fp1 != fp2


# ── Actual ffmpeg render — pixels, not strings ────────────────────────────────

pytest.importorskip("numpy")


def _ffmpeg_bin():
    from services.clipper.ffmpeg_tools import ffmpeg_bin
    return ffmpeg_bin()


def _skip_if_no_ffmpeg():
    if not (shutil.which(_ffmpeg_bin()) or shutil.which("ffmpeg")):
        pytest.skip("ffmpeg not available")


def _make_source(path: str, src_w: int, src_h: int,
                 cx: int, cy: int, cw: int, ch: int) -> None:
    """Source video with distinguishable markers in content crop [cx,cy,cw,ch].

    Edge markers (luma=255): left/right cols at crop x-edges, top/bottom rows at
    crop y-edges. Disc (luma=235) at content centre. Content fill luma=80.
    Outside content luma=30. The output encode below uses CRF=1.
    """
    dcx, dcy = cx + cw // 2, cy + ch // 2
    r2 = (min(cw, ch) // 4) ** 2
    x1, x2, y1, y2 = cx, cx + cw, cy, cy + ch
    in_c  = f"gte(X,{x1})*lt(X,{x2})*gte(Y,{y1})*lt(Y,{y2})"
    disc  = f"lt((X-{dcx})*(X-{dcx})+(Y-{dcy})*(Y-{dcy}),{r2})"
    l_s   = f"lt(X,{x1+8})*gte(X,{x1})"
    r_s   = f"gte(X,{x2-8})*lt(X,{x2})"
    t_s   = f"lt(Y,{y1+4})*gte(Y,{y1})"
    b_s   = f"gte(Y,{y2-4})*lt(Y,{y2})"
    lum = (
        f"if({in_c}*{disc},235,"
        f"if({in_c}*{l_s},255,"
        f"if({in_c}*{r_s},255,"
        f"if({in_c}*{t_s},255,"
        f"if({in_c}*{b_s},255,"
        f"if({in_c},80,30))))))"
    )
    subprocess.run([
        _ffmpeg_bin(), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=gray:s={src_w}x{src_h}:d=2.0:r=30",
        "-vf", f"geq=lum='{lum}':cb=128:cr=128",
        "-pix_fmt", "yuv420p", path,
    ], check=True, capture_output=True)


def test_render_preserves_foreground_proportions(tmp_path):
    """Decode actual pixels: edge markers, disc proportions, face band dark.

    Source 320×240; content at x=40 y=0 w=240 h=120 (nonzero x origin).
    out_w=540, out_h=960, face_pct=0.40.
      game_band_h = 960-384 = 576
      content_ar = 240/120 = 2.0 > band_ar = 540/576 → width binds
      fg_w=540, fg_h=even(540*120/240)=270, off_x=0, off_y=even((576-270)/2)=152
    Foreground rows [152, 422), cols [0, 540). Scale: sx=sy=2.25.

    Left  marker  src x∈[40,48)  → fg cols [0,18)
    Right marker  src x∈[272,280)→ fg cols [522,540)
    Top   marker  src y∈[0,4)    → fg rows [152,161)
    Bottom marker src y∈[116,120)→ fg rows [414,422)
    Disc  src=(160,60) → fg=(270,287), radius≈68.
    """
    import numpy as np

    _skip_if_no_ffmpeg()

    src_w, src_h = 320, 240
    cx, cy, cw, ch = 40, 0, 240, 120
    src = str(tmp_path / "src.mp4")
    _make_source(src, src_w, src_h, cx, cy, cw, ch)

    plan = plan_reaction_layout(
        {"x": cx, "y": cy, "w": cw, "h": ch},
        {"x": 0, "y": cy + ch, "w": src_w, "h": src_h - ch},
        src_w, src_h, face_pct=0.40,
    )

    out_w, out_h = 540, 960
    out = str(tmp_path / "out.mp4")
    from models import ClipModel, ProjectModel
    from services.clipper import output_identity, render_input
    from workers.clipper_render_output import render_export

    # In-memory models only. This exercises the real common encode and saved
    # sidecar together; a fake sidecar dict cannot prove pipeline pass-through.
    project = ProjectModel(id="reaction-pixels", width=src_w, height=src_h)
    clip = ClipModel(id="reaction-pixels", project_id=project.id, title="pixels",
                     start_time=0.0, end_time=1.5, duration=1.5)
    decision = dict(cfg={}, dyn=None, plan=plan, ass_path=None, fps=30, drop=[],
                    caption_y=None, watermark="", caption_policy=None,
                    layout_policy=None, edit_profile=None, creator_view=None,
                    regime_view=None, rhythm_view=None,
                    render=dict(out_w=out_w, out_h=out_h, crf=1, preset="ultrafast"))
    asyncio.run(render_export(clip, project, decision, out, src=src))
    stored = json.loads(Path(out).with_suffix(".json").read_text(encoding="utf-8"))
    assert stored["layout_plan"] == plan
    assert stored["fingerprint_schema"] == render_input.FINGERPRINT_SCHEMA_V2
    assert stored["render_record"]["caption_filter"] is False
    assert output_identity.matches(stored["output_identity"], out)[0] is True
    digest = render_input.input_fingerprint(stored, schema=stored["fingerprint_schema"])
    assert stored["input_fingerprint"] == digest
    for key, value in (("game_content_fit", False), ("src_w", 640), ("src_h", 480)):
        changed = {**stored, "layout_plan": {**plan, key: value}}
        assert render_input.input_fingerprint(changed, schema=stored["fingerprint_schema"]) != digest

    raw = subprocess.run([
        _ffmpeg_bin(), "-v", "error", "-ss", "0.5", "-i", out,
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ], check=True, capture_output=True).stdout
    frame = np.frombuffer(raw, np.uint8).reshape(out_h, out_w)

    # off_y=152, fg_h=270 → fg rows [152, 422); scale 2.25
    fg = frame[152:422, 0:540]

    # 1. Left edge marker (cols [0,18)): substantial mean, not a single pixel.
    left = fg[:, 0:18]
    assert left.mean() >= 180, f"Left marker too dim: mean={left.mean():.1f}"

    # 2. Right edge marker (cols [522,540)).
    right = fg[:, 522:540]
    assert right.mean() >= 180, f"Right marker too dim: mean={right.mean():.1f}"

    # 3. Top edge marker (rows [0,9) in fg, cols [18,522) to exclude side strips).
    top_strip = fg[:9, 18:522]
    assert top_strip.mean() >= 180, f"Top marker too dim: mean={top_strip.mean():.1f}"

    # 4. Bottom edge marker (rows [261,270) in fg: src y∈[116,120)→116*2.25=261).
    bot_strip = fg[261:270, 18:522]
    assert bot_strip.mean() >= 180, f"Bottom marker too dim: mean={bot_strip.mean():.1f}"

    # 5. Disc proportionality (no stretch): width ≈ height ±5 px.
    # Interior excludes edge-marker cols and rows; disc centre at fg≈(270,135).
    fg_int = fg[10:260, 18:522]
    bright = fg_int > 180
    cols = np.flatnonzero(bright.any(axis=0))
    rows = np.flatnonzero(bright.any(axis=1))
    assert len(cols) > 0 and len(rows) > 0, "No disc found in foreground interior"
    disc_w = int(cols[-1] - cols[0] + 1)
    disc_h = int(rows[-1] - rows[0] + 1)
    # scale_x == scale_y (both 2.25); ≤5 px tolerance covers even-rounding.
    assert abs(disc_w - disc_h) <= 5, (
        f"Foreground distorted: disc_w={disc_w}, disc_h={disc_h}"
    )

    # 6. Face band must be dark (luma=30 in source).
    face_band = frame[576:, :]
    assert float(face_band.mean()) < 80, (
        f"Face band too bright: mean={face_band.mean():.1f}"
    )
