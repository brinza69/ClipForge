"""
ClipForge — AI Stream Clipper: rendering a dynamic edit in ONE encode.

`dynamic_edit.plan_dynamic_edit` decides the shots; this module expresses them
as ffmpeg. The whole edit — every camera switch, every push-in, the shake, the
hit flashes, the caption burn — is a single `-filter_complex` over a single
libx264 pass, for the same reason `render.py` and
`remix_pipeline._stage_match_and_caption` fuse their stages: a second encode
would buy nothing and cost a generation of compression.

How the switching actually works (verified against ffmpeg 8.1 on this rig):

  * `crop`'s w/h/x/y are all runtime-settable (the `T` flag in `-h filter=crop`),
    so a `sendcmd` script can hard-switch the crop rectangle on an exact frame.
    Changing w/h reconfigures the link and the downstream `scale` follows.
  * crop's x/y EXPRESSIONS are re-evaluated per frame and can see `t`, so shake
    and drift cost no commands at all.
  * because x/y are written in terms of `out_w`/`out_h`, a size command alone
    re-centres the rectangle: stepping w/h across a shot IS the push-in.
  * `eq` with `eval=frame` takes expressions on brightness/saturation/contrast,
    which is where the hit flashes live.

Two hard constraints on every expression emitted here:

  * NO COMMAS. A comma separates filters in a filtergraph and arguments in a
    sendcmd entry, so `clip(v,lo,hi)` would silently truncate the graph. Range
    safety is therefore baked into the CONSTANTS instead (see `_anchor`).
  * every crop dimension stays even — H.264 with yuv420p refuses odd crops.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Sequence

from services.clipper.dynamic_geometry import (  # noqa: F401  (re-exported)
    COMPOSITIONS,
    canvas_size,
    _position_exprs,
    _size,
    _size_timeline,
    build_sendcmd,
    composition_of,
    write_sendcmd,
)
from services.clipper.ffmpeg_tools import (
    AUDIO_RATE,
    FFmpegError,
    LIMITER_CEILING,
    LOUDNESS_I,
    LOUDNESS_TP,
    escape_filter_path,
    even,
    ffmpeg_bin,
    loudness_chain,
    run,
)

logger = logging.getLogger("clipforge.clipper.dynamic_render")

__all__ = [
    "RENDER_VERSION",
    "build_sendcmd",
    "build_dynamic_filtergraph",
    "build_dynamic_cmd",
    "render_dynamic_clip",
]

#: What this renderer DOES, stamped on anything that evaluates its output.
#:
#: A blind review compares clips, and clips are the product of a selection AND a
#: render. When the render changes underneath, verdicts given on the old one are
#: not wrong — they are about a different video, and without a name for which
#: video they were about, they are unattributable. The first pilot's sessions
#: were stamped after the fact and one of them had to be marked invalid outright.
#:
#:   render_v1_dynamic_edit  — facecam+gameplay grammar applied to everything;
#:                             a 9:16 window on every sequence, and the crop
#:                             following the largest face cluster in each window
#:   render_v2_subject_aware — the whole frame when nobody is on screen, and the
#:                             source's fixed subject when it has one. SHIPPED
#:                             STRETCHED: `scale` fixed its output size when the
#:                             filter was configured, so every full-frame shot
#:                             was squeezed into 9:16 instead of letterboxed, and
#:                             four review sessions carried the same note
#:   render_v3_letterbox     — the same selection, with the geometry correct and
#:                             the bars filled with a blurred copy of the frame
RENDER_VERSION = "render_v3_letterbox"

#: How hard the letterbox fill is blurred. See `build_dynamic_filtergraph`.
BACKDROP_SIGMA = 40

RENDER_TIMEOUT = 3600.0
MIN_OUTPUT_BYTES = 1024

# Loudness target for vertical short-form. The reference edits are all pushed
# hard and flat; a clip mastered at broadcast level sounds broken next to them.
# The loudness chain and its constants moved to `ffmpeg_tools` when the static
# renderer needed the same one — the two had drifted, and a clip that fell back
# to the static layout shipped un-normalised. Re-exported because the tests and
# the recipe both name them here.


# ---------------------------------------------------------------------------
# the grade / hit expressions
# ---------------------------------------------------------------------------

def _hit_sum(hits: Sequence[float], width: float) -> str:
    """Sum of unit gaussians at the hit times, or "0" when there are none.

    `pow()` and `clip()` both take commas, so the square is written out longhand
    and the amplitude is kept small enough that clamping is unnecessary.
    """
    terms = [f"exp(-{width:.1f}*(t-{t:.3f})*(t-{t:.3f}))" for t in hits or []]
    return "(" + "+".join(terms) + ")" if terms else "0"


def _eq_filter(plan: dict) -> str:
    """A punchy base grade with a short flash on each hit."""
    style = (plan or {}).get("style") or {}
    hits = list((plan or {}).get("hits") or [])
    sat = float(style.get("saturation") or 1.0)
    con = float(style.get("contrast") or 1.0)

    if not hits:
        return f"eq=saturation={sat:.3f}:contrast={con:.3f}"

    # The references blow out for ~0.2-0.28s on a transition, not a single
    # frame. `flash_s` is that half-amplitude width; the gaussian constant
    # follows from it (4*ln2 / width^2).
    width = max(0.04, float(style.get("flash_s") or 0.18))
    flash = _hit_sum(hits, 2.7726 / (width * width))
    return (
        "eq=eval=frame"
        f":brightness='0.16*{flash}'"
        f":saturation='{sat:.3f}+0.30*{flash}'"
        f":contrast='{con:.3f}+0.14*{flash}'"
    )


# ---------------------------------------------------------------------------
# the filtergraph
# ---------------------------------------------------------------------------

def build_dynamic_filtergraph(plan: dict, cmd_path: str, ass_path: str | None,
                              *, src_w: int, src_h: int,
                              out_w: int = 1080, out_h: int = 1920) -> tuple[str, str]:
    """Return (filter_complex, the label to -map). One graph, one encode."""
    shots = (plan or {}).get("shots") or []
    first = shots[0] if shots else {}
    rect = first.get("rect") or {}
    canvas_w, canvas_h, off_y = canvas_size(src_w, src_h)

    if composition_of(first) == "fit":
        w0, h0, x0, y0 = canvas_w, canvas_h, 0, 0
    else:
        w0 = even(rect.get("w") or _size(src_h, src_w, src_h)[0])
        h0 = even(rect.get("h") or _size(src_h, src_w, src_h)[1])
        x0 = even(rect.get("x") or (src_w - w0) // 2)
        y0 = even((rect.get("y") or (src_h - h0) // 2) + off_y)

    # PAD FIRST, and this is not a style choice. `scale` fixes its output size
    # when the filter is configured and does not recompute it when `sendcmd`
    # changes the crop, so `force_original_aspect_ratio` did nothing for a `fit`
    # shot — the output stayed 1080x1920 and the whole 16:9 frame was STRETCHED
    # into it. The reviewer saw it on every source: "aceeași problemă cu imaginea
    # streched". The graph read correctly and the pixels were wrong.
    #
    # Letterboxing the source onto a 9:16 canvas up front makes every crop —
    # including the full-frame one — 9:16, so `scale` has exactly one geometry
    # and needs no aspect handling at all.
    chain = [
        # TRANSPARENT bars, not black. The canvas geometry is what makes `fit`
        # possible; the fill is what makes it watchable. Padding with alpha lets
        # the blurred copy below show through exactly where the bars are, and
        # costs nothing on a `crop` shot, whose window covers the whole output.
        f"format=yuva420p",
        f"pad={canvas_w}:{canvas_h}:0:{off_y}:color=black@0",
        f"sendcmd=f='{escape_filter_path(cmd_path)}'",
        f"crop={w0}:{h0}:{x0}:{y0}",
        f"scale={even(out_w)}:{even(out_h)}:flags=lanczos",
        "setsar=1",
        _eq_filter(plan),
    ]
    # THE CAPTION IS BURNED AFTER THE OVERLAY, and this is the whole of a defect
    # a human found and every instrument here missed.
    #
    # `pad=...:color=black@0` makes the letterbox bars TRANSPARENT so the blurred
    # copy below can show through them. Burning the subtitles onto that frame
    # draws the text into the colour planes and leaves the alpha where it was —
    # zero — so `overlay` composited it away. On a `crop` shot the frame is
    # opaque everywhere and the caption survived; on a `fit` shot the caption
    # sits in the bar by construction and vanished.
    #
    # MEASURED before the fix: 331 seconds across 27 of the 88 stored clips had
    # no caption at all, 7.7% of the corpus, and one clip was silent for 88% of
    # its length. `caption_placement` reported those 27 clips as "the caption
    # lands on the letterbox band" — geometrically true, and it never asked
    # whether the text was drawn.
    #
    # Reproduced in isolation with two labels, one over the source and one over
    # the bar: before the change only the first rendered, after it both do.
    subtitle_filter = ""
    if ass_path:
        from services.font_manager import fonts_dir

        subtitle_filter = (
            f",subtitles=filename='{escape_filter_path(ass_path)}'"
            f":fontsdir='{escape_filter_path(fonts_dir())}'"
        )

    # The fill: the same frame, zoomed to cover 9:16 and blurred past reading.
    #
    # BUILT AT OUTPUT SIZE, which is not a detail. Blurring the source-resolution
    # canvas — 3840x6826, 26 megapixels a frame — measured 16.5s for 3 seconds of
    # video, five and a half times realtime. At 1080x1920 the same picture costs
    # 2.7s for 3s, because almost all of what the expensive version blurs is
    # thrown away by the downscale immediately afterwards.
    #
    # `sigma` is chosen, not measured: below about 25 the fill still reads as a
    # second copy of the subject and competes with the real one, above about 60
    # it is a flat colour and black would have done. 40 is where it stops being
    # legible and still feels like the same shot.
    # `split` rather than naming [0:v] twice: ffmpeg would insert one anyway,
    # but the graph then reads as two passes over the input when it is one, and
    # a test that guards "one encode" by counting input references would be
    # right to complain.
    fill = (f"[b]scale={even(out_w)}:{even(out_h)}"
            ":force_original_aspect_ratio=increase,"
            f"crop={even(out_w)}:{even(out_h)},gblur=sigma={BACKDROP_SIGMA}[bg]")
    return (f"[0:v]split=2[a][b];[a]{','.join(chain)}[fg];{fill};"
            f"[bg][fg]overlay=0:0{subtitle_filter}[vout]", "[vout]")


def build_dynamic_cmd(src: str, plan: dict, cmd_path: str, ass_path: str | None,
                      out: str, *, start: float, duration: float,
                      src_w: int, src_h: int, fps: int = 30, crf: int = 18,
                      preset: str = "medium", out_w: int = 1080,
                      out_h: int = 1920, loudness: bool = True,
                      watermark: str = "",
                      drop_spans: Sequence[tuple[float, float]] | None = None,
                      has_audio: bool = True) -> list[str]:
    """One ffmpeg argv list for one dynamically-edited clip. Pure.

    `-ss` before `-i` so the seek is by keyframe index; on a 6-hour VOD that is
    seconds instead of twenty minutes. It also re-bases output timestamps to
    zero, which is what lets every time in the plan — and every sendcmd entry —
    be clip-relative.

    `watermark`, `drop_spans` and `has_audio` exist here so that this path and
    the static one take the same options. They did not until 2026-08-17, and
    `dynamic_edit` has been the DEFAULT since the day before: every export was
    silently losing the watermark and ignoring `trim_silence`. Worse, the
    caption file is built with the drop spans applied either way, so with
    trimming on the subtitles were shifted for cuts this renderer never made
    and drifted out of sync by the whole trimmed duration.
    """
    graph, vlabel = build_dynamic_filtergraph(
        plan, cmd_path, ass_path, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)

    if watermark and watermark.strip():
        # Imported from the static renderer rather than reimplemented: the two
        # paths drawing their own watermark is how they came to disagree in the
        # first place, and `_watermark_filter` carries two measured gotchas
        # (`expansion=none`, and a None return meaning "no usable font").
        from services.clipper.render import _watermark_filter

        mark = _watermark_filter(watermark, even(out_w), even(out_h))
        if mark:
            graph = f"{graph};[{vlabel.strip('[]')}]{mark}[vmark]"
            vlabel = "[vmark]"

    alabel = "0:a?"
    if drop_spans:
        from services.clipper.dead_air import removed_seconds, select_expr

        # `-t` is an OUTPUT duration because it sits after `-i`, so leaving it
        # at the window length makes ffmpeg read PAST the window to refill the
        # seconds `select` just dropped. The static renderer learned this on a
        # real clip; the arithmetic is the same here.
        duration = max(0.1, duration - removed_seconds(drop_spans))
        keep = select_expr(drop_spans)
        # `sendcmd` is the first filter in the chain and fires on INPUT
        # timestamps, so every surviving frame still carries the crop its shot
        # asked for; dropping frames afterwards cannot desynchronise the
        # camera work. What it does change is shot LENGTH — a shot with dead
        # air inside it gets shorter — so the measured cut rhythm is not what
        # the planner laid out. That is the honest cost of combining §15 with
        # the multi-shot edit, and it is why `trim_silence` stays off by
        # default.
        graph = (f"{graph};[{vlabel.strip('[]')}]select='{keep}',"
                 f"setpts=N/FRAME_RATE/TB[vcut]")
        vlabel = "[vcut]"
        if has_audio:
            graph += f";[0:a]aselect='{keep}',asetpts=N/SR/TB[acut]"
            alabel = "[acut]"

    # Loudness goes INSIDE the graph once the audio has been through `aselect`:
    # ffmpeg will not run `-af` on a stream a complex graph produced. Without
    # trimming it stays on `-af`, so a command with no drop spans is exactly
    # what it was before this option existed.
    af: list[str] = []
    if loudness and has_audio:
        if drop_spans:
            graph += f";[{alabel.strip('[]')}]{loudness_chain()}[aout]"
            alabel = "[aout]"
        else:
            af = ["-af", loudness_chain()]

    cmd = [
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-ss", f"{max(0.0, start):.3f}",
        "-i", str(src),
        "-t", f"{max(0.1, duration):.3f}",
        "-filter_complex", graph,
        "-map", vlabel,
        "-map", alabel,
    ]
    cmd += af
    cmd += [
        "-c:v", "libx264",
        "-preset", str(preset),
        "-crf", str(int(crf)),
        "-r", str(int(fps)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    return cmd


def render_dynamic_preview(src: str, plan: dict, out: str, *, start: float,
                           work_dir: str | Path, ass_path: str | None = None,
                           src_w: int = 1920, src_h: int = 1080,
                           max_seconds: float = 12.0, **kwargs: Any
                           ) -> dict[str, Any]:
    """The same shot list at preview resolution.

    The editor used to preview through the STATIC renderer while the export
    took the multi-shot path, so a person approved a fixed split screen and
    received an edit with a dozen cuts in it. Same plan, same .ass, same
    options — only the resolution and the encode settings differ, which is the
    rule `render_preview` already states for the static pair.

    The preview is capped at `max_seconds`, so sendcmd entries past the cap
    simply never fire and the clip shows its opening shots. `-t` is what
    enforces it; the plan is not rewritten, because a truncated plan is a
    different edit and this is meant to be a window onto the real one.
    """
    from services.clipper.render import (
        PREVIEW_CRF, PREVIEW_FPS, PREVIEW_H, PREVIEW_PRESET, PREVIEW_W,
    )

    capped = min(float(plan.get("duration") or 0.0), max(0.1, float(max_seconds)))
    if kwargs.get("drop_spans"):
        from services.clipper.dead_air import spans_within

        # Same reason as the static preview: `-t` is shortened by every second
        # it is handed, so a span past the cap steals time from a window it was
        # never inside.
        kwargs["drop_spans"] = spans_within(kwargs["drop_spans"], capped)

    return render_dynamic_clip(
        src, {**plan, "duration": capped}, out, start=start, work_dir=work_dir,
        ass_path=ass_path, src_w=src_w, src_h=src_h,
        fps=PREVIEW_FPS, crf=PREVIEW_CRF, preset=PREVIEW_PRESET,
        out_w=PREVIEW_W, out_h=PREVIEW_H, **kwargs)


def render_dynamic_clip(src: str, plan: dict, out: str, *, start: float,
                        work_dir: str | Path, ass_path: str | None = None,
                        src_w: int = 1920, src_h: int = 1080,
                        is_cancelled: Any = None, ease_s: float = 0.0,
                        **kwargs: Any) -> dict[str, Any]:
    """Write the sendcmd script, run the one encode, verify it produced bytes.

    `is_cancelled` is checked once, before the encode starts, which is exactly
    what the static path does — an export that has already been cancelled
    should not spend two minutes of GPU on a file nobody will open. Neither
    path can interrupt ffmpeg mid-encode.
    """
    if is_cancelled is not None and is_cancelled():
        # The static path's own helper, so both raise the SAME exception and
        # the queue treats a cancelled dynamic export the way it already
        # treats a cancelled static one.
        from services.clipper.render import _raise_if_cancelled

        _raise_if_cancelled(is_cancelled)

    from services.clipper import storage

    final = Path(out)
    temp = storage.temporary_output_path(final)
    work = Path(work_dir)
    # `ease_s` ramps a composition change instead of cutting it. 0.0 for every
    # caller: what to do about the long junctions a human objected to has not
    # been decided, and this exists to be demonstrated on those windows.
    cmd_path = write_sendcmd(plan, src_w, src_h,
                             work / f"{Path(out).stem}.cmd.txt",
                             ease_s=ease_s)

    cmd = build_dynamic_cmd(
        src, plan, cmd_path, ass_path, str(temp),
        start=start, duration=float(plan.get("duration") or 0.0),
        src_w=src_w, src_h=src_h, **kwargs)

    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        run(cmd, timeout=RENDER_TIMEOUT, what="dynamic clip render")

        size = temp.stat().st_size if temp.is_file() else 0
        if size <= MIN_OUTPUT_BYTES:
            raise FFmpegError(
                f"dynamic render produced no usable output ({size} bytes): {out}"
            )
        storage.finalize_output(temp, final)
        return {"path": str(final), "size": size, "sendcmd": cmd_path,
                "shots": len(plan.get("shots") or []), "hits": len(plan.get("hits") or [])}
    finally:
        temp.unlink(missing_ok=True)
