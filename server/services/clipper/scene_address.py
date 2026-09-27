"""
ClipForge — AI Stream Clipper: the scene pass, and where each cut lies in the source.

signals.scene_timeline has always run one ffmpeg pass over the proxy and kept
`sorted({round(pts_time, 3)})`: the integer PTS and the time_base were thrown
away and duplicates merged. This module runs the SAME decode with `showinfo`
after `select`, so every selected frame keeps its integer PTS, and parses the
legacy list from the same stdout by the same expression: `scenes` is unchanged.

`scenes_addressed` sits NEXT TO `scenes`, never instead of it. No consumer
reads it; an old signals.json has none and nobody requires it.

ONE ROW PER SELECTED FRAME, not per unique float. The row id is the decode
ordinal; `legacy_index` points into the final (sorted, deduplicated, `t > 0`)
legacy list, or is null with the reason. Several rows may point at one index.
The float is only that join key: nothing is computed from it. Counts:
N selected, K eligible after the legacy filter, L unique legacy times.
interval + refused == N and len(scenes) == L, or the pass is `decode_mismatch`.

STATES of a pass (exactly one, each with its reason):
  proxy_missing / decode_failed / decode_mismatch   no rows, `detected: null`.
      decode_mismatch keeps the legacy list parsed validly from stdout: a
      failure of the new evidence must not change the old montage.
  provenance_missing / identity_changed / clock_unvalidated / domain_refused
      one row per selected frame, every row refused with the project reason.
  ok  one row per selected frame: `interval`, or refused with the reason of
      address_slot / interval_for_event. `ok` with none is `detected: 0`.
No retrospective estimate: a legacy or unknown-build proxy gets no interval,
no "consistent with", no -7-frame constant.

Provenance is read BEFORE the decode used as evidence and again AFTER the
whole step; answers that differ make the whole pass `identity_changed`. It is
read_provenance, a fingerprint (CACHE_REUSE) answer, and labelled so — never
"verified".

ISOLATION. interval_for_event refuses isolated=None and False; nothing here
passes True by default. Proxy slots n-2..n+1 come from one local grab, each
emitted raw frame bound to its showinfo PTS; m1, m2, m3 are the mean absolute
differences of the consecutive pairs (not ffmpeg's scene_score, which
subtracts the previous MAFD and reads two consecutive cuts as isolation).
Isolated iff m2 > 0, m1 <= m2/2 and m3 <= m2/2. That is F-offset's clean-cut
rule on the SOURCE (F-offset-result.md:152) moved to the proxy: a DECLARED
HYPOTHESIS, not a demonstrated calibration, and not tuned on the pilot. It
supports only the single-cut hypothesis at 0.1 s: two cuts between two samples
stay invisible, which every interval says in `not_excluded`.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import numpy as np

from services.clipper import proxy_clock
from services.clipper.ffmpeg_tools import FFmpegError, creationflags, ffmpeg_bin, ffprobe_bin

logger = logging.getLogger("clipforge.clipper.scene_address")

# Lives inside scenes_addressed only: bump when how a selected frame's PTS is
# obtained changes. Not ANALYSIS_VERSION, not the proxy recipe, not the clock.
SCENE_READER_VERSION = "scene-select-showinfo-1"
ISOLATION_RULE = ("isolated iff m2 > 0 and m1 <= m2/2 and m3 <= m2/2, MAFD of proxy slots "
                  "(n-2,n-1), (n-1,n), (n,n+1); F-offset's source rule moved to the proxy: a declared "
                  "hypothesis, not a demonstrated calibration")
NOT_EXCLUDED = "two_cuts_between_samples"
NO_ROW_STATES = ("proxy_missing", "decode_failed", "decode_mismatch")

_PTS_RE = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")     # the legacy expression, verbatim
_META_FRAME = re.compile(r"^frame:(\d+)\s+pts:(-?\d+|NOPTS)\s+pts_time:(\S+)")
_SHOWINFO_TB = re.compile(r"\[Parsed_showinfo_\d+ @ [0-9a-fA-Fx]+\] config in time_base: (\d+)/(\d+)")
_SHOWINFO_N = re.compile(r"\[Parsed_showinfo_\d+ @ [0-9a-fA-Fx]+\] n:\s*(\d+) pts:\s*(-?\d+|NOPTS) ")


def _pts(s: str) -> int | None:
    return None if s == "NOPTS" else int(s)


def legacy_times(stdout: str) -> list[float]:
    """What scene_timeline always returned, from the metadata stdout."""
    times = sorted({round(float(m), 3) for m in _PTS_RE.findall(stdout or "")})
    return [t for t in times if t > 0]


# ── 3a: the scene pass ───────────────────────────────────────────────────────

def parse_scene_output(stdout: str, stderr: str) -> dict[str, Any]:
    """Rows from metadata (stdout) and showinfo (stderr) of one decode.

    `times` is always the legacy list. `rows` is None, with `mismatch` saying
    why, when the two reports disagree in count or PTS, the time_base is not
    exactly one, or the rows do not rebuild the legacy list."""
    times = legacy_times(stdout)
    meta: list[dict] = []
    for line in (stdout or "").splitlines():
        m = _META_FRAME.match(line)
        if m:
            meta.append({"k": int(m.group(1)), "pts": _pts(m.group(2)), "line": line, "score": None})
        elif meta and line.startswith("lavfi.scene_score="):
            meta[-1]["score"] = line.split("=", 1)[1]
    info = [(int(n), _pts(p)) for n, p in _SHOWINFO_N.findall(stderr or "")]
    tbs = _SHOWINFO_TB.findall(stderr or "")

    def mismatch(why: str) -> dict:
        return {"times": times, "rows": None, "time_base": None, "mismatch": why}

    if len(tbs) != 1:
        return mismatch(f"showinfo reported {len(tbs)} input time_bases, not one")
    if len(meta) != len(info):
        return mismatch(f"metadata {len(meta)} frames, showinfo {len(info)}")
    tb = f"{tbs[0][0]}/{tbs[0][1]}"
    index = {t: i for i, t in enumerate(times)}
    rows = []
    for i, (m, (n, p)) in enumerate(zip(meta, info)):
        if m["k"] != i or n != i or m["pts"] != p:
            return mismatch(f"frame {i}: metadata frame:{m['k']} pts:{m['pts']}, showinfo n:{n} pts:{p}")
        found = _PTS_RE.findall(m["line"])
        t = round(float(found[0]), 3) if len(found) == 1 else None
        rows.append({"id": i, "proxy_pts": p, "time_base": tb, "t": t, "scene_score": m["score"],
                     "slot": proxy_clock.slot_of(p, tb),
                     "legacy_index": index.get(t) if t is not None and t > 0 else None,
                     "legacy_excluded": None if t is not None and t > 0
                     else ("t_le_zero (legacy filter t > 0)" if t is not None
                           else "pts_time not read by the legacy expression")})
    eligible = sorted({r["t"] for r in rows if r["legacy_index"] is not None})
    if eligible != times or any(r["t"] is not None and r["t"] > 0 and r["legacy_index"] is None
                                for r in rows):
        return mismatch(f"rows rebuild {len(eligible)} legacy times, stdout has {len(times)}")
    return {"times": times, "rows": rows, "time_base": tb, "mismatch": None}


def scene_pass(proxy_path: str, *, threshold: float = 0.30) -> dict[str, Any]:
    """One ffmpeg pass over the proxy: the legacy `times` plus one row per selected frame."""
    base = {"threshold": threshold, "times": [], "rows": None, "time_base": None}
    if not proxy_path or not Path(proxy_path).exists():
        logger.warning("scene_timeline: missing proxy %s", proxy_path)
        return {**base, "state": "proxy_missing", "reason": f"no proxy file ({proxy_path})"}
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin",
        "-i", str(proxy_path),
        "-an",
        # showinfo only after select, so it sees only the selected frames.
        "-vf", f"select='gt(scene,{threshold:.4f})',showinfo,metadata=print:file=-",
        "-f", "null", "-",
    ]
    try:
        # Both pipes drained by capture_output. The whole proxy still has to be
        # decoded, so the default 600 s ceiling is too tight for a multi-hour VOD.
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=3600,
                              creationflags=creationflags())
        if proc.returncode != 0:
            tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
            raise FFmpegError(f"scene detect failed (rc={proc.returncode}): {tail}")
    except subprocess.TimeoutExpired:
        exc = FFmpegError("scene detect timed out after 3600s")
        logger.warning("scene_timeline: %s", exc)
        return {**base, "state": "decode_failed", "reason": str(exc)}
    except FFmpegError as exc:
        logger.warning("scene_timeline: %s", exc)
        return {**base, "state": "decode_failed", "reason": str(exc)[-300:]}
    parsed = parse_scene_output(proc.stdout or "", proc.stderr or "")
    if parsed["mismatch"]:
        return {**base, "times": parsed["times"], "state": "decode_mismatch", "reason": parsed["mismatch"]}
    return {**base, "times": parsed["times"], "rows": parsed["rows"], "time_base": parsed["time_base"],
            "state": "ok", "reason": None}


# ── 3b: the evidence each row is addressed with ──────────────────────────────

def isolation_verdict(mafd: list[float] | None) -> bool | None:
    """True only from three measured MAFDs; (0, 0, 0) is not an isolated cut."""
    if mafd is None or len(mafd) != 3:
        return None
    m1, m2, m3 = (float(x) for x in mafd)
    return bool(m2 > 0 and m1 <= m2 / 2 and m3 <= m2 / 2)


def bind_grab(raw: bytes, stderr: str, n: int, w: int, h: int, time_base: str) -> dict[str, Any]:
    """Tie the raw frames a grab EMITTED to their showinfo PTS: slots n-2..n+1.

    showinfo can report one frame more than was written (the filter passes a
    lookahead frame before -frames:v stops the output: the AD12 trap), so only
    the first E reports are bound, E = frames actually emitted. A gap, a slot
    out of place, another time_base or more than one extra report is refused."""
    def refused(why: str) -> dict:
        return {"isolated": None, "reason": f"isolation_unknown ({why})", "pts": {}, "mafd": None}

    size = w * h
    if size <= 0 or len(raw) % size:
        return refused(f"{len(raw)} bytes is not a whole number of {w}x{h} frames")
    emitted = len(raw) // size
    tbs = _SHOWINFO_TB.findall(stderr or "")
    info = [(int(k), _pts(p)) for k, p in _SHOWINFO_N.findall(stderr or "")]
    if len(tbs) != 1 or f"{tbs[0][0]}/{tbs[0][1]}" != time_base:
        return refused(f"grab time_base {tbs} is not the pass's {time_base}")
    if emitted > 4 or len(info) not in (emitted, emitted + 1) or any(k != i for i, (k, _) in enumerate(info)):
        return refused(f"{emitted} frames emitted, showinfo reported {[k for k, _ in info]}")
    pts = {}
    for i, (_, p) in enumerate(info[:emitted]):
        if proxy_clock.slot_of(p, time_base) != n - 2 + i:
            return refused(f"emitted frame {i} has pts {p}, not slot {n - 2 + i}")
        pts[n - 2 + i] = p
    if emitted < 4:
        return {"isolated": None, "pts": pts, "mafd": None,
                "reason": f"isolation_unknown (grab emitted {emitted} of slots {n - 2}..{n + 1})"}
    f = np.frombuffer(raw, np.uint8).reshape(4, h, w).astype(np.int16)
    mafd = [float(np.mean(np.abs(f[i + 1] - f[i]))) for i in range(3)]
    return {"isolated": isolation_verdict(mafd), "pts": pts, "mafd": mafd, "reason": None}


def isolation_grab(proxy_path: str, n: int, w: int, h: int, time_base: str) -> dict[str, Any]:
    """Decode proxy slots n-2..n+1 in one local grab and measure m1, m2, m3."""
    if n - 2 < 0:
        return {"isolated": None, "reason": "isolation_unknown (slot n-2 is before the stream)",
                "pts": {}, "mafd": None}
    ss = Fraction(n - 2, proxy_clock.PROXY_FPS) - Fraction(5, 100)
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "info",
           "-ss", f"{float(ss):.3f}", "-copyts", "-i", str(proxy_path),
           "-an", "-frames:v", "4", "-vf", "showinfo",
           # passthrough: no CFR dup/drop between the filter and the frames emitted.
           "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=creationflags())
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"isolated": None, "reason": f"isolation_unknown (grab failed: {exc})", "pts": {}, "mafd": None}
    if proc.returncode != 0:
        return {"isolated": None, "reason": f"isolation_unknown (grab rc={proc.returncode})",
                "pts": {}, "mafd": None}
    return bind_grab(proc.stdout, proc.stderr.decode(errors="replace"), n, w, h, time_base)


def read_source_packets(source_path: str, n: int, registered_tb: str, *,
                        windowed: bool = True) -> tuple[list[int], list[int], tuple[int, int]]:
    """Source (pts, duration) around proxy slot n, sorted; ffprobe packets, the
    reader of the evidence (reproduce_synth.py:68-74), restricted with
    -read_intervals to [(n-1)/10 - 2 s, n/10 + 1 s]. Raises ValueError when the
    stream time_base is not the registered one or a packet has no pts/duration."""
    lo = max(Fraction(0), Fraction(n - 1, proxy_clock.PROXY_FPS) - 2)
    hi = Fraction(n, proxy_clock.PROXY_FPS) + 1
    cmd = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
           *(["-read_intervals", f"{float(lo):.3f}%{float(hi):.3f}"] if windowed else []),
           "-show_entries", "stream=time_base:packet=pts,duration", "-of", "json", str(source_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=300,
                              creationflags=creationflags())
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"window_unreadable (ffprobe timed out: {exc})") from exc
    if proc.returncode != 0:
        raise ValueError(f"window_unreadable (ffprobe rc={proc.returncode})")
    data = json.loads(proc.stdout or "{}")
    tb = ((data.get("streams") or [{}])[0]).get("time_base")
    if tb != registered_tb:
        raise ValueError(f"pts_unknown (source time_base {tb} != registered {registered_tb})")
    rows = []
    for pk in data.get("packets") or []:
        if not isinstance(pk.get("pts"), int) or not isinstance(pk.get("duration"), int):
            raise ValueError(f"window_unreadable (packet without pts/duration: {pk})")
        rows.append((pk["pts"], pk["duration"]))
    rows.sort()
    return [p for p, _ in rows], [d for _, d in rows], proxy_clock._tb(tb)


def _sample(a: dict | None) -> dict | None:
    if not a or a.get("state") != "addressed":
        return None
    return {k: a[k] for k in ("slot", "proxy_pts", "source_index", "source_pts")}


def address_row(row: dict, ctx: dict) -> dict:
    """Address one selected frame in an `ok` pass: current slot n, previous slot
    n-1 (its PTS from the isolation grab), then interval_for_event."""
    proxy, source = ctx["proxy"], ctx["source"]
    kw = {"clock": ctx["clock"], "source_nb_frames": ctx["source_nb"], "proxy_nb_frames": ctx["proxy_nb"]}
    n = row["slot"]
    if n is None:     # off the grid or unknown: address_slot says which, before any window
        cur = proxy_clock.address_slot(row["proxy_pts"], proxy, source, [], **kw)
        return {"state": "refused", "reason": cur["reason"], "refused_at": "current"}
    try:
        pts, durs, stb = read_source_packets(ctx["source_path"], n, source["stream"]["time_base"])
    except (ValueError, OSError, FFmpegError) as exc:
        return {"state": "refused", "reason": str(exc)[:300], "refused_at": "source_window"}
    cur = proxy_clock.address_slot(row["proxy_pts"], proxy, source, proxy_clock.window(pts, durs, stb, n), **kw)
    if cur["state"] != "addressed":
        return {"state": "refused", "reason": cur["reason"], "refused_at": "current"}
    grab = isolation_grab(ctx["proxy_path"], n, ctx["width"], ctx["height"], proxy["stream"]["time_base"])
    iso = {"mafd": grab["mafd"], "isolated": grab["isolated"], "reason": grab["reason"]}
    prev = proxy_clock.address_slot(grab["pts"].get(n - 1), proxy, source,
                                    proxy_clock.window(pts, durs, stb, n - 1), **kw)
    out = {"samples": {"previous": _sample(prev), "current": _sample(cur)}, "isolation": iso}
    if prev["state"] != "addressed":
        return {**out, "state": "refused", "reason": prev["reason"], "refused_at": "previous"}
    got = proxy_clock.interval_for_event(prev, cur, isolated=grab["isolated"])
    if got["state"] != "interval":
        reason = got["reason"] if grab["reason"] is None or "isolation_unknown" not in got["reason"] \
            else grab["reason"]
        return {**out, "state": "refused", "reason": reason, "refused_at": "interval"}
    return {**out, "state": "interval", "reason": None, "interval": {**got, "not_excluded": NOT_EXCLUDED}}


# ── 3b: the state table ──────────────────────────────────────────────────────

def _int(v) -> int | None:
    try:
        return int(v) if not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def project_state(prov: dict, time_base: str | None) -> tuple[str, str | None, list[str]]:
    """(state, reason, all reasons) of a provenance answer, for rows that decoded."""
    st = prov.get("state")
    if st == "provenance_missing":
        return st, (prov.get("clock") or {}).get("reasons", ["provenance_missing"])[0], []
    if st != "recorded":
        return "identity_changed", f"identity_changed ({prov.get('reason') or st})", []
    if set((prov.get("identity_check") or {}).values()) != {"cache_reuse"}:
        return "identity_changed", f"identity_changed (not checked: {prov.get('identity_check')})", []
    clock = prov.get("clock") or {}
    if clock.get("state") != "validated":
        why = (clock.get("reasons") or ["clock not validated"])[0]
        return "clock_unvalidated", f"clock_unvalidated ({why})", []
    why = list((prov.get("domain") or {}).get("reasons") or [])
    if (prov.get("domain") or {}).get("state") != "ok" and not why:
        why.append("domain_not_ok")
    ptb = ((prov.get("proxy_identity") or {}).get("stream") or {}).get("time_base")
    if time_base != ptb:
        why.append(f"pts_unknown (decoded time_base {time_base} != registered proxy {ptb})")
    for who in ("proxy", "source"):
        if _int(((prov.get(f"{who}_identity") or {}).get("stream") or {}).get("nb_frames")) is None:
            why.append(f"nb_frames_unknown ({who})")
    if why:
        return "domain_refused", f"domain_refused ({why[0]})", why
    return "ok", None, []


def _identity_key(prov: dict) -> tuple:
    ids = [prov.get(f"{w}_identity") or {} for w in ("source", "proxy")]
    return (prov.get("state"), prov.get("recorded_at"), *[(i.get("sha256"), json.dumps(i.get("fingerprint"),
            sort_keys=True)) for i in ids], (prov.get("ffmpeg_build") or {}).get("exe_sha256"))


def _provenance_summary(before: dict, after: dict | None) -> dict:
    return {"before": before.get("state"), "after": (after or {}).get("state"),
            "identity_check": "cache_reuse: fingerprint compared, content never re-verified here",
            "clock": (before.get("clock") or {}).get("clock") or (before.get("clock") or {}).get("state"),
            "proxy_sha256": (before.get("proxy_identity") or {}).get("sha256"),
            "source_sha256": (before.get("source_identity") or {}).get("sha256")}


def _decode_facts(row: dict) -> dict:
    return {k: row[k] for k in ("id", "t", "legacy_index", "legacy_excluded", "proxy_pts", "time_base",
                                "slot", "scene_score")}


def _counts(rows: list[dict], legacy_unique: int) -> dict:
    n = len(rows)
    k = sum(1 for r in rows if r["legacy_index"] is not None)
    by: dict[str, int] = {}
    for r in rows:
        if r["state"] == "refused":
            key = str(r["reason"]).split(" ")[0]
            by[key] = by.get(key, 0) + 1
    return {"detected": n, "interval": sum(1 for r in rows if r["state"] == "interval"),
            "refused": sum(1 for r in rows if r["state"] == "refused"), "by_reason": by,
            "legacy": {"selected": n, "eligible": k, "unique": legacy_unique, "excluded": n - k,
                       "merged": k - legacy_unique}}


def _header(scene: dict) -> dict:
    return {"reader_version": SCENE_READER_VERSION, "addressing_version": proxy_clock.ADDRESSING_VERSION,
            "threshold": scene.get("threshold"), "time_base": scene.get("time_base"),
            "isolation_rule": ISOLATION_RULE}


def assemble(scene: dict, before: dict, read_after: Callable[[], dict],
             addresser: Callable[[dict, dict], dict], ctx: dict | None = None) -> dict:
    """scenes_addressed for one scene pass. `before` is the provenance read
    before the decode; `read_after` reads it again once every row is done."""
    doc: dict[str, Any] = _header(scene)
    legacy_unique = len(scene.get("times") or [])
    if scene["state"] in NO_ROW_STATES or scene.get("rows") is None:
        state = scene["state"] if scene["state"] in NO_ROW_STATES else "decode_mismatch"
        return {**doc, "state": state, "reason": scene.get("reason"), "reasons": [],
                "counts": {"detected": None, "interval": None, "refused": None, "by_reason": {},
                           "legacy": {"selected": None, "eligible": None, "unique": legacy_unique,
                                      "excluded": None, "merged": None}},
                "provenance": _provenance_summary(before, None), "rows": None}
    state, reason, reasons = project_state(before, scene["time_base"])
    rows = []
    for row in scene["rows"]:
        if state == "ok":
            rows.append({**_decode_facts(row), **addresser(row, ctx or {})})
        else:
            rows.append({**_decode_facts(row), "state": "refused", "reason": reason, "refused_at": "project"})
    after = read_after()
    if _identity_key(after) != _identity_key(before):
        reason = (f"identity_changed (provenance {before.get('state')} before the decode, "
                  f"{after.get('state')} after the step)")
        state, reasons = "identity_changed", []
        rows = [{**_decode_facts(r), "state": "refused", "reason": reason, "refused_at": "project"}
                for r in rows]
    counts = _counts(rows, legacy_unique)
    # Denominator checks: every selected frame is a row with a state, and the
    # rows rebuild exactly the legacy list.
    if counts["interval"] + counts["refused"] != counts["detected"] or \
            len({r["legacy_index"] for r in rows if r["legacy_index"] is not None}) != legacy_unique:
        return {**doc, "state": "decode_mismatch", "reasons": [],
                "reason": f"counts_inconsistent ({counts})", "counts": {**counts, "detected": None},
                "provenance": _provenance_summary(before, after), "rows": None}
    return {**doc, "state": state, "reason": reason, "reasons": reasons, "counts": counts,
            "provenance": _provenance_summary(before, after), "rows": rows}


def provenance_now(project_id: str, proxy_path: str) -> dict:
    """read_provenance with the source the record registered and this proxy."""
    from services.clipper import proxy_provenance  # local: the pass above stays storage-free

    src = ((proxy_provenance.read_raw(project_id) or {}).get("source_identity") or {}).get("path")
    return proxy_provenance.read_provenance(project_id, source_path=src, proxy_path=proxy_path)


def not_requested(scene: dict, before: dict) -> dict:
    """scenes_addressed when the costly 3b pass is off (`clipper_scene_addressing`,
    codex-verdict-next-16 §3). The decode's own states still win (a failed decode is
    not "not requested"); otherwise every selected frame keeps its 3a decode facts,
    with `state: not_requested` — never `refused`, and never an empty result: with
    zero scenes it is `not_requested` with `detected: 0`, not `ok`."""
    if scene["state"] in NO_ROW_STATES or scene.get("rows") is None:
        return assemble(scene, before, lambda: before, address_row, {})
    rows = [{**_decode_facts(r), "state": "not_requested", "reason": None} for r in scene["rows"]]
    legacy_unique = len(scene.get("times") or [])
    counts = {**_counts(rows, legacy_unique), "not_requested": len(rows)}
    if len({r["legacy_index"] for r in rows if r["legacy_index"] is not None}) != legacy_unique:
        return {**_header(scene), "state": "decode_mismatch", "reasons": [],
                "reason": f"counts_inconsistent ({counts})", "counts": {**counts, "detected": None},
                "provenance": _provenance_summary(before, None), "rows": None}
    return {**_header(scene), "state": "not_requested", "reasons": [],
            "reason": "scene addressing not requested (CLIPFORGE_CLIPPER_SCENE_ADDRESSING off)",
            "counts": counts, "provenance": _provenance_summary(before, None), "rows": rows}


def address_scenes(project_id: str, proxy_path: str, scene: dict, before: dict) -> dict:
    """scenes_addressed for build_signals. `before` = provenance_now() read before scene_pass."""
    from config import settings  # local: the pass above stays config-free for its tests

    if not settings.clipper_scene_addressing:
        return not_requested(scene, before)
    ctx = {}
    if before.get("state") == "recorded":
        p, s = before.get("proxy_identity") or {}, before.get("source_identity") or {}
        ctx = {"proxy": {"stream": p.get("stream") or {}, "format": p.get("format") or {}},
               "source": {"stream": s.get("stream") or {}, "format": s.get("format") or {}},
               "clock": before.get("clock") or {}, "source_path": s.get("path"), "proxy_path": proxy_path,
               "source_nb": _int((s.get("stream") or {}).get("nb_frames")),
               "proxy_nb": _int((p.get("stream") or {}).get("nb_frames")),
               "width": _int((p.get("stream") or {}).get("width")) or 0,
               "height": _int((p.get("stream") or {}).get("height")) or 0}
    return assemble(scene, before, lambda: provenance_now(project_id, proxy_path), address_row, ctx)