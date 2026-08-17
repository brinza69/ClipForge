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
    # "small" → "medium" cuts WER roughly in half on accented speech (RO, FR,
    # non-native EN) at the cost of ~3× transcribe time. Override via the
    # CLIPFORGE_WHISPER_MODEL env var if you want "large-v3" (~5× slower
    # again) or "tiny" (faster but noticeably worse).
    whisper_model: str = "medium"
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

    @property
    def doodle_dir(self) -> Path:
        return self.data_dir / "doodle"

    @property
    def tiktok_dir(self) -> Path:
        """Per-project workspace for the TikTok Transformation wizard."""
        return self.data_dir / "tiktok"

    @property
    def clipper_dir(self) -> Path:
        """Per-project workspace for the AI Stream Clipper (proxies, signals,
        sampled frames, previews, exports). Under data/, so .gitignore already
        excludes it and deleting the dir reclaims everything."""
        return self.data_dir / "clipper"

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
            self.doodle_dir,
            self.tiktok_dir,
            self.clipper_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    # ── AI Stream Clipper ─────────────────────────────────────────────────────
    # Feature flag. False leaves the code in place but registers neither the
    # router nor the job handlers — the documented rollback switch.
    clipper_enabled: bool = True

    # Ingestion limits. The cap keeps one bad paste from filling the disk; the
    # free-space check runs before the download starts, not after.
    # 12 h rather than 6: this is a *stream* clipper, and a full Twitch/YouTube
    # live VOD routinely runs 6-10 hours. A 6-hour cap rejected ordinary input.
    clipper_max_source_duration_s: float = 43200.0     # 12 hours
    clipper_max_upload_bytes: int = 21_474_836_480     # 20 GB
    clipper_min_free_bytes: int = 10_737_418_240       # 10 GB

    # Analysis proxy. EVERY analysis pass reads this, never the original — a
    # 480px/10fps proxy makes a multi-hour VOD tractable on CPU.
    clipper_proxy_width: int = 480
    clipper_proxy_fps: int = 10
    # How many frames the vision passes may sample in total, regardless of
    # source length. Bounds the OpenCV cost on a 6-hour stream.
    clipper_max_sampled_frames: int = 400

    # Output shape. 15-45s is the documented sweet spot for stream clips; we
    # allow up to 90s for explanations that genuinely need it.
    clipper_default_clip_count: int = 8
    clipper_min_clip_s: float = 15.0
    clipper_max_clip_s: float = 90.0
    clipper_target_clip_s: float = 35.0

    # Duplicate removal.
    clipper_overlap_threshold: float = 0.4
    clipper_text_similarity_threshold: float = 0.62

    # Pass D (expensive multimodal review). Blank engine → the pass is skipped
    # entirely and headlines fall back to deterministic extraction, so the
    # pipeline never fails just because an optional provider is missing.
    clipper_top_n_llm: int = 8
    # Hands-off mode: render the top N clips as soon as scoring finishes,
    # instead of stopping at the board and waiting for someone to pick.
    #
    # 0 = off, and off is the default because rendering is the one stage that
    # costs real minutes and produces files on disk — doing that uninvited to
    # someone who only wanted to see what the source contained is the wrong
    # surprise. It is a per-project setting; the source form offers it up front,
    # which is where a "paste a link and walk away" run is actually decided.
    clipper_auto_export: int = 0
    clipper_llm_engine: str = ""

    # Export. crf 18 + slow is the single quality pass — crop, scale, stack and
    # caption burn are fused into ONE encode (no second generation of loss).
    clipper_export_crf: int = 18
    clipper_export_preset: str = "slow"
    clipper_export_fps: int = 30

    # Gaming layout: default share of the 1080x1920 canvas given to the facecam.
    clipper_face_pct: float = 0.35
    # Multi-shot export: plan a shot list and cut between cameras instead of
    # rendering one fixed split screen for the whole clip.
    #
    # ON as of 2026-08-17, by the owner's decision, after it stopped being a
    # thing only tests had seen: a viewer judged the rhythm and camera choice
    # good, named the one fault (gameplay cropped too tight), and that was
    # settled by an A/B. Everything measured since — cuts on speech pauses, the
    # wide gameplay framing, Pass D, the audio ceiling — lands on this path and
    # NOWHERE ELSE, so leaving it off meant none of it reached a clip.
    #
    # It still falls back to the static layout on a missing proxy, a plan with
    # fewer than two shots, or any exception, so the old path remains the floor
    # rather than becoming dead code.
    clipper_dynamic_edit: bool = True
    # Cut dead seconds out of the MIDDLE of a chosen window (brief §15). OFF by
    # default: `trim_silence` has sat in the settings dict since the clipper
    # shipped with nothing reading it, so no export has ever been trimmed and
    # nobody's expectations depend on it. Turning it on by default would change
    # every deliverable at once.
    clipper_trim_silence: bool = False

    # 0 = never auto-purge project artifacts.
    clipper_retention_days: int = 0

    # Use the learned ranker when it qualifies (enough labels AND it beats the
    # heuristic on held-out NDCG@5). Otherwise heuristic weights stand.
    clipper_ranker_enabled: bool = True

    # Let a language model nominate moments and judge candidates. Off by
    # default: it needs either Ollama running or an API key, and a clipper run
    # must not depend on either. Both passes degrade to the heuristic ranking
    # rather than failing, so turning this on can only change the ordering.
    #
    # Measured with tiktoken on real transcripts (90..395 tokens/minute across
    # 19 of them): about 3.7 cents for a 12-hour gaming stream and 11.1 for a
    # talk-heavy one, with nomination on a local model and judging on an API.
    # How the clipper reasons about what deserves a clip.
    #   "legacy"   — interesting signals -> window -> features -> score
    #   "story_v1" — payoff first: find what happened, then reconstruct the
    #                earliest start that carries every fact the payoff needs
    # Legacy stays the default until story_v1 has been measured on more than
    # one source. Both need clipper_llm_select; with it off this has no effect.
    clipper_reasoning_version: str = "legacy"

    clipper_llm_select: bool = False
    # The judging pass needs a FRONTIER model, and the repo-wide default is a
    # small one. Measured on the same 46 candidates: gpt-4o-mini answered
    # almost everything 50, 40 or 10 with reasons like "Excitement about
    # discovery" and moved the reference clip from #45 only to #36, where a
    # frontier model spread its scores over eight values and moved it to #4.
    # Nomination is bulk reading and a small model is fine at it; judging is
    # taste and it is not. Blank falls back to the repo default.
    clipper_llm_judge_model: str = "gpt-4o"
    # How much of `overall` the model's verdict carries.
    #
    # Swept on the co-stream against two hand-labelled reference clips — a bit
    # where chat trolls the streamer and he calls them out (good), and a
    # stretch of "let's cook our food" (filler). Their ranks out of 46:
    #
    #   weight   0.0    0.3    0.5    0.7    1.0
    #   good      45     33     14      6      4
    #   filler     2      6      6      8     13
    #
    # 0.5 is not enough to undo the heuristic's ordering. 1.0 throws away what
    # the model cannot see — audio energy, boundary quality, duration fit — and
    # the model is not deterministic between runs, so the heuristic is also
    # what keeps an ordering stable. 0.7 gets both reference clips where they
    # belong and keeps that anchor.
    #
    # TREAT THIS AS PROVISIONAL. It is fitted to TWO labels on ONE 12-minute
    # segment. The direction is consistent and the reasoning holds, but the
    # sample cannot justify a third decimal — re-sweep it when there is real
    # feedback to fit against.
    clipper_llm_weight: float = 0.7

    # CORS: comma-separated list of allowed origins.
    # E.g. CLIPFORGE_ALLOWED_ORIGINS="https://myapp.vercel.app,http://localhost:3000"
    allowed_origins_raw: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins_raw.split(",") if o.strip()]

    # Optional: explicit path to ffmpeg binary directory (auto-detected if blank)
    ffmpeg_path: str = ""

    # ── AI Upscaler (Real-ESRGAN ncnn-vulkan) ─────────────────────────────────
    # Explicit path to realesrgan-ncnn-vulkan.exe. Blank → auto-detect at
    # <repo>/tools/realesrgan/pkg/realesrgan-ncnn-vulkan.exe.
    realesrgan_path: str = ""
    # Vulkan GPU index for the upscaler. NOTE: the ncnn Vulkan device order is
    # REVERSED vs nvidia-smi/CUDA on this rig — index 0 = RTX 3060 (fast),
    # index 1 = GTX 1660 Super. Override via CLIPFORGE_REALESRGAN_GPU_ID.
    realesrgan_gpu_id: int = 0

    @property
    def realesrgan_bin(self) -> str | None:
        """Absolute path to the Real-ESRGAN binary, or None if not installed."""
        from pathlib import Path as _Path
        if self.realesrgan_path:
            p = _Path(self.realesrgan_path)
            return str(p) if p.exists() else None
        exe = "realesrgan-ncnn-vulkan" + (".exe" if __import__("os").name == "nt" else "")
        default = self.data_dir.parent / "tools" / "realesrgan" / "pkg" / exe
        if default.exists():
            return str(default)
        import shutil
        return shutil.which("realesrgan-ncnn-vulkan")

    # ── yt-dlp authentication ────────────────────────────────────────────────
    # YouTube answers an increasing share of requests with "Sign in to confirm
    # you're not a bot", which no retry gets past. Both of these are blank by
    # default: without one, a gated URL fails with `login_required` exactly as
    # it did before.
    #
    # CLIPFORGE_YTDLP_COOKIES_FILE — path to a Netscape cookies.txt export.
    # CLIPFORGE_YTDLP_COOKIES_FROM_BROWSER — "chrome", "firefox", "edge", or
    #   "<browser>:<profile>" e.g. "chrome:Profile 1". Reads the browser's own
    #   cookie store, so the browser must be closed on Windows.
    # The file wins when both are set: it is the explicit one.
    ytdlp_cookies_file: str = ""
    ytdlp_cookies_from_browser: str = ""

    # JavaScript runtimes yt-dlp may use to solve YouTube's `n` challenge.
    # Without one, YouTube returns storyboards only and the download fails with
    # "Requested format is not available" — measured on a public 4-hour VOD,
    # where every player client returned zero audio/video formats until this
    # was set. yt-dlp enables "deno" alone by default; this repo already ships
    # Node for the frontend, so CLIPFORGE_YTDLP_JS_RUNTIMES=node is the cheap
    # answer. Comma-separated. Blank keeps yt-dlp's own default.
    # Solving also needs the challenge script: `pip install yt-dlp-ejs`.
    ytdlp_js_runtimes: str = ""

    @property
    def ytdlp_opts(self) -> dict:
        """Everything yt-dlp needs to reach a gated source. Merged at both
        call sites, so metadata and download always agree."""
        opts = dict(self.ytdlp_cookie_opts)
        runtimes = [r.strip().lower()
                    for r in self.ytdlp_js_runtimes.split(",") if r.strip()]
        if runtimes:
            opts["js_runtimes"] = {name: {} for name in runtimes}
        return opts

    @property
    def ytdlp_cookie_opts(self) -> dict:
        """yt-dlp options carrying whatever authentication is configured."""
        from pathlib import Path as _Path
        if self.ytdlp_cookies_file:
            path = _Path(self.ytdlp_cookies_file)
            if path.exists():
                return {"cookiefile": str(path)}
            return {}
        spec = self.ytdlp_cookies_from_browser.strip()
        if not spec:
            return {}
        browser, _, profile = spec.partition(":")
        # yt-dlp wants (browser, profile, keyring, container); trailing Nones
        # mean "default", which is what an unset profile should be.
        return {"cookiesfrombrowser": (browser.strip().lower(),
                                       profile.strip() or None, None, None)}

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