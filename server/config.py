"""
ClipForge Worker - Configuration
Reads from environment variables with sensible defaults.
All data is isolated under DATA_DIR.
"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8420
    debug: bool = False
    max_concurrent_jobs: int = 2

    # ── Pipeline & Video Generation Settings ──────────────────────────────────
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "float16"
    # Clip duration bounds — wide range to accommodate short-form content (30-90s)
    # and longer interview cuts (up to 3 min). Target ~75s (TikTok sweet spot).
    min_clip_duration: float = 30.0
    max_clip_duration: float = 120.0
    target_clip_duration: float = 75.0
    default_clip_count: int = 10
    overlap_threshold: float = 0.3
    export_width: int = 1080
    export_height: int = 1920
    export_fps: int = 30
    export_bitrate: str = "4000k"
    export_codec: str = "libx264"
    export_audio_codec: str = "aac"
    export_audio_bitrate: str = "192k"

    # ── Data directories ──────────────────────────────────────────────────────
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "clipforge.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def thumbnails_dir(self) -> Path:
        return self.data_dir / "thumbnails"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "temp"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"

    def ensure_dirs(self) -> None:
        for d in [
            self.data_dir,
            self.db_path.parent,
            self.media_dir,
            self.exports_dir,
            self.thumbnails_dir,
            self.temp_dir,
            self.cache_dir,
            self.knowledge_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    model_config = {"env_prefix": "CLIPFORGE_"}


settings = Settings()