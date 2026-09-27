"""One full-quality execution and sidecar writer for exports and video probes.

Planning, paid reviews, job state and feedback belong to callers. A resolved
render decision comes here; both renderer branches use the SAME options that
are recorded beside the resulting file. Probe callers choose another output
directory, never another implementation of the export recipe.

Legacy sidecars remain untouched. New runs explicitly declare fingerprint v3.
A recipe match describes the recorded inputs, not visual/editorial quality.
"""
from __future__ import annotations

import asyncio
import threading
from copy import deepcopy
from pathlib import Path

from config import settings
from services.clipper import storage

EXPORT_W, EXPORT_H = 1080, 1920

def _size_bytes(path: str | Path) -> int | None:
    """The source file's size, or None when it cannot be read.

    Part of the render fingerprint: two runs against the same path are only the
    same input if the file behind it has not been replaced. None rather than 0,
    because a source that is gone and a source that is empty are different
    facts and the audit refuses to spell absence as a measurement.
    """
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


async def render_export(clip, project, decision: dict, out: str | Path, *,
                        src: str, review_result=None, after_render=None,
                        on_progress=None, is_cancelled=None,
                        discard_on_cancel=False, destination=None, attempt=None) -> dict:
    """Execute a resolved decision, then atomically write its v3 sidecar.

    The source-caption gate runs on the decision about to be encoded, whatever
    the caller (SC-addendum-v2 §4): a treatment is executed from the mask loaded
    now, into `out` only when `destination="versioned"` and never over an
    export, or refused before the encode. `attempt` (`{job_id, nonce}`) names
    this attempt's patch and manifest; a fresh nonce when omitted.

    `after_render` lets the job attach its advisory review of the actual file;
    the callback owns review policy and failures. Probes omit it. Neither path
    writes a sidecar for a failed encode. The caller owns DB state and feedback.

    `discard_on_cancel`: for a caller whose `out` is its own staging name. A
    cancel stops this coroutine, not the dynamic encoder's thread, which then
    creates `out` after the caller's cleanup ran; the thread removes it itself.
    Off by default — probes and the re-render scripts write the real path.
    """
    from services.clipper import dynamic_render, render as static_render

    decision = deepcopy(decision)
    dyn = decision["dyn"]
    if dyn:
        # These are working observations for Pass D and the shadow proposals,
        # not renderer inputs. Clean ALL callers, not only the normal worker;
        # otherwise probes fingerprint private tracks no export records.
        for key in ("_review_faces", "_panels", "_stable_track", "_motion",
                    "_motion_hop", "_rhythm", "_face_space"):
            dyn.pop(key, None)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    render = {"fps": int(decision["fps"]),
              "crf": int(settings.clipper_export_crf),
              "preset": settings.clipper_export_preset,
              "watermark": decision["watermark"],
              "out_w": EXPORT_W, "out_h": EXPORT_H}
    # Frozen-plan replay carries the encoder options of that recipe. Ordinary
    # jobs/probes use the defaults above. In both cases this SAME dictionary is
    # passed to the encoder and stored; no caller builds a second command.
    render.update(decision.get("render") or {})
    from services.clipper import source_treatment_render as treat

    # None, THIS attempt's verified patch, or a refusal before any encode. Only
    # the dynamic path can hold a patch: the gate refuses a static treatment.
    patch = await asyncio.to_thread(treat.prepare, clip, project, decision, src=src, out=out,
                                    destination=destination, attempt=attempt)
    if dyn:
        abandoned = threading.Event()

        def encode(render_dynamic_clip, *args, **kwargs):
            try:
                return render_dynamic_clip(*args, **kwargs)
            finally:
                treat.discard(patch)
                # Set only after the await was cancelled; a file finished
                # before that is still there for the caller's own cleanup.
                if abandoned.is_set():
                    # `render_dynamic_clip` names its sendcmd `{work_dir}/{stem}.cmd.txt`.
                    for path in (out, out.with_suffix(".cmd.txt")):
                        path.unlink(missing_ok=True)

        try:
            result = await asyncio.to_thread(
                encode, dynamic_render.render_dynamic_clip,
                src, dyn, str(out), start=float(clip.start_time or 0.0),
                work_dir=out.parent, ass_path=decision["ass_path"],
                src_w=int(project.width or 1920),
                src_h=int(project.height or 1080),
                watermark=render["watermark"], drop_spans=decision["drop"],
                has_audio=static_render._has_audio(src),
                is_cancelled=is_cancelled,
                fps=render["fps"], crf=render["crf"], preset=render["preset"],
                out_w=render["out_w"], out_h=render["out_h"], source_patch=patch)
        except asyncio.CancelledError:
            if discard_on_cancel:
                abandoned.set()
            raise
    else:
        from workers.clipper_render_plan import _candidate

        result = await static_render.render_clip(
            src, _candidate(clip), decision["plan"], decision["ass_path"], str(out),
            fps=render["fps"], crf=render["crf"], preset=render["preset"],
            out_w=render["out_w"], out_h=render["out_h"],
            watermark=render["watermark"], drop_spans=decision["drop"],
            on_progress=on_progress, is_cancelled=is_cancelled, source_patch=patch)
    if after_render is not None:
        review_result = await after_render(out, review_result)
    body = _write_sidecar(clip, project, decision, out, src=src, dyn=dyn,
                          render=render, result=result, review_result=review_result,
                          patch=patch)
    return {**result, "sidecar": body}


def _write_sidecar(clip, project, decision, out, *, src, dyn, render,
                   result, review_result, patch=None):
    from services.clipper import (dynamic_render, edit_quality, output_identity,
                                  render as static_render, render_input,
                                  source_treatment_render)

    plan, drop, caption_y = decision["plan"], decision["drop"], decision["caption_y"]
    source = {"path": src, "url": project.source_url, "size_bytes": _size_bytes(src)}
    body = {
        "clip_id": clip.id,
        "project_id": project.id,
        # The selection trace that produced this clip. This labels provenance;
        # it is not part of the image recipe and therefore stays outside the
        # render fingerprint. NULL is honest for clips created before S7f.
        "selection_run_id": clip.selection_run_id,
        # WHICH renderer made this file. The two paths produce different videos
        # from the same plan, so one constant for both would file a static
        # export under a grammar of shots it never had.
        "render_version": (dynamic_render.RENDER_VERSION if dyn
                           else static_render.RENDER_VERSION),
        "source": source,
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
        # The plan as STORED, plus the two things the export decided about it.
        # Not a pre-merged "effective" plan: merging here would put a second
        # copy of `_write_ass`'s rule in this file, and the merged result cannot
        # be taken apart again by anything that needs to know what was decided
        # at score time and what was decided at render time.
        # Since D2 an alternative's plan may be BUILT for this render and never
        # stored; the decision carries it, and `caption_plan_state.origin` says
        # which it was. Callers that decide without it fall back to the row.
        "caption_plan": decision.get("caption_plan", clip.caption_plan),
        # How the render came by that plan, and — for `unavailable` — why a
        # burn produced no captions (`reason`). Labels only, not fingerprinted.
        "caption_plan_state": decision.get("caption_plan_state"),
        # The height actually burned after UI/face placement; `null` when the
        # stored position stood.
        "caption_y": caption_y,
        # Placement evidence is advisory; effective caption_y is fingerprinted.
        "caption_face_placement": decision.get("caption_face_placement"),
        "content_type": clip.content_type,
        # What grammar this clip WOULD be cut with, why, and whether the mode in
        # force actually applied it. Recorded on every export since R2 so the
        # profile can be compared against the delivered edit without changing it.
        "edit_profile": decision["edit_profile"],
        # R3a: the composition the creator's own presence would have chosen,
        # beside the one that shipped. Recorded on every export, applied on
        # none — the difference is the measurement.
        "creator_view": decision["creator_view"],
        # R3b: what each stretch is, and the gap between how many regime
        # boundaries exist and how many the viewer would see. Recorded, applied
        # to nothing — forcing a cut on every regime change would put back the
        # 116 invisible cuts R1 removed.
        "regime_view": decision["regime_view"],
        # R4: which boundaries would earn a cut and why, against the §4 band for
        # this profile. The band is compared, never enforced — a proposal padded
        # to reach a guardrail would make the guardrail unfalsifiable.
        "rhythm_view": decision["rhythm_view"],
        "analysis_version": project.analysis_version,
        "ranker_version": clip.ranker_version,
        # WHETHER A CAPTION LAYER WAS BURNED AT ALL, and who decided. A sidecar
        # that simply has no `.ass` beside it cannot distinguish "the source
        # already carries captions so we added none" from "the file was cleaned
        # up" — and that ambiguity is exactly what `own_caption_layer` had to
        # stop answering `False` to.
        "caption_policy": decision["caption_policy"],
        # AND WHETHER THE SOURCE HAD A SECOND REGION AT ALL. A plan that never
        # chose the second camera and a source that never had one produce the
        # same shot list, so without this a later reader cannot tell a clip
        # framed on one camera by decision from one framed that way by chance.
        "layout_policy": decision["layout_policy"],
        # The dead seconds this render removed. Without them the sidecar
        # describes a longer clip than the file: every downstream time —
        # captions, shot boundaries — is on a clock the mp4 does not keep.
        "drop_spans": drop,
        "render": render,
    }

    # Computed from the sidecar itself, through the ONE projection the audit
    # uses to recheck it. Building a separate payload here is how a fingerprint
    # stops meaning anything: the two definitions drift, and the check passes
    # for a file whose plan has changed underneath it.
    # WHAT THE CALL ACTUALLY CARRIED, before the fingerprint, because v2 covers
    # the CONTENT of the `.ass` that was burned — an input that changes the
    # picture. The paths and the argv digest inside it are details of one run
    # and `render_input.caption_identity` leaves them out; two renders of the
    # same recipe write the subtitle file to two temporary names and must still
    # fingerprint the same.
    body["render_record"] = (result or {}).get("render_record")
    # WHAT WAS DONE TO THE SOURCE'S OWN TEXT. The label (who decided) is not
    # fingerprinted; the identity is, and it comes from the record — what the
    # encode consumed — never from the decision (SC-addendum-v2 §5–§6).
    body["source_treatment"] = source_treatment_render.label(decision)
    body["source_treatment_identity"] = source_treatment_render.sidecar_identity(
        body["render_record"], patch)
    # AND THE SCHEMA IS DECLARED. A record with no such field is read with the
    # v1 formula under an assumption that is reported; from here the assumption
    # is not needed, and a v3 mismatch may never be rescued by v2 or v1.
    body["fingerprint_schema"] = render_input.FINGERPRINT_SCHEMA_V3
    body["input_fingerprint"] = edit_quality.input_fingerprint(
        body, schema=render_input.FINGERPRINT_SCHEMA_V3)
    # AND THE OTHER HALF, which the recipe cannot reach. `input_fingerprint`
    # proves the plan was not edited after the render; it never touches the mp4,
    # so `provenance_complete` had no route to a pass at all. This measures what
    # came out — digest, bytes, width/height — independently of the dimensions
    # now passed explicitly in `render`. Requested geometry and measured output
    # are separate facts; agreement cannot be inferred from the recipe.
    #
    # AFTER the fingerprint, deliberately: the recipe digest must not depend on
    # the output, or a re-render of the same plan would change its own recipe.
    body["output_identity"] = output_identity.probe(out)

    storage.atomic_write_json(
        out.with_suffix(".json"), body, indent=2, ensure_ascii=False, default=str)

    return body
