"""
ClipForge — Metadata Service
Lightweight metadata + thumbnail extraction from URLs using yt-dlp.
No video download happens here — this is metadata-only.
"""

import logging
import subprocess
import json
import re
import httpx
from pathlib import Path
from typing import Optional, Dict, Any

from config import settings
from models import SourceType

logger = logging.getLogger("clipforge.metadata")


def detect_source_type(url: str) -> str:
    """Detect platform from URL pattern."""
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return SourceType.youtube.value
    elif "twitch.tv" in url_lower:
        return SourceType.twitch.value
    elif "vimeo.com" in url_lower:
        return SourceType.vimeo.value
    elif url_lower.endswith((".mp4", ".webm", ".mkv", ".m3u8")):
        return SourceType.direct.value
    return SourceType.unknown.value


def format_duration(seconds: Optional[float]) -> Optional[str]:
    """Format seconds into HH:MM:SS or MM:SS."""
    if seconds is None:
        return None
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_filesize(size_bytes: Optional[int]) -> Optional[str]:
    """Format bytes to human-readable size."""
    if size_bytes is None:
        return None
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


async def fetch_metadata(url: str, project_id: str) -> Dict[str, Any]:
    """
    Fetch video metadata and thumbnail without downloading the video.
    Uses yt-dlp --dump-json for metadata extraction.
    Returns a dict matching ProjectMetadata schema fields.
    """
    logger.info(f"Fetching metadata for: {url}")

    source_type = detect_source_type(url)

    try:
        # Use yt-dlp to extract metadata only (no download)
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-download",
            "--no-playlist",
            "--no-warnings",
            url,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

        if result.returncode != 0:
            logger.error(f"yt-dlp metadata extraction failed: {result.stderr}")
            raise RuntimeError(f"Failed to fetch metadata: {result.stderr[:200]}")

        info = json.loads(result.stdout)

        # Extract best format for size estimation
        estimated_size = info.get("filesize") or info.get("filesize_approx")
        if not estimated_size and info.get("formats"):
            # Try to estimate from best format
            for fmt in reversed(info.get("formats", [])):
                if fmt.get("filesize"):
                    estimated_size = fmt["filesize"]
                    break
                elif fmt.get("filesize_approx"):
                    estimated_size = fmt["filesize_approx"]
                    break

        # Download thumbnail
        thumbnail_url = info.get("thumbnail")
        thumbnail_path = None
        if thumbnail_url:
            thumbnail_path = await download_thumbnail(thumbnail_url, project_id)

        # Build available formats list (simplified)
        formats_available = []
        for fmt in info.get("formats", []):
            if fmt.get("vcodec") != "none" and fmt.get("height"):
                formats_available.append({
                    "format_id": fmt.get("format_id"),
                    "resolution": f"{fmt.get('width', '?')}x{fmt.get('height', '?')}",
                    "ext": fmt.get("ext"),
                    "fps": fmt.get("fps"),
                    "filesize": fmt.get("filesize") or fmt.get("filesize_approx"),
                    "filesize_formatted": format_filesize(fmt.get("filesize") or fmt.get("filesize_approx")),
                    "vcodec": fmt.get("vcodec", ""),
                    "acodec": fmt.get("acodec", ""),
                })

        # Deduplicate and sort by resolution
        seen = set()
        unique_formats = []
        for f in formats_available:
            key = f["resolution"]
            if key not in seen:
                seen.add(key)
                unique_formats.append(f)
        unique_formats.sort(key=lambda x: int(x["resolution"].split("x")[-1]) if "x" in x["resolution"] else 0, reverse=True)

        duration = info.get("duration")

        metadata = {
            "title": info.get("title", "Unknown Title"),
            "channel_name": info.get("uploader") or info.get("channel") or info.get("uploader_id"),
            "duration": duration,
            "duration_formatted": format_duration(duration),
            "source_type": source_type,
            "width": info.get("width"),
            "height": info.get("height"),
            "fps": info.get("fps"),
            "thumbnail_url": thumbnail_url,
            "thumbnail_path": str(thumbnail_path) if thumbnail_path else None,
            "estimated_size": estimated_size,
            "estimated_size_formatted": format_filesize(estimated_size),
            "upload_date": info.get("upload_date"),
            "description": (info.get("description") or "")[:500],
            "formats_available": unique_formats[:10],  # Top 10 formats
        }

        logger.info(f"Metadata fetched: {metadata['title']} ({metadata['duration_formatted']})")
        return metadata

    except subprocess.TimeoutExpired:
        raise RuntimeError("Metadata fetch timed out (30s). Try again or check your connection.")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse metadata response: {e}")


async def download_thumbnail(url: str, project_id: str) -> Optional[Path]:
    """Download a thumbnail image to the thumbnails directory."""
    try:
        thumb_dir = settings.thumbnails_dir / project_id
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = thumb_dir / "thumbnail.jpg"

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            thumb_path.write_bytes(resp.content)
            logger.info(f"Thumbnail saved: {thumb_path}")
            return thumb_path

    except Exception as e:
        logger.warning(f"Failed to download thumbnail: {e}")
        return None


async def validate_url(url: str) -> Dict[str, Any]:
    """Quick URL validation without full metadata fetch."""
    url = url.strip()
    if not url:
        return {"valid": False, "error": "URL is empty"}

    # Basic URL pattern check
    url_pattern = re.compile(
        r'^https?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE
    )

    if not url_pattern.match(url):
        return {"valid": False, "error": "Invalid URL format"}

    source_type = detect_source_type(url)
    return {
        "valid": True,
        "source_type": source_type,
        "url": url,
    }
