"""window_address through the real window encode, with the barcode as the independent witness.

Barcode sources (frame i draws i in 16 bars, the AD3 helpers of
test_clipper_proxy_clock.py), the proxy made by the shipping recipe, then
`analyse_window(..., address=True)`. Every addressed point is checked against the
barcode of the pixels actually read, compared on SOURCE PTS (CC1-C0 §8.6), never
on the grid alone: a one-slot slip stays on the grid (C0 §3).

Runs only on the validated ffmpeg build; on any other build every test SKIPS.
"""
from __future__ import annotations

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.clipper import dynamic_window as DW, proxy_clock as C, proxy_provenance as P
from services.clipper import scene_address as S, storage, window_address as A
from services.clipper.ffmpeg_tools import ffmpeg_bin, ffprobe_bin
from tests.test_clipper_proxy_clock import BITS, _barcode_source, _packets


def _build_skip() -> str | None:
    if not (shutil.which(ffmpeg_bin()) and shutil.which(ffprobe_bin())):
        return "ffmpeg/ffprobe not installed"
    b = P.ffmpeg_build()
    if any(b.get(k) != v for k, v in C.VALIDATED_CLOCKS[0]["ffmpeg_build"].items()):
        return f"installed ffmpeg is not the validated build: {b.get('version_line')!r}"
    return None


_SKIP = _build_skip()
pytestmark = [pytest.mark.real_ffmpeg, pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")]

W, H = 480, 272
STARTS = ["12.340", "0.020", "0.050", "0.080", "0.250", "12.345",
          "0.000", "5.020", "5.050", "5.080", "5.250", "16.050"]
LEGACY = {"faces", "motion", "motion_hop", "face_decoded_space", "proxy_width", "proxy_height",
          "focus", "detail", "ui", "band", "panels", "hop"}


def _bar(grey) -> int | None:
    bw = W // BITS
    means = [grey[H // 4:3 * H // 4, b * bw + bw // 4:b * bw + 3 * bw // 4].mean() for b in range(BITS)]
    if any(64 < v < 192 for v in means):
        return None                                          # never a guess
    return sum(1 << b for b, v in enumerate(means) if v >= 192)


class _BarDetector:
    """Reads the barcode of the frame face_presence handed it; never detects."""
    def __init__(self):
        self.seen: list[int | None] = []

    def detect(self, frame):
        import cv2
        self.seen.append(_bar(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
        return {"state": "empty", "reason": None, "boxes": []}


def _cv_bars(window: Path) -> list[int | None]:
    """The pixels of each sequential OpenCV read, independent of the telemetry."""
    import cv2
    cap, out = cv2.VideoCapture(str(window)), []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        out.append(_bar(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
    cap.release()
    return out


def _project(tmp: Path, pid: str, fps: Fraction, tbd: int, secs: int, bsf: str | None = None) -> dict:
    src = tmp / f"{pid}_src.mp4"
    _barcode_source(src, int(fps * secs), W, H, fps, tbd, int(Fraction(tbd) / fps), bsf)
    storage.ensure_dirs(pid)
    prx = storage.paths(pid)["proxy"]
    build = P.ffmpeg_build(ffmpeg_bin())
    argv, recipe = P.proxy_recipe(str(src), str(prx), W, 10.0)
    subprocess.run([ffmpeg_bin(), *argv], check=True)
    P.record(pid, src, prx, recipe, build=build)
    return {"pid": pid, "src": src, "prx": prx, "spts": _packets(src)[0], "fps": fps, "tbd": tbd,
            "prov": lambda: S.provenance_now(pid, str(prx))}


def _edited(p: dict, vf: str) -> dict:
    """The proxy with frames removed or moved (CC1-C0 `gap` / `drop3`), PTS kept, lossless.
    Not a recipe output, so its provenance is constructed: the domain is what is under test."""
    out = p["src"].with_name(p["pid"] + "_" + str(abs(hash(vf)) % 10**6) + ".mp4")
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(p["prx"]), "-vf", vf,
                    "-fps_mode", "passthrough", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0",
                    "-bf", "0", "-pix_fmt", "yuv420p", "-enc_time_base:v", "1/10240",
                    "-video_track_timescale", "10240", str(out)], check=True, capture_output=True)
    return {**p, "prx": out, "prov": _fake_prov(p, out)}


def _fake_prov(p: dict, prx: Path):
    rate = f"{p['fps'].numerator}/{p['fps'].denominator}"

    def ident(path: Path, stream: dict) -> dict:
        return {"path": str(path), "sha256": path.name, "fingerprint": {"f": path.name},
                "stream": {**stream, "start_time": "0.000000", "start_pts": 0}, "format": {"start_time": "0.000000"}}
    doc = {"state": "recorded", "recorded_at": "t", "ffmpeg_build": {"exe_sha256": "e"},
           "identity_check": {"source": "cache_reuse", "proxy": "cache_reuse"},
           "clock": {"state": "validated", "clock": "cc1 constructed"}, "domain": {"state": "ok"},
           "proxy_identity": ident(prx, {"r_frame_rate": "10/1", "avg_frame_rate": "10/1", "time_base": "1/10240",
                                         "nb_frames": len(_packets(prx)[0])}),
           "source_identity": ident(p["src"], {"r_frame_rate": rate, "avg_frame_rate": rate,
                                               "time_base": f"1/{p['tbd']}", "nb_frames": len(p["spts"])})}
    return lambda: doc


@pytest.fixture(scope="module")
def r60(tmp_path_factory):
    return _project(tmp_path_factory.mktemp("r60"), "cc1r60", Fraction(60), 15360, 20)


@pytest.fixture(scope="module")
def r23976(tmp_path_factory):
    return _project(tmp_path_factory.mktemp("r23976"), "cc1r23976", Fraction(24000, 1001), 24000, 20)


@pytest.fixture
def keep(monkeypatch, tmp_path):
    """analyse_window's temp dir, kept so the window and its stats can be witnessed."""
    monkeypatch.setattr(DW.tempfile, "mkdtemp", lambda prefix="": str(tmp_path))
    monkeypatch.setattr(DW.shutil, "rmtree", lambda *a, **k: None)
    return tmp_path


def _run(p: dict, start: str, dur: str = "6.000") -> tuple[dict, _BarDetector]:
    det = _BarDetector()
    res = DW.analyse_window(p["prx"], float(start), float(dur), (0.0, 1.0, 0.0, 1.0), W, detector=det,
                            address=True, provenance=p["prov"])
    return res, det


def _witness(res: dict, det: _BarDetector, window: Path, spts: list[int]) -> dict:
    """Addressed points whose source PTS is NOT the PTS of the frame the barcode shows."""
    wa, bars = res["window_addressed"], _cv_bars(window)
    seen = iter(det.seen)
    checked = wrong = 0
    for raw, row in zip(res["faces"], wa["faces"]):
        bar = next(seen) if raw["state"] != "unreadable" else None
        if row["state"] == "addressed":
            checked += 1
            wrong += bar is None or spts[bar] != row["source_pts"]
    for m in wa["motion"]:
        pt = m["point"]
        if pt["state"] == "addressed":
            checked += 1
            wrong += bars[m["read"]] is None or spts[bars[m["read"]]] != pt["source_pts"]
    for end in ("first", "last"):
        pt = wa["panels"].get(end) or {}
        if pt.get("state") == "addressed":
            checked += 1
            wrong += spts[bars[pt["read"]]] != pt["source_pts"]
    return {"checked": checked, "wrong": wrong, "reads": len(bars)}


def _refusals(wa: dict) -> set[str]:
    rows = wa["faces"] + [m["point"] for m in wa["motion"]]
    return {r["reason"].split(" ")[0] for r in rows if r["state"] == "refused"}


@pytest.mark.parametrize("start", STARTS)
def test_the_twelve_phases_address_what_the_barcode_shows_60(r60, keep, start):
    res, det = _run(r60, start)
    wa = res["window_addressed"]
    w = _witness(res, det, keep / "window.mp4", r60["spts"])
    assert wa["state"] == "ok" and wa["tie"]["rows"] == w["reads"] > 0
    assert w["checked"] > 0 and w["wrong"] == 0, w
    assert _refusals(wa) <= {"start_transient", "end_of_stream", "sample_unreadable"}
    assert {m["pair"]["state"] for m in wa["motion"][1:]} <= {"span", "refused"}
    assert wa["motion"][0]["state"] == "motion_init"
    assert wa["coverage"]["motion"]["state"] == "complete"


@pytest.mark.parametrize("start", STARTS[:6])
def test_the_addendum_phases_address_what_the_barcode_shows_23976(r23976, keep, start):
    res, det = _run(r23976, start)
    w = _witness(res, det, keep / "window.mp4", r23976["spts"])
    assert res["window_addressed"]["state"] == "ok" and w["checked"] > 0 and w["wrong"] == 0, w


@pytest.mark.parametrize("shift", [-1024, 1024])
def test_a_one_slot_slip_stays_on_the_grid_and_the_barcode_catches_it(r60, keep, monkeypatch, shift):
    real = A.ts_offset
    monkeypatch.setattr(A, "ts_offset", lambda s, tb: real(s, tb) + shift)
    res, det = _run(r60, "12.340")
    w = _witness(res, det, keep / "window.mp4", r60["spts"])
    assert res["window_addressed"]["state"] == "ok"                 # the self-check passes it...
    assert w["checked"] > 0 and w["wrong"] == w["checked"]          # ...every address is wrong


def test_a_gap_is_one_unsteady_pair_and_every_frame_keeps_its_own_address(r60, keep):
    p = _edited(r60, "select=not(eq(n\\,100))")
    res, det = _run(p, "9.000", "4.000")
    wa = res["window_addressed"]
    w = _witness(res, det, keep / "window.mp4", p["spts"])
    assert wa["state"] == "ok" and w["checked"] > 0 and w["wrong"] == 0, w
    bad = [m["pair"] for m in wa["motion"] if m["pair"]["state"] == "refused"]
    assert len(bad) == 1 and bad[0]["reason"].startswith("pair_not_steady")
    assert bad[0]["slots"][1] - bad[0]["slots"][0] == 3
    assert {m["pair"]["proxy_duration"] for m in wa["motion"] if m["state"] == "span"} == {"1/5"}
    assert res["motion_hop"] != 0.2              # the legacy step/fps: not a measured duration here


def test_three_frames_on_one_slot_drop_one_and_refuse_the_window(r60, keep):
    p = _edited(r60, "setpts=if(eq(N\\,100)\\,PTS-1024\\,if(eq(N\\,101)\\,PTS-2048\\,PTS))")
    res, _det = _run(p, "8.020", "4.000")
    wa = res["window_addressed"]
    rows = A.parse_enc_stats((keep / "enc_stats.txt").read_text())
    assert any(b["ni"] - a["ni"] == 2 for a, b in zip(rows, rows[1:]))          # the real drop
    assert wa["state"] == "window_tie_off_grid"
    assert {r["refused_at"] for r in wa["faces"] + wa["motion"]} == {"window"}


def test_a_shifted_source_is_refused_per_sample_and_never_misaddressed(tmp_path, keep):
    shift = "gt(N\\,200)*(1280)"
    p = _project(tmp_path, "cc1srcgap", Fraction(25), 12800, 20,
                 f"setts=pts=PTS+{shift}:dts=DTS+{shift}:duration=DURATION+eq(N\\,200)*(1280)")
    p = {**p, "prov": _fake_prov(p, p["prx"])}
    res, det = _run(p, "7.520", "4.000")
    w = _witness(res, det, keep / "window.mp4", p["spts"])
    assert res["window_addressed"]["state"] == "ok" and w["checked"] > 0 and w["wrong"] == 0, w
    assert "vfr_local" in _refusals(res["window_addressed"])


def test_a_source_that_cannot_be_probed_refuses_the_window(r60, keep, tmp_path):
    p = {**r60, "src": tmp_path / "gone.mp4"}
    res, _ = _run({**p, "prov": _fake_prov(p, r60["prx"])}, "12.340")
    assert res["window_addressed"]["state"] == "source_window_unreadable"


def _damaged(window: Path, kind: str) -> Path:
    b = bytearray(window.read_bytes())
    if kind == "truncated":
        b = b[:int(len(b) * 0.8)]                  # the window encode has no +faststart: moov is lost
    else:
        i = 0
        while b[i + 4:i + 8] != b"mdat":
            i += int.from_bytes(b[i:i + 4], "big")
        lo, hi = i + 8, i + int.from_bytes(b[i:i + 4], "big")
        at = lo + int((hi - lo) * {"zero40": 0.4, "zero70": 0.7}[kind])
        b[at:at + 3000] = bytes(3000)
    out = window.with_name(f"{kind}.mp4")
    out.write_bytes(bytes(b))
    return out


def _compose_on(p: dict, keep: Path, window: Path, hop: float) -> tuple[dict, list]:
    seen_m, seen_p = {}, {}
    totals = DW.region_motion(window, hop, (0.0, 1.0, 0.0, 1.0), W, observed=seen_m)[0]
    panels = DW.ui_panels(window, W, H, observed=seen_p)
    before = p["prov"]()
    wa = A.compose(request={"start": "12.340", "duration": "6.000"},
                   recipe=A.recipe_of(A.WINDOW_TEMPLATE[:-1] + ("w.mp4",)),
                   stats_text=(keep / "enc_stats.txt").read_text(), faces=[], motion_count=len(totals),
                   seen_motion=seen_m, seen_panels=seen_p, before=before, read_after=p["prov"],
                   build=P.ffmpeg_build(ffmpeg_bin()))
    return wa, panels


def test_damaged_windows_report_what_was_read_and_never_certify_pixels(r60, keep):
    res, _ = _run(r60, "12.340")
    clean = keep / "window.mp4"
    assert res["panels"] == []                               # this window has no panels...
    shut, panels = _compose_on(r60, keep, _damaged(clean, "truncated"), DW.FACE_HOP_S)
    assert panels == []                                      # ...and an unopened file says the same
    # Its reader never opened, so its backend is unknown: the window is refused (C1r), and
    # the coverage still says nothing was read.
    assert shut["state"] == "window_reader_unvalidated" and "capture_not_opened" in shut["reason"]
    assert shut["panels"]["state"] == "refused" and {m["state"] for m in shut["motion"]} <= {"refused"}
    assert shut["coverage"]["panels"]["state"] == shut["coverage"]["motion"]["state"] == "not_opened"
    early, _ = _compose_on(r60, keep, _damaged(clean, "zero40"), DW.FACE_HOP_S)
    cov = early["coverage"]["motion"]
    assert cov["state"] == "incomplete" and cov["reads"] < cov["rows"] == res["window_addressed"]["tie"]["rows"]
    assert cov["stop"]["after_reads"] == cov["reads"] and cov["count_check_aux"] is False
    assert early["panels"]["state"] == "aggregate" and early["panels"]["last"]["read"] < cov["rows"] - 5
    # read 39 shows frame 38 (C0 §5.1): index and PTS name 39, so it is ADDRESSED as 39, and
    # the witness disagrees. The output never calls that read verified.
    hidden = _damaged(clean, "zero70")
    wa, _ = _compose_on(r60, keep, hidden, 0.1)              # step 1: every read is a sample
    bars = _cv_bars(hidden)
    spts = r60["spts"]
    off = [m["read"] for m in wa["motion"] if m["point"]["state"] == "addressed"
           and spts[bars[m["read"]]] != m["point"]["source_pts"]]
    assert off == [39] and wa["integrity"].startswith("unverified")
    assert wa["motion"][39]["state"] == "span"               # two equal images are not proof of damage


def test_default_off_is_todays_argv_and_result(r60, monkeypatch, tmp_path):
    calls = []
    real_run = subprocess.run
    monkeypatch.setattr(DW, "subprocess", SimpleNamespace(   # this module's spawns only
        run=lambda argv, **k: calls.append(list(argv)) or real_run(argv, **k)))
    for name in ("compose", "read_source_range", "stats_options"):
        monkeypatch.setattr(A, name, lambda *a, **k: pytest.fail("addressing ran with address off"))
    monkeypatch.setattr(P, "ffmpeg_build", lambda *a, **k: pytest.fail("build read with address off"))
    off = DW.analyse_window(r60["prx"], 12.34, 6.0, None, W)
    assert len(calls) == 1 and calls[0][1:-1] == [
        "-y", "-loglevel", "error", "-ss", "12.340", "-i", str(r60["prx"]), "-t", "6.000",
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24"]
    assert calls[0][-1].endswith("window.mp4") and set(off) == LEGACY
    monkeypatch.undo()
    on = DW.analyse_window(r60["prx"], 12.34, 6.0, None, W, address=True, provenance=r60["prov"])
    assert on["window_addressed"]["state"] == "ok"
    assert {k: v for k, v in on.items() if k != "window_addressed"} == off


def test_observed_none_keeps_the_old_results(r60, keep):
    DW.analyse_window(r60["prx"], 12.34, 6.0, None, W)
    window = keep / "window.mp4"
    band = (0.25, 1.0, 0.0, 0.8)
    assert DW.region_motion(window, 0.25, band, W) == DW.region_motion(window, 0.25, band, W, observed={})
    assert DW.ui_panels(window, W, H) == DW.ui_panels(window, W, H, observed={})
