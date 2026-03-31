"""
ClipForge — Database Models
SQLAlchemy ORM models + Pydantic schemas for the API.
"""

import uuid
from datetime import datetime
from typing import Optional, List
from enum import Enum

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text, JSON,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship
from pydantic import BaseModel, Field

from database import Base


# ============================================================================
# Enums
# ============================================================================

class ProjectStatus(str, Enum):
    pending = "pending"
    fetching_metadata = "fetching_metadata"
    metadata_ready = "metadata_ready"
    downloading = "downloading"
    downloaded = "downloaded"
    transcribing = "transcribing"
    transcribed = "transcribed"
    scoring = "scoring"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    cancelled = "cancelled"


class SourceType(str, Enum):
    youtube = "youtube"
    twitch = "twitch"
    vimeo = "vimeo"
    direct = "direct"
    local = "local"
    unknown = "unknown"


class JobType(str, Enum):
    fetch_metadata = "fetch_metadata"
    download = "download"
    transcribe = "transcribe"
    score = "score"
    reframe = "reframe"
    export = "export"
    full_pipeline = "full_pipeline"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class ClipStatus(str, Enum):
    candidate = "candidate"
    approved = "approved"
    exporting = "exporting"
    exported = "exported"
    rejected = "rejected"


# ============================================================================
# SQLAlchemy ORM Models
# ============================================================================

def gen_id() -> str:
    return uuid.uuid4().hex[:12]


class ProjectModel(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=gen_id)
    title = Column(String, nullable=False, default="Untitled Project")
    source_url = Column(String, nullable=True)
    source_type = Column(String, default=SourceType.unknown.value)
    status = Column(String, default=ProjectStatus.pending.value)

    # Media info (populated after metadata fetch)
    video_path = Column(String, nullable=True)
    audio_path = Column(String, nullable=True)
    duration = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    fps = Column(Float, nullable=True)
    filesize = Column(Integer, nullable=True)  # bytes
    estimated_size = Column(Integer, nullable=True)  # bytes (before download)

    # Metadata from source
    channel_name = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    upload_date = Column(String, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    thumbnail_path = Column(String, nullable=True)

    # Storage tracking
    total_storage = Column(Integer, default=0)  # bytes used by project

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    clips = relationship("ClipModel", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("JobModel", back_populates="project", cascade="all, delete-orphan")


class TranscriptModel(Base):
    __tablename__ = "transcripts"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    language = Column(String, default="en")
    segments = Column(JSON, default=list)  # [{start, end, text, confidence, words}]
    full_text = Column(Text, default="")
    word_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())


class ClipModel(Base):
    __tablename__ = "clips"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    title = Column(String, default="Untitled Clip")
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    duration = Column(Float, nullable=False)

    # Momentum Score breakdown
    momentum_score = Column(Float, default=0.0)
    hook_strength = Column(Float, default=0.0)
    narrative_completeness = Column(Float, default=0.0)
    curiosity_score = Column(Float, default=0.0)
    emotional_intensity = Column(Float, default=0.0)
    caption_readability = Column(Float, default=0.0)
    confidence = Column(Float, default=0.0)

    # Content
    transcript_text = Column(Text, default="")
    transcript_segments = Column(JSON, default=list)

    # Processing state
    status = Column(String, default=ClipStatus.candidate.value)
    export_path = Column(String, nullable=True)
    caption_preset_id = Column(String, nullable=True)
    reframe_mode = Column(String, default="auto")  # auto, single, dual
    reframe_data = Column(JSON, nullable=True)  # keyframes for crop positions

    created_at = Column(DateTime, default=func.now())

    project = relationship("ProjectModel", back_populates="clips")


class JobModel(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    clip_id = Column(String, nullable=True)
    type = Column(String, nullable=False)
    status = Column(String, default=JobStatus.queued.value)
    progress = Column(Float, default=0.0)
    progress_message = Column(String, default="")
    error = Column(Text, nullable=True)
    metadata = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    project = relationship("ProjectModel", back_populates="jobs")


class CaptionPresetModel(Base):
    __tablename__ = "caption_presets"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    font_family = Column(String, default="Montserrat")
    font_size = Column(Integer, default=68)
    font_weight = Column(String, default="Bold")
    text_color = Column(String, default="#FFFFFF")
    highlight_color = Column(String, default="#FFD700")
    outline_color = Column(String, default="#000000")
    outline_width = Column(Integer, default=4)
    shadow_color = Column(String, default="#00000080")
    position = Column(String, default="bottom")  # top, center, bottom
    uppercase = Column(Boolean, default=True)
    animation = Column(String, default="word")  # word, phrase, line
    max_words_per_line = Column(Integer, default=3)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())


class SettingModel(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(JSON)


# ============================================================================
# Pydantic Schemas (API Layer)
# ============================================================================

class ProjectCreate(BaseModel):
    source_url: Optional[str] = None
    title: Optional[str] = None


class ProjectMetadata(BaseModel):
    """Lightweight metadata returned before download."""
    title: str
    channel_name: Optional[str] = None
    duration: Optional[float] = None
    duration_formatted: Optional[str] = None
    source_type: str
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    thumbnail_url: Optional[str] = None
    thumbnail_path: Optional[str] = None
    estimated_size: Optional[int] = None
    estimated_size_formatted: Optional[str] = None
    upload_date: Optional[str] = None
    description: Optional[str] = None
    formats_available: Optional[List[dict]] = None


class ProjectResponse(BaseModel):
    id: str
    title: str
    source_url: Optional[str] = None
    source_type: str
    status: str
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    thumbnail_url: Optional[str] = None
    thumbnail_path: Optional[str] = None
    channel_name: Optional[str] = None
    estimated_size: Optional[int] = None
    total_storage: int = 0
    clip_count: int = 0
    exported_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ClipResponse(BaseModel):
    id: str
    project_id: str
    title: str
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
    transcript_text: str
    status: str
    export_path: Optional[str] = None
    reframe_mode: str = "auto"
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobResponse(BaseModel):
    id: str
    project_id: str
    clip_id: Optional[str] = None
    type: str
    status: str
    progress: float
    progress_message: str = ""
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ProjectAction(BaseModel):
    """User action after preview: download_process, download_only, audio_only, cancel."""
    action: str
    format_id: Optional[str] = None


class CaptionPresetResponse(BaseModel):
    id: str
    name: str
    font_family: str
    font_size: int
    font_weight: str
    text_color: str
    highlight_color: str
    outline_color: str
    outline_width: int
    position: str
    uppercase: bool
    animation: str
    max_words_per_line: int
    is_default: bool

    model_config = {"from_attributes": True}
