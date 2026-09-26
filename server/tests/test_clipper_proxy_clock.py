"""proxy_clock: the ffmpeg n8.1.1 `-r 10` replica, its refusals, the validated
(recipe, build) pairs and the scene-change interval.

The pure tests pin ffmpeg's arithmetic. The `test_real_ffmpeg_*` tests build
CONSTRUCTED barcode sources (each frame shows its own index) through the proxy
recipe and read every proxy slot back, so the replica is checked against the
installed ffmpeg rather than against itself. They were ported from the frozen
M0 v2 suite (B/gates/m0v2/test_m0v2.py) with a self-contained barcode.
"""
from __future__ import annotations

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from services.clipper import proxy_clock as C
from services.clipper.ffmpeg_tools import ffmpeg_bin, ffprobe_bin


def meta(rate="60/1", avg=None, tb="1/15360", start="0.000000", start_pts="0", fmt_start="0.000000"):
    return {"stream": {"r_frame_rate": rate, "avg_frame_rate": avg or rate, "time_base": tb,
                       "start_time": start, "start_pts": start_pts},
            "format": {"start_time": fmt_start}}


PROXY = meta("10/1", tb="1/10240")
CLOCK = {"state": "validated", "clock": "test clock"}
VALID_BUILD = dict(C.VALIDATED_CLOCKS[0]["ffmpeg_build"])
VALID_RECIPE = {"template_sha256": C.template_sha256(C.VALIDATED_RECIPE_TEMPLATE),
                "params": {"width": 480, "fps": "10", "gop": "10"}}


def cfr(fps: Fraction, tb_den: int, n: int):
    step = int(Fraction(tb_den) / fps)
    return [i * step for i in range(n)], [step] * n, (1, tb_den)


def addr(slot, pts, durs, tb, src_meta=None, proxy_meta=None, nb=None, pnb=None, clock=CLOCK):
    return C.address_slot(slot * 1024, proxy_meta or PROXY, src_meta or meta(),
                          C.window(pts, durs, tb, slot), clock=clock,
                          source_nb_frames=nb if nb is not None else len(pts), proxy_nb_frames=pnb)


RATES = [(Fraction(60), 15360), (Fraction(30), 15360), (Fraction(25), 12800),
         (Fraction(60000, 1001), 60000), (Fraction(24), 12288), (Fraction(24000, 1001), 24000)]


def _m(fps, tbd):
    return meta(f"{fps.numerator}/{fps.denominator}", tb=f"1/{tbd}")


# ── the C arithmetic ─────────────────────────────────────────────────────────

def test_rescale_q_rounds_half_away_from_zero():
    assert C.rescale_q(1, (1, 2), (1, 1)) == 1
    assert C.rescale_q(-1, (1, 2), (1, 1)) == -1
    assert C.rescale_q(3, (1, 4), (1, 1)) == 1
    assert C.rescale_q(1, (1, 4), (1, 1)) == 0
    assert C.rescale_q(5404399, (1, 60000), (1, 10 << 26)) == (5404399 * (10 << 26) + 30000) // 60000


def test_sync_ipts_nudges_only_non_integers():
    assert C.sync_ipts(90, (1, 100)) == 9.0
    x = C.sync_ipts(5404399, (1, 60000))
    q = C.rescale_q(5404399, (1, 60000), (1, 10 << 16)) / 65536.0     # extra_bits clipped to 16
    assert q != float(Fraction(5404399 * 10, 60000))
    assert x == q + 2 ** -17


@pytest.mark.parametrize("shift,kept", [(-1, False), (0, True), (1, True)])
def test_threshold_non_integer_sync_both_sides(shift, kept):
    """59.94: frame 5399 ends EXACTLY at slot 902's edge (90.09 = 90.2 - 0.11) at shift 0."""
    p, d = 5399 * 1001, 1001 + shift
    assert C.exact_end_vs_edge(p, d, (1, 60000), 902) == shift
    nb, _ = C.cfr_decision(*C.deltas(p, d, (1, 60000), 902))
    assert (nb == 1) is kept


@pytest.mark.parametrize("shift,kept", [(-1, False), (0, True), (1, True)])
def test_threshold_integer_sync_both_sides(shift, kept):
    """tb 1/100, start 0.9 s (no nudge), 0.09 s long: -2 + 9*0.01/0.1 is the same double as
    the literal -1.1, so `delta < -1.1` is false: KEPT at exact equality (real ffmpeg agrees,
    test_real_ffmpeg_threshold_integer_sync)."""
    p, d = 90, 9 + shift
    assert C.exact_end_vs_edge(p, d, (1, 100), 11) == shift
    d0, dl = C.deltas(p, d, (1, 100), 11)
    if shift == 0:
        assert d0 == -2.0 and dl == -1.1
    nb, _ = C.cfr_decision(d0, dl)
    assert (nb == 1) is kept


def test_at_23976_the_nudge_is_what_keeps_the_exact_tie():
    """Frame 2159 at 24000/1001 ends exactly at slot 902's edge. Without the 2^-17 nudge the
    quantised sum is -1.1000065 and the frame would be DROPPED; with it, -1.0999989: KEPT,
    as ffmpeg does (test_real_ffmpeg_threshold_nudge_23976). At 59.94 the quantisation alone
    already lands above -1.1, so the 59.94 tie does not pin the nudge (measured, AD12)."""
    p, d, tb = 2159 * 1001, 1001, (1, 24000)
    assert C.exact_end_vs_edge(p, d, tb, 902) == 0
    unnudged = C.rescale_q(p, tb, (1, 10 << 16)) / 65536.0 - 902 + C.out_duration(d, tb)
    assert unnudged < -1.1
    assert C.cfr_decision(*C.deltas(p, d, tb, 902))[0] == 1


# ── first / last slots, steady state ─────────────────────────────────────────

def test_first_slots_are_the_start_transient():
    for fps, tbd, want in ((Fraction(60), 15360, [0, 1, 5, 11]), (Fraction(30), 15360, [0, 1, 2, 5]),
                           (Fraction(24), 12288, [0, 1, 2, 4]), (Fraction(24000, 1001), 24000, [0, 1, 2, 4])):
        pts, durs, tb = cfr(fps, tbd, int(fps * 20))
        assert C.simulate(pts, durs, tb)[:4] == want
        for s in (0, 1):
            r = addr(s, pts, durs, tb, _m(fps, tbd))
            assert r["state"] == "refused" and r["reason"].startswith("start_transient")


def test_last_slot_is_refused_as_end_of_stream():
    pts, durs, tb = cfr(Fraction(24), 12288, 24 * 20)
    sim = C.simulate(pts, durs, tb)
    last = len(sim) - 1
    assert sim[last] == len(pts) - 1
    r = addr(last, pts, durs, tb, meta("24/1", tb="1/12288"), pnb=len(sim))
    assert r["state"] == "refused" and r["reason"].startswith("end_of_stream")
    r = addr(last + 1, pts, durs, tb, meta("24/1", tb="1/12288"), pnb=len(sim))
    assert r["state"] == "refused" and r["reason"].startswith("slot_not_in_proxy")


@pytest.mark.parametrize("fps,tbd", RATES)
def test_address_equals_whole_stream_replica_at_every_validated_rate(fps, tbd):
    pts, durs, tb = cfr(fps, tbd, int(fps * 120))
    sim = C.simulate(pts, durs, tb)
    addressed = 0
    for s in range(len(sim)):
        r = addr(s, pts, durs, tb, _m(fps, tbd), pnb=len(sim))
        if r["state"] == "addressed":
            addressed += 1
            assert r["source_index"] == sim[s], (s, r)
            assert r["addressing_version"] == C.ADDRESSING_VERSION
        else:
            assert s < 3 or s == len(sim) - 1, (s, r["reason"])
    assert addressed >= len(sim) - 4


def test_60fps_steady_state_is_v1s_minus_7():
    pts, durs, tb = cfr(Fraction(60), 15360, 60 * 60)
    for s in range(3, 590):
        assert addr(s, pts, durs, tb)["source_index"] == 6 * s - 7


# ── refusals, one per domain ─────────────────────────────────────────────────

def _one(src_meta=None, proxy_meta=None, slot=500, frames=None, nb=None, clock=CLOCK):
    pts, durs, tb = cfr(Fraction(60), 15360, 60 * 100)
    win = frames if frames is not None else C.window(pts, durs, tb, slot)
    return C.address_slot(slot * 1024, proxy_meta or PROXY, src_meta or meta(), win, clock=clock,
                          source_nb_frames=nb if nb is not None else len(pts), proxy_nb_frames=None)


@pytest.mark.parametrize("kw,reason", [
    (dict(src_meta=meta("60/1", avg="5997/100")), "source_vfr"),
    (dict(src_meta=meta(start="0.500000")), "source_start_time_nonzero"),
    (dict(src_meta=meta(fmt_start="1.000000")), "source_start_time_nonzero"),
    (dict(proxy_meta=meta("10/1", tb="1/10240", start="0.100000")), "proxy_start_time_nonzero"),
    (dict(proxy_meta=meta("10/1", tb="1/10240", start_pts="N/A")), "proxy_start_time_nonzero"),
    (dict(proxy_meta=meta("25/1", tb="1/12800")), "proxy_rate_not_validated"),
    (dict(src_meta=meta("10/1")), "source_fps_le_proxy_fps"),
    (dict(src_meta=meta("8/1")), "source_fps_le_proxy_fps"),
    (dict(src_meta=meta("50/1")), "source_rate_not_validated"),
    (dict(src_meta=meta("120/1")), "source_rate_not_validated"),
    (dict(src_meta=meta(tb="")), "pts_unknown"),
])
def test_out_of_contract_domains_are_refused(kw, reason):
    r = _one(**kw)
    assert r["state"] == "refused" and r["reason"].startswith(reason), r
    assert "source_index" not in r


def test_local_vfr_window_is_refused():
    pts, durs, tb = cfr(Fraction(60), 15360, 60 * 100)
    win = C.window(pts, durs, tb, 500)
    gap = win[:5] + [(p + 256, d) for p, d in win[6:]]
    assert _one(frames=gap)["reason"].startswith("vfr_local")
    odd = win[:5] + [(win[5][0], 300)] + win[6:]
    assert _one(frames=odd)["reason"].startswith("vfr_local")


def test_off_grid_unknown_and_short_are_refused():
    pts, durs, tb = cfr(Fraction(60), 15360, 60 * 100)
    kw = dict(clock=CLOCK, source_nb_frames=len(pts), proxy_nb_frames=None)
    assert C.address_slot(500 * 1024 + 1, PROXY, meta(), C.window(pts, durs, tb, 500), **kw)[
        "reason"].startswith("proxy_pts_off_grid")
    assert C.address_slot(None, PROXY, meta(), [], **kw)["reason"].startswith("pts_unknown")
    win = C.window(pts, durs, tb, 500)
    assert _one(frames=win[-4:])["reason"].startswith("window_short")
    assert _one(frames=win[:3])["state"] == "refused"


def test_domain_lists_every_failing_condition():
    d = C.domain(meta("25/1", tb="1/12800", start="0.1"), meta("8/1", start="0.2"))
    kinds = {x.split(" ")[0] for x in d["reasons"]}
    assert {"proxy_rate_not_validated", "proxy_start_time_nonzero", "source_start_time_nonzero",
            "source_fps_le_proxy_fps"} <= kinds


def test_slot_of_is_exact_and_refuses_off_grid():
    assert C.slot_of(500 * 1024, "1/10240") == 500
    assert C.slot_of(500 * 1024 + 1, "1/10240") is None
    assert C.slot_of(None, "1/10240") is None
    assert C.slot_of(1024, None) is None
    assert C.slot_of(True, "1/10") is None           # a bool is not a PTS


# ── the (recipe, build) pairs ────────────────────────────────────────────────

def test_the_exact_pair_is_validated_with_its_evidence():
    v = C.clock_validation(VALID_RECIPE, VALID_BUILD)
    assert v["state"] == "validated" and v["evidence"]


def test_every_validated_rate_is_covered_by_probe_evidence():
    ev = C.VALIDATED_CLOCKS[0]["evidence"][0]
    assert len(ev["sha256"]) == 64
    covered = {Fraction(r): x for r, x in ev["rates"].items()}
    assert set(covered) == C.VALIDATED_SOURCE_RATES
    assert all(x["sim_mismatches"] == 0 and x["addr_mismatches"] == 0 for x in covered.values())


@pytest.mark.parametrize("recipe,build", [(None, VALID_BUILD), (VALID_RECIPE, None), (None, None)])
def test_no_recorded_recipe_or_build_is_provenance_missing(recipe, build):
    v = C.clock_validation(recipe, build)
    assert v["state"] == "provenance_missing"
    r = _one(clock=v)
    assert r["state"] == "refused" and r["reason"].startswith("provenance_missing")


@pytest.mark.parametrize("field,value", [
    ("exe_sha256", "0" * 64),                                  # same version line, other binary
    ("version_line", "ffmpeg version 8.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the "
                     "FFmpeg developers"),                     # same major version
    ("libs", {**VALID_BUILD["libs"], "libavfilter": "11.14.100"}),
    ("configuration_sha256", "f" * 64),
])
def test_another_build_is_unvalidated_even_with_the_same_major_version(field, value):
    v = C.clock_validation(VALID_RECIPE, {**VALID_BUILD, field: value})
    assert v["state"] == "unvalidated"
    assert any(field in why for why in v["reasons"]), v
    # It would reproduce every proxy's frame count all the same: that is not an input,
    # and address_slot refuses on the clock before it looks at a single frame.
    r = _one(clock=v)
    assert r["state"] == "refused" and r["reason"].startswith("clock_unvalidated")


_CRF = C.VALIDATED_RECIPE_TEMPLATE.index("-crf") + 1


@pytest.mark.parametrize("recipe", [
    {**VALID_RECIPE, "template_sha256": C.template_sha256(
        C.VALIDATED_RECIPE_TEMPLATE[:_CRF] + ("28",) + C.VALIDATED_RECIPE_TEMPLATE[_CRF + 1:])},
    {**VALID_RECIPE, "params": {"width": 320, "fps": "10", "gop": "10"}},
    {**VALID_RECIPE, "params": {"width": 480, "fps": "12", "gop": "12"}},
])
def test_another_recipe_is_unvalidated(recipe):
    assert C.VALIDATED_RECIPE_TEMPLATE[_CRF] == "30"
    assert C.clock_validation(recipe, VALID_BUILD)["state"] == "unvalidated"


# ── the scene-change interval: (source_pts_prev, source_pts_cur] ─────────────

def _event_slot(sim: list[int], k: int) -> int:
    """The first slot whose frame is at or after the cut frame k: where a detector
    comparing consecutive slots first sees the new content."""
    return next(s for s, i in enumerate(sim) if i >= k)


def _interval(pts, durs, tb, m, n, pnb):
    prev = addr(n - 1, pts, durs, tb, m, pnb=pnb)
    cur = addr(n, pts, durs, tb, m, pnb=pnb)
    return prev, cur, C.interval_for_event(prev, cur, isolated=True)


@pytest.mark.parametrize("fps,tbd", RATES)
def test_every_cut_lies_in_the_interval_of_the_slot_that_first_shows_it(fps, tbd):
    pts, durs, tb = cfr(fps, tbd, int(fps * 30))
    sim = C.simulate(pts, durs, tb)
    m = _m(fps, tbd)
    checked = 0
    for k in range(int(fps * 2), int(fps * 28)):
        n = _event_slot(sim, k)
        prev, cur, iv = _interval(pts, durs, tb, m, n, len(sim))
        assert iv["state"] == "interval" and iv["hypothesis"] == "single_cut", (k, iv)
        assert C.interval_contains(iv, pts[k]), (k, n, iv)
        assert iv["frames"] == cur["source_index"] - prev["source_index"]
        checked += 1
    assert checked > 0


def test_interval_both_ends_explicitly():
    """60 fps, slot 100 shows frame 593, slot 99 shows frame 587."""
    pts, durs, tb = cfr(Fraction(60), 15360, 60 * 30)
    sim = C.simulate(pts, durs, tb)
    prev, cur, iv = _interval(pts, durs, tb, meta(), 100, len(sim))
    assert (prev["source_index"], cur["source_index"]) == (587, 593)
    assert (iv["source_pts_after"], iv["source_pts_through"]) == (pts[587], pts[593])
    # exclusive before: a cut AT the frame slot 99 shows would have been seen at slot 99
    assert not C.interval_contains(iv, pts[587])
    assert _event_slot(sim, 587) == 99
    # the first frame after it is the lowest cut slot 100 can report
    assert C.interval_contains(iv, pts[588]) and _event_slot(sim, 588) == 100
    # inclusive current: the frame slot 100 shows may itself be the first new one
    assert C.interval_contains(iv, pts[593]) and _event_slot(sim, 593) == 100
    # and one frame later is the next slot's business
    assert not C.interval_contains(iv, pts[594]) and _event_slot(sim, 594) == 101
    assert iv["frames"] == 6


@pytest.mark.parametrize("isolated,reason", [
    (False, "needs_source_refinement"),        # neighbouring pair also changed
    (None, "isolation_unknown"),               # nobody looked: not a pass
    (1, "isolation_not_a_verdict"),            # 1 == True, but it is not a verdict
])
def test_several_cuts_or_a_transition_never_get_an_interval(isolated, reason):
    pts, durs, tb = cfr(Fraction(60), 15360, 60 * 30)
    prev, cur = addr(99, pts, durs, tb), addr(100, pts, durs, tb)
    r = C.interval_for_event(prev, cur, isolated=isolated)
    assert r["state"] == "refused" and r["reason"].startswith(reason), r
    assert "source_pts_after" not in r


def test_a_dissolve_seen_on_two_consecutive_pairs_is_refused_on_both():
    """A transition spanning slots 99..101 changes both pairs; the detector reports
    each change as not isolated, and neither gets an interval."""
    pts, durs, tb = cfr(Fraction(60), 15360, 60 * 30)
    a = [addr(s, pts, durs, tb) for s in (99, 100, 101)]
    for prev, cur in ((a[0], a[1]), (a[1], a[2])):
        assert C.interval_for_event(prev, cur, isolated=False)["reason"].startswith(
            "needs_source_refinement")


def test_interval_refuses_refused_or_non_adjacent_samples():
    pts, durs, tb = cfr(Fraction(60), 15360, 60 * 30)
    a99, a100, a101 = (addr(s, pts, durs, tb) for s in (99, 100, 101))
    assert C.interval_for_event(a99, a101, isolated=True)["reason"].startswith("samples_not_adjacent")
    bad = addr(1, pts, durs, tb)
    assert C.interval_for_event(bad, addr(2, pts, durs, tb), isolated=True)["reason"].startswith(
        "previous_sample_not_addressed")
    assert C.interval_for_event(a100, a99, isolated=True)["state"] == "refused"


# ── real ffmpeg: constructed barcode sources through the recipe ──────────────

_HAVE_FFMPEG = bool(shutil.which(ffmpeg_bin()) and shutil.which(ffprobe_bin()))
real = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
BITS = 16


def _barcode_source(dest: Path, n: int, w: int, h: int, fps: Fraction, tb_den: int, step: int,
                    bsf: str | None = None) -> None:
    """n gray frames, frame i drawing i in BITS vertical bars; pts = i * step at tb 1/tb_den."""
    bw = w // BITS
    cols = np.arange(w) // bw
    log = dest.with_suffix(".log")
    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", f"{w}x{h}", "-framerate", f"{fps.numerator}/{fps.denominator}", "-i", "-",
           "-vf", f"settb=1/{tb_den},setpts=N*{step}", "-fps_mode", "passthrough",
           "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-bf", "0", "-pix_fmt", "yuv420p",
           "-enc_time_base:v", f"1/{tb_den}"] + (["-bsf:v", bsf] if bsf else []) + [
           "-video_track_timescale", str(tb_den), str(dest)]
    with open(log, "wb") as err:           # stderr to a file: a pipe nobody drains blocks ffmpeg
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        for i in range(n):
            bits = ((i >> np.minimum(cols, BITS - 1)) & 1) * 255
            proc.stdin.write(np.broadcast_to(bits.astype(np.uint8), (h, w)).tobytes())
        proc.stdin.close()
        assert proc.wait() == 0, log.read_text(errors="replace")


def _recipe_proxy(src: Path, out: Path, width: int) -> None:
    fill = {"source": str(src), "width": str(width), "fps": "10", "gop": "10", "out": str(out)}
    subprocess.run([ffmpeg_bin()] + [t.format(**fill) for t in C.VALIDATED_RECIPE_TEMPLATE], check=True)


def _packets(media: Path) -> tuple[list[int], list[int]]:
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "packet=pts,duration", "-of", "csv=p=0", str(media)],
                       capture_output=True, text=True, check=True)
    rows = sorted(tuple(int(x) for x in ln.strip().rstrip(",").split(",")[:2])
                  for ln in r.stdout.splitlines() if ln.strip())
    return [p for p, _ in rows], [d for _, d in rows]


def _read_slots(proxy: Path, w: int, h: int) -> list[int | None]:
    """Every proxy frame, in presentation order, read back as the index it shows."""
    pp, _ = _packets(proxy)
    assert pp == [k * 1024 for k in range(len(pp))]               # every slot exactly on the grid
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(proxy), "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w)
    assert len(frames) == len(pp)
    bw = w // BITS
    out: list[int | None] = []
    for f in frames:
        means = [f[h // 4:3 * h // 4, b * bw + bw // 4:b * bw + 3 * bw // 4].mean() for b in range(BITS)]
        if any(64 < v < 192 for v in means):
            out.append(None)                                      # not a clean read: never a guess
        else:
            out.append(sum(1 << b for b, v in enumerate(means) if v >= 192))
    return out


def _constructed(tmp: Path, fps: Fraction, tb_den: int, secs: float, at: int | None = None,
                 shift: int = 0, w: int = 256, h: int = 144):
    step = int(Fraction(tb_den) / fps)
    bsf = None
    if at is not None:
        sh = f"gt(N\\,{at})*({shift})"                 # \, : a bare comma splits the bsf chain
        bsf = f"setts=pts=PTS+{sh}:dts=DTS+{sh}:duration=DURATION+eq(N\\,{at})*({shift})"
    src, prx = tmp / "src.mp4", tmp / "prx.mp4"
    _barcode_source(src, int(fps * secs), w, h, fps, tb_den, step, bsf)
    pts, durs = _packets(src)
    _recipe_proxy(src, prx, w)
    shown = _read_slots(prx, w, h)
    assert None not in shown
    return shown, C.simulate(pts, durs, (1, tb_den)), pts, durs


@real
@pytest.mark.parametrize("shift", [-1, 0, 1])
def test_real_ffmpeg_threshold_non_integer_sync(tmp_path, shift):
    shown, sim, pts, durs = _constructed(tmp_path, Fraction(60000, 1001), 60000, 91, 5399, shift)
    i = 5399
    assert (pts[i], durs[i], pts[i + 1]) == (i * 1001, 1001 + shift, (i + 1) * 1001 + shift)
    assert C.exact_end_vs_edge(pts[i], durs[i], (1, 60000), 902) == shift
    assert shown == sim
    assert shown[902] == (5400 if shift < 0 else 5399)


@real
@pytest.mark.parametrize("shift", [-1, 0, 1])
def test_real_ffmpeg_threshold_nudge_23976(tmp_path, shift):
    shown, sim, pts, durs = _constructed(tmp_path, Fraction(24000, 1001), 24000, 91, 2159, shift)
    assert C.exact_end_vs_edge(pts[2159], durs[2159], (1, 24000), 902) == shift
    assert shown == sim
    assert shown[902] == (2160 if shift < 0 else 2159)


@real
@pytest.mark.parametrize("shift", [-1, 0, 1])
def test_real_ffmpeg_threshold_integer_sync(tmp_path, shift):
    """Frame 50 starts at 4.5 s (on the slot grid) and, unshifted, ends at slot 47 - 0.11,
    past the ~2 s start transient of a 100/9 fps source."""
    shown, sim, pts, durs = _constructed(tmp_path, Fraction(100, 9), 100, 6, 50, shift)
    assert (pts[50], durs[50], pts[51]) == (450, 9 + shift, 459 + shift)
    assert C.exact_end_vs_edge(450, 9 + shift, (1, 100), 47) == shift
    assert shown == sim
    assert shown[46] == 49
    assert shown[47] == (51 if shift < 0 else 50)


@real
@pytest.mark.parametrize("fps,tbd", RATES)
def test_real_ffmpeg_recipe_at_width_480_matches_replica_and_address(tmp_path, fps, tbd):
    """The installed build, the validated recipe and parameters, 20 s per rate: every slot
    equals simulate(), and address_slot() names the same frame on every steady slot."""
    shown, sim, pts, durs = _constructed(tmp_path, fps, tbd, 20, w=480, h=272)
    assert shown == sim
    m = _m(fps, tbd)
    for s in range(3, len(sim) - 1):
        r = C.address_slot(s * 1024, PROXY, m, C.window(pts, durs, (1, tbd), s), clock=CLOCK,
                           source_nb_frames=len(pts), proxy_nb_frames=len(sim))
        assert r["state"] == "addressed" and r["source_index"] == shown[s], (s, r)
