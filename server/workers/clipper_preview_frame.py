"""Editor still from the saved export recipe, on the delivered timeline.

Uses the export planner and command builders, including sendcmd, crop/fit,
caption policy and pause removal. This is a fresh preview, not evidence that
an existing export was made from today's settings. No corpus file is changed.
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from services.clipper import dynamic_render, render
from services.clipper.dead_air import removed_seconds
from services.clipper.ffmpeg_tools import run
from workers import clipper_render_plan as planning


async def render_frame(clip, project, offset: float) -> dict:
    if not math.isfinite(offset) or offset < 0:
        raise ValueError("Frame time must be a finite, non-negative number.")
    src = planning._source_path(project)
    with TemporaryDirectory(prefix="clipper-frame-") as directory:
        work = Path(directory)
        decision = await planning._decide_render(clip, project, work)
        duration = max(0.1, float(clip.end_time - clip.start_time)
                       - removed_seconds(decision["drop"]))
        fps = int(decision["fps"])
        frame_index = min(math.ceil(offset * fps - 1e-9),
                          max(0, math.ceil(duration * fps - 1e-9) - 1))
        at = frame_index / fps
        png = await asyncio.to_thread(
            _render_frame, clip, project, decision, src, work, at)
        return {"png": png, "at": at, "duration": duration,
                "caption_action": decision["caption_policy"]["action"]}


def _render_frame(clip, project, decision, src, work: Path, at: float) -> bytes:
    from services.clipper import render_record, source_treatment_render

    out = work / "frame.png"
    # The same source-caption gate as `render_export`, on the decision about to
    # be drawn: the still shows the treatment the export would execute, or
    # refuses — it never shows the untouched source text instead.
    patch = source_treatment_render.prepare(clip, project, decision, src=src, scratch_root=work)
    kwargs = dict(fps=decision["fps"], crf=18, preset="medium",
                  out_w=1080, out_h=1920, watermark=decision["watermark"],
                  drop_spans=decision["drop"], has_audio=False, source_patch=patch)
    dyn = decision["dyn"]
    if dyn:
        w, h = int(project.width or 1920), int(project.height or 1080)
        commands = dynamic_render.write_sendcmd(dyn, w, h, work / "frame.cmd.txt")
        cmd = dynamic_render.build_dynamic_cmd(
            src, dyn, commands, decision["ass_path"], str(out),
            start=clip.start_time, duration=float(dyn["duration"]),
            src_w=w, src_h=h, **kwargs)
    else:
        cmd = render.build_render_cmd(
            src, planning._candidate(clip), decision["plan"], decision["ass_path"],
            str(out), **kwargs)
    # Process the prefix, then select on the export's shared CFR grid. Output -ss
    # alone re-phases that grid: at source start 386.47s / 60fps, the Speed
    # still requested at 10.5s matched MP4 frame 631 instead of frame 630.
    # sendcmd and libass must run BEFORE this sampling. Sampling source start+at
    # would lose both previous commands
    # and the caption clock after pause removal.
    cmd = cmd[:cmd.index("-c:v")]
    graph_index, map_index = cmd.index("-filter_complex") + 1, cmd.index("-map") + 1
    frame_index = round(at * decision["fps"])
    cmd[graph_index] += f";{cmd[map_index]}select='eq(n,{frame_index})'[vframe]"
    cmd[map_index] = "[vframe]"
    cmd += ["-frames:v", "1", "-an", "-fps_mode", "passthrough",
            "-c:v", "png", "-f", "image2", str(out)]
    # Refused exactly as the export is (codex-verdict-next-22 §1): the FINAL argv — after the frame
    # selection above — is corroborated against the manifest before it runs, and the patch is
    # hashed again after it ran, before any byte of the image is returned. `prepare` alone did not
    # catch a patch replaced after its manifest was written (next22-check/editor-patch-result.json).
    record = None
    if patch is not None:
        record = render_record.record(cmd, ass_path=decision["ass_path"], treatment_manifest=patch)
        render_record.require_corroborated(record)
    run(cmd, timeout=render.PREVIEW_TIMEOUT, what="clip editor frame")
    if record is not None:
        try:
            render_record.confirm_patch(record)
        except Exception:
            out.unlink(missing_ok=True)
            raise
    return out.read_bytes()
