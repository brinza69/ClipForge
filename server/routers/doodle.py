"""
ClipForge — Auto Story Doodle Video router.

Topic → OpenAI script + scene split + image prompts → Kokoro TTS voiceover →
user drags Manual Flow images into scene slots → FFmpeg assembles the final
MP4. See PRPs/auto-story-doodle.md for the full contract this implements.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from database import async_session
from models import JobModel, JobStatus, JobType
from services.doodle import image_providers, storage

logger = logging.getLogger("clipforge.routers.doodle")
router = APIRouter(prefix="/api/doodle", tags=["doodle"])

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


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


class ReorderRequest(BaseModel):
    order: list[int]


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
# NOTE: /images/bulk MUST be registered before /images/{scene_index} — FastAPI
# matches path routes in registration order, and {scene_index}'s int converter
# would otherwise 422 on the literal "bulk" segment before this route is tried.

@router.post("/projects/{project_id}/images/bulk")
async def upload_scene_images_bulk(project_id: str, files: list[UploadFile] = File(...)):
    sb = _load_or_404(project_id)
    scenes = sb.get("scenes") or []

    matched = 0
    unmatched: list[str] = []

    async def _match_and_save(name: str, data: bytes) -> bool:
        idx = _extract_scene_index(name)
        if idx is None:
            return False
        scene = _find_scene(sb, idx)
        if scene is None:
            return False
        ext = Path(name).suffix.lower()
        if ext not in _IMAGE_EXTS:
            ext = ".png"
        flow_filename = scene.get("flow_filename") or f"scene_{idx:03d}.png"
        dest_name = Path(flow_filename).stem + ext
        dest = storage.project_dir(project_id) / "images" / dest_name
        _remove_scene_image_file(project_id, scene)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        scene["image_path"] = f"images/{dest_name}"
        return True

    for upload in files:
        name = upload.filename or ""
        data = await upload.read()
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for zi in zf.infolist():
                        if zi.is_dir():
                            continue
                        inner_name = Path(zi.filename).name
                        if Path(inner_name).suffix.lower() not in _IMAGE_EXTS:
                            continue
                        inner_data = zf.read(zi)
                        if await _match_and_save(inner_name, inner_data):
                            matched += 1
                        else:
                            unmatched.append(inner_name)
            except zipfile.BadZipFile:
                unmatched.append(name)
            continue

        if Path(name).suffix.lower() not in _IMAGE_EXTS:
            unmatched.append(name)
            continue
        if await _match_and_save(name, data):
            matched += 1
        else:
            unmatched.append(name)

    storage.save_storyboard(project_id, sb)
    return {"matched": matched, "unmatched": unmatched}


@router.post("/projects/{project_id}/images/{scene_index}")
async def upload_scene_image(project_id: str, scene_index: int, file: UploadFile = File(...)):
    sb = _load_or_404(project_id)
    scene = _find_scene(sb, scene_index)
    if scene is None:
        raise HTTPException(404, f"Scene {scene_index} not found")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty upload")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in _IMAGE_EXTS:
        ext = ".png"
    flow_filename = scene.get("flow_filename") or f"scene_{scene_index:03d}.png"
    dest_name = Path(flow_filename).stem + ext
    dest = storage.project_dir(project_id) / "images" / dest_name

    # Clear any previously stored image under a different extension.
    _remove_scene_image_file(project_id, scene)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    scene["image_path"] = f"images/{dest_name}"
    storage.save_storyboard(project_id, sb)
    return scene


@router.delete("/projects/{project_id}/images/{scene_index}")
async def delete_scene_image(project_id: str, scene_index: int):
    sb = _load_or_404(project_id)
    scene = _find_scene(sb, scene_index)
    if scene is None:
        raise HTTPException(404, f"Scene {scene_index} not found")
    _remove_scene_image_file(project_id, scene)
    scene["image_path"] = None
    storage.save_storyboard(project_id, sb)
    return scene


# ── Reorder ──────────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/scenes/reorder")
async def reorder_scenes(project_id: str, req: ReorderRequest):
    sb = _load_or_404(project_id)
    scenes = sb.get("scenes") or []
    by_index = {int(s["index"]): s for s in scenes}

    if sorted(req.order) != sorted(by_index.keys()):
        raise HTTPException(400, "order must be a permutation of existing scene indexes")

    pdir = storage.project_dir(project_id)
    images_dir = pdir / "images"
    audio_dir = pdir / "audio"

    # Stage renames through temp names first to avoid collisions when indexes
    # shuffle (e.g. swapping 0 and 1 would otherwise overwrite mid-loop).
    staged: list[tuple[dict, int, int]] = []  # (scene, old_index, new_index)
    for new_index, old_index in enumerate(req.order):
        staged.append((by_index[old_index], old_index, new_index))

    tmp_suffix = "__reorder_tmp__"
    for scene, old_index, new_index in staged:
        if old_index == new_index:
            continue
        _rename_scene_files(images_dir, scene, old_index, f"{tmp_suffix}{new_index}", is_image=True)
        _rename_scene_files(audio_dir, scene, old_index, f"{tmp_suffix}{new_index}", is_image=False)

    new_scenes: list[dict] = []
    for scene, old_index, new_index in staged:
        if old_index != new_index:
            _rename_scene_files(images_dir, scene, f"{tmp_suffix}{new_index}", new_index, is_image=True)
            _rename_scene_files(audio_dir, scene, f"{tmp_suffix}{new_index}", new_index, is_image=False)
        scene["index"] = new_index
        scene["flow_filename"] = f"scene_{new_index:03d}.png"
        new_scenes.append(scene)

    sb["scenes"] = new_scenes
    storage.save_storyboard(project_id, sb)
    storage.write_prompt_exports(project_id, sb)
    return sb


def _rename_scene_files(directory: Path, scene: dict, old_index, new_index, is_image: bool) -> None:
    """Rename a scene's on-disk file(s) from *_{old_index} to *_{new_index}
    (index args may be int or a str temp suffix) and update the scene dict."""
    if not directory.exists():
        return
    old_stem = f"scene_{int(old_index):03d}" if isinstance(old_index, int) else f"scene_{old_index}"
    new_stem = f"scene_{int(new_index):03d}" if isinstance(new_index, int) else f"scene_{new_index}"

    # Find any file in `directory` whose stem matches old_stem regardless of ext.
    for candidate in directory.glob(f"{old_stem}.*"):
        new_path = directory / f"{new_stem}{candidate.suffix}"
        try:
            if new_path.exists():
                new_path.unlink()
            candidate.rename(new_path)
            if is_image:
                scene["image_path"] = f"images/{new_path.name}"
            else:
                scene["audio_path"] = f"audio/{new_path.name}"
        except Exception:
            logger.exception(f"reorder rename failed: {candidate} -> {new_path}")


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


def _find_scene(sb: dict, index: int) -> Optional[dict]:
    for s in sb.get("scenes") or []:
        if int(s.get("index", -1)) == index:
            return s
    return None


def _remove_scene_image_file(project_id: str, scene: dict) -> None:
    image_path = scene.get("image_path")
    if not image_path:
        return
    p = storage.project_dir(project_id) / image_path
    if p.exists():
        try:
            p.unlink()
        except Exception:
            logger.exception(f"could not remove old image {p}")


_SCENE_NUM_RE = re.compile(r"scene[_\-]?(\d+)", re.IGNORECASE)
_BARE_NUM_RE = re.compile(r"(\d+)")


def _extract_scene_index(filename: str) -> Optional[int]:
    """Match `scene_003.png` style names, or a bare number like `3.png`."""
    stem = Path(filename).stem
    m = _SCENE_NUM_RE.search(stem)
    if m:
        return int(m.group(1))
    m = _BARE_NUM_RE.fullmatch(stem.strip())
    if m:
        return int(m.group(1))
    return None


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
