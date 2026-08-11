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


def anchor_prompt(lines: str, want: int) -> str:
    """Payoff-first. The question is not where a window should start."""
    from services.clipper.story import ARCHETYPES

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
        f"Archetypes, choose one or two: {', '.join(ARCHETYPES)}\n\n"
        "Ignore moments that are only loud. Narrating routine actions, reading "
        "an inventory and filler are not payoffs.\n\n"
        'Answer as JSON only: [{"payoff_t": <seconds>, "payoff_strength": '
        '<0-1>, "archetypes": ["..."], "why": "<max 12 words>", '
        '"required_context": [{"t": <seconds>, "fact": "<max 8 words>"}], '
        '"hook": {"t": <seconds>}, "unresolved_context": ["..."], '
        '"confidence": <0-1>}]\n\n'
        f"--- TRANSCRIPT ---\n{lines}"
    )


JUDGE_PROMPT_VERSION = "judge_v2_comparative"

# Reject reasons the judge may name. A closed list so they can be counted and
# filtered rather than read one at a time.
REJECT_REASONS: tuple[str, ...] = (
    "no_payoff", "no_story", "context_debt", "late_hook", "dead_open",
    "weak_ending", "all_energy_no_meaning", "all_setup_no_payoff",
    "fans_only", "needs_outside_knowledge", "transcript_broken",
)


def judge_prompt(cands: Sequence[dict], want: int) -> str:
    """Rank the field, do not score each clip alone.

    Absolute scores compress: measured on 46 real candidates, an earlier
    version answered with eight distinct values and, on a quiet source,
    everything between 5 and 30. A forced ordering cannot compress, and it is
    also the question that actually matters — if only N of these can be
    published, which N.
    """
    from services.clipper.story import ARCHETYPE_SHAPE

    body, kinds = [], set()
    for i, cand in enumerate(cands):
        text = " ".join(str(cand.get("text") or "").split())[:MAX_CLIP_CHARS]
        tags = (cand.get("story") or {}).get("archetypes") or []
        kinds.update(tags)
        label = f" <{'+'.join(tags)}>" if tags else ""
        body.append(f"{i}.{label} [{_num(cand.get('start')):.0f}s] {text}")

    # Only the rubrics in play — a FUNNY clip must not be marked down for
    # lacking stakes, and a CLUTCH one must not be excused for lacking them.
    rubric = ""
    present = [k for k in ARCHETYPE_SHAPE if k in kinds]
    if present:
        rubric = ("\nWhere a candidate is tagged, judge it against the shape "
                  "that kind of clip needs:\n"
                  + "\n".join(f"  {k}: {' -> '.join(ARCHETYPE_SHAPE[k])}"
                              for k in present) + "\n")

    return (
        f"Below are {len(cands)} candidate clips from ONE livestream.\n\n"
        f"If only {want} of them could be published, which deserve the slots? "
        f"Return the best {min(len(cands), want * 2)} IN RANK ORDER, best "
        "first, each id at most once. Everything you leave out is one you are "
        "saying does not make the cut. Judge them against each other, not "
        "against an absolute standard.\n\n"
        "Judge what HAPPENS, not how loud it is. Transcription noise — "
        "repeated words, gibberish — is not energy.\n"
        f"{rubric}\n"
        "For each candidate give three verdicts:\n"
        "  story_editor — is there a setup, a turn, a payoff, an ending? "
        "strong | medium | weak\n"
        "  cold_viewer — someone who does not know this streamer and did not "
        "watch the stream: do they care? strong | medium | weak\n"
        "  critic — actively look for why this should NOT be posted. Zero or "
        "more of: " + ", ".join(REJECT_REASONS) + "\n\n"
        'Answer as JSON only, in rank order: [{"id": <number>, '
        '"story_editor": "...", "cold_viewer": "...", "critic": ["..."], '
        '"why": "<max 8 words>"}]\n\n'
        "--- CANDIDATES ---\n" + "\n".join(body)
    )


# A verdict from the brutal editor is worth this much of the clip's score.
# Not a veto: the ranking already saw the same clip, so a reject reason is a
# second opinion, not an override.
_REJECT_PENALTY = 12.0
_PERSPECTIVE = {"strong": 1.0, "medium": 0.55, "weak": 0.15}


def apply_ranking(cands: list[dict], verdicts: Any, *,
                  weight: float = 0.7) -> int:
    """Turn a ranked list into scores and blend them in. Returns how many hit.

    Position drives the score, which is the whole point of ranking instead of
    scoring: the top of the field gets 100 and the bottom gets near zero on
    every source, so a quiet stream no longer compresses into a ten-point
    band. The three perspectives then move a clip inside its neighbourhood,
    and each reject reason costs it a fixed amount.
    """
    if not isinstance(verdicts, list) or not verdicts:
        return 0
    ordered = [v for v in verdicts if isinstance(v, dict) and v.get("id") is not None]
    if not ordered:
        return 0

    span = max(1, len(ordered) - 1)
    w = min(1.0, max(0.0, weight))
    hit = 0
    shortlisted: set[int] = set()
    for position, verdict in enumerate(ordered):
        try:
            index = int(verdict["id"])
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(cands):
            continue
        cand = cands[index]

        score = 100.0 * (1.0 - position / span)
        editor = _PERSPECTIVE.get(str(verdict.get("story_editor", "")).lower())
        viewer = _PERSPECTIVE.get(str(verdict.get("cold_viewer", "")).lower())
        if editor is not None or viewer is not None:
            # A cold viewer's verdict is what decides a short, so it carries
            # more here than the story editor's craft judgement.
            blend = (0.4 * (editor if editor is not None else 0.55)
                     + 0.6 * (viewer if viewer is not None else 0.55))
            score = 0.7 * score + 0.3 * (100.0 * blend)

        rejects = [str(r) for r in (verdict.get("critic") or [])
                   if str(r) in REJECT_REASONS]
        score = max(0.0, score - _REJECT_PENALTY * len(rejects))

        cand["llm_score"] = round(score, 1)
        cand["llm_rank"] = position + 1
        cand["llm_verdict"] = {
            "story_editor": verdict.get("story_editor"),
            "cold_viewer": verdict.get("cold_viewer"),
            "reject_reasons": rejects,
            "prompt_version": JUDGE_PROMPT_VERSION,
        }
        if verdict.get("why"):
            cand["llm_reason"] = str(verdict["why"])[:200]
        cand["overall"] = round((1.0 - w) * _num(cand.get("overall")) + w * score, 2)
        shortlisted.add(index)
        hit += 1

    # Everything the model left out is one it is saying does not make the cut,
    # and it has to be blended too. Measured: with only the shortlist blended,
    # 9 of 42 candidates got a rank-derived score and the other 33 kept an
    # unblended heuristic around 58 — so a candidate the judge declined to
    # rank beat one it had ranked fourth.
    if hit:
        for index, cand in enumerate(cands):
            if index in shortlisted:
                continue
            cand["llm_score"] = 0.0
            cand["overall"] = round((1.0 - w) * _num(cand.get("overall")), 2)
    return hit


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


async def detect_anchors(segments: Sequence[dict], duration: float, *,
                         per_chunk: int = 10,
                         engines: Sequence[str] = NOMINATE_ENGINES,
                         model: str | None = None) -> list[dict]:
    """Anchors: a payoff, what a viewer must know for it to land, an archetype.

    The richer sibling of `nominate`, and the input to the story engine. Same
    failure contract — [] when no engine answers, so the run falls back to the
    heuristic candidates rather than stopping.

    Chunked, never the whole stream in one prompt: a 12-hour transcript is
    ~64k tokens at the measured rate and up to 285k on a talkative source,
    past the context of the models this would otherwise use.
    """
    from services.clipper.story import normalise_anchor

    lines = transcript_lines(segments)
    if not lines:
        return []
    found: list[dict] = []
    for chunk in chunk_lines(lines):
        answer = await _ask(engines, anchor_prompt(chunk, per_chunk), model=model)
        if answer is None:
            continue
        for raw in (parse_json(answer) or []):
            anchor = normalise_anchor(raw, duration)
            if anchor is not None:
                anchor["prompt_version"] = ANCHOR_PROMPT_VERSION
                found.append(anchor)
    found.sort(key=lambda a: a["payoff_t"])
    logger.info("llm_select: %d anchors (%s)", len(found), ANCHOR_PROMPT_VERSION)
    return found


async def judge(cands: list[dict], *, weight: float = 0.5,
                engines: Sequence[str] = JUDGE_ENGINES,
                model: str | None = None, want: int = 8) -> int:
    """Rank candidates against each other and blend it in. 0 when unavailable."""
    if not cands:
        return 0
    subset = cands[:MAX_JUDGE_CLIPS]
    answer = await _ask(engines, judge_prompt(subset, want), model=model)
    if answer is None:
        return 0
    verdicts = parse_json(answer)
    # A model that ignored "rank all of them" and scored them instead is still
    # useful, but the two answers are told apart by SHAPE, not by whether the
    # first parse succeeded: a scored answer carries ids too, so ranking it by
    # position would silently replace its scores with their order.
    scored = isinstance(verdicts, list) and any(
        isinstance(v, dict) and v.get("score") is not None
        and not any(k in v for k in ("story_editor", "cold_viewer", "critic"))
        for v in verdicts)
    if scored:
        hit = apply_scores(subset, verdicts, weight=weight)
    else:
        hit = apply_ranking(subset, verdicts, weight=weight)
    logger.info("llm_select: judged %d of %d candidates (%s)",
                hit, len(subset), JUDGE_PROMPT_VERSION)
    return hit
