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

import logging
from pathlib import Path

from sqlalchemy import update

from config import settings
from database import async_session
from models import ClipModel, ClipStatus, ProjectModel
from services.clipper import storage

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


async def handle_export(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    """Full-quality 1080x1920 deliverable."""
    import asyncio

    from services.clipper.render import _has_audio, render_clip

    if not clip_id:
        raise RuntimeError("export job started without a clip id")

    clip, project = await _load(clip_id)
    src = _source_path(project)
    paths = storage.paths(project_id)

    await queue.update_progress(job_id, 0.05, "Rendering export")

    decision = await _decide_render(
        clip, project, paths["exports_dir"],
        on_stage=lambda p, m: queue.update_progress(job_id, p, m))
    cfg = decision["cfg"]
    drop = decision["drop"]
    plan = decision["plan"]
    dyn = decision["dyn"]
    fps = decision["fps"]
    caption_y = decision["caption_y"]
    ass_path = decision["ass_path"]

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
        finally:
            dyn.pop("_review_faces", None)
            dyn.pop("_panels", None)

    out = storage.export_path(project_id, clip.id)
    try:
        if dyn:
            from services.clipper import dynamic_render

            await queue.update_progress(
                job_id, 0.20, f"Rendering {len(dyn['shots'])} shots")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: dynamic_render.render_dynamic_clip(
                    src, dyn, str(out), start=float(clip.start_time or 0.0),
                    work_dir=paths["exports_dir"], ass_path=ass_path,
                    src_w=int(project.width or 1920),
                    src_h=int(project.height or 1080),
                    fps=fps, crf=int(settings.clipper_export_crf),
                    preset=settings.clipper_export_preset,
                    # The four the static call below has always had, and this
                    # one never did. `dynamic_edit` became the default on
                    # 2026-08-17, so the options were live on a path nothing
                    # took: the watermark vanished, `trim_silence` did nothing,
                    # and — because `_write_ass` above applies `drop` either
                    # way — the captions were shifted for cuts that were never
                    # made.
                    watermark=str(cfg.get("watermark_text") or ""),
                    drop_spans=drop,
                    has_audio=_has_audio(src),
                    is_cancelled=lambda: queue.is_cancelled(job_id)),
            )
        else:
            result = await render_clip(
                src,
                _candidate(clip),
                plan,
                ass_path,
                str(out),
                fps=fps,
                crf=int(settings.clipper_export_crf),
                preset=settings.clipper_export_preset,
                watermark=str(cfg.get("watermark_text") or ""),
                drop_spans=drop,
                on_progress=lambda p, m: queue.update_progress(job_id, 0.05 + 0.9 * p, m),
                is_cancelled=lambda: queue.is_cancelled(job_id),
            )
    except Exception:
        async with async_session() as session:
            await session.execute(
                update(ClipModel).where(ClipModel.id == clip.id).values(
                    status=ClipStatus.failed.value
                )
            )
            await session.commit()
        raise

    # Pass D's second half, and the only part of the pipeline that spends money.
    # It runs AFTER the encode where the local half runs before it, and the
    # asymmetry is deliberate: the local half can still change the caption
    # position, so arriving early is worth something; this one is advisory, so
    # it is worth more seeing exactly what ships — captions burned, crop
    # applied — than a reconstruction of it.
    if review_result is not None and bool(
            cfg.get("vision_review", settings.clipper_vision_review)):
        try:
            review_result = await _vision_review(clip, cfg, out, review_result)
        except Exception:
            logger.warning("clip %s: vision review failed", clip.id, exc_info=True)

    # A sidecar with everything needed to reproduce this file — source
    # timestamps, scores, the layout and caption plans, and the model versions
    # that produced them (brief §25).
    sidecar = out.with_suffix(".json")
    storage.atomic_write_json(
        sidecar,
        {
            "clip_id": clip.id,
            "project_id": project_id,
            "source": {"path": src, "url": project.source_url},
            "source_start": clip.start_time,
            "source_end": clip.end_time,
            "duration": clip.duration,
            "title": clip.title,
            "headline": clip.headline_text,
            "transcript": clip.transcript_text,
            "overall_score": clip.overall_score,
            "sub_scores": clip.sub_scores,
            "score_reason": clip.score_reason,
            "layout_plan": plan,
            # Present only when the multi-shot path rendered this file. The
            # static layout_plan above is still written either way, because
            # it is what a re-render falls back to.
            "dynamic_plan": dyn,
            # Pass D's verdict on this exact cut. Written whether or not it
            # found anything: "APPROVE, twelve frames sampled" is a fact
            # about the file, and an absent key would be ambiguous between
            # "clean" and "never reviewed".
            "review": review_result,
            "caption_plan": clip.caption_plan,
            "content_type": clip.content_type,
            "analysis_version": project.analysis_version,
            "ranker_version": clip.ranker_version,
            "render": {"fps": fps, "crf": settings.clipper_export_crf,
                       "preset": settings.clipper_export_preset},
        },
        indent=2,
        ensure_ascii=False,
        default=str,
    )

    async with async_session() as session:
        # The review goes on the row as well as the sidecar. Sidecar-only was
        # how it shipped first, and nothing in the API or the UI could read a
        # file on disk — which is this repo's oldest failure, a structure
        # nobody reads, committed again on the day it was warned about.
        await session.execute(
            update(ClipModel)
            .where(ClipModel.id == clip.id)
            .values(status=ClipStatus.exported.value, export_path=str(out),
                    review=review_result)
        )
        await session.commit()

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
