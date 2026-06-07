"""
ClipForge — Parallel Processing Pipeline Worker

Produce N output videos from a SINGLE source link, sharing the expensive
work and forking only the cheap, per-variant parts.

Shared once for all variants:
    download ──► transcribe ──► erase ──► clean transcript

Per variant (2–4), each with its own voice / captions / commentator:
    cleaned text ──► TTS ──► speed-match ──► caption burn ──► commentator ──► final_i

The erase zone, the caption zone and the cleaned transcript text are the
same across every variant — only the voice, the caption template/style and
the commentator preset change.

Progress budget (0..1):
    download      0.00 – 0.05
    transcribe    0.05 – 0.10
    erase         0.10 – 0.40   (shared, slowest)
    clean         0.40 – 0.48   (shared)
    variants      0.48 – 0.97   (split equally across N)
    descriptions  0.97 – 1.00   (shared, once)
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from database import async_session
from models import JobModel, JobType

# Reuse every stage from the remix pipeline — same building blocks, different
# orchestration.
from workers.remix_pipeline import (
    _Sliced,
    _creationflags,
    _ffmpeg_bin,
    _stage_commentator,
    _stage_descriptions,
    _stage_download,
    _stage_erase,
    _stage_match_and_caption,
    _stage_transcribe,
    synth_voice_from_text,
)

logger = logging.getLogger("clipforge.parallel_pipeline")


def _num_parts(duration_s: float) -> int:
    """How many equal parts to split into. Whole minutes = X; if the leftover
    beyond X minutes is more than 40s, add one more part (so no part runs much
    past ~1:20). Min 1.
        2:00→2  1:40→1  2:40→2  2:41→3  0:45→1
    """
    x = int(duration_s // 60)
    extra = duration_s - x * 60
    parts = x + 1 if extra > 40.0 + 1e-6 else x
    return max(1, parts)


def _split_video(final_path: Path, out_stem: str) -> List[dict]:
    """Split the finished mp4 into N equal parts (re-encoded for exact cuts).
    Returns [] when the clip is short enough to stay a single part."""
    from services.speed_match import probe_duration
    try:
        dur = float(probe_duration(str(final_path)))
    except Exception:
        return []
    parts = _num_parts(dur)
    if parts <= 1 or dur <= 0:
        return []
    part_len = dur / parts
    ffmpeg = _ffmpeg_bin()
    out: List[dict] = []
    for k in range(parts):
        start = k * part_len
        dst = final_path.parent / f"{out_stem}_part{k + 1}of{parts}.mp4"
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-i", str(final_path), "-t", f"{part_len:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(dst),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_creationflags())
        if r.returncode != 0 or not dst.exists():
            logger.warning(f"split part {k + 1}/{parts} failed: "
                           f"{(r.stderr or '')[-200:]}")
            continue
        out.append({
            "part": k + 1, "of": parts, "path": str(dst), "filename": dst.name,
            "start": round(start, 2), "duration": round(part_len, 2),
        })
    logger.info(f"split {final_path.name} into {len(out)}/{parts} parts ({part_len:.1f}s each)")
    return out


# Fields that belong to a single variant. Everything else in the request is
# shared. A per-variant cfg is the shared cfg with these keys overlaid, so the
# reused remix stage functions (which read flat cfg["tts_engine"] etc.) work
# unchanged.
_VARIANT_KEYS = (
    "name",
    "tts_engine", "tts_voice_id", "tts_language", "tts_speed",
    "caption_template_id", "caption_font_family", "caption_scale",
    "caption_text_color", "caption_outline_color", "caption_outline_width",
    "caption_uppercase", "caption_italic", "caption_words_per_chunk",
    "caption_strip_punct",
    "commentator_preset_id", "commentator_chroma_color",
    "commentator_chroma_similarity", "commentator_chroma_blend",
    "drive_folder", "split_into_parts",
)


def _variant_cfg(shared: Dict, variant: Dict) -> Dict:
    """Build a flat cfg for one variant: shared base + variant overrides."""
    cfg = dict(shared)
    for k in _VARIANT_KEYS:
        if k in variant:
            cfg[k] = variant[k]
    return cfg


async def handle_parallel_pipeline(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict,
    queue,
):
    """Orchestrate the shared-then-fork parallel pipeline. Metadata schema:

        {
            "url": "...",
            "title": "...",
            "erase_zone": {...},        # shared
            "caption_zone": {...},      # shared
            "erase_mode": "inpaint",    # shared
            "erase_algorithm": "telea",
            "erase_auto_detect": false,
            "transcript_engine": "ollama",
            "transcript_target_lang": "ro",
            "variants": [               # 2–4 entries
                {
                    "name": "Grinch",
                    "tts_engine": "elevenlabs", "tts_voice_id": "...",
                    "tts_language": "ro", "tts_speed": 1.0,
                    "caption_template_id": "bold_impact", ...,
                    "commentator_preset_id": "povestitor_cel_verde", ...
                },
                ...
            ]
        }
    """
    cfg = dict(metadata)
    variants: List[Dict] = list(cfg.get("variants") or [])
    if len(variants) < 2:
        raise RuntimeError("Parallel processing needs at least 2 variants.")
    if len(variants) > 4:
        raise RuntimeError("Parallel processing supports at most 4 variants.")

    project_dir = Path(settings.media_dir) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    # ── Shared stage 1 — download (0.00–0.05) ──────────────────────────────
    video_path = await _stage_download(
        cfg, _Sliced(queue, job_id, 0.00, 0.05), queue, job_id, project_id
    )

    # ── Shared stage 2 — transcribe (0.05–0.10) ────────────────────────────
    tx_result = await _stage_transcribe(video_path, _Sliced(queue, job_id, 0.05, 0.10))
    raw_transcript_text = tx_result.get("full_text") or ""
    if not raw_transcript_text.strip():
        raise RuntimeError("Transcription produced no text — cannot continue.")

    from services.caption_overlays import probe_video_dims
    src_w, src_h = probe_video_dims(str(video_path))

    # ── Shared stage 3 — erase (0.10–0.40) ─────────────────────────────────
    erased_path = project_dir / "video_erased.mp4"
    await _stage_erase(
        video_path, erased_path, cfg["erase_zone"], src_w, src_h,
        _Sliced(queue, job_id, 0.10, 0.40), job_id, queue,
        mode=cfg.get("erase_mode", "inpaint"),
        algorithm=cfg.get("erase_algorithm", "telea"),
        auto_detect=bool(cfg.get("erase_auto_detect", False)),
    )

    # ── Shared stage 4 — clean transcript ONCE (0.40–0.48) ─────────────────
    from services.transcript_cleaner import clean_transcript

    slc_clean = _Sliced(queue, job_id, 0.40, 0.48)
    await slc_clean.update(0.0, "Cleaning transcript (shared)…")
    cleaned_text = await clean_transcript(
        raw_transcript_text,
        engine=cfg["transcript_engine"],
        target_language=cfg.get("transcript_target_lang") or None,
        progress_cb=lambda i, total: None,
    )
    if not cleaned_text.strip():
        raise RuntimeError("Transcript cleaning produced empty text.")
    await slc_clean.update(1.0, "Transcript ready (shared)")

    # ── Per-variant fork (0.48–0.97) ───────────────────────────────────────
    n = len(variants)
    var_lo, var_hi = 0.48, 0.97
    var_span = (var_hi - var_lo) / n
    results: List[Dict[str, Any]] = []

    for i, variant in enumerate(variants):
        vcfg = _variant_cfg(cfg, variant)
        vdir = project_dir / f"v{i}"
        vdir.mkdir(parents=True, exist_ok=True)
        v_base = var_lo + i * var_span
        v_slc = _Sliced(queue, job_id, v_base, v_base + var_span)

        vname = (variant.get("name") or "").strip()
        com_id = variant.get("commentator_preset_id") or None
        label = vname or com_id or f"variant {i + 1}"

        await v_slc.update(0.0, f"[{i + 1}/{n}] {label}: voice…")

        # 1) voice (0–40% of this variant's slice)
        voice_path = await synth_voice_from_text(
            cleaned_text, vdir, vcfg, v_slc.sub(0.0, 0.40), out_stem="voice",
        )

        # 2+3) FUSED speed-match + caption burn (one encode) on the shared
        # erased video against this variant's voice.
        await v_slc.update(0.40, f"[{i + 1}/{n}] {label}: speed-match + captions…")
        has_com = bool(com_id)
        cap_hi = 0.80 if has_com else 1.0
        captioned_path = vdir / ("video_captioned.mp4" if has_com else "video_final.mp4")
        sm_stats = await _stage_match_and_caption(
            erased_path, voice_path, cleaned_text, vcfg, captioned_path,
            v_slc.sub(0.40, cap_hi),
        )

        # 4) commentator (optional)
        final_path = vdir / "video_final.mp4"
        commentator_stats = None
        if has_com:
            commentator_stats = await _stage_commentator(
                captioned_path, final_path, vcfg, v_slc.sub(0.80, 1.0),
            )
        else:
            final_path = captioned_path

        base_name = Path(cfg.get("title") or project_id).stem
        stem = f"{base_name}_{(vname or com_id or f'v{i + 1}')}"
        out_name = f"{stem}.mp4"

        import asyncio as _asyncio
        loop_ = _asyncio.get_event_loop()

        # Optional: split the finished video into equal parts for multi-part
        # posting (e.g. a 2:40 clip → two 1:20 parts).
        parts: List[dict] = []
        if variant.get("split_into_parts"):
            await v_slc.update(0.96, f"[{i + 1}/{n}] {label}: splitting into parts…")
            parts = await loop_.run_in_executor(None, lambda: _split_video(final_path, stem))

        # Optional: upload to the variant's Drive folder. When split, upload the
        # PARTS; otherwise the whole video. Download stays available either way.
        drive_result = None
        drive_folder = (variant.get("drive_folder") or "").strip()
        if drive_folder:
            await v_slc.update(0.98, f"[{i + 1}/{n}] {label}: uploading to Drive…")
            from services.drive_upload import upload_files
            targets = [Path(p["path"]) for p in parts] if parts else [final_path]
            drive_result = await loop_.run_in_executor(
                None, lambda: upload_files(drive_folder, targets)
            )
            logger.info(f"variant {i} Drive upload: {drive_result.get('status')}")

        results.append({
            "index": i,
            "name": vname,
            "label": label,
            "commentator_preset_id": com_id,
            "tts_engine": vcfg.get("tts_engine"),
            "tts_voice_id": vcfg.get("tts_voice_id"),
            "caption_template_id": vcfg.get("caption_template_id"),
            "final_path": str(final_path),
            "output_filename": out_name,
            "parts": parts,
            "speed_match_stats": sm_stats,
            "commentator_stats": commentator_stats,
            "drive": drive_result,
        })
        await v_slc.update(1.0, f"[{i + 1}/{n}] {label}: done")

    # ── Shared stage 5 — descriptions ONCE (0.97–1.00) ─────────────────────
    descriptions = await _stage_descriptions(
        cfg.get("source_description", ""),
        cleaned_text or raw_transcript_text,
        cfg,
        _Sliced(queue, job_id, 0.97, 1.00),
    )

    # Persist outputs on the job so the router can list/serve each variant.
    cfg.update({
        "video_path": str(video_path),
        "erased_path": str(erased_path),
        "cleaned_text": cleaned_text,
        "transcript_text": raw_transcript_text,
        "descriptions": descriptions,
        "results": results,
    })
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        if job:
            job.metadata_json = json.dumps(cfg)
            await session.commit()

    logger.info(f"parallel_pipeline {job_id}: done → {n} variants")
    await queue.update_progress(job_id, 1.0, f"Parallel complete — {n} videos")


def register_parallel_handlers(queue):
    queue.register_handler(JobType.parallel_pipeline.value, handle_parallel_pipeline)
    logger.info("Parallel pipeline handler registered")
