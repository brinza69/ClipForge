"""
ClipForge — Video Transformare TikTok: OpenAI vision frame descriptions.

describe_frames() sends the selected/candidate frames to an OpenAI vision
model in small batches (6-8 images per call, "low" detail — we only need to
know the stage/materials/structure visible, not pixel-level detail) and asks
for exactly ONE short factual sentence per frame.

This is the ONLY source of visual truth for script_gen.generate_script(): per
docs/clipforge-transformation-decisions.md (D5), these clips have no
narration (music only), so faster-whisper yields nothing. Without vision the
script model would have to invent materials/costs/locations, which spec §7
forbids — so a vision failure must raise a clear error, never degrade to a
fabricated note.

Blocking function (matches every tiktok_transform service — called from a job
handler via run_in_executor). Bridges to the shared async with_retry helper
via asyncio.run() since the retry-aware httpx call must be async.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

import httpx

from services.retry import with_retry
from services.transcript_cleaner import get_openai_key

logger = logging.getLogger("clipforge.tiktok.vision")

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_VISION_MODEL = os.environ.get("CLIPFORGE_VISION_MODEL", "gpt-4o-mini")

# ~6-8 images per call per spec — keeps each completion small and fast while
# still giving the model enough neighbouring frames for context.
BATCH_SIZE = 7

ProgressCb = Optional[Callable[[float, str], None]]

_SYSTEM_PROMPT = (
    "You are a visual analyst describing frames extracted from a video of a "
    "physical transformation/build (tiny house, bunker, hidden room, "
    "renovation, before/after, etc). For EACH numbered frame, write exactly "
    "ONE short, factual sentence describing ONLY what is visibly present: "
    "materials, structure, and the stage of work shown. Never guess, "
    "speculate, or invent anything not directly visible in the image — no "
    "costs, no locations, no names, no reasons, no future or past events. If "
    "a frame is unclear or ambiguous, describe only what can be confidently "
    "seen (e.g. 'A wooden frame partially built, surroundings unclear.'). "
    "Respond with strict JSON only, no commentary."
)


def _encode_frame(path: str) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")


def _user_content(batch: list[dict]) -> list[dict]:
    """Build the mixed text/image content array for one batch."""
    parts: list[dict] = [{
        "type": "text",
        "text": (
            "Describe each of the following frames, in the order given. "
            "Respond as strict JSON: "
            '{"notes": [{"index": <int>, "note": "..."}, ...]} '
            "with exactly one entry per frame, using the SAME index values "
            "given below for each frame."
        ),
    }]
    for frame in batch:
        t = frame.get("t")
        t_str = f", t={t:.1f}s" if isinstance(t, (int, float)) else ""
        parts.append({
            "type": "text",
            "text": f"Frame index {frame['index']}{t_str}:",
        })
        parts.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{_encode_frame(frame['file'])}",
                "detail": "low",
            },
        })
    return parts


async def _call_batch(batch: list[dict], model: str, key: str) -> dict:
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(batch)},
        ],
    }

    async def _call() -> dict:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                OPENAI_CHAT_URL,
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    try:
        data = await with_retry(_call, label="OpenAI vision")
    except httpx.HTTPStatusError as e:
        resp = e.response
        try:
            err = resp.json().get("error", {}).get("message") or resp.text
        except Exception:
            err = resp.text
        raise RuntimeError(
            f"Analiza vizuală OpenAI a eșuat (cod {resp.status_code}): {str(err)[:300]}"
        ) from e
    except Exception as e:
        raise RuntimeError(f"Apelul către OpenAI (analiză vizuală) a eșuat: {e}") from e

    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Răspunsul OpenAI (analiză vizuală) nu conține conținut: {e}") from e

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OpenAI (analiză vizuală) a răspuns cu JSON invalid: {e}") from e

    if not isinstance(parsed, dict) or not isinstance(parsed.get("notes"), list):
        raise RuntimeError("Răspunsul OpenAI (analiză vizuală) nu are forma așteptată ('notes').")

    return parsed


async def _describe_frames_async(
    frames: list[dict], model: str, key: str, on_progress: ProgressCb,
) -> list[dict]:
    batches = [frames[i : i + BATCH_SIZE] for i in range(0, len(frames), BATCH_SIZE)]
    out_by_index: dict[int, str] = {}
    missing = 0

    for b_num, batch in enumerate(batches):
        result = await _call_batch(batch, model, key)
        for item in result.get("notes", []):
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            note = str(item.get("note") or "").strip()
            if note:
                out_by_index[idx] = note

        if on_progress:
            fraction = (b_num + 1) / len(batches)
            first = b_num * BATCH_SIZE + 1
            last = min((b_num + 1) * BATCH_SIZE, len(frames))
            on_progress(
                fraction,
                f"Analiză vizuală: cadrele {first}-{last} din {len(frames)}...",
            )

    # A frame the model skipped gets an honest "no description" placeholder —
    # never a fabricated guess. script_gen simply has nothing factual to draw
    # on for that frame.
    notes: list[dict] = []
    for frame in frames:
        idx = frame["index"]
        note = out_by_index.get(idx)
        if note is None:
            missing += 1
            note = "Cadru fără descriere disponibilă."
        notes.append({"index": idx, "t": frame.get("t", 0.0), "note": note})

    if missing:
        logger.warning(f"tiktok vision: {missing}/{len(frames)} frame(s) got no note from the model")

    return notes


def describe_frames(
    frames: list[dict], *, model: str | None = None, on_progress: ProgressCb = None,
) -> list[dict]:
    """Describe each selected frame with one short factual Romanian/English
    sentence, in batches, via an OpenAI vision model.

    frames: [{"index","t","file"}] (chronological) -> [{"index","t","note"}]

    Raises RuntimeError with a Romanian-facing message if no OpenAI key is
    configured, or if the API call ultimately fails (never falls back to a
    fabricated script).
    """
    if not frames:
        return []

    key = get_openai_key()
    if not key:
        raise RuntimeError(
            "Nu este configurată o cheie API OpenAI. Adaugă una în Setări "
            "înainte de a genera analiza vizuală și scriptul."
        )

    resolved_model = model or DEFAULT_VISION_MODEL
    logger.info(
        f"tiktok vision: describing {len(frames)} frame(s) in batches of "
        f"{BATCH_SIZE}, model={resolved_model}"
    )

    if on_progress:
        on_progress(0.0, "Se analizează cadrele...")

    notes = asyncio.run(_describe_frames_async(frames, resolved_model, key, on_progress))

    if on_progress:
        on_progress(1.0, f"Analiză vizuală completă — {len(notes)} cadre descrise")

    return notes
