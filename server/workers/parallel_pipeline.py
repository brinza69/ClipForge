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
import re
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


def _split_plan(duration_s: float) -> List[float]:
    """Part durations (seconds). Rules:
      - a clip up to 1:30 stays ONE part (the max long part is 1:30);
      - otherwise cut 1:00 parts, and the leftover becomes a final SHORT part
        if it is >= 30s; if the leftover is < 30s it is folded into the last
        part (which then runs 1:01–1:29).
    So the short part is never < 30s and no part is ever longer than 1:30.
        1:40→[60,40]  1:41→[60,41]  2:41→[60,60,41]  2:10→[60,70]  2:00→[60,60]
    """
    if duration_s <= 90.0 + 1e-6:
        return [duration_s]
    n = int(duration_s // 60)
    rem = duration_s - 60 * n
    if rem <= 1e-6:
        return [60.0] * n
    if rem >= 30.0:
        return [60.0] * n + [rem]
    # leftover < 30s → fold it into the last full minute (60–89s)
    return [60.0] * (n - 1) + [60.0 + rem]


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_stem(s: str) -> str:
    """Sanitize a filename stem — keep alphanum/._-, collapse other chars to _."""
    s = _SAFE_NAME_RE.sub("_", (s or "").strip())
    return s.strip("._") or "output"


def _split_video(final_path: Path, out_stem: str, part_suffix: str = "_part") -> List[dict]:
    """Split the finished mp4 per _split_plan (re-encoded for exact cuts).
    Returns [] when the clip stays a single part.

    `part_suffix` controls the per-part filename pattern:
      - default "_part" → "<stem>_part1of3.mp4" (existing behaviour)
      - "_p"            → "<stem>_p1.mp4"      (Sheets mode: short, no total)
    """
    from services.speed_match import probe_duration
    try:
        dur = float(probe_duration(str(final_path)))
    except Exception:
        return []
    plan = _split_plan(dur)
    if len(plan) <= 1 or dur <= 0:
        return []
    ffmpeg = _ffmpeg_bin()
    out: List[dict] = []
    start = 0.0
    total = len(plan)
    for k, part_len in enumerate(plan):
        if part_suffix == "_p":
            dst = final_path.parent / f"{out_stem}_p{k + 1}.mp4"
        else:
            dst = final_path.parent / f"{out_stem}_part{k + 1}of{total}.mp4"
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-i", str(final_path), "-t", f"{part_len:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(dst),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_creationflags(), timeout=600)
        if r.returncode == 0 and dst.exists():
            out.append({
                "part": k + 1, "of": total, "path": str(dst), "filename": dst.name,
                "start": round(start, 2), "duration": round(part_len, 2),
            })
        else:
            logger.warning(f"split part {k + 1}/{total} failed: {(r.stderr or '')[-200:]}")
        start += part_len
    logger.info(f"split {final_path.name} into {len(out)}/{total} parts: "
                f"{[round(p) for p in plan]}")
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
    # Allow 1 variant — the /auto endpoint reuses this pipeline for headless
    # single-variant automation. UI-driven /parallel still enforces 2+ via
    # its StartRequest schema.
    if len(variants) < 1:
        raise RuntimeError("Pipeline needs at least 1 variant.")
    if len(variants) > 4:
        raise RuntimeError("Pipeline supports at most 4 variants.")

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

        # Naming: when the job is tied to a Sheets row, every variant's
        # files are named after the row's <number>. Otherwise fall back to
        # "<title>_<variant>" (existing behaviour).
        sheets_number = (cfg.get("sheets_number") or "").strip()
        if sheets_number:
            stem = _safe_stem(sheets_number)
            part_suffix = "_p"          # <num>_p1.mp4
        else:
            base_name = Path(cfg.get("title") or project_id).stem
            stem = f"{base_name}_{(vname or com_id or f'v{i + 1}')}"
            part_suffix = "_part"       # <stem>_part1of3.mp4
        out_name = f"{stem}.mp4"

        # Rename the on-disk final video so Drive uploads + download all
        # carry the proper name (drive_upload uses fp.name as Drive filename).
        desired_final = final_path.parent / out_name
        if final_path.exists() and final_path != desired_final:
            try:
                if desired_final.exists():
                    desired_final.unlink()
                final_path.rename(desired_final)
                final_path = desired_final
            except Exception as e:
                logger.warning(
                    f"could not rename {final_path.name} → {desired_final.name}: {e}"
                )

        import asyncio as _asyncio
        loop_ = _asyncio.get_event_loop()

        # Optional: split the finished video into equal parts for multi-part
        # posting (e.g. a 2:40 clip → two 1:20 parts).
        parts: List[dict] = []
        if variant.get("split_into_parts"):
            await v_slc.update(0.96, f"[{i + 1}/{n}] {label}: splitting into parts…")
            parts = await loop_.run_in_executor(
                None, lambda: _split_video(final_path, stem, part_suffix)
            )

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

    # ── Optional: commit description back to Google Sheets ─────────────────
    # When the job was started with sheets_row, the AI-generated description
    # for variant #0 (descriptions are shared across variants — they're
    # generated once from the cleaned transcript) is written into the row's
    # description column, and next_row is advanced. A failure here is logged
    # but does NOT fail the job — the videos are already on disk and Drive.
    sheets_commit_result = None
    sheets_row = cfg.get("sheets_row")
    if sheets_row:
        ai_desc = (descriptions or {}).get("ai_generated") or ""
        if not ai_desc.strip():
            logger.warning(
                f"sheets commit skipped: AI description is empty for job {job_id} "
                f"(row {sheets_row})"
            )
            sheets_commit_result = {"status": "skipped_empty_description", "row": int(sheets_row)}
        else:
            try:
                from services import sheets as _sheets, sheets_config as _scfg
                _cfg_doc = _scfg.load()
                if not _cfg_doc:
                    raise RuntimeError("Sheets config disappeared between start and commit.")
                _sheets.write_cell(
                    _cfg_doc["spreadsheet_id"], _cfg_doc["tab"],
                    _cfg_doc["col_description"], int(sheets_row), ai_desc,
                )
                _scfg.update_next_row(int(sheets_row) + 1)
                sheets_commit_result = {
                    "status": "written",
                    "row": int(sheets_row),
                    "next_row": int(sheets_row) + 1,
                }
                logger.info(f"sheets commit OK: row {sheets_row} → next_row {sheets_row + 1}")
            except Exception as e:
                logger.exception(f"sheets commit failed for row {sheets_row}")
                sheets_commit_result = {
                    "status": "failed",
                    "row": int(sheets_row),
                    "reason": str(e)[-300:],
                }

    # Persist outputs on the job so the router can list/serve each variant.
    cfg.update({
        "video_path": str(video_path),
        "erased_path": str(erased_path),
        "cleaned_text": cleaned_text,
        "transcript_text": raw_transcript_text,
        "descriptions": descriptions,
        "results": results,
        "sheets_commit": sheets_commit_result,
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
