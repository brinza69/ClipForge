"""
ElevenLabs API wrapper — alternative TTS engine.

Why this exists: XTTS-v2 doesn't support Romanian, but ElevenLabs's
`eleven_multilingual_v2` model does (along with ~29 other languages,
with proper accent and prosody). When the user needs Romanian (or just
better quality across the board), they can drop in an API key and switch.

API key resolution order:
  1. ELEVENLABS_API_KEY env var
  2. data/tts_config.json -> elevenlabs_api_key

No key stored in the frontend, no key in source control.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, Optional

import httpx

logger = logging.getLogger("clipforge.elevenlabs")

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "eleven_multilingual_v2"  # supports Romanian


def _config_path() -> Path:
    from config import settings
    return Path(settings.data_dir) / "tts_config.json"


def _read_config() -> dict:
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Could not parse tts_config.json")
        return {}


def _write_config(cfg: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get_api_key() -> Optional[str]:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key and key.strip():
        return key.strip()
    cfg = _read_config()
    val = cfg.get("elevenlabs_api_key")
    if val and isinstance(val, str) and val.strip():
        return val.strip()
    return None


def set_api_key(key: str) -> None:
    cfg = _read_config()
    if key and key.strip():
        cfg["elevenlabs_api_key"] = key.strip()
    else:
        cfg.pop("elevenlabs_api_key", None)
    _write_config(cfg)


def is_configured() -> bool:
    return get_api_key() is not None


async def list_voices() -> List[dict]:
    """Fetch the user's available ElevenLabs voices (own + curated library)."""
    key = get_api_key()
    if not key:
        raise RuntimeError("ElevenLabs API key not configured")

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{ELEVENLABS_API_BASE}/voices",
            headers={"xi-api-key": key, "accept": "application/json"},
        )
        if r.status_code == 401:
            raise RuntimeError("ElevenLabs API key invalid or expired")
        r.raise_for_status()
        data = r.json()

    out: List[dict] = []
    for v in data.get("voices", []):
        labels = v.get("labels") or {}
        out.append({
            "id": v.get("voice_id"),
            "name": v.get("name") or "Unnamed",
            "category": v.get("category"),
            "preview_url": v.get("preview_url"),
            "description": v.get("description"),
            "gender": labels.get("gender"),
            "age": labels.get("age"),
            "accent": labels.get("accent"),
            "use_case": labels.get("use case") or labels.get("use_case"),
        })
    return out


async def get_user_info() -> dict:
    """Fetch the subscription / character-usage info — useful for UI display."""
    key = get_api_key()
    if not key:
        raise RuntimeError("ElevenLabs API key not configured")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{ELEVENLABS_API_BASE}/user",
            headers={"xi-api-key": key, "accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    sub = data.get("subscription") or {}
    return {
        "tier": sub.get("tier"),
        "character_count": sub.get("character_count"),
        "character_limit": sub.get("character_limit"),
        "next_invoice": sub.get("next_invoice_at_unix"),
    }


async def synthesize(
    text: str,
    voice_id: str,
    output_path: str,
    *,
    model_id: str = DEFAULT_MODEL,
    stability: float = 0.5,
    similarity_boost: float = 0.75,
    style: float = 0.0,
    speaker_boost: bool = True,
) -> str:
    """
    POST to /v1/text-to-speech/{voice_id} and write the returned MP3 to disk.

    Returns the output path. Raises RuntimeError on API failure with the
    body of the error (which usually contains useful hints — e.g. quota
    exceeded, invalid voice, etc).
    """
    key = get_api_key()
    if not key:
        raise RuntimeError("ElevenLabs API key not configured")

    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": max(0.0, min(1.0, float(stability))),
            "similarity_boost": max(0.0, min(1.0, float(similarity_boost))),
            "style": max(0.0, min(1.0, float(style))),
            "use_speaker_boost": bool(speaker_boost),
        },
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(
            f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": key,
                "accept": "audio/mpeg",
                "content-type": "application/json",
            },
            json=payload,
        )
        if r.status_code != 200:
            # Try to extract a useful error message
            try:
                err = r.json()
                detail = err.get("detail") or err
            except Exception:
                detail = r.text
            msg = str(detail)[:500]
            raise RuntimeError(f"ElevenLabs API error {r.status_code}: {msg}")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        logger.info(f"ElevenLabs synth OK: {out.stat().st_size // 1024}KB → {out.name}")
        return str(out)


# Sensible language list for the UI — ElevenLabs multilingual_v2 supports
# all of these natively. (XTTS-v2 only covers 17, no Romanian.)
SUPPORTED_LANGUAGES = [
    "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl", "cs",
    "ar", "zh", "ja", "hu", "ko", "hi", "ro", "sv", "da", "fi", "id",
    "fil", "ms", "el", "bg", "uk", "hr", "sk", "ta", "vi",
]
