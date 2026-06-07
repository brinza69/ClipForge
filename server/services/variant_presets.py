"""
ClipForge — Variant Presets

A variant preset is a saved bundle of one Parallel-Processing variant's
settings: voice (engine + voice + language + speed), caption template +
style, and commentator. Lets the user configure a look once ("Grinch",
"Narrator") and reload it into any variant slot.

Stored as flat JSON files in data/variant_presets/{id}.json — same simple
file-store pattern as caption_templates and commentators.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("clipforge.variant_presets")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

# Fields a preset carries. Mirrors the frontend VariantState minus volatile
# per-run bits. `name` doubles as the preset's display label.
_FIELDS = (
    "name",
    "tts_engine", "tts_voice_id", "tts_language", "tts_speed",
    "caption_template_id", "caption_font_family", "caption_scale",
    "caption_text_color", "caption_uppercase", "caption_italic",
    "caption_words_per_chunk", "caption_strip_punct",
    "commentator_preset_id",
    "drive_folder",
    "split_into_parts",
)


def _root() -> Path:
    from config import settings
    d = Path(settings.data_dir) / "variant_presets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s[:63] or "preset"


def list_presets() -> List[dict]:
    """All saved presets, newest first."""
    out: List[dict] = []
    for p in _root().glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data["id"] = p.stem
            out.append(data)
        except Exception:
            logger.warning(f"skipping unreadable preset {p.name}")
    out.sort(key=lambda d: d.get("_saved_at", 0), reverse=True)
    return out


def save_preset(name: str, fields: dict, preset_id: Optional[str] = None) -> dict:
    """Persist a preset. Returns the stored record (with its id)."""
    import time

    if not name or not name.strip():
        raise ValueError("Preset name is required")

    pid = (preset_id or _slugify(name)).lower()
    if not _ID_RE.match(pid):
        raise ValueError("preset id must be lowercase letters/digits/_/- (1–63 chars)")

    record = {k: fields.get(k) for k in _FIELDS}
    record["name"] = name.strip()
    record["_saved_at"] = time.time()

    (_root() / f"{pid}.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    record["id"] = pid
    logger.info(f"saved variant preset '{pid}'")
    return record


def delete_preset(preset_id: str) -> None:
    p = _root() / f"{preset_id}.json"
    if not p.exists():
        raise FileNotFoundError(preset_id)
    p.unlink()
    logger.info(f"deleted variant preset '{preset_id}'")
