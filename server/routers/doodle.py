"""
ClipForge — Auto Story Doodle Video router.

Topic → OpenAI script + scene split + image prompts → Kokoro TTS voiceover →
user drags Manual Flow images into scene slots → FFmpeg assembles the final
MP4. See PRPs/auto-story-doodle.md for the full contract this implements.
"""

from __future__ import annotations

import logging
import zipfile
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from database import async_session
from models import JobModel, JobStatus, JobType
from routers.doodle_images import router as doodle_images_router
from services.doodle import image_providers, storage

logger = logging.getLogger("clipforge.routers.doodle")
router = APIRouter(prefix="/api/doodle", tags=["doodle"])
router.include_router(doodle_images_router)


def _err(code: str, message: str, details: str | None = None) -> dict:
    """Structured error detail: {error, message, details} — the frontend
    surfaces `message` in toasts and logs the whole object to the console."""
    return {"error": code, "message": message, "details": details}


# ── Request schemas ──────────────────────────────────────────────────────────

class NewProjectRequest(BaseModel):
    mode: str = "topic"  # "topic" | "script"
    topic: Optional[str] = None
    script_text: Optional[str] = None
    niche: str = "history"
    custom_niche: Optional[str] = None

    target_duration_seconds: int = 180
    frame_interval_seconds: object = 3  # int 2/3/4 or "auto"
    aspect_ratio: str = "16:9"

    voice: str = "am_michael"
    voice_speed: Optional[float] = 0.95
    subtitle_style: Optional[str] = "youtube_clean"
    burn_subtitles: Optional[bool] = True
    motion_style: Optional[str] = "subtle"
    motion_intensity: Optional[float] = 0.5
    openai_model: Optional[str] = None
    render_quality: Optional[str] = "high"
    use_gpu: Optional[bool] = True


class RenderRequest(BaseModel):
    allow_placeholders: Optional[bool] = False
    # "none" | "minimal_bottom" | "youtube_clean" | "tiktok_big" — overrides
    # the project's stored subtitle settings for this render only.
    subtitle_mode: Optional[str] = None


class VoiceoverRequest(BaseModel):
    # When true, only scenes without a valid audio file/duration are
    # (re)voiced — existing scene audio is kept untouched.
    only_missing: Optional[bool] = False


class GenerateImagesRequest(BaseModel):
    only_missing: Optional[bool] = True
    scene_indexes: Optional[list[int]] = None


class SettingsPatch(BaseModel):
    voice: Optional[str] = None
    voice_speed: Optional[float] = None
    subtitle_style: Optional[str] = None
    burn_subtitles: Optional[bool] = None
    motion_style: Optional[str] = None
    motion_intensity: Optional[float] = None
    openai_model: Optional[str] = None
    render_quality: Optional[str] = None
    use_gpu: Optional[bool] = None
    allow_placeholders: Optional[bool] = None
    target_duration_seconds: Optional[int] = None
    frame_interval_seconds: Optional[object] = None
    aspect_ratio: Optional[str] = None
    image_provider: Optional[str] = None


# ── Voices / providers ───────────────────────────────────────────────────────

@router.get("/voices")
async def get_voices():
    try:
        from services.doodle import kokoro_service
        ok, reason = kokoro_service.is_available()
        voices = kokoro_service.VOICES
    except Exception as e:
        logger.warning(f"kokoro_service unavailable: {e}")
        ok, reason, voices = False, str(e), []
    return {"available": ok, "reason": None if ok else reason, "voices": voices}


@router.get("/image-providers")
async def get_image_providers():
    return image_providers.list_providers()


@router.get("/comfy/status")
async def get_comfy_status():
    from services.doodle import comfy_provider  # lazy — keeps router import light
    return await comfy_provider.get_comfy_status()


# ── Projects ─────────────────────────────────────────────────────────────────

@router.get("/projects")
async def list_projects():
    return storage.list_projects()


@router.post("/projects")
async def create_project(req: NewProjectRequest):
    """Create the project ONLY: validate, make folders, save storyboard.json.
    Never touches OpenAI / Kokoro / FFmpeg — script generation, voiceover and
    render are separate steps with their own dependency checks."""
    logger.info(
        f"POST /api/doodle/projects mode={req.mode!r} niche={req.niche!r} "
        f"topic={(req.topic or '')[:80]!r} duration={req.target_duration_seconds}s "
        f"interval={req.frame_interval_seconds!r} ar={req.aspect_ratio!r} voice={req.voice!r}"
    )

    if req.mode not in ("topic", "script"):
        raise HTTPException(400, detail=_err("INVALID_MODE", "mode must be 'topic' or 'script'"))
    if req.mode == "topic" and not (req.topic or "").strip():
        raise HTTPException(400, detail=_err("TOPIC_REQUIRED", "Enter a topic for From Topic mode."))
    if req.mode == "script" and not (req.script_text or "").strip():
        raise HTTPException(400, detail=_err("SCRIPT_REQUIRED", "Paste your script for From Script mode."))

    niche = req.custom_niche.strip() if (req.niche == "custom" and req.custom_niche) else req.niche

    payload = {
        "mode": req.mode,
        "topic": req.topic,
        "script_text": req.script_text,
        "niche": niche,
        "target_duration_seconds": req.target_duration_seconds,
        "frame_interval_seconds": req.frame_interval_seconds,
        "aspect_ratio": req.aspect_ratio,
        "voice": req.voice,
        "voice_speed": req.voice_speed,
        "subtitle_style": req.subtitle_style,
        "burn_subtitles": req.burn_subtitles,
        "motion_style": req.motion_style,
        "motion_intensity": req.motion_intensity,
        "openai_model": req.openai_model,
        "render_quality": req.render_quality,
        "use_gpu": req.use_gpu,
    }

    try:
        sb = storage.create_project(payload)
    except OSError as e:
        logger.exception("doodle project folder creation failed")
        raise HTTPException(500, detail=_err(
            "PROJECT_CREATE_FAILED",
            "Project folder could not be created. Check disk space and permissions.",
            str(e),
        ))
    except Exception as e:
        logger.exception("doodle project creation failed")
        raise HTTPException(500, detail=_err(
            "PROJECT_CREATE_FAILED", "Could not create the project.", str(e),
        ))

    project_id = sb["id"]
    logger.info(f"doodle project {project_id} created at {storage.project_dir(project_id)}")
    return {"success": True, "projectId": project_id, "project": sb}


@router.post("/projects/{project_id}/script")
async def start_script(project_id: str):
    """Generate the script/storyboard for an existing project (background job).
    This is the ONLY step that needs the OpenAI key — checked here so the
    failure is an immediate, clear 400 instead of a silently failed job."""
    sb = _load_or_404(project_id)

    from services.transcript_cleaner import get_openai_key  # lazy — keeps router import light
    if not get_openai_key():
        raise HTTPException(400, detail=_err(
            "OPENAI_KEY_MISSING",
            "OpenAI API key is not configured. Add it in Settings → Transcript (OpenAI), "
            "or set the OPENAI_API_KEY environment variable, then try again. "
            "The project was created and is saved — nothing is lost.",
        ))

    if sb.get("status") in ("scripting", "voicing", "rendering"):
        raise HTTPException(409, detail=_err(
            "BUSY", f"Project is busy ({sb['status']}). Wait for the current step to finish."))

    job_id = await _enqueue(project_id, JobType.doodle_script.value, {
        "mode": sb.get("mode") or "topic",
        "topic": sb.get("topic"),
        "script_text": sb.get("script_text"),
        "niche": sb.get("niche"),
    })
    sb["status"] = "scripting"
    sb["error"] = None
    storage.save_storyboard(project_id, sb)
    logger.info(f"doodle project {project_id}: script job {job_id} enqueued")
    return {"success": True, "job_id": job_id}


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    try:
        sb = storage.load_storyboard(project_id)
    except FileNotFoundError:
        raise HTTPException(404, "Doodle project not found")
    sb = dict(sb)
    sb["missing_images"] = storage.missing_images(sb)
    return sb


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    storage.delete_project(project_id)
    return {"status": "deleted", "id": project_id}


@router.post("/projects/{project_id}/voiceover")
async def start_voiceover(project_id: str, req: Optional[VoiceoverRequest] = None):
    sb = _load_or_404(project_id)
    if not sb.get("scenes"):
        raise HTTPException(409, detail=_err(
            "NO_SCENES", "Project has no scenes yet — generate the script first."))
    if sb.get("status") in ("scripting", "voicing", "rendering"):
        raise HTTPException(409, detail=_err(
            "BUSY", f"Project is busy ({sb['status']}). Wait for the current step to finish."))

    # Kokoro is only required HERE, never at project creation.
    try:
        from services.doodle import kokoro_service
        ok, reason = kokoro_service.is_available()
    except Exception as e:
        ok, reason = False, str(e)
    if not ok:
        raise HTTPException(400, detail=_err(
            "KOKORO_UNAVAILABLE",
            "Kokoro TTS is not available. The project is fine — install/fix Kokoro, then retry.",
            reason,
        ))

    job_id = await _enqueue(project_id, JobType.doodle_tts.value, {
        "only_missing": bool(req.only_missing) if req else False,
    })
    sb["status"] = "voicing"
    sb["error"] = None
    storage.save_storyboard(project_id, sb)
    return {"job_id": job_id}


@router.post("/projects/{project_id}/generate-images")
async def start_generate_images(project_id: str, req: Optional[GenerateImagesRequest] = None):
    sb = _load_or_404(project_id)
    if not sb.get("scenes"):
        raise HTTPException(409, detail=_err(
            "NO_SCENES", "Project has no scenes yet — generate the script first."))

    from services.doodle import comfy_provider, comfy_workflows  # lazy

    status = await comfy_provider.get_comfy_status()
    if not status["any_alive"]:
        raise HTTPException(400, detail=_err(
            "COMFY_UNAVAILABLE",
            "No ComfyUI GPU is reachable. Start ComfyUI first using scripts/start_comfy_all.bat",
        ))
    if not comfy_workflows.model_file_found("sdxl_turbo"):
        raise HTTPException(400, detail=_err(
            "COMFY_MODEL_MISSING",
            "SDXL Turbo checkpoint file not found in ComfyUI's models/checkpoints folder. "
            "Wait for the download to finish, then retry.",
        ))

    only_missing = bool(req.only_missing) if req and req.only_missing is not None else True
    scene_indexes = req.scene_indexes if req else None

    job_id = await _enqueue(project_id, JobType.doodle_images.value, {
        "only_missing": only_missing,
        "scene_indexes": scene_indexes,
    })
    return {"job_id": job_id}


@router.post("/projects/{project_id}/render")
async def start_render(project_id: str, req: RenderRequest):
    sb = _load_or_404(project_id)

    # FFmpeg is only required HERE, never at project creation.
    import shutil as _shutil
    if not _shutil.which("ffmpeg") or not _shutil.which("ffprobe"):
        raise HTTPException(400, detail=_err(
            "FFMPEG_MISSING",
            "FFmpeg/ffprobe not found on PATH. Install FFmpeg (winget install Gyan.FFmpeg), "
            "restart the backend, then retry. The project is fine.",
        ))

    # Voiceover must exist BEFORE render — block here with a clear 409 instead
    # of letting the render job fail and mark the whole project "failed".
    # Images and all other project state are untouched by this check.
    scenes = sb.get("scenes") or []
    no_audio = [int(s.get("index", 0)) for s in scenes if not s.get("audio_duration")]
    if no_audio:
        raise HTTPException(409, detail={
            "error": "VOICE_REQUIRED",
            "message": (
                f"{len(no_audio)} of {len(scenes)} scene(s) have no voiceover yet. "
                "Images are safe. Generate voiceover before rendering."
            ),
            "missing_audio_scenes": no_audio,
        })

    missing = storage.missing_images(sb)
    allow_placeholders = bool(req.allow_placeholders)
    if missing and not allow_placeholders:
        raise HTTPException(
            409,
            detail={
                "error": "MISSING_IMAGES",
                "message": f"{len(missing)} scene(s) missing images",
                "missing_scenes": missing,
            },
        )

    if allow_placeholders:
        sb.setdefault("settings", {})["allow_placeholders"] = True
        storage.save_storyboard(project_id, sb)

    from services.doodle.subtitles import normalize_subtitle_mode  # lazy
    if req.subtitle_mode:
        mode = normalize_subtitle_mode(req.subtitle_mode)
    elif not (sb.get("settings") or {}).get("burn_subtitles", True):
        mode = "none"
    else:
        mode = normalize_subtitle_mode((sb.get("settings") or {}).get("subtitle_style"))

    job_id = await _enqueue(project_id, JobType.doodle_render.value, {
        "allow_placeholders": allow_placeholders,
        "subtitle_mode": mode,
    })
    sb["status"] = "rendering"
    sb.setdefault("renders", {})[mode] = {"status": "rendering", "path": None, "error": None}
    storage.save_storyboard(project_id, sb)
    return {"job_id": job_id, "subtitle_mode": mode}


@router.post("/projects/{project_id}/backup-images")
async def backup_images(project_id: str):
    """Zip every uploaded scene image (numbered scene_XXX.<ext>) into
    exports/project_images_backup_numbered.zip. Read-only for the images —
    originals are never moved, renamed, or deleted."""
    sb = _load_or_404(project_id)
    pdir = storage.project_dir(project_id)
    zip_path = pdir / "exports" / "project_images_backup_numbered.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for s in sorted(sb.get("scenes") or [], key=lambda s: int(s.get("index", 0))):
                image_path = s.get("image_path")
                if not image_path:
                    continue
                src = pdir / image_path
                if not src.exists():
                    continue
                zf.write(src, f"scene_{int(s.get('index', 0)):03d}{src.suffix}")
                count += 1
    except Exception as e:
        logger.exception(f"image backup failed for {project_id}")
        raise HTTPException(500, detail=_err(
            "BACKUP_FAILED", "Could not create the image backup ZIP.", str(e)))

    logger.info(f"doodle {project_id}: backed up {count} images -> {zip_path}")
    return {
        "success": True,
        "count": count,
        "zip_path": "exports/project_images_backup_numbered.zip",
    }


# ── Images ───────────────────────────────────────────────────────────────────
# Routes moved to routers/doodle_images.py (kept this file under the 500-line
# limit) and merged below via include_router — paths are unchanged.


# ── Reorder ──────────────────────────────────────────────────────────────────
# Route moved to routers/doodle_images.py (kept this file under the 500-line
# limit) and merged above via include_router — path is unchanged.


# ── Settings ─────────────────────────────────────────────────────────────────

@router.patch("/projects/{project_id}/settings")
async def patch_settings(project_id: str, patch: SettingsPatch):
    sb = _load_or_404(project_id)
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    settings_obj = sb.setdefault("settings", {})
    settings_obj.update(updates)
    if "aspect_ratio" in updates:
        settings_obj["resolution"] = storage._RESOLUTION_BY_RATIO.get(
            updates["aspect_ratio"], settings_obj.get("resolution")
        )
    storage.save_storyboard(project_id, sb)
    return sb


# ── Prompt exports ───────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/prompts.csv")
async def download_prompts_csv(project_id: str):
    sb = _load_or_404(project_id)
    storage.write_prompt_exports(project_id, sb)
    path = storage.project_dir(project_id) / "prompts" / "flow_prompts.csv"
    if not path.exists():
        raise HTTPException(404, "prompts not generated yet")
    return FileResponse(path, media_type="text/csv", filename="flow_prompts.csv")


@router.get("/projects/{project_id}/prompts.json")
async def download_prompts_json(project_id: str):
    sb = _load_or_404(project_id)
    storage.write_prompt_exports(project_id, sb)
    path = storage.project_dir(project_id) / "prompts" / "flow_prompts.json"
    if not path.exists():
        raise HTTPException(404, "prompts not generated yet")
    return FileResponse(path, media_type="application/json", filename="flow_prompts.json")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_or_404(project_id: str) -> dict:
    try:
        return storage.load_storyboard(project_id)
    except FileNotFoundError:
        raise HTTPException(404, "Doodle project not found")


async def _enqueue(project_id: str, job_type: str, metadata: dict) -> str:
    import json as _json
    async with async_session() as session:
        job = JobModel(
            project_id=project_id,
            type=job_type,
            status=JobStatus.queued.value,
            metadata_json=_json.dumps(metadata),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id
