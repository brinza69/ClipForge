"""
ClipForge — AI Stream Clipper headline (context hook).

A 3-10 word line that tells a scroller what they are about to watch. It is
strictly optional: an LLM improves it, but the pipeline must never stall or
fail because a provider is unconfigured, slow or down. Every path through
`generate_headline` returns a usable string.

The heuristic is extractive on purpose — it can only ever quote the clip's own
words, so it cannot fabricate a claim the video does not support.
"""

from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger("clipforge.clipper.headline")

MAX_WORDS = 10
# The LLM gets a little slack over MAX_WORDS before we throw its answer away —
# an 11-word headline is still usable, a 20-word one is a summary.
LLM_REJECT_WORDS = 12
LLM_TIMEOUT_S = 30.0

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_STRIP_EDGE_RE = re.compile(r"^[\s\"'“”„«»\-–—:;,.!?]+|[\s\"'“”„«»\-–—:;,.!?]+$")
_WS_RE = re.compile(r"\s+")

# Cues that mark a sentence as carrying the clip's tension rather than its
# setup. Lowercase, matched as substrings against a lowercased sentence.
_HOOK_CUES = (
    # English
    "never", "actually", "insane", "crazy", "unbelievable", "wait", "look",
    "watch", "secret", "nobody", "everyone", "why", "how", "because",
    "literally", "first time", "worst", "best", "can't believe", "turns out",
    # Romanian
    "niciodată", "incredibil", "nebunie", "stai", "uite", "secret", "nimeni",
    "toată lumea", "de ce", "cum", "pentru că", "prima dată", "chiar",
    "cel mai", "nu pot să cred", "se pare",
)


# ---------------------------------------------------------------------------
# Heuristic
# ---------------------------------------------------------------------------

def _clip_text(cand: dict) -> str:
    text = ((cand or {}).get("text") or "").strip()
    if text:
        return _WS_RE.sub(" ", text)
    words = (cand or {}).get("words") or []
    return _WS_RE.sub(" ", " ".join(str(w.get("word") or "") for w in words)).strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _tidy(sentence: str) -> str:
    """Trim to <= MAX_WORDS, drop edge punctuation, capitalise the first word.

    Only the FIRST character is touched: title-casing every word would wreck
    Romanian ("Nu Pot Să Cred") and lowercasing would wreck proper nouns.
    Diacritics are never stripped — .upper() on "ă" gives "Ă".
    """
    s = _STRIP_EDGE_RE.sub("", _WS_RE.sub(" ", sentence or ""))
    if not s:
        return ""
    words = s.split(" ")
    if len(words) > MAX_WORDS:
        s = " ".join(words[:MAX_WORDS])
        s = _STRIP_EDGE_RE.sub("", s)
    if not s:
        return ""
    return s[0].upper() + s[1:]


def heuristic_headline(cand: dict, language: str) -> str:
    """The clip's strongest sentence, trimmed to a headline.

    Order: first question -> shortest cue-bearing sentence in the opening third
    -> first sentence. `language` is unused: the rules are punctuation- and
    cue-based, and the cue list already covers both EN and RO.
    """
    text = _clip_text(cand)
    if not text:
        return ""

    sents = _sentences(text)
    if not sents:
        return _tidy(text)

    for s in sents:
        if s.rstrip().endswith("?"):
            return _tidy(s)

    third = max(1, len(sents) // 3)
    cue_bearing = [
        s for s in sents[:third]
        if any(cue in s.lower() for cue in _HOOK_CUES)
    ]
    if cue_bearing:
        return _tidy(min(cue_bearing, key=len))

    return _tidy(sents[0])


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

def _language_name(code: str) -> str:
    try:
        from services.transcript_cleaner import LANGUAGE_NAMES
    except Exception:  # pragma: no cover - only if the module moves
        return code or "the clip's language"
    return LANGUAGE_NAMES.get((code or "").lower(), code or "the clip's language")


def _prompt(text: str, language: str) -> str:
    # Every rule lives in the USER message because the shared caller owns the
    # system prompt (it targets 1-3 sentence descriptions, so the length rule
    # has to be stated loudly here and is re-checked by _accept afterwards).
    return (
        f"Write ONE headline of 3 to 10 words for the video clip below, in "
        f"{_language_name(language)}.\n\n"
        "RULES:\n"
        "- 3 to 10 words. Not a sentence, not a summary, not a paragraph.\n"
        "- Only state what the transcript actually supports. Never invent a "
        "number, name, outcome or claim.\n"
        "- No clickbait that misstates the content.\n"
        "- No emoji, no hashtags, no quotation marks, no trailing period.\n"
        "- Do not simply repeat the clip's opening words — give the context "
        "they are missing.\n"
        "- Output the headline alone, nothing else.\n\n"
        f"--- CLIP TRANSCRIPT ---\n{text[:4000]}"
    )


async def _llm_headline(cand: dict, engine: str, language: str) -> str:
    """Ask the configured provider for a headline. Raises on any failure.

    Reuses `descriptions._call_llm`, the repo's existing ollama/openai/anthropic
    caller, rather than opening a fourth HTTP path. The transcript_cleaner
    engine functions can't serve here — they hard-code the cleaning prompt.
    An unknown engine raises there, which lands in the caller's fallback.
    """
    from services.descriptions import _call_llm

    text = _clip_text(cand)
    if not text:
        raise RuntimeError("candidate has no transcript text")
    return await _call_llm(engine, _prompt(text, language))


def _normalise(text: str) -> str:
    return _WS_RE.sub(" ", re.sub(r"[^\w\s]", "", (text or "").lower())).strip()


def _repeats_opening(headline: str, cand: dict) -> bool:
    """True when the headline is just the clip's own opening words."""
    norm_head = _normalise(headline)
    if not norm_head:
        return True
    clip = _clip_text(cand)
    if _normalise(_sentences(clip)[0] if _sentences(clip) else clip) == norm_head:
        return True
    opening = " ".join(_normalise(clip).split(" ")[: len(norm_head.split(" "))])
    return opening == norm_head


def _accept(raw: str, cand: dict) -> str:
    """Sanitise the model's answer, or return "" to force the fallback."""
    # Salvage order: whole answer -> first line -> first sentence. A model that
    # ignored "output the headline alone" usually still put the headline first,
    # and a clean first line beats discarding the call entirely. Anything still
    # too long is a summary, not a headline, and is dropped.
    lines = [ln for ln in (raw or "").splitlines() if ln.strip()]
    attempts = [raw or ""]
    if lines:
        attempts.append(lines[0])
        attempts.append(_sentences(lines[0])[0] if _sentences(lines[0]) else lines[0])

    text = ""
    for attempt in attempts:
        cleaned = _STRIP_EDGE_RE.sub("", _WS_RE.sub(" ", attempt.replace("\n", " ")))
        if cleaned and len(cleaned.split(" ")) <= LLM_REJECT_WORDS:
            text = cleaned
            break
    if not text:
        logger.info("headline: rejected over-long LLM answer (%d chars)", len(raw or ""))
        return ""
    if _repeats_opening(text, cand):
        logger.info("headline: LLM answer repeats the clip opening, falling back")
        return ""
    return text[0].upper() + text[1:]


async def generate_headline(cand: dict, *, engine: str | None, language: str) -> dict:
    """{"text": str, "source": "llm" | "heuristic"} — never raises.

    An optional provider must not be able to fail the clip, so every LLM error,
    timeout and malformed answer degrades to the extractive headline.
    """
    fallback = heuristic_headline(cand, language)
    if not engine:
        return {"text": fallback, "source": "heuristic"}

    try:
        raw = await asyncio.wait_for(
            _llm_headline(cand, str(engine), language), timeout=LLM_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("headline: %s timed out after %.0fs", engine, LLM_TIMEOUT_S)
        return {"text": fallback, "source": "heuristic"}
    except Exception as exc:
        logger.warning("headline: %s failed (%s), using heuristic", engine, exc)
        return {"text": fallback, "source": "heuristic"}

    text = _accept(raw, cand)
    if not text:
        return {"text": fallback, "source": "heuristic"}
    return {"text": text, "source": "llm"}


__all__ = ["generate_headline", "heuristic_headline", "MAX_WORDS"]
