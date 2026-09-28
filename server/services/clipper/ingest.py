"""
ClipForge — AI Stream Clipper: source ingestion and derived-media building.

Everything downstream of this module reads the *proxy*, never the source: a
480px/10fps H.264 file is two orders of magnitude cheaper to seek and decode
than a 6-hour 1080p60 VOD, and every Pass-A/B/C signal we compute is robust at
that resolution. The full-resolution source is opened exactly once per export.

URL handling delegates to services/downloader.py (yt-dlp, TikTok silent-file
remux, and a 10-pattern error classification table already live there) after
services/clipper/urlguard.py has cleared the URL. Nothing here re-implements
either — this module owns only the placement of the results under
storage.paths() and the derived proxy / speech track / frames.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from config import settings
from services import downloader
from services.clipper import proxy_provenance, storage
from services.clipper.ffmpeg_tools import even, ffmpeg_bin, run, video_info
from services.clipper.urlguard import UrlRejected, check_url

logger = logging.getLogger("clipforge.clipper.ingest")

ProgressFn = Callable[[float, str], Any]
CancelFn = Callable[[], bool]


# ── Small shared helpers ─────────────────────────────────────────────────────

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

        raise JobCancelledError("Ingest cancelled by user.")


async def _in_thread(fn: Callable[[], Any]) -> Any:
    """Run a blocking call (ffmpeg, a multi-GB copy) off the event loop."""
    return await asyncio.get_event_loop().run_in_executor(None, fn)


async def _ffmpeg(cmd: list[str], *, timeout: float, what: str) -> None:
    await _in_thread(lambda: run(cmd, timeout=timeout, what=what))


def _encode_timeout(duration: float) -> float:
    """Wall-clock budget for a full-length transcode of `duration` seconds.

    1x realtime with a 15-minute floor: the proxy encode runs far faster than
    that, so this only ever fires on a genuinely stuck child process."""
    return max(900.0, float(duration or 0.0))


def _gb(n: float) -> str:
    return f"{n / 1_073_741_824:.1f} GB"


def _rejected(exc: UrlRejected) -> dict[str, str]:
    """UrlRejected -> the {error, error_code, suggestion} shape the router turns
    into a 400. Deliberately not exc.to_dict(), whose keys are {code, message,
    suggestion}: callers of probe_source must not have to branch on which of the
    two failure paths (guard or yt-dlp) produced the dict."""
    return {
        "error": exc.message or str(exc),
        "error_code": exc.code,
        "suggestion": exc.suggestion or "Paste a public http(s) video link.",
    }


# ── Preflight ────────────────────────────────────────────────────────────────

def check_disk_space(min_free_bytes: int) -> None:
    """Raise RuntimeError unless the clipper volume has `min_free_bytes` free.

    A 6-hour source plus its proxy, wav, frames and exports easily runs to tens
    of GB; finding that out halfway through a 40-minute analysis wastes far more
    of the user's time than one stat call at the door."""
    if not min_free_bytes or min_free_bytes <= 0:
        return

    # The clipper dir may not exist on a first run — walk up to the nearest
    # ancestor that does, since free space is a property of the volume.
    probe_dir = Path(settings.clipper_dir)
    while not probe_dir.exists() and probe_dir.parent != probe_dir:
        probe_dir = probe_dir.parent

    try:
        free = shutil.disk_usage(probe_dir).free
    except OSError as exc:
        raise RuntimeError(f"Could not read free disk space for {probe_dir}: {exc}") from exc

    if free < min_free_bytes:
        raise RuntimeError(
            f"Not enough free disk space on {probe_dir}: {_gb(free)} available, "
            f"{_gb(min_free_bytes)} required. Free up space or delete old clipper projects."
        )


async def probe_source(url: str) -> dict[str, Any]:
    """URL policy check + yt-dlp metadata, WITHOUT downloading.

    Returns the metadata dict, or {'error','error_code','suggestion'} — never
    raises for a bad source, because the router maps the error dict to a 400.
    """
    try:
        guard = check_url(url)
    except UrlRejected as exc:
        logger.info(f"clipper source rejected by urlguard [{exc.code}]: {exc.message}")
        return _rejected(exc)

    meta = await downloader.fetch_metadata(guard.get("url") or url)
    if meta.get("error"):
        return meta

    # yt-dlp happily hands back metadata for an in-progress broadcast; the whole
    # pipeline assumes a fixed duration and a complete transcript.
    if meta.get("is_live"):
        return {
            "error": "This source is currently live.",
            "error_code": "live_stream",
            "suggestion": "Wait for the VOD to be published, then analyse that.",
        }

    duration = float(meta.get("duration") or 0.0)
    limit = float(settings.clipper_max_source_duration_s or 0.0)
    if limit > 0 and duration > limit:
        return {
            "error": f"Source is {duration / 3600:.1f} h long, the limit is {limit / 3600:.1f} h.",
            "error_code": "source_too_long",
            "suggestion": "Trim the source first, or raise CLIPFORGE_CLIPPER_MAX_SOURCE_DURATION_S.",
        }

    return meta


# ── Ingestion ────────────────────────────────────────────────────────────────

async def ingest_source(
    project_id: str,
    *,
    url: str | None,
    local_path: str | None,
    max_duration_s: float,
    min_free_bytes: int,
    on_progress: ProgressFn | None = None,
    is_cancelled: CancelFn | None = None,
) -> dict[str, Any]:
    """Download or copy the source into the project's source/ dir.

    Returns {'video_path','duration','width','height','fps','filesize'} with the
    dimensions read back from the file itself, not from yt-dlp's metadata (which
    reports the pre-merge stream and is frequently None after a mux).
    """
    if not url and not local_path:
        raise RuntimeError("No source given: pass either a URL or a local file path.")

    await _report(on_progress, 0.02, "Validating source")
    check_disk_space(min_free_bytes)
    storage.ensure_dirs(project_id)
    _raise_if_cancelled(is_cancelled)

    if local_path:
        dest = await _ingest_local(project_id, local_path, on_progress, is_cancelled)
    else:
        dest = await _ingest_url(project_id, str(url), max_duration_s, on_progress, is_cancelled)

    info = await _in_thread(lambda: video_info(str(dest)))
    duration = float(info.get("duration") or 0.0)

    if not info.get("has_audio"):
        raise RuntimeError(
            "This source has no audio track. The clipper ranks moments from speech, "
            "so a silent video cannot be analysed."
        )
    if duration <= 0:
        raise RuntimeError(
            f"Could not read a duration from {dest.name}. The file is probably truncated "
            "or still being written."
        )
    if max_duration_s and max_duration_s > 0 and duration > max_duration_s:
        raise RuntimeError(
            f"Source is {duration / 3600:.1f} h long; the limit is {max_duration_s / 3600:.1f} h."
        )

    try:
        filesize = dest.stat().st_size
    except OSError:
        filesize = 0

    logger.info(
        f"clipper ingest done: {dest} "
        f"({info.get('width')}x{info.get('height')} @{info.get('fps')}fps, {duration:.1f}s)"
    )
    return {
        "video_path": str(dest),
        "duration": round(duration, 3),
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
        "fps": float(info.get("fps") or 0.0),
        "filesize": int(filesize),
    }


async def _ingest_local(
    project_id: str,
    local_path: str,
    on_progress: ProgressFn | None,
    is_cancelled: CancelFn | None,
) -> Path:
    src = Path(local_path).expanduser()
    if not src.exists() or not src.is_file():
        raise RuntimeError(f"Local file not found: {local_path}")

    # Probe before copying: rejecting a non-video costs one ffprobe call instead
    # of a multi-GB copy. video_info raises FFmpegError (a RuntimeError) if there
    # is no video stream at all.
    await _report(on_progress, 0.05, "Reading metadata")
    await _in_thread(lambda: video_info(str(src)))
    _raise_if_cancelled(is_cancelled)

    dest = storage.paths(project_id)["source_dir"] / f"source{src.suffix.lower() or '.mp4'}"
    dest.parent.mkdir(parents=True, exist_ok=True)

    # The user's original is never moved or referenced in place — delete_project()
    # wipes this tree, and it must never be able to take their file with it.
    if src.resolve() == dest.resolve():
        return dest

    # ...but a file ALREADY INSIDE this project's source dir is not the user's
    # original any more: `create_project` moved it here, and delete_project()
    # already owns it. Copying it doubles a multi-GB file for nothing.
    #
    # Measured before changing it: every project on this rig held its source
    # twice, under the uploaded name and as source.mp4 — 25.8 GB of byte-identical
    # duplication across eleven projects, confirmed by hashing both copies.
    if src.parent.resolve() == dest.parent.resolve():
        await _report(on_progress, 0.10, "Preparing source")
        await _in_thread(lambda: src.replace(dest))
        return dest

    await _report(on_progress, 0.10, "Downloading")
    await _in_thread(lambda: shutil.copy2(src, dest))
    return dest


async def _ingest_url(
    project_id: str,
    url: str,
    max_duration_s: float,
    on_progress: ProgressFn | None,
    is_cancelled: CancelFn | None,
) -> Path:
    await _report(on_progress, 0.05, "Reading metadata")
    meta = await probe_source(url)
    if meta.get("error"):
        raise RuntimeError(f"{meta['error']} {meta.get('suggestion', '')}".strip())

    # Enforce the caller's own cap before spending bandwidth; probe_source only
    # applied the global settings limit.
    duration = float(meta.get("duration") or 0.0)
    if max_duration_s and max_duration_s > 0 and duration > max_duration_s:
        raise RuntimeError(
            f"Source is {duration / 3600:.1f} h long; the limit is {max_duration_s / 3600:.1f} h."
        )
    _raise_if_cancelled(is_cancelled)

    await _report(on_progress, 0.10, "Downloading")
    result = await downloader.download_video(
        url,
        project_id,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )
    downloaded = Path(str(result.get("video_path") or ""))
    if not downloaded.exists():
        raise RuntimeError("Download finished but produced no file on disk.")

    # downloader writes into media_dir/{project_id}; move it under the clipper
    # project so dir_size_bytes() and delete_project() account for it.
    dest = storage.paths(project_id)["source_dir"] / f"source{downloaded.suffix.lower() or '.mp4'}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if downloaded.resolve() != dest.resolve():
        dest.unlink(missing_ok=True)
        await _in_thread(lambda: shutil.move(str(downloaded), str(dest)))
        _cleanup_download_dir(downloaded.parent)
    return dest


def _cleanup_download_dir(directory: Path) -> None:
    """Remove downloader's now-empty scratch dir. Non-empty means another
    feature is using the same project folder — leave it alone."""
    try:
        directory.rmdir()
    except OSError:
        pass


# ── Derived media ────────────────────────────────────────────────────────────

async def build_proxy(project_id: str, video_path: str, *, width: int = 480, fps: int = 10,
                      on_progress: ProgressFn | None = None,
                      is_cancelled: CancelFn | None = None) -> str:
    """Encode the analysis proxy and return its path.

    Small, keyframe-dense and audio-less: every Pass-A/B pass seeks around this
    file repeatedly, so decode cost here is multiplied by every later stage.

    on_progress(fraction of this stage, message) is called once, when the
    files start being verified; is_cancelled stops that verification between
    two hash blocks.
    """
    storage.ensure_dirs(project_id)
    out = storage.paths(project_id)["proxy"]
    info = await _in_thread(lambda: video_info(str(video_path)))

    src_w = int(info.get("width") or 0)
    src_fps = float(info.get("fps") or 0.0)
    target_w = even(min(int(width), src_w) if src_w > 0 else int(width))
    if target_w < 16:  # a broken probe must not produce a 0-width filtergraph
        target_w = even(width) or 480
    target_fps = float(fps) if fps and fps > 0 else 10.0
    if src_fps > 0:
        target_fps = min(target_fps, src_fps)  # -r above source fps only duplicates frames

    out.parent.mkdir(parents=True, exist_ok=True)
    # The tokens and their reasons live in proxy_provenance.PROXY_RECIPE, which
    # is also what gets recorded as this proxy's recipe.
    argv, recipe = proxy_provenance.proxy_recipe(str(video_path), str(out), target_w, target_fps)
    # The executable is resolved ONCE and identified before it runs: the record
    # must name the build that made this proxy, not whatever is installed later.
    exe = ffmpeg_bin()
    cmd = [exe, *argv]
    # The record describes the proxy this encode is about to overwrite. Its
    # source hash may be reused below; the record itself must not survive.
    previous = proxy_provenance.read_raw(project_id)
    proxy_provenance.forget(project_id)
    try:
        build = await _in_thread(lambda: proxy_provenance.ffmpeg_build(exe))
    except Exception:
        build = None
        logger.warning("clipper ffmpeg build not identified for %s", project_id, exc_info=True)
    await _ffmpeg(cmd, timeout=_encode_timeout(info.get("duration") or 0.0), what="proxy encode")
    logger.info(f"clipper proxy built: {out} ({target_w}px @{target_fps:g}fps)")
    if build is None:
        return str(out)
    # Hashes both files in full, once, off the loop and with no DB session open.
    # A failure is logged and leaves no record; a cancellation stops the job.
    await _report(on_progress, 0.5, "Verifying source and proxy (sha256)")
    await _in_thread(lambda: proxy_provenance.record_unless_failed(
        project_id, video_path, out, recipe, previous, build=build,
        check=lambda: _raise_if_cancelled(is_cancelled)))
    return str(out)


async def extract_audio(project_id: str, video_path: str) -> str:
    """Extract the 16 kHz mono PCM speech track and return its path.

    16 kHz mono s16le is exactly what faster-whisper resamples to internally, so
    doing it once here saves every transcription chunk from repeating the work.
    """
    storage.ensure_dirs(project_id)
    info = await _in_thread(lambda: video_info(str(video_path)))
    if not info.get("has_audio"):
        raise RuntimeError(
            f"{Path(video_path).name} has no audio stream — the clipper needs speech to rank moments."
        )

    out = storage.paths(project_id)["audio"]
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out),
    ]
    await _ffmpeg(cmd, timeout=_encode_timeout(info.get("duration") or 0.0), what="audio extract")
    logger.info(f"clipper speech track extracted: {out}")
    return str(out)


async def extract_thumbnail(project_id: str, video_path: str, t: float, name: str) -> str:
    """Grab a single JPEG at `t` seconds into thumbs/{name}.jpg."""
    storage.ensure_dirs(project_id)
    # `name` is typically a clip id off an HTTP route: strip any path structure
    # and let safe_join prove the result stays inside the project.
    stem = Path(str(name)).name.strip() or "thumb"
    filename = stem if stem.lower().endswith(".jpg") else f"{stem}.jpg"
    out = storage.safe_join(project_id, "thumbs", filename)
    out.parent.mkdir(parents=True, exist_ok=True)

    await _ffmpeg(
        _frame_cmd(video_path, max(0.0, float(t or 0.0)), out, quality=3),
        timeout=120,
        what="thumbnail",
    )
    return str(out)


def _frame_cmd(video_path: str, t: float, out: Path, *, quality: int,
               decoded_pts: bool = False) -> list[str]:
    """Single-frame grab. -ss before -i seeks on the container instead of
    decoding from zero, which is the difference between a 20 ms and a 30 s grab
    two hours into a stream.

    decoded_pts: also report WHICH frame was grabbed. -copyts keeps the file's
    own timestamps and showinfo prints the decoded frame's integer PTS. M0 v2
    measured the JPEG byte-identical to the plain grab on 202 stored frames, so
    frames grabbed either way stay comparable."""
    return [
        ffmpeg_bin(), "-y", "-loglevel", "info" if decoded_pts else "error",
        "-ss", f"{t:.3f}",
        *(["-copyts"] if decoded_pts else []),
        "-i", str(video_path),
        "-frames:v", "1",
        "-an", "-sn", "-dn",
        "-q:v", str(quality),
        *(["-vf", "showinfo"] if decoded_pts else []),
        str(out),
    ]


# Frame sampling moved out at the 500-line limit (OW1); re-exported so
# `ingest.sample_frames` keeps working for every caller.
from services.clipper.frame_sampling import _sample_frames_sync, sample_frames  # noqa: E402,F401
