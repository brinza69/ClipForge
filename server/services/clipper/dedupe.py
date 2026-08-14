"""
ClipForge — AI Stream Clipper: near-duplicate collapse and diversity ranking.

Candidate generation deliberately overproduces: the same moment gets proposed
with three different in-points, and a long answer gets proposed both whole and
as its punchline. Left alone that fills the top of the list with the same joke
three times.

This module collapses those into groups, keeps the best member as the winner
and the rest as retrievable alternatives, then spreads the winners across the
source timeline so a two-hour stream does not return five clips from the same
ten minutes.

Nothing is deleted. `deduplicate` returns every candidate it was given —
losing members are only flagged, because the UI offers them as "other cuts of
this moment" and the feedback loop needs to know what was rejected.

Pure standard library: no scikit-learn, no embeddings. Token Jaccard catches
"same words", character trigrams catch "same words, slightly different
transcription", and their geometric mean demands both.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections import Counter
from typing import Any

logger = logging.getLogger("clipforge.clipper.dedupe")

# Promoting a clip purely for timeline spread is only worth it while the
# quality cost stays small. Past this many points the better clip wins outright.
MAX_DIVERSITY_SACRIFICE = 15.0

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


# --------------------------------------------------------------------------
# text similarity


def _normalise(text: Any) -> str:
    """Case-folded, punctuation-stripped, whitespace-collapsed."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    return _SPACE.sub(" ", _PUNCT.sub(" ", text.casefold())).strip()


def _trigrams(text: str) -> Counter[str]:
    if len(text) < 3:
        return Counter()
    return Counter(text[i:i + 3] for i in range(len(text) - 2))


def _profile(text: Any) -> tuple[frozenset[str], Counter[str], str]:
    """Everything similarity needs from one string, computed once.

    `deduplicate` is O(n^2) in comparisons; re-tokenising inside the loop is
    what actually makes that hurt on a stream with 300 candidates.
    """
    norm = _normalise(text)
    return frozenset(norm.split()), _trigrams(norm), norm


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _cosine(a: Counter[str], b: Counter[str], norm_a: str, norm_b: str) -> float:
    if not a or not b:
        # Under 3 characters there are no trigrams; fall back to plain equality
        # rather than reporting a false zero.
        return 1.0 if norm_a and norm_a == norm_b else 0.0
    dot = sum(count * b[gram] for gram, count in a.items() if gram in b)
    if dot == 0:
        return 0.0
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a <= 0 or mag_b <= 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _similarity(pa: tuple[frozenset[str], Counter[str], str],
                pb: tuple[frozenset[str], Counter[str], str]) -> float:
    if not pa[2] or not pb[2]:
        return 0.0
    product = _jaccard(pa[0], pb[0]) * _cosine(pa[1], pb[1], pa[2], pb[2])
    return math.sqrt(product) if product > 0 else 0.0


def text_similarity(a: str, b: str) -> float:
    """0..1 similarity of two transcript strings.

    Geometric mean of token Jaccard and character-trigram cosine, so a pair has
    to look alike by both measures to score high. Empty input scores 0.
    """
    return _similarity(_profile(a), _profile(b))


# --------------------------------------------------------------------------
# time overlap


def _span(cand: dict) -> tuple[float, float]:
    """(start, end) in seconds, accepting either naming convention."""
    def _num(value: Any) -> float:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return 0.0
        return out if math.isfinite(out) else 0.0

    cand = cand or {}
    start = _num(cand.get("start", cand.get("start_time", 0.0)))
    end = _num(cand.get("end", cand.get("end_time", 0.0)))
    if end < start:
        start, end = end, start
    return start, end


def overlap_ratio(a: dict, b: dict) -> float:
    """Shared time as a fraction of the SHORTER candidate, 0..1.

    Intersection-over-shorter rather than IoU on purpose: a 12 s punchline that
    sits entirely inside a 60 s telling of the same story is a duplicate, and
    IoU would score that pair at 0.2 and let both through.
    """
    a_start, a_end = _span(a)
    b_start, b_end = _span(b)
    shortest = min(a_end - a_start, b_end - b_start)
    if shortest <= 0:
        return 0.0
    inter = min(a_end, b_end) - max(a_start, b_start)
    if inter <= 0:
        return 0.0
    return min(1.0, inter / shortest)


# --------------------------------------------------------------------------
# grouping + diversity


def _score_of(cand: dict) -> float:
    for key in ("overall", "overall_score"):
        value = (cand or {}).get(key)
        try:
            out = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(out):
            return out
    return 0.0


def _text_of(cand: dict) -> str:
    cand = cand or {}
    for key in ("text", "transcript_text"):
        value = cand.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _bucket_of(cand: dict, source_duration: float, buckets: int) -> int:
    """Which slice of the source timeline this candidate's midpoint falls in."""
    if buckets <= 1 or source_duration <= 0:
        return 0
    start, end = _span(cand)
    midpoint = (start + end) / 2.0
    index = int(midpoint / source_duration * buckets)
    return max(0, min(buckets - 1, index))


def _group(cands: list[dict], order: list[int], overlap_threshold: float,
           text_threshold: float) -> list[list[int]]:
    """Greedy grouping in descending-score order. Returns index groups."""
    profiles = [_profile(_text_of(c)) for c in cands]
    claimed: set[int] = set()
    groups: list[list[int]] = []

    for leader in order:
        if leader in claimed:
            continue
        claimed.add(leader)
        members = [leader]
        for other in order:
            if other in claimed:
                continue
            # Compared against the group LEADER, not against every member:
            # chaining through members lets a group drift far from what it
            # started as, and two clips 40 minutes apart end up "duplicates".
            same_time = overlap_ratio(cands[leader], cands[other]) > overlap_threshold
            same_words = _similarity(profiles[leader], profiles[other]) > text_threshold
            if same_time or same_words or same_story(cands[leader], cands[other]):
                claimed.add(other)
                members.append(other)
        groups.append(members)

    return groups


# Two payoffs this close are one moment. Wider than it looks: the story path
# proposes several cuts of one anchor and a model naming the same payoff twice
# rarely picks the same second for it.
SAME_PAYOFF_S = 6.0


def _kinds_of(cand: Any) -> frozenset[str]:
    story = (cand or {}).get("story")
    if not isinstance(story, dict):
        return frozenset()
    return frozenset(str(k) for k in (story.get("archetypes") or []))


def _thread_of(cand: Any) -> str | None:
    story = (cand or {}).get("story")
    return story.get("thread_id") if isinstance(story, dict) else None


def same_story(a: Any, b: Any) -> bool:
    """True when two candidates are cuts of the SAME moment.

    Time overlap and shared words already catch most duplicates, and they miss
    the case the story path creates: two windows built from one anchor, or
    from two anchors the model placed a few seconds apart, can share little
    text — a tight cut and a story-rich cut of one joke have different
    openings — while being the same clip. What makes them the same is the
    payoff, so that is what this compares.

    Returns False when neither side carries a story, which is every legacy
    candidate: this only ever adds groupings, never removes one.
    """
    sa = (a or {}).get("story") if isinstance(a, dict) else None
    sb = (b or {}).get("story") if isinstance(b, dict) else None
    if not isinstance(sa, dict) or not isinstance(sb, dict):
        return False
    pa, pb = sa.get("payoff_t"), sb.get("payoff_t")
    if pa is None or pb is None:
        return False
    try:
        return abs(float(pa) - float(pb)) <= SAME_PAYOFF_S
    except (TypeError, ValueError):
        return False


def _diversify(winners: list[dict], source_duration: float,
               target_count: int) -> list[dict]:
    """Order winners so the first `target_count` spread across the source.

    Three axes, tried in this order because that is how clearly each one means
    "something else": a different STRETCH of the stream, then a different
    STORY, then a different KIND of clip. The middle one is why threads exist —
    a stream that spends an hour on one boss can otherwise hand back a board
    that is entirely that boss, with every clip in a different ten-minute
    bucket and every one of them the same arc.

    All three share one ceiling: a clip is promoted for variety only while the
    quality it costs stays under MAX_DIVERSITY_SACRIFICE. Diversity must not
    rescue a weak clip; it exists to stop redundancy.
    """
    buckets = max(1, target_count)
    remaining = sorted(winners, key=lambda c: -_score_of(c))
    used_time: set[int] = set()
    used_kind: set[str] = set()
    used_thread: set[str] = set()
    ordered: list[dict] = []

    while remaining:
        best = remaining[0]
        pick = best
        # Timeline spread first: a clip from an unseen stretch of the stream is
        # more clearly new than one that is merely a different genre.
        fresh = next(
            (c for c in remaining
             if _bucket_of(c, source_duration, buckets) not in used_time),
            None,
        )
        if fresh is None:
            fresh = next((c for c in remaining
                          if _thread_of(c) and _thread_of(c) not in used_thread),
                         None)
        if fresh is None:
            fresh = next((c for c in remaining
                          if _kinds_of(c) and not (_kinds_of(c) & used_kind)), None)
        # Once every bucket and every archetype has a winner `fresh` is None
        # and this degenerates to plain score order, which is what "one per
        # bucket first" means.
        if fresh is not None and _score_of(best) - _score_of(fresh) <= MAX_DIVERSITY_SACRIFICE:
            pick = fresh
        ordered.append(pick)
        remaining.remove(pick)
        used_time.add(_bucket_of(pick, source_duration, buckets))
        used_kind |= _kinds_of(pick)
        if _thread_of(pick):
            used_thread.add(_thread_of(pick))

    return ordered


def deduplicate(cands: list[dict], *, overlap_threshold: float,
                text_threshold: float, target_count: int) -> list[dict]:
    """Collapse near-duplicates, then rank the survivors for timeline spread.

    Mutates each candidate in place with `dedupe_group`, `is_alternative` and
    `rank_position` (1-based on winners, 0 on alternatives — they are not in
    the ranked list). Returns every input candidate: winners in rank order
    first, then the alternatives grouped behind them.
    """
    cands = [c for c in (cands or []) if isinstance(c, dict)]
    if not cands:
        return []

    def _threshold(value: Any, fallback: float) -> float:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return fallback
        return out if math.isfinite(out) else fallback

    overlap_threshold = _threshold(overlap_threshold, 0.5)
    text_threshold = _threshold(text_threshold, 0.8)
    try:
        target_count = max(1, int(target_count))
    except (TypeError, ValueError):
        target_count = 1

    order = sorted(range(len(cands)), key=lambda i: -_score_of(cands[i]))
    groups = _group(cands, order, overlap_threshold, text_threshold)

    winners: list[dict] = []
    alternatives: list[dict] = []
    for members in groups:
        group_id = uuid.uuid4().hex[:12]
        for position, index in enumerate(members):
            cand = cands[index]
            cand["dedupe_group"] = group_id
            cand["is_alternative"] = position > 0
            cand["rank_position"] = 0
            (winners if position == 0 else alternatives).append(cand)

    # No project duration is passed in, so the last candidate's end is the best
    # available proxy for how far the timeline runs.
    source_duration = max((_span(c)[1] for c in cands), default=0.0)
    ranked = _diversify(winners, source_duration, target_count)
    for position, cand in enumerate(ranked, start=1):
        cand["rank_position"] = position

    if alternatives:
        logger.debug("dedupe kept %d winners, %d alternatives from %d candidates",
                     len(ranked), len(alternatives), len(cands))

    return ranked + alternatives
