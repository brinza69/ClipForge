"""
ClipForge — Video Transformare TikTok: Romanian voice-over script generation.

generate_script() turns the chronological vision notes (produced by
services/tiktok_transform/vision.py::describe_frames) into the Romanian
TikTok voice-over script, per spec §7-8 of
clipforge_tiktok_transformation_prompt_v2.md: hook, initial place, start of
the work, stages, final result, question, CTA. Per D5 in
docs/clipforge-transformation-decisions.md, the vision notes are the ONLY
factual input for these clips (music-only, no whisper transcript) — the
prompt explicitly forbids inventing materials/costs/locations/durations.

Blocking functions (matches every tiktok_transform service — called from a
job handler via run_in_executor). Bridges to the shared async with_retry
helper via asyncio.run() since the retry-aware httpx call must be async.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Callable, Optional

import httpx

from services.retry import with_retry
from services.transcript_cleaner import DEFAULT_OPENAI_MODEL, get_openai_key

logger = logging.getLogger("clipforge.tiktok.script_gen")

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

ProgressCb = Optional[Callable[[float, str], None]]

_STYLE_HINTS = {
    "cinematic": "cinematic, epic, vivid and atmospheric tone",
    "misterios": "mysterious, suspenseful tone that teases what is being revealed",
    "energic": "energetic, fast-paced, excited tone",
    "documentar": "calm, documentary-narrator tone, matter-of-fact",
}

_COMMON_RULES = (
    "STRICT RULES:\n"
    "1. Never invent materials, costs, locations, durations, names, or any "
    "technical detail that is not already given to you.\n"
    "2. Natural, clear Romanian sentences with good voice-over rhythm. No "
    "lists, no headings, no markdown, no emoji.\n"
    "3. Output ONLY the final voice-over text — nothing before or after it, "
    "no preface, no explanation of what you did.\n"
)

_GENERATE_SYSTEM_PROMPT = (
    "You are a scriptwriter for viral Romanian-language TikTok voice-overs "
    "about physical transformations/builds (tiny house, bunker, hidden room, "
    "renovation, before/after, abandoned object turned into a livable space, "
    "etc). You receive chronological notes describing what is visible in the "
    "video's frames — these notes are your ONLY source of facts.\n\n" + _COMMON_RULES
)

_REGENERATE_SYSTEM_PROMPT = (
    "You are a scriptwriter editing an existing Romanian-language TikTok "
    "voice-over script about a physical transformation/build. You rewrite "
    "only the requested part and return the complete script.\n\n" + _COMMON_RULES
)


# ---------------------------------------------------------------------------
# OpenAI call helper (modelled on doodle/script_generator.py's
# _call_openai_json pattern — JSON-object response, with_retry, clear errors)
# ---------------------------------------------------------------------------

async def _call_openai_json(
    system_prompt: str, user_prompt: str, *, model: str, temperature: float,
) -> dict:
    key = get_openai_key()
    if not key:
        raise RuntimeError(
            "Nu este configurată o cheie API OpenAI. Adaugă una în Setări "
            "înainte de a genera scriptul."
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
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(
                OPENAI_CHAT_URL,
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    try:
        data = await with_retry(_call, label="OpenAI script")
    except httpx.HTTPStatusError as e:
        resp = e.response
        try:
            err = resp.json().get("error", {}).get("message") or resp.text
        except Exception:
            err = resp.text
        raise RuntimeError(f"Eroare OpenAI (cod {resp.status_code}): {str(err)[:300]}") from e
    except Exception as e:
        raise RuntimeError(f"Apelul către OpenAI a eșuat: {e}") from e

    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Răspunsul OpenAI nu conține conținut: {e}") from e

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OpenAI a răspuns cu JSON invalid: {e}") from e

    if not isinstance(parsed, dict):
        raise RuntimeError("Răspunsul OpenAI nu este un obiect JSON.")

    return parsed


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _notes_block(vision_notes: list[dict]) -> str:
    lines = []
    for n in sorted(vision_notes, key=lambda x: x.get("index", 0)):
        t = n.get("t")
        t_str = f" (t={t:.1f}s)" if isinstance(t, (int, float)) else ""
        lines.append(f"{n.get('index', 0)}{t_str}: {(n.get('note') or '').strip()}")
    return "\n".join(lines)


def _toggle_instructions(settings: dict) -> tuple[str, str, str]:
    """Returns (hook_instr, question_instr, cta_instr) from settings toggles."""
    include_no_scroll = bool(settings.get("include_no_scroll", True))
    include_question = bool(settings.get("include_question", True))
    include_cta = bool(settings.get("include_cta", True))
    cta_text = (settings.get("cta_text") or "").strip()

    hook_instr = (
        'Începe hook-ul cu o formulare de tip "nu da scroll" (ex: o variantă '
        'proprie a lui "Nu da scroll!"), sau o variantă apropiată de ea.'
        if include_no_scroll else
        'NU folosi o formulare de tip "nu da scroll" — folosește alt tip de '
        "hook (de exemplu teasing despre ce se ascunde/se descoperă, sau "
        "afirmarea faptului că transformarea este surprinzătoare)."
    )
    question_instr = (
        "Termină cu o întrebare scurtă pentru comentarii, chiar înainte de CTA."
        if include_question else
        "NU include nicio întrebare pentru comentarii."
    )
    if include_cta and cta_text:
        cta_instr = f'Termină scriptul cu acest text CTA exact, cuvânt cu cuvânt: "{cta_text}"'
    elif include_cta:
        cta_instr = "Termină cu un CTA scurt care invită la like și follow."
    else:
        cta_instr = "NU include niciun CTA."

    return hook_instr, question_instr, cta_instr


def _build_generate_prompt(vision_notes: list[dict], settings: dict, *, min_chars: int, max_chars: int) -> str:
    notes_block = _notes_block(vision_notes)
    style = settings.get("style") or "cinematic"
    style_hint = _STYLE_HINTS.get(style, _STYLE_HINTS["cinematic"])
    hook_instr, question_instr, cta_instr = _toggle_instructions(settings)
    target_chars = settings.get("target_chars", 1200)

    # Internal prompt per spec §8, adapted with the settings toggles from §7.
    return (
        "Analizează cronologic notele vizuale de mai jos (rezumatul vizual al "
        "videoclipului, în ordine cronologică).\n\n"
        "Scrie un voice-over TikTok în limba română, natural și ușor de "
        "urmărit.\n\n"
        "Cerințe:\n"
        f"- între {min_chars} și {max_chars} de caractere cu spații "
        f"(țintă recomandată: aproximativ {target_chars});\n"
        "- începe cu un hook puternic în prima propoziție;\n"
        "- descrie numai elementele vizibile în notele de mai jos;\n"
        "- nu inventa materiale, costuri, locații, durate sau alte detalii "
        "tehnice care nu apar în note;\n"
        "- urmează ordinea procesului așa cum apare în note;\n"
        "- pune accent pe transformare și pe reveal-ul final;\n"
        "- folosește propoziții clare și ritm bun pentru voice-over;\n"
        "- nu folosi liste;\n"
        "- nu folosi titluri;\n"
        "- nu include explicații înainte sau după transcript;\n"
        "- livrează exclusiv textul final.\n\n"
        f"Ton: {style_hint}.\n"
        f"{hook_instr}\n{question_instr}\n{cta_instr}\n\n"
        'Respond as strict JSON with this exact shape: {"text": "..."}\n\n'
        f"--- NOTE VIZUALE CRONOLOGICE ---\n{notes_block}"
    )


# ---------------------------------------------------------------------------
# Length guard — retry-then-trim-at-sentence-boundary, modelled on
# transcript_cleaner._is_bloated / _trim_to_ratio. Only ever trims from
# above (never pads from below — that would mean fabricating content).
# ---------------------------------------------------------------------------

_SENT_END_RE = re.compile(r"(?<=[.!?…])\s+")


def _trim_to_max_chars(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    sentences = _SENT_END_RE.split(text)
    kept: list[str] = []
    total = 0
    for s in sentences:
        add = len(s) + 1
        if total + add > max_chars and kept:
            break
        kept.append(s)
        total += add
    result = " ".join(kept).strip()
    if not result:
        cut = text[:max_chars]
        sp = cut.rfind(" ")
        result = (cut[:sp] if sp > 0 else cut).strip()
    return result


async def _generate_once(
    vision_notes: list[dict], settings: dict, min_chars: int, max_chars: int, model: str,
) -> str:
    prompt = _build_generate_prompt(vision_notes, settings, min_chars=min_chars, max_chars=max_chars)
    result = await _call_openai_json(_GENERATE_SYSTEM_PROMPT, prompt, model=model, temperature=0.7)
    text = str(result.get("text") or "").strip()
    if not text:
        raise RuntimeError("Răspunsul OpenAI pentru script a fost gol.")
    return text


async def _generate_script_async(
    vision_notes: list[dict], settings: dict, model: str, on_progress: ProgressCb,
) -> str:
    min_chars = int(settings.get("min_chars") or 1100)
    max_chars = int(settings.get("max_chars") or 1250)

    if on_progress:
        on_progress(0.1, "Se generează scriptul...")

    text = await _generate_once(vision_notes, settings, min_chars, max_chars, model)

    if len(text) > max_chars:
        logger.warning(
            f"tiktok script_gen: {len(text)} chars > max {max_chars} — retrying once"
        )
        if on_progress:
            on_progress(0.5, "Scriptul e prea lung, se regenerează...")
        retry_text = await _generate_once(vision_notes, settings, min_chars, max_chars, model)
        text = retry_text if len(retry_text) < len(text) else text
        if len(text) > max_chars:
            before = len(text)
            text = _trim_to_max_chars(text, max_chars)
            logger.warning(
                f"tiktok script_gen: still over cap after retry — hard-trimmed "
                f"{before} -> {len(text)} chars (cap {max_chars})"
            )
    elif len(text) < min_chars:
        logger.warning(
            f"tiktok script_gen: {len(text)} chars < min {min_chars} — retrying once"
        )
        if on_progress:
            on_progress(0.5, "Scriptul e prea scurt, se regenerează...")
        retry_text = await _generate_once(vision_notes, settings, min_chars, max_chars, model)
        if len(retry_text) > len(text):
            text = retry_text
        if len(text) < min_chars:
            # Cannot fabricate content just to hit the floor — keep the best
            # of the two attempts and let the UI show the real char count.
            logger.warning(
                f"tiktok script_gen: still under {min_chars} chars after retry "
                f"({len(text)}) — keeping as-is, will not invent content"
            )

    if on_progress:
        on_progress(0.9, "Se finalizează scriptul...")

    return text


def _build_result(text: str, settings: dict) -> dict:
    chars = len(text)
    words = len(text.split())
    tts_speed = float(settings.get("tts_speed") or 1.0) or 1.0
    # Romanian speech averages ~17 chars/second of audio at normal (1.0x)
    # pace; a faster TTS speed reads the same text in proportionally less
    # time, hence dividing by tts_speed. Matches D4's observed range (a
    # ~1200-char script at speed 1.1 lands at ~65-72s: 1200/17/1.1 ≈ 64s).
    estimated_duration = round((chars / 17.0) / tts_speed, 1)
    return {
        "text": text,
        "chars": chars,
        "words": words,
        "estimated_duration": estimated_duration,
    }


def generate_script(
    vision_notes: list[dict], settings: dict, *, on_progress: ProgressCb = None,
) -> dict:
    """Generate the Romanian TikTok voice-over script from chronological
    vision notes.

    Returns {"text","chars","words","estimated_duration"}.

    Raises RuntimeError with a Romanian-facing message if there are no vision
    notes, no OpenAI key is configured, or the API call ultimately fails
    (never falls back to a fabricated script).
    """
    if not vision_notes:
        raise RuntimeError(
            "Nu există note vizuale — rulează întâi analiza cadrelor (pasul "
            "Cadre) înainte de generarea scriptului."
        )

    logger.info(f"tiktok script_gen: generating script from {len(vision_notes)} vision note(s)")

    text = asyncio.run(
        _generate_script_async(vision_notes, settings, DEFAULT_OPENAI_MODEL, on_progress)
    )

    if on_progress:
        on_progress(1.0, "Script generat")

    return _build_result(text, settings)


# ---------------------------------------------------------------------------
# Partial regeneration — hook or final part only
# ---------------------------------------------------------------------------

def _build_regenerate_prompt(text: str, part: str, settings: dict) -> str:
    if part == "hook":
        instr = (
            "Rescrie DOAR hook-ul de deschidere (prima propoziție sau două) al "
            "scriptului TikTok românesc de mai jos, cu un hook nou, mai "
            "puternic. Păstrează restul scriptului exact la fel, cuvânt cu "
            "cuvânt. Nu inventa fapte noi — hook-ul trebuie să se potrivească "
            "în continuare cu restul scriptului."
        )
        include_no_scroll = bool(settings.get("include_no_scroll", True))
        instr += (
            ' Poți folosi o formulare de tip "nu da scroll".'
            if include_no_scroll else
            ' NU folosi o formulare de tip "nu da scroll".'
        )
    else:
        instr = (
            "Rescrie DOAR partea finală a scriptului TikTok românesc de mai "
            "jos — descrierea rezultatului final, întrebarea pentru "
            "comentarii și CTA-ul — cu o variantă nouă. Păstrează tot ce este "
            "înainte de această parte exact la fel, cuvânt cu cuvânt. Nu "
            "inventa fapte noi care nu apar deja mai devreme în script."
        )
        include_question = bool(settings.get("include_question", True))
        include_cta = bool(settings.get("include_cta", True))
        cta_text = (settings.get("cta_text") or "").strip()
        if not include_question:
            instr += " NU include nicio întrebare pentru comentarii."
        if include_cta and cta_text:
            instr += f' Termină cu acest CTA exact, cuvânt cu cuvânt: "{cta_text}"'
        elif not include_cta:
            instr += " NU include niciun CTA."

    return (
        f"{instr}\n\n"
        'Respond as strict JSON with this exact shape: {"text": "..."} '
        "where text is scriptul COMPLET (nu doar partea rescrisă).\n\n"
        f"--- SCRIPT ACTUAL ---\n{text}"
    )


async def _regenerate_part_async(text: str, part: str, settings: dict, model: str) -> str:
    prompt = _build_regenerate_prompt(text, part, settings)
    result = await _call_openai_json(_REGENERATE_SYSTEM_PROMPT, prompt, model=model, temperature=0.8)
    new_text = str(result.get("text") or "").strip()
    if not new_text:
        raise RuntimeError("Regenerarea a returnat un rezultat gol.")
    return new_text


def regenerate_part(text: str, part: str, settings: dict) -> str:
    """Rewrite only the hook or final part of an existing script, returning
    the FULL new text (unchanged parts included).

    part: "hook" | "final"
    """
    if part not in ("hook", "final"):
        raise ValueError(f"part must be 'hook' or 'final', got {part!r}")
    if not text or not text.strip():
        raise RuntimeError("Nu există text de regenerat.")

    logger.info(f"tiktok script_gen: regenerating part={part!r}")

    return asyncio.run(_regenerate_part_async(text.strip(), part, settings, DEFAULT_OPENAI_MODEL))
