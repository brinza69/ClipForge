"""EN2 (codex-verdict-next-5 §1): the two defects Codex reproduced in EN1.

R1 — `_next_complete_ending` accepted a landing whose acoustic target lies BEFORE the current end, and
     applied it as a "move" (Codex's counter-probe: end 2.5 -> 2.1, below lo 2.3). A later ending must be
     later: target > end and every limit hold BEFORE it is applied; an earlier target is refused, never
     clamped, never declared a move.
R2 — a WAV whose header declares more samples than the file holds was read as a success (16,000 of a
     declared 32,000 returned with total=32000). The read must deliver what it asked for, bounded by the
     declared EOF; a truncation is `unavailable` and the end stays where today's code puts it.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

from services.clipper import candidate_boundaries as cb
from services.clipper import end_acoustics as ea


class Served:
    def __init__(self, win: dict):
        self.win = win

    def window(self, t0: float, t1: float) -> dict:
        return dict(self.win, t1=min(t1, self.win["t1"]))


def _w(word: str, t0: float, t1: float) -> dict:
    return {"word": word, "start": t0, "end": t1}


# Codex's counter-probe, verbatim: VAD [0.5,1.6], [1.8,2.0]; constant -60 dB; window [0, 8].
COUNTER_WIN = {"t0": 0.0, "t1": 8.0, "source_end": 1e9, "spans": [[0.5, 1.6], [1.8, 2.0]],
               "db": [-60.0] * 800}
COUNTER_WORDS = [_w("Stop.", 1.1, 1.5), _w("Done.", 1.8, 3.0)]


# ── R1 ───────────────────────────────────────────────────────────────────

def test_r1_codex_counter_probe_never_moves_the_end_earlier():
    end, ev = ea.settle_end(0.0, 2.5, COUNTER_WORDS, audio=Served(COUNTER_WIN), limit=8.0, lo=2.3)
    assert end == 2.5, ev
    assert ev["status"] != "moved" and ev.get("end_out") is None
    assert not any(p.get("applied") for p in ev["proposals"])


def test_r1_a_target_equal_to_the_end_is_not_a_move():
    # Same evidence; the current end sits exactly on the acoustic target (2.0 + MARGIN_S).
    at = round(2.0 + ea.MARGIN_S, 3)
    end, ev = ea.settle_end(0.0, at, COUNTER_WORDS, audio=Served(COUNTER_WIN), limit=8.0, lo=1.0)
    assert end == at and ev["status"] != "moved", ev


def test_r1_a_later_ending_is_still_taken_when_it_is_later():
    # Control: a real later ending with a long pause after it is still applied.
    words = [_w("Stop.", 1.1, 1.5), _w("Later", 1.8, 2.2), _w("Done.", 2.3, 3.0)]
    win = {"t0": 0.0, "t1": 8.0, "source_end": 1e9, "spans": [[0.5, 1.6], [1.8, 3.0]], "db": [-60.0] * 800}
    end, ev = ea.settle_end(0.0, 1.6, words, audio=Served(win), limit=8.0, lo=1.0)
    assert ev["decision"] == "next_complete_ending" and end > 1.6, ev
    assert end == round(3.0 + ea.MARGIN_S, 3)


def test_r1_invariant_through_refine_boundaries():
    """The ACOUSTIC stage never moves the start and never pulls the end earlier: against the same
    candidate refined without audio, the start is identical, the end is not earlier than the stage's
    end_in, and the duration and ceiling hold. (refine_boundaries' own start snap, 0.0 -> the first
    word, predates EN1 and happens with or without audio.)"""
    words = [_w("so", 0.2, 0.6), _w("we", 0.7, 1.0), _w("Stop.", 1.1, 1.5), _w("Done.", 1.8, 3.0)]
    transcript = {"segments": [{"start": 0.2, "end": 3.0, "text": " ".join(w["word"] for w in words),
                                "words": words}]}
    cand = {"start": 0.0, "end": 2.5, "text": "so we Stop. Done.", "reasons": []}
    plain = cb.refine_boundaries(dict(cand), transcript, {}, min_s=2.3, max_s=8.0)
    out = cb.refine_boundaries(dict(cand), transcript, {}, min_s=2.3, max_s=8.0, audio=Served(COUNTER_WIN))
    ev = out.get("end_evidence") or {}
    assert out["start"] == plain["start"]
    assert ev, "the acoustic stage ran and recorded its evidence"
    assert out["end"] >= ev["end_in"] - 1e-9, (out["end"], ev)
    assert out["end"] - out["start"] <= 8.0 + 1e-9
    assert out["end"] - out["start"] >= 2.3 - 1e-9                # the minimum duration (lo) holds too
    assert out["end"] <= max(3.0, cand["end"]) + 1e-9           # the ceiling: the last word / the candidate


# ── R2 ───────────────────────────────────────────────────────────────────

def _truncated_wav(path: Path, declared: int, actual: int) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(struct.pack("<%dh" % declared, *([0] * declared)))
    data = path.read_bytes()
    path.write_bytes(data[:44 + 2 * actual])      # the header still says `declared`
    return path


def test_r2_a_truncated_wav_is_unavailable_not_a_short_read(tmp_path):
    p = _truncated_wav(tmp_path / "speech.wav", 32000, 16000)
    got = ea.SpeechAudio(p)._samples(0, 32000)
    assert "unavailable" in got, got


def test_r2_the_window_and_the_decision_keep_todays_end(tmp_path):
    p = _truncated_wav(tmp_path / "speech.wav", 32000, 16000)
    audio = ea.SpeechAudio(p)
    assert "unavailable" in audio.window(0.0, 2.0)
    end, ev = ea.settle_end(0.0, 1.2, [_w("Stop.", 0.5, 1.0)], audio=audio, limit=8.0, lo=0.5)
    assert end == 1.2 and ev["status"] == "unavailable", ev


def test_r2_a_legitimate_eof_is_still_read(tmp_path):
    p = tmp_path / "speech.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(struct.pack("<%dh" % 16000, *([0] * 16000)))
    got = ea.SpeechAudio(p)._samples(8000, 40000)          # asks past the declared EOF
    assert "unavailable" not in got and len(got["x"]) == 8000 and got["total"] == 16000
