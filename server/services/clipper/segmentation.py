"""
ClipForge — Pass B semantic segmentation for the AI Stream Clipper.

Turns a word-timestamped transcript plus the Pass A signal timeline into
variable-length semantic windows. The clipper transcribes with
`keep_punctuation=True`, so sentence-final punctuation is the primary and most
reliable boundary signal; pauses, scene cuts, silence-run edges and audio-peak
onsets refine it.

Nothing here emits fixed-size chunks. A window grows from a sentence start
until it is at least `min_s` long AND can close on a real boundary, capped at
`max_s`. That is the whole point of Pass B: a 30-second slice that ends
mid-thought is worthless no matter how good the score is.

Import-safe: no DB, no network, no ffmpeg.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger("clipforge.clipper.segmentation")

# A gap this long between two words reads as a deliberate break, not breathing.
PAUSE_BOUNDARY_S = 0.6
# Without punctuation a gap this long is treated as a sentence break outright.
LONG_PAUSE_S = 1.2
# Runaway guard for transcripts where the model never emits a full stop.
SENTENCE_MAX_S = 30.0
# A pause alone must not end a sentence ON one of these. They are words nobody
# stops after: the thought is demonstrably unfinished, whatever the microphone
# heard. Measured on a real stream, the pause rule was closing sentences on
# "let's", "oh" and "what", so clips ended one word before the payoff — "...of
# water bro let's" | "go", "...trail chamber oh" | "my god". A speaker CAN pause
# after these mid-thought; that is exactly the case being protected.
_CONTINUES = frozenset("""
a an the my your his her its our their this that these those
to of in on at for with from by about into onto over under
and or but so because if when while than as
is are was were be been being am do does did have has had
can could will would shall should may might must
let's i'm it's we're you're they're don't can't won't didn't isn't
i'll we'll you'll i've we've gonna wanna gotta
very really just more most too also even still
oh what how why who which where whose
""".split())
# ...unless the silence is long enough that he plainly stopped anyway.
HARD_PAUSE_S = 2.5
_CONTINUE_STRIP_RE = re.compile(r"^[^\w']+|[^\w']+$", re.UNICODE)
# Boundaries closer together than this are the same event seen twice.
MERGE_TOLERANCE_S = 0.20

_SENTENCE_END_CHARS = ".!?…"
_TRAILING_CHARS = "\"'”’)]}»"
_STRIP_CHARS = _TRAILING_CHARS + "“„«([{,;:-—–" + _SENTENCE_END_CHARS

# Romanian is folded onto ASCII so word lists stay short and match text typed
# with or without diacritics (the rig produces both).
_FOLD = str.maketrans(
    {"ă": "a", "â": "a", "î": "i", "ș": "s", "ş": "s",
     "ț": "t", "ţ": "t", "à": "a", "é": "e", "è": "e"}
)


# ---------------------------------------------------------------------------
# Small shared helpers (candidates.py imports these)
# ---------------------------------------------------------------------------

def norm_token(word: str) -> str:
    """Lowercase, drop punctuation, fold Romanian diacritics onto ASCII."""
    return (word or "").strip().lower().translate(_FOLD).strip(_STRIP_CHARS)


def overlap_seconds(t0: float, t1: float, intervals: Iterable[Any]) -> float:
    """Total seconds of [t0,t1) covered by a list of [start,end] intervals."""
    if t1 <= t0:
        return 0.0
    total = 0.0
    for iv in intervals or ():
        a, b = _interval(iv)
        if b <= t0 or a >= t1:
            continue
        total += min(b, t1) - max(a, t0)
    return total


def points_in(t0: float, t1: float, points: Iterable[Any]) -> list[float]:
    """Timestamps from a point list that fall inside [t0,t1]."""
    out: list[float] = []
    for p in points or ():
        t = _as_float(p if not isinstance(p, dict) else p.get("t"))
        if t is not None and t0 <= t <= t1:
            out.append(t)
    return out


def signal_view(signals: dict | None) -> dict:
    """Normalise `analysis/signals.json` into one flat accessor dict.

    build_signals() merges the output of four independent probes, so the same
    series can plausibly arrive flat (`rms`) or nested (`audio.rms`). Reading
    through one adapter means a shape change upstream degrades to empty lists
    instead of a KeyError in the middle of a two-hour analysis.
    """
    sig = signals if isinstance(signals, dict) else {}
    audio = sig.get("audio") if isinstance(sig.get("audio"), dict) else {}
    motion_blob = sig.get("motion")
    motion_d = motion_blob if isinstance(motion_blob, dict) else {}

    motion = motion_d.get("motion") if motion_d else motion_blob
    rms = sig.get("rms") or sig.get("audio_rms") or audio.get("rms") or []

    return {
        "rms": [_as_float(v) or 0.0 for v in rms if _as_float(v) is not None],
        "rms_hop": _as_float(audio.get("hop_s") or sig.get("hop_s")
                             or sig.get("audio_hop_s")) or 0.25,
        "peaks": sorted(_floats(sig.get("peaks") or audio.get("peaks"))),
        "silence": _intervals(sig.get("silence") or audio.get("silence")),
        "speech": _intervals(sig.get("speech") or audio.get("speech")),
        "scenes": sorted(_floats(sig.get("scenes") or sig.get("scene_cuts"))),
        "motion": [_as_float(v) or 0.0 for v in (motion or [])
                   if _as_float(v) is not None],
        "motion_hop": _as_float(motion_d.get("hop_s")
                                or sig.get("motion_hop_s")) or 0.5,
        # Read only from the motion blob, never from a top-level "ui": the
        # series is sliced with motion_hop, and it shares that hop only because
        # motion_timeline emits both from one decode pass. A loose top-level
        # key would be sliced against a hop it never agreed to.
        "ui": [_as_float(v) or 0.0 for v in (motion_d.get("ui") or [])
               if _as_float(v) is not None],
        "faces": [f for f in (sig.get("faces") or []) if isinstance(f, dict)],
    }


def series_slice(values: list[float], hop: float, t0: float, t1: float) -> list[float]:
    """Samples of a hop-spaced series that fall inside [t0,t1]."""
    if not values or hop <= 0 or t1 <= t0:
        return []
    i0 = max(0, int(t0 / hop))
    i1 = min(len(values), int(t1 / hop) + 1)
    return values[i0:i1] if i1 > i0 else []


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN check


def _floats(values: Any) -> list[float]:
    out = []
    for v in values or ():
        f = _as_float(v if not isinstance(v, dict) else v.get("t"))
        if f is not None:
            out.append(f)
    return out


def _interval(iv: Any) -> tuple[float, float]:
    if isinstance(iv, dict):
        a, b = _as_float(iv.get("start")), _as_float(iv.get("end"))
    elif isinstance(iv, (list, tuple)) and len(iv) >= 2:
        a, b = _as_float(iv[0]), _as_float(iv[1])
    else:
        return (0.0, 0.0)
    if a is None or b is None or b < a:
        return (0.0, 0.0)
    return (a, b)


def _intervals(values: Any) -> list[tuple[float, float]]:
    out = [_interval(v) for v in values or ()]
    return sorted(iv for iv in out if iv[1] > iv[0])


# ---------------------------------------------------------------------------
# Words and sentences
# ---------------------------------------------------------------------------

def word_list(transcript: dict) -> list[dict]:
    """Flatten every segment's words into one time-sorted list.

    Segments without word timestamps get their text spread evenly across the
    segment — the same fallback `captioner_events._extract_clip_words` uses, so
    a transcript from a model without word timings still segments sensibly
    instead of collapsing to zero words.
    """
    segments = (transcript or {}).get("segments") or []
    out: list[dict] = []

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        s0, s1 = _as_float(seg.get("start")), _as_float(seg.get("end"))
        words = seg.get("words") or []
        seg_conf = _as_float(seg.get("confidence"))

        if words:
            for w in words:
                if not isinstance(w, dict):
                    continue
                ws, we = _as_float(w.get("start")), _as_float(w.get("end"))
                text = (w.get("word") or "").strip()
                if ws is None or we is None or not text:
                    continue
                out.append({
                    "word": text,
                    "start": ws,
                    "end": max(we, ws),
                    "probability": _as_float(w.get("probability")),
                })
            continue

        tokens = (seg.get("text") or "").split()
        if not tokens or s0 is None or s1 is None or s1 <= s0:
            continue
        step = (s1 - s0) / len(tokens)
        for i, tok in enumerate(tokens):
            out.append({
                "word": tok,
                "start": s0 + i * step,
                "end": s0 + (i + 1) * step,
                "probability": seg_conf,
            })

    out.sort(key=lambda w: (w["start"], w["end"]))
    return out


def sentences_from_words(words: list[dict]) -> list[dict]:
    """Group words into sentences. Adds `i0`/`i1` word indices for slicing."""
    if not words:
        return []

    sentences: list[dict] = []
    start_i = 0

    def _close(end_i: int) -> None:
        chunk = words[start_i:end_i + 1]
        if not chunk:
            return
        sentences.append({
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "text": " ".join(w["word"] for w in chunk),
            "i0": start_i,
            "i1": end_i,
        })

    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 0.0
        span = w["end"] - words[start_i]["start"]

        close = _ends_sentence(w["word"])
        if not close and nxt and gap >= LONG_PAUSE_S:
            # Punctuation is authoritative; a pause is only evidence. Don't let
            # it close the sentence on a word the speaker cannot have meant to
            # end on, unless the silence is long enough to settle the question.
            close = gap >= HARD_PAUSE_S or not _continues(w["word"])
        if not close and span >= SENTENCE_MAX_S and gap >= PAUSE_BOUNDARY_S:
            close = True

        if close or nxt is None:
            _close(i)
            start_i = i + 1

    return sentences


def sentence_spans(transcript: dict) -> list[dict]:
    """Public Pass B view of sentences: [{'start','end','text'}]."""
    return [
        {"start": s["start"], "end": s["end"], "text": s["text"]}
        for s in sentences_from_words(word_list(transcript))
    ]


def _continues(token: str) -> bool:
    """Is this a word the speaker obviously has not finished a thought on?

    Strips punctuation from both ends but keeps the apostrophe, because the
    contractions are half the list — "let's" without it is "lets".
    """
    return _CONTINUE_STRIP_RE.sub("", (token or "").lower()) in _CONTINUES


def _ends_sentence(token: str) -> bool:
    stripped = (token or "").rstrip(_TRAILING_CHARS)
    if not stripped or stripped[-1] not in _SENTENCE_END_CHARS:
        return False
    # "J." / "3." are initials and list markers, not the end of a thought.
    return len(stripped.rstrip(_SENTENCE_END_CHARS)) > 1


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------

def boundaries(transcript: dict, signals: dict | None) -> list[dict]:
    """Every cut-worthy instant, merged and sorted: [{'t','strength','reasons'}]."""
    words = word_list(transcript)
    return _boundaries(words, sentences_from_words(words), signal_view(signals))


def _boundaries(words: list[dict], sents: list[dict], sv: dict) -> list[dict]:
    raw: list[tuple[float, float, str]] = []

    for s in sents:
        raw.append((s["end"], 1.0, "sentence_end"))

    for a, b in zip(words, words[1:]):
        gap = b["start"] - a["end"]
        if gap >= PAUSE_BOUNDARY_S:
            # Longer pause = stronger break, but flatten out past ~3 s: a
            # 30-second dead patch is not 10x the boundary a 3-second one is.
            strength = 0.6 + min(gap, 3.0) / 3.0 * 0.6
            raw.append((a["end"], strength, f"pause_{gap:.1f}s"))

    for t in sv["scenes"]:
        raw.append((t, 0.8, "scene_cut"))
    for a, b in sv["silence"]:
        raw.append((a, 0.5, "silence_start"))
        raw.append((b, 0.5, "silence_end"))
    for t in sv["peaks"]:
        raw.append((t, 0.3, "audio_peak"))

    if not raw:
        return []

    raw.sort(key=lambda r: r[0])
    merged: list[dict] = []
    for t, strength, reason in raw:
        if merged and t - merged[-1]["t"] <= MERGE_TOLERANCE_S:
            group = merged[-1]
            group["strength"] = min(3.0, group["strength"] + strength)
            if reason not in group["reasons"]:
                group["reasons"].append(reason)
            # A sentence end is the cleanest instant in the group; snap to it.
            if reason == "sentence_end":
                group["t"] = t
            continue
        merged.append({"t": t, "strength": strength, "reasons": [reason]})
    return merged


# ---------------------------------------------------------------------------
# Semantic windows
# ---------------------------------------------------------------------------

def semantic_windows(transcript: dict, signals: dict, *,
                     min_s: float, max_s: float) -> list[dict]:
    """Variable-length windows anchored on sentence starts.

    Each window opens at a sentence start and closes on the strongest boundary
    inside [start+min_s, start+max_s]. Consecutive windows overlap by design —
    a moment that straddles the hop would otherwise never be a candidate.
    """
    lo = max(1.0, float(min_s or 0) or 15.0)
    hi = max(lo + 1.0, float(max_s or 0) or 90.0)

    words = word_list(transcript)
    if not words:
        return []
    sents = sentences_from_words(words)
    if not sents:
        return []

    sv = signal_view(signals)
    bounds = _boundaries(words, sents, sv)
    last_end = words[-1]["end"]
    sweet_spot = (lo + hi) / 2.0

    # Everything the speaker said fits in one window: emit it and stop.
    if last_end - sents[0]["start"] <= lo:
        return [_window(words, sents[0]["start"], last_end,
                        ["start_sentence", "transcript_end"], bounds)]

    windows: list[dict] = []
    i = 0
    while i < len(sents):
        start = sents[i]["start"]
        if last_end - start < lo:
            break

        pick = _close_point(bounds, start + lo, min(start + hi, last_end), sweet_spot, start)
        if pick:
            end, reasons = pick
        else:
            end = min(start + hi, last_end)
            reasons = ["max_window"]

        end = _snap_word_end(words, end)
        if end - start >= lo:
            windows.append(_window(words, start, end, reasons, bounds))

        hop = max(lo * 0.5, (end - start) * 0.6)
        nxt = _next_sentence(sents, i, start + hop)
        if nxt <= i:
            break
        i = nxt

    logger.info("Pass B: %d semantic windows from %d sentences", len(windows), len(sents))
    return windows


def _close_point(bounds: list[dict], lo_t: float, hi_t: float,
                 sweet_spot: float, start: float) -> tuple[float, list[str]] | None:
    """Strongest boundary in range; ties break toward the sweet-spot length."""
    best: dict | None = None
    best_key = (-1.0, 0.0)
    for b in bounds:
        if b["t"] < lo_t:
            continue
        if b["t"] > hi_t:
            break
        key = (b["strength"], -abs((b["t"] - start) - sweet_spot))
        if key > best_key:
            best, best_key = b, key
    return (best["t"], list(best["reasons"])) if best else None


def _snap_word_end(words: list[dict], t: float) -> float:
    """Never end mid-word: pull back to the end of the word covering `t`."""
    for w in words:
        if w["start"] < t < w["end"]:
            return w["end"] if w["end"] - t < t - w["start"] else w["start"]
        if w["start"] > t:
            break
    return t


def _next_sentence(sents: list[dict], i: int, at_least: float) -> int:
    for j in range(i + 1, len(sents)):
        if sents[j]["start"] >= at_least:
            return j
    return i + 1 if i + 1 < len(sents) else i


def _window(words: list[dict], start: float, end: float,
            close_reasons: list[str], bounds: list[dict]) -> dict:
    span = [w for w in words if w["start"] >= start - 1e-6 and w["end"] <= end + 1e-6]
    opener = "start_sentence"
    for b in bounds:
        if abs(b["t"] - start) <= MERGE_TOLERANCE_S:
            strong = next((r for r in b["reasons"] if r != "sentence_end"), b["reasons"][0])
            opener = f"start_{strong}"
            break
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "text": " ".join(w["word"] for w in span),
        "words": span,
        "reasons": [opener] + close_reasons,
    }
