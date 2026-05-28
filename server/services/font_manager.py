"""
ClipForge — Font Manager

Tracks two font sources:
  * System fonts (whatever the OS exposes; we list them via fontTools where
    available, else a small curated whitelist that we know exists on common
    Win/Linux setups).
  * User fonts dropped into `data/fonts/` (or uploaded via the API). These
    are passed to libass via ffmpeg's -fontsdir flag so a freshly uploaded
    .ttf is usable in the very next preview render.

A "font" is identified by its family name (e.g. "Impact"), which is what
gets written into the ASS file's Fontname field.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set

from config import settings

logger = logging.getLogger("clipforge.font_manager")

ALLOWED_EXTS = {".ttf", ".otf", ".ttc"}
MAX_FONT_SIZE = 25 * 1024 * 1024  # 25 MB per font file


# Curated list of system fonts that ship on common platforms. We use this
# as a fallback when fontTools isn't installed — better to show a small,
# reliable set than nothing. Captioner presets pull from this list, so it
# also acts as the "fonts you can rely on for cross-machine playback."
_KNOWN_SYSTEM_FONTS = [
    # Windows native
    "Impact", "Arial Black", "Arial", "Segoe UI", "Segoe UI Black",
    "Bahnschrift", "Bahnschrift Bold", "Calibri", "Consolas", "Tahoma",
    "Times New Roman", "Verdana", "Trebuchet MS",
    # Cross-platform popular
    "Helvetica", "Comic Sans MS", "Courier New",
]


def fonts_dir() -> Path:
    p = Path(settings.data_dir) / "fonts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _enumerate_system_fonts() -> List[str]:
    """Best-effort system-font scan. fontTools if present, else curated list."""
    try:
        # fontTools ships its own ttLib; available without extra deps if a
        # whisper/torch dep pulled it in.
        from fontTools.ttLib import TTFont  # type: ignore
        roots: List[Path] = []
        if os.name == "nt":
            roots += [Path(r"C:\Windows\Fonts")]
            local = os.environ.get("LOCALAPPDATA")
            if local:
                roots.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
        else:
            for r in (
                "/usr/share/fonts",
                "/usr/local/share/fonts",
                "/mnt/c/Windows/Fonts",  # WSL: pick up Windows fonts too
                str(Path.home() / ".fonts"),
                str(Path.home() / ".local/share/fonts"),
            ):
                roots.append(Path(r))

        names: Set[str] = set()
        for root in roots:
            if not root.exists():
                continue
            for f in root.rglob("*"):
                if f.suffix.lower() not in ALLOWED_EXTS:
                    continue
                try:
                    tt = TTFont(str(f), lazy=True)
                    name_table = tt["name"]
                    family = (
                        name_table.getDebugName(16)  # preferred family
                        or name_table.getDebugName(1)  # legacy family
                    )
                    if family:
                        names.add(family.strip())
                    tt.close()
                except Exception:
                    continue
        if names:
            return sorted(names)
    except Exception as e:
        logger.debug(f"fontTools scan unavailable, using curated list: {e}")
    return sorted(_KNOWN_SYSTEM_FONTS)


def _enumerate_user_fonts() -> List[Dict]:
    out: List[Dict] = []
    for f in sorted(fonts_dir().iterdir()):
        if f.suffix.lower() not in ALLOWED_EXTS:
            continue
        family = _family_from_file(f) or f.stem
        out.append({"family": family, "filename": f.name, "size": f.stat().st_size})
    return out


def _family_from_file(path: Path) -> Optional[str]:
    try:
        from fontTools.ttLib import TTFont  # type: ignore
        tt = TTFont(str(path), lazy=True)
        name = (
            tt["name"].getDebugName(16) or tt["name"].getDebugName(1)
        )
        tt.close()
        return (name or "").strip() or None
    except Exception:
        return None


def list_fonts() -> Dict:
    """Combined list of system + user-uploaded fonts."""
    system = _enumerate_system_fonts()
    user = _enumerate_user_fonts()
    # Dedupe: a user-uploaded font with the same family as a system font is
    # still reported under user (so the user knows the file is theirs).
    user_families = {u["family"].lower() for u in user}
    system = [s for s in system if s.lower() not in user_families]
    return {
        "system": system,
        "user": user,
        "fonts_dir": str(fonts_dir()),
    }


def save_uploaded_font(filename: str, content: bytes) -> Dict:
    """Persist an uploaded font file. Returns the saved entry."""
    if not filename:
        raise ValueError("filename required")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(
            f"Unsupported font format {ext or '(none)'}. "
            f"Allowed: {sorted(ALLOWED_EXTS)}"
        )
    if len(content) > MAX_FONT_SIZE:
        raise ValueError(
            f"Font too large ({len(content) // 1024} KB); max {MAX_FONT_SIZE // (1024*1024)} MB"
        )
    # Strip path components from the upload filename for safety.
    safe = Path(filename).name
    dst = fonts_dir() / safe
    if dst.exists():
        # Replace; user is re-uploading the same font.
        logger.info(f"overwriting existing font {safe}")
    dst.write_bytes(content)
    family = _family_from_file(dst) or dst.stem
    logger.info(f"saved font {safe} (family: {family})")
    return {"family": family, "filename": safe, "size": dst.stat().st_size}


def delete_font(filename: str) -> None:
    safe = Path(filename).name
    path = fonts_dir() / safe
    if not path.exists():
        raise FileNotFoundError(filename)
    path.unlink()
    logger.info(f"deleted font {safe}")
