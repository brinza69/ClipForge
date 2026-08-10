"""
ClipForge — AI Stream Clipper: the story model behind a candidate.

The old reasoning was `interesting signals -> window -> features -> score`. It
finds moments where something is loud. Measured on a real co-stream, that put
"let's cook our food, let's cook our diamonds" at #2 of 46 and put a bit where
chat trolls the streamer and he catches them at #45.

This module reasons the other way round, payoff first:

    what happened here that deserves a clip
    -> what must a viewer already know for it to land
    -> where is the earliest moment that carries all of it
    -> where is the earliest ending that feels finished

which is the editing principle the whole file exists to serve:

    LATEST COMPLETE START  +  EARLIEST SATISFYING END

— the shortest cut that still keeps the story, not the shortest cut possible
and not the widest context available.

WHAT IS PURE HERE. Everything. Anchors arrive from `llm_select.detect_anchors`
(or from the heuristic fallback) as plain dicts, and this module turns them
into candidate windows the existing Pass C can refine. That keeps the whole
story model unit-testable without a network call, which matters because the
interesting failures are in the geometry, not in the prompt.

WHAT IS REUSED. Forward reaction and clean endings already exist and are good:
`candidate_boundaries._reaction_end`, `_trim_tail`, `_drop_dangling_tail`, and
`refine_boundaries` runs all of them. A variant produced here is handed to that
same path, so it inherits sentence-true in-points and no dangling final word.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from services.clipper.candidate_terms import _clamp01, _num
from services.clipper.segmentation import norm_token

logger = logging.getLogger("clipforge.clipper.story")

STORY_VERSION = "story_v1"

# The archetypes a clip can be. A candidate may carry more than one — a
# streamer predicting his own failure and then failing is CALLBACK and FAIL at
# once, and judging it as only one of those loses what makes it good.
ARCHETYPES: tuple[str, ...] = (
    "CLUTCH", "FAIL", "RAGE", "FUNNY", "REACTION", "STORY", "REVEAL",
    "HOT_TAKE", "ARGUMENT", "WHOLESOME", "SHOCK", "INFORMATION",
    "CHALLENGE", "COMEBACK", "CALLBACK",
)

# What each archetype needs in order to work, in the shape the judge is asked
# to check. Not scoring weights — a rubric, so a FUNNY clip is not marked down
# for lacking stakes and a CLUTCH one is not excused for lacking them.
ARCHETYPE_SHAPE: dict[str, tuple[str, ...]] = {
    "CLUTCH": ("stakes", "action", "near failure", "win", "reaction"),
    "FAIL": ("expectation", "attempt", "failure", "reaction"),
    "RAGE": ("provocation", "escalation", "outburst"),
    "FUNNY": ("setup", "misdirection", "punchline", "reaction"),
    "REACTION": ("stimulus", "reaction"),
    "STORY": ("curiosity", "context", "escalation", "reveal"),
    "REVEAL": ("build-up", "reveal", "consequence"),
    "HOT_TAKE": ("claim", "justification", "consequence"),
    "ARGUMENT": ("position", "counter", "resolution or stalemate"),
    "WHOLESOME": ("situation", "kindness", "response"),
    "SHOCK": ("normality", "rupture", "reaction"),
    "INFORMATION": ("question or gap", "answer", "why it matters"),
    "CHALLENGE": ("terms", "attempt", "result"),
    "COMEBACK": ("losing position", "turn", "win"),
    "CALLBACK": ("earlier setup", "later echo", "recognition"),
}

# Openers a cold viewer cannot resolve. A clip starting on one of these is
# asking about something it never showed. Romanian spelled without diacritics
# because norm_token folds them onto ASCII.
_DANGLING_REFS = frozenset((
    "he", "she", "they", "it", "him", "her", "them", "this", "that", "those",
    "these", "there", "then", "el", "ea", "ei", "ele", "asta", "aia", "astea",
    "alea", "acolo", "atunci", "lui", "ii",
))
# ...and words that promise a shared history the clip has not shown.
_BACKREFS = frozenset((
    "again", "earlier", "before", "yesterday", "remember", "said", "told",
    "like i", "as i", "iar", "inainte", "ieri", "tineti", "ziceam", "spuneam",
))

# How far back required context may reach. Past this the setup is no longer
# part of the same moment, it is a different clip.
MAX_CONTEXT_REACH_S = 90.0
# A hook this far into the clip is too late — a cold viewer has already gone.
GOOD_HOOK_LATENCY_S = 3.0


# --------------------------------------------------------------------------
# validating what a model handed back
# --------------------------------------------------------------------------

def normalise_anchor(raw: Any, duration: float) -> dict | None:
    """One model-proposed anchor, validated into the shape the rest expects.

    Returns None rather than guessing: an anchor with no payoff time cannot
    seed a backward search, and inventing one drags every later decision along
    with it.
    """
    if not isinstance(raw, dict):
        return None
    payoff = _num(raw.get("payoff_t", raw.get("t", raw.get("payoff"))), -1.0)
    if isinstance(raw.get("payoff"), dict):
        payoff = _num(raw["payoff"].get("t"), -1.0)
    if payoff < 0 or (duration > 0 and payoff > duration):
        return None

    kinds = raw.get("archetypes") or raw.get("archetype") or []
    if isinstance(kinds, str):
        kinds = [kinds]
    kinds = [k.strip().upper() for k in kinds if isinstance(k, str)]
    kinds = [k for k in kinds if k in ARCHETYPES]

    context = []
    for item in raw.get("required_context") or []:
        if isinstance(item, dict):
            t = _num(item.get("t", item.get("time")), -1.0)
            fact = str(item.get("fact") or item.get("why") or "")[:160]
        else:
            t, fact = -1.0, str(item)[:160]
        # Context after the payoff is not context, and context from another
        # part of the stream is a different clip.
        if 0 <= t <= payoff and payoff - t <= MAX_CONTEXT_REACH_S:
            context.append({"t": round(t, 3), "fact": fact})
    context.sort(key=lambda c: c["t"])

    hook = raw.get("hook") if isinstance(raw.get("hook"), dict) else {}
    hook_t = _num(hook.get("t"), -1.0)

    return {
        "payoff_t": round(payoff, 3),
        "payoff_strength": _clamp01(_num(raw.get("payoff_strength",
                                                 (raw.get("payoff") or {}).get("strength")
                                                 if isinstance(raw.get("payoff"), dict)
                                                 else 0.5), 0.5)),
        "archetypes": kinds,
        "why": str(raw.get("why") or raw.get("interesting_because") or "")[:200],
        "required_context": context,
        "hook_t": round(hook_t, 3) if 0 <= hook_t <= payoff else None,
        "unresolved_context": [str(u)[:120]
                               for u in (raw.get("unresolved_context") or [])][:6],
        "confidence": _clamp01(_num(raw.get("confidence"), 0.6)),
        "story_version": STORY_VERSION,
    }


# --------------------------------------------------------------------------
# context debt
# --------------------------------------------------------------------------

def context_debt(words: Sequence[dict], anchor: dict | None = None) -> float:
    """How much a cold viewer has to already know, 0..1.

    Two things make a clip unwatchable on its own: it OPENS on a reference it
    never resolves ("and then he told me..."), and it leans on shared history
    ("remember what he said yesterday"). Both are visible in the words.

    A model's own `unresolved_context` list, when there is one, is stronger
    evidence than either and is folded in — it can see that "Daniel" was never
    introduced, which no word list can.
    """
    tokens = [norm_token(w.get("word", "")) for w in words or []]
    tokens = [t for t in tokens if t]
    if not tokens:
        return 0.0

    # An unresolved reference in the first handful of words is worth far more
    # than one in the middle, where the clip has had a chance to explain itself.
    opening = tokens[:8]
    opens_dangling = any(t in _DANGLING_REFS for t in opening[:3])
    open_refs = sum(1 for t in opening if t in _DANGLING_REFS) / max(1, len(opening))
    backrefs = sum(1 for t in tokens if t in _BACKREFS) / float(len(tokens))

    debt = 0.45 * (1.0 if opens_dangling else 0.0)
    debt += 0.30 * _clamp01(open_refs * 2.0)
    debt += 0.25 * _clamp01(backrefs * 12.0)

    listed = len((anchor or {}).get("unresolved_context") or [])
    if listed:
        debt = max(debt, _clamp01(0.30 + 0.20 * listed))
    return round(_clamp01(debt), 3)


def hook_latency(start: float, hook_t: float | None,
                 words: Sequence[dict]) -> float:
    """Seconds before a viewer learns why to stay.

    With no identified hook, fall back to the first word — a clip that opens on
    silence has already spent its latency.
    """
    if hook_t is not None and hook_t >= start:
        return round(max(0.0, hook_t - start), 3)
    first = next((_num(w.get("start"), -1.0) for w in words or []
                  if _num(w.get("start"), -1.0) >= start), -1.0)
    return round(max(0.0, first - start), 3) if first >= 0 else 0.0


# --------------------------------------------------------------------------
# anchor -> edit variants
# --------------------------------------------------------------------------

def latest_complete_start(anchor: dict, floor: float = 0.0) -> float:
    """The latest moment that still carries every required fact.

    This is the backward reconstruction in one line: the start is derived from
    the EARLIEST required context, not from the first audio spike. Measured on
    the spec's own example, a spike-based detector opens at the kill and loses
    the "if I win this with 1 HP" that makes the kill mean anything.
    """
    payoff = _num(anchor.get("payoff_t"))
    # Re-filter rather than trust the caller: normalise_anchor drops context
    # that sits after the payoff or too far before it, but this is also reached
    # from the heuristic path and from tests, and a context timestamp on the
    # wrong side of the payoff produces a window with a negative length.
    times = [_num(c.get("t"), -1.0) for c in (anchor.get("required_context") or [])
             if isinstance(c, dict)]
    times = [t for t in times if 0 <= t <= payoff and payoff - t <= MAX_CONTEXT_REACH_S]
    if not times:
        hook = _num(anchor.get("hook_t"), -1.0)
        return max(floor, hook if 0 <= hook <= payoff else payoff)
    return max(floor, min(times))


def _clamp_window(start: float, end: float, lo: float, hi: float,
                  ceiling: float) -> tuple[float, float] | None:
    """Force [start,end] into the duration bounds, keeping the END fixed.

    The end is where the story finishes; a window that has to shrink should
    lose setup, never payoff.
    """
    end = min(end, ceiling) if ceiling > 0 else end
    if end <= start:
        return None
    if end - start > hi:
        start = end - hi
    if end - start < lo:
        start = max(0.0, end - lo)
        if end - start < lo:
            return None
    return round(max(0.0, start), 3), round(end, 3)


def variants_from_anchor(anchor: dict, reaction_end: float, *,
                         lo: float, hi: float, ceiling: float = 0.0) -> list[dict]:
    """The two to four cuts worth proposing for one anchor.

    They compete rather than one being chosen here: `dedupe` collapses the ones
    that turn out to be the same clip and the scorer ranks the rest, which is
    the same contract `generate_candidates` already has with its three
    variants. Each carries WHY it was cut that way, because a candidate whose
    rationale cannot be read is a candidate nobody can debug.
    """
    payoff = _num(anchor.get("payoff_t"))
    complete = latest_complete_start(anchor)
    hook_t = anchor.get("hook_t")
    end = max(reaction_end, payoff + 0.5)

    proposals: list[tuple[str, float, str]] = [
        # The principle: everything required, nothing more.
        ("balanced", complete, "opens on the earliest required context"),
        # Payoff and its reaction alone — the cheapest cut that still lands.
        ("tight", max(complete, payoff - 6.0), "opens just before the payoff"),
    ]
    if hook_t is not None and hook_t < complete - 0.5:
        proposals.append(("hook_first", _num(hook_t),
                          "opens on the hook, which sits before the setup"))
    if complete < payoff - 20.0:
        # A long run-up sometimes reads better trimmed to its second half.
        proposals.append(("story_short", (complete + payoff) / 2.0,
                          "halves a long run-up to the payoff"))

    out: list[dict] = []
    seen: set[tuple[float, float]] = set()
    for name, start, why in proposals:
        window = _clamp_window(start, end, lo, hi, ceiling)
        if window is None:
            continue
        key = (round(window[0], 1), round(window[1], 1))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "start": window[0], "end": window[1],
            "reasons": ["story_anchor", f"variant_{name}"],
            "variant": name,
            "story": {
                "anchor_t": payoff,
                "archetypes": list(anchor.get("archetypes") or []),
                "why": anchor.get("why", ""),
                "required_context": list(anchor.get("required_context") or []),
                "payoff_t": payoff,
                "hook_t": hook_t,
                "reaction_end": round(end, 3),
                "edit_reason": why,
                "story_version": STORY_VERSION,
            },
        })
    return out


def describe(cand: dict) -> str:
    """One line explaining a story candidate, for logs and the debug panel."""
    story = cand.get("story") or {}
    kinds = "+".join(story.get("archetypes") or []) or "UNCLASSIFIED"
    ctx = len(story.get("required_context") or [])
    return (f"[{kinds}] {cand.get('start', 0):.1f}-{cand.get('end', 0):.1f}s "
            f"{story.get('edit_reason', '')} ({ctx} context facts) "
            f"— {story.get('why', '')}")
