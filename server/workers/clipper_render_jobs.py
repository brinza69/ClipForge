"""
ClipForge — AI Stream Clipper: the export and preview jobs themselves.

Both handlers run ONE ffmpeg encode and differ only in resolution, quality and
whether the result is a deliverable. WHAT they encode is decided in
`clipper_render_plan._decide_render`, which they both call — they used to
decide separately, and the editor previewed a static split screen for a clip
that shipped with nineteen cuts.

Pass D lives here rather than in the plan module because it judges a decision
that has already been made, and because it is the only part of this path that
can spend money.

Why the caption ASS is rebuilt at render time instead of stored: the user can
edit the transcript, the preset, the position or the crop between analysis and
export, and a stale .ass on disk would silently ship the pre-edit captions.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select, update

from config import settings
from database import async_session
from models import ClipModel, ClipStatus, JobModel, JobStatus, ProjectModel
from services.clipper import storage
from services.clipper.clip_mutations import begin_write
from workers.clipper_render_output import _size_bytes  # noqa: F401 — compatibility

# Re-exported, not just imported: `_decide_render` and friends were defined in
# this module until the 2026-08-18 split, and the tests that pin today's fixes
# reach for them through it.
from workers.clipper_render_plan import (  # noqa: F401
    _candidate,
    _caption_y,
    _clip_words,
    _dead_spans,
    _decide_render,
    _dynamic_plan,
    _layout_plan,
    _load,
    _plan_fits,
    _regions_for,
    _source_path,
    _write_ass,
)

logger = logging.getLogger("clipforge.clipper.render")

async def _review(clip: ClipModel, project: ProjectModel, plan: dict,
                  caption_y: float | None = None) -> dict:
    """Pass D over the planned cut. Advisory: it reports, it does not block.

    Whether a REJECT should stop an export is a product decision nobody has
    made, and a reviewer that silently swallowed clips would be a worse failure
    than the ones it catches. So the verdict is recorded on the sidecar and
    logged, and the export proceeds.
    """
    import asyncio

    from services.clipper import review as review_mod

    paths = storage.paths(project.id)
    caption_plan = clip.caption_plan
    if caption_y is not None and caption_plan:
        caption_plan = {**caption_plan, "y_pct": caption_y}
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: review_mod.review_plan(
            paths["proxy"], plan, caption_plan,
            clip_start=float(clip.start_time or 0.0),
            faces=plan.get("_review_faces") or (),
            panels=plan.get("_panels") or (),
            src_w=int(project.width or 1920)),
    )


def _what_happens(clip: ClipModel) -> str:
    """What the clip is about, in the most event-like words available.

    The order matters and was set by watching the first real answer. Handed the
    headline — which is extracted FROM the transcript — the model reviewed text
    against text: "captions do not show the stated phrase". That is not the
    question. The story engine's `why` describes the moment as something that
    HAPPENS, which is what a picture can be compared against.

    The headline is the fallback rather than the default because it is what
    exists when `llm_select` is off, which is most of the time.
    """
    story = ((clip.reasoning or {}).get("story") or {}) if isinstance(clip.reasoning, dict) else {}
    for candidate in (story.get("why"), clip.headline_text, clip.title,
                      clip.transcript_text):
        text = " ".join(str(candidate or "").split())
        if len(text) >= 8:
            return text
    return ""


async def _vision_review(clip: ClipModel, cfg: dict, rendered: Path,
                         review: dict) -> dict:
    """Ask a vision model about the finished file and fold the answer in.

    Returns the review unchanged when no key is configured. That is not a
    failure worth logging loudly: the switch can be on in a project's settings
    long before anyone pastes a key, and an export must not care.
    """
    from services.clipper import review_vision
    from services.transcript_cleaner import get_openai_key

    key = get_openai_key()
    if not key:
        return review

    about = _what_happens(clip)
    result = await review_vision.review_rendered(
        rendered, about,
        model=str(cfg.get("vision_model") or settings.clipper_vision_model),
        api_key=key,
        frames=int(cfg.get("vision_frames") or settings.clipper_vision_frames),
    )
    merged = review_vision.merge(review, result)
    usage = result.get("usage") or {}
    if result["findings"] or usage:
        logger.info("clip %s: vision review %s — %d finding(s), %s in / %s out",
                    clip.id, result.get("model"), len(result["findings"]),
                    usage.get("prompt_tokens"), usage.get("completion_tokens"))
    return merged


def _job_origin(metadata: object) -> str:
    """Who asked for this render, from the job that carries it.

    An UNSTAMPED job is `system`, which is neutral for training — NOT `manual`.
    Manual was the first answer here and it was wrong in the one direction that
    costs something: today only `_auto_export` enqueues on its own, so a missing
    stamp really did mean a person, but the next piece of automation that
    enqueues an export and forgets to stamp it would have every clip it rendered
    filed as human approval, silently. Every caller that means `manual` now says
    so, and a missing stamp costs a label instead of inventing one.

    The same argument is written out in `feedback.record`, which is why `origin`
    is a required argument there.
    """
    from services.clipper import feedback

    asked = (metadata or {}).get("origin") if isinstance(metadata, dict) else None
    return str(asked) if asked in feedback.ORIGINS else feedback.ORIGIN_SYSTEM


async def _publish_export(job_id: str, clip_id: str, owner: str, staged: Path, out: Path,
                          review, ass: str | None = None) -> bool:
    """Move this attempt's file, sidecar and burned `.ass` into place and mark the row
    exported — only while it is the clip's CURRENT attempt (`export_job_id`),
    the clip is still `exporting`, and the job still runs under THIS worker.

    All under SQLite's write lock: a new submit (which moves `export_job_id`)
    and every other publish take the same lock, so the check and the renames
    cannot interleave with them. An UPDATE guarded the same way would not be
    enough on its own — the file is replaced by a rename, not by the row.
    A crash between the renames and the commit leaves THIS attempt's file with
    an `exporting` row that fail/recovery then release; never an older file.

    THE THREE RENAMES ARE NOT ATOMIC WITH EACH OTHER OR WITH THE COMMIT, and no
    transaction spans the filesystem and SQLite. A failure or crash after the
    first can leave, beside the export: a new mp4 with the previous sidecar and
    `.ass`, or a new mp4 and sidecar with the previous `.ass`. The row then
    still reads `exporting` (nothing committed), and fail/cancel/recovery move
    it to `failed` or keep it `exporting` for a retry — never to `exported`,
    with `export_path` still naming this file. So `/export-file`, which serves
    only an `exported` clip, answers 409 `export_not_current` for that mixed
    set until a later attempt publishes all three (R4c; test_clipper_export_current_r).
    """
    async with async_session() as session:
        await begin_write(session)
        current = await session.scalar(
            select(ClipModel.id)
            .join(JobModel, JobModel.id == ClipModel.export_job_id)
            .where(ClipModel.id == clip_id, ClipModel.export_job_id == job_id,
                   ClipModel.status == ClipStatus.exporting.value,
                   JobModel.status == JobStatus.running.value, JobModel.worker_id == owner))
        if current is None:
            await session.rollback()
            return False
        os.replace(staged, out)
        os.replace(staged.with_suffix(".json"), out.with_suffix(".json"))
        # The `.ass` beside the export is read as "what was burned"
        # (caption_corpus, rerender_pilots). Without captions the old one stays,
        # as it always has; `caption_corpus` skips it when the record's
        # `caption_filter` is False (R4c).
        if ass:
            os.replace(ass, out.with_suffix(".ass"))
        # The review goes on the row as well as the sidecar. Sidecar-only was
        # how it shipped first, and nothing in the API or the UI could read a
        # file on disk — which is this repo's oldest failure, a structure
        # nobody reads, committed again on the day it was warned about.
        await session.execute(
            update(ClipModel)
            .where(ClipModel.id == clip_id)
            .values(status=ClipStatus.exported.value, export_path=str(out), review=review))
        await session.commit()
    return True


async def handle_export(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    """Full-quality 1080x1920 deliverable."""
    from workers.clipper_render_output import render_export

    if not clip_id:
        raise RuntimeError("export job started without a clip id")

    clip, project = await _load(clip_id)
    src = _source_path(project)

    await queue.update_progress(job_id, 0.05, "Rendering export")

    out = storage.export_path(project_id, clip.id)
    # THIS attempt's own files (R4b). A cancel from the other backend or a lost
    # lease does not stop an attempt already running, so two can be at work at
    # once. Every file the encode reads or writes is this attempt's: the mp4 and
    # sidecar at `staged`, the dynamic path's `.cmd.txt` beside it, and the
    # caption `.ass` in the `scratch` directory — the decide writes `{clip}.ass`
    # into whatever directory it is handed, and a shared one let a superseded
    # attempt's late decide replace the captions the current one then burned
    # (review F2). Only `_publish_export` moves them into place. A failed render
    # no longer marks the clip here: fail/cancel do, and only for the current attempt.
    staged = out.with_name(f".{out.stem}.{job_id}-{uuid.uuid4().hex[:8]}{out.suffix}")
    scratch = staged.with_suffix("")
    try:
        decision = await _decide_render(
            clip, project, scratch,
            on_stage=lambda p, m: queue.update_progress(job_id, p, m))
        cfg = decision["cfg"]
        dyn = decision["dyn"]
        caption_y = decision["caption_y"]

        # Pass D. It runs BEFORE the encode on purpose: a finding that arrives after
        # a 12-24s render can only be reported, one that arrives before it can be
        # acted on. Only the multi-shot path is reviewed, because that is the path
        # that makes visual decisions nothing else checks.
        review_result = None
        if dyn:
            await queue.update_progress(job_id, 0.15, "Reviewing the cut")
            try:
                review_result = await _review(clip, project, dyn, caption_y)
                if review_result["findings"]:
                    logger.info("clip %s: review says %s — %s", clip.id,
                                review_result["verdict"],
                                "; ".join(f["detail"] for f in review_result["findings"][:3]))
            except Exception:
                # Advisory, and it stays advisory: a reviewer that crashes must not
                # cost the export it was meant to improve.
                logger.warning("clip %s: review failed", clip.id, exc_info=True)

        async def review_rendered(out, review):
            # Paid, advisory review stays a job concern. It sees the encoded file;
            # a provider failure cannot erase the local findings or lose the export.
            if review is not None and bool(
                    cfg.get("vision_review", settings.clipper_vision_review)):
                try:
                    return await _vision_review(clip, cfg, out, review)
                except Exception:
                    logger.warning("clip %s: vision review failed", clip.id, exc_info=True)
            return review

        await queue.update_progress(job_id, 0.20, "Rendering export")
        result = await render_export(
            clip, project, decision, staged, src=src, review_result=review_result,
            after_render=review_rendered,
            on_progress=lambda p, m: queue.update_progress(job_id, 0.20 + 0.7 * p, m),
            is_cancelled=lambda: queue.is_cancelled(job_id), discard_on_cancel=True)
        review_result = result["sidecar"]["review"]
        ass = decision.get("ass_path")
        if ass:
            # `ass_path` / `ass_path_offered` stay what the encode EXECUTED: this
            # attempt's scratch copy, which no longer exists once published — that
            # is expected, not a missing file (Codex Q3 rejected renaming them).
            # The published file is a SEPARATE key, written only when the argv
            # really read this attempt's `.ass`; the publish is a rename, so the
            # record's `ass_sha256` is the digest of both. A render whose argv has
            # no filter, or names another file, consumed no `.ass` of ours: none
            # is claimed and none is published beside it (R4c).
            side = json.loads(staged.with_suffix(".json").read_text(encoding="utf-8"))
            record = side.get("render_record")
            if (isinstance(record, dict) and record.get("caption_filter") is True
                    and record.get("ass_path")
                    and Path(record["ass_path"]).resolve() == Path(ass).resolve()):
                record["ass_published_path"] = str(out.with_suffix(".ass"))
                storage.atomic_write_json(staged.with_suffix(".json"), side, indent=2,
                                          ensure_ascii=False, default=str)
            else:
                ass = None
        if not await _publish_export(job_id, clip.id, queue.worker_id, staged, out,
                                     review_result, ass):
            raise RuntimeError(f"export {job_id} is no longer clip {clip.id}'s current "
                               "export; its render was discarded")
    finally:
        for leftover in (staged, staged.with_suffix(".json"), staged.with_suffix(".cmd.txt")):
            leftover.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)

    from services.clipper import feedback

    async with async_session() as session:
        await feedback.record(
            session, clip.id, project_id, "exported",
            {"path": str(out), "size": result.get("size")},
            origin=_job_origin(metadata),
        )

    await queue.update_progress(job_id, 1.0, "Completed")
    logger.info(f"clipper export {clip.id}: {result.get('size', 0) // 1024} KB → {out.name}")


async def handle_preview(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    """Fast, low-resolution proxy render for the editor.

    Deliberately never marks the clip exported and never writes a sidecar — a
    preview is scratch, and treating it as a deliverable would pollute both the
    export list and the feedback labels.
    """
    import asyncio

    from services.clipper.render import _has_audio, render_preview

    if not clip_id:
        raise RuntimeError("preview job started without a clip id")

    clip, project = await _load(clip_id)
    src = _source_path(project)
    paths = storage.paths(project_id)

    await queue.update_progress(job_id, 0.10, "Generating previews")

    # The SAME decision the export will make. It used to take the static
    # renderer unconditionally, so with `dynamic_edit` on — the default since
    # 2026-08-17 — the editor showed a fixed split screen for a clip that ships
    # with a dozen cuts, and ignored the trim, the watermark and the resolved
    # caption height as well.
    decision = await _decide_render(clip, project, paths["previews_dir"])
    out = storage.preview_path(project_id, clip.id)

    if decision["dyn"]:
        from services.clipper import dynamic_render

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: dynamic_render.render_dynamic_preview(
                src, decision["dyn"], str(out),
                start=float(clip.start_time or 0.0),
                work_dir=paths["previews_dir"], ass_path=decision["ass_path"],
                src_w=int(project.width or 1920),
                src_h=int(project.height or 1080),
                watermark=decision["watermark"],
                drop_spans=decision["drop"],
                has_audio=_has_audio(src),
                is_cancelled=lambda: queue.is_cancelled(job_id)),
        )
    else:
        await render_preview(src, _candidate(clip), decision["plan"],
                             decision["ass_path"], str(out),
                             watermark=decision["watermark"],
                             drop_spans=decision["drop"])

    async with async_session() as session:
        await session.execute(
            update(ClipModel).where(ClipModel.id == clip.id).values(preview_path=str(out))
        )
        await session.commit()

    from services.clipper import feedback

    async with async_session() as session:
        await feedback.record(session, clip.id, project_id, "previewed", None,
                              origin=_job_origin(metadata))

    await queue.update_progress(job_id, 1.0, "Preview ready")
