"""
ClipForge — AI Stream Clipper: per-clip reaction layout editor.

Three endpoints under /api/clipper/clips/{id}/reaction-*:
  GET  reaction-source?t=…  — decode one source frame (t is CLIP-RELATIVE)
  PUT  reaction-layout       — save a human-drawn reaction layout
  DELETE reaction-layout     — return to automatic framing

All mutations:
  - reject an exporting clip BEFORE committing
  - invalidate the old render reference (clear export/preview paths)
  - record manual feedback
  - return {clip: clip_to_dict(clip, project)} — with the project, so the
    clip carries its real `effective_caption_policy` (C3)

clipper_clips.py already has 499 lines; nothing touches it.
"""
from __future__ import annotations

import asyncio
import io
import logging
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import ClipModel, ClipStatus, ProjectModel
from services.clipper import feedback as feedback_mod
from services.clipper.clip_mutations import lock_clip
from services.clipper.reaction_edit import (
    BINDING_SCHEMA,
    compute_source_version,
    validate_face_aspect,
)
from services.clipper.reaction_layout import plan_reaction_layout
from services.clipper.serialize import (
    clip_to_dict,
    invalidate_render as _invalidate_render,
)

logger = logging.getLogger("clipforge.clipper.reaction")

router = APIRouter(prefix="/api/clipper", tags=["clipper"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _err(status: int, code: str, message: str, **extra: Any) -> Any:
    from fastapi import HTTPException
    return HTTPException(status, {"error": code, "message": message, **extra})


async def _load_clip_and_project(
    session: AsyncSession, clip_id: str, *, lock: bool = False
) -> tuple[ClipModel, ProjectModel]:
    # `lock=True` for a mutation: the exporting check must see the row the write
    # lands on, not one a claim changed after it was read (`lock_clip`).
    clip = await (lock_clip(session, clip_id) if lock else session.get(ClipModel, clip_id))
    if not clip:
        raise _err(404, "clip_not_found", "That clip no longer exists.")
    project = await session.get(ProjectModel, clip.project_id)
    if not project:
        raise _err(404, "project_not_found", "Project for this clip no longer exists.")
    return clip, project


def _reject_exporting(clip: ClipModel) -> None:
    if clip.status == ClipStatus.exporting.value:
        raise _err(409, "clip_exporting",
                   "Cannot edit this clip while it is being exported.")


def _source_path(project: ProjectModel) -> str:
    src = project.video_path or ""
    if not src or not Path(src).exists():
        raise _err(404, "source_not_found",
                   "The source video is not on disk.")
    return src


def _require_finite_number(val: Any, name: str) -> float:
    """Raise HTTP 422 if val is not a finite non-boolean number."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise _err(422, "invalid_field",
                   f"{name} must be a number, got {type(val).__name__}: {val!r}")
    if not math.isfinite(float(val)):
        raise _err(422, "invalid_field", f"{name} must be finite, got {val!r}")
    return float(val)


# ---------------------------------------------------------------------------
# GET /clips/{id}/reaction-source?t=…
# ---------------------------------------------------------------------------

def _decode_source_frame(
    source_path: str,
    target_t: float,
    clip_end: float,
) -> dict:
    """Decode the first frame at/after target_t (absolute source seconds).

    Reports ACTUAL frame width/height and time_base from the decoded frame.
    Rejects any frame whose time is at/after clip_end.
    Performs before/after file stat to detect source replacement during read.
    """
    import av
    from PIL import Image

    p = Path(source_path)
    before = p.stat()
    stat_before = (before.st_size, before.st_mtime_ns)

    src_w = src_h = None
    decoded_time: float | None = None
    time_base_str: str | None = None
    png_bytes: bytes | None = None
    reject_reason: str | None = None

    # Seek uses the stream clock; the accepted frame supplies its own clock
    # and raster dimensions. Neither is inferred from project metadata.
    source_version: str | None = None

    with av.open(source_path) as container:
        stream = container.streams.video[0]
        stream_time_base = stream.time_base

        seek_pts = int(max(target_t - 0.5, 0.0) / float(stream_time_base))
        container.seek(seek_pts, stream=stream, backward=True)

        for packet in container.demux(stream):
            for frame in packet.decode():
                if frame.pts is None or frame.time_base is None:
                    continue
                frame_t = float(frame.pts * frame.time_base)
                if frame_t < target_t - 1e-6:
                    continue
                # Frame is at/after clip_end — no valid frame in this clip.
                if frame_t >= clip_end - 1e-6:
                    reject_reason = (
                        f"first frame at/after target ({frame_t:.4f}s) is "
                        f"at or after clip_end ({clip_end:.4f}s)"
                    )
                    break
                decoded_time = frame_t
                src_w = frame.width
                src_h = frame.height
                time_base_str = str(frame.time_base)
                # Compute version token from ACTUAL frame dims while stat is
                # still guaranteed unchanged (between stat_before and the
                # stat_after check below).
                source_version = compute_source_version(source_path, src_w, src_h)
                img: Image.Image = frame.to_image()
                if img.width > 960:
                    new_w = 960
                    new_h = max(1, round(img.height * 960 / img.width))
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=False)
                png_bytes = buf.getvalue()
                break
            if png_bytes is not None or reject_reason:
                break

    after = p.stat()
    stat_after = (after.st_size, after.st_mtime_ns)
    if stat_before != stat_after:
        raise ValueError("source file changed during frame decode")

    if reject_reason:
        raise ValueError(reject_reason)
    if png_bytes is None:
        raise ValueError(f"no frame decoded at or after t={target_t:.3f}s")

    return {
        "png": png_bytes,
        "src_w": src_w,
        "src_h": src_h,
        "decoded_time": decoded_time,
        "time_base": time_base_str,
        "source_version": source_version,
    }


@router.get("/clips/{clip_id}/reaction-source")
async def get_reaction_source(
    clip_id: str,
    t: float = Query(..., description="Clip-relative seconds (0 <= t < clip_duration)"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Decode one frame from the original source for the reaction layout editor.

    t is CLIP-RELATIVE (not absolute source seconds). Target source time is
    clip_start + t. Refuses: missing source, non-finite t, t outside
    [0, clip_duration), and any decoded frame at/after clip_end.
    Returns image/png scaled to ≤960px wide with headers:
      X-Source-Width/Height: actual decoded frame dimensions
      X-Source-Time: actual decoded source seconds (PTS-derived, not requested t)
      X-Source-Version: opaque guard token (use as-is in PUT)
      X-Clip-Start/End, Cache-Control: no-store
    """
    clip, project = await _load_clip_and_project(session, clip_id)

    if not math.isfinite(t) or t < 0:
        raise _err(422, "invalid_time",
                   "t must be a finite non-negative number of clip-relative seconds.")

    clip_start = float(clip.start_time or 0.0)
    clip_end = float(clip.end_time or 0.0)
    duration = clip_end - clip_start
    if t >= duration - 1e-6:
        raise _err(422, "time_outside_clip",
                   f"t={t:.4f} is outside the clip window [0, {duration:.4f}).")

    src = _source_path(project)
    target_t = clip_start + t

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: _decode_source_frame(src, target_t, clip_end)
        )
    except Exception as exc:
        logger.warning("clip %s: reaction-source decode failed: %s", clip_id, exc)
        raise _err(
            404 if "at or after clip_end" in str(exc) or "no frame" in str(exc) else 500,
            "decode_failed", f"Could not decode frame: {exc}",
        )

    # The decoder froze the token while checking this image's file version.
    # Re-stamping here could attach a replacement file's identity to old pixels.
    version = result["source_version"]

    return Response(
        content=result["png"],
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Source-Width": str(result["src_w"]),
            "X-Source-Height": str(result["src_h"]),
            "X-Source-Time": str(result["decoded_time"]),
            "X-Source-Version": version,
            "X-Clip-Start": str(clip_start),
            "X-Clip-End": str(clip_end),
        },
    )


# ---------------------------------------------------------------------------
# PUT /clips/{id}/reaction-layout
# ---------------------------------------------------------------------------

@router.put("/clips/{clip_id}/reaction-layout")
async def put_reaction_layout(
    clip_id: str,
    payload: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Save a human-drawn reaction layout for this clip.

    Body: {content_rect:{x,y,w,h}, face_rect:{x,y,w,h},
           source_version:str, source_start:float, source_end:float,
           src_w:int, src_h:int}

    source_start/end must be finite non-boolean numbers equal to the current
    clip boundaries. Rects must be even-integer source pixels. Validates source
    identity, face aspect. Refuses an exporting clip. For automatic captions,
    resolves y_pct using the new plan safe_zones (manual captions are kept).
    """
    clip, project = await _load_clip_and_project(session, clip_id, lock=True)
    _reject_exporting(clip)

    src = _source_path(project)
    proj_w = int(project.width or 0)
    proj_h = int(project.height or 0)

    # --- required fields ---------------------------------------------------
    content_rect = payload.get("content_rect")
    face_rect = payload.get("face_rect")
    source_version = payload.get("source_version")
    if None in (content_rect, face_rect, source_version):
        raise _err(422, "missing_field",
                   "content_rect, face_rect, and source_version are required.")

    # --- src_w / src_h: integers, not bool ---------------------------------
    req_w = payload.get("src_w")
    req_h = payload.get("src_h")
    if (isinstance(req_w, bool) or not isinstance(req_w, int) or
            isinstance(req_h, bool) or not isinstance(req_h, int)):
        raise _err(422, "invalid_dimensions", "src_w and src_h must be integers.")
    if req_w <= 0 or req_h <= 0:
        raise _err(422, "invalid_dimensions", "src_w and src_h must be positive.")

    # --- project metadata consistency (clear error if stale) ---------------
    if req_w != proj_w or req_h != proj_h:
        raise _err(422, "wrong_source_dimensions",
                   f"src_w={req_w}×src_h={req_h} does not match project "
                   f"source {proj_w}×{proj_h}. Project metadata may be stale.")

    # --- source_version ----------------------------------------------------
    current_version = compute_source_version(src, req_w, req_h)
    if source_version != current_version:
        raise _err(409, "stale_source_version",
                   "Source file changed since this frame was captured. "
                   "Re-open the editor to get a fresh frame.")

    # --- source_start / source_end: finite, equal to clip boundaries -------
    source_start = payload.get("source_start")
    source_end = payload.get("source_end")
    bs = _require_finite_number(source_start, "source_start")
    be = _require_finite_number(source_end, "source_end")

    clip_start = float(clip.start_time or 0.0)
    clip_end = float(clip.end_time or 0.0)
    eps = 1e-4
    if abs(bs - clip_start) > eps or abs(be - clip_end) > eps:
        raise _err(422, "window_mismatch",
                   f"source_start/end [{bs:.4f}, {be:.4f}] must equal the "
                   f"current clip boundaries [{clip_start:.4f}, {clip_end:.4f}].")

    # --- face aspect (before builder to get a specific error) --------------
    if isinstance(face_rect, dict):
        fw = face_rect.get("w")
        fh = face_rect.get("h")
        if (isinstance(fw, bool) or not isinstance(fw, int) or
                isinstance(fh, bool) or not isinstance(fh, int)):
            raise _err(422, "invalid_face_rect", "face_rect.w and .h must be integers.")
        try:
            validate_face_aspect(fw, fh)
        except ValueError as exc:
            raise _err(422, "invalid_face_aspect", str(exc))

    # --- canonical builder (validates all rect geometry) -------------------
    try:
        plan = plan_reaction_layout(
            content_rect=content_rect,
            face_rect=face_rect,
            src_w=req_w,
            src_h=req_h,
            face_pct=0.40,
        )
    except ValueError as exc:
        raise _err(422, "invalid_geometry", str(exc))

    # --- attach binding ----------------------------------------------------
    plan["reaction_binding"] = {
        "schema": BINDING_SCHEMA,
        "source_version": current_version,
        "source_start": clip_start,
        "source_end": clip_end,
        "src_w": req_w,
        "src_h": req_h,
        "by": "human",
    }

    # --- automatic caption placement ---------------------------------------
    # Only resolve when the layer will actually be burned. A suppressed layer
    # with an unsupported style or a no-gap region must not block the PUT.
    # A ValueError is an actionable placement failure → 422. Any other
    # exception is unexpected and also produces a non-2xx response so that
    # no mutation reaches the DB.
    new_caption_plan = clip.caption_plan
    from services.clipper import caption_policy as cap_pol
    # SC3: a reaction framing renders on the STATIC path, where a stored blur cannot execute
    # (codex-verdict-next-34 R3/R4) — refused here rather than left to fail every later render.
    stored = getattr(clip, "source_caption_treatment", None)
    if isinstance(stored, dict) and stored.get("treatment") == "blur":
        raise _err(422, "static_path_unsupported",
                   "This clip blurs the source's text, which needs the multi-shot renderer; a reaction "
                   "framing renders statically. Set the source text to no treatment first.")
    _burn = (cap_pol.decide(
        (project.clipper_settings or {}).get(cap_pol.SETTING),
        clip_setting=clip.source_has_burned_captions,
        layer=getattr(clip, "caption_layer", None),
    )["action"] == cap_pol.BURN)
    if _burn and new_caption_plan and not new_caption_plan.get("y_pct_manual"):
        from services.clipper.reaction_captions import (
            NoCaptionGap, caption_ready_height, resolve_reaction_caption_y)
        try:
            new_y = resolve_reaction_caption_y(
                plan, new_caption_plan, clip_duration=clip_end - clip_start)
            if new_y is not None:
                new_caption_plan = {**new_caption_plan, "y_pct": new_y}
        except NoCaptionGap as exc:
            # Say WHICH box would pass; found by the same builder and resolver.
            h = caption_ready_height(content_rect, face_rect, req_w, req_h,
                                     new_caption_plan, face_pct=0.40,
                                     clip_duration=clip_end - clip_start)
            hint = (f" At this width ({content_rect['w']} px), a content height of at "
                    f"most {h} px leaves room for automatic captions." if h else "")
            raise _err(422, "caption_placement_failed", str(exc) + hint,
                       max_content_height=h) from exc
        except ValueError as exc:
            raise _err(422, "caption_placement_failed", str(exc)) from exc
        except Exception as exc:
            raise _err(500, "caption_placement_error",
                       "Caption placement failed unexpectedly. "
                       "Try again or set caption position manually.") from exc

    # --- commit ------------------------------------------------------------
    # Saving the framing already on the clip changes nothing: no event, and the
    # render made from it stays valid.
    if plan == clip.layout_plan and new_caption_plan == clip.caption_plan:
        return {"clip": clip_to_dict(clip, project)}
    clip.layout_plan = plan
    if new_caption_plan is not clip.caption_plan:
        clip.caption_plan = new_caption_plan
    _invalidate_render(clip)

    # `add`, not `record`: the event and the edit are one commit (B1).
    await feedback_mod.add(
        session, clip_id, clip.project_id,
        "layout_changed",
        payload={"reaction_layout": True, "by": "human"},
        origin=feedback_mod.ORIGIN_MANUAL,
    )
    await session.commit()
    return {"clip": clip_to_dict(clip, project)}


# ---------------------------------------------------------------------------
# DELETE /clips/{id}/reaction-layout
# ---------------------------------------------------------------------------

@router.delete("/clips/{clip_id}/reaction-layout")
async def delete_reaction_layout(
    clip_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return this clip to automatic framing.

    Clears any plan with game_content_fit=True — including malformed or
    unbound plans — so a user is never stuck with an export refusal and no
    UI recovery path. Invalidates the render and logs manual feedback.
    If no such plan exists, no mutation is performed.
    Does not delete files or change project settings.
    """
    clip, project = await _load_clip_and_project(session, clip_id, lock=True)

    plan = clip.layout_plan
    has_reaction = isinstance(plan, dict) and plan.get("game_content_fit") is True
    if not has_reaction:
        return {"clip": clip_to_dict(clip, project)}

    _reject_exporting(clip)

    clip.layout_plan = None
    _invalidate_render(clip)

    await feedback_mod.add(
        session, clip_id, clip.project_id,
        "layout_changed",
        payload={"reaction_layout_cleared": True, "by": "human"},
        origin=feedback_mod.ORIGIN_MANUAL,
    )
    await session.commit()
    return {"clip": clip_to_dict(clip, project)}
