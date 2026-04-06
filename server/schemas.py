"""
ClipForge Worker - Pydantic schemas for API request/response validation.
"""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


# ── Requests ─────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    source_url: Optional[str] = None
    title: Optional[str] = None


class ProjectAction(BaseModel):
    action: Literal[
        "download_process",
        "download_only",
        "audio_only",
        "transcribe",
        "score",
        "cancel",
    ]


# ── Responses ────────────────────────────────────────────────────────────────

class ProjectResponse(BaseModel):
    id: str
    title: str
    source_url: Optional[str] = None
    source_type: str = "unknown"
    status: str = "pending"
    channel_name: Optional[str] = None
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    thumbnail_url: Optional[str] = None
    thumbnail_path: Optional[str] = None
    estimated_size: Optional[int] = None
    upload_date: Optional[str] = None
    description: Optional[str] = None
    webpage_url: Optional[str] = None
    extractor: Optional[str] = None
    is_live: Optional[bool] = None
    was_live: Optional[bool] = None
    availability: Optional[str] = None
    video_path: Optional[str] = None
    filesize: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MetadataPreview(BaseModel):
    """Lightweight metadata preview response — no download needed."""
    title: str
    channel_name: Optional[str] = None
    duration: Optional[float] = None
    duration_formatted: Optional[str] = None
    source_type: str = "unknown"
    extractor: Optional[str] = None
    webpage_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    thumbnail_url: Optional[str] = None
    estimated_size: Optional[int] = None
    estimated_size_formatted: Optional[str] = None
    upload_date: Optional[str] = None
    description: Optional[str] = None
    is_live: Optional[bool] = None
    was_live: Optional[bool] = None
    availability: Optional[str] = None


class MetadataError(BaseModel):
    """Structured error returned when metadata extraction fails."""
    error: str
    error_code: str  # e.g. "geo_blocked", "login_required", "drm_protected"
    suggestion: Optional[str] = None
    url: Optional[str] = None


class JobResponse(BaseModel):
    id: str
    project_id: str
    clip_id: Optional[str] = None
    type: str
    status: str
    progress: float = 0.0
    progress_message: str = ""
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class ClipResponse(BaseModel):
    id: str
    project_id: str
    title: str
    thumbnail_path: Optional[str] = None
    start_time: float
    end_time: float
    duration: float
    momentum_score: float
    hook_strength: float
    narrative_completeness: float
    curiosity_score: float
    emotional_intensity: float
    caption_readability: float
    confidence: float
    transcript_text: Optional[str] = None
    transcript_segments: Optional[list | dict] = None
    hook_text: Optional[str] = None
    explanation: Optional[str] = None
    status: str
    export_path: Optional[str] = None
    reframe_mode: Optional[str] = None
    reframe_data: Optional[list | dict] = None
    caption_preset_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
