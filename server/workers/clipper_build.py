"""
ClipForge — AI Stream Clipper: the candidate-building stage.

Takes the cached transcript + signals and produces ranked, deduplicated,
laid-out, captioned candidates as `clips` rows.

This is where Pass B (semantic windows), Pass C (candidates + scoring) and the
cheap parts of Pass E (layout + caption planning) run. Pass D — the LLM
judgement — is bounded to the top N winners and is skipped entirely when no
engine is configured, so an absent optional provider can never fail a run.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update

from config import settings
from database import async_session
from job_queue import JobCancelledError
from models import (
    ClipModel,
    ClipStatus,
    JobType,
    ProjectModel,
    ProjectStatus,
    TranscriptModel,
)
from services.clipper import storage
from services.clipper.serialize import effective_content_type

logger = logging.getLogger("clipforge.clipper.build")


def _anchor_stamp(cfg: dict, duration: float) -> dict:
    """What a cached anchor set is only valid for.

    A checkpoint without one is worse than no checkpoint: change the prompt,
    the engine or the reasoning version, re-score, and the run silently reuses
    answers the new configuration would never have produced. Everything here
    changes what the model is asked or which model is asked.
    """
    from services.clipper import llm_select

    return {
        "prompt": llm_select.ANCHOR_PROMPT_VERSION,
        "reasoning": str(cfg.get("reasoning_version")
                         or settings.clipper_reasoning_version or "legacy"),
        "engines": list(llm_select.NOMINATE_ENGINES),
        "duration": round(float(duration or 0.0), 1),
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


async def _fetch_transcript(project_id: str) -> dict[str, Any]:
    async with async_session() as session:
        result = await session.execute(
            select(TranscriptModel).where(TranscriptModel.project_id == project_id).limit(1)
        )
        row = result.scalar_one_or_none()
    if not row or not row.segments:
        raise RuntimeError("No transcript for this project — run the transcribe stage first.")
    return {"language": row.language, "segments": row.segments, "full_text": row.full_text}


def _guard(queue, job_id: str) -> None:
    if queue.is_cancelled(job_id):
        raise JobCancelledError("Cancelled by user.")


async def handle_score(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    from services.clipper import candidates as cand_mod
    from services.clipper import captions as cap_mod
    from services.clipper import dedupe as dedupe_mod
    from services.clipper import layout as layout_mod
    from services.clipper import atoms as atoms_mod
    from services.clipper import llm_select
    from services.clipper import promises as promises_mod
    from services.clipper import ranker, scoring, segmentation
    from services.clipper import segment_type as seg_type_mod
    from services.clipper.candidate_terms import _num
    from services.clipper import threads as threads_mod

    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
    if not project:
        raise RuntimeError(f"project {project_id} disappeared mid-pipeline")

    cfg = dict(project.clipper_settings or {})
    duration = float(project.duration or 0.0)
    src_w = int(project.width or 1920)
    src_h = int(project.height or 1080)
    profile = effective_content_type(project)
    platform = cfg.get("platform") or "tiktok"

    min_s = float(cfg.get("min_clip_s") or settings.clipper_min_clip_s)
    max_s = float(cfg.get("max_clip_s") or settings.clipper_max_clip_s)
    target_count = int(cfg.get("clip_count") or settings.clipper_default_clip_count)

    transcript = await _fetch_transcript(project_id)
    sig = storage.read_artifact(project_id, "signals") or {}
    regions = storage.read_artifact(project_id, "regions") or {}
    faces_blob = storage.read_artifact(project_id, "faces") or {}
    faces = faces_blob.get("samples") or []
    # What each STRETCH of the source is, rather than what the file is. Every
    # long live source labelled by hand runs two or three types in a row, so a
    # single project-level answer is wrong for a third of the clips on them no
    # matter how good the classifier gets. Measured on slice4h00test against
    # the labels: the whole-file answer was right for 0 of 12 stretches, the
    # per-stretch one for 9. Falls back to `profile` wherever a stretch is too
    # short to classify, and the override still wins over everything.
    seg_types: list[dict] = []
    if not project.content_type_override:
        seg_types = _segment_types(project_id, duration, transcript)

    # ── Pass B ──────────────────────────────────────────────────────────────
    await queue.update_progress(job_id, 0.05, "Building semantic segments")
    windows = segmentation.semantic_windows(transcript, sig, min_s=min_s, max_s=max_s)
    storage.write_artifact(project_id, "segments", windows)
    _guard(queue, job_id)
    if not windows:
        raise RuntimeError(
            "No semantic segments could be built from this transcript. The source may be "
            "too short or contain almost no speech."
        )

    # ── Pass C ──────────────────────────────────────────────────────────────
    await queue.update_progress(job_id, 0.20, "Creating candidates")
    raw = cand_mod.generate_candidates(
        windows,
        sig,
        min_s=min_s,
        max_s=max_s,
        target_s=float(settings.clipper_target_clip_s),
    )
    # A cheap model reads the whole transcript and nominates moments. UNIONED
    # with the rule-based candidates, never substituted: a small model
    # nominates roughly the same obvious moments the scorer already finds, so
    # using it as a filter would discard exactly the non-obvious picks the
    # judging pass is paid to find. Two recalls that fail differently.
    reasoning = str(cfg.get("reasoning_version")
                    or settings.clipper_reasoning_version or "legacy").lower()
    if bool(cfg.get("llm_select", settings.clipper_llm_select)):
        await queue.update_progress(job_id, 0.25, "Reading the transcript")
        segments = transcript.get("segments") or []
        try:
            if reasoning == "story_v1":
                # The stream as units the reasoning can point at, each
                # carrying its own signals. Built from the transcript and the
                # Pass A series with no model involved — a 12-hour stream is
                # ~8,600 atoms and the cost rule forbids a call per two
                # seconds of video.
                atoms = storage.read_artifact(project_id, "atoms")
                if not isinstance(atoms, list):
                    atoms = atoms_mod.build(transcript, sig)
                    storage.write_artifact(project_id, "atoms", atoms)
                # Setups that could pay off later, swept once over the whole
                # transcript and checkpointed. Anchor detection runs per chunk
                # with no memory across chunks, so without this a payoff that
                # lands on a prediction from an hour earlier is invisible.
                known = storage.read_artifact(project_id, "promises")
                if not isinstance(known, list):
                    known = await promises_mod.detect(segments, duration)
                    storage.write_artifact(project_id, "promises", known)
                # Payoff first: each anchor carries what a viewer must already
                # know, so the window can open on the earliest required fact
                # rather than on the first audio spike.
                # Narrative arcs, by lexical chaining over the atoms — no
                # model. Diversity reads them so a stream that spends an hour
                # on one boss cannot hand back a board that is all that boss.
                arcs = storage.read_artifact(project_id, "threads")
                if not isinstance(arcs, list):
                    arcs = threads_mod.build(atoms)
                    storage.write_artifact(project_id, "threads", arcs)
                # `arcs` also becomes the rolling summary (§2): a chunk at hour
                # seven is told what the stream has been about before it, which
                # is the one thing a per-chunk pass otherwise cannot know.
                anchors = _cached(project_id, "anchors",
                                  _anchor_stamp(cfg, duration))
                if anchors is None:
                    anchors = await llm_select.detect_anchors(
                        segments, duration, promises=known, atoms=atoms,
                        threads=arcs)
                    _cache(project_id, "anchors",
                           _anchor_stamp(cfg, duration), anchors)
                storage.write_artifact(project_id, "graph",
                                       threads_mod.edges(arcs, known, anchors))
                nominated = cand_mod.candidates_from_anchors(
                    anchors, transcript, sig, min_s=min_s, max_s=max_s,
                    duration=duration, atoms=atoms, threads=arcs)
            else:
                nominated = await llm_select.nominate(segments, duration)
        except Exception:
            logger.warning("LLM proposal failed; keeping the rule-based set",
                           exc_info=True)
            nominated = []
        if nominated:
            raw = cand_mod.merge_nominations(
                raw, nominated, transcript, min_s=min_s, max_s=max_s,
                keep_overlaps=(reasoning == "story_v1"))
        _guard(queue, job_id)

    refined = []
    for cand in raw:
        try:
            refined.append(
                cand_mod.refine_boundaries(cand, transcript, sig, min_s=min_s, max_s=max_s)
            )
        except Exception:
            # One bad window must not sink the run — keep the unrefined form.
            logger.warning("boundary refinement failed for a candidate", exc_info=True)
            refined.append(cand)
    _guard(queue, job_id)

    await queue.update_progress(job_id, 0.40, "Scoring candidates")
    model = ranker.load_model() if settings.clipper_ranker_enabled else None
    use_learned = False
    if model:
        async with async_session() as session:
            from services.clipper import feedback

            rows = await feedback.training_rows(session)
        use_learned = ranker.should_use_learned(model, len(rows))

    for cand in refined:
        features = cand_mod.extract_features(cand, transcript, sig, duration)
        # The profile of the stretch this candidate sits in, at its midpoint.
        here = seg_type_mod.type_at(
            seg_types, (_num(cand.get("start")) + _num(cand.get("end"))) / 2.0,
            profile)
        cand["content_type"] = here
        scored = scoring.score_candidate(cand, features, profile=here, platform=platform)
        cand["features"] = features
        cand["sub_scores"] = scored["sub_scores"]
        cand["reason"] = scored["reason"]
        heuristic = float(scored["overall"])
        if use_learned:
            # Blend rather than replace: the learned model is trained on this
            # user's taste but on a small dataset, so the transparent heuristic
            # keeps half the vote.
            learned = ranker.predict(model, features) * 100.0
            cand["overall"] = round(0.5 * heuristic + 0.5 * learned, 2)
            cand["ranker_version"] = model.get("version")
        else:
            cand["overall"] = round(heuristic, 2)
            cand["ranker_version"] = "heuristic-1"
    # The frontier pass, over the union. Blended into `overall`, so a failure
    # here costs the judgement, not the run.
    if bool(cfg.get("llm_select", settings.clipper_llm_select)):
        await queue.update_progress(job_id, 0.48, "Judging candidates")
        try:
            await llm_select.judge(
                refined,
                weight=float(settings.clipper_llm_weight),
                want=target_count,
                model=(cfg.get("llm_judge_model")
                       or settings.clipper_llm_judge_model or None))
        except Exception:
            logger.warning("LLM judging failed; keeping the heuristic ranking",
                           exc_info=True)
        _guard(queue, job_id)

    storage.write_artifact(project_id, "candidates", refined)

    # ── Dedupe + diversity ──────────────────────────────────────────────────
    # Moments already on the board as exports come out FIRST, before dedupe
    # picks group leaders. Doing it at write time instead silently shrank the
    # board: dedupe would elect a leader, the leader would be dropped for
    # overlapping an export, and its group's runner-up was never promoted —
    # measured, 8 winners became 3 and every story-built cut was left flagged
    # as an alternative behind a leader that no longer existed.
    refined = drop_moments_already_exported(
        refined, await _exported_spans(project_id),
        float(settings.clipper_overlap_threshold))

    await queue.update_progress(job_id, 0.55, "Removing duplicates")
    ranked = dedupe_mod.deduplicate(
        refined,
        overlap_threshold=float(settings.clipper_overlap_threshold),
        text_threshold=float(settings.clipper_text_similarity_threshold),
        target_count=target_count,
    )
    winners = [c for c in ranked if not c.get("is_alternative")]
    min_score = float(cfg.get("min_score") or 0)
    if min_score > 0:
        held = [c for c in winners if float(c.get("overall") or 0) >= min_score]
        # Never return an empty board because the threshold was set too high —
        # keep the best one and let the score speak for itself.
        winners = held or winners[:1]
    winners = winners[:target_count]

    # Everything dedupe kept but we did not select is an ALTERNATIVE, not a
    # winner. Without this the board shows every surviving candidate: a 6-hour
    # VOD produced 352 "winners" for a request of 8, because dedupe only marks
    # near-duplicates and the truncation above lives in a Python list that
    # _write_clips never sees. Re-flagging here keeps them retrievable behind
    # "show near-duplicates" instead of dropping them.
    chosen = {id(c) for c in winners}
    for cand in ranked:
        if id(cand) not in chosen:
            cand["is_alternative"] = True
            cand["rank_position"] = None

    _guard(queue, job_id)

    # ── Pass E (cheap half) + Pass D ────────────────────────────────────────
    await queue.update_progress(job_id, 0.70, "Preparing layouts")
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
                content_type=profile,
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

    _guard(queue, job_id)
    await _attach_headlines(winners, cfg, queue, job_id)

    # ── Persist ─────────────────────────────────────────────────────────────
    await queue.update_progress(job_id, 0.90, "Generating previews")
    await _write_clips(project_id, ranked, winners, profile)

    async with async_session() as session:
        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(status=ProjectStatus.ready.value)
        )
        await session.commit()

    queued = await _auto_export(project_id, cfg, queue)
    await queue.update_progress(
        job_id, 1.0,
        f"Rendering the top {queued}" if queued else "Ready for review")
    logger.info(
        f"clipper build for {project_id}: {len(winners)} candidates "
        f"({len(ranked) - len(winners)} alternatives), profile={profile}"
        + (f", auto-exporting {queued}" if queued else "")
    )


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
        await queue.enqueue(project_id=project_id,
                            job_type=JobType.clipper_export.value,
                            clip_id=clip_id)
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
    if cand.get("reasons"):
        out["reasons"] = [str(r) for r in cand["reasons"]][:12]
    for key in ("story", "variant", "llm_score", "llm_rank", "llm_verdict",
                "llm_reason", "llm_tag"):
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
            is_winner = id(cand) in winner_ids
            layout = cand.get("layout") if is_winner else None
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
                    content_type=profile,
                    layout_plan=layout,
                    caption_plan=cand.get("captions") if is_winner else None,
                    warnings=(layout or {}).get("warnings") or [],
                    reasoning=_reasoning_of(cand),
                    dedupe_group=cand.get("dedupe_group"),
                    is_alternative=bool(cand.get("is_alternative")),
                    rank_position=cand.get("rank_position"),
                    feature_vector=cand.get("features"),
                    ranker_version=cand.get("ranker_version"),
                    status=ClipStatus.candidate.value,
                )
            )
        await session.commit()
