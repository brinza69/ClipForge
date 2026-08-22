"""
ClipForge — AI Stream Clipper: how the clipper talks to a language model.

The plumbing, split from llm_select.py when the tracing added in Batch 0 took
that file past the repo's 500-line limit. The seam is real: everything here is
about REACHING a model and recording what happened — which engines were tried,
which failed, what fell back. What to ASK it, and what to do with the answer,
stays next door.

Nothing here raises. A clipper run must not die because Ollama is down or a key
expired; it falls back to the heuristic ranking, which is what shipped before
any of this existed. That is also why the trace matters: with failures
swallowed, an unrecorded attempt is an attempt nobody can ever prove happened.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Sequence

logger = logging.getLogger("clipforge.clipper.llm_select")

_SYSTEM = (
    "You pick moments from livestream transcripts that work as standalone "
    "short-form clips. You judge what HAPPENS, not how loud it is. "
    "Answer only with the JSON asked for — no preface, no code fence, no prose."
)

_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)

# Engines tried in order, and the two limits on what the judge is shown. They
# live HERE rather than in llm_select because llm_judge needs them and
# llm_select re-exports llm_judge at its own bottom: with the constants up
# there, importing llm_judge FIRST hit a half-initialised llm_select and the
# whole package became order-dependent. Nothing else moved.
JUDGE_ENGINES = ("openai", "anthropic", "ollama")
MAX_JUDGE_CLIPS = 80
MAX_CLIP_CHARS = 900


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def parse_json(raw: str) -> Any:
    """The JSON in a model's answer, or None.

    Models wrap JSON in code fences and prefaces however firmly you ask them
    not to, and a whole analysis pass must not be lost to a stray backtick.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    match = _JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        logger.warning("llm_select: answer was not JSON (%.120s)", text)
        return None


async def _ask(engines: Sequence[str], prompt: str, *, model: str | None = None,
               num_ctx: int = 32768, trace: Any = None,
               stage: str = "ask", request: str = "",
               timeout: float | None = None) -> str | None:
    """First engine that answers. None when they all fail — never raises.

    `trace`, when given, is a reasoning_trace.RunTrace and every attempt is
    recorded on it, the failures as well as the success. Without the failures a
    fallback is invisible: the artefact shows an answer from the second engine
    and nothing says the first one was ever tried.

    Returns a STRING, so "answered" here means the transport worked. Whether
    the answer is usable is `_ask_json`'s question — see the comment there for
    why the two were once the same and should not have been.
    """
    import asyncio

    from services.descriptions import _call_llm

    for engine in engines:
        try:
            call = _call_llm(engine, prompt, model=model, system=_SYSTEM,
                             temperature=0.2, num_ctx=num_ctx)
            # A provider that never answers is the one failure mode the retry
            # list cannot route around: without a deadline the whole run waits
            # on it and the fallback engines are never reached.
            answer = (await asyncio.wait_for(call, timeout)
                      if timeout and timeout > 0 else await call)
        except Exception as exc:  # noqa: BLE001 — any engine failure is the same to us
            logger.info("llm_select: %s unavailable (%s)", engine, exc)
            _note(trace, stage, engine, ok=False, error=exc, request=request)
            continue
        if answer and answer.strip():
            _note(trace, stage, engine, ok=True, request=request)
            return answer
        logger.info("llm_select: %s returned nothing", engine)
        _note(trace, stage, engine, ok=False, error="empty response",
              request=request)
    return None


def _note(trace: Any, stage: str, engine: str, *, ok: bool,
          error: Any = None, request: str = "") -> None:
    """Record an attempt if anyone is listening. Swallows its own failures —
    the trace must never be able to break the run it is describing."""
    if trace is None:
        return
    try:
        trace.note_attempt(stage, engine, ok=ok, request=request,
                           error=None if error is None else str(error))
    except Exception:  # noqa: BLE001
        logger.debug("llm_select: trace rejected an attempt", exc_info=True)


def _note_chunk(trace: Any, kind: str, index: int, chunk: str,
                produced: int) -> None:
    """Record the slice a model was given and what came back from it.

    `t_start`/`t_end` are left None: `chunk_lines` splits on CHARACTERS and has
    no idea where it landed in the source. Reading the real times back out of
    the chunk text would invent precision the chunker does not have — the
    honest record is that the time span is unknown, and the batch that puts a
    clock on the chunker is the one that fills these in.
    """
    if trace is None:
        return
    try:
        trace.note_chunk(kind, index, chars=len(chunk or ""), produced=produced)
    except Exception:  # noqa: BLE001
        logger.debug("llm_select: trace rejected a chunk", exc_info=True)




async def _ask_json(engines, prompt, *, model=None, num_ctx: int = 32768,
                    trace=None, stage: str = "ask", request: str = "",
                    want: type = list, timeout: float | None = None,
                    is_cancelled=None, keys: Sequence[str] = ()):
    """The first engine that returns a USABLE answer, parsed. None when none do.

    `_ask` accepted the first non-empty string, and a string is not an answer:
    models return prose, apologies and half-fenced blocks. The whole pass was
    then lost, because the caller parsed AFTER the loop had already committed
    to that engine — the fallback existed and could not be reached.

    Validation is JSON, the container shape, and — when `keys` is given — that
    at least one element actually carries one of them. The container alone is
    not enough: `[{"garbage": 1}]` is a list, so it stopped the fallback while
    producing zero anchors, which is the same failure as accepting prose one
    level further in. Anything deeper still belongs to the caller, which knows
    what a valid anchor is.

    `is_cancelled` is checked between engines. A run cancelled while a slow
    provider is being tried should stop at the next decision point rather than
    walk the whole engine list first.
    """
    for engine in engines:
        if is_cancelled is not None and is_cancelled():
            return None
        answer = await _ask([engine], prompt, model=model, num_ctx=num_ctx,
                            trace=trace, stage=stage, request=request,
                            timeout=timeout)
        if answer is None:
            continue
        parsed = parse_json(answer)
        ok = parsed is not None and isinstance(parsed, want)
        if ok and keys and isinstance(parsed, list):
            # An EMPTY list is a real answer: "nothing here worth clipping" is
            # what a quiet stretch should return, and retrying it on another
            # provider would buy nothing and cost a call.
            ok = not parsed or any(isinstance(item, dict)
                                   and any(k in item for k in keys)
                                   for item in parsed)
        _note_result(trace, stage, request or stage, ok)
        if ok:
            return parsed
        logger.info("llm_select: %s answered but not as %s", engine, want.__name__)
    return None


def _note_result(trace: Any, stage: str, request: str, parsed: bool) -> None:
    """Record whether an ANSWER was usable, not just whether one arrived.

    A provider returning prose instead of JSON is a failure of the pass, and
    without this it looked identical to a success: the attempt is `ok`, the
    request is not exhausted, and the run reports `complete`.
    """
    if trace is None:
        return
    try:
        trace.note_result(stage, request, parsed=parsed)
    except Exception:  # noqa: BLE001
        logger.debug("llm_select: trace rejected a result", exc_info=True)
