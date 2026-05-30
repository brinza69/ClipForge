"""
ClipForge — Commentator Presets

A "commentator" is a small character video that gets composited as a layer
over the final captioned remix — like a podcast-style avatar in the corner
that talks/moves along with the main video.

Each preset lives in its own folder:

    data/commentators/
      └── grumpy_kid/
          ├── meta.json          # name, default position, scale, chroma settings
          ├── video.mp4          # the source clip (chroma-keyed or alpha)
          └── thumb.jpg          # first-frame thumbnail for the UI picker

meta.json:
    {
      "id":               "grumpy_kid",
      "name":             "Grumpy Kid",
      "default_position": "bottom-left",  # one of corners or "custom"
      "default_scale":    0.30,           # fraction of main video width
      "chroma_key":       null,           # null = no key; or "#00FF00", "#FFFFFF", etc.
      "chroma_similarity": 0.10,          # tolerance (0..1)
      "chroma_blend":     0.05            # edge softness (0..1)
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from config import settings

logger = logging.getLogger("clipforge.commentators")

VALID_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}
MAX_VIDEO_BYTES = 200 * 1024 * 1024  # 200 MB ceiling — these are short loops, not full clips

VALID_POSITIONS = {
    "bottom-left", "bottom-right", "top-left", "top-right",
    "bottom-center", "top-center", "custom",
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def _root() -> Path:
    d = Path(settings.data_dir) / "commentators"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ffmpeg() -> str:
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", name.lower()).strip("_")
    return base[:60] or "commentator"


def _meta_path(preset_id: str) -> Path:
    return _root() / preset_id / "meta.json"


def _video_path(preset_id: str) -> Path:
    # Picks whichever extension is on disk.
    for ext in VALID_VIDEO_EXTS:
        p = _root() / preset_id / f"video{ext}"
        if p.exists():
            return p
    return _root() / preset_id / "video.mp4"


def _ai_processed_path(preset_id: str) -> Path:
    """Output of the AI bg-removal pass — WebM with a real alpha channel."""
    return _root() / preset_id / "processed.webm"


def _thumb_path(preset_id: str) -> Path:
    return _root() / preset_id / "thumb.jpg"


def list_presets() -> List[Dict]:
    """Enumerate all presets on disk."""
    out: List[Dict] = []
    for d in sorted(_root().iterdir()):
        if not d.is_dir():
            continue
        meta_p = d / "meta.json"
        if not meta_p.exists():
            continue
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            meta["id"] = meta.get("id") or d.name
            vid = _video_path(meta["id"])
            meta["video_available"] = vid.exists()
            meta["video_size"] = vid.stat().st_size if vid.exists() else 0
            meta["thumb_available"] = _thumb_path(meta["id"]).exists()
            meta["ai_processed"] = _ai_processed_path(meta["id"]).exists()
            # Probe duration once for the UI ("Avatar loops every 30s")
            if vid.exists() and "duration" not in meta:
                try:
                    meta["duration"] = _probe_duration(str(vid))
                except Exception:
                    meta["duration"] = None
            out.append(meta)
        except Exception as e:
            logger.warning(f"Skipping malformed commentator preset {d.name}: {e}")
    out.sort(key=lambda m: m.get("name", m["id"]).lower())
    return out


def get_preset(preset_id: str) -> Optional[Dict]:
    p = _meta_path(preset_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _probe_duration(path: str) -> float:
    ffprobe = _ffmpeg().replace("ffmpeg", "ffprobe")
    if not Path(ffprobe).exists() and ffprobe != "ffprobe":
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, creationflags=_creationflags(),
    )
    return float(r.stdout.strip() or "0")


def _make_thumb(video_path: Path, thumb_path: Path, time_s: float = 0.5) -> None:
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-ss", f"{max(0.0, time_s):.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", "scale=320:-1",
        "-q:v", "3",
        str(thumb_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_creationflags())
    if r.returncode != 0:
        logger.warning(f"thumb generation failed: {r.stderr[-300:]}")


def _probe_has_alpha(video_path: Path) -> bool:
    """
    Detect whether the uploaded video carries a real alpha channel.

    Two cases that matter in practice:
      - VP9 in WebM with `alpha_mode=1` tag (CapCut "Export with alpha"
        and most modern editors produce this).
      - QuickTime MOV with ProRes 4444 / Animation / HEVC with alpha,
        which show up as `pix_fmt=yuva*` or `bgra` / `rgba` etc.

    Returns False on probe failure so we don't accidentally skip chroma
    keying for files that actually need it.
    """
    try:
        r = subprocess.run(
            [_ffprobe(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=pix_fmt:stream_tags=alpha_mode",
             "-of", "default=noprint_wrappers=1", str(video_path)],
            capture_output=True, text=True, timeout=15, creationflags=_creationflags(),
        )
    except Exception:
        return False
    text = (r.stdout or "").lower()
    if "alpha_mode=1" in text:
        return True
    for pix in ("yuva420p", "yuva422p", "yuva444p", "bgra", "rgba", "argb", "abgr"):
        if f"pix_fmt={pix}" in text:
            return True
    return False


def _ffprobe() -> str:
    f = _ffmpeg()
    p = f.replace("ffmpeg", "ffprobe")
    if Path(p).exists() or p == "ffprobe":
        return p
    return shutil.which("ffprobe") or "ffprobe"


def save_preset(
    *,
    name: str,
    video_bytes: bytes,
    video_filename: str,
    default_position: str = "bottom-left",
    default_scale: float = 0.30,
    chroma_key: Optional[str] = None,
    chroma_similarity: float = 0.10,
    chroma_blend: float = 0.05,
    preset_id: Optional[str] = None,
) -> Dict:
    """Persist a new (or replace existing) commentator preset."""
    if not name or not name.strip():
        raise ValueError("name is required")
    if len(video_bytes) > MAX_VIDEO_BYTES:
        raise ValueError(
            f"Video too large ({len(video_bytes) // (1024*1024)} MB); "
            f"max {MAX_VIDEO_BYTES // (1024*1024)} MB"
        )
    ext = Path(video_filename).suffix.lower()
    if ext not in VALID_VIDEO_EXTS:
        raise ValueError(f"Unsupported video format {ext or '(none)'}; allowed: {sorted(VALID_VIDEO_EXTS)}")
    if default_position not in VALID_POSITIONS:
        raise ValueError(f"position must be one of {sorted(VALID_POSITIONS)}")
    if not (0.05 <= float(default_scale) <= 1.0):
        raise ValueError("default_scale must be in [0.05, 1.0]")

    pid = (preset_id or _slugify(name)).lower()
    if not _ID_RE.match(pid):
        raise ValueError("preset_id must be lowercase letters/digits/_/- only (1–63 chars)")

    folder = _root() / pid
    folder.mkdir(parents=True, exist_ok=True)

    # Clean any prior video file (extension may differ from the new one)
    for old_ext in VALID_VIDEO_EXTS:
        old = folder / f"video{old_ext}"
        if old.exists():
            try: old.unlink()
            except Exception: pass

    video_path = folder / f"video{ext}"
    video_path.write_bytes(video_bytes)

    # Thumbnail at 0.5s — good enough to identify in the picker
    try:
        _make_thumb(video_path, _thumb_path(pid))
    except Exception as e:
        logger.warning(f"thumb gen for {pid} failed (non-fatal): {e}")

    duration = 0.0
    try:
        duration = _probe_duration(str(video_path))
    except Exception:
        pass

    has_alpha = _probe_has_alpha(video_path)
    if has_alpha:
        logger.info(f"preset {pid}: source has native alpha — chromakey will be skipped")

    meta = {
        "id": pid,
        "name": name.strip(),
        "default_position": default_position,
        "default_scale": float(default_scale),
        # When the upload already carries alpha (CapCut-exported webm with
        # alpha, ProRes 4444 mov, etc.), chroma settings are stored but
        # bypassed by the overlay stage.
        "has_native_alpha": has_alpha,
        "chroma_key": chroma_key,
        "chroma_similarity": float(chroma_similarity),
        "chroma_blend": float(chroma_blend),
        "duration": duration,
        "filename": Path(video_filename).name,
    }
    _meta_path(pid).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    logger.info(f"saved commentator preset {pid} ({len(video_bytes) // 1024} KB, {duration:.1f}s)")
    return meta


def update_chroma(
    preset_id: str,
    *,
    chroma_key: Optional[str] = None,
    chroma_similarity: Optional[float] = None,
    chroma_blend: Optional[float] = None,
) -> Dict:
    """Partial update of the chroma-key settings on an existing preset."""
    meta_p = _meta_path(preset_id)
    if not meta_p.exists():
        raise FileNotFoundError(preset_id)
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    # Treat empty string as "disable keying".
    if chroma_key is not None:
        meta["chroma_key"] = chroma_key.strip() if chroma_key.strip() else None
    if chroma_similarity is not None:
        if not (0.0 <= float(chroma_similarity) <= 1.0):
            raise ValueError("chroma_similarity must be in [0, 1]")
        meta["chroma_similarity"] = float(chroma_similarity)
    if chroma_blend is not None:
        if not (0.0 <= float(chroma_blend) <= 1.0):
            raise ValueError("chroma_blend must be in [0, 1]")
        meta["chroma_blend"] = float(chroma_blend)
    meta_p.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def delete_preset(preset_id: str) -> None:
    folder = _root() / preset_id
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(preset_id)
    shutil.rmtree(folder, ignore_errors=True)
    logger.info(f"deleted commentator preset {preset_id}")


# ── Coordinate helpers used by the overlay stage ───────────────────────────


def position_to_xy(
    position: str,
    main_w: int,
    main_h: int,
    overlay_w: int,
    overlay_h: int,
    margin_pct: float = 0.02,
) -> tuple[int, int]:
    """Translate a position label into pixel offsets for ffmpeg's overlay filter."""
    mx = int(main_w * margin_pct)
    my = int(main_h * margin_pct)
    if position == "bottom-left":
        return mx, max(my, main_h - overlay_h - my)
    if position == "bottom-right":
        return max(mx, main_w - overlay_w - mx), max(my, main_h - overlay_h - my)
    if position == "top-left":
        return mx, my
    if position == "top-right":
        return max(mx, main_w - overlay_w - mx), my
    if position == "bottom-center":
        return max(0, (main_w - overlay_w) // 2), max(my, main_h - overlay_h - my)
    if position == "top-center":
        return max(0, (main_w - overlay_w) // 2), my
    # "custom" or unknown: dead-center as a safe fallback
    return max(0, (main_w - overlay_w) // 2), max(0, (main_h - overlay_h) // 2)
