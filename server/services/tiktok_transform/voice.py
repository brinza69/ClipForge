"""
ClipForge — Video Transformare TikTok: voice-over synthesis (step 4, spec §9).

synthesize_voice() calls ElevenLabs for the Romanian narration, then converts
the returned MP3 to a polished WAV 48 kHz file (gain, fade in/out, optional
EBU R128 loudnorm) with ffmpeg — mirroring the loudnorm+fade idiom already
used by workers/remix_pipeline.py's synth_voice_from_text(), but simpler
(no desilence pass; that isn't part of this step's contract).

NOTE: this function is `async`, unlike the rest of the tiktok_transform
services (which are blocking and run via run_in_executor). It directly
`await`s services.elevenlabs.synthesize (an async httpx call), so it can't be
a plain blocking function — the calling job handler must `await` it directly
instead of scheduling it on the executor.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, Optional

from services.speed_match import probe_duration

logger = logging.getLogger("clipforge.tiktok.voice")

# ElevenLabs' hard API limit (services/elevenlabs.py clamps silently too, but
# we clamp + log here ourselves so a too-aggressive setting is surfaced).
_EL_SPEED_MIN = 0.7
_EL_SPEED_MAX = 1.2


# ── ffmpeg idiom (copied from services/speed_match.py, per the task's READ FIRST) ──
# NOTE: config.settings is imported locally (not at module level, mirroring
# tiktok_transform/storage.py's own convention) because synthesize_voice's
# contracted parameter name below is also `settings` (the project dict) —
# importing the config singleton under the same name at module scope would
# shadow it.


def _ffmpeg_bin() -> str:
    from config import settings as app_config

    loc = app_config.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def _report(on_progress: Optional[Callable[[float, str], None]], fraction: float, message: str) -> None:
    if on_progress is not None:
        on_progress(fraction, message)


async def synthesize_voice(
    text: str,
    out_path: str,
    settings: Dict,
    *,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> Dict:
    """ElevenLabs TTS -> MP3 -> WAV 48 kHz (+gain/fade/loudnorm).

    Reads from `settings` (the project's settings dict — see
    tiktok_transform/storage.py DEFAULT_SETTINGS):
        tts_voice_id, tts_model_id, tts_stability, tts_similarity, tts_style,
        tts_speaker_boost, tts_speed, voice_gain_db, fade_ms, normalize_audio

    Returns {"path": out_path, "duration": <real ffprobe seconds>}.
    Raises on any failure (ElevenLabs error, ffmpeg conversion failure) —
    never fabricates a voice file.
    """
    from services.elevenlabs import synthesize as el_synthesize

    text = (text or "").strip()
    if not text:
        raise ValueError("synthesize_voice: text is empty")

    voice_id = settings.get("tts_voice_id")
    if not voice_id:
        raise ValueError("synthesize_voice: settings.tts_voice_id is required")

    model_id = settings.get("tts_model_id") or "eleven_multilingual_v2"
    stability = float(settings.get("tts_stability", 0.5))
    similarity = float(settings.get("tts_similarity", 0.75))
    style = float(settings.get("tts_style", 0.0))
    speaker_boost = bool(settings.get("tts_speaker_boost", True))

    requested_speed = float(settings.get("tts_speed") or 1.0)
    speed = max(_EL_SPEED_MIN, min(_EL_SPEED_MAX, requested_speed))
    if abs(speed - requested_speed) > 1e-6:
        logger.warning(
            f"tts_speed {requested_speed:.3f} outside ElevenLabs' {_EL_SPEED_MIN}-{_EL_SPEED_MAX} "
            f"range; clamped to {speed:.3f}"
        )

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw_mp3 = out.parent / f"{out.stem}_raw.mp3"

    _report(on_progress, 0.0, "Generating voice-over (ElevenLabs)...")
    await el_synthesize(
        text,
        voice_id,
        str(raw_mp3),
        model_id=model_id,
        stability=stability,
        similarity_boost=similarity,
        style=style,
        speaker_boost=speaker_boost,
        speed=speed,
    )

    _report(on_progress, 0.6, "Converting voice to WAV 48kHz...")

    raw_dur = max(0.1, probe_duration(str(raw_mp3)))
    gain_db = float(settings.get("voice_gain_db", 0.0))
    fade_ms = float(settings.get("fade_ms", 0.0) or 0.0)
    # Cap the fade so in+out never exceeds the clip (short lines with a long
    # fade_ms would otherwise overlap or invert the fade-out start).
    fade_s = max(0.0, min(fade_ms / 1000.0, raw_dur * 0.2))
    normalize = bool(settings.get("normalize_audio", True))

    # Order: loudnorm first (so it normalizes the raw ElevenLabs level), then
    # the user's manual trim on top of that normalized level, then fades last
    # so they act on the final sample values.
    af_parts = []
    if normalize:
        af_parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if abs(gain_db) > 1e-6:
        af_parts.append(f"volume={gain_db:.2f}dB")
    if fade_s > 0:
        fade_out_start = max(0.0, raw_dur - fade_s)
        af_parts.append(f"afade=t=in:st=0:d={fade_s:.3f}")
        af_parts.append(f"afade=t=out:st={fade_out_start:.3f}:d={fade_s:.3f}")

    cmd = [_ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(raw_mp3)]
    if af_parts:
        cmd += ["-af", ",".join(af_parts)]
    cmd += ["-ar", "48000", "-c:a", "pcm_s16le", str(out)]

    r = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=_creationflags(), timeout=300,
    )
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg voice conversion failed: {(r.stderr or '')[-500:]}")

    try:
        raw_mp3.unlink(missing_ok=True)
    except Exception:
        pass  # cosmetic cleanup only; the WAV we need is already written

    duration = probe_duration(str(out))
    _report(on_progress, 1.0, "Voice ready")
    logger.info(
        f"tiktok voice synthesized: {out.name} ({duration:.2f}s, speed={speed:.2f}, "
        f"gain={gain_db:+.1f}dB, normalize={normalize})"
    )
    return {"path": str(out), "duration": duration}
