"""
ClipForge — AI Stream Clipper: proxy slot -> source frame addressing.

Every analysis pass reads the 10 fps proxy, and the source frame a proxy slot
shows is NOT `slot / 10` seconds into the source. ffmpeg's `-r 10` CFR vsync
shows the first source frame whose END reaches about slot/10 - 0.11 s, so at
60 fps slot n shows frame 6n - 7 and at 23.976 the lag is 2.6-3.6 frames. This
module is the one place that mapping lives; a consumer calls it rather than
adding or subtracting its own constant.

It is an exact replica of what ffmpeg n8.1.1 does, run on the DECODED proxy PTS
and on the source frames' own PTS and durations. It was frozen as M0 v2
(`data/claude-master-20260924/B/gates/m0v2/addrlib.py`, sha256 c8ec66fc…; a
verbatim copy is server/tests/data/proxy_clock/addrlib.py.txt) and
is copied here, never imported. Outside the validated domain the answer is
`refused` with a reason: never a constant offset, never a nearest guess.

WHICH CODE THIS REPRODUCES (tag n8.1.1, file hashes in FFMPEG_SOURCES):

  fftools/ffmpeg_filter.c
    2453-2488  adjust_frame_pts_to_encoder_tb(): the PTS is rescaled (av_rescale_q,
               round half away from zero) into tb_dst with
               `extra_bits = av_clip(29 - av_log2(tb.den), 0, 16)` = 16 for 1/10
               (CLIPPED, not 26), divided back as a double and, WHEN NOT AN
               INTEGER, nudged by FFSIGN(pts) * 2^-17 (lines 2472-2473). The nudge
               decides exact ties off the slot grid: such a frame is KEPT. On the
               grid there is no nudge and the double sum decides: tb 1/100, start
               0.9 s, 0.09 s long -> -2 + 9*0.01/0.1 is the same double as the
               literal -1.1, so `delta < -1.1` is false and the frame is KEPT too.
               Both kinds were checked against real ffmpeg.      -> sync_ipts()
    2533       duration = frame->duration * av_q2d(tb) / av_q2d(tb_out)
                                                                  -> out_duration()
    2539-2540  delta0 = sync_ipts - next_pts; delta = delta0 + duration
    2548-2562  the clipping block rewrites everything except `delta`, so it never
               changes a CFR decision.
    2572-2583  VSYNC_CFR, frame_drop_threshold 0 (ffmpeg_opt.c:62): delta < -1.1
               drops; delta > 1.1 emits llrintf(delta), with llrintf(delta0 - 0.6)
               copies of the previous frame first when delta0 > 1.1; else 1.
                                                                  -> cfr_decision()
    2519-2528, 2593-2598, 2712-2729, 2764-2765  EOF median3, history, previous-
               frame copies, next_pts++                           -> simulate()
    2445-2447  tb_out = 1/fr with fr the -r rate (10/1)
  fftools/ffmpeg_dec.c 401     frame->pts = best_effort_timestamp
  fftools/ffmpeg_demux.c 2121  a non-zero start_time shifts every frame before this
               rule sees it. NOT handled: refused.
  libavutil/mathematics.c 142-144  av_rescale_q = AV_ROUND_NEAR_INF

THE STEADY-STATE ADDRESS. Slot n shows the first frame after slot n-1's that is
not dropped (`not (delta < -1.1)`, in ffmpeg's double arithmetic, ties
included). It is exact only in the steady state, which address_slot() proves
locally: the window is CFR and contiguous, slot n-1's choice is strictly
earlier, the chosen frame is emitted once. "Shown at p - 0.11 s" is an
approximation of this and is not used.

A RULE IS NOT A CONTRACT FOR EVERY FILE. It holds for a proxy made by a given
recipe on a given ffmpeg build, and only the (recipe, build) pairs in
VALIDATED_CLOCKS are validated, each by the probes named next to it. Another
build is `unvalidated` until probed — a matching major version proves nothing,
and neither does an equal frame count.

Every refusal is a value (`{"state": "refused", "reason": ...}`), never an
exception, and never turned into a guess by the caller.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from fractions import Fraction

import numpy as np

# The reader/mapping version: bump when this rule or its refusals change. It is
# NOT the proxy recipe's version (ingest owns that) and NOT ANALYSIS_VERSION.
ADDRESSING_VERSION = "proxy-clock-1 (ffmpeg n8.1.1 VSYNC_CFR replica, m0v2-addr-1)"

FFMPEG_SOURCES = {  # sha256 of the files read at tag n8.1.1
    "fftools/ffmpeg_filter.c": "32b478325d0843c982c810a865016bed5b154f1d6c2f0b957be297dee7beb2ec",
    "fftools/ffmpeg_dec.c": "dd79988a38f82e5ae0b8508eb5e1db9ab7f4a4c544d939535d185039180f4c5a",
    "fftools/ffmpeg_demux.c": "28e36593d954d053122ac028708a8cf10852d5986ccee201f4f3ea3993fdffb8",
    "fftools/ffmpeg_opt.c": "95951e05371ac6debc4856304a340d5da294c8a7adc844c9b7d2af7743cc4c6c",
    "libavutil/mathematics.c": "c38b36d09a886b8a0471fa1c8f0174a1a852f437011d818569ece3824b1d4b6b",
}

PROXY_FPS = 10
VALIDATED_PROXY_RATES = {Fraction(10)}
# Each rate: a 10-minute synthetic barcode source through the proxy recipe, every
# slot decoded and compared with simulate() and address_slot(), 0 mismatches
# (VALIDATED_CLOCKS[...]["evidence"]). tests/test_clipper_proxy_clock.py ties this
# set to that evidence and re-probes the installed build;
# tests/test_clipper_proxy_clock_evidence.py checks the evidence files themselves.
VALIDATED_SOURCE_RATES = {Fraction(60), Fraction(30), Fraction(25), Fraction(60000, 1001),
                          Fraction(24), Fraction(24000, 1001)}
START_SLOTS = 2          # slots 0 and 1 show frames 0 and 1 (start transient)


# ── The validated (recipe, ffmpeg build) pairs ───────────────────────────────

# build_proxy's argument list with its variable parts as placeholders
# (ingest.py:339-354 at fd76b8f). The recipe's identity is the sha256 of this
# list, so an edit to the command is a different, unvalidated recipe.
VALIDATED_RECIPE_TEMPLATE = (
    "-y", "-loglevel", "error", "-i", "{source}", "-an", "-sn", "-dn",
    "-vf", "scale={width}:-2:flags=bilinear", "-r", "{fps}",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
    "-g", "{gop}", "-movflags", "+faststart", "{out}",
)


def template_sha256(template) -> str:
    return hashlib.sha256(json.dumps(list(template), separators=(",", ":")).encode()).hexdigest()


# Verbatim copies of the M0 v2 evidence (sha256 equal to FROZEN.json's), plus the
# reproduction with the shipping code; README.md there has the commands.
# tests/test_clipper_proxy_clock_evidence.py checks every file and number below.
_EVIDENCE = "server/tests/data/proxy_clock/"
VALIDATED_CLOCKS = (
    {
        "id": "clipper-proxy-recipe-1 @ ffmpeg 8.1.1-full_build-www.gyan.dev",
        "recipe": {"template_sha256": template_sha256(VALIDATED_RECIPE_TEMPLATE),
                   # Only the parameters the probes ran with. A 320-wide proxy is
                   # the same template and still unvalidated.
                   "params": {"width": 480, "fps": "10", "gop": "10"}},
        "ffmpeg_build": {
            "version_line": "ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright (c) 2000-2026 "
                            "the FFmpeg developers",
            "libs": {"libavutil": "60.26.101", "libavcodec": "62.28.101",
                     "libavformat": "62.12.101", "libavdevice": "62.3.101",
                     "libavfilter": "11.14.101", "libswscale": "9.5.101",
                     "libswresample": "6.3.101"},
            "configuration_sha256": "3db14a0b1ee1d47e4fd08568660059c7cb76c32a1df01a06ecece24dc1577ed3",
            "exe_sha256": "09948d4cdd0650da6ff5a87577469f2a218dc2615ae379f8f734d24c49de0f73",
        },
        "evidence": (
            {"file": _EVIDENCE + "synth_24_23.976_60_30_25_59.94.json",
             "sha256": "8468b3b523cb5b068c2fb6cc076115db0acfadd306cf16a0401307286743d6c0",
             "what": "every slot of a 600 s barcode source per rate, through this recipe at "
                     "width 480 / -r 10 / -g 10, equals simulate(); address_slot() 0 mismatches",
             "rates": {r: {"slots": 6002, "sim_mismatches": 0, "addr_mismatches": 0}
                       for r in ("24", "24000/1001", "60", "30", "25", "60000/1001")}},
            {"file": _EVIDENCE + "test_m0v2.py.txt",
             "sha256": "dcf42fdc1469da3be4dd4cb4247ed3f55bc40bfc78ff7d98e7fe0cd84d10d787",
             "what": "real-ffmpeg constructed exact ties, both sides, on and off the slot grid"},
            {"file": _EVIDENCE + "FROZEN.json",
             "sha256": "a3a4fe8b40878d99bf360611541dc53cc8522a65849af282e886bba7fdc359aa",
             "what": "this build's version, libs and hashes"},
            {"file": _EVIDENCE + "reproduce_synth_600s.json",
             "sha256": "1762bd546f2fdd1bd53b986c7437401dc7b0e6637b4dd4f1ac6909cf4bcb0b11",
             "what": "the same six rates, 600 s each, re-run with the SHIPPING proxy_recipe(), "
                     "ffmpeg_build(), simulate() and address_slot(); equal to the frozen probe",
             "rates": {r: {"slots": 6002, "sim_mismatches": 0, "addr_mismatches": 0}
                       for r in ("24", "24000/1001", "60", "30", "25", "60000/1001")}},
            {"file": _EVIDENCE + "reproduce_synth.py",
             "sha256": "e1c0887d19336c8b89e014a1e90ea41dc3582faa07cb9f97f388d4f5a6133b23",
             "what": "the script that wrote reproduce_synth_600s.json (README.md: the command)"},
        ),
    },
)


def clock_validation(recipe: dict | None, build: dict | None) -> dict:
    """Is (the recipe that made a proxy, the ffmpeg build that ran it) a validated pair?

    `recipe` is {"template_sha256", "params"}, `build` is {"version_line", "libs",
    "configuration_sha256", "exe_sha256"} as ingest records them. Either None
    (a proxy built before provenance was recorded) is `provenance_missing`; it is
    never inferred from today's code. Anything short of an exact match on every
    field is `unvalidated`, with each differing field named.
    """
    if not isinstance(recipe, dict) or not isinstance(build, dict):
        return {"state": "provenance_missing",
                "reasons": ["provenance_missing (no recorded recipe or ffmpeg build)"]}
    best: list[str] | None = None
    for clock in VALIDATED_CLOCKS:
        why = []
        if recipe.get("template_sha256") != clock["recipe"]["template_sha256"]:
            why.append(f"recipe_not_validated (template {recipe.get('template_sha256')})")
        for k, want in clock["recipe"]["params"].items():
            if (recipe.get("params") or {}).get(k) != want:
                why.append(f"recipe_params_not_validated ({k} {(recipe.get('params') or {}).get(k)!r})")
        for k, want in clock["ffmpeg_build"].items():
            if build.get(k) != want:
                why.append(f"ffmpeg_build_not_validated ({k} differs)")
        if not why:
            return {"state": "validated", "clock": clock["id"],
                    "evidence": [e["file"] for e in clock["evidence"]]}
        if best is None or len(why) < len(best):
            best = why
    return {"state": "unvalidated", "reasons": best or ["no validated clock"]}


# ── The C arithmetic ─────────────────────────────────────────────────────────

def av_log2(v: int) -> int:
    return int(v).bit_length() - 1


def rescale_q(a: int, bq: tuple[int, int], cq: tuple[int, int]) -> int:
    """av_rescale_q (AV_ROUND_NEAR_INF), as mathematics.c computes it."""
    b = bq[0] * cq[1]
    c = cq[0] * bq[1]
    if a < 0:
        return -rescale_q(-a, bq, cq)
    return (a * b + c // 2) // c


def sync_ipts(pts: int, tb: tuple[int, int], out_fps: int = PROXY_FPS, start_time_us: int = 0) -> float:
    """adjust_frame_pts_to_encoder_tb (ffmpeg_filter.c:2453-2473), tb_dst = 1/out_fps."""
    tb_dst = (1, out_fps)
    extra_bits = min(max(29 - av_log2(tb_dst[1]), 0), 16)
    tbx = (tb_dst[0], tb_dst[1] << extra_bits)
    fp = float(rescale_q(pts, tb, tbx) - rescale_q(start_time_us, (1, 1_000_000), tbx))
    fp /= (1 << extra_bits)
    if fp != float(np.rint(fp)):                       # llrint: exact-integer test only
        fp += (1.0 if fp > 0 else -1.0) * 1.0 / (1 << 17)
    return fp


def out_duration(dur: int, tb: tuple[int, int], out_fps: int = PROXY_FPS) -> float:
    """ffmpeg_filter.c:2533, left to right in double."""
    return dur * (tb[0] / float(tb[1])) / (1 / float(out_fps))


def llrintf(x: float) -> int:
    return int(np.rint(np.float32(x)))


def deltas(pts: int, dur: int, tb, next_pts: int, out_fps: int = PROXY_FPS) -> tuple[float, float]:
    d0 = sync_ipts(pts, tb, out_fps) - float(next_pts)
    return d0, d0 + out_duration(dur, tb, out_fps)


def cfr_decision(delta0: float, delta: float) -> tuple[int, int]:
    """(nb_frames, nb_frames_prev), ffmpeg_filter.c:2572-2583, frame_drop_threshold 0."""
    if delta < -1.1:
        return 0, 0
    if delta > 1.1:
        return llrintf(delta), (llrintf(delta0 - 0.6) if delta0 > 1.1 else 0)
    return 1, 0


def simulate(pts: list[int], durs: list[int], tb, out_fps: int = PROXY_FPS) -> list[int]:
    """The whole CFR conversion, EOF flush included: per output slot, the index
    (into pts) of the frame shown there. pts must be in presentation order."""
    slots: list[int] = []
    hist = [0, 0, 0]
    prev = None
    next_pts = 0
    for i, (p, d) in enumerate(zip(pts, durs)):
        d0, dl = deltas(int(p), int(d), tb, next_pts, out_fps)
        nb, nbp = cfr_decision(d0, dl)
        hist = [nbp] + hist[:2]
        for k in range(nb):
            slots.append(prev if (k < nbp and prev is not None) else i)
            next_pts += 1
        prev = i
    nb = sorted(hist)[1]                               # median3 at EOF
    for _ in range(nb):
        if prev is None:
            break
        slots.append(prev)
        next_pts += 1
    return slots


# ── Domain ───────────────────────────────────────────────────────────────────

def _rate(s: str | None) -> Fraction | None:
    try:
        n, d = str(s).split("/")
        return Fraction(int(n), int(d)) if int(d) else None
    except (ValueError, AttributeError):
        return None


def _tb(s: str | None) -> tuple[int, int] | None:
    try:
        n, d = str(s).split("/")
        return (int(n), int(d)) if int(n) > 0 and int(d) > 0 else None
    except (ValueError, AttributeError):
        return None


def _zero(v) -> bool | None:
    """ffprobe start_time/start_pts -> is it zero? None when absent (unknown)."""
    if v is None or v == "N/A":
        return None
    return Fraction(str(v)) == 0


def domain(proxy: dict, source: dict) -> dict:
    """Per-file domain check on ffprobe {"stream", "format"} fields. Every failing
    condition is listed, not just the first."""
    why: list[str] = []
    ps, pf = proxy.get("stream", {}), proxy.get("format", {})
    ss, sf = source.get("stream", {}), source.get("format", {})
    pr, pa = _rate(ps.get("r_frame_rate")), _rate(ps.get("avg_frame_rate"))
    sr, sa = _rate(ss.get("r_frame_rate")), _rate(ss.get("avg_frame_rate"))
    if pr is None or pr != pa or pr not in VALIDATED_PROXY_RATES:
        why.append(f"proxy_rate_not_validated ({ps.get('r_frame_rate')} / {ps.get('avg_frame_rate')})")
    for who, s, f in (("proxy", ps, pf), ("source", ss, sf)):
        z = [_zero(s.get("start_time")), _zero(s.get("start_pts")), _zero(f.get("start_time"))]
        if not all(x is True for x in z):
            why.append(f"{who}_start_time_nonzero (stream {s.get('start_time')} pts {s.get('start_pts')} "
                       f"format {f.get('start_time')})")
    if _tb(ps.get("time_base")) is None or _tb(ss.get("time_base")) is None:
        why.append("pts_unknown (time_base missing)")
    if sr is None or sa is None or sr != sa:
        why.append(f"source_vfr (r_frame_rate {ss.get('r_frame_rate')} != avg {ss.get('avg_frame_rate')})")
    elif sr <= PROXY_FPS:
        why.append(f"source_fps_le_proxy_fps ({sr})")
    elif sr not in VALIDATED_SOURCE_RATES:
        why.append(f"source_rate_not_validated ({sr})")
    return {"state": "ok"} if not why else {"state": "refused", "reasons": why}


# ── The address ──────────────────────────────────────────────────────────────

def _refuse(reason: str, **kw) -> dict:
    return {"state": "refused", "reason": reason, "addressing_version": ADDRESSING_VERSION, **kw}


def slot_of(proxy_pts: int | None, time_base: str | None) -> int | None:
    """The proxy slot a decoded PTS sits on, or None when it is off the 1/10 s grid
    or unknown. Exact rational arithmetic: no float, no rounding to a slot."""
    tb = _tb(time_base)
    if proxy_pts is None or tb is None or isinstance(proxy_pts, bool):
        return None
    q = Fraction(int(proxy_pts) * tb[0], tb[1]) * PROXY_FPS
    return int(q) if q.denominator == 1 else None


def address_slot(proxy_pts: int | None, proxy: dict, source: dict, frames: list[tuple[int, int]],
                 *, clock: dict, source_nb_frames: int | None,
                 proxy_nb_frames: int | None) -> dict:
    """Address one proxy frame.

    proxy_pts: the DECODED PTS, an integer in the proxy stream time_base (None =
        not tied to a decode).
    proxy/source: ffprobe {"stream": {...}, "format": {...}}, text fields as printed.
    frames: DECODED source frames around the slot, (pts, duration) integers in the
        source time_base, presentation order, reaching back past the slot n-1
        decision and forward past the choice (window() builds one).
    clock: clock_validation() for the proxy's recorded recipe and build.
    source_nb_frames / proxy_nb_frames: the streams' frame counts.
    """
    if clock.get("state") != "validated":
        why = (clock.get("reasons") or ["clock not validated"])[0]
        return _refuse(why if clock.get("state") == "provenance_missing"
                       else f"clock_unvalidated ({why})")
    if proxy_pts is None:
        return _refuse("pts_unknown (frame not tied to a decoded proxy PTS)")
    dom = domain(proxy, source)
    if dom["state"] != "ok":
        return _refuse(dom["reasons"][0], all_reasons=dom["reasons"])
    ptb, stb = _tb(proxy["stream"]["time_base"]), _tb(source["stream"]["time_base"])
    slot_q = Fraction(proxy_pts * ptb[0], ptb[1]) * PROXY_FPS
    if slot_q.denominator != 1:
        return _refuse(f"proxy_pts_off_grid (pts {proxy_pts} x {ptb[0]}/{ptb[1]} = {slot_q / PROXY_FPS} s)")
    n = int(slot_q)
    base = {"slot": n, "proxy_pts": proxy_pts, "proxy_pts_time": str(Fraction(n, PROXY_FPS))}
    if n < START_SLOTS:
        return _refuse("start_transient (slots 0-1 show source frames 0 and 1; not the steady rule)", **base)
    if proxy_nb_frames is not None and n >= proxy_nb_frames:
        return _refuse(f"slot_not_in_proxy (slot {n} >= {proxy_nb_frames} frames)", **base)
    fps = _rate(source["stream"]["r_frame_rate"])
    if len(frames) < 3:
        return _refuse("window_short (fewer than 3 decoded source frames)", **base)
    step = Fraction(stb[1], stb[0]) / fps                  # one frame in source tb units
    if step.denominator != 1:
        return _refuse(f"vfr_local (1/fps = {step} ticks, not an integer)", **base)
    step = int(step)
    for k, (p, d) in enumerate(frames):
        if d != step or (k + 1 < len(frames) and frames[k + 1][0] != p + d) or p % step:
            return _refuse(f"vfr_local (frame pts {p} dur {d}; expected contiguous {step}-tick frames "
                           f"on the index grid)", **base)
    first_nd = {}
    for m in (n - 1, n):
        first_nd[m] = next((k for k, (p, d) in enumerate(frames)
                            if cfr_decision(*deltas(p, d, stb, m))[0] != 0), None)
    if first_nd[n - 1] is None or first_nd[n] is None:
        return _refuse("window_short (no frame in the window survives the slot test)", **base)
    if first_nd[n - 1] == 0 and frames[0][0] == 0:
        return _refuse("start_transient (the slot n-1 decision reaches the first frame of the stream)", **base)
    if first_nd[n - 1] == 0:
        return _refuse("window_short (the window does not reach back to a frame dropped at slot n-1)", **base)
    if not first_nd[n] > first_nd[n - 1]:
        return _refuse("not_steady (the frame chosen for slot n-1 would be chosen again)", **base)
    k = first_nd[n]
    p, d = frames[k]
    d0, dl = deltas(p, d, stb, n)
    if cfr_decision(d0, dl) != (1, 0):
        return _refuse(f"duplicated (delta {dl!r} emits {cfr_decision(d0, dl)})", **base)
    idx = p // step
    if source_nb_frames is not None and idx >= source_nb_frames - 1:
        return _refuse(f"end_of_stream (chosen frame {idx} is the last of {source_nb_frames})", **base)
    if k + 1 >= len(frames):
        return _refuse("window_short (nothing decoded after the chosen frame)", **base)
    return {"state": "addressed", "addressing_version": ADDRESSING_VERSION, "clock": clock.get("clock"),
            **base, "source_index": idx, "source_pts": p, "source_duration": d,
            "source_time_base": f"{stb[0]}/{stb[1]}",
            "source_pts_time": str(Fraction(p * stb[0], stb[1])),
            "tie": Fraction((p + d) * stb[0], stb[1]) == Fraction(n, PROXY_FPS) - Fraction(11, 100)}


def window(pts: list[int], durs: list[int], tb, n: int, before: Fraction = Fraction(6, 10),
           after: Fraction = Fraction(2, 10)) -> list[tuple[int, int]]:
    """The frames of a sorted (pts, dur) list whose pts time lies in [n/10 - before, n/10 + after]."""
    lo = Fraction(n, PROXY_FPS) - before
    hi = Fraction(n, PROXY_FPS) + after
    a = bisect.bisect_left(pts, int(lo * tb[1] / tb[0]))
    b = bisect.bisect_right(pts, int(hi * tb[1] / tb[0]))
    return list(zip(pts[a:b], durs[a:b]))


def exact_end_vs_edge(p: int, d: int, tb, n: int) -> int:
    """Sign of (frame end - (n/10 - 0.11)) in exact rational arithmetic."""
    v = Fraction((p + d) * tb[0], tb[1]) - (Fraction(n, PROXY_FPS) - Fraction(11, 100))
    return (v > 0) - (v < 0)


# ── A change seen between two samples ────────────────────────────────────────

def interval_for_event(prev: dict, cur: dict, *, isolated: bool | None) -> dict:
    """Where a change first seen at slot n (slot n-1 old, slot n new) can lie in the source.

    prev / cur: address_slot() for slots n-1 and n. Under the hypothesis of ONE
    cut between the two samples, the first frame of the new content has a PTS in
    `(prev.source_pts, cur.source_pts]`: exclusive before (the frame slot n-1
    shows is still old, or the change would have been seen there), inclusive
    current (the frame slot n shows may itself be the first new one). That is an
    interval, not the cut frame; the exact frame needs a source decode of it.

    isolated: the detector's verdict that neither neighbouring sample pair also
    changed. False means several cuts or a transition span the samples: the
    single-cut hypothesis does not hold and the answer is a refusal asking for
    source refinement. None means nobody looked, which is not a pass either.
    Two cuts inside ONE interval are invisible from the samples; that is what
    `hypothesis` states on every answer.
    """
    if not (isolated is None or isinstance(isolated, bool)):
        return _refuse(f"isolation_not_a_verdict ({isolated!r})")
    for who, a in (("previous", prev), ("current", cur)):
        if a.get("state") != "addressed":
            return _refuse(f"{who}_sample_not_addressed ({a.get('reason')})")
    if cur["slot"] != prev["slot"] + 1:
        return _refuse(f"samples_not_adjacent (slots {prev['slot']} and {cur['slot']})")
    if cur["source_time_base"] != prev["source_time_base"] or cur["source_pts"] <= prev["source_pts"]:
        return _refuse("not_steady (the two samples do not show increasing source frames)")
    if isolated is None:
        return _refuse("isolation_unknown (single-cut hypothesis not checked)")
    if not isolated:
        return _refuse("needs_source_refinement (neighbouring samples also changed: several "
                       "cuts or a transition)")
    return {"state": "interval", "addressing_version": ADDRESSING_VERSION,
            "hypothesis": "single_cut", "slot": cur["slot"],
            "source_time_base": cur["source_time_base"],
            "source_pts_after": prev["source_pts"],       # exclusive
            "source_pts_through": cur["source_pts"],      # inclusive
            "source_index_after": prev["source_index"],
            "source_index_through": cur["source_index"],
            "frames": cur["source_index"] - prev["source_index"]}


def interval_contains(interval: dict, source_pts: int) -> bool:
    """Whether a source frame's PTS lies in (source_pts_after, source_pts_through]."""
    return (interval.get("state") == "interval"
            and interval["source_pts_after"] < int(source_pts) <= interval["source_pts_through"])
