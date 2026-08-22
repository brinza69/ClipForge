"""
ClipForge — AI Stream Clipper: what a project's settings mean.

Split from routers/clipper.py when that file crossed the repo's 500-line limit,
which it did as Batch 1 added the reasoning-mode contract. No routes live here:
this is the one place that decides what a settings dict is allowed to say, and
both HTTP entry points — create and patch — go through it.

THE DEFECT IT WAS BUILT AROUND. `_normalise_settings` keeps only keys already
present in `_default_settings()`. A key the form posts and that dict does not
know is discarded in SILENCE: the project is created, the run proceeds, and the
feature simply does nothing. That is how the story engine spent months
unreachable from the UI and the API — neither of the two keys that turned it on
was ever listed. `server/tests/test_settings_parity.py` compares the two
dictionaries directly for exactly that reason.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from config import settings
from services.clipper import reasoning_mode

logger = logging.getLogger("clipforge.clipper.api")


def _err(status: int, code: str, message: str, details: str = "") -> HTTPException:
    """Structured error matching main.py's unhandled-exception shape, so the
    frontend's readApiError() reads both the same way."""
    return HTTPException(status, {"error": code, "message": message, "details": details})


# Modes the pipeline knows but does not implement yet, and why. Refused with a
# distinct error code so a client can tell "not yet" from "no such thing".
_NOT_AVAILABLE_YET: dict[str, str] = {
    reasoning_mode.STORY_V2: (
        "The rule it names works, but it has not been compared against legacy "
        "on a corpus yet. Run story_v2_shadow first: it writes the v2 ordering "
        "into the trace while your board stays exactly as it is."
    ),
}


def _rig_reasoning_mode() -> str:
    """The rig's default reasoning mode, clamped to one that actually exists yet.

    The availability gate below refuses an unimplemented mode when a CLIENT asks
    for it. config.py is a second door into the same setting, and without this
    an operator who set CLIPFORGE_CLIPPER_REASONING_MODE to one of those modes
    would put EVERY new project in it — the gate refused to the browser and
    granted to the machine.

    Clamped and logged rather than fatal: this is the operator's own rig, and
    refusing to serve the whole app over one clipper setting is out of
    proportion to the mistake.
    """
    mode = reasoning_mode.resolve(
        None,
        llm_select_default=settings.clipper_llm_select,
        version_default=settings.clipper_reasoning_version,
        mode_default=settings.clipper_reasoning_mode,
    )
    if mode not in reasoning_mode.SELECTABLE:
        logger.warning(
            "clipper: reasoning mode %r is configured but not available yet — "
            "falling back to %r. Available: %s",
            mode, reasoning_mode.LEGACY, ", ".join(reasoning_mode.SELECTABLE),
        )
        return reasoning_mode.LEGACY
    return mode


def _default_settings() -> dict[str, Any]:
    """Mirrors DEFAULT_SETTINGS in src/types/clipper.ts, sourced from config so
    the operator can retune the rig without touching the frontend."""
    return {
        "clip_count": settings.clipper_default_clip_count,
        "min_clip_s": settings.clipper_min_clip_s,
        "max_clip_s": settings.clipper_max_clip_s,
        "platform": "tiktok",
        "fps": settings.clipper_export_fps,
        "language": "auto",
        "auto_export": settings.clipper_auto_export,
        "vision_review": settings.clipper_vision_review,
        "vision_model": settings.clipper_vision_model,
        "caption_preset_id": "bold_impact",
        "caption_position": "bottom",
        "caption_highlight": True,
        "headline_enabled": True,
        "headline_auto": True,
        "emoji_enabled": False,
        "profanity_mask": False,
        "trim_silence": settings.clipper_trim_silence,
        "jump_cuts": False,
        "auto_zoom": True,
        "reaction_zoom": True,
        "facecam_emphasis": True,
        "dynamic_edit": settings.clipper_dynamic_edit,
        "include_chat": False,
        "watermark_text": "",
        "min_score": 0,
        "layout_mode": "auto",
        "face_pct": settings.clipper_face_pct,
        # Resolved rather than hardcoded: an operator who turned the story
        # engine on through the old environment variables must keep it. See
        # services/clipper/reasoning_mode.py for why that pair still exists.
        "reasoning_mode": _rig_reasoning_mode(),
    }


def _normalise_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Merge client settings over the defaults and clamp everything.

    Clamping happens here, once, rather than in each consumer: an out-of-range
    value from a hand-rolled API call must never reach the pipeline (a
    max_clip_s of 10 hours would make the renderer try to encode the whole VOD).
    """
    out = _default_settings()
    rig_mode = str(out["reasoning_mode"])
    for key, value in (raw or {}).items():
        if key in out and value is not None:
            out[key] = value

    def _num(key: str, lo: float, hi: float, cast=float):
        try:
            out[key] = cast(max(lo, min(hi, cast(out[key]))))
        except (TypeError, ValueError):
            out[key] = cast(_default_settings()[key])

    _num("clip_count", 1, 20, int)
    # Same ceiling as clip_count: auto-export cannot ask for more clips than the
    # run will produce, and an unbounded value here is unattended render time.
    _num("auto_export", 0, 20, int)
    _num("min_clip_s", 3, 600)
    _num("max_clip_s", 5, 900)
    _num("face_pct", 0.15, 0.6)
    _num("min_score", 0, 100)

    # A min above max would produce zero candidates with no visible reason.
    if out["min_clip_s"] >= out["max_clip_s"]:
        out["min_clip_s"], out["max_clip_s"] = (
            settings.clipper_min_clip_s,
            settings.clipper_max_clip_s,
        )

    if out.get("platform") not in {"tiktok", "youtube_shorts", "instagram_reels", "facebook_reels"}:
        out["platform"] = "tiktok"
    if out.get("caption_position") not in {"bottom", "center", "top"}:
        out["caption_position"] = "bottom"
    if out.get("fps") not in {"source", 30, 60}:
        out["fps"] = settings.clipper_export_fps
    out["watermark_text"] = str(out.get("watermark_text") or "")[:80]

    # The one value that is REFUSED rather than clamped. Everything else here
    # falls back to a default when it is out of range, because a bad number is
    # almost always a client bug and silently fixing it keeps the run going.
    # A reasoning mode is different: it decides which engine runs, so a request
    # that reads back as something other than what it asked for is the exact
    # defect Batch 1 exists to end.
    #
    # Validate what was ASKED FOR, not what survived the merge: an unknown mode
    # has to be refused rather than quietly replaced by the default.
    mode = str((raw or {}).get("reasoning_mode") or "").strip().lower()
    if mode in _NOT_AVAILABLE_YET:
        # Known, spelled correctly, and not implemented — which is a different
        # answer from "no such mode", and the client should be able to tell
        # them apart. No alternative is suggested: the only other non-legacy
        # mode is the one that is also refused.
        raise _err(400, "reasoning_mode_unavailable",
                   f"{mode} is not available yet.", _NOT_AVAILABLE_YET[mode])
    if mode and mode not in reasoning_mode.SELECTABLE:
        raise _err(
            400, "reasoning_mode_invalid",
            f"Unknown reasoning mode {mode!r}.",
            "Valid modes: " + ", ".join(reasoning_mode.SELECTABLE) + ".",
        )

    # A settings dict that predates this key still carries the pair it replaced,
    # and PATCH sends the WHOLE dict — so resolving here is what stops an
    # unrelated edit from turning the story engine off. Without it the two
    # story projects on this rig lose story_v1 the first time anyone changes
    # their clip count: the old keys are not in `out`, so they are dropped, and
    # a bare default would then be written on top as if it had been chosen.
    out["reasoning_mode"] = reasoning_mode.resolve(
        raw,
        llm_select_default=settings.clipper_llm_select,
        version_default=settings.clipper_reasoning_version,
        mode_default=rig_mode,
    )
    return out
