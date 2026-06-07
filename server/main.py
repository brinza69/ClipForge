"""
ClipForge Worker - Main Entry Point (Phase 1)
FastAPI server handling metadata extraction and soon video processing.
"""

import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from database import init_db
from routers.jobs import router as jobs_router
from routers.utilities import router as utilities_router
from routers.tts import router as tts_router
from routers.transcript import router as transcript_router
from routers.captions import router as captions_router
from routers.remix import router as remix_router
from routers.parallel import router as parallel_router
from routers.variant_presets import router as variant_presets_router
from routers.drive_auth import router as drive_auth_router
from routers.commentators import router as commentators_router
from job_queue import job_queue
from workers.utility_jobs import register_utility_handlers

# Configure logging
logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("clipforge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("=" * 50)
    logger.info(f"ClipForge Worker starting (port {settings.port})...")
    
    settings.ensure_dirs()
    logger.info(f"Data directory ready: {settings.data_dir}")

    # Initialize DB (creates sqlite file and schemas)
    await init_db()
    logger.info(f"Database ready: {settings.db_path}")

    # Pipeline setup
    register_utility_handlers(job_queue)
    from workers.remix_pipeline import register_remix_handlers
    register_remix_handlers(job_queue)
    from workers.parallel_pipeline import register_parallel_handlers
    register_parallel_handlers(job_queue)
    queue_task = asyncio.create_task(job_queue.start())
    logger.info("Background job queue started.")

    # Lightweight cleanup loop for in-memory eraser jobs (drops stale temp files)
    from services.erase_jobs import start_cleanup_task as start_erase_cleanup
    erase_cleanup_task = start_erase_cleanup()

    logger.info("=" * 50)

    yield

    logger.info("ClipForge Worker shutting down...")
    await job_queue.stop()
    erase_cleanup_task.cancel()
    try:
        await queue_task
    except asyncio.CancelledError:
        pass
    try:
        await erase_cleanup_task
    except (asyncio.CancelledError, Exception):
        pass


app = FastAPI(
    title="ClipForge Worker",
    version="0.1.0",
    description="Local AI video clipping backend (Phase 1: Metadata)",
    lifespan=lifespan,
)

# CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs_router)
app.include_router(utilities_router)
app.include_router(tts_router)
app.include_router(transcript_router)
app.include_router(captions_router)
app.include_router(remix_router)
app.include_router(parallel_router)
app.include_router(variant_presets_router)
app.include_router(drive_auth_router)
app.include_router(commentators_router)

app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
app.mount("/exports", StaticFiles(directory=settings.exports_dir), name="exports")
app.mount("/thumbnails", StaticFiles(directory=settings.thumbnails_dir), name="thumbnails")


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "clipforge-worker"}


@app.get("/api/system")
async def system_info():
    """System information."""
    import shutil
    try:
        total, used, free = shutil.disk_usage(str(settings.data_dir))
    except Exception:
        total = used = free = 0

    gpu_available = False
    gpu_name = None
    try:
        import torch
        gpu_available = torch.cuda.is_available()
        if gpu_available:
            gpu_name = torch.cuda.get_device_name(0)
    except ImportError:
        pass

    return {
        "gpu_available": gpu_available,
        "gpu_name": gpu_name,
        "whisper_model": settings.whisper_model,
        "whisper_device": settings.whisper_device,
        "data_dir": str(settings.data_dir),
        "exports_dir": str(settings.exports_dir),
        "disk_free_gb": round(free / (1024**3), 1) if free else 0,
        "disk_total_gb": round(total / (1024**3), 1) if total else 0,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
