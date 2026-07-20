"""
ClipForge — Auto Story Doodle pipeline workers.

Three job types drive one doodle project through its lifecycle:
    doodle_script  — topic/script -> full storyboard (title/scenes/prompts)
    doodle_tts     — Kokoro voiceover per scene + concatenated final track
    doodle_render  — FFmpeg assembly of the final MP4

IMPORTANT: script_generator / kokoro_service / renderer are owned by sibling
agents writing them in parallel. Import them LAZILY inside each handler
function (not at module top) so this module always imports cleanly even if
those files are momentarily missing or broken during parallel development.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from job_queue import JobCancelledError
from models import JobType
from services.doodle import storage

logger = logging.getLogger("clipforge.doodle_pipeline")


async def handle_doodle_script(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict[str, Any],
    queue,
):
    """topic/script -> storyboard scenes + image prompts."""
    from services.doodle.script_generator import generate_storyboard  # lazy

    sb = storage.load_storyboard(project_id)
    sb["status"] = "scripting"
    storage.save_storyboard(project_id, sb)

    settings_obj = sb.get("settings") or {}

    async def _progress(fraction: float, message: str) -> None:
        if queue.is_cancelled(job_id):
            raise JobCancelledError()
        await queue.update_progress(job_id, min(max(fraction, 0.0), 1.0), message)

    try:
        result = await generate_storyboard(
            mode=metadata.get("mode") or sb.get("mode") or "topic",
            topic=metadata.get("topic") or sb.get("topic"),
            script_text=metadata.get("script_text") or sb.get("script_text"),
            niche=metadata.get("niche") or sb.get("niche") or "history",
            target_duration_seconds=settings_obj.get("target_duration_seconds", 180),
            frame_interval_seconds=settings_obj.get("frame_interval_seconds", 3),
            aspect_ratio=settings_obj.get("aspect_ratio", "16:9"),
            model=settings_obj.get("openai_model"),
            progress_cb=_progress,
        )
    except JobCancelledError:
        raise
    except Exception as e:
        sb = storage.load_storyboard(project_id)
        sb["status"] = "failed"
        sb["error"] = str(e)
        storage.save_storyboard(project_id, sb)
        raise

    sb = storage.load_storyboard(project_id)
    sb["title"] = result.get("title") or sb.get("title") or ""
    sb["description"] = result.get("description") or ""
    sb["tags"] = result.get("tags") or []
    sb["scenes"] = result.get("scenes") or []
    sb["status"] = "script_ready"
    sb["error"] = None
    storage.save_storyboard(project_id, sb)
    storage.write_prompt_exports(project_id, sb)

    logger.info(f"doodle_script {job_id}: project {project_id} -> {len(sb['scenes'])} scenes")


async def handle_doodle_tts(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict[str, Any],
    queue,
):
    """Per-scene Kokoro voiceover + concatenated final track."""
    from services.doodle.kokoro_service import (  # lazy
        concatenate_audio_files,
        generate_all_scene_audio,
    )

    sb = storage.load_storyboard(project_id)
    scenes = sb.get("scenes") or []
    if not scenes:
        raise RuntimeError("Project has no scenes to voice")

    sb["status"] = "voicing"
    storage.save_storyboard(project_id, sb)

    voice = (sb.get("settings") or {}).get("voice", "am_michael")
    speed = (sb.get("settings") or {}).get("voice_speed", 0.95)
    pdir = storage.project_dir(project_id)
    audio_dir = pdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # only_missing: keep scenes that already have a valid wav + duration and
    # voice just the rest (file names are per-index, so partial runs are safe).
    def _has_audio(s: dict) -> bool:
        ap = s.get("audio_path")
        return bool(ap) and (pdir / ap).exists() and bool(s.get("audio_duration"))

    if metadata.get("only_missing"):
        to_voice = [s for s in scenes if not _has_audio(s)]
    else:
        to_voice = list(scenes)
    if not to_voice:
        logger.info(f"doodle_tts {job_id}: nothing to voice (all scenes have audio)")

    async def _progress(done: int, message: str) -> None:
        if queue.is_cancelled(job_id):
            raise JobCancelledError()
        total = max(len(to_voice), 1)
        await queue.update_progress(job_id, min(0.05 + 0.85 * (done / total), 0.9), message)

    try:
        # generate_all_scene_audio mutates the scene dicts in-place (same
        # objects as in `scenes`), so the full list stays consistent.
        await generate_all_scene_audio(
            to_voice, voice, speed, audio_dir, progress_cb=_progress,
        )
        updated_scenes = scenes
    except JobCancelledError:
        raise
    except Exception as e:
        sb = storage.load_storyboard(project_id)
        sb["status"] = "failed"
        sb["error"] = str(e)
        storage.save_storyboard(project_id, sb)
        raise

    await queue.update_progress(job_id, 0.92, "Concatenating final voiceover…")
    final_path = storage.project_dir(project_id) / "audio" / "final_voiceover.wav"
    files = [storage.project_dir(project_id) / (s.get("audio_path") or "") for s in updated_scenes]
    total_duration = concatenate_audio_files(files, final_path)

    sb = storage.load_storyboard(project_id)
    sb["scenes"] = updated_scenes
    sb["final_voiceover_path"] = "audio/final_voiceover.wav"
    sb["total_audio_duration"] = total_duration
    sb["status"] = "voice_ready"
    sb["error"] = None
    storage.save_storyboard(project_id, sb)

    await queue.update_progress(job_id, 1.0, "Voiceover ready")
    logger.info(f"doodle_tts {job_id}: project {project_id} -> {total_duration:.1f}s voiceover")


async def handle_doodle_render(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict[str, Any],
    queue,
):
    """Assemble the final MP4 with FFmpeg (images + voiceover + subtitles + motion).

    Each subtitle mode ("none" / "minimal_bottom" / "youtube_clean" /
    "tiktok_big") renders to its own output file and tracks its own status in
    storyboard["renders"][mode] — one failed style never marks the whole
    project failed.
    """
    from services.doodle.renderer import render_video  # lazy
    from services.doodle.subtitles import normalize_subtitle_mode  # lazy

    sb = storage.load_storyboard(project_id)

    if metadata.get("allow_placeholders"):
        sb.setdefault("settings", {})["allow_placeholders"] = True

    # Preconditions: these mean "render is blocked", NOT "project failed" —
    # the storyboard status is left as-is so nothing else is lost. The router
    # already 409s on both cases; this is defense in depth.
    no_audio = [int(s.get("index", 0)) for s in sb.get("scenes") or [] if not s.get("audio_duration")]
    if no_audio:
        msg = (
            f"Render blocked: {len(no_audio)} scene(s) have no voiceover yet "
            f"(scenes {no_audio[:8]}{'…' if len(no_audio) > 8 else ''}). "
            "Images are safe. Generate voiceover before rendering."
        )
        sb["error"] = msg
        storage.save_storyboard(project_id, sb)
        raise RuntimeError(msg)

    missing = storage.missing_images(sb)
    if missing and not (sb.get("settings") or {}).get("allow_placeholders", False):
        msg = f"Render blocked: missing images for scenes {missing}. Upload them or render with placeholders."
        sb["error"] = msg
        storage.save_storyboard(project_id, sb)
        raise RuntimeError(msg)

    # Effective mode for this render: explicit override from the request,
    # else the project's stored subtitle settings.
    if metadata.get("subtitle_mode"):
        mode = normalize_subtitle_mode(metadata["subtitle_mode"])
    elif not (sb.get("settings") or {}).get("burn_subtitles", True):
        mode = "none"
    else:
        mode = normalize_subtitle_mode((sb.get("settings") or {}).get("subtitle_style"))

    prev_status = sb.get("status")
    sb["status"] = "rendering"
    renders = sb.setdefault("renders", {})
    renders[mode] = {"status": "rendering", "path": None, "error": None}
    storage.save_storyboard(project_id, sb)

    async def _progress(fraction: float, message: str) -> None:
        if queue.is_cancelled(job_id):
            raise JobCancelledError()
        await queue.update_progress(job_id, min(max(fraction, 0.0), 1.0), message)

    try:
        export_path: Path = await render_video(
            storage.project_dir(project_id), sb, progress_cb=_progress,
            subtitle_mode=mode,
        )
    except JobCancelledError:
        raise
    except Exception as e:
        sb = storage.load_storyboard(project_id)
        renders = sb.setdefault("renders", {})
        renders[mode] = {"status": "failed", "path": None, "error": str(e)}
        # Only THIS render style failed — never mark the whole project failed.
        any_done = any((r or {}).get("status") == "done" for r in renders.values())
        if any_done:
            sb["status"] = "done"
        else:
            sb["status"] = prev_status if prev_status not in ("rendering", "failed") else "voice_ready"
        sb["error"] = f"[render:{mode}] {e}"
        storage.save_storyboard(project_id, sb)
        raise

    rel_path = str(Path(export_path).relative_to(storage.project_dir(project_id)))
    sb = storage.load_storyboard(project_id)
    renders = sb.setdefault("renders", {})
    renders[mode] = {"status": "done", "path": rel_path, "error": None}
    sb["export_path"] = rel_path  # most recent successful render
    sb["status"] = "done"
    sb["error"] = None
    storage.save_storyboard(project_id, sb)

    _write_video_description(export_path, sb)

    logger.info(f"doodle_render {job_id}: project {project_id} [{mode}] -> {export_path}")


def _write_video_description(export_path: Path, sb: Dict[str, Any]) -> None:
    """Deliver the description WITH the video: <video>.description.txt next to
    the MP4 (title + description + tags as hashtags), ready to paste when
    posting. Best-effort — a write failure never fails the render."""
    try:
        lines: list[str] = []
        title = (sb.get("title") or "").strip()
        desc = (sb.get("description") or "").strip()
        if title:
            lines.append(title)
        if desc:
            if lines:
                lines.append("")
            lines.append(desc)
        tags = [t.strip() for t in (sb.get("tags") or []) if t and str(t).strip()]
        if tags:
            hashtags = " ".join(
                "#" + re.sub(r"[^\w]+", "", str(t).title()) for t in tags
            )
            if hashtags.strip("#"):
                if lines:
                    lines.append("")
                lines.append(hashtags)
        if lines:
            out = Path(export_path).with_suffix(".description.txt")
            out.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"description written -> {out.name}")
    except Exception:
        logger.exception("description.txt write failed (video is fine)")


async def handle_doodle_images(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict[str, Any],
    queue,
):
    """Generate scene images via local ComfyUI across both GPUs in parallel.
    Only scenes without an existing image are touched when only_missing is
    true (default) — scenes that already have an image are NEVER regenerated
    unless explicitly listed in scene_indexes."""
    from datetime import datetime, timezone

    from services.doodle.comfy_provider import generate_project_images_parallel  # lazy

    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    sb = storage.load_storyboard(project_id)
    image_gen = sb.setdefault("image_generation", {})
    image_gen["status"] = "running"
    image_gen["model"] = "sdxl_turbo"
    image_gen["generated"] = 0
    image_gen["failed"] = []
    image_gen["updated_at"] = _now_iso()
    storage.save_storyboard(project_id, sb)

    async def _progress(fraction: float, message: str) -> None:
        if queue.is_cancelled(job_id):
            raise JobCancelledError()
        await queue.update_progress(job_id, min(max(fraction, 0.0), 1.0), message)

    try:
        result = await generate_project_images_parallel(
            project_id,
            scene_indexes=metadata.get("scene_indexes"),
            only_missing=metadata.get("only_missing", True),
            progress_cb=_progress,
        )
    except JobCancelledError:
        raise
    except Exception as e:
        sb = storage.load_storyboard(project_id)
        image_gen = sb.setdefault("image_generation", {})
        image_gen["status"] = "failed"
        image_gen["updated_at"] = _now_iso()
        sb["error"] = str(e)
        storage.save_storyboard(project_id, sb)
        raise

    sb = storage.load_storyboard(project_id)
    image_gen = sb.setdefault("image_generation", {})
    image_gen["generated"] = result["generated"]
    image_gen["failed"] = result["failed"]
    image_gen["model"] = result["model"]
    image_gen["updated_at"] = _now_iso()
    # Only every image failing marks the batch failed — partial success
    # (some scenes got images) is still "done" so the UI shows progress.
    total_attempted = result["generated"] + len(result["failed"])
    if total_attempted > 0 and result["generated"] == 0:
        image_gen["status"] = "failed"
        sb["error"] = "All image generations failed — see image_generation.failed for details."
    else:
        image_gen["status"] = "done"
        sb["error"] = None
    storage.save_storyboard(project_id, sb)

    logger.info(
        f"doodle_images {job_id}: project {project_id} -> "
        f"{result['generated']} generated, {len(result['failed'])} failed"
    )


def register_doodle_handlers(queue) -> None:
    queue.register_handler(JobType.doodle_script.value, handle_doodle_script)
    queue.register_handler(JobType.doodle_tts.value, handle_doodle_tts)
    queue.register_handler(JobType.doodle_render.value, handle_doodle_render)
    queue.register_handler(JobType.doodle_images.value, handle_doodle_images)
    logger.info("Doodle pipeline handlers registered")
