"""
ClipForge — AI Stream Clipper: a shot list turned into crop geometry over time.

Split from `dynamic_render.py` when the `fit` composition pushed it past the
repo's 500-line limit. The seam is one the module already had a heading for:
everything here is ARITHMETIC ON A PLAN — sizes, anchors, expressions and the
`sendcmd` script that schedules them. Nothing here builds a filtergraph or runs
ffmpeg; that half stayed behind.

The two hard constraints travel with the arithmetic, because this is where they
are enforced:

  * NO COMMAS. A comma separates filters in a filtergraph and arguments in a
    sendcmd entry, so `clip(v,lo,hi)` would silently truncate the graph. Range
    safety is baked into the CONSTANTS instead (see `_anchor`).
  * every crop dimension stays even — H.264 with yuv420p refuses odd crops.

Moved verbatim; the only edits are the imports it needs to stand alone.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from services.clipper.dynamic_edit import ASPECT
from services.clipper.ffmpeg_tools import even

__all__ = ["COMPOSITIONS", "composition_of", "build_sendcmd", "write_sendcmd"]


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


#: What a shot does with the frame.
#:
#:   "crop" — the only behaviour there has ever been: a 9:16 window pointed at
#:            a subject, pushed and snapped.
#:   "fit"  — the WHOLE frame, letterboxed. For the sequences that have no
#:            subject to point at: a diagram, a screen share, a cutaway. The
#:            blind review's clearest single result was that a 9:16 window on
#:            those produces a wall of texture — a "8 million pixels" slide
#:            became an unreadable strip of grid — while the whole frame is
#:            small but legible.
COMPOSITIONS = ("crop", "fit")


def composition_of(shot: dict) -> str:
    return "fit" if str((shot or {}).get("composition") or "crop") == "fit" else "crop"


def _size_timeline(shot: dict, style: dict, src_w: int, src_h: int
                   ) -> list[tuple[float, int, int]]:
    """[(t, w, h)] control points for one shot, snap then push, deduplicated.

    A `snap` opens the shot slightly wide and closes onto the target in
    `snap_s` — that is what makes a hard cut land like a hit rather than a
    dissolve-free slide. A `push`/`pull` then walks the size across the rest of
    the shot at `push_hz`, which is dense enough to read as continuous motion
    and sparse enough that the filter is not reconfigured every frame.

    A `fit` shot emits ONE point at the full frame and never reaches `_size`.
    That is the whole trap: `_size` forces 9:16 on everything it is given, so a
    rect of the full 3840×2160 comes back out as 1214×2160 and the letterbox
    never happens. Nor does it snap or push — a shot that exists because there
    is nothing to point at has nothing to move toward.
    """
    t0, t1 = float(shot["t0"]), float(shot["t1"])
    if composition_of(shot) == "fit":
        return [(round(t0, 3), even(src_w), even(src_h))]
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
    # A `fit` shot is the whole frame: origin at 0,0 and nothing to shake. The
    # anchor logic below exists to keep a SMALLER window on a subject, and a
    # window the size of the frame has neither a subject nor room to move.
    if composition_of(shot) == "fit":
        return "0", "0"

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
