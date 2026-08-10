"""
ClipForge — Video Transformare TikTok: TikTok description generation (step 8).

Reuses the LLM-call plumbing pattern from services/descriptions.py (OpenAI
chat/completions, same key storage via services.transcript_cleaner), but:
  - is a BLOCKING function (contract: def, not async def — called via
    run_in_executor by the job handler, same as every other tiktok_transform
    service), so it uses a sync httpx.Client instead of AsyncClient.
  - uses a NEW system prompt, because services.descriptions._SYSTEM_PROMPT
    explicitly forbids hashtags and this feature requires them.
  - always writes in Romanian and asks for strict JSON (same idiom as
    services/doodle/script_generator.py's _call_openai_json).

Source of truth for the caption content is the project's OWN generated
script / vision notes (never invented) — see D5 in
docs/clipforge-transformation-decisions.md: the source clips have no
narration, so anything not grounded in the vision-pass notes or the
generated script would be a fabricated fact.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

import httpx

from services.transcript_cleaner import (
    DEFAULT_OPENAI_MODEL,
    _strip_meta_commentary,
    get_openai_key,
)

logger = logging.getLogger("clipforge.tiktok.description")

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

VARIANTS = ("short", "normal", "engagement", "no_tags")

# Per-variant shape of the request. "no_tags" still gets a normal-length
# caption — only the hashtags are suppressed (per contract: "no_tags returns
# none", i.e. no hashtags, not a shorter caption).
_VARIANT_SPECS = {
    "short": {"sentences": "1-2", "tags": "5-7", "tone": "concis și direct la subiect"},
    "normal": {"sentences": "2-4", "tags": "6-10", "tone": "cald, descriptiv, cinematic"},
    "engagement": {
        "sentences": "2-4",
        "tags": "6-10",
        "tone": "jucăuș, care invită explicit privitorul să-și spună părerea sau reacția",
    },
    "no_tags": {"sentences": "2-4", "tags": "0", "tone": "cald, descriptiv, cinematic"},
}

_SYSTEM_PROMPT = (
    "You write short, catchy Romanian captions for TikTok 'transformation' "
    "videos (before/after reveals, cleaning, restorations, DIY, makeovers). "
    "Base the caption ONLY on the information given to you in the user "
    "message — never invent facts, places, people, prices, or details that "
    "are not present in that input. Always write the caption in Romanian, "
    "with correct diacritics. The caption must end with a question that "
    "invites the viewer to react or comment. When hashtags are requested, "
    "pick ones genuinely relevant to the content — a mix of Romanian and "
    "common TikTok English tags is fine (e.g. satisfying, transformare, "
    "inaintesidupa) — return them as plain words/phrases without a leading "
    "'#' and without internal spaces; the caller adds the '#' itself. "
    "Respond with STRICT JSON only, no commentary, no markdown fences."
)

_TAG_CLEAN_RE = re.compile(r"[^\w]+", re.UNICODE)


def _gather_context(project: dict) -> str:
    """Pull the only factual inputs this project has: its own generated
    script, or (failing that) the raw vision notes, or the source video's
    own title/description. Returns "" if none exist — the caller must not
    call the LLM with nothing to ground it in."""
    script = project.get("script") or {}
    script_text = (script.get("text") or "").strip()
    if script_text:
        return script_text

    notes = script.get("vision_notes") or []
    note_lines = [
        str(n.get("note")).strip()
        for n in notes
        if isinstance(n, dict) and n.get("note") and str(n.get("note")).strip()
    ]
    if note_lines:
        return "Note cronologice despre cadrele video-ului:\n" + "\n".join(f"- {n}" for n in note_lines)

    source = project.get("source") or {}
    parts = [str(source.get("title") or "").strip(), str(source.get("description") or "").strip()]
    parts = [p for p in parts if p]
    if parts:
        return "\n".join(parts)

    return ""


def _build_user_prompt(context: str, variant: str) -> str:
    spec = _VARIANT_SPECS[variant]
    tag_instr = (
        'Do not produce any hashtags — return "hashtags": [].'
        if spec["tags"] == "0"
        else f"Also produce {spec['tags']} relevant hashtags."
    )
    return (
        f"Write a {spec['sentences']}-sentence Romanian TikTok caption "
        f"(tone: {spec['tone']}) describing the transformation shown in the "
        f"video content below. {tag_instr} The caption text must end with a "
        f"question.\n\n"
        f"Respond as strict JSON with this exact shape:\n"
        f'{{"text": "...", "hashtags": ["...", ...]}}\n\n'
        f"--- VIDEO CONTENT (script / notes — do not invent beyond this) ---\n"
        f"{context[:4000]}"
    )


def _call_openai_json_sync(
    system_prompt: str,
    user_prompt: str,
    *,
    api_key: str,
    model: str,
    temperature: float = 0.6,
    max_attempts: int = 3,
) -> dict:
    """Blocking JSON-mode OpenAI chat/completions call with a small retry
    loop for transient errors. Mirrors services/doodle/script_generator.py's
    _call_openai_json, but synchronous (this module's contract is a plain
    `def`, not `async def`)."""
    payload = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}

    data: Optional[dict] = None
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            with httpx.Client(timeout=120.0) as client:
                r = client.post(OPENAI_CHAT_URL, headers=headers, json=payload)
            if r.status_code in _RETRYABLE_STATUS and attempt < max_attempts:
                logger.warning(f"OpenAI description call: HTTP {r.status_code} on attempt {attempt}, retrying")
                time.sleep(1.5 * attempt)
                continue
            r.raise_for_status()
            data = r.json()
            break
        except httpx.HTTPStatusError as e:
            try:
                err = e.response.json().get("error", {}).get("message") or e.response.text
            except Exception:
                err = e.response.text
            raise RuntimeError(f"OpenAI error {e.response.status_code}: {str(err)[:400]}") from e
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            last_exc = e
            if attempt >= max_attempts:
                raise RuntimeError(f"OpenAI request failed: {e}") from e
            time.sleep(1.5 * attempt)

    if data is None:
        raise RuntimeError(f"OpenAI request failed after {max_attempts} attempts: {last_exc}")

    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"OpenAI response missing content: {e}") from e

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OpenAI returned unparsable JSON: {e}") from e

    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI JSON response was not an object")
    return parsed


def _format_hashtags(raw) -> list[str]:
    """Normalize LLM-returned tags into '#Tag' strings, deduped, capped at 10."""
    out: list[str] = []
    seen: set[str] = set()
    for t in raw or []:
        if not isinstance(t, str):
            continue
        cleaned = _TAG_CLEAN_RE.sub("", t.strip().lstrip("#"))
        if not cleaned:
            continue
        tag = "#" + cleaned
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out[:10]


def generate_description(project: dict, variant: str = "normal") -> dict:
    """Generate a Romanian TikTok caption + hashtags for the project.

    variant: "short" | "normal" | "engagement" | "no_tags" ("no_tags" omits
    hashtags entirely; the caption itself is still full-length).
    Returns {"text": str, "hashtags": list[str]}.
    Raises RuntimeError/ValueError on missing key, missing content, or any
    upstream failure — never fabricates a description.
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown description variant {variant!r}; expected one of {VARIANTS}")

    context = _gather_context(project)
    if not context:
        raise RuntimeError(
            f"project {project.get('id')} has no script, vision notes, or source "
            f"metadata to describe — run script generation (step 3) first"
        )

    api_key = get_openai_key()
    if not api_key:
        raise RuntimeError(
            "OpenAI API key not configured. Add one on the Settings page "
            "(or set OPENAI_API_KEY) before generating a TikTok description."
        )

    user_prompt = _build_user_prompt(context, variant)
    result = _call_openai_json_sync(
        _SYSTEM_PROMPT, user_prompt, api_key=api_key, model=DEFAULT_OPENAI_MODEL
    )

    text = _strip_meta_commentary(str(result.get("text") or "").strip())
    if not text:
        raise RuntimeError("OpenAI description response had empty text")

    hashtags = [] if variant == "no_tags" else _format_hashtags(result.get("hashtags"))

    logger.info(f"tiktok project {project.get('id')}: description variant={variant!r} ({len(text)} chars, {len(hashtags)} tags)")
    return {"text": text, "hashtags": hashtags}
