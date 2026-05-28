"""
ClipForge — Caption Template Store

Templates live as one .json file per template in data/caption_templates/.
On first import we seed the directory from the builtin DEFAULT_PRESETS so
existing clips keep working without migration. After that, users can drop
new .json files (or upload them via the UI) and they show up immediately.

Schema (matches what services.captioner.generate_captions accepts):

    {
        "id": "bold_impact",         # filename stem; required
        "name": "Bold Impact",
        "font_family": "Impact",
        "font_size": 72,
        "font_weight": "Bold",
        "text_color": "#FFFFFF",
        "highlight_color": "#FFD700",
        "outline_color": "#000000",
        "outline_width": 5,
        "shadow_offset": 2.5,
        "shadow_color": "#000000B0",
        "position": "bottom",
        "uppercase": true,
        "animation": "word",
        "max_words_per_line": 3,
        "builtin": true              # if true, can't be deleted via UI
    }
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from config import settings

logger = logging.getLogger("clipforge.caption_templates")

# Reuse the existing presets as the seed set — keep `name` and other fields
# byte-identical so existing clips referencing them by id keep rendering.
from services.captioner import DEFAULT_PRESETS  # noqa: E402

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def _dir() -> Path:
    return Path(settings.data_dir) / "caption_templates"


def _seed_if_empty() -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    # Re-write builtins on every boot so updates to DEFAULT_PRESETS propagate.
    # User-created files (without builtin=true) are never touched.
    for tid, preset in DEFAULT_PRESETS.items():
        path = d / f"{tid}.json"
        # If a builtin was customized by the user previously, leave it alone.
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if not existing.get("builtin"):
                    continue
            except Exception:
                pass
        payload = {"id": tid, "builtin": True, **preset}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def list_templates() -> List[Dict]:
    """Return all templates ordered builtins first, then user-added alphabetic."""
    _seed_if_empty()
    out: List[Dict] = []
    for path in sorted(_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["id"] = data.get("id") or path.stem
            out.append(data)
        except Exception as e:
            logger.warning(f"skipping malformed template {path.name}: {e}")
    out.sort(key=lambda t: (not t.get("builtin", False), t.get("name", "").lower()))
    return out


def get_template(template_id: str) -> Optional[Dict]:
    _seed_if_empty()
    path = _dir() / f"{template_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_template(payload: Dict) -> Dict:
    """Validate + persist. `id` field is the filename stem; required."""
    tid = (payload.get("id") or "").strip().lower()
    if not _ID_RE.match(tid):
        raise ValueError(
            "id must be lowercase letters/digits/_/- only (1–63 chars)"
        )
    if not payload.get("name"):
        raise ValueError("`name` is required")

    # Strip control fields a caller shouldn't be able to set on a user template.
    payload = dict(payload)
    payload["id"] = tid
    payload.pop("builtin", None)  # user-saved templates are not builtins

    _seed_if_empty()
    path = _dir() / f"{tid}.json"
    # Refuse to overwrite a builtin via save (DELETE-then-save is the path).
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("builtin"):
                raise ValueError(
                    f"'{tid}' is a builtin template; pick a different id "
                    "to save a custom version."
                )
        except ValueError:
            raise
        except Exception:
            pass

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(f"saved template {tid}")
    return payload


def delete_template(template_id: str) -> None:
    _seed_if_empty()
    path = _dir() / f"{template_id}.json"
    if not path.exists():
        raise FileNotFoundError(template_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("builtin"):
            raise PermissionError(
                f"'{template_id}' is a builtin template and cannot be deleted"
            )
    except (json.JSONDecodeError, OSError):
        # If the file is corrupt we still allow delete (cleanup path).
        pass
    path.unlink()
    logger.info(f"deleted template {template_id}")
