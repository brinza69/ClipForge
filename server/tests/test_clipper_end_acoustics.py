"""EN1: `end_acoustics` on its own — the reader, the onset veto, the guards.

The E0 cases through `refine_boundaries` are in test_clipper_end_cases.py.
These feed the evidence directly: spans and energy are E0's measurements
(tests/data/endings/, see MANIFEST.txt), and the reader is exercised on tiny
WAVs written here, with the VAD stubbed so no model is needed.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from services.clipper import end_acoustics as ea

DATA = Path(__file__).parent / "data" / "endings"


def _win(name: str) -> dict:
    fx = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    return {"t0": fx["energy"]["t0"], "t1": fx["vad"]["t1"], "source_end": 1e9,
            "spans": fx["vad"]["spans"], "db": fx["energy"]["db"]}


class Served:
    def __init__(self, win: dict):
        self.win = win

    def window(self, t0: float, t1: float) -> dict:
        return dict(self.win, t1=min(t1, self.win["t1"]))


def _w(word: str, t0: float, t1: float) -> dict:
    return {"word": word, "start": t0, "end": t1}


# ── the onset veto on measured energy ─────────────────────────────────────

def test_the_onset_e0_heard_at_02dea_reads_as_an_onset():
    """The voiced onset of "I'm" rises -46 -> -22 dB right at the cut."""
    hit = ea.onset_in(_win("02dea6f0a9e9"), 3543.81, 3543.91)
    assert hit is not None and hit["rise_db"] >= ea.ONSET_RISE_DB


def test_the_decaying_release_of_happening_is_not_an_onset():
    assert ea.onset_in(_win("1204e0fdfba2"), 11501.9, 11502.224) is None


def test_an_onset_that_starts_inside_the_interval_counts_even_if_it_holds_past_it():
    """The hold may run past the new end: that is exactly a cut onset."""
    assert ea.onset_in(_win("1204e0fdfba2_spoken_so"), 11501.9, 11502.01) is not None


# ── the landing ───────────────────────────────────────────────────────────

def test_the_target_is_the_acoustic_end_plus_the_margin_and_inside_the_pause():
    win = _win("dea939f254a1")
    land = ea._landing(win, _w("Minecraft.", 13082.72, 13083.16), after=13083.56,
                       limit=1e9, words=[])
    assert land["blocked"] is None
    assert land["target"] == pytest.approx(13083.544 + ea.MARGIN_S, abs=1e-6)
    assert land["target"] < land["vad"]["next_onset"]


def test_a_pause_shorter_than_p_min_is_refused_not_squeezed():
    win = {"t0": 0.0, "t1": 5.0, "source_end": 5.0, "db": [-60.0] * 500,
           "spans": [[0.5, 1.2], [1.2 + ea.P_MIN_S - 0.01, 2.0]]}
    land = ea._landing(win, _w("word.", 0.9, 1.1), after=1.1, limit=9.0, words=[])
    assert land["blocked"] == "pause_too_short"


def test_a_pause_of_exactly_p_min_is_enough():
    win = {"t0": 0.0, "t1": 5.0, "source_end": 5.0, "db": [-60.0] * 500,
           "spans": [[0.5, 1.2], [1.2 + ea.P_MIN_S, 2.0]]}
    land = ea._landing(win, _w("word.", 0.9, 1.1), after=1.1, limit=9.0, words=[])
    assert land["blocked"] is None
    assert land["target"] < 1.2 + ea.P_MIN_S


def test_an_unread_pause_at_the_window_edge_is_not_a_pause():
    """The window ends 0.2 s after the speech and the file goes on: unread."""
    win = {"t0": 0.0, "t1": 1.4, "source_end": 60.0, "db": [-60.0] * 140,
           "spans": [[0.5, 1.2]]}
    land = ea._landing(win, _w("word.", 0.9, 1.1), after=1.1, limit=9.0, words=[])
    assert land["blocked"] == "window_too_short"


def test_the_end_of_the_source_closes_the_pause():
    win = {"t0": 0.0, "t1": 1.7, "source_end": 1.7, "db": [-60.0] * 170,
           "spans": [[0.5, 1.2]]}
    land = ea._landing(win, _w("word.", 0.9, 1.1), after=1.1, limit=9.0, words=[])
    assert land["blocked"] is None and land["target"] == pytest.approx(1.3)


def test_the_limit_is_never_crossed():
    win = _win("dea939f254a1")
    land = ea._landing(win, _w("Minecraft.", 13082.72, 13083.16), after=13083.16,
                       limit=13083.6, words=[])
    assert land["blocked"] == "limit"


def test_a_tail_longer_than_tail_max_is_speech_not_a_release():
    win = {"t0": 0.0, "t1": 5.0, "source_end": 5.0, "db": [-60.0] * 500,
           "spans": [[0.5, 1.1 + ea.TAIL_MAX_S + 0.01]]}
    land = ea._landing(win, _w("word", 0.9, 1.1), after=1.1, limit=9.0, words=[])
    assert land["blocked"] == "speech_continues"


# ── settle_end's own decisions ────────────────────────────────────────────

def test_an_unpunctuated_end_with_no_pause_is_a_conflict_not_a_search():
    """Punctuation proposes the next complete ending; with none, nothing moves."""
    win = {"t0": 0.0, "t1": 9.0, "source_end": 9.0, "db": [-60.0] * 900,
           "spans": [[0.5, 3.0], [3.8, 4.5]]}
    words = [_w("so", 0.6, 1.0), _w("then", 1.1, 1.5), _w("we", 1.6, 2.9),
             _w("go.", 3.9, 4.4)]
    end, ev = ea.settle_end(0.0, 1.5, words, audio=Served(win), limit=8.0, lo=1.0)
    assert end == 1.5
    assert (ev["status"], ev["decision"]) == ("conflict", "speech_continues")


def test_a_sentence_end_with_a_short_pause_also_searches_forward():
    win = {"t0": 0.0, "t1": 9.0, "source_end": 9.0, "db": [-60.0] * 900,
           "spans": [[0.5, 1.6], [1.8, 3.0], [3.8, 4.5]]}
    words = [_w("stop.", 1.1, 1.5), _w("then", 1.8, 2.2), _w("done.", 2.5, 2.9),
             _w("next", 3.9, 4.4)]
    end, ev = ea.settle_end(0.0, 1.5, words, audio=Served(win), limit=8.0, lo=1.0)
    assert end == pytest.approx(3.1)
    assert ev["decision"] == "next_complete_ending"


def test_the_next_complete_ending_is_sought_no_further_than_the_reach():
    """A qualifying ending 5 s out is a content change, not an ending repair:
    beyond NEXT_ENDING_REACH_S (3.5 s) the end stays and the conflict says so."""
    win = {"t0": 0.0, "t1": 20.0, "source_end": 20.0, "db": [-60.0] * 2000,
           "spans": [[0.5, 6.6], [7.5, 8.0]]}
    words = [_w("stop.", 1.1, 1.5), _w("then", 1.8, 2.2), _w("done.", 6.1, 6.5)]
    end, ev = ea.settle_end(0.0, 1.5, words, audio=Served(win), limit=19.0, lo=1.0)
    assert end == 1.5
    assert ev["decision"] == "no_complete_ending_with_pause"
    near = dict(win, spans=[[0.5, 4.6], [5.5, 6.0]])
    words = [_w("stop.", 1.1, 1.5), _w("then", 1.8, 2.2), _w("done.", 4.1, 4.5)]
    end, ev = ea.settle_end(0.0, 1.5, words, audio=Served(near), limit=19.0, lo=1.0)
    assert end == pytest.approx(4.7), "3.2 s out: inside the reach"


def test_an_end_already_past_the_margin_is_never_pulled_back():
    win = _win("3e42c5a399c2")
    words = [_w("worked.", 9439.33, 9439.71)]
    end, ev = ea.settle_end(9404.87, 9440.3, words, audio=Served(win), limit=1e9, lo=15.0)
    assert end == 9440.3 and ev["decision"] == "already_clear"


# ── the reader ────────────────────────────────────────────────────────────

def _wav(path: Path, seconds: float, rate: int = 16000, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate) * channels)
    return path


def test_no_file_is_unavailable(tmp_path):
    assert ea.SpeechAudio(tmp_path / "nope.wav").window(0, 1) == {"unavailable": "no speech.wav"}
    assert ea.SpeechAudio(None).window(0, 1) == {"unavailable": "no speech.wav"}


def test_a_file_in_another_format_is_unavailable(tmp_path):
    out = ea.SpeechAudio(_wav(tmp_path / "s.wav", 1.0, rate=8000)).window(0, 1)
    assert "unavailable" in out


def test_a_corrupt_file_is_unavailable(tmp_path):
    bad = tmp_path / "s.wav"
    bad.write_bytes(b"RIFF\x00\x00garbage")
    assert ea.SpeechAudio(bad).window(0, 1)["unavailable"].startswith("decode failed")


def test_a_window_past_the_end_of_the_audio_is_unavailable(tmp_path):
    out = ea.SpeechAudio(_wav(tmp_path / "s.wav", 1.0)).window(5.0, 6.0)
    assert out == {"unavailable": "window outside the audio"}


def test_a_vad_failure_is_unavailable_not_a_pause(tmp_path, monkeypatch):
    def boom(_x):
        raise RuntimeError("onnx")
    monkeypatch.setattr(ea, "vad_spans", boom)
    out = ea.SpeechAudio(_wav(tmp_path / "s.wav", 2.0)).window(0.5, 1.5)
    assert out == {"unavailable": "vad failed: RuntimeError"}


def _tone_wav(path: Path, seconds: float, bursts: list[tuple[float, float]]) -> Path:
    import numpy as np

    t = np.arange(int(seconds * 16000)) / 16000
    x = np.zeros_like(t)
    for a, b in bursts:
        x[(t >= a) & (t < b)] = 0.5 * np.sin(2 * np.pi * 200 * t[(t >= a) & (t < b)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def _loud(x, check=None) -> list[list[float]]:
    """A stand-in VAD that is exact about WHERE the sound is: 1 ms frames over 0.1."""
    import numpy as np

    n = len(x) // 16
    on = np.abs(np.asarray(x[:n * 16])).reshape(n, 16).max(axis=1) > 0.1
    edges = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(int), [0]])))
    return [[a * 16 / 16000, b * 16 / 16000] for a, b in zip(edges[::2], edges[1::2])]


def test_a_span_is_one_reading_of_the_file_whichever_window_asks(tmp_path, monkeypatch):
    """Blocks of the file, each read once with its own lead-in: the edges of a
    span do not depend on the window that asked (E0's windows were arbitrary,
    and a single Silero pass moves with the chunk phase)."""
    monkeypatch.setattr(ea, "vad_consensus", _loud)
    audio = ea.SpeechAudio(_tone_wav(tmp_path / "s.wav", 70.0, [(5.0, 8.0), (29.0, 32.0)]))
    near, wide = audio.window(4.1, 9.0), audio.window(0.0, 45.0)
    assert near["spans"] == [[5.0, 8.0]]
    assert wide["spans"] == [[5.0, 8.0], [29.0, 32.0]], "one span across the 30 s seam"
    assert near["t0"] == pytest.approx(128 * 512 / 16000)   # 4.096, the chunk holding 4.1
    assert near["source_end"] == pytest.approx(70.0)
    assert len(near["db"]) == (int(9.0 * 16000) - 128 * 512 - 320) // 160 + 1


def test_a_span_cut_at_the_edge_of_what_was_read_is_unread_not_ended(tmp_path, monkeypatch):
    """Block 0 alone sees the 29-32 s span stop at its seam (30 s). A window
    ending at 29.5 must not report 30.0 as where the sound stopped."""
    monkeypatch.setattr(ea, "vad_consensus", _loud)
    audio = ea.SpeechAudio(_tone_wav(tmp_path / "s.wav", 70.0, [(29.0, 32.0)]))
    win = audio.window(20.0, 29.5)
    assert win["spans"] == [[29.0, win["t1"]]]
    land = ea._landing(win, _w("word", 29.1, 29.2), after=29.2, limit=99.0, words=[])
    assert land["blocked"] in ("window_too_short", "speech_continues")


def test_each_block_is_read_once_per_run(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ea, "vad_consensus", lambda x, check=None: calls.append(len(x)) or [])
    audio = ea.SpeechAudio(_tone_wav(tmp_path / "s.wav", 70.0, []))
    audio.window(10.0, 12.0)
    audio.window(11.0, 20.0)
    audio.window(25.0, 35.0)
    assert len(calls) == 2, "block 0 once, block 1 once"


def test_the_worker_hands_the_projects_speech_wav_to_every_refinement():
    """The rule reaches new scoring runs through this call and nothing else: no
    ANALYSIS_VERSION bump (nothing cached turns stale), no migration, and a
    kept or exported row is never re-refined (E1)."""
    import inspect

    from workers import clipper_boundary_pass, clipper_build

    source = inspect.getsource(clipper_build.handle_score)
    assert 'audio_path=storage.paths(project_id)["audio"]' in source
    source = inspect.getsource(clipper_boundary_pass.refine_off_loop)   # EN2 R3: off the loop
    assert "audio = SpeechAudio(audio_path, stop=stopping)" in source
    assert "max_s=max_s, atoms=atoms, audio=audio)" in source


def test_an_unreadable_window_through_settle_end_leaves_the_end():
    class Gone:
        def window(self, t0, t1):
            return {"unavailable": "decode failed: EOFError"}
    end, ev = ea.settle_end(0.0, 1.5, [_w("stop.", 1.1, 1.5)], audio=Gone(), limit=9.0, lo=1.0)
    assert end == 1.5
    assert ev["status"] == "unavailable" and ev["why"] == "decode failed: EOFError"
    assert ev["reasons"] == []
