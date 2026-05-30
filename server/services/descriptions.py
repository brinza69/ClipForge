"""
ClipForge — Video Descriptions

Generates two short descriptions for a remix run:

  1. ``original_translated`` — the source platform's description (TikTok /
     YouTube / etc) rewritten in the user's target language.
  2. ``ai_generated`` — a fresh 1–3 sentence description written by the LLM
     from the transcript content.

Runs as the final stage of the remix pipeline (after caption burn /
commentator). Both calls go through the same engine the user picked for
transcript cleaning (ollama / openai / anthropic), so no extra config.

Failure mode: a failed call returns an empty string for that variant —
the field is still present in the response so the frontend can render
gracefully.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from services.transcript_cleaner import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OPENAI_MODEL,
    LANGUAGE_NAMES,
    OLLAMA_BASE,
    _strip_meta_commentary,
    get_anthropic_key,
    get_openai_key,
)

logger = logging.getLogger("clipforge.descriptions")


_SYSTEM_PROMPT = (
    "You write short, punchy social-media video descriptions (1–3 sentences, "
    "no hashtags unless they appear in the source, no emoji unless natural). "
    "Output ONLY the description text — no preface, no labels, no quotes."
)


async def _call_llm(
    engine: str,
    user: str,
    model: Optional[str] = None,
) -> str:
    """Multi-engine LLM call with the descriptions system prompt.
    Returns text with meta-commentary stripped."""
    if engine == "ollama":
        payload = {
            "model": model or DEFAULT_OLLAMA_MODEL,
            "stream": False,
            "options": {"temperature": 0.5, "num_ctx": 8192},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            if r.status_code != 200:
                raise RuntimeError(f"Ollama error {r.status_code}: {r.text[:300]}")
            data = r.json()
        return _strip_meta_commentary((data.get("message") or {}).get("content") or "")

    if engine == "openai":
        key = get_openai_key()
        if not key:
            raise RuntimeError("OpenAI API key not configured")
        payload = {
            "model": model or DEFAULT_OPENAI_MODEL,
            "temperature": 0.5,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json=payload,
            )
            if r.status_code != 200:
                raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:300]}")
            data = r.json()
        return _strip_meta_commentary(data["choices"][0]["message"]["content"] or "")

    if engine == "anthropic":
        key = get_anthropic_key()
        if not key:
            raise RuntimeError("Anthropic API key not configured")
        payload = {
            "model": model or DEFAULT_ANTHROPIC_MODEL,
            "max_tokens": 1024,
            "temperature": 0.5,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}],
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            if r.status_code != 200:
                raise RuntimeError(f"Anthropic error {r.status_code}: {r.text[:300]}")
            data = r.json()
        parts = data.get("content", [])
        return _strip_meta_commentary(
            "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        )

    raise RuntimeError(f"Unknown engine: {engine}")


async def generate_video_descriptions(
    original_description: str,
    transcript: str,
    engine: str,
    target_language: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """Produce two video descriptions for the remix result.

    Returns ``{"original_translated": str, "ai_generated": str}``. Either
    field may be empty if the upstream call fails or the input is missing.
    """
    lang_name = (
        LANGUAGE_NAMES.get(target_language, target_language)
        if target_language and target_language != "auto"
        else None
    )
    lang_instr = f" Write in {lang_name}." if lang_name else ""

    out = {"original_translated": "", "ai_generated": ""}

    # 1. Translate / rewrite the source description (if we have one).
    src = (original_description or "").strip()
    if src:
        prompt = (
            f"Rewrite the following video description as a clean, natural "
            f"social-media caption.{lang_instr} Preserve the meaning; do not "
            f"invent new facts. If the source is mostly hashtags or junk, "
            f"return a single short sentence summarising the video.\n\n"
            f"--- SOURCE DESCRIPTION ---\n{src}"
        )
        try:
            out["original_translated"] = await _call_llm(engine, prompt, model)
        except Exception as e:
            logger.warning(f"description (original→translated) failed: {e}")

    # 2. Generate a fresh description from the transcript.
    tx = (transcript or "").strip()
    if tx:
        # First ~3000 chars is plenty for a 1–3 sentence hook.
        tx_snippet = tx[:3000]
        prompt = (
            f"Write a short, engaging video description (1–3 sentences) "
            f"based on the transcript below.{lang_instr} Make it catchy but "
            f"truthful to the content. Do not invent facts or add hashtags.\n\n"
            f"--- TRANSCRIPT ---\n{tx_snippet}"
        )
        try:
            out["ai_generated"] = await _call_llm(engine, prompt, model)
        except Exception as e:
            logger.warning(f"description (ai_generated) failed: {e}")

    return out
