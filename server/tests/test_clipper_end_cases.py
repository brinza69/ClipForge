"""EN1: the five endings a person listened to, through `refine_boundaries`.

Every case is a stored transcript's words around one real clip, E0's Silero
spans on that project's speech.wav and the 10 ms energy of the same window
(tests/data/endings/, built by B/gates/endings/en1/en1_fixtures.py; sha256 in
MANIFEST.txt; no media). The human evidence each one is held to
(A/editorial-review/listening-2026-09-26.txt):

  02dea  "se taie cuvântul chickens astfel încât se aude doar chicken";
         of three endings the person chose C = 3545.670, after "...else."
  1204   "se taie happening, mai bine lasă puțin din acea pauză lungă";
         preferred B = 11502.30 over the 11501.90 today's code gives.
  3e42   preferred B = 9440.11, the `_keep_release` tail.
  dea939 preferred B = 13083.56 over 13083.16 ("tăiat Minecraft").
  70ca   today's 2014.39 is "mai ok" than the accepted 2012.72: an ACCEPTED
         CONTROL for this case, not a validation of the end-snap in general.

Two levels of evidence differ at 02dea and both are kept: E0 measured the
voiced onset of "I'm" starting ~30 ms before the cut; the person heard the
sibilant of "chickens" cut. The rule answers both the same way — the end has
no pause after it — without deciding which one the ear caught.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.clipper import candidate_boundaries as cb

DATA = Path(__file__).parent / "data" / "endings"


def _fx(name: str) -> dict:
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


class FixtureAudio:
    """E0's measured spans and energy, served the way SpeechAudio serves a file."""

    def __init__(self, fx: dict):
        self.fx = fx

    def window(self, t0: float, t1: float) -> dict:
        vad, energy = self.fx["vad"], self.fx["energy"]
        return {"t0": energy["t0"], "t1": min(t1, vad["t1"]), "source_end": 1e9,
                "spans": vad["spans"], "db": energy["db"]}


class Unreadable:
    def window(self, t0: float, t1: float) -> dict:
        return {"unavailable": "no speech.wav"}


def _refine(fx: dict, audio=None, **kw) -> dict:
    words = fx["words"]
    transcript = {"segments": [{"start": words[0]["start"], "end": words[-1]["end"],
                                "words": words}]}
    cand = {"start": fx["entry"]["start"], "end": fx["entry"]["end"],
            "reasons": list(fx["entry"]["reasons"])}
    opts = {"min_s": fx["min_s"], "max_s": fx["max_s"], **kw}
    if audio is not None:
        opts["audio"] = audio
    return cb.refine_boundaries(cand, transcript, {}, **opts)


# today's end for each case: E1's replay of this code (replay_today.json), and
# for dea939 E1's tail-only replay from the stored window
TODAY = {"02dea6f0a9e9": 3543.81, "1204e0fdfba2": 11501.9, "3e42c5a399c2": 9440.11,
         "dea939f254a1": 13083.56, "70ca6472b03b": 2014.39}


@pytest.mark.parametrize("case", sorted(TODAY))
def test_without_audio_every_case_ends_where_todays_code_ends(case):
    """The fixtures reproduce E1's replay, so what changes below is the rule."""
    out = _refine(_fx(case))
    assert out["end"] == TODAY[case]
    assert "end_evidence" not in out


@pytest.mark.parametrize("case", sorted(TODAY))
def test_unreadable_audio_leaves_every_end_exactly_as_today(case):
    """`unavailable` is its own verdict, never a pause: same window, same text,
    same reasons as the transcript-only run."""
    fx = _fx(case)
    plain, blind = _refine(fx), _refine(fx, Unreadable())
    for key in ("start", "end", "text", "reasons", "words"):
        assert blind[key] == plain[key], key
    assert blind["end_evidence"]["status"] == "unavailable"
    assert blind["end_evidence"]["why"] == "no speech.wav"


def test_02dea_moves_to_the_next_complete_ending_with_a_pause():
    """The person's C. "chickens." sits inside 1.76 s of continuous speech, so
    it is not an ending the audio allows; the next punctuated one, "else.", has
    a 416 ms pause (>= P_MIN 352 ms) and lands at its VAD end + 100 ms."""
    out = _refine(_fx("02dea6f0a9e9"), FixtureAudio(_fx("02dea6f0a9e9")))
    assert out["end"] == pytest.approx(3545.670, abs=1e-6)
    assert out["text"].endswith("focus on something else.")
    ev = out["end_evidence"]
    assert (ev["status"], ev["decision"]) == ("moved", "next_complete_ending")
    assert "end_next_pause" in out["reasons"]
    applied = [p for p in ev["proposals"] if p.get("applied")]
    assert [p["after"] for p in applied] == ["else."]
    assert applied[0]["vad"]["next_onset"] > out["end"], "the next phrase is not cut"
    # Whisper boxes "All" from 3545.63; its sound begins at 3545.986 (VAD)
    assert applied[0]["boxed_words"][0]["word"] == "All"
    earlier = [p for p in ev["proposals"] if p.get("side") == "earlier"]
    assert earlier and not any(p["applied"] for p in earlier)


def test_02dea_with_no_safe_ending_inside_the_limit_keeps_the_end_and_says_so():
    """max 38.5 s puts the limit at 3544.57, before "else." can land: no bound-
    less extension, the end stays and the proposals and the conflict are kept."""
    out = _refine(_fx("02dea6f0a9e9"), FixtureAudio(_fx("02dea6f0a9e9")), max_s=38.5)
    assert out["end"] == 3543.81
    ev = out["end_evidence"]
    assert (ev["status"], ev["decision"]) == ("conflict", "no_complete_ending_with_pause")
    assert ev["proposals"] and "end_acoustic_conflict" in out["reasons"]


def test_1204_extends_over_the_dropped_word_only_on_audio_that_shows_no_onset():
    """`_drop_dangling_tail` removed "so" (a one-second Whisper box) and
    `_keep_release` still refuses to pad over it. The audio: the span holding
    "happening" runs 224 ms on, decaying (max 30 ms rise 4.3 dB < 9), then 4.5 s
    of pause. The end moves to 11502.224; "so" stays out of the text."""
    fx = _fx("1204e0fdfba2")
    out = _refine(fx, FixtureAudio(fx))
    assert out["end"] == pytest.approx(11502.224, abs=1e-6)
    assert "release_kept" not in out["reasons"], "_keep_release is unchanged"
    assert "end_acoustic_tail" in out["reasons"]
    assert not out["text"].rstrip().endswith("so")
    assert out["words"][-1]["word"] == "happening"
    ev = out["end_evidence"]
    assert ev["dropped_word"]["word"] == "so"
    assert [w["word"] for w in ev["boxed_words"]] == ["so"]
    assert ev.get("energy_onset") is None
    assert ev["vad"]["next_onset"] > out["end"]


def test_1204_keep_release_still_reads_the_dropped_word():
    """Codex §4: the fix must not drop the word from the list the guard reads."""
    words = _fx("1204e0fdfba2")["words"]
    assert cb._keep_release(11501.9, words, 1e9, 1e9) == 11501.9


def test_1204_negative_the_dropped_word_really_spoken_is_not_cut_into():
    """MANDATORY (Codex §4). Same dropped "so", but spoken right after the end:
    the VAD span runs on and the energy shows a real onset (02dea's measured
    "I'm", -46 -> -22 dB). The extension would slice it: refused, today's end
    kept, the conflict and the proposal recorded."""
    fx = _fx("1204e0fdfba2_spoken_so")
    out = _refine(fx, FixtureAudio(fx))
    assert out["end"] == 11501.9
    ev = out["end_evidence"]
    assert (ev["status"], ev["decision"]) == ("conflict", "onset_in_interval")
    assert ev["energy_onset"]["t"] < ev["proposals"][0]["end"]
    assert "end_acoustic_conflict" in out["reasons"]


def test_1204_negative_speech_that_runs_on_is_not_a_release():
    """The span running 824 ms past the word is speech, not the word's release
    (TAIL_MAX 0.57 s): no extension even with no energy onset."""
    fx = _fx("1204e0fdfba2_long_tail")
    out = _refine(fx, FixtureAudio(fx))
    assert out["end"] == 11501.9
    assert out["end_evidence"]["decision"] == "speech_continues"
    assert out["end_evidence"]["proposals"][0]["end"] is None


def test_3e42_keeps_the_tail_the_person_preferred():
    """`_keep_release` already ends at 9440.11, past the VAD end 9439.966 + 100
    ms: in the clear, so the audio moves nothing."""
    out = _refine(_fx("3e42c5a399c2"), FixtureAudio(_fx("3e42c5a399c2")))
    assert out["end"] == 9440.11
    assert out["end_evidence"]["decision"] == "already_clear"


def test_dea939_tail_reaches_the_acoustic_end_plus_the_margin():
    """Today's pad stops 16 ms past the VAD end (13083.544); the rule leaves
    100 ms: 13083.644, 84 ms past the variant the person chose (B = 13083.56)
    and 5.85 s before the next speech. That 84 ms has not been heard."""
    out = _refine(_fx("dea939f254a1"), FixtureAudio(_fx("dea939f254a1")))
    assert out["end"] == pytest.approx(13083.644, abs=1e-6)
    assert out["end_evidence"]["decision"] == "tail_extended"


def test_70ca_the_accepted_control_stays_where_todays_code_puts_it():
    """2014.39 sits inside the 2.1 s pause (VAD 2012.336 -> 2014.512): clear."""
    out = _refine(_fx("70ca6472b03b"), FixtureAudio(_fx("70ca6472b03b")))
    assert out["end"] == 2014.39
    assert out["end_evidence"]["decision"] == "already_clear"


@pytest.mark.parametrize("case", sorted(TODAY) + ["1204e0fdfba2_spoken_so",
                                                  "1204e0fdfba2_long_tail"])
def test_the_audio_only_ever_moves_an_end_later_and_inside_the_limit(case):
    fx = _fx(case)
    plain, heard = _refine(fx), _refine(fx, FixtureAudio(fx))
    assert heard["start"] == plain["start"]
    assert heard["end"] >= plain["end"]
    assert heard["end"] - heard["start"] <= fx["max_s"] + 1e-6
    json.dumps(heard["end_evidence"])  # rides in candidates.json
