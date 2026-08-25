"""Sheet ids, Drive folder ids and channel names — kept OUT of the repo.

This repository is public. A Google Sheet id or Drive folder id is not a
credential, but it IS the access if that sheet or folder is link-shared, and the
render outputs must be world-readable by link for Buffer to fetch them at publish
time. So none of these live in tracked code.

Values go in `data/targets.json` (`data/` is gitignored, next to
`buffer_config.json` and `drive_oauth_*.json`):

    {
      "pov_sheet_id":                "…",   # sheet-ul romanesc (Sheet1)
      "pov_tab":                     "Sheet1",
      "fr_sheet_id":                 "…",
      "fr_tab":                      "Victoria",
      "fr_drive_folder":             "…",   # Victoria (franceza)
      "povestitor_drive_folder":     "…",   # povestitor RO (contine posted/)
      "povestitor_en_drive_folder":  "…",   # povestitor EN
      "narator_drive_folder":        "…",
      "comentator_drive_folder":     "…",
      "tiktok_channel_ro":           "…",
      "tiktok_channel_fr":           "…",
      "facebook_channel":            "…",
      "facebook_channel_fr":         "…"
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
    """Unde stau tintele.

    Sunt comune pe rig — acelasi sheet, aceleasi foldere, aceleasi canale —
    spre deosebire de `data_b/`, care tine starea SEPARATA a celui de-al doilea
    backend (presete, DB, token). Dispecerul legat de placa B mostenea
    `CLIPFORGE_DATA_DIR=data_b` de la watchdog si murea la pornire cautand un
    `data_b/targets.json` care nu exista si nu trebuie sa existe. Deci: daca
    exista in folderul indicat de mediu, se foloseste; altfel `data/` din repo.
    """
    data = os.environ.get("CLIPFORGE_DATA_DIR")
    if data:
        base = pathlib.Path(data)
        if not base.is_absolute():
            base = _ROOT / base
        p = base / "targets.json"
        if p.exists():
            return p
    return _ROOT / "data" / "targets.json"


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
