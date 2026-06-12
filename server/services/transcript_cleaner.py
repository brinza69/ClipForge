"""
ClipForge — Transcript Cleaner / Translator

Turns a raw transcript (timestamped lines, ASR fragments, etc) into
clean, coherent prose, optionally translated into a target language.

Backends (engine pills in the UI):
  - ollama       — local, free, runs against http://localhost:11434
  - openai       — gpt-4o-mini by default, uses data/transcript_config.json key
  - anthropic    — claude-haiku-4-5 by default, uses data/transcript_config.json key

Design rules (per user):
  * "Coherent and makes sense" — output is prose, not bullet list, not summary
  * "Almost the length of the original" — preserve content, do not condense
  * Chunked: long transcripts split into ~1500-word pieces and joined
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger("clipforge.transcript_cleaner")


# ---------------------------------------------------------------------------
# Config / API key storage  (mirrors elevenlabs.py)
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    from config import settings
    return Path(settings.data_dir) / "transcript_config.json"


def _read_config() -> dict:
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Could not parse transcript_config.json")
        return {}


def _write_config(cfg: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _resolve_key(env_var: str, cfg_key: str) -> Optional[str]:
    v = os.environ.get(env_var)
    if v and v.strip():
        return v.strip()
    cfg = _read_config()
    cv = cfg.get(cfg_key)
    if cv and isinstance(cv, str) and cv.strip():
        from services.secret_storage import decrypt
        return (decrypt(cv.strip()) or "").strip() or None
    return None


def get_openai_key() -> Optional[str]:
    return _resolve_key("OPENAI_API_KEY", "openai_api_key")


def get_anthropic_key() -> Optional[str]:
    return _resolve_key("ANTHROPIC_API_KEY", "anthropic_api_key")


def set_openai_key(key: str) -> None:
    cfg = _read_config()
    if key and key.strip():
        from services.secret_storage import encrypt
        cfg["openai_api_key"] = encrypt(key.strip())
    else:
        cfg.pop("openai_api_key", None)
    _write_config(cfg)


def set_anthropic_key(key: str) -> None:
    cfg = _read_config()
    if key and key.strip():
        from services.secret_storage import encrypt
        cfg["anthropic_api_key"] = encrypt(key.strip())
    else:
        cfg.pop("anthropic_api_key", None)
    _write_config(cfg)


# ---------------------------------------------------------------------------
# Ollama probe
# ---------------------------------------------------------------------------

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
DEFAULT_OLLAMA_MODEL = os.environ.get("CLIPFORGE_OLLAMA_MODEL", "qwen2.5:7b-instruct")


async def ollama_status() -> dict:
    """Returns {running, models: [...], hint}."""
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            if r.status_code != 200:
                return {"running": False, "models": [], "hint": f"Ollama at {OLLAMA_BASE} returned {r.status_code}"}
            data = r.json()
            models = [m.get("name") for m in data.get("models", []) if m.get("name")]
            return {"running": True, "models": models, "hint": None}
    except Exception as e:
        return {
            "running": False,
            "models": [],
            "hint": f"Ollama not reachable at {OLLAMA_BASE}. Install from ollama.com then `ollama pull {DEFAULT_OLLAMA_MODEL}`.",
        }


# ---------------------------------------------------------------------------
# Input parsers — txt / srt / vtt / whisper-json
# ---------------------------------------------------------------------------

_TS_PATTERN = re.compile(
    r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]?\d*\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]?\d*",
)
_LEADING_TS = re.compile(
    r"^\s*\[?\(?\d{1,2}:\d{2}(?::\d{2})?[.,]?\d*\]?\)?[\s\-:]*",
)
_INDEX_LINE = re.compile(r"^\s*\d+\s*$")
_TAG = re.compile(r"<[^>]+>")
_BRACKETED_NOISE = re.compile(r"\[(?:music|laughter|applause|inaudible|sound|noise)[^\]]*\]", re.I)


def parse_transcript(raw: str, filename: str = "") -> str:
    """Strip timestamps / index lines / cue tags, return a single block of text.

    Handles .srt, .vtt, plain timestamped lines (`[00:01:23] foo bar`),
    and pre-cleaned text (returned unchanged).
    """
    if not raw:
        return ""

    name = (filename or "").lower()

    # Whisper JSON: {"segments": [{"text": "..."}, ...]} or {"text": "..."}
    if name.endswith(".json") or raw.lstrip().startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                if isinstance(obj.get("segments"), list):
                    return " ".join((s.get("text") or "").strip() for s in obj["segments"]).strip()
                if isinstance(obj.get("text"), str):
                    return obj["text"].strip()
        except Exception:
            pass  # fall through

    out_lines: List[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.upper().startswith("WEBVTT"):
            continue
        if _TS_PATTERN.match(s):
            continue
        if _INDEX_LINE.match(s):
            continue
        s = _LEADING_TS.sub("", s)
        s = _TAG.sub("", s)
        s = _BRACKETED_NOISE.sub("", s)
        s = s.strip()
        if s:
            out_lines.append(s)
    text = " ".join(out_lines)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Chunking — split on sentence boundaries, ~1500 words per chunk
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[\.!?])\s+(?=[A-ZĂÂÎȘȚ])")


def chunk_text(text: str, max_words: int = 1500) -> List[str]:
    text = text.strip()
    if not text:
        return []
    words = text.split()
    if len(words) <= max_words:
        return [text]

    sentences = _SENT_SPLIT.split(text)
    chunks: List[str] = []
    buf: List[str] = []
    buf_words = 0
    for sent in sentences:
        sw = len(sent.split())
        if buf_words + sw > max_words and buf:
            chunks.append(" ".join(buf).strip())
            buf, buf_words = [sent], sw
        else:
            buf.append(sent)
            buf_words += sw
    if buf:
        chunks.append(" ".join(buf).strip())
    return chunks


# ---------------------------------------------------------------------------
# Prompt — single source of truth for all engines
# ---------------------------------------------------------------------------

LANGUAGE_NAMES = {
    "en": "English", "ro": "Romanian", "es": "Spanish", "fr": "French",
    "de": "German", "it": "Italian", "pt": "Portuguese", "nl": "Dutch",
    "pl": "Polish", "tr": "Turkish", "ru": "Russian", "uk": "Ukrainian",
    "ar": "Arabic", "hi": "Hindi", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese (Simplified)", "cs": "Czech", "hu": "Hungarian",
    "el": "Greek", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    "no": "Norwegian", "bg": "Bulgarian", "sk": "Slovak", "sr": "Serbian",
    "hr": "Croatian", "vi": "Vietnamese", "id": "Indonesian", "th": "Thai",
    "he": "Hebrew",
}

SYSTEM_PROMPT = (
    "You are a transcript editor. You receive raw speech transcripts that are "
    "fragmented, contain filler words, false starts, and inconsistent punctuation. "
    "Your job is to turn them into clean, coherent prose that a reader can enjoy.\n\n"
    "Strict rules:\n"
    "1. Preserve ALL meaning. Do not summarize, condense, or skip content.\n"
    "2. The output length must be close to the input length (within ~15%).\n"
    "3. Fix punctuation, capitalize properly, break into natural paragraphs.\n"
    "4. Remove obvious filler (uh, um, like, you know) and false starts.\n"
    "5. Do NOT invent facts or add information that was not in the original.\n"
    "6. Output ONLY the cleaned text. NO preface, NO headings, NO commentary, "
    "NO explanation of your edits, NO horizontal rules (---), NO repeated versions. "
    "Do not write the text twice. Do not say 'Here is...' or 'Iată...' or "
    "'This version...' or 'Acesta este...'. Just emit the cleaned prose once, "
    "and stop."
)


# ----------------------------------------------------------------------------
# Post-processing to undo common model misbehavior (works around small LLMs
# that ignore "no commentary" instructions — qwen2.5:7b in particular).
# ----------------------------------------------------------------------------

# Markdown horizontal rules the model inserts before a meta-commentary section.
_MD_HR_RE = re.compile(r"\n\s*[-–—_*]{3,}\s*\n")

# Phrases that signal "I'm about to explain what I just did" — anything after
# them is junk the TTS should never read. Multilingual.
_META_HEADERS = [
    # English
    "here is the cleaned",
    "here's the cleaned",
    "this is the cleaned",
    "this version",
    "the cleaned version",
    "note:",
    "explanation:",
    # Romanian
    "iată textul",
    "iată versiunea",
    "iată variată",
    "acesta este un text",
    "aceasta este versiunea",
    "pentru a fi mai natural",
    "textul ar putea fi ajustat",
    "respectă toate cerin",
    "rămâne aproape la lungimea",
    # Generic structural
    "translated version:",
    "cleaned version:",
]


def _strip_meta_commentary(text: str) -> str:
    """Remove meta-commentary that small LLMs leak into the output."""
    if not text:
        return text

    # 1. Hard cut at the first markdown horizontal rule. Everything after `---`
    #    is, in qwen2.5's pattern, either a "natural Romanian version" rewrite
    #    or a closing remark. Either way, not transcript prose.
    parts = _MD_HR_RE.split(text, maxsplit=1)
    head = parts[0]

    # 2. Trim a trailing block that starts with a meta-header sentence.
    #    Walk the text once looking for the EARLIEST meta-header occurrence
    #    that has at least one paragraph of "real" content before it.
    lower = head.lower()
    earliest = -1
    for needle in _META_HEADERS:
        idx = lower.find(needle)
        if idx > 0 and (earliest == -1 or idx < earliest):
            earliest = idx
    if earliest > 0:
        head = head[:earliest]

    # 3. Trim trailing punctuation/whitespace noise.
    head = head.rstrip(" \n\t-–—_*:")

    return head.strip()


def _user_prompt(text: str, target_language: Optional[str]) -> str:
    if target_language and target_language != "auto":
        lang_name = LANGUAGE_NAMES.get(target_language, target_language)
        instr = (
            f"Clean up the transcript below and translate the result into {lang_name}. "
            f"The final output must be in {lang_name} and read naturally as if originally written "
            f"in that language. Keep all meaning intact and stay close to the original length.\n\n"
            f"--- TRANSCRIPT ---\n{text}"
        )
    else:
        instr = (
            "Clean up the transcript below into coherent prose in its original language. "
            "Keep all meaning intact and stay close to the original length.\n\n"
            f"--- TRANSCRIPT ---\n{text}"
        )
    return instr


# ---------------------------------------------------------------------------
# Engine calls
# ---------------------------------------------------------------------------

async def _call_ollama(text: str, target_language: Optional[str], model: str) -> str:
    payload = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(text, target_language)},
        ],
    }
    from services.retry import with_retry

    async def _call() -> dict:
        async with httpx.AsyncClient(timeout=600.0) as client:
            r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            r.raise_for_status()
            return r.json()

    # Local engine — retry only twice (a 5xx usually means the model crashed;
    # one retry is enough, more just delays a clear failure).
    try:
        data = await with_retry(_call, max_attempts=2, label="Ollama chat")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Ollama error {e.response.status_code}: {e.response.text[:300]}")
    msg = (data.get("message") or {}).get("content") or ""
    return _strip_meta_commentary(msg)


async def _call_openai(text: str, target_language: Optional[str], model: str) -> str:
    key = get_openai_key()
    if not key:
        raise RuntimeError("OpenAI API key not configured")
    payload = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(text, target_language)},
        ],
    }
    from services.retry import with_retry

    async def _call() -> dict:
        async with httpx.AsyncClient(timeout=600.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    try:
        data = await with_retry(_call, label="OpenAI chat")
    except httpx.HTTPStatusError as e:
        resp = e.response
        try:
            err = resp.json().get("error", {}).get("message") or resp.text
        except Exception:
            err = resp.text
        raise RuntimeError(f"OpenAI error {resp.status_code}: {str(err)[:300]}")
    return _strip_meta_commentary(data["choices"][0]["message"]["content"] or "")


async def _call_anthropic(text: str, target_language: Optional[str], model: str) -> str:
    key = get_anthropic_key()
    if not key:
        raise RuntimeError("Anthropic API key not configured")
    payload = {
        "model": model,
        "max_tokens": 8192,
        "temperature": 0.3,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _user_prompt(text, target_language)}],
    }
    from services.retry import with_retry

    async def _call() -> dict:
        async with httpx.AsyncClient(timeout=600.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    try:
        data = await with_retry(_call, label="Anthropic messages")
    except httpx.HTTPStatusError as e:
        resp = e.response
        try:
            err = resp.json().get("error", {}).get("message") or resp.text
        except Exception:
            err = resp.text
        raise RuntimeError(f"Anthropic error {resp.status_code}: {str(err)[:300]}")
    parts = data.get("content", [])
    out = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return _strip_meta_commentary(out)


# ---------------------------------------------------------------------------
# Verify API key by listing models  (used by /key POST to reject bad keys)
# ---------------------------------------------------------------------------

async def verify_openai_key() -> dict:
    key = get_openai_key()
    if not key:
        raise RuntimeError("No key set")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        if r.status_code == 401:
            raise RuntimeError("Key rejected by OpenAI (401)")
        if r.status_code != 200:
            raise RuntimeError(f"OpenAI status {r.status_code}")
    return {"ok": True}


async def verify_anthropic_key() -> dict:
    key = get_anthropic_key()
    if not key:
        raise RuntimeError("No key set")
    # cheapest verification: 1-token completion
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        if r.status_code == 401:
            raise RuntimeError("Key rejected by Anthropic (401)")
        if r.status_code not in (200, 400):
            raise RuntimeError(f"Anthropic status {r.status_code}: {r.text[:200]}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Public entrypoint — clean (+ optionally translate) a full transcript
# ---------------------------------------------------------------------------

DEFAULT_OPENAI_MODEL = os.environ.get("CLIPFORGE_OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_ANTHROPIC_MODEL = os.environ.get("CLIPFORGE_ANTHROPIC_MODEL", "claude-haiku-4-5")


async def clean_transcript(
    raw_text: str,
    engine: str,
    target_language: Optional[str] = None,
    *,
    source_filename: str = "",
    model: Optional[str] = None,
    progress_cb=None,
) -> str:
    """Returns the cleaned (and optionally translated) prose."""
    parsed = parse_transcript(raw_text, source_filename)
    if not parsed:
        raise RuntimeError("Transcript appears empty after parsing")

    chunks = chunk_text(parsed, max_words=1500)
    logger.info(f"transcript_cleaner: {len(chunks)} chunk(s), {len(parsed.split())} words, engine={engine}")

    out_parts: List[str] = []
    for i, chunk in enumerate(chunks):
        if progress_cb:
            progress_cb(i, len(chunks))
        if engine == "ollama":
            piece = await _call_ollama(chunk, target_language, model or DEFAULT_OLLAMA_MODEL)
        elif engine == "openai":
            piece = await _call_openai(chunk, target_language, model or DEFAULT_OPENAI_MODEL)
        elif engine == "anthropic":
            piece = await _call_anthropic(chunk, target_language, model or DEFAULT_ANTHROPIC_MODEL)
        else:
            raise RuntimeError(f"Unknown engine: {engine}")
        out_parts.append(piece)

    if progress_cb:
        progress_cb(len(chunks), len(chunks))
    return "\n\n".join(out_parts).strip()
