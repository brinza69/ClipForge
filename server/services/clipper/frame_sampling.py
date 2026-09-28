"""
ClipForge — AI Stream Clipper: the sampled analysis frames.

`sample_frames` and `_sample_frames_sync` moved here verbatim from ingest.py
when OW1 took that file past the 500-line limit; ingest re-exports both. What
OW1 added: the caller's `frames_dir` / `frames_pts` (the attempt's own
generation), the stop checked before every ffmpeg, and `hold`, which keeps the
attempt's cleanup waiting until this thread ends.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from config import settings
from services.clipper import attempt_stop, proxy_provenance, storage

logger = logging.getLogger("clipforge.clipper.ingest")


async def sample_frames(project_id: str, proxy_path: str, times: list[float], *,
                        frames_dir: Path | None = None, frames_pts: Path | None = None,
                        stop=None, hold=None) -> list[str]:
    """Grab one JPEG per timestamp from the proxy; returns the paths written.

    A timestamp past the end of a stream (or in a corrupt region) is skipped, not
    fatal: the sampling grid is built from estimates and losing a frame costs one
    sample out of hundreds.

    OW1: the analysis passes its generation's `frames_dir` and `frames_pts` so
    no attempt writes into the shared frames/ (AD3H saw two attempts' JPEGs
    interleave there); `stop` is checked before every ffmpeg; `hold` wraps the
    thread body so the attempt's cleanup waits for it (attempt_stop.Threads).
    """
    if not times:
        return []
    storage.ensure_dirs(project_id)
    frames_dir = frames_dir or storage.paths(project_id)["frames_dir"]
    frames_dir.mkdir(parents=True, exist_ok=True)

    cap = int(settings.clipper_max_sampled_frames or 0)
    wanted = [max(0.0, float(t or 0.0)) for t in times]
    if cap > 0 and len(wanted) > cap:
        logger.info(f"clipper frame sampling capped at {cap} (asked for {len(wanted)})")
        wanted = wanted[:cap]

    # One executor hop for the whole batch: N short ffmpeg runs, sequential, so a
    # 400-frame grid does not spawn 400 threads or 400 concurrent decoders.
    def run() -> list[str]:
        with hold or contextlib.nullcontext():
            return _sample_frames_sync(project_id, proxy_path, frames_dir, wanted,
                                       frames_pts, stop)
    from services.clipper.ingest import _in_thread

    return await _in_thread(run)


def _sample_frames_sync(project_id: str, proxy_path: str, frames_dir: Path,
                        times: list[float], frames_pts: Path | None = None,
                        stop=None) -> list[str]:
    written: list[str] = []
    # One row per REQUESTED time, failures included: frames_pts.json's
    # denominator is what was asked for, not what came back.
    rows: list[dict] = []
    failures = 0
    from services.clipper.ingest import _frame_cmd

    for i, t in enumerate(times):
        attempt_stop.check(stop)
        out = frames_dir / f"frame_{i:05d}.jpg"
        try:
            got = proxy_provenance.run_showinfo(
                _frame_cmd(proxy_path, t, out, quality=4, decoded_pts=True),
                timeout=60, what="frame sample")
        except RuntimeError as exc:
            failures += 1
            rows.append(proxy_provenance.frame_row(out.name, t, "failed", reason=str(exc)[-200:]))
            continue
        if out.exists() and out.stat().st_size > 0:
            written.append(str(out))
            rows.append(proxy_provenance.frame_row(
                out.name, t, "decoded" if got else "pts_unknown", got,
                None if got else "showinfo did not report exactly one frame"))
        else:
            failures += 1
            rows.append(proxy_provenance.frame_row(out.name, t, "failed", reason="no frame written"))
    if failures:
        logger.warning(f"clipper frame sampling skipped {failures}/{len(times)} timestamps")
    if frames_pts is not None:
        # The generation's copy is REQUIRED (its manifest lists it): no swallow.
        proxy_provenance.record_frames(project_id, proxy_path, rows, out=frames_pts)
        return written
    try:
        proxy_provenance.record_frames(project_id, proxy_path, rows)
    except Exception:
        logger.warning("clipper frames_pts not recorded for %s", project_id, exc_info=True)
    return written
