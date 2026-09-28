"""EN3 (codex-verdict-next-14 §2): the pause kept after the last word, under EN1's guards, off unless configured.

The end EN1 settled on a vocal end moves to vocal_end + target_s, never earlier, and only when the added audio
does not reach the next speech (VAD onset or Whisper word, minus GUARD_S), holds no energy onset (EN1's veto),
and stays inside the limit and the audio. Each refusal keeps EN1's end and says why.
"""
from __future__ import annotations

import pytest

from services.clipper import candidate_boundaries as cb
from services.clipper import end_tail as et


class Served:
    def __init__(self, win: dict):
        self.win = win

    def window(self, t0: float, t1: float) -> dict:
        return dict(self.win, t1=min(t1, self.win["t1"]))


def _w(word: str, t0: float, t1: float) -> dict:
    return {"word": word, "start": t0, "end": t1}


def _win(spans, *, db=None, source_end=1e9, t1=20.0) -> dict:
    return {"t0": 0.0, "t1": t1, "source_end": source_end, "spans": spans, "db": db or [-60.0] * 2000}


# "Done." ends at 10.0 in its box; the voice sounds until 10.08 (the VAD span); EN1 put the end at 10.18.
WORDS = [_w("so", 8.0, 8.4), _w("Done.", 9.2, 10.0), _w("next", 12.0, 12.4)]
SPANS = [[7.9, 10.08], [12.0, 12.5]]
EV_OK = {"decision": "tail_extended"}


def _extend(end=10.18, *, words=WORDS, win=None, ev=EV_OK, limit=90.0, target=0.4):
    return et.extend_tail(end, words, ev, audio=Served(win or _win(SPANS)), limit=limit, target_s=target)


def test_the_end_moves_to_the_vocal_end_plus_the_target():
    end, tail = _extend()
    assert end == pytest.approx(10.48) and tail["state"] == "moved" and tail["end_out"] == end
    assert tail["vocal_end"] == pytest.approx(10.08) and tail["next_speech"] == pytest.approx(12.0)


@pytest.mark.parametrize("target", [0, 0.0, -0.4, None, True, "0.4"])
def test_off_changes_nothing_and_records_nothing(target):
    assert _extend(target=target) == (10.18, None)


def test_the_next_speech_by_vad_refuses():
    end, tail = _extend(win=_win([[7.9, 10.08], [10.40, 11.0]]))
    assert end == 10.18 and tail["state"] == "refused" and tail["why"] == "enters_next_speech"


def test_the_next_whisper_word_refuses_even_without_a_vad_onset():
    words = [_w("so", 8.0, 8.4), _w("Done.", 9.2, 10.0), _w("and", 10.45, 10.6)]
    end, tail = _extend(words=words, win=_win([[7.9, 10.08]]))
    assert end == 10.18 and tail["why"] == "enters_next_speech"


def test_the_guard_margin_is_kept_before_the_next_speech():
    ok_end, ok = _extend(win=_win([[7.9, 10.08], [10.531, 11.0]]))       # target 10.48 <= 10.531 - 0.05
    no_end, no = _extend(win=_win([[7.9, 10.08], [10.52, 11.0]]))        # target 10.48 > 10.52 - 0.05
    assert ok["state"] == "moved" and ok_end == pytest.approx(10.48)
    assert no["state"] == "refused" and no_end == 10.18


def test_an_energy_onset_in_the_added_audio_refuses():
    db = [-60.0] * 2000
    for k in range(1030, 1045):          # 10.30-10.45 s: a sound rising 30 dB, held
        db[k] = -30.0
    end, tail = _extend(win=_win(SPANS, db=db))
    assert end == 10.18 and tail["why"] == "onset_in_interval" and tail["energy_onset"]["t"] == pytest.approx(10.30)


def test_the_limit_and_the_audio_end_refuse():
    end, tail = _extend(limit=10.30)
    assert end == 10.18 and tail["why"] == "limit"
    end, tail = _extend(win=_win(SPANS, source_end=10.40))
    assert end == 10.18 and tail["why"] == "limit"


def test_an_end_already_longer_is_kept():
    end, tail = _extend(end=10.9, ev={"decision": "next_complete_ending"})
    assert end == 10.9 and tail["state"] == "kept" and tail["why"] == "already_longer"


@pytest.mark.parametrize("decision", ["onset_in_interval", "unavailable", "no_word_before_end", None])
def test_an_end_en1_did_not_settle_is_left_alone(decision):
    end, tail = _extend(ev={"decision": decision})
    assert end == 10.18 and tail["state"] == "not_applicable"


def test_an_unreadable_window_keeps_the_end():
    class Broken:
        def window(self, *_a):
            return {"unavailable": "truncated speech.wav"}
    end, tail = et.extend_tail(10.18, WORDS, EV_OK, audio=Broken(), limit=90.0, target_s=0.4)
    assert end == 10.18 and tail["state"] == "unavailable"


@pytest.mark.parametrize("win", [_win(SPANS), _win([[7.9, 10.08], [10.40, 11.0]])])
@pytest.mark.parametrize("end", [9.5, 10.18, 10.6, 11.5])
def test_never_earlier(win, end):
    out, _ = _extend(end=end, win=win)
    assert out >= end


def test_through_refine_boundaries_with_the_setting_on_and_off(monkeypatch):
    from config import settings
    # The transcript goes on after the clip (the next sentence at 5.0 s), as a real one does: the ceiling
    # is not the clip's own last word, and EN1 settles the end at the voice's end + 0.1 s.
    words = [_w("so", 0.2, 0.6), _w("we", 0.7, 1.0), _w("Stop.", 1.1, 1.5), _w("Next", 5.0, 5.4)]
    transcript = {"segments": [{"start": 0.2, "end": 1.5, "text": "so we Stop.", "words": words[:3]},
                               {"start": 5.0, "end": 5.4, "text": "Next", "words": words[3:]}]}
    win = {"t0": 0.0, "t1": 8.0, "source_end": 1e9, "spans": [[0.15, 1.58], [5.0, 5.45]], "db": [-60.0] * 800}
    cand = {"start": 0.0, "end": 1.5, "text": "so we Stop.", "reasons": []}

    monkeypatch.setattr(settings, "clipper_end_tail_s", 0.0)
    off = cb.refine_boundaries(dict(cand), transcript, {}, min_s=1.0, max_s=8.0, audio=Served(win))
    monkeypatch.setattr(settings, "clipper_end_tail_s", 0.4)
    on = cb.refine_boundaries(dict(cand), transcript, {}, min_s=1.0, max_s=8.0, audio=Served(win))

    assert "tail" not in off["end_evidence"]
    assert on["end_evidence"]["tail"]["state"] == "moved"
    assert on["end"] == pytest.approx(1.58 + 0.4, abs=1e-3) and on["end"] > off["end"]
    assert "end_tail_extended" in on["reasons"] and on["start"] == off["start"]


def test_no_word_before_the_end_is_left_alone():
    end, tail = _extend(words=[_w("later", 12.0, 12.4)])
    assert end == 10.18 and tail["state"] == "not_applicable" and tail["why"] == "no_word_before_end"
