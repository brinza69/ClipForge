"""Laughter and shouting, from the audio instead of from a word list.

`laughter_score` was word hits — "haha", "hehe", "lol" — and Whisper does not
transcribe laughter as any of them, so it read 0 on every window of every
source while carrying 30% of `emotion` and 20% of `reaction`. Measured over
1073 real candidates, 76% scored exactly 10/100 on emotion: eight points of
profile weight spent on a constant.

The detector was verified the only way it can be — a person listened to the six
strongest hits on a real source and confirmed five were laughter or shouting,
with the sixth a shout mixed with game audio.
"""

from __future__ import annotations

import math
import wave

import numpy as np
import pytest

from services.clipper import vocal_bursts as vb


def _wav(path, seconds=20.0, rate=16000, build=None):
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    data = build(t) if build else np.zeros_like(t)
    pcm = np.clip(data, -1, 1) * 32767
    with wave.open(str(path), "wb") as h:
        h.setnchannels(1)
        h.setsampwidth(2)
        h.setframerate(rate)
        h.writeframes(pcm.astype(np.int16).tobytes())
    return path


def test_a_harmonic_burst_in_quiet_audio_is_found(tmp_path):
    """A voiced shout: a fundamental with harmonics, loud, over near-silence.

    The burst is 10% of the file on purpose. The loud bar is the source's OWN
    p80, so a synthetic where the burst is a THIRD of the audio puts that
    percentile inside the burst and only its very peak clears it — the relative
    threshold working correctly on an unrealistic signal. Real sources run
    under 1%.
    """
    def build(t):
        quiet = 0.02 * np.random.default_rng(0).standard_normal(t.size)
        voice = sum(np.sin(2 * np.pi * f * t) / (i + 1)
                    for i, f in enumerate((220, 440, 660, 880)))
        burst = ((t >= 2.0) & (t < 4.0)).astype(float)
        return quiet + 0.6 * voice * burst
    path = _wav(tmp_path / "a.wav", build=build)

    timeline = vb.vocal_burst_timeline(path)
    assert timeline, "nothing was measured at all"
    hot = [i for i, v in enumerate(timeline) if v > 0]
    assert hot, "the burst was missed"
    seconds = [i * vb.FRAME_S for i in hot]
    assert 1.5 <= min(seconds) <= 2.5, seconds[:5]
    assert 3.5 <= max(seconds) <= 4.5, seconds[-5:]


def test_broadband_noise_at_the_same_loudness_is_not(tmp_path):
    """An explosion is as loud and is not a voice. Without this the feature
    would reward game audio, which is worse than reading zero."""
    def build(t):
        quiet = 0.02 * np.random.default_rng(1).standard_normal(t.size)
        noise = np.random.default_rng(2).standard_normal(t.size)
        burst = ((t >= 2.0) & (t < 4.0)).astype(float)
        return quiet + 0.6 * noise * burst
    path = _wav(tmp_path / "b.wav", build=build)

    assert not any(v > 0 for v in vb.vocal_burst_timeline(path))


def test_a_burst_covered_by_transcribed_words_is_ignored(tmp_path):
    """The test that makes the other two specific. Whisper transcribes speech
    and not laughter, so a loud voiced moment it produced words for is speech."""
    def build(t):
        voice = sum(np.sin(2 * np.pi * f * t) / (i + 1)
                    for i, f in enumerate((220, 440, 660, 880)))
        return 0.02 * np.random.default_rng(3).standard_normal(t.size) + \
            0.6 * voice * ((t >= 2.0) & (t < 4.0))
    path = _wav(tmp_path / "c.wav", build=build)

    words = [{"word": "hello", "start": 1.9, "end": 4.1}]
    assert not any(v > 0 for v in vb.vocal_burst_timeline(path, words))


def test_a_click_is_too_short_to_count(tmp_path):
    def build(t):
        voice = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 440 * t)
        return 0.02 * np.random.default_rng(4).standard_normal(t.size) + \
            0.8 * voice * ((t >= 2.0) & (t < 2.2))
    path = _wav(tmp_path / "d.wav", build=build)

    assert not any(v > 0 for v in vb.vocal_burst_timeline(path))


def test_loudness_is_judged_against_the_source_not_a_constant():
    """Four times now this codebase has learned that an absolute threshold on a
    busy source measures nothing. The bar is the source's own p80."""
    import inspect

    src = inspect.getsource(vb.vocal_burst_timeline)
    assert "percentile" in src and "_LOUD_PCT" in src


# ── how a window is scored ───────────────────────────────────────────────────


def test_strength_not_share():
    """The first version averaged across the window, which asks what fraction
    of a clip is laughter — about 0.02 for a 35 s clip with one real laugh in
    it. Emotion's spread did not move (sd 5.41 -> 5.27). Peak-based: 11.73."""
    hop = vb.FRAME_S
    timeline = [0.0] * 140
    timeline[40:48] = [0.9] * 8              # two seconds of laughter at 10 s
    got = vb.burst_share(timeline, 0.0, 140 * hop, hop)
    assert got > 0.8, f"one real laugh in a 35 s window scored {got}"


def test_a_longer_burst_outranks_a_briefer_one_of_equal_peak():
    hop = vb.FRAME_S
    brief = [0.0] * 100
    brief[40:42] = [1.0] * 2                 # 0.5 s
    long = [0.0] * 100
    long[40:48] = [1.0] * 8                  # 2 s
    assert vb.burst_share(long, 0, 25, hop) > vb.burst_share(brief, 0, 25, hop)


def test_duration_matters_sub_linearly():
    """Intensity is most of it. Four times the duration must not be four times
    the score, or a long chuckle beats a short scream."""
    hop = vb.FRAME_S
    short = [0.0] * 100
    short[40:42] = [1.0] * 2
    longer = [0.0] * 100
    longer[40:48] = [1.0] * 8
    ratio = vb.burst_share(longer, 0, 25, hop) / vb.burst_share(short, 0, 25, hop)
    assert 1.0 < ratio < 4.0, ratio
    assert ratio == pytest.approx(math.sqrt(4.0), rel=0.35)


def test_a_window_with_no_burst_scores_zero():
    assert vb.burst_share([0.0] * 50, 0, 12, vb.FRAME_S) == 0.0
    assert vb.burst_share([], 0, 12, vb.FRAME_S) == 0.0


def test_unreadable_audio_loses_the_feature_not_the_run(tmp_path):
    broken = tmp_path / "x.wav"
    broken.write_bytes(b"not a wav")
    assert vb.vocal_burst_timeline(broken) == []
