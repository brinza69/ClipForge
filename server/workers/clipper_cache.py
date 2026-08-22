"""
ClipForge — AI Stream Clipper: what a previous run left behind, and whether it
can be trusted.

Split from clipper_build.py, which crossed the repo's 500-line limit again as
Batch 4 and Batch 5 added the chunk planner and the moment pool. One concern:
every function here answers "is the thing already on disk still an answer to
the question we are asking now".

A checkpoint without a stamp is WORSE than no checkpoint. Change the prompt,
the engine, the reasoning mode or the chunk plan, re-score, and the run
silently reuses answers the new configuration would never have produced — and
it looks like nothing changed, because nothing recomputed.
"""

from __future__ import annotations

import logging
from typing import Any

from services.clipper import reasoning_mode, storage

logger = logging.getLogger("clipforge.clipper.build")


def _reasoning_mode(cfg: dict) -> str:
    """Which engine this project runs. One call site's worth of defaults.

    Reads the project's own settings first and the rig's config second, so a
    project stored before `reasoning_mode` existed still resolves to the mode
    its old `llm_select`/`reasoning_version` pair meant.
    """
    from config import settings

    return reasoning_mode.resolve(
        cfg,
        llm_select_default=settings.clipper_llm_select,
        version_default=settings.clipper_reasoning_version,
        mode_default=settings.clipper_reasoning_mode,
    )


def _anchor_stamp(cfg: dict, duration: float) -> dict:
    """What a cached anchor set is only valid for.

    A checkpoint without one is worse than no checkpoint: change the prompt,
    the engine or the reasoning version, re-score, and the run silently reuses
    answers the new configuration would never have produced. Everything here
    changes what the model is asked or which model is asked.
    """
    from services.clipper import chunking, llm_select

    return {
        "prompt": llm_select.ANCHOR_PROMPT_VERSION,
        # Resolves to the same string the old pair produced for every project
        # that has one — "story_v1" stays "story_v1" — so switching to
        # reasoning_mode does not invalidate a single cached anchor set.
        "reasoning": _reasoning_mode(cfg),
        "engines": list(llm_select.NOMINATE_ENGINES),
        "duration": round(float(duration or 0.0), 1),
        # The CHUNK PLAN decides which stretches the model was ever shown, so
        # answers produced under a different plan are not answers to the same
        # question. Without this, a project cached under the old
        # character-only split reuses two chunks covering 3h21m and 38m after
        # the planner has been replaced, and the run looks unchanged because
        # nothing recomputed.
        "chunking": [round(chunking.MAX_CHUNK_SECONDS, 1),
                     int(chunking.MAX_CHUNK_CHARS),
                     round(chunking.OVERLAP_SECONDS, 1)],
    }


def _cached(project_id: str, name: str, stamp: dict) -> Any | None:
    """A previous run's model output, or None when it cannot be trusted."""
    blob = storage.read_artifact(project_id, name)
    if not isinstance(blob, dict) or blob.get("stamp") != stamp:
        return None
    logger.info("clipper %s: reusing %s from a previous run", project_id, name)
    return blob.get("data")


def _cache(project_id: str, name: str, stamp: dict, data: Any) -> None:
    storage.write_artifact(project_id, name, {"stamp": stamp, "data": data})


def _segment_types(project_id: str, duration: float, transcript: dict) -> list[dict]:
    """Per-stretch content types, checkpointed. [] when it cannot be worked out.

    Never fatal: a source whose stretches cannot be classified scores exactly
    as it did before this existed.
    """
    from services.clipper import segment_type as seg_type_mod

    cached = storage.read_artifact(project_id, "segment_types")
    if isinstance(cached, list) and cached:
        return cached

    paths = storage.paths(project_id)
    frames = sorted(str(p) for p in paths["frames_dir"].glob("*.jpg"))
    times = (storage.read_artifact(project_id, "faces") or {}).get("times") or []
    signals = storage.read_artifact(project_id, "signals") or {}
    if not frames or len(times) != len(frames) or duration <= 0:
        return []
    try:
        out = seg_type_mod.classify_ranges(
            frames, times, signals, transcript,
            seg_type_mod.clock_ranges(duration))
    except Exception:
        logger.warning("clipper %s: per-stretch content typing failed; using "
                       "the whole-file profile", project_id, exc_info=True)
        return []
    if out:
        storage.write_artifact(project_id, "segment_types", out)
        logger.info("clipper %s: %d stretches typed (%s)", project_id, len(out),
                    ", ".join(sorted({str(s["content_type"]) for s in out})))
    return out
