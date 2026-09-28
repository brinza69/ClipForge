"""
ClipForge — AI Stream Clipper, Pass C: what the audio says about where a clip may end.

`candidate_boundaries` chooses WHICH word a clip ends on from the transcript,
and lands the cut on that word's Whisper `end`. That timestamp is a proposal:
it marks where the model stopped hearing the word, not where the sound stops.
Measured by E0 (B/E0-result.md) on the five ends a person listened to, the
audible sound ran 224-384 ms past the Whisper end on every cut placed there,
and the one ending the person heard as a cut WORD (02dea: "chickens" heard as
"chicken") sat on a sentence end with no pause after it at all — the next
phrase began ~30 ms before the cut. Traced by E1 (B/E1-result.md) to three
different causes, none of which a longer fixed pad repairs:

  * an old window never re-refined (3e42, dea939) — not this module's job;
  * `_keep_release` blocked by the Whisper box of the very word
    `_drop_dangling_tail` had just removed (1204);
  * `_nearest_sentence_end` snapping onto a sentence end inside continuous
    speech (02dea).

So this module reads the audio around the end — Silero VAD from the installed
faster-whisper with E0's options, agreed over four chunk phases, with the 10 ms
energy only as a confirmation — and makes the SMALLEST move the evidence
supports:

  1. tail: an end short of the last word's acoustic end moves to that end
     plus MARGIN_S, inside the pause, never past the next VAD onset;
  2. a punctuated sentence end with no sufficient pause after it moves to the
     NEXT complete ending that has one, within start + max_s;
  3. anything else, or any disagreement between the signals, keeps the end and
     records the proposals and the conflict.

VAD, energy and punctuation PROPOSE; the ear confirms. Nothing here is a
universal pad: every move is bounded by what the audio shows at that end.

A window that cannot be read — no speech.wav, a decode failure, a VAD failure —
is `unavailable` and the end stays exactly where today's rules put it. It is
never read as "pause": a thing that could not be read must never read as a pass.
"""

from __future__ import annotations

import bisect
import logging
import math
import wave
from pathlib import Path
from typing import Any, Callable, Sequence

from services.clipper.candidate_terms import _CLOSERS, _EPS, SENTENCE_END_REACH_S, _num
from services.clipper.segmentation import _ends_sentence
from services.clipper.end_lookup import _brief, _last_word, _span_at

logger = logging.getLogger("clipforge.clipper.end_acoustics")

# Recorded on every candidate this rule looks at. A new threshold or a new
# decision is a new version, so an `end_evidence` always says which rule wrote it.
RULE_VERSION = "end_acoustics_v2"   # v2 = EN2: R1 target_not_later, R2 truncated WAV unavailable

SAMPLE_RATE = 16000       # storage.py: audio/speech.wav is mono 16 kHz PCM
# Silero via faster_whisper.vad, as E0 ran it (B/gates/endings/e0_vad.py):
# threshold 0.5 (library default; neg 0.35), min_silence 100 ms, pad 0. The
# library's ASR defaults (2000 ms / 400 ms pad) merge segments and would hide
# exactly the pauses this rule looks for.
VAD_THRESHOLD = 0.5
VAD_MIN_SILENCE_MS = 100
VAD_PAD_MS = 0
VAD_CHUNK = 512           # the model's own 32 ms chunk; windows start on this grid
# A span edge from ONE pass moves with the 32 ms chunk phase: measured on the
# five E0 cases at 16 phases 2 ms apart (B/gates/endings/en1/en1_vad_phase.py),
# edges moved 30-56 ms, and 140-178 ms on dea939's unvoiced "-craft"; at 3e42 a
# phantom onset (9440.16 or 9440.63) appeared in 4 of 16 phases with a 1 s
# lead-in, and the one the first probe hit sent the end 5.5 s later. So the
# VAD runs at VAD_PHASES phases VAD_CHUNK / VAD_PHASES apart, and a moment is
# speech when at least half of them say so (a tie is speech: the cautious side
# when what is sought is a pause).
VAD_PHASES = 4
ENERGY_HOP_S = 0.010      # E0's 10 ms energy frames
ENERGY_BAND_HZ = (150.0, 4000.0)  # E0's band: keeps speech, drops rumble and hiss
# A pause long enough to END on. p10 of the 32 VAD gaps after a terminal-
# punctuated word in E0's slice4h00test windows (B/gates/endings/report.json
# -> p_min; n = 32, one gaming project, one main speaker). Not a completion
# signal on its own: 4 of 8 gaps after a NON-terminal word were as long.
P_MIN_S = 0.352
# Room left after the acoustic end. NOT validated by ear: E0 proposed it (as
# min(100 ms, gap/2); the half-gap cap never binds once the pause is at least
# P_MIN_S), and the paired listening files of EN1 are its first check.
MARGIN_S = 0.100
# How far a word's sound may run past its Whisper end and still be THAT word's
# release. p95 of candidate_terms.TAIL_PAD_S's measurement (300 sentence-final
# words, Whisper end -> RMS noise floor: p50 0.16, p90 0.40, p95 0.57 s). E0's
# three measured tails (224, 256, 384 ms) sit inside it; 02dea's 1.76 s of
# continuous speech does not. Longer means the speaker went on talking.
TAIL_MAX_S = 0.57
# An energy ONSET: frames rising at least ONSET_RISE_DB above the lowest level
# since the start of the interval, held for ONSET_HOLD_S. Measured with
# energy_db() below on E0's five speech.wav windows (B/gates/endings/en1/
# en1_onsets.py): 9 dB is p10 of the sustained rise at the 66 VAD onsets
# there, so the veto sees 9 onsets in 10. It is NOT a clean separation — game
# audio gives release tails and pause heads rises of 20 dB and more — so an
# onset only ever KEEPS today's end; it never moves one. 30 ms is the hold
# where false alarms fell (H1 -> H3) and onset recall had not yet (H4 lost it).
ONSET_RISE_DB = 9.0
ONSET_HOLD_S = 0.030
# Audio read before each VAD block. The VAD needs a lead-in: a window opening
# 1 s before the word, mid-speech, ended dea939's span 1.1-1.2 s early and gave
# 3e42 its phantom onsets; 5 s still left one (9440.63, 2 of 16 phases); with
# 10 s neither happened (en1_vad_phase.log).
LOOK_BEFORE_S = 10.0
# How far the NEXT complete ending may lie past the current end. Bounded only by
# start + max_s, the first probe over a whole project (slice4h00test, 882
# candidates, en1/project_cost_slice4h00test.json) moved 89 ends by p50 3.2 s,
# p90 16.7 s and at most 41.5 s: whole sentences added, a content change and not
# an ending repair. So the search reaches as far as the transcript rule itself
# is allowed to push an end out to finish a sentence (SENTENCE_END_REACH_S);
# the person's choice at 02dea lies +1.86 s out. Nothing inside it: conflict.
NEXT_ENDING_REACH_S = SENTENCE_END_REACH_S
LOOK_AFTER_S = 2.0        # TAIL_MAX_S + P_MIN_S + MARGIN_S = 1.02 s, with room
# The VAD is read in fixed blocks of the file, each once per run, each with its
# own LOOK_BEFORE_S lead-in. So a span edge is ONE reading of the file, the same
# whichever candidate asks, and a run costs at most one consensus pass over the
# audio it touches (measured: one pass ~518x realtime, VAD_PHASES of them ~140x).
BLOCK_S = 30.0


class Stopped(Exception):
    """The run asked the reader to stop (EN2 R3). Never an `unavailable`: a
    stopped read is not a reading, and nothing may keep an end on it."""


class SpeechAudio:
    """Windows of one project's speech.wav, read on demand. Never raises,
    except `Stopped` when `stop()` says so.

    `window()` returns {"t0", "t1", "source_end", "spans", "db"} on the file's
    own clock (the transcript's: E0 measured speech.wav against the render
    source at 0 ms), or {"unavailable": why}. `db` starts at `t0`.
    """

    def __init__(self, path: str | Path | None, stop: Callable[[], bool] | None = None):
        self.path = Path(path) if path else None
        self._blocks: dict[int, Any] = {}
        self._stop = stop     # asked before every VAD phase, so before every block too

    def _check(self) -> None:
        if self._stop is not None and self._stop():
            raise Stopped("the run stopped the reader")

    def window(self, t0: float, t1: float) -> dict:
        if self.path is None or not self.path.is_file():
            return {"unavailable": "no speech.wav"}
        a = max(0, int(math.floor(t0 * SAMPLE_RATE / VAD_CHUNK)) * VAD_CHUNK)
        got = self._samples(a, int(math.ceil(t1 * SAMPLE_RATE)))
        if "unavailable" in got:
            return got
        x, total = got["x"], got["total"]
        spans: list[list[float]] = []
        first = int(a / SAMPLE_RATE // BLOCK_S)
        last = int((a + len(x) - 1) / SAMPLE_RATE // BLOCK_S)
        for k in range(first, last + 1):
            block = self._block(k, total)
            if isinstance(block, dict):
                return block
            for s, e in block:
                if spans and abs(spans[-1][1] - s) < 1e-3:   # one span across a seam
                    spans[-1][1] = e
                else:
                    spans.append([s, e])
        # Only what this window covers: a span cut at a seam this read did not
        # load must reach the window's edge, where it reads as unread.
        t0, t1 = a / SAMPLE_RATE, (a + len(x)) / SAMPLE_RATE
        spans = [[s, min(e, t1)] for s, e in spans if e > t0 and s < t1]
        return {"t0": t0, "t1": t1, "source_end": total / SAMPLE_RATE,
                "spans": spans, "db": energy_db(x)}

    def _samples(self, a: int, b: int) -> dict:
        try:
            import numpy as np

            with wave.open(str(self.path), "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                    return {"unavailable": "speech.wav is not 16 kHz mono 16-bit"}
                total = w.getnframes()
                if a >= total or b <= a:
                    return {"unavailable": "window outside the audio"}
                w.setpos(a)
                raw = w.readframes(want := min(b, total) - a)
            if len(raw) != 2 * want:   # EN2 R2: short of the DECLARED EOF (min above) is truncation
                return {"unavailable": f"truncated speech.wav: read {len(raw) // 2} of {want} samples"}
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        except Exception as exc:  # a truncated or foreign file is not a pause
            return {"unavailable": f"decode failed: {type(exc).__name__}"}
        return {"x": x, "total": total}

    def _block(self, k: int, total: int) -> Any:
        """Speech spans inside [k, k+1) * BLOCK_S, absolute seconds, or an
        {"unavailable": why} that the window passes on."""
        if k not in self._blocks:
            lo, hi = k * BLOCK_S, (k + 1) * BLOCK_S
            a = max(0, int(math.floor((lo - LOOK_BEFORE_S) * SAMPLE_RATE / VAD_CHUNK)) * VAD_CHUNK)
            got = self._samples(a, min(total, int((hi + LOOK_AFTER_S) * SAMPLE_RATE)))
            if "unavailable" not in got:
                try:
                    t0 = a / SAMPLE_RATE
                    got = [[max(lo, t0 + s), min(hi, t0 + e)]
                           for s, e in vad_consensus(got["x"], check=self._check)
                           if t0 + e > lo and t0 + s < hi]
                except Stopped:
                    raise     # a stop is not a VAD failure: never cached as `unavailable`
                except Exception as exc:
                    logger.warning("end acoustics: VAD failed", exc_info=True)
                    got = {"unavailable": f"vad failed: {type(exc).__name__}"}
            self._blocks[k] = got
        return self._blocks[k]


def vad_spans(x: Any) -> list[list[float]]:
    """Silero speech spans (seconds from the start of `x`), E0's options."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(threshold=VAD_THRESHOLD, min_silence_duration_ms=VAD_MIN_SILENCE_MS,
                      speech_pad_ms=VAD_PAD_MS)
    return [[s["start"] / SAMPLE_RATE, s["end"] / SAMPLE_RATE]
            for s in get_speech_timestamps(x, opts)]


def vad_consensus(x: Any, check: Callable[[], None] | None = None) -> list[list[float]]:
    """Speech spans (seconds from the start of `x`) where at least half of
    VAD_PHASES single passes, each started VAD_CHUNK / VAD_PHASES later, agree.
    `check` runs before each phase and raises to stop."""
    import numpy as np

    step, res = VAD_CHUNK // VAD_PHASES, 16          # phase step; 1 ms vote grid
    votes = np.zeros(len(x) // res + 1, dtype=np.int32)
    for k in range(VAD_PHASES):
        if check is not None:
            check()
        offset = k * step / SAMPLE_RATE
        for s, e in vad_spans(x[k * step:]):
            votes[int(round((s + offset) * SAMPLE_RATE / res)):
                  int(round((e + offset) * SAMPLE_RATE / res))] += 1
    speech = np.concatenate([[False], 2 * votes >= VAD_PHASES, [False]])
    edges = np.flatnonzero(np.diff(speech.astype(np.int8)))
    return [[int(a) * res / SAMPLE_RATE, int(b) * res / SAMPLE_RATE]
            for a, b in zip(edges[::2], edges[1::2])]


def energy_db(x: Any) -> list[float]:
    """Band energy in dB, one value per 10 ms hop; frame k starts at k * hop.

    A 20 ms Hann frame per hop, so a frame's value describes [k*hop, k*hop+20ms).
    """
    import numpy as np

    hop, size = int(ENERGY_HOP_S * SAMPLE_RATE), int(2 * ENERGY_HOP_S * SAMPLE_RATE)
    n = max(0, (len(x) - size) // hop + 1)
    if n == 0:
        return []
    idx = np.arange(size)[None, :] + hop * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(np.asarray(x, dtype=np.float64)[idx] * np.hanning(size), axis=1)) ** 2
    freqs = np.fft.rfftfreq(size, 1.0 / SAMPLE_RATE)
    band = (freqs >= ENERGY_BAND_HZ[0]) & (freqs <= ENERGY_BAND_HZ[1])
    power = spec[:, band].sum(axis=1) / (size * size)
    return [round(float(v), 2) for v in 10.0 * np.log10(power + 1e-12)]


# --------------------------------------------------------------------------
# reading a window — pure functions over {"spans", "db", "t0", "t1"}
# --------------------------------------------------------------------------

def _pause_after(win: dict, word_end: float) -> dict:
    """The acoustic end of a word ending at `word_end`, and the pause after it.

    acoustic_end = the end of the VAD span holding `word_end`, or of the last
    span before it when the word already ended in silence. `pause_s` runs to
    the next VAD onset, or to the edge of what was read (then `open`).
    """
    spans = win["spans"]
    held = _span_at(spans, word_end)
    if held is not None:
        acoustic_end = held[1]
    else:
        before = [s1 for s0, s1 in spans if s1 <= word_end + _EPS]
        acoustic_end = max(before) if before else win["t0"]
    onsets = [s0 for s0, _s1 in spans if s0 > acoustic_end + _EPS]
    edge = min(win["t1"], win["source_end"])
    next_onset = min(onsets) if onsets else None
    return {"acoustic_end": round(acoustic_end, 3),
            "tail_s": round(acoustic_end - word_end, 3),
            "next_onset": None if next_onset is None else round(next_onset, 3),
            "pause_s": round((next_onset if next_onset is not None else edge) - acoustic_end, 3),
            "open": next_onset is None and edge < win["source_end"] - _EPS}


def onset_in(win: dict, a: float, b: float) -> dict | None:
    """The first energy onset starting in (a, b], or None.

    An onset is a run of frames at least ONSET_RISE_DB above the lowest frame
    since `a`, held ONSET_HOLD_S. The hold may run past `b`: an onset that
    begins inside the interval is cut by an end placed at `b`.
    """
    db, t0 = win["db"], win["t0"]
    hold = max(1, int(round(ONSET_HOLD_S / ENERGY_HOP_S)))
    k0 = max(0, int(math.floor((a - t0) / ENERGY_HOP_S)))
    floor = None
    for k in range(k0, len(db)):
        t = t0 + k * ENERGY_HOP_S
        if t > b + _EPS:
            break
        floor = db[k] if floor is None else min(floor, db[k])
        if t <= a + _EPS:
            continue
        run = db[k:k + hold]
        if len(run) == hold and min(run) >= floor + ONSET_RISE_DB:
            return {"t": round(t, 3), "rise_db": round(min(run) - floor, 1)}
    return None


# --------------------------------------------------------------------------
# the decision
# --------------------------------------------------------------------------

def _landing(win: dict, word: dict, *, after: float, limit: float,
             words: Sequence[dict]) -> dict:
    """Where an end after `word` would land, and every reason it may not.

    `after` is where the clip currently ends: only (after, target] is new audio.

    A Whisper box that STARTS in the added audio — the word `_drop_dangling_tail`
    removed, or simply the next word — is a possible onset, not a verdict. Box
    starts are proposals too: at 02dea the person chose 3545.670, 40 ms inside
    the box Whisper gives "All", whose sound (VAD) begins at 3545.986. So the
    box is recorded and the audio decides: the extension passes it only with
    no new VAD onset before the target (by construction: the target stays
    inside the pause and the span holding the word began before it) and no
    energy onset in the added audio.
    """
    we = _num(word["end"])
    pause = _pause_after(win, we)
    # Inside the pause whenever the pause is long enough to end on at all:
    # P_MIN_S (0.352) is more than MARGIN_S, so the target never reaches the
    # next onset. A shorter pause is refused below, not squeezed.
    target = round(pause["acoustic_end"] + MARGIN_S, 3)
    out = {"after": _brief(word), "vad": pause, "target": target, "blocked": None}
    if pause["tail_s"] > TAIL_MAX_S:
        out["blocked"] = "speech_continues"      # not a release: the speaker went on
    elif pause["pause_s"] < P_MIN_S and not pause["open"]:
        out["blocked"] = "pause_too_short"
    elif pause["open"] and pause["pause_s"] < P_MIN_S:
        out["blocked"] = "window_too_short"      # unread, so not a pause
    elif target > limit + _EPS:
        out["blocked"] = "limit"
    else:
        lo_t = max(after, we)
        boxed = [_brief(w) for w in words
                 if lo_t - _EPS <= _num(w["start"]) < target - _EPS]
        if boxed:
            out["boxed_words"] = boxed[:3]
        onset = onset_in(win, lo_t, target)
        out["energy_onset"] = onset
        if onset is not None:
            out["blocked"] = "onset_in_interval"
    return out


def settle_end(start: float, end: float, words: Sequence[dict], *, audio: Any,
               limit: float, lo: float, dropped: dict | None = None) -> tuple[float, dict]:
    """The end the audio supports, and the evidence for it.

    Only ever moves the end LATER: an end the rules above already placed in
    the clear is left alone. `dropped` is the word `_drop_dangling_tail`
    removed. It stays a possible onset — `_keep_release` still reads it and
    still refuses to pad over it — and this rule may pass its Whisper box only
    on the evidence `_landing` requires of every box in the added audio.
    Returns (end, end_evidence).
    """
    ev: dict[str, Any] = {"rule": RULE_VERSION, "end_in": round(end, 3),
                          "status": "kept", "decision": None, "proposals": [],
                          "conflict": None, "reasons": []}
    if dropped is not None:
        ev["dropped_word"] = _brief(dropped)
    # Only the words this can touch: every scan below is over them, and the
    # whole transcript of a 12-hour source is ~150k words per candidate.
    by_start = (lambda w: _num(w["start"]))
    words = words[bisect.bisect_left(words, start - 1.0, key=by_start):
                  bisect.bisect_right(words, limit + LOOK_AFTER_S, key=by_start)]
    word = _last_word(words, end)
    if word is None:
        ev["decision"] = "no_word_before_end"
        return end, ev
    we = _num(word["end"])
    win = audio.window(min(we, end) - LOOK_BEFORE_S, max(we, end) + LOOK_AFTER_S)
    if "unavailable" in win:
        ev.update(status="unavailable", decision="unavailable", why=win["unavailable"])
        return end, ev

    here = _landing(win, word, after=end, limit=limit, words=words)
    ev["word"], ev["vad"] = here["after"], here["vad"]
    for key in ("energy_onset", "boxed_words"):
        if here.get(key) is not None:
            ev[key] = here[key]
    next_onset = here["vad"]["next_onset"]

    if here["blocked"] is None:
        if end >= here["target"] - _EPS:
            clear = next_onset is None or end < next_onset
            ev["decision"] = "already_clear" if clear else "end_after_next_onset"
            return end, ev
        ev.update(status="moved", decision="tail_extended", end_out=here["target"])
        ev["reasons"] = ["end_acoustic_tail"]
        return here["target"], ev

    ev["proposals"].append(_proposal(here))
    sentence = _ends_sentence(str(word.get("word", "")).rstrip(_CLOSERS))
    if here["blocked"] in ("speech_continues", "pause_too_short") and sentence:
        return _next_complete_ending(start, end, word, words, audio=audio, limit=limit,
                                     lo=lo, ev=ev)
    ev.update(status="conflict", decision=here["blocked"],
              conflict=f"end after {here['after']['word']!r}: {here['blocked']}")
    ev["reasons"] = ["end_acoustic_conflict"]
    return end, ev


def _next_complete_ending(start: float, end: float, word: dict, words: Sequence[dict], *,
                          audio: Any, limit: float, lo: float, ev: dict) -> tuple[float, dict]:
    """02dea: the sentence end has no pause after it. Take the NEXT punctuated
    ending that has one, inside the limit and NEXT_ENDING_REACH_S — only the
    next: the first one that qualifies is the one move this rule makes, so there
    is never a choice between several. The earlier one is recorded and never
    applied (at 02dea it drops the clip's last line). None: keep, and say so."""
    we = _num(word["end"])
    limit = min(limit, end + NEXT_ENDING_REACH_S)
    win = audio.window(we - LOOK_BEFORE_S, limit + LOOK_AFTER_S)
    if "unavailable" in win:
        ev.update(status="unavailable", decision="unavailable", why=win["unavailable"])
        return end, ev
    later = [w for w in words if we + _EPS < _num(w["end"]) <= limit + _EPS
             and _ends_sentence(str(w.get("word", "")).rstrip(_CLOSERS))]
    chosen = None
    for w in later:
        land = _landing(win, w, after=end, limit=limit, words=words)
        # EN2 R1: a later WORD is not a later ENDING (its sound can end before `end`: 2.5 -> 2.1 under
        # lo). Refused and recorded, never clamped or called a move; `_landing` refuses past `limit`.
        if land["blocked"] is None and land["target"] <= end + _EPS:
            land["blocked"] = "target_not_later"
            ev["proposals"].append(_proposal(land))
            continue
        if land["blocked"] is None:
            chosen = land
            break
    ev["proposals"].extend(_earlier_complete_endings(start, word, words, audio=audio, lo=lo))
    if chosen is None:
        ev.update(status="conflict", decision="no_complete_ending_with_pause",
                  conflict="no punctuated ending with a sufficient pause within "
                           f"{NEXT_ENDING_REACH_S:g} s and the limit")
        ev["reasons"] = ["end_acoustic_conflict"]
        return end, ev
    ev["proposals"].append({"end": chosen["target"], "after": chosen["after"]["word"],
                            "vad": chosen["vad"], "side": "later", "applied": True,
                            "boxed_words": chosen.get("boxed_words")})
    ev.update(status="moved", decision="next_complete_ending", end_out=chosen["target"])
    ev["reasons"] = ["end_next_pause"]
    return chosen["target"], ev


def _earlier_complete_endings(start: float, word: dict, words: Sequence[dict], *,
                              audio: Any, lo: float) -> list[dict]:
    """Proposals only, never applied: up to three punctuated endings before this
    one, nearest first, each with why it would not do, stopping at the first
    that would. Searched back to start + lo, the clip's minimum."""
    we = _num(word["end"])
    floor = start + lo
    earlier = [w for w in words if floor - _EPS <= _num(w["end"]) < we - _EPS
               and _ends_sentence(str(w.get("word", "")).rstrip(_CLOSERS))]
    earlier = earlier[-3:]
    if not earlier:
        return []
    win = audio.window(_num(earlier[0]["end"]) - LOOK_BEFORE_S, we + LOOK_AFTER_S)
    if "unavailable" in win:
        return [{"side": "earlier", "unavailable": win["unavailable"]}]
    out: list[dict] = []
    for w in reversed(earlier):
        land = _landing(win, w, after=_num(w["end"]), limit=we, words=words)
        out.append({**_proposal(land), "side": "earlier", "applied": False})
        if land["blocked"] is None:
            break
    return out


def _proposal(land: dict) -> dict:
    """A landing as a proposal. Where the speaker went on there is no ending
    after that word to propose, so no time is given for it."""
    end = None if land["blocked"] == "speech_continues" else land["target"]
    return {"end": end, "after": land["after"]["word"], "blocked": land["blocked"]}
