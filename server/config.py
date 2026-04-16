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
    # Chunked transcription: split long audio into N-second chunks before
    # passing to faster-whisper so peak RAM stays bounded per chunk.
    # Set to 0 to disable chunking (transcribe the whole file in one pass).
    whisper_chunk_duration_s: float = 600.0
    # Minimum total duration (seconds) before chunking kicks in. Short clips
    # bypass chunking to avoid ffmpeg overhead.
    whisper_chunk_min_duration_s: float = 900.0
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
    def previews_dir(self) -> Path:
        return self.data_dir / "previews"

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
            self.previews_dir,
            self.thumbnails_dir,
            self.temp_dir,
            self.cache_dir,
            self.knowledge_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    # CORS: comma-separated list of allowed origins.
    # E.g. CLIPFORGE_ALLOWED_ORIGINS="https://myapp.vercel.app,http://localhost:3000"
    allowed_origins_raw: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins_raw.split(",") if o.strip()]

    # Optional: explicit path to ffmpeg binary directory (auto-detected if blank)
    ffmpeg_path: str = ""

    @property
    def ffmpeg_location(self) -> str | None:
        """Return ffmpeg binary directory for yt-dlp, or None to let yt-dlp find it."""
        if self.ffmpeg_path:
            return self.ffmpeg_path
        import shutil
        exe = shutil.which("ffmpeg")
        if exe:
            from pathlib import Path as _Path
            return str(_Path(exe).parent)
        return None

    model_config = {"env_prefix": "CLIPFORGE_"}


settings = Settings()