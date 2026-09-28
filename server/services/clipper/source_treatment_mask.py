"""Load and verify a source-caption mask (`clipper_source_caption_mask_v1`).

The mask says, for every source frame the render can read, which burned line is
on screen — or that none was observed, or that nobody could tell (`unknown`).
It is data A makes and a person views (SC lot 0); this module only checks it,
from the file's BYTES, every time it is used. Schema: SC-addendum-v2 §2.2.

What a mask must prove before anything treats a pixel with it:

  - it is the canonical bytes its name hashes to (content-addressed store);
  - it was made for THIS source: the full-hash identity and the stream fields
    the clock reads (AD12 `proxy_provenance.media_identity`);
  - the clock is constant-rate and every recorded pts sits on the frame step,
    so `k = (pts - start_pts) / pts_step` is a frame and not a rounding;
  - its window covers every frame the encode could consume for this clip's
    CURRENT bounds (a trim can move them outside);
  - every frame of the window has a state, and none of them is `unknown` —
    `unknown` is "nobody read it", never "no text";
  - no run rests on the detector alone: an unviewed `detector_only` run is
    not accepted (the SC1 detector once put F1's one-frame gap on a line and
    agreed with itself);
  - each glyph PNG hashes, from the bytes read now, to what the mask records —
    an unchanged JSON over a changed PNG is refused.

AN OPEN EDGE IS NOT AN END. A line whose `k_on` or `k_off` was never observed
(`null`) continues beyond the observation; its run is taken up to the window's
edge and reported as `continues_beyond_observation`, never as the frame where
the subtitle appeared or went away (codex-verdict-next-10 §3).
"""

from __future__ import annotations

import json
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from services.clipper.source_treatment import (MASK_SCHEMA, SourceTreatmentRefused,
                                               canonical_bytes, is_int, sha256_bytes)

#: The stream fields the mask's `source.stream` must share with the probed
#: identity — the ones `media_identity` carries and the clock depends on.
IDENTITY_STREAM_KEYS: tuple[str, ...] = ("codec_name", "width", "height", "time_base",
                                         "r_frame_rate", "avg_frame_rate", "start_pts")
MARGIN_FRAMES = 2
BASES: tuple[str, ...] = ("viewed", "strip+detector", "detector_only")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _bad(detail: str) -> SourceTreatmentRefused:
    return SourceTreatmentRefused("mask_invalid", detail)


def _dict(x: Any, what: str) -> dict:
    if not isinstance(x, dict):
        raise _bad(f"{what} is not an object")
    return x


def _int(x: Any, what: str) -> int:
    if not is_int(x):
        raise _bad(f"{what} is not an integer: {x!r}")
    return x


def _opt_int(x: Any, what: str) -> int | None:
    return None if x is None else _int(x, what)


def _frac(x: Any, what: str) -> Fraction:
    try:
        f = Fraction(x) if isinstance(x, str) else None
    except (ValueError, ZeroDivisionError):
        f = None
    if f is None or f <= 0:
        raise _bad(f"{what} is not a positive rational: {x!r}")
    return f


def load_mask(path: str | Path, *, source_identity: Any, clip_id: str,
              clip_start: Any, clip_end: Any) -> dict:
    """The verified mask, or `SourceTreatmentRefused`.

    `source_identity` is `proxy_provenance.media_identity(source)` (a declared
    cache reuse is fine; the MASK must record a full hash). `clip_start`/
    `clip_end` are the clip's current bounds in source seconds. Returns
    `{sha256, doc, clip_id, clock, window, lines, line_runs}`, where
    `line_runs[id] = (k_from, k_to)` is that line's frames inside the window."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise SourceTreatmentRefused("mask_unreadable", str(e)) from None
    sha = sha256_bytes(data)
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SourceTreatmentRefused("mask_unreadable", str(e)) from None
    if not isinstance(doc, dict) or doc.get("schema") != MASK_SCHEMA:
        raise _bad(f"schema is not {MASK_SCHEMA}")
    if canonical_bytes(doc) != data:
        raise SourceTreatmentRefused("mask_not_canonical", "the bytes are not the canonical form")
    if path.stem != sha:
        raise SourceTreatmentRefused("mask_hash_mismatch", f"file {path.name} hashes to {sha}")

    source = _check_source(_dict(doc.get("source"), "source"), source_identity)
    clock = _check_clock(_dict(doc.get("clock"), "clock"), _dict(source.get("stream"), "stream"))
    clip = _dict(doc.get("clip"), "clip")
    if clip.get("clip_id") != clip_id:
        raise SourceTreatmentRefused("mask_clip_mismatch",
                                     f"mask is for {clip.get('clip_id')!r}, not {clip_id!r}")
    window = _check_window(_dict(doc.get("window"), "window"), clock, clip_start, clip_end,
                           source_identity)
    clock_check = _dict(clock["cfr_check"], "clock.cfr_check")
    if _int(clock_check.get("off_step"), "cfr_check.off_step") != 0:
        raise SourceTreatmentRefused("source_clock_refused",
                                     f"{clock_check['off_step']} frames off the pts step")
    if _int(clock_check.get("frames"), "cfr_check.frames") != window[1] - window[0] + 1:
        raise SourceTreatmentRefused("source_clock_refused",
                                     "the CFR check did not cover exactly the window")
    lines = _check_lines(doc.get("lines"), path.parent, source["stream"])
    runs = _check_frames(doc.get("frames"), window, lines)
    _check_strips(_dict(doc.get("inspection"), "inspection").get("strips"), clock)
    return {"sha256": sha, "doc": doc, "clip_id": clip_id, "clock": clock,
            "window": window, "lines": lines, "line_runs": runs}


def _check_source(source: dict, identity: Any) -> dict:
    if source.get("sha256_how") != "full_hash" or not _SHA.match(str(source.get("sha256"))):
        raise _bad("the mask's source was not hashed in full")
    if not isinstance(identity, dict) or not isinstance(identity.get("stream"), dict):
        raise SourceTreatmentRefused("mask_source_mismatch", "no source identity to compare")
    if identity.get("sha256") != source["sha256"] or identity.get("size") != source.get("size"):
        raise SourceTreatmentRefused("mask_source_mismatch",
                                     f"source is {identity.get('sha256')}, mask was made on {source['sha256']}")
    stream = _dict(source.get("stream"), "source.stream")
    for k in IDENTITY_STREAM_KEYS:
        if stream.get(k) != identity["stream"].get(k):
            raise SourceTreatmentRefused("mask_source_mismatch",
                                         f"stream {k}: mask {stream.get(k)!r}, "
                                         f"source {identity['stream'].get(k)!r}")
    return source


def _check_clock(clock: dict, stream: dict) -> dict:
    """k is derived from the probed clock, never assumed to be 30 fps."""
    tb = _frac(clock.get("time_base"), "clock.time_base")
    fps = _frac(clock.get("fps"), "clock.fps")
    step = _int(clock.get("pts_step"), "clock.pts_step")
    start = _int(clock.get("start_pts"), "clock.start_pts")
    if stream.get("r_frame_rate") != stream.get("avg_frame_rate"):
        raise SourceTreatmentRefused("source_clock_refused",
                                     f"VFR: r {stream.get('r_frame_rate')} avg {stream.get('avg_frame_rate')}")
    if (clock["time_base"] != stream.get("time_base") or clock["fps"] != stream.get("r_frame_rate")
            or start != stream.get("start_pts")):
        raise SourceTreatmentRefused("source_clock_refused", "the clock is not the stream's")
    if step <= 0 or step * tb != 1 / fps:
        raise SourceTreatmentRefused("source_clock_refused",
                                     f"pts_step {step} x {tb} is not one frame at {fps}")
    return clock


def _stream_frames(identity: dict) -> int | None:
    n = identity["stream"].get("nb_frames")
    return int(n) if isinstance(n, str) and n.isdigit() else n if is_int(n) else None


def _check_window(window: dict, clock: dict, start: Any, end: Any,
                  identity: dict) -> tuple[int, int]:
    """Every frame the graph could consume: `floor(start*fps) - 2 ..
    ceil(end*fps) + 2`, inclusive, clamped to the stream (SC-addendum-v2 §2.1).
    Seconds go through their decimal text: `Fraction(238.6) * 30` is the binary
    float's 7157.999…, floored to 7157; `Fraction("238.6") * 30` is 7158."""
    k_first, k_last = _int(window.get("k_first"), "window.k_first"), _int(window.get("k_last"),
                                                                           "window.k_last")
    if not 0 <= k_first <= k_last:
        raise _bad(f"window [{k_first}, {k_last}]")
    if _int(window.get("margin_frames"), "window.margin_frames") != MARGIN_FRAMES:
        raise _bad(f"margin_frames {window['margin_frames']} is not the declared {MARGIN_FRAMES}")
    for v in (start, end):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise _bad(f"clip bound {v!r}")
    if not start < end:
        raise _bad(f"clip bounds [{start}, {end}]")
    fps = Fraction(clock["fps"])
    need_first = max(0, math.floor(Fraction(repr(float(start))) * fps) - MARGIN_FRAMES)
    need_last = math.ceil(Fraction(repr(float(end))) * fps) + MARGIN_FRAMES
    frames = _stream_frames(identity)
    if frames is not None:
        need_last = min(need_last, frames - 1)
    if k_first > need_first or k_last < need_last:
        raise SourceTreatmentRefused("mask_window_does_not_cover_clip",
                                     f"window [{k_first}, {k_last}], the clip reads "
                                     f"[{need_first}, {need_last}]")
    return k_first, k_last


def _check_lines(lines: Any, root: Path, stream: dict) -> dict[str, dict]:
    if not isinstance(lines, list):
        raise _bad("lines is not a list")
    out: dict[str, dict] = {}
    width, height = _int(stream.get("width"), "stream.width"), _int(stream.get("height"),
                                                                     "stream.height")
    for i, line in enumerate(lines):
        line = _dict(line, f"lines[{i}]")
        lid = line.get("id")
        if not isinstance(lid, str) or not lid or lid in out:
            raise _bad(f"lines[{i}].id {lid!r} is missing or repeated")
        k_on, k_off = _opt_int(line.get("k_on"), f"{lid}.k_on"), _opt_int(line.get("k_off"),
                                                                          f"{lid}.k_off")
        if k_on is not None and k_off is not None and not k_on < k_off:
            raise _bad(f"{lid}: k_on {k_on} is not before k_off {k_off}")
        if line.get("karaoke") is not False:
            raise _bad(f"{lid}: karaoke {line.get('karaoke')!r} — only a plain line is treated")
        r = _dict(line.get("rect"), f"{lid}.rect")
        x0, x1, y0, y1 = (_int(r.get(k), f"{lid}.rect.{k}") for k in ("x0", "x1", "y0", "y1"))
        if not (0 <= x0 < x1 < width and 0 <= y0 < y1 < height):
            raise _bad(f"{lid}: rect {r} outside the {width}x{height} frame")
        if x0 % 2 or y0 % 2 or not x1 % 2 or not y1 % 2:
            raise _bad(f"{lid}: rect {r} is not even-aligned outward")
        _check_glyph(_dict(line.get("glyph_png"), f"{lid}.glyph_png"), root, lid)
        out[lid] = line
    return out


def _check_glyph(glyph: dict, root: Path, lid: str) -> None:
    """Hashed from the bytes on disk now; no lookup by name anywhere else."""
    rel, want = glyph.get("file"), glyph.get("sha256")
    if not isinstance(rel, str) or not rel or not _SHA.match(str(want)):
        raise _bad(f"{lid}: glyph_png file/sha256")
    target = (root / rel).resolve()
    if not target.is_relative_to(root.resolve()):
        raise _bad(f"{lid}: glyph {rel!r} is outside the mask store")
    try:
        data = target.read_bytes()
    except OSError as e:
        raise SourceTreatmentRefused("glyph_missing", f"{lid}: {e}") from None
    if sha256_bytes(data) != want:
        raise SourceTreatmentRefused("glyph_hash_mismatch", f"{lid}: {rel} no longer hashes to {want}")


def _check_frames(frames: Any, window: tuple[int, int],
                  lines: dict[str, dict]) -> dict[str, tuple[int, int]]:
    """Half-open runs covering exactly the window; each line one contiguous run
    that agrees with its own `k_on`/`k_off` (an open edge reaches the window's)."""
    if not isinstance(frames, list) or not frames:
        raise _bad("frames is not a non-empty list")
    k_first, end = window[0], window[1] + 1
    at = k_first
    unknown = unverified = 0
    runs: dict[str, list[int]] = {}
    last_line = None
    for i, run in enumerate(frames):
        run = _dict(run, f"frames[{i}]")
        a, b = _int(run.get("k_from"), f"frames[{i}].k_from"), _int(run.get("k_to"),
                                                                     f"frames[{i}].k_to")
        if a != at or not a < b:
            raise _bad(f"frames[{i}] [{a}, {b}) does not continue at {at}")
        at = b
        state, basis = run.get("state"), run.get("basis")
        if basis not in BASES:
            raise _bad(f"frames[{i}].basis {basis!r}")
        line_id = state[5:] if isinstance(state, str) and state.startswith("line:") else None
        if line_id is not None:
            if line_id not in lines:
                raise _bad(f"frames[{i}] names no line: {state!r}")
            if line_id in runs and (last_line != line_id or runs[line_id][1] != a):
                raise _bad(f"{line_id} appears in two separate runs")
            runs.setdefault(line_id, [a, b])[1] = b
        elif state == "unknown":
            unknown += b - a
        elif state != "none_observed":
            raise _bad(f"frames[{i}].state {state!r}")
        last_line = line_id
        unverified += (b - a) if basis == "detector_only" else 0
    if at != end:
        raise _bad(f"frames end at {at}, the window at {end}")
    if unknown:
        raise SourceTreatmentRefused("unknown_frames_in_window",
                                     f"{unknown} of {end - k_first} frames are unknown")
    if unverified:
        raise SourceTreatmentRefused("unverified_run",
                                     f"{unverified} of {end - k_first} frames rest on the detector alone")
    for lid, line in lines.items():
        lo = k_first if line["k_on"] is None else max(line["k_on"], k_first)
        hi = end if line["k_off"] is None else min(line["k_off"], end)
        want = (lo, hi) if lo < hi else None
        got = tuple(runs[lid]) if lid in runs else None
        if want != got:
            raise _bad(f"{lid}: frames give {got}, k_on/k_off give {want}")
    return {lid: (r[0], r[1]) for lid, r in runs.items()}


def _check_strips(strips: Any, clock: dict) -> None:
    """Every inspected strip is labelled with the k and pts it was DECODED at;
    a pts off the step is a VFR or mislabelled frame, not a rounding to fix."""
    if not isinstance(strips, list):
        raise _bad("inspection.strips is not a list")
    step, start = clock["pts_step"], clock["start_pts"]
    for i, s in enumerate(strips):
        s = _dict(s, f"strips[{i}]")
        k, pts = _int(s.get("k"), f"strips[{i}].k"), _int(s.get("pts"), f"strips[{i}].pts")
        if pts != k * step + start:
            raise SourceTreatmentRefused("source_clock_refused",
                                         f"strip k {k} carries pts {pts}, off the step")
