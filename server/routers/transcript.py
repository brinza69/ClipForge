"""
ClipForge — Transcript Studio Router

UI: src/app/transcript/page.tsx
Backend: services/transcript_cleaner.py

Endpoints:
  GET  /api/transcript/engines             — list backends + ready state
  POST /api/transcript/openai/key          — save / clear OpenAI key (verifies it)
  POST /api/transcript/anthropic/key       — save / clear Anthropic key (verifies it)
  POST /api/transcript/clean               — start a clean+translate job (returns {job_id})
  POST /api/transcript/upload              — same as /clean but accepts a file upload
  GET  /api/transcript/jobs/{job_id}       — poll status
  GET  /api/transcript/jobs/{job_id}/result — fetch the cleaned text (JSON)
  GET  /api/transcript/jobs/{job_id}/download — download as .txt
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

logger = logging.getLogger("clipforge.routers.transcript")
router = APIRouter(prefix="/api/transcript", tags=["transcript"])


@dataclass
class CleanJob:
    id: str
    status: str = "queued"
    progress: float = 0.0
    message: str = ""
    engine: str = "ollama"
    target_language: Optional[str] = None
    result: Optional[str] = None
    source_length: int = 0
    result_length: int = 0
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_status(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "engine": self.engine,
            "target_language": self.target_language,
            "source_length": self.source_length,
            "result_length": self.result_length,
            "error": self.error,
        }


_jobs: Dict[str, CleanJob] = {}


def _cleanup_old_jobs():
    now = time.time()
    for jid, job in list(_jobs.items()):
        if job.finished_at and (now - job.finished_at) > 3600:
            _jobs.pop(jid, None)


def _make_job() -> CleanJob:
    j = CleanJob(id=uuid.uuid4().hex[:16], message="Queued")
    _jobs[j.id] = j
    return j


# ---------------------------------------------------------------------------
# Engine listing + key management
# ---------------------------------------------------------------------------

@router.get("/engines")
async def list_engines():
    from services.transcript_cleaner import (
        ollama_status, get_openai_key, get_anthropic_key,
        DEFAULT_OLLAMA_MODEL, DEFAULT_OPENAI_MODEL, DEFAULT_ANTHROPIC_MODEL,
        LANGUAGE_NAMES,
    )

    ostatus = await ollama_status()
    ollama_has_default = ostatus["running"] and (
        DEFAULT_OLLAMA_MODEL in ostatus["models"]
        or any(m.startswith(DEFAULT_OLLAMA_MODEL.split(":")[0] + ":") for m in ostatus["models"])
    )
    ollama_hint = ostatus["hint"]
    if ostatus["running"] and not ollama_has_default and not ostatus["models"]:
        ollama_hint = f"Ollama is running but no models pulled. Run `ollama pull {DEFAULT_OLLAMA_MODEL}`."
    elif ostatus["running"] and not ollama_has_default:
        ollama_hint = (
            f"Default model `{DEFAULT_OLLAMA_MODEL}` not found. Available: "
            f"{', '.join(ostatus['models'][:6])}. Pick one or pull the default."
        )

    languages = [{"code": k, "name": v} for k, v in LANGUAGE_NAMES.items()]

    return {
        "engines": [
            {
                "id": "ollama",
                "label": "Ollama (local)",
                "ready": ostatus["running"] and (ollama_has_default or len(ostatus["models"]) > 0),
                "hint": ollama_hint,
                "default_model": DEFAULT_OLLAMA_MODEL,
                "available_models": ostatus["models"],
                "cost": "free",
            },
            {
                "id": "openai",
                "label": "OpenAI",
                "ready": bool(get_openai_key()),
                "hint": None if get_openai_key() else "Paste an OpenAI API key in Settings.",
                "default_model": DEFAULT_OPENAI_MODEL,
                "cost": "paid (gpt-4o-mini ≈ $0.001 per 30 min transcript)",
            },
            {
                "id": "anthropic",
                "label": "Anthropic",
                "ready": bool(get_anthropic_key()),
                "hint": None if get_anthropic_key() else "Paste an Anthropic API key in Settings.",
                "default_model": DEFAULT_ANTHROPIC_MODEL,
                "cost": "paid (claude-haiku-4-5)",
            },
        ],
        "languages": languages,
    }


class APIKeyRequest(BaseModel):
    key: Optional[str] = None


@router.post("/openai/key")
async def set_openai(req: APIKeyRequest):
    from services.transcript_cleaner import set_openai_key, verify_openai_key
    key = (req.key or "").strip()
    set_openai_key(key)
    if not key:
        return {"ok": True, "configured": False}
    try:
        await verify_openai_key()
    except Exception as e:
        set_openai_key("")
        raise HTTPException(401, f"OpenAI key rejected: {str(e)[-200:]}")
    return {"ok": True, "configured": True}


@router.post("/anthropic/key")
async def set_anthropic(req: APIKeyRequest):
    from services.transcript_cleaner import set_anthropic_key, verify_anthropic_key
    key = (req.key or "").strip()
    set_anthropic_key(key)
    if not key:
        return {"ok": True, "configured": False}
    try:
        await verify_anthropic_key()
    except Exception as e:
        set_anthropic_key("")
        raise HTTPException(401, f"Anthropic key rejected: {str(e)[-200:]}")
    return {"ok": True, "configured": True}


# ---------------------------------------------------------------------------
# Whisper device + model — diagnostics and live config
# ---------------------------------------------------------------------------

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
WHISPER_DEVICES = ["auto", "cuda", "cpu"]


class WhisperConfigRequest(BaseModel):
    whisper_model: Optional[str] = None
    whisper_device: Optional[str] = None


@router.get("/device")
async def whisper_device_status(verify: bool = False):
    """Report what Whisper model/device is configured + (optional) actually load
    the model to verify CUDA works. With verify=false (default) it just
    introspects config without paying the load cost (~10s for medium).
    With verify=true it forces a load and reports the resolved device — use
    this to confirm CUDA isn't silently falling back to CPU."""
    from services.transcriber import get_model_info, _get_model

    info = get_model_info()
    if verify:
        try:
            # Loading in the main process for diagnostics (separate cache
            # from the subprocess used by the actual transcribe pipeline).
            _get_model()
            info = get_model_info()
        except Exception as e:
            info = {**info, "error": f"{type(e).__name__}: {str(e)[-200:]}"}

    # Probe torch.cuda directly for an extra signal — the UI shows this
    # next to the configured device so the user can see if their GPU is
    # actually visible to PyTorch.
    try:
        import torch
        info["cuda_available"] = bool(torch.cuda.is_available())
        info["cuda_device_name"] = (
            torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        )
    except Exception:
        info["cuda_available"] = False
        info["cuda_device_name"] = None

    info["models"] = WHISPER_MODELS
    info["devices"] = WHISPER_DEVICES
    return info


@router.post("/device")
async def whisper_device_set(req: WhisperConfigRequest):
    """Persist model/device preferences to data/whisper_config.json and drop
    the cached model so the next transcribe loads with the new values."""
    from services.transcriber import write_config_overrides, unload_model

    if req.whisper_model and req.whisper_model not in WHISPER_MODELS:
        raise HTTPException(400, f"Unknown model. Use one of {WHISPER_MODELS}.")
    if req.whisper_device and req.whisper_device not in WHISPER_DEVICES:
        raise HTTPException(400, f"Unknown device. Use one of {WHISPER_DEVICES}.")

    cfg = write_config_overrides(
        model=req.whisper_model, device=req.whisper_device,
    )
    unload_model()
    return {"ok": True, "saved": cfg}


# ---------------------------------------------------------------------------
# Clean job
# ---------------------------------------------------------------------------

class CleanRequest(BaseModel):
    text: str
    engine: str = "ollama"
    target_language: Optional[str] = None
    model: Optional[str] = None
    source_filename: str = ""


async def _run_clean_job(job_id: str, text: str, engine: str,
                          target_language: Optional[str], model: Optional[str],
                          source_filename: str):
    job = _jobs.get(job_id)
    if not job:
        return
    try:
        from services.transcript_cleaner import clean_transcript, parse_transcript

        job.status = "running"
        job.message = "Parsing transcript…"
        job.progress = 0.05

        parsed_preview = parse_transcript(text, source_filename)
        job.source_length = len(parsed_preview)

        def cb(done: int, total: int):
            if total > 0:
                job.progress = 0.1 + 0.85 * (done / total)
            job.message = f"Cleaning chunk {min(done + 1, total)} / {total}"

        result = await clean_transcript(
            text, engine,
            target_language=target_language,
            source_filename=source_filename,
            model=model,
            progress_cb=cb,
        )

        job.result = result
        job.result_length = len(result)
        job.status = "done"
        job.progress = 1.0
        job.message = "Complete"
        job.finished_at = time.time()
        _cleanup_old_jobs()
    except Exception as e:
        logger.exception(f"Transcript job {job_id} failed")
        job.status = "failed"
        job.error = f"{type(e).__name__}: {str(e)[-300:]}"
        job.message = "Failed"
        job.finished_at = time.time()


def _validate_engine_ready(engine: str):
    from services.transcript_cleaner import get_openai_key, get_anthropic_key
    if engine == "openai" and not get_openai_key():
        raise HTTPException(503, "OpenAI key not configured. Paste a key in Transcript Studio settings.")
    if engine == "anthropic" and not get_anthropic_key():
        raise HTTPException(503, "Anthropic key not configured. Paste a key in Transcript Studio settings.")
    if engine not in ("ollama", "openai", "anthropic"):
        raise HTTPException(400, f"Unknown engine: {engine}")


@router.post("/clean")
async def start_clean(req: CleanRequest):
    if not (req.text or "").strip():
        raise HTTPException(400, "text is required")
    _validate_engine_ready(req.engine)

    job = _make_job()
    job.engine = req.engine
    job.target_language = req.target_language
    asyncio.create_task(_run_clean_job(
        job.id, req.text, req.engine, req.target_language, req.model, req.source_filename,
    ))
    return {"job_id": job.id}


@router.post("/upload")
async def start_clean_from_upload(
    file: UploadFile = File(...),
    engine: str = Form("ollama"),
    target_language: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
):
    _validate_engine_ready(engine)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 5MB)")
    try:
        text = content.decode("utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(400, f"Could not decode file as UTF-8: {e}")
    if not text.strip():
        raise HTTPException(400, "File appears empty")

    job = _make_job()
    job.engine = engine
    job.target_language = target_language
    asyncio.create_task(_run_clean_job(
        job.id, text, engine, target_language, model, file.filename or "",
    ))
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_status()


@router.get("/jobs/{job_id}/result")
async def get_result(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status == "failed":
        raise HTTPException(422, job.error or "Job failed")
    if job.status != "done":
        raise HTTPException(425, "Still processing")
    return {
        "text": job.result or "",
        "source_length": job.source_length,
        "result_length": job.result_length,
        "engine": job.engine,
        "target_language": job.target_language,
    }


@router.get("/jobs/{job_id}/download")
async def download_result(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status == "failed":
        raise HTTPException(422, job.error or "Job failed")
    if job.status != "done" or not job.result:
        raise HTTPException(425, "Still processing")
    filename = f"transcript_{job.id}.txt"
    return Response(
        content=job.result.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
