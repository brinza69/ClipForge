"""Reproduce the synthetic evidence behind proxy_clock.VALIDATED_CLOCKS with the SHIPPING code.

    cd server
    .venv/Scripts/python.exe tests/data/proxy_clock/reproduce_synth.py [--seconds 600] [--out F] [rate ...]

Rates: 24 23.976 60 30 25 59.94 (default: all). Per rate:
  1. a constructed barcode source: frame i draws i in 16 vertical bars, pts exactly i*step at
     the rate's mp4 time base, lossless x264, no B-frames; its packets must be that grid;
  2. the proxy is built with proxy_provenance.proxy_recipe() at width 480 / 10 fps: the tokens
     ingest.build_proxy runs (pinned by test_build_proxy_runs_the_same_command_as_before), run by
     the executable identified FIRST with proxy_provenance.ffmpeg_build();
  3. every proxy frame is decoded and read back as the index it shows (an unclean read is
     None, never a guess) and compared with proxy_clock.simulate() and address_slot().
The clock given to address_slot() is clock_validation(recipe, build) of THIS run: on another
ffmpeg build every address is refused `clock_unvalidated`, only simulate() speaks, and the exit
code is 1. Media live in a temp dir, deleted at the end. Exit 0 only when every rate was read,
matched on every slot and was refused only for the start transient and the end of stream.

It differs from the frozen probe (synth_probe.py.txt, next to this file) in the source only:
that one drew the barcode with lavfi geq at 960x540 (scaled down to 480 by the recipe); this
one pipes raw frames at 480x272 (--size to change it). The recipe, its params and the rule
under test are the same.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from collections import Counter
from fractions import Fraction
from pathlib import Path

import numpy as np

SERVER = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVER))
from services.clipper import proxy_clock as C, proxy_provenance as P  # noqa: E402
from services.clipper.ffmpeg_tools import ffmpeg_bin, ffprobe_bin, probe  # noqa: E402

RATES = {"24": (Fraction(24), 12288), "23.976": (Fraction(24000, 1001), 24000),
         "60": (Fraction(60), 15360), "30": (Fraction(30), 15360), "25": (Fraction(25), 12800),
         "59.94": (Fraction(60000, 1001), 60000)}
BITS = 16
OK_REFUSALS = {"start_transient", "end_of_stream"}


def _source(exe: str, dest: Path, n: int, w: int, h: int, fps: Fraction, tbd: int, step: int) -> None:
    cols = np.arange(w) // (w // BITS)
    with open(dest.with_suffix(".log"), "wb") as err:     # stderr to a file: never an undrained pipe
        proc = subprocess.Popen(
            [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
             "-framerate", f"{fps.numerator}/{fps.denominator}", "-i", "-",
             "-vf", f"settb=1/{tbd},setpts=N*{step}", "-fps_mode", "passthrough",
             "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-bf", "0", "-pix_fmt", "yuv420p",
             "-enc_time_base:v", f"1/{tbd}", "-video_track_timescale", str(tbd), str(dest)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        for i in range(n):
            bits = ((i >> np.minimum(cols, BITS - 1)) & 1) * 255
            proc.stdin.write(np.broadcast_to(bits.astype(np.uint8), (h, w)).tobytes())
        proc.stdin.close()
        if proc.wait():
            raise RuntimeError(dest.with_suffix(".log").read_text(errors="replace")[-500:])


def _packets(media: Path) -> tuple[list[int], list[int]]:
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "packet=pts,duration", "-of", "csv=p=0", str(media)],
                       capture_output=True, text=True, check=True)
    rows = sorted(tuple(int(x) for x in ln.strip().rstrip(",").split(",")[:2])
                  for ln in r.stdout.splitlines() if ln.strip())
    return [p for p, _ in rows], [d for _, d in rows]


def _shown(exe: str, proxy: Path, w: int, h: int, log: Path) -> list[int | None]:
    """Every proxy frame, presentation order, read as the index it shows; streamed, so a
    10-minute proxy never sits in memory and stdout is always drained."""
    out: list[int | None] = []
    bw = w // BITS
    with open(log, "wb") as err:
        proc = subprocess.Popen([exe, "-v", "error", "-i", str(proxy), "-fps_mode", "passthrough",
                                 "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                stdout=subprocess.PIPE, stderr=err)
        while len(buf := proc.stdout.read(w * h)) == w * h:
            f = np.frombuffer(buf, np.uint8).reshape(h, w)
            means = [f[h // 4:3 * h // 4, b * bw + bw // 4:b * bw + 3 * bw // 4].mean() for b in range(BITS)]
            out.append(None if any(64 < v < 192 for v in means)
                       else sum(1 << b for b, v in enumerate(means) if v >= 192))
        proc.wait()
    return out


def _meta(media: Path) -> dict:
    d = probe(str(media))
    return {"stream": next(s for s in d["streams"] if s.get("codec_type") == "video"),
            "format": d.get("format") or {}}


def run_rate(name: str, secs: int, w: int, h: int, exe: str, build: dict, work: Path) -> dict:
    fps, tbd = RATES[name]
    step = int(Fraction(tbd) / fps)
    n = math.ceil(fps * secs)                               # the frozen probe's count (n_exp)
    src, prx = work / f"src_{name}.mp4", work / f"proxy_{name}.mp4"
    _source(exe, src, n, w, h, fps, tbd, step)
    pts, durs = _packets(src)
    res: dict = {"rate": name, "fps": str(fps), "seconds": secs, "source_frames": len(pts),
                 "source_off_grid": sum(1 for i, (p, d) in enumerate(zip(pts, durs))
                                        if p != i * step or d != step)}
    argv, recipe = P.proxy_recipe(str(src), str(prx), min(480, w), min(10.0, float(fps)))
    subprocess.run([exe, *argv], check=True)
    ppts, _ = _packets(prx)
    pmeta, smeta = _meta(prx), _meta(src)
    per_slot = Fraction(C._tb(pmeta["stream"]["time_base"])[1], C._tb(pmeta["stream"]["time_base"])[0]) / 10
    shown = _shown(exe, prx, int(pmeta["stream"]["width"]), int(pmeta["stream"]["height"]),
                   work / f"read_{name}.log")
    sim = C.simulate(pts, durs, (1, tbd))
    clock = C.clock_validation(recipe, build)
    refused: Counter = Counter()
    addr_mism = addressed = 0
    for s in range(len(shown)):
        a = C.address_slot(ppts[s] if s < len(ppts) else None, pmeta, smeta,
                           C.window(pts, durs, (1, tbd), s), clock=clock,
                           source_nb_frames=len(pts), proxy_nb_frames=len(ppts))
        if a["state"] == "addressed":
            addressed += 1
            addr_mism += a["source_index"] != shown[s]
        else:
            refused[a["reason"].split(" ")[0]] += 1
    res.update(clock=clock["state"], recipe_params=recipe["params"], domain=C.domain(pmeta, smeta)["state"],
               slots=len(shown), proxy_packets=len(ppts), simulated_slots=len(sim),
               proxy_pts_off_grid=sum(1 for k, p in enumerate(ppts) if p != k * per_slot),
               unreadable=sum(1 for x in shown if x is None),
               sim_mismatches=sum(1 for a, b in zip(shown, sim) if a != b) + abs(len(shown) - len(sim)),
               addressed=addressed, addr_mismatches=addr_mism, refused_by_reason=dict(refused))
    return res


def failed(r: dict) -> bool:
    return bool(r["source_off_grid"] or r["clock"] != "validated" or r["domain"] != "ok"
                or r["proxy_pts_off_grid"] or r["unreadable"] or r["sim_mismatches"]
                or r["addr_mismatches"] or set(r["refused_by_reason"]) - OK_REFUSALS
                or r["slots"] != r["proxy_packets"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rates", nargs="*", default=list(RATES))
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--size", default="480x272")
    ap.add_argument("--out")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.split("x"))
    exe = ffmpeg_bin()
    build = P.ffmpeg_build(exe)                               # identified before anything runs
    print(f"ffmpeg {build['exe']} sha256 {build['exe_sha256']}")
    print(f"rates requested {len(a.rates)}: {a.rates}; {a.seconds} s each at {w}x{h}")
    results = []
    with tempfile.TemporaryDirectory(prefix="proxy_clock_synth_") as tmp:
        for name in a.rates:
            try:
                r = run_rate(name, a.seconds, w, h, exe, build, Path(tmp))
            except Exception as exc:                      # a rate that could not be read is its own row
                r = {"rate": name, "state": "not_read", "why": str(exc)[-300:]}
            else:
                r["state"] = "FAIL" if failed(r) else "ok"
            results.append(r)
            print(json.dumps(r))
    bad = [r["rate"] for r in results if r["state"] != "ok"]
    print(f"rates run {len(results)} of {len(a.rates)}; not ok {len(bad)} {bad}")
    if a.out:
        Path(a.out).write_text(json.dumps({"ffmpeg_build": build, "results": results}, indent=1))
    return 1 if bad or len(results) != len(a.rates) or not results else 0


if __name__ == "__main__":
    sys.exit(main())
