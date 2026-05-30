"""
ClipForge — Remix Pipeline Worker

End-to-end "remix a video with a different voice + captions" flow:

    download ──► transcribe ──► (caption-erase ∥ clean-transcript ──► TTS ──► desilence)
                                                      │
                                            speed-match video to voice
                                                      │
                                              caption burn-in
                                                      │
                                                  final mp4

The two parallel branches are run with asyncio.gather. The erase branch is
GPU+CPU heavy; the audio branch is mostly I/O + a TTS call. They share the
asyncio loop fine because the heavy work lives in run_in_executor.

Progress is divided across stages:
    download         0.00 – 0.10
    transcribe       0.10 – 0.20
    parallel start   0.20
        erase        0.20 – 0.65   (slowest stage)
        clean+TTS    0.20 – 0.50   (runs concurrently)
        desilence    0.50 – 0.55
    speed-match      0.65 – 0.75
    caption-burn     0.75 – 1.00
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from database import async_session
from models import JobModel, JobType, ProjectModel, ProjectStatus

logger = logging.getLogger("clipforge.remix_pipeline")


# ── ffmpeg helpers (mirroring services/inpaint.py) ──────────────────────────


def _ffmpeg_bin() -> str:
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def _probe_audio_dur(path: str) -> float:
    """ffprobe wrapper used by the loudnorm/fade pass to compute fade-out start."""
    try:
        from services.speed_match import probe_duration
        return float(probe_duration(path))
    except Exception:
        return 0.0


# ── Progress slicing ────────────────────────────────────────────────────────


class _Sliced:
    """Wraps the queue so a handler's 0..1 progress maps to a slice of the parent."""
    def __init__(self, queue, job_id: str, lo: float, hi: float):
        self._q = queue
        self._jid = job_id
        self._lo = lo
        self._hi = hi

    async def update(self, p: float, msg: str) -> None:
        p = max(0.0, min(1.0, p))
        mapped = self._lo + p * (self._hi - self._lo)
        await self._q.update_progress(self._jid, mapped, msg)


# ── Caption distribution from cleaned text ──────────────────────────────────


# Punctuation we strip when caption_strip_punct is True. Keeps hyphens and
# apostrophes in compound words ("three-quarters", "don't") so the text stays
# readable; everything that adds visual noise without phonetic value goes.
_CAPTION_PUNCT_RE = re.compile(r"[.,!?;:…\"“”„‚‘’()\[\]{}<>«»]+")


def _strip_caption_punct(text: str) -> str:
    return _CAPTION_PUNCT_RE.sub("", text or "").strip()


def _split_into_caption_chunks(text: str, words_per_chunk: int = 4) -> List[str]:
    """
    Split a paragraph of cleaned prose into short caption-sized chunks.
    Respects sentence boundaries when possible — never breaks a chunk in
    the middle of a clause more aggressively than necessary.
    """
    if not text:
        return []
    # Normalize whitespace.
    flat = re.sub(r"\s+", " ", text).strip()
    words = flat.split(" ")
    chunks: List[str] = []
    buf: List[str] = []
    for w in words:
        buf.append(w)
        # Hard cut at words_per_chunk OR softly at any sentence-ending punctuation.
        ends_sentence = bool(re.search(r"[.!?…][\"')\]]?$", w))
        if len(buf) >= words_per_chunk or (ends_sentence and len(buf) >= max(2, words_per_chunk - 1)):
            chunks.append(" ".join(buf))
            buf = []
    if buf:
        chunks.append(" ".join(buf))
    return chunks


def _evenly_timed_overlays(
    chunks: List[str],
    total_duration_s: float,
    template_id: str,
    x_pct: float,
    y_pct: float,
    scale: float = 1.0,
) -> List[Dict]:
    """Distribute caption chunks evenly across `total_duration_s`."""
    if not chunks or total_duration_s <= 0:
        return []
    per = total_duration_s / len(chunks)
    overlays: List[Dict] = []
    for i, c in enumerate(chunks):
        start = i * per
        end = min(total_duration_s, (i + 1) * per)
        overlays.append({
            "text": c,
            "template_id": template_id,
            "start_t": round(start, 3),
            "end_t": round(end, 3),
            "x_pct": x_pct,
            "y_pct": y_pct,
            "scale": scale,
        })
    return overlays


# ── Sub-stage runners (each owns a progress slice) ──────────────────────────


async def _stage_download(meta: Dict, slc: _Sliced, queue, job_id: str, project_id: str) -> Path:
    """Run the existing downloader and return the video path on disk."""
    from services.downloader import download_video, fetch_metadata

    await slc.update(0.0, "Fetching metadata…")

    # The project row already exists (router creates it). Make sure metadata
    # is populated (fetch_metadata is idempotent if it ran during enqueue).
    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
        if project and not project.duration:
            metdat = await fetch_metadata(meta["url"], project_id)
            for f in ("title", "duration", "width", "height", "fps", "thumbnail_url", "description"):
                if metdat.get(f):
                    setattr(project, f, metdat[f])
            project.status = ProjectStatus.metadata_ready.value
            await session.commit()
        # Carry the source description forward — the descriptions stage needs it.
        meta["source_description"] = (project.description if project else "") or ""
        meta["source_title"] = (project.title if project else "") or meta.get("title", "")

    await slc.update(0.05, "Downloading video…")

    loop = asyncio.get_event_loop()

    async def on_progress(p: float, m: str):
        await slc.update(0.05 + 0.92 * p, m or "Downloading…")

    info = await download_video(
        meta["url"], project_id, on_progress=on_progress,
        is_cancelled=(lambda: queue.is_cancelled(job_id)),
    )
    video_path = info.get("video_path") or info.get("path")
    if not video_path or not Path(video_path).exists():
        raise RuntimeError(f"Downloader did not produce a video file: {info}")
    await slc.update(1.0, "Download complete")
    return Path(video_path)


async def _stage_transcribe(video_path: Path, slc: _Sliced) -> Dict[str, Any]:
    from services.transcriber import transcribe
    from workers.pipeline import _has_audio_stream

    # Pre-flight: refuse to call faster-whisper on a file with no audio stream.
    # Without this, PyAV's streams.audio[0] raises IndexError deep in the worker
    # and the user only sees "tuple index out of range" on chunk 1/1.
    if not _has_audio_stream(str(video_path)):
        raise RuntimeError(
            "Source video has no audio track — nothing to transcribe. "
            "TikTok HEVC variants sometimes deliver video-only files; "
            "try the URL again or pick a different source."
        )

    await slc.update(0.0, "Transcribing audio…")

    async def on_progress(p: float, m: str):
        await slc.update(p, m or "Transcribing…")

    result = await transcribe(str(video_path), on_progress=on_progress)
    await slc.update(1.0, "Transcript ready")
    return result


async def _stage_erase(
    video_path: Path,
    output_path: Path,
    erase_zone: Dict,
    src_w: int,
    src_h: int,
    slc: _Sliced,
    job_id: str,
    queue,
    mode: str = "inpaint",
    algorithm: str = "telea",
    auto_detect: bool = False,
) -> Path:
    """Run the eraser on a single rect or auto-detected time-varying segments.

    Modes:
      - inpaint (default): LaMa GPU neural inpainting, OpenCV (telea/ns) fallback
      - blur:              ffmpeg avgblur — fastest, less invasive look

    auto_detect (inpaint mode only): OCR the video, only inpaint frames where
    captions actually appear, and use tight per-segment bboxes. Skips the
    user-drawn rect.
    """
    # If the rect was selected on a thumbnail at thumb_w × thumb_h, scale.
    zw = int(erase_zone.get("src_w") or src_w)
    zh = int(erase_zone.get("src_h") or src_h)
    sx = src_w / zw if zw > 0 else 1.0
    sy = src_h / zh if zh > 0 else 1.0
    x = int(round(int(erase_zone["x"]) * sx))
    y = int(round(int(erase_zone["y"]) * sy))
    w = int(round(int(erase_zone["w"]) * sx))
    h = int(round(int(erase_zone["h"]) * sy))

    loop = asyncio.get_event_loop()

    if mode == "blur":
        await slc.update(0.0, "Blurring region…")
        # Mirror the avgblur approach used by handle_erase_project. The boxblur
        # filter is applied only to the rect via crop/overlay so motion outside
        # the region passes through untouched.
        ffmpeg = _ffmpeg_bin()
        # Sigma scales with rect height for a uniform look across sizes.
        blur_sigma = max(8, min(50, h // 6))
        vf = (
            f"[0:v]crop={w}:{h}:{x}:{y},avgblur={blur_sigma}[blur];"
            f"[0:v][blur]overlay={x}:{y}[out]"
        )
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", str(video_path),
            "-filter_complex", vf,
            "-map", "[out]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        def _run_blur() -> int:
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_creationflags())
            if r.returncode != 0:
                tail = "\n".join((r.stderr or "").strip().splitlines()[-8:])
                raise RuntimeError(f"blur ffmpeg failed: {tail}")
            return output_path.stat().st_size
        size = await loop.run_in_executor(None, _run_blur)
        await slc.update(1.0, f"Blur done ({size // 1024} KB)")
        return output_path

    # mode == "inpaint"
    if algorithm not in ("telea", "ns"):
        algorithm = "telea"
    from services.inpaint import inpaint_region

    detected_segments = None
    if auto_detect:
        # Auto-detect: OCR-scan the video to find time-varying caption boxes.
        # Solves both "OCR zone is too large" (per-segment tight bboxes with
        # drift split) and "fragments not erased" (higher sample rate +
        # lower confidence threshold) compared to the old defaults.
        from services.caption_detector import detect_caption_segments

        await slc.update(0.0, "Scanning for captions (OCR)…")

        def _det_progress(p: float, msg: str):
            mapped = max(0.0, min(0.4, p * 0.4))  # OCR uses 0-40% of erase slice
            asyncio.run_coroutine_threadsafe(slc.update(mapped, msg), loop)

        detected_segments = await loop.run_in_executor(
            None,
            lambda: detect_caption_segments(str(video_path), on_progress=_det_progress),
        )
        if not detected_segments:
            logger.warning(
                "auto-detect found no captions; falling back to user-drawn rect"
            )
            detected_segments = None
        else:
            await slc.update(
                0.40,
                f"Detected {len(detected_segments)} caption segment(s) — inpainting…",
            )

    await slc.update(detected_segments and 0.40 or 0.0, "Erasing region…")

    def _progress_cb(frame_idx: int, total: int):
        if total <= 0:
            return
        # When auto-detect ran, inpaint occupies 0.40-1.0 of the erase slice.
        base = 0.40 if detected_segments else 0.0
        span = 1.0 - base
        p = base + span * max(0.0, min(1.0, frame_idx / total))
        asyncio.run_coroutine_threadsafe(slc.update(p, f"Erasing {frame_idx}/{total}"), loop)

    if detected_segments:
        await inpaint_region(
            str(video_path), str(output_path),
            segments=detected_segments,
            algorithm=algorithm,
            on_progress=_progress_cb,
        )
    else:
        await inpaint_region(
            str(video_path), str(output_path),
            x=x, y=y, w=w, h=h,
            algorithm=algorithm,
            on_progress=_progress_cb,
        )
    if not output_path.exists():
        raise RuntimeError("Inpaint produced no output")
    await slc.update(1.0, "Erase complete")
    return output_path


async def _stage_audio_chain(
    transcript_text: str,
    project_dir: Path,
    cfg: Dict,
    slc: _Sliced,
) -> Tuple[Path, str]:
    """
    clean_transcript → TTS → silence_remove. Returns (final_voice_path, cleaned_text).
    """
    from services.transcript_cleaner import clean_transcript
    from services.silence_remover import remove_silence

    # 1/3 — clean
    await slc.update(0.0, "Cleaning transcript…")
    cleaned = await clean_transcript(
        transcript_text,
        engine=cfg["transcript_engine"],
        target_language=cfg.get("transcript_target_lang") or None,
        progress_cb=lambda i, total: None,  # progress is coarse here
    )
    if not cleaned:
        raise RuntimeError("Transcript cleaning produced empty text")
    await slc.update(0.30, "Generating voice…")

    # 2/3 — TTS
    raw_voice = project_dir / "voice_raw.wav"
    raw_voice_path = await _run_tts(cleaned, cfg, str(raw_voice), slc)
    await slc.update(0.85, "Removing silence…")

    # 3/3 — desilence
    desilenced_raw = project_dir / "voice_desilenced.wav"
    stats = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: remove_silence(
            raw_voice_path,
            str(desilenced_raw),
            mode="audio",
        ),
    )
    logger.info(f"voice desilenced: {stats}")

    # 4/3 — loudnorm + tiny fades. EBU R128 normalization to TikTok/Spotify
    # standard (-16 LUFS). 50ms fade in/out removes the abrupt start/end
    # "click" you sometimes hear with TTS clips.
    await slc.update(0.95, "Polishing audio (loudnorm + fade)…")
    desilenced = project_dir / "voice.wav"
    voice_dur = max(0.5, _probe_audio_dur(str(desilenced_raw)))
    fade_in = 0.05
    fade_out = 0.05
    fade_out_start = max(0.0, voice_dur - fade_out)
    af = (
        f"loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}"
    )
    polish_cmd = [
        _ffmpeg_bin(), "-y", "-loglevel", "error",
        "-i", str(desilenced_raw),
        "-af", af,
        "-ar", "44100",
        str(desilenced),
    ]
    polish_loop = asyncio.get_event_loop()
    def _polish() -> None:
        r = subprocess.run(polish_cmd, capture_output=True, text=True, creationflags=_creationflags())
        if r.returncode != 0:
            # Don't fail the whole pipeline if loudnorm trips — just keep the raw.
            tail = "\n".join((r.stderr or "").strip().splitlines()[-5:])
            logger.warning(f"loudnorm/fade pass failed, using raw voice: {tail}")
            shutil.copy(desilenced_raw, desilenced)
    await polish_loop.run_in_executor(None, _polish)

    await slc.update(1.0, "Voice ready")
    return desilenced, cleaned


async def _run_tts(text: str, cfg: Dict, output_path: str, slc: _Sliced) -> str:
    """Dispatch to the user-selected TTS engine."""
    engine = cfg["tts_engine"]
    voice_id = cfg["tts_voice_id"]
    language = cfg.get("tts_language", "en")
    speed = float(cfg.get("tts_speed") or 1.0)
    loop = asyncio.get_event_loop()

    if engine == "xtts":
        from services.tts import synthesize as xtts_synth
        # XTTS accepts speed 0.5-2.0; the wrapper clamps internally.
        out = await loop.run_in_executor(
            None,
            lambda: xtts_synth(
                text=text, voice_id=voice_id, language=language,
                output_path=output_path, speed=speed,
            ),
        )
        return out

    if engine == "elevenlabs":
        from services.elevenlabs import synthesize as el_synth
        # ElevenLabs accepts 0.7-1.2; service clamps.
        await el_synth(
            text=text, voice_id=voice_id, output_path=output_path, speed=speed,
        )
        return output_path

    if engine == "local_clone":
        from services.local_clone import synthesize_cloned
        # reference_audio_path is the path to the user-uploaded WAV identified by voice_id.
        # local_clone (Piper + OpenVoice) has no speed knob in the wrapper —
        # ignore the param so the call doesn't change.
        from services.tts import get_voice_path
        ref = get_voice_path(voice_id)
        if not ref:
            raise RuntimeError(f"Reference voice '{voice_id}' not found for local_clone")
        out = await loop.run_in_executor(
            None,
            lambda: synthesize_cloned(text=text, reference_audio_path=str(ref), output_path=output_path),
        )
        return out

    raise ValueError(f"unknown tts_engine: {engine!r}")


async def _stage_commentator(
    main_video: Path,
    output_path: Path,
    cfg: Dict,
    slc: _Sliced,
) -> Dict:
    """Composite the chosen commentator preset on top of the captioned video."""
    from services.commentator_overlay import composite_commentator

    preset_id = cfg.get("commentator_preset_id")
    if not preset_id:
        # Caller already guarded against this; defensive copy.
        shutil.copy(main_video, output_path)
        return {"preset_id": None}

    await slc.update(0.10, f"Compositing commentator '{preset_id}'…")

    loop = asyncio.get_event_loop()

    # Per-run chroma override. Empty string => disable keying for this run;
    # null/missing => fall back to whatever the preset has saved.
    chroma_override = cfg.get("commentator_chroma_color")
    if chroma_override == "":
        # Use a sentinel so composite_commentator knows to skip keying.
        chroma_override = "__none__"

    def _run() -> Dict:
        return composite_commentator(
            str(main_video), str(output_path),
            preset_id=preset_id,
            chroma_override=chroma_override,
            chroma_similarity_override=cfg.get("commentator_chroma_similarity"),
            chroma_blend_override=cfg.get("commentator_chroma_blend"),
        )

    stats = await loop.run_in_executor(None, _run)
    await slc.update(1.0, "Commentator burned in")
    return stats


async def _stage_speed_match(
    erased_video: Path,
    voice: Path,
    output_path: Path,
    slc: _Sliced,
) -> Dict:
    from services.speed_match import match_video_to_voice

    await slc.update(0.05, "Matching video length to voice…")
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(
        None,
        lambda: match_video_to_voice(str(erased_video), str(voice), str(output_path)),
    )
    await slc.update(1.0, f"Speed-match done (factor {stats['factor']:.2f})")
    return stats


async def _stage_caption_burn(
    speed_matched: Path,
    cleaned_text: str,
    voice_duration_s: float,
    voice_path: Path,
    cfg: Dict,
    output_path: Path,
    slc: _Sliced,
) -> Path:
    from services.caption_overlays import build_overlays_ass, probe_video_dims
    from services.font_manager import fonts_dir

    await slc.update(0.05, "Composing captions…")

    w, h = probe_video_dims(str(speed_matched))
    czone = cfg["caption_zone"]
    zw = int(czone.get("src_w") or w)
    zh = int(czone.get("src_h") or h)
    cx_center = (int(czone["x"]) + int(czone["w"]) / 2) / zw
    cy_center = (int(czone["y"]) + int(czone["h"]) / 2) / zh
    cap_h_pct = int(czone["h"]) / zh
    # Choose scale so the largest line roughly fits the zone height.
    scale = max(0.5, min(3.0, cap_h_pct * 4.0))

    template_id = cfg.get("caption_template_id", "bold_impact")

    # User scale override multiplies the auto-fit zone scale.
    user_scale_mult = float(cfg.get("caption_scale") or 1.0)
    scale = scale * user_scale_mult

    # Per-word and punctuation knobs.
    words_per_chunk = max(1, min(10, int(cfg.get("caption_words_per_chunk") or 1)))
    strip_punct = bool(cfg.get("caption_strip_punct", True))

    # Build the inline `style` dict that overrides template defaults on
    # every overlay. Only set fields the user actually changed.
    style_override: Dict = {}
    if cfg.get("caption_font_family"):
        style_override["font_family"] = cfg["caption_font_family"]
    if cfg.get("caption_text_color"):
        style_override["text_color"] = cfg["caption_text_color"]
    if cfg.get("caption_outline_color"):
        style_override["outline_color"] = cfg["caption_outline_color"]
    if cfg.get("caption_outline_width") is not None:
        try:
            style_override["outline_width"] = float(cfg["caption_outline_width"])
        except (TypeError, ValueError):
            pass
    if cfg.get("caption_uppercase") is not None:
        style_override["uppercase"] = bool(cfg["caption_uppercase"])
    if cfg.get("caption_italic") is not None:
        style_override["italic"] = bool(cfg["caption_italic"])

    def _decorate(o: Dict) -> Dict:
        if style_override:
            o["style"] = dict(style_override)
        if strip_punct:
            o["text"] = _strip_caption_punct(o.get("text", ""))
        return o

    # For voices >20s, force-align: whisper on the voice gives us accurate
    # per-word timestamps; we keep whisper's timing but use cleaned_text's
    # spelling (whisper occasionally mishears clean TTS audio). For short
    # voices the even-distribution drift is negligible — skip the ~10–20s
    # whisper round-trip.
    overlays: List[Dict]
    if voice_duration_s >= 20.0:
        await slc.update(0.10, "Aligning caption timing to voice (whisper)…")
        try:
            from services.caption_aligner import align_words, group_into_caption_chunks
            aligned = await align_words(str(voice_path), cleaned_text)
            chunk_dicts = group_into_caption_chunks(aligned, words_per_chunk=words_per_chunk)
            overlays = [_decorate({
                "text": c["text"],
                "template_id": template_id,
                "start_t": round(float(c["start"]), 3),
                "end_t": round(float(c["end"]), 3),
                "x_pct": cx_center,
                "y_pct": cy_center,
                "scale": scale,
            }) for c in chunk_dicts]
            logger.info(
                f"caption alignment: {len(aligned)} aligned words → "
                f"{len(overlays)} chunks"
            )
        except Exception as e:
            logger.warning(
                f"caption alignment failed ({e}); falling back to even distribution"
            )
            chunks = _split_into_caption_chunks(cleaned_text, words_per_chunk=words_per_chunk)
            overlays = [_decorate(o) for o in _evenly_timed_overlays(
                chunks, voice_duration_s, template_id, cx_center, cy_center, scale,
            )]
    else:
        chunks = _split_into_caption_chunks(cleaned_text, words_per_chunk=words_per_chunk)
        overlays = [_decorate(o) for o in _evenly_timed_overlays(
            chunks, voice_duration_s, template_id, cx_center, cy_center, scale,
        )]

    ass_path = output_path.parent / "captions.ass"
    build_overlays_ass(overlays, w, h, str(ass_path))

    await slc.update(0.20, "Burning captions…")

    ass_arg = str(ass_path).replace("\\", "/").replace(":", "\\:")
    fdir = str(fonts_dir()).replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles=filename='{ass_arg}':fontsdir='{fdir}'"

    ffmpeg = _ffmpeg_bin()
    # FINAL stage = use a higher-quality encode. preset=slow keeps motion
    # detail; crf=18 cuts compression artefacts vs the old crf=20. Costs
    # ~2-3× the time of veryfast/20 but happens once, and the file lands
    # on a social platform that re-encodes — better to feed it cleaner bits.
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(speed_matched),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    loop = asyncio.get_event_loop()

    def _run() -> int:
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_creationflags())
        if r.returncode != 0:
            tail = "\n".join((r.stderr or "").strip().splitlines()[-8:])
            raise RuntimeError(f"caption burn failed: {tail}")
        return Path(output_path).stat().st_size

    size = await loop.run_in_executor(None, _run)
    await slc.update(1.0, f"Final video ready ({size // 1024} KB)")
    return output_path


async def _stage_descriptions(
    source_description: str,
    transcript_for_ai: str,
    cfg: Dict,
    slc: _Sliced,
) -> Dict[str, str]:
    """Produce the two description variants. Re-uses the transcript cleaner's
    engine choice so the user does not have to configure anything extra."""
    from services.descriptions import generate_video_descriptions

    engine = cfg.get("transcript_engine") or "ollama"
    target_lang = cfg.get("transcript_target_lang") or None

    await slc.update(0.1, "Writing descriptions…")
    try:
        result = await generate_video_descriptions(
            original_description=source_description,
            transcript=transcript_for_ai,
            engine=engine,
            target_language=target_lang,
        )
    except Exception as e:
        logger.warning(f"descriptions stage failed: {e}")
        result = {"original_translated": "", "ai_generated": ""}
    await slc.update(1.0, "Descriptions ready")
    return result


# ── Main orchestrator ──────────────────────────────────────────────────────


async def handle_remix_pipeline(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict,
    queue,
):
    """
    Orchestrate the full remix pipeline. Metadata schema:

        {
            "url": "https://...",
            "erase_zone": {x, y, w, h, src_w, src_h},
            "caption_zone": {x, y, w, h, src_w, src_h},
            "transcript_engine": "ollama|openai|anthropic",
            "transcript_target_lang": "en"|"ro"|"",
            "tts_engine": "xtts|elevenlabs|local_clone",
            "tts_voice_id": "...",
            "tts_language": "en",
            "caption_template_id": "bold_impact"
        }
    """
    cfg = dict(metadata)
    project_dir = Path(settings.media_dir) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1 — download (0.00–0.10)
    slc_dl = _Sliced(queue, job_id, 0.00, 0.10)
    video_path = await _stage_download(cfg, slc_dl, queue, job_id, project_id)

    # Stage 2 — transcribe (0.10–0.20)
    slc_tx = _Sliced(queue, job_id, 0.10, 0.20)
    tx_result = await _stage_transcribe(video_path, slc_tx)
    raw_transcript_text = tx_result.get("full_text") or ""
    if not raw_transcript_text.strip():
        raise RuntimeError("Transcription produced no text — cannot continue.")

    # Probe video dims so we can rescale the user-drawn rectangles correctly.
    from services.caption_overlays import probe_video_dims
    src_w, src_h = probe_video_dims(str(video_path))

    # Stage 3 — parallel: erase ∥ (clean → TTS → desilence)
    erased_path = project_dir / "video_erased.mp4"
    slc_erase = _Sliced(queue, job_id, 0.20, 0.65)
    slc_audio = _Sliced(queue, job_id, 0.20, 0.55)

    erase_task = asyncio.create_task(
        _stage_erase(
            video_path, erased_path, cfg["erase_zone"],
            src_w, src_h, slc_erase, job_id, queue,
            mode=cfg.get("erase_mode", "inpaint"),
            algorithm=cfg.get("erase_algorithm", "telea"),
            auto_detect=bool(cfg.get("erase_auto_detect", False)),
        )
    )
    audio_task = asyncio.create_task(
        _stage_audio_chain(raw_transcript_text, project_dir, cfg, slc_audio)
    )

    # gather will surface either task's exception once they both finish (or
    # cancel on first error if return_exceptions=False)
    erased_path, (voice_path, cleaned_text) = await asyncio.gather(
        erase_task, audio_task
    )

    # Stage 4 — speed-match video to voice (0.65–0.75)
    slc_sm = _Sliced(queue, job_id, 0.65, 0.75)
    matched_path = project_dir / "video_voicematched.mp4"
    sm_stats = await _stage_speed_match(erased_path, voice_path, matched_path, slc_sm)
    voice_dur = sm_stats["voice_dur"]

    # Stage 5 — caption burn. With commentator: 0.75–0.90, without: 0.75–0.95.
    # Stage 6 (commentator, optional): 0.90–0.95.
    # Stage 7 (descriptions): 0.95–1.00 (always runs).
    has_commentator = bool(cfg.get("commentator_preset_id"))
    cap_hi = 0.90 if has_commentator else 0.95
    slc_cap = _Sliced(queue, job_id, 0.75, cap_hi)
    captioned_path = project_dir / ("video_captioned.mp4" if has_commentator else "video_final.mp4")
    await _stage_caption_burn(
        matched_path, cleaned_text, voice_dur, voice_path,
        cfg, captioned_path, slc_cap,
    )

    # Stage 6 (optional) — commentator overlay. When no commentator is
    # selected, the captioned output IS the final.
    final_path = project_dir / "video_final.mp4"
    commentator_stats = None
    if has_commentator:
        slc_com = _Sliced(queue, job_id, 0.90, 0.95)
        commentator_stats = await _stage_commentator(
            captioned_path, final_path, cfg, slc_com,
        )
    else:
        final_path = captioned_path

    # Stage 7 — descriptions (0.95–1.00). Two short LLM calls; the user gets
    # both an original-translated and an AI-generated description.
    slc_desc = _Sliced(queue, job_id, 0.95, 1.00)
    descriptions = await _stage_descriptions(
        cfg.get("source_description", ""),
        cleaned_text or raw_transcript_text,
        cfg,
        slc_desc,
    )

    # Persist outputs on the job so the router can serve the final file.
    cfg.update({
        "video_path": str(video_path),
        "erased_path": str(erased_path),
        "voice_path": str(voice_path),
        "matched_path": str(matched_path),
        "captioned_path": str(captioned_path),
        "commentator_stats": commentator_stats,
        "final_path": str(final_path),
        "cleaned_text": cleaned_text,
        "transcript_text": raw_transcript_text,
        "speed_match_stats": sm_stats,
        "descriptions": descriptions,
        "output_filename": f"{Path(cfg.get('title') or project_id).stem}_remix.mp4",
    })
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        if job:
            job.metadata_json = json.dumps(cfg)
            await session.commit()

    logger.info(f"remix_pipeline {job_id}: done → {final_path}")
    await queue.update_progress(job_id, 1.0, "Remix complete")


def register_remix_handlers(queue):
    queue.register_handler(JobType.remix_pipeline.value, handle_remix_pipeline)
    logger.info("Remix pipeline handler registered")
