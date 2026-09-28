"""
ClipForge — AI Stream Clipper: what made the proxy, and which proxy frame each
stored analysis frame is.

`proxy_clock` can only address a proxy whose recipe and ffmpeg build are known,
and only through a DECODED proxy PTS. This module records both, and nothing
else: it does not change what gets cut, scored or framed.

  analysis/proxy_provenance.json  written once per proxy by ingest.build_proxy:
      the recipe (its token list and its own version), the ffmpeg build, the
      source and proxy identities, each with a FULL sha256.
  analysis/frames_pts.json        written by ingest.sample_frames: per stored
      frames/frame_NNNNN.jpg, the requested time AND the PTS ffmpeg decoded.

Both sit PARALLEL to the old data. `faces.json.times` and every other artefact
keep what they had; nothing here relabels a requested time as a decoded one.

VERSIONS, and why none of them makes an analysis stale. `stale_artifacts` (and
so `/retry`) compares `meta.analysis_version` with ANALYSIS_VERSION, and this
module touches neither. The recipe version travels with the recipe in
proxy_provenance.json, the reader version with frames_pts.json, and
ADDRESSING_VERSION is never compared with anything stored: the clock is
re-validated on every read against today's VALIDATED_CLOCKS. Bumping any of
them changes what `read_provenance` answers, not whether a project re-runs.

A project built before this existed has neither file. It reads as
`provenance_missing`: never inferred from today's code, never a pass, and
never an obstacle to its legacy path, which reads what it always read.

HASHING. The full sha256 is the content identity and is computed ONCE, when a
source or proxy is registered (inside build_proxy, in a worker thread, with no
DB session open: 101 s for a 14.6 GB source on this rig). It is its own
progress stage and stops between two blocks when the job is cancelled; a
cancelled or refused registration writes no record. A retry that rebuilds
the proxy reuses the source's hash when its partial fingerprint (size, mtime,
first and last MiB) is unchanged. The fingerprint is a cache key, never the
identity, and every later read checks the fingerprint only: no per-frame
rehash.

WHAT EACH ANSWER PROVES. Two states, never merged:
  FULL_HASH    the sha256 was computed over the whole file (`sha256_how` in a
               record; `identity_check` of verify_provenance).
  CACHE_REUSE  the sha256 was taken over because the fingerprint matched, or
               (read_provenance) only the fingerprint was compared. That is
               "the file is ASSUMED unchanged": a same-size edit in the middle
               of a file whose mtime was put back is invisible to it.
`recorded` means the second. Nothing may present it as "fully verified now";
only verify_provenance says `verified`, and it writes nothing back.
Policy for an independent check such as M0: verify_provenance ONCE at the start
of a lot, then read_provenance inside that lot with the files held unchanged;
never a rehash per frame.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from services.clipper import proxy_clock, storage
from services.clipper.ffmpeg_tools import FFmpegError, creationflags, ffmpeg_bin, probe, run

logger = logging.getLogger("clipforge.clipper.provenance")

PROVENANCE_FILE = "proxy_provenance.json"
FRAMES_FILE = "frames_pts.json"
# The stored-frame reader: ingest._frame_cmd with -copyts + showinfo. Bump when
# how a frame's decoded PTS is obtained changes.
READER_VERSION = "frame-grab-showinfo-1"

FULL_HASH = "full_hash"
CACHE_REUSE = "cache_reuse"

_HASH_BLOCK = 1 << 22
_FP_BYTES = 1 << 20
_FP_KIND = "size + mtime_ns + sha256(first MiB + last MiB): a cache key, not a content identity"
_SHOWINFO_TB = re.compile(r"\[Parsed_showinfo_\d+ @ [0-9a-fA-Fx]+\] config in time_base: (\d+)/(\d+)")
_SHOWINFO_N = re.compile(r"\[Parsed_showinfo_\d+ @ [0-9a-fA-Fx]+\] n:\s*(\d+) pts:\s*(-?\d+) ")
_LIB = re.compile(r"^(lib\w+)\s+(\d+)\.\s*(\d+)\.\s*(\d+)\s*/\s*(\d+)\.\s*(\d+)\.\s*(\d+)")
_build_cache: dict[tuple, dict] = {}


class ProvenanceRefused(RuntimeError):
    """A file changed while it was being registered: no record is written."""


def _path(project_id: str, name: str) -> Path:
    return storage.safe_join(project_id, "analysis", name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── The recipe ───────────────────────────────────────────────────────────────

# build_proxy's argument list, its variable parts as placeholders. The version
# is the RECIPE's, recorded with every proxy; proxy_clock validates (recipe,
# ffmpeg build) pairs separately. Change a token and bump the version: proxies
# already on disk were not made by the new recipe.
PROXY_RECIPE_VERSION = "clipper-proxy-recipe-1"
PROXY_RECIPE = (
    "-y", "-loglevel", "error",
    "-i", "{source}",
    "-an", "-sn", "-dn",
    # -2 keeps the aspect ratio and lands on an even height, which H.264
    # requires; even() already guarantees the width.
    "-vf", "scale={width}:-2:flags=bilinear",
    "-r", "{fps}",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
    "-pix_fmt", "yuv420p",
    # Short GOP + faststart: the analysis passes seek constantly, and a
    # 250-frame GOP would make every seek decode from far behind.
    "-g", "{gop}",
    "-movflags", "+faststart",
    "{out}",
)


def proxy_recipe(source: str, out: str, target_w: int, target_fps: float) -> tuple[list[str], dict]:
    """(the arguments after the ffmpeg executable, the recipe record)."""
    params = {"width": int(target_w), "fps": f"{target_fps:g}",
              "gop": f"{max(1, int(round(target_fps)))}"}
    whole = {"{source}": source, "{out}": out}          # paths whole, never .format()ted
    argv = [whole[t] if t in whole else t.format(**params) for t in PROXY_RECIPE]
    return argv, {"version": PROXY_RECIPE_VERSION, "template": list(PROXY_RECIPE),
                  "template_sha256": proxy_clock.template_sha256(PROXY_RECIPE), "params": params}


# ── Identities ───────────────────────────────────────────────────────────────

def sha256_file(path: str | Path, chunk: int | None = None, *,
                check: Callable[[], None] | None = None) -> str:
    """`check` runs before every block and raises to stop (a cancelled job). It
    only reads a flag: no sleep, no progress event per block."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            if check is not None:
                check()
            block = fh.read(chunk or _HASH_BLOCK)
            if not block:
                return h.hexdigest()
            h.update(block)


def partial_fingerprint(path: str | Path) -> dict[str, Any]:
    st = os.stat(path)
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read(_FP_BYTES))
        if st.st_size > _FP_BYTES:
            fh.seek(max(_FP_BYTES, st.st_size - _FP_BYTES))
            h.update(fh.read(_FP_BYTES))
    return {"kind": _FP_KIND, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "head_tail_sha256": h.hexdigest()}


_STREAM_KEYS = ("codec_name", "width", "height", "r_frame_rate", "avg_frame_rate", "time_base",
                "start_time", "start_pts", "nb_frames", "duration_ts")
_FORMAT_KEYS = ("format_name", "start_time", "duration", "size")


def media_identity(path: str | Path, reuse: dict | None = None, *,
                   check: Callable[[], None] | None = None) -> dict[str, Any]:
    """Path, sha256, partial fingerprint and the ffprobe fields the clock reads.

    `reuse` is this file's identity from an earlier registration: its sha256 is
    taken over only when the same path still has the same fingerprint, and the
    result then says CACHE_REUSE, not FULL_HASH."""
    fp = partial_fingerprint(path)
    if (isinstance(reuse, dict) and reuse.get("path") == str(path)
            and reuse.get("fingerprint") == fp and reuse.get("sha256")):
        sha, how = reuse["sha256"], CACHE_REUSE
    else:
        sha, how = sha256_file(path, check=check), FULL_HASH
    data = probe(str(path))
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), {})
    fmt = data.get("format") or {}
    return {"path": str(path), "size": fp["size"], "sha256": sha, "sha256_how": how,
            "fingerprint": fp,
            "stream": {k: video.get(k) for k in _STREAM_KEYS},
            "format": {k: fmt.get(k) for k in _FORMAT_KEYS}}


def ffmpeg_build(exe: str | None = None) -> dict[str, Any]:
    """The concrete ffmpeg build: version line, libav* versions, configuration and
    executable hashes. Cached per (path, size, mtime) for the process.

    Call it with the executable a run WILL use, before that run: record()
    refuses when the file no longer has this size and mtime."""
    exe = exe or ffmpeg_bin()
    try:
        st = os.stat(exe)
    except OSError:
        return {"exe": exe, "version_line": None, "libs": {}, "configuration_sha256": None,
                "exe_sha256": None, "exe_size": None, "exe_mtime_ns": None}
    key = (exe, st.st_size, st.st_mtime_ns)
    if key not in _build_cache:
        lines = run([exe, "-hide_banner", "-version"], timeout=60, what="ffmpeg -version").splitlines()
        conf = next((ln for ln in lines if ln.startswith("configuration:")), "")
        libs = {}
        for ln in lines:
            m = _LIB.match(ln.strip())
            if m:
                built, runtime = ".".join(m.group(2, 3, 4)), ".".join(m.group(5, 6, 7))
                libs[m.group(1)] = built if built == runtime else f"{built}/{runtime}"
        _build_cache[key] = {"exe": exe, "version_line": lines[0] if lines else None, "libs": libs,
                             "configuration_sha256": hashlib.sha256(conf.encode()).hexdigest()
                             if conf else None,
                             "exe_sha256": sha256_file(exe),
                             "exe_size": st.st_size, "exe_mtime_ns": st.st_mtime_ns}
    return dict(_build_cache[key])


def _exe_stat(exe: str) -> tuple[int | None, int | None]:
    try:
        st = os.stat(exe)
    except OSError:
        return None, None
    return st.st_size, st.st_mtime_ns


# ── Per project: the proxy ───────────────────────────────────────────────────

def read_raw(project_id: str) -> dict | None:
    """The stored provenance document, or None when absent or unreadable."""
    return _read_json(_path(project_id, PROVENANCE_FILE))


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def forget(project_id: str) -> None:
    """Drop the record of a proxy that is about to be replaced."""
    _path(project_id, PROVENANCE_FILE).unlink(missing_ok=True)


def record(project_id: str, source: str | Path, proxy: str | Path, recipe: dict,
           previous: dict | None = None, *, build: dict,
           check: Callable[[], None] | None = None) -> dict:
    """Register a freshly built proxy. Blocking (hashes whole files): call it off
    the event loop and outside any DB session.

    `build` is ffmpeg_build() of the executable the encode RAN, taken before it
    ran; resolving it here would name whatever is installed now. `check` is
    passed to every block of the hash (cancellation). Raises ProvenanceRefused,
    and writes nothing, when the executable, the source or the proxy changed
    while this ran."""
    src = media_identity(source, reuse=(previous or {}).get("source_identity"), check=check)
    prx = media_identity(proxy, check=check)
    for who, path, ident in (("source", source, src), ("proxy", proxy, prx)):
        if partial_fingerprint(path) != ident["fingerprint"]:
            raise ProvenanceRefused(f"{who} changed while it was being recorded")
    if _exe_stat(build.get("exe") or "") != (build.get("exe_size"), build.get("exe_mtime_ns")):
        raise ProvenanceRefused("ffmpeg executable changed after it was identified for this encode")
    doc = {"schema": "proxy_provenance/1", "recorded_at": _now(),
           "recipe_version": recipe.get("version"), "recipe": recipe,
           "ffmpeg_build": build, "source_identity": src, "proxy_identity": prx,
           # What the rule said when the proxy was made. Informational only:
           # read_provenance re-validates against today's rule every time.
           "addressing_version_at_build": proxy_clock.ADDRESSING_VERSION,
           "clock_at_build": proxy_clock.clock_validation(recipe, build),
           "domain_at_build": proxy_clock.domain(prx, src)}
    storage.atomic_write_json(_path(project_id, PROVENANCE_FILE), doc, indent=1)
    return doc


def record_unless_failed(project_id: str, source: str | Path, proxy: str | Path, recipe: dict,
                         previous: dict | None = None, *, build: dict,
                         check: Callable[[], None] | None = None) -> dict | None:
    """record() for build_proxy. A failure (a refusal included) is logged and
    leaves no record, which reads as provenance_missing: the proxy is still
    usable by every legacy consumer, so the ingest does not fail. A cancellation
    raised by `check` is not such a failure: it is re-raised and stops the job."""
    from job_queue import JobCancelledError  # local: keeps this module queue-free

    try:
        return record(project_id, source, proxy, recipe, previous, build=build, check=check)
    except JobCancelledError:
        raise
    except Exception:
        logger.warning("clipper proxy provenance not recorded for %s", project_id, exc_info=True)
        return None


def read_provenance(project_id: str, *, source_path: str | Path | None = None,
                    proxy_path: str | Path | None = None) -> dict[str, Any]:
    """The proxy's provenance, re-validated now.

    state: `recorded`, `provenance_missing` (no record: every project built before
    lot 1), or `identity_changed` (a file on disk no longer has the fingerprint it
    was registered with, so the recorded sha256 does not describe it). Only a
    `recorded` answer (or verify_provenance's `verified`) carries a clock a
    consumer may address with.

    `recorded` is a CACHE answer: `identity_check` says CACHE_REUSE for each file
    whose fingerprint was compared, `not_checked` for a path not given. It never
    says the content was re-verified; that is verify_provenance."""
    doc = _read_json(_path(project_id, PROVENANCE_FILE))
    if not doc or not isinstance(doc.get("recipe"), dict) or not isinstance(doc.get("ffmpeg_build"), dict):
        return {"state": "provenance_missing",
                "clock": proxy_clock.clock_validation(None, None)}
    checked = {}
    for who, path in (("source", source_path), ("proxy", proxy_path)):
        checked[who] = "not_checked" if path is None else CACHE_REUSE
        if path is None:
            continue
        # Fingerprint only. A match is "assumed unchanged", not a re-verification.
        ident = doc.get(f"{who}_identity") or {}
        try:
            now = partial_fingerprint(path)
        except OSError as exc:
            return {"state": "identity_changed", "reason": f"{who} unreadable: {exc}",
                    "clock": {"state": "unvalidated", "reasons": [f"{who}_identity_changed"]}}
        if now != ident.get("fingerprint"):
            return {"state": "identity_changed", "reason": f"{who} fingerprint differs from its registration",
                    "clock": {"state": "unvalidated", "reasons": [f"{who}_identity_changed"]}}
    return {"state": "recorded", **doc, "identity_check": checked,
            "clock": proxy_clock.clock_validation(doc["recipe"], doc["ffmpeg_build"]),
            "domain": proxy_clock.domain(doc.get("proxy_identity") or {}, doc.get("source_identity") or {}),
            "addressing_version": proxy_clock.ADDRESSING_VERSION}


def verify_provenance(project_id: str, *, source_path: str | Path, proxy_path: str | Path,
                      check: Callable[[], None] | None = None) -> dict[str, Any]:
    """FULL verification: rehash both files now and compare with the recorded
    sha256. `verified` only when both match; otherwise `identity_changed` (or
    `provenance_missing`). Blocking and as slow as the registration (off the
    loop, outside any DB session). Writes nothing: a later read_provenance is
    still a cache answer."""
    prov = read_provenance(project_id)
    if prov["state"] != "recorded":
        return prov
    for who, path in (("source", source_path), ("proxy", proxy_path)):
        want = (prov.get(f"{who}_identity") or {}).get("sha256")
        try:
            got = sha256_file(path, check=check)
        except OSError as exc:
            got, why = None, f"{who} unreadable: {exc}"
        else:
            why = f"{who} content differs from its recorded sha256"
        if not want or got != want:
            return {"state": "identity_changed", "reason": why,
                    "clock": {"state": "unvalidated", "reasons": [f"{who}_identity_changed"]}}
    return {**prov, "state": "verified", "identity_check": {"source": FULL_HASH, "proxy": FULL_HASH}}


# ── Per stored frame: the decoded PTS ────────────────────────────────────────

def run_showinfo(cmd: Sequence[str], *, timeout: float, what: str) -> dict | None:
    """Run a single-frame grab whose filtergraph is `showinfo` and return the one
    frame's {"pts", "time_base"}, or None when it did not report exactly one.
    Raises FFmpegError like ffmpeg_tools.run on a non-zero exit."""
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout,
                              creationflags=creationflags())
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"{what} timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
        raise FFmpegError(f"{what} failed (rc={proc.returncode}): {tail}")
    return parse_showinfo(proc.stderr or "")


def parse_showinfo(stderr: str) -> dict | None:
    """The written frame is showinfo's n:0. With -frames:v 1 the filter still
    passes one more frame (n:1) before the output stops, measured on 8.1.1; that
    frame is never encoded, so it is ignored. Anything but exactly one n:0 and one
    input time_base is None."""
    tbs = _SHOWINFO_TB.findall(stderr)
    first = [pts for n, pts in _SHOWINFO_N.findall(stderr) if n == "0"]
    if len(tbs) != 1 or len(first) != 1:
        return None
    return {"pts": int(first[0]), "time_base": f"{tbs[0][0]}/{tbs[0][1]}"}


def frame_row(name: str, t: float, state: str, got: dict | None = None, reason: str | None = None) -> dict:
    pts, tb = (got or {}).get("pts"), (got or {}).get("time_base")
    return {"file": name, "t_requested": t, "state": state, "reason": reason,
            "proxy_pts_decoded": pts, "proxy_time_base": tb,
            "slot": proxy_clock.slot_of(pts, tb) if state == "decoded" else None}


def record_frames(project_id: str, proxy_path: str | Path, rows: list[dict],
                  out: Path | None = None) -> dict:
    """Write frames_pts.json for one sample_frames run. The proxy it describes is
    named by its REGISTERED sha256 (checked by fingerprint, not rehashed, and
    labelled so: identity_check CACHE_REUSE). `out`: the analysis generation's
    own copy (OW1); None keeps the flat analysis/frames_pts.json."""
    prov = read_provenance(project_id, proxy_path=proxy_path)
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in ("decoded", "pts_unknown", "failed")}
    doc = {"schema": "frames_pts/1", "recorded_at": _now(), "reader_version": READER_VERSION,
           "proxy": {"provenance": prov["state"],
                     "sha256": (prov.get("proxy_identity") or {}).get("sha256")
                     if prov["state"] == "recorded" else None,
                     "identity_check": (prov.get("identity_check") or {}).get("proxy")},
           "requested": len(rows), **counts, "frames": rows}
    storage.atomic_write_json(out or _path(project_id, FRAMES_FILE), doc, separators=(",", ":"))
    return doc


def read_frames(project_id: str, path: Path | None = None) -> dict[str, Any]:
    """frames_pts.json, or `provenance_missing` for a project whose frames were
    grabbed before it existed (those keep `faces.json.times` only). `path`: a
    generation's copy (`analysis_generation.Context.frames_pts_path()`)."""
    doc = _read_json(path or _path(project_id, FRAMES_FILE))
    if not doc or not isinstance(doc.get("frames"), list):
        return {"state": "provenance_missing"}
    return {"state": "recorded", **doc}
