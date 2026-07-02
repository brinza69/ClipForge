"""
ClipForge — Auto Router

Headless automation entry point. The full ClipForge pipeline driven by a
single POST, with no UI involvement. Designed for:
  - Google Sheets workflows (pull URL + run + write description back)
  - cron-style queue feeders
  - external automation (Zapier / n8n / Make / Apps Script)

Two usage modes:

1) Explicit URL — pass `url` and `variant_preset_ids` (1–4 saved preset IDs).
   Uses sensible default erase/caption zones derived from the source's
   reported width/height; auto-detect is on by default and refines them.

2) From Sheets — pass `from_sheets: true` (no `url` needed). The endpoint
   reads the next row's URL + number, attaches them to the job so the
   pipeline writes the AI description back when done.

  POST /api/auto
    body:
      url: str | null         # required when from_sheets=false
      variant_preset_ids: [str, ...]      # 1–4 ids from data/variant_presets/
      from_sheets: bool = false
      auto_detect_zones: bool = true
      erase_method: "lama" | "ns" | "blur" = "lama"
      transcript_engine: str = "ollama"
      transcript_target_lang: str | null = null
      erase_zone: { x, y, w, h, src_w, src_h } | null       # optional override
      caption_zone: { x, y, w, h, src_w, src_h } | null     # optional override

    returns:
      { job_id, project_id, url, variants: [name,...],
        sheets_row: int | null, sheets_number: str | null }

Failures map to HTTP codes:
  400 — bad input (missing url, bad preset id format, …)
  404 — preset_id not found
  409 — from_sheets but sheets not configured / row empty
  422 — yt-dlp metadata failed
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import async_session
from models import JobModel, JobStatus, JobType, ProjectModel, ProjectStatus
from services import variant_presets, sheets, sheets_config
from services.downloader import detect_source_type, fetch_metadata, validate_url

logger = logging.getLogger("clipforge.routers.auto")
router = APIRouter(prefix="/api/auto", tags=["auto"])


# Subset of VariantConfig fields we want to copy from a saved preset into
# the per-variant job spec. Kept in sync with routers/parallel.py and
# services/variant_presets._FIELDS.
_PRESET_VARIANT_FIELDS = (
    "name",
    "tts_engine", "tts_voice_id", "tts_language", "tts_speed",
    "tts_stability", "tts_similarity",
    "caption_template_id", "caption_font_family", "caption_scale",
    "caption_text_color", "caption_uppercase", "caption_italic",
    "caption_words_per_chunk", "caption_strip_punct",
    "commentator_preset_id",
    "drive_folder",
    "split_into_parts",
    "voice_target_sec",
    "match_to_source_duration",
)


class Zone(BaseModel):
    x: int
    y: int
    w: int
    h: int
    src_w: int
    src_h: int


class AutoRequest(BaseModel):
    url: Optional[str] = None
    number: Optional[str] = None         # names the output <number>.mp4 (explicit-url flows)
    variant_preset_ids: List[str] = Field(min_length=1, max_length=4)
    from_sheets: bool = False
    auto_detect_zones: bool = True
    erase_method: str = "lama"          # "lama" | "ns" | "blur"
    erase_coverage: str = "tight"       # tight | band | thorough (T20)
    transcript_engine: str = "ollama"
    transcript_target_lang: Optional[str] = None
    erase_zone: Optional[Zone] = None
    caption_zone: Optional[Zone] = None


def _default_zones(width: int, height: int) -> tuple[dict, dict]:
    """Sensible defaults for a vertical TikTok-style clip — top 12% / bottom 14%.
    These get refined by auto-detect inside the pipeline (it scans the ROI
    OCR-tight + per-frame edge density). Works fine for any aspect: the
    relative bands stay reasonable for 9:16, 1:1, and 16:9 alike."""
    w = max(1, int(width or 1080))
    h = max(1, int(height or 1920))
    erase = {
        "x": round(w * 0.05), "y": round(h * 0.08),
        "w": round(w * 0.90), "h": round(h * 0.12),
        "src_w": w, "src_h": h,
    }
    caption = {
        # y lifted from 0.78 → 0.73 so auto captions sit a little higher
        # (clear of the very bottom + any corner avatar).
        "x": round(w * 0.05), "y": round(h * 0.73),
        "w": round(w * 0.90), "h": round(h * 0.14),
        "src_w": w, "src_h": h,
    }
    return erase, caption


def _preset_to_variant(preset: dict) -> dict:
    """Project a stored preset dict onto the keys parallel_pipeline expects
    in each variants[i]. Drops fields the pipeline doesn't read."""
    out = {k: preset.get(k) for k in _PRESET_VARIANT_FIELDS}
    # `name` falls back to the preset's display name if the saved variant
    # didn't carry one — Drive folder labels look better that way.
    if not out.get("name"):
        out["name"] = preset.get("name") or preset.get("id")
    return out


@router.post("")
async def auto_run(req: AutoRequest):
    # ── 1. Resolve source URL (explicit OR pulled from Sheets) ───────────
    sheets_row: Optional[int] = None
    sheets_number: Optional[str] = None
    if req.from_sheets:
        cfg = sheets_config.load()
        if not cfg:
            raise HTTPException(409, "Sheets not configured. Configure once via /parallel-sheets or POST /api/sheets/config.")
        row = int(cfg.get("next_row") or cfg.get("start_row") or 1)
        try:
            url_val, num_val = sheets.read_pair(
                cfg["spreadsheet_id"], cfg["tab"],
                cfg["col_url"], cfg["col_number"], row,
            )
        except sheets.SheetsScopeMissing as e:
            raise HTTPException(401, str(e))
        except sheets.SheetsError as e:
            raise HTTPException(400, str(e))
        if not url_val:
            raise HTTPException(409, f"Row {row} has no URL in column {cfg['col_url']}.")
        url = url_val
        sheets_row = row
        sheets_number = (num_val or "").strip() or None
    else:
        if not req.url or not req.url.strip():
            raise HTTPException(400, "url is required when from_sheets=false")
        url = req.url.strip()
        sheets_number = (req.number or "").strip() or None

    # ── 2. Resolve presets → list of full variant dicts ──────────────────
    presets: List[dict] = []
    for pid in req.variant_preset_ids:
        p = variant_presets.load_preset(pid)
        if not p:
            raise HTTPException(404, f"Variant preset not found: '{pid}'")
        # Pre-flight: a preset with no voice_id can't run.
        if not (p.get("tts_voice_id") or "").strip():
            raise HTTPException(400, f"Preset '{pid}' has no tts_voice_id set — open it in /parallel and save.")
        presets.append(p)
    if not presets:
        raise HTTPException(400, "variant_preset_ids must reference at least one preset")

    # ── 3. Fetch yt-dlp metadata (gives us real dims for default zones) ──
    check = await validate_url(url)
    if not check.get("valid"):
        raise HTTPException(400, check.get("error", "Invalid URL"))
    metdat = await fetch_metadata(url, None)
    if "error" in metdat:
        raise HTTPException(422, metdat["error"])

    src_w = int(metdat.get("width") or 0) or 1080
    src_h = int(metdat.get("height") or 0) or 1920

    default_erase, default_caption = _default_zones(src_w, src_h)
    erase_zone = req.erase_zone.model_dump() if req.erase_zone else default_erase
    caption_zone = req.caption_zone.model_dump() if req.caption_zone else default_caption

    # ── 4. Create the project + job rows ─────────────────────────────────
    async with async_session() as session:
        project = ProjectModel(
            title=metdat.get("title") or "Auto",
            source_url=url,
            source_type=detect_source_type(url),
            status=ProjectStatus.metadata_ready.value,
        )
        for f in ("duration", "width", "height", "fps", "thumbnail_url"):
            if metdat.get(f) is not None:
                setattr(project, f, metdat[f])
        session.add(project)
        await session.commit()
        await session.refresh(project)
        project_id = project.id

    job_id = uuid.uuid4().hex[:12]
    job_meta = {
        "url": url,
        "title": metdat.get("title"),
        "erase_zone": erase_zone,
        "caption_zone": caption_zone,
        "erase_mode": "blur" if req.erase_method == "blur" else "inpaint",
        "erase_algorithm": "ns" if req.erase_method == "ns" else "telea",
        "erase_auto_detect": bool(req.auto_detect_zones),
        "erase_coverage": req.erase_coverage,
        "transcript_engine": req.transcript_engine,
        "transcript_target_lang": req.transcript_target_lang,
        "variants": [_preset_to_variant(p) for p in presets],
        "sheets_row": sheets_row,
        "sheets_number": sheets_number,
    }

    async with async_session() as session:
        row = JobModel(
            id=job_id,
            project_id=project_id,
            type=JobType.parallel_pipeline.value,
            status=JobStatus.queued.value,
            metadata_json=json.dumps(job_meta),
        )
        session.add(row)
        await session.commit()

    logger.info(
        f"auto_run {job_id} enqueued (project={project_id}, {len(presets)} variant(s), "
        f"from_sheets={req.from_sheets}, sheets_row={sheets_row})"
    )
    return {
        "job_id": job_id,
        "project_id": project_id,
        "url": url,
        "variants": [p.get("name") or p.get("id") for p in presets],
        "sheets_row": sheets_row,
        "sheets_number": sheets_number,
        "src_dims": {"w": src_w, "h": src_h},
        "zones": {"erase": erase_zone, "caption": caption_zone},
    }
