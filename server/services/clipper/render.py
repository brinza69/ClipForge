"""
ClipForge — AI Stream Clipper: the export/preview render layer.

One encode, always. `build_render_cmd` folds the layout filtergraph, the
caption burn and the optional watermark into a SINGLE `-filter_complex`, so a
9:16 export costs exactly one pass through libx264. A second pass to burn
captions would cost a whole generation of compression loss for nothing — the
same reasoning that shaped `remix_pipeline._stage_match_and_caption`.

`build_render_cmd` is pure: it returns an argv list and runs nothing, which is
what makes the render contract testable without ffmpeg on the box.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
from typing import Any, Callable

from services.clipper.ffmpeg_tools import (
    FFmpegError,
    escape_filter_path,
    even,
    ffmpeg_bin,
    run,
    video_info,
)

logger = logging.getLogger("clipforge.clipper.render")

ProgressFn = Callable[[float, str], Any]
CancelFn = Callable[[], bool]

# A 60-second clip encodes in well under a minute; this ceiling only ever fires
# on a wedged child process, not on a slow-but-working encode.
RENDER_TIMEOUT = 1800.0
PREVIEW_TIMEOUT = 600.0

# The editor's feedback loop: half resolution, throwaway quality, seconds not
# minutes. Nobody judges colour grading from a scrub preview.
PREVIEW_W, PREVIEW_H = 540, 960
PREVIEW_FPS, PREVIEW_CRF, PREVIEW_PRESET = 30, 30, "veryfast"

MIN_OUTPUT_BYTES = 1024
MAX_WATERMARK_CHARS = 120

_FONT_FILE_CACHE: str | None = None


# ── Escaping ─────────────────────────────────────────────────────────────────

def _escape_drawtext(text: str) -> str:
    r"""Escape user text for a drawtext `text='...'` value.

    Watermark text is user input that lands inside a filter string, so every
    character the filtergraph tokeniser treats specially has to go: backslash,
    colon, percent and the single quote that would otherwise close the value.
    The quote becomes a curly apostrophe rather than an escape — nesting quotes
    inside a quoted filter argument is the one case ffmpeg's parser gets wrong
    on both platforms.

    The escapes are consumed by the tokeniser, so the drawtext filter itself
    must run with `expansion=none` (see `_watermark_filter`) for them to land
    as literals. Verified against ffmpeg 8.1 on this rig with
    `Ratio: 100% 'live' back\slash %{pts}`, which renders verbatim.
    """
    text = " ".join((text or "").split())  # newlines/tabs would break the argv line
    text = text[:MAX_WATERMARK_CHARS]
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    return text.replace("'", "\u2019")


def _escape_fontfile(path: str) -> str:
    r"""Escape a font path for drawtext's `fontfile=` option.

    drawtext unescapes option values a second time on top of the filtergraph
    parser, so a Windows drive colon needs `\\:` here where the `subtitles`
    filter is happy with a single `\:`. Verified against the ffmpeg build on
    this rig; the doodle renderer carries the same note.
    """
    return str(path).replace("\\", "/").replace(":", "\\\\:")


def _watermark_fontfile() -> str | None:
    """Locate a TTF for drawtext, or None.

    We pass `fontfile=` rather than `font=`: fontconfig lookup does not fail
    gracefully on a box with no fontconfig.conf, it takes the whole process
    down with an access violation (reproduced here on ffmpeg 8.1). A None
    return therefore means "drop the watermark", never "let drawtext guess".
    Cached — this runs on every render call.
    """
    global _FONT_FILE_CACHE
    if _FONT_FILE_CACHE is not None:
        return _FONT_FILE_CACHE or None

    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    _FONT_FILE_CACHE = next((c for c in candidates if Path(c).exists()), "")
    return _FONT_FILE_CACHE or None


# ── Filtergraph assembly ─────────────────────────────────────────────────────

def _default_chain(out_w: int, out_h: int) -> str:
    """Centre-crop fill to the output frame.

    Used when there is no layout plan at all — an un-analysed clip, or a
    preview the user asked for before Pass E ran. Filling and cropping beats
    letterboxing for vertical short-form.
    """
    return (
        f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h},setsar=1[v]"
    )


def _video_chain(plan: dict | None, out_w: int, out_h: int) -> str:
    """The layout filtergraph, normalised to end in a bare `[v]` pad."""
    if not plan:
        return _default_chain(out_w, out_h)

    # Imported lazily so this module stays importable (and unit-testable)
    # without the layout engine, and so an import error surfaces at the call
    # that needs it rather than at process start.
    from services.clipper import layout

    chain = (layout.build_filtergraph(plan, out_w, out_h) or "").strip().rstrip(";")
    if not chain:
        return _default_chain(out_w, out_h)
    if not chain.endswith("[v]"):
        raise ValueError("layout.build_filtergraph must end in a [v] pad")
    return chain


def _watermark_filter(text: str, out_w: int, out_h: int) -> str | None:
    """Bottom-centred translucent drawtext, or None if no font is usable.

    `expansion=none` is load-bearing twice over: with the default expansion a
    literal `%` in user text makes drawtext bail out and draw NOTHING (it logs
    only a "Stray %" warning and still exits 0 — a silently watermark-less
    export), and `%{expr\\:...}` in a caption would otherwise be evaluated
    rather than shown.
    """
    font = _watermark_fontfile()
    if not font:
        logger.warning("no usable font for the watermark; rendering without it")
        return None

    fontsize = max(14, even(out_w * 0.030))
    margin = max(12, even(out_h * 0.035))
    return (
        f"drawtext=fontfile={_escape_fontfile(font)}:"
        f"text='{_escape_drawtext(text)}':expansion=none:"
        f"fontcolor=white@0.55:fontsize={fontsize}:"
        f"shadowcolor=black@0.35:shadowx=1:shadowy=1:"
        f"x=(w-text_w)/2:y=h-text_h-{margin}:fix_bounds=1"
    )


def _filter_complex(
    plan: dict | None,
    ass_path: str | None,
    watermark: str,
    out_w: int,
    out_h: int,
) -> tuple[str, str]:
    """Return (filter_complex, label to -map). ONE graph, one encode."""
    chain = _video_chain(plan, out_w, out_h)

    tail: list[str] = []
    if ass_path:
        from services.font_manager import fonts_dir

        tail.append(
            f"subtitles=filename='{escape_filter_path(ass_path)}'"
            f":fontsdir='{escape_filter_path(fonts_dir())}'"
        )
    if watermark and watermark.strip():
        mark = _watermark_filter(watermark, out_w, out_h)
        if mark:
            tail.append(mark)

    if not tail:
        return chain, "[v]"
    return f"{chain};[v]{','.join(tail)}[vout]", "[vout]"


def _window(cand: dict) -> tuple[float, float]:
    """(start, duration) from a candidate, clamped to something renderable."""
    def _f(key: str) -> float:
        try:
            return float(cand.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    start = max(0.0, _f("start"))
    # A zero-length window would make ffmpeg emit an empty file and "succeed";
    # a 3-second candidate is legitimate, an inverted one is a caller bug.
    duration = max(0.1, _f("end") - start)
    return start, duration


# ── Public API ───────────────────────────────────────────────────────────────

def build_render_cmd(
    src: str,
    cand: dict,
    plan: dict,
    ass_path: str | None,
    out: str,
    *,
    fps: int,
    crf: int,
    preset: str,
    out_w: int = 1080,
    out_h: int = 1920,
    watermark: str = "",
) -> list[str]:
    """One ffmpeg argv list for one clip. Pure — builds, runs nothing.

    `-ss` goes BEFORE `-i` so ffmpeg seeks by keyframe index instead of
    decoding from zero; on a 6-hour VOD that is the difference between two
    seconds and twenty minutes of setup.
    """
    out_w, out_h = even(out_w), even(out_h)
    start, duration = _window(cand)
    graph, vlabel = _filter_complex(plan, ass_path, watermark, out_w, out_h)

    return [
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}",
        "-i", str(src),
        "-t", f"{duration:.3f}",
        "-filter_complex", graph,
        "-map", vlabel,
        "-map", "0:a?",  # `?` keeps a silent source renderable instead of fatal
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


async def render_clip(
    src: str,
    cand: dict,
    plan: dict,
    ass_path: str | None,
    out: str,
    *,
    fps: int,
    crf: int,
    preset: str,
    watermark: str = "",
    on_progress: ProgressFn | None = None,
    is_cancelled: CancelFn | None = None,
) -> dict[str, Any]:
    """Render one clip at full resolution. Returns {path, size, duration}."""
    _raise_if_cancelled(is_cancelled)

    cmd = build_render_cmd(
        src, cand, plan, ass_path, out,
        fps=fps, crf=crf, preset=preset, watermark=watermark,
    )
    _, duration = _window(cand)
    await _report(on_progress, 0.05, f"Encoding {duration:.1f}s at 1080x1920")

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    await _in_thread(lambda: run(cmd, timeout=RENDER_TIMEOUT, what="clip render"))

    await _report(on_progress, 0.95, "Verifying output")
    return await _verify(out, duration)


async def render_preview(
    src: str,
    cand: dict,
    plan: dict,
    ass_path: str | None,
    out: str,
    *,
    max_seconds: float = 12.0,
) -> dict[str, Any]:
    """Render a short, low-res proxy of the same graph for the editor.

    Deliberately the SAME filtergraph as the export: a preview that composes
    differently from the final render is worse than no preview.
    """
    start, duration = _window(cand)
    capped = min(duration, max(0.1, float(max_seconds)))
    window = {"start": start, "end": start + capped}

    cmd = build_render_cmd(
        src, window, plan, ass_path, out,
        fps=PREVIEW_FPS, crf=PREVIEW_CRF, preset=PREVIEW_PRESET,
        out_w=PREVIEW_W, out_h=PREVIEW_H,
    )
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    await _in_thread(lambda: run(cmd, timeout=PREVIEW_TIMEOUT, what="clip preview"))
    return await _verify(out, capped)


# ── Execution helpers ────────────────────────────────────────────────────────

async def _verify(out: str, expected: float) -> dict[str, Any]:
    """Confirm ffmpeg actually produced playable bytes.

    ffmpeg exits 0 on some inputs while writing a header-only file; the size
    floor catches that before a 12-byte 'clip' reaches the review UI.
    """
    path = Path(out)
    size = path.stat().st_size if path.exists() else 0
    if size <= MIN_OUTPUT_BYTES:
        raise FFmpegError(f"render produced no usable output ({size} bytes): {out}")

    try:
        duration = float((await _in_thread(lambda: video_info(str(path))))["duration"])
    except Exception as exc:  # a probe failure must not discard a good render
        logger.warning(f"could not probe rendered clip {path.name}: {exc}")
        duration = round(expected, 3)

    return {"path": str(path), "size": size, "duration": duration}


async def _report(on_progress: ProgressFn | None, pct: float, message: str) -> None:
    """Push a stage update. Accepts sync or async callbacks — the job queue's
    update_progress is a coroutine, tests hand in a plain lambda."""
    if on_progress is None:
        return
    result = on_progress(pct, message)
    if inspect.isawaitable(result):
        await result


def _raise_if_cancelled(is_cancelled: CancelFn | None) -> None:
    if is_cancelled is not None and is_cancelled():
        from job_queue import JobCancelledError  # local: keeps this module queue-free

        raise JobCancelledError("Render cancelled by user.")


async def _in_thread(fn: Callable[[], Any]) -> Any:
    """Run a blocking ffmpeg call off the event loop."""
    return await asyncio.get_event_loop().run_in_executor(None, fn)
