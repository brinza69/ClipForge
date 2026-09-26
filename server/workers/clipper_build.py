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
from services.clipper import ANALYSIS_VERSION
from services.clipper import feedback as feedback_mod
from services.clipper import reasoning_mode, reasoning_trace, selection, storage
from services.clipper import story_evidence
from services.clipper.serialize import effective_content_type
# What a previous run left on disk, and whether it can be trusted. Split out
# when this file crossed 500 lines; re-exported because the tests and the
# runbook reach for these by their old names.
# Running the judge over pools of moments. Split out when this file crossed
# 500 lines; re-exported because the tests reach for these by their old names.
from workers import clipper_scoring
from workers.clipper_judging import _judge_pool, _judge_rounds  # noqa: F401
from workers.clipper_cache import (  # noqa: F401
    _anchor_stamp, _atoms_stamp, _base_stamp, _cache, _cached,
    _episodes_stamp, _promises_stamp, _reasoning_mode, _segment_types,
    _threads_stamp,
)
from workers.clipper_boundary_pass import _record_completeness, refine_off_loop
logger = logging.getLogger("clipforge.clipper.build")

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
    from services.clipper import anchor_identity
    from services.clipper import captions as cap_mod
    from services.clipper import dedupe as dedupe_mod
    from services.clipper import layout as layout_mod
    from services.clipper import atoms as atoms_mod
    from services.clipper import episodes as episodes_mod
    from services.clipper import llm_select
    from services.clipper import promises as promises_mod
    from services.clipper import segmentation
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

    # Bound here, not in the story branch: `refine_boundaries` reads them on
    # EVERY path, and the legacy path never enters that branch.
    atoms: list[dict] = []

    min_s = float(cfg.get("min_clip_s") or settings.clipper_min_clip_s)
    max_s = float(cfg.get("max_clip_s") or settings.clipper_max_clip_s)
    target_count = int(cfg.get("clip_count") or settings.clipper_default_clip_count)

    # Everything this run did, written to disk at the end. Created here so the
    # settings it snapshots are the ones the run actually used, not the ones a
    # later reader assumes from the defaults — the gap that made two story runs
    # unusable as evidence (see reasoning_trace.py).
    trace = reasoning_trace.RunTrace(
        project_id,
        mode=_reasoning_mode(cfg),
        settings_snapshot=cfg,
        launched_by=str((metadata or {}).get("launched_by") or "worker"),
    )
    trace.note_versions(
        analysis=ANALYSIS_VERSION,
        anchor_identity=anchor_identity.ANCHOR_ID_VERSION,
        anchor_prompt=llm_select.ANCHOR_PROMPT_VERSION,
        dedupe_score=dedupe_mod.DEDUPE_SCORE_VERSION,
        judge_prompt=llm_select.JUDGE_PROMPT_VERSION,
        content_profile=profile,
        duration=round(duration, 1),
    )

    transcript = await _fetch_transcript(project_id)
    cache_base = _base_stamp(project_id, transcript)
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
        seg_types = _segment_types(project_id, duration, transcript,
                                   base=cache_base)

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
    mode = _reasoning_mode(cfg)
    if reasoning_mode.uses_llm(mode):
        await queue.update_progress(job_id, 0.25, "Reading the transcript")
        segments = transcript.get("segments") or []
        llm_timeout = float(settings.clipper_llm_timeout_s or 0.0)
        # Cancellation reaches INSIDE the model passes now. A four-hour source
        # is six chunks and up to three engines each; before this, pressing
        # cancel waited for every one of them.
        def cancelled() -> bool:
            return bool(queue.is_cancelled(job_id))

        # Bound before the try: the story branch assigns them and the
        # nomination branch does not, so the trace below and the refinement
        # further down would raise NameError on the very paths that have
        # neither. `atoms` is read by remeasure to resolve back-references.
        anchors: list[dict] = []
        try:
            if reasoning_mode.uses_story(mode):
                # The stream as units the reasoning can point at, each
                # carrying its own signals. Built from the transcript and the
                # Pass A series with no model involved — a 12-hour stream is
                # ~8,600 atoms and the cost rule forbids a call per two
                # seconds of video.
                atoms_stamp = _atoms_stamp(cache_base, sig)
                atoms = _cached(project_id, "atoms", atoms_stamp)
                if not isinstance(atoms, list):  # noqa: SIM108
                    atoms = atoms_mod.build(transcript, sig)
                    _cache(project_id, "atoms", atoms_stamp, atoms)
                # Setups that could pay off later, swept once over the whole
                # transcript and checkpointed. Anchor detection runs per chunk
                # with no memory across chunks, so without this a payoff that
                # lands on a prediction from an hour earlier is invisible.
                promises_stamp = _promises_stamp(
                    cache_base, duration, timeout=llm_timeout)
                promises_cache = _cached(project_id, "promises", promises_stamp)
                known = await promises_mod.detect(
                    segments, duration, trace=trace, timeout=llm_timeout,
                    is_cancelled=cancelled, chunk_cache=promises_cache,
                    checkpoint=lambda state: _cache(
                        project_id, "promises", promises_stamp, state))
                # Payoff first: each anchor carries what a viewer must already
                # know, so the window can open on the earliest required fact
                # rather than on the first audio spike.
                # Narrative arcs, by lexical chaining over the atoms — no model.
                threads_stamp = _threads_stamp(cache_base, atoms)
                arcs = _cached(project_id, "threads", threads_stamp)
                if not isinstance(arcs, list):
                    arcs = threads_mod.build(atoms)
                    _cache(project_id, "threads", threads_stamp, arcs)
                # `arcs` also becomes the rolling summary handed to later chunks.
                episodes_stamp = _episodes_stamp(cache_base, atoms, arcs)
                episodes = _cached(project_id, "episodes", episodes_stamp)
                if not isinstance(episodes, list):
                    episodes = episodes_mod.build(arcs, atoms)
                    _cache(project_id, "episodes", episodes_stamp, episodes)
                anchors_stamp = _anchor_stamp(
                    cfg, duration, base=cache_base, promises=known, atoms=atoms,
                    threads=arcs, episodes=episodes, timeout=llm_timeout)
                anchors_cache = _cached(project_id, "anchors", anchors_stamp)
                anchors = await llm_select.detect_anchors(
                    segments, duration, promises=known, atoms=atoms,
                    threads=arcs, episodes=episodes, trace=trace,
                    timeout=llm_timeout, is_cancelled=cancelled,
                    chunk_cache=anchors_cache,
                    checkpoint=lambda state: _cache(
                        project_id, "anchors", anchors_stamp, state))
                storage.write_artifact(project_id, "graph",
                                       threads_mod.edges(arcs, known, anchors))
                # Check each claim against the atoms it says it came from,
                # BEFORE the anchor becomes a window. Marks, never drops: see
                # story_evidence for why a first-version text matcher must not
                # double as a recall filter.
                anchors = [story_evidence.ground_anchor(a, atoms)
                           for a in (anchors or [])]
                trace.note_count(
                    "anchors_grounded",
                    sum(1 for a in anchors if a.get("payoff_grounded")))
                nominated = cand_mod.candidates_from_anchors(
                    anchors, transcript, sig, min_s=min_s, max_s=max_s,
                    duration=duration, atoms=atoms, threads=arcs)
            else:
                nominated = await llm_select.nominate(
                    segments, duration, trace=trace, timeout=llm_timeout,
                    is_cancelled=cancelled)
        except Exception as exc:
            logger.warning("LLM proposal failed; keeping the rule-based set",
                           exc_info=True)
            trace.note_error("propose", exc)
            nominated = []
        trace.note_count("anchors", len(anchors or []))
        trace.note_count("nominated", len(nominated or []))
        trace.note_stage("propose", "ok" if nominated else "empty",
                         f"mode={mode}")
        if nominated:
            raw = cand_mod.merge_nominations(
                raw, nominated, transcript, min_s=min_s, max_s=max_s,
                keep_overlaps=reasoning_mode.uses_story(mode))
        _guard(queue, job_id)

    # The ends are settled on what speech.wav shows (end_acoustics). A missing
    # or unreadable file reads as `unavailable` on each candidate and leaves
    # every end where the transcript rules put it. Off the event loop (EN2 R3):
    # the heartbeat shares it, and reading the audio takes minutes on a long VOD.
    refined = await refine_off_loop(
        raw, transcript, sig, min_s=min_s, max_s=max_s, atoms=atoms,
        audio_path=storage.paths(project_id)["audio"], queue=queue, job_id=job_id)
    _guard(queue, job_id)

    # R5, recorded and applied to nothing: is each window a COMPLETE thought,
    # and what would the one bounded repair be. `eligible` rides in
    # `candidates.json` and decides nothing — the plan gates it on this
    # validator passing the corpus first, because a rule that silently removes
    # moments has to be measured before it is trusted, not after.
    _record_completeness(refined, transcript, max_s=max_s, duration=duration)

    await queue.update_progress(job_id, 0.40, "Scoring candidates")
    model, use_learned = await clipper_scoring.use_learned_ranker()
    clipper_scoring.score_candidates(
        refined, transcript=transcript, signals=sig, duration=duration,
        profile=profile, platform=platform, seg_types=seg_types,
        overridden=bool(project.content_type_override),
        model=model, use_learned=use_learned)
    # The frontier pass, over the union. Blended into `overall`, so a failure
    # here costs the judgement, not the run.
    judged = False
    if reasoning_mode.uses_llm(_reasoning_mode(cfg)):
        await queue.update_progress(job_id, 0.48, "Judging candidates")
        judged = await _judge_rounds(refined, duration, target_count, cfg,
                                     trace, queue, job_id,
                                     project_id=project_id, cache_base=cache_base)
        _guard(queue, job_id)

    # Stamp the verdict status on the WHOLE field before anything is written or
    # dropped. Two reasons it belongs here and not inside the board rule:
    # `candidates.json` is written on this line and would otherwise carry no
    # status at all, and a tally taken later describes only the survivors — the
    # first version counted zero `not_selected_in_judged_pool` because every one
    # of the 60 the judge declined had already been filtered out of the list it
    # was counting.
    tally = selection.mark(refined)
    trace.note_stage("verdicts", "marked", ", ".join(
        f"{k}={v}" for k, v in sorted(tally.items())))

    storage.write_artifact(project_id, "candidates", refined)

    # The FULL post-judge field, kept for the selection trace. Everything below
    # this line removes candidates, and "why is this moment missing" is the
    # question the trace exists to answer — built from the survivors it could
    # only answer "how did the winners rank".
    field = list(refined)
    field_index = {id(c): i for i, c in enumerate(field)}
    eliminated: dict[int, str] = {}

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
    survived = {id(c) for c in refined}
    for cand in field:
        if id(cand) not in survived:
            eliminated[field_index[id(cand)]] = (
                reasoning_trace.ELIMINATED_ALREADY_EXPORTED)

    await queue.update_progress(job_id, 0.55, "Removing duplicates")
    ranked = dedupe_mod.deduplicate(
        refined,
        overlap_threshold=float(settings.clipper_overlap_threshold),
        text_threshold=float(settings.clipper_text_similarity_threshold),
        target_count=target_count,
        winner_scale=dedupe_mod.SELECTION_SCORE,
    )
    # `deduplicate` returns EVERY input — it marks `is_alternative` rather than
    # dropping, so this loop is expected to find nothing today. It is not dead
    # weight: a candidate that vanishes here means that contract changed, and
    # the alternative is for the trace to lose it in silence. Which of the two
    # a reader is looking at is exactly what `eliminated_by` answers.
    kept = {id(c) for c in ranked}
    for cand in field:
        index = field_index[id(cand)]
        if id(cand) not in kept and index not in eliminated:
            eliminated[index] = reasoning_trace.ELIMINATED_DEDUPED

    # THE RULE. When a judge ran, the board is drawn from the moments it chose,
    # not from the whole field ranked by a number that means different things
    # for different candidates. In shadow mode it is computed and recorded and
    # the legacy order still ships — that is what shadow means.
    # ONLY story_v2 orders the board with it. The first version read
    # `judged and not shadow`, which is inverted: it applied the rule in
    # story_v1 and llm_nominate — modes that never asked for it — and skipped it
    # in shadow, the one mode written for it. A user on story_v1 would have had
    # their board silently reordered by a rule that has not been compared
    # against legacy on anything.
    shadow = mode == reasoning_mode.STORY_V2_SHADOW
    # A judge that was asked and came back with nothing is a FAILURE, and the
    # plan asks for the whole field to return to the heuristic order when that
    # happens. A mode that simply does not use the rule is not a failure.
    picked = selection.board(
        ranked, want=target_count,
        judged=selection.rule_applies(mode, judged),
        min_score=float(cfg.get("min_score") or 0),
        revert=reasoning_mode.uses_llm(mode) and not judged)
    winners = picked["winners"]
    trace.note_stage(
        "board", "shadow" if shadow else ("judged" if judged else "legacy"),
        f"{len(winners)} winners, {picked['backfilled']} backfilled, "
        f"{picked['tally'][selection.SELECTED]} selectable")
    trace.note_count("board_backfilled", picked["backfilled"])
    if shadow:
        # The whole point of shadow: compute the v2 board and write down how it
        # differs, WITHOUT shipping it. Recorded here rather than derived later
        # because `is_alternative` and `rank_position` are rewritten below, and
        # a comparison made after that is a comparison with the legacy answer.
        would = selection.board(ranked, want=target_count, judged=judged)
        legacy_ids = [id(c) for c in winners]
        # STAMPED, not just counted. The count answers "did v2 disagree"; a
        # blind review answers "was it right", and that needs to know WHICH
        # moments v2 would have shipped. Written on the candidate so
        # `_write_clips` carries it to the row and the trace picks it up —
        # `rank_position` and `is_alternative` stay untouched, which is what
        # keeps shadow inert.
        for position, cand in enumerate(would["winners"], start=1):
            cand["shadow_rank"] = position
            # The rank belongs to the run that produced it. Without this a rank
            # read months later cannot be told apart from one a different
            # ordering produced, and the blind review would compare two boards
            # that never coexisted.
            cand["shadow_run_id"] = getattr(trace, "run_id", "")
        trace.note_stage(
            "board_v2", "would",
            f"{len(would['winners'])} winners, {would['backfilled']} backfilled, "
            f"{sum(1 for c in would['winners'] if id(c) not in legacy_ids)} "
            "differ from legacy")

    # Everything dedupe kept but we did not select is an ALTERNATIVE, not a
    # winner. Without this the board shows every surviving candidate: a 6-hour
    # VOD produced 352 "winners" for a request of 8, because dedupe only marks
    # near-duplicates and the truncation above lives in a Python list that
    # _write_clips never sees. Re-flagging here keeps them retrievable behind
    # "show near-duplicates" instead of dropping them.
    # The flag is CLEARED on winners as well as set on everything else. Dedupe
    # elects its leaders by `overall`, which the judge's verdict is blended
    # into, so a judged candidate can be flagged `is_alternative` before the
    # rule ever runs — and the rule exists precisely to rescue those. Leaving
    # the flag on meant `_write_clips` stored the rescued winner as an
    # alternative and it never reached the board at all.
    # `selection.board` has already numbered the winners; this only flags the
    # rest. Re-deriving the order here from `overall` would put the heuristic
    # back in charge of the one thing the rule exists to take away from it.
    selection.apply_board(ranked, winners)

    _guard(queue, job_id)

    # ── Pass E (cheap half) + Pass D ────────────────────────────────────────
    await queue.update_progress(job_id, 0.70, "Preparing layouts")
    # The shadow board's picks are planned TOO, and this is not a nicety. They
    # are `is_alternative` rows, so without it they carry `layout_plan: null`
    # and `caption_plan: null` — measured on `pilotf81b`, 69 of 69 alternatives.
    # A blind review that renders them next to legacy's picks would compare a
    # captioned, framed clip against an uncaptioned, unframed one and call the
    # difference selection quality. Planning both is what makes the comparison
    # about the choice.
    plan_winners(winners + [c for c in ranked
                            if c.get("shadow_rank") and c not in winners],
                 cfg, transcript, regions, faces, src_w, src_h, profile)

    _guard(queue, job_id)
    await _attach_headlines(winners, cfg, queue, job_id)

    # ── Persist ─────────────────────────────────────────────────────────────
    await queue.update_progress(job_id, 0.90, "Generating previews")
    await _write_clips(project_id, ranked, winners, profile, trace.run_id)
    _write_traces(project_id, trace, field, mode, eliminated)

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



# The half that carries the decision out — headlines, traces, `clips` rows and
# the unattended renders. Split when this file crossed 500 lines; re-exported
# because the tests, the runbook and `handle_score` above all reach for these
# by their old names.
from workers.clipper_finalize import (  # noqa: E402,F401
    _attach_headlines,
    _auto_export,
    plan_winners,
    _exported_spans,
    _reasoning_of,
    _write_clips,
    _write_traces,
    drop_moments_already_exported,
)
