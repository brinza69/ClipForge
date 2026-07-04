"""
ClipForge — Auto Story Doodle: Script Generator

Two-stage OpenAI script generation for faceless doodle-explainer videos:
  Stage 1 — full script: title, description, tags, complete narration
            (hook-first, cozy storytelling voice, no AI filler).
  Stage 2 — scene split: breaks narration into 2-4s scenes + one doodle
            image prompt per scene, batched at <= 25 scenes/call so long
            videos don't blow past a single completion.

Public entrypoint: `generate_storyboard(...)` — see contract in
PRPs/auto-story-doodle.md. Raises RuntimeError with a clear message on
failure (no key configured, OpenAI error, unusable response).

No image-generation API calls happen here — only OpenAI chat/completions
to produce TEXT (script + prompts).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional

import httpx

from services.doodle._script_normalize import (
    MAX_SCENE_DURATION,
    MIN_SCENE_DURATION,
    SUBTITLE_MAX_CHARS,
    WORDS_PER_MINUTE,
    normalize_scenes as _normalize_scenes,
    normalize_tags as _normalize_tags,
    split_narration_into_chunks as _split_narration_into_chunks,
)
from services.retry import with_retry
from services.transcript_cleaner import DEFAULT_OPENAI_MODEL, get_openai_key

logger = logging.getLogger("clipforge.doodle.script_generator")

ProgressCb = Optional[Callable[[float, str], Awaitable[None]]]

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# Scene batching: long videos can need 100+ scenes — split scene+prompt
# generation into batches so we never ask one completion for more than this.
MAX_SCENES_PER_BATCH = 25


# Style rules shared by every image prompt.
_DOODLE_STYLE_RULES = (
    "Every image_prompt must describe a simple hand-drawn doodle: white "
    "background, thick black imperfect marker lines, stick figures / arrows / "
    "timelines / big handwritten words where useful, basic colored highlights, "
    "cozy educational explainer style, minimal shapes. Explicitly state: "
    "\"no 3D, no realism, no cinematic lighting, no photorealism\". End every "
    "prompt with the aspect ratio tag. Prompts MUST vary scene-to-scene — "
    "alternate compositions such as a character doodle, a timeline, a map, a "
    "diagram, a labeled object, a before/after split, etc. Each prompt must "
    "match its narration specifically. Never depict copyrighted characters or "
    "real identifiable people. Only put text inside the image when it helps "
    "(a single big handwritten word/phrase, e.g. \"HUMAN HISTORY\")."
)

_SCRIPT_SYSTEM_PROMPT = (
    "You are a scriptwriter for faceless, retention-focused YouTube "
    "documentary/explainer videos (doodle-style). Your writing voice is "
    "natural, cozy, and conversational storytelling — never robotic. "
    "The script must open with a strong hook in the first sentence. "
    "Never use generic AI filler phrases such as 'in this video', "
    "'let's dive in', 'delve', 'in conclusion', or similar throat-clearing. "
    "Write in English only. Output strict JSON only, no commentary."
)

_SCENE_SYSTEM_PROMPT = (
    "You split narration scripts into short on-screen scenes for a "
    "hand-drawn doodle explainer video and write one image prompt per scene. "
    + _DOODLE_STYLE_RULES
    + " Output strict JSON only, no commentary."
)


def _niche_label(niche: str) -> str:
    return (niche or "general").strip() or "general"


def _target_word_count(target_duration_seconds: int) -> int:
    return max(30, round(target_duration_seconds / 60 * WORDS_PER_MINUTE))


def _resolve_frame_interval(frame_interval_seconds: Any) -> float:
    """'auto' -> 3.0 target; int/str numeric -> float; anything else -> 3.0."""
    if frame_interval_seconds == "auto" or frame_interval_seconds is None:
        return 3.0
    try:
        val = float(frame_interval_seconds)
    except (TypeError, ValueError):
        return 3.0
    return val if val > 0 else 3.0


# OpenAI call helper (httpx pattern per services/descriptions.py).
async def _call_openai_json(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    temperature: float,
) -> dict:
    """POST a chat/completions call requesting a JSON object response.

    Raises RuntimeError with a clear message on missing key / API failure /
    unparsable response.
    """
    key = get_openai_key()
    if not key:
        raise RuntimeError(
            "OpenAI API key not configured. Add one on the Settings page "
            "(or set OPENAI_API_KEY) before generating a doodle script."
        )

    payload = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    async def _call() -> dict:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                OPENAI_CHAT_URL,
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    try:
        data = await with_retry(_call, label="OpenAI doodle script")
    except httpx.HTTPStatusError as e:
        resp = e.response
        try:
            err = resp.json().get("error", {}).get("message") or resp.text
        except Exception:
            err = resp.text
        raise RuntimeError(f"OpenAI error {resp.status_code}: {str(err)[:400]}") from e
    except Exception as e:
        raise RuntimeError(f"OpenAI request failed: {e}") from e

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


# Stage 1 — full script (title, description, tags, narration)
async def _generate_full_script(
    *,
    mode: str,
    topic: Optional[str],
    script_text: Optional[str],
    niche: str,
    target_duration_seconds: int,
    model: str,
) -> dict:
    """Returns {"title","description","tags","narration"}."""
    niche_label = _niche_label(niche)
    target_words = _target_word_count(target_duration_seconds)

    if mode == "script":
        source = (script_text or "").strip()
        if not source:
            raise RuntimeError("mode='script' requires non-empty script_text")
        user_prompt = (
            f"Below is the user's own narration script for a faceless '{niche_label}' "
            f"doodle explainer video. Do NOT rewrite or rephrase the narration — keep "
            f"the user's wording intact, only apply light cleanup (fix obvious typos, "
            f"stray whitespace, punctuation). Then, from this narration, generate a "
            f"title, a short YouTube description, and 5-8 relevant tags.\n\n"
            f"Respond as strict JSON with this exact shape:\n"
            f'{{"title": "...", "description": "...", "tags": ["...", ...], '
            f'"narration": "..."}}\n\n'
            f"--- USER SCRIPT ---\n{source}"
        )
        temperature = 0.5
    else:
        topic_str = (topic or "").strip()
        if not topic_str:
            raise RuntimeError("mode='topic' requires non-empty topic")
        user_prompt = (
            f"Write a full narration script for a faceless, retention-focused "
            f"YouTube documentary/explainer video in the '{niche_label}' niche, "
            f"about: \"{topic_str}\".\n\n"
            f"Requirements:\n"
            f"- Open with a strong hook in the very first sentence.\n"
            f"- Natural, cozy, storytelling voice throughout — never robotic, "
            f"never generic AI filler ('in this video', 'let's dive in', 'delve', "
            f"'in conclusion').\n"
            f"- Target narration length: approximately {target_words} words "
            f"(this maps to about {target_duration_seconds} seconds spoken aloud).\n"
            f"- English only.\n"
            f"- Also produce a catchy YouTube title, a short description, and "
            f"5-8 relevant tags.\n\n"
            f"Respond as strict JSON with this exact shape:\n"
            f'{{"title": "...", "description": "...", "tags": ["...", ...], '
            f'"narration": "..."}}'
        )
        temperature = 0.8

    result = await _call_openai_json(
        _SCRIPT_SYSTEM_PROMPT, user_prompt, model=model, temperature=temperature
    )

    narration = str(result.get("narration") or "").strip()
    if not narration:
        raise RuntimeError("OpenAI script response had empty narration")

    return {
        "title": str(result.get("title") or "").strip(),
        "description": str(result.get("description") or "").strip(),
        "tags": _normalize_tags(result.get("tags")),
        "narration": narration,
    }


# Stage 2 — scene split + image prompts (batched)
async def _generate_scene_batch(
    *,
    narration_chunks: list[str],
    start_index: int,
    niche_label: str,
    aspect_ratio: str,
    prior_summary: str,
    model: str,
) -> list[dict]:
    """Ask the LLM to turn one batch of narration chunks into scenes with
    subtitle + image_prompt. Returns a list of raw (unvalidated) scene dicts
    in the same order as `narration_chunks`.
    """
    numbered = "\n".join(
        f"{start_index + i}: {chunk}" for i, chunk in enumerate(narration_chunks)
    )
    context = (
        f" Previously used compositions/prompts in this video (avoid repeating "
        f"the same composition back-to-back): {prior_summary}"
        if prior_summary
        else ""
    )

    user_prompt = (
        f"This is a '{niche_label}' faceless doodle explainer video, aspect "
        f"ratio {aspect_ratio}. Below is a numbered list of narration pieces, "
        f"each already the right length for ONE on-screen scene (2-4 seconds "
        f"of spoken narration). For EACH numbered piece, produce one scene "
        f"object with:\n"
        f"- index (the same number as given)\n"
        f"- narration (the narration text for that scene; you may lightly "
        f"polish wording for flow, but keep meaning and length essentially "
        f"unchanged)\n"
        f"- subtitle (a shortened version of the narration, at most "
        f"{SUBTITLE_MAX_CHARS} characters, ellipsis allowed)\n"
        f"- estimated_duration (seconds, a number between "
        f"{MIN_SCENE_DURATION} and {MAX_SCENE_DURATION}, based on word count "
        f"at ~{WORDS_PER_MINUTE}wpm)\n"
        f"- image_prompt (one hand-drawn doodle image prompt for this exact "
        f"scene)\n\n"
        f"{_DOODLE_STYLE_RULES}{context}\n\n"
        f"Respond as strict JSON with this exact shape:\n"
        f'{{"scenes": [{{"index": {start_index}, "narration": "...", '
        f'"subtitle": "...", "estimated_duration": 3.0, '
        f'"image_prompt": "..."}}, ...]}}\n\n'
        f"--- NUMBERED NARRATION PIECES ---\n{numbered}"
    )

    result = await _call_openai_json(
        _SCENE_SYSTEM_PROMPT, user_prompt, model=model, temperature=0.7
    )

    scenes = result.get("scenes")
    if not isinstance(scenes, list):
        raise RuntimeError("OpenAI scene batch response missing 'scenes' list")
    return scenes


def _prompt_summary(scenes: list[dict], max_items: int = 6) -> str:
    """Short summary of recent image prompts, for prompt-variety context."""
    tail = scenes[-max_items:]
    parts = []
    for s in tail:
        prompt = str(s.get("image_prompt") or "")
        parts.append(prompt[:60])
    return " | ".join(p for p in parts if p)


# Public entrypoint
async def generate_storyboard(
    *,
    mode: str,
    topic: Optional[str],
    script_text: Optional[str],
    niche: str,
    target_duration_seconds: int,
    frame_interval_seconds: Any,
    aspect_ratio: str,
    model: Optional[str] = None,
    progress_cb: ProgressCb = None,
) -> dict:
    """Generate a full storyboard draft (title/description/tags/scenes) from
    either a topic or a user-supplied script.

    Returns:
        {"title": str, "description": str, "tags": list[str],
         "scenes": [{"index","narration","subtitle","estimated_duration",
                     "image_prompt","flow_filename"}, ...]}

    Raises RuntimeError with a clear message if no OpenAI key is configured
    or the API call fails.
    """
    if mode not in ("topic", "script"):
        raise RuntimeError(f"Invalid mode: {mode!r} (expected 'topic' or 'script')")

    resolved_model = model or DEFAULT_OPENAI_MODEL
    niche_label = _niche_label(niche)
    frame_interval = _resolve_frame_interval(frame_interval_seconds)

    async def _report(fraction: float, message: str) -> None:
        if progress_cb:
            await progress_cb(fraction, message)

    await _report(0.05, "Writing full narration script...")

    full_script = await _generate_full_script(
        mode=mode,
        topic=topic,
        script_text=script_text,
        niche=niche_label,
        target_duration_seconds=target_duration_seconds,
        model=resolved_model,
    )

    await _report(0.3, "Splitting script into scenes...")

    narration_chunks = _split_narration_into_chunks(
        full_script["narration"], frame_interval
    )
    if not narration_chunks:
        raise RuntimeError("Narration produced no usable content to split into scenes")

    total_chunks = len(narration_chunks)
    batches = [
        narration_chunks[i : i + MAX_SCENES_PER_BATCH]
        for i in range(0, total_chunks, MAX_SCENES_PER_BATCH)
    ]

    raw_scenes: list[dict] = []
    for batch_num, batch in enumerate(batches):
        start_index = batch_num * MAX_SCENES_PER_BATCH
        prior_summary = _prompt_summary(raw_scenes)

        batch_scenes = await _generate_scene_batch(
            narration_chunks=batch,
            start_index=start_index,
            niche_label=niche_label,
            aspect_ratio=aspect_ratio,
            prior_summary=prior_summary,
            model=resolved_model,
        )
        raw_scenes.extend(batch_scenes)

        fraction = 0.3 + 0.6 * ((batch_num + 1) / len(batches))
        await _report(
            min(fraction, 0.9),
            f"Generated scenes {start_index + 1}-{start_index + len(batch)} "
            f"of {total_chunks}...",
        )

    scenes = _normalize_scenes(raw_scenes)
    if not scenes:
        raise RuntimeError("Scene generation produced no usable scenes")

    await _report(1.0, f"Script ready — {len(scenes)} scenes")

    return {
        "title": full_script["title"],
        "description": full_script["description"],
        "tags": full_script["tags"],
        "scenes": scenes,
    }
