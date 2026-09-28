"""
ClipForge — AI Stream Clipper: which SOURCE frame each window sample shows.

A DIAGNOSTIC with no production caller (`analyse_window(..., address=True)`;
codex-verdict-next-19 §2, next-22 §2; design: B/clock-composition-addendum.md
as amended by B/CC1C0-result.md §8). It composes three clocks:

  window frame j  --(encoder stats of the SAME encode: row n == j, ni, ptsi)-->
  proxy PTS = ptsi + rescale_q(start_us, 1/1e6, proxy tb)   (the demuxer's ts_offset)
  --(proxy_clock.address_slot, one source ffprobe per window)--> source frame

The tie is accepted on ONE ffmpeg build, ONE window recipe and ONE OpenCV
decoder (VALIDATED_TIES, CC1-C0 P0); anything else is `window_reader_unvalidated`,
never a fallback. The window encode is NOT CFR: a missing proxy frame is an
encoder-PTS jump, three frames on one slot a real drop (an `ni` skip).

What is kept apart, and never converted into one another:
  - a POINT (faces, detail, ui): one window frame, one address;
  - a SPAN (motion, focus): two addresses and their real duration; slots not
    exactly `step` apart -> `pair_not_steady`. The first motion entry has no
    previous frame: `motion_init`, not a span;
  - an AGGREGATE (panels): the reads that were actually sampled, first/last;
  - the raw face observation and the gap TRACKING (`tracked` is not `detected`,
    a refused track is not an absence, an unread frame is never `empty`);
  - the computed address, the read coverage, and the decode integrity, which
    stays `unverified`: OpenCV's index and PTS do not certify pixels (C0 §5.1,
    read 39 showed frame 38).

Audio and the transcript never receive this offset: no function here takes a
candidate, words, peaks or audio, and the output has no corrected time.
"""

from __future__ import annotations

import json
import math
import subprocess
from fractions import Fraction
from typing import Any, Callable

from services.clipper import proxy_clock, scene_address
from services.clipper.ffmpeg_tools import creationflags, ffprobe_bin
from services.clipper.window_address_rows import (  # noqa: F401  (enc_time, frame_of: the tie's API)
    _refused, counts, enc_time, face_row, frame_of, motion_rows, panels_aggregate, refused_rows)

VERSION = "window-address-1"
STATS_FMT = "{n} {ni} {tb} {pts} {tbi} {ptsi}"
# dynamic_window.analyse_window's argv after the executable, stats included.
WINDOW_TEMPLATE = ("-y", "-loglevel", "error", "-ss", "{start}", "-i", "{proxy}", "-t", "{duration}",
                   "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
                   "-stats_enc_pre", "{stats}", "-stats_enc_pre_fmt", STATS_FMT, "{out}")
VALIDATED_TIES = (
    {"id": "window-stats-tie-1 @ ffmpeg 8.1.1-full_build-www.gyan.dev, OpenCV 4.14.0",
     "template_sha256": proxy_clock.template_sha256(WINDOW_TEMPLATE),
     "ffmpeg_build": proxy_clock.VALIDATED_CLOCKS[0]["ffmpeg_build"],
     "reader": {"opencv": "4.14.0", "backend": "FFMPEG", "avcodec": "61.19.100"},
     "evidence": "B/CC1C0-result.md P0: 52 windows, 2 700 frames; barcode 2 694/2 694, "
                 "one-slot shift 0/2 694"},
)


def stats_options(path: Any) -> list[str]:
    return ["-stats_enc_pre", str(path), "-stats_enc_pre_fmt", STATS_FMT]


def recipe_of(argv: list[str]) -> dict:
    """The executed argv (no executable) with its variable tokens as placeholders."""
    t = list(argv)
    for flag, name in (("-ss", "{start}"), ("-i", "{proxy}"), ("-t", "{duration}"),
                       ("-stats_enc_pre", "{stats}")):
        if flag in t[:-1]:
            t[t.index(flag) + 1] = name
    t[-1] = "{out}"
    return {"template": t, "template_sha256": proxy_clock.template_sha256(t)}


def tie_validation(recipe: dict, build: dict, reader: dict) -> tuple[bool, list[str]]:
    # Every reader component must be PRESENT and equal: an unknown decoder is not the
    # validated one. Skipping a None let a reader with no backend or avcodec address
    # every face (codex-verdict-next-26 §3, C1r).
    for tie in VALIDATED_TIES:
        why = [] if recipe.get("template_sha256") == tie["template_sha256"] else ["window recipe differs"]
        why += [f"ffmpeg {k} differs" for k, v in tie["ffmpeg_build"].items() if (build or {}).get(k) != v]
        why += [f"reader {k} {(reader or {}).get(k)!r}" for k, v in tie["reader"].items()
                if (reader or {}).get(k) != v]
        if not why:
            return True, []
    return False, why


def reader_of(seen: dict | None) -> dict:
    """One capture's identity, from its OWN telemetry (`observed`); absent stays None."""
    return {"opencv": (seen or {}).get("opencv"), "backend": (seen or {}).get("backend"),
            "avcodec": ((seen or {}).get("decoder") or {}).get("avcodec")}


def parse_enc_stats(text: str | None) -> list[dict] | None:
    """Rows of STATS_FMT, or None when absent or any line does not parse."""
    if not text:
        return None
    rows = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        parts = ln.split()
        if len(parts) != 6:
            return None
        try:
            rows.append({"n": int(parts[0]), "ni": int(parts[1]), "tb": parts[2], "pts": int(parts[3]),
                         "tbi": parts[4], "ptsi": int(parts[5])})
        except ValueError:
            return None
    return rows or None


def ts_offset(start: str, proxy_tb: tuple[int, int]) -> int | None:
    """ffmpeg's ts_offset for `-ss start`: the STRING it parsed, in µs, rescaled near-inf."""
    us = Fraction(start) * 1_000_000
    return proxy_clock.rescale_q(int(us), (1, 1_000_000), proxy_tb) if us.denominator == 1 else None


def tie_window(rows: list[dict] | None, start: str, proxy_tb: str) -> dict:
    """Per window frame j: {j, ni, enc_pts, proxy_pts, slot, duplicate_of}, or the refusal."""
    ptb = proxy_clock._tb(proxy_tb)

    def no(state: str, why: str, frames=None) -> dict:
        return {"state": state, "reason": f"{state} ({why})", "rows": len(rows or []), "frames": frames}

    if not rows:
        return no("window_tie_unavailable", "stats missing, empty or unparseable")
    if any(r["n"] != j for j, r in enumerate(rows)):
        return no("window_tie_unavailable", "a stats row is not the encoded frame of its position")
    tbs = {r["tb"] for r in rows}
    if len(tbs) != 1 or proxy_clock._tb(next(iter(tbs))) is None:
        return no("window_tie_unavailable", f"encoder time_base {sorted(tbs)}")
    if ptb is None or any(r["tbi"] != proxy_tb for r in rows):
        return no("pts_unknown", f"input time_base {sorted({r['tbi'] for r in rows})} != registered {proxy_tb}")
    if any(r["ni"] < 0 or r["ptsi"] < 0 for r in rows):
        return no("window_tie_unavailable", "an input frame or PTS is unknown")
    off = ts_offset(start, ptb)
    if off is None:
        return no("window_tie_unavailable", f"start {start!r} is not whole microseconds")
    frames, first = [], {}
    for j, r in enumerate(rows):
        pp = r["ptsi"] + off
        dup = first.setdefault(r["ni"], j)
        frames.append({"j": j, "ni": r["ni"], "enc_pts": r["pts"], "proxy_pts": pp,
                       "slot": proxy_clock.slot_of(pp, proxy_tb), "duplicate_of": None if dup == j else dup})
    off_grid = [f["j"] for f in frames if f["slot"] is None]
    if off_grid:
        return no("window_tie_off_grid", f"window frames {off_grid[:8]} are off the proxy slot grid", frames)
    return {"state": "ok", "reason": None, "rows": len(rows), "enc_time_base": next(iter(tbs)),
            "proxy_time_base": proxy_tb, "offset": off, "frames": frames}


def read_source_range(source_path: str, lo_slot: int, hi_slot: int, registered_tb: str
                      ) -> tuple[list[int], list[int], tuple[int, int]]:
    """Source (pts, duration) packets over [(lo-1)/10 - 2 s, hi/10 + 1 s]: the shape of
    scene_address.read_source_packets over a whole window, ONE ffprobe. Raises ValueError."""
    lo = max(Fraction(0), Fraction(lo_slot - 1, proxy_clock.PROXY_FPS) - 2)
    hi = Fraction(hi_slot, proxy_clock.PROXY_FPS) + 1
    cmd = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
           "-read_intervals", f"{float(lo):.3f}%{float(hi):.3f}",
           "-show_entries", "stream=time_base:packet=pts,duration", "-of", "json", str(source_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=300,
                              creationflags=creationflags())
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError(f"source_window_unreadable (ffprobe: {exc})") from exc
    if proc.returncode != 0:
        raise ValueError(f"source_window_unreadable (ffprobe rc={proc.returncode})")
    data = json.loads(proc.stdout or "{}")
    tb = ((data.get("streams") or [{}])[0]).get("time_base")
    if tb != registered_tb:
        raise ValueError(f"pts_unknown (source time_base {tb} != registered {registered_tb})")
    rows = []
    for pk in data.get("packets") or []:
        if not isinstance(pk.get("pts"), int) or not isinstance(pk.get("duration"), int):
            raise ValueError(f"source_window_unreadable (packet without pts/duration: {pk})")
        rows.append((pk["pts"], pk["duration"]))
    rows.sort()
    return [p for p, _ in rows], [d for _, d in rows], proxy_clock._tb(tb)


def _ctx(prov: dict) -> dict:
    p, s = prov.get("proxy_identity") or {}, prov.get("source_identity") or {}
    return {"proxy": {"stream": p.get("stream") or {}, "format": p.get("format") or {}},
            "source": {"stream": s.get("stream") or {}, "format": s.get("format") or {}},
            "clock": prov.get("clock") or {}, "source_path": s.get("path"),
            "source_nb": scene_address._int((s.get("stream") or {}).get("nb_frames")),
            "proxy_nb": scene_address._int((p.get("stream") or {}).get("nb_frames"))}


def not_requested(n_faces: int, n_motion: int) -> dict:
    """What a diagnostic records for a window run with `address=False`: not a
    refusal, not empty; every sample keeps its facts."""
    return {"state": "not_requested", "reason": "window addressing not requested (address=False)",
            "version": VERSION, "counts": {"denominator": {"faces": n_faces, "motion": n_motion},
                                           "not_requested": n_faces + n_motion}}


def compose(*, request: dict, recipe: dict, stats_text: str | None, faces: list[dict],
            motion_count: int, seen_motion: dict | None, seen_panels: dict | None,
            before: dict, read_after: Callable[[], dict], build: dict,
            read_packets: Callable[..., tuple] | None = None) -> dict:
    """`window_addressed` for one window. One state for the window; when it is a
    refusal every sample carries it (`refused_at: window`) and no ffprobe runs."""
    reader = reader_of(seen_motion)
    doc: dict[str, Any] = {"version": VERSION, "addressing_version": proxy_clock.ADDRESSING_VERSION,
                           "request": request, "recipe": recipe, "reader": reader,
                           "reader_panels": reader_of(seen_panels) if seen_panels is not None else None,
                           "ffmpeg_build": {k: (build or {}).get(k) for k in
                                            ("version_line", "configuration_sha256", "exe_sha256")},
                           "integrity": "unverified (OpenCV indices/PTS do not certify pixels)"}
    ptb = ((before.get("proxy_identity") or {}).get("stream") or {}).get("time_base")
    state, reason, _all = scene_address.project_state(before, ptb)
    tie: dict = {"state": None}
    ok, why = tie_validation(recipe, build, reader)
    if state == "ok" and not ok:
        if seen_motion is not None and not seen_motion.get("opened"):
            why.append("motion capture_not_opened")      # why its identity is unknown
        state, reason = "window_reader_unvalidated", f"window_reader_unvalidated ({'; '.join(why)})"
    if state == "ok":
        tie = tie_window(parse_enc_stats(stats_text), request["start"], ptb)
        if tie["state"] != "ok":
            state, reason = tie["state"], tie["reason"]
    after = None
    if state == "ok":
        ctx = _ctx(before)
        slots = [f["slot"] for f in tie["frames"]]
        try:
            pts, durs, stb = (read_packets or read_source_range)(
                ctx["source_path"], min(slots), max(slots), ctx["source"]["stream"].get("time_base"))
        except (ValueError, OSError) as exc:
            state, reason = "source_window_unreadable", str(exc)[:300]
    if state == "ok":
        cache: dict[int, dict] = {}

        def address(j: int) -> dict:
            f = tie["frames"][j]
            if f["slot"] not in cache:
                cache[f["slot"]] = proxy_clock.address_slot(
                    f["proxy_pts"], ctx["proxy"], ctx["source"], proxy_clock.window(pts, durs, stb, f["slot"]),
                    clock=ctx["clock"], source_nb_frames=ctx["source_nb"], proxy_nb_frames=ctx["proxy_nb"])
            a = cache[f["slot"]]
            out = {"window_frame": j, "slot": f["slot"], "proxy_pts": f["proxy_pts"],
                   "duplicate_of": f["duplicate_of"]}
            if a["state"] != "addressed":
                return {**out, "state": "refused", "reason": a["reason"], "refused_at": "address"}
            return {**out, "state": "addressed", "reason": None, "source_index": a["source_index"],
                    "source_pts": a["source_pts"], "source_time_base": a["source_time_base"]}

        rows = {"faces": [face_row(i, s, tie, address) for i, s in enumerate(faces)],
                "motion": motion_rows(motion_count, seen_motion, tie, address),
                "panels": panels_aggregate(seen_panels, tie, address)}
        # ui_panels reads through its OWN capture: the motion reader's identity does not
        # certify it. An opened panels capture that is not the validated reader refuses the
        # aggregate only; faces and motion keep their addresses.
        if seen_panels is not None and seen_panels.get("opened"):
            ok_panels, why_panels = tie_validation(recipe, build, reader_of(seen_panels))
            if not ok_panels:
                rows["panels"] = _refused(f"panels_reader_unvalidated ({'; '.join(why_panels)})", "reads")
        after = read_after()
        if scene_address._identity_key(after) != scene_address._identity_key(before):
            state = "identity_changed"
            reason = (f"identity_changed (provenance {before.get('state')} before the cut, "
                      f"{after.get('state')} after the last address)")
    if state != "ok":
        rows = refused_rows(len(faces), motion_count, reason)
    doc["provenance"] = scene_address._provenance_summary(before, after)
    doc["tie"] = {k: v for k, v in tie.items() if k != "frames"}
    doc["coverage"] = {k: _coverage(seen, tie) for k, seen in (("motion", seen_motion), ("panels", seen_panels))}
    return {**doc, "state": state, "reason": reason, **rows, "counts": counts(rows)}


def _coverage(seen: dict | None, tie: dict) -> dict:
    """What the reader got, beside what the encoder wrote. A short read is reported,
    never filled in from start/dur; the count check is auxiliary, never proof."""
    if seen is None:
        return {"state": "unobserved"}
    n_rows = len(tie.get("frames") or []) if tie.get("state") == "ok" else None
    reads, step = len(seen.get("reads") or []), seen.get("step")
    return {"state": ("not_opened" if not seen.get("opened") else "unknown" if n_rows is None
                      else "complete" if reads == n_rows else "incomplete"),
            "opened": seen.get("opened"), "reads": reads, "rows": n_rows, "stop": seen.get("stop"),
            "step": step, "sampled": len(seen.get("sampled") or []),
            "count_check_aux": (None if n_rows is None or not step
                                else len(seen.get("sampled") or []) == math.ceil(n_rows / step))}
