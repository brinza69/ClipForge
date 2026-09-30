"""Estimate one stream's chat reaction lag from the audio, instead of assuming it.

Usage:
    python chat_lag.py speech.wav chat.json

speech.wav : the clipper's extracted audio (data/clipper/<project>/audio/speech.wav),
             16-bit PCM mono
chat.json  : the output of fetch_chat.py

Per second: audio excitement = loudness above its own +-60 s median; chat = the
share of messages carrying a laugh or hype emote (analyze_chat.EMOTES), both
smoothed over 5 s. The lag is where their correlation peaks between -30 and
+90 s. The null is the same statistic with the chat series circularly shifted
by a random 300 s or more (200 draws): a peak that does not clear the null's
95th percentile is reported as unavailable (exit 2), never as a lag.
Needs numpy (the server venv has it).
"""
from __future__ import annotations

import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_chat as A  # noqa: E402

EDGE_S = 120
LAGS = range(-30, 91)


def smooth(v: np.ndarray, k: int = 5) -> np.ndarray:
    return np.convolve(v, np.ones(k) / k, mode="same")


def zscore(v: np.ndarray) -> np.ndarray:
    v = v - v.mean()
    s = v.std()
    return v / s if s else v


def audio_excitement(path: str) -> np.ndarray:
    with wave.open(path) as w:
        sr, n = w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    secs = n // sr
    rms = np.sqrt(np.mean(x[: secs * sr].reshape(secs, sr) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    med = np.array([np.median(db[max(0, i - 60): i + 61]) for i in range(secs)])
    return np.clip(db - med, 0, None)


def chat_share(path: str, secs: int, cats: tuple[str, ...]) -> tuple[np.ndarray, int]:
    msgs = [m for m in A.load_chat(path) if m["type"] == "text_message"]
    total, hits = np.zeros(secs), np.zeros(secs)
    words = set().union(*(A.EMOTES[c] for c in cats))
    for m in msgs:
        t = int(m["t"])
        if 0 <= t < secs:
            total[t] += 1
            if any(w in words for w in A.tokens(m["text"])):
                hits[t] += 1
    return smooth(hits) / np.maximum(smooth(total), 1e-9), len(msgs)


def curve(aud: np.ndarray, share: np.ndarray, lo: int, hi: int) -> dict[int, float]:
    a = zscore(smooth(aud)[lo:hi])
    out = {}
    for lag in LAGS:
        c = share[lo + lag: hi + lag]
        if len(c) == len(a):
            out[lag] = float(np.mean(a * zscore(c)))
    return out


def null(aud: np.ndarray, share: np.ndarray, lo: int, hi: int) -> np.ndarray:
    rng = np.random.default_rng(7)
    a = zscore(smooth(aud)[lo:hi])
    return np.array([float(np.mean(a * zscore(np.roll(share, int(rng.integers(300, len(share) - 300)))[lo:hi])))
                     for _ in range(200)])


def main(argv: list[str]) -> int:
    aud = audio_excitement(argv[1])
    secs = len(aud)
    lo, hi = EDGE_S, secs - EDGE_S
    if hi - lo < 600:
        print(f"audio: {secs} s — unavailable (need at least {600 + 2 * EDGE_S} s)")
        return 2
    rc = 0
    for cats in (("funny", "hype"), ("funny",), ("hype",)):
        share, n_msgs = chat_share(argv[2], secs, cats)
        if not n_msgs:
            print("chat: unavailable (0 text messages)")
            return 2
        cur, nul = curve(aud, share, lo, hi), null(aud, share, lo, hi)
        best = max(cur, key=cur.get)
        p95 = float(np.percentile(nul, 95))
        print(f"{'+'.join(cats):10s} seconds {hi - lo}, messages {n_msgs}: best lag {best:+d} s, "
              f"corr {cur[best]:.3f}; null mean {nul.mean():.3f}, p95 {p95:.3f}, max {nul.max():.3f}; "
              f"at 0/10/25/35 s: {', '.join(f'{cur[x]:.3f}' for x in (0, 10, 25, 35))}")
        if cats == ("funny", "hype") and cur[best] <= p95:
            print("  -> lag unavailable: the peak does not clear the null's 95th percentile")
            rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
