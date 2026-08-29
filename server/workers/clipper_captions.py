"""What the burned captions say, where they sit, and on which clock.

Split out of `clipper_render_plan.py` when Batch R3a took it past the repo's
500-line limit. The seam is the one the file already had: everything here is
about the caption track — the words on the clip's clock, the height the export
chooses when it finds game UI the score-time plan could not have known about,
and the .ass the renderer burns. Nothing here decides what is on screen.

Re-exported from `clipper_render_plan`, so every existing import still works.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from models import ClipModel

logger = logging.getLogger("clipforge.clipper.render")


def _clip_words(clip: ClipModel) -> list[dict]:
    """The clip's word timings, on the SOURCE clock.

    `dynamic_edit` needs these and had never been given them: `_boundaries`
    places its cuts on speech pauses and `_speech_ratio` decides whether the
    streamer is talking in a shot, and both were reading an empty list on every
    export. Measured on clip 6b34b8d37259: with words the planner cuts 11 shots
    on the pauses, without them 9 on audio peaks and scene changes alone — and
    the second is exactly what shipped.

    `transcript_segments` is the obvious home for this and is NULL on every clip
    the pipeline writes, so the words come from the caption plan, which carries
    them because the word-highlight overlay needs them. That also keeps the cut
    grid and the burned captions reading the same timings.

    The caption plan's clock is clip-relative and `dynamic_edit` subtracts
    `clip_start` from every word, so the offset has to go back on here.
    """
    plan = clip.caption_plan if isinstance(clip.caption_plan, dict) else None
    if not plan:
        return []
    offset = float(clip.start_time or 0.0)
    out: list[dict] = []
    for chunk in plan.get("chunks") or []:
        for word in (chunk or {}).get("words") or []:
            try:
                start = float(word["start"]) + offset
                end = float(word.get("end", word["start"])) + offset
            except (KeyError, TypeError, ValueError):
                continue
            out.append({"word": str(word.get("word") or ""),
                        "start": start, "end": end})
    return out


def _caption_y(clip: ClipModel, dyn: dict | None) -> float | None:
    """Where the caption should sit given the UI panels THIS cut exposes.

    The stored `y_pct` was resolved at score time against `regions.json`, whose
    `hud` list is empty on the source where a caption demonstrably landed on the
    game UI. The panels are detected per clip and the shot list says where each
    one lands in the output frame, so both halves only exist here, at export.

    Returns None when there is nothing new to say, and the stored position
    stands.
    """
    panels = (dyn or {}).get("_panels") or []
    if not panels or not clip.caption_plan:
        return None
    if (clip.caption_plan or {}).get("y_pct_manual"):
        # Somebody moved it in the editor. Re-placing it around detected UI is
        # right when nobody has expressed a preference and wrong the moment
        # somebody has — an edit that the next export silently undoes is worse
        # than no editor at all.
        return None
    try:
        from services.clipper.captions import panels_to_keep_out, resolve_position

        keep = panels_to_keep_out(panels, (dyn or {}).get("shots") or [])
        if not keep:
            return None
        existing = ((clip.layout_plan or {}).get("safe_zones") or {}).get("keep_out") or []
        _x, y = resolve_position(
            str((clip.caption_plan or {}).get("position") or "bottom"),
            {"safe_zones": {"keep_out": list(existing) + keep}})
        return y
    except Exception:
        logger.warning("clip %s: could not re-place the caption around the UI "
                       "panels; keeping the stored position", clip.id, exc_info=True)
        return None


def _write_ass(clip: ClipModel, out_dir: Path,
               drop_spans: Sequence[tuple[float, float]] | None = None,
               y_pct: float | None = None) -> str | None:
    """Render the stored caption plan to an .ass file. Returns None when the
    clip has no captions, which is a legitimate state (the user can turn them
    off) — the render then simply skips the subtitles filter.

    `drop_spans` are the dead seconds the render is about to remove. The
    overlays have to move with them: libass positions against absolute times,
    so a caption left on the untrimmed clock drifts further out of sync with
    every second cut.

    `y_pct` overrides the stored caption height when the export found game UI
    the score-time plan could not have known about. The plan itself is left
    alone: it is a record of what was decided then, and a re-score would
    recompute it anyway.
    """
    from services.caption_overlays import build_overlays_ass
    from services.clipper.captions import caption_plan_to_overlays

    if not clip.caption_plan:
        return None
    plan = clip.caption_plan
    if y_pct is not None and abs(float(plan.get("y_pct") or 0.0) - y_pct) > 1e-4:
        logger.info("clip %s: caption moved %.3f -> %.3f to clear detected game UI",
                    clip.id, float(plan.get("y_pct") or 0.0), y_pct)
        plan = {**plan, "y_pct": y_pct}
    try:
        overlays = caption_plan_to_overlays(plan)
    except Exception:
        logger.warning("could not turn the caption plan into overlays", exc_info=True)
        return None
    if drop_spans:
        from services.clipper.dead_air import remap_overlays

        overlays = remap_overlays(overlays, drop_spans)
    if not overlays:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    ass_path = out_dir / f"{clip.id}.ass"
    # The ASS canvas is the OUTPUT canvas: libass positions against PlayRes, and
    # the plan's x_pct/y_pct were resolved against 1080x1920 safe zones.
    build_overlays_ass(overlays, 1080, 1920, str(ass_path))
    return str(ass_path)
