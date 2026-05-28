"""
ClipForge — Caption Forced-Alignment

After TTS, we know *exactly* what the voice was supposed to say (cleaned_text)
but we don't know *when* each word lands in the rendered audio. Whisper on the
TTS audio gives us perfect word-level timestamps, but it occasionally mishears
(particularly on cleanly-articulated TTS with abbreviations, numbers, or
unusual names). So we combine both:

  - timing  ← faster-whisper word_timestamps on the voice file
  - spelling ← cleaned_text (the ground truth fed to the TTS)

Alignment is a classical sequence-match problem: difflib gives us matching
"blocks" between the two word streams; for matched words we use whisper's
timestamp; for cleaned-text words that whisper missed we interpolate
between the surrounding matched timestamps.

Public API:
    align_words(voice_path, cleaned_text)
        -> [{"word": "...", "start": float, "end": float}, ...]
    group_into_caption_chunks(words, words_per_chunk=4)
        -> [{"text": "...", "start": float, "end": float}, ...]
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("clipforge.caption_aligner")


# ── Token normalization (for fuzzy matching) ────────────────────────────────


_WORD_PUNCT_RE = re.compile(r"^\W+|\W+$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9ăâîșțĂÂÎȘȚ]+")


def _norm(w: str) -> str:
    """Lowercase, strip punctuation. Used only for matching, not for output."""
    w = (w or "").strip().lower()
    return _NON_ALNUM_RE.sub("", w)


def _tokenize_cleaned(text: str) -> List[str]:
    """Split prose into display tokens, preserving punctuation as the token's tail."""
    text = (text or "").strip()
    if not text:
        return []
    # Split on whitespace; punctuation stays attached so captions keep their look.
    return [t for t in re.split(r"\s+", text) if t]


# ── Whisper invocation (reuses services.transcriber) ────────────────────────


async def _whisper_words(voice_path: str) -> List[Dict[str, Any]]:
    """Run faster-whisper on the voice audio, return a flat word list."""
    from services.transcriber import transcribe

    result = await transcribe(str(voice_path))
    flat: List[Dict[str, Any]] = []
    for seg in result.get("segments", []):
        for w in seg.get("words") or []:
            wt = (w.get("word") or "").strip()
            if not wt:
                continue
            flat.append({
                "word": wt,
                "start": float(w.get("start") or 0.0),
                "end": float(w.get("end") or 0.0),
            })
    return flat


# ── Alignment ───────────────────────────────────────────────────────────────


def _align(
    whisper_words: List[Dict[str, Any]],
    cleaned_words: List[str],
    voice_duration: float,
) -> List[Dict[str, Any]]:
    """
    Match cleaned_words ↔ whisper_words, return one entry per cleaned_word
    with timing pulled from the matched whisper word (or interpolated when
    cleaned_words[i] has no match in whisper).
    """
    if not cleaned_words:
        return []
    if not whisper_words:
        # Whisper couldn't find anything (silent audio?). Fall back to
        # uniform spacing — same behaviour as the even-distribution path.
        return _uniform_layout(cleaned_words, voice_duration)

    w_norm = [_norm(w["word"]) for w in whisper_words]
    c_norm = [_norm(w) for w in cleaned_words]

    matcher = difflib.SequenceMatcher(a=w_norm, b=c_norm, autojunk=False)

    # `mapped[i]` will hold (start, end) for cleaned_words[i] when matched,
    # or None when whisper had no corresponding word.
    mapped: List[Optional[tuple]] = [None] * len(cleaned_words)
    for block in matcher.get_matching_blocks():
        # block = (a_idx, b_idx, size) — a is whisper, b is cleaned
        for k in range(block.size):
            wi = block.a + k
            ci = block.b + k
            w = whisper_words[wi]
            mapped[ci] = (w["start"], w["end"])

    # Interpolate timestamps for cleaned-text words whisper missed.
    # Walk forward, finding gaps between matched indices, and spread the
    # missing words uniformly across the time gap.
    matched_indices = [i for i, m in enumerate(mapped) if m is not None]
    if not matched_indices:
        return _uniform_layout(cleaned_words, voice_duration)

    # Fill anything BEFORE the first matched word.
    first = matched_indices[0]
    if first > 0:
        # No anchor on the left — pin the first cleaned words against t=0.
        t0 = 0.0
        t1 = mapped[first][0]
        step = (t1 - t0) / (first + 1)
        for i in range(first):
            mapped[i] = (t0 + step * i, t0 + step * (i + 1))

    # Fill between matched indices.
    for a, b in zip(matched_indices[:-1], matched_indices[1:]):
        if b - a <= 1:
            continue
        # Interpolate (b - a - 1) missing entries between mapped[a].end and mapped[b].start
        t0 = mapped[a][1]
        t1 = mapped[b][0]
        if t1 <= t0:
            # Whisper out-of-order or zero gap — pad by 0.05s per word.
            for i in range(a + 1, b):
                mapped[i] = (t0, t0 + 0.05)
                t0 += 0.05
            continue
        gap = b - a
        step = (t1 - t0) / gap
        for k, i in enumerate(range(a + 1, b), start=1):
            mapped[i] = (t0 + step * (k - 1), t0 + step * k)

    # Fill anything AFTER the last matched word.
    last = matched_indices[-1]
    if last < len(cleaned_words) - 1:
        t0 = mapped[last][1]
        t1 = max(voice_duration, t0 + 0.5)
        rem = len(cleaned_words) - 1 - last
        step = (t1 - t0) / rem
        for k, i in enumerate(range(last + 1, len(cleaned_words)), start=1):
            mapped[i] = (t0 + step * (k - 1), t0 + step * k)

    # Materialize as dicts with the cleaned-text spelling (NOT whisper's).
    out: List[Dict[str, Any]] = []
    for i, m in enumerate(mapped):
        if m is None:
            # Should be impossible now, but stay safe.
            t0 = (out[-1]["end"] if out else 0.0)
            m = (t0, t0 + 0.2)
        s, e = m
        out.append({
            "word": cleaned_words[i],
            "start": max(0.0, float(s)),
            "end": max(float(s) + 0.05, float(e)),
        })
    return out


def _uniform_layout(words: List[str], total: float) -> List[Dict[str, Any]]:
    """Even-distribution fallback when whisper gives us nothing usable."""
    if not words:
        return []
    per = total / len(words) if total > 0 else 0.3
    return [
        {"word": w, "start": i * per, "end": (i + 1) * per}
        for i, w in enumerate(words)
    ]


# ── Public entry points ─────────────────────────────────────────────────────


async def align_words(voice_path: str, cleaned_text: str) -> List[Dict[str, Any]]:
    """
    Returns: [{"word": str, "start": float (s), "end": float (s)}, ...]

    `word` is the cleaned-text spelling (preserves punctuation/case for the
    caption render), `start`/`end` come from whisper's word timestamps.
    """
    from services.speed_match import probe_duration

    cleaned_words = _tokenize_cleaned(cleaned_text)
    if not cleaned_words:
        return []

    duration = 0.0
    try:
        duration = probe_duration(voice_path)
    except Exception:
        pass

    whisper_words = await _whisper_words(voice_path)
    logger.info(
        f"caption_aligner: cleaned={len(cleaned_words)} words, "
        f"whisper={len(whisper_words)} words, voice={duration:.2f}s"
    )

    aligned = _align(whisper_words, cleaned_words, duration)
    return aligned


def group_into_caption_chunks(
    words: List[Dict[str, Any]],
    words_per_chunk: int = 4,
) -> List[Dict[str, Any]]:
    """
    Group word-level entries into caption-sized chunks. Respects sentence
    boundaries — a chunk closes early when the last word ends with .!?…

    Returns: [{"text": str, "start": float, "end": float}, ...]
    """
    if not words:
        return []
    chunks: List[Dict[str, Any]] = []
    buf: List[Dict[str, Any]] = []
    for w in words:
        buf.append(w)
        ends_sentence = bool(re.search(r"[.!?…][\"')\]]?$", w["word"]))
        if len(buf) >= words_per_chunk or (
            ends_sentence and len(buf) >= max(2, words_per_chunk - 1)
        ):
            chunks.append({
                "text": " ".join(b["word"] for b in buf),
                "start": buf[0]["start"],
                "end": buf[-1]["end"],
            })
            buf = []
    if buf:
        chunks.append({
            "text": " ".join(b["word"] for b in buf),
            "start": buf[0]["start"],
            "end": buf[-1]["end"],
        })
    # Guarantee no overlap (whisper sometimes emits identical adjacent timestamps).
    for i in range(1, len(chunks)):
        if chunks[i]["start"] < chunks[i - 1]["end"]:
            chunks[i]["start"] = chunks[i - 1]["end"]
        if chunks[i]["end"] <= chunks[i]["start"]:
            chunks[i]["end"] = chunks[i]["start"] + 0.1
    return chunks
