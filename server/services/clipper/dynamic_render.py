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

from services.clipper.dynamic_edit import ASPECT
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
    "build_sendcmd",
    "build_dynamic_filtergraph",
    "build_dynamic_cmd",
    "render_dynamic_clip",
]

RENDER_TIMEOUT = 3600.0
MIN_OUTPUT_BYTES = 1024

# Loudness target for vertical short-form. The reference edits are all pushed
# hard and flat; a clip mastered at broadcast level sounds broken next to them.
# The loudness chain and its constants moved to `ffmpeg_tools` when the static
# renderer needed the same one — the two had drifted, and a clip that fell back
# to the static layout shipped un-normalised. Re-exported because the tests and
# the recipe both name them here.


# ---------------------------------------------------------------------------
# geometry per shot
# ---------------------------------------------------------------------------

def _size(height: float, src_w: int, src_h: int) -> tuple[int, int]:
    """The even 9:16 crop size for a target height, clamped into the frame."""
    h = even(min(src_h, max(160, height)))
    w = even(min(src_w, max(90, h * ASPECT)))
    if w > src_w:
        w = even(src_w)
        h = even(min(src_h, w / ASPECT))
    return w, h


def _size_timeline(shot: dict, style: dict, src_w: int, src_h: int
                   ) -> list[tuple[float, int, int]]:
    """[(t, w, h)] control points for one shot, snap then push, deduplicated.

    A `snap` opens the shot slightly wide and closes onto the target in
    `snap_s` — that is what makes a hard cut land like a hit rather than a
    dissolve-free slide. A `push`/`pull` then walks the size across the rest of
    the shot at `push_hz`, which is dense enough to read as continuous motion
    and sparse enough that the filter is not reconfigured every frame.
    """
    t0, t1 = float(shot["t0"]), float(shot["t1"])
    base = float((shot.get("rect") or {}).get("h") or src_h)
    snap_s = float(style.get("snap_s") or 0.0)
    snap_amount = float(style.get("snap_amount") or 0.0)
    push_amount = float(style.get("push_amount") or 0.0)
    hz = max(2.0, float(style.get("push_hz") or 10.0))

    points: list[tuple[float, float]] = []
    body_start = t0
    if shot.get("snap") and snap_s > 0.01 and snap_amount > 0:
        steps = max(2, int(snap_s * hz * 1.5))
        for i in range(steps + 1):
            frac = i / steps
            points.append((t0 + snap_s * frac,
                           base * (1.0 + snap_amount * (1.0 - frac))))
        body_start = t0 + snap_s

    move = str(shot.get("move") or "hold")
    span = t1 - body_start
    if move in ("push", "pull") and span > 0.05 and push_amount > 0:
        end = base * (1.0 - push_amount) if move == "push" else base * (1.0 + push_amount)
        steps = max(2, int(span * hz))
        for i in range(steps + 1):
            frac = i / steps
            points.append((body_start + span * frac, base + (end - base) * frac))
    elif not points:
        points.append((t0, base))
    elif move == "hold":
        points.append((body_start, base))

    out: list[tuple[float, int, int]] = []
    for t, h in points:
        w, hh = _size(h, src_w, src_h)
        if out and out[-1][1] == w and out[-1][2] == hh:
            continue
        out.append((round(min(max(t, t0), t1), 3), w, hh))
    return out


def _anchor(centre: float, limit: int, biggest: int, margin: float) -> float:
    """Clamp a subject centre so `centre - size/2` stays in frame for EVERY size.

    This is why no expression here needs `clip()` — and it must not, because
    `clip(v,lo,hi)` contains commas that a filtergraph would read as filter
    separators. Clamping against the WIDEST size the shot uses keeps every
    narrower one safely inside too, and `margin` reserves room for the shake.
    """
    lo = biggest / 2.0 + margin
    hi = limit - biggest / 2.0 - margin
    if lo > hi:                       # crop as large as the frame: only one spot
        return limit / 2.0
    return min(max(centre, lo), hi)


def _position_exprs(shot: dict, biggest: tuple[int, int],
                    src_w: int, src_h: int) -> tuple[str, str]:
    """The crop x/y expressions for one shot: anchor-centred, plus shake.

    Written against `out_w`/`out_h` rather than fixed numbers so that a size
    command alone re-centres the rectangle — that is what turns the push-in into
    a zoom TOWARD THE ANCHOR instead of toward the middle of the frame.
    """
    shake = float(shot.get("shake") or 0.0)
    anchor = shot.get("anchor") or [src_w / 2.0, src_h / 2.0]
    ax = _anchor(float(anchor[0]), src_w, biggest[0], shake + 2.0)
    ay = _anchor(float(anchor[1]), src_h, biggest[1], shake + 2.0)

    x = f"{ax:.1f}-out_w/2"
    y = f"{ay:.1f}-out_h/2"
    if shake > 0.05:
        # Two incommensurate frequencies: a single sine reads as a pendulum,
        # which looks mechanical rather than hand-held.
        wx = 2.0 * math.pi * 7.5
        wy = 2.0 * math.pi * 7.5 * 1.37
        x += f"+{shake:.2f}*sin({wx:.3f}*t)"
        y += f"+{shake * 0.7:.2f}*sin({wy:.3f}*t+1.1)"
    return x, y


# ---------------------------------------------------------------------------
# the sendcmd script
# ---------------------------------------------------------------------------

def build_sendcmd(plan: dict, src_w: int, src_h: int) -> str:
    """The whole edit as a sendcmd script. Pure — returns text, writes nothing.

    Order within an entry matters: w and h go before x and y, because crop
    re-clamps the position against the CURRENT size every time it reconfigures.
    """
    style = (plan or {}).get("style") or {}
    lines: list[str] = []

    for shot in (plan or {}).get("shots") or []:
        timeline = _size_timeline(shot, style, src_w, src_h)
        if not timeline:
            continue
        biggest = (max(w for _, w, _ in timeline), max(h for _, _, h in timeline))
        x_expr, y_expr = _position_exprs(shot, biggest, src_w, src_h)

        first_t, first_w, first_h = timeline[0]
        lines.append(
            f"{first_t:.3f} crop w {first_w}, crop h {first_h}, "
            f"crop x '{x_expr}', crop y '{y_expr}';"
        )
        for t, w, h in timeline[1:]:
            lines.append(f"{t:.3f} crop w {w}, crop h {h};")

    return "\n".join(lines) + "\n"


def write_sendcmd(plan: dict, src_w: int, src_h: int, path: str | Path) -> str:
    """Write the sendcmd script next to the render and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_sendcmd(plan, src_w, src_h), encoding="utf-8")
    return str(target)


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
    w0 = even(rect.get("w") or _size(src_h, src_w, src_h)[0])
    h0 = even(rect.get("h") or _size(src_h, src_w, src_h)[1])
    x0 = even(rect.get("x") or (src_w - w0) // 2)
    y0 = even(rect.get("y") or (src_h - h0) // 2)

    chain = [
        f"sendcmd=f='{escape_filter_path(cmd_path)}'",
        f"crop={w0}:{h0}:{x0}:{y0}",
        f"scale={even(out_w)}:{even(out_h)}:flags=lanczos",
        "setsar=1",
        _eq_filter(plan),
    ]
    if ass_path:
        from services.font_manager import fonts_dir

        chain.append(
            f"subtitles=filename='{escape_filter_path(ass_path)}'"
            f":fontsdir='{escape_filter_path(fonts_dir())}'"
        )
    return "[0:v]" + ",".join(chain) + "[vout]", "[vout]"


def build_dynamic_cmd(src: str, plan: dict, cmd_path: str, ass_path: str | None,
                      out: str, *, start: float, duration: float,
                      src_w: int, src_h: int, fps: int = 30, crf: int = 18,
                      preset: str = "medium", out_w: int = 1080,
                      out_h: int = 1920, loudness: bool = True) -> list[str]:
    """One ffmpeg argv list for one dynamically-edited clip. Pure.

    `-ss` before `-i` so the seek is by keyframe index; on a 6-hour VOD that is
    seconds instead of twenty minutes. It also re-bases output timestamps to
    zero, which is what lets every time in the plan — and every sendcmd entry —
    be clip-relative.
    """
    graph, vlabel = build_dynamic_filtergraph(
        plan, cmd_path, ass_path, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)

    cmd = [
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-ss", f"{max(0.0, start):.3f}",
        "-i", str(src),
        "-t", f"{max(0.1, duration):.3f}",
        "-filter_complex", graph,
        "-map", vlabel,
        "-map", "0:a?",
    ]
    if loudness:
        cmd += ["-af", loudness_chain()]
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


def render_dynamic_clip(src: str, plan: dict, out: str, *, start: float,
                        work_dir: str | Path, ass_path: str | None = None,
                        src_w: int = 1920, src_h: int = 1080, **kwargs: Any
                        ) -> dict[str, Any]:
    """Write the sendcmd script, run the one encode, verify it produced bytes."""
    work = Path(work_dir)
    cmd_path = write_sendcmd(plan, src_w, src_h, work / f"{Path(out).stem}.cmd.txt")

    cmd = build_dynamic_cmd(
        src, plan, cmd_path, ass_path, out,
        start=start, duration=float(plan.get("duration") or 0.0),
        src_w=src_w, src_h=src_h, **kwargs)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run(cmd, timeout=RENDER_TIMEOUT, what="dynamic clip render")

    size = Path(out).stat().st_size if Path(out).exists() else 0
    if size <= MIN_OUTPUT_BYTES:
        raise FFmpegError(f"dynamic render produced no usable output ({size} bytes): {out}")
    return {"path": str(out), "size": size, "sendcmd": cmd_path,
            "shots": len(plan.get("shots") or []), "hits": len(plan.get("hits") or [])}
