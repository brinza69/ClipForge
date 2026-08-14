"""
ClipForge — AI Stream Clipper: LLM judgement over candidate clips.

Why this exists, in one measurement. On a 12-minute IShowSpeed co-stream the
rule-based scorer put "let's cook our food, let's cook our diamonds" at #5 of
its top eight, and put this at #53 of 53:

    "Look to your left — it's a blast furnace." / "What do you mean, look to
    my left? How do you make a blast furnace?" / "You don't have that in the
    game." / "I'm lying, bro." / "Y'all just sat there and lied, bro."

Chat trolled him, he fell for it, he called them out. Setup, trap, punchline.
Dead last. The scorer judges audio energy, pacing and sentence structure, and
none of those can tell "naming my inventory" from "chat lied to me and I
noticed". That gap is what a language model closes.

TWO FILTERS THAT FAIL DIFFERENTLY
---------------------------------
`nominate()` reads the whole transcript on a CHEAP model and proposes moments.
Its picks are UNIONED with the rule-based candidates, never substituted for
them. That is the whole design: a small model nominates roughly the same
obvious moments the scorer already finds, so using it as a filter would throw
away exactly the non-obvious picks the expensive model is being paid to judge.
Two independent recalls, one judgement pass.

`judge()` scores that union on a FRONTIER model and returns 0..100 per clip.

USE A FRONTIER MODEL FOR THE JUDGING. Measured on the same 46 candidates:
gpt-4o-mini answered almost everything 50, 40 or 10 with reasons like
"Excitement about discovery", and moved the good clip from #45 only to #36.
A frontier model spread its scores over eight values, sank pure narration to
the bottom on its own, and moved the same clip to #4. Nomination is bulk
reading and a small model is fine at it; judging is taste and it is not.

The models are not deterministic between runs — the same 46 candidates were
scored twice and the ordering moved. That is a second reason the verdict is
blended with the heuristic rather than replacing it: the heuristic is stable.

COST, measured with tiktoken on real transcripts (90..395 tokens/minute across
19 of them): 3.7 cents for a 12-hour gaming stream, 11.1 for a talk-heavy one.
Frontier OUTPUT is the dominant term — 10 $/1M against 2.50 for input — which
is why `judge` asks for `id: score` and one short reason, never prose.

Everything here degrades to None rather than raising. A clipper run must not
fail because Ollama is down or a key expired; it falls back to the heuristic
ranking, which is what shipped before this module existed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Sequence

logger = logging.getLogger("clipforge.clipper.llm_select")

# Engines tried in order. Local first: pass 1 is bulk reading, which is what a
# small local model is good at and what an API charges most for.
NOMINATE_ENGINES = ("ollama", "openai")
JUDGE_ENGINES = ("openai", "anthropic", "ollama")

MAX_TRANSCRIPT_CHARS = 600_000    # ~150k tokens; longer sources are chunked
CHUNK_CHARS = 120_000             # ~30k tokens per nomination chunk
MAX_JUDGE_CLIPS = 80
MAX_CLIP_CHARS = 900

_SYSTEM = (
    "You pick moments from livestream transcripts that work as standalone "
    "short-form clips. You judge what HAPPENS, not how loud it is. "
    "Answer only with the JSON asked for — no preface, no code fence, no prose."
)

_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


# --------------------------------------------------------------------------
# pure helpers — no network, unit-testable
# --------------------------------------------------------------------------

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


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def transcript_lines(segments: Sequence[dict], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """`[seconds] text` per segment — the model needs a timestamp to point at."""
    out: list[str] = []
    total = 0
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        text = " ".join(str(seg.get("text") or "").split())
        if not text:
            continue
        line = f"[{int(_num(seg.get('start')))}] {text}"
        total += len(line) + 1
        if total > limit:
            break
        out.append(line)
    return "\n".join(out)


def chunk_lines(lines: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split on line boundaries so no segment is cut in half."""
    if len(lines) <= size:
        return [lines] if lines else []
    chunks, current, total = [], [], 0
    for line in lines.splitlines():
        if total + len(line) + 1 > size and current:
            chunks.append("\n".join(current))
            current, total = [], 0
        current.append(line)
        total += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def moments_to_windows(moments: Any, duration: float, *, pad_s: float = 4.0,
                       min_s: float = 15.0, max_s: float = 90.0) -> list[dict]:
    """Model output -> candidate windows, clamped into the source.

    A model naming a moment gives one timestamp, not a span, so the window is
    built around it. Anything unparseable is dropped rather than guessed at.
    """
    out: list[dict] = []
    if not isinstance(moments, list):
        return out
    for item in moments:
        if not isinstance(item, dict):
            continue
        t = _num(item.get("t", item.get("start", item.get("time"))), -1.0)
        if t < 0:
            continue
        want = _num(item.get("duration"), 30.0)
        want = min(max(want, min_s), max_s)
        start = max(0.0, t - pad_s)
        end = start + want
        if duration > 0:
            end = min(end, duration)
            start = max(0.0, min(start, max(0.0, end - min_s)))
        if end - start < min_s:
            continue
        out.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "reasons": ["llm_nominated"],
            "llm_tag": str(item.get("why") or item.get("tag") or "")[:120],
        })
    return out


def apply_scores(cands: list[dict], verdicts: Any, *, weight: float = 0.5) -> int:
    """Blend `llm` scores into each candidate's `overall`. Returns how many hit.

    Blended, not substituted: the heuristic is transparent and the model has
    no idea what renders well, so neither gets the whole vote. `llm_score` is
    kept alongside so a bad blend can be diagnosed without re-running.
    """
    if not isinstance(verdicts, list):
        return 0
    by_id: dict[int, dict] = {}
    for v in verdicts:
        if isinstance(v, dict) and v.get("id") is not None:
            try:
                by_id[int(v["id"])] = v
            except (TypeError, ValueError):
                continue
    hit = 0
    w = min(1.0, max(0.0, weight))
    for i, cand in enumerate(cands):
        verdict = by_id.get(i)
        if verdict is None:
            continue
        # Check BEFORE clamping: a missing or non-numeric score sentinels as
        # -1, and clamping first would silently turn it into a real 0 and drag
        # the blend down. A model that skipped a clip has said nothing about
        # it, which is not the same as calling it worthless.
        raw = _num(verdict.get("score"), -1.0)
        if raw < 0:
            continue
        score = min(100.0, raw)
        cand["llm_score"] = round(score, 1)
        if verdict.get("why"):
            cand["llm_reason"] = str(verdict["why"])[:200]
        cand["overall"] = round((1.0 - w) * _num(cand.get("overall")) + w * score, 2)
        hit += 1
    return hit


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------

def nominate_prompt(lines: str, want: int) -> str:
    return (
        f"Below is a livestream transcript, one line per segment, each prefixed "
        f"with its start time in seconds.\n\n"
        f"List up to {want} moments that would work as standalone short clips. "
        f"Favour: a joke that lands, a story with a payoff, a genuine reaction, "
        f"someone being proved wrong, a rule or a plan being broken, an argument. "
        f"Ignore: narrating routine actions, listing inventory, filler, and "
        f"stretches where nothing is resolved.\n\n"
        f'Answer as JSON only: [{{"t": <seconds>, "duration": <15-90>, '
        f'"why": "<max 8 words>"}}]\n\n'
        f"--- TRANSCRIPT ---\n{lines}"
    )


ANCHOR_PROMPT_VERSION = "anchor_v1"


def anchor_prompt(lines: str, want: int,
                  promises: Sequence[dict] | None = None) -> str:
    """Payoff-first. The question is not where a window should start."""
    from services.clipper.story import ARCHETYPES

    recall = ""
    if promises:
        # Only the setups still open at this point in the stream. A payoff
        # that lands on one of these is a callback, and it is the one kind of
        # clip a per-chunk pass cannot otherwise see.
        #
        # Stated as its own numbered step, not as an aside. Measured: with the
        # instruction buried and `callback_to` last in a ten-field schema, a
        # model found "finds diamond AFTER ASKING ADMINS" — recognising the
        # link in prose — and still left the field null on all four anchors.
        recall = (
            "\n  5. CALLBACK — these were said EARLIER in the stream and are "
            "still unresolved:\n"
            + "\n".join(f"       [{p['t']:.0f}] ({p['kind']}) {p['text']}"
                        for p in promises[:12])
            + "\n     If a moment below is the answer to one of them, you MUST "
              "set `callback_to` to that timestamp. A payoff that resolves "
              "something said earlier is worth far more than one that does "
              "not, so do not leave it out. Set it to null when nothing "
              "matches.\n")

    return (
        "Below is a livestream transcript, one line per segment, prefixed with "
        "its start time in seconds.\n\n"
        f"Find up to {want} moments that deserve a standalone short clip. For "
        "each one, work BACKWARDS from what happened:\n"
        "  1. the PAYOFF — the thing that makes the moment worth watching, and "
        "when it happens\n"
        "  2. the REQUIRED CONTEXT — every fact a viewer who saw nothing else "
        "must already know for that payoff to land, each with the timestamp "
        "where it is established. Usually one or two. Never more than 90 "
        "seconds before the payoff.\n"
        "  3. the HOOK — the earliest line that gives a stranger a reason to "
        "keep watching, if there is one\n"
        "  4. UNRESOLVED CONTEXT — anything the clip would still leave "
        "unexplained: a name never introduced, an event referred to but not "
        "shown\n\n"
        f"{recall}\n"
        f"Archetypes, choose one or two: {', '.join(ARCHETYPES)}\n\n"
        "Ignore moments that are only loud. Narrating routine actions, reading "
        "an inventory and filler are not payoffs.\n"
        "Some lines end in <angle brackets> with what the audio and picture "
        "were doing there. Treat that as evidence, never as the reason on its "
        "own — LOUD without something happening is not a payoff.\n\n"
        'Answer as JSON only: [{"payoff_t": <seconds>, "payoff_strength": '
        '<0-1>, "archetypes": ["..."], "why": "<max 12 words>", '
        '"required_context": [{"t": <seconds>, "fact": "<max 8 words>"}], '
        '"hook": {"t": <seconds>}, "unresolved_context": ["..."], '
        '"callback_to": <seconds or null>, "confidence": <0-1>}]\n\n'
        f"--- TRANSCRIPT ---\n{lines}"
    )



# --------------------------------------------------------------------------
# the two passes
# --------------------------------------------------------------------------

async def _ask(engines: Sequence[str], prompt: str, *, model: str | None = None,
               num_ctx: int = 32768) -> str | None:
    """First engine that answers. None when they all fail — never raises."""
    from services.descriptions import _call_llm

    for engine in engines:
        try:
            answer = await _call_llm(engine, prompt, model=model, system=_SYSTEM,
                                     temperature=0.2, num_ctx=num_ctx)
        except Exception as exc:  # noqa: BLE001 — any engine failure is the same to us
            logger.info("llm_select: %s unavailable (%s)", engine, exc)
            continue
        if answer and answer.strip():
            return answer
        logger.info("llm_select: %s returned nothing", engine)
    return None


async def nominate(segments: Sequence[dict], duration: float, *,
                   per_chunk: int = 12,
                   engines: Sequence[str] = NOMINATE_ENGINES) -> list[dict]:
    """Moments a cheap model thinks are clip-worthy. [] when no engine answers."""
    lines = transcript_lines(segments)
    if not lines:
        return []
    found: list[dict] = []
    for chunk in chunk_lines(lines):
        answer = await _ask(engines, nominate_prompt(chunk, per_chunk))
        if answer is None:
            continue
        found.extend(moments_to_windows(parse_json(answer), duration))
    logger.info("llm_select: nominated %d moments", len(found))
    return found


def _attach_callback(anchor: dict, raw: Any, promises: Sequence[dict]) -> None:
    """Link an anchor to the setup it pays off, if the model named one.

    The setup is minutes or hours away, so it can never be inside the window —
    a callback is context the clip OWES, not context it can carry. Recording
    it is what lets `story.context_debt` charge for that and the headline say
    what the viewer missed.
    """
    from services.clipper.promises import MIN_CALLBACK_GAP_S

    t = _num((raw or {}).get("callback_to"), -1.0)
    if t < 0:
        return
    match = min(
        (p for p in promises
         if anchor["payoff_t"] - _num(p.get("t"), -1e9) >= MIN_CALLBACK_GAP_S),
        key=lambda p: abs(_num(p.get("t")) - t), default=None)
    if match is None or abs(_num(match.get("t")) - t) > 30.0:
        return
    anchor["callback_to"] = dict(match)
    if "CALLBACK" not in anchor["archetypes"]:
        anchor["archetypes"] = (anchor["archetypes"] + ["CALLBACK"])[:3]


async def detect_anchors(segments: Sequence[dict], duration: float, *,
                         per_chunk: int = 10,
                         engines: Sequence[str] = NOMINATE_ENGINES,
                         model: str | None = None,
                         promises: Sequence[dict] | None = None,
                         atoms: Sequence[dict] | None = None) -> list[dict]:
    """Anchors: a payoff, what a viewer must know for it to land, an archetype.

    The richer sibling of `nominate`, and the input to the story engine. Same
    failure contract — [] when no engine answers, so the run falls back to the
    heuristic candidates rather than stopping.

    Chunked, never the whole stream in one prompt: a 12-hour transcript is
    ~64k tokens at the measured rate and up to 285k on a talkative source,
    past the context of the models this would otherwise use.
    """
    from services.clipper.story import normalise_anchor

    # Atom lines carry the evidence for their own moment — that the room got
    # loud, the picture cut, the game went into a menu — where a transcript
    # line carries only the words. Features as evidence, at the grain of one
    # utterance, which is what atoms exist for.
    if atoms:
        from services.clipper.atoms import to_lines
        lines = to_lines(atoms, MAX_TRANSCRIPT_CHARS)
    else:
        lines = transcript_lines(segments)
    if not lines:
        return []
    from services.clipper import promises as promise_mod

    found: list[dict] = []
    for chunk in chunk_lines(lines):
        # Setups still open anywhere up to the END of this chunk, which
        # includes ones inside it. Filtering to "before the chunk" was wrong
        # at real scale: a chunk holds five hours of this source, and a model
        # will not reliably connect a payoff at hour three to a line at hour
        # one buried in 30k tokens. The recall list is exactly that aid.
        #
        # Still bounded — `open_at` drops anything older than the lifetime or
        # closer than the gap, so a payoff never sees a prediction it cannot
        # possibly resolve.
        stamps = [_num(line.split("]", 1)[0].lstrip("["), -1.0)
                  for line in chunk.splitlines() if line.startswith("[")]
        last_t = max([t for t in stamps if t >= 0] or [0.0])
        live = promise_mod.open_at(promises or [], last_t)
        answer = await _ask(engines, anchor_prompt(chunk, per_chunk, live),
                            model=model)
        if answer is None:
            continue
        for raw in (parse_json(answer) or []):
            anchor = normalise_anchor(raw, duration)
            if anchor is not None:
                anchor["prompt_version"] = ANCHOR_PROMPT_VERSION
                _attach_callback(anchor, raw, promises or [])
                found.append(anchor)
    found.sort(key=lambda a: a["payoff_t"])
    logger.info("llm_select: %d anchors (%s)", len(found), ANCHOR_PROMPT_VERSION)
    return found




# Judging lives in llm_judge.py — it reads finished candidates and
# ranks them, where this module proposes them. Re-exported so
# `from services.clipper.llm_select import judge` keeps working.
from services.clipper.llm_judge import (  # noqa: E402,F401
    JUDGE_PROMPT_VERSION, REJECT_REASONS, apply_ranking, judge,
    judge_prompt,
)
