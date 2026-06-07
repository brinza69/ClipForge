"""
ClipForge — Sheets config persistence

Stores the user's Parallel-from-Sheets configuration plus the iterator
state (`next_row`). One config per ClipForge install — the user said
"coloanele nu se vor schimba" (columns are constant), so a single record
is enough.

File: data/sheets_config.json (gitignored, lives next to drive_oauth_*.json).

Shape:
    {
        "spreadsheet_id": "1abc...XYZ",
        "spreadsheet_url": "https://docs.google.com/.../edit",
        "tab": "Sheet1",
        "col_url": "B",
        "col_number": "A",
        "col_description": "C",
        "start_row": 2,
        "next_row": 5,
        "spreadsheet_title": "My TikTok queue",
        "_updated_at": 1738000000.0
    }
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clipforge.sheets_config")


def _config_path() -> Path:
    from config import settings
    return Path(settings.data_dir) / "sheets_config.json"


def load() -> Optional[dict]:
    """Return the saved config or None if not configured yet."""
    p = _config_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("could not read sheets_config.json")
        return None


def save(cfg: dict) -> dict:
    """Write the config (merging on top of existing). Returns the stored doc."""
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load() or {}
    merged = {**existing, **cfg, "_updated_at": time.time()}
    p.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    logger.info(
        f"sheets_config saved: next_row={merged.get('next_row')} "
        f"tab={merged.get('tab')} cols={merged.get('col_url')}/"
        f"{merged.get('col_number')}/{merged.get('col_description')}"
    )
    return merged


def update_next_row(row: int) -> Optional[dict]:
    """Patch next_row only. Returns the updated config or None if unconfigured."""
    cfg = load()
    if not cfg:
        return None
    return save({"next_row": int(row)})


def clear() -> None:
    p = _config_path()
    if p.exists():
        p.unlink()
        logger.info("sheets_config cleared")
