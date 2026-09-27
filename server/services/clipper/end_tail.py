"""EN3 — how long a clip keeps running after its last word (codex-verdict-next-14 §2: limited, reversible).

EN1/EN2 (`end_acoustics.settle_end`) put an end on the last word's acoustic end + MARGIN_S (0.1 s). In blind
round 3, with the corrected playback, the person chose the end at vocal end + 0.4 s in 3 of 4 new pairs, EN1 in
none, and answered both identical pairs "no difference" (A/editorial-review/tail-probe-3-answers-2026-09-27.txt;
rounds 1-2 had procedural faults and are not the same evidence). It supports this limited experiment, not 0.4 s
as optimal elsewhere (codex-verdict-next-18 §1).

`target_s` is a TARGET after the vocal end, not an amount added to EN1's end: with EN1 at vocal + 0.1 s, a
target of 0.4 extends the clip by ~0.3 s. `extend_tail` moves an end that EN1 settled on a vocal end to that
vocal end + `target_s`, under the SAME
guards the probe used, and never makes it earlier:
- the added audio never reaches the next speech — the earlier of the VAD next onset and the next Whisper word
  start — minus GUARD_S;
- no energy onset in the added audio: EN1's veto, `end_acoustics.onset_in`, unchanged;
- never past `limit` (the clip's maximum and the media end) or the audio's own end;
- measured on the audio at the settled end; a window that cannot be read keeps the end.
A refused extension keeps EN1's end and records why. OFF unless `settings.clipper_end_tail_s` > 0: the
activation is a separate decision, as EN2's was.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.clipper.candidate_terms import _EPS, _num
from services.clipper.end_acoustics import _pause_after, onset_in
from services.clipper.end_lookup import _last_word

RULE = "end_tail_v1"
GUARD_S = 0.05
# The EN1 decisions that leave the end on a clean vocal end. A conflict, an unreadable window or no word
# before the end is not a place to add pause to.
OK_DECISIONS = frozenset({"tail_extended", "already_clear", "next_complete_ending"})


def extend_tail(end: float, words: Sequence[dict], ev: dict | None, *, audio: Any, limit: float,
                target_s: float) -> tuple[float, dict | None]:
    """(end, tail evidence). Off (`target_s` <= 0): the end unchanged and no evidence at all."""
    if isinstance(target_s, bool) or not isinstance(target_s, (int, float)) or not target_s > 0:
        return end, None
    tail: dict[str, Any] = {"rule": RULE, "target_s": float(target_s), "end_in": round(end, 3)}
    decision = (ev or {}).get("decision")
    if decision not in OK_DECISIONS:
        return end, {**tail, "state": "not_applicable", "why": decision}
    word = _last_word(words, end)
    if word is None:
        return end, {**tail, "state": "not_applicable", "why": "no_word_before_end"}
    we = _num(word["end"])
    win = audio.window(min(we, end) - 10.0, max(we, end) + 2.0)
    if "unavailable" in win:
        return end, {**tail, "state": "unavailable", "why": win["unavailable"]}
    pause = _pause_after(win, we)
    vocal_end = pause["acoustic_end"]
    later = [_num(w["start"]) for w in words if _num(w["start"]) > vocal_end + 1e-3]
    speech = [t for t in (pause["next_onset"], min(later) if later else None) if t is not None]
    next_speech = min(speech) if speech else None
    target = round(vocal_end + float(target_s), 3)
    tail.update(vocal_end=vocal_end, next_speech=next_speech, requested_end=target)
    if end >= target - _EPS:
        return end, {**tail, "state": "kept", "why": "already_longer"}
    if next_speech is not None and target > next_speech - GUARD_S + _EPS:
        return end, {**tail, "state": "refused", "why": "enters_next_speech"}
    if target > limit + _EPS or target > win["source_end"] + _EPS:
        return end, {**tail, "state": "refused", "why": "limit"}
    onset = onset_in(win, end, target)
    if onset is not None:
        return end, {**tail, "state": "refused", "why": "onset_in_interval", "energy_onset": onset}
    return target, {**tail, "state": "moved", "end_out": target}
