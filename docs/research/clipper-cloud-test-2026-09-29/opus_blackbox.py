"""Describe another clipper's picks on the test VOD with signals we can measure.

Usage:
    python opus_blackbox.py picks.json speech.wav chat.json clipforge.db PROJECT_ID [--lag 10]

picks.json : [{"rank": 1, "start": "13:13" | 793.0, "end": "13:40" | 820.0,
               "score": 92 (optional), "title": "..." (optional)}, ...]
             Times may be seconds, "mm:ss" or "hh:mm:ss". A stitched clip is
             one entry per range with the same rank.

For every pick, and for random windows of the same lengths (the baseline):
  - boundaries: does it start/end within 0.6 s of a transcript segment edge?
  - words per second inside the clip (transcript density)
  - audio excitement (loudness above its +-60 s median, as in chat_lag.py)
  - chat laugh share in [start+lag, end+lag] over the VOD's median
  - overlap with the ClipForge board and the best-overlapping candidate's score
A signal that separates the picks from the baseline is one the other clipper
plausibly follows. With a dozen picks this is a description, not a model of it.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import statistics as st
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_chat as A  # noqa: E402
import chat_lag as L  # noqa: E402

EDGE_TOL_S = 0.6


def secs(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    parts = [float(p) for p in str(v).split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def load_db(db: str, pid: str):
    con = sqlite3.connect(db)
    row = con.execute("SELECT segments FROM transcripts WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
                      (pid,)).fetchone()
    segs = json.loads(row[0]) if row else []
    segs = segs.get("segments", []) if isinstance(segs, dict) else segs
    clips = con.execute("SELECT start_time, end_time, overall_score, is_alternative FROM clips "
                        "WHERE project_id=?", (pid,)).fetchall()
    return segs, clips


def features(a: float, b: float, segs, aud, laugh, lag, clips) -> dict:
    edges_s = [float(s["start"]) for s in segs]
    edges_e = [float(s["end"]) for s in segs]
    words = sum(len((s.get("text") or "").split()) * max(0.0, min(b, s["end"]) - max(a, s["start"]))
                / max(1e-6, s["end"] - s["start"]) for s in segs if s["end"] > a and s["start"] < b)
    i0, i1 = int(a), max(int(a) + 1, int(b))
    c0, c1 = int(a + lag), max(int(a + lag) + 1, int(b + lag))
    board = [(s, e) for s, e, _, alt in clips if not alt]

    def iou(s, e):
        inter = max(0.0, min(b, e) - max(a, s))
        return inter / max(1e-6, (b - a) + (e - s) - inter)
    best = max(clips, key=lambda c: iou(c[0], c[1]), default=None)
    return {
        "starts_on_edge": any(abs(a - x) <= EDGE_TOL_S for x in edges_s),
        "ends_on_edge": any(abs(b - x) <= EDGE_TOL_S for x in edges_e),
        "wps": words / max(1e-6, b - a),
        "excite": float(np.mean(aud[i0:i1])) if i1 <= len(aud) else float("nan"),
        "laugh_ratio": float(np.mean(laugh[c0:c1])) / max(1e-9, float(np.median(laugh))) if c1 <= len(laugh) else float("nan"),
        "board_iou": max((iou(s, e) for s, e in board), default=0.0),
        "cand_iou": iou(best[0], best[1]) if best else 0.0,
        "cand_score": best[2] if best else None,
    }


def summary(rows: list[dict], key: str) -> str:
    vals = [r[key] for r in rows if r[key] is not None and r[key] == r[key]]
    if not vals:
        return "unavailable"
    if isinstance(vals[0], bool):
        return f"{sum(vals)}/{len(vals)}"
    return f"median {st.median(vals):.2f}"


def main(argv: list[str]) -> int:
    lag = 10.0
    if "--lag" in argv:
        i = argv.index("--lag"); lag = float(argv[i + 1]); del argv[i:i + 2]
    picks = json.load(open(argv[1], encoding="utf-8"))
    if not picks:
        print("picks: unavailable (0 entries)")
        return 2
    aud = L.audio_excitement(argv[2])
    laugh, n_msgs = L.chat_share(argv[3], len(aud), ("funny",))
    segs, clips = load_db(argv[4], argv[5])
    print(f"picks {len(picks)}, transcript segments {len(segs)}, chat messages {n_msgs}, "
          f"ClipForge clips {len(clips)} ({sum(1 for c in clips if not c[3])} on the board), lag {lag:.0f} s")
    if not segs or not clips:
        print("transcript or ClipForge clips unavailable")
        return 2
    rows = []
    for p in sorted(picks, key=lambda p: (p.get("rank") or 0, secs(p["start"]))):
        a, b = secs(p["start"]), secs(p["end"])
        f = features(a, b, segs, aud, laugh, lag, clips)
        rows.append(f)
        print(f"  #{p.get('rank', '?'):>2} {a/60:6.2f}-{b/60:6.2f} min ({b-a:4.0f} s) score {p.get('score', '-')!s:>3}  "
              f"edges {'S' if f['starts_on_edge'] else '-'}{'E' if f['ends_on_edge'] else '-'}  "
              f"wps {f['wps']:.2f}  excite {f['excite']:.2f}  laugh x{f['laugh_ratio']:.2f}  "
              f"board IoU {f['board_iou']:.2f}  best cand IoU {f['cand_iou']:.2f} (score {f['cand_score']})")
    rng = random.Random(7)
    dur = len(aud)
    base = []
    for _ in range(40):
        for p in picks:
            L_ = secs(p["end"]) - secs(p["start"])
            s = rng.uniform(A.WINDOW_S, max(A.WINDOW_S, dur - L_ - lag - 1))
            base.append(features(s, s + L_, segs, aud, laugh, lag, clips))
    print("\nsignal            picks            random windows (same lengths, 40x)")
    for k in ("starts_on_edge", "ends_on_edge", "wps", "excite", "laugh_ratio", "board_iou", "cand_iou"):
        print(f"  {k:15s} {summary(rows, k):16s} {summary(base, k)}")
    scored = [(p.get("score"), r) for p, r in zip(sorted(picks, key=lambda p: (p.get("rank") or 0, secs(p["start"]))), rows)
              if isinstance(p.get("score"), (int, float))]
    if len(scored) >= 5:
        sc = np.array([s for s, _ in scored], dtype=float)
        print("\nSpearman with the other clipper's score (n=%d):" % len(scored))
        for k in ("wps", "excite", "laugh_ratio"):
            v = np.array([r[k] for _, r in scored], dtype=float)
            ok = ~np.isnan(v)
            if ok.sum() >= 5:
                ra = np.argsort(np.argsort(sc[ok])); rb = np.argsort(np.argsort(v[ok]))
                print(f"  {k:12s} rho {np.corrcoef(ra, rb)[0, 1]:+.2f}")
    else:
        print("\nscores: unavailable for a correlation (fewer than 5 scored picks)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
