"""Addressing step 3 on real ffmpeg: constructed sources with cuts at known frames, the
proxy made by the shipping proxy_recipe(), provenance by record(), in the suite's
throwaway storage root.

Runs only on the validated ffmpeg build: on any other build every test SKIPS with the
build named — it does not pass. test_clipper_proxy_clock.py's re-probe stays the gate
for the build itself.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from services.clipper import proxy_clock as C, proxy_provenance as P, scene_address as S, signals, storage
from services.clipper.ffmpeg_tools import ffmpeg_bin, ffprobe_bin, run


def _build_skip() -> str | None:
    if not (shutil.which(ffmpeg_bin()) and shutil.which(ffprobe_bin())):
        return "ffmpeg/ffprobe not installed"
    b = P.ffmpeg_build()
    want = C.VALIDATED_CLOCKS[0]["ffmpeg_build"]
    if any(b.get(k) != v for k, v in want.items()):
        return f"installed ffmpeg is not the validated build: {b.get('version_line')!r}"
    return None


_SKIP = _build_skip()
pytestmark = [pytest.mark.real_ffmpeg, pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")]


@pytest.fixture(autouse=True)
def _scene_addressing_on(monkeypatch):
    """This file tests the 3b pass itself, which is opt-in since codex-verdict-next-16 §3."""
    from config import settings
    monkeypatch.setattr(settings, "clipper_scene_addressing", True)

W, H = 480, 272
RATES = [(Fraction(60), 15360), (Fraction(30), 15360), (Fraction(25), 12800),
         (Fraction(60000, 1001), 60000), (Fraction(24), 12288), (Fraction(24000, 1001), 24000)]


def _texture(seed: int) -> np.ndarray:
    """16-px blocks of random grey: survives crf 30, and two of them differ by ~85 on average."""
    rng = np.random.default_rng(seed)
    return np.kron(rng.integers(0, 256, (H // 16 + 1, W // 16 + 1)), np.ones((16, 16)))[:H, :W].astype(np.uint8)


def _source(dest: Path, n: int, fps: Fraction, tbd: int, cuts: list[int],
            dissolves: tuple[tuple[int, int], ...] = (), noise_rows: int = 0, solid: bool = False) -> None:
    """n frames, pts exactly i*step at 1/tbd, lossless, no B-frames. Frame i shows texture
    #(cuts <= i) (solid: black / white alternately); a dissolve (start, length) blends into the next."""
    step = int(Fraction(tbd) / fps)
    tex: dict[int, np.ndarray] = {}
    look = (lambda s: np.full((H, W), 255 * (s % 2), np.uint8)) if solid else _texture
    rng = np.random.default_rng(99)
    with open(dest.with_suffix(".log"), "wb") as err:     # stderr to a file: never an undrained pipe
        proc = subprocess.Popen(
            [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}",
             "-framerate", f"{fps.numerator}/{fps.denominator}", "-i", "-",
             "-vf", f"settb=1/{tbd},setpts=N*{step}", "-fps_mode", "passthrough",
             "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-bf", "0", "-pix_fmt", "yuv420p",
             "-enc_time_base:v", f"1/{tbd}", "-video_track_timescale", str(tbd), str(dest)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        for i in range(n):
            seg = sum(i >= c for c in cuts)
            frame = tex.setdefault(seg, look(seg)).astype(np.float64)
            for start, length in dissolves:
                if start <= i < start + length:
                    a = (i - start + 1) / (length + 1)
                    frame = (1 - a) * tex.setdefault(seg, look(seg)) + a * tex.setdefault(seg + 1, look(seg + 1))
            frame = frame.astype(np.uint8)
            if noise_rows:                                  # incompressible: makes the file > 2 MiB
                frame[:noise_rows] = rng.integers(0, 256, (noise_rows, W), dtype=np.uint8)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        assert proc.wait() == 0, dest.with_suffix(".log").read_text(errors="replace")


def _project(tmp: Path, pid: str, fps: Fraction, tbd: int, secs: float, cuts: list[int], *,
             record: bool = True, build: dict | None = None, **kw) -> tuple[Path, Path]:
    src = tmp / f"{pid}_src.mp4"
    _source(src, int(fps * secs), fps, tbd, cuts, **kw)
    storage.ensure_dirs(pid)
    prx = storage.paths(pid)["proxy"]
    exe = ffmpeg_bin()
    real_build = P.ffmpeg_build(exe)                       # identified before the encode runs
    argv, recipe = P.proxy_recipe(str(src), str(prx), W, 10.0)
    subprocess.run([exe, *argv], check=True)
    if record:
        P.record(pid, src, prx, recipe, build=build or real_build)
    return src, prx


def _addressed(pid: str, prx: Path) -> tuple[dict, dict]:
    before = S.provenance_now(pid, str(prx))
    sc = S.scene_pass(str(prx))
    return sc, S.address_scenes(pid, str(prx), sc, before)


def _shown(fps: Fraction, tbd: int, n_frames: int) -> list[int]:
    step = int(Fraction(tbd) / fps)
    return C.simulate([i * step for i in range(n_frames)], [step] * n_frames, (1, tbd))


def _legacy(prx: Path) -> list[float]:
    """The pre-change scene_timeline command, verbatim, and its expression."""
    out = run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(prx), "-an",
               "-vf", "select='gt(scene,0.3000)',metadata=print:file=-", "-f", "null", "-"],
              timeout=3600, what="scene detect")
    return S.legacy_times(out)


@pytest.mark.parametrize("fps,tbd", RATES, ids=[str(r[0]) for r in RATES])
def test_every_inserted_cut_on_every_phase_is_an_interval_that_contains_it(tmp_path, fps, tbd):
    secs = 26
    n_frames = int(fps * secs)
    shown = _shown(fps, tbd, n_frames)
    # Every frame a slot can first show a change on: (shown[n-1], shown[n]] - both ends included.
    cuts, n = [], 25
    phases = []
    while n < 10 * secs - 25:
        span = list(range(shown[n - 1] + 1, shown[n] + 1))
        k = len(cuts) % len(span)
        cuts.append(span[k])
        phases.append((k, len(span)))
        n += 20
    assert {k for k, _ in phases} == set(range(max(m for _, m in phases)))   # every phase visited
    pid = f"ad3rate{fps.numerator}_{fps.denominator}"
    src, prx = _project(tmp_path, pid, fps, tbd, secs, cuts)
    sc, doc = _addressed(pid, prx)
    assert doc["state"] == "ok", doc["reason"]
    assert sc["times"] == _legacy(prx)                                       # legacy equality
    assert doc["counts"]["detected"] == len(cuts), doc["counts"]           # denominator first
    step = int(Fraction(tbd) / fps)
    stb = f"1/{tbd}"
    for cut, row in zip(cuts, doc["rows"]):
        assert row["state"] == "interval", (cut, row)
        assert C.interval_contains(row["interval"], cut * step), (cut, row["interval"])
        n = row["slot"]
        whole = S.read_source_packets(str(src), n, stb, windowed=False)
        part = S.read_source_packets(str(src), n, stb)
        for m in (n - 1, n):
            assert C.window(*part[:2], part[2], m) == C.window(*whole[:2], whole[2], m)
    assert doc["counts"]["interval"] == len(cuts) and doc["counts"]["refused"] == 0


def test_a_cut_in_slot_1_is_the_start_transient_and_one_at_the_last_slot_is_refused(tmp_path):
    fps, tbd, secs = Fraction(60), 15360, 8
    n_frames = int(fps * secs)
    shown = _shown(fps, tbd, n_frames)
    last = len(shown) - 1
    cuts = [shown[1], shown[last - 1] + 1]
    src, prx = _project(tmp_path, "ad3ends", fps, tbd, secs, cuts)
    sc, doc = _addressed("ad3ends", prx)
    assert doc["state"] == "ok" and doc["counts"]["detected"] == 2
    first, end = doc["rows"]
    assert first["slot"] == 1 and first["reason"].startswith("start_transient")
    assert end["slot"] == last and shown[last] == n_frames - 1
    assert end["reason"].startswith("end_of_stream") and end["refused_at"] == "current"


@pytest.mark.parametrize("kind", ["two_cuts_one_slot_apart", "dissolve_0.3s"])
def test_changed_neighbours_never_get_an_interval(tmp_path, kind):
    fps, tbd, secs = Fraction(60), 15360, 8
    if kind == "two_cuts_one_slot_apart":
        src, prx = _project(tmp_path, "ad3pair", fps, tbd, secs, [240, 246])
    else:
        src, prx = _project(tmp_path, "ad3dissolve", fps, tbd, secs, [240 + 18], dissolves=((240, 18),), solid=True)
    sc, doc = _addressed("ad3pair" if kind.startswith("two") else "ad3dissolve", prx)
    assert doc["state"] == "ok" and doc["counts"]["detected"] >= 1
    assert all(r["reason"].startswith("needs_source_refinement") for r in doc["rows"]), doc["rows"]
    assert doc["counts"]["interval"] == 0


def test_a_rewritten_proxy_is_identity_changed_and_scenes_do_not_move(tmp_path):
    src, prx = _project(tmp_path, "ad3rewrite", Fraction(30), 15360, 8, [100])
    sc0, doc0 = _addressed("ad3rewrite", prx)
    assert doc0["state"] == "ok" and doc0["counts"]["interval"] == 1
    prx.write_bytes(prx.read_bytes())                     # same bytes, new mtime: not the registered file
    sc1, doc1 = _addressed("ad3rewrite", prx)
    assert doc1["state"] == "identity_changed" and sc1["times"] == sc0["times"]
    assert all(r["state"] == "refused" and r["refused_at"] == "project" for r in doc1["rows"])
    assert doc1["counts"]["detected"] == 1 and doc1["counts"]["refused"] == 1


def test_a_proxy_changed_during_the_step_voids_the_pass(tmp_path, monkeypatch):
    src, prx = _project(tmp_path, "ad3during", Fraction(30), 15360, 8, [100])
    before = S.provenance_now("ad3during", str(prx))
    sc = S.scene_pass(str(prx))
    real = S.address_row

    def touching(row, ctx):
        out = real(row, ctx)
        os.utime(prx, ns=(prx.stat().st_atime_ns, prx.stat().st_mtime_ns + 10**9))
        return out
    monkeypatch.setattr(S, "address_row", touching)
    doc = S.address_scenes("ad3during", str(prx), sc, before)
    assert doc["state"] == "identity_changed" and "after the step" in doc["reason"]
    assert [r["state"] for r in doc["rows"]] == ["refused"]


def test_a_same_size_edit_with_mtime_restored_is_the_declared_cache_limit(tmp_path):
    src, prx = _project(tmp_path, "ad3sneaky", Fraction(30), 15360, 4, [60], noise_rows=64)
    assert src.stat().st_size > 3 * (1 << 20)
    st = src.stat()
    with open(src, "r+b") as fh:                          # one byte in the middle, same size
        fh.seek(st.st_size // 2)
        b = fh.read(1)
        fh.seek(st.st_size // 2)
        fh.write(bytes([b[0] ^ 0xFF]))
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    sc, doc = _addressed("ad3sneaky", prx)
    assert doc["provenance"]["before"] == "recorded"      # CACHE_REUSE cannot see it...
    assert doc["provenance"]["identity_check"].startswith("cache_reuse")
    assert P.verify_provenance("ad3sneaky", source_path=src, proxy_path=prx)["state"] == "identity_changed"


def _signals(pid: str, prx: Path) -> dict:
    # OW1: build_signals returns its result and writes nothing.
    return signals.build_signals(pid, str(prx), str(storage.paths(pid)["audio"]), 0.0)


def test_decode_error_is_not_zero_scenes_through_build_signals(tmp_path):
    storage.ensure_dirs("ad3missing")
    missing = _signals("ad3missing", storage.paths("ad3missing")["proxy"])
    src, prx = _project(tmp_path, "ad3trunc", Fraction(30), 15360, 4, [60])
    prx.write_bytes(prx.read_bytes()[:1000])
    trunc = _signals("ad3trunc", prx)
    src, prx = _project(tmp_path, "ad3static", Fraction(30), 15360, 4, [])
    static = _signals("ad3static", prx)
    assert missing["scenes"] == trunc["scenes"] == static["scenes"] == []
    got = [(s["scenes_addressed"]["state"], s["scenes_addressed"]["counts"]["detected"])
           for s in (missing, trunc, static)]
    assert got == [("proxy_missing", None), ("decode_failed", None), ("ok", 0)]


def test_an_unknown_build_and_a_legacy_proxy_refuse_every_row_through_build_signals(tmp_path):
    build = P.ffmpeg_build()
    build = {**build, "libs": {**build["libs"], "libavutil": "60.26.999"}}
    src, prx = _project(tmp_path, "ad3build", Fraction(30), 15360, 6, [100], build=build)
    got = _signals("ad3build", prx)["scenes_addressed"]
    assert got["state"] == "clock_unvalidated" and got["reason"].startswith("clock_unvalidated (ffmpeg_build")
    assert [r["state"] for r in got["rows"]] == ["refused"] and got["counts"]["detected"] == 1
    src, prx = _project(tmp_path, "ad3legacy", Fraction(30), 15360, 6, [100], record=False)
    sig = _signals("ad3legacy", prx)
    got = sig["scenes_addressed"]
    assert got["state"] == "provenance_missing" and [r["reason"].split(" ")[0] for r in got["rows"]] == \
        ["provenance_missing"]
    assert "interval" not in got["rows"][0] and sig["scenes"] == [round(got["rows"][0]["t"], 3)]
