"""The source patch itself: decode the window, treat each frame's segment, write raw yuv420p.

Split out of `source_treatment_render` (rule 2); that module gates, checks and
lays the patch into the graph, this one makes the pixels. The recipe and every
difference from SC1's are described there.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from fractions import Fraction
from pathlib import Path

from services.clipper import source_treatment as st
from services.clipper.source_treatment import BLUR, ERASE, SourceTreatmentRefused

_SHOWINFO = re.compile(r"\bn:\s*\d+\s+pts:\s*(-?\d+)")


# ── the patch ────────────────────────────────────────────────────────────────

def _even_box(x0: int, y0: int, x1: int, y1: int, w: int, h: int) -> tuple[int, int, int, int]:
    """Half-open, even-aligned outward, clamped to the frame."""
    return (max(0, x0 - x0 % 2), max(0, y0 - y0 % 2), min(w, x1 + x1 % 2), min(h, y1 + y1 % 2))


def geometry(mask: dict, segs: list[dict], values: dict) -> dict:
    """The patch box (union of the treated lines' rects) and the decoded
    context around it: blur's own `context_px`; erase needs the top-hat's
    support (twice its half-width) and Telea's radius outside the rect."""
    stream = mask["doc"]["source"]["stream"]
    w, h = stream["width"], stream["height"]
    rects = [mask["lines"][lid]["rect"] for lid in sorted({s["line_id"] for s in segs})]
    box = _even_box(min(r["x0"] for r in rects), min(r["y0"] for r in rects),
                    max(r["x1"] for r in rects) + 1, max(r["y1"] for r in rects) + 1, w, h)
    ctx = 0
    if BLUR in values:
        ctx = max(ctx, values[BLUR]["context_px"])
    if ERASE in values:
        ctx = max(ctx, 2 * (values[ERASE]["footprint"]["tophat"] // 2),
                  max(values[ERASE]["telea_radius"]))
    return {"patch": box, "context": _even_box(box[0] - ctx, box[1] - ctx,
                                                box[2] + ctx, box[3] + ctx, w, h)}


def _decode(src: str, clock: dict, k_from: int, count: int, box: tuple, log: Path):
    """Yield `(k, Y, U, V)` for source frames k_from.., cropped to `box`.

    Each frame's k comes from the pts ffmpeg reports for it (`showinfo`),
    checked after the run against the exact expected run of pts. stderr goes to
    a file: a chatty child with a full stderr pipe blocks forever (CLAUDE.md)."""
    import numpy as np

    from services.clipper.ffmpeg_tools import ffmpeg_bin

    step, start = clock["pts_step"], clock["start_pts"]
    tb = Fraction(clock["time_base"])
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    lo, hi = k_from * step + start, (k_from + count) * step + start
    ss = max(0.0, float(Fraction(lo - start) * tb) - 1.0)
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostats", "-loglevel", "info", "-copyts",
           "-ss", f"{ss:.6f}", "-i", str(src), "-map", "0:v:0", "-an",
           "-vf", f"trim=start_pts={lo}:end_pts={hi},crop={w}:{h}:{x0}:{y0},showinfo",
           "-f", "rawvideo", "-pix_fmt", "yuv420p", "pipe:1"]
    size = w * h + 2 * (w // 2) * (h // 2)
    with open(log, "wb") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        got = 0
        try:
            while True:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    if buf:
                        raise SourceTreatmentRefused("patch_build_failed", "a truncated frame")
                    break
                a = np.frombuffer(buf, np.uint8)
                yield (k_from + got, a[:w * h].reshape(h, w),
                       a[w * h:w * h + (w // 2) * (h // 2)].reshape(h // 2, w // 2),
                       a[w * h + (w // 2) * (h // 2):].reshape(h // 2, w // 2))
                got += 1
        finally:
            proc.stdout.close()
            rc = proc.wait()
    pts = [int(p) for p in _SHOWINFO.findall(log.read_text(encoding="utf-8", errors="replace"))]
    if rc != 0 or pts != [lo + i * step for i in range(got)]:
        raise SourceTreatmentRefused("patch_build_failed",
                                     f"decode rc {rc}: {got} frames, pts {pts[:3]}… not the run from {lo}")


def _footprints(src: str, mask: dict, segs: list[dict], values: dict, ctx: tuple, log: Path) -> dict:
    """Per erased line: glyph pixels (white top-hat > min, luma > min) present
    on at least `persist` of the line's frames in the window, inside its rect."""
    import cv2
    import numpy as np

    lines = sorted({s["line_id"] for s in segs if s["treatment"] == ERASE})
    if not lines:
        return {}
    fp = values[ERASE]["footprint"]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (fp["tophat"], fp["tophat"]))
    runs = {lid: mask["line_runs"][lid] for lid in lines}
    lo, hi = min(a for a, _ in runs.values()), max(b for _, b in runs.values())
    h, w = ctx[3] - ctx[1], ctx[2] - ctx[0]
    counts = {lid: np.zeros((h, w), np.int32) for lid in lines}
    frames = dict.fromkeys(lines, 0)
    for k, y, _, _ in _decode(src, mask["clock"], lo, hi - lo, ctx, log):
        lid = next((lid for lid, (a, b) in runs.items() if a <= k < b), None)
        if lid is not None:
            th = cv2.morphologyEx(y, cv2.MORPH_TOPHAT, kernel)
            counts[lid] += (th > fp["tophat_min"]) & (y > fp["luma_min"])
            frames[lid] += 1
    out = {}
    for lid in lines:
        if frames[lid] != runs[lid][1] - runs[lid][0]:
            raise SourceTreatmentRefused("patch_build_failed", f"{lid}: {frames[lid]} frames decoded")
        out[lid] = (counts[lid] / frames[lid] >= fp["persist"]) & _rect_box(mask, lid, ctx)
    return out


def _rect_box(mask: dict, lid: str, ctx: tuple, half: bool = False):
    import numpy as np

    r, d = mask["lines"][lid]["rect"], 2 if half else 1
    box = np.zeros(((ctx[3] - ctx[1]) // d, (ctx[2] - ctx[0]) // d), bool)
    box[(r["y0"] - ctx[1]) // d:(r["y1"] + 1 - ctx[1]) // d,
        (r["x0"] - ctx[0]) // d:(r["x1"] + 1 - ctx[0]) // d] = True
    return box


def _treat(seg: dict, y, u, v, values: dict, masks: dict, box, cbox):
    import cv2

    y, u, v = y.copy(), u.copy(), v.copy()
    if seg["treatment"] == BLUR:
        sy, sc = (float(s) for s in values[BLUR]["sigma"])
        y[box] = cv2.GaussianBlur(y, (0, 0), sy, borderType=cv2.BORDER_REFLECT)[box]
        u[cbox] = cv2.GaussianBlur(u, (0, 0), sc, borderType=cv2.BORDER_REFLECT)[cbox]
        v[cbox] = cv2.GaussianBlur(v, (0, 0), sc, borderType=cv2.BORDER_REFLECT)[cbox]
        return y, u, v
    m, mc = masks[seg["line_id"]]
    r0, r1 = values[ERASE]["telea_radius"]
    return (cv2.inpaint(y, m, r0, cv2.INPAINT_TELEA), cv2.inpaint(u, mc, r1, cv2.INPAINT_TELEA),
            cv2.inpaint(v, mc, r1, cv2.INPAINT_TELEA))


def write_patch(src: str, mask: dict, segs: list[dict], values: dict, geom: dict,
                path: Path) -> dict:
    """Decode the window (+1 spare frame), treat each frame's segment inside its
    line's rect, write the patch box as raw yuv420p. Segments may be empty:
    that patch is the plumbing control, a copy of the source."""
    import cv2
    import numpy as np

    clock = mask["clock"]
    k_first, k_last = mask["window"]
    ctx, (px0, py0, px1, py1) = geom["context"], geom["patch"]
    sy, sx = slice(py0 - ctx[1], py1 - ctx[1]), slice(px0 - ctx[0], px1 - ctx[0])
    csy, csx = slice(sy.start // 2, sy.stop // 2), slice(sx.start // 2, sx.stop // 2)
    masks = {}
    if segs:
        fps_ = _footprints(src, mask, segs, values, ctx, path.with_name("footprint.log"))
        for lid, fp in fps_.items():
            size = values[ERASE]["dilation"]["size"]
            ell = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
            m = cv2.dilate(fp.astype(np.uint8), ell).astype(bool) & _rect_box(mask, lid, ctx)
            mc = m.reshape(m.shape[0] // 2, 2, m.shape[1] // 2, 2).max(axis=(1, 3)).astype(np.uint8)
            mc = cv2.dilate(mc, np.ones((3, 3), np.uint8)).astype(bool) & _rect_box(mask, lid, ctx, True)
            masks[lid] = (m.astype(np.uint8), mc.astype(np.uint8))
    by_k = {k: s for s in segs for k in range(s["k_from"], s["k_to"])}
    digest, written, last = hashlib.sha256(), 0, None
    with open(path, "wb") as f:
        for k, y, u, v in _decode(src, clock, k_first, k_last - k_first + 2, ctx,
                                  path.with_name("decode.log")):
            last = (y, u, v)
            seg = by_k.get(k) if k <= k_last else None
            if seg is not None:
                r = mask["lines"][seg["line_id"]]["rect"]
                box = (slice(r["y0"] - ctx[1], r["y1"] + 1 - ctx[1]),
                       slice(r["x0"] - ctx[0], r["x1"] + 1 - ctx[0]))
                cbox = (slice(box[0].start // 2, box[0].stop // 2),
                        slice(box[1].start // 2, box[1].stop // 2))
                y, u, v = _treat(seg, y, u, v, values, masks, box, cbox)
            for plane in (y[sy, sx], u[csy, csx], v[csy, csx]):
                data = np.ascontiguousarray(plane).tobytes()
                f.write(data)
                digest.update(data)
            written += 1
        n = k_last - k_first + 1
        if written == n and last is not None:
            # The window ends on the stream's last frame: the spare is that
            # frame untouched. Nothing follows it, so nothing can show it.
            for plane in (last[0][sy, sx], last[1][csy, csx], last[2][csy, csx]):
                data = np.ascontiguousarray(plane).tobytes()
                f.write(data)
                digest.update(data)
            written += 1
    if written != n + 1:
        raise SourceTreatmentRefused("patch_build_failed", f"{written} patch frames for a window of {n}")
    return {"sha256": digest.hexdigest(), "bytes": path.stat().st_size, "frames": written,
            "w": px1 - px0, "h": py1 - py0, "x": px0, "y": py0,
            "footprint_px": {lid: int(m[0].sum()) for lid, m in masks.items()}}


def protect_report(mask: dict, segs: list[dict]) -> list[dict]:
    """Informational (SC-addendum §5): treated frames whose line rect meets a
    region the mask's author asked to protect. Reported, never gated."""
    out = []
    for p in (mask["doc"].get("provenance") or {}).get("protect") or []:
        keys = ("k_from", "k_to", "x0", "x1", "y0", "y1")
        if not isinstance(p, dict) or not all(st.is_int(p.get(k)) for k in keys):
            out.append({"label": p.get("label") if isinstance(p, dict) else None, "unreadable": True})
            continue
        touched = 0
        for s in segs:
            r = mask["lines"][s["line_id"]]["rect"]
            if r["x0"] <= p["x1"] and p["x0"] <= r["x1"] and r["y0"] <= p["y1"] and p["y0"] <= r["y1"]:
                touched += max(0, min(s["k_to"], p["k_to"]) - max(s["k_from"], p["k_from"]))
        out.append({"label": p.get("label"), "treated_frames_touching": touched})
    return out
