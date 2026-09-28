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

# From the module that OWNS it, not through `dynamic_edit`'s re-export. Going
# the long way round made this module import the planner, and the planner could
# not then import the geometry it plans — an accidental cycle that decided where
# the shot merge was allowed to live.
from services.clipper.dynamic_cameras import ASPECT
from services.clipper.ffmpeg_tools import even

__all__ = ["COMPOSITIONS", "canvas_size", "composition_of", "visual_key",
           "merge_equivalent_shots", "build_sendcmd", "write_sendcmd",
           "MIN_FIT_DWELL_S", "absorb_brief_fit_islands", "JUNCTION_EASE_S"]

# Two shots are contiguous when the second starts where the first ended. Float
# noise from json, not an editorial judgement — the same tolerance the audit
# uses, for the same reason.
JOIN_EPS = 0.001


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


def canvas_size(src_w: int, src_h: int) -> tuple[int, int, int]:
    """The 9:16 canvas the source is letterboxed onto, and its y offset.

    THE WHOLE REASON THE GRAPH PADS FIRST. `scale` computes its output size when
    the filter is configured and does NOT recompute it when `sendcmd` changes
    the crop mid-stream, so one `scale` cannot serve two output geometries.
    `force_original_aspect_ratio` therefore did nothing for a `fit` shot: the
    output stayed 1080x1920 and the 16:9 frame was STRETCHED into it. The graph
    looked right, the pixels were wrong, and a test that read the graph's text
    passed while the reviewer was looking at distorted video.

    Padding first removes the problem instead of working around it: every crop —
    including the one that is the whole frame — is 9:16 of the canvas, so
    `scale` has exactly one job.

    A source already at or narrower than 9:16 needs no canvas; it gets itself.
    """
    if src_w <= 0 or src_h <= 0 or (src_w / float(src_h)) <= ASPECT:
        return even(src_w), even(src_h), 0
    canvas_h = even(src_w / ASPECT)
    return even(src_w), canvas_h, (canvas_h - even(src_h)) // 2


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


#: How long a `fit` stretch has to last before switching to it is worth what the
#: switch costs. CHOSEN, not derived — say so, the way §4's rhythm bands do.
#:
#: WHAT THE SWITCH COSTS IS FIXED AND LARGE. A `crop` shows a 607.5px window of
#: a 1920px source blown up to 1080 (1.78x); a `fit` shows all 1920 squeezed
#: into 1080 (0.5625x). The same subject therefore changes apparent size by
#: 1.78/0.5625 = 3.16x across the junction, and that is an IDENTITY of 16:9
#: geometry, not a number that can be tuned down. Measured across the 58 pilot
#: exports: 84 junctions, median jump 3.58x, worst 13.52x, and not one below 3x.
#: A human watching four clips timestamped the junctions and nothing else.
#:
#: WHY ONLY IN ONE DIRECTION, and this is the part the measurement decided. The
#: interior runs split cleanly:
#:
#:     `crop` islands   18, median 14.2s, SHORTEST 3.6s
#:     `fit`  islands   39, median  5.3s, shortest 0.6s — 15 under 4s
#:
#: A short `crop` island barely exists, so there is nothing to absorb there, and
#: absorbing one would shrink a subject that was demonstrably present. A short
#: `fit` island is the common case and it is best read as the subject detector
#: BLINKING — the subject is there before it and there after it — so returning
#: those seconds to `crop` restores continuity rather than sacrificing framing.
#:
#: WHAT THIS DOES NOT FIX, stated here so nobody reads a bigger claim into it:
#: a long, earned `fit` run still costs its 3.16x on the way in and on the way
#: out. On the clip whose junctions a human timestamped, the runs are 8.8s,
#: 13.8s, 15.8s, 14.6s and 7.5s, so this rule removes NONE of them. It removes
#: 30 of the corpus's 84 junctions; the other 54 need either an animated
#: transition or a different idea of what `fit` frames, and both are somebody's
#: decision rather than this function's.
MIN_FIT_DWELL_S = 4.0


def absorb_brief_fit_islands(shots: list[dict], *,
                             min_dwell_s: float = MIN_FIT_DWELL_S,
                             crop_keeps_content=None) -> list[dict]:
    """Return `shots` with short interior `fit` runs put back to `crop`.

    OFF UNLESS SOMEBODY CAN SHOW THE CROP KEEPS THE CONTENT, and it was not.
    The rule shipped as "a `fit` run under the dwell, with `crop` on both sides,
    becomes `crop`", on the reasoning that the subject was there before and
    after so it did not really leave. That reasoning is wrong, and a frame from
    the corpus is what showed it.

        `pilotee0e/aaf5f324e832`, 4.82-7.36s, absorbed at 2.54s. The old `fit`
        export held both people. The new `crop` cuts the man off at the left
        edge, pushes the woman's head to the bottom of the frame, and fills the
        rest with blank wall.

    A SHORT DURATION IS NOT EVIDENCE THAT ANYTHING FITS. It says the planner
    changed its mind quickly, which is a fact about the plan rather than about
    the picture — the same distinction as `move: push` on a shot that never
    moves. Two extra cuts are preferable to a person cut in half, and the
    junction count is an indicator this rule was optimising while the framing
    got worse.

    So `crop_keeps_content` is REQUIRED and there is no default. Given the run's
    shots it answers whether the proposed crop preserves what matters over that
    interval; only `True` absorbs. Absent, `None` or anything else leaves the
    run as `fit`, which is what every caller gets today.

    WHAT STAYS TRUE FROM THE FIRST VERSION. The run must be bounded on both
    sides by `crop` — a leading or trailing `fit` has evidence on one side only.
    Every shot already carries the `rect` it would be cropped to, so flipping
    the label invents no geometry. And it runs BEFORE `merge_equivalent_shots`,
    because absorbing a run can leave neighbours delivering the same picture and
    the merge is what removes the cut between them.
    """
    if not isinstance(shots, list) or len(shots) < 3:
        return shots
    if not callable(crop_keeps_content):
        # The rule is off. Not silently — this is the whole finding: absorbing
        # on duration alone made a measurably worse frame.
        return shots
    runs: list[list[int]] = []
    for i, shot in enumerate(shots):
        if runs and composition_of(shots[runs[-1][0]]) == composition_of(shot):
            runs[-1].append(i)
        else:
            runs.append([i])

    for pos, run in enumerate(runs):
        if pos == 0 or pos == len(runs) - 1:
            continue
        if composition_of(shots[run[0]]) != "fit":
            continue
        try:
            span = float(shots[run[-1]]["t1"]) - float(shots[run[0]]["t0"])
        except (KeyError, TypeError, ValueError):
            # A shot whose clock cannot be read is not a short one. Absent is
            # not zero, and a zero here would silently absorb it.
            continue
        if span != span or span >= min_dwell_s:
            continue
        try:
            keeps = crop_keeps_content([shots[i] for i in run])
        except Exception:
            # An answer nobody could obtain is not a yes.
            continue
        if keeps is not True:
            continue
        for i in run:
            shots[i] = {**shots[i], "composition": "crop",
                        # WHY THIS SHOT IS NOT WHAT THE SUBJECT PASS SAID, kept
                        # on the shot because the next reader will otherwise
                        # find a `crop` over a span with no detected subject and
                        # conclude the detector was wrong.
                        "composition_absorbed": round(span, 3)}
    return shots


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
        cw, ch, _ = canvas_size(src_w, src_h)
        return [(round(t0, 3), cw, ch)]
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

    # Clamped against the SOURCE, then moved onto the canvas. Clamping against
    # the canvas would let a crop wander into the black bars.
    _, _, off_y = canvas_size(src_w, src_h)
    shake = float(shot.get("shake") or 0.0)
    anchor = shot.get("anchor") or [src_w / 2.0, src_h / 2.0]
    ax = _anchor(float(anchor[0]), src_w, biggest[0], shake + 2.0)
    ay = _anchor(float(anchor[1]), src_h, biggest[1], shake + 2.0) + off_y

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
# what the viewer actually receives
# ---------------------------------------------------------------------------

def visual_key(shot: dict, style: dict, src_w: int, src_h: int) -> tuple | None:
    """The delivered image of one shot, or None when it cannot be compared.

    This is the whole of R1. A cut exists only if the picture changes, and the
    picture is not the plan's rectangle — it is what `build_sendcmd` schedules:
    a size timeline and a pair of position expressions. Two `fit` shots have
    different rects and deliver the identical full frame, which is why 116 cuts
    in the pilot corpus were invisible.

    None means "has a size timeline with more than one point", i.e. the shot
    SNAPS or PUSHES. Its size changes across its own length, so joining it to a
    neighbour would restart that movement mid-shot, and the join is refused
    whatever else matches.

    The timestamp of the single point is deliberately dropped: it is the shot's
    own start, so keeping it would make every shot unique and the key useless.

    Shake does NOT break equivalence, and the reason is worth keeping: the shake
    term is a function of ABSOLUTE `t`, so an identical expression continues
    unbroken across a join. A single point therefore means static SIZE, not a
    static image — a shaking shot still qualifies, provided the anchor and the
    amplitude are the same, because then the two expressions are the same
    expression.

    Exact comparison only. No IoU, no rect proximity, no perceptual threshold:
    those need a measurement first, and R1 is the batch that removes the cuts
    nobody can defend, not the ones somebody might.
    """
    timeline = _size_timeline(shot, style, src_w, src_h)
    if len(timeline) != 1:
        return None
    _t, w, h = timeline[0]
    return (w, h) + _position_exprs(shot, (w, h), src_w, src_h)


def merge_equivalent_shots(plan: dict, src_w: int, src_h: int) -> dict:
    """A new plan in which no cut is invisible. Pure: the input is not touched.

    The first shot of a group keeps its geometry AND its metadata; only `t1`
    grows, to the end of the group. Nothing here picks a "dominant" reason:
    shots carry no `reason` or `confidence` yet, so choosing between two sets of
    energies would be an invented rule dressed as a measurement. That
    consolidation belongs to the batch that gives a shot a reason to state.

    Runs ONCE, inside the planner. It replaced a merge that compared rectangles,
    which could not see composition at all — and a second pass afterwards would
    be too late, because by then the plan no longer records what the absorbed
    shot's composition had been.
    """
    shots = (plan or {}).get("shots") or []
    style = (plan or {}).get("style") or {}
    merged: list[dict] = []
    for shot in shots:
        if merged and _joins(merged[-1], shot, style, src_w, src_h):
            merged[-1] = {**merged[-1], "t1": shot.get("t1")}
            continue
        merged.append(dict(shot))
    for i, shot in enumerate(merged):
        shot["index"] = i
    # How many shots the PLANNER decided on, kept because a later reader cannot
    # recover it: `clipper_render_plan` refuses a plan of fewer than two shots
    # and renders it statically, and without this a clip whose only fault was an
    # invisible cut would silently change renderer, crop and captions. Removing
    # a command nobody could see must not change the picture.
    # Idempotent in the PROVENANCE too, not just in the shot list. Recomputing
    # this from `len(shots)` made a second application report the merged count
    # as the planned one, and the count is the only thing standing between a
    # clip with one invisible cut and the static renderer.
    before = int((plan or {}).get("shot_count_before_merge") or len(shots))
    return {**(plan or {}), "shots": merged,
            "shot_count_before_merge": before,
            "equivalent_cuts_removed": before - len(merged)}


def _joins(previous: dict, shot: dict, style: dict, src_w: int, src_h: int) -> bool:
    """Whether `shot` continues the image `previous` is already showing."""
    try:
        gap = abs(float(previous.get("t1")) - float(shot.get("t0")))
    except (TypeError, ValueError):
        return False
    if gap > JOIN_EPS:
        # A hole between them means something else was on screen. Two shots
        # either side of it are not one shot however alike they look.
        return False
    key = visual_key(previous, style, src_w, src_h)
    return key is not None and key == visual_key(shot, style, src_w, src_h)


# ---------------------------------------------------------------------------
# the sendcmd script
# ---------------------------------------------------------------------------

#: How long a composition change may take when somebody asks for it to be eased
#: rather than cut. OFF BY DEFAULT and applied to nothing: see `ease_s` below.
JUNCTION_EASE_S = 0.3


def _centre_of(shot: dict, biggest: tuple[int, int],
               src_w: int, src_h: int) -> tuple[float, float]:
    """Where this shot's crop window is centred, in CANVAS coordinates.

    `_position_exprs` writes `anchor - out_w/2`, so the anchor IS the centre and
    a size command alone re-centres — that is what makes a push zoom toward the
    subject. A `fit` shot writes `0, 0` instead, because at the full canvas size
    the origin and the centred position are the same point. They stop being the
    same point the moment the window is smaller than the canvas, which is
    exactly what a ramp does.
    """
    canvas_w, canvas_h, off_y = canvas_size(src_w, src_h)
    if composition_of(shot) == "fit":
        return canvas_w / 2.0, canvas_h / 2.0
    shake = float(shot.get("shake") or 0.0)
    anchor = shot.get("anchor") or [src_w / 2.0, src_h / 2.0]
    return (_anchor(float(anchor[0]), src_w, biggest[0], shake + 2.0),
            _anchor(float(anchor[1]), src_h, biggest[1], shake + 2.0) + off_y)


def _ease_points(prev_h: int, next_w: int, next_h: int, at: float,
                 ease_s: float, src_w: int, src_h: int,
                 prev_centre: tuple[float, float],
                 next_centre: tuple[float, float]
                 ) -> list[tuple[float, int, int, float, float]]:
    """A short ramp from the previous window to the next: size AND centre.

    THIS IS ONLY A ZOOM AND A PAN, and that is why it costs no filtergraph
    change. Every crop the graph ever takes is 9:16 of the padded canvas —
    `canvas_size` says so in as many words, and it is why the pad comes first —
    so the `crop` and the `fit` windows differ in size and position and nothing
    else.

    IT SIZES AGAINST THE CANVAS, NOT THE SOURCE. `_size` clamps its height to
    `src_h`, because a crop window lives inside the frame — but the `fit` window
    is the PADDED canvas and is taller than the source by construction: 3412
    against 1080 on a 16:9 input. Every step of the first version came back
    clamped to 1080, the whole ramp collapsed to one repeated size, and the
    junction cut exactly as hard as before while the script looked longer.

    AND IT MOVES THE CENTRE, which the second version did not. Pinned at the
    `fit` shot's own `0, 0`, a mid-ramp window sits in the TOP-LEFT of the
    canvas — which is transparent padding, so the composite showed the blurred
    background and nothing else. A frame pulled from the middle of the ramp is
    what found it; the sendcmd script looked perfectly reasonable.
    """
    canvas_w, canvas_h, _ = canvas_size(src_w, src_h)
    out: list[tuple[float, int, int, float, float]] = []
    if prev_h <= 0 or next_h <= 0:
        return out
    steps = max(2, int(ease_s * 20))
    seen: set[tuple[int, int]] = {(next_w, next_h)}
    for i in range(steps):
        frac = (i + 1) / (steps + 1)
        # Geometric on size, linear on position: apparent size is a ratio and
        # the jump this exists for is 3.16x, while a pan is a distance.
        height = prev_h * (next_h / prev_h) ** frac
        h = even(min(canvas_h, max(160, height)))
        w = even(min(canvas_w, max(90, h * ASPECT)))
        if (w, h) in seen:
            continue
        seen.add((w, h))
        cx = prev_centre[0] + (next_centre[0] - prev_centre[0]) * frac
        cy = prev_centre[1] + (next_centre[1] - prev_centre[1]) * frac
        out.append((round(at + ease_s * frac, 3), w, h, cx, cy))
    return out


def build_sendcmd(plan: dict, src_w: int, src_h: int, *,
                  ease_s: float = 0.0) -> str:
    """The whole edit as a sendcmd script. Pure — returns text, writes nothing.

    Order within an entry matters: w and h go before x and y, because crop
    re-clamps the position against the CURRENT size every time it reconfigures.

    `ease_s` RAMPS A COMPOSITION CHANGE instead of cutting it, and is 0.0 —
    off — for every caller today. A human timestamped four junctions on one clip
    and every one was a `crop`<->`fit` change; the jump is at least 3.16x by the
    geometry of 16:9, and `absorb_brief_fit_islands` removes only the ones short
    enough not to have earned their place. What to do about the rest has not
    been decided, so this exists to be DEMONSTRATED on those four windows and
    compared against the hard cut, not switched on.
    """
    style = (plan or {}).get("style") or {}
    lines: list[str] = []
    previous: tuple[int, int] | None = None
    previous_comp: str | None = None
    previous_centre: tuple[float, float] | None = None

    for shot in (plan or {}).get("shots") or []:
        timeline = _size_timeline(shot, style, src_w, src_h)
        if not timeline:
            continue
        biggest = (max(w for _, w, _ in timeline), max(h for _, _, h in timeline))
        x_expr, y_expr = _position_exprs(shot, biggest, src_w, src_h)

        first_t, first_w, first_h = timeline[0]
        centre = _centre_of(shot, biggest, src_w, src_h)
        if (ease_s > 0 and previous is not None
                and previous_centre is not None
                and previous_comp is not None
                and composition_of(shot) != previous_comp):
            # Size AND position on every step, w/h before x/y because crop
            # re-clamps the position against the current size each time it
            # reconfigures. The shot's own expression takes over at the end.
            for t, w, h, cx, cy in _ease_points(
                    previous[1], first_w, first_h, first_t, ease_s,
                    src_w, src_h, previous_centre, centre):
                lines.append(f"{t:.3f} crop w {w}, crop h {h}, "
                             f"crop x '{cx:.1f}-out_w/2', "
                             f"crop y '{cy:.1f}-out_h/2';")
            lines.append(f"{first_t + ease_s:.3f} crop w {first_w}, "
                         f"crop h {first_h}, crop x '{x_expr}', "
                         f"crop y '{y_expr}';")
        else:
            lines.append(
                f"{first_t:.3f} crop w {first_w}, crop h {first_h}, "
                f"crop x '{x_expr}', crop y '{y_expr}';"
            )
        for t, w, h in timeline[1:]:
            lines.append(f"{t:.3f} crop w {w}, crop h {h};")
        previous = (timeline[-1][1], timeline[-1][2])
        previous_comp = composition_of(shot)
        previous_centre = centre

    return "\n".join(lines) + "\n"


def write_sendcmd(plan: dict, src_w: int, src_h: int, path: str | Path, *,
                  ease_s: float = 0.0) -> str:
    """Write the sendcmd script next to the render and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_sendcmd(plan, src_w, src_h, ease_s=ease_s),
                      encoding="utf-8")
    return str(target)
