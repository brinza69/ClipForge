"""
ClipForge — Configuration
Central configuration management using Pydantic Settings.
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field

# Resolve project root (one level up from server/)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8420
    debug: bool = True

    # Paths
    data_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "data")
    media_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "data" / "media")
    exports_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "data" / "exports")
    cache_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "data" / "cache")
    temp_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "data" / "temp")
    thumbnails_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "data" / "thumbnails")
    db_path: Path = Field(default_factory=lambda: PROJECT_ROOT / "data" / "db" / "clipforge.db")

    # Whisper / Transcription
    whisper_model: str = "base"
    whisper_device: str = "auto"  # "auto", "cuda", "cpu"
    whisper_compute_type: str = "float16"

    # Export defaults
    export_width: int = 1080
    export_height: int = 1920
    export_fps: int = 30
    export_bitrate: str = "8M"
    export_codec: str = "libx264"
    export_audio_codec: str = "aac"
    export_audio_bitrate: str = "192k"

    # Scoring
    default_clip_count: int = 10
    min_clip_duration: float = 30.0
    max_clip_duration: float = 120.0
    target_clip_duration: float = 75.0
    overlap_threshold: float = 0.3

    # Storage
    auto_cleanup_temp: bool = True
    delete_source_after_export: bool = True
    max_concurrent_jobs: int = 2

    model_config = {
        "env_prefix": "CLIPFORGE_",
        "env_file": str(PROJECT_ROOT / ".env.local"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def ensure_dirs(self):
        """Create all data directories if they don't exist."""
        for d in [
            self.data_dir,
            self.media_dir,
            self.exports_dir,
            self.cache_dir,
            self.temp_dir,
            self.thumbnails_dir,
            self.db_path.parent,
        ]:
            d.mkdir(parents=True, exist_ok=True)


# Singleton
settings = Settings()
settings.ensure_dirs()
