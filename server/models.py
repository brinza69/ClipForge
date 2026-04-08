"""
ClipForge Worker - SQLAlchemy ORM models (Phase 1: Projects + Jobs)
"""

import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, Enum, JSON,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


# ── Helpers ──────────────────────────────────────────────────────────────────

def _uuid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums ────────────────────────────────────────────────────────────────────

class SourceType(str, enum.Enum):
    youtube = "youtube"
    twitch = "twitch"
    vimeo = "vimeo"
    direct = "direct"
    m3u8 = "m3u8"
    generic = "generic"
    local = "local"
    unknown = "unknown"


class ProjectStatus(str, enum.Enum):
    pending = "pending"
    fetching_metadata = "fetching_metadata"
    metadata_ready = "metadata_ready"
    downloading = "downloading"
    downloaded = "downloaded"
    transcribing = "transcribing"
    transcribed = "transcribed"
    scoring = "scoring"
    ready = "ready"
    failed = "failed"
    cancelled = "cancelled"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"

class ClipStatus(str, enum.Enum):
    candidate = "candidate"
    approved = "approved"
    rejected = "rejected"
    exporting = "exporting"
    exported = "exported"
    failed = "failed"


class JobType(str, enum.Enum):
    fetch_metadata = "fetch_metadata"
    download = "download"
    transcribe = "transcribe"
    score = "score"
    export = "export"
    full_pipeline = "full_pipeline"


# ── Models ───────────────────────────────────────────────────────────────────

class ProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(500), default="Untitled")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), default="unknown")
    status: Mapped[str] = mapped_column(
        String(30), default=ProjectStatus.pending.value
    )

    # Metadata (filled after fetch_metadata, before any download)
    channel_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upload_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    webpage_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    extractor: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_live: Mapped[bool | None] = mapped_column(nullable=True)
    was_live: Mapped[bool | None] = mapped_column(nullable=True)
    availability: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Post-download fields (empty in Phase 1)
    video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    filesize: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class JobModel(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(12), index=True)
    clip_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(
        String(20), default=JobStatus.queued.value
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    progress_message: Mapped[str] = mapped_column(String(200), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

class TranscriptModel(Base):
    __tablename__ = "transcripts"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(12), index=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    segments: Mapped[list | dict | None] = mapped_column(JSON, nullable=True)
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

class ClipModel(Base):
    __tablename__ = "clips"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(12), index=True)
    title: Mapped[str] = mapped_column(String(500), default="Untitled Clip")
    start_time: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    momentum_score: Mapped[float] = mapped_column(Float, default=0.0)
    hook_strength: Mapped[float] = mapped_column(Float, default=0.0)
    narrative_completeness: Mapped[float] = mapped_column(Float, default=0.0)
    curiosity_score: Mapped[float] = mapped_column(Float, default=0.0)
    emotional_intensity: Mapped[float] = mapped_column(Float, default=0.0)
    caption_readability: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_segments: Mapped[list | dict | None] = mapped_column(JSON, nullable=True)
    hook_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default=ClipStatus.candidate.value)
    
    # Export fields
    export_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    reframe_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reframe_data: Mapped[list | dict | None] = mapped_column(JSON, nullable=True)
    caption_preset_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Style overrides (nullable — null means "use preset default")
    caption_font_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caption_text_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    caption_highlight_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    caption_outline_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    caption_y_position: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "bottom", "center", "top"
    hook_font_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hook_text_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    hook_bg_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    hook_y_position: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

