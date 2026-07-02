"""
ClipForge — Auto Story Doodle: FFmpeg renderer.

Assembles the final MP4 from per-scene images + the real per-scene audio
durations recorded in the storyboard:

  1. For each scene: build a short video segment (image -> zoompan motion
     -> trimmed to the scene's REAL audio_duration).
  2. Concat all segments (concat demuxer) into one silent video track.
  3. Concat all scene wavs (or reuse final_voiceover.wav if present) into
     one audio track.
  4. Mux video + audio, burn subtitles (if enabled), encode with nvenc or
     libx264, verify duration with ffprobe.

Standalone module — only imports from `subtitles.py` / `renderer_ffmpeg.py`
within this package (no imports from other doodle modules).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

from .subtitles import build_srt, subtitle_style_args
from .renderer_ffmpeg import (
    FPS,
    ffmpeg_bin,
    escape_filter_path,
    even,
    has_nvenc,
    make_placeholder_image,
    probe_duration,
    render_scene_segment,
    run_ffmpeg,
    concat_audio,
    concat_video_segments,
)

logger = logging.getLogger("clipforge.doodle.renderer")

ProgressCB = Optional[Callable[[float, str], Awaitable[None]]]

RESOLUTIONS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}


async def render_video(project_dir: Path, storyboard: dict, progress_cb: ProgressCB = None) -> Path:
    """
    Assembles exports/final_video.mp4 from the storyboard's scenes.

    Raises RuntimeError (with ffmpeg stderr tail where applicable) on any
    failure: missing images without allow_placeholders, missing audio
    durations, ffmpeg failures, or a final duration mismatch.
    """
    project_dir = Path(project_dir)
    settings_dict = storyboard.get("settings") or {}
    scenes: List[Dict] = storyboard.get("scenes") or []
    if not scenes:
        raise RuntimeError("Cannot render: storyboard has no scenes")

    aspect_ratio = settings_dict.get("aspect_ratio", "16:9")
    width, height = RESOLUTIONS.get(aspect_ratio, RESOLUTIONS["16:9"])
    width, height = even(width), even(height)

    allow_placeholders = bool(settings_dict.get("allow_placeholders", False))
    motion_style = settings_dict.get("motion_style", "subtle")
    motion_intensity = float(settings_dict.get("motion_intensity", 0.5) or 0.5)
    subtitle_style = settings_dict.get("subtitle_style", "youtube_clean")
    burn_subtitles = bool(settings_dict.get("burn_subtitles", True))
    render_quality = settings_dict.get("render_quality", "high")
    use_gpu = bool(settings_dict.get("use_gpu", True))

    audio_dir = project_dir / "audio"
    captions_dir = project_dir / "captions"
    exports_dir = project_dir / "exports"
    work_dir = project_dir / "_render_tmp"
    for d in (captions_dir, exports_dir, work_dir):
        d.mkdir(parents=True, exist_ok=True)

    async def _report(fraction: float, message: str) -> None:
        if progress_cb:
            await progress_cb(fraction, message)

    # --- Validate images / audio durations -------------------------------
    missing_scenes: List[int] = []
    for scene in scenes:
        image_path = scene.get("image_path")
        resolved = (project_dir / image_path) if image_path else None
        if not resolved or not resolved.exists():
            missing_scenes.append(scene["index"])

    if missing_scenes and not allow_placeholders:
        raise RuntimeError(
            f"Cannot render: missing images for scenes {missing_scenes}. "
            "Upload images or enable placeholder frames."
        )

    for scene in scenes:
        if scene.get("audio_duration") is None:
            raise RuntimeError(
                f"Cannot render: scene {scene.get('index')} has no audio_duration. "
                "Generate the voiceover first."
            )

    await _report(0.02, "Preparing render...")

    # --- Build/verify final voiceover (concat per-scene audio if needed) --
    final_voiceover_rel = storyboard.get("final_voiceover_path")
    final_voiceover_path = (
        (project_dir / final_voiceover_rel) if final_voiceover_rel else (audio_dir / "final_voiceover.wav")
    )

    if not final_voiceover_path.exists():
        scene_audio_paths = []
        for scene in scenes:
            ap = scene.get("audio_path")
            if not ap:
                raise RuntimeError(f"Cannot render: scene {scene.get('index')} has no audio_path")
            resolved_ap = project_dir / ap
            if not resolved_ap.exists():
                raise RuntimeError(f"Cannot render: audio file missing for scene {scene.get('index')}: {resolved_ap}")
            scene_audio_paths.append(resolved_ap)
        final_voiceover_path.parent.mkdir(parents=True, exist_ok=True)
        await concat_audio(scene_audio_paths, final_voiceover_path)

    total_audio_duration = await probe_duration(final_voiceover_path)
    if total_audio_duration <= 0:
        raise RuntimeError("Cannot render: final voiceover has zero duration")

    # --- Per-scene segments ------------------------------------------------
    segment_paths: List[Path] = []
    n = len(scenes)
    scene_progress_span = 0.75 - 0.05  # progress runs 0.05..0.75 across scenes
    for i, scene in enumerate(scenes):
        idx = scene["index"]
        image_path = scene.get("image_path")
        resolved_image = (project_dir / image_path) if image_path else None

        if not resolved_image or not resolved_image.exists():
            # Placeholder path (only reachable if allow_placeholders is True,
            # validated above).
            resolved_image = work_dir / f"placeholder_{idx:03d}.png"
            placeholder_text = scene.get("subtitle") or scene.get("narration") or f"Scene {idx + 1}"
            await make_placeholder_image(placeholder_text, width, height, resolved_image)

        duration = float(scene.get("audio_duration") or scene.get("estimated_duration") or 3.0)
        seg_path = work_dir / f"segment_{idx:03d}.mp4"
        await render_scene_segment(
            resolved_image, duration, width, height,
            motion_style, motion_intensity, idx, seg_path,
        )
        segment_paths.append(seg_path)

        frac = 0.05 + scene_progress_span * ((i + 1) / n)
        await _report(frac, f"Rendering scene {i + 1}/{n}...")

    # --- Concat video segments -------------------------------------------
    await _report(0.8, "Combining scenes...")
    silent_video_path = work_dir / "silent_concat.mp4"
    await concat_video_segments(segment_paths, silent_video_path)

    silent_duration = await probe_duration(silent_video_path)
    pad_amount = total_audio_duration - silent_duration
    final_silent_path = silent_video_path
    if pad_amount > 0.05:
        # Extend the last frame so video duration matches audio duration
        # exactly (never truncate audio with -shortest; pad the video side).
        padded_path = work_dir / "silent_concat_padded.mp4"
        tpad_filter = f"tpad=stop_mode=clone:stop_duration={pad_amount:.3f}"
        args = [
            ffmpeg_bin(), "-y", "-loglevel", "error",
            "-i", str(silent_video_path),
            "-vf", tpad_filter,
            "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(padded_path),
        ]
        await run_ffmpeg(args, timeout=300)
        final_silent_path = padded_path

    # --- Subtitles: always write SRT ---------------------------------------
    srt_text = build_srt(scenes)
    srt_path = captions_dir / "captions.srt"
    srt_path.write_text(srt_text, encoding="utf-8")

    # --- Final mux + optional subtitle burn --------------------------------
    await _report(0.9, "Muxing audio + finalizing...")
    export_path = exports_dir / "final_video.mp4"

    vf_parts = [f"scale={width}:{height}"]
    if burn_subtitles:
        style_str = subtitle_style_args(subtitle_style, (width, height))
        escaped_srt = escape_filter_path(srt_path)
        vf_parts.append(f"subtitles=filename='{escaped_srt}':force_style='{style_str}'")
    vf = ",".join(vf_parts)

    def _build_mux_args(use_nvenc: bool) -> List[str]:
        if use_nvenc:
            enc_args = ["-c:v", "h264_nvenc", "-preset", "p5"]
            enc_args += ["-cq", "21"] if render_quality == "high" else ["-cq", "26"]
        else:
            enc_args = ["-c:v", "libx264", "-preset", "medium"]
            enc_args += ["-crf", "19"] if render_quality == "high" else ["-crf", "23"]
        return [
            ffmpeg_bin(), "-y", "-loglevel", "error",
            "-i", str(final_silent_path),
            "-i", str(final_voiceover_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf,
            *enc_args,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(export_path),
        ]

    use_nvenc = use_gpu and await has_nvenc()
    try:
        await run_ffmpeg(_build_mux_args(use_nvenc), timeout=1800)
    except RuntimeError as e:
        if use_nvenc:
            logger.warning(f"nvenc encode failed, retrying with libx264: {e}")
            await run_ffmpeg(_build_mux_args(False), timeout=1800)
        else:
            raise

    # --- Verify duration -----------------------------------------------------
    final_duration = await probe_duration(export_path)
    if abs(final_duration - total_audio_duration) > 0.5:
        raise RuntimeError(
            f"Render duration mismatch: final video is {final_duration:.2f}s, "
            f"expected ~{total_audio_duration:.2f}s (audio duration)"
        )

    # --- Cleanup temp work dir ------------------------------------------------
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:
        pass

    await _report(1.0, "Render complete")
    return export_path.resolve()
