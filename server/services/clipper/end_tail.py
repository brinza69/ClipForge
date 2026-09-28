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

import math
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


STATES = frozenset({"moved", "kept", "refused", "not_applicable", "unavailable"})
# The window check compares two numbers that are the same arithmetic done twice: the argv prints `-ss` and `-t`
# with `%.3f` (±0.5 ms each) and the record rounds its end to the millisecond (±0.5 ms) — 1.5 ms, 2 with slack.
WINDOW_TOL_S = 0.002
# The output check compares the argv's `-t` with what ffprobe measures, and the encoder does not stop exactly
# there. Video leaves on the `-r` grid, so its last frame lands up to one frame (1/fps) either side of `-t`
# (EN3V2 f81b ON: video 61.200 for 61.214). AAC encodes whole frames of 1024 samples at `AUDIO_RATE` (every
# export's loudness chain resamples to it), so the audio can run up to one such frame, 21.3 ms, past it. The
# container reports the longer stream. Plus the probe's own rounding to the millisecond and the argv's: 1 ms.
# At 60 fps that is 16.7 + 21.3 + 1 = 39 ms — a frame plus an audio frame, not the scoring's 1 ms.
AAC_FRAME_SAMPLES = 1024
ROUNDING_S = 0.001


def sidecar_block(reasoning: Any, clip_end: Any, record: Any, drop_spans: Any, fps: Any, output: Any) -> dict:
    """EN3's evidence for the END an export rendered (codex-verdict-next-23 §3, EN3T; next-29 R1/R2, EN3Tr).

    What the scoring recorded (`reasoning.end_tail`), bound to the file this render produced. Five bindings:
    - `absent`: no record. Never rebuilt from today's setting.
    - `invalid_record`: a record the binding cannot rely on — an unknown `rule` or `state`, or the end that
      state implies (`end_out` for `moved`, `end_in` otherwise) or a time it carries not a finite number (a bool
      is not one). Not an edited end, and never `applies` because a number happens to match.
    - `end_changed_since_scoring`: the clip row ends elsewhere now — it was edited after the scoring. The row is
      read only for this; it never corroborates anything.
    - `not_corroborated` (`why`): the record stands and the row agrees, but the file does not certify it: the
      window the renderer executed (`render_record.window`, read off the argv) does not end at the recorded
      end, or the output's probed duration is unavailable or does not match within `tolerance_s`.
    - `applies`: the recorded end is the executed window's end AND the probed file's end. That certifies where
      the file ENDS — the container's duration, which follows its longer stream — not that the audio tail is
      there or sound to that end: a VFR source, another encoder or audio shorter than the video were never
      measured, and the video can hold the container open past the audio (codex-verdict-next-30 §2).
    `applies` on a `refused` or `kept` record means the recorded DECISION matches the rendered end — the end
    that decision left in place — not that an extension was applied; only `moved` extended anything.

    Times in `recorded` are the source clock, as scored. `delivered_s` (only on `applies`) is them on the
    file's clock: minus the executed start and the seconds this render dropped before them; None for a time
    inside a dropped span or outside `[0, delivered end]` — the next speech after the end is context, not an
    event of the file. `delivered_s.end` is that remap, computed; `measured_duration_s` is the probe, measured.
    Evidence, not recipe: `render_input` does not read it, so it stays outside the fingerprint, and no older
    sidecar is rewritten. A record that did not come from this clip's scoring says where it did come from in
    `reasoning.end_tail_provenance` (a frozen replay: the sidecar it was carried from); it is copied beside the
    record as `provenance`, and the binding is still computed here, on this render.
    """
    ev = reasoning.get("end_tail") if isinstance(reasoning, dict) else None
    if not isinstance(ev, dict):
        return {"binding": "absent", "recorded": None}
    prov = reasoning.get("end_tail_provenance")
    extra = {"provenance": prov} if isinstance(prov, dict) else {}
    final, why = _recorded_end(ev)
    if why is not None:
        return {"binding": "invalid_record", "why": why, "recorded": ev, **extra}
    if not _finite(clip_end) or abs(float(clip_end) - final) > 1e-3:
        return {"binding": "end_changed_since_scoring", "recorded": ev, "clock": "source_s", **extra}
    block: dict[str, Any] = {"binding": "not_corroborated", "why": None, "recorded": ev, "clock": "source_s",
                             "executed": None, "measured_duration_s": None, "tolerance_s": None, **extra}
    window = record.get("window") if isinstance(record, dict) else None
    ss, t = (window.get("ss"), window.get("t")) if isinstance(window, dict) else (None, None)
    spans = _spans(drop_spans)
    if not (_finite(ss) and _finite(t)) or spans is None:
        return {**block, "why": "executed_window_unavailable"}
    block["executed"] = {"start_s": float(ss), "duration_s": float(t)}
    end_d = _delivered(final, ss, spans)
    if end_d is None or abs(end_d - float(t)) > WINDOW_TOL_S:
        return {**block, "why": "executed_window_ends_elsewhere"}
    measured = output.get("duration_s") if isinstance(output, dict) and not output.get("refused") else None
    if not _finite(measured) or not _finite(fps) or not fps > 0:
        return {**block, "why": "output_probe_unavailable"}
    from services.clipper.ffmpeg_tools import AUDIO_RATE

    tol = 1.0 / float(fps) + (AAC_FRAME_SAMPLES / AUDIO_RATE if output.get("has_audio") else 0.0) + ROUNDING_S
    block.update(measured_duration_s=float(measured), tolerance_s=round(tol, 4))
    if abs(float(measured) - end_d) > tol:
        return {**block, "why": "output_duration_differs"}
    delivered = {k: _delivered(ev.get(k), ss, spans, limit=end_d) for k in ("vocal_end", "next_speech")}
    delivered["end"] = end_d
    return {**block, "binding": "applies", "delivered_s": delivered}


def _finite(x: Any) -> bool:
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)


def _recorded_end(ev: dict) -> tuple[float | None, str | None]:
    """(the end the record's state implies, None) or (None, why the record cannot be relied on)."""
    if ev.get("rule") != RULE:
        return None, "unknown_rule"
    if ev.get("state") not in STATES:
        return None, "unknown_state"
    final = ev.get("end_out") if ev["state"] == "moved" else ev.get("end_in")
    if not _finite(final):
        return None, "recorded_end_not_a_finite_number"
    if any(ev.get(k) is not None and not _finite(ev.get(k)) for k in ("vocal_end", "next_speech")):
        return None, "recorded_time_not_a_finite_number"
    return float(final), None


def _spans(spans: Any) -> list[tuple[float, float]] | None:
    """The executed drop spans as numbers, or None when one cannot be read."""
    out = []
    for span in spans or ():
        if not isinstance(span, (list, tuple)) or len(span) != 2 or not all(_finite(v) for v in span):
            return None
        out.append((float(span[0]), float(span[1])))
    return out


def _delivered(t: Any, start: float, spans: Sequence, limit: float | None = None) -> float | None:
    """A source time on the delivered file's clock: clip time minus the seconds dropped before it. None
    for no time, a time inside a dropped span, one before the start, or one past `limit` (the delivered end)."""
    if not _finite(t):
        return None
    x = float(t) - float(start)
    removed = 0.0
    for a, b in spans:
        if a < x < b:
            return None
        if b <= x:
            removed += b - a
    y = round(x - removed, 3)
    return None if x < 0 or (limit is not None and y > limit) else y
