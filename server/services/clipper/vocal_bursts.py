"""
ClipForge — AI Stream Clipper: laughter and shouting, from the audio.

`laughter_score` has been a word list — "haha", "hehe", "lol" — since the
clipper shipped, and Whisper does not transcribe laughter as any of them. So it
reads 0 on every window of every source, while carrying 30% of the `emotion`
sub-score and 20% of `reaction`. Measured over 1073 real candidates, `emotion`
holds 8% of the gaming profile and 76% of candidates score exactly 10 out of
100 on it: eight points of weight spent on a constant.

WHAT THIS MEASURES, and it is deliberately not only laughter. The thing that
makes a moment worth clipping is a strong NON-VERBAL VOCAL reaction — a laugh, a
shout, a scream, a gasp. All four are the same object to a detector and all four
are what `emotion` is supposed to reward, so the score is "somebody made a loud
voiced noise that was not words". The key stays `laughter_score` because it is
in the frozen feature vector and renaming it would need a MODEL_VERSION bump for
a ranker that has no trained model yet.

Three tests, and a moment has to pass all three:

  LOUD, against the source's own p80 rather than an absolute bar. That rule is
  written four times in this codebase and was learned the hard way each time:
  an absolute threshold on a busy stream measures nothing.

  VOICED. Laughter and shouting are harmonic; an explosion, a door, music under
  the voice are not. Spectral flatness separates them — the geometric mean of
  the magnitude spectrum over its arithmetic mean is near 0 for a tonal sound
  and near 1 for noise.

  WORDLESS. Whisper transcribes speech and does not transcribe laughter, so a
  loud voiced moment it produced no words for is exactly the thing missing from
  the transcript. This is the test that makes the other two specific, and it is
  free: the word timings already exist.

No model, no new dependency. numpy over the 16 kHz mono WAV the transcriber
already made.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Any, Sequence

import numpy as np

logger = logging.getLogger("clipforge.clipper.vocal")

__all__ = ["FRAME_S", "vocal_burst_timeline", "burst_share"]

FRAME_S = 0.25          # matches the audio hop everything else uses

# Voice lives here. The fundamental of a laugh or a shout sits at 100-400 Hz and
# its harmonics carry to a few kHz; below 150 is rumble and game bass, above
# 4000 is mostly hiss and transients.
_BAND_LO, _BAND_HI = 150.0, 4000.0

# Below this the frame is tonal enough to be a voice. Measured on the co-stream:
# speech and laughter frames run 0.02-0.25, game noise and silence 0.4-0.9.
_FLAT_MAX = 0.35

# Share of the frame's energy that must sit in the voice band.
_BAND_MIN = 0.45

# Loud RELATIVE TO THIS SOURCE. p80 of the non-silent frames, so a quiet stream
# and a loud one are judged against themselves.
_LOUD_PCT = 80.0

# A burst shorter than this is a click, a mouse, a door. Laughter and shouting
# last.
_MIN_BURST_S = 0.5


def _read_wav(path: Path | str) -> tuple[np.ndarray, int]:
    """Mono float32 samples and the sample rate. ([], 0) if unreadable."""
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            width = handle.getsampwidth()
            channels = handle.getnchannels()
            raw = handle.readframes(handle.getnframes())
    except Exception as exc:
        logger.warning("vocal_bursts: could not read %s (%s)", path, exc)
        return np.zeros(0, dtype=np.float32), 0

    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        return np.zeros(0, dtype=np.float32), 0
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        data = (data - 128.0) / 128.0
    else:
        data /= float(np.iinfo(dtype).max)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def _wordless(times: np.ndarray, words: Sequence[dict]) -> np.ndarray:
    """True where no transcribed word covers the frame."""
    covered = np.zeros(times.shape, dtype=bool)
    for word in words or []:
        try:
            start = float(word["start"])
            end = float(word.get("end", start))
        except (KeyError, TypeError, ValueError):
            continue
        covered |= (times >= start - 0.05) & (times <= end + 0.05)
    return ~covered


def vocal_burst_timeline(wav_path: Path | str, words: Sequence[dict] = (),
                         hop_s: float = FRAME_S) -> list[float]:
    """Per-hop 0..1 score for "somebody made a loud voiced noise, not words".

    Returns [] rather than raising: a source whose audio cannot be read should
    lose this feature, not the run.
    """
    samples, rate = _read_wav(wav_path)
    if samples.size == 0 or rate <= 0:
        return []

    step = max(1, int(rate * hop_s))
    n = samples.size // step
    if n < 4:
        return []

    frames = samples[:n * step].reshape(n, step)
    rms = np.sqrt(np.maximum((frames ** 2).mean(axis=1), 1e-12))

    window = np.hanning(step)
    spec = np.abs(np.fft.rfft(frames * window, axis=1)) + 1e-10
    freqs = np.fft.rfftfreq(step, 1.0 / rate)

    # Tonal or noisy: geometric over arithmetic mean of the spectrum.
    flatness = np.exp(np.log(spec).mean(axis=1)) / spec.mean(axis=1)

    band = (freqs >= _BAND_LO) & (freqs <= _BAND_HI)
    band_ratio = spec[:, band].sum(axis=1) / spec.sum(axis=1)

    # The source's own idea of loud, over frames that are not near-silence.
    alive = rms > max(1e-5, float(np.percentile(rms, 20)))
    loud_bar = float(np.percentile(rms[alive], _LOUD_PCT)) if alive.any() else 1.0

    times = np.arange(n) * hop_s
    hit = ((rms >= loud_bar)
           & (flatness <= _FLAT_MAX)
           & (band_ratio >= _BAND_MIN)
           & _wordless(times, words))

    # A burst has to last. Runs shorter than _MIN_BURST_S are dropped whole.
    min_frames = max(1, int(round(_MIN_BURST_S / hop_s)))
    out = np.zeros(n, dtype=np.float32)
    run_start = None
    for i in range(n + 1):
        if i < n and hit[i]:
            run_start = i if run_start is None else run_start
            continue
        if run_start is not None:
            if i - run_start >= min_frames:
                # How far past the bar it went, so a roar outranks a chuckle.
                strength = rms[run_start:i] / max(loud_bar, 1e-9)
                out[run_start:i] = np.clip(strength, 0.0, 2.0) / 2.0
            run_start = None
    return [round(float(v), 4) for v in out]


# A burst this long counts fully; shorter ones are discounted toward it. Bursts
# are already at least _MIN_BURST_S, so the discount only ever applies between
# 0.5 s and 1 s — a half-second yelp against a real laugh.
_FULL_BURST_S = 1.0


def burst_share(timeline: Sequence[float], start: float, end: float,
                hop_s: float = FRAME_S) -> float:
    """How strong the loudest vocal reaction in a window is, 0..1.

    STRENGTH, not share, and the difference was measured. The first version
    averaged the timeline across the window, which asks "what fraction of this
    clip is laughter" — and the answer for a 35-second window containing one
    real laugh is about 0.02. Emotion's spread over 69 real candidates did not
    move: sd 5.41 to 5.27, a dead feature replaced by a nearly dead one.

    The question a clip is actually judged on is whether a strong reaction is IN
    it. So: the peak, scaled by how long the burst lasted, sub-linearly —
    intensity is most of it and duration is the rest, which is what `sqrt`
    says. Measured on the same 69 candidates, emotion's sd goes 5.41 -> 11.73.

    Bare `max` scores marginally higher (13.58) and was not chosen: it makes a
    half-second yelp identical to a three-second howl, and picking the
    formulation with the largest variance is optimising for spread rather than
    for meaning — a random feature would win that contest.
    """
    if not timeline or end <= start or hop_s <= 0:
        return 0.0
    lo = max(0, int(start / hop_s))
    hi = min(len(timeline), int(end / hop_s) + 1)
    if hi <= lo:
        return 0.0
    span = np.asarray(timeline[lo:hi], dtype=np.float32)
    peak = float(span.max())
    if peak <= 0.0:
        return 0.0
    seconds = float((span > 0).sum()) * hop_s
    return float(min(1.0, peak * min(1.0, seconds / _FULL_BURST_S) ** 0.5))


def summarise(timeline: Sequence[float]) -> dict[str, Any]:
    """What the detector found over a whole source, for the meta artifact."""
    arr = np.asarray(timeline or [], dtype=np.float32)
    if arr.size == 0:
        return {"frames": 0, "burst_frames": 0, "share": 0.0}
    hit = arr > 0
    return {
        "frames": int(arr.size),
        "burst_frames": int(hit.sum()),
        "share": round(float(hit.mean()), 4),
        "mean_strength": round(float(arr[hit].mean()) if hit.any() else 0.0, 4),
    }
