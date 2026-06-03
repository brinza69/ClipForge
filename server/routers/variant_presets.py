"""
ClipForge — Variant Presets Router

CRUD for reusable Parallel-Processing variant presets (voice + captions +
commentator bundles).

  GET    /api/variant-presets          — list all presets
  POST   /api/variant-presets          — create / overwrite a preset
  DELETE /api/variant-presets/{id}     — delete a preset
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import variant_presets

logger = logging.getLogger("clipforge.routers.variant_presets")
router = APIRouter(prefix="/api/variant-presets", tags=["variant-presets"])


class PresetBody(BaseModel):
    name: str
    preset_id: Optional[str] = None

    tts_engine: str = "xtts"
    tts_voice_id: str = ""
    tts_language: str = "en"
    tts_speed: float = 1.0

    caption_template_id: str = "bold_impact"
    caption_font_family: Optional[str] = None
    caption_scale: float = 1.0
    caption_text_color: Optional[str] = None
    caption_uppercase: Optional[bool] = None
    caption_italic: Optional[bool] = None
    caption_words_per_chunk: int = 1
    caption_strip_punct: bool = True

    commentator_preset_id: Optional[str] = None
    drive_folder: Optional[str] = None


@router.get("")
async def list_all():
    return {"presets": variant_presets.list_presets()}


@router.post("")
async def create(body: PresetBody):
    try:
        rec = variant_presets.save_preset(
            name=body.name,
            fields=body.model_dump(exclude={"name", "preset_id"}),
            preset_id=body.preset_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return rec


@router.delete("/{preset_id}")
async def delete(preset_id: str):
    try:
        variant_presets.delete_preset(preset_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Preset not found: {preset_id}")
    return {"ok": True}
