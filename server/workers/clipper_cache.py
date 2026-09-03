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

from services.clipper import reasoning_cache, reasoning_mode, storage

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


def _base_stamp(project_id: str, transcript: dict) -> dict:
    """Source and transcript identities shared by every reasoning cache."""
    meta = storage.read_artifact(project_id, "meta")
    # Source means the ingested media identity recorded by ingest (duration,
    # dimensions, fps and byte size), not every derived-analysis setting.
    # Proxy or frame sampling changes must invalidate only artifacts that read
    # those products; putting them here would needlessly repay transcript-only
    # promises and defeat targeted invalidation.
    source = meta.get("source") if isinstance(meta, dict) else None
    return reasoning_cache.base_inputs(source, transcript)


def _chunk_config() -> dict:
    from services.clipper import chunking

    return {
        "max_seconds": chunking.MAX_CHUNK_SECONDS,
        "max_chars": chunking.MAX_CHUNK_CHARS,
        "overlap_seconds": chunking.OVERLAP_SECONDS,
    }


def _model_inputs(engines, model=None, timeout=None) -> dict:
    from services.clipper import llm_engine
    from services import descriptions

    defaults = {
        "ollama": descriptions.DEFAULT_OLLAMA_MODEL,
        "openai": descriptions.DEFAULT_OPENAI_MODEL,
        "anthropic": descriptions.DEFAULT_ANTHROPIC_MODEL,
    }
    resolved = ({str(engine): model for engine in engines}
                if model else
                {str(engine): defaults.get(str(engine)) for engine in engines})

    return {
        "model": {"requested": model or None,
                  "resolved_by_engine": resolved},
        "temperature": llm_engine.TEMPERATURE,
        "system_prompt": llm_engine.SYSTEM_PROMPT_VERSION,
        "num_ctx": llm_engine.NUM_CTX,
        "timeout_s": timeout,
        # The shared client has no seed parameter.  Recording None is different
        # from silently implying determinism; per-chunk recovery will preserve
        # the response whose provider could not be seeded.
        "seed": None,
    }


def _atoms_stamp(base: dict, signals: dict) -> dict:
    from services.clipper import atoms

    return reasoning_cache.artifact_inputs(
        base, service_version=atoms.ATOMS_VERSION,
        upstream={"signals": signals},
        parameters={"min_s": atoms.MIN_ATOM_S, "max_s": atoms.MAX_ATOM_S,
                    "merge_ceiling_s": atoms.MERGE_CEILING_S},
    )


def _promises_stamp(base: dict, duration: float, *, model=None,
                    timeout=None) -> dict:
    from services.clipper import llm_select, promises, reasoning_chunks

    llm = _model_inputs(llm_select.NOMINATE_ENGINES, model, timeout)
    return reasoning_cache.artifact_inputs(
        base, service_version=promises.PROMISE_PROMPT_VERSION,
        prompt_version=promises.PROMISE_PROMPT_VERSION, model=llm["model"],
        temperature=llm["temperature"], engines=llm_select.NOMINATE_ENGINES,
        chunk_config=_chunk_config(),
        parameters={"duration": round(float(duration or 0.0), 3),
                    "per_chunk": 6,
                    "chunk_set_version": reasoning_chunks.CHUNK_SET_VERSION,
                    **llm},
    )


def _threads_stamp(base: dict, atoms: list[dict]) -> dict:
    from services.clipper import threads

    return reasoning_cache.artifact_inputs(
        base, service_version=threads.THREADS_VERSION,
        upstream={"atoms": atoms},
        parameters={"gap_s": threads.THREAD_GAP_S,
                    "min_shared": threads.MIN_SHARED,
                    "vocab_memory": threads.VOCAB_MEMORY,
                    "min_atoms": threads.MIN_THREAD_ATOMS},
    )


def _episodes_stamp(base: dict, atoms: list[dict], threads: list[dict]) -> dict:
    from services.clipper import episodes

    return reasoning_cache.artifact_inputs(
        base, service_version=episodes.EPISODE_VERSION,
        upstream={"atoms": atoms, "threads": threads},
        parameters={"episode_s": episodes.EPISODE_S,
                    "min_thread_span_s": episodes.MIN_THREAD_SPAN_S,
                    "keywords": episodes.EPISODE_KEYWORDS,
                    "min_mentions": episodes.MIN_MENTIONS},
    )


def _anchor_stamp(cfg: dict, duration: float, *, base: dict | None = None,
                  promises=None, atoms=None, threads=None, episodes=None,
                  model=None, timeout=None) -> dict:
    """What a cached anchor set is only valid for.

    A checkpoint without one is worse than no checkpoint: change the prompt,
    the engine or the reasoning version, re-score, and the run silently reuses
    answers the new configuration would never have produced. Everything here
    changes what the model is asked or which model is asked.
    """
    from services.clipper import llm_select, reasoning_chunks, story

    llm = _model_inputs(llm_select.NOMINATE_ENGINES, model, timeout)
    # The optional base keeps the old helper callable in isolated tests, but
    # production always passes the real source/transcript pair.
    base = base or reasoning_cache.base_inputs(
        {"duration": round(float(duration or 0.0), 3)}, {})
    return reasoning_cache.artifact_inputs(
        base,
        service_version=f"{story.STORY_VERSION}:anchors_v1",
        prompt_version=llm_select.ANCHOR_PROMPT_VERSION,
        model=llm["model"], temperature=llm["temperature"],
        engines=llm_select.NOMINATE_ENGINES,
        chunk_config=_chunk_config(),
        upstream={"promises": promises or [], "atoms": atoms or [],
                  "threads": threads or [], "episodes": episodes or []},
        parameters={"duration": round(float(duration or 0.0), 3),
                    "per_chunk": 10, "reasoning": _reasoning_mode(cfg),
                    "chunk_set_version": reasoning_chunks.CHUNK_SET_VERSION,
                    **llm},
    )


def _judge_stamp(base: dict, *, model=None) -> dict:
    """Identity of the provider contract; each round binds its exact prompt."""
    from services.clipper import judge_cache, llm_engine, llm_judge

    llm = _model_inputs(llm_engine.JUDGE_ENGINES, model, None)
    return reasoning_cache.artifact_inputs(
        base, service_version=judge_cache.JUDGE_CACHE_VERSION,
        prompt_version=llm_judge.JUDGE_PROMPT_VERSION, model=llm["model"],
        temperature=llm["temperature"], engines=llm_engine.JUDGE_ENGINES,
        parameters={"judge_cache_version": judge_cache.JUDGE_CACHE_VERSION,
                    **llm},
    )


def _cached(project_id: str, name: str, stamp: dict) -> Any | None:
    """A previous run's model output, or None when it cannot be trusted."""
    blob = storage.read_artifact(project_id, name)
    read = reasoning_cache.inspect(blob, name, stamp)
    if not read.hit:
        if blob is not None:
            logger.info("clipper %s: refusing cached %s (%s)", project_id,
                        name, read.reason)
        return None
    logger.info("clipper %s: reusing %s from a previous run", project_id, name)
    return read.data


def _cache(project_id: str, name: str, stamp: dict, data: Any) -> None:
    storage.write_artifact(project_id, name,
                           reasoning_cache.envelope(name, stamp, data))


def _frame_manifest(frames: list[str]) -> list[dict]:
    """Cheap identity of the sampled images the classifier actually reads."""
    from pathlib import Path

    out = []
    for raw in frames:
        path = Path(raw)
        try:
            stat = path.stat()
            out.append({"name": path.name, "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns})
        except OSError:
            out.append({"name": path.name, "missing": True})
    return out


def _segment_types_stamp(base: dict, duration: float, signals: dict,
                         times: list, frames: list[str]) -> dict:
    from services.clipper import segment_type

    ranges = segment_type.clock_ranges(duration)
    return reasoning_cache.artifact_inputs(
        base, service_version=segment_type.SEGMENT_TYPE_VERSION,
        upstream={"signals": signals, "frame_times": times,
                  "frames": _frame_manifest(frames)},
        parameters={"duration": round(float(duration or 0.0), 3),
                    "ranges": ranges,
                    "min_segment_s": segment_type.MIN_SEGMENT_S},
    )


def _segment_types(project_id: str, duration: float, transcript: dict,
                   *, base: dict | None = None) -> list[dict]:
    """Per-stretch content types, checkpointed. [] when it cannot be worked out.

    Never fatal: a source whose stretches cannot be classified scores exactly
    as it did before this existed.
    """
    from services.clipper import segment_type as seg_type_mod

    paths = storage.paths(project_id)
    frames = sorted(str(p) for p in paths["frames_dir"].glob("*.jpg"))
    times = (storage.read_artifact(project_id, "faces") or {}).get("times") or []
    signals = storage.read_artifact(project_id, "signals") or {}
    if not frames or len(times) != len(frames) or duration <= 0:
        return []
    stamp = _segment_types_stamp(base or _base_stamp(project_id, transcript),
                                 duration, signals, times, frames)
    cached = _cached(project_id, "segment_types", stamp)
    if isinstance(cached, list):
        return cached
    try:
        out = seg_type_mod.classify_ranges(
            frames, times, signals, transcript,
            seg_type_mod.clock_ranges(duration))
    except Exception:
        logger.warning("clipper %s: per-stretch content typing failed; using "
                       "the whole-file profile", project_id, exc_info=True)
        return []
    # Empty is a real answer when every range was too short or had no usable
    # frames.  Not caching it would repay the classifier on every re-score and
    # make an empty result look different from every other reasoning artifact.
    _cache(project_id, "segment_types", stamp, out)
    if out:
        logger.info("clipper %s: %d stretches typed (%s)", project_id, len(out),
                    ", ".join(sorted({str(s["content_type"]) for s in out})))
    return out
