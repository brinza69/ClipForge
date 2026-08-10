"""
ClipForge — Video Transformare TikTok: on-disk project storage.

project.json is the single source of truth for a transformation project.
Everything here is plain dict/JSON (no ORM, no new SQL table) — the same
precedent the doodle feature set (services/doodle/storage.py). Every write is
atomic (temp file + os.replace) so a crash mid-write never corrupts the file.

Layout (settings.tiktok_dir / {project_id}/), per PRPs/tiktok-transformation.md:
    project.json
    source/     original_tiktok.mp4, original_audio.wav
    frames/     candidates/frame_0000.jpg …, selected/
    script/     transcript.txt, transcript.json, description.txt
    audio/      voiceover.wav, mixed_audio.wav
    subtitles/  subtitles.srt, subtitles.ass
    thumbnail/  thumbnail_before_after.png, thumbnail_mystery.png,
                thumbnail_final_result.png
    preview/
    export/     final_1080x1920_60fps.mp4

The wizard shows 9 steps but only 7 run background jobs: "montage" and
"subtitles" are configuration screens whose settings feed the single fused
export encode (mirroring remix_pipeline's fused speed-match + caption burn,
which avoids a second generation of compression loss).
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("clipforge.tiktok.storage")

# The 9 wizard steps, in order. `job` marks the ones that enqueue a background
# job; the other two are pure settings screens consumed by the export step.
STEPS: list[dict[str, Any]] = [
    {"id": "import", "label": "Import", "job": "tiktok_import"},
    {"id": "frames", "label": "Cadre", "job": "tiktok_frames"},
    {"id": "script", "label": "Script", "job": "tiktok_script"},
    {"id": "voice", "label": "Voce", "job": "tiktok_voice"},
    {"id": "montage", "label": "Montaj", "job": None},
    {"id": "subtitles", "label": "Subtitrări", "job": None},
    {"id": "thumbnail", "label": "Thumbnail", "job": "tiktok_thumbnails"},
    {"id": "description", "label": "Descriere", "job": "tiktok_description"},
    {"id": "export", "label": "Export", "job": "tiktok_render"},
]

STEP_IDS = [s["id"] for s in STEPS]

# Defaults come from the user's existing "Narator" variant preset
# (data/variant_presets/narator.json) so the wizard sounds/looks like the rest
# of the rig out of the box. Loaded lazily in _voice_defaults() so a missing
# preset file degrades to these literals instead of crashing project creation.
_NARATOR_FALLBACK = {
    "voice_id": "8nBBDfYxYXmDNaqTCxPH",
    "speed": 1.1,
    "caption_template_id": "bold_impact",
    "caption_scale": 2.0,
    "caption_words_per_chunk": 1,
}

DEFAULT_SETTINGS: dict[str, Any] = {
    # ── Script (spec §7) ──────────────────────────────────────────────────
    "language": "ro",
    "min_chars": 1100,
    "max_chars": 1250,
    "target_chars": 1200,
    "style": "cinematic",          # cinematic | misterios | energic | documentar
    "include_no_scroll": True,     # the "Nu da scroll!" opener
    "include_question": True,
    "include_cta": True,
    "cta_text": "Lasă un like și dă follow pentru mai multe transformări incredibile!",
    # ── Frames (spec §6) ──────────────────────────────────────────────────
    "target_frames": 20,           # aim for 12–30 important frames
    "min_frames": 12,
    "max_frames": 30,
    "scene_threshold": 0.28,       # ffmpeg scene-detect sensitivity
    # ── Voice (spec §9) ───────────────────────────────────────────────────
    "tts_engine": "elevenlabs",
    "tts_voice_id": None,          # filled from the Narator preset
    "tts_model_id": "eleven_multilingual_v2",
    "tts_speed": None,             # filled from the Narator preset (1.1)
    "tts_stability": 0.5,
    "tts_similarity": 0.75,
    "tts_style": 0.0,
    "tts_speaker_boost": True,
    "voice_gain_db": 0.0,
    "fade_ms": 60,
    "normalize_audio": True,
    # ── Montage (spec §10) — user chose the "original video + speed-match"
    # model: the source clip stays the base and is time-stretched onto the
    # voice-over length (services/speed_match.compute_speed_plan).
    "montage_mode": "speed_match",
    "original_audio_volume": 0.0,  # 0.0 = mute original, 1.0 = keep as-is
    "min_output_duration_s": 60.0,  # user requirement: 1 minute+ videos
    # ── Subtitles (spec §11) ──────────────────────────────────────────────
    "subtitle_mode": "minimal",    # none | minimal | dynamic
    "caption_template_id": None,   # filled from the Narator preset
    "caption_scale": None,         # filled from the Narator preset
    "caption_words_per_chunk": None,
    "caption_uppercase": False,
    "caption_strip_punct": True,
    # ── Export (spec §15) ─────────────────────────────────────────────────
    "export_width": 1080,
    "export_height": 1920,
    "export_fps": 60,
    "export_crf": 19,              # spec recommends 18–21
    "export_preset": "medium",
    "export_audio_bitrate": "192k",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


def _voice_defaults() -> dict[str, Any]:
    """Pull voice/caption defaults from the saved 'Narator' variant preset."""
    out = dict(_NARATOR_FALLBACK)
    try:
        from services.variant_presets import load_preset

        preset = load_preset("narator") or {}
        if preset.get("tts_voice_id"):
            out["voice_id"] = preset["tts_voice_id"]
        if preset.get("tts_speed"):
            out["speed"] = float(preset["tts_speed"])
        for src, dst in (
            ("caption_template_id", "caption_template_id"),
            ("caption_scale", "caption_scale"),
            ("caption_words_per_chunk", "caption_words_per_chunk"),
        ):
            if preset.get(src) is not None:
                out[dst] = preset[src]
    except Exception:
        logger.warning("narator preset unreadable; using built-in defaults", exc_info=True)
    return out


# ── Paths ────────────────────────────────────────────────────────────────────

def project_dir(project_id: str) -> Path:
    from config import settings

    return settings.tiktok_dir / project_id


def _project_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


_SUBDIRS = (
    "source",
    "frames",
    "frames/candidates",
    "frames/selected",
    "script",
    "audio",
    "subtitles",
    "thumbnail",
    "preview",
    "export",
)


def _ensure_subdirs(pdir: Path) -> None:
    for sub in _SUBDIRS:
        (pdir / sub).mkdir(parents=True, exist_ok=True)


def paths(project_id: str) -> dict[str, Path]:
    """Canonical artifact paths for a project (spec §5 layout)."""
    d = project_dir(project_id)
    return {
        "dir": d,
        "project_json": d / "project.json",
        "source_video": d / "source" / "original_tiktok.mp4",
        "source_audio": d / "source" / "original_audio.wav",
        "candidates_dir": d / "frames" / "candidates",
        "selected_dir": d / "frames" / "selected",
        "transcript_txt": d / "script" / "transcript.txt",
        "transcript_json": d / "script" / "transcript.json",
        "description_txt": d / "script" / "description.txt",
        "voiceover": d / "audio" / "voiceover.wav",
        "mixed_audio": d / "audio" / "mixed_audio.wav",
        "subtitles_srt": d / "subtitles" / "subtitles.srt",
        "subtitles_ass": d / "subtitles" / "subtitles.ass",
        "thumb_before_after": d / "thumbnail" / "thumbnail_before_after.png",
        "thumb_mystery": d / "thumbnail" / "thumbnail_mystery.png",
        "thumb_final": d / "thumbnail" / "thumbnail_final_result.png",
        "preview_dir": d / "preview",
        "export": d / "export" / "final_1080x1920_60fps.mp4",
    }


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


# ── Step state ───────────────────────────────────────────────────────────────

def _blank_steps() -> dict[str, dict[str, Any]]:
    """Per-step independent status, mirroring doodle's sb['renders'][mode]."""
    return {
        sid: {"status": "pending", "error": None, "updated_at": None}
        for sid in STEP_IDS
    }


def set_step(
    project: dict,
    step_id: str,
    status: str,
    error: str | None = None,
) -> dict:
    """Mutate (in place) one step's status. Caller still has to save_project()."""
    if step_id not in STEP_IDS:
        raise ValueError(f"unknown step {step_id!r}")
    steps = project.setdefault("steps", _blank_steps())
    steps[step_id] = {
        "status": status,
        "error": (str(error)[:600] if error else None),
        "updated_at": _now_iso(),
    }
    return project


def mark_step(project_id: str, step_id: str, status: str, error: str | None = None) -> dict:
    """Reload-mutate-save one step atomically.

    Always re-reads from disk first (never trusts an in-memory copy) so a
    concurrent write from another stage is not clobbered — the same discipline
    every doodle handler follows.
    """
    project = load_project(project_id)
    set_step(project, step_id, status, error)
    save_project(project_id, project)
    return project


# ── Project CRUD ─────────────────────────────────────────────────────────────

def create_project(payload: dict) -> dict:
    """Create a new transformation project on disk. Returns the project dict."""
    project_id = _uuid()
    pdir = project_dir(project_id)
    _ensure_subdirs(pdir)

    vd = _voice_defaults()
    settings_in = dict(DEFAULT_SETTINGS)
    settings_in["tts_voice_id"] = vd["voice_id"]
    settings_in["tts_speed"] = vd["speed"]
    settings_in["caption_template_id"] = vd["caption_template_id"]
    settings_in["caption_scale"] = vd["caption_scale"]
    settings_in["caption_words_per_chunk"] = vd["caption_words_per_chunk"]
    for key in DEFAULT_SETTINGS:
        if key in payload and payload[key] is not None:
            settings_in[key] = payload[key]

    now = _now_iso()
    project = {
        "id": project_id,
        "title": (payload.get("title") or "").strip(),
        "url": (payload.get("url") or "").strip(),
        "status": "created",
        "error": None,
        "settings": settings_in,
        # ── Step 1: import ────────────────────────────────────────────────
        "source": {
            "path": None,
            "audio_path": None,
            "duration": None,
            "width": None,
            "height": None,
            "fps": None,
            "title": None,
            "uploader": None,
            "description": None,
        },
        # ── Step 2: frames ────────────────────────────────────────────────
        # candidates: [{index, t, file, sharpness, score, stage}]
        # selected:   ordered list of candidate indexes
        # marks:      {"before": idx, "after": idx, "hook": idx,
        #              "thumbnail": idx, "final_reveal": idx}
        "frames": {"candidates": [], "selected": [], "marks": {}},
        # ── Step 3: script ────────────────────────────────────────────────
        # vision_notes: per-frame descriptions produced by the vision pass
        "script": {
            "text": "",
            "chars": 0,
            "words": 0,
            "estimated_duration": 0.0,
            "vision_notes": [],
            "generated_at": None,
        },
        # ── Step 4: voice ─────────────────────────────────────────────────
        "voice": {"path": None, "duration": None, "generated_at": None},
        # ── Step 9: export ────────────────────────────────────────────────
        "export": {
            "path": None,
            "duration": None,
            "width": None,
            "height": None,
            "fps": None,
            "size": None,
            "speed_factor": None,
            "exported_at": None,
        },
        # ── Steps 7–8 ─────────────────────────────────────────────────────
        "thumbnails": {"variants": [], "selected": None},
        "description": {"text": "", "hashtags": [], "variant": "normal"},
        "steps": _blank_steps(),
        "created_at": now,
        "updated_at": now,
    }
    save_project(project_id, project)
    logger.info(f"tiktok project {project_id} created url={project['url'][:80]!r}")
    return project


def load_project(project_id: str) -> dict:
    path = _project_path(project_id)
    if not path.exists():
        raise FileNotFoundError(f"project {project_id} not found")
    return json.loads(path.read_text(encoding="utf-8"))


def save_project(project_id: str, project: dict) -> None:
    project["updated_at"] = _now_iso()
    _atomic_write_text(
        _project_path(project_id),
        json.dumps(project, ensure_ascii=False, indent=2),
    )


def _summary(project: dict) -> dict:
    """Compact row for the project list (avoids shipping every candidate frame)."""
    return {
        "id": project.get("id"),
        "title": project.get("title") or project.get("source", {}).get("title") or "",
        "url": project.get("url"),
        "status": project.get("status"),
        "error": project.get("error"),
        "duration": project.get("source", {}).get("duration"),
        "frames_selected": len(project.get("frames", {}).get("selected") or []),
        "has_voice": bool(project.get("voice", {}).get("path")),
        "has_export": bool(project.get("export", {}).get("path")),
        "steps": project.get("steps") or {},
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
    }


def list_projects() -> list[dict]:
    from config import settings

    root = settings.tiktok_dir
    if not root.exists():
        return []
    out: list[dict] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        pj = child / "project.json"
        if not pj.exists():
            continue
        try:
            out.append(_summary(json.loads(pj.read_text(encoding="utf-8"))))
        except Exception:
            logger.warning(f"skipping unreadable project.json in {child.name}")
    out.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return out


def delete_project(project_id: str) -> bool:
    pdir = project_dir(project_id)
    if not pdir.exists():
        return False
    shutil.rmtree(pdir, ignore_errors=True)
    logger.info(f"tiktok project {project_id} deleted")
    return True


def patch_settings(project_id: str, patch: dict) -> dict:
    """Merge a partial settings update (used by the Montaj/Subtitrări screens)."""
    project = load_project(project_id)
    settings_in = project.setdefault("settings", dict(DEFAULT_SETTINGS))
    for key, value in patch.items():
        if key in DEFAULT_SETTINGS and value is not None:
            settings_in[key] = value
    save_project(project_id, project)
    return project
