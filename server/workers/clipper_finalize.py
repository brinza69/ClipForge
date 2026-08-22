"""
ClipForge — AI Stream Clipper: everything after the candidates are chosen.

Split from clipper_build.py, which crossed the repo's 500-line limit as Batch 1
and Batch 0 added the reasoning-mode contract and the run traces. The seam is
real: clipper_build DECIDES which moments win, and this module carries that
decision out — headlines, the trace artefacts, the `clips` rows, and the
unattended renders that follow them.

Nothing here changes what was chosen. It was moved verbatim; the only edits are
the imports it needs to stand alone.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import select

from config import settings
from database import async_session
from job_queue import JobCancelledError
from models import ClipModel, ClipStatus, JobType, ProjectModel
from services.clipper import feedback as feedback_mod
from services.clipper import reasoning_trace, storage

logger = logging.getLogger("clipforge.clipper.build")


def _write_traces(project_id: str, trace: Any, field: list[dict],
                  mode: str, eliminated: dict[int, str] | None = None) -> None:
    """Persist the two observability artefacts. Never fails the run.

    A trace that could break the run it describes would be turned off within a
    week, and then the next audit would again have to reconstruct what happened
    from the results — which is exactly the position this batch exists to end.
    """
    # Written INDEPENDENTLY. Sharing one try meant a failure serialising either
    # one lost both, and the two answer different questions — losing the
    # selection trace because a provider name would not serialise is a bad
    # trade.
    for name, build in (
        ("reasoning_run", trace.as_dict),
        ("selection_trace", lambda: reasoning_trace.build_selection_trace(
            field, mode=mode, run_id=getattr(trace, "run_id", ""),
            eliminated=eliminated,
            judged_count=trace.counts.get("judge_hits", 0),
            # Read from the run's own counter, never left at the default: the
            # two artefacts disagreed on `gateslice4h` — reasoning_run said 1
            # round, selection_trace said 0. `clipper_judging` counts from one
            # (`round_index + 1`), so 0 does not mean "accepted on the first
            # pass" — it means the judge never ran at all, which for a judged
            # run is a different claim entirely.
            pool_rounds=trace.counts.get("pool_rounds", 0))),
    ):
        try:
            storage.write_artifact(project_id, name, build())
        except Exception:  # noqa: BLE001
            logger.warning("clipper %s: could not write %s", project_id, name,
                           exc_info=True)


async def _auto_export(project_id: str, cfg: dict, queue) -> int:
    """Queue renders for the best N clips, for a run nobody is watching.

    The pipeline has always stopped here, and stopping here is right when a
    person is going to look at the board: rendering is the one stage that costs
    minutes and writes files, so doing it uninvited is the wrong surprise.

    It is the wrong answer for the other use, which is pasting a link and
    walking away. So the chain continues only when the project asked for it.

    Alternatives are excluded. They exist so a human can compare two cuts of one
    moment, and rendering both is exactly the duplication dedupe just removed.
    """
    try:
        want = int(cfg.get("auto_export", settings.clipper_auto_export) or 0)
    except (TypeError, ValueError):
        want = 0
    if want <= 0:
        return 0

    async with async_session() as session:
        rows = await session.execute(
            select(ClipModel.id)
            .where(ClipModel.project_id == project_id,
                   ClipModel.is_alternative.is_not(True),
                   ClipModel.status == ClipStatus.candidate.value)
            .order_by(ClipModel.overall_score.desc())
            .limit(want)
        )
        clip_ids = [r[0] for r in rows]

    for clip_id in clip_ids:
        # One job each rather than one job for the batch: the export lane is
        # bounded, a failed render should cost its own clip and not the rest,
        # and the board fills in as they land instead of all at the end.
        # The job carries WHO asked for it. Without this the export handler
        # cannot tell an unattended render from one a person clicked, and every
        # auto-exported clip lands in the training set as approval.
        await queue.enqueue(project_id=project_id,
                            job_type=JobType.clipper_export.value,
                            clip_id=clip_id,
                            metadata={"origin": feedback_mod.ORIGIN_AUTO})
    return len(clip_ids)


async def _attach_headlines(winners: list[dict], cfg: dict, queue, job_id: str) -> None:
    """Pass D — bounded and entirely optional.

    Runs on at most clipper_top_n_llm winners, and only when the user asked for
    headlines. A missing or broken LLM falls back to the deterministic
    extractive headline rather than failing the run.
    """
    from services.clipper.headline import generate_headline

    if not cfg.get("headline_enabled", True):
        return

    engine = settings.clipper_llm_engine or None
    language = cfg.get("language") or "auto"
    budget = max(0, int(settings.clipper_top_n_llm))

    for index, cand in enumerate(winners):
        if queue.is_cancelled(job_id):
            raise JobCancelledError("Cancelled by user.")
        # Past the budget we still want a headline — just not an LLM one.
        use_engine = engine if index < budget else None
        try:
            result = await generate_headline(cand, engine=use_engine, language=language)
            cand["headline"] = result.get("text") or ""
        except Exception:
            logger.warning("headline generation failed for a candidate", exc_info=True)
            cand["headline"] = ""


async def _exported_spans(project_id: str) -> list[dict]:
    """Spans of clips the user already exported — real deliverables, preserved
    across a re-analysis, and therefore moments the new set must not re-propose."""
    async with async_session() as session:
        rows = await session.execute(
            select(ClipModel.start_time, ClipModel.end_time)
            .where(ClipModel.project_id == project_id)
            .where(ClipModel.status == ClipStatus.exported.value)
        )
    return [{"start": float(a or 0.0), "end": float(b or 0.0)}
            for a, b in rows.all()]


def drop_moments_already_exported(ranked: list[dict], kept_spans: list[dict],
                                  threshold: float) -> list[dict]:
    """Candidates whose moment is not already on the board as an export.

    A preserved export still occupies its moment, but dedupe only ever sees the
    fresh candidates, so nothing else stops the new set proposing it again.
    Observed after three exports and a re-score: 11 winners for a requested 8,
    with one moment on the board three times.
    """
    from services.clipper import dedupe as dedupe_mod

    if not kept_spans:
        return list(ranked)
    return [c for c in ranked
            if not any(dedupe_mod.overlap_ratio(c, span) > threshold
                       for span in kept_spans)]


def _reasoning_of(cand: dict) -> dict | None:
    """Everything that explains this pick, or None when there is nothing to say.

    Kept as one JSON column rather than a dozen: the shape differs between the
    legacy path (reasons only) and story_v1 (anchor, payoff, required context,
    archetype, variant), and freezing a schema across both would mean a
    migration every time the reasoning changes.
    """
    out: dict = {}
    # `_packet` is scaffolding for the judge prompt, rebuilt from the same
    # fields every run. Persisting it would double the reasoning column and
    # freeze a second, stale copy of evidence that `remeasure` keeps current.
    if cand.get("moment_id"):
        out["moment_id"] = cand["moment_id"]
    if cand.get("reasons"):
        out["reasons"] = [str(r) for r in cand["reasons"]][:12]
    # Every scale under its own name. `overall` alone could not say whether it
    # held the heuristic, the heuristic blended with the ranker, or that blended
    # with the judge — and by the time a clip reached the UI the reading was
    # unrecoverable. Kept in the `reasoning` JSON rather than as columns: the
    # shape still differs between the legacy and story paths, and freezing it
    # into a schema would mean a migration every time the reasoning changes.
    for key in ("story", "variant", "llm_score", "llm_rank", "llm_verdict",
                "llm_reason", "llm_tag", "heuristic_score", "learned_score",
                "judge_score", "selection_score", "eligibility",
                "judge_status", "judge_round", "board_reason",
                "payoff_source", "verdict_inherited",
                # Also columns, deliberately. The column is what a query filters
                # on to build a review session; this copy is what survives in
                # the explanation a person reads next to the clip.
                "shadow_rank", "shadow_run_id"):
        value = cand.get(key)
        if value not in (None, "", [], {}):
            out[key] = value
    return out or None


async def _write_clips(
    project_id: str, ranked: list[dict], winners: list[dict], profile: str
) -> None:
    """Replace this project's candidates with the new set.

    A re-analysis should not leave the previous run's clips behind, but clips
    the user already exported are real deliverables — those are preserved.

    A preserved clip still occupies its moment, so the new set must not propose
    that moment again: dedupe only ever sees the fresh candidates, and without
    this the board shows the same window twice, once as the export and once as
    a new winner. Observed after three exports and a re-score — 11 winners for
    a requested 8, with one moment on the board three times.
    """
    winner_ids = {id(c) for c in winners}

    async with async_session() as session:
        keep = await session.execute(
            select(ClipModel.id, ClipModel.start_time, ClipModel.end_time)
            .where(ClipModel.project_id == project_id)
            .where(ClipModel.status == ClipStatus.exported.value)
        )
        kept = keep.all()
        keep_ids = {row[0] for row in kept}
        kept_spans = [{"start": float(row[1] or 0.0), "end": float(row[2] or 0.0)}
                      for row in kept]

        stmt = sql_delete(ClipModel).where(ClipModel.project_id == project_id)
        if keep_ids:
            stmt = stmt.where(ClipModel.id.notin_(keep_ids))
        await session.execute(stmt)

        # Belt and braces: handle_score already filtered these out before
        # dedupe, but _write_clips is the only thing guarding the table.
        fresh = drop_moments_already_exported(
            ranked, kept_spans, float(settings.clipper_overlap_threshold))
        for cand in fresh:
            # A shadow pick is `is_alternative`, so the plans it was given would
            # be dropped here — and a clip with no layout cannot be rendered at
            # all. `planned` is what HAS a plan, which is winners plus whatever
            # the shadow board asked for; `is_winner` still decides what ships.
            is_winner = id(cand) in winner_ids
            planned = is_winner or bool(cand.get("shadow_rank"))
            layout = cand.get("layout") if planned else None
            start = float(cand.get("start") or 0.0)
            end = float(cand.get("end") or 0.0)
            session.add(
                ClipModel(
                    project_id=project_id,
                    title=(cand.get("headline") or cand.get("title") or "Untitled clip")[:400],
                    start_time=start,
                    end_time=end,
                    duration=round(max(0.0, end - start), 3),
                    overall_score=float(cand.get("overall") or 0.0),
                    sub_scores=cand.get("sub_scores"),
                    score_reason=cand.get("reason"),
                    headline_text=cand.get("headline") or None,
                    transcript_text=(cand.get("text") or "")[:20000],
                    content_type=cand.get("content_type") or profile,
                    layout_plan=layout,
                    caption_plan=cand.get("captions") if planned else None,
                    warnings=(layout or {}).get("warnings") or [],
                    reasoning=_reasoning_of(cand),
                    dedupe_group=cand.get("dedupe_group"),
                    is_alternative=bool(cand.get("is_alternative")),
                    rank_position=cand.get("rank_position"),
                    shadow_rank=cand.get("shadow_rank"),
                    shadow_run_id=cand.get("shadow_run_id") or None,
                    feature_vector=cand.get("features"),
                    ranker_version=cand.get("ranker_version"),
                    status=ClipStatus.candidate.value,
                )
            )
        await session.commit()


def plan_winners(winners: list[dict], cfg: dict, transcript: dict,
                 regions: dict, faces: list, src_w: int, src_h: int,
                 profile: str) -> None:
    """Pass E's cheap half: a 9:16 layout and a caption plan per winner.

    Mutates in place, which is what the rest of the stage already assumes.
    Both steps fall back rather than raise: a clip with no layout plan cannot
    be rendered at all, and one bad candidate must not cost the other seven.
    """
    from services.clipper import captions as cap_mod
    from services.clipper import layout as layout_mod

    layout_mode = cfg.get("layout_mode") or "auto"
    face_pct = float(cfg.get("face_pct") or settings.clipper_face_pct)
    preset_id = cfg.get("caption_preset_id") or "bold_impact"
    position = cfg.get("caption_position") or "bottom"

    for cand in winners:
        try:
            cand["layout"] = layout_mod.plan_layout(
                cand, regions, faces, src_w, src_h,
                mode=layout_mode,
                face_pct=face_pct,
                include_chat=bool(cfg.get("include_chat")),
                content_type=cand.get("content_type") or profile,
            )
        except Exception:
            logger.warning("layout planning failed; falling back to a full-frame crop",
                           exc_info=True)
            cand["layout"] = {
                "layout": "fullscreen_crop",
                "face_rect": None, "game_rect": None, "chat_rect": None,
                "keyframes": [], "warnings": ["Layout planning failed; using a centre crop."],
                "face_pct": face_pct, "safe_zones": {},
            }
        try:
            cand["captions"] = cap_mod.build_caption_plan(
                cand, transcript,
                preset_id=preset_id,
                max_words=3,
                position=position,
                layout=cand["layout"],
            )
        except Exception:
            logger.warning("caption planning failed for a candidate", exc_info=True)
            cand["captions"] = None
