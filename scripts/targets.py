"""Sheet ids, Drive folder ids and channel names — kept OUT of the repo.

This repository is public. A Google Sheet id or Drive folder id is not a
credential, but it IS the access if that sheet or folder is link-shared, and the
render outputs must be world-readable by link for Buffer to fetch them at publish
time. So none of these live in tracked code.

Values go in `data/targets.json` (`data/` is gitignored, next to
`buffer_config.json` and `drive_oauth_*.json`):

    {
      "fr_sheet_id":               "…",
      "fr_tab":                    "Victoria",
      "fr_drive_folder":           "…",
      "povestitor_drive_folder":   "…",
      "tiktok_channel_ro":         "…",
      "tiktok_channel_fr":         "…",
      "facebook_channel":          "…"
    }

Missing keys fail loudly with the key name, rather than silently pointing a
dispatcher at the wrong sheet.
"""
import json
import os
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_cache: dict | None = None


def _path() -> pathlib.Path:
    data = os.environ.get("CLIPFORGE_DATA_DIR")
    base = pathlib.Path(data) if data else _ROOT / "data"
    if not base.is_absolute():
        base = _ROOT / base
    return base / "targets.json"


def all_targets() -> dict:
    global _cache
    if _cache is None:
        p = _path()
        if not p.exists():
            raise SystemExit(
                f"lipseste {p}\n"
                f"Vezi docstring-ul din scripts/targets.py pentru forma fisierului."
            )
        _cache = json.loads(p.read_text(encoding="utf-8"))
    return _cache


def get(key: str, default=None):
    """Env var CLIPFORGE_<KEY> wins, then data/targets.json, then `default`."""
    env = os.environ.get("CLIPFORGE_" + key.upper())
    if env:
        return env
    val = all_targets().get(key, default)
    if val is None:
        raise SystemExit(f"lipseste cheia '{key}' din {_path()}")
    return val
