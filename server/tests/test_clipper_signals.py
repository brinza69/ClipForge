"""
Tests for AI Stream Clipper Pass A (services/clipper/signals.py).

Only the pure numeric helpers and the stdlib-wave reader are covered here:
they are where the judgement calls live (what counts as a peak, what counts as
silence, how loudness is normalised) and they run without ffmpeg, opencv, a
GPU or any media on disk. The ffmpeg scene pass and the cv2 motion/face passes
need real video and are exercised by the pipeline's manual end-to-end run.
"""

import wave

import numpy as np
import pytest

from services.clipper import signals


# --------------------------------------------------------------------------
# _normalise
# --------------------------------------------------------------------------

def test_normalise_ignores_a_single_clipped_outlier():
    # 99 ordinary hops plus one 100x spike. Normalising against the max would
    # squash every ordinary hop to 0.01; the 95th percentile must not.
    out = signals._normalise(np.array([0.5] * 99 + [50.0]))
    assert out[0] == 1.0
    assert out[-1] == 1.0  # the outlier is clipped, not used as the reference


def test_normalise_is_proportional_below_the_reference():
    out = signals._normalise(np.linspace(0.0, 1.0, 101))
    assert out[0] == 0.0
    assert out[-1] == 1.0
    assert 0.52 < out[50] < 0.53  # 0.5 / p95(=0.95)
    assert all(0.0 <= v <= 1.0 for v in out)


def test_normalise_handles_empty_and_silent_input():
    assert signals._normalise(np.array([])) == []
    assert signals._normalise(np.zeros(5)) == [0.0] * 5


# --------------------------------------------------------------------------
# _rolling_median
# --------------------------------------------------------------------------

def test_rolling_median_keeps_length_and_pads_edges():
    values = np.array([0.0, 0.0, 9.0, 0.0, 0.0, 0.0, 0.0])
    out = signals._rolling_median(values, 3)
    assert out.size == values.size
    assert out.tolist() == [0.0] * 7  # a lone spike never becomes the median


def test_rolling_median_falls_back_to_global_median_when_window_exceeds_input():
    out = signals._rolling_median(np.array([1.0, 2.0, 3.0]), 99)
    assert out.tolist() == [2.0, 2.0, 2.0]


def test_rolling_median_of_empty_input_is_empty():
    assert signals._rolling_median(np.array([]), 5).size == 0


# --------------------------------------------------------------------------
# _pick_peaks
# --------------------------------------------------------------------------

def test_pick_peaks_finds_an_isolated_spike_at_the_hop_midpoint():
    values = np.full(40, 0.1)
    values[20] = 1.0
    peaks = signals._pick_peaks(values, 0.25)
    assert peaks == [pytest.approx(5.125)]  # (20 + 0.5) * 0.25


def test_pick_peaks_reports_a_plateau_once():
    values = np.array([0.1] * 10 + [1.0] * 3 + [0.1] * 10)
    peaks = signals._pick_peaks(values, 0.25)
    assert peaks == [pytest.approx(2.625)]  # first index of the plateau only


def test_pick_peaks_ignores_a_loud_but_flat_stream():
    # Sustained loudness has no moments in it: everything beats the 80th
    # percentile, nothing beats 1.5x its own neighbourhood.
    assert signals._pick_peaks(np.full(60, 0.8), 0.25) == []


def test_pick_peaks_requires_absolute_loudness_too():
    # A 1.5x bump inside a near-silent passage is not a moment.
    values = np.full(60, 0.9)
    values[:20] = 0.001
    values[10] = 0.01  # 10x its local median, but far below the 80th percentile
    assert 2.625 not in signals._pick_peaks(values, 0.25)


def test_pick_peaks_degenerate_input():
    assert signals._pick_peaks(np.array([]), 0.25) == []
    assert signals._pick_peaks(np.array([1.0, 2.0]), 0.25) == []
    assert signals._pick_peaks(np.full(10, 1.0), 0.0) == []


# --------------------------------------------------------------------------
# _silence_runs
# --------------------------------------------------------------------------

def test_silence_runs_keeps_long_gaps_and_drops_short_ones():
    values = np.array([1.0] * 8 + [0.0] * 4 + [1.0] * 8 + [0.0] * 1 + [1.0] * 8)
    runs = signals._silence_runs(values, 0.25)
    assert runs == [[2.0, 3.0]]  # the single-hop dip (0.25 s < 0.35 s) is not silence


def test_silence_runs_is_relative_to_the_median_not_an_absolute_floor():
    # A quiet recording: "silence" is still 8% of *this* stream's median.
    values = np.array([0.02] * 8 + [0.0005] * 4 + [0.02] * 8)
    assert signals._silence_runs(values, 0.25) == [[2.0, 3.0]]


def test_silence_runs_marks_a_fully_silent_track_as_one_span():
    assert signals._silence_runs(np.zeros(20), 0.25) == [[0.0, 5.0]]


def test_silence_runs_clamps_to_the_reported_duration():
    values = np.array([1.0] * 4 + [0.0] * 8)
    assert signals._silence_runs(values, 0.25, duration=2.4) == [[1.0, 2.4]]


def test_silence_runs_degenerate_input():
    assert signals._silence_runs(np.array([]), 0.25) == []
    assert signals._silence_runs(np.ones(10), 0.0) == []


# --------------------------------------------------------------------------
# _complement / _merge_spans
# --------------------------------------------------------------------------

def test_complement_inverts_spans_within_the_duration():
    assert signals._complement([[2.0, 3.0]], 7.25) == [[0.0, 2.0], [3.0, 7.25]]


def test_complement_of_full_coverage_is_empty():
    assert signals._complement([[0.0, 5.0]], 5.0) == []
    assert signals._complement([], 0.0) == []


def test_complement_of_nothing_is_the_whole_timeline():
    assert signals._complement([], 12.5) == [[0.0, 12.5]]


def test_merge_spans_joins_only_sub_gap_neighbours():
    spans = [[0.0, 1.0], [1.1, 2.0], [4.0, 5.0]]
    assert signals._merge_spans(spans, gap_s=0.25) == [[0.0, 2.0], [4.0, 5.0]]


def test_merge_spans_sorts_and_drops_empty_spans():
    spans = [[4.0, 5.0], [0.0, 1.0], [2.0, 2.0]]
    assert signals._merge_spans(spans, gap_s=0.25) == [[0.0, 1.0], [4.0, 5.0]]


# --------------------------------------------------------------------------
# _face_sample_times
# --------------------------------------------------------------------------

def test_face_sample_times_stay_bounded_on_a_long_vod():
    times = signals._face_sample_times(6 * 3600.0)  # a 6-hour stream
    assert len(times) <= signals.MAX_FACE_SAMPLES
    assert times[0] > 0 and times[-1] < 6 * 3600.0


def test_face_sample_times_use_the_default_hop_on_short_input():
    assert signals._face_sample_times(10.0) == [1.0, 3.0, 5.0, 7.0, 9.0]
    assert signals._face_sample_times(0.0) == []


# --------------------------------------------------------------------------
# audio_timeline — stdlib `wave` only, no ffmpeg
# --------------------------------------------------------------------------

def _write_wav(path, blocks, rate=16000):
    """blocks = [(seconds, amplitude), ...] rendered as a 16 kHz mono sine."""
    parts = []
    for seconds, amplitude in blocks:
        n = int(seconds * rate)
        t = np.arange(n, dtype=np.float64) / rate
        parts.append((np.sin(2 * np.pi * 220.0 * t) * amplitude).astype("<i2"))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(np.concatenate(parts).tobytes())


def test_audio_timeline_reads_a_synthetic_wav(tmp_path):
    path = tmp_path / "speech.wav"
    _write_wav(path, [(2.0, 8000), (1.0, 0), (2.0, 8000)])

    out = signals.audio_timeline(str(path), hop_s=0.25)
    assert out["hop_s"] == 0.25
    assert out["duration"] == pytest.approx(5.0)
    assert len(out["rms"]) == 20
    assert out["silence"] == [[2.0, 3.0]]
    assert out["speech"] == [[0.0, 2.0], [3.0, 5.0]]
    assert all(0.0 <= v <= 1.0 for v in out["rms"])


def test_audio_timeline_on_a_silent_wav(tmp_path):
    path = tmp_path / "silent.wav"
    _write_wav(path, [(1.0, 0)])

    out = signals.audio_timeline(str(path), hop_s=0.25)
    assert out["rms"] == [0.0] * 4
    assert out["silence"] == [[0.0, 1.0]]
    assert out["speech"] == []
    assert out["peaks"] == []


def test_audio_timeline_never_raises_on_bad_input(tmp_path):
    empty = signals.audio_timeline(str(tmp_path / "nope.wav"))
    assert empty == {
        "hop_s": 0.25, "duration": 0.0,
        "rms": [], "peaks": [], "silence": [], "speech": [],
    }

    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a riff header at all")
    assert signals.audio_timeline(str(junk))["rms"] == []

    # A non-positive hop is corrected rather than dividing by zero.
    path = tmp_path / "ok.wav"
    _write_wav(path, [(1.0, 4000)])
    assert signals.audio_timeline(str(path), hop_s=0.0)["hop_s"] == 0.25
