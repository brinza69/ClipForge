"""
ClipForge — Text-to-Speech Router

UI: src/app/tts/page.tsx
Backend: services/tts.py (Coqui XTTS-v2, lazy-loaded)

Endpoints:
  GET  /api/tts/health               — is the engine available
  GET  /api/tts/voices               — list reference clips in data/voices/
  POST /api/tts/voices               — upload a new reference clip
  DELETE /api/tts/voices/{voice_id}  — remove a reference clip
  POST /api/tts/synthesize           — start a job (returns {job_id})
  GET  /api/tts/jobs/{job_id}        — poll status
  GET  /api/tts/jobs/{job_id}/download — fetch the result WAV
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger("clipforge.routers.tts")
router = APIRouter(prefix="/api/tts", tags=["tts"])


@dataclass
class TTSJob:
    id: str
    status: str = "queued"
    progress: float = 0.0
    message: str = ""
    output_path: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    engine: str = "xtts"
    output_mime: str = "audio/wav"

    def to_status(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "error": self.error,
            "engine": self.engine,
        }


_jobs: Dict[str, TTSJob] = {}


def _make_job() -> TTSJob:
    j = TTSJob(id=uuid.uuid4().hex[:16], message="Queued")
    _jobs[j.id] = j
    return j


def _cleanup_old_jobs():
    """Drop finished jobs older than 30 min."""
    now = time.time()
    for jid, job in list(_jobs.items()):
        if job.finished_at and (now - job.finished_at) > 1800:
            if job.output_path:
                try:
                    Path(job.output_path).unlink(missing_ok=True)
                except Exception:
                    pass
            _jobs.pop(jid, None)


@router.get("/health")
async def tts_health():
    from services.tts import is_available, SUPPORTED_LANGS
    installed, hint = is_available()
    return {
        "installed": installed,
        "hint": hint,
        "languages": SUPPORTED_LANGS,
    }


@router.get("/engines")
async def list_engines():
    """Returns the status of every available TTS backend so the UI can show toggles."""
    from services.tts import is_available as xtts_available, SUPPORTED_LANGS as XTTS_LANGS
    from services.elevenlabs import (
        is_configured as eleven_configured,
        SUPPORTED_LANGUAGES as ELEVEN_LANGS,
    )
    from services.local_clone import status as local_status
    xtts_ok, xtts_hint = xtts_available()
    eleven_ok = eleven_configured()
    lc = local_status()

    # Build the local engine's hint
    if lc["ready"]:
        lc_hint = None
    else:
        parts = []
        if not lc["piper_installed"]:
            parts.append(lc["piper_hint"])
        if not lc["openvoice_installed"]:
            parts.append(lc["openvoice_hint"])
        lc_hint = " · ".join(parts) if parts else "Models will download on first run."

    return {
        "engines": [
            {
                "id": "xtts",
                "label": "XTTS-v2 (local)",
                "ready": xtts_ok,
                "hint": xtts_hint,
                "languages": XTTS_LANGS,
                "supports_romanian": False,
                "supports_cloning": True,
                "cost": "free",
            },
            {
                "id": "elevenlabs",
                "label": "ElevenLabs API",
                "ready": eleven_ok,
                "hint": None if eleven_ok else "Paste an API key in Settings.",
                "languages": ELEVEN_LANGS,
                "supports_romanian": True,
                "supports_cloning": True,
                "cost": "paid (free tier: 10k chars/mo)",
            },
            {
                "id": "local_clone",
                "label": "Local clone (RO)",
                "ready": lc["ready"],
                "hint": lc_hint,
                "languages": ["ro"],
                "supports_romanian": True,
                "supports_cloning": True,
                "cost": "free",
                "details": lc,
            },
        ],
    }


class APIKeyRequest(BaseModel):
    key: Optional[str] = None


@router.get("/elevenlabs/status")
async def elevenlabs_status():
    """Reports whether a key is configured + (if so) usage stats."""
    from services.elevenlabs import is_configured, get_user_info
    configured = is_configured()
    info: Optional[dict] = None
    error: Optional[str] = None
    if configured:
        try:
            info = await get_user_info()
        except Exception as e:
            error = str(e)[-200:]
    return {"configured": configured, "info": info, "error": error}


@router.post("/elevenlabs/key")
async def set_elevenlabs_key(req: APIKeyRequest):
    """Save (or clear, when key is empty) the ElevenLabs API key on the server."""
    from services.elevenlabs import set_api_key, get_user_info, list_voices
    key = (req.key or "").strip()
    set_api_key(key)
    if not key:
        return {"ok": True, "configured": False}
    # Verify by hitting /v1/voices (what the app actually uses). Modern
    # ElevenLabs scoped keys often lack `user_read` but include `voices_read`
    # + `text_to_speech`, so checking /v1/user would falsely reject valid keys.
    try:
        await list_voices()
    except Exception as e:
        set_api_key("")
        raise HTTPException(401, f"Key rejected by ElevenLabs: {str(e)[-200:]}")
    # Best-effort usage info — don't fail the save if /v1/user is gated.
    info: Optional[dict] = None
    try:
        info = await get_user_info()
    except Exception:
        pass
    return {"ok": True, "configured": True, "info": info}


@router.get("/voices")
async def list_voice_packs(engine: str = "xtts"):
    """List reference voices for the selected engine.

    - xtts: scans data/voices/ for user-uploaded clips
    - elevenlabs: pulls live from the ElevenLabs voice library
    """
    if engine == "elevenlabs":
        from services.elevenlabs import list_voices as el_list, is_configured
        if not is_configured():
            raise HTTPException(400, "ElevenLabs API key not configured")
        try:
            voices = await el_list()
        except Exception as e:
            raise HTTPException(502, f"ElevenLabs voices fetch failed: {str(e)[-200:]}")
        return {"engine": "elevenlabs", "voices": voices, "count": len(voices)}

    # xtts and local_clone both source from the local data/voices/ folder
    from services.tts import list_voices, voices_dir
    voices = list_voices()
    return {
        "engine": engine,
        "voices": voices,
        "voices_dir": str(voices_dir()),
        "count": len(voices),
    }


_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}


@router.post("/voices")
async def upload_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
):
    from services.tts import voices_dir

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _AUDIO_EXTS:
        raise HTTPException(400, f"Unsupported audio format. Use one of {sorted(_AUDIO_EXTS)}")

    content = await file.read()
    if len(content) < 1000:
        raise HTTPException(400, "File appears to be empty")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "Voice sample too large (max 20MB)")

    safe = re.sub(r"[^A-Za-z0-9._\- ]+", "_", name).strip().replace(" ", "_") or "voice"
    target = voices_dir() / f"{safe}{suffix}"
    n = 1
    while target.exists():
        target = voices_dir() / f"{safe}_{n}{suffix}"
        n += 1
    target.write_bytes(content)
    logger.info(f"Voice clip uploaded: {target.name} ({len(content) // 1024} KB)")
    return {"id": target.name, "name": safe.replace("_", " "), "size_kb": len(content) // 1024}


@router.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str):
    from services.tts import get_voice_path
    p = get_voice_path(voice_id)
    if not p:
        raise HTTPException(404, "Voice not found")
    try:
        p.unlink()
    except Exception as e:
        raise HTTPException(500, f"Could not delete: {e}")
    return {"ok": True}


class SynthRequest(BaseModel):
    text: str
    voice_id: str
    engine: str = "xtts"            # "xtts" | "elevenlabs"
    language: str = "en"
    # XTTS knobs
    speed: float = 1.0
    temperature: float = 0.7
    # ElevenLabs knobs
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0


async def _run_tts_job(job_id: str, req: SynthRequest):
    job = _jobs.get(job_id)
    if not job:
        return
    try:
        job.status = "running"
        job.progress = 0.1
        loop = asyncio.get_event_loop()

        if req.engine == "elevenlabs":
            from services.elevenlabs import synthesize as el_synth
            from config import settings

            out_dir = Path(settings.data_dir) / "tts_out"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = str(out_dir / f"tts_eleven_{int(time.time() * 1000)}.mp3")

            job.message = "Sending text to ElevenLabs…"
            job.output_mime = "audio/mpeg"
            await el_synth(
                req.text,
                req.voice_id,
                out_path,
                stability=req.stability,
                similarity_boost=req.similarity_boost,
                style=req.style,
            )
        elif req.engine == "local_clone":
            # Piper (RO TTS) → OpenVoice tone color converter (cloning)
            from services.local_clone import synthesize_cloned
            from services.tts import get_voice_path
            from config import settings

            ref = get_voice_path(req.voice_id)
            if ref is None:
                raise RuntimeError(
                    f"Reference voice '{req.voice_id}' not found in the voice library. "
                    "Upload a 6-30s clip via the voice library on the right first."
                )

            out_dir = Path(settings.data_dir) / "tts_out"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = str(out_dir / f"tts_local_{int(time.time() * 1000)}.wav")

            job.message = "Piper synthesising Romanian…"
            job.output_mime = "audio/wav"
            job.progress = 0.2

            def _do_local():
                return synthesize_cloned(req.text, str(ref), out_path)

            # Both stages are heavy CPU/GPU work — keep them in the thread pool
            await loop.run_in_executor(None, _do_local)
        else:
            # Local XTTS-v2
            from services.tts import synthesize as xtts_synth
            job.message = "Loading model & generating speech…"
            job.output_mime = "audio/wav"
            out_path = await loop.run_in_executor(
                None,
                lambda: xtts_synth(
                    req.text,
                    req.voice_id,
                    req.language,
                    speed=req.speed,
                    temperature=req.temperature,
                ),
            )

        job.output_path = out_path
        job.status = "done"
        job.progress = 1.0
        job.message = "Complete"
        job.finished_at = time.time()
        logger.info(f"TTS job {job_id} done ({job.engine}) → {out_path}")
        _cleanup_old_jobs()
    except Exception as e:
        logger.exception(f"TTS job {job_id} failed")
        job.status = "failed"
        job.error = f"{type(e).__name__}: {str(e)[-300:]}"
        job.message = "Failed"
        job.finished_at = time.time()


@router.post("/synthesize")
async def start_synthesize(req: SynthRequest):
    if not req.text.strip():
        raise HTTPException(400, "text is required")
    if not req.voice_id:
        raise HTTPException(400, "voice_id is required")

    if req.engine == "elevenlabs":
        from services.elevenlabs import is_configured
        if not is_configured():
            raise HTTPException(503, "ElevenLabs API key not configured. Paste a key in Voice Studio settings.")
    elif req.engine == "xtts":
        from services.tts import is_available
        installed, hint = is_available()
        if not installed:
            raise HTTPException(503, hint or "XTTS engine not available")
    elif req.engine == "local_clone":
        from services.local_clone import status as local_status
        st = local_status()
        if not st["ready"]:
            missing = []
            if not st["piper_installed"]:
                missing.append(st["piper_hint"])
            if not st["openvoice_installed"]:
                missing.append(st["openvoice_hint"])
            raise HTTPException(503, "Local clone engine not ready. " + " · ".join(missing))
    else:
        raise HTTPException(400, f"Unknown engine: {req.engine}")

    job = _make_job()
    job.engine = req.engine
    asyncio.create_task(_run_tts_job(job.id, req))
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
async def get_tts_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_status()


@router.get("/jobs/{job_id}/download")
async def download_tts_result(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status == "failed":
        raise HTTPException(422, job.error or "TTS failed")
    if job.status != "done" or not job.output_path:
        raise HTTPException(425, "Still processing")
    p = Path(job.output_path)
    if not p.exists():
        raise HTTPException(410, "Result file no longer available")
    ext = p.suffix.lower() or (".mp3" if job.output_mime == "audio/mpeg" else ".wav")
    filename = f"clipforge_tts_{job.id}{ext}"
    return FileResponse(
        path=str(p),
        media_type=job.output_mime or "audio/wav",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
