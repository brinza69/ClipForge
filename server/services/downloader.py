"""
ClipForge Worker - Metadata Extraction Service

Uses yt-dlp to extract video metadata WITHOUT downloading.
Handles failures gracefully with structured error codes.

Supported sources (actively tested):
  - YouTube (public videos, shorts, playlist entries)
  - Twitch VODs and highlights
  - Vimeo (public / unlisted)
  - Direct MP4 / WebM / MKV URLs
  - m3u8 / HLS streams (best effort)
  - Generic yt-dlp-supported sites (results vary)

Known limitations:
  - Login-required content needs yt-dlp cookie config (not in scope)
  - DRM-protected content is not downloadable
  - Some geo-restricted content may fail
  - Live streams are not supported (only completed VODs)
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yt_dlp

logger = logging.getLogger("clipforge.downloader")


# ── Error classification ─────────────────────────────────────────────────────

_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    # (regex pattern on error message, error_code, user-facing suggestion)
    (
        r"(geo.?restrict|not available in your country|geo.?block)",
        "geo_blocked",
        "This video is geo-restricted. Try using a VPN, or download the file manually and use local upload.",
    ),
    (
        r"(sign in|log.?in|login.?required|cookies|members.only)",
        "login_required",
        "This video requires authentication. Download it manually using yt-dlp with --cookies and then use local upload.",
    ),
    (
        r"(drm|widevine|fairplay|content protection|encrypted)",
        "drm_protected",
        "This video uses DRM protection and cannot be downloaded. Try screen-recording or local file upload.",
    ),
    (
        r"(private video|been removed|no longer available|deleted)",
        "private_video",
        "This video is private or has been removed by the uploader.",
    ),
    (
        r"(is live|live stream|live event)",
        "live_stream",
        "Live streams are not supported. Wait for the VOD to become available, then try again.",
    ),
    (
        r"(age.?restrict|age.?gate|confirm your age)",
        "age_restricted",
        "This video is age-restricted. Configure yt-dlp cookies from a logged-in browser session.",
    ),
    (
        r"(unsupported url|no suitable|no video formats|unable to extract)",
        "unsupported_site",
        "This URL is not supported. Try pasting a direct MP4 link or uploading a local file.",
    ),
    (
        r"(timed? ?out|connection refused|network|unreachable|dns)",
        "network_error",
        "Network error. Check your internet connection and try again.",
    ),
    (
        r"(http error 403|forbidden)",
        "forbidden",
        "Access to this video is forbidden (HTTP 403). The link may be expired or restricted.",
    ),
    (
        r"(http error 404|not found)",
        "not_found",
        "Video not found (HTTP 404). Double-check the URL.",
    ),
]


def _classify_error(error_msg: str) -> tuple[str, str]:
    """Match an error message to a code and suggestion."""
    lower = error_msg.lower()
    for pattern, code, suggestion in _ERROR_PATTERNS:
        if re.search(pattern, lower):
            return code, suggestion
    return "unknown", "An unexpected error occurred. Try a different link or use local file upload."


# ── Source type detection ────────────────────────────────────────────────────

def detect_source_type(url: str) -> str:
    """Detect the source platform from a URL string."""
    u = url.lower().strip()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "twitch.tv" in u:
        return "twitch"
    if "vimeo.com" in u:
        return "vimeo"
    if u.endswith(".m3u8") or ".m3u8?" in u:
        return "m3u8"
    if any(u.endswith(ext) for ext in (".mp4", ".webm", ".mkv", ".mov", ".avi")):
        return "direct"
    if u.startswith("http"):
        return "generic"
    return "unknown"


# ── URL validation ───────────────────────────────────────────────────────────

async def validate_url(url: str) -> dict[str, Any]:
    """Quick URL validation. Returns {valid, error?, source_type}."""
    url = url.strip()
    if not url:
        return {"valid": False, "error": "URL is empty."}
    if not url.startswith(("http://", "https://")):
        return {"valid": False, "error": "URL must start with http:// or https://"}
    if len(url) > 2000:
        return {"valid": False, "error": "URL is too long."}
    return {"valid": True, "source_type": detect_source_type(url)}


# ── Core metadata extraction ────────────────────────────────────────────────

async def fetch_metadata(url: str, project_id: str | None = None) -> dict[str, Any]:
    """
    Extract metadata from a URL using yt-dlp WITHOUT downloading.

    Returns a dict with:
        title, channel_name, duration, thumbnail_url, width, height, fps,
        estimated_size, upload_date, description, webpage_url, extractor,
        source_type, is_live, was_live, availability

    On failure returns:
        error, error_code, suggestion
    """
    logger.info(f"Fetching metadata: {url}")

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _extract_info_sync, url)
        return result
    except Exception as exc:
        error_msg = str(exc)
        code, suggestion = _classify_error(error_msg)
        logger.warning(f"Metadata extraction failed [{code}]: {error_msg[:200]}")
        return {
            "error": error_msg[:300],
            "error_code": code,
            "suggestion": suggestion,
            "url": url,
        }


def _extract_info_sync(url: str) -> dict[str, Any]:
    """
    Synchronous yt-dlp metadata extraction.
    Runs in a thread executor to keep the event loop free.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,          # NEVER download in this phase
        "extract_flat": False,          # We want full info, not playlist stubs
        "no_color": True,
        "socket_timeout": 15,
        "retries": 2,
        # Pick best format to estimate filesize, but do not download
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise RuntimeError(str(exc)) from exc
        except yt_dlp.utils.ExtractorError as exc:
            raise RuntimeError(str(exc)) from exc
        except Exception as exc:
            raise RuntimeError(f"Extraction failed: {exc}") from exc

    if info is None:
        raise RuntimeError("yt-dlp returned no data for this URL.")

    # ── Build metadata dict ──────────────────────────────────────────────────
    source_type = detect_source_type(url)

    # Estimate filesize from format info
    estimated_size = _estimate_filesize(info)

    # Duration formatting
    duration = info.get("duration")
    duration_fmt = None
    if duration:
        h, rem = divmod(int(duration), 3600)
        m, s = divmod(rem, 60)
        duration_fmt = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # Size formatting
    size_fmt = None
    if estimated_size:
        if estimated_size >= 1_073_741_824:
            size_fmt = f"{estimated_size / 1_073_741_824:.1f} GB"
        elif estimated_size >= 1_048_576:
            size_fmt = f"{estimated_size / 1_048_576:.0f} MB"
        else:
            size_fmt = f"{estimated_size / 1024:.0f} KB"

    return {
        "title": info.get("title") or info.get("fulltitle") or "Untitled",
        "channel_name": (
            info.get("uploader")
            or info.get("channel")
            or info.get("uploader_id")
        ),
        "duration": duration,
        "duration_formatted": duration_fmt,
        "thumbnail_url": info.get("thumbnail"),
        "width": info.get("width"),
        "height": info.get("height"),
        "fps": info.get("fps"),
        "estimated_size": estimated_size,
        "estimated_size_formatted": size_fmt,
        "upload_date": info.get("upload_date"),
        "description": (info.get("description") or "")[:1000],
        "webpage_url": info.get("webpage_url") or url,
        "extractor": info.get("extractor") or info.get("extractor_key"),
        "source_type": source_type,
        "is_live": info.get("is_live"),
        "was_live": info.get("was_live"),
        "availability": info.get("availability"),
    }


def _estimate_filesize(info: dict) -> int | None:
    """Estimate total filesize from yt-dlp format info."""
    # Try direct filesize
    if info.get("filesize"):
        return int(info["filesize"])
    if info.get("filesize_approx"):
        return int(info["filesize_approx"])

    # Try from requested_formats (separate video + audio streams)
    requested = info.get("requested_formats") or []
    total = 0
    for fmt in requested:
        sz = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        total += sz
    if total > 0:
        return total

    # Fallback: estimate from bitrate * duration
    duration = info.get("duration") or 0
    tbr = info.get("tbr") or 0  # total bitrate in kbps
    if duration and tbr:
        return int(tbr * 1000 / 8 * duration)

    # Try from formats list (pick best)
    formats = info.get("formats") or []
    best_size = 0
    for fmt in formats:
        sz = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        if sz > best_size:
            best_size = sz
    if best_size > 0:
        return best_size

    return None

def _ffmpeg_tools_paths() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe) paths, honoring CLIPFORGE_FFMPEG_PATH."""
    from config import settings
    ffmpeg_exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    ffprobe_exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    ffmpeg = ffmpeg_exe
    ffprobe = ffprobe_exe
    loc = settings.ffmpeg_location
    if loc:
        cand_m = Path(loc) / ffmpeg_exe
        cand_p = Path(loc) / ffprobe_exe
        if cand_m.exists():
            ffmpeg = str(cand_m)
        if cand_p.exists():
            ffprobe = str(cand_p)
    resolved_m = shutil.which(ffmpeg) if ffmpeg == ffmpeg_exe else ffmpeg
    resolved_p = shutil.which(ffprobe) if ffprobe == ffprobe_exe else ffprobe
    return resolved_m or ffmpeg, resolved_p or ffprobe


def _file_has_audio(path: Path, ffprobe: str) -> bool:
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return bool((out.stdout or "").strip())
    except Exception:
        return True  # if probe fails, don't block — let the existing path run


def _ensure_audio_track(video_path: Path, url: str) -> Path:
    """
    Some sources (TikTok HEVC) hand back a video-only file even when yt-dlp's
    metadata claims it has audio. If `video_path` lacks an audio stream, fetch
    a known-muxed H.264 variant of the same URL, then remux: keep the original
    video stream, splice in the donor's audio. Returns the (possibly new) path.
    """
    from config import settings
    ffmpeg, ffprobe = _ffmpeg_tools_paths()

    if _file_has_audio(video_path, ffprobe):
        return video_path

    logger.info(
        f"Downloaded file has no audio stream, fetching audio donor "
        f"({video_path.name}). This is the TikTok HEVC mute-file workaround."
    )

    donor_dir = video_path.parent / "_audio_donor"
    donor_dir.mkdir(parents=True, exist_ok=True)
    donor_tmpl = str(donor_dir / "donor.%(ext)s")

    donor_opts = {
        "outtmpl": donor_tmpl,
        # Prefer muxed H.264 (avc1) — that's the variant that reliably carries
        # audio on TikTok. Fall back to any format that yt-dlp thinks has audio.
        "format": "best[vcodec*=avc1]/best[acodec!=none]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 2,
        "concurrent_fragment_downloads": 1,
    }
    loc = settings.ffmpeg_location
    if loc:
        donor_opts["ffmpeg_location"] = loc

    try:
        with yt_dlp.YoutubeDL(donor_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        downloads = info.get("requested_downloads") or []
        donor_path = Path(downloads[0]["filepath"]) if downloads else None
        if not donor_path or not donor_path.exists():
            # Fallback: glob the donor dir
            cands = list(donor_dir.glob("donor.*"))
            donor_path = cands[0] if cands else None
        if not donor_path or not donor_path.exists():
            logger.warning("Audio donor download produced no file; keeping silent video.")
            return video_path
        if not _file_has_audio(donor_path, ffprobe):
            logger.warning("Audio donor also lacks an audio stream; keeping silent video.")
            return video_path

        muxed_path = video_path.with_suffix(".muxed.mp4")
        cmd = [
            ffmpeg, "-y",
            "-loglevel", "error",
            "-i", str(video_path),
            "-i", str(donor_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "copy",
            "-shortest",
            "-movflags", "+faststart",
            str(muxed_path),
        ]
        creationflags = 0x08000000 if os.name == "nt" else 0
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, creationflags=creationflags)
        if proc.returncode != 0 or not muxed_path.exists() or muxed_path.stat().st_size < 1000:
            logger.warning(
                f"ffmpeg audio remux failed (rc={proc.returncode}); keeping silent video. "
                f"stderr tail: {(proc.stderr or '').strip().splitlines()[-3:]}"
            )
            muxed_path.unlink(missing_ok=True)
            return video_path

        # Swap original with muxed.
        video_path.unlink(missing_ok=True)
        muxed_path.rename(video_path)
        logger.info(f"Audio remuxed in; final file has video+audio: {video_path}")
        return video_path
    finally:
        # Always clean up the donor directory.
        try:
            shutil.rmtree(donor_dir, ignore_errors=True)
        except Exception:
            pass


async def download_video(
    url: str,
    project_id: str,
    on_progress=None,
    audio_only: bool = False,
    is_cancelled=None,
) -> dict[str, Any]:
    """Download video or audio using yt-dlp to a project folder."""
    from config import settings
    
    output_dir = settings.media_dir / project_id
    output_dir.mkdir(parents=True, exist_ok=True)
    
    loop = asyncio.get_event_loop()
    
    def _progress_hook(d):
        if d['status'] == 'downloading':
            try:
                if is_cancelled and is_cancelled():
                    from job_queue import JobCancelledError
                    raise JobCancelledError("Download cancelled by user.")

                # Try to clean ANSI escape codes from yt-dlp's percent string
                p_str = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_percent_str', '')).strip('% \n')
                p = float(p_str) / 100.0 if p_str and p_str != 'Unknown' else 0.0
                speed = d.get('_speed_str', '')
                eta = d.get('_eta_str', '')
                msg = f"Downloading... Speed: {speed} ETA: {eta}"
                if on_progress:
                    asyncio.run_coroutine_threadsafe(on_progress(p, msg), loop)
            except Exception as e:
                # Preserve cancellation exceptions, otherwise ignore hook failures.
                if is_cancelled and is_cancelled():
                    raise
                return
                
    outtmpl = str(output_dir / ('audio.%(ext)s' if audio_only else 'video.%(ext)s'))

    ydl_opts = {
        "outtmpl": outtmpl,
        # Format chain rationale:
        #   1. bestvideo+bestaudio (m4a)  — wins on YouTube (separate streams).
        #   2. best[vcodec*=avc1]         — H.264 muxed fallback. Critical for TikTok:
        #                                   their HEVC (bytevc1) variants are video-only
        #                                   despite advertising acodec=aac, so picking them
        #                                   produces a silent file and transcription crashes.
        #   3/4. generic mp4 / any best.
        # Format chain: prefer separate streams (YouTube) → best mp4 (e.g. TikTok
        # 1080p HEVC, which may end up audio-less — we fix that post-hoc by
        # remuxing in audio from a known-good muxed H.264 variant below).
        "format": (
            "bestaudio[ext=m4a]/best" if audio_only else
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        # Avoid multi-connection downloads that are harder to cancel reliably.
        "concurrent_fragment_downloads": 1,
        "progress_hooks": [_progress_hook] if on_progress else [],
    }
    # Point yt-dlp at the correct ffmpeg location (Windows PATH may not include it)
    _ffmpeg_loc = settings.ffmpeg_location
    if _ffmpeg_loc:
        ydl_opts["ffmpeg_location"] = _ffmpeg_loc
    
    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)

    info = await loop.run_in_executor(None, _run)
    
    filepath = outtmpl.replace("%(ext)s", "m4a" if audio_only else "mp4")
    
    # Check requested_downloads for explicit filepath output
    downloads = info.get("requested_downloads")
    if downloads and len(downloads) > 0:
        filepath = downloads[0].get("filepath", filepath)

    # Verify the file was actually created on disk
    if not Path(filepath).exists():
        # Try to find any video file in the output directory
        found = list(output_dir.glob("video.*")) + list(output_dir.glob("audio.*"))
        if found:
            filepath = str(found[0])
        else:
            raise RuntimeError(
                f"Download appeared to succeed but no file found at {filepath}. "
                f"Files in output dir: {list(output_dir.iterdir())}"
            )

    # Post-process: if the video file came back without an audio stream (TikTok
    # HEVC quirk), fetch a known-muxed donor and remux audio in. Returns the
    # same path on success; falls back silently if the donor or ffmpeg fails.
    if not audio_only:
        try:
            fixed = await loop.run_in_executor(None, _ensure_audio_track, Path(filepath), url)
            filepath = str(fixed)
        except Exception as e:
            logger.warning(f"Audio remux step crashed; keeping original file: {e}")

    return {
        "video_path": filepath,
        "duration": info.get("duration"),
        "width": info.get("width"),
        "height": info.get("height"),
        "fps": info.get("fps"),
        "filesize": info.get("filesize") or info.get("filesize_approx"),
    }

