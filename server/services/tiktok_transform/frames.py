"""
ClipForge — Video Transformare TikTok: frame extraction + ranking (wizard step 2).

`extract_candidates()` finds visually meaningful frames in the source clip via
two ffmpeg passes:
  * scene-change detection (`select='gt(scene,threshold)'`) — catches cuts and
    reveals, which is where the interesting "before/after" moments live;
  * a regular-interval fallback (`fps=1/interval`) — so a long, mostly-static
    shot (no scene changes at all) still yields baseline timeline coverage.

Both passes are read via ffmpeg's `showinfo` filter (parsed from stderr) to
get each written frame's real presentation timestamp. Frames are then merged
chronologically, deduped (near-identical timestamps collapse to whichever is
sharper), scored (sharpness via variance-of-Laplacian + a near-black check),
and reduced to ~`target` frames spread evenly across the clip — always
keeping the first and last surviving frame, since the "before" shot and the
final reveal are what step 3 (vision) and step 7 (thumbnails) most need.

ffmpeg shell-out idiom copied from services/speed_match.py /
services/caption_overlays.py (bin resolution + Windows creationflags).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import cv2

from config import settings

logger = logging.getLogger("clipforge.tiktok.frames")

ProgressFn = Optional[Callable[[float, str], None]]

# Frames whose sharpness normalizes below this floor (relative to the sharpest
# frame in the candidate pool) are almost certainly blur/motion smear.
_BLUR_FLOOR = 0.05
# Mean-luma floor (0-255 grayscale) below which a frame counts as "near black".
_BLACK_FLOOR = 12.0
# Candidates within this many seconds of an already-accepted frame are treated
# as the same shot; only the sharper one survives.
_DEDUPE_GAP_S = 0.45


def _ffmpeg_bin() -> str:
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_bin() -> str:
    f = _ffmpeg_bin()
    p = f.replace("ffmpeg", "ffprobe")
    if Path(p).exists() or p == "ffprobe":
        return p
    return shutil.which("ffprobe") or "ffprobe"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def _probe_duration(video_path: str) -> float:
    r = subprocess.run(
        [
            _ffprobe_bin(), "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True, text=True, creationflags=_creationflags(),
        timeout=60,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"ffprobe duration failed for {video_path}: {r.stderr[-300:]}")
    return float(r.stdout.strip())


def _extract_pass(
    video_path: str, vf: str, out_pattern: Path, timeout: int,
) -> list[tuple[float, Path]]:
    """Run one ffmpeg extraction pass; return [(pts_time, file)] chronologically.

    `showinfo` logs each frame's real pts_time at AV_LOG_INFO as it passes
    through the filter chain, in the same order the numbered output files are
    written — so zipping the parsed timestamps with the sorted file glob lines
    them up.
    """
    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg, "-y", "-loglevel", "info",
        "-i", str(video_path),
        "-vf", vf,
        "-vsync", "vfr",
        "-qscale:v", "2",
        str(out_pattern),
    ]
    r = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=_creationflags(),
        timeout=timeout,
    )
    stderr = r.stderr or ""
    if r.returncode != 0:
        # The `select` scene-detect filter legitimately matches zero frames on
        # clips with no big scene changes (e.g. a static talking-head shot) —
        # ffmpeg's image2 muxer then errors because it received no packets.
        # That is exactly the case the regular-interval pass exists to cover,
        # not a real failure, so treat it as "this pass found nothing".
        if "Nothing was written into output file" in stderr:
            return []
        raise RuntimeError(f"ffmpeg frame-extract pass failed: {stderr[-500:]}")
    times = [float(m) for m in re.findall(r"pts_time:([\d.]+)", stderr)]
    files = sorted(out_pattern.parent.glob(out_pattern.name.replace("%04d", "*")))
    n = min(len(times), len(files))
    return list(zip(times[:n], files[:n]))


def _score_frame(path: Path) -> tuple[float, float]:
    """Return (sharpness, brightness): variance-of-Laplacian + mean grayscale luma."""
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0, 0.0
    sharpness = float(cv2.Laplacian(img, cv2.CV_64F).var())
    brightness = float(img.mean())
    return sharpness, brightness


def _select_spread(
    pool: list[dict], target: int, min_frames: int, max_frames: int, duration: float,
) -> list[dict]:
    """Reduce `pool` (chronological, already scored) to ~`target` frames spread
    across the timeline, always keeping the earliest and latest candidate —
    the "before" shot and the final reveal matter most downstream.
    """
    pool = sorted(pool, key=lambda c: c["t"])
    desired = max(min_frames, min(target, max_frames, len(pool)))
    if len(pool) <= desired:
        return pool

    # Bucket the timeline into `desired` equal slices; keep the best-scoring
    # candidate per slice so selections spread evenly instead of clumping
    # wherever scene-detect happened to fire most.
    bucket_w = duration / desired
    buckets: dict[int, dict] = {}
    for cand in pool:
        idx = min(desired - 1, int(cand["t"] / bucket_w)) if bucket_w > 0 else 0
        cur = buckets.get(idx)
        if cur is None or cand["score"] > cur["score"]:
            buckets[idx] = cand
    chosen = {id(c): c for c in buckets.values()}

    # Some slices may end up empty on lumpy content (all candidates fell into
    # neighboring buckets) — backfill with the next-best unchosen candidates
    # so the count still lands near `desired`.
    if len(chosen) < desired:
        leftovers = sorted(
            (c for c in pool if id(c) not in chosen), key=lambda c: -c["score"],
        )
        for c in leftovers:
            if len(chosen) >= desired:
                break
            chosen[id(c)] = c

    first, last = pool[0], pool[-1]
    for edge in (first, last):
        if id(edge) in chosen:
            continue
        if len(chosen) < max_frames:
            chosen[id(edge)] = edge
        else:
            # Already at the cap — bump the weakest non-edge pick to make room.
            worst_id = min(
                (cid for cid in chosen if cid not in (id(first), id(last))),
                key=lambda cid: chosen[cid]["score"],
                default=None,
            )
            if worst_id is not None:
                del chosen[worst_id]
                chosen[id(edge)] = edge

    return sorted(chosen.values(), key=lambda c: c["t"])


def extract_candidates(
    video_path: str,
    out_dir: str,
    *,
    target: int = 20,
    min_frames: int = 12,
    max_frames: int = 30,
    scene_threshold: float = 0.28,
    on_progress: ProgressFn = None,
) -> list[dict]:
    """Extract, score, and select candidate frames from `video_path` into `out_dir`.

    Returns a chronological, deduped list of
    [{"index", "t", "file", "sharpness", "score"}] with `min_frames` <=
    len(result) <= `max_frames` (best effort — a very short or degenerate
    source clip may still yield fewer than `min_frames`).
    """
    video_path = str(video_path)
    if not Path(video_path).exists():
        raise FileNotFoundError(video_path)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("frame_*.jpg"):
        stale.unlink(missing_ok=True)

    min_frames = max(1, min_frames)
    max_frames = max(min_frames, max_frames)
    target = max(min_frames, min(target, max_frames))

    if on_progress:
        on_progress(0.02, "Se analizează durata clipului")
    duration = _probe_duration(video_path)
    if duration <= 0:
        raise RuntimeError(f"video has zero duration: {video_path}")

    workdir = Path(tempfile.mkdtemp(prefix="tiktok_frames_", dir=str(settings.temp_dir)))
    try:
        # Pass 1: scene-change detection — catches cuts/reveals, the frames
        # most likely to matter for a "before/after" narrative.
        if on_progress:
            on_progress(0.08, "Se detectează schimbările de scenă")
        scene_vf = f"select='gt(scene,{scene_threshold})',showinfo"
        scene_hits = _extract_pass(
            video_path, scene_vf, workdir / "scene_%04d.jpg", timeout=600,
        )

        # Pass 2: regular-interval fallback so a static clip (no scene changes
        # at all) still yields baseline coverage across the timeline.
        if on_progress:
            on_progress(0.35, "Se eșantionează cadre la interval regulat")
        n_baseline = max(min_frames, target)
        interval = max(0.34, duration / n_baseline)
        interval_vf = f"fps=1/{interval:.4f},showinfo"
        interval_hits = _extract_pass(
            video_path, interval_vf, workdir / "interval_%04d.jpg", timeout=600,
        )

        merged = sorted(scene_hits + interval_hits, key=lambda p: p[0])
        if not merged:
            raise RuntimeError("no frames extracted from either ffmpeg pass")

        if on_progress:
            on_progress(0.55, "Se scorează cadrele (claritate/expunere)")
        raw: list[dict] = [
            {"t": t, "path": path, **dict(zip(("sharpness", "brightness"), _score_frame(path)))}
            for t, path in merged
        ]

        # Dedupe: scene-detect and the interval pass can both fire within the
        # same shot; keep only the sharper of any two frames that close together.
        raw.sort(key=lambda c: c["t"])
        deduped: list[dict] = []
        for cand in raw:
            if deduped and cand["t"] - deduped[-1]["t"] < _DEDUPE_GAP_S:
                if cand["sharpness"] > deduped[-1]["sharpness"]:
                    deduped[-1] = cand
                continue
            deduped.append(cand)

        max_sharp = max((c["sharpness"] for c in deduped), default=0.0) or 1.0
        for c in deduped:
            norm_sharp = c["sharpness"] / max_sharp
            black_penalty = (
                1.0 if c["brightness"] >= _BLACK_FLOOR else c["brightness"] / _BLACK_FLOOR
            )
            c["score"] = round(0.75 * norm_sharp + 0.25 * black_penalty, 4)
            c["rejected"] = c["brightness"] < _BLACK_FLOOR or norm_sharp < _BLUR_FLOOR

        if on_progress:
            on_progress(0.7, "Se aleg cele mai relevante cadre")
        good = [c for c in deduped if not c["rejected"]]
        if len(good) >= min_frames:
            pool = good
        else:
            # Too few clean frames to hit the floor — relax the black/blur
            # reject and take the best-scoring frames overall instead.
            pool = sorted(deduped, key=lambda c: -c["score"])[: max(min_frames, len(good))]
            pool.sort(key=lambda c: c["t"])

        selected = _select_spread(pool, target, min_frames, max_frames, duration)

        if on_progress:
            on_progress(0.9, "Se salvează cadrele selectate")
        result: list[dict] = []
        for i, cand in enumerate(selected):
            dest = out / f"frame_{i:04d}.jpg"
            shutil.copy2(cand["path"], dest)
            result.append(
                {
                    "index": i,
                    "t": round(cand["t"], 3),
                    "file": str(dest),
                    "sharpness": round(cand["sharpness"], 2),
                    "score": cand["score"],
                }
            )

        if on_progress:
            on_progress(1.0, f"{len(result)} cadre extrase")
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
