"""
ClipForge — Worker Backend
FastAPI server that handles all media processing, transcription, and AI tasks.
"""

import logging
import asyncio
import sys
import os

# Add server dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from database import init_db
from queue import job_queue
from workers.pipeline import register_pipeline_handlers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("clipforge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("=" * 50)
    logger.info("ClipForge Worker starting...")
    logger.info(f"Data directory: {settings.data_dir}")
    logger.info(f"Database: {settings.db_path}")
    logger.info("=" * 50)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Register pipeline handlers
    register_pipeline_handlers(job_queue)

    # Start job queue processor
    queue_task = asyncio.create_task(job_queue.start())
    logger.info("Job queue processor started")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await job_queue.stop()
    queue_task.cancel()
    try:
        await queue_task
    except asyncio.CancelledError:
        pass


# Create FastAPI app
app = FastAPI(
    title="ClipForge Worker",
    version="0.1.0",
    description="Local AI video clipping backend",
    lifespan=lifespan,
)

# CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (thumbnails, exports)
if settings.thumbnails_dir.exists():
    app.mount(
        "/thumbnails",
        StaticFiles(directory=str(settings.thumbnails_dir)),
        name="thumbnails",
    )

if settings.exports_dir.exists():
    app.mount(
        "/exports",
        StaticFiles(directory=str(settings.exports_dir)),
        name="exports",
    )

# Register routers
from routers.projects import router as projects_router
from routers.jobs import router as jobs_router
from routers.clips import router as clips_router
from routers.exports import router as exports_router

app.include_router(projects_router)
app.include_router(jobs_router)
app.include_router(clips_router)
app.include_router(exports_router)


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "clipforge-worker",
        "version": "0.1.0",
    }


@app.get("/api/system")
async def system_info():
    """System information for the frontend."""
    import shutil

    # Check GPU availability
    gpu_available = False
    gpu_name = None
    try:
        import torch
        if torch.cuda.is_available():
            gpu_available = True
            gpu_name = torch.cuda.get_device_name(0)
    except ImportError:
        pass

    # Disk space
    try:
        total, used, free = shutil.disk_usage(str(settings.data_dir))
    except Exception:
        total = used = free = 0

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
        log_level="info",
    )
