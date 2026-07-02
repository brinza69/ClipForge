"""
ClipForge — Auto Story Doodle: on-disk project storage.

storyboard.json is the single source of truth for a doodle project. All
reads/writes here treat it as plain dict/JSON (no ORM) — see PRPs/auto-story-doodle.md
for the full schema. Every write is atomic (write to a temp file, then
os.replace) so a crash mid-write never corrupts storyboard.json.

Layout (settings.doodle_dir / {project_id}/):
    storyboard.json
    script/script.json, script/script.txt
    prompts/flow_prompts.csv, prompts/flow_prompts.json
    audio/scene_000.wav …, audio/final_voiceover.wav
    images/scene_000.png …
    captions/captions.srt
    exports/final_video.mp4
"""

from __future__ import annotations

import csv
import io
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("clipforge.doodle.storage")

DEFAULT_SETTINGS: dict[str, Any] = {
    "target_duration_seconds": 180,
    "frame_interval_seconds": 3,
    "aspect_ratio": "16:9",
    "resolution": "1920x1080",
    "voice": "am_michael",
    "voice_speed": 0.95,
    "subtitle_style": "youtube_clean",
    "burn_subtitles": True,
    "motion_style": "subtle",
    "motion_intensity": 0.5,
    "openai_model": None,
    "render_quality": "high",
    "use_gpu": True,
    "allow_placeholders": False,
}

_RESOLUTION_BY_RATIO = {
    "16:9": "1920x1080",
    "9:16": "1080x1920",
    "1:1": "1080x1080",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


# ── Paths ────────────────────────────────────────────────────────────────────

def project_dir(project_id: str) -> Path:
    from config import settings
    return settings.doodle_dir / project_id


def _storyboard_path(project_id: str) -> Path:
    return project_dir(project_id) / "storyboard.json"


def _ensure_subdirs(pdir: Path) -> None:
    for sub in ("script", "prompts", "audio", "images", "captions", "exports"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)


# ── Atomic JSON I/O ──────────────────────────────────────────────────────────

def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{uuid.uuid4().hex[:8]}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{uuid.uuid4().hex[:8]}")
    tmp.write_bytes(data)
    tmp.replace(path)


# ── Project CRUD ─────────────────────────────────────────────────────────────

def create_project(payload: dict) -> dict:
    """Create a new doodle project on disk. Returns the initial storyboard dict."""
    project_id = _uuid()
    pdir = project_dir(project_id)
    _ensure_subdirs(pdir)

    settings_in = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if key in payload and payload[key] is not None:
            settings_in[key] = payload[key]
    aspect = settings_in.get("aspect_ratio", "16:9")
    settings_in["resolution"] = _RESOLUTION_BY_RATIO.get(aspect, settings_in["resolution"])

    now = _now_iso()
    storyboard = {
        "id": project_id,
        "title": payload.get("title") or "",
        "description": "",
        "tags": [],
        "topic": payload.get("topic") or "",
        "niche": payload.get("niche") or "history",
        "mode": payload.get("mode") or "topic",
        # Persisted so script generation can run later as a separate step
        # (creation never calls OpenAI / Kokoro / FFmpeg).
        "script_text": payload.get("script_text"),
        "status": "created",
        "error": None,
        "settings": settings_in,
        "scenes": [],
        "final_voiceover_path": None,
        "total_audio_duration": None,
        "export_path": None,
        "created_at": now,
        "updated_at": now,
    }
    save_storyboard(project_id, storyboard)
    logger.info(f"doodle project created: {project_id}")
    return storyboard


def load_storyboard(project_id: str) -> dict:
    path = _storyboard_path(project_id)
    if not path.exists():
        raise FileNotFoundError(f"Doodle project not found: {project_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_storyboard(project_id: str, sb: dict) -> None:
    sb["updated_at"] = _now_iso()
    path = _storyboard_path(project_id)
    _ensure_subdirs(project_dir(project_id))
    _atomic_write_text(path, json.dumps(sb, indent=2, ensure_ascii=False))


def list_projects() -> list[dict]:
    """Storyboard summaries, newest first."""
    from config import settings

    root = settings.doodle_dir
    if not root.exists():
        return []

    out: list[dict] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        sb_path = child / "storyboard.json"
        if not sb_path.exists():
            continue
        try:
            sb = json.loads(sb_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception(f"failed to read storyboard for {child.name}")
            continue
        out.append(_summary(sb))

    out.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return out


def _summary(sb: dict) -> dict:
    scenes = sb.get("scenes") or []
    images_uploaded = sum(1 for s in scenes if s.get("image_path"))
    return {
        "id": sb.get("id"),
        "title": sb.get("title"),
        "topic": sb.get("topic"),
        "niche": sb.get("niche"),
        "status": sb.get("status"),
        "scene_count": len(scenes),
        "images_uploaded": images_uploaded,
        "missing_images": missing_images(sb),
        "created_at": sb.get("created_at"),
        "total_audio_duration": sb.get("total_audio_duration"),
        "export_path": sb.get("export_path"),
        "settings": sb.get("settings"),
    }


def delete_project(project_id: str) -> None:
    pdir = project_dir(project_id)
    if pdir.exists():
        shutil.rmtree(pdir, ignore_errors=True)
        logger.info(f"doodle project deleted: {project_id}")


# ── Prompt exports ───────────────────────────────────────────────────────────

def write_prompt_exports(project_id: str, sb: dict) -> None:
    """Writes prompts/flow_prompts.csv + .json and script/script.json + .txt
    from the current storyboard. Regenerated any time it's requested so the
    exports always reflect the latest scene edits/reorder."""
    pdir = project_dir(project_id)
    _ensure_subdirs(pdir)
    scenes = sb.get("scenes") or []

    # script/*
    script_json = {
        "title": sb.get("title"),
        "description": sb.get("description"),
        "tags": sb.get("tags"),
        "scenes": scenes,
    }
    _atomic_write_text(
        pdir / "script" / "script.json",
        json.dumps(script_json, indent=2, ensure_ascii=False),
    )
    full_text = "\n\n".join((s.get("narration") or "") for s in scenes)
    _atomic_write_text(pdir / "script" / "script.txt", full_text)

    # prompts/*
    rows = [
        {
            "index": s.get("index"),
            "narration": s.get("narration") or "",
            "subtitle": s.get("subtitle") or "",
            "image_prompt": s.get("image_prompt") or "",
            "expected_filename": s.get("flow_filename") or f"scene_{int(s.get('index', 0)):03d}.png",
        }
        for s in scenes
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["index", "narration", "subtitle", "image_prompt", "expected_filename"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    _atomic_write_text(pdir / "prompts" / "flow_prompts.csv", buf.getvalue())
    _atomic_write_text(
        pdir / "prompts" / "flow_prompts.json",
        json.dumps(rows, indent=2, ensure_ascii=False),
    )


# ── Scene helpers ────────────────────────────────────────────────────────────

def missing_images(sb: dict) -> list[int]:
    """Scene indexes with image_path None or the file missing on disk."""
    pdir = project_dir(sb.get("id", ""))
    out: list[int] = []
    for s in sb.get("scenes") or []:
        image_path = s.get("image_path")
        if not image_path or not (pdir / image_path).exists():
            out.append(int(s.get("index", 0)))
    return out
